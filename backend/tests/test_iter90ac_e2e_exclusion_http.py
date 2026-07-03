"""
Iter90ac E2E HTTP tests: exclusion sur les lots d'une cle de repartition.

Verifie via HTTP (production URL) :
1. POST /api/distribution-keys accepte lots avec excluded=true et retourne la valeur.
2. POST /api/lots/{id}/mutate-preview sur lot exclu -> lot_excluded_from_key=true,
   roulement_quota=0, PAS de 400.
3. POST /api/lots/{id}/mutate-preview sur lot non-exclu -> denominateur ignore les exclus.
4. POST /api/invoices avec distribution_key contenant 1 lot exclu -> distribution_lines
   ne contient PAS le lot exclu, share_ratio calcule sur denominateur reduit.
5. POST /api/fund-calls avec distribution_key contenant 1 lot exclu -> distribution
   ne contient PAS le lot exclu.

Seed data via MongoDB direct (motor) + cleanup obligatoire par ACP id temporaire.
"""
import asyncio
import os
import sys
import uuid
import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def ctx():
    """Seed ACP + FY + PCMN + owners + lots + roulement JE. Cleanup after tests."""
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        cid = f"iter90ac-e2e-{uuid.uuid4()}"
        fy_id = f"fy-{uuid.uuid4()}"
        o1 = f"o1-{uuid.uuid4()}"
        o2 = f"o2-{uuid.uuid4()}"
        lot_a = f"lot-a-{uuid.uuid4()}"
        lot_b = f"lot-b-{uuid.uuid4()}"
        await db.coproprietes.insert_one({
            "id": cid, "name": "iter90ac_e2e",
            "reference": "TE2E-" + cid[-6:], "status": "active"})
        await db.fiscal_years.insert_one({
            "id": fy_id, "name": "2026", "start_date": "2026-01-01",
            "end_date": "2026-12-31", "copropriete_id": cid})
        await db.pcmn_accounts.insert_many([
            {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
            {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
            {"number": "4100001", "name": "V", "class_num": 4, "copropriete_id": cid},
            {"number": "4100002", "name": "A", "class_num": 4, "copropriete_id": cid},
            {"number": "611", "name": "Charges", "class_num": 6, "copropriete_id": cid},
        ])
        await db.owners.insert_many([
            {"id": o1, "name": "Vendeur", "last_name": "V", "auxiliary_code": "C0001",
             "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
            {"id": o2, "name": "Acheteur", "last_name": "A", "auxiliary_code": "C0002",
             "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
        ])
        await db.lots.insert_many([
            {"id": lot_a, "number": "A1", "owner_id": o1, "owner_ids": [o1],
             "copropriete_id": cid, "quotity": 500.0},
            {"id": lot_b, "number": "B1", "owner_id": o1, "owner_ids": [o1],
             "copropriete_id": cid, "quotity": 1000.0},
        ])
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01",
            "copropriete_id": cid,
            "lines": [{"account_number": "4100001", "debit": 3000.0, "credit": 0.0},
                      {"account_number": "100", "debit": 0.0, "credit": 3000.0}],
            "total_debit": 3000.0, "total_credit": 3000.0,
        })
        client.close()
        return {"cid": cid, "fy_id": fy_id, "o1": o1, "o2": o2,
                "lot_a": lot_a, "lot_b": lot_b}

    async def _cleanup(cid):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.coproprietes.delete_one({"id": cid})
        for coll in ("fiscal_years", "pcmn_accounts", "owners", "lots",
                     "journal_entries", "distribution_keys", "fund_calls",
                     "mutation_records", "invoices", "budgets"):
            await db[coll].delete_many({"copropriete_id": cid})
        client.close()

    data = asyncio.run(_seed())
    yield data
    asyncio.run(_cleanup(data["cid"]))


# 1) Create distribution-key with excluded lot via HTTP
def test_create_distribution_key_with_excluded_lot(api, ctx):
    payload = {
        "name": "Cle E2E excl",
        "key_type": "quotity",
        "copropriete_id": ctx["cid"],
        "is_default": True,
        "lots": [
            {"lot_id": ctx["lot_a"], "lot_number": "A1", "share": 500.0, "excluded": True},
            {"lot_id": ctx["lot_b"], "lot_number": "B1", "share": 1000.0, "excluded": False},
        ],
    }
    r = api.post(f"{BASE_URL}/api/distribution-keys", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "id" in data
    assert len(data["lots"]) == 2
    lots_by_id = {l["lot_id"]: l for l in data["lots"]}
    assert lots_by_id[ctx["lot_a"]]["excluded"] is True
    assert lots_by_id[ctx["lot_b"]]["excluded"] is False
    ctx["dk_id"] = data["id"]


# 2) Mutate-preview on excluded lot -> roulement=0, no 400
def test_mutate_preview_on_excluded_lot(api, ctx):
    assert "dk_id" in ctx, "prereq test_create_distribution_key_with_excluded_lot failed"
    payload = {"new_owner_id": ctx["o2"], "sale_date": "2026-03-15"}
    r = api.post(f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate-preview",
                 json=payload, timeout=15)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert data.get("lot_excluded_from_key") is True, data
    assert data.get("lot_share_in_key") == 0.0
    assert data.get("roulement_quota") == 0.0
    assert data.get("key_total_quotity") == 1000.0


# 3) Mutate-preview on non-excluded lot: denominator ignores excluded
def test_mutate_preview_denominator_ignores_excluded(api, ctx):
    payload = {"new_owner_id": ctx["o2"], "sale_date": "2026-03-15"}
    r = api.post(f"{BASE_URL}/api/lots/{ctx['lot_b']}/mutate-preview",
                 json=payload, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("lot_excluded_from_key") is False
    assert data.get("lot_share_in_key") == 1000.0
    assert data.get("key_total_quotity") == 1000.0
    # 3000 * 1000/1000 = 3000 EUR (car lot A exclu)
    assert data.get("roulement_quota") == 3000.00, data


# 4) Invoice created with distribution_key_id excluding a lot -> line not distributed
def test_invoice_distribution_excludes_lot(api, ctx):
    payload = {
        "supplier": "TEST_Fournisseur excl",
        "number": f"INV-EXCL-{uuid.uuid4().hex[:6]}",
        "description": "TEST invoice iter90ac",
        "date": "2026-04-15",
        "total_amount": 1500.0,
        "account_number": "611",
        "distribution_key_id": ctx["dk_id"],
        "copropriete_id": ctx["cid"],
    }
    r = api.post(f"{BASE_URL}/api/invoices", json=payload, timeout=20)
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    inv = r.json()
    dist = inv.get("distribution_lines") or []
    lot_ids = [d["lot_id"] for d in dist]
    assert ctx["lot_a"] not in lot_ids, f"lot_a (excluded) present in distribution: {dist}"
    assert ctx["lot_b"] in lot_ids
    # 1500 EUR distribue integralement sur lot_b (denominateur=1000)
    b_line = next(d for d in dist if d["lot_id"] == ctx["lot_b"])
    assert b_line["amount"] == 1500.0, b_line


# 5) Fund call with distribution_key containing excluded lot
def test_fund_call_distribution_excludes_lot(api, ctx):
    payload = {
        "name": "TEST_AF Excl",
        "date": "2026-05-10",
        "due_date": "2026-06-10",
        "fiscal_year_id": ctx["fy_id"],
        "total_amount": 2000.0,
        "call_type": "provisions",
        "distribution_key_id": ctx["dk_id"],
        "copropriete_id": ctx["cid"],
    }
    r = api.post(f"{BASE_URL}/api/fund-calls", json=payload, timeout=20)
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    fc = r.json()
    dist = fc.get("distribution") or []
    lot_ids = [d["lot_id"] for d in dist]
    assert ctx["lot_a"] not in lot_ids, f"lot_a (excluded) present in fund-call: {dist}"
    assert ctx["lot_b"] in lot_ids
    b_line = next(d for d in dist if d["lot_id"] == ctx["lot_b"])
    assert b_line["amount"] == 2000.0, b_line


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
