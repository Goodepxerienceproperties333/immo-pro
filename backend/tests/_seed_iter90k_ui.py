"""Seed minimal ACP + statement + non-lettered transaction + 1 expense_category (classe 6) + 1 distribution_key
for manual UI testing of iter90k categorize dialog. Sets admin.copropriete_ids to include this ACP."""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
from server import db  # noqa


async def main():
    # Wipe previous test seed
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-uiseed-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-uiseed-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-uiseed-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-uiseed-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-uiseed-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-uiseed-"}})

    cid = f"c-uiseed-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP UI SEED iter90k",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    for num, name, cls in [
        ("550000", "Banque", 5),
        ("650000", "Frais bancaires", 6),
        ("750000", "Interets crediteurs", 7),
    ]:
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls, "active": True,
        })
    dk = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": dk, "copropriete_id": cid, "code": "100",
        "name": "Charges communes", "lots": [],
    })
    cat_id = str(uuid.uuid4())
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid,
        "name": "Frais bancaires UI", "account_number": "650000",
        "kind": "charge",
    })
    sid = f"bs-uiseed-{uuid.uuid4().hex[:8]}"
    await db.bank_statements.insert_one({
        "id": sid, "number": "S-UI-001", "date": "2026-02-01",
        "account_number": "550000", "opening_balance": 0, "closing_balance": -25.0,
        "status": "draft", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tid = f"bt-uiseed-{uuid.uuid4().hex[:8]}"
    await db.bank_transactions.insert_one({
        "id": tid, "statement_id": sid, "date": "2026-02-15",
        "amount": -25.0, "account_number": "550000",
        "transaction_type": "debit",
        "counterparty_name": "Bank UI Seed",
        "communication": "Frais tenue compte", "matched": False,
        "match_type": "", "matched_to": "", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Attach ACP to admin
    await db.users.update_one({"email": "admin@copro.be"},
                               {"$addToSet": {"copropriete_ids": cid}})
    print(f"SEEDED cid={cid} sid={sid} tid={tid} cat_id={cat_id} dk={dk}")


asyncio.run(main())
