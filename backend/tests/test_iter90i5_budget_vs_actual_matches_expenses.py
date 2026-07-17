"""iter90i5 : Test invariant critique - le `total_actual` retourne par
`GET /api/fiscal/budget-comparison/{fy_id}` (Budget vs Reel) doit
EXACTEMENT correspondre au `totals.total` de la Liste des depenses
(`compute_expense_rows`) sur la meme periode.

Motivation : le user a signale que le montant "reel" dans Budget vs
Reel ne correspond PAS a la Liste des depenses. Cause probable :
l'ancien calcul par `debit-credit` sur toutes les journal_entries
incluait les contre-passations, ecarts d'imputation, etc.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed(db, copro_id, fy_id):
    await db.coproprietes.delete_many({"id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.invoices.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.fiscal_years.delete_many({"id": fy_id})
    await db.budgets.delete_many({"fiscal_year_id": fy_id})

    await db.coproprietes.insert_one({"id": copro_id, "name": "ACP i5"})
    await db.lots.insert_one({"id": f"lot-{copro_id}",
                              "copropriete_id": copro_id, "number": "01"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": copro_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    await db.budgets.insert_one({
        "id": f"bud-{fy_id}", "fiscal_year_id": fy_id,
        "copropriete_id": copro_id,
        "lines": [
            {"account_number": "614000", "account_name": "Assurance",
             "amount": 1000.0},
        ],
        "total": 1000.0,
    })
    # 3 factures : 500 payee, 300 impayee, note credit -50, + 1 privative 999 (a exclure)
    for i, (num, amount, status, priv) in enumerate([
        ("F1", 500.0, "paid", False),
        ("F2", 300.0, "unpaid", False),
        ("NC1", -50.0, "credit_note", False),
        ("PRIV1", 999.0, "unpaid", True),
    ]):
        await db.invoices.insert_one({
            "id": f"inv-i5-{i}-{copro_id}", "copropriete_id": copro_id,
            "date": f"2026-0{i+1}-15", "supplier": "X", "number": num,
            "total_amount": amount, "status": status,
            "is_private_fee": priv,
            "lines": [{"amount": amount, "account_number": "614000"}],
        })
    # + JE contre-passee (reversal) sur classe 6 -> DOIT etre exclue
    rev_id = f"je-rev-{copro_id}"
    await db.journal_entries.insert_one({
        "id": rev_id, "copropriete_id": copro_id,
        "date": "2026-04-10", "journal_type": "OD",
        "reversed": True, "is_reversal": False,
        "lines": [
            {"account_number": "614000", "debit": 77.0, "credit": 0},
            {"account_number": "550000", "debit": 0, "credit": 77.0},
        ],
    })
    # + Sa contre-passation (is_reversal=True)
    await db.journal_entries.insert_one({
        "id": f"{rev_id}-rev", "copropriete_id": copro_id,
        "date": "2026-04-11", "journal_type": "OD",
        "reversed": False, "is_reversal": True,
        "lines": [
            {"account_number": "614000", "debit": 0, "credit": 77.0},
            {"account_number": "550000", "debit": 77.0, "credit": 0},
        ],
    })


async def _cleanup(db, copro_id, fy_id):
    await db.coproprietes.delete_many({"id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.invoices.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.fiscal_years.delete_many({"id": fy_id})
    await db.budgets.delete_many({"fiscal_year_id": fy_id})


def test_budget_vs_actual_total_matches_expenses_page():
    """iter90i5 : total_actual == totals.total de compute_expense_rows."""
    copro_id = f"acp-i5-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-i5-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from expense_rows import compute_expense_rows
        from routes.fiscal import create_fiscal_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, fy_id)
            # 1) Source de verite : compute_expense_rows sur la periode FY
            _, totals_expenses = await compute_expense_rows(
                db, copro_id,
                date_from="2026-01-01", date_to="2026-12-31",
            )
            expected_total = float(totals_expenses["total"])
            # 2) Budget vs Reel via l'endpoint
            router = create_fiscal_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/fiscal/budget-comparison/{fiscal_year_id}":
                    handler = r.endpoint
                    break
            assert handler is not None, "Endpoint budget-comparison introuvable"
            data = await handler(fiscal_year_id=fy_id)
            actual_total = float(data["total_actual"])
            # Invariant strict
            assert abs(actual_total - expected_total) < 0.01, (
                f"total_actual budget-comparison ({actual_total}) != "
                f"expenses ({expected_total}) - iter90i5 doit garantir l'egalite"
            )
            # Sanity : 500 + 300 - 50 = 750 (privative exclue, reversals exclus)
            assert abs(expected_total - 750.0) < 0.01, (
                f"Expected 750, got {expected_total}"
            )
        finally:
            await _cleanup(db, copro_id, fy_id)
            client.close()

    asyncio.run(_run())


def test_budget_vs_actual_shows_credit_notes_reducing_expense():
    """iter90i5 : la note de credit REDUIT le total_actual (montant negatif)."""
    copro_id = f"acp-i5-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-i5-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.fiscal import create_fiscal_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.fiscal_years.delete_many({"id": fy_id})
            await db.budgets.delete_many({"fiscal_year_id": fy_id})

            await db.coproprietes.insert_one({"id": copro_id, "name": "ACP i5b"})
            await db.lots.insert_one({"id": f"lot-{copro_id}",
                                      "copropriete_id": copro_id, "number": "01"})
            await db.fiscal_years.insert_one({
                "id": fy_id, "copropriete_id": copro_id, "name": "2026",
                "start_date": "2026-01-01", "end_date": "2026-12-31",
                "status": "open",
            })
            await db.budgets.insert_one({
                "id": f"bud-{fy_id}", "fiscal_year_id": fy_id,
                "copropriete_id": copro_id,
                "lines": [{"account_number": "614000", "amount": 1000.0}],
                "total": 1000.0,
            })
            # Facture 114 + note credit -57.03 = 56.97 net
            await db.invoices.insert_one({
                "id": f"i5-inv-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-03-01", "supplier": "Electrabel",
                "number": "F1", "total_amount": 114.0, "status": "unpaid",
                "lines": [{"amount": 114.0, "account_number": "614000"}],
            })
            await db.invoices.insert_one({
                "id": f"i5-nc-{copro_id}", "copropriete_id": copro_id,
                "date": "2026-05-03", "supplier": "Electrabel",
                "number": "NC1", "total_amount": -57.03,
                "status": "credit_note",
                "lines": [{"amount": -57.03, "account_number": "614000"}],
            })
            router = create_fiscal_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/fiscal/budget-comparison/{fiscal_year_id}")
            data = await handler(fiscal_year_id=fy_id)
            assert abs(float(data["total_actual"]) - 56.97) < 0.01, (
                f"Note de credit doit REDUIRE le total ; got {data['total_actual']}"
            )
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.invoices.delete_many({"copropriete_id": copro_id})
            await db.fiscal_years.delete_many({"id": fy_id})
            await db.budgets.delete_many({"fiscal_year_id": fy_id})
            client.close()

    asyncio.run(_run())
