"""Bulk merge all duplicate owners by normalized name."""
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from scripts.merge_owners import _apply_merge


async def run_all_merges():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "copro")]

    all_owners = await db.owners.find({}, {"_id": 0}).to_list(1000)
    groups = defaultdict(list)
    for o in all_owners:
        key = (o.get("name") or "").strip().lower()
        if key:
            groups[key].append(o)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Found {len(dupes)} duplicate groups\n")

    total_merged = 0
    total_deleted = 0

    for name, members in sorted(dupes.items(), key=lambda x: -len(x[1])):
        scored = []
        for o in members:
            oid = o["id"]
            lots = await db.lots.count_documents({"$or": [{"owner_id": oid}, {"owner_ids": oid}]})
            je = await db.journal_entries.count_documents({"lines.third_party_id": oid})
            tiers = len(o.get("tier_accounts") or {})
            copros = len(o.get("copropriete_ids") or [])
            has_email = 1 if (o.get("email") or "").strip() else 0
            has_vcs = 1 if (o.get("vcs_code") or "").strip() else 0
            has_aux = 1 if (o.get("auxiliary_code") or "").strip() else 0
            score = lots * 100 + je * 50 + tiers * 10 + copros * 5 + has_email + has_vcs + has_aux
            scored.append((score, o))

        scored.sort(key=lambda x: -x[0])
        keeper = scored[0][1]
        removals = [s[1] for s in scored[1:]]
        remove_ids = [r["id"] for r in removals]

        print(f'=== "{name}" x{len(members)} -> KEEP {keeper["id"][:20]}... (score={scored[0][0]}), REMOVE {len(remove_ids)} ===')

        report = await _apply_merge(
            db,
            source_ids=remove_ids,
            target_id=keeper["id"],
            new_name=keeper.get("name"),
            execute=True,
        )

        c = report["counts"]
        print(f"  JE={c['journal_lines_rewritten']} lots={c['lots_owner_id_rewritten']} "
              f"inv={c['invoices_owner_rewritten']} mut={c['mutations_rewritten']} "
              f"del={c['sources_deleted']} tier={c['tier_accounts_merged']}")
        total_merged += 1
        total_deleted += c["sources_deleted"]

    print(f"\n=== DONE: {total_merged} groups merged, {total_deleted} duplicates deleted ===")

    remaining = await db.owners.count_documents({})
    print(f"Remaining owners: {remaining}")

    # Verify
    all_after = await db.owners.find({}, {"_id": 0, "name": 1}).to_list(1000)
    names_after = defaultdict(int)
    for o in all_after:
        k = (o.get("name") or "").strip().lower()
        if k:
            names_after[k] += 1
    remaining_dupes = {k: v for k, v in names_after.items() if v > 1}
    print(f"Remaining duplicate names: {len(remaining_dupes)}")
    for k, v in remaining_dupes.items():
        print(f"  WARNING: \"{k}\" still {v}")

    client.close()


if __name__ == "__main__":
    asyncio.run(run_all_merges())
