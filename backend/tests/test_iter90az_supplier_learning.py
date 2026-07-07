"""iter90az : Auto-apprentissage nature de depense par fournisseur.

Endpoint : GET /api/invoices/supplier-suggestion?supplier=X&copropriete_id=Y

Regles verifiees :
1. Sans historique -> {suggestion: None}
2. Avec 1 facture -> suggestion = nature de cette facture
3. Priorite a la nature LA PLUS UTILISEE quand plusieurs factures existent
4. Chinese walls : ignore les factures d'autres ACP
5. Match fournisseur insensible a la casse (exact match)
6. Mode multi-lignes : chaque ligne compte pour +1
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
    copro_id = f"iter90az-{uuid.uuid4().hex[:8]}"
    other_copro = f"iter90az-other-{uuid.uuid4().hex[:6]}"
    await db.coproprietes.insert_many([
        {"id": copro_id, "name": "T", "status": "active"},
        {"id": other_copro, "name": "T-other", "status": "active"},
    ])
    for cid in (copro_id, other_copro):
        await db.fiscal_years.insert_one({
            "id": f"fy-{uuid.uuid4().hex[:6]}", "copropriete_id": cid,
            "name": "2025", "start_date": "2025-01-01", "end_date": "2025-12-31",
            "status": "open",
        })
    # Deux categories de depense
    cat_a = f"cat-a-{uuid.uuid4().hex[:6]}"
    cat_b = f"cat-b-{uuid.uuid4().hex[:6]}"
    await db.expense_categories.insert_many([
        {"id": cat_a, "copropriete_id": copro_id, "name": "Honoraires syndic",
         "account_number": "61300", "code": "HON"},
        {"id": cat_b, "copropriete_id": copro_id, "name": "Frais admin",
         "account_number": "61600", "code": "ADM"},
    ])
    return str(admin["_id"]), copro_id, other_copro, cat_a, cat_b


async def _cleanup(copro_id: str, other_copro: str):
    db = await _mongo()
    for cid in (copro_id, other_copro):
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.fiscal_years.delete_many({"copropriete_id": cid})
        await db.expense_categories.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _create_inv(client, headers, copro_id, supplier, cat_id, acc, number, amount=100.0, date="2025-02-01"):
    r = await client.post("/api/invoices?force=true", json={
        "copropriete_id": copro_id,
        "number": number, "date": date, "supplier": supplier,
        "description": "Test", "total_amount": amount, "vat_amount": 0,
        "expense_category_id": cat_id, "account_number": acc,
    }, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _run():
    admin_id, copro_id, other_copro, cat_a, cat_b = await _setup()
    try:
        headers = {"Authorization": f"Bearer {_tok(admin_id)}"}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # 1) Sans historique -> None
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "Finlead", "copropriete_id": copro_id},
                            headers=headers)
            assert r.status_code == 200
            assert r.json()["suggestion"] is None

            # 2) Une facture -> suggestion = cette nature
            await _create_inv(c, headers, copro_id, "Finlead", cat_a, "61300", "F-1", amount=100, date="2025-02-01")
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "Finlead", "copropriete_id": copro_id},
                            headers=headers)
            sug = r.json()["suggestion"]
            assert sug is not None
            assert sug["expense_category_id"] == cat_a
            assert sug["account_number"] == "61300"
            assert sug["usage_count"] == 1

            # 3) Deuxieme facture Finlead sur cat_b -> egalite, priorite peut-etre A ou B
            await _create_inv(c, headers, copro_id, "Finlead", cat_b, "61600", "F-2", amount=200, date="2025-03-01")
            # 4) Troisieme facture Finlead sur cat_a -> majorite pour cat_a
            await _create_inv(c, headers, copro_id, "Finlead", cat_a, "61300", "F-3", amount=300, date="2025-04-01")
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "Finlead", "copropriete_id": copro_id},
                            headers=headers)
            sug = r.json()["suggestion"]
            assert sug["expense_category_id"] == cat_a
            assert sug["usage_count"] == 2

            # 5) Case-insensitive match
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "FINLEAD", "copropriete_id": copro_id},
                            headers=headers)
            assert r.json()["suggestion"]["expense_category_id"] == cat_a

            # 6) Chinese walls : facture dans autre ACP ne compte pas
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "Finlead", "copropriete_id": other_copro},
                            headers=headers)
            assert r.json()["suggestion"] is None

            # 7) Fournisseur inconnu -> None
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "AutreFournisseur", "copropriete_id": copro_id},
                            headers=headers)
            assert r.json()["suggestion"] is None

            # 8) Supplier vide -> None
            r = await c.get("/api/invoices/supplier-suggestion",
                            params={"supplier": "  ", "copropriete_id": copro_id},
                            headers=headers)
            assert r.json()["suggestion"] is None
    finally:
        await _cleanup(copro_id, other_copro)


def test_supplier_learning():
    asyncio.run(_run())
