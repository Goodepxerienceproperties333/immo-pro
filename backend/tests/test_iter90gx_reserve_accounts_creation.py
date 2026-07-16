"""iter90gx — Verrou : `assign_owner_accounts` doit creer les comptes
manquants (reserve OU provisions) meme si un mapping partiel existe deja.

Contexte utilisateur :
  - Ordre du user (16/07/2026) : "il faut qu'une fois des corrections
    executees tu verouilles les choses apres validation sinon on arrete
    jamais de corriger des bugs".
  - Bug reproduit sur ACP Maria Test V1 : owners issus d'imports Optipro
    legacy n'avaient QUE `provisions` renseigne dans `tier_accounts[copro]`,
    pas `reserve`. `assign_owner_accounts` retournait tot au path 'already
    assigned' et ne creait jamais le compte reserve manquant.
  - Consequence : `generate_sale_entry` (auto_entries.py) skippait toutes
    les lignes reserve (car `accs.get("reserve") is None`), le VE final
    etait vide et `return None` -> le journal Ventes ne contenait AUCUN
    appel de fonds de reserve alors que le `fund_call` etait cree.

Tests :
  1. Owner avec `tier_accounts[copro] = {provisions: X}` (SANS reserve)
     -> apres `assign_owner_accounts`, la cle `reserve` doit exister et
     pointer sur un compte PCMN 41000XXX.
  2. Owner avec `tier_accounts[copro] = {reserve: Y}` (SANS provisions)
     -> apres `assign_owner_accounts`, la cle `provisions` doit exister.
  3. Owner deja complet -> aucune modif (fast path preserve).
  4. Un fund_call type=reserve genere bien un VE non-vide.
"""
import os
import uuid
import pytest
import asyncio


@pytest.fixture(scope="function")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def db(event_loop):
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from motor.motor_asyncio import AsyncIOMotorClient
    # Client Motor doit etre cree DANS l'event loop utilise par les tests
    asyncio.set_event_loop(event_loop)
    c = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=event_loop)
    return c[os.environ["DB_NAME"]]


@pytest.fixture(scope="function")
def test_copro(db, event_loop):
    """Cree une ACP jetable, la nettoie a la fin."""
    copro_id = f"test-copro-iter90gx-{uuid.uuid4()}"
    event_loop.run_until_complete(db.coproprietes.insert_one({
        "id": copro_id,
        "name": "Test iter90gx",
        "syndic_user_id": "test",
    }))
    yield copro_id
    event_loop.run_until_complete(db.coproprietes.delete_one({"id": copro_id}))
    event_loop.run_until_complete(db.owners.delete_many({"copropriete_ids": copro_id}))
    event_loop.run_until_complete(db.journal_entries.delete_many({"copropriete_id": copro_id}))
    event_loop.run_until_complete(db.fund_calls.delete_many({"copropriete_id": copro_id}))
    event_loop.run_until_complete(db.pcmn_accounts.delete_many({"copropriete_id": copro_id}))


def test_iter90gx_assign_creates_missing_reserve(db, test_copro, event_loop):
    """Owner legacy avec provisions=X mais SANS reserve -> reserve doit etre cree."""
    from tier_accounts import assign_owner_accounts
    oid = f"o-{uuid.uuid4()}"
    event_loop.run_until_complete(db.owners.insert_one({
        "id": oid,
        "name": "Legacy Optipro Owner",
        "last_name": "Optipro",
        "copropriete_ids": [test_copro],
        # tier_accounts partiel : reserve manquant
        "tier_accounts": {test_copro: {"provisions": "4100001"}},
    }))
    o = event_loop.run_until_complete(db.owners.find_one({"id": oid}))
    o2 = event_loop.run_until_complete(assign_owner_accounts(db, o, test_copro))
    accs = (o2.get("tier_accounts") or {}).get(test_copro, {})
    assert accs.get("provisions") == "4100001", f"Provisions preserve: {accs}"
    assert accs.get("reserve") and accs["reserve"].startswith("41000"), \
        f"Reserve doit avoir ete cree (prefixe 41000) : {accs}"


