"""iter90iw : Ajout colonne "Compte tier" (tier_account_number) dans la
liste des fournisseurs.

iter90iv : Changement de strategie matching - NOM d'ABORD, compte tier
en FALLBACK dans reconcile-orphan-suppliers-from-je + wizard d'import
Optipro (commit_invoices, _resolve_third_party).

Verrouille :
- L'endpoint GET /api/suppliers retourne bien `tier_account_number` dans
  chaque fiche (le frontend l'utilise pour la nouvelle colonne).
- reconcile-orphan-suppliers-from-je match par NOM d'abord (homonyme
  detecte -> reuse fiche existante + reecriture account_number ligne JE
  avec le canonique de la fiche).
- Le wizard d'import (commit_invoices + _resolve_third_party) applique
  la meme strategie : nom d'abord, aux_code Optipro en fallback.
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
# iter90iw : GET /api/suppliers renvoie tier_account_number
# ---------------------------------------------------------------------------
def test_list_suppliers_returns_tier_account_number():
    """iter90iw-1 : la liste des fournisseurs expose bien le champ
    `tier_account_number` (le frontend l'affiche dans la nouvelle colonne)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import create_suppliers_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iw-{suffix}"
        sup_id = f"sup-iw-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"EngieCol-{suffix}",
                "copropriete_id": acp,
                "tier_account_number": "44000123",
                "bce_number": "",
            })
            # Bypass auth (superadmin -> voit tout)
            import server
            async def _fake(req):
                return {"id": "test", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_suppliers_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/suppliers" and "GET" in (r.methods or set()):
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

            result = await endpoint(_Req(), search=None, copropriete_id=acp)
            fiche = next((s for s in result if s["id"] == sup_id), None)
            assert fiche is not None, "La fiche cree doit etre dans la liste"
            assert fiche.get("tier_account_number") == "44000123", (
                f"tier_account_number doit etre expose (frontend en depend). "
                f"Recu : {fiche}"
            )
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# iter90iv : reconcile match par NOM d'abord (pas par compte)
# ---------------------------------------------------------------------------
def test_reconcile_prefers_name_match_over_account():
    """iter90iv-1 : Scenario Maria Auto - une fiche Engie EXISTE deja
    avec `tier_account_number=44000005`. Un JE oprhelin utilise `44000110`
    (compte different) mais `account_name='Engie'`. Reconcile doit :
    - Trouver la fiche existante Engie par NOM.
    - NE PAS creer une nouvelle fiche.
    - Reecrire `lines.account_number = 44000005` (canonique de la fiche).
    - Poser `third_party_id = <engie_id>`.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.admin import create_admin_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iv-{suffix}"
        engie_id = f"engie-iv-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "ACP iv", "status": "active"})
            # Fiche Engie existante avec compte tier 44000005
            await db.suppliers.insert_one({
                "id": engie_id, "name": f"Engie-{suffix}",
                "copropriete_id": acp, "tier_account_number": "44000005",
                "bce_number": "",
            })
            # JE orphelin : compte 44000110 (different) mais account_name Engie
            await db.journal_entries.insert_one({
                "id": f"je-iv-{suffix}", "copropriete_id": acp,
                "journal_type": "AC", "date": "2026-05-15",
                "reference": "AC-IV-001",
                "total_debit": 100.0, "total_credit": 100.0,
                "lines": [
                    {"account_number": "61210", "account_name": "Elec", "debit": 100.0, "credit": 0.0},
                    {"account_number": "44000110", "account_name": f"Engie-{suffix}",
                     "debit": 0.0, "credit": 100.0},
                ],
            })

            import server
            async def _fake(req):
                return {"id": "test", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_admin_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/admin/reconcile-orphan-suppliers-from-je":
                    endpoint = r.endpoint
                    break

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

            result = await endpoint(_Req({"copropriete_id": acp, "dry_run": False}))
            assert result["totals"]["suppliers_created"] == 0, (
                f"Aucune fiche ne doit etre creee (Engie existe). "
                f"Result : {result}"
            )
            assert result["totals"]["suppliers_reused"] == 1
            assert result["totals"]["je_lines_repointed"] == 1

            # Ligne JE : account_number reecrite avec le canonique 44000005
            je = await db.journal_entries.find_one({"reference": "AC-IV-001"}, {"_id": 0})
            engie_line = next(
                ln for ln in je["lines"] if ln.get("account_name", "").startswith("Engie")
            )
            assert engie_line["account_number"] == "44000005", (
                f"account_number doit etre reecrit avec le canonique 44000005 "
                f"(pas 44000110). Recu : {engie_line}"
            )
            assert engie_line["third_party_id"] == engie_id

            # Le report doit indiquer matched_by=name
            item = result["results"][0]
            assert item.get("matched_by") == "name", (
                f"Le matching doit avoir ete effectue par NOM. Recu : {item}"
            )
        finally:
            await db.suppliers.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.coproprietes.delete_one({"id": acp})
            client.close()
    _run(_go())


def test_reconcile_falls_back_to_account_match_when_no_name_match():
    """iter90iv-2 : Aucune fiche avec le nom donne, MAIS une fiche existe
    avec `tier_account_number = compte de la ligne`. Reconcile doit
    reutiliser cette fiche via le fallback compte.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.admin import create_admin_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-ivf-{suffix}"
        sup_id = f"sup-ivf-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "ACP ivf", "status": "active"})
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"OldName-{suffix}",  # nom different
                "copropriete_id": acp, "tier_account_number": "44000042",
                "bce_number": "",
            })
            # JE avec un nom NON MATCHABLE mais compte 44000042 identique
            await db.journal_entries.insert_one({
                "id": f"je-ivf-{suffix}", "copropriete_id": acp,
                "journal_type": "AC", "date": "2026-05-16",
                "reference": "AC-IVF-001",
                "total_debit": 50.0, "total_credit": 50.0,
                "lines": [
                    {"account_number": "61600", "account_name": "Charge", "debit": 50.0, "credit": 0.0},
                    {"account_number": "44000042", "account_name": f"MysteryVendor-{suffix}",
                     "debit": 0.0, "credit": 50.0},
                ],
            })

            import server
            async def _fake(req):
                return {"id": "test", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
            server.get_current_user = _fake

            router = create_admin_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/admin/reconcile-orphan-suppliers-from-je":
                    endpoint = r.endpoint
                    break

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

            result = await endpoint(_Req({"copropriete_id": acp, "dry_run": False}))
            assert result["totals"]["suppliers_created"] == 0, (
                f"Fallback compte doit reutiliser la fiche. Result : {result}"
            )
            assert result["totals"]["suppliers_reused"] == 1
            item = result["results"][0]
            assert item.get("matched_by") == "account", (
                f"Matching doit etre par account (fallback). Recu : {item}"
            )
        finally:
            await db.suppliers.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.coproprietes.delete_one({"id": acp})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# iter90iv : commit_invoices match par NOM d'abord
# ---------------------------------------------------------------------------
def test_commit_invoices_prefers_name_match_over_aux():
    """iter90iv-3 : verifie via inspection code que commit_invoices lit
    d'abord `inv.supplier_name` pour matcher par nom, puis fallback sur
    `sup_by_aux[supplier_aux]` en dernier recours."""
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # Le nouveau pattern doit contenir le comment iter90iv
    assert "iter90iv (change de strategie) : matching par NOM d'ABORD" in src, (
        "Le commentaire iter90iv doit etre present dans commit_invoices "
        "pour tracer le refactor."
    )
    # L'ancien pattern nu (aux d'abord sans nom) ne doit plus subsister.
    old_pattern = "supplier_doc = sup_by_aux.get(supplier_aux) if supplier_aux else None\n" \
                  "                # iter90gk"
    assert old_pattern not in src, (
        "L'ancien pattern 'aux d'abord' subsiste dans commit_invoices. "
        "Il devrait etre remplace par NOM d'abord + aux en fallback."
    )
    # Le nouveau pattern doit boucler sur db.suppliers.find (matching par
    # nom sur TOUTES les fiches de l'ACP)
    assert 'db.suppliers.find(\n                                {"copropriete_id": copro_id}' in src, (
        "Le matching par nom doit iterer sur tous les suppliers de l'ACP."
    )


def test_resolve_third_party_prefers_name_match():
    """iter90iv-4 : _resolve_third_party dans commit_opening_balance
    matche par NOM d'abord (label), aux_code Optipro en fallback."""
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # Le nouveau pattern doit avoir la boucle name-match AVANT sup_by_aux
    # (contrairement a avant iter90iv ou aux etait en 1er).
    iv_start = src.find("iter90iv : Strategie utilisateur")
    assert iv_start > 0, (
        "Commentaire iter90iv attendu dans _resolve_third_party."
    )
    # Verifie que la boucle name-match arrive AVANT le lookup aux dans le meme bloc.
    aux_lookup = src.find("sup = suppliers_by_aux.get(aux)", iv_start)
    name_lookup = src.find("cand.get(\"name\", \"\")", iv_start)
    assert 0 < name_lookup < aux_lookup, (
        "Le matching par NOM doit venir AVANT le lookup aux_code. "
        f"iter90iv={iv_start}, name={name_lookup}, aux={aux_lookup}"
    )
