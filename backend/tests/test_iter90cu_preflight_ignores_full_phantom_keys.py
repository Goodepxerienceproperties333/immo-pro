"""
iter90cu : REGRESSION LOCK - Preflight orphan check et ownership-at-date
doivent ignorer les cles 100% phantom (fallback iter90cr resout la distrib).

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "en production ca ne fonctionne toujours pas ce truc il faut considerer
> le premier proprietaire a la date de debut d'exercice"

Contexte : L'audit ownership modal confirme que les 30 lots ont Matexi
comme proprietaire actuel ET a la date 01/10/2025 (colonne verte). Mais
le BudgetWizard affiche encore :
- "30 lot(s) sans proprietaire assigne a la date d'appel" (rouge)
- "30 lot(s) orphelin(s) detecte(s) (100% des shares)" (jaune)

Cause : les cles "Charges communes generales" et "Charges ascenseurs"
ont TOUTES leurs entrees en phantom (10000/10000 shares). Le preflight
flagge chaque phantom comme error, alors que iter90cr fallback distribue
correctement sur les lots reels de l'ACP.

Fix iter90cu :
- `_detect_lots_unresolved_at_dates` : si un phantom appartient a une cle
  100% phantom, l'ignore (fallback iter90cr).
- `_detect_orphan_lots_for_budget` : si une cle est 100% phantom, skip
  la detection orphan (fallback distribue correctement).
- Ajoute `phantom_keys_fallback` field a la reponse (info soft, count des
  cles concernees).

Test coverage :
1. Cle 100% phantom + lots reels avec Matexi -> unresolved_count=0 + info
2. Cle partiellement phantom -> phantom lots flagged en warnings
3. Cle propre -> pas de warning ni info (regression normale)
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


async def _setup(prefix: str, key_config: list):
    """ACP + 3 lots Matexi + cle configurable + budget.

    key_config : liste de dict {lot_ref, share, is_phantom, lot_number}
    """
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = str(uuid.uuid4())
    matexi_id = f"matexi-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "61", "class_num": 6, "copropriete_id": cid, "name": "Charges"},
        {"number": "400000", "class_num": 4, "copropriete_id": cid, "name": "Prov"},
        {"number": "4100021", "class_num": 4, "copropriete_id": cid, "name": "T-Matexi"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid, "name": "VE"},
    ])
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "last_name": "Matexi",
        "auxiliary_code": "M001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "4100021"}},
    })

    L1_id = f"lot-{uuid.uuid4()}"
    L2_id = f"lot-{uuid.uuid4()}"
    L3_id = f"lot-{uuid.uuid4()}"
    real_map = {"L1": (L1_id, "001"), "L2": (L2_id, "002"), "L3": (L3_id, "003")}
    await db.lots.insert_many([
        {"id": L1_id, "number": "001", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3000.0},
        {"id": L2_id, "number": "002", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
        {"id": L3_id, "number": "003", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
    ])

    key_id = str(uuid.uuid4())
    key_lots = []
    for cfg in key_config:
        if cfg.get("is_phantom"):
            key_lots.append({
                "lot_id": f"phantom-{uuid.uuid4()}",
                "share": cfg["share"], "lot_number": cfg.get("lot_number", ""),
            })
        else:
            lid, num = real_map[cfg["lot_ref"]]
            key_lots.append({"lot_id": lid, "share": cfg["share"], "lot_number": num})
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Charges communes generales",
        "is_default": True, "key_type": "quotity", "lots": key_lots,
    })

    return {
        "db": db, "cid": cid, "fy_id": fy_id, "key_id": key_id,
        "matexi": matexi_id,
        "L1": L1_id, "L2": L2_id, "L3": L3_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "budgets",
                 "distribution_keys"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_one({"id": ctx["matexi"]})


async def _create_budget(client, hdr, ctx):
    r = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
        "fiscal_year_id": ctx["fy_id"],
        "name": "Budget test",
        "copropriete_id": ctx["cid"],
        "lines": [{"account_number": "61", "amount": 4000.0,
                   "distribution_key_id": ctx["key_id"]}],
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


async def _preflight(client, hdr, ctx, budget_id):
    r = await client.post(f"{BACKEND_URL}/api/fund-calls/preflight-orphan-check",
                          headers=hdr, json={
        "budget_id": budget_id, "copropriete_id": ctx["cid"],
        "frequency": 4, "start_date": "2026-01-01", "due_offset_days": 30,
        "reserve_fund": {"enabled": False, "amount": 0.0},
        "roulement_fund": {"enabled": False, "amount": 0.0},
    })
    assert r.status_code == 200, r.text
    return r.json()


# =========================================================================
# SCENARIO 1 : Cle 100% phantom - preflight silence les alertes
# =========================================================================
async def _scenario_100pct_phantom_key_silences_alerts():
    ctx = await _setup("iter90cu-100", [
        {"share": 3000.0, "is_phantom": True, "lot_number": "001"},
        {"share": 3500.0, "is_phantom": True, "lot_number": "002"},
        {"share": 3500.0, "is_phantom": True, "lot_number": "003"},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx)
            resp = await _preflight(client, hdr, ctx, b["id"])

            # ownership_at_date_warning : phantom lots doivent etre ignores
            unresolved = resp["ownership_at_date_warning"]["unresolved_count"]
            assert unresolved == 0, (
                f"REGRESSION iter90cu : ownership_at_date_warning devrait etre "
                f"0 (cle 100% phantom, fallback iter90cr). Obtenu : {unresolved}"
            )
            # info soft doit etre presente
            phantom_info = resp["ownership_at_date_warning"].get("phantom_keys_fallback", {})
            assert phantom_info.get("count", 0) >= 1, (
                f"iter90cu : phantom_keys_fallback devrait indiquer 1 cle. "
                f"Obtenu : {phantom_info}"
            )

            # orphan_lots_warning : orphan_count devrait etre 0 aussi
            orphan_count = resp["orphan_lots_warning"]["orphan_count"]
            assert orphan_count == 0, (
                f"REGRESSION iter90cu : orphan_count devrait etre 0 pour cle "
                f"100% phantom. Obtenu : {orphan_count}"
            )
            phantom_fallback = resp["orphan_lots_warning"].get("phantom_keys_fallback", [])
            assert len(phantom_fallback) >= 1, (
                f"iter90cu : orphan_lots_warning.phantom_keys_fallback devrait "
                f"inclure la cle. Obtenu : {phantom_fallback}"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Cle partiellement phantom - phantom entries flaggees en rouge
# =========================================================================
async def _scenario_partial_phantom_still_warns():
    ctx = await _setup("iter90cu-partial", [
        {"lot_ref": "L1", "share": 3000.0, "is_phantom": False},
        {"share": 3500.0, "is_phantom": True, "lot_number": "002"},
        {"lot_ref": "L3", "share": 3500.0, "is_phantom": False},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx)
            resp = await _preflight(client, hdr, ctx, b["id"])

            # Le phantom middle doit rester flagge (cle n'est pas 100% phantom)
            unresolved = resp["ownership_at_date_warning"]["unresolved_count"]
            assert unresolved >= 1, (
                f"iter90cu : phantom partiel doit toujours flagger "
                f"(cle n'est PAS 100% phantom). Obtenu : {unresolved}"
            )
            # Aucune cle 100% phantom dans le fallback
            phantom_info = resp["ownership_at_date_warning"].get("phantom_keys_fallback", {})
            assert phantom_info.get("count", 0) == 0, (
                f"iter90cu : phantom partiel NE doit PAS declencher fallback. "
                f"Obtenu : {phantom_info}"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : Cle 100% valide - regression normale
# =========================================================================
async def _scenario_clean_key_no_warnings():
    ctx = await _setup("iter90cu-clean", [
        {"lot_ref": "L1", "share": 3000.0, "is_phantom": False},
        {"lot_ref": "L2", "share": 3500.0, "is_phantom": False},
        {"lot_ref": "L3", "share": 3500.0, "is_phantom": False},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            b = await _create_budget(client, hdr, ctx)
            resp = await _preflight(client, hdr, ctx, b["id"])

            assert resp["ownership_at_date_warning"]["unresolved_count"] == 0
            assert resp["orphan_lots_warning"]["orphan_count"] == 0
            phantom_info = resp["ownership_at_date_warning"].get("phantom_keys_fallback", {})
            assert phantom_info.get("count", 0) == 0
    finally:
        await _cleanup(ctx)


def test_100pct_phantom_key_silences_alerts():
    asyncio.run(_scenario_100pct_phantom_key_silences_alerts())


def test_partial_phantom_still_warns():
    asyncio.run(_scenario_partial_phantom_still_warns())


def test_clean_key_no_warnings():
    asyncio.run(_scenario_clean_key_no_warnings())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
