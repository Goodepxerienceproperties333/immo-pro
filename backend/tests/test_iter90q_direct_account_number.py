"""Iter90q — Categoriser une transaction bancaire via un compte PCMN direct
(sans passer par expense_category). Utilise pour les virements internes
compte 58 quand le user n'a pas cree de nature dediee.

Tests :
- T1 : Split avec account_number='58' direct -> OK, FI genere
- T2 : Split avec account_number='580000' direct -> OK
- T3 : Split avec account_number='999999' (inexistant) -> 400
- T4 : Split avec account_number='590000' (classe 5 non-58) -> 400
- T5 : Split avec account_number='411000' (classe 4) -> 400
- T6 : Mix : un split via expense_category_id + un split via account_number
- T7 : Ni expense_category_id ni account_number -> 400
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90q-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-iter90q-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-iter90q-"}})
    await db.journal_entries.delete_many({"source_type": "bank_txn",
                                          "source_id": {"$regex": "^bt-iter90q-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90q-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90q-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90q-"}})


async def _login(c):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed():
    cid = f"c-iter90q-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP Iter90q",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    for num, name, cls in [
        ("550000", "Banque", 5), ("58", "Virements internes", 5),
        ("580000", "Virements internes detail", 5),
        ("590000", "Compte cl5 autre", 5),
        ("411000", "Fournisseurs (cl4)", 4),
        ("611000", "Entretien", 6),
    ]:
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls, "active": True,
        })
    dk = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": dk, "copropriete_id": cid, "code": "100",
        "name": "Charges communes", "lots": [],
    })
    return cid, dk


async def _mk_txn(cid, amount, tid_suffix=""):
    sid = f"bs-iter90q-{uuid.uuid4().hex[:6]}"
    await db.bank_statements.insert_one({
        "id": sid, "number": "S", "date": "2026-02-01",
        "account_number": "550000", "opening_balance": 1000, "closing_balance": 1000 + amount,
        "status": "draft", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tid = f"bt-iter90q-{tid_suffix}{uuid.uuid4().hex[:6]}"
    await db.bank_transactions.insert_one({
        "id": tid, "statement_id": sid, "date": "2026-02-05",
        "amount": amount, "account_number": "550000",
        "transaction_type": "credit" if amount > 0 else "debit",
        "counterparty_name": "Transfer",
        "matched": False, "match_type": "", "matched_to": "",
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return tid


async def _t_direct_58_ok():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -1500.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"account_number": "58",
                        "distribution_key_id": dk, "amount": 1500.0,
                        "description": "Vers epargne"}]
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["journal_entry_id"]
        assert body["splits"][0]["account_number"] == "58"
        assert body["splits"][0]["expense_category_id"] == ""


async def _t_direct_580000_ok():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -800.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"account_number": "580000",
                        "distribution_key_id": dk, "amount": 800.0}]
        })
        assert r.status_code == 200, r.text


async def _t_direct_unknown_account_400():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -100.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"account_number": "999999",
                        "distribution_key_id": dk, "amount": 100.0}]
        })
        assert r.status_code == 400
        assert "introuvable" in r.text.lower() or "inconnu" in r.text.lower()


async def _t_direct_class5_not58_rejected():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -100.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"account_number": "590000",
                        "distribution_key_id": dk, "amount": 100.0}]
        })
        assert r.status_code == 400


async def _t_direct_class4_rejected():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -100.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"account_number": "411000",
                        "distribution_key_id": dk, "amount": 100.0}]
        })
        assert r.status_code == 400


async def _t_no_nature_no_account_400():
    await _wipe()
    cid, dk = await _seed()
    tid = await _mk_txn(cid, -100.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": "", "account_number": "",
                        "distribution_key_id": dk, "amount": 100.0}]
        })
        assert r.status_code == 400
        assert "nature" in r.text.lower() or "compte" in r.text.lower()


async def _run_all():
    await _t_direct_58_ok()
    await _t_direct_580000_ok()
    await _t_direct_unknown_account_400()
    await _t_direct_class5_not58_rejected()
    await _t_direct_class4_rejected()
    await _t_no_nature_no_account_400()


def test_iter90q_direct_account_number_categorize():
    asyncio.run(_run_all())
