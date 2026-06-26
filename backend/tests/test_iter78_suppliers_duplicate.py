"""Regression test - iter78 - Check anti-doublon fournisseurs.

Demande : "pas de doublon de fournisseur autorise, check fait sur le nr BCE,
nom et compte bancaire".

Implementation :
- Helper `find_duplicate_supplier()` dans routes/suppliers.py
- POST /api/suppliers : 409 si doublon detecte
- PUT /api/suppliers/{id} : 409 si l'edit cree un doublon avec un AUTRE supplier
- import_wizard (CSV + PDF) : skip silencieux des doublons (skipped_duplicates retourne)

Normalisation :
- Nom : minuscules + espaces multiples reduits + trim
- BCE/TVA/IBAN : alphanumerique uppercase uniquement (ignore espaces/points/tirets)

Scope : limite a l'ACP (chinese wall). 2 ACPs peuvent avoir le meme fournisseur
sans declencher de doublon (separation comptable totale).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _setup_acp(db):
    cid = f"itr78-{uuid.uuid4()}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER78", "reference": "TEST-ITER78", "status": "active",
    })
    return cid


async def _cleanup(db, cid):
    await db.coproprietes.delete_many({"id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})


def test_normalize_helpers():
    from routes.suppliers import _norm_name, _norm_id
    # Name : casse + espaces + tri alphabetique des mots (tolere l'ordre)
    assert _norm_name("Engie  SA  ") == "engie sa"
    assert _norm_name("ENGIE SA") == "engie sa"
    assert _norm_name("engie sa") == "engie sa"
    # Reorder : "Finlead srl" == "SRL Finlead" apres tri
    assert _norm_name("Finlead srl") == _norm_name("SRL Finlead")
    assert _norm_name("Finlead srl") == "finlead srl"
    # BCE/IBAN : alphanumerique
    assert _norm_id("BE 0123.456.789") == "BE0123456789"
    assert _norm_id("BE0123456789") == "BE0123456789"
    assert _norm_id("be0123-456-789") == "BE0123456789"
    # IBAN avec espaces
    assert _norm_id("BE12 3456 7890 1234") == "BE12345678901234"


async def _test_duplicate_bce():
    """Insertion d'un fournisseur avec meme BCE (normalisation differente) -> doublon."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = await _setup_acp(db)
    try:
        # Premier fournisseur avec BCE "BE0123.456.789"
        await db.suppliers.insert_one({
            "id": "s1", "name": "Engie SA", "bce_number": "BE0123.456.789",
            "iban": "BE12 3456 7890 1234", "copropriete_id": cid,
        })
        # Recherche avec format different "be 0123456789"
        dup = await find_duplicate_supplier(
            db, name="Different Name", bce_number="be 0123456789",
            vat_number="", iban="", copro_id=cid,
        )
        assert dup is not None
        assert dup["field"] == "bce_number"
        assert dup["supplier"]["id"] == "s1"

        # Pas de match avec un BCE different
        dup2 = await find_duplicate_supplier(
            db, name="Other Name", bce_number="BE9999999999",
            vat_number="", iban="", copro_id=cid,
        )
        assert dup2 is None
    finally:
        await _cleanup(db, cid)


async def _test_duplicate_iban():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = await _setup_acp(db)
    try:
        await db.suppliers.insert_one({
            "id": "s1", "name": "Engie SA",
            "iban": "BE12 3456 7890 1234", "copropriete_id": cid,
        })
        # Meme IBAN sans espaces
        dup = await find_duplicate_supplier(
            db, name="Autre Engie", bce_number="", vat_number="",
            iban="BE12345678901234", copro_id=cid,
        )
        assert dup is not None
        assert dup["field"] == "iban"
        assert dup["supplier"]["id"] == "s1"
    finally:
        await _cleanup(db, cid)


async def _test_duplicate_name_normalized():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = await _setup_acp(db)
    try:
        await db.suppliers.insert_one({
            "id": "s1", "name": "ENGIE  SA", "copropriete_id": cid,
        })
        # Meme nom casse + espaces differents
        dup = await find_duplicate_supplier(
            db, name="engie sa", bce_number="", vat_number="", iban="", copro_id=cid,
        )
        assert dup is not None
        assert dup["field"] == "name"
    finally:
        await _cleanup(db, cid)


async def _test_no_duplicate_across_acps():
    """ACP A et ACP B peuvent avoir le meme fournisseur (chinese wall) - PAS un doublon."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid_a = await _setup_acp(db)
    cid_b = await _setup_acp(db)
    try:
        # Fournisseur dans ACP A avec un BCE
        await db.suppliers.insert_one({
            "id": "sA", "name": "Engie", "bce_number": "BE0123456789",
            "copropriete_id": cid_a,
        })
        # Le meme BCE dans ACP B ne doit PAS etre detecte comme doublon
        dup = await find_duplicate_supplier(
            db, name="Engie", bce_number="BE0123456789", vat_number="",
            iban="", copro_id=cid_b,
        )
        assert dup is None, f"Faux positif cross-ACP : {dup}"
    finally:
        await _cleanup(db, cid_a)
        await _cleanup(db, cid_b)


async def _test_exclude_id_on_update():
    """Lors d'un UPDATE on doit pouvoir editer un fournisseur sans declencher
    un doublon contre lui-meme."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = await _setup_acp(db)
    try:
        await db.suppliers.insert_one({
            "id": "s1", "name": "Engie SA", "bce_number": "BE0123456789",
            "copropriete_id": cid,
        })
        # On 'edite' s1 (meme BCE) -> avec exclude_id, pas de doublon
        dup = await find_duplicate_supplier(
            db, name="Engie SA", bce_number="BE0123456789", vat_number="",
            iban="", copro_id=cid, exclude_id="s1",
        )
        assert dup is None
        # Sans exclude_id, doublon detecte
        dup2 = await find_duplicate_supplier(
            db, name="Engie SA", bce_number="BE0123456789", vat_number="",
            iban="", copro_id=cid,
        )
        assert dup2 is not None
    finally:
        await _cleanup(db, cid)


def test_duplicate_bce():
    asyncio.run(_test_duplicate_bce())


def test_duplicate_iban():
    asyncio.run(_test_duplicate_iban())


def test_duplicate_name_normalized():
    asyncio.run(_test_duplicate_name_normalized())


def test_no_duplicate_across_acps():
    asyncio.run(_test_no_duplicate_across_acps())


def test_exclude_id_on_update():
    asyncio.run(_test_exclude_id_on_update())
