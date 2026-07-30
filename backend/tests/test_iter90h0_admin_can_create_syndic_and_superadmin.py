"""iter90h0 — Superadmin peut creer des comptes syndic OU superadmin.

Contexte utilisateur (Feb 2026) :
  "creer un nouveau super utilisateur avec [...]"
  Puis : "le super admin doit pouvoir creer tout type de compte syndic ou super administrateur"

Avant le fix, `POST /api/admin/users` refusait tout role autre que 'syndic'
(HTTP 400). Idem pour `PUT /api/admin/users/{id}` : le role etait ge en dur.

Fix iter90h0 :
- `create_user` accepte `role in ('syndic', 'superadmin')`.
- `update_user` accepte la bascule entre les deux.
- Empeche un superadmin de se retrograder lui-meme (safe-net contre le
  scenario ou plus aucun superadmin n'existe).
- Frontend `AdminUsersPage` : Select de role + avertissement violet
  quand 'superadmin' est choisi.
"""
import os
import asyncio
import bcrypt
import pytest
from dotenv import load_dotenv
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from httpx import AsyncClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BACKEND_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://optipro-parser-fix.preview.emergentagent.com",
).rstrip("/")

# Superadmin fixture credentials (idempotent seed)
SEED_EMAIL = "iter90h0_seed_super@example.test"
SEED_PWD = "Seed90h0!SuperTest"


async def _ensure_seed_super(db):
    pw_hash = bcrypt.hashpw(SEED_PWD.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one(
        {"email": SEED_EMAIL},
        {"$set": {
            "email": SEED_EMAIL,
            "name": "Seed 90h0",
            "role": "superadmin",
            "password_hash": pw_hash,
            "is_suspended": False,
            "copropriete_ids": [],
        }},
        upsert=True,
    )


def _run(coro):
    return asyncio.run(coro)


def test_iter90h0_create_syndic_and_superadmin_via_api():
    """Le superadmin peut creer des comptes 'syndic' ET 'superadmin' via
    POST /api/admin/users. Les autres roles restent refuses (400)."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        await _ensure_seed_super(db)
        created_ids = []
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200, r_login.text
                cookies = {k: v for k, v in r_login.cookies.items()}
                # 1. Creer un superadmin
                r_su = await c.post("/api/admin/users",
                                    json={
                                        "email": "iter90h0_new_super@example.test",
                                        "password": "NewSuper90h0!",
                                        "name": "Iter90h0 New Super",
                                        "role": "superadmin",
                                        "must_change_password": False,
                                    },
                                    cookies=cookies)
                assert r_su.status_code == 200, r_su.text
                data_su = r_su.json()
                assert data_su["role"] == "superadmin"
                created_ids.append(data_su["id"])
                # 2. Creer un syndic
                r_sy = await c.post("/api/admin/users",
                                    json={
                                        "email": "iter90h0_new_syndic@example.test",
                                        "password": "NewSynd90h0!",
                                        "name": "Iter90h0 New Syndic",
                                        "role": "syndic",
                                        "must_change_password": False,
                                    },
                                    cookies=cookies)
                assert r_sy.status_code == 200, r_sy.text
                data_sy = r_sy.json()
                assert data_sy["role"] == "syndic"
                created_ids.append(data_sy["id"])
                # 3. Role invalide -> 400
                r_bad = await c.post("/api/admin/users",
                                     json={
                                         "email": "iter90h0_bad@example.test",
                                         "password": "Bad90h0!",
                                         "name": "Iter90h0 Bad",
                                         "role": "gestionnaire",
                                         "must_change_password": False,
                                     },
                                     cookies=cookies)
                assert r_bad.status_code == 400
                assert "syndic" in r_bad.text.lower() and "superadmin" in r_bad.text.lower()
        finally:
            # Cleanup
            from bson import ObjectId
            for uid in created_ids:
                try:
                    await db.users.delete_one({"_id": ObjectId(uid)})
                except Exception:
                    pass
            await db.users.delete_one({"email": SEED_EMAIL})
            mongo.close()
    _run(_t())


def test_iter90h0_switch_role_syndic_to_superadmin_and_reverse():
    """PUT /api/admin/users/{id} accepte la bascule syndic <-> superadmin.
    Un role invalide (ex: gestionnaire) reste refuse."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        await _ensure_seed_super(db)
        target_id = None
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                # Cree un syndic
                r_sy = await c.post("/api/admin/users",
                                    json={
                                        "email": "iter90h0_switch@example.test",
                                        "password": "Switch90h0!",
                                        "name": "Iter90h0 Switch",
                                        "role": "syndic",
                                        "must_change_password": False,
                                    },
                                    cookies=cookies)
                assert r_sy.status_code == 200
                target_id = r_sy.json()["id"]
                # Bascule syndic -> superadmin
                r_up = await c.put(f"/api/admin/users/{target_id}",
                                   json={"role": "superadmin"},
                                   cookies=cookies)
                assert r_up.status_code == 200, r_up.text
                assert r_up.json()["role"] == "superadmin"
                # Bascule superadmin -> syndic
                r_dn = await c.put(f"/api/admin/users/{target_id}",
                                   json={"role": "syndic"},
                                   cookies=cookies)
                assert r_dn.status_code == 200, r_dn.text
                assert r_dn.json()["role"] == "syndic"
                # Role invalide -> 400
                r_bad = await c.put(f"/api/admin/users/{target_id}",
                                    json={"role": "gestionnaire"},
                                    cookies=cookies)
                assert r_bad.status_code == 400
        finally:
            if target_id:
                from bson import ObjectId
                try:
                    await db.users.delete_one({"_id": ObjectId(target_id)})
                except Exception:
                    pass
            await db.users.delete_one({"email": SEED_EMAIL})
            mongo.close()
    _run(_t())


def test_iter90h0_cannot_demote_self():
    """Un superadmin ne peut PAS retrograder son propre compte -> 400.
    Empeche le scenario ou plus aucun superadmin n'existe sur la plateforme."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        await _ensure_seed_super(db)
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                # Recupere son propre id via /auth/me
                me = await c.get("/api/auth/me", cookies=cookies)
                assert me.status_code == 200
                self_id = me.json()["id"]
                # Tentative de retrogradation self -> refuse (400)
                r = await c.put(f"/api/admin/users/{self_id}",
                                json={"role": "syndic"},
                                cookies=cookies)
                assert r.status_code == 400
                assert "retrograder" in r.text.lower() or "vous ne pouvez pas" in r.text.lower()
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            mongo.close()
    _run(_t())


def test_iter90h0_frontend_has_role_select():
    """AdminUsersPage.js expose un Select pour choisir le role a la creation."""
    with open("/app/frontend/src/pages/AdminUsersPage.js") as f:
        content = f.read()
    assert "CREATABLE_ROLES" in content, "AdminUsersPage doit lister les roles creables"
    assert "'syndic'" in content and "'superadmin'" in content
    assert 'data-testid="user-role-select"' in content
    assert "Super Administrateur" in content
    # L'avertissement violet doit apparaitre en mode superadmin
    assert "acces" in content and "TOTAL" in content
