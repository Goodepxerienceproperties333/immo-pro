"""Tests du champ module_ag_active + verification /api/auth/syndic-check.

Verifie :
- Le champ module_ag_active est stocke en base et retourne par l'API admin.
- Seul le superadmin peut le modifier (PUT /api/admin/users/{id}).
- syndic-check refuse la connexion (403) si module_ag_active=False ou absent.
- syndic-check accepte (200) si module_ag_active=True + reste des criteres OK.
- Le champ apparait dans la reponse valide (`valid.user.module_ag_active`).
- Un syndic (non-superadmin) ne peut PAS bascule le champ.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
SYNC_TOKEN = os.environ["EXPORT_SYNC_TOKEN"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@copro.be")
ADMIN_PWD = os.environ["ADMIN_PASSWORD"]


async def _make_syndic(db, email: str, password: str, **extra):
    await db.users.delete_many({"email": email})
    doc = {
        "email": email,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": "syndic",
        "name": f"Syn {email}",
        "copropriete_ids": [],
        "is_suspended": False,
        "must_change_password": False,
    }
    doc.update(extra)
    r = await db.users.insert_one(doc)
    return str(r.inserted_id)


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    tag = uuid.uuid4().hex[:6]
    email = f"syn-agmodule-{tag}@t.be"
    pwd = "SynAG2026!"

    try:
        user_id = await _make_syndic(db, email, pwd)  # PAS de module_ag_active -> defaut False

        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            hdr = {"X-Sync-Token": SYNC_TOKEN}

            # T1 : sans module_ag_active -> 403
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": email, "password": pwd})
            assert r.status_code == 403, r.text
            assert "module ag" in r.json()["detail"].lower()
            print("PASS T1 module absent -> 403")

            # T2 : login superadmin
            r = await h.post("/api/auth/login",
                             json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
            assert r.status_code == 200, r.text
            adm_token = r.cookies.get("access_token")
            assert adm_token, "no access_token cookie returned"
            adm_hdr = {"Authorization": f"Bearer {adm_token}"}

            # T3 : PUT admin/users active le module
            r = await h.put(f"/api/admin/users/{user_id}", headers=adm_hdr,
                            json={"module_ag_active": True})
            assert r.status_code == 200, r.text
            assert r.json()["module_ag_active"] is True
            print("PASS T3 superadmin peut activer -> True")

            # T4 : GET admin/users retourne le champ
            r = await h.get("/api/admin/users", headers=adm_hdr)
            u = next((x for x in r.json() if x["id"] == user_id), None)
            assert u and u.get("module_ag_active") is True
            print("PASS T4 champ expose dans list_users")

            # T5 : syndic-check accepte
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": email, "password": pwd})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["valid"] is True
            assert body["user"]["module_ag_active"] is True
            print("PASS T5 module actif -> 200 + module_ag_active:true")

            # T6 : PUT admin desactive
            r = await h.put(f"/api/admin/users/{user_id}", headers=adm_hdr,
                            json={"module_ag_active": False})
            assert r.status_code == 200 and r.json()["module_ag_active"] is False
            print("PASS T6 superadmin peut desactiver -> False")

            # T7 : syndic-check refuse a nouveau
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": email, "password": pwd})
            assert r.status_code == 403
            print("PASS T7 module desactive -> 403")

            # T8 : mauvais mdp toujours 401 (mdp prioritaire sur module_ag)
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": email, "password": "WRONG"})
            assert r.status_code == 401
            print("PASS T8 mauvais mdp -> 401 (mdp check avant module_ag)")

            # T9 : un syndic (non-superadmin) ne peut PAS bascule le champ.
            r = await h.post("/api/auth/login", json={"email": email, "password": pwd})
            assert r.status_code == 200
            syn_token = r.cookies.get("access_token")
            syn_hdr = {"Authorization": f"Bearer {syn_token}"}
            r = await h.put(f"/api/admin/users/{user_id}", headers=syn_hdr,
                            json={"module_ag_active": True})
            assert r.status_code == 403, f"Syndic should be blocked by RBAC, got {r.status_code}"
            print("PASS T9 syndic non-superadmin bloque par RBAC -> 403")

        print("\nAll module_ag_active tests passed.")
    finally:
        await db.users.delete_many({"email": email})
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
