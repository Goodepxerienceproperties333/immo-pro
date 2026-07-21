"""iter90jm : Synchronisation Master/Slave Bank Statements <-> FI JEs.

Tests :
1. `generate_bank_entry` ecrit `statement_line_id` + `bank_statement_id`
   sur la FI creee.
2. `update_statement` avec nouvelle date propage la date a toutes les FIs
   enfants (le statement est le maitre).
3. `delete_statement` (posted) supprime toutes les txns + FIs enfants
   (plus de 409 - le maitre disparait donc les esclaves aussi).
4. `backfill_statement_line_id_on_fi` : dry-run ne modifie rien ;
   execute renseigne les champs manquants + idempotence.
5. `cleanup_orphaned_fi` : detecte les FIs dont la txn source a ete
   supprimee ; hard-delete en mode execute.
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


# ---------------------------------------------------------------------------
# Test 1 : generate_bank_entry ecrit les 2 champs Master/Slave
# ---------------------------------------------------------------------------
def test_generate_bank_entry_writes_statement_line_id_and_statement_id():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_bank_entry

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jm1-{suffix}"
        stmt_id = f"stmt-{suffix}"
        txn_id = f"txn-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jm1-{suffix}",
                "bank_accounts": [{"iban": "BE04001952089331",
                                    "pcmn_number": "55133100",
                                    "label": "Vue", "is_default": True}],
            })
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()), "number": "55133100",
                "name": "Vue", "class_num": 5, "copropriete_id": acp,
            })
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp, "date": "2026-04-15",
                "account_number": "BE04001952089331", "status": "posted",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp,
                "statement_id": stmt_id, "date": "2026-04-15",
                "amount": 100.0, "counterparty_name": "Test",
                "communication": "Test payment",
                "account_number": "BE04001952089331",
            })
            txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            je = await generate_bank_entry(db, txn)
            assert je is not None
            # Le nouveau JE doit avoir statement_line_id + bank_statement_id
            fresh = await db.journal_entries.find_one({"id": je["id"]}, {"_id": 0})
            assert fresh.get("statement_line_id") == txn_id, (
                f"statement_line_id doit valoir txn_id. Vu {fresh.get('statement_line_id')}"
            )
            assert fresh.get("bank_statement_id") == stmt_id, (
                f"bank_statement_id doit valoir stmt_id. Vu {fresh.get('bank_statement_id')}"
            )
            # Le lien source_id historique reste aussi (compat)
            assert fresh.get("source_id") == txn_id
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 2 : update_statement propage la date aux FIs enfants
# ---------------------------------------------------------------------------
def test_update_statement_propagates_new_date_to_child_fi_entries():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router, StatementInput

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jm2-{suffix}"
        stmt_id = f"stmt-{suffix}"
        txn_id = f"txn-{suffix}"
        je_id = f"je-{suffix}"
        try:
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp,
                "number": "STMT-001",
                "date": "2026-04-15",
                "account_number": "BE04001952089331",
                "opening_balance": 0.0, "closing_balance": 100.0,
                "status": "posted",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp,
                "statement_id": stmt_id, "date": "2026-04-15",
                "amount": 100.0,
            })
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-04-15",
                "total_debit": 100, "total_credit": 100,
                "auto_generated": True,
                "source_type": "bank_txn", "source_id": txn_id,
                "statement_line_id": txn_id,
                "bank_statement_id": stmt_id,
                "lines": [
                    {"account_number": "55133100", "debit": 100, "credit": 0},
                    {"account_number": "44000015", "debit": 0, "credit": 100},
                ],
            })

            router = create_banking_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "").endswith("/statements/{stmt_id}"):
                    if "PUT" in getattr(r, "methods", set()):
                        handler = r.endpoint
                        break
            assert handler is not None, "Handler PUT statements/{stmt_id} introuvable"

            new_stmt = await handler(
                stmt_id=stmt_id,
                data=StatementInput(
                    number="STMT-001",
                    date="2026-05-20",  # <-- nouvelle date
                    account_number="BE04001952089331",
                    opening_balance=0.0, closing_balance=100.0,
                    copropriete_id=acp,
                ),
            )
            assert new_stmt is not None
            assert new_stmt.get("_iter90jm_fi_dates_propagated") == 1, (
                f"Doit propager 1 FI. Vu {new_stmt.get('_iter90jm_fi_dates_propagated')}"
            )

            # La FI a bien la nouvelle date
            fresh_je = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            assert fresh_je["date"] == "2026-05-20", (
                f"Date FI doit suivre. Vu {fresh_je['date']}"
            )
            # La txn est aussi mise en coherence (parce que txn.date == old_stmt.date)
            fresh_txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            assert fresh_txn["date"] == "2026-05-20", (
                f"Txn.date doit aussi suivre. Vu {fresh_txn['date']}"
            )
        finally:
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_many({"id": je_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 3 : delete_statement (posted) - cascade suppression Master -> Slave
# ---------------------------------------------------------------------------
def test_delete_posted_statement_cascades_to_transactions_and_fi():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jm3-{suffix}"
        stmt_id = f"stmt-{suffix}"
        txn_id = f"txn-{suffix}"
        je_id = f"je-{suffix}"
        try:
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp,
                "date": "2026-06-15",
                "account_number": "BE04001952089331",
                "status": "posted",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp,
                "statement_id": stmt_id, "date": "2026-06-15",
                "amount": 100.0,
            })
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-06-15",
                "total_debit": 100, "total_credit": 100,
                "auto_generated": True,
                "source_type": "bank_txn", "source_id": txn_id,
                "bank_statement_id": stmt_id,
                "lines": [
                    {"account_number": "55133100", "debit": 100, "credit": 0},
                    {"account_number": "44000015", "debit": 0, "credit": 100},
                ],
            })

            router = create_banking_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "").endswith("/statements/{stmt_id}") and \
                   "DELETE" in getattr(r, "methods", set()):
                    handler = r.endpoint
                    break
            assert handler is not None

            result = await handler(stmt_id=stmt_id)
            assert result["txns_deleted"] == 1
            assert result["fi_deleted"] == 1

            assert await db.bank_statements.find_one({"id": stmt_id}) is None
            assert await db.bank_transactions.find_one({"id": txn_id}) is None
            assert await db.journal_entries.find_one({"id": je_id}) is None
        finally:
            # Cleanup safety
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_one({"id": je_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 4 : backfill script - dry-run + execute + idempotence
# ---------------------------------------------------------------------------
def test_backfill_writes_statement_line_id_when_missing():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.backfill_statement_line_id_on_fi import _run as _backfill
        import argparse

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        stmt_id = f"stmt-bf-{suffix}"
        txn_id = f"txn-bf-{suffix}"
        je_id = f"je-bf-{suffix}"
        acp = f"acp-jm4-{suffix}"
        try:
            await db.bank_statements.insert_one({
                "id": stmt_id, "copropriete_id": acp, "date": "2026-07-01",
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": acp, "statement_id": stmt_id,
                "date": "2026-07-01", "amount": 50.0,
            })
            # FI LEGACY : n'a QUE source_id, pas statement_line_id ni bank_statement_id
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-07-01",
                "total_debit": 50, "total_credit": 50,
                "auto_generated": True,
                "source_type": "bank_txn", "source_id": txn_id,
                "lines": [
                    {"account_number": "55133100", "debit": 50, "credit": 0},
                    {"account_number": "44000015", "debit": 0, "credit": 50},
                ],
            })

            # DRY-RUN
            args_dry = argparse.Namespace(execute=False)
            await _backfill(args_dry)
            je_after_dry = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            assert not je_after_dry.get("statement_line_id"), (
                "Dry-run ne doit RIEN ecrire"
            )

            # EXECUTE
            args_exec = argparse.Namespace(execute=True)
            await _backfill(args_exec)
            je_fresh = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            assert je_fresh["statement_line_id"] == txn_id
            assert je_fresh["bank_statement_id"] == stmt_id

            # IDEMPOTENCE - 2eme run -> 0 modification
            await _backfill(args_exec)
            je_after_2 = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            assert je_after_2["statement_line_id"] == txn_id  # inchange
        finally:
            await db.bank_statements.delete_one({"id": stmt_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_one({"id": je_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 5 : cleanup_orphaned_fi hard-delete des FIs sans txn parente
# ---------------------------------------------------------------------------
def test_cleanup_orphaned_fi_hard_deletes_when_source_txn_missing():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.cleanup_orphaned_fi import _run as _cleanup
        import argparse

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jm5-{suffix}"
        orphan_je = f"je-orphan-{suffix}"
        live_je = f"je-live-{suffix}"
        live_txn = f"txn-live-{suffix}"
        try:
            # Une txn "vivante" avec son FI
            await db.bank_transactions.insert_one({
                "id": live_txn, "copropriete_id": acp,
                "date": "2026-08-01", "amount": 100.0,
            })
            await db.journal_entries.insert_one({
                "id": live_je, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-08-01",
                "auto_generated": True,
                "source_type": "bank_txn", "source_id": live_txn,
                "total_debit": 100, "total_credit": 100, "lines": [],
            })
            # Un FI ORPHELIN dont la txn a disparu
            await db.journal_entries.insert_one({
                "id": orphan_je, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-08-01",
                "auto_generated": True,
                "source_type": "bank_txn", "source_id": f"ghost-{suffix}",
                "total_debit": 50, "total_credit": 50, "lines": [],
            })

            # DRY-RUN
            args_dry = argparse.Namespace(execute=False, copropriete_id=acp)
            await _cleanup(args_dry)
            assert await db.journal_entries.find_one({"id": orphan_je}) is not None, (
                "Dry-run ne doit PAS supprimer"
            )
            assert await db.journal_entries.find_one({"id": live_je}) is not None

            # EXECUTE
            args_exec = argparse.Namespace(execute=True, copropriete_id=acp)
            await _cleanup(args_exec)
            assert await db.journal_entries.find_one({"id": orphan_je}) is None, (
                "L'orphelin doit etre HARD-DELETE"
            )
            # Le JE avec txn vivante reste intact
            assert await db.journal_entries.find_one({"id": live_je}) is not None
        finally:
            await db.bank_transactions.delete_one({"id": live_txn})
            await db.journal_entries.delete_many({
                "id": {"$in": [orphan_je, live_je]}
            })

    _run(_go())
