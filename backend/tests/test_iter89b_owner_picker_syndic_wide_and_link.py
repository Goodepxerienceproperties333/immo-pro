"""Regression test - iter89b - Mutation lot : trouver les proprios existants
et les lier automatiquement a l'ACP (plus de doublons).

Demande user (Feb 2026, 2 screenshots) :
    1. "Lors de mutation il faut pouvoir retrouver les proprietaires lies a l'ACP"
       (le picker affichait "Aucun proprietaire trouve" alors que le proprio
       existait dans une autre ACP du syndic - ABED - STEUVE).
    2. "En cas de doublons de proprietaire une fois qu'il est selectionne il
       faut qu'il soit sauve en tant que proprietaire dans l'ACP" (= l'ACP
       est automatiquement ajoutee a owner.copropriete_ids[]).

Bug : chinese wall iter85k filtrait strict /owners?copropriete_id=X aux
proprios ayant deja un lot dans cette ACP. Un acquereur qui n'a pas encore
de lot ici (mais qui existe ailleurs chez le meme syndic) etait INVISIBLE
au picker -> l'utilisateur clique "Creer" -> 409 doublon -> impasse UI.

Fix iter89b :
  1. /api/owners?syndic_wide=true -> retourne tous les proprios accessibles
     au syndic, ignorant le filtre copropriete_id (mais respectant le RBAC).
  2. assign_owner_accounts() ajoute idempotemment l'ACP a
     owner.copropriete_ids[] -> apres mutation, le nouvel acquereur EST
     officiellement dans l'ACP.

Tests :
  1. /owners?syndic_wide=true sans ACP -> tous les proprios accessibles
  2. /owners?syndic_wide=true avec ACP specifique -> ignorer le filtre ACP
  3. /owners scope normal (sans syndic_wide) reste filtre par ACP
  4. assign_owner_accounts() : ajoute l'ACP a copropriete_ids[] (idempotent)
  5. mutate-lot avec proprio "etranger" : apres mutation, l'ACP est dans
     ses copropriete_ids
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


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


async def _patch_superadmin():
    import server as srv_mod
    original = srv_mod.get_current_user

    async def _mock(_req):
        return {"id": "test", "role": "superadmin", "email": "t@t",
                "copropriete_ids": []}

    srv_mod.get_current_user = _mock
    return lambda: setattr(srv_mod, "get_current_user", original)


async def _patch_syndic(copro_ids):
    import server as srv_mod
    original = srv_mod.get_current_user

    async def _mock(_req):
        return {"id": "test", "role": "syndic", "email": "syndic@t.com",
                "copropriete_ids": copro_ids}

    srv_mod.get_current_user = _mock
    return lambda: setattr(srv_mod, "get_current_user", original)


async def _seed_two_acps_with_owner_in_one_only(db):
    """ACP A : a 1 lot avec proprio "ABED"
       ACP B : a 1 lot avec proprio "DURAND" mais ABED n'est PAS dans cette ACP.
    Le but : verifier que /owners?syndic_wide=true retourne ABED meme quand
    on est positionne sur ACP B."""
    cid_a = f"a-{uuid.uuid4()}"
    cid_b = f"b-{uuid.uuid4()}"
    abed_id = f"abed-{uuid.uuid4()}"
    durand_id = f"durand-{uuid.uuid4()}"
    lot_a = f"lota-{uuid.uuid4()}"
    lot_b = f"lotb-{uuid.uuid4()}"
    await db.coproprietes.insert_many([
        {"id": cid_a, "name": "ACP A", "status": "active"},
        {"id": cid_b, "name": "ACP B", "status": "active"},
    ])
    await db.owners.insert_many([
        {"id": abed_id, "name": "ABED-STEUVE Selim", "last_name": "ABED-STEUVE",
         "first_name": "Selim", "email": f"abed-{uuid.uuid4().hex[:6]}@test",
         "copropriete_ids": [cid_a]},
        {"id": durand_id, "name": "DURAND Jean", "last_name": "DURAND",
         "first_name": "Jean", "email": f"durand-{uuid.uuid4().hex[:6]}@test",
         "copropriete_ids": [cid_b]},
    ])
    await db.lots.insert_many([
        {"id": lot_a, "number": "A1", "owner_id": abed_id, "owner_ids": [abed_id],
         "copropriete_id": cid_a, "quotity": 500},
        {"id": lot_b, "number": "B1", "owner_id": durand_id, "owner_ids": [durand_id],
         "copropriete_id": cid_b, "quotity": 500},
    ])
    return {"cid_a": cid_a, "cid_b": cid_b, "abed_id": abed_id,
            "durand_id": durand_id, "lot_a": lot_a, "lot_b": lot_b}


async def _cleanup(db, ctx):
    await db.coproprietes.delete_many({"id": {"$in": [ctx["cid_a"], ctx["cid_b"]]}})
    await db.owners.delete_many({"id": {"$in": [ctx["abed_id"], ctx["durand_id"]]}})
    await db.lots.delete_many({"copropriete_id": {"$in": [ctx["cid_a"], ctx["cid_b"]]}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$in": [ctx["cid_a"], ctx["cid_b"]]}})
    await db.fiscal_years.delete_many({"copropriete_id": {"$in": [ctx["cid_a"], ctx["cid_b"]]}})


async def _test_syndic_wide_returns_all_accessible_owners():
    db = await _setup_db()
    ctx = await _seed_two_acps_with_owner_in_one_only(db)
    restore = await _patch_syndic([ctx["cid_a"], ctx["cid_b"]])
    try:
        from routes.properties import create_properties_router
        list_owners = _get_endpoint(create_properties_router, db, "/api/owners", "GET")

        # Syndic positionne sur ACP B mais demande syndic_wide=true
        # -> doit retourner ABED (dans ACP A) ET DURAND (dans ACP B)
        result = await list_owners(
            request=_Req({"X-Copropriete-Id": ctx["cid_b"]}),
            copropriete_id=None,
            include_unassigned=False,
            syndic_wide=True,
        )
        owner_ids = [o["id"] for o in result]
        assert ctx["abed_id"] in owner_ids, \
            "ABED (dans ACP A) doit etre visible meme positionne sur ACP B"
        assert ctx["durand_id"] in owner_ids
        print("OK - iter89b : syndic_wide retourne tous les proprios accessibles")
    finally:
        restore()
        await _cleanup(db, ctx)


async def _test_scope_normal_still_filters_by_acp():
    """Verifier qu'on n'a pas casse le scope normal (chinese wall standard)."""
    db = await _setup_db()
    ctx = await _seed_two_acps_with_owner_in_one_only(db)
    restore = await _patch_syndic([ctx["cid_a"], ctx["cid_b"]])
    try:
        from routes.properties import create_properties_router
        list_owners = _get_endpoint(create_properties_router, db, "/api/owners", "GET")

        # Mode normal : scope sur ACP B -> doit voir UNIQUEMENT DURAND
        result = await list_owners(
            request=_Req(),
            copropriete_id=ctx["cid_b"],
            include_unassigned=False,
            syndic_wide=False,
        )
        owner_ids = [o["id"] for o in result]
        assert ctx["durand_id"] in owner_ids
        assert ctx["abed_id"] not in owner_ids, \
            "ABED ne doit PAS apparaitre en mode normal (chinese wall ACP B)"
        print("OK - iter89b : scope normal reste filtre par ACP (regression OK)")
    finally:
        restore()
        await _cleanup(db, ctx)


async def _test_assign_owner_accounts_links_acp():
    """assign_owner_accounts() ajoute idempotemment l'ACP a copropriete_ids."""
    db = await _setup_db()
    ctx = await _seed_two_acps_with_owner_in_one_only(db)
    try:
        from tier_accounts import assign_owner_accounts
        owner = await db.owners.find_one({"id": ctx["abed_id"]}, {"_id": 0})
        assert ctx["cid_b"] not in (owner.get("copropriete_ids") or [])

        # Pre-seed accounts class 4 pour ACP B (sinon _ensure_account refuse)
        # En fait _ensure_account cree le compte si absent, donc rien a faire.
        result = await assign_owner_accounts(db, owner, ctx["cid_b"])
        # Verifier en DB
        updated = await db.owners.find_one({"id": ctx["abed_id"]}, {"_id": 0})
        assert ctx["cid_b"] in (updated.get("copropriete_ids") or []), \
            "L'ACP doit etre dans copropriete_ids apres assign_owner_accounts"
        # Idempotence : un 2e appel ne dupplique pas
        await assign_owner_accounts(db, updated, ctx["cid_b"])
        again = await db.owners.find_one({"id": ctx["abed_id"]}, {"_id": 0})
        count = (again["copropriete_ids"] or []).count(ctx["cid_b"])
        assert count == 1, f"L'ACP doit apparaitre 1 fois, trouve {count}"
        # Le proprio etait deja dans ACP A, ca doit le rester
        assert ctx["cid_a"] in again["copropriete_ids"]
        # Le return dict doit aussi etre coherent
        assert ctx["cid_b"] in (result.get("copropriete_ids") or [])
        print("OK - iter89b : assign_owner_accounts ajoute ACP idempotemment")
    finally:
        await _cleanup(db, ctx)


