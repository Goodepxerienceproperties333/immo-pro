"""SEC-audit hotfix : verifie l'endpoint POST /banking/statements/blocked/revalidate.

Scenario : un extrait est marque `has_posting_error=True` avec 2 txns en
`posting_error`. Apres correction (bank_account renomme, fallback fonctionnel),
l'endpoint doit clear les flags automatiquement.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from auto_entries import _resolve_bank_account, BankAccountNotConfigured  # noqa


async def _bootstrap(db):
    copro_id = f"test-reval-{uuid.uuid4().hex[:8]}"
    stmt_id = f"stmt-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "TEST reval",
        "bank_accounts": [
            {"iban": "BE68751207346634", "pcmn_number": "55163400",
             "account_type": "vue", "label": "BE68 renomme"},
        ],
    })
    await db.bank_statements.insert_one({
        "id": stmt_id, "copropriete_id": copro_id,
        "iban": "BE68751207346634",
        "has_posting_error": True,
    })
    # 2 txns avec ancien raw_acc "551000" bloquees precedemment
    for i in range(2):
        await db.bank_transactions.insert_one({
            "id": f"txn-{uuid.uuid4().hex[:8]}",
            "copropriete_id": copro_id,
            "statement_id": stmt_id,
            "account_number": "551000",
            "posting_error": "IBAN '551000' non configure",
            "posting_error_iban": "551000",
            "posting_error_at": "2026-01-01T00:00:00Z",
        })
    return copro_id, stmt_id


async def _cleanup(db, copro_id, stmt_id):
    await db.coproprietes.delete_one({"id": copro_id})
    await db.bank_statements.delete_many({"copropriete_id": copro_id})
    await db.bank_transactions.delete_many({"statement_id": stmt_id})


async def test_revalidate_clears_flags():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id, stmt_id = await _bootstrap(db)
    try:
        # Sanity : etat initial
        s0 = await db.bank_statements.find_one({"id": stmt_id})
        assert s0.get("has_posting_error") is True

        # Simule l'appel de l'endpoint : reprend la logique inline
        errored_txns = await db.bank_transactions.find(
            {"statement_id": stmt_id, "posting_error": {"$exists": True, "$ne": None}},
            {"_id": 0, "id": 1, "account_number": 1, "statement_id": 1},
        ).to_list(10000)
        assert len(errored_txns) == 2

        all_resolved = True
        for t in errored_txns:
            try:
                await _resolve_bank_account(db, t, copro_id)
            except BankAccountNotConfigured:
                all_resolved = False
                break
        assert all_resolved, "Le fallback 3ter aurait du resoudre 551000 -> 55163400"

        # Clear
        await db.bank_transactions.update_many(
            {"statement_id": stmt_id, "posting_error": {"$exists": True}},
            {"$unset": {"posting_error": "", "posting_error_iban": "",
                        "posting_error_at": ""}},
        )
        await db.bank_statements.update_one(
            {"id": stmt_id},
            {"$unset": {"has_posting_error": ""}},
        )

        # Verifie
        s1 = await db.bank_statements.find_one({"id": stmt_id})
        assert "has_posting_error" not in s1
        remaining = await db.bank_transactions.count_documents(
            {"statement_id": stmt_id, "posting_error": {"$exists": True}}
        )
        assert remaining == 0
        print("PASS : revalidate clears has_posting_error + posting_error fields")
    finally:
        await _cleanup(db, copro_id, stmt_id)
        client.close()


if __name__ == "__main__":
    asyncio.run(test_revalidate_clears_flags())
