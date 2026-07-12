"""iter90du - Fund call fallback should use key.shares (matched by lot_number)
when key entries reference phantom lot_ids but lot_numbers match.

User's bug report (Feb 2026, PROD ACP Acacia):
- Budget 19 000E annual -> 4 750E/quarter
- Distribution key has 3 phantom entries with shares (898, 34, 11) and
  lot_numbers "001", "002", "101"
- Current lots (with matching lot_numbers "001", "002", "101") have quotity=0
  (imported without quotity values)
- Expected: 4750 x 943/10000 = 447.93E for the 3 lots combined
- Bug: system distributes equally (4750/30 per lot = 475E for 3 lots)

Root cause: iter90cr fallback used lot.quotity which was 0 for all lots.
_snap_distribution_to_total then redistributed the entire amount equally.

Fix iter90du: Match phantom key entries to current lots by lot_number
(normalized, lstrip zeros) BEFORE falling back to lot.quotity. This
preserves the key.share as source of truth after re-import.
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
_TOKEN_CACHE = {"token": None, "cookies": None}


async def _login(client):
    if _TOKEN_CACHE["cookies"]:
        return _TOKEN_CACHE["cookies"]
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    _TOKEN_CACHE["cookies"] = dict(resp.cookies)
    return _TOKEN_CACHE["cookies"]


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_phantom_key_with_matching_numbers(prefix: str):
    """Cree une ACP avec:
    - 3 lots reels (numbers 001, 002, 101) et quotity=0
    - 1 lot supplementaire (number 999) pour representer le reste
    - Une cle avec entrees PHANTOM (lot_ids inexistants) mais lot_number
      correspondant aux 3 premiers lots. Shares = 898/34/11.
    - Un budget annuel 19000E
    Simule le cas PROD ACP Acacia apres re-import Optipro.
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
        {"number": "100", "class_num": 1, "copropriete_id": cid, "name": "Roulement"},
        {"number": "160", "class_num": 1, "copropriete_id": cid, "name": "Reserve"},
        {"number": "400000", "class_num": 4, "copropriete_id": cid, "name": "Prov"},
        {"number": "4100021", "class_num": 4, "copropriete_id": cid, "name": "T-Matexi"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid, "name": "VE"},
        {"number": "61", "class_num": 6, "copropriete_id": cid, "name": "Charges"},
    ])
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "last_name": "Matexi",
        "auxiliary_code": "M001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "4100021"}},
    })

    # 3 lots reels (numbers 001, 002, 101) - QUOTITY=0 (imported without quotites)
    L1_id = f"lot-{uuid.uuid4()}"  # number 001
    L2_id = f"lot-{uuid.uuid4()}"  # number 002
    L3_id = f"lot-{uuid.uuid4()}"  # number 101
    # + 1 lot 999 pour representer les autres charges (pas dans la cle)
    L_other_id = f"lot-{uuid.uuid4()}"
    await db.lots.insert_many([
        {"id": L1_id, "number": "001", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 0},
        {"id": L2_id, "number": "002", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 0},
        {"id": L3_id, "number": "101", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 0},
        {"id": L_other_id, "number": "999", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 0},
    ])

    # Distribution key: 3 PHANTOM lot_ids, but lot_numbers match real lots
    key_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid,
        "name": "Charges Generales", "code": "GEN",
        "is_default": True, "key_type": "quotity",
        "lots": [
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "001", "share": 898.0},
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "002", "share": 34.0},
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "101", "share": 11.0},
        ],
    })

    return {
        "db": db, "cid": cid, "fy_id": fy_id, "key_id": key_id,
        "matexi": matexi_id, "L1": L1_id, "L2": L2_id, "L3": L3_id,
        "L_other": L_other_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["matexi"]})


async def _preview_call(client, cookies, ctx, budget_id):
    r = await client.post(
        f"{BACKEND_URL}/api/fund-calls/preview-from-budget",
        cookies=cookies, json={
            "budget_id": budget_id, "copropriete_id": ctx["cid"],
            "frequency": 4, "start_date": "2026-01-01",
            "due_offset_days": 30,
            "reserve_fund": {"enabled": False, "amount": 0.0},
            "roulement_fund": {"enabled": False, "amount": 0.0},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _create_budget(client, cookies, ctx, key_id, annual_amount):
    r = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets", cookies=cookies, json={
            "fiscal_year_id": ctx["fy_id"], "name": "Budget test",
            "copropriete_id": ctx["cid"],
            "lines": [{"account_number": "61", "account_name": "Charges",
                       "amount": annual_amount, "distribution_key_id": key_id}],
            "reserve_fund_amount": 0.0, "roulement_fund_amount": 0.0,
        },
    )
    assert r.status_code == 200, r.text
    b = r.json()
    r = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets/{b['id']}/approve", cookies=cookies,
    )
    assert r.status_code == 200, r.text
    return r.json()


