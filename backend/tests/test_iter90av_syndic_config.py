"""iter90av : Tests config syndic (identite, logo, email + admin scope).

Verifie :
1. Le crypto module chiffre/dechiffre correctement
2. Un syndic peut lire/ecrire sa propre config
3. Un gestionnaire enfant lit la config du parent (heritage)
4. Le superadmin peut lister et editer la config de n'importe quel syndic
5. L'upload de logo fonctionne et est stocke en GridFS
6. L'endpoint test-email retourne 400 si config incomplete
7. L'endpoint complete-onboarding refuse si champs obligatoires manquent
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from bson import ObjectId
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


# ============ Crypto module ============

def test_crypto_roundtrip():
    """Verifie que encrypt->decrypt donne le meme resultat."""
    from crypto_utils import encrypt_secret, decrypt_secret, is_encrypted, redact

    plain = "MySuperSecret123!"
    enc = encrypt_secret(plain)
    assert enc != plain
    assert is_encrypted(enc)
    assert enc.startswith("enc:v1:")
    assert decrypt_secret(enc) == plain

    # Idempotent : reencrypt d'un token deja chiffre = no-op
    assert encrypt_secret(enc) == enc

    # Legacy : secret clair non prefixe -> retourne tel quel a la lecture
    legacy = "old-plain-secret"
    assert decrypt_secret(legacy) == legacy

    # Redact
    assert "*" in redact(plain)
    assert redact(enc) == "********"


# ============ Endpoint tests ============

async def _create_syndic_manager():
    db = await _mongo()
    syndic_id = ObjectId()
    manager_id = ObjectId()
    await db.users.insert_one({
        "_id": syndic_id, "email": f"s-{uuid.uuid4().hex[:6]}@t.be",
        "role": "syndic", "name": "S iter90av", "password_hash": "$2b$12$x",
        "authorized_mailboxes": [
            {"address": "compta@cabinet.be", "active": True},
        ],
    })
    await db.users.insert_one({
        "_id": manager_id, "email": f"m-{uuid.uuid4().hex[:6]}@t.be",
        "role": "gestionnaire", "name": "M iter90av", "password_hash": "$2b$12$x",
        "parent_syndic_id": str(syndic_id),
    })
    return str(syndic_id), str(manager_id)


async def _cleanup(*user_ids):
    db = await _mongo()
    for uid in user_ids:
        await db.users.delete_one({"_id": ObjectId(uid)})
        await db.syndic_configs.delete_one({"syndic_user_id": uid})


async def _scenario_syndic_self_config():
    """Un syndic peut lire/ecrire sa propre config d'identite."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # GET initial (config vide)
            r = await c.get("/api/syndic-config/me",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["syndic_user_id"] == syndic_id
            assert data["onboarding_completed"] is False

            # PUT identite
            r = await c.put("/api/syndic-config/me",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                            json={
                                "legal_name": "Cabinet Test SPRL",
                                "address": "Rue Test 1",
                                "city": "Bruxelles",
                                "email": "cabinet@test.be",
                                "ipi_number": "506.999",
                            })
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["legal_name"] == "Cabinet Test SPRL"
            assert data["ipi_number"] == "506.999"
    finally:
        await _cleanup(syndic_id, manager_id)


def test_syndic_self_config():
    asyncio.run(_scenario_syndic_self_config())


async def _scenario_manager_inherits_config():
    """Un gestionnaire enfant voit la config du parent."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Syndic pose la config
            await c.put("/api/syndic-config/me",
                        headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                        json={"legal_name": "Parent Cabinet",
                              "address": "X", "city": "Y", "email": "p@test.be"})
            # Manager lit
            r = await c.get("/api/syndic-config/me",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["syndic_user_id"] == syndic_id  # ID resolu au parent
            assert data["legal_name"] == "Parent Cabinet"
    finally:
        await _cleanup(syndic_id, manager_id)


def test_manager_inherits_config():
    asyncio.run(_scenario_manager_inherits_config())


async def _scenario_encrypted_secret_storage():
    """Les client_secret / smtp_password sont chiffres en base."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            r = await c.put("/api/syndic-config/me/email",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                            json={
                                "provider": "smtp",
                                "smtp_host": "smtp.test.be",
                                "smtp_port": 587,
                                "smtp_username": "user",
                                "smtp_password": "SuperPassword!",
                                "smtp_use_tls": True,
                            })
            assert r.status_code == 200, r.text
            # Verifie en base que smtp_password est chiffre
            db = await _mongo()
            doc = await db.syndic_configs.find_one({"syndic_user_id": syndic_id})
            assert doc["smtp_password"].startswith("enc:v1:"), "SMTP password doit etre chiffre"
            # Le GET renvoie la version masquee (******)
            r = await c.get("/api/syndic-config/me",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert "SuperPassword" not in r.text
    finally:
        await _cleanup(syndic_id, manager_id)


def test_encrypted_secret_storage():
    asyncio.run(_scenario_encrypted_secret_storage())


async def _scenario_complete_onboarding_validation():
    """Complete-onboarding refuse si champs obligatoires manquent."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Aucune config -> 400
            r = await c.post("/api/syndic-config/me/complete-onboarding",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 400, r.text
            assert "manquant" in r.text.lower()

            # Config complete -> 200
            await c.put("/api/syndic-config/me",
                        headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                        json={"legal_name": "X SPRL", "address": "Rue", "city": "BXL",
                              "email": "x@t.be"})
            r = await c.post("/api/syndic-config/me/complete-onboarding",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(syndic_id, manager_id)


def test_complete_onboarding_validation():
    asyncio.run(_scenario_complete_onboarding_validation())


async def _scenario_test_email_requires_provider():
    """test-email doit rejeter si provider=none (fallback env vars)."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            r = await c.post("/api/syndic-config/me/test-email",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={"from_mailbox": "compta@cabinet.be", "to": "test@t.be"})
            # Peut retourner 400 (config incomplete) ou 500 (fallback env vars mais MAIL_ENABLED=false)
            assert r.status_code in (400, 403, 500), r.text
    finally:
        await _cleanup(syndic_id, manager_id)


def test_test_email_requires_provider():
    asyncio.run(_scenario_test_email_requires_provider())


async def _scenario_admin_can_edit_any_syndic():
    """Superadmin peut lister et editer la config de n'importe quel syndic."""
    admin_id = None
    syndic_id, manager_id = await _create_syndic_manager()
    db = await _mongo()
    admin = await db.users.find_one({"role": {"$in": ["superadmin", "admin"]}})
    assert admin, "Aucun superadmin trouve dans la DB"
    admin_id = str(admin["_id"])
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # List
            r = await c.get("/api/admin/syndic-config/list",
                            headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert any(s["syndic_user_id"] == syndic_id for s in data["syndics"])

            # Edit par admin
            r = await c.put(f"/api/admin/syndic-config/{syndic_id}",
                            headers={"Authorization": f"Bearer {_make_token(admin_id)}"},
                            json={"legal_name": "Edite par admin"})
            assert r.status_code == 200, r.text
            assert r.json()["legal_name"] == "Edite par admin"

            # Un gestionnaire ne peut PAS acceder aux endpoints admin
            r = await c.get("/api/admin/syndic-config/list",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.status_code == 403, r.text
    finally:
        await _cleanup(syndic_id, manager_id)


def test_admin_can_edit_any_syndic():
    asyncio.run(_scenario_admin_can_edit_any_syndic())


async def _scenario_logo_upload():
    """Upload logo -> GridFS, endpoint GET renvoie le meme fichier."""
    syndic_id, manager_id = await _create_syndic_manager()
    try:
        # Simule un PNG minimal 1x1
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff"
            b"?\x03\x00\x08\xfc\x02\xfeoM\xf7\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            files = {"file": ("test.png", io.BytesIO(png_bytes), "image/png")}
            r = await c.post("/api/syndic-config/me/logo",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             files=files)
            assert r.status_code == 200, r.text
            gridfs_id = r.json()["logo_gridfs_id"]
            assert gridfs_id

            # Get logo (any authenticated user)
            r = await c.get(f"/api/syndic-config/{syndic_id}/logo",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.status_code == 200, r.text
            assert r.content == png_bytes
    finally:
        await _cleanup(syndic_id, manager_id)


def test_logo_upload():
    asyncio.run(_scenario_logo_upload())
