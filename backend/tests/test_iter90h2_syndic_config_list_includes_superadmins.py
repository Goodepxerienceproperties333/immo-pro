"""iter90h2 — Config email : la liste admin doit inclure les superadmins.

Contexte utilisateur (Feb 2026) :
  Screenshot de la page /admin/syndic-config affichant uniquement 4 syndics :
  "je ne vois pas les super admin sur cette page :-)"

Root cause :
  GET /api/admin/syndic-config/list filtrait `{"role": "syndic"}` uniquement.
  Les super admins (co-gestionnaires plateforme qui envoient aussi des
  emails aux syndics/owners) etaient absents.

Fix iter90h2 :
  Le filtre passe a `{"role": {"$in": ["syndic", "superadmin"]}}`.
  La reponse ajoute `user_role` pour permettre au frontend d'afficher un
  badge distinct (violet pour Super Admin, bleu pour Syndic).
"""
import os
import asyncio
import bcrypt
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from httpx import AsyncClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BACKEND_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://teuwen-reports.preview.emergentagent.com",
).rstrip("/")

SEED_EMAIL = "iter90h2_seed@example.test"
SEED_PWD = "Seed90h2!List"


async def _ensure_seed_super(db):
    pw_hash = bcrypt.hashpw(SEED_PWD.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one(
        {"email": SEED_EMAIL},
        {"$set": {
            "email": SEED_EMAIL, "name": "Seed 90h2",
            "role": "superadmin", "password_hash": pw_hash,
            "is_suspended": False, "copropriete_ids": [],
        }},
        upsert=True,
    )


def _run(coro):
    return asyncio.run(coro)


def test_iter90h2_syndic_config_list_includes_superadmins():
    """La liste /api/admin/syndic-config/list contient a la fois syndics
    et superadmins, avec un champ `user_role` pour differencier."""
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
                r = await c.get("/api/admin/syndic-config/list", cookies=cookies)
                assert r.status_code == 200, r.text
                data = r.json()
                syndics = data.get("syndics", [])
                # Au moins 1 superadmin (notre seed) doit apparaitre
                supers = [s for s in syndics if s.get("user_role") == "superadmin"]
                assert len(supers) >= 1, "Aucun superadmin dans la liste"
                # Notre seed doit y etre
                assert any(s.get("user_email") == SEED_EMAIL for s in supers)
                # Chaque entree a un champ user_role explicite
                for s in syndics:
                    assert s.get("user_role") in ("syndic", "superadmin"), s
        finally:
            await db.users.delete_one({"email": SEED_EMAIL})
            mongo.close()
    _run(_t())


def test_iter90h2_frontend_shows_role_badge():
    """AdminSyndicConfigPage.js expose des badges Role distincts pour
    syndic vs superadmin."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert "user_role === 'superadmin'" in content, "Badge superadmin manquant"
    assert "Super Admin" in content
    assert "role-badge-" in content, "Test-ID data-testid=role-badge-{id} manquant"


def test_iter90h2_backend_query_uses_in_operator():
    """Le filtre MongoDB doit utiliser $in pour syndic + superadmin."""
    with open("/app/backend/routes/syndic_config.py") as f:
        content = f.read()
    assert '"role": {"$in": ["syndic", "superadmin"]}' in content, (
        "Le filtre role doit inclure syndic ET superadmin"
    )
