#!/usr/bin/env python3
"""
cleanup_expense_categories.py — Fusion des natures de depense doublons
======================================================================

Detecte et fusionne les natures de depense en doublon (nom tres similaire).
Reassigne toutes les factures et ecritures vers la nature conservee.

Usage :
    python cleanup_expense_categories.py                      # dry-run
    python cleanup_expense_categories.py --apply              # execute
    python cleanup_expense_categories.py --copro <ID>         # filtre ACP
    python cleanup_expense_categories.py --apply --copro <ID> # execute pour une ACP
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
    print(f"  NATURES DE DEPENSE — FUSION DOUBLONS — {mode_label}")
    print(f"{'='*60}\n")

    q = {}
    if COPRO_FILTER:
        q["copropriete_id"] = COPRO_FILTER

    cats = await db.expense_categories.find(q, {"_id": 1, "id": 1, "name": 1, "account_number": 1, "copropriete_id": 1}).to_list(5000)
    print(f"  Natures de depense trouvees : {len(cats)}\n")

    # Group by (copropriete_id, normalized name)
    groups = {}
    for c in cats:
        key = (c.get("copropriete_id", ""), norm(c.get("name", "")))
        if not key[1]:
            continue
        groups.setdefault(key, []).append(c)

    # Also do fuzzy grouping: merge groups with similar normalized names within same ACP
    copro_groups = {}
    for (copro, nname), accs in groups.items():
        copro_groups.setdefault(copro, []).append((nname, accs))

    merged_groups = {}
    for copro, name_groups in copro_groups.items():
        # Build clusters
        used = set()
        for i, (name_a, accs_a) in enumerate(name_groups):
            if i in used:
                continue
            cluster = list(accs_a)
            cluster_name = name_a
            for j, (name_b, accs_b) in enumerate(name_groups):
                if j <= i or j in used:
                    continue
                if similar(name_a, name_b):
                    cluster.extend(accs_b)
                    used.add(j)
            if len(cluster) > 1:
                merged_groups[(copro, cluster_name)] = cluster

    print(f"  Groupes de doublons : {len(merged_groups)}\n")

    total_merged = 0
    total_invoices = 0

    for (copro, gname), cats_in_group in sorted(merged_groups.items()):
        # Keep the one with most invoices referencing it
        counts = []
        for c in cats_in_group:
            cnt = await db.invoices.count_documents({"expense_category_id": c.get("id", ""), "copropriete_id": copro})
            counts.append((cnt, c))
        counts.sort(key=lambda x: -x[0])

        keep = counts[0][1]
        to_remove = [c for _, c in counts[1:]]

        print(f"  GROUPE '{cats_in_group[0].get('name','')}' (ACP: {copro[:12]}...)")
        print(f"    GARDER  : '{keep.get('name','')}' (id: {keep.get('id','')[:8]}, cpte: {keep.get('account_number','')}, {counts[0][0]} factures)")
        for cnt_val, r in counts[1:]:
            print(f"    FUSIONNER: '{r.get('name','')}' (id: {r.get('id','')[:8]}, cpte: {r.get('account_number','')}, {cnt_val} factures) → {keep.get('id','')[:8]}")

        if APPLY:
            for _, r in counts[1:]:
                rid = r.get("id", "")
                kid = keep.get("id", "")

                # Reassign invoices
                inv_res = await db.invoices.update_many(
                    {"expense_category_id": rid, "copropriete_id": copro},
                    {"$set": {"expense_category_id": kid}},
                )
                total_invoices += inv_res.modified_count

                # Delete the duplicate category
                await db.expense_categories.delete_one({"_id": r["_id"]})
                total_merged += 1
                print(f"      → '{r.get('name','')}' supprimee, {inv_res.modified_count} factures reassignees")
        else:
            total_merged += len(to_remove)
        print()

    print(f"{'='*60}")
    print(f"  RESUME")
    print(f"    Natures fusionnees   : {total_merged}")
    if APPLY:
        print(f"    Factures reassignees : {total_invoices}")
    else:
        print(f"    (dry-run — relancez avec --apply pour executer)")
    print(f"{'='*60}\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
