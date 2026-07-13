"""iter90fd : blocage strict des doublons de fournisseurs. Toute
tentative de creer un fournisseur dont le nom (ou le contenu entre
parentheses) matche une fiche existante DOIT retourner 409.

Scenario Finlead reproduit :
- Fiche existante : "Finlead SRL"
- Tentative de creation : "Finlead Properties (Finlead srl)"
- Resultat attendu : 409 (le contenu de la parenthese matche).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

import httpx  # noqa: E402

BACKEND = "http://localhost:8001"


async def _login():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BACKEND}/api/auth/login",
                         json={"email": "admin@copro.be", "password": "admin123"})
        r.raise_for_status()
        return r.cookies


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    suffix = uuid.uuid4().hex[:6]
    cid = f"iter90fd-cid-{suffix}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90fdACP", "reference": f"T90fd{suffix}",
        "status": "active",
    })
    # Fiche canonique existante
    canonical = {
        "id": f"sup-canon-{suffix}",
        "name": f"Finlead {suffix} SRL",
        "bce_number": f"BE07289908{suffix[:2]}",
        "copropriete_id": cid,
    }
    await db.suppliers.insert_one(canonical)
    return db, cid, canonical, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})


def test_create_supplier_with_parenthesized_duplicate_returns_409():
    """POST /suppliers avec 'Finlead X Properties (Finlead X srl)'
    alors que 'Finlead X SRL' existe -> 409, avec le nom fautif renvoye."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            trade = f"Finlead {suffix} Properties (Finlead {suffix} srl)"
            async with httpx.AsyncClient(cookies=cookies) as c:
                r = await c.post(f"{BACKEND}/api/suppliers", json={
                    "name": trade,
                    "copropriete_id": cid,
                })
                assert r.status_code == 409, (
                    f"iter90fd : creation doit etre refusee 409, got {r.status_code}: {r.text}"
                )
                detail = r.json().get("detail", "").lower()
                assert "doublon" in detail
                # Le nom deja en base doit etre mentionne
                assert canonical["name"].lower() in detail
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_create_supplier_same_as_existing_returns_409():
    """POST /suppliers avec exactement 'Finlead X SRL' -> 409."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            async with httpx.AsyncClient(cookies=cookies) as c:
                r = await c.post(f"{BACKEND}/api/suppliers", json={
                    "name": canonical["name"],
                    "copropriete_id": cid,
                })
                assert r.status_code == 409, r.text
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_update_supplier_to_matching_name_returns_409():
    """PUT /suppliers/{other_id} name='Finlead Properties (Finlead srl)'
    alors que 'Finlead SRL' existe -> 409."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            # Cree un 2eme fournisseur "AutreXY" que l'on va tenter de renommer
            other = {
                "id": f"sup-other-{suffix}",
                "name": f"AutreXY {suffix}",
                "copropriete_id": cid,
            }
            await db.suppliers.insert_one(other)
            trade = f"Finlead {suffix} Properties (Finlead {suffix} srl)"
            async with httpx.AsyncClient(cookies=cookies) as c:
                r = await c.put(
                    f"{BACKEND}/api/suppliers/{other['id']}",
                    json={"name": trade, "copropriete_id": cid},
                )
                assert r.status_code == 409, (
                    f"iter90fd : update vers duplicate doit etre 409, "
                    f"got {r.status_code}: {r.text}"
                )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_bundle_import_skips_parenthesized_duplicates():
    """Le commit d'un CSV de fournisseurs contenant
    'Finlead Properties (Finlead srl)' alors que 'Finlead SRL' existe
    doit skipper la ligne (comptage skipped_duplicates) et non creer
    de doublon."""
    async def _run():
        from routes.suppliers import find_duplicate_supplier
        db, cid, canonical, suffix = await _setup()
        try:
            # test direct de la fonction utilisee par tous les imports
            dup = await find_duplicate_supplier(
                db,
                name=f"Finlead {suffix} Properties (Finlead {suffix} srl)",
                copro_id=cid,
            )
            assert dup is not None, "find_duplicate_supplier doit detecter le doublon"
            assert dup["supplier"]["name"] == canonical["name"]
            assert dup["field"] == "name"
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_create_supplier_with_parenthesized_duplicate_returns_409()
    test_create_supplier_same_as_existing_returns_409()
    test_update_supplier_to_matching_name_returns_409()
    test_bundle_import_skips_parenthesized_duplicates()
    print("OK")
