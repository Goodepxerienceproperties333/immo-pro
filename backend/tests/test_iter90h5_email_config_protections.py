"""iter90h5 — Protections contre l'ecrasement de config email + audit log.

Contexte utilisateur (Feb 2026) :
  "Corrige et mets en place des protections pour que ca n'arrive plus"
  "Et mets en place un verrouillage pour ne plus modifier le code a ce sujet"

Incident racine (17/07/2026) : L'agent a ecrase par erreur le Client Secret
du compte `welcome@goodexperienceproperties.be` avec la valeur factice
`secret_test_123` lors d'un test curl de diagnostic. Le vrai secret,
chiffre en DB, est irrecuperable -> l'utilisateur doit regenerer un secret
dans Azure Portal.

Protections livrees (iter90h5) :

1. **Anti-motifs faux** : `_looks_like_fake_secret()` refuse tout secret
   contenant `test_`, `fake_`, `dummy_`, `faux_`, `secret_test`, `1234abcd`,
   `changeme`, `placeholder`, ou trop de caracteres repetes. Rejet 400 avec
   message explicite.

2. **Verrou (lock)** : chaque config peut etre verrouillee.
   - `POST /admin/syndic-config/{id}/email/lock` -> pose le flag
   - `POST /admin/syndic-config/{id}/email/unlock` -> retire le flag
   - Tant que `locked=True`, le PUT sur `/email` renvoie 423 Locked.
   - Le compte welcome@goodexperienceproperties.be a ete verrouille par
     defaut suite a l'incident.

3. **Audit log** : collection `syndic_config_audit`. Chaque modification
   (email_update, lock, unlock, reject_fake_*) est logguee avec :
   - `at`, `actor_user_id`, `actor_email`
   - `fields_changed`, `secret_changed`
   - `snapshot_before` (secrets rediges)
   - `extra` (raison de rejet)
   Endpoint `GET /admin/syndic-config/{id}/audit?limit=50`.
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

SEED_EMAIL = "iter90h5_seed@example.com"
SEED_PWD = "Seed90h5!Protect"


async def _ensure_seed_super(db):
    pw_hash = bcrypt.hashpw(SEED_PWD.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one(
        {"email": SEED_EMAIL},
        {"$set": {
            "email": SEED_EMAIL, "name": "Seed 90h5",
            "role": "superadmin", "password_hash": pw_hash,
            "is_suspended": False, "copropriete_ids": [],
        }},
        upsert=True,
    )
    return await db.users.find_one({"email": SEED_EMAIL})


def _run(coro):
    return asyncio.run(coro)


def test_iter90h5_fake_secret_detector_helper():
    """Helper `_looks_like_fake_secret` detecte les motifs de test evidents."""
    from routes.syndic_config import _looks_like_fake_secret
    # Positifs (doivent etre bloques)
    for bad in [
        "secret_test_123",           # motif exact de l'incident
        "test_secret_abc",
        "changeme_123",
        "fake_secret_xyz",
        "dummy_password",
        "faux-secret",
        "placeholder_val",
        "aaaaaaaa",                  # 8 chars mais 1 seul distinct
        "111111111111",              # trop peu distinct
        "abc",                       # trop court
    ]:
        reason = _looks_like_fake_secret(bad)
        assert reason is not None, f"'{bad}' aurait du etre rejete"

    # Negatifs (secrets legitimes)
    for good in [
        "Kx4gN9v!zP2mQ8wR7tY5eL1uJ3sB",  # secret Azure realiste
        "Zx7~Wv3nT8pR2mQ.aH9jY_bL5sK",
        "supersecure_p@ssw0rd_2026!",
        "3nH8_Kx4gN9v.zP2m",
    ]:
        reason = _looks_like_fake_secret(good)
        assert reason is None, f"'{good}' rejete a tort : {reason}"


def test_iter90h5_fake_uuid_detector_helper():
    """UUID triviaux (0-1 uniquement, tres peu de caracteres distincts) sont detectes."""
    from routes.syndic_config import _looks_like_fake_uuid
    assert _looks_like_fake_uuid("11111111-1111-1111-1111-111111111111") is not None
    assert _looks_like_fake_uuid("00000000-0000-0000-0000-000000000000") is not None
    # UUID Azure realiste doit passer
    assert _looks_like_fake_uuid("56399ef8-d127-479f-ae3e-5edbe4b995c4") is None
    assert _looks_like_fake_uuid("5b245644-0340-4928-b1a9-d83441344aeb") is None


def test_iter90h5_admin_update_email_rejects_fake_secret():
    """Le PUT admin/syndic-config/{id}/email refuse un secret de test avec 400 clair."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                # Secret de test explicite
                r = await c.put(
                    f"/api/admin/syndic-config/{uid}/email",
                    json={"provider": "graph", "graph_client_secret": "secret_test_123"},
                    cookies=cookies,
                )
                assert r.status_code == 400, r.text
                assert "secret" in r.text.lower()
                assert "test" in r.text.lower() or "placeholder" in r.text.lower()
                # Verifie l'audit log
                audit = await db.syndic_config_audit.find(
                    {"syndic_user_id": uid, "action": "reject_fake_secret"}
                ).to_list(10)
                assert len(audit) >= 1
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h5_admin_update_email_rejects_fake_uuid():
    """UUID trivial (11111...) refuse avec 400."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.put(
                    f"/api/admin/syndic-config/{uid}/email",
                    json={"provider": "graph", "graph_tenant_id": "11111111-1111-1111-1111-111111111111"},
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


def test_iter90h5_lock_prevents_email_update():
    """Une config verrouillee refuse tout PUT (/email) avec 423."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                # 1. Lock
                r = await c.post(f"/api/admin/syndic-config/{uid}/email/lock", cookies=cookies)
                assert r.status_code == 200
                assert r.json().get("locked") is True
                # 2. Tentative modif => 423 Locked
                r = await c.put(
                    f"/api/admin/syndic-config/{uid}/email",
                    json={"provider": "graph", "graph_tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                    cookies=cookies,
                )
                assert r.status_code == 423, r.text
                assert "verrouil" in r.text.lower() or "locked" in r.text.lower()
                # 3. Unlock
                r = await c.post(f"/api/admin/syndic-config/{uid}/email/unlock", cookies=cookies)
                assert r.status_code == 200
                assert r.json().get("locked") is False
                # 4. Modif OK apres unlock (avec valeurs valides)
                r = await c.put(
                    f"/api/admin/syndic-config/{uid}/email",
                    json={"provider": "graph",
                          "graph_tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                          "graph_client_id": "12345678-abcd-1234-abcd-123456789012",
                          "graph_client_secret": "Zx7~Wv3nT8pR2mQ.aH9jY_bL5sK"},
                    cookies=cookies,
                )
                assert r.status_code == 200, r.text
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h5_audit_log_records_all_actions():
    """GET /admin/syndic-config/{id}/audit retourne l'historique des actions."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                cookies = {k: v for k, v in r_login.cookies.items()}
                # Serie d'actions
                await c.put(f"/api/admin/syndic-config/{uid}/email",
                            json={"provider": "graph", "graph_client_secret": "secret_test_123"},
                            cookies=cookies)  # sera rejete
                await c.post(f"/api/admin/syndic-config/{uid}/email/lock", cookies=cookies)
                await c.post(f"/api/admin/syndic-config/{uid}/email/unlock", cookies=cookies)
                # Audit
                r = await c.get(f"/api/admin/syndic-config/{uid}/audit", cookies=cookies)
                assert r.status_code == 200, r.text
                rows = r.json().get("audit", [])
                actions = {row["action"] for row in rows}
                # Toutes les actions doivent apparaitre
                assert "reject_fake_secret" in actions
                assert "lock" in actions
                assert "unlock" in actions
                # Actor email preserve
                assert all(row.get("actor_email") == SEED_EMAIL for row in rows)
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": uid})
            await db.syndic_config_audit.delete_many({"syndic_user_id": uid})
            mongo.close()
    _run(_t())


def test_iter90h5_frontend_has_lock_and_audit_ui():
    """AdminSyndicConfigPage expose les boutons Verrouiller/Deverrouiller/Historique."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert 'data-testid="admin-btn-lock-email"' in content
    assert 'data-testid="admin-btn-unlock-email' in content  # lock et unlock
    assert 'data-testid="admin-btn-audit-log"' in content
    assert 'data-testid="dialog-audit-log"' in content
    assert 'data-testid="badge-email-locked"' in content
    # Le bandeau rouge quand locked
    assert 'lock-banner' in content
    # Le bouton Save doit etre desactive quand locked
    assert 'disabled={saving || detail.email_config_locked}' in content
