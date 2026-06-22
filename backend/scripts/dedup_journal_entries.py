#!/usr/bin/env python3
"""Dedup duplicate FI/OD journal entries created by repeated runs of
`commit-journals` (or commit-bank-statement) in the import wizard.

Bug context (iter72septies) :
- ACP Gaura had 618 FI journal_entries while only 206 unique signatures exist.
- Each (date, reference, lines amounts) was duplicated 3x.
- The expense list endpoint then triple-counts every bank fee :
  650 Frais bancaires shows 894.96 EUR (3 x 298.32 PDF).

Strategy :
- Group FI/OD JEs by (copropriete_id, date, reference, sorted lines summary).
- Keep the earliest by `created_at`.
- Delete the rest.
- Be careful to NOT touch AC (Achats) since each has a unique invoice link.

Note : this script is idempotent. Running it again returns 0.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print("=== Dedup duplicate FI/OD journal_entries ===\n")

    total_deleted = 0
    affected_acps = 0

    async for cop in db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}):
        ACP = cop["id"]
        seen: dict[tuple, list[dict]] = {}
        async for je in db.journal_entries.find(
            {"copropriete_id": ACP, "journal_type": {"$in": ["FI", "OD"]}},
            {"_id": 0},
        ):
            lines_sig = tuple(sorted(
                f"{ln.get('account_number','')}:{round(float(ln.get('debit',0) or 0) - float(ln.get('credit',0) or 0), 2)}"
                for ln in (je.get("lines") or [])
            ))
            sig = (
                je.get("date", ""),
                je.get("reference", ""),
                je.get("journal_type", ""),
                lines_sig,
            )
            seen.setdefault(sig, []).append(je)

        dups = {k: v for k, v in seen.items() if len(v) > 1}
        if not dups:
            continue
        affected_acps += 1
        deleted_in_acp = 0
        for sig, jes in dups.items():
            # Keep the EARLIEST (oldest created_at). The first stays.
            jes.sort(key=lambda j: j.get("created_at", ""))
            keep = jes[0]
            for dup in jes[1:]:
                await db.journal_entries.delete_one({"id": dup["id"]})
                deleted_in_acp += 1
        total_deleted += deleted_in_acp
        print(f"  {ACP[:8]} {cop['name']:<15} : {deleted_in_acp} JEs deleted "
              f"({len(dups)} dup signatures)")

    print(f"\n=== SUMMARY ===")
    print(f"ACPs affected : {affected_acps}")
    print(f"JEs deleted : {total_deleted}")

    # ------------------------------------------------------------------
    # Bank transactions + bank_statements were also created N times when
    # the user clicked "commit-journals" multiple times. Same dedup logic.
    # ------------------------------------------------------------------
    print("\n=== Dedup bank_transactions ===")
    bt_deleted = 0
    async for cop in db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}):
        ACP = cop["id"]
        seen_tx: dict[tuple, list[dict]] = {}
        async for t in db.bank_transactions.find({"copropriete_id": ACP}, {"_id": 0}):
            sig = (
                t.get("date", ""),
                (t.get("reference") or t.get("communication") or "")[:50],
                round(float(t.get("amount", 0) or 0), 2),
                t.get("account_number", ""),
            )
            seen_tx.setdefault(sig, []).append(t)
        dups_tx = {k: v for k, v in seen_tx.items() if len(v) > 1}
        if not dups_tx:
            continue
        local_deleted = 0
        for sig, lst in dups_tx.items():
            lst.sort(key=lambda x: x.get("created_at", "") or "")
            for dup in lst[1:]:
                await db.bank_transactions.delete_one({"id": dup["id"]})
                local_deleted += 1
        bt_deleted += local_deleted
        print(f"  {ACP[:8]} {cop['name']:<15} : {local_deleted} bank_transactions deleted")
    print(f"Total bank_transactions deleted : {bt_deleted}")

    print("\n=== Dedup bank_statements ===")
    bs_deleted = 0
    async for cop in db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}):
        ACP = cop["id"]
        seen_bs: dict[tuple, list[dict]] = {}
        async for s in db.bank_statements.find({"copropriete_id": ACP}, {"_id": 0}):
            sig = (
                s.get("statement_date") or s.get("date") or "",
                s.get("statement_number") or s.get("reference") or "",
                round(float(s.get("end_balance", 0) or s.get("total_amount", 0) or 0), 2),
                s.get("account_number", ""),
            )
            seen_bs.setdefault(sig, []).append(s)
        dups_bs = {k: v for k, v in seen_bs.items() if len(v) > 1}
        if not dups_bs:
            continue
        local_deleted = 0
        for sig, lst in dups_bs.items():
            lst.sort(key=lambda x: x.get("created_at", "") or "")
            for dup in lst[1:]:
                await db.bank_statements.delete_one({"id": dup["id"]})
                local_deleted += 1
        bs_deleted += local_deleted
        print(f"  {ACP[:8]} {cop['name']:<15} : {local_deleted} bank_statements deleted")
    print(f"Total bank_statements deleted : {bs_deleted}")


asyncio.run(main())
