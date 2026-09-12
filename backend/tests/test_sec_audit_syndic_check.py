"""Tests de securite pour POST /api/auth/syndic-check.

Verifie que :
- Sans token X-Sync-Token -> 401
- Token faible / non defini -> 503 (fail-closed)
- Email inconnu -> 401 "Identifiants invalides"
- Compte superadmin avec BON mdp -> 401 (role != syndic)
- Compte proprietaire avec BON mdp -> 401 (role != syndic)
- Compte syndic BON mdp -> 200 valid:true
- Compte syndic MAUVAIS mdp -> 401
- Compte syndic suspendu -> 403
- Compte syndic must_change_password -> 403
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
TOKEN = os.environ["EXPORT_SYNC_TOKEN"]


async def _make_user(db, email: str, role: str, password: str = "TestPass2026!", **extra):
    await db.users.delete_many({"email": email})
    doc = {
        "email": email,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": role,
        "name": f"Test {role}",
        "copropriete_ids": [],
        "is_suspended": False,
        "must_change_password": False,
    }
    doc.update(extra)
    r = await db.users.insert_one(doc)
    return str(r.inserted_id), password


async def _cleanup(db, emails):
    for e in emails:
        await db.users.delete_many({"email": e})


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    tag = uuid.uuid4().hex[:6]
    e_syn = f"syn-{tag}@t.be"
    e_susp = f"susp-{tag}@t.be"
    e_init = f"init-{tag}@t.be"
    e_own = f"own-{tag}@t.be"
    e_sadm = f"sadm-{tag}@t.be"

    try:
        _, pw_syn = await _make_user(db, e_syn, "syndic")
        _, pw_susp = await _make_user(db, e_susp, "syndic", is_suspended=True)
        _, pw_init = await _make_user(db, e_init, "syndic", must_change_password=True)
        _, pw_own = await _make_user(db, e_own, "owner")
        _, pw_sadm = await _make_user(db, e_sadm, "superadmin")

        async with httpx.AsyncClient(base_url=API, timeout=10.0) as h:
            # T1 : sans token
            r = await h.post("/api/auth/syndic-check", json={"email": e_syn, "password": pw_syn})
            assert r.status_code == 401 and "sync" in r.json()["detail"].lower(), r.text
            print("PASS T1 sans token -> 401 sync")

            hdr = {"X-Sync-Token": TOKEN}

            # T2 : email inconnu
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": "unknown@nowhere.be", "password": "x"})
            assert r.status_code == 401 and r.json()["detail"] == "Identifiants invalides"
            print("PASS T2 email inconnu -> 401")

            # T3 : owner avec BON mdp -> refuse (role != syndic)
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_own, "password": pw_own})
            assert r.status_code == 401 and r.json()["detail"] == "Identifiants invalides"
            print("PASS T3 owner refuse -> 401")

            # T4 : superadmin avec BON mdp -> refuse (role != syndic)
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_sadm, "password": pw_sadm})
            assert r.status_code == 401 and r.json()["detail"] == "Identifiants invalides"
            print("PASS T4 superadmin refuse -> 401")

            # T5 : syndic BON mdp -> 200
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_syn, "password": pw_syn})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["valid"] is True
            assert body["user"]["role"] == "syndic"
            assert body["user"]["email"] == e_syn
            print("PASS T5 syndic OK -> 200 valid:true")

            # T6 : syndic MAUVAIS mdp -> 401
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_syn, "password": "wrong"})
            assert r.status_code == 401
            print("PASS T6 mauvais mdp -> 401")

            # T7 : syndic suspendu -> 403
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_susp, "password": pw_susp})
            assert r.status_code == 403 and "suspendu" in r.json()["detail"].lower()
            print("PASS T7 suspendu -> 403")

            # T8 : syndic must_change_password -> 403
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": e_init, "password": pw_init})
            assert r.status_code == 403 and "non initialise" in r.json()["detail"].lower()
            print("PASS T8 must_change_password -> 403")

            # T9 : payload invalide (pas email) -> 422
            r = await h.post("/api/auth/syndic-check", headers=hdr,
                             json={"email": "not-an-email", "password": "x"})
            assert r.status_code == 422
            print("PASS T9 email invalide -> 422")

        print("\nAll syndic-check security tests passed.")
    finally:
        await _cleanup(db, [e_syn, e_susp, e_init, e_own, e_sadm])
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
