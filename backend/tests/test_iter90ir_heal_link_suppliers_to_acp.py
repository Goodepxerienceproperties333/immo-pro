"""iter90ir : Endpoint de reparation ciblee superadmin
`POST /api/admin/heal-link-suppliers-to-acp`.

Verrouille le comportement demande par l'utilisateur pour Maria Auto 2 :
- Recherche des fournisseurs par nom (matching robuste via
  `_norm_name_candidates`, global cross-ACP).
- Ajout/set idempotent de `tier_accounts.<copro_id>.main = <account>`.
- Creation du compte PCMN dans l'ACP cible.
- Reparation des lignes JE de l'ACP qui utilisent ce compte mais sans
  `third_party_id` valide.
- Report detaille (dry_run/live).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

# iter90is : tous les tests de ce module sont OBSOLETES.
# L'endpoint /api/admin/heal-link-suppliers-to-acp ecrit dans le dict
# `tier_accounts` qui a ete supprime par le refactor Chinese Wall strict.
# Le nouveau modele "1 supplier = 1 ACP" ne permet plus de rattacher une
# fiche existante a une autre ACP. Voir test_iter90is_chinese_wall_strict.py.
pytestmark = pytest.mark.skip(
    reason="OBSOLETE iter90is (Chinese Wall strict) : le partage cross-ACP "
           "via tier_accounts a ete supprime. Voir test_iter90is_chinese_wall_strict.py."
)

def _run(coro):
    return asyncio.run(coro)


async def _bootstrap():
    """Cree un env de test isole (ACP + 2 suppliers globaux + JE avec lignes
    sans tpid). Utilise des noms uniques pour eviter la collision avec
    d'autres suppliers "Engie" existants en DB (fixtures d'autres tests)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    suffix = uuid.uuid4().hex[:8]
    acp_id = f"acp-ir-{suffix}"
    other_acp = f"acp-other-{suffix}"
    engie_id = f"sup-engie-{suffix}"
    finlead_id = f"sup-finlead-{suffix}"
    # Noms uniques pour eviter la collision globale.
    engie_name = f"EngieIterIr{suffix}"
    finlead_name = f"SRL FinleadIterIr{suffix}"

    await db.coproprietes.insert_one({"id": acp_id, "name": f"Maria Auto 2 Test {suffix}", "status": "active"})

    # Engie et Finlead : existent globalement, mais rattaches a other_acp,
    # PAS a l'ACP cible.
    await db.suppliers.insert_one({
        "id": engie_id,
        "name": engie_name,
        "copropriete_id": other_acp,
        "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
        "tier_accounts": {other_acp: {"main": "44000001"}},
    })
    await db.suppliers.insert_one({
        "id": finlead_id,
        "name": finlead_name,
        "copropriete_id": other_acp,
        "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
        "tier_accounts": {other_acp: {"main": "44000002"}},
    })

    # 2 JE dans l'ACP cible : lignes utilisant les comptes 44000110 (Engie)
    # et 44000004 (Finlead) mais SANS third_party_id -> orphelines.
    await db.journal_entries.insert_one({
        "id": f"je1-{suffix}",
        "copropriete_id": acp_id,
        "journal_type": "AC",
        "date": "2026-01-15",
        "reference": "AC-IR-001",
        "total_debit": 100.0,
        "total_credit": 100.0,
        "lines": [
            {"account_number": "61210", "account_name": "Electricite", "debit": 100.0, "credit": 0.0},
            {"account_number": "44000110", "account_name": engie_name, "debit": 0.0, "credit": 100.0},
        ],
    })
    await db.journal_entries.insert_one({
        "id": f"je2-{suffix}",
        "copropriete_id": acp_id,
        "journal_type": "AC",
        "date": "2026-01-16",
        "reference": "AC-IR-002",
        "total_debit": 250.0,
        "total_credit": 250.0,
        "lines": [
            {"account_number": "61300", "account_name": "Honoraires", "debit": 250.0, "credit": 0.0},
            {"account_number": "44000004", "account_name": finlead_name, "debit": 0.0, "credit": 250.0},
        ],
    })
    return {
        "db": db, "client": client, "acp_id": acp_id, "other_acp": other_acp,
        "engie_id": engie_id, "finlead_id": finlead_id, "suffix": suffix,
        "engie_name": engie_name, "finlead_name": finlead_name,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["acp_id"]})
    await db.suppliers.delete_many({"id": {"$in": [ctx["engie_id"], ctx["finlead_id"]]}})
    await db.journal_entries.delete_many({"copropriete_id": ctx["acp_id"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["acp_id"]})
    ctx["client"].close()


def _get_endpoint(db):
    from routes.admin import create_admin_router
    router = create_admin_router(db)
    for r in router.routes:
        if r.path == "/api/admin/heal-link-suppliers-to-acp":
            return r.endpoint
    return None


class _MockRequest:
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


async def _patch_superadmin():
    import server
    async def _fake(req):
        return {"id": "test-admin", "role": "superadmin", "email": "a@t.be", "copropriete_ids": []}
    server.get_current_user = _fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_heal_link_dry_run_reports_planned_actions():
    """iter90ir-1 : dry_run detecte les actions a effectuer sans muter la DB."""
    async def _go():
        ctx = await _bootstrap()
        try:
            await _patch_superadmin()
            endpoint = _get_endpoint(ctx["db"])
            assert endpoint is not None
            req = _MockRequest({
                "copropriete_id": ctx["acp_id"],
                "mapping": [
                    {"name": ctx["engie_name"], "account": "44000110"},
                    {"name": ctx["finlead_name"], "account": "44000004"},
                ],
                "dry_run": True,
            })
            r = await endpoint(req)
            assert r["mode"] == "dry_run"
            assert r["totals"]["suppliers_matched"] == 2
            assert r["totals"]["suppliers_not_found"] == 0
            assert r["totals"]["tier_accounts_set"] == 2
            assert r["totals"]["pcmn_accounts_created"] == 2
            assert r["totals"]["je_lines_repaired"] == 2, (
                f"Attendu 2 lignes JE a reparer, recu : {r['totals']['je_lines_repaired']}"
            )
            # Verifie que la DB n'a PAS ete mutee (dry_run)
            engie = await ctx["db"].suppliers.find_one({"id": ctx["engie_id"]}, {"_id": 0})
            assert ctx["acp_id"] not in (engie.get("tier_accounts") or {}), (
                "En dry_run, tier_accounts NE DOIT PAS etre mute"
            )
            n_pcmn = await ctx["db"].pcmn_accounts.count_documents({"copropriete_id": ctx["acp_id"]})
            assert n_pcmn == 0, "En dry_run, aucun compte PCMN ne doit etre cree"
        finally:
            await _cleanup(ctx)
    _run(_go())


def test_heal_link_live_updates_tier_accounts_and_pcmn():
    """iter90ir-2 : live mute la DB. Verifie tier_accounts + PCMN + JE lines."""
    async def _go():
        ctx = await _bootstrap()
        try:
            await _patch_superadmin()
            endpoint = _get_endpoint(ctx["db"])
            req = _MockRequest({
                "copropriete_id": ctx["acp_id"],
                "mapping": [
                    {"name": ctx["engie_name"], "account": "44000110"},
                    {"name": ctx["finlead_name"], "account": "44000004"},
                ],
                "dry_run": False,
            })
            r = await endpoint(req)
            assert r["mode"] == "live"

            # Engie : tier_accounts.<acp_id>.main == "44000110"
            engie = await ctx["db"].suppliers.find_one({"id": ctx["engie_id"]}, {"_id": 0})
            assert engie["tier_accounts"][ctx["acp_id"]]["main"] == "44000110"
            # Verifie que l'ancien lien n'est pas efface (multi-ACP)
            assert engie["tier_accounts"][ctx["other_acp"]]["main"] == "44000001"

            # PCMN cree
            pcmn = await ctx["db"].pcmn_accounts.find_one(
                {"copropriete_id": ctx["acp_id"], "number": "44000110"},
                {"_id": 0},
            )
            assert pcmn is not None
            assert pcmn.get("is_tier_account") is True

            # JE lines : third_party_id est bien defini sur la ligne du compte tier.
            je1 = await ctx["db"].journal_entries.find_one({"copropriete_id": ctx["acp_id"], "reference": "AC-IR-001"}, {"_id": 0})
            engie_line = next(ln for ln in je1["lines"] if ln["account_number"] == "44000110")
            assert engie_line["third_party_id"] == ctx["engie_id"]
            assert engie_line["third_party_type"] == "supplier"

            je2 = await ctx["db"].journal_entries.find_one({"copropriete_id": ctx["acp_id"], "reference": "AC-IR-002"}, {"_id": 0})
            finlead_line = next(ln for ln in je2["lines"] if ln["account_number"] == "44000004")
            assert finlead_line["third_party_id"] == ctx["finlead_id"]
        finally:
            await _cleanup(ctx)
    _run(_go())


def test_heal_link_is_idempotent():
    """iter90ir-3 : un second appel live ne mute plus rien (already_linked)."""
    async def _go():
        ctx = await _bootstrap()
        try:
            await _patch_superadmin()
            endpoint = _get_endpoint(ctx["db"])
            body = {
                "copropriete_id": ctx["acp_id"],
                "mapping": [{"name": ctx["engie_name"], "account": "44000110"}],
                "dry_run": False,
            }
            r1 = await endpoint(_MockRequest(body))
            r2 = await endpoint(_MockRequest(body))
            # Le second appel : tier deja pose, PCMN deja cree, ligne JE deja
            # reparee -> tous les compteurs a 0 SAUF suppliers_matched (idempotent).
            assert r2["totals"]["suppliers_matched"] == 1
            assert r2["totals"]["tier_accounts_set"] == 0, (
                f"Le tier deja pose ne doit plus etre re-set. r2={r2}"
            )
            assert r2["totals"]["pcmn_accounts_created"] == 0
            assert r2["totals"]["je_lines_repaired"] == 0
            assert r2["totals"]["je_lines_already_ok"] == 1, (
                "La ligne JE deja reparee doit etre comptee dans already_ok"
            )
            # Sanity : r1 avait bien fait les operations
            assert r1["totals"]["tier_accounts_set"] == 1
        finally:
            await _cleanup(ctx)
    _run(_go())


def test_heal_link_supplier_not_found_is_reported():
    """iter90ir-4 : un nom inconnu est rapporte, ne casse pas l'endpoint."""
    async def _go():
        ctx = await _bootstrap()
        try:
            await _patch_superadmin()
            endpoint = _get_endpoint(ctx["db"])
            body = {
                "copropriete_id": ctx["acp_id"],
                "mapping": [
                    {"name": ctx["engie_name"], "account": "44000110"},
                    {"name": "UnknownSupplier-" + uuid.uuid4().hex[:6], "account": "44000999"},
                ],
                "dry_run": False,
            }
            r = await endpoint(_MockRequest(body))
            assert r["totals"]["suppliers_matched"] == 1
            assert r["totals"]["suppliers_not_found"] == 1
            not_found_row = next(row for row in r["results"] if row["action"] == "supplier_not_found")
            assert "Unknown" in not_found_row["name"]
        finally:
            await _cleanup(ctx)
    _run(_go())


def test_heal_link_normalizes_account_to_8_chars():
    """iter90ir-5 : l'account passe par `canonize_supplier_tier_account` :
    '4400110' (7 chars) devient '44000110' (8 chars) avant le tier update."""
    async def _go():
        ctx = await _bootstrap()
        try:
            await _patch_superadmin()
            endpoint = _get_endpoint(ctx["db"])
            body = {
                "copropriete_id": ctx["acp_id"],
                "mapping": [{"name": ctx["engie_name"], "account": "4400110"}],  # 7 chars
                "dry_run": False,
            }
            r = await endpoint(_MockRequest(body))
            row = r["results"][0]
            assert row["account_source"] == "4400110"
            assert row["account_canonical"] == "44000110"
            # Le tier doit etre pose sur le canonique 8 chars
            engie = await ctx["db"].suppliers.find_one({"id": ctx["engie_id"]}, {"_id": 0})
            assert engie["tier_accounts"][ctx["acp_id"]]["main"] == "44000110"
        finally:
            await _cleanup(ctx)
    _run(_go())
