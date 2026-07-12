"""iter90dx - Detection et reparation des mutations avec from/to inverses.

User bug (Feb 2026, PROD ACP Acacia):
> "sens inverse (DEBIT au lieu de CREDIT) sur 17/18/19 novembre : il s'agit
> de vente donc bug"

Cas reel : Matexi est le vendeur founder de 30 lots. Les mutations
17/11-19/11 ont Matexi en to_owner_id (comme acheteur) au lieu de
from_owner_id (vendeur). Impact : les OD MUT-R generent des DEBITS sur
Matexi (au lieu de CREDITS).

Fix iter90dx :
- GET /api/mutations/detect-inversions?copropriete_id=X detecte les
  mutations dont le from_owner_id declaré ne matche pas la chaine
  d'ownership attendue.
- POST /api/mutations/{id}/fix-inversion?dry_run=false swap from/to,
  contre-passe les ODs et met a jour lot.owner_id.
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


async def _setup_inverted_mutation(tag: str):
    """Setup : Matexi possede lot L, vend a Dewinter le 17/11/2025.
    On enregistre volontairement la mutation INVERSEE (Dewinter -> Matexi)
    pour simuler le bug PROD.
    """
    db = await _mongo()
    cid = f"iter90dx-{tag}-{uuid.uuid4()}"
    matexi = f"o-matexi-{uuid.uuid4().hex[:6]}"
    dewinter = f"o-dewin-{uuid.uuid4().hex[:6]}"
    lot_id = f"lot-{uuid.uuid4().hex[:6]}"

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia T", "status": "active"})
    await db.owners.insert_many([
        {"id": matexi, "name": "Matexi", "auxiliary_code": "M",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100021"}}},
        {"id": dewinter, "name": "Dewinter", "auxiliary_code": "D",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100022"}}},
    ])

    # Lot avec 1 mutation INVERSEE (Dewinter -> Matexi le 17/11) et
    # lot.owner_id = Matexi (coherent avec la mutation inversee)
    # Alors qu'en realite Matexi -> Dewinter, l'owner FINAL devrait etre Dewinter.
    mut_id = str(uuid.uuid4())
    await db.lots.insert_one({
        "id": lot_id, "number": "101", "owner_id": matexi,
        "owner_ids": [matexi], "copropriete_id": cid, "quotity": 11.0,
        "mutations": [{
            "id": mut_id, "date": "2025-11-17",
            "old_owner_id": dewinter,  # INVERSE - vrai vendeur = Matexi
            "new_owner_id": matexi,     # INVERSE - vrai acheteur = Dewinter
        }],
    })
    await db.mutations.insert_one({
        "id": mut_id, "copropriete_id": cid, "lot_id": lot_id,
        "sale_date": "2025-11-17",
        "from_owner_id": dewinter,  # INVERSE
        "to_owner_id": matexi,       # INVERSE
    })

    # Un OD MUT-R lie a cette mutation (dans le mauvais sens)
    od_id = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        "id": od_id, "journal_type": "OD",
        "date": "2025-11-17",
        "reference": "MUT-101-R",
        "description": (
            "Mutation lot 101 - Fonds de roulement (backfill retroactif via appel Q1): "
            "Dewinter -> Matexi (490.36 EUR)"  # INVERSE
        ),
        "lines": [
            {"account_number": "4100021", "debit": 490.36, "credit": 0.0,
             "third_party_id": matexi},
            {"account_number": "4100022", "debit": 0.0, "credit": 490.36,
             "third_party_id": dewinter},
        ],
        "total_debit": 490.36, "total_credit": 490.36,
        "copropriete_id": cid,
        "source_type": "lot_mutation", "source_id": lot_id,
        "source_subtype": "fonds_roulement",
    })

    return {"db": db, "cid": cid, "matexi": matexi, "dewinter": dewinter,
            "lot_id": lot_id, "mut_id": mut_id, "od_id": od_id}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("lots", "mutations", "journal_entries"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["matexi"], ctx["dewinter"]]}})


async def _test_detect_inversion():
    """detect-inversions doit signaler la mutation inversee (via founder heuristic)."""
    ctx = await _setup_inverted_mutation("detect")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.get(
                f"{BACKEND_URL}/api/mutations/detect-inversions",
                cookies=cookies, params={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],  # heuristic: Matexi ne devrait jamais etre acheteur
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["total_mutations_scanned"] == 1
            assert data["suspects_count"] == 1
            assert data["auto_fixable_count"] == 1
            s = data["suspects"][0]
            assert s["mutation_id"] == ctx["mut_id"]
            assert s["lot_id"] == ctx["lot_id"]
            assert s["kind"] == "from_to_swapped"
            assert s["auto_fixable"] is True
            assert s["declared_from_id"] == ctx["dewinter"]
            assert s["declared_to_id"] == ctx["matexi"]
            assert "founder" in s["reason"].lower()
    finally:
        await _cleanup(ctx)


async def _test_dry_run_no_side_effect():
    """dry_run=true : retourne le plan mais ne modifie rien."""
    ctx = await _setup_inverted_mutation("dryrun")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/mutations/{ctx['mut_id']}/fix-inversion",
                cookies=cookies, params={"dry_run": "true"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is True
            assert data["applied"] is False
            assert data["swap"]["from"]["after_id"] == ctx["matexi"]
            assert data["swap"]["to"]["after_id"] == ctx["dewinter"]
            assert len(data["od_entries_to_reverse"]) == 1

            # Verifie que la DB n'a pas ete modifiee
            mut = await ctx["db"].mutations.find_one({"id": ctx["mut_id"]}, {"_id": 0})
            assert mut["from_owner_id"] == ctx["dewinter"]  # toujours inverse
            od = await ctx["db"].journal_entries.find_one({"id": ctx["od_id"]}, {"_id": 0})
            assert od.get("reversed") is not True
    finally:
        await _cleanup(ctx)


async def _test_commit_applies_fix():
    """dry_run=false : swap + reverse OD + update lot.owner_id."""
    ctx = await _setup_inverted_mutation("commit")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/mutations/{ctx['mut_id']}/fix-inversion",
                cookies=cookies, params={"dry_run": "false"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["applied"] is True
            assert data["reversed_ods_count"] == 1

            # Verifie db.mutations swap
            mut = await ctx["db"].mutations.find_one({"id": ctx["mut_id"]}, {"_id": 0})
            assert mut["from_owner_id"] == ctx["matexi"], mut
            assert mut["to_owner_id"] == ctx["dewinter"], mut
            assert "iter90dx_fix_applied_at" in mut

            # Verifie lot.mutations[] swap
            lot = await ctx["db"].lots.find_one({"id": ctx["lot_id"]}, {"_id": 0})
            lot_mut = lot["mutations"][0]
            assert lot_mut["old_owner_id"] == ctx["matexi"]
            assert lot_mut["new_owner_id"] == ctx["dewinter"]

            # Verifie lot.owner_id mis a jour au VRAI proprietaire actuel (Dewinter)
            assert lot["owner_id"] == ctx["dewinter"]
            assert lot["owner_ids"] == [ctx["dewinter"]]

            # Verifie OD marque reversed
            od = await ctx["db"].journal_entries.find_one({"id": ctx["od_id"]}, {"_id": 0})
            assert od.get("reversed") is True
            assert od.get("reversal_reason") == "iter90dx_mutation_inversion_fix"
    finally:
        await _cleanup(ctx)


async def _test_after_fix_no_more_suspects():
    """Apres fix, detect-inversions ne signale plus la mutation."""
    ctx = await _setup_inverted_mutation("after")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            # Applique le fix
            await client.post(
                f"{BACKEND_URL}/api/mutations/{ctx['mut_id']}/fix-inversion",
                cookies=cookies, params={"dry_run": "false"},
            )
            # Re-detect (avec founder_owner_id=Matexi)
            r = await client.get(
                f"{BACKEND_URL}/api/mutations/detect-inversions",
                cookies=cookies, params={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                },
            )
            data = r.json()
            assert data["suspects_count"] == 0, (
                f"Apres fix, 0 suspect attendu. Obtenu {data}"
            )
    finally:
        await _cleanup(ctx)


def test_detect_inversion_returns_suspect():
    asyncio.run(_test_detect_inversion())


def test_dry_run_no_side_effect():
    asyncio.run(_test_dry_run_no_side_effect())


def test_commit_applies_fix():
    asyncio.run(_test_commit_applies_fix())


def test_after_fix_no_more_suspects():
    asyncio.run(_test_after_fix_no_more_suspects())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
