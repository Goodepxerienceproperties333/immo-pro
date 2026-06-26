"""Regression test - iter77 - Lettrage en lot (N transactions -> 1 facture).

Implementation : nouveau endpoint POST /api/banking/lettrage-batch qui accepte
une liste de transaction_ids et les lettre toutes a une SEULE facture. La facture
est marquee 'paid' si la somme atteint le total TVAC (a 0.01 EUR pres), sinon
'partially_paid'. Un lettrage_code commun est assigne aux txns du groupe pour
les tracer.

Le delettrage individuel (POST /unlettrage/{id}) recalcule maintenant le statut
de la facture en fonction des txns restantes (pas un simple unpaid).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _setup_invoice_and_txns(db, invoice_amount=300.00, txn_amounts=(100, 100, 100)):
    cid = f"itr77-{uuid.uuid4()}"
    inv_id = f"inv-{uuid.uuid4()}"
    stmt_id = f"stmt-{uuid.uuid4()}"
    txn_ids = [f"txn-{uuid.uuid4()}" for _ in txn_amounts]

    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER77", "reference": "TEST-ITER77", "status": "active",
    })
    await db.invoices.insert_one({
        "id": inv_id, "number": "F-001", "supplier": "Engie", "date": "2026-01-15",
        "total_amount": invoice_amount, "status": "unpaid",
        "copropriete_id": cid,
    })
    await db.bank_statements.insert_one({
        "id": stmt_id, "number": "REL-001", "date": "2026-01-31",
        "account_number": "5500", "status": "draft",
        "copropriete_id": cid,
    })
    for i, amt in enumerate(txn_amounts):
        await db.bank_transactions.insert_one({
            "id": txn_ids[i],
            "statement_id": stmt_id,
            "date": "2026-01-20",
            "amount": amt,  # credit
            "counterparty_name": "ENGIE",
            "communication": f"Paiement partiel {i+1}",
            "transaction_type": "credit",
            "account_number": "5500",
            "matched": False, "matched_to": "", "match_type": "",
            "copropriete_id": cid,
        })

    return cid, inv_id, stmt_id, txn_ids


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


async def _test_batch_solde_exact():
    """3 txns de 100 EUR vers facture 300 EUR -> status=paid, lettrage_code unique."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, inv_id, stmt_id, txn_ids = await _setup_invoice_and_txns(db, 300.0, (100, 100, 100))
    try:
        from routes.banking import create_banking_router
        router = create_banking_router(db)
        fn = _get_endpoint(router, "/api/banking/lettrage-batch")
        assert fn is not None, "endpoint introuvable"
        from routes.banking import LettrageBatchInput
        payload = LettrageBatchInput(transaction_ids=txn_ids, match_to_id=inv_id)
        res = await fn(data=payload)
        assert res["status"] == "paid", f"status attendu paid, recu {res['status']}"
        assert abs(res["total_paid"] - 300.00) < 0.01
        assert abs(res["invoice_amount"] - 300.00) < 0.01
        assert abs(res["remaining"]) < 0.01
        assert len(res["lettrage_code"]) >= 6

        # Verifier que toutes les txns ont matched=True + meme lettrage_code
        codes = set()
        for tid in txn_ids:
            t = await db.bank_transactions.find_one({"id": tid}, {"_id": 0})
            assert t["matched"] is True
            assert t["matched_to"] == inv_id
            assert t["match_type"] == "invoice"
            codes.add(t.get("lettrage_code"))
        assert len(codes) == 1, f"Codes incoherents : {codes}"

        # Verifier que la facture est 'paid' avec amount_paid + paid_by_transaction_ids
        inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert inv["status"] == "paid"
        assert abs(inv["amount_paid"] - 300.00) < 0.01
        assert set(inv["paid_by_transaction_ids"]) == set(txn_ids)
    finally:
        await _cleanup(db, cid)


async def _test_batch_partiel():
    """2 txns de 100 EUR vers facture 300 EUR -> status=partially_paid, remaining=100."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, inv_id, stmt_id, txn_ids = await _setup_invoice_and_txns(db, 300.0, (100, 100))
    try:
        from routes.banking import create_banking_router, LettrageBatchInput
        router = create_banking_router(db)
        fn = _get_endpoint(router, "/api/banking/lettrage-batch")
        payload = LettrageBatchInput(transaction_ids=txn_ids, match_to_id=inv_id)
        res = await fn(data=payload)
        assert res["status"] == "partially_paid"
        assert abs(res["total_paid"] - 200.00) < 0.01
        assert abs(res["remaining"] - 100.00) < 0.01
        inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert inv["status"] == "partially_paid"
        assert abs(inv["amount_paid"] - 200.00) < 0.01
    finally:
        await _cleanup(db, cid)


async def _test_batch_overpayment_refused():
    """3 txns de 200 EUR vers facture 300 EUR -> HTTPException 400 (sur-paiement)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from fastapi import HTTPException
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, inv_id, stmt_id, txn_ids = await _setup_invoice_and_txns(db, 300.0, (200, 200, 200))
    try:
        from routes.banking import create_banking_router, LettrageBatchInput
        router = create_banking_router(db)
        fn = _get_endpoint(router, "/api/banking/lettrage-batch")
        payload = LettrageBatchInput(transaction_ids=txn_ids, match_to_id=inv_id)
        try:
            await fn(data=payload)
            raise AssertionError("Sur-paiement aurait du etre refuse")
        except HTTPException as e:
            assert e.status_code == 400
            assert "Sur" in e.detail or "sur" in e.detail.lower()
    finally:
        await _cleanup(db, cid)


async def _test_unlettrage_partial_recalc():
    """Apres lettrage en lot 3x100=300 (paid), delettrer 1 -> partially_paid 200."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid, inv_id, stmt_id, txn_ids = await _setup_invoice_and_txns(db, 300.0, (100, 100, 100))
    try:
        from routes.banking import create_banking_router, LettrageBatchInput
        router = create_banking_router(db)
        fn_batch = _get_endpoint(router, "/api/banking/lettrage-batch")
        fn_unlettrage = _get_endpoint(router, "/api/banking/unlettrage/{txn_id}")
        payload = LettrageBatchInput(transaction_ids=txn_ids, match_to_id=inv_id)
        await fn_batch(data=payload)
        # Etat initial : paid
        inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert inv["status"] == "paid"
        # Delettrer une seule txn
        await fn_unlettrage(txn_id=txn_ids[0])
        inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert inv["status"] == "partially_paid", f"Statut attendu partially_paid, recu {inv['status']}"
        assert abs(inv["amount_paid"] - 200.00) < 0.01
    finally:
        await _cleanup(db, cid)


def test_lettrage_batch_solde_exact():
    asyncio.run(_test_batch_solde_exact())


def test_lettrage_batch_partiel():
    asyncio.run(_test_batch_partiel())


def test_lettrage_batch_overpayment_refused():
    asyncio.run(_test_batch_overpayment_refused())


def test_unlettrage_recalcul_statut_facture():
    asyncio.run(_test_unlettrage_partial_recalc())
