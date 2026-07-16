"""iter90gj Phase 3 : endpoints d'assignation des proprietaires beneficiaires
pour les frais privatifs.

**Contrats** :
- GET /api/invoices/private-fees-pending?copropriete_id=X
  -> liste des factures is_private_fee=True sans allocation.
- POST /api/invoices/{id}/private-fee-allocations
  { allocations: [{owner_id, amount}, ...] }
  -> assigne les proprietaires + validation : sum(amount) == total_amount.
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


async def test_pending_and_allocation_happy_path():
    """1) Cree 1 facture privatif + 2 owners.
    2) GET pending -> retourne la facture.
    3) POST allocations -> assigne 60/40 aux 2 owners.
    4) GET pending -> facture disparait de la liste.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gj3-{suffix}"
    inv_id = f"inv-{suffix}"
    o1_id = f"o1-{suffix}"
    o2_id = f"o2-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj3-{suffix}"})
    await db.owners.insert_many([
        {"id": o1_id, "name": f"Owner1_{suffix}", "copropriete_ids": [cid]},
        {"id": o2_id, "name": f"Owner2_{suffix}", "copropriete_ids": [cid]},
    ])
    await db.invoices.insert_one({
        "id": inv_id, "copropriete_id": cid,
        "number": f"PRV-{suffix}", "internal_reference": "FA-2026-0001",
        "date": "2026-05-01", "supplier": "SRL Test",
        "description": "Frais privatif test",
        "total_amount": 100.00, "account_number": "643001",
        "is_private_fee": True, "private_fee_allocations": [],
        "created_at": "2026-05-01T00:00:00+00:00",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)

            # 1) List pending
            r = await client.get(f"{BACKEND_URL}/api/invoices/private-fees-pending",
                                  params={"copropriete_id": cid})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["count"] >= 1
            assert any(i["id"] == inv_id for i in body["invoices"])

            # 2) Allocate 60/40
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/{inv_id}/private-fee-allocations",
                json={"allocations": [
                    {"owner_id": o1_id, "amount": 60.00},
                    {"owner_id": o2_id, "amount": 40.00},
                ]},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["allocations"]) == 2

            # 3) DB check
            inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
            assert inv["private_fee_allocations"][0]["owner_id"] == o1_id
            assert inv["private_fee_allocations"][0]["amount"] == 60.0
            assert inv["private_fee_allocations"][1]["owner_id"] == o2_id
            assert inv["private_fee_owner_id"] == "", "primary vide car >1 owner"

            # 4) List pending -> facture n'apparait plus
            r = await client.get(f"{BACKEND_URL}/api/invoices/private-fees-pending",
                                  params={"copropriete_id": cid})
            assert r.status_code == 200
            assert not any(i["id"] == inv_id for i in r.json()["invoices"])
    finally:
        await db.invoices.delete_one({"id": inv_id})
        await db.owners.delete_many({"id": {"$in": [o1_id, o2_id]}})
        await db.coproprietes.delete_one({"id": cid})


async def test_allocation_rejects_sum_mismatch():
    """La somme des montants doit egaler total_amount (tolerance 0.01)."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gj3b-{suffix}"
    inv_id = f"inv2-{suffix}"
    o1_id = f"o1b-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj3b-{suffix}"})
    await db.owners.insert_one({"id": o1_id, "name": f"Ob_{suffix}",
                                  "copropriete_ids": [cid]})
    await db.invoices.insert_one({
        "id": inv_id, "copropriete_id": cid, "number": f"PRV-{suffix}",
        "date": "2026-05-01", "supplier": "T", "total_amount": 100.00,
        "account_number": "643001", "is_private_fee": True,
        "private_fee_allocations": [],
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # Somme = 50 mais total = 100 -> rejet
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/{inv_id}/private-fee-allocations",
                json={"allocations": [{"owner_id": o1_id, "amount": 50.00}]},
            )
            assert r.status_code == 400
            assert "somme" in r.json()["detail"].lower() or "egaler" in r.json()["detail"].lower()

            # Somme = 100 -> OK
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/{inv_id}/private-fee-allocations",
                json={"allocations": [{"owner_id": o1_id, "amount": 100.00}]},
            )
            assert r.status_code == 200
    finally:
        await db.invoices.delete_one({"id": inv_id})
        await db.owners.delete_one({"id": o1_id})
        await db.coproprietes.delete_one({"id": cid})


async def test_allocation_rejects_non_private_fee():
    """Une facture is_private_fee=False ne peut pas recevoir d'allocations."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gj3c-{suffix}"
    inv_id = f"inv3-{suffix}"
    o1_id = f"o1c-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj3c-{suffix}"})
    await db.owners.insert_one({"id": o1_id, "name": "T", "copropriete_ids": [cid]})
    await db.invoices.insert_one({
        "id": inv_id, "copropriete_id": cid, "number": "STD-001",
        "date": "2026-05-01", "supplier": "T", "total_amount": 100.00,
        "account_number": "61300", "is_private_fee": False,
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/{inv_id}/private-fee-allocations",
                json={"allocations": [{"owner_id": o1_id, "amount": 100.00}]},
            )
            assert r.status_code == 400
            assert "privatif" in r.json()["detail"].lower()
    finally:
        await db.invoices.delete_one({"id": inv_id})
        await db.owners.delete_one({"id": o1_id})
        await db.coproprietes.delete_one({"id": cid})


if __name__ == "__main__":
    asyncio.run(test_pending_and_allocation_happy_path())
    print("OK test_pending_and_allocation_happy_path")
    asyncio.run(test_allocation_rejects_sum_mismatch())
    print("OK test_allocation_rejects_sum_mismatch")
    asyncio.run(test_allocation_rejects_non_private_fee())
    print("OK test_allocation_rejects_non_private_fee")
