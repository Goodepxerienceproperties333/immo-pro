"""iter90fa : snap-to-card - une facture creee avec un nom fournisseur
libre qui matche (par normalisation legale) une fiche existante voit
son champ `supplier` remplace par le nom canonique de la fiche.
Empeche la coexistence "Finlead" (libre) + "Finlead SRL" (fiche).
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
    cid = f"iter90fa-cid-{suffix}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90faACP", "reference": f"T90fa{suffix}",
        "status": "active",
    })
    # Exercice fiscal ouvert couvrant 2026-01-15
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid, "name": "2025-2026",
        "start_date": "2025-10-01", "end_date": "2026-09-30", "status": "active",
    })
    await db.pcmn_accounts.insert_many([
        {"number": f"6140{suffix[:2]}", "name": "Charge test", "class_num": 6,
         "copropriete_id": cid},
        {"number": f"4400001", "name": "Fournisseur",
         "class_num": 4, "copropriete_id": cid},
    ])
    # Fiche canonique existante : "Finlead SRL"
    canonical = {
        "id": f"sup-{suffix}",
        "name": f"Finlead {suffix} SRL",
        "bce_number": f"BE07289908{suffix[:2]}",
        "copropriete_id": cid,
    }
    await db.suppliers.insert_one(canonical)
    return db, cid, canonical, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_free_text_supplier_snaps_to_existing_card():
    """Facture creee avec 'Finlead' (libre) alors que la fiche 'Finlead SRL'
    existe -> le supplier doit etre remplace par 'Finlead SRL'."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            # Cas 1 : nom simple sans SRL
            simple_name = canonical["name"].replace(" SRL", "")
            async with httpx.AsyncClient(cookies=cookies) as c:
                r = await c.post(f"{BACKEND}/api/invoices", json={
                    "supplier": simple_name,
                    "number": f"FA-1-{suffix}",
                    "date": "2026-01-15",
                    "total_amount": 100.0,
                    "account_number": f"6140{suffix[:2]}",
                    "copropriete_id": cid,
                    "description": "Test 1",
                })
                assert r.status_code == 200, r.text
                inv_id = r.json()["id"]
            inv = await db.invoices.find_one({"id": inv_id})
            assert inv["supplier"] == canonical["name"], (
                f"Snap-to-card failed: expected '{canonical['name']}', got '{inv['supplier']}'"
            )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_free_text_supplier_wrong_case_and_particles():
    """Test avec variations SRL/sprl/en desordre + case mixte."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            # Nom bizarre : 'srl FINLEAD {suffix}' (particule en 1er, case up)
            weird = f"srl FINLEAD {suffix}"
            async with httpx.AsyncClient(cookies=cookies) as c:
                r = await c.post(f"{BACKEND}/api/invoices", json={
                    "supplier": weird,
                    "number": f"FA-2-{suffix}",
                    "date": "2026-01-15",
                    "total_amount": 100.0,
                    "account_number": f"6140{suffix[:2]}",
                    "copropriete_id": cid,
                    "description": "Test 2",
                })
                assert r.status_code == 200, r.text
                inv_id = r.json()["id"]
            inv = await db.invoices.find_one({"id": inv_id})
            assert inv["supplier"] == canonical["name"], (
                f"Snap-to-card failed pour '{weird}': "
                f"expected '{canonical['name']}', got '{inv['supplier']}'"
            )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_unknown_supplier_stays_as_is():
    """Un fournisseur totalement different ne doit pas etre snappe."""
    async def _run():
        db, cid, canonical, suffix = await _setup()
        cookies = await _login()
        try:
            async with httpx.AsyncClient(cookies=cookies) as c:
                other = f"AutreFournisseur {suffix}"
                r = await c.post(f"{BACKEND}/api/invoices", json={
                    "supplier": other,
                    "number": f"FA-3-{suffix}",
                    "date": "2026-01-15",
                    "total_amount": 100.0,
                    "account_number": f"6140{suffix[:2]}",
                    "copropriete_id": cid,
                    "description": "Test 3",
                })
                assert r.status_code == 200, r.text
                inv_id = r.json()["id"]
            inv = await db.invoices.find_one({"id": inv_id})
            assert inv["supplier"] == other, (
                f"Un fournisseur unknown ne doit pas etre snappe : got '{inv['supplier']}'"
            )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_free_text_supplier_snaps_to_existing_card()
    test_free_text_supplier_wrong_case_and_particles()
    test_unknown_supplier_stays_as_is()
    print("OK")
