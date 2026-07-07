"""iter90be : Detection stricte des doublons fournisseurs avec particules
juridiques ("Finlead" == "SRL Finlead" == "Finlead SRL").

Bug avant iter90be : la normalisation gardait "srl", "sa", "sprl", etc.
donc "Finlead" (norm='finlead') et "SRL Finlead" (norm='finlead srl')
n'etaient pas detectes comme doublons -> deux fiches distinctes en base
-> deux lignes "Finlead" et "SRL Finlead" dans le bilan PASSIF class 440.

Fix : filtre des particules juridiques dans _norm_name.
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
    copro_id = f"iter90be-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({"id": copro_id, "name": "T", "status": "active"})
    return str(admin["_id"]), copro_id


async def _cleanup(copro_id: str):
    db = await _mongo()
    await db.suppliers.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_one({"id": copro_id})


async def _run_finlead():
    admin_id, copro_id = await _setup()
    try:
        headers = {"Authorization": f"Bearer {_tok(admin_id)}",
                   "X-Copropriete-Id": copro_id}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # 1) Cree "Finlead"
            r = await c.post("/api/suppliers", json={
                "name": "Finlead", "copropriete_id": copro_id,
            }, headers=headers)
            assert r.status_code == 200, r.text

            # 2) Essai de creer "SRL Finlead" -> doit etre detecte comme
            #    doublon EXACT (regle 2 de find_duplicate_supplier).
            r2 = await c.post("/api/suppliers/check-duplicate", json={
                "name": "SRL Finlead", "copropriete_id": copro_id,
            }, headers=headers)
            assert r2.status_code == 200, r2.text
            data = r2.json()
            assert data.get("exact") is not None, data
            assert data["exact"]["field"] == "name", data
            # 3) Aussi Finlead SRL
            r3 = await c.post("/api/suppliers/check-duplicate", json={
                "name": "Finlead SRL", "copropriete_id": copro_id,
            }, headers=headers)
            assert r3.json().get("exact") is not None
            # 4) Autre cas : "AXA S.A." vs "AXA SA"
            r4a = await c.post("/api/suppliers", json={
                "name": "AXA S.A.", "copropriete_id": copro_id,
            }, headers=headers)
            assert r4a.status_code == 200
            r4b = await c.post("/api/suppliers/check-duplicate", json={
                "name": "AXA SA", "copropriete_id": copro_id,
            }, headers=headers)
            assert r4b.json().get("exact") is not None
            # 5) Nom different (pas de doublon)
            r5 = await c.post("/api/suppliers/check-duplicate", json={
                "name": "MyOtherCorp", "copropriete_id": copro_id,
            }, headers=headers)
            d5 = r5.json()
            assert d5.get("exact") is None
            assert d5.get("similar", []) == []
    finally:
        await _cleanup(copro_id)


def test_finlead_srl_finlead_is_duplicate():
    asyncio.run(_run_finlead())
