"""iter90ei : Edition d'un exercice comptable.

L'utilisateur souhaite pouvoir modifier :
- nom
- date debut / fin
- invoice_number_prefix (reference interne pour la numerotation auto des factures)

L'endpoint PUT /api/fiscal/years/{year_id} existe deja, mais il n'etait pas
expose dans le frontend. iter90ei ajoute le bouton "Modifier" (Pencil) dans
la table des exercices ouverts et reutilise le dialog existant en mode edition.
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
_TOKEN_CACHE = {"token": None}


async def _login(client):
    """Login via cookie session (backend renvoie Set-Cookie, pas access_token)."""
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return {}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_update_fiscal_year_persists_prefix():
    """PUT /fiscal/years/{id} met a jour le prefixe."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90ei-cid-{suffix}"
        fy_id = f"iter90ei-fy-{suffix}"
        await db.coproprietes.insert_one({"id": cid, "name": "iter90ei ACP"})
        await db.fiscal_years.insert_one({
            "id": fy_id, "copropriete_id": cid,
            "name": "Exercice 2026",
            "start_date": "2026-01-01", "end_date": "2026-12-31",
            "status": "open",
            "invoice_number_prefix": "FA-2026-",
        })
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.put(
                    f"{BACKEND_URL}/api/fiscal/years/{fy_id}",
                    json={
                        "copropriete_id": cid,
                        "name": "Exercice 2026 - Modifie",
                        "start_date": "2026-01-01",
                        "end_date": "2026-12-31",
                        "invoice_number_prefix": "ACACIA-26-",
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["name"] == "Exercice 2026 - Modifie"
                assert data["invoice_number_prefix"] == "ACACIA-26-"
                # Persistance en base
                fy_db = await db.fiscal_years.find_one({"id": fy_id})
                assert fy_db["name"] == "Exercice 2026 - Modifie"
                assert fy_db["invoice_number_prefix"] == "ACACIA-26-"
        finally:
            await db.fiscal_years.delete_one({"id": fy_id})
            await db.coproprietes.delete_one({"id": cid})

    asyncio.run(_run())


def test_update_fiscal_year_404_when_not_found():
    """PUT /fiscal/years/{id} 404 si exercice inexistant."""
    async def _run():
        async with httpx.AsyncClient() as c:
            await _login(c)
            r = await c.put(
                f"{BACKEND_URL}/api/fiscal/years/does-not-exist",
                json={
                    "copropriete_id": "any", "name": "x",
                    "start_date": "2026-01-01", "end_date": "2026-12-31",
                    "invoice_number_prefix": "",
                },
            )
            assert r.status_code == 404

    asyncio.run(_run())


def test_update_fiscal_year_can_change_dates():
    """PUT /fiscal/years/{id} peut modifier les dates de debut/fin."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90ei-cid-{suffix}"
        fy_id = f"iter90ei-fy-{suffix}"
        await db.coproprietes.insert_one({"id": cid, "name": "iter90ei ACP dates"})
        await db.fiscal_years.insert_one({
            "id": fy_id, "copropriete_id": cid,
            "name": "Ex 2026",
            "start_date": "2026-01-01", "end_date": "2026-12-31",
            "status": "open",
            "invoice_number_prefix": "",
        })
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.put(
                    f"{BACKEND_URL}/api/fiscal/years/{fy_id}",
                    json={
                        "copropriete_id": cid,
                        "name": "Ex 2026",
                        "start_date": "2026-04-01",
                        "end_date": "2027-03-31",
                        "invoice_number_prefix": "",
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["start_date"] == "2026-04-01"
                assert data["end_date"] == "2027-03-31"
        finally:
            await db.fiscal_years.delete_one({"id": fy_id})
            await db.coproprietes.delete_one({"id": cid})

    asyncio.run(_run())
