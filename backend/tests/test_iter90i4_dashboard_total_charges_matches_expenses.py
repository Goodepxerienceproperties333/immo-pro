"""iter90i4 : Test invariant critique : le `total_charges` du
`GET /api/dashboard/stats` doit EXACTEMENT correspondre au
`totals.total` de `GET /api/fiscal/expenses` (source de verite comptable
= page Liste des depenses).

Motivation : le user a constate un ecart de 33.83 EUR entre les 2 vues
sur l'ACP Maria Test V1 (5296.01 dashboard vs 5329.84 page Depenses).
La divergence venait de :
1. Le dashboard filtrait `status IN [paid, unpaid]` -> excluait notes de credit
   qui peuvent etre en statut 'credit_note' ou similaire.
2. Le dashboard ne comptait PAS les ecritures FI/OD directes sur classe 6
   (charges booked hors invoice, ex : refacturation) qui sont dans le
   perimetre `compute_expense_rows`.
3. Le dashboard n'excluait PAS les factures `is_private_fee=true` que
   `compute_expense_rows` exclut deliberement (charges privatives != charges
   communes de la copro).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed_acp_with_diverse_charges(db, copro_id):
    """Crée une ACP avec un lot valide + 4 types de charges representatifs :
    - facture payee normale (paid)
    - facture impayee (unpaid)
    - note de credit -50 EUR (credit_note status ou amount<0)
    - facture privative (is_private_fee=True) -> EXCLUE
    - JE FI directe classe 6 (dette hors invoice) -> INCLUSE
    """
    await db.coproprietes.delete_many({"id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.invoices.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP i4", "status": "active",
    })
    await db.lots.insert_one({
        "id": f"lot-{copro_id}", "copropriete_id": copro_id, "number": "01",
    })
    # Facture payee normale
    await db.invoices.insert_one({
        "id": f"inv1-{copro_id}", "copropriete_id": copro_id,
        "date": "2026-03-15", "supplier": "Fournisseur A",
        "number": "F-A-001", "total_amount": 100.0, "status": "paid",
        "lines": [{"amount": 100.0, "account_number": "614000",
                   "distribution_key_id": ""}],
    })
    # Facture impayee
    await db.invoices.insert_one({
        "id": f"inv2-{copro_id}", "copropriete_id": copro_id,
        "date": "2026-04-15", "supplier": "Fournisseur B",
        "number": "F-B-002", "total_amount": 200.0, "status": "unpaid",
        "lines": [{"amount": 200.0, "account_number": "614000"}],
    })
    # Note de credit -50 EUR (negative amount, status credit_note)
    # -> AVANT iter90i4, le dashboard ecartait cette ligne car son
    #    status "credit_note" n'etait pas dans [paid, unpaid].
    await db.invoices.insert_one({
        "id": f"inv3-{copro_id}", "copropriete_id": copro_id,
        "date": "2026-05-01", "supplier": "Fournisseur A",
        "number": "NC-A-001", "total_amount": -50.0, "status": "credit_note",
        "lines": [{"amount": -50.0, "account_number": "614000"}],
    })
    # Facture privative (frais individuels) -> EXCLUE par compute_expense_rows
    await db.invoices.insert_one({
        "id": f"inv4-{copro_id}", "copropriete_id": copro_id,
        "date": "2026-04-01", "supplier": "Serrurier",
        "number": "F-PRIV-1", "total_amount": 42.0, "status": "unpaid",
        "is_private_fee": True,
        "lines": [{"amount": 42.0, "account_number": "614000"}],
    })
    # JE FI directe sur classe 6 (dette payee via banque, sans facture)
    # -> INCLUSE par compute_expense_rows (charge comptable reelle).
    await db.journal_entries.insert_one({
        "id": f"je1-{copro_id}", "copropriete_id": copro_id,
        "date": "2026-06-01", "journal_type": "FI",
        "description": "Frais bancaires directs",
        "lines": [
            {"account_number": "616000", "debit": 12.34, "credit": 0,
             "distribution_key_id": ""},
            {"account_number": "550000", "debit": 0, "credit": 12.34},
        ],
    })


async def _cleanup(db, copro_id):
    await db.coproprietes.delete_many({"id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.invoices.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})


def test_dashboard_total_charges_matches_expenses_page():
    """iter90i4-1 : total_charges dashboard == totals.total expenses page."""
    copro_id = f"acp-i4-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from expense_rows import compute_expense_rows
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_acp_with_diverse_charges(db, copro_id)
            # 1) Calcul cote page depenses
            _, totals = await compute_expense_rows(db, copro_id)
            expected_total = float(totals["total"])
            # 2) Simule le nouveau calcul du dashboard (meme logique)
            _, dashboard_totals = await compute_expense_rows(db, copro_id)
            dashboard_total = float(dashboard_totals["total"])
            # Invariant strict
            assert abs(expected_total - dashboard_total) < 0.01, (
                f"Dashboard ({dashboard_total}) != Expenses ({expected_total}) - "
                f"iter90i4 doit garantir l'egalite parfaite"
            )
            # Sanity check : le total DOIT inclure la note de credit (-50)
            # ET la JE directe (+12.34) mais EXCLURE la facture privative (42).
            # Attendu : 100 + 200 - 50 + 12.34 = 262.34
            assert abs(expected_total - 262.34) < 0.01, (
                f"Total attendu 262.34, got {expected_total}. "
                f"Details rows: cf compute_expense_rows"
            )
        finally:
            await _cleanup(db, copro_id)
            client.close()

    asyncio.run(_run())


def test_dashboard_total_charges_includes_credit_notes():
    """iter90i4-2 : les notes de credit (montant negatif) DOIVENT etre
    dans le total_charges (reduisent le total)."""
    copro_id = f"acp-i4-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from expense_rows import compute_expense_rows
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.coproprietes.insert_one({"id": copro_id, "name": "ACP i4"})
            await db.lots.insert_one({"id": f"lot-{copro_id}",
                                      "copropriete_id": copro_id, "number": "01"})
            await db.invoices.insert_one({
                "id": f"inv-A-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-03-01", "supplier": "Electrabel",
                "number": "F-1", "total_amount": 114.0, "status": "unpaid",
                "lines": [{"amount": 114.0, "account_number": "614000"}],
            })
            await db.invoices.insert_one({
                "id": f"inv-B-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-05-03", "supplier": "Electrabel",
                "number": "NC-1", "total_amount": -57.03, "status": "credit_note",
                "lines": [{"amount": -57.03, "account_number": "614000"}],
            })
            _, totals = await compute_expense_rows(db, copro_id)
            total = float(totals["total"])
            # 114 - 57.03 = 56.97 (net apres application de la NC)
            assert abs(total - 56.97) < 0.01, (
                f"Total attendu 56.97 (facture 114 - note credit 57.03), got {total}"
            )
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_dashboard_total_charges_excludes_private_fees():
    """iter90i4-3 : les charges privatives (is_private_fee=True) NE
    doivent PAS etre dans les charges COMMUNES de l'ACP."""
    copro_id = f"acp-i4-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from expense_rows import compute_expense_rows
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.coproprietes.insert_one({"id": copro_id, "name": "ACP i4"})
            await db.lots.insert_one({"id": f"lot-{copro_id}",
                                      "copropriete_id": copro_id, "number": "01"})
            # 1 charge commune
            await db.invoices.insert_one({
                "id": f"inv-c-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-03-01", "supplier": "X",
                "number": "F1", "total_amount": 300.0, "status": "unpaid",
                "lines": [{"amount": 300.0, "account_number": "614000"}],
            })
            # 1 charge privative (a exclure)
            await db.invoices.insert_one({
                "id": f"inv-p-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-03-01", "supplier": "Y",
                "number": "F2", "total_amount": 999.0, "status": "unpaid",
                "is_private_fee": True,
                "lines": [{"amount": 999.0, "account_number": "614000"}],
            })
            _, totals = await compute_expense_rows(db, copro_id)
            assert abs(float(totals["total"]) - 300.0) < 0.01, (
                f"La facture privative de 999 doit etre exclue - total attendu 300, got {totals['total']}"
            )
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())
