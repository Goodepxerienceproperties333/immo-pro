"""Regression test - iter85k - Scope owners par ACP + sentinelle 'all'.

Demande user : "Corriger le scoping de la liste des proprietaires pour qu'elle
ne montre que ceux de l'ACP courante."

Backend (`routes/properties.py::list_owners`) :
- Si `copropriete_id` est fourni (query ou X-Copropriete-Id header) -> filtre
  par les lots de cette ACP (jointure existante).
- Si `copropriete_id == 'all'` (sentinelle iter85k) -> traite comme None ->
  retourne TOUS les owners (utilise par CoproprietesPage pour creer une
  nouvelle ACP et lier des proprietaires existants).

Frontend :
- /app/frontend/src/lib/api.js : `/owners` retire de GLOBAL_PATH_PREFIXES,
  donc auto-injection du copropriete_id.
- /app/frontend/src/contexts/AuthContext.js : dispatch d'un event
  'copropriete-changed' quand selectedCopro change.
- /app/frontend/src/pages/OwnersPage.js : lit selectedCopro depuis AuthContext,
  affiche etat "Selectionnez une ACP" si vide.
- /app/frontend/src/pages/CoproprietesPage.js : passe explicitement
  `copropriete_id: 'all'` aux appels /owners.

Tests :
  1. copropriete_id="all" -> tous les owners
  2. copropriete_id=cid -> owners scoped (chinese wall)
  3. X-Copropriete-Id header -> meme effet que query param
  4. Aucun param + non-super -> filtre sur allowed_copros (deja teste iter72)
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
    cid1 = f"itr85k-1-{uuid.uuid4()}"
    cid2 = f"itr85k-2-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    o2 = f"o2-{uuid.uuid4()}"
    o3 = f"o3-{uuid.uuid4()}"
    o_orphan = f"orphan-{uuid.uuid4()}"

    await db.coproprietes.insert_many([
        {"id": cid1, "name": "ACACIA", "status": "active"},
        {"id": cid2, "name": "BAOBAB", "status": "active"},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "OWNER ACP1", "copropriete_ids": [cid1]},
        {"id": o2, "name": "OWNER ACP2", "copropriete_ids": [cid2]},
        {"id": o3, "name": "OWNER ACP1+2", "copropriete_ids": [cid1, cid2]},
        {"id": o_orphan, "name": "ORPHAN", "copropriete_ids": [], "copropriete_id": ""},
    ])
    await db.lots.insert_many([
        {"id": f"lt-{uuid.uuid4()}", "owner_id": o1, "owner_ids": [o1], "copropriete_id": cid1, "number": "A1"},
        {"id": f"lt-{uuid.uuid4()}", "owner_id": o2, "owner_ids": [o2], "copropriete_id": cid2, "number": "B1"},
        {"id": f"lt-{uuid.uuid4()}", "owner_id": o3, "owner_ids": [o3], "copropriete_id": cid1, "number": "A2"},
        {"id": f"lt-{uuid.uuid4()}", "owner_id": o3, "owner_ids": [o3], "copropriete_id": cid2, "number": "B2"},
    ])
    return {"db": db, "cid1": cid1, "cid2": cid2,
            "o1": o1, "o2": o2, "o3": o3, "o_orphan": o_orphan}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_many({"id": {"$in": [ctx["cid1"], ctx["cid2"]]}})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"], ctx["o3"], ctx["o_orphan"]]}})
    await db.lots.delete_many({"copropriete_id": {"$in": [ctx["cid1"], ctx["cid2"]]}})


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


def _get_list_owners_fn(db):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == "/api/owners" and "GET" in (r.methods or set()):
            return r.endpoint
    return None


class _MockReq:
    def __init__(self, header_cid=None):
        self.headers = {"X-Copropriete-Id": header_cid} if header_cid else {}
        self.cookies = {}
        self.state = type("S", (), {"copropriete_id": ""})()


async def _test_copro_id_all_returns_all_owners():
    """iter85k : copropriete_id='all' -> tous les owners."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_list_owners_fn(ctx["db"])
        result = await fn(request=_MockReq(), copropriete_id="all", include_unassigned=False)
        ids = {o["id"] for o in result}
        # Doit contenir tous les owners (superadmin + 'all' = vue plateforme,
        # y compris orphans car 'all' est traite comme aucun scope)
        assert ctx["o1"] in ids
        assert ctx["o2"] in ids
        assert ctx["o3"] in ids
        # Orphan present aussi car superadmin sans scope = tout
        assert ctx["o_orphan"] in ids
        print(f"OK - 'all' retourne {len(ids)} owners (cross-ACP + orphans)")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_copro_id_scopes_to_acp():
    """copropriete_id=cid1 -> seuls les owners ayant un lot dans cid1."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_list_owners_fn(ctx["db"])
        result = await fn(request=_MockReq(), copropriete_id=ctx["cid1"], include_unassigned=False)
        ids = {o["id"] for o in result}
        assert ctx["o1"] in ids, "o1 (lot dans cid1) doit etre present"
        assert ctx["o3"] in ids, "o3 (lots dans cid1 et cid2) doit etre present"
        assert ctx["o2"] not in ids, "o2 (lot UNIQUEMENT dans cid2) NE doit PAS apparaitre"
        print("OK - scope ACP via query param")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_x_copro_header_scopes_when_query_empty():
    """X-Copropriete-Id header utilise si query param vide."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_list_owners_fn(ctx["db"])
        result = await fn(
            request=_MockReq(header_cid=ctx["cid1"]),
            copropriete_id=None,
            include_unassigned=False,
        )
        ids = {o["id"] for o in result}
        assert ctx["o1"] in ids
        assert ctx["o2"] not in ids
        print("OK - X-Copropriete-Id header utilise comme scope")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


async def _test_include_unassigned_returns_orphans():
    """include_unassigned=true -> ajoute les orphan owners (utile pour
    CoproprietesPage : lier des owners juste importes mais sans lot)."""
    ctx = await _setup()
    orig = _setup_auth_mock()
    try:
        fn = _get_list_owners_fn(ctx["db"])
        result = await fn(request=_MockReq(), copropriete_id="all", include_unassigned=True)
        ids = {o["id"] for o in result}
        assert ctx["o_orphan"] in ids, "Orphan doit apparaitre quand include_unassigned=true"
        print("OK - include_unassigned=true ajoute les orphans")
    finally:
        _restore_auth_mock(orig)
        await _cleanup(ctx)


def test_iter85k_copro_id_all_returns_all():
    asyncio.run(_test_copro_id_all_returns_all_owners())


def test_iter85k_copro_id_scopes_to_acp():
    asyncio.run(_test_copro_id_scopes_to_acp())


def test_iter85k_x_copro_header_scopes_when_query_empty():
    asyncio.run(_test_x_copro_header_scopes_when_query_empty())


def test_iter85k_include_unassigned_returns_orphans():
    asyncio.run(_test_include_unassigned_returns_orphans())
