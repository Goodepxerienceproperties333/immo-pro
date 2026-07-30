"""iter93bs : Factures hybrides - nature de depense pour portion charges communes.

Contexte utilisateur : "je dois pouvoir choisir la nature de depenses" pour
la portion charges communes des factures hybrides (frais privatif + charges
communes partiel).

Comportement teste :
- Hybride avec common_charge_expense_category_id -> derive account_number
  automatiquement, sauvegarde OK.
- Hybride avec common_charge_account_number direct -> OK.
- Hybride sans common_charge_* -> 400 (verrou strict).
- Hybride sans common_charge_distribution_key_id -> 400.
- L'ecriture AC auto-generee est SPLIT :
  Dr 643 (private_amount) + Dr common_acc (common_amount) / Cr supplier (total)
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
    cid = f"iter93bs-{suffix}"
    oid = f"o-{suffix}"
    cat_id = f"cat-{suffix}"
    dk_id = f"dk-{suffix}"
    lot_id = f"lot-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter93bs-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    await db.owners.insert_one({
        "id": oid, "name": f"Owner_{suffix}", "copropriete_ids": [cid],
    })
    await db.lots.insert_one({
        "id": lot_id, "copropriete_id": cid, "number": "L01", "owner_id": oid,
    })
    await db.distribution_keys.insert_one({
        "id": dk_id, "copropriete_id": cid, "name": "Cle test",
        "lots": [{"lot_id": lot_id, "lot_number": "L01", "share": 100}],
    })
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid, "name": "Assurance",
        "account_number": "6140", "default_distribution_key_id": dk_id,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "643", "name": "Frais privatifs", "copropriete_id": cid},
        {"number": "6140", "name": "Assurance", "copropriete_id": cid},
        {"number": "440001", "name": "Fournisseur", "copropriete_id": cid},
    ])
    return cid, oid, cat_id, dk_id


async def _cleanup(db, cid):
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"copropriete_ids": cid})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.coproprietes.delete_one({"id": cid})


async def test_hybrid_with_category_ok():
    """Hybride avec common_charge_expense_category_id -> 200 + AC entry split."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, cat_id, dk_id = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"HYB-{sfx}", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "Facture hybride cat",
                "total_amount": 200, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 60}],
                "common_charge_expense_category_id": cat_id,
                "common_charge_distribution_key_id": dk_id,
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            inv = r.json()
            assert inv["common_charge_expense_category_id"] == cat_id
            assert inv["common_charge_account_number"] == "6140"
            assert inv["common_charge_distribution_key_id"] == dk_id
            assert abs(inv["common_charge_amount"] - 140) < 0.01

            # Verifie l'ecriture AC split
            ac = await db.journal_entries.find_one({
                "source_id": inv["id"], "journal_type": "AC",
            })
            assert ac is not None, "AC entry not found"
            debits = {ln["account_number"]: ln["debit"] for ln in ac["lines"] if ln["debit"] > 0}
            assert "643" in debits and abs(debits["643"] - 60) < 0.01, f"Expected Dr 643 = 60, got {debits}"
            assert "6140" in debits and abs(debits["6140"] - 140) < 0.01, f"Expected Dr 6140 = 140, got {debits}"
    finally:
        await _cleanup(db, cid)


async def test_hybrid_missing_common_category_or_account_rejected():
    """Hybride sans common_charge_* -> 400."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, _cat_id, dk_id = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"HYB-{sfx}-nocat", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "Facture hybride sans cat",
                "total_amount": 200, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 60}],
                "common_charge_distribution_key_id": dk_id,
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
            assert "compte pcmn" in r.text.lower() or "expense_category" in r.text.lower()
    finally:
        await _cleanup(db, cid)


async def test_hybrid_missing_dist_key_rejected():
    """Hybride sans common_charge_distribution_key_id -> 400."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, cat_id, _dk_id = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"HYB-{sfx}-nodk", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "Facture hybride sans cle",
                "total_amount": 200, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 60}],
                "common_charge_expense_category_id": cat_id,
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
            assert "cle de repartition" in r.text.lower() or "distribution_key" in r.text.lower()
    finally:
        await _cleanup(db, cid)


async def test_hybrid_with_direct_account_ok():
    """Hybride avec common_charge_account_number direct (sans cat) -> 200."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, _cat_id, dk_id = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"HYB-{sfx}-directacc", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "Facture hybride direct account",
                "total_amount": 300, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 100}],
                "common_charge_account_number": "6140",
                "common_charge_distribution_key_id": dk_id,
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            inv = r.json()
            assert inv["common_charge_account_number"] == "6140"
            assert abs(inv["common_charge_amount"] - 200) < 0.01
    finally:
        await _cleanup(db, cid)


if __name__ == "__main__":
    asyncio.run(test_hybrid_with_category_ok())
    print("OK test_hybrid_with_category_ok")
    asyncio.run(test_hybrid_missing_common_category_or_account_rejected())
    print("OK test_hybrid_missing_common_category_or_account_rejected")
    asyncio.run(test_hybrid_missing_dist_key_rejected())
    print("OK test_hybrid_missing_dist_key_rejected")
    asyncio.run(test_hybrid_with_direct_account_ok())
    print("OK test_hybrid_with_direct_account_ok")
    print("\n=== ALL 4 TESTS PASSED ===")
