"""iter90du - Bulk rebuild endpoint for distribution keys with phantom entries.
Tests that /api/distribution-keys/bulk-rebuild correctly detects and repairs
all phantom keys of an ACP in one call.
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


async def _setup(tag: str):
    """3 lots ('001', '002', '101') + 2 cles:
    - K1: 3 entrees phantom (lot_numbers matchent) shares 898/34/11
    - K2: 3 entrees valides (regression - ne doit pas etre repare)
    """
    db = await _mongo()
    cid = f"iter90du-bulk-{tag}-{uuid.uuid4()}"
    owner_id = f"owner-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "T", "status": "active"})
    await db.owners.insert_one({
        "id": owner_id, "name": "Matexi", "auxiliary_code": "M",
        "copropriete_ids": [cid],
    })

    l1, l2, l3 = [f"lot-{uuid.uuid4().hex[:6]}" for _ in range(3)]
    await db.lots.insert_many([
        {"id": l1, "number": "001", "owner_id": owner_id,
         "copropriete_id": cid, "quotity": 100.0},
        {"id": l2, "number": "002", "owner_id": owner_id,
         "copropriete_id": cid, "quotity": 200.0},
        {"id": l3, "number": "101", "owner_id": owner_id,
         "copropriete_id": cid, "quotity": 300.0},
    ])

    # K1: 3 entrees phantom matchables par lot_number
    k1_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": k1_id, "copropriete_id": cid,
        "name": "K1 Phantom", "code": "K1", "is_default": False,
        "key_type": "quotity",
        "lots": [
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "001", "share": 898.0},
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "002", "share": 34.0},
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "101", "share": 11.0},
        ],
    })

    # K2: 3 entrees valides (regression)
    k2_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": k2_id, "copropriete_id": cid,
        "name": "K2 Valid", "code": "K2", "is_default": False,
        "key_type": "quotity",
        "lots": [
            {"lot_id": l1, "lot_number": "001", "share": 500.0},
            {"lot_id": l2, "lot_number": "002", "share": 250.0},
            {"lot_id": l3, "lot_number": "101", "share": 250.0},
        ],
    })

    return {"db": db, "cid": cid, "k1_id": k1_id, "k2_id": k2_id,
            "owner_id": owner_id, "l1": l1, "l2": l2, "l3": l3}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("lots", "distribution_keys"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["owner_id"]})


async def _test_dry_run_detects_only_phantom_key():
    """Dry-run doit detecter K1 (phantom) et IGNORER K2 (valide)."""
    ctx = await _setup("dry")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/bulk-rebuild",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "mode": "match_by_number",
                    "dry_run": True,
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()

            assert data["dry_run"] is True
            assert data["keys_scanned"] == 2  # K1 + K2
            assert data["keys_with_phantoms"] == 1  # K1 seulement
            assert data["keys_rebuilt"] == 0  # dry-run
            assert len(data["details"]) == 1
            d = data["details"][0]
            assert d["key_id"] == ctx["k1_id"]
            assert d["phantom_count"] == 3
            assert d["stats"]["phantom_matched_by_number"] == 3
            assert d["stats"]["phantom_removed"] == 0
            assert d["applied"] is False
            # Verifie que la DB n'a PAS ete modifiee
            k1 = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["k1_id"]}, {"_id": 0},
            )
            first_lot_id = k1["lots"][0]["lot_id"]
            assert first_lot_id.startswith("phantom-"), (
                "Dry-run ne doit pas modifier la DB. Trouve lot_id="
                f"{first_lot_id}"
            )
    finally:
        await _cleanup(ctx)


async def _test_commit_repairs_phantom_key():
    """Commit doit reparer K1 en preservant les shares (898/34/11)."""
    ctx = await _setup("cmt")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/bulk-rebuild",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "mode": "match_by_number",
                    "dry_run": False,
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is False
            assert data["keys_rebuilt"] == 1

            # Verifie que K1 a bien ete modifiee
            k1 = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["k1_id"]}, {"_id": 0},
            )
            # Les lot_ids doivent maintenant etre ceux des lots reels
            by_num = {l["lot_number"]: l for l in k1["lots"] if not l.get("excluded")}
            assert by_num["001"]["lot_id"] == ctx["l1"], by_num["001"]
            assert by_num["001"]["share"] == 898.0
            assert by_num["002"]["lot_id"] == ctx["l2"]
            assert by_num["002"]["share"] == 34.0
            assert by_num["101"]["lot_id"] == ctx["l3"]
            assert by_num["101"]["share"] == 11.0

            # K2 (valide) reste inchangee
            k2 = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["k2_id"]}, {"_id": 0},
            )
            assert k2["lots"][0]["lot_id"] == ctx["l1"]  # inchange

            # Verifie le champ audit iter90cs_rebuilt_at
            assert "iter90cs_rebuilt_at" in k1
            assert k1.get("iter90cs_rebuild_mode") == "match_by_number"
    finally:
        await _cleanup(ctx)


async def _test_idempotence():
    """Un 2eme commit consecutif ne doit rien changer (0 cles a reparer)."""
    ctx = await _setup("idem")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            # 1er commit
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/bulk-rebuild",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "mode": "match_by_number",
                    "dry_run": False,
                },
            )
            assert r.status_code == 200

            # 2eme commit : plus rien a reparer
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/bulk-rebuild",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "mode": "match_by_number",
                    "dry_run": False,
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert data["keys_with_phantoms"] == 0
            assert data["keys_rebuilt"] == 0
    finally:
        await _cleanup(ctx)


def test_dry_run_detects_only_phantom_key():
    asyncio.run(_test_dry_run_detects_only_phantom_key())


def test_commit_repairs_phantom_key():
    asyncio.run(_test_commit_repairs_phantom_key())


def test_idempotence():
    asyncio.run(_test_idempotence())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
