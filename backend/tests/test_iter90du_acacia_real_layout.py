"""Verifie que iter90du gere correctement les lot_numbers avec lettres (Acacia reel).
Cas cible: Cle "Charges communes" (0001) avec 30 lots dont
- Appartements: "001", "002", "101", "102", "103", "201", "202", "203", "301", "302"
- Caves: "C1", "C2", ..., "C10"
- Parking Ext.: "Pe01", "Pe02", ..., "Pe10"

Verifie que le matching phantom -> current par lot_number normalise fonctionne
sur tous ces formats (numeriques, lettres+chiffres, prefixes).
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


async def _scenario_acacia_real_layout():
    """Simule EXACTEMENT le layout Acacia PDF utilisateur:
    - 30 lots: 10 appt + 10 caves + 10 parking ext.
    - Cle "Charges communes" avec 30 entrees phantom totalisant 10 000
    - Owner C2612 possede: Lot 001 (898) + Cave C1 (11) + Parking Pe01 (34) = 943
    - Budget 19 000E annual (Q1 = 4750 EUR)
    - Attendu: C2612 total pour ses 3 lots = 4750 x 943/10000 = 447.925 EUR
    """
    db = await _mongo()
    cid = f"iter90du-acacia-real-{uuid.uuid4()}"
    fy_id = str(uuid.uuid4())

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia Real", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "400000", "class_num": 4, "copropriete_id": cid, "name": "P"},
        {"number": "4100021", "class_num": 4, "copropriete_id": cid, "name": "T-2612"},
        {"number": "4100022", "class_num": 4, "copropriete_id": cid, "name": "T-2613"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid, "name": "VE"},
        {"number": "61", "class_num": 6, "copropriete_id": cid, "name": "C"},
    ])
    # 10 owners (un par code C2612..C2620 + C2941)
    owner_ids = {}
    for code in ["C2612", "C2613", "C2614", "C2615", "C2616",
                 "C2617", "C2618", "C2619", "C2620", "C2941"]:
        oid = f"owner-{code}-{uuid.uuid4().hex[:6]}"
        owner_ids[code] = oid
        await db.owners.insert_one({
            "id": oid, "name": f"Owner {code}", "auxiliary_code": code,
            "copropriete_ids": [cid],
            "tier_accounts": {cid: {"provisions": "4100021"}},
        })

    # Layout exact du PDF utilisateur (30 lots, 10000 total shares)
    # (lot_number, share, owner_code)
    layout = [
        # Appartements (sum = 9541)
        ("001", 898, "C2612"),
        ("002", 1095, "C2613"),
        ("101", 692, "C2614"),
        ("102", 1197, "C2615"),
        ("103", 968, "C2616"),
        ("201", 693, "C2617"),
        ("202", 1009, "C2618"),
        ("203", 969, "C2619"),
        ("301", 1287, "C2620"),
        ("302", 733, "C2941"),
        # Caves (sum = 119)
        ("C1", 11, "C2612"),
        ("C2", 10, "C2613"),
        ("C3", 10, "C2616"),
        ("C4", 11, "C2620"),
        ("C5", 15, "C2614"),
        ("C6", 11, "C2617"),
        ("C7", 11, "C2619"),
        ("C8", 13, "C2618"),
        ("C9", 16, "C2615"),
        ("C10", 11, "C2941"),
        # Parking Ext. (sum = 340)
        ("Pe01", 34, "C2612"),
        ("Pe02", 34, "C2613"),
        ("Pe03", 34, "C2614"),
        ("Pe04", 34, "C2615"),
        ("Pe05", 34, "C2619"),
        ("Pe06", 34, "C2617"),
        ("Pe07", 34, "C2618"),
        ("Pe08", 34, "C2941"),
        ("Pe09", 34, "C2620"),
        ("Pe10", 34, "C2616"),
    ]
    assert sum(s for _, s, _ in layout) == 10000

    # Cree les 30 lots avec quotity=0 (import Optipro sans quotites)
    lot_ids_by_num = {}
    lots_docs = []
    for lot_num, _share, owner_code in layout:
        lid = f"lot-{lot_num}-{uuid.uuid4().hex[:6]}"
        lot_ids_by_num[lot_num] = lid
        lots_docs.append({
            "id": lid, "number": lot_num,
            "owner_id": owner_ids[owner_code],
            "owner_ids": [owner_ids[owner_code]],
            "copropriete_id": cid, "quotity": 0,
        })
    await db.lots.insert_many(lots_docs)

    # Cle avec 30 entrees phantom (lot_ids inexistants) mais lot_numbers matchant
    key_id = str(uuid.uuid4())
    key_lots = []
    for lot_num, share, _ in layout:
        key_lots.append({
            "lot_id": f"phantom-{uuid.uuid4()}",  # phantom - n'existe pas
            "lot_number": lot_num,
            "share": float(share),
        })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid,
        "name": "0001 - Charges communes", "code": "0001",
        "is_default": True, "key_type": "quotity",
        "lots": key_lots,
    })

    ctx = {"db": db, "cid": cid, "fy_id": fy_id, "key_id": key_id}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)

            # Cree budget 19000
            r = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets", cookies=cookies, json={
                    "fiscal_year_id": fy_id, "name": "Budget 2026",
                    "copropriete_id": cid,
                    "lines": [{
                        "account_number": "61", "account_name": "Charges",
                        "amount": 19000.0, "distribution_key_id": key_id,
                    }],
                    "reserve_fund_amount": 0.0, "roulement_fund_amount": 0.0,
                },
            )
            assert r.status_code == 200, r.text
            b_id = r.json()["id"]
            r = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets/{b_id}/approve",
                cookies=cookies,
            )
            assert r.status_code == 200, r.text

            r = await client.post(
                f"{BACKEND_URL}/api/fund-calls/preview-from-budget",
                cookies=cookies, json={
                    "budget_id": b_id, "copropriete_id": cid,
                    "frequency": 4, "start_date": "2026-01-01",
                    "due_offset_days": 30,
                    "reserve_fund": {"enabled": False, "amount": 0.0},
                    "roulement_fund": {"enabled": False, "amount": 0.0},
                },
            )
            assert r.status_code == 200, r.text
            resp = r.json()

            calls = resp.get("calls") or []
            first_call = calls[0]
            assert abs(first_call["total_amount"] - 4750.0) < 0.01

            distribution = first_call.get("distribution") or []
            assert len(distribution) == 30, (
                f"30 lots attendus (10 appt + 10 cave + 10 parking), "
                f"obtenu {len(distribution)}"
            )

            by_num = {d["lot_number"]: d for d in distribution}

            # Vérifie que tous les 30 lots sont présents (matching par lot_number OK)
            all_numbers = {lot_num for lot_num, _, _ in layout}
            actual_numbers = set(by_num.keys())
            assert all_numbers == actual_numbers, (
                f"Missing lots: {all_numbers - actual_numbers}. "
                f"Extra: {actual_numbers - all_numbers}"
            )

            # Verification des 3 lots de C2612 (le cas user)
            # Lot 001: 4750 x 898/10000 = 426.55
            assert abs(by_num["001"]["amount"] - 426.55) < 0.5, by_num["001"]
            # Lot Pe01: 4750 x 34/10000 = 16.15
            assert abs(by_num["Pe01"]["amount"] - 16.15) < 0.5, by_num["Pe01"]
            # Lot C1: 4750 x 11/10000 = 5.225 -> 5.23
            assert abs(by_num["C1"]["amount"] - 5.23) < 0.5, by_num["C1"]

            # Total C2612 = 447.925 (attendu ~447.93 avec snap)
            c2612_id = owner_ids["C2612"]
            c2612_total = sum(
                d["amount"] for d in distribution
                if d.get("owner_id") == c2612_id
            )
            assert abs(c2612_total - 447.925) < 1.0, (
                f"iter90du: C2612 doit avoir 447.93 EUR (943/10000 x 4750). "
                f"Obtenu: {c2612_total}. Bug attendu: 475 EUR (equal-split)."
            )

            # Non-regression: somme totale = call_total
            total_dist = sum(d["amount"] for d in distribution)
            assert abs(total_dist - 4750.0) < 0.01

    finally:
        await db.coproprietes.delete_one({"id": cid})
        for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                     "journal_entries", "distribution_keys", "mutations",
                     "budgets"):
            await db[coll].delete_many({"copropriete_id": cid})
        await db.owners.delete_many({"id": {"$in": list(owner_ids.values())}})


def test_acacia_real_layout_30_lots_letters_and_prefixes():
    asyncio.run(_scenario_acacia_real_layout())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
