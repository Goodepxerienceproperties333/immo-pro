"""iter90ay : Le blocage anti-doublon "montant+fournisseur+date proche" doit
etre CONTOURNABLE via ?force=true lorsque le numero de facture differe.

- Regle 1 (meme numero fournisseur) : HARD BLOCK, meme avec force=true
- Regle 2 (montant+date+fournisseur similaires) : SOFT BLOCK, contournable
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _make_token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": f"u-{sub}@t.be", "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_context():
    db = await _mongo()
    admin = await db.users.find_one({"role": {"$in": ["superadmin", "admin"]}})
    assert admin
    copro_id = f"iter90ay-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": copro_id, "name": "T", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "name": "2025", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "status": "open",
    })
    return str(admin["_id"]), copro_id


async def _cleanup(copro_id: str):
    db = await _mongo()
    await db.invoices.delete_many({"copropriete_id": copro_id})
    await db.fiscal_years.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_one({"id": copro_id})


async def _scenario_soft_duplicate_can_be_forced():
    admin_id, copro_id = await _setup_context()
    try:
        headers = {"Authorization": f"Bearer {_make_token(admin_id)}"}
        base = {
            "copropriete_id": copro_id, "supplier": "GEP",
            "date": "2025-01-14", "description": "Test",
            "total_amount": 15, "vat_amount": 2.6, "number": "INV/0026",
        }
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # 1re facture cree OK
            r = await c.post("/api/invoices", json=base, headers=headers)
            assert r.status_code == 200, r.text

            # 2e avec numero different mais tout le reste identique -> SOFT block
            base2 = {**base, "number": "INV/0027"}
            r = await c.post("/api/invoices", json=base2, headers=headers)
            assert r.status_code == 409, r.text
            assert "[SOFT_DUPLICATE]" in r.text, r.text

            # 2e avec force=true -> passe
            r = await c.post("/api/invoices?force=true", json=base2, headers=headers)
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(copro_id)


def test_soft_duplicate_can_be_forced():
    asyncio.run(_scenario_soft_duplicate_can_be_forced())


async def _scenario_hard_duplicate_cannot_be_forced():
    """Le blocage numero identique doit persister meme avec force=true."""
    admin_id, copro_id = await _setup_context()
    try:
        headers = {"Authorization": f"Bearer {_make_token(admin_id)}"}
        base = {
            "copropriete_id": copro_id, "supplier": "GEP",
            "date": "2025-01-14", "description": "T",
            "total_amount": 15, "vat_amount": 2.6, "number": "INV/0026",
        }
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            r = await c.post("/api/invoices", json=base, headers=headers)
            assert r.status_code == 200

            # Meme numero -> HARD block meme avec force
            r = await c.post("/api/invoices?force=true", json=base, headers=headers)
            assert r.status_code == 409, r.text
            assert "[SOFT_DUPLICATE]" not in r.text, "Should be a hard duplicate error"
            assert "numero identique" in r.text.lower() or "numero" in r.text.lower()
    finally:
        await _cleanup(copro_id)


def test_hard_duplicate_cannot_be_forced():
    asyncio.run(_scenario_hard_duplicate_cannot_be_forced())


async def _scenario_update_supports_force():
    """PUT /invoices/{id}?force=true doit aussi contourner le SOFT block."""
    admin_id, copro_id = await _setup_context()
    try:
        headers = {"Authorization": f"Bearer {_make_token(admin_id)}"}
        base = {
            "copropriete_id": copro_id, "supplier": "GEP",
            "date": "2025-01-14", "description": "T",
            "total_amount": 15, "vat_amount": 2.6, "number": "INV/0026",
        }
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            r1 = await c.post("/api/invoices", json=base, headers=headers)
            assert r1.status_code == 200
            r2 = await c.post("/api/invoices", json={**base, "number": "INV/0028",
                              "total_amount": 20}, headers=headers)
            assert r2.status_code == 200
            inv2_id = r2.json()["id"]

            # PUT : essayer de rendre inv2 identique a inv1 (montant+date) -> SOFT block
            update = {**base, "number": "INV/0028"}  # meme montant que inv1, meme date
            r = await c.put(f"/api/invoices/{inv2_id}", json=update, headers=headers)
            assert r.status_code == 409, r.text
            assert "[SOFT_DUPLICATE]" in r.text

            # PUT avec force=true : OK
            r = await c.put(f"/api/invoices/{inv2_id}?force=true", json=update, headers=headers)
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(copro_id)


def test_update_supports_force():
    asyncio.run(_scenario_update_supports_force())
