"""iter90jn : Master/Slave sync ETENDU au LETTRAGE.

Regles :
- DELETE statement -> annule TOUS les lettrages des txns du statement
  (factures retournent en unpaid ou partially_paid selon les txns restantes).
- UNPOST statement (repassage en brouillon) -> PRESERVE le lettrage
  (txns.matched=True + matched_to intacts).

Tests :
1. `delete_statement` sur un extrait posted avec 1 txn lettree a une facture
   -> facture repasse en "unpaid", paid_by_transaction_ids unset.
2. `delete_statement` avec 2 txns qui payaient partiellement la meme facture
   dans 2 statements differents -> ne supprime QUE le lettrage du statement
   supprime, l'autre reste et la facture reste "partially_paid".
3. `unpost_statement` -> matched, matched_to intacts.
4. Script `cleanup_orphaned_invoice_lettrage.py` : detecte + repare les
   factures dont TOUTES les paid_by_transaction_ids ont disparu -> unpaid.
5. Le script recalcule partially_paid quand certaines txns survivent.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


def _run(coro):
    return asyncio.run(coro)


def _get_handler(router, path_suffix, method="POST"):
    for r in router.routes:
        if getattr(r, "path", "").endswith(path_suffix) and method in getattr(r, "methods", set()):
            return r.endpoint
    return None


# ---------------------------------------------------------------------------
# Test 1 : delete_statement annule le lettrage de la facture
# ---------------------------------------------------------------------------
def test_delete_statement_cancels_lettrage_and_restores_invoice_to_unpaid():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jn1-{suffix}"
        stmt_id = f"stmt-{suffix}"
        txn_id = f"txn-{suffix}"
        invoice_id = f"inv-{suffix}"
        try:
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp,
                "date": "2026-06-15", "status": "posted",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp,
                "statement_id": stmt_id, "date": "2026-06-15",
                "amount": -150.0,
                "matched": True, "matched_to": invoice_id,
                "match_type": "invoice",
                "lettrage_code": "L-2026-001",
            })
            # Facture "paid" grace a cette txn
            await db.invoices.insert_one({
                "id": invoice_id, "copropriete_id": acp,
                "amount_ttc": 150.0, "total_amount": 150.0,
                "status": "paid",
                "paid_by_transaction_id": txn_id,
                "paid_by_transaction_ids": [txn_id],
                "amount_paid": 150.0,
                "lettrage_code": "L-2026-001",
            })

            router = create_banking_router(db)
            handler = _get_handler(router, "/statements/{stmt_id}", "DELETE")
            assert handler is not None

            result = await handler(stmt_id=stmt_id)
            assert result["invoices_unlettered"] >= 1, (
                f"Doit annuler au moins 1 lettrage. Vu {result}"
            )

            # La facture est repassee en "unpaid"
            inv_fresh = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            assert inv_fresh["status"] == "unpaid", (
                f"Facture doit etre unpaid apres delete_statement. Vu {inv_fresh.get('status')}"
            )
            assert "amount_paid" not in inv_fresh or not inv_fresh.get("amount_paid")
            assert not inv_fresh.get("paid_by_transaction_id")
            assert not inv_fresh.get("paid_by_transaction_ids")
        finally:
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_many({"id": txn_id})
            await db.invoices.delete_one({"id": invoice_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 2 : delete_statement partiel - la facture reste partially_paid
# ---------------------------------------------------------------------------
def test_delete_statement_preserves_lettrage_from_other_statements():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jn2-{suffix}"
        stmt_a = f"stmt-A-{suffix}"
        stmt_b = f"stmt-B-{suffix}"
        txn_a = f"txn-A-{suffix}"
        txn_b = f"txn-B-{suffix}"
        invoice_id = f"inv-{suffix}"
        try:
            # 2 statements distincts
            await db.bank_statements.insert_many([
                {"id": stmt_a, "copropriete_id": acp, "date": "2026-06-15", "status": "posted"},
                {"id": stmt_b, "copropriete_id": acp, "date": "2026-07-15", "status": "posted"},
            ])
            # 2 txns qui paient la meme facture (60 + 40 = 100)
            await db.bank_transactions.insert_many([
                {"id": txn_a, "copropriete_id": acp, "statement_id": stmt_a,
                 "date": "2026-06-15", "amount": -60.0,
                 "matched": True, "matched_to": invoice_id, "match_type": "invoice"},
                {"id": txn_b, "copropriete_id": acp, "statement_id": stmt_b,
                 "date": "2026-07-15", "amount": -40.0,
                 "matched": True, "matched_to": invoice_id, "match_type": "invoice"},
            ])
            # Facture 100 EUR, deja marquee "paid" via les 2 txns
            await db.invoices.insert_one({
                "id": invoice_id, "copropriete_id": acp,
                "amount_ttc": 100.0, "status": "paid",
                "paid_by_transaction_ids": [txn_a, txn_b],
                "amount_paid": 100.0,
            })

            router = create_banking_router(db)
            handler = _get_handler(router, "/statements/{stmt_id}", "DELETE")
            assert handler is not None

            # Supprime stmt_a (60 EUR)
            await handler(stmt_id=stmt_a)

            inv_fresh = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            # 40 EUR restants sur 100 -> partially_paid
            assert inv_fresh["status"] == "partially_paid", (
                f"Doit etre partially_paid apres retrait de 60 EUR. Vu {inv_fresh.get('status')}"
            )
            assert abs(float(inv_fresh.get("amount_paid") or 0) - 40.0) < 0.01
            assert txn_b in (inv_fresh.get("paid_by_transaction_ids") or [])
            assert txn_a not in (inv_fresh.get("paid_by_transaction_ids") or [])
        finally:
            await db.bank_statements.delete_many({"id": {"$in": [stmt_a, stmt_b]}})
            await db.bank_transactions.delete_many({"id": {"$in": [txn_a, txn_b]}})
            await db.invoices.delete_one({"id": invoice_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 3 : unpost_statement PRESERVE le lettrage
# ---------------------------------------------------------------------------
def test_unpost_statement_preserves_lettrage():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jn3-{suffix}"
        stmt_id = f"stmt-{suffix}"
        txn_id = f"txn-{suffix}"
        invoice_id = f"inv-{suffix}"
        try:
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp,
                "date": "2026-06-15", "status": "posted",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp,
                "statement_id": stmt_id, "date": "2026-06-15",
                "amount": -100.0,
                "matched": True, "matched_to": invoice_id,
                "match_type": "invoice",
                "lettrage_code": "L-2026-042",
            })
            await db.invoices.insert_one({
                "id": invoice_id, "copropriete_id": acp,
                "amount_ttc": 100.0, "status": "paid",
                "paid_by_transaction_ids": [txn_id],
                "amount_paid": 100.0,
            })

            router = create_banking_router(db)
            handler = _get_handler(router, "/statements/{stmt_id}/unpost", "POST")
            assert handler is not None

            await handler(stmt_id=stmt_id)

            # Le statement passe en draft
            stmt_fresh = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
            assert stmt_fresh["status"] == "draft"
            # La txn EXISTE toujours (unpost ne supprime pas la txn)
            txn_fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            assert txn_fresh is not None
            # Le lettrage est PRESERVE
            assert txn_fresh.get("matched") is True, (
                f"Le lettrage doit etre PRESERVE apres unpost. Vu matched={txn_fresh.get('matched')}"
            )
            assert txn_fresh.get("matched_to") == invoice_id
            assert txn_fresh.get("match_type") == "invoice"
            assert txn_fresh.get("lettrage_code") == "L-2026-042"
            # La facture reste "paid"
            inv_fresh = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            assert inv_fresh["status"] == "paid"
        finally:
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.invoices.delete_one({"id": invoice_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 4 : cleanup_orphaned_invoice_lettrage repare les factures orphelines
# ---------------------------------------------------------------------------
def test_cleanup_orphaned_invoice_lettrage_full_loss_reverts_to_unpaid():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.cleanup_orphaned_invoice_lettrage import _run as _cleanup
        import argparse

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jn4-{suffix}"
        invoice_id = f"inv-{suffix}"
        try:
            # Facture "paid" par une txn qui a disparu
            await db.invoices.insert_one({
                "id": invoice_id, "copropriete_id": acp,
                "amount_ttc": 200.0, "status": "paid",
                "paid_by_transaction_id": f"ghost-{suffix}",
                "paid_by_transaction_ids": [f"ghost-{suffix}"],
                "amount_paid": 200.0,
                "lettrage_code": "L-orphan",
            })

            # DRY-RUN
            args_dry = argparse.Namespace(execute=False, copropriete_id=acp)
            await _cleanup(args_dry)
            inv_dry = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            assert inv_dry["status"] == "paid", "Dry-run ne doit rien modifier"

            # EXECUTE
            args_exec = argparse.Namespace(execute=True, copropriete_id=acp)
            await _cleanup(args_exec)
            inv_exec = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            assert inv_exec["status"] == "unpaid"
            assert not inv_exec.get("paid_by_transaction_id")
            assert not inv_exec.get("paid_by_transaction_ids")
            assert not inv_exec.get("amount_paid")
        finally:
            await db.invoices.delete_one({"id": invoice_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 5 : cleanup avec certaines txns survivantes -> partially_paid
# ---------------------------------------------------------------------------
def test_cleanup_orphaned_invoice_lettrage_partial_loss_becomes_partially_paid():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.cleanup_orphaned_invoice_lettrage import _run as _cleanup
        import argparse

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jn5-{suffix}"
        invoice_id = f"inv-{suffix}"
        live_txn = f"txn-live-{suffix}"
        ghost_txn = f"ghost-{suffix}"
        try:
            await db.bank_transactions.insert_one({
                "id": live_txn, "copropriete_id": acp,
                "date": "2026-06-15", "amount": -40.0,
                "matched": True, "matched_to": invoice_id, "match_type": "invoice",
            })
            await db.invoices.insert_one({
                "id": invoice_id, "copropriete_id": acp,
                "amount_ttc": 100.0, "status": "paid",
                "paid_by_transaction_ids": [live_txn, ghost_txn],
                "amount_paid": 100.0,
            })

            args_exec = argparse.Namespace(execute=True, copropriete_id=acp)
            await _cleanup(args_exec)
            inv_fresh = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
            assert inv_fresh["status"] == "partially_paid"
            assert abs(float(inv_fresh.get("amount_paid") or 0) - 40.0) < 0.01
            assert live_txn in (inv_fresh.get("paid_by_transaction_ids") or [])
            assert ghost_txn not in (inv_fresh.get("paid_by_transaction_ids") or [])
        finally:
            await db.bank_transactions.delete_one({"id": live_txn})
            await db.invoices.delete_one({"id": invoice_id})

    _run(_go())
