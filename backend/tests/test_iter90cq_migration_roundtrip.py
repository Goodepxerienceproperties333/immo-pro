"""
iter90cq : Test end-to-end du round-trip export/import ACP.

Verifie que :
1. Une ACP creee avec ses donnees liees (owners, lots, budget, fund_calls,
   journal_entries, mutations) est exportee correctement vers JSON.
2. Apres suppression complete de l'ACP, l'import restaure fidelement toutes
   les donnees.
3. Les comptes des documents restaures sont identiques a l'original.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


BACKEND_DIR = "/app/backend"


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_acp(prefix: str):
    """Cree un ACP complet avec donnees minimales dans chaque collection scoped."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = str(uuid.uuid4())
    lot_id = str(uuid.uuid4())
    owner1_id = str(uuid.uuid4())
    owner2_id = str(uuid.uuid4())
    budget_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    je_id = str(uuid.uuid4())
    mut_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    await db.coproprietes.insert_one({
        "id": cid, "name": f"{prefix}-ACP", "reference": prefix[:15],
        "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2025", "start_date": "2025-01-01",
        "end_date": "2025-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": owner1_id, "name": "Alice", "last_name": "Test",
         "auxiliary_code": "A001", "copropriete_ids": [cid]},
        {"id": owner2_id, "name": "Bob", "last_name": "Test",
         "auxiliary_code": "B001", "copropriete_ids": [cid]},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "1", "owner_id": owner1_id,
        "owner_ids": [owner1_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "1"}],
    })
    await db.budgets.insert_one({
        "id": budget_id, "name": "Budget test", "fiscal_year_id": fy_id,
        "copropriete_id": cid, "status": "approved",
        "lines": [{"account_number": "6", "amount": 1000.0}],
    })
    await db.fund_calls.insert_one({
        "id": call_id, "name": "Q1", "copropriete_id": cid,
        "date": "2025-01-01", "total_amount": 1000.0,
    })
    await db.journal_entries.insert_one({
        "id": je_id, "reference": "TEST-1", "copropriete_id": cid,
        "date": "2025-01-01", "journal_type": "VE",
        "lines": [], "total_debit": 1000.0, "total_credit": 1000.0,
    })
    await db.mutations.insert_one({
        "id": mut_id, "copropriete_id": cid, "lot_id": lot_id,
        "sale_date": "2025-06-01", "from_owner_id": owner1_id,
        "to_owner_id": owner2_id, "sale_price": 100000.0,
    })
    await db.bank_accounts.insert_one({
        "id": str(uuid.uuid4()), "copropriete_id": cid,
        "iban": "BE00 0000 0000 0000", "name": "Compte test",
    })
    await db.suppliers.insert_one({
        "id": str(uuid.uuid4()), "copropriete_id": cid,
        "name": "Fournisseur test", "bce": "BE0123.456.789",
    })

    return {
        "db": db, "cid": cid,
        "owner1": owner1_id, "owner2": owner2_id,
        "counts_expected": {
            "coproprietes": 1, "fiscal_years": 1, "pcmn_accounts": 3,
            "owners": 2, "lots": 1, "distribution_keys": 1,
            "budgets": 1, "fund_calls": 1, "journal_entries": 1,
            "mutations": 1, "bank_accounts": 1, "suppliers": 1,
        },
    }


async def _cleanup_full(cid: str, owner_ids: list):
    db = await _mongo()
    await db.coproprietes.delete_one({"id": cid})
    all_colls = [
        "fiscal_years", "pcmn_accounts", "distribution_keys",
        "lots", "mutations",
        "budgets", "fund_calls", "journal_entries",
        "bank_accounts", "bank_transactions", "bank_statements", "bank_statement_lines",
        "invoices", "invoice_templates", "invoice_bundle_sessions", "suppliers",
        "documents", "document_categories", "expense_categories",
        "meters", "meter_readings",
        "ag_meetings", "legal_documents", "legal_document_history", "legal_rgpd_register",
        "owner_notifications", "owner_access_audit",
        "syndic_configs", "release_notes_ack", "audit_log",
    ]
    for coll in all_colls:
        try:
            await db[coll].delete_many({"copropriete_id": cid})
        except Exception:
            pass
    await db.owners.delete_many({"id": {"$in": owner_ids}})


async def _count_scoped(cid: str, coll: str):
    db = await _mongo()
    return await db[coll].count_documents({"copropriete_id": cid})


async def _test_roundtrip():
    ctx = await _setup_acp("iter90cq-roundtrip")
    cid = ctx["cid"]
    owner_ids = [ctx["owner1"], ctx["owner2"]]
    expected = ctx["counts_expected"]

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    export_path = tmp.name

    try:
        # --- 1. Export
        result = subprocess.run(
            [sys.executable, "scripts/export_acp_prod.py",
             "--copropriete-id", cid, "--out", export_path],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"Export failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert os.path.getsize(export_path) > 0, "Export file empty"

        # Verifie contenu JSON
        with open(export_path) as f:
            payload = json.load(f)
        assert payload["meta"]["copropriete_id"] == cid
        assert len(payload["coproprietes"]) == 1
        assert len(payload["owners"]) == expected["owners"]
        for coll_name, exp_count in expected.items():
            if coll_name in ("coproprietes", "owners"):
                continue
            got = len(payload.get(coll_name) or [])
            assert got == exp_count, (
                f"Export mismatch {coll_name}: expected {exp_count}, got {got}"
            )

        # --- 2. Suppression totale ACP
        await _cleanup_full(cid, owner_ids)
        # Verifie que tout est vide
        assert await _count_scoped(cid, "lots") == 0
        assert await _count_scoped(cid, "fund_calls") == 0

        # --- 3. Import (avec --commit)
        result = subprocess.run(
            [sys.executable, "scripts/import_acp_preview.py",
             "--in", export_path, "--commit"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"Import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )

        # --- 4. Verifie que tout est restaure
        db = await _mongo()
        acp_after = await db.coproprietes.find_one({"id": cid}, {"_id": 0})
        assert acp_after, "ACP not restored"

        for coll_name, exp_count in expected.items():
            if coll_name == "coproprietes":
                continue
            if coll_name == "owners":
                # Owners restored via copropriete_ids
                got = await db.owners.count_documents({"copropriete_ids": cid})
            else:
                got = await _count_scoped(cid, coll_name)
            assert got == exp_count, (
                f"Import mismatch {coll_name}: expected {exp_count}, got {got}"
            )
    finally:
        try:
            os.unlink(export_path)
        except Exception:
            pass
        await _cleanup_full(cid, owner_ids)


def test_export_import_roundtrip():
    asyncio.run(_test_roundtrip())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
