#!/usr/bin/env python3
"""Remediation script for ACP 'import' (be6e826c-7e5b-4eda-9fc6-764a5c4d6d12).

Issue : owners LENOTRE-MANSART / RUBENS-RENOIR / VELASQUEZ-GOYA / RAPHAEL-MICHEL ANGE
have "disappeared" from the UI for ACP "import".

Root cause :
- The owner duplicates (with empty aux_code, often double-named) have polluted DB.
- The 4 canonical owners (C0960..C0963) have `tier_accounts[ACP]` set BUT
  `copropriete_ids=[]` AND no lot in ACP has `owner_id` filled.
- The `list_owners` endpoint fetches owners through `lots.owner_id` for a given
  ACP. With owner_id="" on every lot, the endpoint returns ZERO owners.

Fix :
1. Identify 4 canonical owners (aux C0960..C0963).
2. Delete the doubled-name duplicates (same name+name like
   "LENOTRE-MANSART LENOTRE-MANSART") - they have no tier_accounts and no JE refs.
3. For the single-name duplicates without aux_code:
   - If they have any JE references, remap to canonical and delete.
   - Otherwise delete.
4. Link the 4 apartments (Lots Le Nôtre-Mansart / Raphael / Rubens / Velasquez)
   in ACP "import" to their canonical owner_id.
5. Add ACP "import" to canonical owners' `copropriete_ids`.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

ACP = "be6e826c-7e5b-4eda-9fc6-764a5c4d6d12"

# Canonical owner mappings
CANONICAL = {
    "C0960": "Lots Le Nôtre-Mansart",
    "C0961": "Lots Raphael-Michel- ange",
    "C0962": "Lots Rubens-Renoir",
    "C0963": "Lots Velasquez-Goya",
}


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print(f"=== Remediation ACP {ACP[:8]} ===\n")

    # Step 1 : identify canonical owners
    canonical_owners = {}
    for aux in CANONICAL.keys():
        o = await db.owners.find_one({"auxiliary_code": aux})
        if not o:
            print(f"  ! Canonical {aux} NOT FOUND, skipping.")
            continue
        canonical_owners[aux] = o
        print(f"  Canonical {aux}: id={o['id'][:8]} name={o['name']}")
    print()

    # Step 2 : delete doubled-name duplicates
    print("Step 2 - Delete doubled-name duplicates:")
    doubled_names = [
        "LENOTRE-MANSART LENOTRE-MANSART",
        "RUBENS-RENOIR RUBENS-RENOIR",
        "VELASQUEZ-GOYA VELASQUEZ-GOYA",
        "RAPHAEL-MICHEL ANGE RAPHAEL-MICHEL ANGE",
    ]
    deleted_doubled = 0
    for name in doubled_names:
        async for o in db.owners.find({"name": name, "auxiliary_code": ""}):
            # Safety: check no JE references
            ref_count = await db.journal_entries.count_documents({
                "lines.third_party_id": o["id"]
            })
            if ref_count > 0:
                print(f"  ! {o['id'][:8]} {name} has {ref_count} JE refs, skipping")
                continue
            await db.owners.delete_one({"id": o["id"]})
            deleted_doubled += 1
            print(f"  - Deleted {o['id'][:8]} {name}")
    print(f"  Total doubled deleted: {deleted_doubled}\n")

    # Step 3 : handle single-name dup (one per canonical) -> remap or delete
    print("Step 3 - Merge single-name duplicates into canonical:")
    merged = 0
    for aux, canonical in canonical_owners.items():
        canonical_name = canonical["name"]
        # Find dupes : same name + empty aux_code + id != canonical
        async for o in db.owners.find({
            "name": canonical_name,
            "auxiliary_code": "",
            "id": {"$ne": canonical["id"]},
        }):
            # Remap any JE references
            je_count = 0
            async for je in db.journal_entries.find({"lines.third_party_id": o["id"]}, {"_id": 0}):
                new_lines = []
                changed = False
                for ln in je.get("lines", []) or []:
                    nl = dict(ln)
                    if nl.get("third_party_id") == o["id"]:
                        nl["third_party_id"] = canonical["id"]
                        changed = True
                    new_lines.append(nl)
                if changed:
                    await db.journal_entries.update_one(
                        {"id": je["id"]},
                        {"$set": {"lines": new_lines}},
                    )
                    je_count += 1
            # Remap lots
            lot_count = await db.lots.update_many(
                {"owner_id": o["id"]},
                {"$set": {"owner_id": canonical["id"]}},
            )
            # Remap bank_transactions
            await db.bank_transactions.update_many(
                {"counterparty_supplier_id": o["id"]},
                {"$set": {"counterparty_supplier_id": canonical["id"]}},
            )
            # Delete dup
            await db.owners.delete_one({"id": o["id"]})
            merged += 1
            print(f"  - Merged {o['id'][:8]} -> {canonical['id'][:8]} ({canonical_name}) | {je_count} JEs remapped, {lot_count.modified_count} lots remapped")
    print(f"  Total merged: {merged}\n")

    # Step 4 : link 4 apartments to their canonical owners
    print("Step 4 - Link 4 apartments to canonical owners:")
    for aux, lot_number in CANONICAL.items():
        canonical = canonical_owners.get(aux)
        if not canonical:
            continue
        result = await db.lots.update_one(
            {"copropriete_id": ACP, "number": lot_number, "quotity": {"$gt": 0}},
            {"$set": {"owner_id": canonical["id"], "owner_ids": [canonical["id"]]}},
        )
        print(f"  Lot '{lot_number}' -> {canonical['name']} ({canonical['id'][:8]}) : modified={result.modified_count}")
    print()

    # Step 5 : add ACP to canonical owners' copropriete_ids
    print("Step 5 - Add ACP to canonical owners' copropriete_ids:")
    for aux, canonical in canonical_owners.items():
        result = await db.owners.update_one(
            {"id": canonical["id"]},
            {"$addToSet": {"copropriete_ids": ACP}},
        )
        print(f"  {aux} {canonical['name']} : modified={result.modified_count}")
    print()

    # Final verification
    print("=== VERIFICATION ===")
    # 1. Canonical owners visible via lots
    lot_oids = await db.lots.distinct("owner_id", {"copropriete_id": ACP})
    print(f"Distinct owner_ids on lots in ACP: {len([o for o in lot_oids if o])}")
    for oid in [o for o in lot_oids if o]:
        o = await db.owners.find_one({"id": oid})
        if o:
            print(f"  -> {o.get('auxiliary_code','')} {o.get('name','')}")

    # 2. Total owners cleanup
    total = await db.owners.count_documents({})
    print(f"\nTotal owners remaining: {total}")

    # 3. Total related dup checks
    for aux in CANONICAL.keys():
        canonical = canonical_owners.get(aux)
        if not canonical:
            continue
        name_count = await db.owners.count_documents({"name": {"$regex": canonical["name"].split()[0], "$options": "i"}})
        print(f"  {aux} name '{canonical['name'].split()[0]}' total in DB: {name_count}")

asyncio.run(main())
