"""iter90dk : tests unitaires pour la mutation multi-lots (checklist).

Verifie :
1. GET /api/lots/{id}/mutation-candidates retourne primary_lot + children + other_owner_lots
2. POST mutate-preview avec additional_lot_ids inclut ces lots dans le calcul
3. POST mutate avec additional_lot_ids cree une mutation par lot
4. Le PDF genere contient TOUS les lots (multi-lot layout)
5. Validation : additional_lot doit meme ACP + meme vendeur
6. Cancel restaure aussi les additional_lots
"""
import asyncio
import io
import os
import sys
import uuid

import httpx
import pytest
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

# On teste via httpx contre le serveur en local (supervisor)
BASE_URL = "http://localhost:8001"
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PWD = "admin123"


async def _login(client):
    r = await client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PWD},
    )
    r.raise_for_status()
    # cookie is set on the client automatically
    return r.cookies


async def _setup_scenario():
    """Setup a fresh ACP with 3 lots (1 primary + 2 additional) belonging to
    the same owner, no parent-child link. Uses direct DB access.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id = str(uuid.uuid4())
    seller_id = str(uuid.uuid4())
    buyer_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    # Cleanup any previous run
    await db.coproprietes.delete_many({"id": copro_id})
    await db.owners.delete_many({"id": {"$in": [seller_id, buyer_id]}})

    # Create copro
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP iter90dk Test", "address": "Test",
        "postal_code": "1000", "city": "Bruxelles", "reference": "ACP-TEST-DK",
    })
    # Create owners
    await db.owners.insert_one({
        "id": seller_id, "name": "Vendeur Test", "last_name": "Vendeur",
        "email": f"v_{uuid.uuid4().hex[:6]}@test.be", "copropriete_ids": [copro_id],
    })
    await db.owners.insert_one({
        "id": buyer_id, "name": "Acheteur Test", "last_name": "Acheteur",
        "email": f"a_{uuid.uuid4().hex[:6]}@test.be", "copropriete_ids": [copro_id],
    })
    # Create 3 lots all owned by seller (NO parent-child link)
    lot_a = str(uuid.uuid4())
    lot_b = str(uuid.uuid4())
    lot_c = str(uuid.uuid4())
    for lid, num, quot in [(lot_a, "A001", 800.0),
                            (lot_b, "A002", 600.0),
                            (lot_c, "A003", 400.0)]:
        await db.lots.insert_one({
            "id": lid, "number": num, "type": "appartement",
            "quotity": quot, "copropriete_id": copro_id,
            "owner_id": seller_id, "owner_ids": [seller_id],
        })
    # Create default key including all 3 lots
    await db.distribution_keys.insert_one({
        "id": key_id, "name": "Cle par defaut", "is_default": True,
        "copropriete_id": copro_id,
        "lots": [
            {"lot_id": lot_a, "lot_number": "A001", "share": 800.0},
            {"lot_id": lot_b, "lot_number": "A002", "share": 600.0},
            {"lot_id": lot_c, "lot_number": "A003", "share": 400.0},
        ],
    })
    # Create tier accounts for owners (needed by mutate)
    from tier_accounts import assign_owner_accounts
    await assign_owner_accounts(db, await db.owners.find_one({"id": seller_id}), copro_id)
    await assign_owner_accounts(db, await db.owners.find_one({"id": buyer_id}), copro_id)

    # Create a fund of roulement (compte 100) journal entry so we have something to transfer.
    # Simulate initial call for roulement:
    #   Debit  4100XX (tier)   1800  (each lot gets share)
    #   Credit 100     1800
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()),
        "journal_type": "OD",
        "date": "2025-01-01",
        "description": "Roulement initial ACP iter90dk",
        "copropriete_id": copro_id,
        "lines": [
            {"account_number": "410000",
             "third_party_id": seller_id,
             "third_party_name": "Vendeur",
             "debit": 1800.0, "credit": 0.0},
            {"account_number": "100",
             "debit": 0.0, "credit": 1800.0},
        ],
        "total_debit": 1800.0,
        "total_credit": 1800.0,
        "auto_generated": False,
    })

    return {
        "copro_id": copro_id, "seller_id": seller_id, "buyer_id": buyer_id,
        "lot_a": lot_a, "lot_b": lot_b, "lot_c": lot_c,
    }


async def _cleanup(ctx):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await db.coproprietes.delete_many({"id": ctx["copro_id"]})
    await db.lots.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["seller_id"], ctx["buyer_id"]]}})
    await db.distribution_keys.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["copro_id"]})
    await db.mutations.delete_many({"copropriete_id": ctx["copro_id"]})


async def _scenario_candidates_lists_other_owner_lots():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            r = await client.get(f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutation-candidates")
            r.raise_for_status()
            data = r.json()
            assert data["primary_lot"]["id"] == ctx["lot_a"]
            assert data["children"] == []
            other_ids = [o["id"] for o in data["other_owner_lots"]]
            assert ctx["lot_b"] in other_ids
            assert ctx["lot_c"] in other_ids
            assert len(other_ids) == 2
    finally:
        await _cleanup(ctx)


async def _scenario_preview_multi_lots_aggregates_correctly():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            # Without additional lots: only lot_a
            r1 = await client.post(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate-preview",
                json={"new_owner_id": ctx["buyer_id"],
                      "sale_date": "2025-06-15",
                      "sale_price": 0},
            )
            r1.raise_for_status()
            d1 = r1.json()
            assert d1["linked_lots_count"] == 0
            assert d1["additional_lots_count"] == 0
            assert len(d1["per_lot_breakdowns"]) == 1
            solo_total = d1["grouped_total_roulement"]

            # With additional lots: lot_a + lot_b + lot_c
            r2 = await client.post(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate-preview",
                json={"new_owner_id": ctx["buyer_id"],
                      "sale_date": "2025-06-15",
                      "sale_price": 0,
                      "additional_lot_ids": [ctx["lot_b"], ctx["lot_c"]]},
            )
            r2.raise_for_status()
            d2 = r2.json()
            assert d2["additional_lots_count"] == 2
            assert len(d2["per_lot_breakdowns"]) == 3
            # Sum should equal total fund of roulement (1800 EUR)
            total = d2["grouped_total_roulement"]
            assert abs(total - 1800.0) < 0.02, f"Expected ~1800, got {total}"
            # Each lot has non-zero roulement quota
            quotas = {b["lot_number"]: b["roulement_quota"] for b in d2["per_lot_breakdowns"]}
            assert quotas["A001"] > 0
            assert quotas["A002"] > 0
            assert quotas["A003"] > 0
            # Solo (only lot_a) should be smaller than group total
            assert solo_total < total
    finally:
        await _cleanup(ctx)


async def _scenario_apply_mutation_creates_journal_per_lot():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            r = await client.post(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate",
                json={"new_owner_id": ctx["buyer_id"],
                      "sale_date": "2025-06-15",
                      "sale_price": 200000,
                      "note": "iter90dk apply test",
                      "additional_lot_ids": [ctx["lot_b"], ctx["lot_c"]]},
            )
            r.raise_for_status()
            data = r.json()
            assert len(data["grouped_mutations"]) == 3
            # Verify each lot got its own mutation
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = mongo[os.environ["DB_NAME"]]
            for lot_id in [ctx["lot_a"], ctx["lot_b"], ctx["lot_c"]]:
                lt = await db.lots.find_one({"id": lot_id})
                assert lt["owner_id"] == ctx["buyer_id"], f"lot {lt['number']} not migrated"
                assert lt.get("mutations"), f"lot {lt['number']} has no mutation"
                # grouped_parent_lot_id should be lot_a for lot_b, lot_c, empty for lot_a
                last = lt["mutations"][-1]
                if lot_id == ctx["lot_a"]:
                    assert last.get("grouped_parent_lot_id") == ""
                else:
                    assert last.get("grouped_parent_lot_id") == ctx["lot_a"]

            # Also verify PDF contains all 3 lots
            r_pdf = await client.get(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutations/last/decompte.pdf"
            )
            r_pdf.raise_for_status()
            reader = PdfReader(io.BytesIO(r_pdf.content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            for num in ["A001", "A002", "A003"]:
                assert num in text, f"Lot {num} missing from decompte PDF"
    finally:
        await _cleanup(ctx)


async def _scenario_validation_rejects_wrong_owner():
    """Un lot appartenant a un AUTRE proprietaire ne peut pas etre inclus."""
    ctx = await _setup_scenario()
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = mongo[os.environ["DB_NAME"]]
        # Change lot_c owner to buyer (different from seller)
        await db.lots.update_one({"id": ctx["lot_c"]}, {"$set": {"owner_id": ctx["buyer_id"]}})
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            r = await client.post(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate-preview",
                json={"new_owner_id": ctx["buyer_id"],
                      "sale_date": "2025-06-15",
                      "sale_price": 0,
                      "additional_lot_ids": [ctx["lot_c"]]},
            )
            assert r.status_code == 400, f"Expected 400, got {r.status_code}"
            assert "vendeur" in r.text.lower()
    finally:
        await _cleanup(ctx)


async def _scenario_cancel_restores_all_lots():
    ctx = await _setup_scenario()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _login(client)
            # Apply multi-lot mutation
            r = await client.post(
                f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate",
                json={"new_owner_id": ctx["buyer_id"],
                      "sale_date": "2025-06-15",
                      "sale_price": 0,
                      "additional_lot_ids": [ctx["lot_b"], ctx["lot_c"]]},
            )
            r.raise_for_status()
            # Cancel
            r_cancel = await client.delete(f"{BASE_URL}/api/lots/{ctx['lot_a']}/mutate/last")
            r_cancel.raise_for_status()
            data = r_cancel.json()
            # Should cancel 3 mutations (primary + 2 additional)
            assert data.get("cancelled_count") == 3
            # Verify each lot restored
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = mongo[os.environ["DB_NAME"]]
            for lot_id in [ctx["lot_a"], ctx["lot_b"], ctx["lot_c"]]:
                lt = await db.lots.find_one({"id": lot_id})
                assert lt["owner_id"] == ctx["seller_id"], f"lot {lt['number']} not restored"
    finally:
        await _cleanup(ctx)


# ---- pytest entry points ----
def test_candidates_lists_other_owner_lots():
    asyncio.run(_scenario_candidates_lists_other_owner_lots())


def test_preview_multi_lots_aggregates_correctly():
    asyncio.run(_scenario_preview_multi_lots_aggregates_correctly())


def test_apply_mutation_creates_journal_per_lot():
    asyncio.run(_scenario_apply_mutation_creates_journal_per_lot())


def test_validation_rejects_wrong_owner():
    asyncio.run(_scenario_validation_rejects_wrong_owner())


def test_cancel_restores_all_lots():
    asyncio.run(_scenario_cancel_restores_all_lots())
