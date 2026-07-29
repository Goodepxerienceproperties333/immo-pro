"""iter93bg : validation stricte "frais privatif" -> proprietaire obligatoire.

Contexte utilisateur (FR) :
  "dans le systeme de facturation on ne peut pas avoir coche la case
   'frais privatif' et sauver la facture sans avoir de proprietaire selectionne"

Verifie que POST /api/invoices et PUT /api/invoices/{id} rejettent tous les
scenarios ou is_private_fee=True mais aucun proprietaire valide n'est
selectionne (ni via `private_fee_allocations`, ni via `private_fee_owner_id`).
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
    r = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    r.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_copro_with_fy(db, cid):
    """Utilitaire : cree ACP + exercice fiscal ouvert."""
    fy_id = f"fy-{cid}"
    await db.coproprietes.insert_one({"id": cid, "name": cid})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "status": "open",
    })
    return fy_id


async def _cleanup_copro(db, cid):
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.coproprietes.delete_one({"id": cid})


def _base_payload(cid, number):
    return {
        "number": number,
        "date": "2026-02-15",
        "due_date": "2026-03-15",
        "supplier": f"Test SRL {number}",
        "description": "Facture de test frais privatif",
        "total_amount": 100.00,
        "vat_amount": 0.0,
        "account_number": "",
        "copropriete_id": cid,
        "status": "unpaid",
    }


async def test_create_private_fee_rejects_no_owner_at_all():
    """POST /api/invoices : is_private_fee=True + AUCUN champ owner -> 400."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-a-{suffix}"
    await _seed_copro_with_fy(db, cid)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = _base_payload(cid, f"T-{suffix}-1")
            payload["is_private_fee"] = True
            # Ni private_fee_allocations, ni private_fee_owner_id
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
            body = r.json()
            detail = body.get("detail", "").lower()
            assert "proprietaire" in detail or "privatif" in detail, \
                f"Unexpected error message: {detail}"
    finally:
        await _cleanup_copro(db, cid)


async def test_create_private_fee_rejects_empty_allocations_list():
    """POST /api/invoices : is_private_fee=True + allocations=[] -> 400."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-b-{suffix}"
    await _seed_copro_with_fy(db, cid)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = _base_payload(cid, f"T-{suffix}-2")
            payload["is_private_fee"] = True
            payload["private_fee_allocations"] = []
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
    finally:
        await _cleanup_copro(db, cid)


async def test_create_private_fee_rejects_allocation_empty_owner_id():
    """POST /api/invoices : is_private_fee=True + alloc avec owner_id='' -> 400."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-c-{suffix}"
    await _seed_copro_with_fy(db, cid)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = _base_payload(cid, f"T-{suffix}-3")
            payload["is_private_fee"] = True
            payload["private_fee_allocations"] = [{"owner_id": "", "amount": 100.0}]
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
            assert "owner_id" in r.json().get("detail", "").lower() \
                or "proprietaire" in r.json().get("detail", "").lower()
    finally:
        await _cleanup_copro(db, cid)


async def test_create_private_fee_rejects_legacy_owner_whitespace():
    """POST /api/invoices : is_private_fee=True + private_fee_owner_id='   ' -> 400."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-d-{suffix}"
    await _seed_copro_with_fy(db, cid)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = _base_payload(cid, f"T-{suffix}-4")
            payload["is_private_fee"] = True
            payload["private_fee_owner_id"] = "   "
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
    finally:
        await _cleanup_copro(db, cid)


async def test_create_private_fee_accepts_valid_allocation():
    """POST /api/invoices : cas nominal -> 200 (invariant : le cas OK marche)."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-e-{suffix}"
    oid = f"o-{suffix}"
    await _seed_copro_with_fy(db, cid)
    await db.owners.insert_one({
        "id": oid, "name": f"Owner_{suffix}",
        "copropriete_ids": [cid],
    })
    inv_id = None
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = _base_payload(cid, f"T-{suffix}-5")
            payload["is_private_fee"] = True
            payload["private_fee_allocations"] = [{"owner_id": oid, "amount": 100.0}]
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            inv_id = r.json().get("id")
    finally:
        if inv_id:
            await db.invoices.delete_one({"id": inv_id})
            await db.journal_entries.delete_many({"invoice_id": inv_id})
        await db.owners.delete_one({"id": oid})
        await _cleanup_copro(db, cid)


async def test_update_private_fee_rejects_no_owner():
    """PUT /api/invoices/{id} : passer une facture existante en frais privatif
    sans proprietaire -> 400."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter93bg-f-{suffix}"
    oid = f"o-{suffix}"
    await _seed_copro_with_fy(db, cid)
    await db.owners.insert_one({
        "id": oid, "name": f"Owner_{suffix}",
        "copropriete_ids": [cid],
    })
    inv_id = None
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # 1) Cree une facture normale (non privatif)
            payload = _base_payload(cid, f"T-{suffix}-6")
            payload["account_number"] = "61300"
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=payload)
            assert r.status_code == 200, r.text
            inv_id = r.json()["id"]

            # 2) PUT pour la passer en frais privatif SANS owner -> 400
            put_payload = _base_payload(cid, f"T-{suffix}-6")
            put_payload["is_private_fee"] = True
            r = await client.put(f"{BACKEND_URL}/api/invoices/{inv_id}", json=put_payload)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

            # 3) PUT avec allocation vide -> 400
            put_payload["private_fee_allocations"] = [{"owner_id": "", "amount": 100.0}]
            r = await client.put(f"{BACKEND_URL}/api/invoices/{inv_id}", json=put_payload)
            assert r.status_code == 400

            # 4) PUT avec allocation valide -> 200
            put_payload["private_fee_allocations"] = [{"owner_id": oid, "amount": 100.0}]
            r = await client.put(f"{BACKEND_URL}/api/invoices/{inv_id}", json=put_payload)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    finally:
        if inv_id:
            await db.invoices.delete_one({"id": inv_id})
            await db.journal_entries.delete_many({"invoice_id": inv_id})
        await db.owners.delete_one({"id": oid})
        await _cleanup_copro(db, cid)


if __name__ == "__main__":
    asyncio.run(test_create_private_fee_rejects_no_owner_at_all())
    print("OK test_create_private_fee_rejects_no_owner_at_all")
    asyncio.run(test_create_private_fee_rejects_empty_allocations_list())
    print("OK test_create_private_fee_rejects_empty_allocations_list")
    asyncio.run(test_create_private_fee_rejects_allocation_empty_owner_id())
    print("OK test_create_private_fee_rejects_allocation_empty_owner_id")
    asyncio.run(test_create_private_fee_rejects_legacy_owner_whitespace())
    print("OK test_create_private_fee_rejects_legacy_owner_whitespace")
    asyncio.run(test_create_private_fee_accepts_valid_allocation())
    print("OK test_create_private_fee_accepts_valid_allocation")
    asyncio.run(test_update_private_fee_rejects_no_owner())
    print("OK test_update_private_fee_rejects_no_owner")
    print("\n=== ALL 6 TESTS PASSED ===")
