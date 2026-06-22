#!/usr/bin/env python3
"""Migrate existing distribution_keys from wizard-only schema (`lines`) to
the unified schema (`lots`) expected by the API and the expense distribution.

Bug context (iter72sexies) :
- The import wizard `commit-distribution-keys` was creating docs with field
  `lines: [{lot_id, lot_label_raw, lot_code_raw, owner_label_raw, quotity}]`
  whereas the API expects `lots: [{lot_id, lot_number, share}]`.
- Consequence : after import, the UI editor shows "Total quotité = 0.00" and
  the expense allocation per owner returns 0 because invoices.py reads
  `key['lots']` which is empty.
- Additional bug : lot matching used `lot_code` (often "-") and the full
  `lot_label` (e.g. "B0-1 - APPARTEMENT") so `lot_id` was never resolved.

This script :
1. For every distribution_key with non-empty `lines` and empty/missing `lots`,
   rebuild `lots` by:
   - Extracting the lot_number from `lot_label_raw` (strip " - SUFFIX")
   - Matching against lots in the same ACP by number then by description
2. Set `key_type = 'quotity'` (or 'equal' if type was 'equal')
3. Recompute `total_quotities` as sum of shares
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _extract_lot_number(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if " - " in s:
        s = s.split(" - ", 1)[0].strip()
    return s.lower().strip()


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print("=== Migrate distribution_keys schema (`lines` -> `lots`) ===\n")

    migrated_keys = 0
    matched_lots_total = 0
    unmatched_lots_total = 0

    async for key in db.distribution_keys.find({}, {"_id": 0}):
        copro_id = key.get("copropriete_id")
        lines = key.get("lines") or []
        existing_lots = key.get("lots") or []
        if not lines:
            continue
        if existing_lots:
            # Already migrated, skip
            continue

        # Fetch lots for this ACP
        acp_lots = await db.lots.find(
            {"copropriete_id": copro_id},
            {"_id": 0, "id": 1, "number": 1, "description": 1},
        ).to_list(2000)
        lots_by_number = {(l.get("number") or "").lower().strip(): l for l in acp_lots}
        lots_by_desc = {(l.get("description") or "").lower().strip(): l for l in acp_lots}

        api_lots = []
        matched = 0
        unmatched = 0
        for ln in lines:
            share = float(ln.get("quotity") or 0)
            if share <= 0:
                continue
            lot_label = ln.get("lot_label_raw") or ln.get("lot_label") or ""
            lot_code = ln.get("lot_code_raw") or ln.get("lot_code") or ""

            candidates = [
                _extract_lot_number(lot_code),
                _extract_lot_number(lot_label),
                (lot_code or "").lower().strip(),
                (lot_label or "").lower().strip(),
            ]
            matched_lot = None
            for cand in candidates:
                if not cand or cand == "-":
                    continue
                matched_lot = lots_by_number.get(cand) or lots_by_desc.get(cand)
                if matched_lot:
                    break

            api_lots.append({
                "lot_id": matched_lot["id"] if matched_lot else "",
                "lot_number": matched_lot["number"] if matched_lot else (lot_code or lot_label or ""),
                "share": share,
                "lot_label_raw": lot_label,
                "lot_code_raw": lot_code,
                "owner_label_raw": ln.get("owner_label_raw") or ln.get("owner_label") or "",
            })
            if matched_lot:
                matched += 1
            else:
                unmatched += 1

        if not api_lots:
            continue

        total_q = sum(l["share"] for l in api_lots)
        key_type_in = (key.get("type") or "").lower()
        api_key_type = "equal" if key_type_in == "equal" else "quotity"

        await db.distribution_keys.update_one(
            {"id": key["id"]},
            {"$set": {
                "lots": api_lots,
                "lines": api_lots,  # legacy alias to keep both readers in sync
                "key_type": api_key_type,
                "total_quotities": total_q,
            }},
        )
        migrated_keys += 1
        matched_lots_total += matched
        unmatched_lots_total += unmatched
        print(f"  [{key['id'][:8]}] {key.get('name', '')[:30]:<30} | ACP {copro_id[:8]} | {matched} matched / {unmatched} unmatched / total {total_q:.2f}")

    print(f"\n=== SUMMARY ===")
    print(f"Keys migrated : {migrated_keys}")
    print(f"Lots matched : {matched_lots_total}")
    print(f"Lots unmatched : {unmatched_lots_total}")


asyncio.run(main())
