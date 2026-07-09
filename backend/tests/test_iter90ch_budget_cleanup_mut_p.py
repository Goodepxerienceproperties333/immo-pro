"""
iter90ch : REGRESSION LOCK - Suppression / devalidation d'un budget doit
contre-passer AUSSI les OD MUT-P retroactives liees aux appels du budget.

Bug rapporte utilisateur (Feb 2026) :
> "Lors de la suppression ou devalidation du budget les OD doivent etre
> supprimees aussi."

Contexte : `_delete_auto_entries("fund_call", call_id)` ne contre-passe
que les VE (source_type='fund_call'). Les OD MUT-P retroactives creees par
`generate_prorata_mut_ods_for_call` ont `source_type='lot_mutation'` +
`source_subtype='prorata_post_mutation'` + `fund_call_id=call_id`. Elles
n'etaient donc PAS nettoyees lors d'une suppression / devalidation du
budget parent -> orphelins comptables.

Fix iter90ch :
- Nouveau helper `reverse_post_mutation_ods_for_call` (fund_calls.py)
- Utilise dans : delete_fund_call, delete_all_fund_calls,
  regenerate_from_budget, delete_budget (fiscal.py), revoke_budget (fiscal.py)

Test coverage :
1. Suppression budget -> OD MUT-P contre-passees
2. Devalidation budget -> OD MUT-P contre-passees
3. delete-all-fund-calls -> OD MUT-P contre-passees
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
_TOKEN_CACHE = {"token": None}


async def _login(client):
    """iter90ch : cache le token au niveau module pour eviter le rate-limit
    (login = 10/min max)."""
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _base_setup(prefix: str):
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    seller_id = f"seller-{uuid.uuid4()}"
    buyer_id = f"buyer-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"
    budget_id = f"bud-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100021", "name": "T-Seller", "class_num": 4, "copropriete_id": cid},
        {"number": "4100022", "name": "T-Buyer", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Seller", "last_name": "Seller",
         "auxiliary_code": "S001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100021"}}},
        {"id": buyer_id, "name": "Buyer", "last_name": "Buyer",
         "auxiliary_code": "B001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100022"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": seller_id,
        "owner_ids": [seller_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "A1"}],
    })
    await db.budgets.insert_one({
        "id": budget_id, "name": "Budget 2026", "fiscal_year_id": fy_id,
        "copropriete_id": cid, "status": "approved",
        "lines": [{"account_number": "6", "account_name": "Charges",
                   "amount": 1488.0, "distribution_key_id": key_id}],
        # iter90ck : budget doit avoir roulement_fund_amount > 0 pour que
        # generate_prorata_mut_ods_for_call ne raise pas HTTPException 400
        # quand une mutation est detectee dans la periode d'un appel wizard.
        "reserve_fund_amount": 0.0,
        "reserve_fund_key_id": key_id,
        "roulement_fund_amount": 1000.0,
        "roulement_fund_key_id": key_id,
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
        "seller": seller_id, "buyer": buyer_id, "key": key_id,
        "budget_id": budget_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"]]}})


async def _mutate(client, ctx, sale_date):
    hdr = await _login(client)
    resp = await client.post(
        f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
        headers=hdr,
        json={"new_owner_id": ctx["buyer"], "sale_date": sale_date,
              "sale_price": 200000.0},
    )
    assert resp.status_code == 200, resp.text


async def _create_straddling_call(client, ctx):
    """Cree un appel Q2 (01/04) qui straddle mutation 15/05 -> OD MUT-P attendu."""
    hdr = await _login(client)
    resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
        "name": "Trimestriel 2/4 - 2026",
        "date": "2026-04-01",
        "due_date": "2026-04-30",
        "fiscal_year_id": ctx["fy_id"],
        "description": "Q2",
        "total_amount": 372.0,
        "call_type": "provisions",
        "distribution_key_id": ctx["key"],
        "copropriete_id": ctx["cid"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_call_via_budget(client, ctx):
    """Genere les appels via le budget wizard (regenerate) pour lier au budget_id."""
    hdr = await _login(client)
    resp = await client.post(f"{BACKEND_URL}/api/fund-calls/regenerate-from-budget",
                              headers=hdr, json={
        "budget_id": ctx["budget_id"],
        "frequency": 4,
        "start_date": "2026-01-01",
        "due_offset_days": 30,
        "copropriete_id": ctx["cid"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _count_active_mut_p(db, cid):
    return await db.journal_entries.count_documents({
        "copropriete_id": cid,
        "source_subtype": "prorata_post_mutation",
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
    })


async def _count_reversed_mut_p(db, cid):
    return await db.journal_entries.count_documents({
        "copropriete_id": cid,
        "source_subtype": "prorata_post_mutation",
        "reversed": True,
    })


# =========================================================================
# SCENARIO 1 : DELETE budget -> OD MUT-P contre-passees
# =========================================================================
async def _scenario_delete_budget_reverses_mut_p():
    ctx = await _base_setup("iter90ch-del")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)

            # 1) Mutation puis appel straddling via budget
            await _mutate(client, ctx, "2026-05-15")
            await _create_call_via_budget(client, ctx)

            # 2) Verifie qu'au moins 1 OD MUT-P a ete creee (appel Q2 straddling)
            active_before = await _count_active_mut_p(ctx["db"], ctx["cid"])
            assert active_before >= 1, (
                f"Prerequis : au moins 1 OD MUT-P active attendue apres "
                f"generation. Obtenu : {active_before}."
            )

            # 3) Supprime le budget (avec force=true si necessaire, non paye ici)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            # 4) Verifie que toutes les OD MUT-P actives sont contre-passees
            active_after = await _count_active_mut_p(ctx["db"], ctx["cid"])
            reversed_count = await _count_reversed_mut_p(ctx["db"], ctx["cid"])
            assert active_after == 0, (
                f"REGRESSION iter90ch : {active_after} OD MUT-P active(s) "
                f"restante(s) apres suppression du budget. Attendu : 0. "
                f"reversed_count={reversed_count}"
            )
            assert reversed_count >= active_before, (
                f"REGRESSION iter90ch : contre-passations manquantes. "
                f"Actives avant={active_before}, reversed apres={reversed_count}."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : REVOKE budget -> OD MUT-P contre-passees
# =========================================================================
async def _scenario_revoke_budget_reverses_mut_p():
    ctx = await _base_setup("iter90ch-rev")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            await _mutate(client, ctx, "2026-05-15")
            await _create_call_via_budget(client, ctx)

            active_before = await _count_active_mut_p(ctx["db"], ctx["cid"])
            assert active_before >= 1

            resp = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets/{ctx['budget_id']}/revoke",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            active_after = await _count_active_mut_p(ctx["db"], ctx["cid"])
            assert active_after == 0, (
                f"REGRESSION iter90ch : {active_after} OD MUT-P active(s) apres "
                f"devalidation du budget. Attendu : 0."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : delete-all-fund-calls -> OD MUT-P contre-passees
# =========================================================================
async def _scenario_delete_all_reverses_mut_p():
    ctx = await _base_setup("iter90ch-all")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            await _mutate(client, ctx, "2026-05-15")
            await _create_call_via_budget(client, ctx)

            active_before = await _count_active_mut_p(ctx["db"], ctx["cid"])
            assert active_before >= 1

            resp = await client.post(
                f"{BACKEND_URL}/api/fund-calls/delete-all",
                headers=hdr,
                params={"copropriete_id": ctx["cid"]},
            )
            assert resp.status_code == 200, resp.text

            active_after = await _count_active_mut_p(ctx["db"], ctx["cid"])
            assert active_after == 0, (
                f"REGRESSION iter90ch : {active_after} OD MUT-P active(s) apres "
                f"delete-all-fund-calls. Attendu : 0."
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_delete_budget_reverses_mut_p():
    asyncio.run(_scenario_delete_budget_reverses_mut_p())


def test_revoke_budget_reverses_mut_p():
    asyncio.run(_scenario_revoke_budget_reverses_mut_p())


def test_delete_all_reverses_mut_p():
    asyncio.run(_scenario_delete_all_reverses_mut_p())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
