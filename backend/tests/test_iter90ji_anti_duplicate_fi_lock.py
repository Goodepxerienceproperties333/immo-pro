"""iter90ji : verrou anti-doublon FI auto vs FI importe.

Verrouille que `generate_bank_entry` NE cree PAS de nouveau JE FI quand un
JE FI importe (via wizard Optipro) existe deja pour la meme signature :
- meme copropriete_id
- meme date
- meme montant total
- au moins un compte tier commun

Dans ce cas, la bank_transaction est simplement liee au JE existant via
`matched_je_id` (audit trail).
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


def test_generate_bank_entry_skips_creation_if_imported_je_exists():
    """iter90ji-1 : bank_transaction lettree a une facture qui a deja un JE FI
    importe -> generate_bank_entry NE CREE PAS de nouveau doublon, il lie
    juste la txn au JE existant.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_bank_entry

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-ji-{suffix}"
        sup_id = f"sup-{suffix}"
        inv_id = f"inv-{suffix}"
        txn_id = f"txn-{suffix}"
        imported_je_id = f"je-imp-{suffix}"

        # Setup : ACP + supplier + invoice + JE FI importe deja present
        await db.coproprietes.insert_one({
            "id": acp, "name": f"iter90ji-{suffix}",
            "bank_accounts": [{"iban": "BE04001952089331", "account_type": "vue", "is_default": True, "pcmn_number": "550000"}],
        })
        await db.suppliers.insert_one({
            "id": sup_id, "name": f"Baloise-{suffix}",
            "copropriete_id": acp, "tier_account_number": "44000042",
        })
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": acp,
            "supplier": f"Baloise-{suffix}", "supplier_id": sup_id,
            "number": "F-001", "date": "2026-01-15",
        })
        # JE FI importe : Dr 44000042 60.76 / Cr 550000 60.76
        await db.journal_entries.insert_one({
            "id": imported_je_id, "journal_type": "FI",
            "date": "2026-01-15",
            "reference": "OPT-FI-001",
            "description": "Paiement Baloise (Optipro import)",
            "lines": [
                {"account_number": "44000042", "account_name": f"Baloise-{suffix}",
                 "debit": 60.76, "credit": 0.0, "third_party_id": sup_id, "third_party_type": "supplier"},
                {"account_number": "550000", "account_name": "Banque",
                 "debit": 0.0, "credit": 60.76},
            ],
            "total_debit": 60.76, "total_credit": 60.76,
            "copropriete_id": acp,
            "import_session_id": f"ses-{suffix}",  # <- marqueur "importe Optipro"
        })
        # bank_transaction lettree a la facture
        txn = {
            "id": txn_id, "copropriete_id": acp,
            "date": "2026-01-15", "amount": -60.76, "transaction_type": "debit",
            "account_number": "BE04001952089331",
            "matched": True, "match_type": "invoice", "matched_to": inv_id,
        }
        await db.bank_transactions.insert_one(txn)

        try:
            # ACT : appelle generate_bank_entry
            result = await generate_bank_entry(db, txn)

            # ASSERT 1 : le resultat pointe vers le JE importe (pas un nouveau)
            assert result["id"] == imported_je_id, (
                f"generate_bank_entry doit retourner le JE importe existant. Vu id={result.get('id')}"
            )

            # ASSERT 2 : la txn est liee au JE importe via matched_je_id
            txn_fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            assert txn_fresh.get("matched_je_id") == imported_je_id, (
                f"txn.matched_je_id doit pointer vers le JE importe. Vu {txn_fresh.get('matched_je_id')}"
            )
            assert txn_fresh.get("matched_je_source") == "imported", (
                f"txn.matched_je_source doit etre 'imported'. Vu {txn_fresh.get('matched_je_source')}"
            )

            # ASSERT 3 : AUCUN nouveau JE FI cree (0 auto_generated pour cette ACP + date + montant)
            new_autos = await db.journal_entries.count_documents({
                "copropriete_id": acp, "journal_type": "FI",
                "auto_generated": True, "date": "2026-01-15", "total_debit": 60.76,
                "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            })
            assert new_autos == 0, (
                f"Aucun JE FI auto ne doit etre cree (verrou anti-doublon). Vu {new_autos}"
            )
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.suppliers.delete_one({"id": sup_id})
            await db.invoices.delete_one({"id": inv_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())


def test_generate_bank_entry_creates_je_when_no_imported_pair():
    """iter90ji-2 : bank_transaction lettree SANS pair importe -> le JE FI
    auto EST bien cree (comportement standard preserve).
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_bank_entry

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-ji2-{suffix}"
        sup_id = f"sup-{suffix}"
        inv_id = f"inv-{suffix}"
        txn_id = f"txn-{suffix}"

        await db.coproprietes.insert_one({
            "id": acp, "name": f"iter90ji2-{suffix}",
            "bank_accounts": [{"iban": "BE04001952089331", "account_type": "vue", "is_default": True, "pcmn_number": "550000"}],
        })
        await db.suppliers.insert_one({
            "id": sup_id, "name": f"Engie-{suffix}",
            "copropriete_id": acp, "tier_account_number": "44000099",
        })
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": acp,
            "supplier": f"Engie-{suffix}", "supplier_id": sup_id,
            "number": "F-002", "date": "2026-02-15",
        })
        txn = {
            "id": txn_id, "copropriete_id": acp,
            "date": "2026-02-15", "amount": -155.00, "transaction_type": "debit",
            "account_number": "BE04001952089331",
            "matched": True, "match_type": "invoice", "matched_to": inv_id,
        }
        await db.bank_transactions.insert_one(txn)

        try:
            result = await generate_bank_entry(db, txn)
            assert result is not None
            # Le JE cree doit etre auto_generated=True (nouveau, pas un pair importe)
            new_je = await db.journal_entries.find_one(
                {"id": result["id"]}, {"_id": 0},
            )
            assert new_je.get("auto_generated") is True
            # Compte tier attendu = 44000099 (Engie)
            tier_lines = [ln for ln in new_je.get("lines", []) if ln.get("account_number", "").startswith("440")]
            assert len(tier_lines) == 1
            assert tier_lines[0]["account_number"] == "44000099"
            # txn liee au nouveau JE
            txn_fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            assert txn_fresh.get("matched_je_id") == result["id"]
            assert txn_fresh.get("matched_je_source") == "auto"
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.suppliers.delete_one({"id": sup_id})
            await db.invoices.delete_one({"id": inv_id})
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())


def test_cleanup_script_reverses_only_auto_duplicates():
    """iter90ji-3 : le script cleanup_duplicate_auto_fi contre-passe UNIQUEMENT
    les JE FI auto qui ont un pair importe. Le JE importe est preserve.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-ji3-{suffix}"
        # 2 JEs FI meme signature (44000042 / 60.76 / 2026-01-15) :
        # 1 importe (Optipro) + 1 auto-genere
        imp_id = f"je-imp-{suffix}"
        auto_id = f"je-auto-{suffix}"
        try:
            await db.journal_entries.insert_many([
                {"id": imp_id, "journal_type": "FI", "date": "2026-01-15",
                 "reference": "OPT-001", "copropriete_id": acp,
                 "total_debit": 60.76, "total_credit": 60.76,
                 "import_session_id": f"ses-{suffix}",
                 "lines": [
                     {"account_number": "44000042", "debit": 60.76, "credit": 0.0},
                     {"account_number": "550000", "debit": 0.0, "credit": 60.76},
                 ]},
                {"id": auto_id, "journal_type": "FI", "date": "2026-01-15",
                 "reference": f"FI-{suffix}", "copropriete_id": acp,
                 "total_debit": 60.76, "total_credit": 60.76,
                 "auto_generated": True,
                 "source_type": "bank_txn", "source_id": f"txn-{suffix}",
                 "lines": [
                     {"account_number": "44000042", "debit": 60.76, "credit": 0.0},
                     {"account_number": "550000", "debit": 0.0, "credit": 60.76},
                 ]},
            ])
            # Import du script en dry-run
            from scripts.cleanup_duplicate_auto_fi import _run as _cleanup_run
            import argparse
            args = argparse.Namespace(execute=True, copropriete_id=acp)
            await _cleanup_run(args)
            # Verifs : imp reste actif, auto est reversed
            imp = await db.journal_entries.find_one({"id": imp_id}, {"_id": 0})
            auto = await db.journal_entries.find_one({"id": auto_id}, {"_id": 0})
            assert not imp.get("reversed"), "Le JE importe NE doit PAS etre contre-passe"
            assert auto.get("reversed"), "Le JE auto doit etre contre-passe"
        finally:
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())
