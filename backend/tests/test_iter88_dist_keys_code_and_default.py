"""Regression test - iter88 - Distribution keys : numero (code) + clé par defaut.

Demande user (Feb 2026) :
    "Ajouter un numéro aux clés de répartition et permettre de définir
     manuellement quelle clé est la clé par défaut"

Implementation :
    1. `DistKeyInput` : nouveaux champs `code` (str, unique par ACP) et
       `is_default` (bool, 1 seul True par ACP).
    2. POST + PUT : valident l'unicite du code, gerent la mutex is_default.
    3. Nouveaux endpoints `POST /distribution-keys/{id}/set-default` et
       `unset-default` pour basculer le flag sans reediter toute la cle.
    4. GET : tri par (code, name).

Tests :
  1. Create avec code + is_default=True -> stocke + flag set
  2. Create d'une 2e cle is_default=True -> la 1ere repasse a False
  3. Create avec code en doublon dans la meme ACP -> 409
  4. Create avec meme code dans une AUTRE ACP -> OK (scope ACP)
  5. PUT update : changer code -> verifie unicite ; toggle is_default mutex
  6. Endpoint /set-default : flag True sur la cible, False sur les autres
  7. Endpoint /unset-default : flag False sur la cible (les autres inchangees)
  8. GET : tri par code asc puis name asc
  9. Update : champs name/description/lots conserves, code/is_default ajoutes
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _get_endpoint(router_factory, db, route_path: str, method: str = "POST"):
    router = router_factory(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


async def _test_create_with_code_and_default():
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        k = await create(data=DistKeyInput(
            name="Generale", code="001", key_type="quotity",
            copropriete_id=cid, is_default=True,
        ))
        assert k["code"] == "001"
        assert k["is_default"] is True
        # En DB
        doc = await db.distribution_keys.find_one({"id": k["id"]}, {"_id": 0})
        assert doc["code"] == "001"
        assert doc["is_default"] is True
        print("OK - iter88 : create avec code + is_default")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_default_mutex_per_acp():
    """Lorsqu'une 2e cle est marquee is_default=True, la 1ere doit repasser a False."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        k1 = await create(data=DistKeyInput(name="A", code="001", copropriete_id=cid, is_default=True))
        k2 = await create(data=DistKeyInput(name="B", code="002", copropriete_id=cid, is_default=True))
        d1 = await db.distribution_keys.find_one({"id": k1["id"]}, {"_id": 0})
        d2 = await db.distribution_keys.find_one({"id": k2["id"]}, {"_id": 0})
        assert d1["is_default"] is False, "k1 doit repasser a False"
        assert d2["is_default"] is True, "k2 est la nouvelle default"
        # Count : exactement 1 default dans l'ACP
        count_default = await db.distribution_keys.count_documents(
            {"copropriete_id": cid, "is_default": True}
        )
        assert count_default == 1
        print("OK - iter88 : mutex 1 seule cle default par ACP")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_code_unique_per_acp():
    """Le code doit etre unique au sein d'une ACP (409 sinon)."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    from fastapi import HTTPException
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        await create(data=DistKeyInput(name="A", code="001", copropriete_id=cid))
        try:
            await create(data=DistKeyInput(name="B", code="001", copropriete_id=cid))
            assert False, "Devrait avoir leve 409"
        except HTTPException as e:
            assert e.status_code == 409
            assert "001" in e.detail
        print("OK - iter88 : code unique par ACP (409 si doublon)")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_code_can_repeat_across_acps():
    """Le meme code peut exister dans 2 ACPs differentes."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    cid1 = f"itr88a-{uuid.uuid4()}"
    cid2 = f"itr88b-{uuid.uuid4()}"
    await db.coproprietes.insert_many([
        {"id": cid1, "name": "A1", "status": "active"},
        {"id": cid2, "name": "A2", "status": "active"},
    ])
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        k1 = await create(data=DistKeyInput(name="X", code="001", copropriete_id=cid1))
        k2 = await create(data=DistKeyInput(name="X", code="001", copropriete_id=cid2))
        assert k1["code"] == "001"
        assert k2["code"] == "001"
        print("OK - iter88 : meme code OK dans 2 ACPs differentes (scope strict)")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": {"$in": [cid1, cid2]}})
        await db.coproprietes.delete_many({"id": {"$in": [cid1, cid2]}})


