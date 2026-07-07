"""iter90bb : Categoriser une transaction bancaire avec un compte 58*
(virement interne) doit fonctionner SANS cle de repartition.

Un virement interne (ex: transfert du compte a vue vers le compte epargne)
n'a pas de sens comptable a etre reparti sur les proprietaires : c'est un
simple mouvement de tresorerie.

Bug avant iter90bb : le backend exigeait `distribution_key_id` obligatoire,
et le frontend passait deux updates React consecutives, la 2e ecrasant le
`account_number` saisi dans le champ "OU COMPTE DIRECT".
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
    copro_id = f"iter90bb-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": copro_id, "name": "T", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "name": "2025", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "status": "open",
    })
    # Comptes PCMN necessaires : 58 (virement interne) et 550000 (banque)
    await db.pcmn_accounts.insert_many([
        {"id": f"acc-58-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
         "number": "58", "name": "Virements internes", "class_num": 5},
        {"id": f"acc-550-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
         "number": "550000", "name": "Banque - compte a vue", "class_num": 5},
    ])
    # Compte bancaire + statement + transaction
    ba_id = f"ba-{uuid.uuid4().hex[:6]}"
    await db.bank_accounts.insert_one({
        "id": ba_id, "copropriete_id": copro_id,
        "account_number": "550000", "iban": "BE00", "label": "Compte a vue",
    })
    stmt_id = f"stmt-{uuid.uuid4().hex[:6]}"
    await db.bank_statements.insert_one({
        "id": stmt_id, "copropriete_id": copro_id, "bank_account_id": ba_id,
        "account_number": "550000", "period_from": "2025-01-01",
        "period_to": "2025-01-31", "opening_balance": 1000, "closing_balance": -500,
    })
    txn_id = f"txn-{uuid.uuid4().hex[:6]}"
    await db.bank_transactions.insert_one({
        "id": txn_id, "copropriete_id": copro_id, "statement_id": stmt_id,
        "bank_account_id": ba_id, "account_number": "550000",
        "date": "2025-01-15", "amount": -1500.0, "signed_amount": -1500.0,
        "matched": False, "counterparty_name": "TRANSFERT FONDS DE RESERVE",
        "communication": "",
    })
    return str(admin["_id"]), copro_id, txn_id


async def _cleanup(copro_id: str):
    db = await _mongo()
    await db.bank_transactions.delete_many({"copropriete_id": copro_id})
    await db.bank_statements.delete_many({"copropriete_id": copro_id})
    await db.bank_accounts.delete_many({"copropriete_id": copro_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
    await db.fiscal_years.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_one({"id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})


async def _run_58_direct_no_key():
    admin_id, copro_id, txn_id = await _setup()
    try:
        headers = {"Authorization": f"Bearer {_tok(admin_id)}",
                   "X-Copropriete-Id": copro_id}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # Compte 58 direct SANS cle de repartition -> DOIT PASSER
            r = await c.post(
                f"/api/banking/transactions/{txn_id}/categorize",
                json={"splits": [{
                    "account_number": "58",
                    "distribution_key_id": "",
                    "amount": 1500.0,
                    "description": "Transfert epargne",
                }]},
                headers=headers,
            )
            assert r.status_code == 200, r.text

            # Cleanup categorization pour test suivant
            r2 = await c.delete(
                f"/api/banking/transactions/{txn_id}/categorize",
                headers=headers,
            )
            assert r2.status_code in (200, 204), r2.text

            # Compte 6* (charge) SANS cle -> DOIT ECHOUER
            # Ajout d'un compte 611000
            db = await _mongo()
            await db.pcmn_accounts.insert_one({
                "id": f"acc-611-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
                "number": "611000", "name": "Entretien", "class_num": 6})
            r3 = await c.post(
                f"/api/banking/transactions/{txn_id}/categorize",
                json={"splits": [{
                    "account_number": "611000",
                    "distribution_key_id": "",
                    "amount": 1500.0,
                }]},
                headers=headers,
            )
            assert r3.status_code == 400, r3.text
            assert "cle de repartition" in r3.text.lower()
    finally:
        await _cleanup(copro_id)


def test_categorize_58_direct_no_key_required():
    asyncio.run(_run_58_direct_no_key())
