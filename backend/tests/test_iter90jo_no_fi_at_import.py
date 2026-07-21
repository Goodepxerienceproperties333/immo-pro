"""iter90jo : commit-journals ne doit PLUS creer d'ecritures FI a l'import.

Le wizard cree UNIQUEMENT bank_statements + bank_transactions. Les ecritures
FI sont generees APRES coup, lors de la comptabilisation dans l'UI Bancaire.

Tests :
1. commit-journals retourne journal_entries=0 (pas de JE FI creee).
2. Le bank_transaction cree a auto_je_id="" (pas de JE liee).
3. Le bank_statement est toujours cree.
4. La reponse contient une 'note' expliquant que les FI seront generees a la comptabilisation.
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


def _get_commit_journals_handler(db):
    from routes.import_wizard import create_import_wizard_router
    router = create_import_wizard_router(db)
    for r in router.routes:
        if getattr(r, "path", "").endswith("/sessions/{session_id}/commit-journals"):
            return r.endpoint
    raise AssertionError("commit-journals handler not found")


def test_commit_journals_no_fi_no_je_bank_txn_only():
    """iter90jo : commit-journals cree extrait + txn, PAS de JE FI."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import CommitJournalsInput

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jo-{suffix}"
        session_id = str(uuid.uuid4())
        fy_id = f"fy-jo-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jo-{suffix}",
                "bank_accounts": [
                    {"id": "ba-1", "iban": "BE04001952089331",
                     "pcmn_number": "55133100", "label": "Vue",
                     "is_default": True},
                ],
            })
            await db.fiscal_years.insert_one({
                "id": fy_id, "copropriete_id": acp,
                "name": "2026", "start_date": "2026-01-01",
                "end_date": "2026-12-31", "status": "open",
            })
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp,
                "status": "in_progress",
            })
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()), "number": "44000015",
                "name": "Fournisseur Test", "class_num": 4,
                "copropriete_id": acp,
            })

            handler = _get_commit_journals_handler(db)

            import server
            original = server.get_current_user

            async def _fake_su(_req):
                return {"role": "superadmin", "copropriete_ids": [acp]}
            server.get_current_user = _fake_su

            class _FakeReq:
                headers = {}
                class state:
                    copropriete_id = None

            data = CommitJournalsInput(
                transactions=[
                    {
                        "date_value": "2026-01-15",
                        "amount": 100.0,
                        "direction": "out",
                        "libelle": "Paiement fournisseur",
                        "num_doc": "PAY-001",
                        "bank_account": "55133100",
                        "bank_account_label": "Vue",
                        "counterparty_account": "44000015",
                        "counterparty_account_label": "Fournisseur",
                    },
                    {
                        "date_value": "2026-01-20",
                        "amount": 50.0,
                        "direction": "in",
                        "libelle": "Encaissement",
                        "num_doc": "IN-001",
                        "bank_account": "55133100",
                        "counterparty_account": "44000015",
                    },
                ],
                bank_account_mapping={},
            )
            try:
                result = await handler(session_id=session_id, data=data, request=_FakeReq())
                # (1) Response contract : journal_entries=0
                assert isinstance(result, dict)
                assert result.get("journal_entries") == 0, (
                    f"Attendu journal_entries=0, got {result.get('journal_entries')}"
                )
                assert result.get("statements_created", 0) >= 1
                assert "note" in result and "comptabilisation" in result["note"].lower()

                # (2) Aucun JE FI ne doit etre en DB pour cette ACP
                je_count = await db.journal_entries.count_documents({
                    "copropriete_id": acp, "journal_type": "FI",
                })
                assert je_count == 0, f"Aucun JE FI attendu, trouve {je_count}"

                # (3) bank_transactions avec auto_je_id=""
                txns = await db.bank_transactions.find(
                    {"copropriete_id": acp, "import_session_id": session_id},
                    {"_id": 0, "auto_je_id": 1, "amount": 1},
                ).to_list(length=None)
                assert len(txns) == 2, f"Attendu 2 txns, got {len(txns)}"
                for t in txns:
                    assert t.get("auto_je_id", "") == "", (
                        f"auto_je_id doit etre vide (pas de JE). Vu: {t.get('auto_je_id')}"
                    )

                # (4) bank_statements crees
                stmts_count = await db.bank_statements.count_documents({
                    "copropriete_id": acp, "import_session_id": session_id,
                })
                assert stmts_count >= 1
            finally:
                server.get_current_user = original
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.fiscal_years.delete_one({"id": fy_id})
            await db.import_sessions.delete_one({"id": session_id})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.bank_statements.delete_many({"copropriete_id": acp})
            await db.bank_statement_lines.delete_many({"copropriete_id": acp})
            await db.bank_transactions.delete_many({"copropriete_id": acp})

    _run(_go())
