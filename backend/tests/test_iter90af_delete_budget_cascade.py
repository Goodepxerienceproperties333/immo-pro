"""
Iter90af : DELETE budget doit supprimer en cascade fund_calls + journal_entries.

Regle metier :
- DELETE /api/fiscal/budgets/{id} sans force :
  - Si aucun appel paye : supprime budget + appels + ecritures VE.
  - Si appels payes : 400 avec liste des appels.
- DELETE /api/fiscal/budgets/{id}?force=true : delettre bank_transactions,
  supprime tout.
- Idempotent : re-supprimer un budget deja supprime -> 404.
- Balance de tiers doit refleter la suppression (calcul dynamique).

Tests e2e HTTP via httpx.
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


async def _get_admin_token(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return resp.json().get("access_token") or resp.json().get("token")


async def _mongo():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _setup(name: str):
    """Cree une ACP + budget draft + 2 fund_calls fictifs + VE fictives."""
    db = await _mongo()
    cid = f"iter90af-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    budget_id = f"bg-{uuid.uuid4()}"
    fc1_id = f"fc1-{uuid.uuid4()}"
    fc2_id = f"fc2-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": name, "reference": "T-" + name[:10], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.owners.insert_one({
        "id": o1_id, "name": "Test Owner", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "4100001"}},
    })
    await db.pcmn_accounts.insert_many([
        {"number": "4100001", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "70", "name": "Ventes", "class_num": 7, "copropriete_id": cid},
    ])
    await db.budgets.insert_one({
        "id": budget_id, "fiscal_year_id": fy_id, "copropriete_id": cid,
        "name": "Budget test", "status": "draft",
        "lines": [{"account_number": "60", "amount": 1200}],
        "total_amount": 1200,
    })
    # 2 fund_calls lies au budget
    await db.fund_calls.insert_many([
        {
            "id": fc1_id, "budget_id": budget_id, "copropriete_id": cid,
            "name": "Q1 2026", "date": "2026-01-01", "total_amount": 600,
            "distribution": [{"owner_id": o1_id, "amount": 600, "paid": False}],
        },
        {
            "id": fc2_id, "budget_id": budget_id, "copropriete_id": cid,
            "name": "Q2 2026", "date": "2026-04-01", "total_amount": 600,
            "distribution": [{"owner_id": o1_id, "amount": 600, "paid": False}],
        },
    ])
    # 2 VE auto-generees pour ces appels
    await db.journal_entries.insert_many([
        {
            "id": f"je-{uuid.uuid4()}", "copropriete_id": cid, "journal_type": "VE",
            "date": "2026-01-01", "reference": "AF-Q1", "auto_generated": True,
            "source_type": "fund_call", "source_id": fc1_id,
            "total_debit": 600, "total_credit": 600,
            "lines": [
                {"account_number": "4100001", "third_party_id": o1_id, "debit": 600, "credit": 0},
                {"account_number": "70", "debit": 0, "credit": 600},
            ],
        },
        {
            "id": f"je-{uuid.uuid4()}", "copropriete_id": cid, "journal_type": "VE",
            "date": "2026-04-01", "reference": "AF-Q2", "auto_generated": True,
            "source_type": "fund_call", "source_id": fc2_id,
            "total_debit": 600, "total_credit": 600,
            "lines": [
                {"account_number": "4100001", "third_party_id": o1_id, "debit": 600, "credit": 0},
                {"account_number": "70", "debit": 0, "credit": 600},
            ],
        },
    ])
    return {"db": db, "cid": cid, "fy_id": fy_id, "budget_id": budget_id,
            "fc1": fc1_id, "fc2": fc2_id, "owner": o1_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "budgets", "fund_calls", "journal_entries",
                 "pcmn_accounts", "bank_transactions"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["owner"]})


async def _scenario_delete_cascade():
    """Suppression cascade complete : fund_calls + VE disparaissent."""
    ctx = await _setup("iter90af_cascade")
    try:
        # Pre-condition : 2 fund_calls + 2 VE existent
        assert await ctx["db"].fund_calls.count_documents({"budget_id": ctx["budget_id"]}) == 2
        assert await ctx["db"].journal_entries.count_documents({
            "source_type": "fund_call",
            "source_id": {"$in": [ctx["fc1"], ctx["fc2"]]}
        }) == 2

        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["deleted_fund_calls"] == 2
        assert r["unlettred_transactions"] == 0

        # Post-condition : fund_calls et budget disparus (hard delete OK, ce ne sont
        # pas des ecritures comptables). Les journal_entries auto-generees sont
        # CONSERVEES avec reversed=True + contre-passations creees (iter90bx :
        # audit trail legal PCMN art. III.86 CDE).
        assert await ctx["db"].fund_calls.count_documents({"budget_id": ctx["budget_id"]}) == 0
        # Les originales sont preservees, marquees reversed=True
        originals = await ctx["db"].journal_entries.count_documents({
            "source_type": "fund_call",
            "source_id": {"$in": [ctx["fc1"], ctx["fc2"]]},
            "reversed": True,
        })
        assert originals == 2, f"2 originales reversed attendues, obtenu {originals}"
        # 2 contre-passations creees (is_reversal=True, pointent vers les originales)
        reversals = await ctx["db"].journal_entries.count_documents({
            "copropriete_id": ctx["cid"],
            "is_reversal": True,
        })
        assert reversals == 2, f"2 contre-passations attendues, obtenu {reversals}"
        assert await ctx["db"].budgets.find_one({"id": ctx["budget_id"]}) is None
    finally:
        await _cleanup(ctx)


async def _scenario_delete_paid_without_force_rejected():
    """Un appel paye + force=false -> 400 clair."""
    ctx = await _setup("iter90af_paid_reject")
    try:
        # Marquer fc1 comme paye
        await ctx["db"].fund_calls.update_one(
            {"id": ctx["fc1"]},
            {"$set": {"distribution.0.paid": True}}
        )
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400, resp.text
        assert "paiement" in resp.json().get("detail", "").lower()
        # Rien n'a bouge
        assert await ctx["db"].fund_calls.count_documents({"budget_id": ctx["budget_id"]}) == 2
        assert await ctx["db"].budgets.find_one({"id": ctx["budget_id"]}) is not None
    finally:
        await _cleanup(ctx)


async def _scenario_delete_paid_with_force():
    """force=true + appel paye -> delettre + supprime."""
    ctx = await _setup("iter90af_force_paid")
    tx_id = f"tx-{uuid.uuid4()}"
    try:
        await ctx["db"].fund_calls.update_one(
            {"id": ctx["fc1"]},
            {"$set": {"distribution.0.paid": True}}
        )
        # Transaction bancaire matchee a fc1
        await ctx["db"].bank_transactions.insert_one({
            "id": tx_id, "copropriete_id": ctx["cid"],
            "matched": True, "matched_fund_call_id": ctx["fc1"],
            "amount": 600.0,
        })

        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}?force=true",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["deleted_fund_calls"] == 2
        assert r["unlettred_transactions"] == 1

        # Verifier tx delettree
        tx = await ctx["db"].bank_transactions.find_one({"id": tx_id}, {"_id": 0})
        assert tx["matched"] is False
        assert tx.get("matched_fund_call_id") is None
    finally:
        await _cleanup(ctx)


async def _scenario_idempotence_404():
    """Re-supprimer un budget deja supprime -> 404."""
    ctx = await _setup("iter90af_idempotent")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            r1 = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r1.status_code == 200
            r2 = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r2.status_code == 404
    finally:
        await _cleanup(ctx)


async def _scenario_delete_without_calls():
    """Un budget draft sans appels ni ecritures -> suppression triviale, deleted_fund_calls=0."""
    db = await _mongo()
    cid = f"iter90af-empty-{uuid.uuid4()}"
    budget_id = f"bg-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "empty", "reference": "T-e", "status": "active"})
        await db.fiscal_years.insert_one({"id": fy_id, "name": "2026", "start_date": "2026-01-01",
                                          "end_date": "2026-12-31", "copropriete_id": cid})
        await db.budgets.insert_one({
            "id": budget_id, "fiscal_year_id": fy_id, "copropriete_id": cid,
            "name": "Empty budget", "status": "draft", "lines": [], "total_amount": 0,
        })
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{budget_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted_fund_calls"] == 0
        assert await db.budgets.find_one({"id": budget_id}) is None
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_many({"copropriete_id": cid})
        await db.budgets.delete_many({"copropriete_id": cid})


def test_delete_cascade_clears_calls_and_entries():
    asyncio.run(_scenario_delete_cascade())


def test_delete_paid_without_force_rejected():
    asyncio.run(_scenario_delete_paid_without_force_rejected())


def test_delete_paid_with_force_unlettres_and_deletes():
    asyncio.run(_scenario_delete_paid_with_force())


def test_delete_idempotence_returns_404():
    asyncio.run(_scenario_idempotence_404())


def test_delete_without_calls_succeeds_trivially():
    asyncio.run(_scenario_delete_without_calls())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
