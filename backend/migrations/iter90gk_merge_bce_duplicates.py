"""iter90gk : Fusion des doublons BCE fournisseurs.

Strategie : pour chaque BCE duplique, garde le fournisseur "master" (le
plus utilise ou le premier ayant un copropriete_id) et fusionne les autres :
  - Reassigne toutes les references (invoices.supplier_id, journal_entries.lines.third_party_id)
  - Merge les tier_accounts (union des ACPs)
  - Merge les copropriete_ids (union)
  - Supprime les fournisseurs "victimes"

Puis re-cree l'index unique sur bce_number.

Usage :
  python -m migrations.iter90gk_merge_bce_duplicates            # dry-run
  python -m migrations.iter90gk_merge_bce_duplicates --apply   # applique
"""
import asyncio
import argparse
import os
import sys
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


async def _pick_master(sups: list, db) -> dict:
    """Choisit le fournisseur master parmi les doublons.
    Priorite :
    1. Celui avec le plus de journal_entries qui le referencent
    2. Sinon celui avec le plus d'invoices
    3. Sinon celui avec un copropriete_id + tier_accounts
    4. Sinon le premier (par ordre alphabetique d'id)
    """
    scored = []
    for s in sups:
        invoices = await db.invoices.count_documents({"supplier_id": s["id"]})
        je = await db.journal_entries.count_documents({"lines.third_party_id": s["id"]})
        has_copro = 1 if s.get("copropriete_id") else 0
        has_tier = 1 if s.get("tier_accounts") else 0
        score = (je * 1000) + (invoices * 100) + (has_copro * 10) + has_tier
        scored.append((score, s["id"], s))
    scored.sort(reverse=True)
    return scored[0][2]


async def merge_bce_duplicates(db, apply: bool = False) -> dict:
    from routes.suppliers import _norm_id
    report = {"pairs": [], "deleted": 0, "invoices_updated": 0, "je_updated": 0}

    by_bce = defaultdict(list)
    async for s in db.suppliers.find(
        {"bce_number": {"$type": "string", "$ne": ""}}, {"_id": 0}
    ):
        norm = _norm_id(s.get("bce_number", ""))
        if norm:
            by_bce[norm].append(s)

    for bce, sups in by_bce.items():
        if len(sups) <= 1:
            continue
        master = await _pick_master(sups, db)
        victims = [s for s in sups if s["id"] != master["id"]]
        pair_report = {
            "bce": bce, "master_id": master["id"], "master_name": master["name"],
            "victims": [], "invoices_moved": 0, "je_moved": 0,
        }
        merged_copros = set(master.get("copropriete_ids") or [])
        if master.get("copropriete_id"):
            merged_copros.add(master["copropriete_id"])
        merged_tier_accounts = dict(master.get("tier_accounts") or {})

        for v in victims:
            v_id = v["id"]
            invoices_moved = 0
            je_moved = 0
            # Reassign invoices
            inv_cnt = await db.invoices.count_documents({"supplier_id": v_id})
            if inv_cnt > 0 and apply:
                res = await db.invoices.update_many(
                    {"supplier_id": v_id}, {"$set": {"supplier_id": master["id"]}}
                )
                invoices_moved = res.modified_count
            elif inv_cnt > 0:
                invoices_moved = inv_cnt
            # Reassign journal_entries lines
            je_cnt = 0
            entries = await db.journal_entries.find(
                {"lines.third_party_id": v_id}, {"_id": 0, "id": 1, "lines": 1}
            ).to_list(10000)
            for e in entries:
                touched = False
                for ln in e.get("lines", []):
                    if ln.get("third_party_id") == v_id:
                        ln["third_party_id"] = master["id"]
                        touched = True
                if touched:
                    je_cnt += 1
                    if apply:
                        await db.journal_entries.update_one(
                            {"id": e["id"]}, {"$set": {"lines": e["lines"]}}
                        )
            je_moved = je_cnt
            # Merge tier_accounts + copro_ids
            for k, va in (v.get("tier_accounts") or {}).items():
                if k not in merged_tier_accounts:
                    merged_tier_accounts[k] = va
            for c in (v.get("copropriete_ids") or []):
                if c:
                    merged_copros.add(c)
            if v.get("copropriete_id"):
                merged_copros.add(v["copropriete_id"])
            pair_report["victims"].append({
                "id": v_id, "name": v["name"],
                "invoices_moved": invoices_moved, "je_moved": je_moved,
            })
            pair_report["invoices_moved"] += invoices_moved
            pair_report["je_moved"] += je_moved
            report["invoices_updated"] += invoices_moved
            report["je_updated"] += je_moved
            if apply:
                await db.suppliers.delete_one({"id": v_id})
                report["deleted"] += 1
        # Update master with merged copros/tiers
        if apply:
            await db.suppliers.update_one(
                {"id": master["id"]},
                {"$set": {
                    "copropriete_ids": list(merged_copros),
                    "tier_accounts": merged_tier_accounts,
                }},
            )
        report["pairs"].append(pair_report)

    return report


async def recreate_bce_index(db):
    try:
        await db.suppliers.drop_index("uq_supplier_bce")
    except Exception:
        pass
    try:
        await db.suppliers.create_index(
            "bce_number", unique=True, name="uq_supplier_bce",
            partialFilterExpression={"bce_number": {"$type": "string", "$gt": ""}},
        )
        return "created"
    except Exception as e:
        return f"failed: {e}"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== iter90gk merge BCE duplicates ({mode}) ===\n")

    report = await merge_bce_duplicates(db, apply=args.apply)
    for p in report["pairs"]:
        print(f"BCE={p['bce']}")
        print(f"  master: {p['master_id'][:12]} {p['master_name']}")
        for v in p["victims"]:
            print(f"  victim: {v['id'][:12]} {v['name']} -> {v['invoices_moved']} invoices, {v['je_moved']} JE entries moved to master")
        print()
    print(f"Total : {report['deleted']} suppliers deleted, {report['invoices_updated']} invoices reassigned, {report['je_updated']} JE entries reassigned")

    if args.apply:
        print("\n=== Re-creating unique BCE index ===")
        status = await recreate_bce_index(db)
        print(f"Index status: {status}")
        # Verify
        async for i in db.suppliers.list_indexes():
            if i.get("name") == "uq_supplier_bce":
                print(f"  Verified: {i.get('name')} key={i.get('key')} unique={i.get('unique')} sparse={i.get('sparse')}")


if __name__ == "__main__":
    asyncio.run(main())
