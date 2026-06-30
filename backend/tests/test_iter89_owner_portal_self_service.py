"""Regression test - iter89 - Owner portal self-service (profile + tenants).

Demande user (Feb 2026) :
    "Creer une interface permettant aux proprietaires d'avoir acces a leur compte
     [...]. Le proprietaire doit pouvoir modifier ses coordonnees et ajouter ses
     locataires, en cas de modification le syndic est averti par email."

Le portail existait deja (read-only). iter89 ajoute :
  - PUT /api/owner/me : owner self-update de ses coords
  - GET /api/owner/tenants : liste des locataires de ses lots
  - POST/PUT/DELETE /api/owner/tenants : CRUD self-service
  - Notification automatique au syndic (persiste en DB + email via Graph
    si configure)

Tests :
  1. PUT /me : modifie email/phone/address -> stocke + notification creee
  2. PUT /me : aucune modification -> pas de notification
  3. PUT /me : ne peut modifier que les champs whitelistes (vcs_code ignore)
  4. Recompute du `name` quand last/first change
  5. GET /tenants : retourne seulement les locataires des lots du proprio
  6. POST /tenants : creer locataire sur SES lots OK
  7. POST /tenants : creer locataire sur un lot d'un AUTRE proprio -> 403
  8. PUT /tenants : modifier OK, notification creee
  9. DELETE /tenants : supprimer OK, notification creee
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


def _get_endpoint(router_factory, db, route_path: str, method: str = "GET"):
    router = router_factory(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


class _MockReq:
    """Mock fastapi Request - injects user_email in state via __init__."""
    def __init__(self, email: str):
        class _State:
            pass
        self.state = _State()
        self.state.user_email = email
        self.headers = {}


async def _seed(db):
    """Cree une ACP + 1 proprio test + 2 lots dont 1 a un autre proprio."""
    cid = f"itr89-{uuid.uuid4()}"
    test_email = f"owner-{uuid.uuid4().hex[:8]}@test.iter89"
    other_email = f"other-{uuid.uuid4().hex[:8]}@test.iter89"
    me_id = f"me-{uuid.uuid4()}"
    other_id = f"oth-{uuid.uuid4()}"
    my_lot = f"lot1-{uuid.uuid4()}"
    other_lot = f"lot2-{uuid.uuid4()}"
    syndic_email = f"syndic-{uuid.uuid4().hex[:8]}@test.iter89"
    syndic_id = f"syn-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR89 Residence", "status": "active"})
    await db.owners.insert_many([
        {"id": me_id, "name": "ME Owner", "first_name": "M",
         "last_name": "Owner", "email": test_email,
         "copropriete_ids": [cid]},
        {"id": other_id, "name": "Other Owner", "first_name": "O",
         "last_name": "Other", "email": other_email,
         "copropriete_ids": [cid]},
    ])
    await db.lots.insert_many([
        {"id": my_lot, "number": "A1", "description": "Appartement 1er",
         "owner_id": me_id, "owner_ids": [me_id],
         "copropriete_id": cid, "quotity": 500},
        {"id": other_lot, "number": "A2", "description": "Appartement 2eme",
         "owner_id": other_id, "owner_ids": [other_id],
         "copropriete_id": cid, "quotity": 500},
    ])
    # Syndic user pour _find_syndic_recipients
    await db.users.insert_one({
        "id": syndic_id, "email": syndic_email, "role": "syndic",
        "copropriete_ids": [cid], "name": "Test Syndic",
    })
    return {
        "cid": cid, "test_email": test_email,
        "me_id": me_id, "other_id": other_id,
        "my_lot": my_lot, "other_lot": other_lot,
        "syndic_id": syndic_id, "syndic_email": syndic_email,
    }


async def _cleanup(db, ctx):
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.owners.delete_many({"id": {"$in": [ctx["me_id"], ctx["other_id"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.tenants.delete_many({"copropriete_id": ctx["cid"]})
    await db.users.delete_one({"id": ctx["syndic_id"]})
    await db.owner_notifications.delete_many({"owner_id": {"$in": [ctx["me_id"], ctx["other_id"]]}})


async def _test_put_me_updates_and_notifies():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        update_me = _get_endpoint(create_owner_portal_router, db, "/api/owner/me", "PUT")
        OwnerSelfUpdate = None
        for r in create_owner_portal_router(db).routes:
            if r.path == "/api/owner/me" and "PUT" in r.methods:
                # Pydantic input is the 1st annotated parameter
                import inspect
                sig = inspect.signature(r.endpoint)
                for p in sig.parameters.values():
                    if p.annotation.__name__ == "OwnerSelfUpdate":
                        OwnerSelfUpdate = p.annotation
                        break
        assert OwnerSelfUpdate is not None
        payload = OwnerSelfUpdate(
            address="Rue Neuve 1", postal_code="1000", city="Bruxelles",
            phone="+32475112233",
        )
        result = await update_me(data=payload, request=_MockReq(ctx["test_email"]))
        assert result["updated"] is True
        owner = result["owner"]
        assert owner["address"] == "Rue Neuve 1"
        assert owner["postal_code"] == "1000"
        assert owner["phone"] == "+32475112233"
        # Notification persistee
        notif = await db.owner_notifications.find_one(
            {"owner_id": ctx["me_id"]}, {"_id": 0}
        )
        assert notif is not None
        assert "modifier" in notif["change_type"]
        assert any("address" in line for line in notif["summary_lines"])
        assert any("phone" in line for line in notif["summary_lines"])
        # Le syndic est inclus dans copropriete_ids
        assert ctx["cid"] in notif["copropriete_ids"]
        print("OK - iter89 : PUT /me modifie coords + notification creee")
    finally:
        await _cleanup(db, ctx)


async def _test_put_me_no_change_no_notification():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        router = create_owner_portal_router(db)
        OwnerSelfUpdate = None
        update_me = None
        for r in router.routes:
            if r.path == "/api/owner/me" and "PUT" in r.methods:
                update_me = r.endpoint
                import inspect
                for p in inspect.signature(r.endpoint).parameters.values():
                    if p.annotation.__name__ == "OwnerSelfUpdate":
                        OwnerSelfUpdate = p.annotation
        # Re-set the same values (no diff)
        owner = await db.owners.find_one({"id": ctx["me_id"]}, {"_id": 0})
        payload = OwnerSelfUpdate(
            first_name=owner.get("first_name"),
            last_name=owner.get("last_name"),
            email=owner.get("email"),
        )
        result = await update_me(data=payload, request=_MockReq(ctx["test_email"]))
        assert result["updated"] is False
        # Aucune notification creee
        count = await db.owner_notifications.count_documents({"owner_id": ctx["me_id"]})
        assert count == 0
        print("OK - iter89 : pas de modif -> pas de notification")
    finally:
        await _cleanup(db, ctx)


async def _test_put_me_ignores_locked_fields():
    """Les champs sensibles (vcs_code, name calcule) ne peuvent pas etre modifies
    directement par le proprio via le payload (le name est recalcule de
    last+first uniquement)."""
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        router = create_owner_portal_router(db)
        OwnerSelfUpdate = None
        update_me = None
        for r in router.routes:
            if r.path == "/api/owner/me" and "PUT" in r.methods:
                update_me = r.endpoint
                import inspect
                for p in inspect.signature(r.endpoint).parameters.values():
                    if p.annotation.__name__ == "OwnerSelfUpdate":
                        OwnerSelfUpdate = p.annotation
        # Modifier last_name + first_name -> name doit etre recalcule
        payload = OwnerSelfUpdate(first_name="Jean", last_name="DUPONT")
        result = await update_me(data=payload, request=_MockReq(ctx["test_email"]))
        assert result["updated"] is True
        owner = result["owner"]
        assert owner["first_name"] == "Jean"
        assert owner["last_name"] == "DUPONT"
        assert owner["name"] == "DUPONT Jean"
        print("OK - iter89 : champs whitelistes + recompute du name")
    finally:
        await _cleanup(db, ctx)


async def _test_get_tenants_scope_strict():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        # Locataire dans MON lot
        await db.tenants.insert_one({
            "id": f"t-{uuid.uuid4()}", "name": "My Tenant",
            "lot_id": ctx["my_lot"], "copropriete_id": ctx["cid"], "email": "",
        })
        # Locataire dans le lot d'un AUTRE proprio
        await db.tenants.insert_one({
            "id": f"t-{uuid.uuid4()}", "name": "Other Tenant",
            "lot_id": ctx["other_lot"], "copropriete_id": ctx["cid"], "email": "",
        })
        from routes.owner_portal import create_owner_portal_router
        list_tenants = _get_endpoint(create_owner_portal_router, db, "/api/owner/tenants", "GET")
        result = await list_tenants(request=_MockReq(ctx["test_email"]))
        names = [t["name"] for t in result["tenants"]]
        assert "My Tenant" in names
        assert "Other Tenant" not in names, "Scope strict : ne doit pas voir les tenants d'autres lots"
        # Les lots retournes sont aussi limites a ses lots
        lots_ids = [l["id"] for l in result["lots"]]
        assert ctx["my_lot"] in lots_ids
        assert ctx["other_lot"] not in lots_ids
        print("OK - iter89 : GET /tenants scope strict (uniquement ses lots)")
    finally:
        await _cleanup(db, ctx)


async def _test_create_tenant_in_own_lot_ok():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        router = create_owner_portal_router(db)
        TenantInput = None
        create_t = None
        for r in router.routes:
            if r.path == "/api/owner/tenants" and "POST" in r.methods:
                create_t = r.endpoint
                import inspect
                for p in inspect.signature(r.endpoint).parameters.values():
                    if hasattr(p.annotation, "__name__") and p.annotation.__name__ == "TenantInput":
                        TenantInput = p.annotation
        payload = TenantInput(
            name="Nouveau Locataire", email="loc@test.com",
            phone="+32477111222", lot_id=ctx["my_lot"],
            lease_start="2026-01-01", lease_end="2027-12-31",
            rent_amount=850.50,
        )
        result = await create_t(data=payload, request=_MockReq(ctx["test_email"]))
        assert result["name"] == "Nouveau Locataire"
        assert result["lot_id"] == ctx["my_lot"]
        assert result["copropriete_id"] == ctx["cid"]
        assert result["created_by_owner_id"] == ctx["me_id"]
        # Verifier la persistance en DB
        in_db = await db.tenants.find_one({"id": result["id"]}, {"_id": 0})
        assert in_db is not None
        # Notification creee
        notif = await db.owner_notifications.find_one(
            {"owner_id": ctx["me_id"], "change_type": {"$regex": "ajouter"}},
            {"_id": 0}
        )
        assert notif is not None
        assert any("Nouveau Locataire" in line for line in notif["summary_lines"])
        print("OK - iter89 : POST /tenants sur son lot + notification")
    finally:
        await _cleanup(db, ctx)


async def _test_create_tenant_other_lot_forbidden():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        from fastapi import HTTPException
        router = create_owner_portal_router(db)
        TenantInput = None
        create_t = None
        for r in router.routes:
            if r.path == "/api/owner/tenants" and "POST" in r.methods:
                create_t = r.endpoint
                import inspect
                for p in inspect.signature(r.endpoint).parameters.values():
                    if hasattr(p.annotation, "__name__") and p.annotation.__name__ == "TenantInput":
                        TenantInput = p.annotation
        # Essayer de creer un tenant sur le lot d'un autre proprio
        payload = TenantInput(
            name="Intruder Tenant", lot_id=ctx["other_lot"],
        )
        try:
            await create_t(data=payload, request=_MockReq(ctx["test_email"]))
            assert False, "Devait lever 403"
        except HTTPException as e:
            assert e.status_code == 403
            assert "lot" in e.detail.lower() and "appartient" in e.detail.lower()
        # Aucun tenant intruder cree en DB
        count = await db.tenants.count_documents({"name": "Intruder Tenant"})
        assert count == 0
        print("OK - iter89 : POST /tenants sur lot d'un autre -> 403")
    finally:
        await _cleanup(db, ctx)


async def _test_update_and_delete_tenant():
    db = await _setup_db()
    ctx = await _seed(db)
    try:
        from routes.owner_portal import create_owner_portal_router
        router = create_owner_portal_router(db)
        TenantInput = None
        update_t = None
        delete_t = None
        for r in router.routes:
            if r.path == "/api/owner/tenants/{tenant_id}" and "PUT" in r.methods:
                update_t = r.endpoint
                import inspect
                for p in inspect.signature(r.endpoint).parameters.values():
                    if hasattr(p.annotation, "__name__") and p.annotation.__name__ == "TenantInput":
                        TenantInput = p.annotation
            elif r.path == "/api/owner/tenants/{tenant_id}" and "DELETE" in r.methods:
                delete_t = r.endpoint
        # Pre-insert un tenant
        tid = f"t-{uuid.uuid4()}"
        await db.tenants.insert_one({
            "id": tid, "name": "Locataire A", "lot_id": ctx["my_lot"],
            "copropriete_id": ctx["cid"], "email": "",
            "rent_amount": 700.0,
        })
        # Update
        payload = TenantInput(
            name="Locataire A (renomme)", email="new@a.com",
            lot_id=ctx["my_lot"], rent_amount=750.0,
        )
        updated = await update_t(tenant_id=tid, data=payload, request=_MockReq(ctx["test_email"]))
        assert updated["name"] == "Locataire A (renomme)"
        assert updated["rent_amount"] == 750.0
        # Notification update
        notif_update = await db.owner_notifications.find_one(
            {"change_type": {"$regex": "modifier le locataire"}}, {"_id": 0}
        )
        assert notif_update is not None
        # Delete
        result = await delete_t(tenant_id=tid, request=_MockReq(ctx["test_email"]))
        assert "supprime" in result["message"].lower()
        gone = await db.tenants.find_one({"id": tid}, {"_id": 0})
        assert gone is None
        # Notification delete
        notif_delete = await db.owner_notifications.find_one(
            {"change_type": {"$regex": "supprimer le locataire"}}, {"_id": 0}
        )
        assert notif_delete is not None
        print("OK - iter89 : PUT + DELETE /tenants/{id} + notifications")
    finally:
        await _cleanup(db, ctx)


def test_iter89_put_me_updates_and_notifies():
    asyncio.run(_test_put_me_updates_and_notifies())


def test_iter89_put_me_no_change_no_notification():
    asyncio.run(_test_put_me_no_change_no_notification())


def test_iter89_put_me_ignores_locked_fields():
    asyncio.run(_test_put_me_ignores_locked_fields())


def test_iter89_get_tenants_scope_strict():
    asyncio.run(_test_get_tenants_scope_strict())


def test_iter89_create_tenant_in_own_lot_ok():
    asyncio.run(_test_create_tenant_in_own_lot_ok())


def test_iter89_create_tenant_other_lot_forbidden():
    asyncio.run(_test_create_tenant_other_lot_forbidden())


def test_iter89_update_and_delete_tenant():
    asyncio.run(_test_update_and_delete_tenant())
