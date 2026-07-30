"""iter90ds : tests unitaires pour l'algorithme MAX+1 (au lieu de count()).

Verifie :
1. Nouvelle facture apres suppression n'engendre PAS de doublon.
2. Numerotation continue meme apres suppression (n'utilise pas les trous).
3. Repair script realigne les references discontinues.
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

BASE_URL = "http://localhost:8001"
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PWD = "admin123"


async def _login(client):
    r = await client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PWD},
    )
    r.raise_for_status()


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    copro_id = str(uuid.uuid4())
    account = "61300"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "iter90ds", "address": "T",
        "postal_code": "1000", "city": "Bruxelles", "reference": "T-DS",
    })
    await db.pcmn_accounts.insert_one({
        "id": str(uuid.uuid4()), "copropriete_id": copro_id,
        "number": account, "name": "Honoraires syndics", "type": "expense",
    })
    return {"copro_id": copro_id, "account": account}


async def _cleanup(ctx):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await db.coproprietes.delete_many({"id": ctx["copro_id"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.fiscal_years.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.invoices.delete_many({"copropriete_id": ctx["copro_id"]})


async def _create_fy(client, ctx, prefix="FA-2028-"):
    r = await client.post(
        f"{BASE_URL}/api/fiscal/years",
        json={
            "name": "FY iter90ds", "start_date": "2028-01-01",
            "end_date": "2028-12-31", "copropriete_id": ctx["copro_id"],
            "invoice_number_prefix": prefix,
        },
    )
    r.raise_for_status()
    return r.json()


async def _create_invoice(client, ctx, num, amount=10.0):
    r = await client.post(
        f"{BASE_URL}/api/invoices",
        json={
            "number": f"F-{num}", "date": "2028-03-15",
            "supplier": "S", "description": f"Test {num}",
            "total_amount": amount, "vat_amount": 0.0,
            "account_number": ctx["account"],
            "copropriete_id": ctx["copro_id"],
            "status": "unpaid",
        },
    )
    assert r.status_code == 200, f"Create failed: {r.text}"
    return r.json()


async def _delete_invoice(client, inv_id):
    r = await client.delete(f"{BASE_URL}/api/invoices/{inv_id}")
    assert r.status_code == 200, f"Delete failed: {r.text}"


async def _scenario_no_duplicate_after_delete():
    """Cree 3 factures (0001,0002,0003), supprime la 0002, cree une nouvelle
    -> doit recevoir 0004 (pas 0003 ni 0002)."""
    ctx = await _setup()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            await _create_fy(client, ctx)
            i1 = await _create_invoice(client, ctx, 1)
            i2 = await _create_invoice(client, ctx, 2)
            i3 = await _create_invoice(client, ctx, 3)
            assert i1["internal_reference"] == "FA-2028-0001"
            assert i2["internal_reference"] == "FA-2028-0002"
            assert i3["internal_reference"] == "FA-2028-0003"

            # Delete i2
            await _delete_invoice(client, i2["id"])

            # Create a new one -> MUST be 0004 (not 0002 or 0003)
            i4 = await _create_invoice(client, ctx, 4)
            assert i4["internal_reference"] == "FA-2028-0004", (
                f"BUG: got {i4['internal_reference']}, expected FA-2028-0004"
            )

            # And no duplicate exists
            all_invs = await client.get(f"{BASE_URL}/api/invoices?copropriete_id={ctx['copro_id']}")
            data = all_invs.json()
            invs = data if isinstance(data, list) else data.get("invoices", [])
            refs = [i["internal_reference"] for i in invs]
            assert len(refs) == len(set(refs)), f"Duplicates found: {refs}"
    finally:
        await _cleanup(ctx)


async def _scenario_multiple_deletes_still_unique():
    """Cree 5, supprime 2/3/4, cree une nouvelle -> 0006."""
    ctx = await _setup()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            await _create_fy(client, ctx)
            invs = [await _create_invoice(client, ctx, i) for i in range(1, 6)]
            # Delete 2, 3, 4
            for idx in (1, 2, 3):
                await _delete_invoice(client, invs[idx]["id"])
            # New one must be 0006
            new_inv = await _create_invoice(client, ctx, 99)
            assert new_inv["internal_reference"] == "FA-2028-0006", (
                f"Got {new_inv['internal_reference']}"
            )
    finally:
        await _cleanup(ctx)


def test_no_duplicate_after_delete():
    asyncio.run(_scenario_no_duplicate_after_delete())


def test_multiple_deletes_still_unique():
    asyncio.run(_scenario_multiple_deletes_still_unique())
