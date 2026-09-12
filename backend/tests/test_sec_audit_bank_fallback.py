"""SEC-audit hotfix (2026-02): verifie que le fallback bancaire retrouve
le bon PCMN quand l'ancien code stocke (`raw_acc`) ne correspond ni au
`pcmn_number` courant ni au default_pcmn derive de l'IBAN.

Cas reel client ACP LEFRANCQ : IBAN BE68751207346634, ancien pcmn
`551000` (manuellement choisi), renomme en `55163400` par le syndic.
Les extraits importes avant le renommage ont `account_number=551000`.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from auto_entries import _resolve_bank_account, BankAccountNotConfigured


async def _setup_acp(db, iban: str, pcmn_current: str) -> str:
    """Cree une ACP mono-bank avec IBAN + pcmn_number renomme."""
    copro_id = f"test-secaudit-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": copro_id,
        "name": "TEST SEC-audit LEFRANCQ",
        "bank_accounts": [
            {
                "iban": iban,
                "pcmn_number": pcmn_current,  # code apres renommage
                "account_type": "vue",
                "label": "BE68 renomme",
            }
        ],
    })
    return copro_id


async def _cleanup(db, copro_id: str):
    await db.coproprietes.delete_one({"id": copro_id})
    await db.bank_statements.delete_many({"copropriete_id": copro_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})


async def test_case_lefrancq_single_bank():
    """Cas exact du bug client : ancien code `551000`, renomme `55163400`,
    IBAN BE68751207346634 (default derive = `55163400`)."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    iban = "BE68751207346634"
    pcmn_current = "55163400"
    old_raw_acc = "551000"  # code au moment de l'import CODA

    copro_id = await _setup_acp(db, iban, pcmn_current)
    try:
        txn = {"account_number": old_raw_acc, "statement_id": None}
        pcmn, label = await _resolve_bank_account(db, txn, copro_id)
        assert pcmn == pcmn_current, f"Attendu {pcmn_current}, recu {pcmn}"
        assert "renomme" in label.lower()
        print(f"PASS : raw_acc=551000 -> {pcmn} ({label})")
    finally:
        await _cleanup(db, copro_id)
        client.close()


async def test_case_multi_bank_via_statement_iban():
    """Plusieurs bank_accounts : la resolution passe par l'IBAN du statement."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id = f"test-secaudit-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": copro_id,
        "name": "TEST multi-bank",
        "bank_accounts": [
            {"iban": "BE68751207346634", "pcmn_number": "55163400", "account_type": "vue", "label": "BE68"},
            {"iban": "BE12345678901234", "pcmn_number": "55023400", "account_type": "epargne", "label": "BE12"},
        ],
    })
    stmt_id = f"stmt-{uuid.uuid4().hex[:8]}"
    await db.bank_statements.insert_one({
        "id": stmt_id,
        "copropriete_id": copro_id,
        "iban": "BE68751207346634",
    })

    try:
        txn = {"account_number": "551000", "statement_id": stmt_id}
        pcmn, label = await _resolve_bank_account(db, txn, copro_id)
        assert pcmn == "55163400", f"Attendu 55163400, recu {pcmn}"
        assert label == "BE68"
        print(f"PASS multi-bank : raw_acc=551000 + stmt.iban=BE68... -> {pcmn} ({label})")
    finally:
        await _cleanup(db, copro_id)
        client.close()


async def test_regression_exact_match_still_wins():
    """Regression : match exact sur pcmn_number courant reste prioritaire."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id = await _setup_acp(db, "BE68751207346634", "55163400")
    try:
        txn = {"account_number": "55163400", "statement_id": None}
        pcmn, label = await _resolve_bank_account(db, txn, copro_id)
        assert pcmn == "55163400"
        print(f"PASS regression : match direct pcmn courant -> {pcmn}")
    finally:
        await _cleanup(db, copro_id)
        client.close()


async def test_still_blocks_unrelated_pcmn():
    """Regression : un raw_acc bancaire sans bank_account configure -> BLOQUE."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id = f"test-secaudit-nobank-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "TEST no-bank", "bank_accounts": [],
    })

    try:
        txn = {"account_number": "551000", "statement_id": None}
        try:
            await _resolve_bank_account(db, txn, copro_id)
            assert False, "Devait bloquer avec BankAccountNotConfigured"
        except BankAccountNotConfigured:
            print("PASS regression : ACP sans bank_account -> BLOQUE correctement")
    finally:
        await _cleanup(db, copro_id)
        client.close()


async def main():
    await test_case_lefrancq_single_bank()
    await test_case_multi_bank_via_statement_iban()
    await test_regression_exact_match_still_wins()
    await test_still_blocks_unrelated_pcmn()
    print("\nAll SEC-audit bank fallback tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
