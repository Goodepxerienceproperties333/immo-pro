"""iter90bj : Delettrage depuis le dialog de lettrage.

Verifie :
1. unlettrage-by-invoice defait le lettrage sur la transaction + remet
   l'invoice en unpaid (cas nominal).
2. unlettrage-by-invoice sur une facture "paid" sans transaction existante
   (cas LEGACY : extrait supprime dans le passe) remet l'invoice en unpaid
   quand meme -> permet a l'user de re-lettrer.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _tok(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": f"u-{sub}@t.be", "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup():
    db = await _mongo()
    admin = await db.users.find_one({"role": {"$in": ["superadmin", "admin"]}})
    assert admin
    copro_id = f"iter90bj-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": copro_id, "name": "T", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "name": "2025", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "status": "open",
    })
    return str(admin["_id"]), copro_id


async def _cleanup(copro_id: str):
    db = await _mongo()
    for coll in ("invoices", "bank_transactions", "bank_statements",
                 "journal_entries", "fiscal_years"):
        await db[coll].delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_one({"id": copro_id})


async def _run():
    admin_id, copro_id = await _setup()
    db = await _mongo()
    try:
        headers = {"Authorization": f"Bearer {_tok(admin_id)}",
                   "X-Copropriete-Id": copro_id}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # ============================================================
            # SCENARIO 1 : Cas LEGACY - facture "paid" sans transaction
            # (extrait supprime dans le passe) -> unlettrage-by-invoice
            # doit quand meme remettre la facture en unpaid.
            # ============================================================
            inv_id = f"inv-{uuid.uuid4().hex[:8]}"
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": copro_id,
                "number": "V-LEGACY", "date": "2025-05-01",
                "supplier": "Finlead", "total_amount": 470.0,
                "status": "paid",  # marquee paid via ancien extrait supprime
                "paid_by_transaction_ids": ["ghost-txn-id-inexistant"],
                "lettrage_code": "L-legacy",
                "amount_paid": 470.0,
            })
            r = await c.post(f"/api/banking/unlettrage-by-invoice/{inv_id}",
                             headers=headers)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data.get("manual_reset") is True, data
            # Verifier que l'invoice est bien remise en unpaid
            fresh = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
            assert fresh["status"] == "unpaid"
            assert "paid_by_transaction_ids" not in fresh
            assert "lettrage_code" not in fresh
            assert "amount_paid" not in fresh

            # ============================================================
            # SCENARIO 2 : Cas nominal - facture paid avec transaction
            # -> unlettrage defait la txn (matched=False) + invoice unpaid.
            # ============================================================
            inv2_id = f"inv-{uuid.uuid4().hex[:8]}"
            txn_id = f"txn-{uuid.uuid4().hex[:8]}"
            stmt_id = f"stmt-{uuid.uuid4().hex[:8]}"
            await db.invoices.insert_one({
                "id": inv2_id, "copropriete_id": copro_id,
                "number": "V-OK", "date": "2025-06-01",
                "supplier": "Finlead", "total_amount": 200.0,
                "status": "paid",
                "paid_by_transaction_ids": [txn_id],
                "lettrage_code": "L-ok", "amount_paid": 200.0,
            })
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": copro_id,
                "number": "IMP-1", "date": "2025-06-05",
                "status": "draft", "account_number": "550000",
                "opening_balance": 0, "closing_balance": -200,
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": copro_id,
                "statement_id": stmt_id, "date": "2025-06-05",
                "amount": -200.0, "signed_amount": -200.0,
                "matched": True, "matched_to": inv2_id, "match_type": "invoice",
                "counterparty_name": "Finlead", "communication": "V-OK",
            })
            r = await c.post(f"/api/banking/unlettrage-by-invoice/{inv2_id}",
                             headers=headers)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data.get("count") == 1, data
            # Verifier que l'invoice est unpaid
            fresh2 = await db.invoices.find_one({"id": inv2_id}, {"_id": 0})
            assert fresh2["status"] == "unpaid"
            # Verifier que la txn est delettree (matched=False)
            fresh_txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            assert fresh_txn["matched"] is False
            assert fresh_txn["matched_to"] == ""
    finally:
        await _cleanup(copro_id)


def test_unlettrage_from_dialog_supports_legacy_and_nominal():
    asyncio.run(_run())
