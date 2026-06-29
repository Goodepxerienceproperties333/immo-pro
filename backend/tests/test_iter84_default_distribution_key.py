"""Regression test - iter84 - Cle de repartition par defaut sur nature de depense.

Demande user : "permettre de definir cles repartition par defaut".

Comportement :
  - ExpenseCategory : nouveau champ `default_distribution_key_id`
  - Frontend : pre-remplit automatiquement le champ distribution_key_id des
    lignes de facture/budget quand on selectionne cette nature
  - List endpoint expose `default_distribution_key_name` pour affichage

Tests :
  1. Create/Update : default_distribution_key_id persiste
  2. List endpoint enrichit avec default_distribution_key_name
  3. Backward compat : pas de cle = "" (tantiemes generaux)
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr84k-{uuid.uuid4()}"
    key_id = f"k-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "KEY DEFAULT", "status": "active"})
    await db.pcmn_accounts.insert_one({
        "number": "610001", "name": "Entretien ascenseur", "class_num": 6,
        "copropriete_id": cid,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "name": "Cle Ascenseur",
        "copropriete_id": cid, "lots": [],
    })
    return {"db": db, "cid": cid, "key_id": key_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.distribution_keys.delete_one({"id": ctx["key_id"]})
    await db.expense_categories.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str, method: str = "POST"):
    from routes.expense_categories import create_expense_categories_router
    router = create_expense_categories_router(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


async def _test_create_with_default_key():
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/expense-categories", "POST")
        list_fn = _get_endpoint(db, "/api/expense-categories", "GET")
        Input = create_fn.__annotations__.get("data")

        payload = Input(
            name="Entretien ascenseur",
            account_number="610001",
            copropriete_id=ctx["cid"],
            default_distribution_key_id=ctx["key_id"],
        )
        created = await create_fn(data=payload)
        assert created.get("default_distribution_key_id") == ctx["key_id"]
        assert created.get("account_number") == "610001"

        # List doit retourner default_distribution_key_name
        cats = await list_fn(copropriete_id=ctx["cid"])
        assert len(cats) == 1
        assert cats[0]["default_distribution_key_id"] == ctx["key_id"]
        assert cats[0]["default_distribution_key_name"] == "Cle Ascenseur"
        print("OK - create avec cle de repartition par defaut + list enrichi")
    finally:
        await _cleanup(ctx)


async def _test_create_without_default_key():
    """Backward compat : sans cle = chaine vide, pas d'erreur."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/expense-categories", "POST")
        list_fn = _get_endpoint(db, "/api/expense-categories", "GET")
        Input = create_fn.__annotations__.get("data")

        payload = Input(
            name="Charges sans cle",
            account_number="610001",
            copropriete_id=ctx["cid"],
        )
        created = await create_fn(data=payload)
        # Champ present mais vide
        assert created.get("default_distribution_key_id", "") == ""
        cats = await list_fn(copropriete_id=ctx["cid"])
        assert cats[0].get("default_distribution_key_name", "") == ""
        print("OK - backward compat : pas de cle defaut = chaine vide")
    finally:
        await _cleanup(ctx)


async def _test_update_default_key():
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/expense-categories", "POST")
        update_fn = _get_endpoint(db, "/api/expense-categories/{cat_id}", "PUT")
        Input = create_fn.__annotations__.get("data")
        payload = Input(name="Cat 1", account_number="610001", copropriete_id=ctx["cid"])
        created = await create_fn(data=payload)

        # Update : ajoute la cle
        new_payload = Input(
            name="Cat 1", account_number="610001", copropriete_id=ctx["cid"],
            default_distribution_key_id=ctx["key_id"],
        )
        updated = await update_fn(cat_id=created["id"], data=new_payload)
        assert updated.get("default_distribution_key_id") == ctx["key_id"]

        # Update : retire la cle (passe a "")
        new_payload2 = Input(
            name="Cat 1", account_number="610001", copropriete_id=ctx["cid"],
            default_distribution_key_id="",
        )
        updated2 = await update_fn(cat_id=created["id"], data=new_payload2)
        assert updated2.get("default_distribution_key_id", "") == ""
        print("OK - update default_distribution_key_id (set + clear)")
    finally:
        await _cleanup(ctx)


def test_iter84_create_with_default_key():
    asyncio.run(_test_create_with_default_key())


def test_iter84_create_without_default_key():
    asyncio.run(_test_create_without_default_key())


def test_iter84_update_default_key():
    asyncio.run(_test_update_default_key())