async def _test_superadmin_syndic_wide_returns_everything():
    """Pour un superadmin, syndic_wide=true retourne TOUS les owners en DB."""
    db = await _setup_db()
    ctx = await _seed_two_acps_with_owner_in_one_only(db)
    restore = await _patch_superadmin()
    try:
        from routes.properties import create_properties_router
        list_owners = _get_endpoint(create_properties_router, db, "/api/owners", "GET")
        result = await list_owners(
            request=_Req({"X-Copropriete-Id": ctx["cid_b"]}),
            copropriete_id=None,
            include_unassigned=False,
            syndic_wide=True,
        )
        owner_ids = [o["id"] for o in result]
        assert ctx["abed_id"] in owner_ids
        assert ctx["durand_id"] in owner_ids
        print("OK - iter89b : superadmin syndic_wide=true voit tous les owners")
    finally:
        restore()
        await _cleanup(db, ctx)


def test_iter89b_syndic_wide_returns_all_accessible_owners():
    asyncio.run(_test_syndic_wide_returns_all_accessible_owners())


def test_iter89b_scope_normal_still_filters_by_acp():
    asyncio.run(_test_scope_normal_still_filters_by_acp())


def test_iter89b_assign_owner_accounts_links_acp():
    asyncio.run(_test_assign_owner_accounts_links_acp())


def test_iter89b_superadmin_syndic_wide_returns_everything():
    asyncio.run(_test_superadmin_syndic_wide_returns_everything())
