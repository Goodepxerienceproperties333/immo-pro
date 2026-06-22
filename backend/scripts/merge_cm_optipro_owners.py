#!/usr/bin/env python3
"""Merge duplicate owner docs (CM-style 40000XXX vs Optipro-style 4101XXX)
and migrate all appels de fonds to the imported Optipro account.

Bug context (iter72duodecies) :
- The Bilan AN imports created owners with `tier_accounts.{ACP}.provisions = 4101XXX`
  (Optipro 7-digit format).
- The Owners CSV import / appels de fonds generation created SEPARATE owner docs
  for the same persons with `tier_accounts.{ACP}.provisions = 40000XXX`,
  `reserve = 40010XXX` (CM 8-digit format).
- Both docs share the same `auxiliary_code` (e.g. C1385 for BERNARD).
- The Balance de Tiers shows them as TWO LINES per owner (the canonical + an
  "Ex-prop" duplicate). Confusing for the manager.

User decision : "garder UNIQUEMENT les comptes comptables importes, les
appels doivent se trouver dans le compte comptable [importe]". So :
1. Canonical = the Optipro-imported owner (provisions 4101XXX, no reserve)
2. ALL appels (provisions VE on 40000XXX + reserve VE on 40010XXX) MUST be
   moved to the canonical account 4101XXX.
3. The CM duplicate owner doc is deleted.

Strategy :
For each ACP, for each (auxiliary_code) duplicate group :
1. Find canonical = owner with tier_accounts.{ACP}.provisions starting with "4101"
2. Find dup = owner with tier_accounts.{ACP}.provisions starting with "40000"
3. In every JE line for this ACP : replace account_number from
   dup's CM accounts (40000XXX, 40010XXX) with canonical's 4101XXX
4. Update lines' third_party_id : if it pointed to dup.id, set to canonical.id
5. Update fund_calls, invoices, bank_transactions, lots refs
6. Delete the dup PCMN accounts (40000XXX, 40010XXX) - they have no movements left
7. Delete the dup owner doc
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print("=== Merge CM/Optipro duplicate owners & migrate appels ===\n")

    total_owners_deleted = 0
    total_je_lines_updated = 0
    total_pcmn_deleted = 0
    total_fund_calls_remapped = 0

    async for cop in db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}):
        ACP = cop["id"]
        # Build aux_code groups for this ACP
        from collections import defaultdict
        by_aux: dict[str, list[dict]] = defaultdict(list)
        async for o in db.owners.find({f"tier_accounts.{ACP}": {"$exists": True}}, {"_id": 0}):
            aux = (o.get("auxiliary_code") or "").upper().strip()
            if aux:
                by_aux[aux].append(o)
        dups = {k: v for k, v in by_aux.items() if len(v) > 1}
        if not dups:
            continue

        print(f"\n--- ACP {ACP[:8]} {cop['name']} : {len(dups)} duplicate aux groups ---")

        for aux, lst in dups.items():
            # Identify canonical (Optipro 4101XXX) vs dup (CM 40000XXX)
            canonical = None
            cm_dups: list[dict] = []
            for o in lst:
                prov = ((o.get("tier_accounts") or {}).get(ACP, {}) or {}).get("provisions", "")
                if prov.startswith("4101") and len(prov) == 7:
                    if not canonical:
                        canonical = o
                    else:
                        # Two canonicals - keep the one with most fields
                        if len(o.get("name", "")) > len(canonical.get("name", "")):
                            canonical, o = o, canonical
                        cm_dups.append(o)
                elif prov.startswith("40000") and len(prov) == 8:
                    cm_dups.append(o)
                else:
                    cm_dups.append(o)  # weird tier, treat as dup
            if not canonical or not cm_dups:
                continue  # nothing to merge

            canonical_prov = canonical["tier_accounts"][ACP]["provisions"]

            for dup in cm_dups:
                dup_ta = (dup.get("tier_accounts") or {}).get(ACP, {}) or {}
                dup_prov = dup_ta.get("provisions", "")
                dup_reserve = dup_ta.get("reserve", "")
                old_accounts = [a for a in (dup_prov, dup_reserve) if a]

                # 1. Remap JE lines
                async for je in db.journal_entries.find({"copropriete_id": ACP}, {"_id": 0}):
                    new_lines = []
                    changed = False
                    for ln in je.get("lines", []) or []:
                        nl = dict(ln)
                        # Remap account_number
                        if nl.get("account_number") in old_accounts:
                            nl["account_number"] = canonical_prov
                            nl["account_name"] = f"Coproprietaires - {canonical.get('name', '')[:30]}"
                            nl["migrated_from_cm_account"] = ln.get("account_number")
                            changed = True
                            total_je_lines_updated += 1
                        # Remap third_party_id
                        if nl.get("third_party_id") == dup["id"]:
                            nl["third_party_id"] = canonical["id"]
                            nl["third_party_type"] = "owner"
                            changed = True
                        new_lines.append(nl)
                    if changed:
                        await db.journal_entries.update_one(
                            {"id": je["id"]},
                            {"$set": {"lines": new_lines}},
                        )

                # 2. Remap fund_calls : owner_id + account_number
                fc_count = 0
                async for fc in db.fund_calls.find({"copropriete_id": ACP}, {"_id": 0}):
                    updates = {}
                    if fc.get("owner_id") == dup["id"]:
                        updates["owner_id"] = canonical["id"]
                    if fc.get("account_number") in old_accounts:
                        updates["account_number"] = canonical_prov
                    if updates:
                        await db.fund_calls.update_one({"id": fc["id"]}, {"$set": updates})
                        fc_count += 1
                total_fund_calls_remapped += fc_count

                # 3. Remap lots
                await db.lots.update_many(
                    {"copropriete_id": ACP, "owner_id": dup["id"]},
                    {"$set": {"owner_id": canonical["id"]}},
                )

                # 4. Remap invoices
                await db.invoices.update_many(
                    {"copropriete_id": ACP, "private_fee_owner_id": dup["id"]},
                    {"$set": {"private_fee_owner_id": canonical["id"]}},
                )

                # 5. Remap bank_transactions
                await db.bank_transactions.update_many(
                    {"copropriete_id": ACP, "counterparty_owner_id": dup["id"]},
                    {"$set": {"counterparty_owner_id": canonical["id"]}},
                )

                # 6. Delete the CM PCMN accounts (now empty) from this ACP
                for acc in old_accounts:
                    has_movements = await db.journal_entries.count_documents({
                        "copropriete_id": ACP,
                        "lines.account_number": acc,
                    })
                    if has_movements == 0:
                        res = await db.pcmn_accounts.delete_one({
                            "copropriete_id": ACP, "number": acc,
                        })
                        total_pcmn_deleted += res.deleted_count

                # 7. Delete the dup owner doc
                # Before deleting, check if dup has tier_accounts for OTHER ACPs
                # (it shouldn't, but be safe)
                dup_ta_full = dup.get("tier_accounts") or {}
                if len(dup_ta_full) > 1:
                    # remove only this ACP from its tier_accounts, keep doc
                    new_ta = {k: v for k, v in dup_ta_full.items() if k != ACP}
                    await db.owners.update_one(
                        {"id": dup["id"]},
                        {"$set": {"tier_accounts": new_ta}},
                    )
                else:
                    await db.owners.delete_one({"id": dup["id"]})
                    total_owners_deleted += 1

                print(f"  [{aux}] {canonical['name'][:30]:<32} | CM {dup_prov}+{dup_reserve} -> Optipro {canonical_prov} | "
                      f"fund_calls={fc_count}")

    print(f"\n=== SUMMARY ===")
    print(f"Owners deleted (dups) : {total_owners_deleted}")
    print(f"JE lines updated (account migrated) : {total_je_lines_updated}")
    print(f"Fund calls remapped : {total_fund_calls_remapped}")
    print(f"PCMN CM accounts deleted : {total_pcmn_deleted}")


asyncio.run(main())
