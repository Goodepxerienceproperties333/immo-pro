"""iter90dq : tests pour l'auto-numerotation des factures via prefixe libre
configure sur l'exercice fiscal.
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


async def _setup_scenario():
    """ACP + fiscal year + PCMN account + 1 lot + owner."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id = str(uuid.uuid4())
    account_number = "61300"

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP iter90dq", "address": "Test",
        "postal_code": "1000", "city": "Bruxelles", "reference": "T-DQ",
    })
    # PCMN account
    await db.pcmn_accounts.insert_one({
        "id": str(uuid.uuid4()),
        "copropriete_id": copro_id,
        "number": account_number,
        "name": "Honoraires syndics",
        "type": "expense",
    })
    return {"copro_id": copro_id, "account": account_number}


async def _cleanup(ctx):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await db.coproprietes.delete_many({"id": ctx["copro_id"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.fiscal_years.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.invoices.delete_many({"copropriete_id": ctx["copro_id"]})


async def _test_custom_prefix_used_on_invoice_creation():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            # 1. Create fiscal year with custom prefix
            r = await client.post(
                f"{BASE_URL}/api/fiscal/years",
                json={
                    "name": "FY iter90dq",
                    "start_date": "2027-01-01",
                    "end_date": "2027-12-31",
                    "copropriete_id": ctx["copro_id"],
                    "invoice_number_prefix": "ACACIA-2027-",
                },
            )
            r.raise_for_status()
            assert r.json().get("invoice_number_prefix") == "ACACIA-2027-"

            # 2. Create 3 invoices in that fiscal year -> should use prefix
            for i in range(3):
                r = await client.post(
                    f"{BASE_URL}/api/invoices",
                    json={
                        "number": f"F-{i+100}",
                        "date": "2027-03-15",
                        "supplier": "Test Supplier",
                        "description": f"Test invoice {i+1}",
                        "total_amount": 100.0 + i,
                        "vat_amount": 21.0,
                        "account_number": ctx["account"],
                        "copropriete_id": ctx["copro_id"],
                        "status": "unpaid",
                    },
                )
                assert r.status_code == 200, f"Invoice {i+1} creation failed: {r.text}"
                inv = r.json()
                expected = f"ACACIA-2027-{i+1:04d}"
                assert inv["internal_reference"] == expected, (
                    f"Expected {expected}, got {inv['internal_reference']}"
                )

            # 3. Verify all 3 invoices use ACACIA-2027 prefix in sequence.
            # Note: Invoices with dates OUTSIDE any FY are blocked by the API
            # (fiscal_year requirement), so the FA-YYYY- fallback path in
            # invoices.py is a code safety-net that isn't reachable via the
            # standard POST /invoices endpoint. We only test the happy path.
            r = await client.get(
                f"{BASE_URL}/api/invoices?copropriete_id={ctx['copro_id']}"
            )
            data = r.json()
            invs = data if isinstance(data, list) else data.get("invoices", [])
            refs = sorted([i["internal_reference"] for i in invs])
            assert refs == ["ACACIA-2027-0001", "ACACIA-2027-0002", "ACACIA-2027-0003"], (
                f"Unexpected refs: {refs}"
            )
    finally:
        await _cleanup(ctx)


async def _test_empty_prefix_falls_back_to_default():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            # Create FY WITHOUT custom prefix
            r = await client.post(
                f"{BASE_URL}/api/fiscal/years",
                json={
                    "name": "FY no prefix",
                    "start_date": "2027-01-01",
                    "end_date": "2027-12-31",
                    "copropriete_id": ctx["copro_id"],
                },
            )
            r.raise_for_status()
            # Create invoice -> should use default FA-2027-
            r = await client.post(
                f"{BASE_URL}/api/invoices",
                json={
                    "number": "F-100",
                    "date": "2027-06-15",
                    "supplier": "Test",
                    "description": "Test",
                    "total_amount": 10.0, "vat_amount": 0.0,
                    "account_number": ctx["account"],
                    "copropriete_id": ctx["copro_id"],
                    "status": "unpaid",
                },
            )
            assert r.status_code == 200
            assert r.json()["internal_reference"] == "FA-2027-0001"
    finally:
        await _cleanup(ctx)


async def _test_prefix_update_only_affects_new_invoices():
    """Modifier le prefixe en cours d'exercice ne renumerote pas les factures
    deja creees."""
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            r = await client.post(
                f"{BASE_URL}/api/fiscal/years",
                json={
                    "name": "FY update", "start_date": "2027-01-01",
                    "end_date": "2027-12-31",
                    "copropriete_id": ctx["copro_id"],
                    "invoice_number_prefix": "OLD-",
                },
            )
            r.raise_for_status()
            fy_id = r.json()["id"]

            # 1. Create 2 invoices with OLD- prefix
            for i in range(2):
                r = await client.post(
                    f"{BASE_URL}/api/invoices",
                    json={
                        "number": f"F-{i}",
                        "date": "2027-06-15",
                        "supplier": "S", "description": "D",
                        "total_amount": 10.0, "vat_amount": 0.0,
                        "account_number": ctx["account"],
                        "copropriete_id": ctx["copro_id"],
                        "status": "unpaid",
                    },
                )
                assert r.status_code == 200
                assert r.json()["internal_reference"].startswith("OLD-")

            # 2. Update FY prefix to NEW-
            r = await client.put(
                f"{BASE_URL}/api/fiscal/years/{fy_id}",
                json={
                    "name": "FY update", "start_date": "2027-01-01",
                    "end_date": "2027-12-31",
                    "invoice_number_prefix": "NEW-",
                },
            )
            r.raise_for_status()

            # 3. Create a new invoice -> should use NEW- (sequence resets since
            # count filters by regex on NEW- prefix)
            r = await client.post(
                f"{BASE_URL}/api/invoices",
                json={
                    "number": "F-new",
                    "date": "2027-06-15",
                    "supplier": "S", "description": "D",
                    "total_amount": 10.0, "vat_amount": 0.0,
                    "account_number": ctx["account"],
                    "copropriete_id": ctx["copro_id"],
                    "status": "unpaid",
                },
            )
            assert r.status_code == 200
            assert r.json()["internal_reference"] == "NEW-0001", (
                f"Expected NEW-0001, got {r.json()['internal_reference']}"
            )

            # 4. Verify old invoices still have OLD- prefix (not renumbered)
            all_invs = await client.get(
                f"{BASE_URL}/api/invoices?copropriete_id={ctx['copro_id']}"
            )
            data = all_invs.json()
            refs = [i["internal_reference"] for i in (data if isinstance(data, list) else data.get("invoices", []))]
            old_count = sum(1 for r in refs if r and r.startswith("OLD-"))
            new_count = sum(1 for r in refs if r and r.startswith("NEW-"))
            assert old_count == 2, f"Expected 2 OLD-, got {old_count} in {refs}"
            assert new_count == 1, f"Expected 1 NEW-, got {new_count} in {refs}"
    finally:
        await _cleanup(ctx)


def test_custom_prefix_used_on_invoice_creation():
    asyncio.run(_test_custom_prefix_used_on_invoice_creation())


def test_empty_prefix_falls_back_to_default():
    asyncio.run(_test_empty_prefix_falls_back_to_default())


def test_prefix_update_only_affects_new_invoices():
    asyncio.run(_test_prefix_update_only_affects_new_invoices())
