"""iter90is : Chinese Wall strict pour les fournisseurs.

Verrouille les regles suivantes :
- Chaque `Supplier` a un `copropriete_id` OBLIGATOIRE.
- `tier_accounts` (dict cross-ACP) est remplace par `tier_account_number`
  (champ simple, un seul par supplier).
- L'import Optipro cree systematiquement une fiche LOCALE a l'ACP en cours,
  meme si un fournisseur du meme nom existe dans une autre ACP.
- `find_duplicate_supplier` cherche STRICTEMENT dans `copropriete_id`
  (aucun fallback global BCE, aucun $or sur tier_accounts).
- `get_supplier_account` refuse l'acces cross-ACP.
- Index MongoDB : `uq_supplier_copro_bce` et `uq_supplier_copro_vat`
  (unicite per-ACP, plus globale).
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
# SupplierInput : copropriete_id obligatoire
# ---------------------------------------------------------------------------
def test_supplier_input_requires_copropriete_id():
    """iter90is-1 : instancier `SupplierInput` sans `copropriete_id` doit
    lever une ValidationError (Pydantic requis, plus de default vide)."""
    from routes.suppliers import SupplierInput
    from pydantic import ValidationError
    # sans copropriete_id -> erreur
    try:
        SupplierInput(name="Test", bce_number="BE0123456789")
    except ValidationError:
        return  # OK
    except Exception as e:
        raise AssertionError(f"Attendu ValidationError, recu {type(e).__name__}")
    raise AssertionError("SupplierInput doit exiger copropriete_id")


def test_supplier_input_accepts_valid_copropriete_id():
    from routes.suppliers import SupplierInput
    s = SupplierInput(name="Engie", bce_number="BE0403201185", copropriete_id="acp-abc")
    assert s.copropriete_id == "acp-abc"


# ---------------------------------------------------------------------------
# find_duplicate_supplier : chinese wall strict, plus de fallback global
# ---------------------------------------------------------------------------
def test_find_duplicate_never_matches_cross_acp():
    """iter90is-2 : un fournisseur Engie dans ACP-A n'est PAS un doublon
    pour un Engie qu'on veut creer dans ACP-B (chinese wall strict).
    Meme BCE, meme nom -> pas de conflit cross-ACP."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import find_duplicate_supplier
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-a-is-{suffix}"
        acp_b = f"acp-b-is-{suffix}"
        engie_a_id = f"engie-a-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": engie_a_id,
                "name": f"Engie-is-{suffix}",
                "copropriete_id": acp_a,
                "bce_number": "BE0403201185",
                "vat_number": "BE0403201185",
                "tier_account_number": "44000001",
            })
            # Cherche dans ACP-B : ne doit PAS trouver le Engie de ACP-A.
            dup = await find_duplicate_supplier(
                db,
                name=f"Engie-is-{suffix}",
                bce_number="BE0403201185",
                vat_number="BE0403201185",
                copro_id=acp_b,
            )
            assert dup is None, (
                f"Chinese wall : Engie dans ACP-A NE DOIT PAS matcher pour ACP-B. "
                f"dup={dup}"
            )
            # Meme requete dans ACP-A -> doit trouver (INTRA-ACP).
            dup_intra = await find_duplicate_supplier(
                db,
                name=f"Engie-is-{suffix}",
                bce_number="BE0403201185",
                copro_id=acp_a,
            )
            assert dup_intra is not None
            assert dup_intra["supplier"]["id"] == engie_a_id
        finally:
            await db.suppliers.delete_many({"id": {"$in": [engie_a_id]}})
            client.close()

    _run(_go())


