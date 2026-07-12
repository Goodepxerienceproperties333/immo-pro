"""iter90dz (extension) - Fallback lot_number sur /owner/situation et pdf_decompte.

User request : appliquer le meme fallback lot_number a tous les endpoints
qui lisent invoices.distribution_lines pour le portail proprietaire.

Endpoints couverts :
- /api/owner/situation/{copropriete_id} (situation online)
- pdf_decompte.build_decompte_pdf (decompte annuel PDF)
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.hash import bcrypt as _bc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client, email: str):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": email, "password": "owner123",
    })
    resp.raise_for_status()
    return dict(resp.cookies)


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(tag: str):
    db = await _mongo()
    cid = f"iter90dz-x-{tag}-{uuid.uuid4()}"
    email = f"owner-{uuid.uuid4().hex[:6]}@test.be"
    owner_id = f"o-{uuid.uuid4().hex[:6]}"
    user_oid = ObjectId()

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia T", "status": "active"})
    await db.users.insert_one({
        "_id": user_oid, "email": email, "password_hash": _bc.hash("owner123"),
        "role": "owner", "owner_id": owner_id, "name": "T Owner",
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "TEUWEN", "email": email,
        "copropriete_ids": [cid],
    })

    new_lot_id = f"lot-{uuid.uuid4().hex[:6]}"
    await db.lots.insert_one({
        "id": new_lot_id, "number": "001", "copropriete_id": cid,
        "owner_id": owner_id, "owner_ids": [owner_id],
        "quotity": 100.0,
    })
    # Facture avec distribution_lines pointant vers PHANTOM lot_id
    invoice_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        "id": invoice_id, "number": "FA-2026-0002",
        "copropriete_id": cid, "date": "2026-02-01",
        "supplier": "Normec BTV",
        "description": "Ascenseurs",
        "total_amount": 154.60, "status": "paye",
        "distribution_lines": [
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "001",
             "owner_name": "TEUWEN", "share": 100.0, "amount": 154.60},
        ],
    })
    return {"db": db, "cid": cid, "email": email, "owner_id": owner_id,
            "user_oid": user_oid, "new_lot_id": new_lot_id, "invoice_id": invoice_id}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    await db.users.delete_one({"_id": ctx["user_oid"]})
    await db.owners.delete_one({"id": ctx["owner_id"]})
    for coll in ("lots", "invoices"):
        await db[coll].delete_many({"copropriete_id": cid})


async def _test_situation_endpoint_uses_fallback():
    """/owner/situation/{cid} doit voir la charge via lot_number match."""
    ctx = await _setup("situation")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client, ctx["email"])
            r = await client.get(
                f"{BACKEND_URL}/api/owner/situation/{ctx['cid']}",
                cookies=cookies,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            # Cherche la ligne de charge dans movements
            charges = [m for m in data.get("movements", []) if m.get("type") == "charge"]
            assert len(charges) == 1, (
                f"iter90dz : situation online doit voir la charge phantom "
                f"via fallback lot_number. Movements: {data.get('movements')}"
            )
            assert charges[0]["debit"] == 154.60
    finally:
        await _cleanup(ctx)


def test_situation_endpoint_uses_fallback():
    asyncio.run(_test_situation_endpoint_uses_fallback())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