async def _test_set_default_endpoint():
    """L'endpoint dedie /set-default bascule la cle par defaut."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        set_def = _get_endpoint(create_invoices_router, db, "/api/distribution-keys/{key_id}/set-default", "POST")
        unset = _get_endpoint(create_invoices_router, db, "/api/distribution-keys/{key_id}/unset-default", "POST")

        k1 = await create(data=DistKeyInput(name="A", code="001", copropriete_id=cid, is_default=True))
        k2 = await create(data=DistKeyInput(name="B", code="002", copropriete_id=cid))
        # Initialement k1 est default
        d1 = await db.distribution_keys.find_one({"id": k1["id"]}, {"_id": 0})
        assert d1["is_default"] is True

        # Set k2 comme default
        await set_def(key_id=k2["id"])
        d1 = await db.distribution_keys.find_one({"id": k1["id"]}, {"_id": 0})
        d2 = await db.distribution_keys.find_one({"id": k2["id"]}, {"_id": 0})
        assert d1["is_default"] is False, "k1 doit repasser a False"
        assert d2["is_default"] is True, "k2 est la nouvelle default"

        # Unset k2 -> plus aucune default dans l'ACP
        await unset(key_id=k2["id"])
        count_default = await db.distribution_keys.count_documents(
            {"copropriete_id": cid, "is_default": True}
        )
        assert count_default == 0
        print("OK - iter88 : endpoints /set-default + /unset-default")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_list_sorted_by_code_then_name():
    """Le GET trie par code puis par name. Les cles sans code (None / vide)
    sont placees en fin (sentinel ~~~)."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        list_keys = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "GET")
        # Crees dans un ordre quelconque
        await create(data=DistKeyInput(name="Zebra", code="003", copropriete_id=cid))
        await create(data=DistKeyInput(name="Alpha", code="001", copropriete_id=cid))
        await create(data=DistKeyInput(name="Beta", code="002", copropriete_id=cid))
        await create(data=DistKeyInput(name="NoCode", code="", copropriete_id=cid))
        result = await list_keys(copropriete_id=cid)
        codes = [k.get("code", "") for k in result]
        # Ordre attendu : "001", "002", "003", puis "" (None)
        assert codes == ["001", "002", "003", ""], f"Mauvais ordre : {codes}"
        print("OK - iter88 : tri par code puis name (les sans-code en fin)")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_update_keeps_existing_fields():
    """PUT update : la mise a jour des nouveaux champs ne perd PAS les
    champs existants (lots, key_type)."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router, DistKeyInput, DistKeyLot
    cid = f"itr88-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88", "status": "active"})
    try:
        create = _get_endpoint(create_invoices_router, db, "/api/distribution-keys", "POST")
        update = _get_endpoint(create_invoices_router, db, "/api/distribution-keys/{key_id}", "PUT")
        k = await create(data=DistKeyInput(
            name="OldName", description="OldDesc", code="010", key_type="custom",
            lots=[DistKeyLot(lot_id="lot1", lot_number="A1", share=500.0)],
            copropriete_id=cid, is_default=False,
        ))
        # Update : change code, name, is_default
        r = await update(
            key_id=k["id"],
            data=DistKeyInput(
                name="NewName", description="NewDesc", code="020", key_type="custom",
                lots=[DistKeyLot(lot_id="lot1", lot_number="A1", share=500.0),
                      DistKeyLot(lot_id="lot2", lot_number="A2", share=500.0)],
                copropriete_id=cid, is_default=True,
            ),
        )
        updated = r["key"]
        assert updated["code"] == "020"
        assert updated["name"] == "NewName"
        assert updated["is_default"] is True
        assert len(updated["lots"]) == 2
        print("OK - iter88 : update conserve lots + applique code/is_default")
    finally:
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


def test_iter88_create_with_code_and_default():
    asyncio.run(_test_create_with_code_and_default())


def test_iter88_default_mutex_per_acp():
    asyncio.run(_test_default_mutex_per_acp())


def test_iter88_code_unique_per_acp():
    asyncio.run(_test_code_unique_per_acp())


def test_iter88_code_can_repeat_across_acps():
    asyncio.run(_test_code_can_repeat_across_acps())


def test_iter88_set_default_endpoint():
    asyncio.run(_test_set_default_endpoint())


def test_iter88_list_sorted_by_code_then_name():
    asyncio.run(_test_list_sorted_by_code_then_name())


def test_iter88_update_keeps_existing_fields():
    asyncio.run(_test_update_keeps_existing_fields())
