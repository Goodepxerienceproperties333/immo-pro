"""iter90gi : POST /api/owners?reuse_on_duplicate=true retourne l'existant
au lieu de lever une 409.

**Ticket utilisateur** :
> "message d'erreur lors de l'import de propriétaires dans le wizzard : 0
> proprietaire(s) crees (11 echec(s))"

**Cause** : lors d'un ré-import CSV/PDF Optipro, l'anti-doublon (nom +
prenom, email, telephone, BCE, adresse) rejette TOUS les owners deja
en base -> 0 crees, 11 echecs.

**Fix iter90gi** :
1. Backend : `POST /api/owners?reuse_on_duplicate=true` retourne l'owner
   existant avec `_reused: true` (statut 200) au lieu de lever HTTPException(409).
2. Rattachement optionnel a l'ACP courante via `copropriete_id`.
3. Frontend : les wizards CSV/PDF passent le flag et affichent
   `X crees + Y reutilises`.

Ces tests verrouillent le contrat backend.
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def test_reuse_on_duplicate_returns_existing_owner():
    """Un POST /owners qui matche un existant en base retourne l'existant
    (statut 200, `_reused: true`) au lieu d'un 409.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    seed_id = f"iter90gi-seed-{suffix}"
    seed_name = f"TESTGI_{suffix} Reuse"
    await db.owners.insert_one({
        "id": seed_id, "first_name": "Reuse", "last_name": f"TESTGI_{suffix}",
        "name": seed_name, "country": "Belgique",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # 1) Sans le flag -> 409
            r1 = await client.post(f"{BACKEND_URL}/api/owners", json={
                "first_name": "Reuse", "last_name": f"TESTGI_{suffix}",
                "name": seed_name, "country": "Belgique",
            })
            assert r1.status_code == 409, f"expected 409, got {r1.status_code}: {r1.text}"

            # 2) Avec le flag -> 200 + _reused:true + meme id
            r2 = await client.post(
                f"{BACKEND_URL}/api/owners?reuse_on_duplicate=true",
                json={
                    "first_name": "Reuse", "last_name": f"TESTGI_{suffix}",
                    "name": seed_name, "country": "Belgique",
                },
            )
            assert r2.status_code == 200, f"expected 200, got {r2.status_code}: {r2.text}"
            body = r2.json()
            assert body.get("_reused") is True, f"expected _reused=true: {body}"
            assert body.get("id") == seed_id, f"expected same id: {body.get('id')} vs {seed_id}"
            assert body.get("_dup_field") == "name", f"expected _dup_field=name: {body.get('_dup_field')}"
    finally:
        await db.owners.delete_one({"id": seed_id})


async def test_reuse_on_duplicate_attaches_to_copropriete():
    """Quand `reuse_on_duplicate=true` + `copropriete_id` fourni, l'owner
    existant (deja rattache a l'ACP) est reutilise et le rattachement
    reste idempotent (pas de doublon dans copropriete_ids[]).
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    seed_id = f"iter90gi-seed2-{suffix}"
    copro_id = f"iter90gi-copro-{suffix}"
    # seed : owner deja dans l'ACP (cas typique d'un re-import)
    await db.owners.insert_one({
        "id": seed_id, "first_name": "Attach", "last_name": f"TESTGI2_{suffix}",
        "name": f"TESTGI2_{suffix} Attach", "country": "Belgique",
        "copropriete_id": copro_id,
        "copropriete_ids": [copro_id],
    })
    await db.coproprietes.insert_one({
        "id": copro_id, "name": f"iter90gi-{suffix}",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/owners?reuse_on_duplicate=true",
                json={
                    "first_name": "Attach", "last_name": f"TESTGI2_{suffix}",
                    "name": f"TESTGI2_{suffix} Attach", "country": "Belgique",
                    "copropriete_id": copro_id,
                },
            )
            assert r.status_code == 200, f"got {r.status_code}: {r.text}"
            body = r.json()
            assert body.get("_reused") is True, f"expected _reused=true: {body}"
            assert body.get("id") == seed_id

            # Idempotent : re-appel ne cree pas de doublon dans copropriete_ids
            r2 = await client.post(
                f"{BACKEND_URL}/api/owners?reuse_on_duplicate=true",
                json={
                    "first_name": "Attach", "last_name": f"TESTGI2_{suffix}",
                    "name": f"TESTGI2_{suffix} Attach", "country": "Belgique",
                    "copropriete_id": copro_id,
                },
            )
            assert r2.status_code == 200
            updated2 = await db.owners.find_one({"id": seed_id}, {"_id": 0})
            copros = updated2.get("copropriete_ids") or []
            assert copros.count(copro_id) == 1, f"expected exactly 1 occurrence: {copros}"
    finally:
        await db.owners.delete_one({"id": seed_id})
        await db.coproprietes.delete_one({"id": copro_id})


async def test_no_reuse_flag_still_creates_new_when_no_duplicate():
    """Sans doublon en base, l'endpoint cree un nouveau owner (regression
    guard : le flag `reuse_on_duplicate` ne casse pas le cas nominal).
    """
    async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
        await _login(client)
        unique = uuid.uuid4().hex[:8]
        r = await client.post(
            f"{BACKEND_URL}/api/owners?reuse_on_duplicate=true",
            json={
                "first_name": "New", "last_name": f"TESTGI3_{unique}",
                "name": f"TESTGI3_{unique} New", "country": "Belgique",
            },
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("_reused") is not True, f"should NOT be reused: {body}"
        assert body.get("id"), "should have id"
        # Cleanup
        db = await _mongo()
        await db.owners.delete_one({"id": body["id"]})


if __name__ == "__main__":
    asyncio.run(test_reuse_on_duplicate_returns_existing_owner())
    print("OK test_reuse_on_duplicate_returns_existing_owner")
    asyncio.run(test_reuse_on_duplicate_attaches_to_copropriete())
    print("OK test_reuse_on_duplicate_attaches_to_copropriete")
    asyncio.run(test_no_reuse_flag_still_creates_new_when_no_duplicate())
    print("OK test_no_reuse_flag_still_creates_new_when_no_duplicate")
