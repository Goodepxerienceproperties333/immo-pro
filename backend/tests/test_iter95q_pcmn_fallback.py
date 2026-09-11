"""iter95q - Test du fallback pour resoudre un IBAN qui est en realite un
code PCMN 55xxxx (cas frequent des imports CODA/PDF sans vrai IBAN).

Verifie aussi que le message d'erreur distingue clairement le cas "PCMN
au lieu d'IBAN" du cas "IBAN classique non configure".
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv

sys.path.insert(0, '/app/backend')
load_dotenv('/app/backend/.env')

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from auto_entries import BankAccountNotConfigured, _resolve_bank_account  # noqa: E402


async def _setup_copro(db, has_bank_account=False, has_pcmn=False):
    copro_id = f"test-iter95q-{uuid.uuid4()}"
    bank_accounts = []
    if has_bank_account:
        bank_accounts = [{
            "iban": "BE68539007547034", "pcmn_number": "551000",
            "label": "Compte principal", "account_type": "vue",
        }]
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "TEST iter95q",
        "bank_accounts": bank_accounts,
    })
    if has_pcmn:
        await db.pcmn_accounts.insert_one({
            "copropriete_id": copro_id,
            "number": "55100000", "name": "Compte 551000",
            "class_num": 5, "type": "balance",
        })
    return copro_id


async def _teardown(db, copro_id):
    await db.coproprietes.delete_one({"id": copro_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})


async def test_pcmn_fallback_matches_when_ba_missing():
    """iter95q : PCMN dans plan comptable -> match meme sans bank_account."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup_copro(db, has_bank_account=False, has_pcmn=True)
    try:
        txn = {"account_number": "551000", "copropriete_id": copro_id}
        acc, label = await _resolve_bank_account(db, txn, copro_id)
        assert acc == "55100000", f"Expected 55100000, got {acc}"
        print(f"PASS test_pcmn_fallback_matches_when_ba_missing (acc={acc}, label={label})")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_ba_match_still_priority():
    """La regle 2 (bank_account.pcmn_number) reste prioritaire sur le fallback."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup_copro(db, has_bank_account=True, has_pcmn=True)
    try:
        txn = {"account_number": "551000", "copropriete_id": copro_id}
        acc, label = await _resolve_bank_account(db, txn, copro_id)
        assert acc == "55100000"
        assert label == "Compte principal", f"Should use bank_account label, got '{label}'"
        print(f"PASS test_ba_match_still_priority (label={label})")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_error_message_when_looks_like_pcmn():
    """Message d'erreur clair quand l'IBAN est en fait un code PCMN."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup_copro(db, has_bank_account=False, has_pcmn=False)
    try:
        txn = {"account_number": "551000", "copropriete_id": copro_id}
        try:
            await _resolve_bank_account(db, txn, copro_id)
            raise AssertionError("Devait lever BankAccountNotConfigured")
        except BankAccountNotConfigured as e:
            msg = str(e)
            assert "PCMN" in msg or "pcmn" in msg or "55xxxx" in msg, msg
            assert "551000" in msg, msg
            print(f"PASS test_error_message_when_looks_like_pcmn : {msg[:120]}...")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_error_message_when_real_iban():
    """Message d'erreur classique quand l'IBAN est un vrai IBAN mais non configure."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup_copro(db, has_bank_account=False, has_pcmn=False)
    try:
        txn = {"account_number": "BE12345678901234", "copropriete_id": copro_id}
        try:
            await _resolve_bank_account(db, txn, copro_id)
            raise AssertionError("Devait lever BankAccountNotConfigured")
        except BankAccountNotConfigured as e:
            msg = str(e)
            assert "BE12345678901234" in msg
            assert "PCMN (55xxxx)" not in msg  # Ce n'est PAS le message PCMN
            print(f"PASS test_error_message_when_real_iban : {msg[:120]}...")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def main():
    await test_pcmn_fallback_matches_when_ba_missing()
    await test_ba_match_still_priority()
    await test_error_message_when_looks_like_pcmn()
    await test_error_message_when_real_iban()
    print("\nAll iter95q tests OK")


if __name__ == "__main__":
    asyncio.run(main())
