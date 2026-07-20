"""iter90i6 : Tests des notes de credit (NC) dans le journal des achats
et la situation fournisseur.

Bug fixe :
- `generate_purchase_entry` skipait les invoices avec `amount <= 0`, donc
  les NC etaient persistees mais SANS ecriture comptable.
- Consequence : NC invisible dans Journaux Comptables > Achats et
  balance fournisseur incorrecte.

Fix : le generateur produit desormais une ecriture INVERSEE pour les NC :
  Dr 44xxxxx (reduit dette fournisseur)
  Cr 6xxxxx  (reduit charge)

Endpoint superadmin `/api/admin/heal-credit-notes` pour backfill les NC
existantes sans ecriture.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed_supplier_and_pcmn(db, copro_id, supplier_name):
    """Cree un fournisseur + son compte tier PCMN + le compte de charge.
    iter90is (Chinese Wall) : le supplier est LOCAL a l'ACP (copropriete_id
    obligatoire) et utilise le champ simple `tier_account_number`."""
    supp_id = f"supp-{uuid.uuid4().hex[:8]}"
    tier_acc = "44000099"
    expense_acc = "61210"
    await db.suppliers.insert_one({
        "id": supp_id, "name": supplier_name,
        "copropriete_id": copro_id,
        "tier_account_number": tier_acc,
        "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
    })
    await db.pcmn_accounts.insert_one({
        "copropriete_id": copro_id, "number": tier_acc,
        "name": f"Fournisseur - {supplier_name}", "class": "44",
    })
    await db.pcmn_accounts.insert_one({
        "copropriete_id": copro_id, "number": expense_acc,
        "name": "Electricite parties communes", "class": "61",
    })
    return supp_id, tier_acc, expense_acc


def test_credit_note_generates_ac_entry_with_inverted_sides():
    """iter90i6-1 : une invoice avec total_amount < 0 produit une AC avec
    Dr fournisseur (44xxx) et Cr charge (6xxx) - conforme PCMN belge."""
    copro_id = f"acp-i6-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_purchase_entry
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.suppliers.delete_many({"name": "TestSupplier-i6"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})

            supp_id, tier_acc, expense_acc = await _seed_supplier_and_pcmn(
                db, copro_id, "TestSupplier-i6",
            )
            nc = {
                "id": f"nc-{uuid.uuid4().hex[:8]}",
                "copropriete_id": copro_id,
                "supplier": "TestSupplier-i6", "supplier_id": supp_id,
                "number": "NC-001", "date": "2026-05-03",
                "total_amount": -57.03,
                "account_number": expense_acc,
                "lines": [{"amount": -57.03, "account_number": expense_acc}],
            }
            await db.invoices.insert_one(nc)
            je = await generate_purchase_entry(db, nc)
            assert je is not None, (
                "generate_purchase_entry doit produire une ecriture "
                "pour une NC (bug corrige : `if amount <= 0` -> `abs(amount) < 0.01`)"
            )
            # Sides inverses par rapport a une facture classique
            assert je["journal_type"] == "AC"
            assert je["total_debit"] == 57.03
            assert je["total_credit"] == 57.03
            # La ligne fournisseur doit etre en DEBIT (reduit la dette)
            supplier_line = next(
                (l for l in je["lines"] if l["account_number"] == tier_acc), None,
            )
            assert supplier_line is not None
            assert supplier_line["debit"] == 57.03, (
                "Fournisseur doit etre DEBITE pour une NC (reduit la dette)"
            )
            assert supplier_line["credit"] == 0
            # La ligne de charge doit etre en CREDIT (contrepasse la charge)
            expense_line = next(
                (l for l in je["lines"] if l["account_number"] == expense_acc), None,
            )
            assert expense_line is not None
            assert expense_line["credit"] == 57.03, (
                "Charge doit etre CREDITEE pour une NC (contrepasse)"
            )
            assert expense_line["debit"] == 0
            # Reference prefixee "NC-" au lieu de "FA-"
            assert je["reference"].startswith("NC-"), (
                f"Reference attendue commencant par 'NC-', got {je['reference']}"
            )
        finally:
            await db.suppliers.delete_many({"name": "TestSupplier-i6"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_regular_invoice_still_generates_ac_entry_normally():
    """iter90i6-2 : non-regression - une facture classique produit toujours
    Dr charge / Cr fournisseur avec la reference FA-...."""
    copro_id = f"acp-i6-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_purchase_entry
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.suppliers.delete_many({"name": "TestSupplier-i6b"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})

            supp_id, tier_acc, expense_acc = await _seed_supplier_and_pcmn(
                db, copro_id, "TestSupplier-i6b",
            )
            inv = {
                "id": f"inv-{uuid.uuid4().hex[:8]}",
                "copropriete_id": copro_id,
                "supplier": "TestSupplier-i6b", "supplier_id": supp_id,
                "number": "F-001", "date": "2026-03-01",
                "total_amount": 114.0,
                "account_number": expense_acc,
                "lines": [{"amount": 114.0, "account_number": expense_acc}],
            }
            await db.invoices.insert_one(inv)
            je = await generate_purchase_entry(db, inv)
            assert je is not None
            supplier_line = next(l for l in je["lines"] if l["account_number"] == tier_acc)
            expense_line = next(l for l in je["lines"] if l["account_number"] == expense_acc)
            # Facture classique : Dr charge / Cr fournisseur
            assert expense_line["debit"] == 114.0
            assert expense_line["credit"] == 0
            assert supplier_line["credit"] == 114.0
            assert supplier_line["debit"] == 0
            assert je["reference"].startswith("FA-")
        finally:
            await db.suppliers.delete_many({"name": "TestSupplier-i6b"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_credit_note_reduces_supplier_balance_correctly():
    """iter90i6-3 : la balance du fournisseur reflete correctement la NC.
    Sequence :
      1. Facture 114 EUR (Cr fournisseur) -> solde 114
      2. NC 57.03 EUR (Dr fournisseur) -> solde 114 - 57.03 = 56.97

    Ce test simule la logique de _line_matches / balance-tiers en direct
    sur les journal_entries generees.
    """
    copro_id = f"acp-i6-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_purchase_entry
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.suppliers.delete_many({"name": "TestSupplier-i6c"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})

            supp_id, tier_acc, expense_acc = await _seed_supplier_and_pcmn(
                db, copro_id, "TestSupplier-i6c",
            )
            # 1) Facture
            inv = {
                "id": f"inv-{uuid.uuid4().hex[:8]}", "copropriete_id": copro_id,
                "supplier": "TestSupplier-i6c", "supplier_id": supp_id,
                "number": "F-001", "date": "2026-03-01",
                "total_amount": 114.0, "account_number": expense_acc,
                "lines": [{"amount": 114.0, "account_number": expense_acc}],
            }
            await db.invoices.insert_one(inv)
            await generate_purchase_entry(db, inv)
            # 2) NC
            nc = {
                "id": f"nc-{uuid.uuid4().hex[:8]}", "copropriete_id": copro_id,
                "supplier": "TestSupplier-i6c", "supplier_id": supp_id,
                "number": "NC-001", "date": "2026-05-03",
                "total_amount": -57.03, "account_number": expense_acc,
                "lines": [{"amount": -57.03, "account_number": expense_acc}],
            }
            await db.invoices.insert_one(nc)
            await generate_purchase_entry(db, nc)

            # Calcul balance fournisseur sur le compte tier (44000099)
            balance = 0.0
            async for e in db.journal_entries.find(
                {"copropriete_id": copro_id, "lines.account_number": tier_acc,
                 "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "lines": 1},
            ):
                for ln in e.get("lines", []):
                    if ln.get("account_number") == tier_acc:
                        # Fournisseur : solde = credit - debit
                        # (positif = fournisseur crediteur = on lui doit)
                        balance += float(ln.get("credit", 0) or 0)
                        balance -= float(ln.get("debit", 0) or 0)
            assert abs(balance - 56.97) < 0.01, (
                f"Balance fournisseur attendue 56.97 EUR (114 - 57.03), got {balance}"
            )
        finally:
            await db.suppliers.delete_many({"name": "TestSupplier-i6c"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_credit_note_appears_in_ac_journal_list():
    """iter90i6-4 : une NC doit apparaitre dans les journal_entries de type AC.
    Non-regression : le filtre historique '$gt: 0' sur amount ne doit
    plus rejeter les NC de la liste des journaux d'achats.
    """
    copro_id = f"acp-i6-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_purchase_entry
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.suppliers.delete_many({"name": "TestSupplier-i6d"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})

            supp_id, tier_acc, expense_acc = await _seed_supplier_and_pcmn(
                db, copro_id, "TestSupplier-i6d",
            )
            nc = {
                "id": f"nc-{uuid.uuid4().hex[:8]}", "copropriete_id": copro_id,
                "supplier": "TestSupplier-i6d", "supplier_id": supp_id,
                "number": "NC-042", "date": "2026-06-15",
                "total_amount": -25.50, "account_number": expense_acc,
                "lines": [{"amount": -25.50, "account_number": expense_acc}],
            }
            await db.invoices.insert_one(nc)
            await generate_purchase_entry(db, nc)
            # Cherche l'AC dans les journaux
            ac_entries = await db.journal_entries.find(
                {"copropriete_id": copro_id, "journal_type": "AC"},
                {"_id": 0},
            ).to_list(10)
            assert len(ac_entries) == 1
            assert ac_entries[0]["reference"] == "NC-NC-042"
            assert ac_entries[0]["total_debit"] == 25.50
        finally:
            await db.suppliers.delete_many({"name": "TestSupplier-i6d"})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())
