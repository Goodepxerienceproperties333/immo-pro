"""Migration one-shot : dedup proprietaires et fournisseurs par syndic.

4 phases :
  1. Backfill syndic_id sur owners + suppliers
  2. Backfill canonical_email / canonical_phone sur owners
  3. Fusion des doublons (syndic_id + canonical_email/phone)
  4. Creation des index uniques

Idempotent : peut etre relance sans risque.
Usage :
  cd /app/backend && python3 scripts/migrate_dedup_syndic.py
  OU via endpoint : POST /api/admin/migrate/dedup-syndic
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient


def canonical_email(raw: str) -> str:
    return (raw or "").strip().lower()


def canonical_phone(raw: str) -> str:
    return re.sub(r"[^0-9+]", "", (raw or ""))


async def run_migration(db, dry_run: bool = False):
    stats = {
        "phase1_owners": 0, "phase1_suppliers": 0,
        "phase2_owners": 0,
        "phase3_fused_email": 0, "phase3_fused_phone": 0,
        "phase4_indexes": 0,
        "errors": [],
    }

    # ---- Phase 1 : Backfill syndic_id ----
    print("Phase 1 : Backfill syndic_id...")
    copro_syndic = {}
    async for c in db.coproprietes.find({}, {"id": 1, "syndic_id": 1}):
        copro_syndic[c["id"]] = c.get("syndic_id", "")

    async for o in db.owners.find(
        {"$or": [{"syndic_id": {"$exists": False}}, {"syndic_id": ""}]},
        {"id": 1, "copropriete_ids": 1, "copropriete_id": 1},
    ):
        copro_ids = o.get("copropriete_ids") or []
        if not copro_ids:
            cid = o.get("copropriete_id")
            if cid:
                copro_ids = [cid]
        sid = next(
            (copro_syndic.get(cid, "") for cid in copro_ids if cid and copro_syndic.get(cid)),
            "",
        )
        if not dry_run:
            await db.owners.update_one({"id": o["id"]}, {"$set": {"syndic_id": sid}})
        stats["phase1_owners"] += 1

    async for s in db.suppliers.find(
        {"$or": [{"syndic_id": {"$exists": False}}, {"syndic_id": ""}]},
        {"id": 1, "copropriete_id": 1},
    ):
        sid = copro_syndic.get(s.get("copropriete_id", ""), "")
        if not dry_run:
            await db.suppliers.update_one({"id": s["id"]}, {"$set": {"syndic_id": sid}})
        stats["phase1_suppliers"] += 1

    print(f"  owners: {stats['phase1_owners']}, suppliers: {stats['phase1_suppliers']}")

    # ---- Phase 2 : Backfill canonical_email / canonical_phone ----
    print("Phase 2 : Backfill canonical fields...")
    async for o in db.owners.find(
        {"$or": [
            {"canonical_email": {"$exists": False}},
            {"canonical_phone": {"$exists": False}},
        ]},
        {"id": 1, "email": 1, "phone": 1},
    ):
        upd = {
            "canonical_email": canonical_email(o.get("email")),
            "canonical_phone": canonical_phone(o.get("phone")),
        }
        if not dry_run:
            await db.owners.update_one({"id": o["id"]}, {"$set": upd})
        stats["phase2_owners"] += 1
    print(f"  owners backfilled: {stats['phase2_owners']}")

    # ---- Phase 3 : Fusion des doublons ----
    print("Phase 3 : Fusion doublons...")

    async def _fuse_group(key_field: str) -> int:
        fused = 0
        pipeline = [
            {"$match": {key_field: {"$ne": ""}, "syndic_id": {"$ne": ""}}},
            {"$group": {
                "_id": {"sid": "$syndic_id", "k": f"${key_field}"},
                "ids": {"$push": "$id"},
                "count": {"$sum": 1},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        async for group in db.owners.aggregate(pipeline):
            ids = group["ids"]
            keep_id = ids[0]
            remove_ids = ids[1:]
            if not dry_run:
                try:
                    await _merge_owners(db, keep_id, remove_ids)
                except Exception as e:
                    stats["errors"].append(f"Merge failed {keep_id}<-{remove_ids}: {e}")
                    continue
            fused += len(remove_ids)
        return fused

    stats["phase3_fused_email"] = await _fuse_group("canonical_email")
    stats["phase3_fused_phone"] = await _fuse_group("canonical_phone")
    print(f"  fused by email: {stats['phase3_fused_email']}, by phone: {stats['phase3_fused_phone']}")

    # ---- Phase 4 : Index uniques ----
    print("Phase 4 : Creation index uniques...")
    index_defs = [
        ("owners", [("syndic_id", 1), ("canonical_email", 1)], {
            "unique": True, "name": "uq_owner_syndic_email",
            "partialFilterExpression": {
                "canonical_email": {"$type": "string", "$gt": ""},
                "syndic_id": {"$type": "string", "$gt": ""},
            },
        }),
        ("owners", [("syndic_id", 1), ("canonical_phone", 1)], {
            "unique": True, "name": "uq_owner_syndic_phone",
            "partialFilterExpression": {
                "canonical_phone": {"$type": "string", "$gt": ""},
                "syndic_id": {"$type": "string", "$gt": ""},
            },
        }),
        ("owners", [("syndic_id", 1)], {"name": "idx_owner_syndic_id"}),
        ("suppliers", [("syndic_id", 1), ("bce_number", 1)], {
            "unique": True, "name": "uq_supplier_syndic_bce",
            "partialFilterExpression": {
                "bce_number": {"$type": "string", "$gt": ""},
                "syndic_id": {"$type": "string", "$gt": ""},
            },
        }),
        ("suppliers", [("syndic_id", 1)], {"name": "idx_supplier_syndic_id"}),
    ]
    for coll_name, keys, opts in index_defs:
        try:
            if not dry_run:
                await db[coll_name].create_index(keys, **opts)
            stats["phase4_indexes"] += 1
            print(f"  {coll_name}.{opts.get('name', keys)} OK")
        except Exception as e:
            msg = f"Index {opts.get('name')}: {e}"
            stats["errors"].append(msg)
            print(f"  ERREUR: {msg}")

    print(f"\nMigration terminee. Stats: {stats}")
    return stats


async def _merge_owners(db, keep_id: str, remove_ids: list[str]):
    """Fusionne remove_ids dans keep_id.

    Reassigne lots, journal_entries, fund_calls, mutations.
    Fusionne copropriete_ids et tier_accounts.
    """
    keeper = await db.owners.find_one({"id": keep_id})
    if not keeper:
        return

    all_copro_ids = set(keeper.get("copropriete_ids") or [])
    merged_tiers = dict(keeper.get("tier_accounts") or {})

    for rid in remove_ids:
        victim = await db.owners.find_one({"id": rid})
        if not victim:
            continue

        # Collect copropriete_ids and tier_accounts
        for cid in (victim.get("copropriete_ids") or []):
            all_copro_ids.add(cid)
        for k, v in (victim.get("tier_accounts") or {}).items():
            if k not in merged_tiers:
                merged_tiers[k] = v

        # Reassign lots
        await db.lots.update_many(
            {"owner_id": rid}, {"$set": {"owner_id": keep_id}}
        )
        await db.lots.update_many(
            {"owner_ids": rid},
            {"$set": {"owner_ids.$": keep_id}},
        )

        # Reassign journal entries
        async for je in db.journal_entries.find(
            {"lines.third_party_id": rid}, {"_id": 1, "lines": 1}
        ):
            changed = False
            for ln in je.get("lines", []):
                if ln.get("third_party_id") == rid:
                    ln["third_party_id"] = keep_id
                    changed = True
            if changed:
                await db.journal_entries.update_one(
                    {"_id": je["_id"]}, {"$set": {"lines": je["lines"]}}
                )

        # Reassign fund_calls
        async for fc in db.fund_calls.find(
            {"details.owner_id": rid}, {"_id": 1, "details": 1}
        ):
            changed = False
            for d in fc.get("details", []):
                if d.get("owner_id") == rid:
                    d["owner_id"] = keep_id
                    changed = True
            if changed:
                await db.fund_calls.update_one(
                    {"_id": fc["_id"]}, {"$set": {"details": fc["details"]}}
                )

        # Reassign mutations
        await db.mutations.update_many(
            {"from_owner_id": rid}, {"$set": {"from_owner_id": keep_id}}
        )
        await db.mutations.update_many(
            {"to_owner_id": rid}, {"$set": {"to_owner_id": keep_id}}
        )

        # Delete the duplicate
        await db.owners.delete_one({"id": rid})

    # Update keeper with merged data
    await db.owners.update_one(
        {"id": keep_id},
        {"$set": {
            "copropriete_ids": list(all_copro_ids),
            "tier_accounts": merged_tiers,
        }},
    )


async def main():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME", "copro")]
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN ===")
    await run_migration(db, dry_run=dry)


if __name__ == "__main__":
    asyncio.run(main())
