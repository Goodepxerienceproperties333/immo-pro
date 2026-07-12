"""iter90eh : Reparation des lettrages bancaires historiques.

Les lettrages effectues AVANT iter90eb (ou importes via Optipro/CODA)
peuvent avoir des factures marquees `paid` avec `paid_by_transaction_id`
mais les `bank_transactions` correspondantes n'ont PAS `matched=True`,
`match_type`, `matched_to` correctement remplis.

Ce test valide :
1. Endpoint `GET /banking/transactions/{id}/lettered-links` : fallback via
   paid_by_transaction_id + auto-reconciliation
2. Endpoint `POST /banking/repair-legacy-lettrages` : bulk repair
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


def test_lettered_links_fallback_via_paid_by_transaction_id():
    """Une facture avec paid_by_transaction_id sur une txn non-matched
    doit etre trouvee par l'endpoint lettered-links (fallback) + reconcilie."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90eh-cid-{suffix}"
        inv_id = f"iter90eh-inv-{suffix}"
        txn_id = f"iter90eh-txn-{suffix}"
        # Facture legacy : paid + paid_by_transaction_id
        await db.invoices.insert_one({
            "id": inv_id, "copropriete_id": cid,
            "number": "V-LEGACY-001", "supplier": "Legacy Supplier",
            "date": "2025-06-15", "total_amount": 300.00,
            "status": "paid", "amount_paid": 300.00,
            "paid_by_transaction_id": txn_id,
        })
        # Transaction legacy : PAS matched
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2025-06-20", "amount": -300.00,
            "counterparty_name": "Legacy Supplier",
            "matched": False,  # <-- champ manquant historiquement
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
                assert data["matched"] is True, "Fallback doit marquer matched=true"
                assert data["match_type"] == "invoice"
                assert len(data["invoices"]) == 1
                assert data["invoices"][0]["id"] == inv_id
                # Reconciliation persistee en base
                txn_after = await db.bank_transactions.find_one({"id": txn_id})
                assert txn_after.get("matched") is True
                assert txn_after.get("matched_to") == inv_id
                assert txn_after.get("match_type") == "invoice"
        finally:
            await db.invoices.delete_one({"id": inv_id})
            await db.bank_transactions.delete_one({"id": txn_id})

    asyncio.run(_run())


def test_repair_legacy_lettrages_endpoint_repairs_bulk():
    """POST /banking/repair-legacy-lettrages reconcilie N transactions."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90eh-cid-{suffix}"
        pairs = []
        for i in range(3):
            inv_id = f"iter90eh-inv-{suffix}-{i}"
            txn_id = f"iter90eh-txn-{suffix}-{i}"
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": cid,
                "number": f"V-LEG-{i}", "supplier": "Sup",
                "date": "2025-06-15", "total_amount": 100.00,
                "status": "paid", "paid_by_transaction_id": txn_id,
            })
            await db.bank_transactions.insert_one({
                "id": txn_id, "copropriete_id": cid,
                "date": "2025-06-20", "amount": -100.00,
                "counterparty_name": "Sup",
                # matched champ absent
            })
            pairs.append((inv_id, txn_id))
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.post(
                    f"{BACKEND_URL}/api/banking/repair-legacy-lettrages",
                    params={"copropriete_id": cid},
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["invoices_scanned"] >= 3
                assert data["transactions_repaired"] >= 3

                # Verifie que les 3 txns sont bien marquees
                for inv_id, txn_id in pairs:
                    txn = await db.bank_transactions.find_one({"id": txn_id})
                    assert txn.get("matched") is True, (
                        f"Txn {txn_id} not repaired: {txn}"
                    )
                    assert txn.get("matched_to") == inv_id
                    assert txn.get("match_type") == "invoice"

                # Re-run : les 3 doivent etre "already_ok" cette fois
                r2 = await client.post(
                    f"{BACKEND_URL}/api/banking/repair-legacy-lettrages",
                    params={"copropriete_id": cid},
                    headers=hdr,
                )
                assert r2.status_code == 200
                assert r2.json()["transactions_repaired"] == 0
                assert r2.json()["transactions_already_ok"] >= 3
        finally:
            for inv_id, txn_id in pairs:
                await db.invoices.delete_one({"id": inv_id})
                await db.bank_transactions.delete_one({"id": txn_id})

    asyncio.run(_run())


def test_repair_handles_multi_invoice_transaction():
    """Une transaction qui paie N factures -> multi_invoice + matched_to_ids."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90eh-cid-{suffix}"
        txn_id = f"iter90eh-txn-{suffix}"
        inv_ids = []
        for i in range(2):
            inv_id = f"iter90eh-multi-{suffix}-{i}"
            await db.invoices.insert_one({
                "id": inv_id, "copropriete_id": cid,
                "number": f"V-MULTI-{i}", "supplier": "MultiSup",
                "date": "2025-06-15", "total_amount": 50.0,
                "status": "paid", "paid_by_transaction_id": txn_id,
            })
            inv_ids.append(inv_id)
        await db.bank_transactions.insert_one({
            "id": txn_id, "copropriete_id": cid,
            "date": "2025-06-20", "amount": -100.00,
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                r = await client.post(
                    f"{BACKEND_URL}/api/banking/repair-legacy-lettrages",
                    params={"copropriete_id": cid}, headers=hdr,
                )
                assert r.status_code == 200
                txn = await db.bank_transactions.find_one({"id": txn_id})
                assert txn.get("match_type") == "multi_invoice"
                assert set(txn.get("matched_to_ids") or []) == set(inv_ids)
        finally:
            for iid in inv_ids:
                await db.invoices.delete_one({"id": iid})
            await db.bank_transactions.delete_one({"id": txn_id})

    asyncio.run(_run())
