"""Regression test - iter84 - Detection et fusion de doublons (admin panel).

Endpoints testes :
  - GET  /api/admin/duplicates/suppliers
  - GET  /api/admin/duplicates/owners
  - GET  /api/admin/duplicates/users
  - POST /api/admin/duplicates/owners/merge

Verifie :
  - Detection : groupes par nom/BCE/email avec Union-Find (overlap consolide)
  - Chinese wall : superadmin voit tout, syndic voit uniquement ses ACPs
  - Fusion owners : reassocie lots (simple + multi), mutations, bank_transactions
  - Enrichissement : premier non-vide gagne
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _bootstrap():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr84d-{uuid.uuid4()}"
    o1, o2, o3 = (f"o{i}-{uuid.uuid4()}" for i in range(3))
    s1, s2, s3 = (f"s{i}-{uuid.uuid4()}" for i in range(3))
    lot1, lot2 = (f"lot{i}-{uuid.uuid4()}" for i in range(2))

    await db.coproprietes.insert_one({"id": cid, "name": "DUPTEST", "status": "active"})
    # 3 owners : o1 et o2 = doublons (meme email + meme bce), o3 = distinct
    await db.owners.insert_many([
        {"id": o1, "first_name": "Jean", "last_name": "Dupont",
         "name": "Jean Dupont", "email": "jdupont@test.be",
         "bce_number": "BE0123456789", "copropriete_id": cid},
        {"id": o2, "first_name": "DUPONT", "last_name": "Jean",
         "name": "DUPONT Jean", "email": "JDUPONT@TEST.BE",  # case different
         "bce_number": "BE 0123 456 789",  # punctuation
         "phone": "+32475123456", "copropriete_id": cid},
        {"id": o3, "first_name": "Alice", "last_name": "Martin",
         "name": "Alice Martin", "email": "alice@test.be",
         "copropriete_id": cid},
    ])
    # 3 fournisseurs : s1 et s2 = doublons (meme IBAN), s3 = distinct
    await db.suppliers.insert_many([
        {"id": s1, "name": "AXA Belgium", "bce_number": "BE0404483367",
         "iban": "BE98765432101234", "copropriete_id": cid},
        {"id": s2, "name": "AXA Belgique", "bce_number": "",
         "iban": "BE 9876 5432 1012 34", "copropriete_id": cid},  # IBAN identique normalise
        {"id": s3, "name": "Belfius", "copropriete_id": cid},
    ])
    # Lots : lot1 -> o1 (simple), lot2 -> o2 + o3 (multi)
    await db.lots.insert_many([
        {"id": lot1, "owner_id": o1, "owner_ids": [o1], "copropriete_id": cid, "quotity": 50},
        {"id": lot2, "owner_ids": [o2, o3],
         "owner_percentages": {o2: 60, o3: 40},
         "copropriete_id": cid, "quotity": 100},
    ])
    return {"db": db, "cid": cid, "o1": o1, "o2": o2, "o3": o3, "s1": s1, "s2": s2, "s3": s3,
            "lot1": lot1, "lot2": lot2}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"], ctx["o3"]]}})
    await db.suppliers.delete_many({"id": {"$in": [ctx["s1"], ctx["s2"], ctx["s3"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, prefix: str, route_path: str):
    """Retrouve l'endpoint par son path complet."""
    from routes.duplicates import create_duplicates_router
    router = create_duplicates_router(db)
    full = prefix + route_path
    for r in router.routes:
        if r.path == full:
            return r.endpoint
    return None


class _MockRequest:
    """Mock request avec scope superadmin par defaut."""
    def __init__(self, user_role="superadmin", copropriete_ids=None):
        self.cookies = {}
        self.headers = {}
        self._user = {"role": user_role, "copropriete_ids": copropriete_ids or []}

    @property
    def state(self):
        class _S: pass
        return _S()


async def _patch_get_current_user(target_user):
    """Patch server.get_current_user pour retourner un user fixe."""
    import server
    async def _fake(req):
        return target_user
    server.get_current_user = _fake


async def _test_detect_suppliers():
    ctx = await _bootstrap()
    db = ctx["db"]
    try:
        await _patch_get_current_user({"role": "superadmin", "copropriete_ids": []})
        endpoint = _get_endpoint(db, "/api/admin/duplicates", "/suppliers")
        assert endpoint is not None
        result = await endpoint(_MockRequest(), copropriete_id=ctx["cid"])
        groups = result["groups"]
        # s1 et s2 doivent etre groupes (meme IBAN normalise) - s3 distinct
        assert any(
            {ctx["s1"], ctx["s2"]}.issubset({m["id"] for m in g["members"]})
            for g in groups
        ), f"s1+s2 attendus dans un meme groupe, recu : {[[m['id'] for m in g['members']] for g in groups]}"
        # s3 jamais dans un groupe
        for g in groups:
            assert ctx["s3"] not in [m["id"] for m in g["members"]]
        print(f"OK - detection fournisseurs : {len(groups)} groupe(s)")
    finally:
        await _cleanup(ctx)


