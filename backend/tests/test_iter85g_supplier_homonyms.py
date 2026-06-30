"""Regression test - iter85g - Detection homonymes fournisseurs.

Demande user : "il faut eviter les doublons de fournisseur verifier les noms
ou les possibles homonyme et demander confirmation avant la creation."

Backend :
- Nouveau endpoint POST /api/suppliers/check-duplicate
- Detection EXACTE (BCE/TVA/IBAN/nom strict) -> exact != null
- Detection FUZZY (Levenshtein >= 0.80) -> similar list (max 5, tries par score)
- POST /api/suppliers bloque maintenant aussi sur similaires sauf si
  force_create_despite_similar=true (l'utilisateur a confirme via dialog)

Tests :
  1. check-duplicate retourne exact si meme nom
  2. check-duplicate retourne similar avec score si nom proche (coquilles)
  3. check-duplicate ne match PAS si noms tres differents
  4. POST /suppliers sans force -> 409 si similaire
  5. POST /suppliers avec force_create_despite_similar=true -> cree quand meme
  6. find_similar_suppliers exclut les noms strictement identiques (deduplication)
  7. Scope ACP : similaires d'une autre ACP ne sont pas remontes
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
    cid = f"itr85g-{uuid.uuid4()}"
    cid_other = f"other-{uuid.uuid4()}"
    await db.coproprietes.insert_many([
        {"id": cid, "name": "ACP1", "status": "active"},
        {"id": cid_other, "name": "ACP2", "status": "active"},
    ])
    # Seed fournisseurs existants
    sup_a_id = str(uuid.uuid4())
    sup_b_id = str(uuid.uuid4())
    sup_c_id = str(uuid.uuid4())
    sup_other_id = str(uuid.uuid4())
    await db.suppliers.insert_many([
        # Sup existant dans cid
        {"id": sup_a_id, "name": "ELEC PLUS SRL", "vat_number": "BE0123456789",
         "iban": "BE12345678901234", "copropriete_id": cid},
        {"id": sup_b_id, "name": "PLOMBERIE Dupont", "vat_number": "BE0987654321",
         "copropriete_id": cid},
        {"id": sup_c_id, "name": "CHAUFFAGE NOLET", "copropriete_id": cid},
        # Sup d'une autre ACP (ne doit PAS apparaitre pour cid)
        {"id": sup_other_id, "name": "ELEC PLUS SRL", "copropriete_id": cid_other},
    ])
    return {"db": db, "cid": cid, "cid_other": cid_other,
            "sup_a": sup_a_id, "sup_b": sup_b_id, "sup_c": sup_c_id,
            "sup_other": sup_other_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_many({"id": {"$in": [ctx["cid"], ctx["cid_other"]]}})
    await db.suppliers.delete_many({"id": {"$in": [ctx["sup_a"], ctx["sup_b"], ctx["sup_c"], ctx["sup_other"]]}})
    # Nettoyer tout fournisseur cree dans ce test
    await db.suppliers.delete_many({"copropriete_id": {"$in": [ctx["cid"], ctx["cid_other"]]}})


def _get_endpoint(db, path: str, method: str):
    from routes.suppliers import create_suppliers_router
    router = create_suppliers_router(db)
    for r in router.routes:
        if r.path == path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


def _mock_superadmin():
    """Bypass auth en mockant get_current_user."""
    import server as srv
    srv.get_current_user = lambda req: _async_user()


async def _async_user():
    return {"_id": "mock", "role": "superadmin", "copropriete_ids": []}


def _setup_auth_mock():
    import server as srv
    orig = srv.get_current_user

    async def _fake(req):
        return {"_id": "mock", "role": "superadmin", "copropriete_ids": []}
    srv.get_current_user = _fake
    return orig


def _restore_auth_mock(orig):
    import server as srv
    srv.get_current_user = orig


class _MockReq:
    def __init__(self, cid):
        self.headers = {}
        self.cookies = {}
        self.state = type("S", (), {"copropriete_id": cid})()


async def _test_check_duplicate_finds_exact():
    """check-duplicate retourne exact si nom strict match."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_endpoint(ctx["db"], "/api/suppliers/check-duplicate", "POST")
        import routes.suppliers as sup_mod
        payload = sup_mod.SupplierCheckDuplicateInput(
            name="ELEC PLUS SRL", copropriete_id=ctx["cid"],
        )
        result = await fn(request=_MockReq(ctx["cid"]), data=payload)
        assert result["exact"] is not None
        assert result["exact"]["field"] == "name"
        assert result["exact"]["supplier"]["id"] == ctx["sup_a"]
        print("OK - check-duplicate exact match")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_check_duplicate_finds_similar_typo():
    """check-duplicate retourne similar avec score si coquille."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_endpoint(ctx["db"], "/api/suppliers/check-duplicate", "POST")
        import routes.suppliers as sup_mod
        # "ELEC PLUSE SRL" - faute de frappe (insert 'E')
        payload = sup_mod.SupplierCheckDuplicateInput(
            name="ELEC PLUSE SRL", copropriete_id=ctx["cid"],
        )
        result = await fn(request=_MockReq(ctx["cid"]), data=payload)
        assert result["exact"] is None, "Pas de match exact"
        assert len(result["similar"]) >= 1, (
            f"Devrait detecter une similarite, recu {result['similar']}"
        )
        names = [s["supplier"]["name"] for s in result["similar"]]
        assert "ELEC PLUS SRL" in names
        # Score > 0.80
        elec_match = next(s for s in result["similar"] if s["supplier"]["name"] == "ELEC PLUS SRL")
        assert elec_match["score"] >= 0.80
        print(f"OK - check-duplicate detecte similar (score={elec_match['score']:.3f})")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_check_duplicate_no_match_for_different_name():
    """Noms tres differents -> aucune similarite."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_endpoint(ctx["db"], "/api/suppliers/check-duplicate", "POST")
        import routes.suppliers as sup_mod
        payload = sup_mod.SupplierCheckDuplicateInput(
            name="TOITURE GENIALE", copropriete_id=ctx["cid"],
        )
        result = await fn(request=_MockReq(ctx["cid"]), data=payload)
        assert result["exact"] is None
        assert len(result["similar"]) == 0, (
            f"Aucune similarite attendue pour un nom tres different, recu {result['similar']}"
        )
        print("OK - check-duplicate ignore noms tres differents")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_create_supplier_blocks_on_similar_without_force():
    """POST /suppliers sans force renvoie 409 si similaire detecte."""
    from fastapi import HTTPException
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        create_fn = _get_endpoint(ctx["db"], "/api/suppliers", "POST")
        import routes.suppliers as sup_mod
        payload = sup_mod.SupplierInput(
            name="ELEC PLUSE SRL",  # similaire a ELEC PLUS SRL
            copropriete_id=ctx["cid"],
        )
        try:
            await create_fn(request=_MockReq(ctx["cid"]), data=payload)
            assert False, "Devrait lever HTTPException 409"
        except HTTPException as exc:
            assert exc.status_code == 409
            assert "homonyme" in exc.detail.lower()
        print("OK - create_supplier bloque sur similaire sans force")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_create_supplier_force_creates_despite_similar():
    """POST /suppliers avec force_create_despite_similar=true cree quand meme."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        create_fn = _get_endpoint(ctx["db"], "/api/suppliers", "POST")
        import routes.suppliers as sup_mod
        payload = sup_mod.SupplierInput(
            name="ELEC PLUSE SRL",
            copropriete_id=ctx["cid"],
            force_create_despite_similar=True,
        )
        result = await create_fn(request=_MockReq(ctx["cid"]), data=payload)
        assert result["name"] == "ELEC PLUSE SRL"
        assert "id" in result
        # Verifier qu'on a 2 fournisseurs avec noms similaires dans cette ACP
        count = await ctx["db"].suppliers.count_documents({
            "copropriete_id": ctx["cid"], "name": {"$regex": "^ELEC PLUS"}
        })
        assert count == 2
        # Le doc stocke ne doit PAS contenir le flag force_create_despite_similar
        stored = await ctx["db"].suppliers.find_one({"id": result["id"]}, {"_id": 0})
        assert "force_create_despite_similar" not in stored
        print("OK - create_supplier accepte force_create_despite_similar")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_check_duplicate_excludes_other_acps():
    """Le sup avec le meme nom dans une autre ACP ne doit PAS apparaitre."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_endpoint(ctx["db"], "/api/suppliers/check-duplicate", "POST")
        import routes.suppliers as sup_mod
        # Le nom "ELEC PLUS SRL" existe EN DOUBLE : 1 dans cid, 1 dans cid_other
        # Pour cid, on doit trouver le sup_a (cid) et PAS le sup_other (cid_other)
        payload = sup_mod.SupplierCheckDuplicateInput(
            name="ELEC PLUSE SRL", copropriete_id=ctx["cid"],
        )
        result = await fn(request=_MockReq(ctx["cid"]), data=payload)
        sup_ids = [s["supplier"]["id"] for s in result["similar"]]
        assert ctx["sup_a"] in sup_ids
        assert ctx["sup_other"] not in sup_ids, "Le sup d'une autre ACP ne doit pas remonter"
        print("OK - check-duplicate respecte le scope ACP")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_find_similar_excludes_exact_match():
    """find_similar_suppliers retourne [] si seul un match strict existe."""
    ctx = await _setup()
    try:
        from routes.suppliers import find_similar_suppliers
        result = await find_similar_suppliers(
            ctx["db"], name="ELEC PLUS SRL", copro_id=ctx["cid"],
        )
        # ELEC PLUS SRL strict -> dans exact, pas dans similar
        names_returned = [s["supplier"]["name"] for s in result]
        assert "ELEC PLUS SRL" not in names_returned, (
            "Le match EXACT ne doit pas etre dans similar (gere par find_duplicate_supplier)"
        )
        print("OK - find_similar exclut les matches stricts")
    finally:
        await _cleanup(ctx)


def test_iter85g_check_duplicate_finds_exact():
    asyncio.run(_test_check_duplicate_finds_exact())


def test_iter85g_check_duplicate_finds_similar_typo():
    asyncio.run(_test_check_duplicate_finds_similar_typo())


def test_iter85g_check_duplicate_no_match_for_different_name():
    asyncio.run(_test_check_duplicate_no_match_for_different_name())


def test_iter85g_create_supplier_blocks_on_similar_without_force():
    asyncio.run(_test_create_supplier_blocks_on_similar_without_force())


def test_iter85g_create_supplier_force_creates_despite_similar():
    asyncio.run(_test_create_supplier_force_creates_despite_similar())


def test_iter85g_check_duplicate_excludes_other_acps():
    asyncio.run(_test_check_duplicate_excludes_other_acps())


def test_iter85g_find_similar_excludes_exact_match():
    asyncio.run(_test_find_similar_excludes_exact_match())
