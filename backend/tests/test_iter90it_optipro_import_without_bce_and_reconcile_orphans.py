"""iter90it : Bug fix - import Optipro crée des orphelins car BCE manquant.

Corrections :
1. `POST /api/suppliers` : BCE plus obligatoire (Chinese Wall suffit).
2. `POST /api/import-wizard/sessions/{id}/commit-suppliers-pdf` :
   action=create ne bloque plus si BCE vide.
3. Nouveau endpoint superadmin `POST /api/admin/reconcile-orphan-suppliers-from-je` :
   scanne les JE d'une ACP -> pour chaque compte 440XXX orphelin, cree
   une fiche fournisseur locale (nom = account_name, tier_account_number
   = account_number) et repointe les lignes.
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
# 1. POST /api/suppliers : BCE plus obligatoire
# ---------------------------------------------------------------------------
def test_create_supplier_without_bce_is_now_allowed():
    """iter90it-1 : le endpoint standard POST /api/suppliers accepte
    desormais une fiche sans BCE ni TVA (Chinese Wall = pas de doublon
    global possible)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import create_suppliers_router, SupplierInput
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-it-nobce-{suffix}"
        sup_name = f"NoBce-{suffix}"
        try:
            # Bypass auth
            import server
            async def _fake(req):
                return {"id": "test-admin", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_suppliers_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/suppliers" and "POST" in (r.methods or set()):
                    endpoint = r.endpoint
                    break
            assert endpoint is not None

            class _Req:
                cookies = {}
                headers = {}
                @property
                def state(self):
                    class _S: pass
                    return _S()

            data = SupplierInput(name=sup_name, copropriete_id=acp, bce_number="")
            result = await endpoint(_Req(), data)
            assert result["name"] == sup_name
            assert result["copropriete_id"] == acp
            # Le supplier existe en DB
            in_db = await db.suppliers.find_one({"id": result["id"]}, {"_id": 0})
            assert in_db is not None
            assert in_db.get("bce_number", "") == ""
        finally:
            await db.suppliers.delete_many({"name": sup_name})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# 2. commit-suppliers-pdf : accepte action=create sans BCE
# ---------------------------------------------------------------------------
def test_commit_suppliers_pdf_creates_without_bce():
    """iter90it-2 : action=create avec BCE vide DOIT reussir (avant le
    fix : erreur 'Creation refusee : BCE obligatoire').
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import create_import_wizard_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-it-cs-{suffix}"
        session_id = f"session-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "ACP Test IT", "status": "active"})
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp,
                "created_at": "2026-01-01", "status": "active",
            })
            # Bypass auth
            import server
            async def _fake(req):
                return {"id": "test-admin", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_import_wizard_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/import-wizard/sessions/{session_id}/commit-suppliers-pdf":
                    endpoint = r.endpoint
                    break
            assert endpoint is not None

            from pydantic import BaseModel
            from typing import List, Dict, Any, Optional
            class _Payload(BaseModel):
                suppliers: List[Dict[str, Any]] = []
                decisions: Optional[Dict[str, Any]] = None

            class _Req:
                cookies = {}
                headers = {}
                @property
                def state(self):
                    class _S: pass
                    return _S()

            payload = _Payload(
                suppliers=[
                    {"name": f"Engie-{suffix}", "auxiliary_code": "F0001", "postal_code": "1000"},
                    {"name": f"Finlead-{suffix}", "auxiliary_code": "F0002"},
                ],
                decisions={
                    "0": {"action": "create", "bce_number": ""},  # <-- BCE vide
                    "1": {"action": "create", "bce_number": ""},  # <-- BCE vide
                },
            )
            result = await endpoint(session_id=session_id, data=payload, request=_Req())
            assert result["inserted"] == 2, (
                f"Attendu 2 inserts (action=create sans BCE), recu {result}"
            )
            assert len(result["errors"]) == 0, (
                f"Aucune erreur attendue, recu : {result['errors']}"
            )
            # Verifie que les fiches sont bien creees LOCALEMENT dans l'ACP
            engie = await db.suppliers.find_one({"name": f"Engie-{suffix}"}, {"_id": 0})
            assert engie is not None
            assert engie["copropriete_id"] == acp
            assert engie.get("bce_number", "") == ""
            assert engie.get("auxiliary_code", "") == "F0001"
        finally:
            await db.suppliers.delete_many({"name": {"$regex": f"-{suffix}$"}})
            await db.coproprietes.delete_one({"id": acp})
            await db.import_sessions.delete_one({"id": session_id})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# 3. reconcile-orphan-suppliers-from-je : dry_run + live
# ---------------------------------------------------------------------------
async def _bootstrap_orphan_scenario():
    """Cree un scenario avec 2 comptes 440XXX orphelins (aucune fiche
    fournisseur ne les couvre) + 1 compte 440XXX avec fiche existante."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    suffix = uuid.uuid4().hex[:8]
    acp = f"acp-rec-{suffix}"
    known_sup_id = f"sup-known-{suffix}"

    await db.coproprietes.insert_one({"id": acp, "name": "ACP RecOrphans", "status": "active"})
    # Fiche fournisseur "AG Insurance" DEJA existante avec 44000005
    await db.suppliers.insert_one({
        "id": known_sup_id, "name": f"AG Insurance-{suffix}",
        "copropriete_id": acp, "tier_account_number": "44000005",
        "bce_number": "",
    })
    # JE 1 : Engie orphelin (44000110, sans tpid)
    await db.journal_entries.insert_one({
        "id": f"je-e-{suffix}", "copropriete_id": acp,
        "journal_type": "AC", "date": "2026-03-01",
        "reference": "AC-REC-001",
        "total_debit": 100.0, "total_credit": 100.0,
        "lines": [
            {"account_number": "61210", "account_name": "Electricite", "debit": 100.0, "credit": 0.0},
            {"account_number": "44000110", "account_name": f"Engie-{suffix}", "debit": 0.0, "credit": 100.0},
        ],
    })
    # JE 2 : Finlead orphelin (44000200, sans tpid)
    await db.journal_entries.insert_one({
        "id": f"je-f-{suffix}", "copropriete_id": acp,
        "journal_type": "AC", "date": "2026-03-02",
        "reference": "AC-REC-002",
        "total_debit": 250.0, "total_credit": 250.0,
        "lines": [
            {"account_number": "61300", "account_name": "Honoraires", "debit": 250.0, "credit": 0.0},
            {"account_number": "44000200", "account_name": f"Finlead-{suffix}", "debit": 0.0, "credit": 250.0},
        ],
    })
    # JE 3 : AG Insurance NON orphelin (tpid correct)
    await db.journal_entries.insert_one({
        "id": f"je-ag-{suffix}", "copropriete_id": acp,
        "journal_type": "AC", "date": "2026-03-03",
        "reference": "AC-REC-003",
        "total_debit": 500.0, "total_credit": 500.0,
        "lines": [
            {"account_number": "61600", "account_name": "Assurance", "debit": 500.0, "credit": 0.0},
            {"account_number": "44000005", "account_name": f"AG Insurance-{suffix}",
             "debit": 0.0, "credit": 500.0, "third_party_id": known_sup_id, "third_party_type": "supplier"},
        ],
    })
    return {"db": db, "client": client, "acp": acp, "known_sup_id": known_sup_id, "suffix": suffix}


async def _cleanup_scenario(ctx):
    db = ctx["db"]
    await db.suppliers.delete_many({"copropriete_id": ctx["acp"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["acp"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["acp"]})
    await db.coproprietes.delete_one({"id": ctx["acp"]})
    ctx["client"].close()


def _get_reconcile_endpoint(db):
    from routes.admin import create_admin_router
    router = create_admin_router(db)
    for r in router.routes:
        if r.path == "/api/admin/reconcile-orphan-suppliers-from-je":
            return r.endpoint
    return None


class _Req:
    def __init__(self, body):
        self._body = body
        self.cookies = {}
        self.headers = {}
    async def json(self):
        return self._body
    @property
    def state(self):
        class _S: pass
        return _S()


async def _patch_super():
    import server
    async def _fake(req):
        return {"id": "test-admin", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
    server.get_current_user = _fake


def test_reconcile_dry_run_detects_orphans_without_mutation():
    """iter90it-3 : dry_run detecte 2 orphelins (Engie + Finlead) sans
    creer de fiche ni modifier les JE."""
    async def _go():
        ctx = await _bootstrap_orphan_scenario()
        try:
            await _patch_super()
            endpoint = _get_reconcile_endpoint(ctx["db"])
            assert endpoint is not None
            r = await endpoint(_Req({"copropriete_id": ctx["acp"], "dry_run": True}))
            assert r["mode"] == "dry_run"
            assert r["totals"]["orphan_accounts"] == 2, r
            assert r["totals"]["suppliers_created"] == 2
            assert r["totals"]["je_lines_repointed"] == 2
            # DB non mutee
            supp_count = await ctx["db"].suppliers.count_documents({"copropriete_id": ctx["acp"]})
            assert supp_count == 1  # que le AG Insurance initial
            # JE lignes non mutees
            je_e = await ctx["db"].journal_entries.find_one({"reference": "AC-REC-001"}, {"_id": 0})
            engie_line = next(ln for ln in je_e["lines"] if ln["account_number"] == "44000110")
            assert not engie_line.get("third_party_id")
        finally:
            await _cleanup_scenario(ctx)
    _run(_go())


def test_reconcile_live_creates_local_suppliers_and_repoints():
    """iter90it-4 : live cree 2 fiches locales (Engie, Finlead) avec
    tier_account_number correct + repointe les lignes JE."""
    async def _go():
        ctx = await _bootstrap_orphan_scenario()
        try:
            await _patch_super()
            endpoint = _get_reconcile_endpoint(ctx["db"])
            r = await endpoint(_Req({"copropriete_id": ctx["acp"], "dry_run": False}))
            assert r["mode"] == "live"
            assert r["totals"]["suppliers_created"] == 2
            assert r["totals"]["je_lines_repointed"] == 2

            # 2 nouvelles fiches locales
            all_supps = await ctx["db"].suppliers.find(
                {"copropriete_id": ctx["acp"]}, {"_id": 0},
            ).to_list(50)
            names = {s["name"] for s in all_supps}
            assert any(n.startswith("Engie") for n in names)
            assert any(n.startswith("Finlead") for n in names)
            engie_fiche = next(s for s in all_supps if s["name"].startswith("Engie"))
            finlead_fiche = next(s for s in all_supps if s["name"].startswith("Finlead"))
            assert engie_fiche["tier_account_number"] == "44000110"
            assert engie_fiche["copropriete_id"] == ctx["acp"]
            assert engie_fiche.get("bce_number", "") == ""  # BCE vide pour import
            assert engie_fiche.get("auto_created") is True
            assert finlead_fiche["tier_account_number"] == "44000200"

            # Lignes JE repointees
            je_e = await ctx["db"].journal_entries.find_one({"reference": "AC-REC-001"}, {"_id": 0})
            engie_line = next(ln for ln in je_e["lines"] if ln["account_number"] == "44000110")
            assert engie_line["third_party_id"] == engie_fiche["id"]
            assert engie_line["third_party_type"] == "supplier"

            # AG Insurance : deja OK, ne doit PAS avoir ete recree
            ag_count = sum(1 for s in all_supps if s["name"].startswith("AG"))
            assert ag_count == 1

            # Comptes PCMN 44000110 et 44000200 crees
            pcmn_engie = await ctx["db"].pcmn_accounts.find_one(
                {"copropriete_id": ctx["acp"], "number": "44000110"},
            )
            assert pcmn_engie is not None
            assert pcmn_engie.get("is_tier_account") is True
        finally:
            await _cleanup_scenario(ctx)
    _run(_go())


def test_reconcile_is_idempotent():
    """iter90it-5 : 2eme appel live -> 0 nouveaux crees, 0 lignes repointees."""
    async def _go():
        ctx = await _bootstrap_orphan_scenario()
        try:
            await _patch_super()
            endpoint = _get_reconcile_endpoint(ctx["db"])
            r1 = await endpoint(_Req({"copropriete_id": ctx["acp"], "dry_run": False}))
            r2 = await endpoint(_Req({"copropriete_id": ctx["acp"], "dry_run": False}))
            assert r1["totals"]["suppliers_created"] == 2
            assert r2["totals"]["orphan_accounts"] == 0, (
                f"Apres reconcile, plus d'orphelins attendus. r2={r2}"
            )
            assert r2["totals"]["suppliers_created"] == 0
            assert r2["totals"]["je_lines_repointed"] == 0
        finally:
            await _cleanup_scenario(ctx)
    _run(_go())


def test_reconcile_reuses_existing_supplier_by_name():
    """iter90it-6 : si une fiche existe deja dans l'ACP avec un NOM
    proche du compte orphelin (mais tier_account_number vide), la reutiliser
    au lieu d'en creer une nouvelle."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-reu-{suffix}"
        sup_id = f"sup-baloise-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "ACP Reuse", "status": "active"})
            # Fiche Baloise EXISTANTE sans tier_account_number
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Baloise Insurance-{suffix}",
                "copropriete_id": acp, "bce_number": "",
                "tier_account_number": "",
            })
            # JE avec un compte 44000300 sans tpid, account_name = "Baloise Insurance-XYZ"
            await db.journal_entries.insert_one({
                "id": f"je-b-{suffix}", "copropriete_id": acp,
                "journal_type": "AC", "date": "2026-04-01",
                "reference": "AC-REU-001",
                "total_debit": 800.0, "total_credit": 800.0,
                "lines": [
                    {"account_number": "61600", "account_name": "Assur", "debit": 800.0, "credit": 0.0},
                    {"account_number": "44000300", "account_name": f"Baloise Insurance-{suffix}",
                     "debit": 0.0, "credit": 800.0},
                ],
            })
            await _patch_super()
            endpoint = _get_reconcile_endpoint(db)
            r = await endpoint(_Req({"copropriete_id": acp, "dry_run": False}))
            assert r["totals"]["suppliers_created"] == 0, (
                f"Doit reutiliser Baloise existant, pas creer. r={r}"
            )
            assert r["totals"]["suppliers_reused"] == 1
            assert r["totals"]["je_lines_repointed"] == 1
            # La fiche existante a ete enrichie du tier_account_number.
            reused = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            assert reused["tier_account_number"] == "44000300"
            # Ligne JE repointee vers la fiche existante
            je_b = await db.journal_entries.find_one({"reference": "AC-REU-001"}, {"_id": 0})
            baloise_line = next(ln for ln in je_b["lines"] if ln["account_number"] == "44000300")
            assert baloise_line["third_party_id"] == sup_id
        finally:
            await db.suppliers.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.coproprietes.delete_one({"id": acp})
            client.close()
    _run(_go())


def test_reconcile_ignores_already_linked_lines():
    """iter90it-7 : les lignes deja bien liees (tpid pointant vers un
    supplier de l'ACP) ne sont PAS re-touchees."""
    async def _go():
        ctx = await _bootstrap_orphan_scenario()
        try:
            await _patch_super()
            endpoint = _get_reconcile_endpoint(ctx["db"])
            r = await endpoint(_Req({"copropriete_id": ctx["acp"], "dry_run": False}))
            # Le AG Insurance (deja lie) ne doit pas apparaitre dans le report.
            for item in r["results"]:
                assert item["tier_account"] != "44000005", (
                    f"AG Insurance ne devrait PAS etre dans les orphelins. r={r}"
                )
            # Le JE AG n'a pas ete touche
            je_ag = await ctx["db"].journal_entries.find_one({"reference": "AC-REC-003"}, {"_id": 0})
            ag_line = next(ln for ln in je_ag["lines"] if ln["account_number"] == "44000005")
            assert ag_line["third_party_id"] == ctx["known_sup_id"]
        finally:
            await _cleanup_scenario(ctx)
    _run(_go())