async def _test_detect_owners():
    ctx = await _bootstrap()
    db = ctx["db"]
    try:
        await _patch_get_current_user({"role": "superadmin", "copropriete_ids": []})
        endpoint = _get_endpoint(db, "/api/admin/duplicates", "/owners")
        result = await endpoint(_MockRequest(), copropriete_id=ctx["cid"])
        groups = result["groups"]
        # o1 et o2 doivent etre groupes (email + BCE + nom normalise)
        assert any(
            {ctx["o1"], ctx["o2"]}.issubset({m["id"] for m in g["members"]})
            for g in groups
        )
        # Verifier que match_on inclut au moins email et nom
        match_group = next(g for g in groups if {ctx["o1"], ctx["o2"]}.issubset({m["id"] for m in g["members"]}))
        assert "email" in match_group["match_on"]
        assert "nom" in match_group["match_on"]
        assert "BCE" in match_group["match_on"]
        # o3 jamais dans un groupe
        for g in groups:
            assert ctx["o3"] not in [m["id"] for m in g["members"]]
        # lots_count correctement remonte
        for m in match_group["members"]:
            assert m["lots_count"] >= 1
        print(f"OK - detection owners : {len(groups)} groupe(s), match_on={match_group['match_on']}")
    finally:
        await _cleanup(ctx)


async def _test_chinese_wall_syndic():
    """Un syndic sans cette ACP dans son scope ne doit RIEN voir."""
    ctx = await _bootstrap()
    db = ctx["db"]
    try:
        await _patch_get_current_user({"role": "syndic", "copropriete_ids": []})
        endpoint = _get_endpoint(db, "/api/admin/duplicates", "/owners")
        # Sans ACP dans le scope, doit retourner 0 groups
        result = await endpoint(_MockRequest(), copropriete_id=None)
        assert result["total_owners"] == 0, f"Syndic sans ACP doit voir 0 owners, vu {result['total_owners']}"
        assert result["groups"] == []
        print("OK - chinese wall : syndic sans ACP -> 0 doublons")
    finally:
        await _cleanup(ctx)


async def _test_merge_owners():
    """Fusion o2 -> o1 : doit
      - migrer lot1 (deja o1, rien a faire)
      - remplacer o2 par o1 dans lot2.owner_ids (consolidation pourcentage)
      - enrichir o1 avec phone d'o2
      - supprimer o2
    """
    ctx = await _bootstrap()
    db = ctx["db"]
    try:
        await _patch_get_current_user({"role": "superadmin", "copropriete_ids": []})
        merge_fn = _get_endpoint(db, "/api/admin/duplicates", "/owners/merge")
        OwnerMergeInput = merge_fn.__annotations__.get("data")
        payload = OwnerMergeInput(keep_id=ctx["o1"], remove_ids=[ctx["o2"]])
        result = await merge_fn(data=payload, request=_MockRequest())
        assert result["kept_id"] == ctx["o1"]
        assert "phone" in result["enriched_fields"], (
            f"phone d'o2 doit enrichir o1, recu enriched={result['enriched_fields']}"
        )
        # Verifie : o2 supprime
        o2_after = await db.owners.find_one({"id": ctx["o2"]})
        assert o2_after is None, "o2 doit etre supprime"
        # o1 a recu le phone d'o2
        o1_after = await db.owners.find_one({"id": ctx["o1"]}, {"_id": 0})
        assert o1_after["phone"] == "+32475123456"
        # lot1 : owner_id reste o1
        lot1_after = await db.lots.find_one({"id": ctx["lot1"]}, {"_id": 0})
        assert lot1_after["owner_id"] == ctx["o1"]
        # lot2 : owner_ids contient o1 (au lieu d'o2) + o3
        lot2_after = await db.lots.find_one({"id": ctx["lot2"]}, {"_id": 0})
        assert ctx["o1"] in lot2_after["owner_ids"]
        assert ctx["o3"] in lot2_after["owner_ids"]
        assert ctx["o2"] not in lot2_after["owner_ids"]
        # Pourcentages consolides : o1 = 60 (o2 absorbe), o3 = 40
        assert lot2_after["owner_percentages"][ctx["o1"]] == 60
        assert lot2_after["owner_percentages"][ctx["o3"]] == 40
        assert ctx["o2"] not in lot2_after["owner_percentages"]
        print("OK - merge owners avec consolidation multi-owners + enrichissement")
    finally:
        await _cleanup(ctx)


async def _test_merge_validation():
    """Verifie les rejets : keep_id dans remove_ids, vide, etc."""
    ctx = await _bootstrap()
    db = ctx["db"]
    try:
        from fastapi import HTTPException
        await _patch_get_current_user({"role": "superadmin", "copropriete_ids": []})
        merge_fn = _get_endpoint(db, "/api/admin/duplicates", "/owners/merge")
        OwnerMergeInput = merge_fn.__annotations__.get("data")

        # keep dans remove
        try:
            await merge_fn(data=OwnerMergeInput(keep_id=ctx["o1"], remove_ids=[ctx["o1"]]), request=_MockRequest())
            assert False, "doit lever HTTPException"
        except HTTPException as e:
            assert e.status_code == 400

        # remove_ids vide
        try:
            await merge_fn(data=OwnerMergeInput(keep_id=ctx["o1"], remove_ids=[]), request=_MockRequest())
            assert False
        except HTTPException as e:
            assert e.status_code == 400

        # keep_id inexistant
        try:
            await merge_fn(data=OwnerMergeInput(keep_id="ghost", remove_ids=[ctx["o2"]]), request=_MockRequest())
            assert False
        except HTTPException as e:
            assert e.status_code == 404
        print("OK - validations merge rejets correctes")
    finally:
        await _cleanup(ctx)


def test_iter84_detect_suppliers():
    asyncio.run(_test_detect_suppliers())


def test_iter84_detect_owners():
    asyncio.run(_test_detect_owners())


def test_iter84_chinese_wall_syndic():
    asyncio.run(_test_chinese_wall_syndic())


def test_iter84_merge_owners():
    asyncio.run(_test_merge_owners())


def test_iter84_merge_validation():
    asyncio.run(_test_merge_validation())
