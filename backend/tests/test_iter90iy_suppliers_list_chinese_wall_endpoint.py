"""iter90iy : Chinese Wall STRICT sur l'endpoint GET /api/suppliers.

Verrouille :
- `list_suppliers` REFUSE (400) une requete sans `copropriete_id`.
- Le filtrage se fait EN DB (query par `copropriete_id`), plus post-load.
- Un fournisseur d'ACP-A n'apparait JAMAIS dans une requete pour ACP-B.
- Superadmin peut bypasser en passant `copropriete_id=all`.
- Non-superadmin voit 403 si l'ACP demandee n'est pas dans son scope.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Requiert copropriete_id sur l'endpoint list_suppliers
# ---------------------------------------------------------------------------
def test_list_suppliers_requires_copropriete_id():
    """iter90iy-1 : appeler GET /suppliers sans copropriete_id -> HTTPException 400.

    On simule un `Request` FastAPI minimal + un user superadmin. L'endpoint
    doit refuser sans copropriete_id meme pour un superadmin (regle stricte).
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import create_suppliers_router
        from fastapi import HTTPException

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        router = create_suppliers_router(db)

        # Extraction de la fonction list_suppliers (handler)
        list_route = None
        for r in router.routes:
            if r.path.endswith("/api/suppliers") and "GET" in getattr(r, "methods", set()):
                list_route = r
                break
        assert list_route is not None, "GET /api/suppliers introuvable dans le router"
        handler = list_route.endpoint

        # Mock Request : headers vide, state vide.
        class _FakeState:
            copropriete_id = None

        class _FakeRequest:
            def __init__(self, user):
                self._user = user
                self.headers = {}
                self.state = _FakeState()
                self.cookies = {}
                self.query_params = {}

        # Patch get_current_user pour simuler un superadmin
        import server
        original_gcu = server.get_current_user

        async def _fake_get_user(request):
            return {"id": "su-1", "email": "su@x.io", "role": "superadmin", "copropriete_ids": []}

        server.get_current_user = _fake_get_user
        try:
            req = _FakeRequest(user={"role": "superadmin"})
            try:
                await handler(request=req, search=None, copropriete_id=None)
                raise AssertionError("Attendu HTTPException 400 mais aucun raise")
            except HTTPException as exc:
                assert exc.status_code == 400, f"code attendu 400, recu {exc.status_code}"
                assert "copropriete_id" in (exc.detail or "").lower(), exc.detail
        finally:
            server.get_current_user = original_gcu

    _run(_go())


# ---------------------------------------------------------------------------
# Le filtre marche par ACP : ne retourne QUE les suppliers de l'ACP demandee
# ---------------------------------------------------------------------------
def test_list_suppliers_filters_strictly_by_copropriete_id():
    """iter90iy-2 : deux ACPs, deux suppliers du meme nom. Un appel scoping
    sur ACP-A ne doit retourner QUE l'Engie de ACP-A (chinese wall strict)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import create_suppliers_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        router = create_suppliers_router(db)

        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-iy-a-{suffix}"
        acp_b = f"acp-iy-b-{suffix}"
        engie_a = f"engie-a-{suffix}"
        engie_b = f"engie-b-{suffix}"
        try:
            await db.suppliers.insert_many([
                {
                    "id": engie_a, "name": f"Engie-{suffix}", "copropriete_id": acp_a,
                    "bce_number": "BE0403201185", "tier_account_number": "44000001",
                },
                {
                    "id": engie_b, "name": f"Engie-{suffix}", "copropriete_id": acp_b,
                    "bce_number": "BE0403201185", "tier_account_number": "44000001",
                },
            ])

            # Simule un superadmin qui interroge ACP-A
            list_route = next(
                r for r in router.routes
                if r.path.endswith("/api/suppliers") and "GET" in getattr(r, "methods", set())
            )
            handler = list_route.endpoint

            class _FakeState:
                copropriete_id = None

            class _FakeRequest:
                headers = {}
                state = _FakeState()

            import server
            original = server.get_current_user

            async def _fake_su(_req):
                return {"role": "superadmin", "copropriete_ids": []}

            server.get_current_user = _fake_su
            try:
                res_a = await handler(request=_FakeRequest(), search=None, copropriete_id=acp_a)
                ids_a = [s["id"] for s in res_a]
                assert engie_a in ids_a, f"ACP-A doit contenir engie_a. ids_a={ids_a}"
                assert engie_b not in ids_a, (
                    f"Chinese wall FUITE : engie_b (ACP-B) est visible dans "
                    f"la requete ACP-A. ids_a={ids_a}"
                )

                res_b = await handler(request=_FakeRequest(), search=None, copropriete_id=acp_b)
                ids_b = [s["id"] for s in res_b]
                assert engie_b in ids_b
                assert engie_a not in ids_b

                # copropriete_id=all pour superadmin -> retourne les deux
                res_all = await handler(request=_FakeRequest(), search=None, copropriete_id="all")
                ids_all = {s["id"] for s in res_all}
                assert engie_a in ids_all and engie_b in ids_all, (
                    f"copropriete_id=all doit retourner tous les suppliers pour superadmin"
                )
            finally:
                server.get_current_user = original
        finally:
            await db.suppliers.delete_many({"id": {"$in": [engie_a, engie_b]}})

    _run(_go())


# ---------------------------------------------------------------------------
# Non-superadmin : 403 si copropriete_id n'est pas dans son scope
# ---------------------------------------------------------------------------
def test_list_suppliers_forbids_non_super_out_of_scope():
    """iter90iy-3 : un syndic ne peut pas lister les suppliers d'une ACP
    qui n'est pas dans ses `copropriete_ids`. Doit lever HTTPException 403."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import create_suppliers_router
        from fastapi import HTTPException

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        router = create_suppliers_router(db)

        list_route = next(
            r for r in router.routes
            if r.path.endswith("/api/suppliers") and "GET" in getattr(r, "methods", set())
        )
        handler = list_route.endpoint

        class _FakeState:
            copropriete_id = None

        class _FakeRequest:
            headers = {}
            state = _FakeState()

        import server
        original = server.get_current_user

        # Syndic scope = seulement acp-scoped-1
        async def _fake_syndic(_req):
            return {"role": "syndic", "copropriete_ids": ["acp-scoped-1"]}

        server.get_current_user = _fake_syndic
        try:
            try:
                await handler(request=_FakeRequest(), search=None, copropriete_id="acp-hors-scope-999")
                raise AssertionError("Attendu HTTPException 403 pour ACP hors scope")
            except HTTPException as exc:
                assert exc.status_code == 403, f"code attendu 403, recu {exc.status_code}"
                assert "scope" in (exc.detail or "").lower() or "copropriete" in (exc.detail or "").lower()
            # Syndic qui demande 'all' -> refuse
            try:
                await handler(request=_FakeRequest(), search=None, copropriete_id="all")
                raise AssertionError("Attendu 403 : seul superadmin peut 'all'")
            except HTTPException as exc:
                assert exc.status_code == 403
        finally:
            server.get_current_user = original

    _run(_go())
