"""iter90jd : Verrou "zero FI-499 sur txn lettree" - fix `_resolve_bank_counterpart`.

Verrouille que quand une bank_transaction est lettree a une facture, le compte
tier resolu est TOUJOURS le supplier local a l'ACP :
- Priorite au `invoice.supplier_id` (source of truth).
- Fallback : match par nom NORMALISE (_norm_name_candidates) + copropriete_id
  filter (Chinese Wall strict).
- Auto-assign du `tier_account_number` si le supplier n'en a pas.
- Le compte tier retourne est TOUJOURS un 440XXXXX (jamais 499).
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


def test_resolve_counterpart_uses_invoice_supplier_id():
    """iter90jd-1 : quand invoice.supplier_id est set, on l'utilise en priorite."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import _resolve_bank_counterpart

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jd-{suffix}"
        sup_id = f"sup-{suffix}"
        inv_id = f"inv-{suffix}"
        txn_id = f"txn-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Baloise-{suffix}",
                "copropriete_id": acp, "tier_account_number": "44000042",
            })
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": acp,
                "supplier": f"Baloise-{suffix}",
                "supplier_id": sup_id,  # <- source of truth
                "number": "F-001",
            })
            txn = {
                "id": txn_id, "copropriete_id": acp,
                "amount": -60.76, "transaction_type": "debit",
                "match_type": "invoice", "matched_to": inv_id, "matched": True,
            }
            acc, name, tp_id, inv_num = await _resolve_bank_counterpart(db, txn, acp)
            assert acc == "44000042", (
                f"Le compte tier doit etre celui du supplier (44000042), pas 499. Vu {acc}"
            )
            assert tp_id == sup_id
            assert name.startswith("Baloise")
            assert inv_num == "F-001"
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            await db.invoices.delete_one({"id": inv_id})

    _run(_go())


def test_resolve_counterpart_fallback_by_name_with_chinese_wall():
    """iter90jd-2 : quand invoice.supplier_id est absent, fallback matching par
    nom NORMALISE dans la MEME ACP (Chinese Wall strict).

    Un supplier "Baloise SA" dans une AUTRE ACP ne doit pas etre matche.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import _resolve_bank_counterpart

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-jda-{suffix}"
        acp_b = f"acp-jdb-{suffix}"
        sup_a = f"sup-a-{suffix}"
        sup_b = f"sup-b-{suffix}"
        inv_id = f"inv-{suffix}"
        try:
            # ACP-A : supplier Baloise local
            await db.suppliers.insert_one({
                "id": sup_a, "name": f"Baloise Insurance-{suffix}",
                "copropriete_id": acp_a, "tier_account_number": "44000042",
            })
            # ACP-B : supplier Baloise (autre ACP)
            await db.suppliers.insert_one({
                "id": sup_b, "name": f"Baloise Insurance-{suffix}",
                "copropriete_id": acp_b, "tier_account_number": "44000099",
            })
            # Invoice dans ACP-A avec SEULEMENT le nom (supplier_id absent)
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": acp_a,
                "supplier": f"Baloise Insurance-{suffix}",
                "number": "F-001",
            })
            txn = {
                "id": f"txn-{suffix}", "copropriete_id": acp_a,
                "amount": -60.76, "transaction_type": "debit",
                "match_type": "invoice", "matched_to": inv_id, "matched": True,
            }
            acc, _name, tp_id, _num = await _resolve_bank_counterpart(db, txn, acp_a)
            assert acc == "44000042", (
                f"Chinese Wall : doit matcher le supplier de ACP-A (44000042), pas ACP-B (44000099). Vu {acc}"
            )
            assert tp_id == sup_a, f"tp_id doit etre sup_a. Vu {tp_id}"
        finally:
            await db.suppliers.delete_many({"id": {"$in": [sup_a, sup_b]}})
            await db.invoices.delete_one({"id": inv_id})

    _run(_go())


def test_resolve_counterpart_matches_slight_name_variation():
    """iter90jd-3 : "Baloise Insurance" (invoice) doit matcher "Baloise Insurance SA"
    (supplier local) via matching normalise (particules juridiques).
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import _resolve_bank_counterpart

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jde-{suffix}"
        sup_id = f"sup-{suffix}"
        inv_id = f"inv-{suffix}"
        try:
            # Supplier avec suffixe "SA"
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"Baloise-{suffix} SA",
                "copropriete_id": acp, "tier_account_number": "44000042",
            })
            # Invoice sans le "SA"
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": acp,
                "supplier": f"Baloise-{suffix}",  # variation
                "number": "F-001",
            })
            txn = {
                "id": f"txn-{suffix}", "copropriete_id": acp,
                "amount": -60.76, "transaction_type": "debit",
                "match_type": "invoice", "matched_to": inv_id, "matched": True,
            }
            acc, _name, tp_id, _num = await _resolve_bank_counterpart(db, txn, acp)
            assert acc == "44000042", (
                f"Variation legere du nom doit matcher via _norm_name_candidates. Vu {acc}"
            )
            assert tp_id == sup_id
        finally:
            await db.suppliers.delete_one({"id": sup_id})
            await db.invoices.delete_one({"id": inv_id})

    _run(_go())
