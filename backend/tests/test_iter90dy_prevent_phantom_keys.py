"""iter90dy - Prevention des cles phantoms :
1. Cascade a la suppression de lot (delete_lot)
2. Smart-import : auto-rebind au moment de la creation (create_lot)

User request (Feb 2026) :
> "cette situation [cles phantoms] ne doit jamais arriver les montants
> doivent toujours etre corrects sur base des lots et des cles de repartition"

Objectif : rendre STRUCTURELLEMENT impossible les cles phantoms via 2 leviers :
- (a) cascade automatique quand un lot est supprime (marque les entrees
      correspondantes excluded=True)
- (d) rebind intelligent quand un lot est cree avec un lot_number
      correspondant a une entree phantom existante
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return dict(resp.cookies)


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_acp(tag: str):
    """Cree une ACP avec 1 propriétaire et 1 clé (initialement propre)."""
    db = await _mongo()
    cid = f"iter90dy-{tag}-{uuid.uuid4()}"
    owner_id = f"o-{uuid.uuid4().hex[:6]}"

    await db.coproprietes.insert_one({"id": cid, "name": "T", "status": "active"})
    await db.owners.insert_one({
        "id": owner_id, "name": "Matexi", "auxiliary_code": "M",
        "copropriete_ids": [cid],
    })
    return {"db": db, "cid": cid, "owner_id": owner_id}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("lots", "distribution_keys", "mutations", "fund_calls",
                 "journal_entries"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["owner_id"]})


# ========================================================================
# CASCADE A LA SUPPRESSION (a)
# ========================================================================
async def _test_delete_lot_cascades_to_keys():
    """Quand on supprime un lot, les entrees des cles sont marquees excluded."""
    ctx = await _setup_acp("cascade")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)

            # Cree 2 lots via API
            r1 = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "001", "copropriete_id": ctx["cid"],
                "quotity": 5000.0, "owner_id": ctx["owner_id"],
            })
            assert r1.status_code == 200, r1.text
            L1_id = r1.json()["id"]
            r2 = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "002", "copropriete_id": ctx["cid"],
                "quotity": 5000.0, "owner_id": ctx["owner_id"],
            })
            L2_id = r2.json()["id"]

            # Cree une cle avec les 2 lots
            key_id = str(uuid.uuid4())
            await ctx["db"].distribution_keys.insert_one({
                "id": key_id, "copropriete_id": ctx["cid"],
                "name": "K1", "code": "K1", "is_default": True,
                "key_type": "quotity",
                "lots": [
                    {"lot_id": L1_id, "lot_number": "001", "share": 5000.0},
                    {"lot_id": L2_id, "lot_number": "002", "share": 5000.0},
                ],
            })

            # Supprime le lot 001
            r_del = await client.delete(f"{BACKEND_URL}/api/lots/{L1_id}", cookies=cookies)
            assert r_del.status_code == 200, r_del.text
            data = r_del.json()
            assert data["cascade"]["distribution_keys_updated"] == 1

            # Verifie que l'entree L1 est maintenant excluded
            k = await ctx["db"].distribution_keys.find_one({"id": key_id}, {"_id": 0})
            entries_by_lot = {e["lot_id"]: e for e in k["lots"]}
            assert entries_by_lot[L1_id]["excluded"] is True
            assert "iter90dy_orphan_since" in entries_by_lot[L1_id]
            # L2 reste inchange
            assert entries_by_lot[L2_id].get("excluded") is not True
    finally:
        await _cleanup(ctx)


# ========================================================================
# SMART IMPORT (d)
# ========================================================================
async def _test_smart_import_rebinds_phantom_by_lot_number():
    """Cree un lot avec un lot_number matchant une entree phantom existante
    -> l'entree est automatiquement rebindee vers le nouveau lot_id."""
    ctx = await _setup_acp("smart")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)

            # 1) Cree une cle avec 3 entrees PHANTOM (lot_ids inexistants)
            key_id = str(uuid.uuid4())
            phantom_ids = [f"phantom-{uuid.uuid4()}" for _ in range(3)]
            await ctx["db"].distribution_keys.insert_one({
                "id": key_id, "copropriete_id": ctx["cid"],
                "name": "K1", "code": "K1", "is_default": True,
                "key_type": "quotity",
                "lots": [
                    {"lot_id": phantom_ids[0], "lot_number": "001", "share": 898.0},
                    {"lot_id": phantom_ids[1], "lot_number": "002", "share": 34.0},
                    {"lot_id": phantom_ids[2], "lot_number": "101", "share": 11.0},
                ],
            })

            # 2) Cree le lot 001 -> doit rebind automatiquement l'entree phantom
            r = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "001", "copropriete_id": ctx["cid"],
                "quotity": 0, "owner_id": ctx["owner_id"],  # quotity=0 : simule import Optipro
            })
            assert r.status_code == 200, r.text
            new_lot_id = r.json()["id"]
            # Le response indique le rebind
            rebound = r.json().get("_rebound_keys", [])
            assert len(rebound) == 1, rebound
            assert rebound[0]["key_id"] == key_id

            # Verifie que la cle a maintenant le NOUVEAU lot_id
            k = await ctx["db"].distribution_keys.find_one({"id": key_id}, {"_id": 0})
            entry_001 = next(e for e in k["lots"] if e.get("lot_number") == "001")
            assert entry_001["lot_id"] == new_lot_id
            assert entry_001["share"] == 898.0  # share preservee
            assert "iter90dy_auto_rebound_at" in entry_001
            assert entry_001["iter90dy_previous_lot_id"] == phantom_ids[0]
            # Les autres entrees phantoms restent (a nettoyer plus tard)
            entry_002 = next(e for e in k["lots"] if e.get("lot_number") == "002")
            assert entry_002["lot_id"] == phantom_ids[1]  # toujours phantom
    finally:
        await _cleanup(ctx)


