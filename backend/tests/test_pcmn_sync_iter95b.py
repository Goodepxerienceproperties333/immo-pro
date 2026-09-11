"""Test iter95b : propagation bank_accounts.label -> pcmn_accounts.name.

Reproduit la logique corrigee de `_create_pcmn_accounts` dans
`routes/coproprietes.py` (fonction interne, non exportee) et verifie :
1. Creation initiale d'un PCMN avec le label.
2. Rename : PUT copropriete change le label -> pcmn_accounts.name suit.
3. Idempotence.
4. Fallback quand label est None.
"""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient


def _generate_pcmn_number(iban: str, account_type: str) -> str:
    digits = ''.join(c for c in iban if c.isdigit())[-4:]
    prefix = "5501" if account_type == "epargne" else "5500"
    return f"{prefix}{digits}"


async def _create_pcmn_accounts(db, bank_accounts, copro_id):
    """Copie fidele de la fonction corrigee dans routes/coproprietes.py."""
    for ba in bank_accounts:
        pn = _generate_pcmn_number(ba["iban"], ba["account_type"])
        default_label = f"Banque {'epargne' if ba['account_type'] == 'epargne' else 'compte a vue'} {ba['iban'][-4:]}"
        desired_name = ba.get("label") or default_label
        existing = await db.pcmn_accounts.find_one({"number": pn, "copropriete_id": copro_id})
        if not existing:
            await db.pcmn_accounts.insert_one({
                "number": pn, "name": desired_name, "class_num": 5,
                "parent": "550000", "type": "balance",
                "copropriete_id": copro_id, "active": True,
            })
        elif existing.get("name") != desired_name:
            await db.pcmn_accounts.update_one(
                {"number": pn, "copropriete_id": copro_id},
                {"$set": {"name": desired_name, "active": True}},
            )


async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    TEST_COPRO_ID = "test-iter95b-pcmn-sync"
    await db.pcmn_accounts.delete_many({"copropriete_id": TEST_COPRO_ID})

    iban = "BE68539007547034"
    pcmn_number = _generate_pcmn_number(iban, "vue")

    # 1. Creation initiale
    await _create_pcmn_accounts(db, [{"iban": iban, "account_type": "vue", "label": "Compte principal"}], TEST_COPRO_ID)
    doc = await db.pcmn_accounts.find_one({"number": pcmn_number, "copropriete_id": TEST_COPRO_ID})
    assert doc['name'] == "Compte principal", f"Step1 FAIL: {doc['name']}"
    print(f"Step1 PASS: creation initiale name='{doc['name']}'")

    # 2. Rename
    await _create_pcmn_accounts(db, [{"iban": iban, "account_type": "vue", "label": "Compte fonds de reserve"}], TEST_COPRO_ID)
    doc = await db.pcmn_accounts.find_one({"number": pcmn_number, "copropriete_id": TEST_COPRO_ID})
    assert doc['name'] == "Compte fonds de reserve", f"Step2 FAIL: {doc['name']}"
    print(f"Step2 PASS: rename propage name='{doc['name']}'")

    # 3. Idempotence
    await _create_pcmn_accounts(db, [{"iban": iban, "account_type": "vue", "label": "Compte fonds de reserve"}], TEST_COPRO_ID)
    doc = await db.pcmn_accounts.find_one({"number": pcmn_number, "copropriete_id": TEST_COPRO_ID})
    assert doc['name'] == "Compte fonds de reserve"
    print(f"Step3 PASS: idempotence OK")

    # 4. Fallback si label None
    await db.pcmn_accounts.update_one({"number": pcmn_number, "copropriete_id": TEST_COPRO_ID}, {"$set": {"name": "obsolete"}})
    await _create_pcmn_accounts(db, [{"iban": iban, "account_type": "vue", "label": None}], TEST_COPRO_ID)
    doc = await db.pcmn_accounts.find_one({"number": pcmn_number, "copropriete_id": TEST_COPRO_ID})
    assert doc['name'] == f"Banque compte a vue {iban[-4:]}", f"Step4 FAIL: {doc['name']}"
    print(f"Step4 PASS: fallback name='{doc['name']}'")

    await db.pcmn_accounts.delete_many({"copropriete_id": TEST_COPRO_ID})
    print("\nAll assertions PASS: iter95b propagation OK")


if __name__ == "__main__":
    asyncio.run(main())
