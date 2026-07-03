"""
Iter90aj : Appel de fonds retroactif -> owner_id = proprietaire a la DATE de l'appel

Bug initial (ACP Acacia, PROD) :
- Mutation lot 001 : Matexi -> TEUWEN, sale_date=2025-11-17
- Budget 2026 vote APRES la mutation, avec appels "Fonds de reserve Annuel"
  et "Fonds de roulement Annuel" dates 2025-10-01 (retroactif).
- Systeme creait la distribution avec `lot.owner_id` (= TEUWEN, current) au
  lieu de resoudre le proprietaire a la date 2025-10-01 (= Matexi, seller).
- Consequence : la VE de la reserve etait a charge de TEUWEN, violation
  totale de la regle "reserve = 100% vendeur".

Fix (iter90aj) :
- _resolve_owner_at_date(lot_id, target_date, fallback) : marche dans
  mutations_by_lot pour retrouver le proprietaire en place a la date cible.
- _rebind_owner_at_call_date(entries, call_date) : re-affecte les entries
  distribution avec le proprietaire correct (sans proratisation).
- Applique aux :
  1. reserve_dist / roul_dist inline dans _generate_from_budget (appel #1)
  2. dist dans _generate_independent_series (fonds avec frequency propre)
  3. distribution dans POST /api/fund-calls (appels standalone reserve/roulement/special)

Scenarios testes :
1. Series reserve independante, appel date < mutation -> VE sur Matexi (seller).
2. Series reserve independante, appel date > mutation -> VE sur TEUWEN (buyer).
3. Standalone POST /api/fund-calls call_type=reserve avec date retroactive -> Matexi.
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

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


async def _get_admin_client():
    client = httpx.AsyncClient(timeout=30, base_url=BACKEND_URL)
    resp = await client.post("/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return client


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup():
    """ACP + 2 owners (Matexi seller, Teuwen buyer) + 1 lot + mutation 17/11/2025.
    lot.owner_id est deja TEUWEN (post-mutation)."""
    db = await _mongo()
    cid = f"iter90aj-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    matexi_id = f"o-matexi-{uuid.uuid4()}"
    teuwen_id = f"o-teuwen-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia-test", "reference": "ACA-t", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2025-10-01", "end_date": "2026-09-30",
        "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier Teuwen", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": matexi_id, "name": "Matexi", "auxiliary_code": "C0001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": teuwen_id, "name": "TEUWEN Gael", "auxiliary_code": "C0002",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # Lot APRES mutation : owner_id = TEUWEN
    await db.lots.insert_one({
        "id": lot_id, "number": "001", "owner_id": teuwen_id, "owner_ids": [teuwen_id],
        "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "001"}],
    })
    # Mutation 17/11/2025 : Matexi -> TEUWEN
    await db.mutations.insert_one({
        "id": f"mut-{uuid.uuid4()}", "copropriete_id": cid, "lot_id": lot_id,
        "from_owner_id": matexi_id, "to_owner_id": teuwen_id,
        "sale_date": "2025-11-17",
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
            "matexi": matexi_id, "teuwen": teuwen_id, "key": key_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["matexi"], ctx["teuwen"]]}})


async def _scenario_standalone_reserve_before_mutation():
    """Standalone POST /api/fund-calls, call_type=reserve, date 01/10/2025
    (avant mutation 17/11/2025) -> distribution doit assigner Matexi."""
    ctx = await _setup()
    try:
        client = await _get_admin_client()
        try:
            resp = await client.post("/api/fund-calls", json={
                "name": "Fonds de reserve - Test retro",
                "date": "2025-10-01",
                "due_date": "2025-10-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Test iter90aj",
                "total_amount": 1000.0,
                "call_type": "reserve",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()
            distribution = call_doc.get("distribution", [])
            assert len(distribution) == 1, f"Expected 1 dist entry, got {len(distribution)}"
            entry = distribution[0]
            assert entry["owner_id"] == ctx["matexi"], (
                f"Expected owner_id=Matexi ({ctx['matexi']}), got {entry.get('owner_id')}. "
                f"Bug iter90aj not fixed: reserve is assigned to current owner instead of "
                f"the owner at call date."
            )
            assert entry["amount"] == 1000.0
        finally:
            await client.aclose()
    finally:
        await _cleanup(ctx)


async def _scenario_standalone_reserve_after_mutation():
    """Standalone POST /api/fund-calls, call_type=reserve, date 15/12/2025
    (apres mutation 17/11/2025) -> distribution doit assigner TEUWEN."""
    ctx = await _setup()
    try:
        client = await _get_admin_client()
        try:
            resp = await client.post("/api/fund-calls", json={
                "name": "Fonds de reserve - Post mutation",
                "date": "2025-12-15",
                "due_date": "2026-01-15",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Test iter90aj apres mutation",
                "total_amount": 500.0,
                "call_type": "reserve",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()
            distribution = call_doc.get("distribution", [])
            assert len(distribution) == 1
            entry = distribution[0]
            assert entry["owner_id"] == ctx["teuwen"], (
                f"Expected owner_id=TEUWEN ({ctx['teuwen']}), got {entry.get('owner_id')}. "
                f"Rebind ne doit PAS s'appliquer quand la date d'appel est post-mutation."
            )
        finally:
            await client.aclose()
    finally:
        await _cleanup(ctx)


async def _scenario_standalone_roulement_before_mutation():
    """call_type=roulement, date pre-mutation -> Matexi."""
    ctx = await _setup()
    try:
        client = await _get_admin_client()
        try:
            resp = await client.post("/api/fund-calls", json={
                "name": "Fonds de roulement - Test retro",
                "date": "2025-10-01",
                "due_date": "2025-10-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Test iter90aj roulement",
                "total_amount": 2000.0,
                "call_type": "roulement",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()
            distribution = call_doc.get("distribution", [])
            assert len(distribution) == 1
            entry = distribution[0]
            assert entry["owner_id"] == ctx["matexi"], (
                f"Expected owner_id=Matexi ({ctx['matexi']}), got {entry.get('owner_id')}"
            )
        finally:
            await client.aclose()
    finally:
        await _cleanup(ctx)


async def _scenario_no_mutation_uses_current_owner():
    """Aucune mutation -> comportement inchange, owner = current lot.owner_id."""
    ctx = await _setup()
    try:
        # Retirer la mutation
        await ctx["db"].mutations.delete_many({"copropriete_id": ctx["cid"]})
        client = await _get_admin_client()
        try:
            resp = await client.post("/api/fund-calls", json={
                "name": "Fonds de reserve - No mutation",
                "date": "2025-10-01",
                "due_date": "2025-10-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Regression sans mutation",
                "total_amount": 1000.0,
                "call_type": "reserve",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()
            entry = call_doc["distribution"][0]
            assert entry["owner_id"] == ctx["teuwen"], (
                f"Sans mutation, owner doit rester current (TEUWEN). Got {entry.get('owner_id')}"
            )
        finally:
            await client.aclose()
    finally:
        await _cleanup(ctx)


def test_standalone_reserve_before_mutation_uses_seller():
    asyncio.run(_scenario_standalone_reserve_before_mutation())


def test_standalone_reserve_after_mutation_uses_buyer():
    asyncio.run(_scenario_standalone_reserve_after_mutation())


def test_standalone_roulement_before_mutation_uses_seller():
    asyncio.run(_scenario_standalone_roulement_before_mutation())


def test_no_mutation_uses_current_owner_regression():
    asyncio.run(_scenario_no_mutation_uses_current_owner())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
