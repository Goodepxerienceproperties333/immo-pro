"""iter90bo : Verifie qu'un syndic peut accepter les CGU sans blocage.

Bug PROD signale : "les syndic ne peuvent pas cliquer pour accepter les
conditions". Cause identifiee cote UI :
  - `OnboardingDialog` (Radix Dialog) ouvert des la connexion pour un
    nouvel utilisateur (`onboarding_completed=false`).
  - Radix Dialog desactive `pointer-events` sur `<body>` -> les clics sur
    le `LegalAcceptanceModal` (div custom, non-Radix) sont bloques.

Fix cote UI (voir OnboardingDialog.js + SyndicOnboardingWizard.js) :
  Attendre `!needs_accept` (via GET /api/legal/my-acceptance) avant
  d'auto-ouvrir les wizards Radix.

Ce test verifie que le BACKEND ne pose pas de blocage supplementaire
pour le role 'syndic' sur les endpoints `/legal/my-acceptance` et
`/legal/accept`.
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
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _tok(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": f"u-{sub[-6:]}@t.be", "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _run():
    db = await _mongo()
    # Cree un syndic sans legal_accepted
    oid = ObjectId()
    syndic_id = str(oid)
    email = f"syndic-{uuid.uuid4().hex[:8]}@t.be"
    await db.users.insert_one({
        "_id": oid,
        "email": email, "name": "T Syndic", "role": "syndic",
        "password_hash": "$2b$12$fake",  # non-utilise (JWT direct)
        "onboarding_completed": False,
        # legal_accepted absent -> needs_accept=True
    })
    try:
        headers = {"Authorization": f"Bearer {_tok(syndic_id)}"}
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            # 1) GET /legal/my-acceptance -> needs_accept=True
            r = await c.get("/api/legal/my-acceptance", headers=headers)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["needs_accept"] is True
            versions = data["current_versions"]
            assert "cgu_version" in versions and "privacy_version" in versions

            # 2) POST /legal/accept avec les bonnes versions -> 200
            r2 = await c.post("/api/legal/accept", json={
                "cgu_version": versions["cgu_version"],
                "privacy_version": versions["privacy_version"],
            }, headers=headers)
            assert r2.status_code == 200, r2.text
            data2 = r2.json()
            assert data2["message"] == "Conditions acceptees"

            # 3) Re-check my-acceptance -> needs_accept=False
            r3 = await c.get("/api/legal/my-acceptance", headers=headers)
            assert r3.json()["needs_accept"] is False

            # 4) POST /legal/accept avec versions obsoletes -> 400
            r4 = await c.post("/api/legal/accept", json={
                "cgu_version": 999, "privacy_version": 999,
            }, headers=headers)
            assert r4.status_code == 400
            assert "obsoletes" in r4.text.lower()
    finally:
        await db.users.delete_one({"_id": oid})
        await db.audit_log.delete_many({"user_id": syndic_id})


def test_syndic_can_accept_legal_terms():
    asyncio.run(_run())
