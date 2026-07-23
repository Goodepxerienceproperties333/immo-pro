#!/usr/bin/env python3
"""
cleanup_pcmn.py — Nettoyage du plan comptable PCMN
===================================================

Actions :
1. Fusionne les comptes doublons de classe 6 (ex: Ascenseurs, Defense en justice)
   → garde le compte au numero le plus court (le plus standard)
2. Corrige 6140 = Assurance Incendie, deplace "Commissaire aux comptes" vers 6141
3. Reassigne toutes les references (journal_entries, invoices, expense_categories)

Usage :
    python cleanup_pcmn.py                          # dry-run (affiche les actions)
    python cleanup_pcmn.py --apply                  # execute les modifications
    python cleanup_pcmn.py --copro <ID>             # filtre par ACP
    python cleanup_pcmn.py --apply --copro <ID>     # execute pour une ACP
"""
import asyncio
import sys
import os
import re
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "nextge_copro")

APPLY = "--apply" in sys.argv
COPRO_FILTER = None
if "--copro" in sys.argv:
    idx = sys.argv.index("--copro")
    COPRO_FILTER = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None


def norm(name: str) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    return len(wa & wb) / max(len(wa), len(wb)) >= 0.7


async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    mode_label = "APPLY" if APPLY else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  PCMN CLEANUP — {mode_label}")
    print(f"{'='*60}\n")

    # --- Scope ---
    q = {"class_num": 6}
    if COPRO_FILTER:
        q["copropriete_id"] = COPRO_FILTER
        print(f"  Filtre ACP : {COPRO_FILTER}\n")

    accounts = await db.pcmn_accounts.find(q, {"_id": 1, "number": 1, "name": 1, "copropriete_id": 1}).to_list(5000)
    print(f"  Comptes classe 6 trouves : {len(accounts)}")

    # Group by (copropriete_id, normalized name)
    groups = {}
    for acc in accounts:
        key = (acc.get("copropriete_id", ""), norm(acc.get("name", "")))
        if not key[1]:
            continue
        groups.setdefault(key, []).append(acc)

    # Find duplicates
    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"  Groupes de doublons detectes : {len(duplicates)}\n")

    total_merged = 0
    total_entries_updated = 0
    total_invoices_updated = 0
    total_cats_updated = 0

    for (copro_id, norm_name), accs in sorted(duplicates.items()):
        # Keep the account with the shortest number (most standard)
        accs_sorted = sorted(accs, key=lambda a: (len(a["number"]), a["number"]))
        keep = accs_sorted[0]
        to_remove = accs_sorted[1:]

        print(f"  GROUPE : '{accs[0].get('name','')}' (ACP: {copro_id[:12]}...)")
        print(f"    GARDER  : {keep['number']} - {keep.get('name','')}")
        for r in to_remove:
            print(f"    FUSIONNER: {r['number']} - {r.get('name','')} → {keep['number']}")

        if APPLY:
            for r in to_remove:
                old_num = r["number"]
                new_num = keep["number"]
                new_name = keep.get("name", "")

                # 1. Journal entries: update account_number in lines[]
                je_result = await db.journal_entries.update_many(
                    {"lines.account_number": old_num, "copropriete_id": copro_id},
                    {"$set": {
                        "lines.$[elem].account_number": new_num,
                        "lines.$[elem].account_name": new_name,
                    }},
                    array_filters=[{"elem.account_number": old_num}],
                )
                je_count = je_result.modified_count
                total_entries_updated += je_count

                # 2. Invoices: update account_number
                inv_result = await db.invoices.update_many(
                    {"account_number": old_num, "copropriete_id": copro_id},
                    {"$set": {"account_number": new_num}},
                )
                inv_count = inv_result.modified_count
                total_invoices_updated += inv_count

                # 3. Expense categories: update account_number
                cat_result = await db.expense_categories.update_many(
                    {"account_number": old_num, "copropriete_id": copro_id},
                    {"$set": {"account_number": new_num, "account_name": new_name}},
                )
                cat_count = cat_result.modified_count
                total_cats_updated += cat_count

                # 4. Delete the duplicate account
                await db.pcmn_accounts.delete_one({"_id": r["_id"]})
                total_merged += 1

                print(f"      → {old_num} supprime. JE:{je_count} INV:{inv_count} CAT:{cat_count} reassignes")
        else:
            total_merged += len(to_remove)
        print()

    # --- Special case: 6140 / 6141 ---
    print(f"\n  --- Correction 6140/6141 ---")
    q6140 = {"number": "6140"}
    if COPRO_FILTER:
        q6140["copropriete_id"] = COPRO_FILTER
    accounts_6140 = await db.pcmn_accounts.find(q6140, {"_id": 0, "number": 1, "name": 1, "copropriete_id": 1}).to_list(100)

    for acc in accounts_6140:
        name_lower = (acc.get("name") or "").lower()
        if "commissaire" in name_lower or "comm.aux" in name_lower:
            print(f"  6140 ({acc.get('copropriete_id','')[:12]}): '{acc.get('name','')}' → doit etre deplace vers 6141")
            if APPLY:
                copro = acc.get("copropriete_id", "")
                # Rename 6140 to "Assurance Incendie"
                await db.pcmn_accounts.update_one(
                    {"number": "6140", "copropriete_id": copro},
                    {"$set": {"name": "Assurance Incendie"}},
                )
                # Ensure 6141 exists
                existing_6141 = await db.pcmn_accounts.find_one({"number": "6141", "copropriete_id": copro})
                if not existing_6141:
                    await db.pcmn_accounts.insert_one({
                        "number": "6141",
                        "name": "Commissaire aux comptes",
                        "class_num": 6,
                        "type": "result",
                        "copropriete_id": copro,
                        "active": True,
                        "is_custom": True,
                    })
                    print(f"    → Compte 6141 cree : Commissaire aux comptes")
                else:
                    await db.pcmn_accounts.update_one(
                        {"number": "6141", "copropriete_id": copro},
                        {"$set": {"name": "Commissaire aux comptes"}},
                    )
                # Reassign journal entries from 6140 to 6141 where description matches
                je_res = await db.journal_entries.update_many(
                    {"lines.account_number": "6140", "copropriete_id": copro,
                     "$or": [
                         {"description": {"$regex": "commissaire", "$options": "i"}},
                         {"lines.description": {"$regex": "commissaire", "$options": "i"}},
                     ]},
                    {"$set": {
                        "lines.$[elem].account_number": "6141",
                        "lines.$[elem].account_name": "Commissaire aux comptes",
                    }},
                    array_filters=[{"elem.account_number": "6140"}],
                )
                print(f"    → {je_res.modified_count} ecritures 6140→6141 (commissaire)")
                # Reassign invoices
                inv_res = await db.invoices.update_many(
                    {"account_number": "6140", "copropriete_id": copro,
                     "description": {"$regex": "commissaire", "$options": "i"}},
                    {"$set": {"account_number": "6141"}},
                )
                print(f"    → {inv_res.modified_count} factures 6140→6141 (commissaire)")
                # Reassign expense categories
                cat_res = await db.expense_categories.update_many(
                    {"account_number": "6140", "copropriete_id": copro,
                     "name": {"$regex": "commissaire", "$options": "i"}},
                    {"$set": {"account_number": "6141", "account_name": "Commissaire aux comptes"}},
                )
                print(f"    → {cat_res.modified_count} natures 6140→6141 (commissaire)")
        else:
            print(f"  6140 ({acc.get('copropriete_id','')[:12]}): '{acc.get('name','')}' — OK (Assurance Incendie)")

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"  RESUME")
    print(f"    Comptes fusionnes    : {total_merged}")
    if APPLY:
        print(f"    Ecritures reassignees: {total_entries_updated}")
        print(f"    Factures reassignees : {total_invoices_updated}")
        print(f"    Natures reassignees  : {total_cats_updated}")
    else:
        print(f"    (dry-run — relancez avec --apply pour executer)")
    print(f"{'='*60}\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
