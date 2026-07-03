"""
Iter90ah : Appels de fonds manuels standalone (reserve / roulement / special).

Regle metier :
- POST /api/fund-calls avec call_type='reserve' et distribution_key_id + total_amount
  cree un appel standalone (pas de budget) et genere les bonnes ecritures :
    * Debit : compte tier reserve (40010XXX) de chaque proprietaire
    * Credit : 160 (Fonds de reserve, classe 1)
- Idem pour call_type='roulement' : debit 40000XXX (tier provisions),
  credit 100 (Fonds de roulement).
- call_type='special' : debit tier + credit 710000 (hors budget).
- Chaque appel utilise la cle de repartition choisie sans passer par un budget.
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
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(name: str):
    db = await _mongo()
    cid = f"iter90ah-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_a = f"lot-a-{uuid.uuid4()}"
    lot_b = f"lot-b-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    o2 = f"o2-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": name, "reference": "T-" + name[:10], "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "700000", "name": "Provisions", "class_num": 7, "copropriete_id": cid},
        {"number": "710000", "name": "Special", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "Owner 1", "auxiliary_code": "C0001",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4000001", "reserve": "4001001"}}},
        {"id": o2, "name": "Owner 2", "auxiliary_code": "C0002",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4000002", "reserve": "4001002"}}},
    ])
    await db.lots.insert_many([
        {"id": lot_a, "number": "A1", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 400.0},
        {"id": lot_b, "number": "B1", "owner_id": o2, "owner_ids": [o2],
         "copropriete_id": cid, "quotity": 600.0},
    ])
    # Cle 40/60 -> 400 pour lot_a, 600 pour lot_b
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Charges generales",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_a, "share": 400.0, "lot_number": "A1"},
                 {"lot_id": lot_b, "share": 600.0, "lot_number": "B1"}],
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "key": key_id,
            "o1": o1, "o2": o2, "lot_a": lot_a, "lot_b": lot_b}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"]]}})


async def _create_call(ctx, call_type: str, amount: float):
    payload = {
        "name": f"Appel {call_type}",
        "date": "2026-06-15",
        "due_date": "2026-07-15",
        "fiscal_year_id": ctx["fy_id"],
        "total_amount": amount,
        "call_type": call_type,
        "distribution_key_id": ctx["key"],
        "copropriete_id": ctx["cid"],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _get_admin_token(client)
        resp = await client.post(
            f"{BACKEND_URL}/api/fund-calls",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Copropriete-Id": ctx["cid"],
            },
            json=payload,
        )
    return resp


async def _scenario_reserve_standalone():
    """Appel de reserve manuel 10000 EUR sur cle 40/60 -> tiers reserve + credit 160."""
    ctx = await _setup("iter90ah_reserve")
    try:
        resp = await _create_call(ctx, "reserve", 10000.0)
        assert resp.status_code == 200, resp.text
        call = resp.json()
        # Distribution correcte : 4000 pour o1, 6000 pour o2
        by_owner = {d["owner_id"]: d for d in call["distribution"]}
        assert round(by_owner[ctx["o1"]]["amount"], 2) == 4000.0, by_owner
        assert round(by_owner[ctx["o2"]]["amount"], 2) == 6000.0

        # Ecriture VE : Debit 4001001 (o1 reserve) 4000 + Debit 4001002 (o2 reserve) 6000
        #                Credit 160 (Fonds reserve) 10000
        je = await ctx["db"].journal_entries.find_one({
            "source_type": "fund_call", "source_id": call["id"]
        }, {"_id": 0})
        assert je is not None, "Aucune ecriture VE generee"
        lines = je["lines"]
        # Doit avoir des lignes de debit sur les comptes tier reserve
        debit_reserve = [l for l in lines if l.get("account_number") == "4001001"]
        assert debit_reserve and debit_reserve[0]["debit"] == 4000.0, lines
        debit_reserve_o2 = [l for l in lines if l.get("account_number") == "4001002"]
        assert debit_reserve_o2 and debit_reserve_o2[0]["debit"] == 6000.0
        # Credit 160
        credit_160 = [l for l in lines if l.get("account_number") == "160"]
        assert credit_160 and credit_160[0]["credit"] == 10000.0, lines
        # AUCUN credit sur 700000
        credit_700 = [l for l in lines if l.get("account_number") == "700000"]
        assert not credit_700, "Credit sur 700000 interdit pour appel reserve"
    finally:
        await _cleanup(ctx)


async def _scenario_roulement_standalone():
    """Appel roulement 5000 EUR -> tiers provisions + credit 100."""
    ctx = await _setup("iter90ah_roulement")
    try:
        resp = await _create_call(ctx, "roulement", 5000.0)
        assert resp.status_code == 200, resp.text
        call = resp.json()
        je = await ctx["db"].journal_entries.find_one({
            "source_type": "fund_call", "source_id": call["id"]
        }, {"_id": 0})
        assert je is not None
        # Debit sur comptes tiers provisions (memes que provisions car roulement = passif partage)
        debit_o1 = [l for l in je["lines"] if l.get("account_number") == "4000001"]
        assert debit_o1 and debit_o1[0]["debit"] == 2000.0, je["lines"]
        # Credit 100 (Fonds roulement)
        credit_100 = [l for l in je["lines"] if l.get("account_number") == "100"]
        assert credit_100 and credit_100[0]["credit"] == 5000.0
        # AUCUN credit 700000
        assert not [l for l in je["lines"] if l.get("account_number") == "700000"]
    finally:
        await _cleanup(ctx)


async def _scenario_provisions_standalone_unchanged():
    """Regression : call_type='provisions' standalone -> credit 700000 comme avant."""
    ctx = await _setup("iter90ah_prov")
    try:
        resp = await _create_call(ctx, "provisions", 1000.0)
        assert resp.status_code == 200, resp.text
        call = resp.json()
        je = await ctx["db"].journal_entries.find_one({
            "source_type": "fund_call", "source_id": call["id"]
        }, {"_id": 0})
        assert je is not None
        # Credit 700000
        credit_700 = [l for l in je["lines"] if l.get("account_number") == "700000"]
        assert credit_700 and credit_700[0]["credit"] == 1000.0
        # AUCUN credit 160 ni 100
        assert not [l for l in je["lines"] if l.get("account_number") in ("160", "100")]
    finally:
        await _cleanup(ctx)


def test_reserve_standalone_produces_correct_je():
    asyncio.run(_scenario_reserve_standalone())


def test_roulement_standalone_produces_correct_je():
    asyncio.run(_scenario_roulement_standalone())


def test_provisions_standalone_regression():
    asyncio.run(_scenario_provisions_standalone_unchanged())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
