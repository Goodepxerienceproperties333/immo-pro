"""Regression test - iter80 - Lettrage 1 transaction -> N factures.

Cas utilisateur : "je dois pouvoir sélectionner 2 factures permettant d'arriver
au montant du paiement". Ex: virement -1144.29 EUR couvre 2 factures :
- F1 (210 EUR) + F2 (934.29 EUR) = 1144.29 EUR
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _setup_txn_and_invoices(db, txn_amount=1144.29, invoice_amounts=(210, 934.29)):
    cid = f"itr80-{uuid.uuid4()}"
    stmt_id = f"stmt-{uuid.uuid4()}"
    txn_id = f"txn-{uuid.uuid4()}"
    inv_ids = [f"inv-{uuid.uuid4()}" for _ in invoice_amounts]
    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER80", "reference": "TEST-ITER80", "status": "active",
    })
    await db.bank_statements.insert_one({
        "id": stmt_id, "number": "REL", "date": "2026-03-07",
        "status": "draft", "copropriete_id": cid,
    })
    await db.bank_transactions.insert_one({
        "id": txn_id, "statement_id": stmt_id, "date": "2026-03-07",
        "amount": -txn_amount,  # debit
        "counterparty_name": "SRL Finlead",
        "communication": "260107",
        "transaction_type": "debit",
        "account_number": "5500",
        "matched": False, "matched_to": "", "match_type": "",
        "copropriete_id": cid,
    })
    for i, amt in enumerate(invoice_amounts):
        await db.invoices.insert_one({
            "id": inv_ids[i], "number": f"260{107+i}", "supplier": "SRL Finlead",
            "date": "2026-03-02", "total_amount": amt, "status": "unpaid",
            "copropriete_id": cid,
        })
    return cid, txn_id, inv_ids


async def _cleanup(db, cid):
    await db.coproprietes.delete_many({"id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.bank_statements.delete_many({"copropriete_id": cid})
    await db.bank_transactions.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def _get_endpoint(router, path):
    for r in router.routes:
        if r.path == path:
            return r.endpoint
    return None


async def _test_multi_invoices_exact_match():
    """txn -1144.29 -> [F1 210, F2 934.29] = 1144.29 -> is_exact=True"""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.banking import create_banking_router, LettrageMultiInvoicesInput
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, txn_id, inv_ids = await _setup_txn_and_invoices(db, 1144.29, (210, 934.29))
    try:
        router = create_banking_router(db)
        fn = _get_endpoint(router, "/api/banking/lettrage-multi-invoices")
        assert fn is not None
        payload = LettrageMultiInvoicesInput(transaction_id=txn_id, invoice_ids=inv_ids)
        res = await fn(data=payload)
        assert res["is_exact"] is True
        assert abs(res["transaction_amount"] - 1144.29) < 0.01
        assert abs(res["invoice_total"] - 1144.29) < 0.01
        assert abs(res["remaining"]) < 0.01
        # Toutes les factures sont paid + meme lettrage_code
        codes = set()
        for iid in inv_ids:
            inv = await db.invoices.find_one({"id": iid}, {"_id": 0})
            assert inv["status"] == "paid"
            assert inv["paid_by_transaction_id"] == txn_id
            codes.add(inv["lettrage_code"])
        assert len(codes) == 1, f"lettrage_codes incoherents: {codes}"
        # Transaction marquee avec matched_to_ids
        txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        assert txn["matched"] is True
        assert txn["match_type"] == "multi_invoice"
        assert set(txn["matched_to_ids"]) == set(inv_ids)
    finally:
        await _cleanup(db, cid)


async def _test_multi_invoices_partial_match():
    """txn 500 -> [F1 200, F2 200] = 400 -> is_exact=False, remaining=100."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.banking import create_banking_router, LettrageMultiInvoicesInput
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, txn_id, inv_ids = await _setup_txn_and_invoices(db, 500.00, (200, 200))
    try:
        router = create_banking_router(db)
        fn = _get_endpoint(router, "/api/banking/lettrage-multi-invoices")
        payload = LettrageMultiInvoicesInput(transaction_id=txn_id, invoice_ids=inv_ids)
        res = await fn(data=payload)
        assert res["is_exact"] is False
        assert abs(res["remaining"] - 100.00) < 0.01
        # Factures payees malgre l'ecart (l'excedent ira sur compte tiers)
        for iid in inv_ids:
            inv = await db.invoices.find_one({"id": iid}, {"_id": 0})
            assert inv["status"] == "paid"
    finally:
        await _cleanup(db, cid)


def test_multi_invoices_exact_match():
    asyncio.run(_test_multi_invoices_exact_match())


def test_multi_invoices_partial_match():
    asyncio.run(_test_multi_invoices_partial_match())