def test_iter90gx_assign_creates_missing_provisions(db, test_copro, event_loop):
    """Owner avec reserve=X mais SANS provisions -> provisions doit etre cree."""
    from tier_accounts import assign_owner_accounts
    oid = f"o-{uuid.uuid4()}"
    event_loop.run_until_complete(db.owners.insert_one({
        "id": oid,
        "name": "Reserve-only Owner",
        "last_name": "Test",
        "copropriete_ids": [test_copro],
        "tier_accounts": {test_copro: {"reserve": "41000099"}},
    }))
    o = event_loop.run_until_complete(db.owners.find_one({"id": oid}))
    o2 = event_loop.run_until_complete(assign_owner_accounts(db, o, test_copro))
    accs = (o2.get("tier_accounts") or {}).get(test_copro, {})
    assert accs.get("reserve") == "41000099", f"Reserve preserve: {accs}"
    assert accs.get("provisions") and accs["provisions"].startswith("410"), \
        f"Provisions doit avoir ete cree : {accs}"


def test_iter90gx_assign_idempotent_when_complete(db, test_copro, event_loop):
    """Owner deja complet (provisions + reserve) -> aucun nouvel appel Mongo update."""
    from tier_accounts import assign_owner_accounts
    oid = f"o-{uuid.uuid4()}"
    event_loop.run_until_complete(db.owners.insert_one({
        "id": oid,
        "name": "Complete Owner",
        "last_name": "Complete",
        "copropriete_ids": [test_copro],
        "tier_accounts": {test_copro: {"provisions": "4100050", "reserve": "41000050"}},
    }))
    o = event_loop.run_until_complete(db.owners.find_one({"id": oid}))
    o2 = event_loop.run_until_complete(assign_owner_accounts(db, o, test_copro))
    accs = (o2.get("tier_accounts") or {}).get(test_copro, {})
    # Fast path preserve les 2 valeurs (aucune modif)
    assert accs == {"provisions": "4100050", "reserve": "41000050"}, \
        f"Fast path doit preserver le mapping complet : {accs}"


def test_iter90gx_reserve_fund_call_generates_valid_ve(db, test_copro, event_loop):
    """Regression test end-to-end : fund_call type=reserve produit un VE non-vide."""
    from tier_accounts import assign_owner_accounts
    from auto_entries import generate_sale_entry
    # Cree 2 owners avec tier_accounts partiels (bug reproduit)
    oid1 = f"o-{uuid.uuid4()}"
    oid2 = f"o-{uuid.uuid4()}"
    for oid, name in [(oid1, "Alpha"), (oid2, "Beta")]:
        event_loop.run_until_complete(db.owners.insert_one({
            "id": oid, "name": name, "last_name": name,
            "copropriete_ids": [test_copro],
            "tier_accounts": {test_copro: {"provisions": f"4100{oid[:3]}"}},
        }))
    # Assigne les comptes (cree reserve manquant grace au fix iter90gx)
    for oid in [oid1, oid2]:
        o = event_loop.run_until_complete(db.owners.find_one({"id": oid}))
        event_loop.run_until_complete(assign_owner_accounts(db, o, test_copro))
    # Fund call reserve
    fc = {
        "id": f"fc-{uuid.uuid4()}",
        "copropriete_id": test_copro,
        "call_type": "reserve",
        "name": "Reserve Annuel 2026",
        "date": "2026-03-01",
        "total_amount": 1000.0,
        "reserve_amount": 0,  # bug initial : injecte totalement via ct=reserve
        "distribution": [
            {"owner_id": oid1, "amount": 600.0},
            {"owner_id": oid2, "amount": 400.0},
        ],
    }
    event_loop.run_until_complete(db.fund_calls.insert_one(dict(fc)))
    ve = event_loop.run_until_complete(generate_sale_entry(db, fc))
    assert ve is not None, "VE ne doit PAS etre None (bug pre-iter90gx)"
    assert ve["total_debit"] == 1000.0
    assert ve["total_credit"] == 1000.0
    # Verifie que le compte 160 (Fonds de reserve, classe 1) est bien credite
    line_160 = [l for l in ve["lines"] if l["account_number"] == "160"]
    assert len(line_160) == 1, f"1 ligne credit 160 attendue : {ve['lines']}"
    assert line_160[0]["credit"] == 1000.0
    # Verifie que les comptes reserve owners (41000XXX) sont debites
    debit_lines = [l for l in ve["lines"] if l.get("debit", 0) > 0]
    assert all(l["account_number"].startswith("41000") for l in debit_lines), \
        f"Toutes les debits doivent etre sur comptes reserve owner (41000...): {debit_lines}"
    total_owner_debits = sum(l["debit"] for l in debit_lines)
    assert abs(total_owner_debits - 1000.0) < 0.01
