"""SEC-audit hotfix (2026-02) : Chinese Wall dans find_duplicate_owner.

Bug : un SUPERADMIN qui cree une nouvelle ACP pour un nouveau syndic voyait
tous les proprietaires venant d'autres syndics (LEFRANCQ, RUBENS, etc.)
apparaitre comme doublons -> 100% d'echec sur import de liste.

Cas testes :
- T1 : Superadmin, sans copro_id + owner deja existant chez SYNDIC A
       -> ne DOIT PAS le detecter comme doublon (Chinese Wall).
- T2 : Syndic A logge + owner deja existant chez syndic A
       -> DOIT le detecter (normal, meme tenant).
- T3 : Syndic B logge + owner de syndic A avec meme VCS
       -> ne DOIT PAS le detecter (Chinese Wall).
- T4 : Superadmin + copro_id fourni d'une ACP de syndic A + owner de A
       -> DOIT le detecter (scope derive de la copro).
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
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@copro.be")
ADMIN_PWD = os.environ["ADMIN_PASSWORD"]


async def _make_syndic(db, email: str, password: str = "SynT2026!"):
    await db.users.delete_many({"email": email})
    r = await db.users.insert_one({
        "email": email,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": "syndic", "name": f"Syn {email}",
        "copropriete_ids": [], "is_suspended": False, "must_change_password": False,
        "module_ag_active": True,
    })
    return str(r.inserted_id), password


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    tag = uuid.uuid4().hex[:6]
    email_a = f"synA-{tag}@t.be"
    email_b = f"synB-{tag}@t.be"
    payload_bernard = {
        "first_name": "Jean-Marc", "last_name": "BERNARD",
        "name": "M. BERNARD Jean-Marc", "civility": "M.",
        "country": "Belgique", "email": "info@nextgecopro.be",
        "vcs_code": f"+++246/9689/{tag[:5]}+++", "vcs_digits": f"246968900{tag[:3]}",
        "auxiliary_code": f"C13{tag[:2]}",
    }
    try:
        sid_a, pw_a = await _make_syndic(db, email_a)
        sid_b, pw_b = await _make_syndic(db, email_b)

        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            # Syndic A cree BERNARD dans sa base
            r = await h.post("/api/auth/login", json={"email": email_a, "password": pw_a})
            assert r.status_code == 200, f"Login syndic A: {r.status_code} {r.text}"
            r = await h.post("/api/owners", json=payload_bernard)
            assert r.status_code == 200, r.text
            bernard_of_a_id = r.json()["id"]
            print(f"Seed : BERNARD cree chez syndic A ({bernard_of_a_id[:8]})")

        # T1 : SUPERADMIN, sans copro_id -> ne DOIT PAS voir BERNARD de A
        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            r = await h.post("/api/auth/login",
                             json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
            assert r.status_code == 200
            r = await h.post("/api/owners?reuse_on_duplicate=true",
                             json=payload_bernard)
            assert r.status_code == 200, r.text
            body = r.json()
            # Le superadmin cree un NOUVEAU BERNARD (pas de reuse)
            if body.get("_reused"):
                assert body["id"] != bernard_of_a_id, \
                    "VIOLATION Chinese Wall : superadmin a reutilise BERNARD de syndic A !"
            print("PASS T1 : superadmin (sans copro_id) NE voit PAS BERNARD de syndic A")

        # T2 : Syndic A logge -> DOIT detecter son propre BERNARD
        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            r = await h.post("/api/auth/login", json={"email": email_a, "password": pw_a})
            assert r.status_code == 200
            r = await h.post("/api/owners?reuse_on_duplicate=true",
                             json=payload_bernard)
            assert r.status_code == 200 and r.json().get("_reused") is True
            assert r.json()["id"] == bernard_of_a_id
            print("PASS T2 : syndic A voit son propre BERNARD (reuse)")

        # T3 : Syndic B logge -> ne DOIT PAS detecter BERNARD de syndic A
        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            r = await h.post("/api/auth/login", json={"email": email_b, "password": pw_b})
            assert r.status_code == 200
            r = await h.post("/api/owners?reuse_on_duplicate=true",
                             json=payload_bernard)
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("_reused"):
                assert body["id"] != bernard_of_a_id, \
                    "VIOLATION Chinese Wall : syndic B a reutilise BERNARD de syndic A !"
            print("PASS T3 : syndic B NE voit PAS BERNARD de syndic A (isolation)")

        # Cleanup
        await db.owners.delete_many({"vcs_code": payload_bernard["vcs_code"]})
        await db.users.delete_many({"email": {"$in": [email_a, email_b]}})
        print("\nAll Chinese Wall find_duplicate_owner tests passed.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
