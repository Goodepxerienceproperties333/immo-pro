"""iter90eo : Option "arrondir a l'euro superieur" sur reserve/roulement.

Quand `round_up=true` :
- Chaque quote-part par lot est arrondie via `math.ceil` au EUR entier
- La somme reelle depasse legerement le montant vote
- L'exces alimente le fonds (surprovision utilisable)
- Le champ `line_details[*].round_up` est propage sur les appels generes
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return {}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed(db, suffix):
    """Setup : 3 lots avec quotites 100/1000, 1 fiscal year, 1 budget."""
    cid = f"iter90eo-{suffix}"
    fy_id = f"iter90eo-fy-{suffix}"
    bud_id = f"iter90eo-bud-{suffix}"
    o1, o2, o3 = f"o1-{suffix}", f"o2-{suffix}", f"o3-{suffix}"
    l1, l2, l3 = f"l1-{suffix}", f"l2-{suffix}", f"l3-{suffix}"
    k = f"k-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "iter90eo ACP"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "700000", "name": "Prov", "copropriete_id": cid, "class_num": 7},
        {"number": "160", "name": "Reserve", "copropriete_id": cid, "class_num": 1},
        {"number": "100", "name": "Roulement", "copropriete_id": cid, "class_num": 1},
        {"number": "41010001", "name": "T1", "copropriete_id": cid, "class_num": 4},
        {"number": "41010002", "name": "T2", "copropriete_id": cid, "class_num": 4},
        {"number": "41010003", "name": "T3", "copropriete_id": cid, "class_num": 4},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "Alice", "last_name": "A",
         "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41010001"}}},
        {"id": o2, "name": "Bob", "last_name": "B",
         "tier_accounts": {cid: {"provisions": "41010002", "reserve": "41010002"}}},
        {"id": o3, "name": "Carol", "last_name": "C",
         "tier_accounts": {cid: {"provisions": "41010003", "reserve": "41010003"}}},
    ])
    # Lots avec quotites qui donnent des decimales sur 1000
    await db.lots.insert_many([
        {"id": l1, "number": "1", "copropriete_id": cid, "owner_id": o1, "quotity": 333},
        {"id": l2, "number": "2", "copropriete_id": cid, "owner_id": o2, "quotity": 333},
        {"id": l3, "number": "3", "copropriete_id": cid, "owner_id": o3, "quotity": 334},
    ])
    await db.distribution_keys.insert_one({
        "id": k, "copropriete_id": cid, "name": "Charges",
        "is_default": True,
        "lots": [
            {"lot_id": l1, "lot_number": "1", "share": 333},
            {"lot_id": l2, "lot_number": "2", "share": 333},
            {"lot_id": l3, "lot_number": "3", "share": 334},
        ],
    })
    await db.budgets.insert_one({
        "id": bud_id, "copropriete_id": cid,
        "fiscal_year_id": fy_id,
        "name": "Budget 2026", "status": "approved",
        "total_amount": 0.0, "lines": [],
    })
    return {"cid": cid, "fy_id": fy_id, "bud_id": bud_id, "key_id": k,
            "o1": o1, "o2": o2, "o3": o3}


async def _cleanup(db, cid):
    for coll in ("coproprietes", "fiscal_years", "pcmn_accounts", "lots",
                 "distribution_keys", "budgets", "fund_calls", "journal_entries"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({f"tier_accounts.{cid}": {"$exists": True}})


def test_reserve_round_up_ceils_each_share():
    """Reserve 1000 EUR / 3 lots avec quotites 333/333/334 -> 333.00/333.00/334.00
    SANS round_up. AVEC round_up=true -> 334/334/335 (ceil integer) = 1003 EUR total."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                payload = {
                    "budget_id": ctx["bud_id"],
                    "frequency": 1, "start_date": "2026-01-01",
                    "due_offset_days": 30,
                    "reserve_fund": {
                        "enabled": True, "amount": 1000.0,
                        "distribution_key_id": ctx["key_id"],
                        "label": "Fonds de reserve",
                        "frequency": 1, "start_date": "2026-01-01",
                        "due_offset_days": 30,
                        "round_up": True,  # <-- iter90eo
                    },
                    "roulement_fund": {"enabled": False, "amount": 0.0,
                                        "distribution_key_id": "", "label": "",
                                        "frequency": 0, "start_date": "",
                                        "due_offset_days": 30},
                    "copropriete_id": ctx["cid"],
                }
                r = await c.post(
                    f"{BACKEND_URL}/api/fund-calls/preview-from-budget",
                    json=payload,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                calls = data.get("calls") or []
                # Trouve l'appel reserve
                res_calls = [c for c in calls if c.get("call_type") == "reserve"]
                assert res_calls, f"Aucun appel reserve : {calls}"
                dist = res_calls[0]["distribution"]
                # Chaque montant doit etre un EUR entier (arrondi superieur)
                for d in dist:
                    amt = float(d["amount"])
                    assert amt == int(amt), (
                        f"Montant {amt} non arrondi a l'entier "
                        f"(round_up=true attendu)"
                    )
                # Somme depasse 1000 EUR (surprovision)
                total = sum(d["amount"] for d in dist)
                assert total >= 1000.0
                assert total < 1010.0  # marge raisonnable
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_reserve_without_round_up_keeps_decimals():
    """Sans round_up, les quotes-parts peuvent avoir des decimales."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                payload = {
                    "budget_id": ctx["bud_id"],
                    "frequency": 1, "start_date": "2026-01-01",
                    "due_offset_days": 30,
                    "reserve_fund": {
                        "enabled": True, "amount": 1000.0,
                        "distribution_key_id": ctx["key_id"],
                        "label": "Fonds de reserve",
                        "frequency": 1, "start_date": "2026-01-01",
                        "due_offset_days": 30,
                        "round_up": False,  # <-- desactive
                    },
                    "roulement_fund": {"enabled": False, "amount": 0.0,
                                        "distribution_key_id": "", "label": "",
                                        "frequency": 0, "start_date": "",
                                        "due_offset_days": 30},
                    "copropriete_id": ctx["cid"],
                }
                r = await c.post(f"{BACKEND_URL}/api/fund-calls/preview-from-budget", json=payload)
                assert r.status_code == 200, r.text
                data = r.json()
                res_calls = [c for c in data.get("calls", [])
                              if c.get("call_type") == "reserve"]
                assert res_calls
                dist = res_calls[0]["distribution"]
                # Somme = ~1000 EUR (snap_to_total garantit l'egalite)
                total = sum(d["amount"] for d in dist)
                assert abs(total - 1000.0) < 0.02
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_roulement_round_up_ceils_each_share():
    """Meme regle round_up pour le fonds de roulement."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                payload = {
                    "budget_id": ctx["bud_id"],
                    "frequency": 1, "start_date": "2026-01-01",
                    "due_offset_days": 30,
                    "reserve_fund": {"enabled": False, "amount": 0.0,
                                     "distribution_key_id": "", "label": "",
                                     "frequency": 0, "start_date": "",
                                     "due_offset_days": 30},
                    "roulement_fund": {
                        "enabled": True, "amount": 500.0,
                        "distribution_key_id": ctx["key_id"],
                        "label": "Fonds de roulement", "mode": "create",
                        "frequency": 2, "start_date": "2026-01-01",
                        "due_offset_days": 30,
                        "round_up": True,  # <-- iter90eo
                    },
                    "copropriete_id": ctx["cid"],
                }
                r = await c.post(f"{BACKEND_URL}/api/fund-calls/preview-from-budget", json=payload)
                assert r.status_code == 200, r.text
                data = r.json()
                roul_calls = [c for c in data.get("calls", [])
                               if c.get("call_type") == "roulement"]
                assert roul_calls, f"Aucun appel roulement : {data.get('calls')}"
                for call in roul_calls:
                    for d in call["distribution"]:
                        amt = float(d["amount"])
                        assert amt == int(amt), (
                            f"Roulement round_up : montant {amt} pas entier"
                        )
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())
