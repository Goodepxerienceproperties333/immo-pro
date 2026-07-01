"""Iter90k extra validation tests (T6 in review request):
- Reject if expense_category_id unknown -> 400
- Reject if account is not classe 6/7 (e.g. classe 5) -> 400
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90k-extra-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-iter90k-x-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-iter90k-x-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90k-x-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90k-x-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90k-x-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-iter90k-x-"}})


async def _seed():
    cid = f"c-iter90k-x-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": cid, "name": "ACP X",
                                       "created_at": datetime.now(timezone.utc).isoformat()})
    for num, name, cls in [
        ("550000", "Banque", 5),
        ("650000", "Frais bancaires", 6),
    ]:
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls, "active": True,
        })
    dk = str(uuid.uuid4())
    await db.distribution_keys.insert_one({"id": dk, "copropriete_id": cid,
                                            "code": "100", "name": "Charges communes", "lots": []})
    # Categorie sur compte classe 5 (invalide)
    bad_cat = str(uuid.uuid4())
    await db.expense_categories.insert_one({
        "id": bad_cat, "copropriete_id": cid, "name": "Bad classe5",
        "account_number": "550000", "kind": "charge",
    })
    sid = f"bs-iter90k-x-{uuid.uuid4().hex[:8]}"
    await db.bank_statements.insert_one({
        "id": sid, "number": "S-X", "date": "2026-02-01",
        "account_number": "550000", "opening_balance": 0, "closing_balance": -10,
        "status": "draft", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tid = f"bt-iter90k-x-{uuid.uuid4().hex[:8]}"
    await db.bank_transactions.insert_one({
        "id": tid, "statement_id": sid, "date": "2026-02-15",
        "amount": -10.0, "account_number": "550000",
        "transaction_type": "debit",
        "counterparty_name": "Bank", "communication": "",
        "matched": False, "match_type": "", "matched_to": "",
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return cid, dk, bad_cat, tid


async def _login(c):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200


async def _run():
    await _wipe()
    cid, dk, bad_cat, tid = await _seed()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        # 1) Unknown expense_category_id
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": "no-such-cat",
                        "distribution_key_id": dk, "amount": 10.0}]
        })
        assert r.status_code == 400, r.text
        assert "inconnue" in r.text.lower() or "nature" in r.text.lower()
        # 2) Category exists but on classe 5 (invalid) -> reject
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": bad_cat,
                        "distribution_key_id": dk, "amount": 10.0}]
        })
        assert r.status_code == 400, r.text
        assert "classe" in r.text.lower()


def test_iter90k_extra_validation():
    asyncio.run(_run())
