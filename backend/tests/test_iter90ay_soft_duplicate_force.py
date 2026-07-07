"""iter90bp (ex-iter90ay) : Anti-doublon revise a la demande utilisateur.

Regle unique (iter90bp) :
- Meme numero fournisseur (normalise) + meme fournisseur + meme ACP
  -> HARD block (HTTP 409). Non contournable meme avec force=true.

Regle SOFT precedente (montant + date +/- 3j avec numero different) est
SUPPRIMEE. Deux factures avec numeros differents ne sont JAMAIS des
doublons, quels que soient montant et date (cas legitime : abonnements
recurrents, achats identiques a jours differents, factures fractionnees).

Le parametre `?force=true` est conserve pour la compatibilite API mais
n'a plus d'effet.
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
    copro_id = f"iter90bp-{uuid.uuid4().hex[:8]}"
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


async def _scenario_different_number_never_duplicate():
    """Deux factures avec numeros DIFFERENTS ne sont JAMAIS doublons,
    meme si tout le reste est identique (fournisseur, montant, date)."""
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

            # 2e avec NUMERO DIFFERENT (mais meme fournisseur / montant / date)
            # -> DOIT PASSER SANS PROMPT SOFT
            base2 = {**base, "number": "INV/0027"}
            r = await c.post("/api/invoices", json=base2, headers=headers)
            assert r.status_code == 200, r.text

            # 3e : encore un numero different, meme date, meme montant
            base3 = {**base, "number": "INV/0028"}
            r = await c.post("/api/invoices", json=base3, headers=headers)
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(copro_id)


def test_different_number_never_duplicate():
    asyncio.run(_scenario_different_number_never_duplicate())


async def _scenario_same_number_hard_block():
    """Meme numero fournisseur -> HARD block, meme avec ?force=true."""
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

            # Meme numero -> HARD block
            r = await c.post("/api/invoices", json=base, headers=headers)
            assert r.status_code == 409, r.text
            assert "numero" in r.text.lower()

            # Meme numero + force=true -> toujours HARD block (integrite)
            r = await c.post("/api/invoices?force=true", json=base, headers=headers)
            assert r.status_code == 409, r.text
    finally:
        await _cleanup(copro_id)


def test_same_number_hard_block():
    asyncio.run(_scenario_same_number_hard_block())


async def _scenario_update_with_different_number_ok():
    """PUT vers un autre numero (existant ou pas) ne genere pas de SOFT
    duplicate meme si montant/date coincident avec une autre facture."""
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

            # PUT : essaie de rendre inv2 identique a inv1 en MONTANT+DATE
            # mais garde le numero different (INV/0028) -> DOIT PASSER
            update = {**base, "number": "INV/0028"}
            r = await c.put(f"/api/invoices/{inv2_id}", json=update, headers=headers)
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(copro_id)


def test_update_with_different_number_ok():
    asyncio.run(_scenario_update_with_different_number_ok())
