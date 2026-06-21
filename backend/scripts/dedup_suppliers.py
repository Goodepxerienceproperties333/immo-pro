#!/usr/bin/env python3
"""Dedup suppliers by auxiliary_code for ACP 'import'.

Same root cause as for owners : multiple supplier docs share an `auxiliary_code`
but split the balance (one carries the AN from Optipro on 4400XXX, another
carries invoices imported later on 44000XXX). Result : balance-tiers shows
the same supplier twice with opposite balances.

Strategy (mirrors `dedup_owners.py` from the previous session) :
1. Group suppliers by uppercase auxiliary_code (only those with aux != '').
2. Choose CANONICAL :
   a) Supplier with the Optipro-style tier (4400XXX, 6 chars) for ACP "import"
   b) else first one with tier_accounts[ACP] set
   c) else first one chronologically
3. Merge tier_accounts of dupes into canonical (collect Optipro aliases).
4. Remap journal_entries.lines.third_party_id from dupes to canonical AND
   if a line uses the dupe's Optipro account, rewrite to canonical's main.
5. Remap invoices, bank_transactions, fund_calls (if any) to canonical.
6. Delete the dupes.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

ACP = "be6e826c-7e5b-4eda-9fc6-764a5c4d6d12"


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print(f"=== Dedup SUPPLIERS for ACP {ACP[:8]} ===\n")

    by_aux: dict[str, list[dict]] = {}
    async for s in db.suppliers.find({"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0}):
        aux = (s.get("auxiliary_code") or "").upper().strip()
        if aux:
            by_aux.setdefault(aux, []).append(s)

    duplicates = {aux: lst for aux, lst in by_aux.items() if len(lst) >= 2}
    print(f"Found {len(duplicates)} aux_code groups with duplicates\n")

    deleted_count = 0
    merged_je_lines = 0
    merged_invoices = 0
    merged_txns = 0

    for aux, sup_list in duplicates.items():
        # Choose canonical : prefer the one with an Optipro tier 4400XXX (6 chars)
        # for ACP "import"
        canonical = None
        for s in sup_list:
            ta = ((s.get("tier_accounts") or {}).get(ACP, {}) or {}).get("main", "")
            if ta and ta.startswith("4400") and len(ta) == 7 and not ta.startswith("44000"):
                # Optipro format like "4400471"
                canonical = s
                break
        if not canonical:
            for s in sup_list:
                if ((s.get("tier_accounts") or {}).get(ACP, {}) or {}).get("main", ""):
                    canonical = s
                    break
        if not canonical:
            canonical = sup_list[0]

        dups = [s for s in sup_list if s["id"] != canonical["id"]]
        if not dups:
            continue

        canonical_tier = ((canonical.get("tier_accounts") or {}).get(ACP, {}) or {})
        canonical_main = canonical_tier.get("main", "")

        print(f"\n[{aux}] canonical={canonical['id'][:8]} name={canonical.get('name','')[:30]:<30} main={canonical_main} | {len(dups)} dupes to merge")

        # Aggregate aliases from dupes
        aliases = list(canonical_tier.get("aliases") or [])
        for d in dups:
            d_main = ((d.get("tier_accounts") or {}).get(ACP, {}) or {}).get("main", "")
            if d_main and d_main != canonical_main and d_main not in aliases:
                aliases.append(d_main)

        if aliases:
            new_tier = {**canonical_tier, "aliases": aliases}
            if not canonical_main and aliases:
                new_tier["main"] = aliases[0]
                canonical_main = aliases[0]
            await db.suppliers.update_one(
                {"id": canonical["id"]},
                {"$set": {f"tier_accounts.{ACP}": new_tier}},
            )
            print(f"  Canonical tier updated : main={new_tier.get('main')} aliases={aliases}")

        # Remap references from each dupe to canonical
        for d in dups:
            d_main = ((d.get("tier_accounts") or {}).get(ACP, {}) or {}).get("main", "")

            # journal_entries
            je_q = {
                "copropriete_id": ACP,
                "$or": [
                    {"lines.third_party_id": d["id"]},
                ],
            }
            if d_main:
                je_q["$or"].append({"lines.account_number": d_main})
            je_count = 0
            async for je in db.journal_entries.find(je_q, {"_id": 0}):
                new_lines = []
                changed = False
                for ln in je.get("lines", []) or []:
                    nl = dict(ln)
                    if nl.get("third_party_id") == d["id"]:
                        nl["third_party_id"] = canonical["id"]
                        changed = True
                    # Rewrite supplier's Optipro account -> canonical main to consolidate balance
                    if d_main and nl.get("account_number") == d_main and canonical_main and canonical_main != d_main:
                        nl["account_number"] = canonical_main
                        nl["migrated_from_account"] = d_main
                        nl["third_party_id"] = canonical["id"]
                        nl["third_party_type"] = "supplier"
                        changed = True
                    new_lines.append(nl)
                if changed:
                    await db.journal_entries.update_one(
                        {"id": je["id"]},
                        {"$set": {"lines": new_lines, "manually_migrated_supplier": True}},
                    )
                    je_count += 1
                    merged_je_lines += 1
            if je_count:
                print(f"  [{d['id'][:8]}] {je_count} journal_entries remapped (main={d_main})")

            # invoices
            inv_res = await db.invoices.update_many(
                {"copropriete_id": ACP, "supplier_id": d["id"]},
                {"$set": {"supplier_id": canonical["id"]}},
            )
            merged_invoices += inv_res.modified_count

            # bank_transactions
            tr_res = await db.bank_transactions.update_many(
                {"copropriete_id": ACP, "counterparty_supplier_id": d["id"]},
                {"$set": {"counterparty_supplier_id": canonical["id"]}},
            )
            merged_txns += tr_res.modified_count

            # Delete the dupe
            await db.suppliers.delete_one({"id": d["id"]})
            deleted_count += 1

    print(f"\n=== SUMMARY ===")
    print(f"Suppliers merged + deleted : {deleted_count}")
    print(f"Journal entry lines updated : {merged_je_lines}")
    print(f"Invoices remapped : {merged_invoices}")
    print(f"Bank transactions remapped : {merged_txns}")


asyncio.run(main())