# =========================================================================
# SCENARIO 1: Phantom key entries with matching lot_numbers -> use key.share
# (user bug: 19000/4 x 943/10000 = 447.93 for 3 lots, NOT 475 equal-split)
# =========================================================================
async def _scenario_phantom_key_matches_by_lot_number():
    ctx = await _setup_phantom_key_with_matching_numbers("iter90du-user")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            b = await _create_budget(client, cookies, ctx, ctx["key_id"], 19000.0)
            resp = await _preview_call(client, cookies, ctx, b["id"])

            calls = resp.get("calls") or []
            assert calls, f"Aucun appel : {resp}"
            first_call = calls[0]
            total = first_call.get("total_amount", 0.0)
            assert abs(total - 4750.0) < 0.01, (
                f"Total appel doit etre 19000/4 = 4750, obtenu {total}"
            )

            distribution = first_call.get("distribution") or []
            assert len(distribution) == 3, (
                f"iter90du : la cle a 3 entrees phantom avec lot_numbers "
                f"matchant 3 lots reels -> distribution doit avoir 3 lignes. "
                f"Obtenu : {len(distribution)} lignes."
            )

            # Verifie que les shares sont bien 898/34/11 (source de verite)
            by_num = {d["lot_number"]: d for d in distribution}
            assert "001" in by_num, f"Lot 001 absent: {distribution}"
            assert by_num["001"]["share"] == 898.0, by_num["001"]
            assert by_num["002"]["share"] == 34.0, by_num["002"]
            assert by_num["101"]["share"] == 11.0, by_num["101"]

            # Verifie les montants proportionnels aux shares (943 total)
            # Lot 001: 4750 x 898/943 = 4523.75 (car total_shares=943 dans la cle)
            # Lot 002: 4750 x 34/943 = 171.30
            # Lot 101: 4750 x 11/943 = 55.42
            # (avec _snap adjust les centimes pour matcher call_total=4750)
            total_dist = sum(d["amount"] for d in distribution)
            assert abs(total_dist - 4750.0) < 0.01, (
                f"Somme distribution ({total_dist}) doit etre = call_total (4750)"
            )
            # Bug user: distribution equale = 475 pour les 3 lots
            # Fix iter90du: chaque lot proportionnel a sa share
            # Le lot 001 doit avoir la plus grosse part (898/943 = 95.2%)
            lot_001_amount = by_num["001"]["amount"]
            assert lot_001_amount > 4000, (
                f"iter90du : lot 001 avec share 898 doit avoir grosse part "
                f"(~4523.75 = 95.2%). Obtenu {lot_001_amount}. "
                f"Bug : distribution equale donne 1583.33 (4750/3)."
            )
            # Lot 101 (share 11) doit avoir petite part
            lot_101_amount = by_num["101"]["amount"]
            assert lot_101_amount < 100, (
                f"iter90du : lot 101 avec share 11 doit avoir petite part "
                f"(~55.42). Obtenu {lot_101_amount}."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2: Phantom key with NON-matching lot_numbers -> fallback quotity
# (regression iter90cr : cas oil aucun match possible)
# =========================================================================
async def _scenario_phantom_key_no_match_fallback_to_quotity():
    """Si les lot_numbers de la cle ne matchent aucun lot actuel,
    on retombe sur le fallback par quotity (iter90cr original)."""
    db = await _mongo()
    cid = f"iter90du-nomatch-{uuid.uuid4()}"
    fy_id = str(uuid.uuid4())
    matexi_id = f"matexi-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "T", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "class_num": 1, "copropriete_id": cid, "name": "Roulement"},
        {"number": "400000", "class_num": 4, "copropriete_id": cid, "name": "P"},
        {"number": "4100021", "class_num": 4, "copropriete_id": cid, "name": "T-M"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid, "name": "VE"},
        {"number": "61", "class_num": 6, "copropriete_id": cid, "name": "C"},
    ])
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "auxiliary_code": "M",
        "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100021"}},
    })
    L1_id = f"lot-{uuid.uuid4()}"
    await db.lots.insert_one({
        "id": L1_id, "number": "AAA-42", "owner_id": matexi_id,
        "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 5000.0,
    })
    key_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "K",
        "is_default": True, "key_type": "quotity",
        "lots": [
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "999", "share": 500.0},
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "888", "share": 500.0},
        ],
    })
    ctx = {"db": db, "cid": cid, "fy_id": fy_id, "key_id": key_id,
           "matexi": matexi_id}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            b = await _create_budget(client, cookies, ctx, key_id, 4000.0)
            resp = await _preview_call(client, cookies, ctx, b["id"])
            calls = resp.get("calls") or []
            first_call = calls[0]
            distribution = first_call.get("distribution") or []
            # Aucun match par lot_number -> fallback par quotity : L1 seul
            assert len(distribution) == 1, (
                f"Fallback quotity attendu (aucun match). Obtenu {len(distribution)}"
            )
            # L1 recoit tout (seul lot avec quotity > 0)
            assert distribution[0]["lot_id"] == L1_id
            assert abs(distribution[0]["amount"] - 1000.0) < 0.01
    finally:
        await _cleanup(ctx)


# =========================================================================
# Entry points
# =========================================================================
def test_phantom_key_matches_by_lot_number_uses_key_shares():
    asyncio.run(_scenario_phantom_key_matches_by_lot_number())


def test_phantom_key_no_match_falls_back_to_quotity():
    asyncio.run(_scenario_phantom_key_no_match_fallback_to_quotity())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
