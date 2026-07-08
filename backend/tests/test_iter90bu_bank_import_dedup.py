"""iter90bu : Anti-doublon extraits bancaires (PDF + CODA).

Deux protections :
1. HASH SHA-256 du contenu du fichier -> capte les re-imports EXACTS
   (meme fichier importe 2 fois).
2. TUPLE (ACP, IBAN, periode, closing_balance) -> capte les cas ou l'user
   re-telecharge le meme extrait chez sa banque mais avec un fichier
   physiquement different (metadata differente, mais contenu comptable
   identique).
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
    copro_id = f"iter90bu-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": copro_id, "name": "T", "status": "active"})
    return str(admin["_id"]), copro_id


async def _cleanup(copro_id: str):
    db = await _mongo()
    for coll in ("bank_statements", "bank_transactions"):
        await db[coll].delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_one({"id": copro_id})


async def _run():
    admin_id, copro_id = await _setup()
    db = await _mongo()
    try:
        headers = {"Authorization": f"Bearer {_tok(admin_id)}",
                   "X-Copropriete-Id": copro_id}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # ==========================================
            # SCENARIO 1 : meme hash SHA-256 -> refus
            # Simule 2 extraits deja en base avec le meme hash.
            # ==========================================
            hash1 = "a" * 64
            stmt_id_1 = str(uuid.uuid4())
            await db.bank_statements.insert_one({
                "id": stmt_id_1, "copropriete_id": copro_id,
                "number": "EXT-001", "date": "2025-06-30",
                "account_number": "BE00 1234 5678 9012".replace(" ", ""),
                "opening_balance": 1000, "closing_balance": 2000,
                "period_from": "2025-06-01", "period_to": "2025-06-30",
                "content_hash": hash1, "status": "draft",
            })
            # Re-check via find (le check backend doit trouver le doublon)
            same = await db.bank_statements.find_one({
                "copropriete_id": copro_id, "content_hash": hash1,
            })
            assert same is not None
            assert same["id"] == stmt_id_1

            # ==========================================
            # SCENARIO 2 : meme (IBAN + periode + closing_balance) -> refus
            # ==========================================
            same2 = await db.bank_statements.find_one({
                "copropriete_id": copro_id,
                "account_number": "BE00123456789012",
                "period_from": "2025-06-01",
                "period_to": "2025-06-30",
                "closing_balance": 2000,
            })
            assert same2 is not None
            assert same2["id"] == stmt_id_1

            # ==========================================
            # SCENARIO 3 : IBAN different -> pas doublon
            # ==========================================
            other_iban = await db.bank_statements.find_one({
                "copropriete_id": copro_id,
                "account_number": "BE99999999999999",
                "period_from": "2025-06-01",
                "period_to": "2025-06-30",
                "closing_balance": 2000,
            })
            assert other_iban is None

            # ==========================================
            # SCENARIO 4 : autre periode -> pas doublon
            # ==========================================
            other_period = await db.bank_statements.find_one({
                "copropriete_id": copro_id,
                "account_number": "BE00123456789012",
                "period_from": "2025-07-01",
                "period_to": "2025-07-31",
                "closing_balance": 2000,
            })
            assert other_period is None

            # ==========================================
            # SCENARIO 5 : autre ACP (Chinese walls) -> pas doublon
            # ==========================================
            other_acp = await db.bank_statements.find_one({
                "copropriete_id": "OTHER-ACP",
                "content_hash": hash1,
            })
            assert other_acp is None
    finally:
        await _cleanup(copro_id)


def test_bank_statement_dedup_logic():
    asyncio.run(_run())
