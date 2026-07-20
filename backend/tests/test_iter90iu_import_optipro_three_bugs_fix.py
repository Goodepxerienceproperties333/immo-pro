"""iter90iu : Bug fixes suite au bug report utilisateur "trois bugs d'importation
Optipro sur property-mgmt-be".

Verrouille les 4 corrections :
1. `commit_invoices` (import_wizard.py:~1208) lit en priorite
   `supplier.tier_account_number` avant de fallback sur le dict legacy
   ou de recalculer via l'aux Optipro.
2. `commit_journals` (import_wizard.py:~1467) normalise `counterparty_account`
   via `canonize_supplier_tier_account` avant de creer le JE FI
   (evite les comptes 7 chars persistant dans le Bilan).
3. `reconcile-orphan-suppliers-from-je` (admin.py:~3279) met a jour
   `account_number` en meme temps que `third_party_id` avec le compte
   canonique 8 chars (repare les JE legacy avec des comptes 7 chars).
4. `assign_supplier_account` (tier_accounts.py) ecrit simultanement
   `tier_account_number` (nouveau champ plat) ET `tier_accounts[copro].main`
   (dict legacy) pour compatibilite avec l'ancien code lecture.
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
# Point n°4 : assign_supplier_account remplit AUSSI le dict legacy
# ---------------------------------------------------------------------------
def test_assign_supplier_account_fills_both_new_and_legacy():
    """iter90iu-1 : assign_supplier_account persiste `tier_account_number`
    (plat) ET `tier_accounts[copro].main` (dict legacy) simultanement."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from tier_accounts import assign_supplier_account
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iu-{suffix}"
        sup_id = f"sup-iu-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Engie-iu-{suffix}",
                "copropriete_id": acp,
                "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
            })
            sup = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            result = await assign_supplier_account(db, sup, copro_id=acp)
            # Champ plat rempli (nouveau)
            assert result.get("tier_account_number", "").startswith("44000")
            num = result["tier_account_number"]
            # Dict legacy egalement rempli (compat)
            legacy = result.get("tier_accounts") or {}
            assert acp in legacy, (
                f"tier_accounts[{acp}] doit exister pour compat legacy. "
                f"Recu : {legacy}"
            )
            assert legacy[acp].get("main") == num, (
                f"tier_accounts[{acp}].main doit == tier_account_number ({num}). "
                f"Recu : {legacy[acp]}"
            )
            # Verifie en DB
            db_doc = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            assert db_doc["tier_account_number"] == num
            assert db_doc["tier_accounts"][acp]["main"] == num
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            client.close()
    _run(_go())


def test_assign_supplier_account_migrates_legacy_dict_when_new_field_only():
    """iter90iu-2 : si `tier_account_number` existe mais `tier_accounts`
    dict est absent (fiche moderne pure), un appel a assign_supplier_account
    AUTO-MIGRE le dict legacy pour compat."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from tier_accounts import assign_supplier_account
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iu2-{suffix}"
        sup_id = f"sup-iu2-{suffix}"
        try:
            # Insert avec tier_account_number rempli mais tier_accounts absent
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Test-{suffix}",
                "copropriete_id": acp,
                "tier_account_number": "44000042",
                # PAS de tier_accounts dict
            })
            sup = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            result = await assign_supplier_account(db, sup, copro_id=acp)
            # Le champ plat est conserve
            assert result["tier_account_number"] == "44000042"
            # Le dict legacy a ete auto-cree
            assert result["tier_accounts"][acp]["main"] == "44000042"
            db_doc = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
            assert db_doc["tier_accounts"][acp]["main"] == "44000042"
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# Point n°3 : reconcile met a jour aussi account_number
# ---------------------------------------------------------------------------
def test_reconcile_updates_line_account_number_to_canonical():
    """iter90iu-3 : reconcile-orphan-suppliers-from-je met a jour
    `lines.{i}.account_number` avec le compte canonique. Repare les JE
    legacy contenant un compte 7 chars ("4400015" -> "44000015")."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.admin import create_admin_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iu3-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "ACP iu3", "status": "active"})
            # JE avec compte 7 chars non canonique ("4400015")
            await db.journal_entries.insert_one({
                "id": f"je-iu3-{suffix}", "copropriete_id": acp,
                "journal_type": "AC", "date": "2026-05-01",
                "reference": "AC-IU3-001",
                "total_debit": 300.0, "total_credit": 300.0,
                "lines": [
                    {"account_number": "61210", "account_name": "Elec", "debit": 300.0, "credit": 0.0},
                    # LIGNE ORPHELINE : compte 7 chars
                    {"account_number": "4400015", "account_name": f"Engie-iu3-{suffix}",
                     "debit": 0.0, "credit": 300.0},
                ],
            })

            # Patch auth superadmin
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
            assert endpoint is not None

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
            assert result["totals"]["suppliers_created"] == 1
            assert result["totals"]["je_lines_repointed"] == 1

            # Verifie que la ligne a ete corrigee :
            # - account_number : 4400015 -> 44000015 (canonique)
            # - third_party_id : ID du nouveau supplier
            je = await db.journal_entries.find_one({"reference": "AC-IU3-001"}, {"_id": 0})
            engie_line = next(
                ln for ln in je["lines"]
                if ln.get("account_name", "").startswith("Engie")
            )
            assert engie_line["account_number"] == "44000015", (
                f"account_number doit avoir ete normalise en 8 chars canonique. "
                f"Recu : {engie_line['account_number']}"
            )
            assert engie_line["third_party_id"], (
                "third_party_id doit etre pose"
            )
            # Le supplier auto-cree a bien tier_account_number = 44000015
            sup = await db.suppliers.find_one({"id": engie_line["third_party_id"]}, {"_id": 0})
            assert sup["tier_account_number"] == "44000015"
        finally:
            await db.suppliers.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.coproprietes.delete_one({"id": acp})
            client.close()
    _run(_go())


