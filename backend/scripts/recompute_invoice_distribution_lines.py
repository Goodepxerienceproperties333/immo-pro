#!/usr/bin/env python3
"""Recompute `invoices.distribution_lines` for ALL invoices linked to a
distribution_key, using the current key.lots mapping. Useful after
re-importing or migrating distribution keys.

After iter72sexies fix, the wizard-imported keys now have proper `lots`
matched to actual lot_ids. But the 63 invoices already in DB still have
`distribution_lines: []` because they were committed before the key was
fully populated.

This script :
1. For each invoice with non-empty `distribution_key_id` and empty
   `distribution_lines`, fetch the key, then compute the distribution_lines
   from key.lots (same logic as POST /api/invoices).
2. Update the invoice in place.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print("=== Recompute invoice distribution_lines from key.lots ===\n")

    total_updated = 0
    async for inv in db.invoices.find({"distribution_key_id": {"$ne": ""}}, {"_id": 0}):
        key_id = inv.get("distribution_key_id", "")
        if not key_id:
            continue
        existing_lines = inv.get("distribution_lines") or []
        if existing_lines:
            continue
        key = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not key:
            continue
        key_lots = key.get("lots") or []
        if not key_lots:
            continue
        total_shares = sum(float(l.get("share") or 0) for l in key_lots) or 1
        total_amount = float(inv.get("total_amount") or 0)
        new_lines = []
        for lot_entry in key_lots:
            lot_id = lot_entry.get("lot_id", "")
            share = float(lot_entry.get("share") or 0)
            if share <= 0:
                continue
            owner_name = ""
            if lot_id:
                lot_doc = await db.lots.find_one({"id": lot_id}, {"_id": 0})
                if lot_doc and lot_doc.get("owner_id"):
                    owner_doc = await db.owners.find_one({"id": lot_doc["owner_id"]}, {"_id": 0})
                    if owner_doc:
                        owner_name = owner_doc.get("name", "")
            share_ratio = share / total_shares
            new_lines.append({
                "lot_id": lot_id,
                "lot_number": lot_entry.get("lot_number", ""),
                "owner_name": owner_name,
                "share": share,
                "amount": round(total_amount * share_ratio, 2),
            })
        if new_lines:
            await db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {"distribution_lines": new_lines}},
            )
            total_updated += 1

    print(f"Invoices updated with new distribution_lines : {total_updated}")


asyncio.run(main())
