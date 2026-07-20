"""iter90jb : Normalisation stricte des IBAN.

Verifie :
- `normalize_iban` produit une representation canonique unique (upper, sans separateur).
- Le POST `/api/coproprietes` dedup les IBAN qui varient uniquement par les espaces.
- Le POST `/api/banking/statements` stocke un `account_number` normalise.
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


def test_normalize_iban_variants_map_to_same_canonical():
    """iter90jb-1 : toutes les variantes d'un meme IBAN produisent la meme sortie."""
    from iban_utils import normalize_iban

    canonical = "BE04001952089331"
    assert normalize_iban("BE04 0019 5208 9331") == canonical
    assert normalize_iban("BE04-0019-5208-9331") == canonical
    assert normalize_iban("be04001952089331") == canonical  # lowercase
    assert normalize_iban(" BE04001952089331 ") == canonical  # trim
    assert normalize_iban("BE04.0019.5208.9331") == canonical  # dots
    assert normalize_iban("") == ""
    assert normalize_iban(None) == ""


def test_coproprietes_dedup_bank_accounts_after_normalization():
    """iter90jb-2 : creer une ACP avec 2 IBAN varientes du meme numero ne cree qu'UNE entree."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.coproprietes import create_coproprietes_router

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        router = create_coproprietes_router(db)

        # Extract handler
        post_route = None
        for r in router.routes:
            if r.path == "/api/coproprietes" and "POST" in getattr(r, "methods", set()):
                post_route = r
                break
        assert post_route is not None
        handler = post_route.endpoint

        # Mock request + get_current_user
        import server
        original = server.get_current_user

        async def _fake_mgr(_req):
            return {"id": "u-test", "role": "superadmin", "copropriete_ids": []}

        server.get_current_user = _fake_mgr

        # Input avec 2 variantes du meme IBAN
        from routes.coproprietes import CoproprieteInput, BankAccountInput
        data = CoproprieteInput(
            name=f"iter90jb-test-{uuid.uuid4().hex[:8]}",
            bank_accounts=[
                BankAccountInput(iban="BE04 0019 5208 9331", account_type="vue", is_default=True),
                BankAccountInput(iban="BE04001952089331", account_type="vue", is_default=False),
                # 3e : format tiret -> doit aussi dedup
                BankAccountInput(iban="BE04-0019-5208-9331", account_type="epargne", is_default=False),
            ],
        )

        class _FakeState:
            copropriete_id = None

        class _FakeRequest:
            headers = {}
            state = _FakeState()

        try:
            result = await handler(data=data, request=_FakeRequest())
            bas = result.get("bank_accounts", [])
            assert len(bas) == 1, (
                f"3 variantes du meme IBAN doivent produire UNE seule fiche. Vu {len(bas)} : {bas}"
            )
            assert bas[0]["iban"] == "BE04001952089331", (
                f"IBAN doit etre canonique. Vu {bas[0]['iban']}"
            )
        finally:
            server.get_current_user = original
            await db.coproprietes.delete_many({"name": data.name})

    _run(_go())


def test_statement_stores_normalized_iban():
    """iter90jb-3 : POST /statements avec IBAN espaces stocke un account_number sans espace."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.banking import create_banking_router
        from fastapi import HTTPException

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        router = create_banking_router(db)

        # Setup : cree une ACP avec 1 IBAN canonique
        suffix = uuid.uuid4().hex[:8]
        acp_id = f"acp-jb-{suffix}"
        canonical = "BE04001952089331"
        await db.coproprietes.insert_one({
            "id": acp_id,
            "name": f"iter90jb-{suffix}",
            "bank_accounts": [{"iban": canonical, "account_type": "vue", "is_default": True, "pcmn_number": "551331"}],
        })
        try:
            # Trouver POST /statements
            post_route = None
            for r in router.routes:
                if r.path == "/api/banking/statements" and "POST" in getattr(r, "methods", set()):
                    post_route = r
                    break
            assert post_route is not None
            handler = post_route.endpoint

            from routes.banking import StatementInput
            data = StatementInput(
                number=f"ST-{suffix}",
                date="2026-01-15",
                account_number="BE04 0019 5208 9331",  # variantes avec espaces
                opening_balance=100.0, closing_balance=100.0,
                copropriete_id=acp_id,
            )

            class _FakeState:
                copropriete_id = None

            class _FakeRequest:
                headers = {}
                state = _FakeState()

            # Patch get_current_user pour bypass auth
            import server
            original = server.get_current_user

            async def _fake_su(_req):
                return {"role": "superadmin", "copropriete_ids": [acp_id]}

            server.get_current_user = _fake_su
            try:
                result = await handler(data=data, request=_FakeRequest())
                assert result["account_number"] == canonical, (
                    f"account_number doit etre normalise. Vu {result['account_number']}"
                )
            except HTTPException as e:
                raise AssertionError(f"POST /statements a echoue : {e.detail}")
            finally:
                server.get_current_user = original
        finally:
            await db.coproprietes.delete_one({"id": acp_id})
            await db.bank_statements.delete_many({"copropriete_id": acp_id})

    _run(_go())
