"""
iter90cr : REGRESSION LOCK - Fallback distribution quand la cle contient
uniquement des entrees phantom (lot_ids ne correspondant a aucun lot en DB).

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "Dans la logique le premier proprietaire est toujours considere comme
> proprietaire a la date du 1er jour de l'exercice. Des lors Matexi est
> attribue a tous les lots donc devrait donc etre considere comme
> proprietaire au 1er jour de l'exercice 01,10,2025 dans ce cas"

Contexte : Les 30 lots existants dans l'UI ont bien Matexi comme proprietaire.
Mais la cle "Charges communes generales" contient 30 entrees phantom
(anciens lot_ids supprimes). Le wizard affichait "30 lots orphan (100%)"
et "0 proprietaires" car `total_shares = 0` -> aucune ligne generee.

Fix iter90cr (`fund_calls.py::_distribute_amount`) :
- Detecte quand `owned_kls == []` OU `total_shares <= 0` apres filtrage.
- Fallback : distribution par quotites sur les lots ACTUELS avec owner.
- Preserve le comportement normal quand la cle a des entrees valides.

Test coverage :
1. Cle 100% phantom -> fallback par quotites (Matexi recoit tout).
2. Cle 50% phantom + 50% valide -> distribution SUR LES 50% VALIDES seuls
   (comportement iter90cb inchange).
3. Cle sans phantoms -> distribution normale (regression).
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


async def _setup_acacia_like(prefix: str, key_lots_config: list):
    """Cree ACP + FY + 2 owners + 3 lots Matexi + budget + cle configurable.

    key_lots_config : liste de tuples (lot_ref, share, is_phantom).
      lot_ref = "L1" | "L2" | "L3" pour reels, ou "P1" | "P2" | ... pour phantoms.
    """
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = str(uuid.uuid4())
    matexi_id = f"matexi-{uuid.uuid4()}"
    dewinter_id = f"dew-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "class_num": 1, "copropriete_id": cid, "name": "Roulement"},
        {"number": "160", "class_num": 1, "copropriete_id": cid, "name": "Reserve"},
        {"number": "400000", "class_num": 4, "copropriete_id": cid, "name": "Prov"},
        {"number": "4100021", "class_num": 4, "copropriete_id": cid, "name": "T-Matexi"},
        {"number": "4100022", "class_num": 4, "copropriete_id": cid, "name": "T-Dewinter"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid, "name": "VE"},
        {"number": "61", "class_num": 6, "copropriete_id": cid, "name": "Charges"},
    ])
    await db.owners.insert_many([
        {"id": matexi_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100021"}}},
        {"id": dewinter_id, "name": "Dewinter", "last_name": "Dewinter",
         "auxiliary_code": "D001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100022"}}},
    ])
    # 3 lots reels, tous chez Matexi
    L1_id = f"lot-{uuid.uuid4()}"
    L2_id = f"lot-{uuid.uuid4()}"
    L3_id = f"lot-{uuid.uuid4()}"
    real_lot_ids = {"L1": L1_id, "L2": L2_id, "L3": L3_id}
    await db.lots.insert_many([
        {"id": L1_id, "number": "001", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3000.0},
        {"id": L2_id, "number": "002", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
        {"id": L3_id, "number": "003", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
    ])
    # Construire la cle avec config
    key_id = str(uuid.uuid4())
    key_lots = []
    for lot_ref, share, is_phantom in key_lots_config:
        if is_phantom:
            key_lots.append({
                "lot_id": f"phantom-{uuid.uuid4()}",
                "share": share, "lot_number": f"phantom-{lot_ref}",
            })
        else:
            key_lots.append({
                "lot_id": real_lot_ids[lot_ref], "share": share,
                "lot_number": lot_ref,
            })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Test key",
        "is_default": True, "key_type": "quotity", "lots": key_lots,
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "key_id": key_id,
        "matexi": matexi_id, "dewinter": dewinter_id,
        "L1": L1_id, "L2": L2_id, "L3": L3_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["matexi"], ctx["dewinter"]]}})


async def _preview_call(client, hdr, ctx, budget_id):
    """POST /api/fund-calls/preview-from-budget pour analyser la distribution."""
    r = await client.post(f"{BACKEND_URL}/api/fund-calls/preview-from-budget",
                          headers=hdr, json={
        "budget_id": budget_id,
        "copropriete_id": ctx["cid"],
        "frequency": 4,
        "start_date": "2026-01-01",
        "due_offset_days": 30,
        "reserve_fund": {"enabled": False, "amount": 0.0},
        "roulement_fund": {"enabled": False, "amount": 0.0},
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _create_budget(client, hdr, ctx, key_id):
    r = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
        "fiscal_year_id": ctx["fy_id"],
        "name": "Budget test",
        "copropriete_id": ctx["cid"],
        "lines": [{"account_number": "61", "account_name": "Charges",
                   "amount": 4000.0, "distribution_key_id": key_id}],
        "reserve_fund_amount": 0.0,
        "roulement_fund_amount": 0.0,
    })
    assert r.status_code == 200, r.text
    b = r.json()
    r = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets/{b['id']}/approve", headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


# =========================================================================
# SCENARIO 1 : Cle 100% phantom -> fallback par quotites (Matexi 100%)
# =========================================================================
async def _scenario_100pct_phantom_fallback():
    ctx = await _setup_acacia_like("iter90cr-100", [
        ("PX1", 3000.0, True),
        ("PX2", 3500.0, True),
        ("PX3", 3500.0, True),
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx, ctx["key_id"])
            resp = await _preview_call(client, hdr, ctx, b["id"])

            # Verifie qu'au moins 1 appel a des distributions
            calls = resp.get("calls") or []
            assert calls, f"Aucun appel prevu : {resp}"
            first_call = calls[0]
            total = first_call.get("total_amount", 0.0)
            assert total > 0, f"Total appel 0 : {first_call}"

            # Verifie que la distribution est appliquee (au moins 1 owner)
            distribution = first_call.get("distribution") or []
            assert distribution, (
                "REGRESSION iter90cr : cle 100% phantom devrait tomber sur le "
                "fallback quotites et generer une distribution. Obtenu : []"
            )

            # Verifie que Matexi recoit 100% (seul owner)
            total_dist = sum(e.get("amount", 0.0) for e in distribution)
            matexi_dist = sum(e.get("amount", 0.0) for e in distribution
                              if e.get("owner_id") == ctx["matexi"])
            assert abs(matexi_dist - total_dist) < 0.01, (
                f"Matexi devrait recevoir 100% ({total_dist}). "
                f"Obtenu : matexi={matexi_dist}, total={total_dist}"
            )
            assert abs(total_dist - total) < 0.01, (
                f"La somme des amounts ({total_dist}) doit etre egale au "
                f"total_amount de l'appel ({total})"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Cle 50% phantom + 50% valide -> distribution sur les 50%
# valides seulement (comportement iter90cb inchange, PAS de fallback complet)
# =========================================================================
async def _scenario_50pct_phantom_no_fallback():
    ctx = await _setup_acacia_like("iter90cr-50", [
        ("L1", 3000.0, False),   # Matexi (reel)
        ("PX1", 3500.0, True),   # phantom
        ("L3", 3500.0, False),   # Matexi (reel)
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx, ctx["key_id"])
            resp = await _preview_call(client, hdr, ctx, b["id"])

            first_call = resp.get("calls", [{}])[0]
            distribution = first_call.get("distribution") or []
            assert distribution, "Distribution vide"

            # Seuls L1 et L3 doivent apparaitre (L2 non present, phantom exclus)
            lot_ids = {e.get("lot_id") for e in distribution}
            assert ctx["L1"] in lot_ids and ctx["L3"] in lot_ids, (
                f"L1 et L3 doivent etre dans la distribution. Obtenu : {lot_ids}"
            )
            assert ctx["L2"] not in lot_ids, (
                "L2 ne devrait pas apparaitre : n'est pas dans la cle "
                "(pas de fallback quand cle a des entrees valides)"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : Cle sans phantoms -> distribution normale (regression)
# =========================================================================
async def _scenario_no_phantom_regression():
    ctx = await _setup_acacia_like("iter90cr-clean", [
        ("L1", 3000.0, False),
        ("L2", 3500.0, False),
        ("L3", 3500.0, False),
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx, ctx["key_id"])
            resp = await _preview_call(client, hdr, ctx, b["id"])

            first_call = resp.get("calls", [{}])[0]
            distribution = first_call.get("distribution") or []
            lot_ids = {e.get("lot_id") for e in distribution}
            assert lot_ids == {ctx["L1"], ctx["L2"], ctx["L3"]}, (
                f"Distribution attendue sur L1+L2+L3. Obtenu : {lot_ids}"
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_100pct_phantom_key_fallbacks_to_quotity():
    asyncio.run(_scenario_100pct_phantom_fallback())


def test_partial_phantom_key_uses_valid_entries_only():
    asyncio.run(_scenario_50pct_phantom_no_fallback())


def test_no_phantom_key_distributes_normally():
    asyncio.run(_scenario_no_phantom_regression())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
