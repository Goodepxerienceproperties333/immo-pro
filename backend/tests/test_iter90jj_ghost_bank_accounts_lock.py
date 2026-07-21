"""iter90jj : Verrouille la creation de comptes bancaires fantomes + fusion.

Trois verrous testes :
1. `_resolve_bank_account` : fallback intelligent sur `bank_accounts` de l'ACP
    (jamais "550000" harcode).
2. `_ensure_pcmn_accounts_exist` : refuse la creation d'un compte 55XXXX
    inconnu depuis un import (raise 400).
3. Script `merge_ghost_bank_accounts` : fusionne 551331/550000 vers 55133100
    sans casser les JEs existants.
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
# Verrou 1 : _resolve_bank_account n'emet plus "550000" en fallback
# ---------------------------------------------------------------------------
def test_resolve_bank_account_uses_acp_default_when_iban_absent():
    """iter90jj-1 : sans IBAN mais avec bank_accounts sur l'ACP, on prend
    le compte par defaut (is_default=True) au lieu de fallback "550000"."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import _resolve_bank_account

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jj1-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jj-{suffix}",
                "bank_accounts": [
                    {"iban": "BE04001952089331", "pcmn_number": "55133100",
                     "account_type": "vue", "label": "Compte a vue",
                     "is_default": True},
                    {"iban": "BE90034363465732", "pcmn_number": "55073200",
                     "account_type": "epargne", "label": "Compte epargne",
                     "is_default": False},
                ],
            })
            # Txn sans account_number et sans statement_id
            txn = {"id": "t-x", "copropriete_id": acp}
            acc, label = await _resolve_bank_account(db, txn, acp)
            assert acc == "55133100", (
                f"Doit fallback sur le default (55133100). Vu {acc}"
            )
            assert label == "Compte a vue"
        finally:
            await db.coproprietes.delete_one({"id": acp})

    _run(_go())


def test_resolve_bank_account_matches_iban_over_default():
    """iter90jj-2 : quand l'IBAN de la txn matche le 2e compte (epargne),
    on utilise l'epargne (55073200), pas le default (55133100)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import _resolve_bank_account

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jj2-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jj-{suffix}",
                "bank_accounts": [
                    {"iban": "BE04001952089331", "pcmn_number": "55133100",
                     "is_default": True, "label": "Vue"},
                    {"iban": "BE90034363465732", "pcmn_number": "55073200",
                     "is_default": False, "label": "Epargne"},
                ],
            })
            txn = {"id": "t-y", "copropriete_id": acp,
                   "account_number": "BE90 0343 6346 5732"}  # avec espaces
            acc, label = await _resolve_bank_account(db, txn, acp)
            assert acc == "55073200", (
                f"IBAN doit prendre priorite sur default. Vu {acc}"
            )
            assert label == "Epargne"
        finally:
            await db.coproprietes.delete_one({"id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Verrou 2 : _ensure_pcmn_accounts_exist refuse un compte 55XXXX inconnu
# ---------------------------------------------------------------------------
def test_import_refuses_ghost_bank_account_creation():
    """iter90jj-3 : un import CSV qui referme un compte 551331 (raccourci)
    doit LEVER une HTTPException 400 au lieu de creer un compte fantome."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import create_import_wizard_router
        from fastapi import HTTPException

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jj3-{suffix}"
        session_id = str(uuid.uuid4())
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jj3-{suffix}",
                "bank_accounts": [{"iban": "BE04001952089331", "pcmn_number": "55133100", "is_default": True}],
            })
            await db.fiscal_years.insert_one({
                "id": f"fy-{suffix}", "copropriete_id": acp,
                "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
            })
            await db.import_sessions.insert_one({
                "id": session_id, "copropriete_id": acp, "status": "in_progress",
            })
            router = create_import_wizard_router(db)
            handler = None
            for r in router.routes:
                p = getattr(r, "path", "")
                if p.endswith("/sessions/{session_id}/commit-journals"):
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

            # Payload : une transaction dont bank_account="551331" (compte fantome)
            from routes.import_wizard import CommitJournalsInput
            data = CommitJournalsInput(transactions=[{
                "date": "2026-01-15", "amount": 100.0, "direction": "credit",
                "description": "Test", "num_doc": "AN-001",
                "counterparty": "615000", "bank_account": "551331",  # <- fantome
            }], bank_account_mapping={})
            try:
                await handler(session_id=session_id, data=data, request=_FakeReq())
                raise AssertionError("Attendu HTTPException 400 pour compte 55 inconnu")
            except HTTPException as e:
                assert e.status_code == 400
                assert "551331" in (e.detail or "").lower() or "5" in (e.detail or "")
            finally:
                server.get_current_user = original
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.fiscal_years.delete_one({"id": f"fy-{suffix}"})
            await db.import_sessions.delete_one({"id": session_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Verrou 3 : Script merge_ghost_bank_accounts renomme les lignes JE
# ---------------------------------------------------------------------------
def test_merge_ghost_bank_accounts_rewrites_je_lines():
    """iter90jj-4 : le script fusionne les lignes JE de 551331 vers 55133100."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.merge_ghost_bank_accounts import _run as _merge
        import argparse

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jj4-{suffix}"
        je_id = f"je-{suffix}"
        try:
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()), "number": "551331",
                "name": "Compte fantome", "class_num": 5, "copropriete_id": acp,
            })
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": acp,
                "journal_type": "FI", "date": "2026-01-15",
                "reference": "F-001",
                "lines": [
                    {"account_number": "551331", "debit": 100.0, "credit": 0.0},
                    {"account_number": "44000042", "debit": 0.0, "credit": 100.0},
                ],
                "total_debit": 100.0, "total_credit": 100.0,
            })
            args = argparse.Namespace(execute=True, copropriete_id=acp,
                                     mapping="551331:55133100")
            await _merge(args)
            je_fresh = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            lines = je_fresh.get("lines", [])
            assert lines[0]["account_number"] == "55133100", (
                f"La ligne 551331 doit etre reecrite en 55133100. Vu {lines[0]['account_number']}"
            )
            # PCMN fantome supprime
            pcmn = await db.pcmn_accounts.find_one({"copropriete_id": acp, "number": "551331"})
            assert pcmn is None, "Le compte PCMN fantome 551331 doit etre supprime"
        finally:
            await db.journal_entries.delete_many({"copropriete_id": acp})
            await db.pcmn_accounts.delete_many({"copropriete_id": acp})

    _run(_go())
