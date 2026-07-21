"""iter90h6 — Bonus : les 3 protections (anti-fake, lock, audit) s'appliquent
aussi au self-service `PUT /api/syndic-config/me/email`.

Contexte utilisateur (Feb 2026) : "Applique le bonus"

Un syndic connecte peut modifier lui-meme sa config email via /mon-bureau.
Sans les protections iter90h5, il pourrait accidentellement ecraser sa
config avec une valeur de test ou tenter un UUID trivial. Le bonus applique
la meme rigueur qu'au path admin.

Fix iter90h6 :
- `PUT /api/syndic-config/me/email` refuse les secrets de test (400)
- `PUT /api/syndic-config/me/email` refuse les UUID triviaux (400)
- Si l'admin a verrouille la config du syndic, le PUT self renvoie 423
  (avec message indiquant de contacter l'admin)
- Toute modification / rejet est audit-loguee avec `self_service=True`
"""
import os
import asyncio
import bcrypt
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from httpx import AsyncClient
from bson import ObjectId

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BACKEND_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://copro-belge-app.preview.emergentagent.com",
).rstrip("/")

SEED_EMAIL = "iter90h6_syndic_seed@example.com"
SEED_PWD = "Seed90h6!Self"


async def _ensure_syndic(db):
    """Cree/rafraichit un syndic seed pour les tests."""
    pw_hash = bcrypt.hashpw(SEED_PWD.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one(
        {"email": SEED_EMAIL},
        {"$set": {
            "email": SEED_EMAIL, "name": "Iter90h6 Syndic",
            "role": "syndic", "password_hash": pw_hash,
            "is_suspended": False, "copropriete_ids": [],
        }},
        upsert=True,
    )
    return await db.users.find_one({"email": SEED_EMAIL})


def _run(coro):
    return asyncio.run(coro)


def test_iter90h6_self_service_rejects_fake_secret():
    """PUT /syndic-config/me/email refuse un secret de test."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_syndic(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.put(
                    "/api/syndic-config/me/email",
                    json={"provider": "graph", "graph_client_secret": "secret_test_123"},
                    cookies=cookies,
                )
                assert r.status_code == 400, r.text
                assert "test" in r.text.lower() or "placeholder" in r.text.lower()
                # Audit avec flag self_service
                audit = await db.syndic_config_audit.find(
                    {"syndic_user_id": uid, "action": "reject_fake_secret"}
                ).to_list(10)
                assert len(audit) >= 1
                assert audit[0].get("extra", {}).get("self_service") is True
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h6_self_service_rejects_fake_uuid():
    """PUT /syndic-config/me/email refuse un UUID trivial."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_syndic(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.put(
                    "/api/syndic-config/me/email",
                    json={"provider": "graph", "graph_tenant_id": "00000000-0000-0000-0000-000000000000"},
                    cookies=cookies,
                )
                assert r.status_code == 400
                assert "trivial" in r.text.lower() or "uuid" in r.text.lower()
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h6_self_service_locked_by_admin_returns_423():
    """Si l'admin a verrouille la config, le PUT self renvoie 423 Locked
    avec un message indiquant de contacter l'administrateur."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_syndic(db)
        uid = str(u["_id"])
        # Verrouille pre-load
        await db.syndic_configs.update_one(
            {"syndic_user_id": uid},
            {"$set": {
                "syndic_user_id": uid,
                "email_config_locked": True,
                "email_locked_at": "2026-07-17T06:59:51+00:00",
                "email_locked_by_email": "admin@copro.be",
            }},
            upsert=True,
        )
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.put(
                    "/api/syndic-config/me/email",
                    json={"provider": "graph",
                          "graph_tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                          "graph_client_id": "12345678-abcd-1234-abcd-123456789012",
                          "graph_client_secret": "Zx7~Wv3nT8pR2mQ.aH9jY_bL5sK"},
                    cookies=cookies,
                )
                assert r.status_code == 423
                # Message doit indiquer de contacter l'admin (pas Deverouiller soi-meme)
                assert "administrateur" in r.text.lower() or "contactez" in r.text.lower()
                # Message doit contenir l'email de l'admin qui a verrouille
                assert "admin@copro.be" in r.text
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h6_self_service_valid_config_saved_and_audited():
    """Un vrai secret + vrai UUID passent les protections et sont sauves.
    L'audit trail contient l'action email_update avec self_service=True."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_syndic(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.put(
                    "/api/syndic-config/me/email",
                    json={"provider": "graph",
                          "graph_tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                          "graph_client_id": "12345678-abcd-1234-abcd-123456789012",
                          "graph_client_secret": "Zx7~Wv3nT8pR2mQ.aH9jY_bL5sK"},
                    cookies=cookies,
                )
                assert r.status_code == 200, r.text
                # Verifie audit
                audit = await db.syndic_config_audit.find(
                    {"syndic_user_id": uid, "action": "email_update"}
                ).to_list(10)
                assert len(audit) >= 1
                assert audit[0].get("extra", {}).get("self_service") is True
                assert audit[0].get("secret_changed") is True
                assert "graph_client_secret" in audit[0].get("fields_changed", [])
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())
