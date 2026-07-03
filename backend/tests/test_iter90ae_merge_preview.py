"""
Iter90ae : Endpoints /merge/preview pour prevision de fusion avant execution.

Test e2e HTTP via httpx sur le backend en cours d'execution.

Scenarios :
1. Owner preview retourne les bons compteurs et NE MODIFIE PAS la base.
2. Supplier preview retourne factures + tx bancaires.
3. Preview rejette keep_id in remove_ids (400).
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


async def _get_admin_token(client):
    """Login as the seed superadmin (admin@copro.be / admin123)."""
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return resp.json().get("access_token") or resp.json().get("token")


async def _mongo():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _scenario_owner_preview():
    db = await _mongo()
    cid = f"iter90ae-op-{uuid.uuid4()}"
    keep = f"o-keep-{uuid.uuid4()}"
    dup1 = f"o-dup1-{uuid.uuid4()}"
    dup2 = f"o-dup2-{uuid.uuid4()}"
    lot1 = f"lot1-{uuid.uuid4()}"
    lot2 = f"lot2-{uuid.uuid4()}"
    tx1 = f"tx1-{uuid.uuid4()}"
    mut1 = f"mut1-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90ae_op",
                                          "reference": "T-op", "status": "active"})
        await db.owners.insert_many([
            {"id": keep, "name": "DUPONT Jean", "copropriete_ids": [cid]},
            {"id": dup1, "name": "DUPONT Jean", "email": "d@test.be", "phone": "0475",
             "copropriete_ids": [cid]},
            {"id": dup2, "name": "DUPONT J.", "iban": "BE68539007547034",
             "copropriete_ids": [cid]},
        ])
        await db.lots.insert_many([
            {"id": lot1, "number": "A1", "owner_id": dup1, "owner_ids": [dup1],
             "copropriete_id": cid, "quotity": 100.0},
            {"id": lot2, "number": "A2", "owner_id": dup2, "owner_ids": [dup2],
             "copropriete_id": cid, "quotity": 100.0},
        ])
        await db.bank_transactions.insert_one({
            "id": tx1, "copropriete_id": cid, "match_type": "owner_payment",
            "matched_to": dup1, "amount": 100.0,
        })
        await db.mutations.insert_one({
            "id": mut1, "copropriete_id": cid, "lot_id": lot1,
            "from_owner_id": dup2, "to_owner_id": keep, "sale_date": "2026-01-15",
        })
        await db.journal_entries.insert_one({
            "id": f"je-{uuid.uuid4()}", "copropriete_id": cid, "journal_type": "OD",
            "date": "2026-01-01", "total_debit": 100.0, "total_credit": 100.0,
            "lines": [{"account_number": "410", "third_party_id": dup1,
                       "debit": 100.0, "credit": 0.0}],
        })
        await db.fund_calls.insert_one({
            "id": f"fc-{uuid.uuid4()}", "copropriete_id": cid, "call_type": "provisions",
            "date": "2026-01-01", "total_amount": 100.0,
            "details": [{"lot_id": lot1, "owner_id": dup1, "amount": 50.0}],
        })

        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.post(
                f"{BACKEND_URL}/api/admin/duplicates/owners/merge/preview",
                headers={"Authorization": f"Bearer {token}"},
                json={"keep_id": keep, "remove_ids": [dup1, dup2]},
            )
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["remove_count"] == 2
        assert r["migrations"]["lots_as_sole_owner"] == 2, r
        assert r["migrations"]["bank_transactions_matched"] == 1
        assert r["migrations"]["mutations_as_seller"] == 1
        assert r["migrations"]["journal_entry_lines"] == 1
        assert r["migrations"]["fund_call_details"] == 1
        assert r["total_refs"] >= 5

        # CRITIQUE : verifier que la base n'a PAS ete modifiee
        assert await db.owners.count_documents({"id": {"$in": [keep, dup1, dup2]}}) == 3
        lot1_after = await db.lots.find_one({"id": lot1}, {"_id": 0})
        assert lot1_after["owner_id"] == dup1, "Preview a migre un lot"
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [keep, dup1, dup2]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.bank_transactions.delete_many({"copropriete_id": cid})
        await db.mutations.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})


async def _scenario_supplier_preview():
    db = await _mongo()
    cid = f"iter90ae-sp-{uuid.uuid4()}"
    keep = f"s-keep-{uuid.uuid4()}"
    dup1 = f"s-dup1-{uuid.uuid4()}"
    dup2 = f"s-dup2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90ae_sp",
                                          "reference": "T-sp", "status": "active"})
        await db.suppliers.insert_many([
            {"id": keep, "name": "ACME Corp", "bce_number": "0404.483.367",
             "copropriete_ids": [cid]},
            {"id": dup1, "name": "ACME Corp", "bce_number": "0404.483.367",
             "iban": "BE68539007547034", "copropriete_ids": [cid]},
            {"id": dup2, "name": "ACME", "bce_number": "0404.483.367",
             "email": "acme@test.be", "copropriete_ids": [cid]},
        ])
        for i in range(5):
            await db.invoices.insert_one({
                "id": f"inv{i}-{uuid.uuid4()}", "supplier_id": dup1 if i < 3 else dup2,
                "supplier_name": "ACME", "total_amount": 100.0, "copropriete_id": cid,
            })
        for i in range(2):
            await db.bank_transactions.insert_one({
                "id": f"tx{i}-{uuid.uuid4()}", "copropriete_id": cid,
                "match_type": "supplier_payment", "matched_to": dup1, "amount": 100.0,
            })

        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.post(
                f"{BACKEND_URL}/api/suppliers/merge/preview",
                headers={"Authorization": f"Bearer {token}"},
                json={"keep_id": keep, "remove_ids": [dup1, dup2]},
            )
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["remove_count"] == 2
        assert r["migrations"]["invoices"] == 5
        assert r["migrations"]["bank_transactions_matched"] == 2
        assert r["total_refs"] == 7
        enriched_fields = [e["field"] for e in r["will_enrich_fields"]]
        assert "iban" in enriched_fields
        assert "email" in enriched_fields

        # Verifier que la base n'a PAS ete modifiee
        assert await db.suppliers.count_documents({"id": {"$in": [keep, dup1, dup2]}}) == 3
        assert await db.invoices.count_documents({"copropriete_id": cid}) == 5
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.suppliers.delete_many({"id": {"$in": [keep, dup1, dup2]}})
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.bank_transactions.delete_many({"copropriete_id": cid})


async def _scenario_preview_rejects_keep_in_remove():
    db = await _mongo()
    cid = f"iter90ae-rej-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    o2 = f"o2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90ae_rej",
                                          "reference": "T-rej", "status": "active"})
        await db.owners.insert_many([
            {"id": o1, "name": "X", "copropriete_ids": [cid]},
            {"id": o2, "name": "Y", "copropriete_ids": [cid]},
        ])
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _get_admin_token(client)
            resp = await client.post(
                f"{BACKEND_URL}/api/admin/duplicates/owners/merge/preview",
                headers={"Authorization": f"Bearer {token}"},
                json={"keep_id": o1, "remove_ids": [o1, o2]},
            )
        assert resp.status_code == 400, resp.text
        assert "keep_id" in resp.json().get("detail", "").lower()
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [o1, o2]}})


def test_owner_merge_preview_counts_correctly():
    asyncio.run(_scenario_owner_preview())


def test_supplier_merge_preview_counts_correctly():
    asyncio.run(_scenario_supplier_preview())


def test_preview_rejects_keep_id_in_remove_ids():
    asyncio.run(_scenario_preview_rejects_keep_in_remove())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
