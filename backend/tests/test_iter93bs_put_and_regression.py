"""iter93bs additional tests : PUT hybrid update + regressions.

- PUT /api/invoices/{id} en mode hybride met a jour common_charge_* correctement.
- Regression : 100% private fee (sum == total) -> ecriture AC classique.
- Regression : facture classique (non-private_fee) fonctionne.
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
    cid = f"iter93bsX-{suffix}"
    oid = f"o-{suffix}"
    cat1 = f"cat1-{suffix}"
    cat2 = f"cat2-{suffix}"
    dk_id = f"dk-{suffix}"
    dk2_id = f"dk2-{suffix}"
    lot_id = f"lot-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter93bsX-{suffix}"})
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
    await db.distribution_keys.insert_many([
        {"id": dk_id, "copropriete_id": cid, "name": "Cle A",
         "lots": [{"lot_id": lot_id, "lot_number": "L01", "share": 100}]},
        {"id": dk2_id, "copropriete_id": cid, "name": "Cle B",
         "lots": [{"lot_id": lot_id, "lot_number": "L01", "share": 100}]},
    ])
    await db.expense_categories.insert_many([
        {"id": cat1, "copropriete_id": cid, "name": "Assurance",
         "account_number": "6140", "default_distribution_key_id": dk_id},
        {"id": cat2, "copropriete_id": cid, "name": "Entretien",
         "account_number": "6150", "default_distribution_key_id": dk2_id},
    ])
    await db.pcmn_accounts.insert_many([
        {"number": "643", "name": "Frais privatifs", "copropriete_id": cid},
        {"number": "6140", "name": "Assurance", "copropriete_id": cid},
        {"number": "6150", "name": "Entretien", "copropriete_id": cid},
        {"number": "440001", "name": "Fournisseur", "copropriete_id": cid},
    ])
    return cid, oid, cat1, cat2, dk_id, dk2_id


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


async def test_put_hybrid_updates_common_charge():
    """PUT hybride: change category -> nouveaux account/distkey/amount recalcules."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, cat1, cat2, dk1, dk2 = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            # Create hybrid with cat1/dk1
            p = {
                "number": f"HYBPUT-{sfx}", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "PUT hybrid",
                "total_amount": 200, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 60}],
                "common_charge_expense_category_id": cat1,
                "common_charge_distribution_key_id": dk1,
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, r.text
            inv = r.json()
            inv_id = inv["id"]
            assert inv["common_charge_account_number"] == "6140"

            # PUT: change cat to cat2 (dist key too), change allocation amount
            p2 = {**p,
                  "private_fee_allocations": [{"owner_id": oid, "amount": 50}],
                  "common_charge_expense_category_id": cat2,
                  "common_charge_distribution_key_id": dk2}
            r2 = await client.put(f"{BACKEND_URL}/api/invoices/{inv_id}", json=p2)
            assert r2.status_code == 200, r2.text
            inv2 = r2.json()
            assert inv2["common_charge_expense_category_id"] == cat2
            assert inv2["common_charge_account_number"] == "6150"
            assert inv2["common_charge_distribution_key_id"] == dk2
            assert abs(inv2["common_charge_amount"] - 150) < 0.01

            # Verify AC entry updated with new split
            ac = await db.journal_entries.find_one({
                "source_id": inv_id, "journal_type": "AC",
            })
            assert ac is not None
            debits = {ln["account_number"]: ln["debit"] for ln in ac["lines"] if ln.get("debit", 0) > 0}
            assert abs(debits.get("643", 0) - 50) < 0.01, f"Dr 643 expected 50, got {debits}"
            assert abs(debits.get("6150", 0) - 150) < 0.01, f"Dr 6150 expected 150, got {debits}"
            total_d = sum(ln.get("debit", 0) for ln in ac["lines"])
            total_c = sum(ln.get("credit", 0) for ln in ac["lines"])
            assert abs(total_d - total_c) < 0.01, f"AC not balanced: {total_d} vs {total_c}"
            assert abs(total_d - 200) < 0.01
    finally:
        await _cleanup(db, cid)


async def test_regression_100_percent_private_fee():
    """Regression : sum(allocations) == total -> AC classique Dr 643 total / Cr supplier total."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, cat1, _cat2, dk1, _dk2 = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"PRIV100-{sfx}", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "100% private",
                "total_amount": 150, "vat_amount": 0,
                "is_private_fee": True,
                "private_fee_allocations": [{"owner_id": oid, "amount": 150}],
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, r.text
            inv = r.json()
            # Not hybrid: common_charge_amount should be 0 / None
            assert not inv.get("common_charge_amount") or inv["common_charge_amount"] == 0

            ac = await db.journal_entries.find_one({
                "source_id": inv["id"], "journal_type": "AC",
            })
            assert ac is not None
            debits = {ln["account_number"]: ln["debit"] for ln in ac["lines"] if ln.get("debit", 0) > 0}
            # Dr 643 total = 150 (no split)
            assert abs(debits.get("643", 0) - 150) < 0.01, f"Dr 643 expected 150, got {debits}"
            # Ensure no split (no other 6xxx debits)
            other_6xx = {k: v for k, v in debits.items() if k.startswith("6") and k != "643"}
            assert not other_6xx, f"Unexpected other 6xx debits: {other_6xx}"
            total_d = sum(ln.get("debit", 0) for ln in ac["lines"])
            total_c = sum(ln.get("credit", 0) for ln in ac["lines"])
            assert abs(total_d - total_c) < 0.01
    finally:
        await _cleanup(db, cid)


async def test_regression_classic_invoice_non_private():
    """Regression : facture classique (is_private_fee=False) -> OK."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, oid, cat1, _cat2, dk1, _dk2 = await _seed(db, sfx)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            p = {
                "number": f"CLA-{sfx}", "date": "2026-02-15",
                "supplier": "TestSupp", "description": "Classique",
                "total_amount": 100, "vat_amount": 0,
                "is_private_fee": False,
                "expense_category_id": cat1,
                "distribution_key_id": dk1,
                "account_number": "6140",
                "copropriete_id": cid, "status": "unpaid",
            }
            r = await client.post(f"{BACKEND_URL}/api/invoices", json=p)
            assert r.status_code == 200, r.text
            inv = r.json()
            ac = await db.journal_entries.find_one({
                "source_id": inv["id"], "journal_type": "AC",
            })
            assert ac is not None
            debits = {ln["account_number"]: ln["debit"] for ln in ac["lines"] if ln.get("debit", 0) > 0}
            assert abs(debits.get("6140", 0) - 100) < 0.01, f"Dr 6140 expected 100, got {debits}"
    finally:
        await _cleanup(db, cid)


if __name__ == "__main__":
    asyncio.run(test_put_hybrid_updates_common_charge())
    print("OK test_put_hybrid_updates_common_charge")
    asyncio.run(test_regression_100_percent_private_fee())
    print("OK test_regression_100_percent_private_fee")
    asyncio.run(test_regression_classic_invoice_non_private())
    print("OK test_regression_classic_invoice_non_private")
    print("\n=== ALL 3 TESTS PASSED ===")
