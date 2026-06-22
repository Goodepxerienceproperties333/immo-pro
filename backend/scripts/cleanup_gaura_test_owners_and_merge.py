#!/usr/bin/env python3
"""Comprehensive Gaura cleanup (iter72sexies) :

Phase A : DELETE 3 test owners (no aux_code, CM-only accounts)
  - ALEXIS Jean-Pierre (40000007/40010007)
  - GRAMME GILLES GRAMME GILLES (40000001/40010001) — duplicated name = obvious test data
  - Wauthier - Catinus Nathalie - Thierry (40000002/40010002)
  + cascade delete : journal_entries, fund_calls, bank_transactions, lots ref

Phase B : MERGE 5 CM/Optipro duplicate pairs the first script missed
  - BERNARD C1385 : 40000006 -> 4101385
  - Heremans C1993 : 40000003 -> 4101993
  - Ferdinande C1991 : 40000004 -> 4101991
  - Leyder C2098 : 40000008 -> 4102098
  - Dubuisson C2083 : 40000005 -> 4102083

Phase C : Final cleanup of orphan CM PCMN accounts.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


ACP = "b5f14232-34f5-4805-9b9e-ebd32ad8baa5"  # Gaura

TEST_OWNERS = [
    # (auxiliary_code, name_keyword_contains, prov_account)
    (None, "alexis jean-pierre", "40000007"),
    (None, "gramme gilles", "40000001"),
    (None, "wauthier - catinus", "40000002"),
]


async def phase_a_delete_test_owners(db):
    print("\n=== PHASE A : Delete test owners with cascade ===")
    test_owner_ids = []
    test_accounts = set()
    for _, name_kw, prov in TEST_OWNERS:
        async for o in db.owners.find(
            {f"tier_accounts.{ACP}.provisions": prov},
            {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1, "auxiliary_code": 1},
        ):
            if name_kw.lower() not in (o.get("name", "")).lower():
                continue
            test_owner_ids.append(o["id"])
            ta = o.get("tier_accounts", {}).get(ACP, {})
            if ta.get("provisions"):
                test_accounts.add(ta["provisions"])
            if ta.get("reserve"):
                test_accounts.add(ta["reserve"])
            print(f"  Identified test owner : {o['name']} ({o['id'][:8]}) | accs={ta.get('provisions','')}/{ta.get('reserve','')}")

    if not test_owner_ids:
        print("  No test owners found - already cleaned.")
        return 0, 0, 0, 0

    # 1. Delete journal_entries that touch these accounts/ids.
    # WARNING : FI entries here are fictitious bank receipts. Delete the whole JE.
    je_query = {
        "copropriete_id": ACP,
        "$or": [
            {"lines.account_number": {"$in": list(test_accounts)}},
            {"lines.third_party_id": {"$in": test_owner_ids}},
        ],
    }
    n_je = await db.journal_entries.count_documents(je_query)
    print(f"  Journal entries to delete (full) : {n_je}")
    res = await db.journal_entries.delete_many(je_query)

    # 2. Delete fund_calls
    fc_query = {"copropriete_id": ACP,
                "$or": [
                    {"owner_id": {"$in": test_owner_ids}},
                    {"account_number": {"$in": list(test_accounts)}},
                ]}
    res_fc = await db.fund_calls.delete_many(fc_query)

    # 3. Delete bank_transactions linked to these owners
    res_bt = await db.bank_transactions.delete_many({
        "copropriete_id": ACP,
        "counterparty_owner_id": {"$in": test_owner_ids},
    })

    # 4. Detach lots (don't delete - lots are physical units that may still be valid)
    n_lots = await db.lots.update_many(
        {"copropriete_id": ACP, "owner_id": {"$in": test_owner_ids}},
        {"$set": {"owner_id": ""}},
    )

    # 5. Detach invoices referencing as private fee owner
    await db.invoices.update_many(
        {"copropriete_id": ACP, "private_fee_owner_id": {"$in": test_owner_ids}},
        {"$set": {"private_fee_owner_id": ""}},
    )

    # 6. Delete the owner docs
    res_o = await db.owners.delete_many({"id": {"$in": test_owner_ids}})

    # 7. Delete the CM PCMN accounts
    for acc in test_accounts:
        await db.pcmn_accounts.delete_one({"copropriete_id": ACP, "number": acc})

    print(f"  Deleted : {res_o.deleted_count} owners, {res.deleted_count} JEs, "
          f"{res_fc.deleted_count} fund_calls, {res_bt.deleted_count} bank_tx, "
          f"{len(test_accounts)} PCMN, {n_lots.modified_count} lots detached")
    return res_o.deleted_count, res.deleted_count, res_fc.deleted_count, len(test_accounts)


async def phase_b_merge_pairs(db):
    print("\n=== PHASE B : Merge remaining CM/Optipro pairs ===")
    from collections import defaultdict
    by_aux = defaultdict(list)
    async for o in db.owners.find({f"tier_accounts.{ACP}": {"$exists": True}}, {"_id": 0}):
        aux = (o.get("auxiliary_code") or "").upper().strip()
        if aux:
            by_aux[aux].append(o)
    dups = {k: v for k, v in by_aux.items() if len(v) > 1}
    print(f"  Found {len(dups)} duplicate aux_code groups")

    total_merged = 0
    total_je_migrated = 0
    total_pcmn_deleted = 0

    for aux, lst in dups.items():
        # Pick canonical = the one with 4101 / 4102 prov (Optipro style)
        canonical = None
        cm_dups = []
        for o in lst:
            prov = (o.get("tier_accounts", {}).get(ACP, {}) or {}).get("provisions", "")
            if prov.startswith("4101") or prov.startswith("4102"):
                if canonical:
                    cm_dups.append(o)
                else:
                    canonical = o
            else:
                cm_dups.append(o)
        if not canonical or not cm_dups:
            print(f"  [{aux}] skipped (no clear canonical/dup split)")
            continue

        canonical_prov = canonical["tier_accounts"][ACP]["provisions"]

        for dup in cm_dups:
            dup_ta = (dup.get("tier_accounts") or {}).get(ACP, {}) or {}
            dup_prov = dup_ta.get("provisions", "")
            dup_reserve = dup_ta.get("reserve", "")
            old_accounts = [a for a in (dup_prov, dup_reserve) if a]

            # Migrate JE lines
            je_migrated = 0
            async for je in db.journal_entries.find({"copropriete_id": ACP, "$or": [
                {"lines.account_number": {"$in": old_accounts}},
                {"lines.third_party_id": dup["id"]},
            ]}, {"_id": 0}):
                new_lines = []
                changed = False
                for ln in je.get("lines", []) or []:
                    nl = dict(ln)
                    if nl.get("account_number") in old_accounts:
                        nl["account_number"] = canonical_prov
                        nl["account_name"] = f"Coproprietaires - {canonical.get('name', '')[:30]}"
                        nl["migrated_from_cm_account"] = ln.get("account_number")
                        changed = True
                        je_migrated += 1
                    if nl.get("third_party_id") == dup["id"]:
                        nl["third_party_id"] = canonical["id"]
                        nl["third_party_type"] = "owner"
                        changed = True
                    new_lines.append(nl)
                if changed:
                    await db.journal_entries.update_one({"id": je["id"]}, {"$set": {"lines": new_lines}})

            # Migrate fund_calls
            await db.fund_calls.update_many(
                {"copropriete_id": ACP, "owner_id": dup["id"]},
                {"$set": {"owner_id": canonical["id"]}},
            )
            await db.fund_calls.update_many(
                {"copropriete_id": ACP, "account_number": {"$in": old_accounts}},
                {"$set": {"account_number": canonical_prov}},
            )

            # Migrate lots
            await db.lots.update_many(
                {"copropriete_id": ACP, "owner_id": dup["id"]},
                {"$set": {"owner_id": canonical["id"]}},
            )

            # Migrate invoices
            await db.invoices.update_many(
                {"copropriete_id": ACP, "private_fee_owner_id": dup["id"]},
                {"$set": {"private_fee_owner_id": canonical["id"]}},
            )

            # Migrate bank_transactions
            await db.bank_transactions.update_many(
                {"copropriete_id": ACP, "counterparty_owner_id": dup["id"]},
                {"$set": {"counterparty_owner_id": canonical["id"]}},
            )

            # Delete dup owner
            await db.owners.delete_one({"id": dup["id"]})

            # Delete CM accounts if no refs left
            for acc in old_accounts:
                n_je = await db.journal_entries.count_documents({
                    "copropriete_id": ACP, "lines.account_number": acc,
                })
                if n_je == 0:
                    r = await db.pcmn_accounts.delete_one({"copropriete_id": ACP, "number": acc})
                    total_pcmn_deleted += r.deleted_count

            print(f"  [{aux}] {canonical['name'][:30]:<32} | CM {dup_prov}+{dup_reserve} -> {canonical_prov} | {je_migrated} JE lines")
            total_merged += 1
            total_je_migrated += je_migrated

    print(f"  Merged : {total_merged} dups, {total_je_migrated} JE lines, {total_pcmn_deleted} PCMN deleted")
    return total_merged


async def phase_c_final_cleanup(db):
    print("\n=== PHASE C : Final cleanup orphan PCMN ===")
    total = 0
    async for a in db.pcmn_accounts.find(
        {"copropriete_id": ACP,
         "$or": [
             {"number": {"$regex": "^40000"}},
             {"number": {"$regex": "^40010"}},
             {"number": "400000"},
             {"number": "400100"},
         ]},
        {"_id": 0, "number": 1},
    ):
        n_je = await db.journal_entries.count_documents({"copropriete_id": ACP, "lines.account_number": a["number"]})
        n_owner = await db.owners.count_documents({f"tier_accounts.{ACP}.provisions": a["number"]})
        n_owner_r = await db.owners.count_documents({f"tier_accounts.{ACP}.reserve": a["number"]})
        if n_je == 0 and n_owner == 0 and n_owner_r == 0:
            await db.pcmn_accounts.delete_one({"copropriete_id": ACP, "number": a["number"]})
            print(f"  Deleted orphan : {a['number']}")
            total += 1
    print(f"  {total} orphan PCMN accounts cleaned")


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print(f"=== Gaura comprehensive cleanup ===")

    await phase_a_delete_test_owners(db)
    await phase_b_merge_pairs(db)
    await phase_c_final_cleanup(db)

    # Final report
    print("\n=== FINAL STATE ===")
    cnt = await db.owners.count_documents({f"tier_accounts.{ACP}": {"$exists": True}})
    print(f"Owners with tier_accounts in Gaura : {cnt}")
    print("\nRemaining CM-style accounts :")
    async for a in db.pcmn_accounts.find(
        {"copropriete_id": ACP, "$or": [{"number": {"$regex": "^40000"}}, {"number": {"$regex": "^40010"}}]},
        {"_id": 0, "number": 1, "name": 1},
    ).sort("number", 1):
        n_je = await db.journal_entries.count_documents({"copropriete_id": ACP, "lines.account_number": a["number"]})
        print(f"  {a['number']} | {a.get('name','')[:35]:<36} | {n_je} JE")


asyncio.run(main())
