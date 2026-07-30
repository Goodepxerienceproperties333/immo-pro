"""iter93bv : Factures hybrides comptabilisees dans la liste des depenses.

Contexte utilisateur : "lors de la combinaison entre les frais privatifs
et les frais sur base de nature de depenses, les depenses communes donc
la nature de depense doit bien se retrouver dans les depenses de l'ACP".

Comportement teste :
1. Facture 100% privatif : PAS dans liste des depenses (comportement iter90i).
2. Facture 100% commune : dans liste des depenses avec total_amount = total facture.
3. Facture HYBRIDE (privatif + charges communes) : dans liste des depenses
   AVEC total_amount = common_charge_amount UNIQUEMENT (pas le total facture).
4. Le compte, la cle, la nature utilises sont ceux de la portion commune
   (common_charge_*).
5. Filtres user (account_number, distribution_key_id, expense_category_id)
   fonctionnent sur les fields common_charge_* pour les factures hybrides.
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

from expense_rows import compute_expense_rows


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed(db, suffix):
    cid = f"iter93bv-{suffix}"
    dk_id = f"dk-{suffix}"
    cat_id = f"cat-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter93bv-{suffix}"})
    await db.distribution_keys.insert_one({
        "id": dk_id, "copropriete_id": cid, "name": "Charges communes",
        "lots": [],
    })
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid, "name": "Entretien jardins",
        "account_number": "61060",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "643", "name": "Frais privatifs", "copropriete_id": cid, "class_num": 6},
        {"number": "61060", "name": "Entretien jardins", "copropriete_id": cid, "class_num": 6},
    ])
    return cid, dk_id, cat_id


async def _cleanup(db, cid):
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.coproprietes.delete_one({"id": cid})


async def test_hybrid_invoice_appears_in_expenses_with_common_amount_only():
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    cid, dk_id, cat_id = await _seed(db, sfx)
    try:
        # Facture hybride : 1395 total, 1120,08 privatif, 274,92 charges communes
        await db.invoices.insert_one({
            "id": f"inv-hyb-{sfx}",
            "copropriete_id": cid,
            "number": f"F-HYB-{sfx}",
            "date": "2026-03-15",
            "supplier": "Leblanc",
            "description": "Entretien annuel",
            "total_amount": 1395.00,
            "vat_amount": 0,
            "account_number": "643",
            "distribution_key_id": "",
            "is_private_fee": True,
            "private_fee_allocations": [
                {"owner_id": "o1", "amount": 1120.08},
            ],
            "common_charge_account_number": "61060",
            "common_charge_distribution_key_id": dk_id,
            "common_charge_expense_category_id": cat_id,
            "common_charge_amount": 274.92,
            "status": "unpaid",
        })
        # Facture 100% privatif : ne doit PAS apparaitre
        await db.invoices.insert_one({
            "id": f"inv-priv-{sfx}",
            "copropriete_id": cid,
            "number": f"F-PRIV-{sfx}",
            "date": "2026-03-16",
            "supplier": "Elec",
            "total_amount": 500.00,
            "vat_amount": 0,
            "account_number": "643",
            "is_private_fee": True,
            "private_fee_allocations": [
                {"owner_id": "o1", "amount": 500.00},
            ],
            "common_charge_amount": 0.0,
            "status": "unpaid",
        })
        # Facture 100% commune : doit apparaitre avec son total
        await db.invoices.insert_one({
            "id": f"inv-comm-{sfx}",
            "copropriete_id": cid,
            "number": f"F-COMM-{sfx}",
            "date": "2026-03-17",
            "supplier": "Water",
            "total_amount": 200.00,
            "vat_amount": 0,
            "account_number": "61060",
            "distribution_key_id": dk_id,
            "expense_category_id": cat_id,
            "is_private_fee": False,
            "status": "unpaid",
        })

        rows, totals = await compute_expense_rows(
            db, cid, date_from="2026-01-01", date_to="2026-12-31",
        )
        ids = {r["id"] for r in rows}
        assert f"inv-hyb-{sfx}" in ids, "Facture hybride absente"
        assert f"inv-priv-{sfx}" not in ids, "Facture 100% privatif ne devrait PAS etre listee"
        assert f"inv-comm-{sfx}" in ids, "Facture 100% commune absente"

        hyb = next(r for r in rows if r["id"] == f"inv-hyb-{sfx}")
        assert abs(hyb["total_amount"] - 274.92) < 0.01, (
            f"Total hybride devrait etre 274.92 (portion commune), got {hyb['total_amount']}"
        )
        assert hyb["account_number"] == "61060"
        assert hyb["distribution_key_id"] == dk_id
        assert hyb["expense_category_id"] == cat_id
        assert hyb["is_hybrid_common_portion"] is True

        expected_total = 274.92 + 200.00
        assert abs(totals["total"] - expected_total) < 0.01, (
            f"Total attendu {expected_total}, got {totals['total']}"
        )

        # Filtres user : le compte 61060 doit remonter hybride + commune
        rows_f, _ = await compute_expense_rows(
            db, cid, date_from="2026-01-01", date_to="2026-12-31",
            account_number="61060",
        )
        ids_f = {r["id"] for r in rows_f}
        assert f"inv-hyb-{sfx}" in ids_f
        assert f"inv-comm-{sfx}" in ids_f

        # Filtre expense_category : idem
        rows_c, _ = await compute_expense_rows(
            db, cid, date_from="2026-01-01", date_to="2026-12-31",
            expense_category_id=cat_id,
        )
        ids_c = {r["id"] for r in rows_c}
        assert f"inv-hyb-{sfx}" in ids_c

        # Filtre distribution_key : idem
        rows_k, _ = await compute_expense_rows(
            db, cid, date_from="2026-01-01", date_to="2026-12-31",
            distribution_key_id=dk_id,
        )
        ids_k = {r["id"] for r in rows_k}
        assert f"inv-hyb-{sfx}" in ids_k
    finally:
        await _cleanup(db, cid)


if __name__ == "__main__":
    asyncio.run(test_hybrid_invoice_appears_in_expenses_with_common_amount_only())
    print("OK test_hybrid_invoice_appears_in_expenses_with_common_amount_only")
    print("\n=== ALL 1 TEST PASSED ===")