def test_find_duplicate_returns_none_when_no_copro_id():
    """iter90is-3 : sans copro_id, retourne None (plus de recherche globale)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import find_duplicate_supplier
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        res = await find_duplicate_supplier(
            db, name="ExistingSupplier", bce_number="BE0403201185", copro_id="",
        )
        assert res is None, "Sans copro_id, chinese wall interdit toute recherche"
        client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# get_supplier_account : refuse l'acces cross-ACP
# ---------------------------------------------------------------------------
def test_get_supplier_account_refuses_cross_acp():
    """iter90is-4 : un supplier de ACP-A ne donne PAS son compte tier si
    on demande pour ACP-B (chinese wall strict)."""
    from tier_accounts import get_supplier_account
    sup = {
        "id": "s1",
        "name": "Engie",
        "copropriete_id": "acp-a",
        "tier_account_number": "44000001",
    }
    # Meme ACP -> retourne le compte
    assert get_supplier_account(sup, "acp-a") == "44000001"
    # Cross-ACP -> chinese wall
    assert get_supplier_account(sup, "acp-b") == ""


def test_get_supplier_account_reads_new_field_first():
    """iter90is-5 : lit `tier_account_number` en priorite. Fallback
    `tier_accounts[copro].main` UNIQUEMENT si :
    - Fiche moderne : `copropriete_id == copro`.
    - Fiche legacy (`copropriete_id` vide) : accepte (backward compat).
    """
    from tier_accounts import get_supplier_account
    # Cas 1 : uniquement nouveau champ (fiche moderne)
    sup_new = {"copropriete_id": "acp-a", "tier_account_number": "44000010"}
    assert get_supplier_account(sup_new, "acp-a") == "44000010"
    # Cas 2 : uniquement legacy dict, avec copro_id renseigne (fiche moderne)
    sup_legacy_modern = {"copropriete_id": "acp-a", "tier_accounts": {"acp-a": {"main": "44000020"}}}
    assert get_supplier_account(sup_legacy_modern, "acp-a") == "44000020"
    # Cas 3 : legacy sans copropriete_id (data pre-iter90is) - backward compat
    sup_legacy_pure = {"tier_accounts": {"acp-a": {"main": "44000099"}}}
    assert get_supplier_account(sup_legacy_pure, "acp-a") == "44000099"
    # Cas 4 : les deux -> priorite au nouveau
    sup_both = {
        "copropriete_id": "acp-a",
        "tier_account_number": "44000030",
        "tier_accounts": {"acp-a": {"main": "44000099"}},
    }
    assert get_supplier_account(sup_both, "acp-a") == "44000030"


# ---------------------------------------------------------------------------
# assign_supplier_account : set tier_account_number (plus tier_accounts)
# ---------------------------------------------------------------------------
def test_assign_supplier_account_writes_new_field():
    """iter90is-6 : `assign_supplier_account` persiste sur le champ simple
    `tier_account_number` (plus le dict `tier_accounts`). Sequence 44000XXX
    unique par ACP."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from tier_accounts import assign_supplier_account
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-assign-{suffix}"
        sup_id = f"sup-assign-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Engie-{suffix}",
                "copropriete_id": acp, "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
            })
            sup = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            result = await assign_supplier_account(db, sup, copro_id=acp)
            # Verifie tier_account_number pose (format 44000XXX 8 chars)
            assert result.get("tier_account_number", "").startswith("44000")
            assert len(result["tier_account_number"]) == 8
            # Verifie en DB
            db_doc = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            assert db_doc.get("tier_account_number", "").startswith("44000")
            # Verifie que le compte PCMN est cree pour l'ACP
            pcmn = await db.pcmn_accounts.find_one({
                "copropriete_id": acp, "number": db_doc["tier_account_number"],
            })
            assert pcmn is not None
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            client.close()
    _run(_go())


def test_assign_supplier_account_refuses_cross_acp_pollution():
    """iter90is-7 : appeler assign_supplier_account avec un `copro_id`
    DIFFERENT de celui de la fiche est un no-op (chinese wall). Aucune
    pollution cross-ACP possible."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from tier_accounts import assign_supplier_account
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-cw-a-{suffix}"
        acp_b = f"acp-cw-b-{suffix}"
        sup_id = f"sup-cw-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Test-{suffix}",
                "copropriete_id": acp_a,
                "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
            })
            sup = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            # Tentative de pollution : assigner un compte pour acp_b
            result = await assign_supplier_account(db, sup, copro_id=acp_b)
            # Fiche inchangee : le compte n'est PAS assigne (chinese wall).
            assert "tier_account_number" not in result or not result.get("tier_account_number")
            db_doc = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            assert not db_doc.get("tier_account_number", ""), (
                f"Chinese wall : tier_account_number ne doit PAS etre pose "
                f"quand copro_id != copropriete_id de la fiche. Recu : {db_doc}"
            )
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# MongoDB indexes : per-ACP au lieu de global
# ---------------------------------------------------------------------------
def test_mongodb_indexes_are_per_acp():
    """iter90is-8 : verifie que l'index unique BCE est per-ACP et non global,
    et qu'aucun `uq_supplier_bce` global ne subsiste."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        idx = await db.suppliers.list_indexes().to_list(50)
        names = {i["name"] for i in idx}
        assert "uq_supplier_bce" not in names, (
            "L'ancien index global 'uq_supplier_bce' doit avoir ete drop."
        )
        assert "uq_supplier_copro_bce" in names, (
            "Le nouvel index per-ACP 'uq_supplier_copro_bce' doit exister."
        )
        # L'index copro_bce doit etre unique + composite
        bce_idx = next(i for i in idx if i["name"] == "uq_supplier_copro_bce")
        assert bce_idx.get("unique") is True
        keys = list(bce_idx["key"].items())
        assert keys == [("copropriete_id", 1), ("bce_number", 1)]
        client.close()
    _run(_go())


