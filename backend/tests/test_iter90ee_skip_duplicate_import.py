"""iter90ee : Import IA batch de factures - permettre d'ignorer les
doublons et passer a la facture suivante.

Le backend anti-doublon renvoie 409. Le frontend en mode batch propose de
skip (via bouton "Ignorer et suivant" ou fallback confirm). Ce test valide
que le POST /invoices renvoie bien 409 sur doublon strict (backend fidele).
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
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed(db, suffix: str) -> dict:
    cid = f"iter90ee-cid-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": "iter90ee ACP"})
    await db.pcmn_accounts.insert_one({
        "id": str(uuid.uuid4()), "number": "61300", "name": "Test",
        "copropriete_id": cid, "class_num": 6, "active": True,
    })
    await db.fiscal_years.insert_one({
        "id": f"iter90ee-fy-{suffix}", "copropriete_id": cid,
        "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "status": "open",
    })
    return {"cid": cid}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})


def test_duplicate_invoice_returns_409_strict():
    """Second POST avec meme supplier + meme number = 409."""
    async def _run():
        db = await _mongo()
        ctx = await _seed(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                payload = {
                    "supplier": "Doublon SRL", "number": "F-2026-001",
                    "date": "2026-05-15", "total_amount": 100.0, "vat_amount": 0.0,
                    "account_number": "61300",
                    "copropriete_id": ctx["cid"],
                    "status": "unpaid",
                    "description": "iter90ee",
                }
                r1 = await client.post(f"{BACKEND_URL}/api/invoices", json=payload, headers=hdr)
                assert r1.status_code == 200, r1.text
                # Meme numero + meme supplier -> doublon strict
                r2 = await client.post(f"{BACKEND_URL}/api/invoices", json=payload, headers=hdr)
                assert r2.status_code == 409
                detail = r2.json().get("detail", "")
                assert "doublon" in detail.lower() or "duplicate" in detail.lower()
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_different_number_same_supplier_not_duplicate():
    """Numeros distincts = pas de doublon (backend strict rule)."""
    async def _run():
        db = await _mongo()
        ctx = await _seed(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                base = {
                    "supplier": "SameSupplier",
                    "date": "2026-05-15", "total_amount": 100.0, "vat_amount": 0.0,
                    "account_number": "61300",
                    "copropriete_id": ctx["cid"],
                    "status": "unpaid",
                    "description": "iter90ee diff",
                }
                r1 = await client.post(f"{BACKEND_URL}/api/invoices",
                                        json={**base, "number": "F-A"}, headers=hdr)
                assert r1.status_code == 200
                r2 = await client.post(f"{BACKEND_URL}/api/invoices",
                                        json={**base, "number": "F-B"}, headers=hdr)
                assert r2.status_code == 200
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())