async def _test_smart_import_no_match_no_rebind():
    """Cree un lot avec un lot_number qui n'existe dans aucune cle
    -> aucun rebind (comportement normal)."""
    ctx = await _setup_acp("nomatch")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            # Cle avec entree phantom "999"
            key_id = str(uuid.uuid4())
            await ctx["db"].distribution_keys.insert_one({
                "id": key_id, "copropriete_id": ctx["cid"],
                "name": "K", "code": "K", "is_default": True,
                "key_type": "quotity",
                "lots": [
                    {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "999", "share": 100.0},
                ],
            })
            # Cree lot 042 (aucun match)
            r = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "042", "copropriete_id": ctx["cid"],
                "quotity": 0, "owner_id": ctx["owner_id"],
            })
            assert r.status_code == 200
            rebound = r.json().get("_rebound_keys", [])
            assert len(rebound) == 0  # aucun rebind
    finally:
        await _cleanup(ctx)


async def _test_smart_import_lot_already_in_key_no_rebind():
    """Si le lot est deja dans la cle avec le bon lot_id, ne pas dupliquer."""
    ctx = await _setup_acp("alrdy")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            # Cree lot 001
            r1 = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "001", "copropriete_id": ctx["cid"],
                "quotity": 100.0, "owner_id": ctx["owner_id"],
            })
            L1_id = r1.json()["id"]
            # Cle avec entree correcte + une phantom sur meme numero
            key_id = str(uuid.uuid4())
            phantom_id = f"phantom-{uuid.uuid4()}"
            await ctx["db"].distribution_keys.insert_one({
                "id": key_id, "copropriete_id": ctx["cid"],
                "name": "K", "code": "K", "is_default": True,
                "key_type": "quotity",
                "lots": [
                    {"lot_id": L1_id, "lot_number": "001", "share": 100.0},
                    {"lot_id": phantom_id, "lot_number": "999", "share": 50.0},
                ],
            })
            # Cree lot 999 (nouveau) -> devrait rebind l'entree 999 phantom
            r2 = await client.post(f"{BACKEND_URL}/api/lots", cookies=cookies, json={
                "number": "999", "copropriete_id": ctx["cid"],
                "quotity": 0, "owner_id": ctx["owner_id"],
            })
            L999_id = r2.json()["id"]
            rebound = r2.json().get("_rebound_keys", [])
            assert len(rebound) == 1, "L'entree phantom 999 doit etre rebindee"

            # Verif cle: L1 inchange + entree phantom 999 rebindee
            k = await ctx["db"].distribution_keys.find_one({"id": key_id}, {"_id": 0})
            entries = {e["lot_id"]: e for e in k["lots"]}
            assert L1_id in entries and entries[L1_id]["lot_number"] == "001"
            assert L999_id in entries
            assert phantom_id not in entries
    finally:
        await _cleanup(ctx)


# ========================================================================
# Entry points
# ========================================================================
def test_delete_lot_cascades_to_keys():
    asyncio.run(_test_delete_lot_cascades_to_keys())


def test_smart_import_rebinds_phantom_by_lot_number():
    asyncio.run(_test_smart_import_rebinds_phantom_by_lot_number())


def test_smart_import_no_match_no_rebind():
    asyncio.run(_test_smart_import_no_match_no_rebind())


def test_smart_import_lot_already_in_key_no_rebind():
    asyncio.run(_test_smart_import_lot_already_in_key_no_rebind())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
