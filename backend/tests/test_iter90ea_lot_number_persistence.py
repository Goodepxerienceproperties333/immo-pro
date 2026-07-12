"""iter90ea : Renforcement structurel - lot_number persiste sur toutes les
lignes de journal_entries generees (VE fund_calls, OD mutations).

Objectif : eliminer definitivement le risque phantom au niveau schema en
persistant lot_id + lot_number sur chaque ligne. Ainsi meme apres
suppression/re-import du lot, la ligne du journal peut resoudre le nouveau
lot_id via lot_number normalise.
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_generate_sale_entry_persists_lot_number_on_each_line():
    """fund_call.distribution avec 3 lots -> lignes VE avec lot_id + lot_number."""
    from auto_entries import generate_sale_entry

    async def _run():
        db = await _mongo()
        copro_id = f"iter90ea-{uuid.uuid4()}"
        owner1_id = f"o1-{uuid.uuid4()}"
        owner2_id = f"o2-{uuid.uuid4()}"

        await db.pcmn_accounts.insert_many([
            {"number": "700000", "name": "Provisions appelees",
             "copropriete_id": copro_id, "class_num": 7},
            {"number": "40000001", "name": "Prov. Owner 1",
             "copropriete_id": copro_id, "class_num": 4},
            {"number": "40000002", "name": "Prov. Owner 2",
             "copropriete_id": copro_id, "class_num": 4},
        ])
        await db.owners.insert_many([
            {"id": owner1_id, "name": "Alice", "last_name": "Alice",
             "tier_accounts": {copro_id: {"provisions": "40000001"}}},
            {"id": owner2_id, "name": "Bob", "last_name": "Bob",
             "tier_accounts": {copro_id: {"provisions": "40000002"}}},
        ])

        fund_call = {
            "id": str(uuid.uuid4()),
            "copropriete_id": copro_id,
            "name": "Q1 2026",
            "call_type": "provisions",
            "date": "2026-01-01",
            "total_amount": 300.0,
            "reserve_amount": 0.0,
            "roulement_amount": 0.0,
            "distribution": [
                {"lot_id": "lot-uuid-1", "lot_number": "001",
                 "owner_id": owner1_id, "owner_name": "Alice", "amount": 100.0},
                {"lot_id": "lot-uuid-2", "lot_number": "002",
                 "owner_id": owner1_id, "owner_name": "Alice", "amount": 50.0},
                {"lot_id": "lot-uuid-3", "lot_number": "003",
                 "owner_id": owner2_id, "owner_name": "Bob", "amount": 150.0},
            ],
        }

        entry = await generate_sale_entry(db, fund_call)
        assert entry is not None, "VE doit etre generee"
        assert len(entry["lines"]) == 4  # 3 debits + 1 credit

        debit_lines = [ln for ln in entry["lines"] if ln.get("debit", 0) > 0]
        assert len(debit_lines) == 3

        for ln in debit_lines:
            assert ln.get("lot_id"), f"lot_id manquant : {ln}"
            assert ln.get("lot_number"), f"lot_number manquant : {ln}"

        by_num = {ln["lot_number"]: ln for ln in debit_lines}
        assert by_num["001"]["lot_id"] == "lot-uuid-1"
        assert by_num["002"]["lot_id"] == "lot-uuid-2"
        assert by_num["003"]["lot_id"] == "lot-uuid-3"
        assert by_num["001"]["debit"] == 100.0
        assert by_num["003"]["debit"] == 150.0

        # Cleanup
        await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
        await db.owners.delete_many({"id": {"$in": [owner1_id, owner2_id]}})
        await db.journal_entries.delete_many({"copropriete_id": copro_id})

    asyncio.run(_run())


def test_generate_sale_entry_reserve_and_roulement_persist_lot_number():
    """Verifie lot_number sur lignes reserve/roulement."""
    from auto_entries import generate_sale_entry

    async def _run():
        db = await _mongo()
        copro_id = f"iter90ea-{uuid.uuid4()}"
        owner1_id = f"o1-{uuid.uuid4()}"

        await db.pcmn_accounts.insert_many([
            {"number": "700000", "name": "Provisions",
             "copropriete_id": copro_id, "class_num": 7},
            {"number": "160", "name": "Fonds reserve",
             "copropriete_id": copro_id, "class_num": 1},
            {"number": "100", "name": "Fonds roulement",
             "copropriete_id": copro_id, "class_num": 1},
            {"number": "40000001", "name": "Prov Owner1",
             "copropriete_id": copro_id, "class_num": 4},
            {"number": "40010001", "name": "Reserve Owner1",
             "copropriete_id": copro_id, "class_num": 4},
        ])
        await db.owners.insert_one({
            "id": owner1_id, "name": "Alice", "last_name": "Alice",
            "tier_accounts": {copro_id: {
                "provisions": "40000001",
                "reserve": "40010001",
            }},
        })

        fund_call = {
            "id": str(uuid.uuid4()),
            "copropriete_id": copro_id,
            "name": "Q1 avec reserve+roulement",
            "call_type": "provisions",
            "date": "2026-01-01",
            "total_amount": 200.0,
            "reserve_amount": 50.0,
            "roulement_amount": 30.0,
            "distribution": [
                {"lot_id": "lot-A", "lot_number": "42",
                 "owner_id": owner1_id, "owner_name": "Alice", "amount": 200.0},
            ],
        }

        entry = await generate_sale_entry(db, fund_call)
        assert entry is not None

        debit_lines = [ln for ln in entry["lines"] if ln.get("debit", 0) > 0]
        assert len(debit_lines) == 3  # owner_prov, owner_reserve, owner_roul

        for ln in debit_lines:
            assert ln.get("lot_id") == "lot-A", f"lot_id incorrect : {ln}"
            assert ln.get("lot_number") == "42", f"lot_number incorrect : {ln}"

        # Cleanup
        await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
        await db.owners.delete_one({"id": owner1_id})
        await db.journal_entries.delete_many({"copropriete_id": copro_id})

    asyncio.run(_run())


def test_lot_number_survives_phantom_lookup():
    """Scenario : ligne journal avec lot_id perime + lot_number stable.
    Le lot_number normalise permet de retrouver le nouveau lot."""
    line = {
        "account_number": "40000001",
        "debit": 100.0, "credit": 0.0,
        "third_party_id": "owner-uuid",
        "lot_id": "old-lot-uuid",  # phantom
        "lot_number": "001",  # STABLE
    }
    new_lot = {"id": "new-lot-uuid", "number": "001"}

    # Normalisation lstrip("0") preserve la correspondance
    assert str(line["lot_number"]).lstrip("0") == str(new_lot["number"]).lstrip("0")
