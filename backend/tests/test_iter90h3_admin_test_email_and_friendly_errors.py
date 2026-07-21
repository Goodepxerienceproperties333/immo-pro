"""iter90h3 — Admin peut tester la config email d'un compte + messages d'erreur clairs.

Contexte utilisateur (Feb 2026) :
  "j'ai edite le super admin pour ses mails mails les donnees ne sont pas
   sauvees en plus il faut un bouton test qui permette de confirmer que
   la configuration est bonne"
  Puis : "l'envoi de mail ne fonctionne pas j'ai teste avec le profile
   super admin dans le cadre tests welcome@... mais le mail n'est ni envoye,
   ni recu"

Root cause : le backend renvoyait un message Azure AD brut ("AADSTS90002:
Tenant '56399ef8-...' not found.") tres peu explicite. En plus le Dialog
de test n'existait pas dans le JSX cote admin.

Fix iter90h3 :
- Nouveau endpoint POST /api/admin/syndic-config/{id}/test-email pour que
  le superadmin teste la config d'un autre compte.
- Messages d'erreur MSGraph traduits en francais lisible :
  - AADSTS90002 -> "Tenant ID Azure introuvable"
  - AADSTS7000215 -> "Client Secret expire ou invalide"
  - MailboxNotFound -> "Boite Exchange n'existe pas / pas de licence"
  - Permission missing -> "Ajoutez la permission Mail.Send + admin consent"
- Frontend Dialog "Tester la configuration" (from/to) qui affiche le message
  clair via toast rouge (8s).
- Apres save, re-fetch du detail pour rafraichir "email_verified" + statut
  "Secret configure ✓".
"""
import os
import asyncio
import bcrypt
import pytest
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

SEED_EMAIL = "iter90h3_seed@example.com"
SEED_PWD = "Seed90h3!TestEmail"


async def _ensure_seed_super(db):
    pw_hash = bcrypt.hashpw(SEED_PWD.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one(
        {"email": SEED_EMAIL},
        {"$set": {
            "email": SEED_EMAIL, "name": "Seed 90h3",
            "role": "superadmin", "password_hash": pw_hash,
            "is_suspended": False, "copropriete_ids": [],
        }},
        upsert=True,
    )
    return await db.users.find_one({"email": SEED_EMAIL})


def _run(coro):
    return asyncio.run(coro)


def test_iter90h3_friendly_error_helpers():
    """Les 2 helpers de traduction d'erreur MSGraph transforment les codes
    AADSTS et Graph en messages francais lisibles."""
    from routes.syndic_config import _friendly_graph_auth_error, _friendly_graph_send_error

    # AADSTS90002 -> Tenant introuvable
    msg = _friendly_graph_auth_error(
        '{"error":"invalid_request","error_description":"AADSTS90002: Tenant \'abc\' not found."}',
        "abc-tenant-id", "some-client-id"
    )
    assert "tenant" in msg.lower() and "abc-tenant-id" in msg
    assert "aadsts" not in msg.lower(), "Le message final ne doit plus contenir le code brut"

    # AADSTS7000215 -> Secret expire
    msg = _friendly_graph_auth_error(
        '{"error":"invalid_client","error_description":"AADSTS7000215: Invalid client secret provided."}',
        "tid", "cid"
    )
    assert "secret" in msg.lower() and ("expire" in msg.lower() or "invalide" in msg.lower())

    # AADSTS700016 -> Client ID inconnu
    msg = _friendly_graph_auth_error(
        '{"error":"unauthorized_client","error_description":"AADSTS700016: Application with identifier not found."}',
        "tid", "my-client-id"
    )
    assert "client" in msg.lower() and "my-client-id" in msg

    # MailboxNotFound -> boite inexistante
    msg = _friendly_graph_send_error(
        '{"error":{"code":"RequestedMailboxNotFound"}}',
        "unknown@domain.be"
    )
    assert "unknown@domain.be" in msg
    assert "n'existe pas" in msg.lower() or "pas d" in msg.lower()

    # AccessDenied -> permission manquante
    msg = _friendly_graph_send_error(
        '{"error":{"code":"AccessDenied","message":"Insufficient authorization for this permission"}}',
        "user@domain.be"
    )
    assert "permission" in msg.lower() and "mail.send" in msg.lower()


def test_iter90h3_admin_test_email_endpoint_registered_and_returns_400_on_bad_config():
    """POST /api/admin/syndic-config/{id}/test-email existe. Sur un compte
    dont la config Graph a un tenant invalide, retourne 400 avec un
    message lisible (traduction AADSTS90002)."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        target_uid = str(u["_id"])
        # Configure une config Graph avec un tenant BIDON
        from crypto_utils import encrypt_secret
        await db.syndic_configs.update_one(
            {"syndic_user_id": target_uid},
            {"$set": {
                "syndic_user_id": target_uid,
                "email_provider": "graph",
                "graph_tenant_id": "00000000-0000-0000-0000-000000000000",
                "graph_client_id": "11111111-1111-1111-1111-111111111111",
                "graph_client_secret": encrypt_secret("faux-secret"),
            }},
            upsert=True,
        )
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=45) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.post(
                    f"/api/admin/syndic-config/{target_uid}/test-email",
                    json={"from_mailbox": SEED_EMAIL, "to": SEED_EMAIL},
                    cookies=cookies,
                )
                assert r.status_code == 400, r.text
                detail = r.json().get("detail", "")
                # Le message doit etre francais et lisible (pas juste un dump JSON brut)
                assert "tenant" in detail.lower(), f"Message peu lisible : {detail}"
                assert len(detail) > 20
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            await db.syndic_configs.delete_one({"syndic_user_id": target_uid})
            mongo.close()
    _run(_t())


def test_iter90h3_admin_test_email_refuses_non_authorized_mailbox():
    """Une boite d'envoi non-associee au compte cible est refusee (403)."""
    async def _t():
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        u = await _ensure_seed_super(db)
        target_uid = str(u["_id"])
        try:
            async with AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
                r_login = await c.post("/api/auth/login",
                                       json={"email": SEED_EMAIL, "password": SEED_PWD})
                assert r_login.status_code == 200
                cookies = {k: v for k, v in r_login.cookies.items()}
                r = await c.post(
                    f"/api/admin/syndic-config/{target_uid}/test-email",
                    json={"from_mailbox": "hacker@evil.example", "to": "test@example.com"},
                    cookies=cookies,
                )
                assert r.status_code == 403
                assert "autorisee" in r.text.lower() or "non autorisee" in r.text.lower()
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            mongo.close()
    _run(_t())


def test_iter90h3_frontend_has_test_button_and_dialog():
    """AdminSyndicConfigPage doit avoir le bouton + le dialog de test."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert 'data-testid="admin-btn-test-email"' in content
    assert 'data-testid="dialog-test-email"' in content
    assert 'data-testid="test-email-from"' in content
    assert 'data-testid="test-email-to"' in content
    assert 'data-testid="test-email-confirm"' in content
    # Statut "Secret configure" doit etre affiche
    assert 'graph-secret-configured' in content
    # Message d'aide "email verifiee"
    assert 'email-verified' in content
