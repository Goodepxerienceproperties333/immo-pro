"""iter90gj : appels hors budget captures dans l'etape "OD d'ouverture"
et stockes sur `fiscal_years` pour utilisation par les mutations et
rapports.

**Ticket utilisateur** : "dans le wizzard il faut aussi mentionner la
liste des appels hors budget"

**Champs stockes sur fiscal_year** :
- reserve_fund_opening_balance (float)
- reserve_fund_has_annual_call (bool)
- reserve_fund_call_amount (float)
- reserve_fund_call_frequency (str : annual|quarterly|monthly)
- roulement_fund_opening_balance (float) ** CRUCIAL POUR MUTATIONS **
- roulement_fund_has_increase (bool)
- roulement_fund_new_total (float)
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


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(db, suffix):
    cid = f"iter90gj-ob-{suffix}"
    sid = f"session-ob-{suffix}"
    fy_id = f"fy-ob-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-ob-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": f"2026-{suffix}",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active",
        "steps": {"fiscal_year": {"fiscal_year_id": fy_id}},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return {"cid": cid, "sid": sid, "fy_id": fy_id}


async def _cleanup(db, cid, sid, fy_id):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_one({"id": fy_id})
    await db.import_sessions.delete_one({"id": sid})
    await db.journal_entries.delete_many({"import_session_id": sid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})


async def test_funds_config_stored_on_fiscal_year():
    """Apres commit-opening-balance avec funds_config, les champs sont
    stockes sur fiscal_year (pour utilisation par mutations et rapports).
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-opening-balance",
                json={
                    "actif": [{"account": "550000", "label": "Banque", "amount": 5000}],
                    "passif": [{"account": "100400", "label": "Fonds roulement", "amount": 5000}],
                    "period_end_date": "31/12/2025",
                    "fiscal_year_id": ctx["fy_id"],
                    "funds_config": {
                        "reserve_fund": {
                            "opening_balance": 12000.50,
                            "has_annual_call": True,
                            "call_amount": 3500.00,
                            "call_frequency": "quarterly",
                        },
                        "roulement_fund": {
                            "opening_balance": 5000.00,
                            "has_increase": True,
                            "new_total": 7500.00,
                        },
                    },
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["funds_saved"] is True

            # Verifie que les champs sont stockes sur fiscal_year
            fy = await db.fiscal_years.find_one({"id": ctx["fy_id"]}, {"_id": 0})
            assert fy["reserve_fund_opening_balance"] == 12000.50
            assert fy["reserve_fund_has_annual_call"] is True
            assert fy["reserve_fund_call_amount"] == 3500.00
            assert fy["reserve_fund_call_frequency"] == "quarterly"
            assert fy["roulement_fund_opening_balance"] == 5000.00
            assert fy["roulement_fund_has_increase"] is True
            assert fy["roulement_fund_new_total"] == 7500.00
            assert "funds_config_updated_at" in fy
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"], ctx["fy_id"])


async def test_funds_config_optional_no_side_effects():
    """Si funds_config n'est pas fourni, le commit fonctionne normalement
    et funds_saved=False.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-opening-balance",
                json={
                    "actif": [{"account": "550000", "label": "Banque", "amount": 100}],
                    "passif": [{"account": "100400", "label": "Fonds", "amount": 100}],
                    "period_end_date": "31/12/2025",
                    "fiscal_year_id": ctx["fy_id"],
                    # Pas de funds_config
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["funds_saved"] is False

            fy = await db.fiscal_years.find_one({"id": ctx["fy_id"]}, {"_id": 0})
            assert "reserve_fund_opening_balance" not in fy
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"], ctx["fy_id"])


if __name__ == "__main__":
    asyncio.run(test_funds_config_stored_on_fiscal_year())
    print("OK test_funds_config_stored_on_fiscal_year")
    asyncio.run(test_funds_config_optional_no_side_effects())
    print("OK test_funds_config_optional_no_side_effects")
