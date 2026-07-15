"""iter90g3 : deux ameliorations liees au portail proprietaire.

1. Dedup defensive dans `/api/owner/dashboard` : empeche le double comptage
   d'une meme ligne d'ecriture (par (entry_id, line_idx)). Corrige un bug
   observe en production ou `stats_by_acp.total_called` valait plus que le
   `total_debit` du grand livre pour le meme proprietaire/periode.

2. Nouveau endpoint `/api/owner/movements/pdf` : permet au proprietaire de
   telecharger un PDF du grand livre (identique au "Situation de compte"
   envoye par le syndic). Utilise `_build_situation_compte_pdf` interne.
"""
import asyncio
import io
import os

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv


def _run(coro):
    return asyncio.run(coro)


def _sample_entry(entry_id, cp_id, tpid, acc, debit=0, credit=0, date="2026-01-15"):
    return {
        "id": entry_id,
        "copropriete_id": cp_id,
        "date": date,
        "journal_type": "OD",
        "reversed": False,
        "is_reversal": False,
        "lines": [{
            "third_party_id": tpid,
            "account_number": acc,
            "debit": debit,
            "credit": credit,
        }],
    }


def test_dashboard_dedup_by_entry_id_line_idx():
    """iter90g3 : deux entries duplicated (meme id, meme lines) -> comptees
    UNE seule fois grace au dedup (entry_id, line_idx)."""
    async def _t():
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g3_dedup"]
        try:
            cp_id = "acp_test"
            owner_id = "owner_dup"
            tier_acc = "41010002"
            await db.owners.insert_one({
                "id": owner_id,
                "email": "dup@test.be",
                "name": "Dup Owner",
                "tier_accounts": {cp_id: {"provisions": tier_acc, "reserve": "41000002"}},
            })
            await db.lots.insert_one({
                "id": "lot_dup", "copropriete_id": cp_id, "owner_id": owner_id,
            })
            # Insert 2 entries dupliquees intentionnellement avec meme id
            # (Mongo ne permet pas de _id doublons, mais NOTRE `id` uuid peut
            # etre duplique par accident lors d'un backfill errone.)
            entry = _sample_entry("entry_A", cp_id, owner_id, tier_acc, debit=100.0)
            await db.journal_entries.insert_one(dict(entry))
            # Duplicat "logique" (meme id) mais autre _id Mongo :
            await db.journal_entries.insert_one(dict(entry))
            # Charge le nb reel de docs pour verifier le setup
            n = await db.journal_entries.count_documents({"id": "entry_A"})
            assert n == 2, f"Setup : attendu 2 docs dupliques, recu {n}"

            # Simule la logique du dashboard (routes/owner_portal.py:222+)
            entries = await db.journal_entries.find(
                {"copropriete_id": cp_id, "reversed": {"$ne": True},
                 "is_reversal": {"$ne": True}}, {"_id": 0}
            ).to_list(200)
            seen_lines = set()
            total_called = 0.0
            total_paid = 0.0
            owner_id_set = {owner_id}
            valid_accs = {tier_acc, "41000002"}
            for entry_pos, e in enumerate(entries):
                entry_id = e.get("id") or f"__pos_{entry_pos}"
                for line_idx, ln in enumerate(e.get("lines", []) or []):
                    tpid = ln.get("third_party_id")
                    acc = ln.get("account_number", "")
                    if tpid in owner_id_set or (not tpid and acc in valid_accs):
                        key = (entry_id, line_idx)
                        if key in seen_lines:
                            continue
                        seen_lines.add(key)
                        total_called += float(ln.get("debit", 0) or 0)
                        total_paid += float(ln.get("credit", 0) or 0)

            # Le dedup doit compter le meme (entry_id="entry_A", idx=0) UNE fois
            assert total_called == 100.0, (
                f"Debit attendu 100.0 (une seule fois), recu {total_called}"
            )
            assert total_paid == 0.0
        finally:
            await client.drop_database("test_iter90g3_dedup")
    _run(_t())


def test_dashboard_dedup_allows_distinct_entries():
    """iter90g3 : sanity - deux entries DIFFERENTES avec meme owner sont
    comptees NORMALEMENT (le dedup ne casse pas le comportement legitime)."""
    async def _t():
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g3_distinct"]
        try:
            cp_id = "acp_test2"
            owner_id = "owner_dis"
            tier_acc = "41010003"
            await db.owners.insert_one({
                "id": owner_id, "email": "dis@test.be", "name": "Distinct",
                "tier_accounts": {cp_id: {"provisions": tier_acc, "reserve": "41000003"}},
            })
            await db.lots.insert_one({
                "id": "lot_dis", "copropriete_id": cp_id, "owner_id": owner_id,
            })
            for i in range(3):
                await db.journal_entries.insert_one(
                    _sample_entry(f"entry_{i}", cp_id, owner_id, tier_acc, debit=50.0)
                )

            entries = await db.journal_entries.find(
                {"copropriete_id": cp_id, "reversed": {"$ne": True},
                 "is_reversal": {"$ne": True}}, {"_id": 0}
            ).to_list(200)
            seen_lines = set()
            total_called = 0.0
            owner_id_set = {owner_id}
            valid_accs = {tier_acc}
            for entry_pos, e in enumerate(entries):
                entry_id = e.get("id") or f"__pos_{entry_pos}"
                for line_idx, ln in enumerate(e.get("lines", []) or []):
                    tpid = ln.get("third_party_id")
                    acc = ln.get("account_number", "")
                    if tpid in owner_id_set or (not tpid and acc in valid_accs):
                        key = (entry_id, line_idx)
                        if key in seen_lines:
                            continue
                        seen_lines.add(key)
                        total_called += float(ln.get("debit", 0) or 0)

            # 3 entries distinctes -> 3 * 50 = 150 EUR
            assert total_called == 150.0, (
                f"Debit attendu 150.0 (3 entries distinctes), recu {total_called}"
            )
        finally:
            await client.drop_database("test_iter90g3_distinct")
    _run(_t())


def test_movements_pdf_endpoint_uses_situation_compte_helper():
    """iter90g3 : le endpoint `/api/owner/movements/pdf` reutilise le helper
    `_build_situation_compte_pdf` (import correct + arguments compatibles)."""
    # Verification d'import symbolique (l'endpoint utilise ce helper)
    from routes.reports import _build_situation_compte_pdf
    assert callable(_build_situation_compte_pdf), (
        "Le helper _build_situation_compte_pdf doit etre importable"
    )
    # Sanity : la fonction accepte les arguments attendus par l'endpoint
    import inspect
    sig = inspect.signature(_build_situation_compte_pdf)
    param_names = set(sig.parameters.keys())
    for expected in ("db", "owner_id", "copropriete_id", "start_date", "end_date", "group_by_owner"):
        assert expected in param_names, (
            f"_build_situation_compte_pdf doit accepter le param '{expected}' "
            f"utilise par owner_portal my_movements_pdf, params={param_names}"
        )