def test_mongodb_allows_same_bce_across_acps():
    """iter90is-9 : deux fiches Engie (meme BCE) peuvent coexister dans
    2 ACPs distinctes SANS violer l'index unique."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        bce = f"BE{uuid.uuid4().int % 10**10:010d}"
        acp_a = f"acp-bce-a-{suffix}"
        acp_b = f"acp-bce-b-{suffix}"
        ids = [f"engie-a-{suffix}", f"engie-b-{suffix}"]
        try:
            await db.suppliers.insert_one({
                "id": ids[0], "name": f"EngieAcpA-{suffix}",
                "copropriete_id": acp_a, "bce_number": bce,
            })
            # Meme BCE, autre ACP -> doit passer.
            await db.suppliers.insert_one({
                "id": ids[1], "name": f"EngieAcpB-{suffix}",
                "copropriete_id": acp_b, "bce_number": bce,
            })
            count = await db.suppliers.count_documents({"bce_number": bce})
            assert count == 2, f"Attendu 2 fiches partageant le BCE {bce}"
        finally:
            await db.suppliers.delete_many({"id": {"$in": ids}})
            client.close()
    _run(_go())


def test_mongodb_rejects_duplicate_bce_within_same_acp():
    """iter90is-10 : impossible d'inserer 2 fiches avec le meme BCE dans la
    MEME ACP (verrouille par uq_supplier_copro_bce)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from pymongo.errors import DuplicateKeyError
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        bce = f"BE{uuid.uuid4().int % 10**10:010d}"
        acp = f"acp-dup-{suffix}"
        ids = [f"e1-{suffix}", f"e2-{suffix}"]
        try:
            await db.suppliers.insert_one({
                "id": ids[0], "name": f"Engie1-{suffix}",
                "copropriete_id": acp, "bce_number": bce,
            })
            try:
                await db.suppliers.insert_one({
                    "id": ids[1], "name": f"Engie2-{suffix}",
                    "copropriete_id": acp, "bce_number": bce,
                })
                raise AssertionError("Duplicate BCE dans la meme ACP doit lever DuplicateKeyError")
            except DuplicateKeyError:
                pass  # OK
        finally:
            await db.suppliers.delete_many({"id": {"$in": ids}})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# preview-suppliers-pdf : ne renvoie plus de fuzzy_matches cross-ACP
# ---------------------------------------------------------------------------
def test_preview_suppliers_pdf_no_cross_acp_fuzzy():
    """iter90is-11 : quand un Engie existe dans ACP-A, l'import dans ACP-B
    ne doit PAS proposer de le reutiliser (fuzzy_matches vide)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import create_import_wizard_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-pa-{suffix}"
        acp_b = f"acp-pb-{suffix}"
        engie_a = f"engie-a-{suffix}"
        session_id = f"session-{suffix}"
        try:
            # Fournisseur Engie dans ACP-A
            await db.suppliers.insert_one({
                "id": engie_a, "name": f"Engie-{suffix}",
                "copropriete_id": acp_a,
                "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
            })
            # Session d'import ACP-B (chinese wall : cible = acp_b)
            await db.coproprietes.insert_one({"id": acp_b, "name": "ACP-B Test", "status": "active"})
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp_b,
                "created_at": "2026-01-01", "status": "active",
            })
            # Patch auth (superadmin bypass ACP check)
            import server
            async def _fake(req):
                return {"id": "test-admin", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_import_wizard_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/import-wizard/sessions/{session_id}/preview-suppliers-pdf":
                    endpoint = r.endpoint
                    break
            assert endpoint is not None

            from pydantic import BaseModel
            from typing import List, Dict, Any
            class _Payload(BaseModel):
                suppliers: List[Dict[str, Any]] = []

            class _Req:
                cookies = {}
                headers = {}
                @property
                def state(self):
                    class _S: pass
                    return _S()

            payload = _Payload(suppliers=[{"name": f"Engie-{suffix}", "bce_number": ""}])
            result = await endpoint(session_id=session_id, data=payload, request=_Req())
            row = result["suppliers"][0]
            # Fuzzy matches DOIT etre vide (chinese wall strict)
            assert row["fuzzy_matches"] == [], (
                f"Chinese wall : preview-suppliers-pdf ne doit PLUS proposer le "
                f"Engie de ACP-A quand on importe dans ACP-B. Recu : {row['fuzzy_matches']}"
            )
            # Strict match aussi vide (le Engie n'est pas dans ACP-B)
            assert row["strict_match"] is None
            # Action suggeree : create (aucun match trouve)
            assert row["suggested_action"] == "create"
        finally:
            await db.suppliers.delete_one({"id": engie_a})
            await db.coproprietes.delete_one({"id": acp_b})
            await db.import_sessions.delete_one({"id": session_id})
            client.close()
    _run(_go())