# ---------------------------------------------------------------------------
# Point n°1 : commit_invoices lit tier_account_number en priorite
# ---------------------------------------------------------------------------
def test_commit_invoices_prefers_flat_tier_account_number():
    """iter90iu-4 : verifie que la fonction commit_invoices lit d'abord
    `supplier.tier_account_number` (source de verite iter90is) avant de
    fallback ou de reconstruire via l'aux Optipro."""
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # Le pattern attendu apres iter90iu :
    #   sup_pcmn = (supplier_doc.get("tier_account_number") or "").strip()
    #   if not sup_pcmn:
    #       sup_pcmn = ((supplier_doc.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
    idx = src.find("sup_pcmn = (supplier_doc.get(\"tier_account_number\") or \"\").strip()")
    assert idx > 0, (
        "commit_invoices doit lire tier_account_number en priorite. "
        "Pattern manquant : "
        "'sup_pcmn = (supplier_doc.get(\"tier_account_number\") or \"\").strip()'"
    )
    # Verifie que le fallback est bien apres (dans un `if not sup_pcmn:`)
    fallback_idx = src.find("if not sup_pcmn:", idx)
    assert 0 < fallback_idx - idx < 400, (
        "Le fallback `if not sup_pcmn: sup_pcmn = tier_accounts[copro].main` "
        "doit suivre immediatement le lookup du champ plat."
    )


# ---------------------------------------------------------------------------
# Point n°2 : commit_journals canonize counterparty_account
# ---------------------------------------------------------------------------
def test_commit_journals_canonizes_counterparty_account():
    """iter90iu-5 : verifie que commit_journals appelle
    canonize_supplier_tier_account sur le compte contrepartie AVANT de
    creer le JE FI."""
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # Le pattern attendu apres iter90iu :
    #   cp_pcmn = canonize_supplier_tier_account((t.get("counterparty_account") or "").strip())
    assert 'cp_pcmn = canonize_supplier_tier_account((t.get("counterparty_account")' in src, (
        "commit_journals doit normaliser counterparty_account via "
        "canonize_supplier_tier_account avant persistance JE FI."
    )
    # Verifie que l'ancien pattern non-canonique n'est plus present
    # (le pattern non-canonique etait: `cp_pcmn = (t.get("counterparty_account") or "").strip()`
    # sans passer par canonize).
    old_pattern = 'cp_pcmn = (t.get("counterparty_account") or "").strip()\n'
    assert old_pattern not in src, (
        "L'ancien pattern non-canonique subsiste : cp_pcmn assigne directement "
        "sans canonize_supplier_tier_account. Doit avoir ete refactore."
    )


# ---------------------------------------------------------------------------
# E2E : reconcile puis relance ne fait plus rien (idempotent et complet)
# ---------------------------------------------------------------------------
def test_reconcile_e2e_normalizes_all_orphans_to_canonical_8_chars():
    """iter90iu-6 : scenario reel Maria Auto - JE avec plusieurs comptes
    7 chars orphelins (4400015, 4400200, 4400300). Un seul reconcile
    LIVE doit :
    - Creer 3 fiches locales
    - Normaliser les 3 comptes en 8 chars
    - Repointer les 3 lignes JE (account_number ET third_party_id)
    Un 2e appel ne fait plus rien (0 orphelins)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.admin import create_admin_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iu6-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": acp, "name": "Maria Auto Test IU6", "status": "active"})
            for i, (raw_acc, name) in enumerate([
                ("4400015", "Engie"),
                ("4400200", "Finlead"),
                ("4400300", "Baloise Insurance"),
            ]):
                await db.journal_entries.insert_one({
                    "id": f"je-iu6-{suffix}-{i}", "copropriete_id": acp,
                    "journal_type": "AC", "date": "2026-05-01",
                    "reference": f"AC-IU6-{i:03d}",
                    "total_debit": 100.0, "total_credit": 100.0,
                    "lines": [
                        {"account_number": "61210", "account_name": "Charge", "debit": 100.0, "credit": 0.0},
                        {"account_number": raw_acc, "account_name": f"{name}-{suffix}",
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

            r1 = await endpoint(_Req({"copropriete_id": acp, "dry_run": False}))
            assert r1["totals"]["orphan_accounts"] == 3
            assert r1["totals"]["suppliers_created"] == 3
            assert r1["totals"]["je_lines_repointed"] == 3

            # Verifie que les 3 lignes JE ont ete normalisees
            for je in await db.journal_entries.find({"copropriete_id": acp}).to_list(10):
                for ln in je["lines"]:
                    if ln.get("account_number", "").startswith("440"):
                        assert len(ln["account_number"]) == 8, (
                            f"account_number doit etre 8 chars canonique apres reconcile. "
                            f"Recu : {ln}"
                        )
                        # tpid pose
                        if ln.get("third_party_type") == "supplier":
                            assert ln.get("third_party_id"), (
                                f"third_party_id doit etre pose. Ligne : {ln}"
                            )

            # 2e appel : plus rien a faire (idempotent + complet)
            r2 = await endpoint(_Req({"copropriete_id": acp, "dry_run": False}))
            assert r2["totals"]["orphan_accounts"] == 0
            assert r2["totals"]["suppliers_created"] == 0
            assert r2["totals"]["je_lines_repointed"] == 0
        finally:
            await db.suppliers.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.coproprietes.delete_one({"id": acp})
            client.close()
    _run(_go())
