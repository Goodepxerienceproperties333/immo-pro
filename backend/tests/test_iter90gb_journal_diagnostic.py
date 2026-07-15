"""iter90gb : endpoint diagnostic GET /api/admin/journal-entries/diagnostic
pour aider le syndic a comprendre "pourquoi les OD sont vides ?" sans
avoir besoin d'un shell PROD.
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


def test_iter90gb_diagnostic_returns_breakdown_by_acp():
    """Le diagnostic doit retourner la structure de retour attendue."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90gb-{suffix}"
        await db.coproprietes.insert_one({"id": cid, "name": f"iter90gb-{suffix}"})
        # Insert 2 OD (dont 1 lot_mutation), 1 AC, 1 reversed
        await db.journal_entries.insert_many([
            {"id": f"od1-{suffix}", "copropriete_id": cid,
             "journal_type": "OD", "date": "2026-06-15",
             "source_type": "lot_mutation"},
            {"id": f"od2-{suffix}", "copropriete_id": cid,
             "journal_type": "OD", "date": "2026-03-15",
             "source_type": "fund_call"},
            {"id": f"ac1-{suffix}", "copropriete_id": cid,
             "journal_type": "AC", "date": "2026-02-15",
             "source_type": "invoice"},
            {"id": f"rev1-{suffix}", "copropriete_id": cid,
             "journal_type": "OD", "date": "2026-06-16",
             "is_reversal": True,
             "source_type": "lot_mutation"},
        ])
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.get(f"{BACKEND_URL}/api/admin/journal-entries/diagnostic")
                assert r.status_code == 200, r.text
                data = r.json()
                # Trouve notre ACP dans le retour
                our = next((a for a in data["coproprietes"] if a["id"] == cid), None)
                assert our is not None, (
                    f"ACP {cid} manquante dans le diagnostic"
                )
                assert our["total_entries"] == 4
                assert our["by_journal_type"].get("OD") == 3
                assert our["by_journal_type"].get("AC") == 1
                assert our["mutation_entries_count"] == 2, (
                    f"2 lot_mutation attendues, recu {our['mutation_entries_count']}"
                )
                assert our["is_reversal_count"] == 1
                assert our["date_min"] == "2026-02-15"
                assert our["date_max"] == "2026-06-16"
        finally:
            await db.coproprietes.delete_one({"id": cid})
            await db.journal_entries.delete_many({"copropriete_id": cid})
    asyncio.run(_run())


def test_iter90gb_non_superadmin_gets_403():
    """Non-superadmin recoit 403."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        from server import hash_password
        syndic_email = f"synd_gb_{suffix}@t.be"
        await db.users.insert_one({
            "email": syndic_email, "name": "S", "role": "syndic",
            "password_hash": hash_password("test1234"),
        })
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{BACKEND_URL}/api/auth/login", json={
                    "email": syndic_email, "password": "test1234"})
                assert r.status_code == 200
                r2 = await c.get(f"{BACKEND_URL}/api/admin/journal-entries/diagnostic")
                assert r2.status_code == 403, (
                    f"Syndic doit etre refuse 403, recu {r2.status_code}"
                )
        finally:
            await db.users.delete_one({"email": syndic_email})
    asyncio.run(_run())
