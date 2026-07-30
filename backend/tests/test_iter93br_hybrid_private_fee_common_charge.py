"""iter93br : combiner frais privatifs + charges communes dans meme facture.

Contexte utilisateur (screenshot Leblanc 1395 EUR) : "il doit etre possible
de combiner des frais privatifs et des natures de depenses".

Comportement :
- `sum(private_fee_allocations) < total_amount` : la difference est portion
  charges communes -> account_number + distribution_key_id requis.
- `sum(...) == total_amount` : 100% privatif (comportement historique).
- `sum(...) > total_amount` : rejete (excedent).
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


async def _seed(db, suffix):
    cid = f"iter93br-{suffix}"
    oid = f"o-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter93br-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    await db.owners.insert_one({
        "id": oid, "name": f"Owner_{suffix}", "copropriete_ids": [cid],
    })
    return cid, oid


async def _cleanup(db, cid):
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"copropriete_ids": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.coproprietes.delete_one({"id": cid})


def _payload(cid, num, is_pf=True, alloc_amount=100, oid=None, total=100, account=""):
    return {
        "number": num, "date": "2026-02-15", "due_date": "2026-03-15",
        "supplier": "Test", "description": "Facture test hybride",
        "total_amount": total, "vat_amount": 0,
        "is_private_fee": is_pf,
        "private_fee_allocations": [{"owner_id": oid, "amount": alloc_amount}] if is_pf and oid else None,
        "account_number": account,
        "copropriete_id": cid, "status": "unpaid",
    }


async def test_partial_allocations_requires_account_number():
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # 1120.08 alloc + 1395 total = 274.92 portion commune, no account -> 400
            p = _payload(cid, f"T-{sfx}-A", alloc_amount=1120.08, oid=oid, total=1395.00)
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
            assert "charges communes" in r.text.lower() or "compte pcmn" in r.text.lower()
    finally:
        await _cleanup(db, cid)


async def test_excess_allocations_rejected():
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # 150 alloc + 100 total -> excedent 50 -> 400
            p = _payload(cid, f"T-{sfx}-B", alloc_amount=150.00, oid=oid, total=100.00, account="61060")
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 400
            assert "depasser" in r.text.lower() or "excedent" in r.text.lower()
    finally:
        await _cleanup(db, cid)


async def test_full_private_fee_still_works_regression():
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # 100 alloc + 100 total = 100% privatif OK
            p = _payload(cid, f"T-{sfx}-C", alloc_amount=100.00, oid=oid, total=100.00)
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    finally:
        await _cleanup(db, cid)


if __name__ == "__main__":
    asyncio.run(test_partial_allocations_requires_account_number())
    print("OK test_partial_allocations_requires_account_number")
    asyncio.run(test_excess_allocations_rejected())
    print("OK test_excess_allocations_rejected")
    asyncio.run(test_full_private_fee_still_works_regression())
    print("OK test_full_private_fee_still_works_regression")
    print("\n=== ALL 3 TESTS PASSED ===")
