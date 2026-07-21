"""iter90jk : Le Wizard doit remapper les comptes bancaires 6-chiffres du
CSV/PDF Optipro vers leur equivalent 8-chiffres officiel de la fiche ACP.

Cas d'usage utilisateur : le CSV Optipro contient "551331" mais la fiche
ACP a le compte officiel "55133100". Sans remap, le wizard bloque avec un
400 "Import bloque : comptes bancaires inconnus". Avec le fix iter90jk,
le wizard remplace transparemment 551331 -> 55133100 avant validation.

Tests :
1. Le helper `_load_canonical_bank_index` construit correctement le mapping
   6->8 chars a partir de coproprietes.bank_accounts.
2. `_remap_bank_account` remape 6-char -> 8-char et laisse le 8-char intact
   (idempotence).
3. `_remap_bank_account` laisse un compte sans mapping inchange (le blocage
   400 aval assure la protection).
4. `commit-journals` accepte des transactions avec bank_account 6-char si
   un 8-char equivalent existe dans coproprietes.bank_accounts.
5. `commit-journals` bloque toujours (400) si le 6-char n'a AUCUN equivalent.
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


def _get_helpers(db):
    """Instancie le router et extrait les 2 helpers a tester."""
    from routes.import_wizard import create_import_wizard_router
    router = create_import_wizard_router(db)
    # Les helpers sont dans le scope de la factory - on les retrouve via
    # inspection des closures des endpoints.
    load_idx = None
    remap = None
    for r in router.routes:
        fn = getattr(r, "endpoint", None)
        if not fn:
            continue
        closure = getattr(fn, "__closure__", None)
        if not closure:
            continue
        names = fn.__code__.co_freevars
        for i, name in enumerate(names):
            if name == "_load_canonical_bank_index" and load_idx is None:
                load_idx = closure[i].cell_contents
            elif name == "_remap_bank_account" and remap is None:
                remap = closure[i].cell_contents
    assert load_idx is not None, "Helper `_load_canonical_bank_index` introuvable"
    assert remap is not None, "Helper `_remap_bank_account` introuvable"
    return load_idx, remap


# ---------------------------------------------------------------------------
# Test 1 : _load_canonical_bank_index lit coproprietes.bank_accounts
# ---------------------------------------------------------------------------
def test_load_canonical_bank_index_maps_6char_and_7char_prefixes():
    """iter90jk-1 : l'index construit a partir de coproprietes.bank_accounts
    doit contenir les mappings 6->8, 7->8 et 8->8 (identite)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jk1-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jk-{suffix}",
                "bank_accounts": [
                    {"iban": "BE04001952089331", "pcmn_number": "55133100",
                     "label": "Vue", "is_default": True},
                    {"iban": "BE90034363465732", "pcmn_number": "55073200",
                     "label": "Epargne", "is_default": False},
                ],
            })
            load_idx, _ = _get_helpers(db)
            idx = await load_idx(acp)
            # Le canonique 8-char apparait 3 fois (identite + 6->8 + 7->8)
            assert idx.get("55133100") == "55133100"
            assert idx.get("551331") == "55133100"
            assert idx.get("5513310") == "55133100"
            assert idx.get("55073200") == "55073200"
            assert idx.get("550732") == "55073200"
            # Un code inconnu n'est pas dans l'index
            assert "999999" not in idx
        finally:
            await db.coproprietes.delete_one({"id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 2 : _remap_bank_account remape 6->8 et est idempotent
# ---------------------------------------------------------------------------
def test_remap_bank_account_maps_6char_and_keeps_8char_and_non_bank():
    """iter90jk-2 : le helper remape "551331" -> "55133100", laisse un 8-char
    canonique inchange, et laisse un compte non-bancaire (44000015) inchange."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jk2-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jk-{suffix}",
                "bank_accounts": [
                    {"iban": "BE04001952089331", "pcmn_number": "55133100",
                     "is_default": True},
                ],
            })
            load_idx, remap = _get_helpers(db)
            idx = await load_idx(acp)
            # 6-char -> 8-char
            assert remap("551331", idx) == "55133100"
            # 8-char idempotent
            assert remap("55133100", idx) == "55133100"
            # Compte fournisseur (pas 55XX) inchange
            assert remap("44000015", idx) == "44000015"
            # Compte 55 sans mapping (fantome) reste inchange (le blocage
            # amont via _ensure_pcmn_accounts fait le reste).
            assert remap("559999", idx) == "559999"
            # Whitespace strip
            assert remap("  551331  ", idx) == "55133100"
            # Vide / None-safe
            assert remap("", idx) == ""
            assert remap(None, idx) is None
        finally:
            await db.coproprietes.delete_one({"id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 3 : commit-journals accepte le 6-char si son 8-char existe (fix P0)
# ---------------------------------------------------------------------------
def test_commit_journals_accepts_6digit_when_canonical_exists_in_acp():
    """iter90jk-3 : le cas critique utilisateur : une transaction avec
    bank_account="551331" et counterparty_account="440015" doit passer
    la validation ET stocker les JE avec les comptes officiels 8-char
    de la fiche ACP (55133100)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import create_import_wizard_router, CommitJournalsInput

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jk3-{suffix}"
        session_id = str(uuid.uuid4())
        fy_id = f"fy-jk3-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jk3-{suffix}",
                "bank_accounts": [
                    {"id": "ba-1", "iban": "BE04001952089331",
                     "pcmn_number": "55133100", "label": "Vue", "is_default": True},
                ],
            })
            await db.fiscal_years.insert_one({
                "id": fy_id, "copropriete_id": acp,
                "name": "2026", "start_date": "2026-01-01",
                "end_date": "2026-12-31", "status": "open",
            })
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp,
                "status": "in_progress",
            })
            # Ensure the counterpart PCMN exists so it doesn't trip the blocker.
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()), "number": "44000015",
                "name": "Fournisseur Test", "class_num": 4,
                "copropriete_id": acp,
            })

            router = create_import_wizard_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "").endswith("/sessions/{session_id}/commit-journals"):
                    handler = r.endpoint
                    break
            assert handler is not None

            import server
            original = server.get_current_user

            async def _fake_su(_req):
                return {"role": "superadmin", "copropriete_ids": [acp]}

            server.get_current_user = _fake_su

            class _FakeReq:
                headers = {}
                class state:
                    copropriete_id = None

            # Payload : transaction avec bank_account 6-char (551331)
            data = CommitJournalsInput(
                transactions=[{
                    "date_value": "2026-01-15",
                    "amount": 100.0,
                    "direction": "out",
                    "libelle": "Paiement fournisseur",
                    "num_doc": "PAY-001",
                    "bank_account": "551331",         # <-- 6-char raccourci
                    "bank_account_label": "Vue",
                    "counterparty_account": "44000015",
                    "counterparty_account_label": "Fournisseur",
                }],
                bank_account_mapping={},
            )
            try:
                result = await handler(session_id=session_id, data=data, request=_FakeReq())
                # Doit reussir SANS 400
                assert isinstance(result, dict), f"Retour attendu dict, got {type(result)}"
                assert result.get("inserted", 0) >= 0
                # Verifie que le JE cree utilise le compte canonique 55133100
                je = await db.journal_entries.find_one(
                    {"copropriete_id": acp, "journal_type": "FI"},
                    {"_id": 0, "lines": 1},
                )
                assert je is not None, "Un JE FI aurait du etre cree"
                acc_nums = [ln.get("account_number") for ln in je.get("lines", [])]
                assert "55133100" in acc_nums, (
                    f"Le JE doit utiliser le compte canonique 55133100. Vu: {acc_nums}"
                )
                assert "551331" not in acc_nums, (
                    f"Le JE ne doit PAS contenir le 6-char raccourci 551331. Vu: {acc_nums}"
                )
                # Verifie le bank_statement stocke le canonique aussi
                stmt = await db.bank_statements.find_one(
                    {"copropriete_id": acp, "import_session_id": session_id},
                    {"_id": 0, "account_number": 1},
                )
                assert stmt is not None
                # account_number du statement = IBAN mappe depuis 55133100
                # (via pcmn_to_iban dans commit_journals). Doit etre IBAN sans espace.
                assert stmt["account_number"] == "BE04001952089331", (
                    f"Le statement doit utiliser l'IBAN officiel. Vu {stmt['account_number']}"
                )
            finally:
                server.get_current_user = original
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.fiscal_years.delete_one({"id": fy_id})
            await db.import_sessions.delete_one({"id": session_id})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.bank_statements.delete_many({"copropriete_id": acp})
            await db.bank_statement_lines.delete_many({"copropriete_id": acp})
            await db.bank_transactions.delete_many({"copropriete_id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 4 : commit-journals BLOQUE si le 6-char n'a AUCUN canonique dispo
# ---------------------------------------------------------------------------
def test_commit_journals_still_blocks_when_no_canonical_available():
    """iter90jk-4 : garde-fou. Si le 6-char n'a pas d'equivalent 8-char
    dans la fiche ACP, le blocage 400 doit persister (protection PCMN)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from fastapi import HTTPException
        from routes.import_wizard import create_import_wizard_router, CommitJournalsInput

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jk4-{suffix}"
        session_id = str(uuid.uuid4())
        fy_id = f"fy-jk4-{suffix}"
        try:
            # Une ACP SANS bank_accounts (ou avec un autre pcmn)
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jk4-{suffix}",
                "bank_accounts": [
                    {"id": "ba-x", "iban": "BE04001952089331",
                     "pcmn_number": "55999900",  # <-- different de 551331
                     "is_default": True},
                ],
            })
            await db.fiscal_years.insert_one({
                "id": fy_id, "copropriete_id": acp,
                "name": "2026", "start_date": "2026-01-01",
                "end_date": "2026-12-31", "status": "open",
            })
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp,
                "status": "in_progress",
            })

            router = create_import_wizard_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "").endswith("/sessions/{session_id}/commit-journals"):
                    handler = r.endpoint
                    break
            assert handler is not None

            import server
            original = server.get_current_user

            async def _fake_su(_req):
                return {"role": "superadmin", "copropriete_ids": [acp]}

            server.get_current_user = _fake_su

            class _FakeReq:
                headers = {}
                class state:
                    copropriete_id = None

            data = CommitJournalsInput(
                transactions=[{
                    "date_value": "2026-01-15",
                    "amount": 100.0,
                    "direction": "out",
                    "libelle": "Test",
                    "num_doc": "T-1",
                    "bank_account": "551331",  # <-- INCONNU dans cette ACP
                    "counterparty_account": "615000",
                }],
                bank_account_mapping={},
            )
            try:
                await handler(session_id=session_id, data=data, request=_FakeReq())
                raise AssertionError("Attendu HTTPException 400 pour 551331 inconnu")
            except HTTPException as e:
                assert e.status_code == 400
                detail = (e.detail or "").lower()
                assert "551331" in detail, (
                    f"Le message doit mentionner 551331. Vu: {e.detail}"
                )
                assert "banc" in detail or "5" in detail
            finally:
                server.get_current_user = original
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.fiscal_years.delete_one({"id": fy_id})
            await db.import_sessions.delete_one({"id": session_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
