"""iter95u - Test du fallback quand le syndic renomme le PCMN par defaut.

Scenario user :
1. Syndic cree ACP avec IBAN BE68...1000 -> PCMN auto-genere = 55100000
2. Import CODA/PDF -> statements avec account_number=551000
3. Syndic edit ACP et change le PCMN a 55163400
4. Les anciens extraits doivent continuer a etre resolus vers 55163400
   (nouveau PCMN configure) sans blocage.
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv

sys.path.insert(0, '/app/backend')
load_dotenv('/app/backend/.env')

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from auto_entries import _resolve_bank_account, BankAccountNotConfigured  # noqa: E402


async def _setup(db, pcmn_configured):
    copro_id = f"test-iter95u-{uuid.uuid4()}"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "TEST iter95u",
        "bank_accounts": [{
            "iban": "BE68751207341000",  # dernier 3 = "000" -> default=55100000
            "account_type": "vue",
            "pcmn_number": pcmn_configured,
            "label": "Compte renomme",
        }],
    })
    return copro_id


async def _teardown(db, copro_id):
    await db.coproprietes.delete_one({"id": copro_id})


async def test_old_default_pcmn_resolves_to_new_configured():
    """Statement importe avec l'ancien 551000 -> resolu vers le PCMN configure actuel."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup(db, pcmn_configured="55163400")
    try:
        # Ancien import : txn porte le PCMN par defaut 551000
        txn = {"account_number": "551000", "copropriete_id": copro_id}
        acc, label = await _resolve_bank_account(db, txn, copro_id)
        # Doit renvoyer le NOUVEAU PCMN configure
        assert acc == "55163400", f"Attendu 55163400, recu {acc}"
        assert label == "Compte renomme"
        print(f"PASS old_default_pcmn_resolves_to_new_configured (acc={acc})")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_new_pcmn_still_matches():
    """Statement recent porte deja le nouveau PCMN 55163400 -> match direct."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup(db, pcmn_configured="55163400")
    try:
        txn = {"account_number": "55163400", "copropriete_id": copro_id}
        acc, label = await _resolve_bank_account(db, txn, copro_id)
        assert acc == "55163400"
        print(f"PASS new_pcmn_still_matches (acc={acc})")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_default_when_pcmn_not_renamed():
    """Cas normal : PCMN configure = default (aucun renommage)."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup(db, pcmn_configured="55100000")
    try:
        txn = {"account_number": "551000", "copropriete_id": copro_id}
        acc, label = await _resolve_bank_account(db, txn, copro_id)
        assert acc == "55100000"
        print(f"PASS default_when_pcmn_not_renamed (acc={acc})")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def test_random_pcmn_still_blocks():
    """Un vrai IBAN inconnu doit toujours lever l'erreur."""
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    copro_id = await _setup(db, pcmn_configured="55163400")
    try:
        txn = {"account_number": "552888", "copropriete_id": copro_id}
        try:
            await _resolve_bank_account(db, txn, copro_id)
            raise AssertionError("Devait lever BankAccountNotConfigured")
        except BankAccountNotConfigured as e:
            assert "552888" in str(e)
            print(f"PASS random_pcmn_still_blocks")
    finally:
        await _teardown(db, copro_id)
        c.close()


async def main():
    await test_old_default_pcmn_resolves_to_new_configured()
    await test_new_pcmn_still_matches()
    await test_default_when_pcmn_not_renamed()
    await test_random_pcmn_still_blocks()
    print("\nAll iter95u tests OK")


if __name__ == "__main__":
    asyncio.run(main())
