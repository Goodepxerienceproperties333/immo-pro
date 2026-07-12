"""iter90eb : Endpoint GET /banking/transactions/{id}/lettered-links.

Retourne les cibles (factures/owner/supplier) deja lettrees a une transaction
bancaire pour eviter des re-lettrages incorrects. Affichage dans l'en-tete
du dialog de lettrage.
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_unmatched_transaction_returns_empty_links():
    """Une transaction NON lettree renvoie matched=false + arrays vides."""
    async def _run():
        db = await _mongo()
        txn_id = f"iter90eb-txn-{uuid.uuid4()}"
        cid = f"iter90eb-cid-{uuid.uuid4()}"
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2026-04-07", "amount": -155.0,
            "counterparty_name": "Finlead srl",
            "matched": False,
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.get(
                    f"{BACKEND_URL}/api/banking/transactions/{txn_id}/lettered-links",
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["transaction_id"] == txn_id
                assert data["matched"] is False
                assert data["invoices"] == []
                assert data["owner"] is None
                assert data["supplier"] is None
                assert data["sibling_transactions"] == []
        finally:
            await db.bank_transactions.delete_one({"id": txn_id})

    asyncio.run(_run())


def test_invoice_lettrage_returns_invoice_details():
    """Une transaction lettree a une facture retourne la facture."""
    async def _run():
        db = await _mongo()
        txn_id = f"iter90eb-txn-{uuid.uuid4()}"
        inv_id = f"iter90eb-inv-{uuid.uuid4()}"
        cid = f"iter90eb-cid-{uuid.uuid4()}"
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": cid,
            "number": "V-260107", "supplier": "Finlead",
            "date": "2026-03-02", "total_amount": 1144.29,
            "status": "paid", "amount_paid": 1144.29,
        })
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2026-03-05", "amount": -1144.29,
            "counterparty_name": "Finlead srl",
            "matched": True, "matched_to": inv_id, "match_type": "invoice",
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.get(
                    f"{BACKEND_URL}/api/banking/transactions/{txn_id}/lettered-links",
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["matched"] is True
                assert data["match_type"] == "invoice"
                assert len(data["invoices"]) == 1
                inv = data["invoices"][0]
                assert inv["number"] == "V-260107"
                assert inv["supplier"] == "Finlead"
                assert inv["total_amount"] == 1144.29
                assert inv["status"] == "paid"
        finally:
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.invoices.delete_one({"id": inv_id})

    asyncio.run(_run())


def test_multi_invoice_returns_all_invoices():
    """Une transaction en multi_invoice retourne les N factures."""
    async def _run():
        db = await _mongo()
        txn_id = f"iter90eb-txn-{uuid.uuid4()}"
        inv1_id = f"iter90eb-inv1-{uuid.uuid4()}"
        inv2_id = f"iter90eb-inv2-{uuid.uuid4()}"
        cid = f"iter90eb-cid-{uuid.uuid4()}"
        await db.invoices.insert_many([
            {"id": inv1_id, "copropriete_id": cid,
             "number": "V-260114", "supplier": "Finlead",
             "date": "2026-02-05", "total_amount": 30.0, "status": "paid"},
            {"id": inv2_id, "copropriete_id": cid,
             "number": "V-260206", "supplier": "Finlead",
             "date": "2026-03-23", "total_amount": 155.0, "status": "paid"},
        ])
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2026-04-07", "amount": -185.0,
            "counterparty_name": "Finlead srl",
            "matched": True, "matched_to": inv1_id,
            "matched_to_ids": [inv1_id, inv2_id],
            "match_type": "multi_invoice",
            "lettrage_code": "ABC12345",
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.get(
                    f"{BACKEND_URL}/api/banking/transactions/{txn_id}/lettered-links",
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["match_type"] == "multi_invoice"
                assert data["lettrage_code"] == "ABC12345"
                assert len(data["invoices"]) == 2
                numbers = sorted(i["number"] for i in data["invoices"])
                assert numbers == ["V-260114", "V-260206"]
        finally:
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.invoices.delete_many({"id": {"$in": [inv1_id, inv2_id]}})

    asyncio.run(_run())


def test_owner_payment_returns_owner_info():
    """Un lettrage owner_payment retourne l'owner + vcs."""
    async def _run():
        db = await _mongo()
        txn_id = f"iter90eb-txn-{uuid.uuid4()}"
        owner_id = f"iter90eb-o-{uuid.uuid4()}"
        cid = f"iter90eb-cid-{uuid.uuid4()}"
        await db.owners.insert_one({
            "id": owner_id, "name": "Matexi Group",
            "vcs_code": "+++987/6543/21012+++", "email": "matexi@x.be",
        })
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2026-04-01", "amount": 5000.0,
            "matched": True, "matched_to": owner_id, "match_type": "owner_payment",
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.get(
                    f"{BACKEND_URL}/api/banking/transactions/{txn_id}/lettered-links",
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["match_type"] == "owner_payment"
                assert data["owner"] is not None
                assert data["owner"]["name"] == "Matexi Group"
                assert data["owner"]["vcs_code"] == "+++987/6543/21012+++"
        finally:
            await db.bank_transactions.delete_one({"id": txn_id})
            await db.owners.delete_one({"id": owner_id})

    asyncio.run(_run())


def test_batch_lettrage_returns_sibling_transactions():
    """N transactions -> 1 facture partagent un lettrage_code -> siblings."""
    async def _run():
        db = await _mongo()
        code = "BATCH123"
        cid = f"iter90eb-cid-{uuid.uuid4()}"
        inv_id = f"iter90eb-inv-{uuid.uuid4()}"
        txn_a = f"iter90eb-a-{uuid.uuid4()}"
        txn_b = f"iter90eb-b-{uuid.uuid4()}"
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": cid,
            "number": "V-300", "supplier": "Test",
            "total_amount": 1000.0, "status": "paid",
        })
        await db.bank_transactions.insert_many([
            {"id": txn_a, "copropriete_id": cid,
             "date": "2026-04-01", "amount": -500.0,
             "counterparty_name": "Test",
             "matched": True, "matched_to": inv_id, "match_type": "invoice",
             "lettrage_code": code},
            {"id": txn_b, "copropriete_id": cid,
             "date": "2026-04-15", "amount": -500.0,
             "counterparty_name": "Test",
             "matched": True, "matched_to": inv_id, "match_type": "invoice",
             "lettrage_code": code},
        ])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.get(
                    f"{BACKEND_URL}/api/banking/transactions/{txn_a}/lettered-links",
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["lettrage_code"] == code
                assert len(data["invoices"]) == 1
                assert data["invoices"][0]["number"] == "V-300"
                # Sibling = txn_b
                assert len(data["sibling_transactions"]) == 1
                assert data["sibling_transactions"][0]["id"] == txn_b
                assert data["sibling_transactions"][0]["amount"] == -500.0
        finally:
            await db.bank_transactions.delete_many({"id": {"$in": [txn_a, txn_b]}})
            await db.invoices.delete_one({"id": inv_id})

    asyncio.run(_run())


def test_transaction_not_found_returns_404():
    """Une transaction inexistante retourne 404."""
    async def _run():
        async with httpx.AsyncClient() as client:
            hdr = await _login(client)
            r = await client.get(
                f"{BACKEND_URL}/api/banking/transactions/does-not-exist/lettered-links",
                headers=hdr,
            )
            assert r.status_code == 404

    asyncio.run(_run())
