"""iter90jl : Fusion de plusieurs fiches proprietaire en une seule.

Cas d'usage
-----------
Le user a 2 (ou N) fiches proprietaire pour la MEME personne physique (ex :
"KASH - GOOVAERTS Paul & Maite" et "KASH - GOOVAERTS Jean-Paul & Maite").
Ce script fusionne toutes les fiches sources vers une fiche cible unique en :

1. Reecrivant journal_entries.lines[].third_party_id (source -> target)
2. Reecrivant lots[].owner_id + lots[].owner_ids[] (source -> target)
3. Reecrivant invoices[].owner_id / private_fee_allocations[]
4. Reecrivant mutations[] (owner references)
5. Reecrivant owner_payments[].owner_id (si collection existe)
6. Union des tier_accounts (garde toutes les ACPs des sources)
7. Union des lot_ids
8. Optionnel : mise a jour du name / auxiliary_code sur la cible
9. Suppression des fiches sources apres fusion

Idempotent : relancer 2x ne fait rien de plus.

Usage
-----
    # Dry-run (recommande d'abord)
    python -m scripts.merge_owners \\
        --sources ID1,ID2 \\
        --target ID3 \\
        --new-name "KASH - GOOVAERTS Jean-Paul & Maite"

    # Execution reelle
    python -m scripts.merge_owners \\
        --sources ID1,ID2 \\
        --target ID3 \\
        --new-name "KASH - GOOVAERTS Jean-Paul & Maite" \\
        --execute

    # Auto-detection par nom (recherche les 2+ fiches partageant un nom)
    python -m scripts.merge_owners \\
        --auto-by-name "GOOVAERTS" \\
        --new-name "KASH - GOOVAERTS Jean-Paul & Maite" \\
        --keep-first \\
        --execute

Rapport : /tmp/merge_owners_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _resolve_targets_by_name(db, name_pattern: str, keep_first: bool):
    """Retourne (sources[], target) resolus depuis un pattern nom."""
    regex = re.compile(re.escape(name_pattern), re.IGNORECASE)
    matches = await db.owners.find(
        {"name": {"$regex": regex.pattern, "$options": "i"}},
        {"_id": 0},
    ).to_list(100)
    if len(matches) < 2:
        raise SystemExit(
            f"[merge-owners] Auto-detection : seulement {len(matches)} fiche(s) matche(nt) "
            f"'{name_pattern}'. Fusion impossible (besoin de 2+)."
        )
    # Trie par (created_at ASC, id ASC) pour stabilite
    matches.sort(key=lambda o: (o.get("created_at", ""), o.get("id", "")))
    if keep_first:
        target = matches[0]
        sources = matches[1:]
    else:
        target = matches[-1]
        sources = matches[:-1]
    return sources, target


async def _apply_merge(db, source_ids: list[str], target_id: str,
                        new_name: str | None, execute: bool) -> dict:
    report = {
        "executed": execute,
        "source_ids": source_ids,
        "target_id": target_id,
        "new_name": new_name,
        "actions": [],
        "counts": {
            "journal_lines_rewritten": 0,
            "lots_owner_id_rewritten": 0,
            "lots_owner_ids_rewritten": 0,
            "invoices_owner_rewritten": 0,
            "invoice_allocations_rewritten": 0,
            "mutations_rewritten": 0,
            "owner_payments_rewritten": 0,
            "sources_deleted": 0,
            "tier_accounts_merged": 0,
        },
    }

    # 1. Charge la cible + sources
    target = await db.owners.find_one({"id": target_id}, {"_id": 0})
    if not target:
        raise SystemExit(f"[merge-owners] Cible {target_id} introuvable.")
    sources = await db.owners.find({"id": {"$in": source_ids}}, {"_id": 0}).to_list(100)
    if not sources:
        raise SystemExit(f"[merge-owners] Aucune source trouvee parmi {source_ids}.")
    src_ids_found = [s["id"] for s in sources]
    if set(src_ids_found) != set(source_ids):
        missing = set(source_ids) - set(src_ids_found)
        print(f"[merge-owners] Attention : sources introuvables {missing} - ignorees.")

    # 2. Union tier_accounts + lot_ids sur la cible
    merged_tier = dict(target.get("tier_accounts") or {})
    merged_lots = set(target.get("lot_ids") or [])
    conflicts_tier: list[dict] = []
    for src in sources:
        src_tier = src.get("tier_accounts") or {}
        for copro_id, accs in src_tier.items():
            if copro_id in merged_tier:
                # Conflit ? On garde ceux de la cible mais on log si different
                if merged_tier[copro_id] != accs:
                    conflicts_tier.append({
                        "copro_id": copro_id,
                        "source_id": src["id"],
                        "target_accs": merged_tier[copro_id],
                        "source_accs": accs,
                    })
            else:
                merged_tier[copro_id] = accs
                report["counts"]["tier_accounts_merged"] += 1
        for lot_id in (src.get("lot_ids") or []):
            merged_lots.add(lot_id)

    # 3. Update the target owner
    target_update = {"tier_accounts": merged_tier, "lot_ids": sorted(list(merged_lots)),
                     "updated_at": _now()}
    if new_name:
        target_update["name"] = new_name
    report["actions"].append({
        "action": "update_target",
        "target_id": target_id,
        "fields_updated": list(target_update.keys()),
        "merged_lot_count": len(merged_lots),
        "merged_tier_copros": len(merged_tier),
        "conflicts_tier": conflicts_tier,
    })

    # 4. Rewrite JE lines : third_party_id source -> target
    je_qry = {"lines.third_party_id": {"$in": src_ids_found}}
    je_count = await db.journal_entries.count_documents(je_qry)
    report["actions"].append({
        "action": "rewrite_je_lines",
        "collection": "journal_entries",
        "docs_matched": je_count,
    })

    # 5. Rewrite lots.owner_id + owner_ids[]
    lots_owner_id = await db.lots.count_documents({"owner_id": {"$in": src_ids_found}})
    lots_owner_ids = await db.lots.count_documents({"owner_ids": {"$in": src_ids_found}})
    report["actions"].append({
        "action": "rewrite_lots",
        "docs_owner_id": lots_owner_id,
        "docs_owner_ids": lots_owner_ids,
    })

    # 6. Rewrite invoices
    inv_owner = await db.invoices.count_documents({"private_fee_owner_id": {"$in": src_ids_found}})
    inv_alloc = await db.invoices.count_documents(
        {"private_fee_allocations.owner_id": {"$in": src_ids_found}}
    )
    report["actions"].append({
        "action": "rewrite_invoices",
        "docs_private_fee_owner": inv_owner,
        "docs_allocations": inv_alloc,
    })

    # 7. Rewrite mutations
    mut_from = await db.mutations.count_documents({"from_owner_id": {"$in": src_ids_found}})
    mut_to = await db.mutations.count_documents({"to_owner_id": {"$in": src_ids_found}})
    report["actions"].append({
        "action": "rewrite_mutations",
        "docs_from": mut_from,
        "docs_to": mut_to,
    })

    if not execute:
        report["dry_run_note"] = "Aucune modification en DB. Relance avec --execute."
        return report

    # ---- EXECUTION ----
    # 4-exec. Journal_entries : $[<idx>] update
    async for je in db.journal_entries.find(je_qry, {"_id": 0, "id": 1, "lines": 1}):
        new_lines = []
        changed = False
        for ln in je.get("lines", []):
            tp = ln.get("third_party_id")
            if tp in src_ids_found:
                nln = dict(ln)
                nln["third_party_id"] = target_id
                new_lines.append(nln)
                changed = True
                report["counts"]["journal_lines_rewritten"] += 1
            else:
                new_lines.append(ln)
        if changed:
            await db.journal_entries.update_one(
                {"id": je["id"]}, {"$set": {"lines": new_lines}},
            )

    # 5-exec. Lots
    res_lots = await db.lots.update_many(
        {"owner_id": {"$in": src_ids_found}},
        {"$set": {"owner_id": target_id}},
    )
    report["counts"]["lots_owner_id_rewritten"] = res_lots.modified_count
    # owner_ids[] contains one of src : need per-doc
    async for lot in db.lots.find({"owner_ids": {"$in": src_ids_found}}, {"_id": 0, "id": 1, "owner_ids": 1}):
        new_ids = []
        for oid in (lot.get("owner_ids") or []):
            if oid in src_ids_found:
                if target_id not in new_ids:
                    new_ids.append(target_id)
            else:
                if oid not in new_ids:
                    new_ids.append(oid)
        await db.lots.update_one({"id": lot["id"]}, {"$set": {"owner_ids": new_ids}})
        report["counts"]["lots_owner_ids_rewritten"] += 1

    # 6-exec. Invoices
    res_inv1 = await db.invoices.update_many(
        {"private_fee_owner_id": {"$in": src_ids_found}},
        {"$set": {"private_fee_owner_id": target_id}},
    )
    report["counts"]["invoices_owner_rewritten"] = res_inv1.modified_count
    async for inv in db.invoices.find(
        {"private_fee_allocations.owner_id": {"$in": src_ids_found}},
        {"_id": 0, "id": 1, "private_fee_allocations": 1},
    ):
        new_alloc = []
        seen_target_in_alloc = False
        for a in (inv.get("private_fee_allocations") or []):
            if a.get("owner_id") in src_ids_found:
                # Merge le pourcentage vers la cible existante ou nouvelle entree
                pct = float(a.get("percentage") or 0)
                if seen_target_in_alloc:
                    # trouve la cible dans new_alloc
                    for a2 in new_alloc:
                        if a2.get("owner_id") == target_id:
                            a2["percentage"] = round(float(a2.get("percentage") or 0) + pct, 4)
                            break
                else:
                    new_alloc.append({**a, "owner_id": target_id, "percentage": pct})
                    seen_target_in_alloc = True
            else:
                if a.get("owner_id") == target_id:
                    seen_target_in_alloc = True
                new_alloc.append(a)
        await db.invoices.update_one({"id": inv["id"]}, {"$set": {"private_fee_allocations": new_alloc}})
        report["counts"]["invoice_allocations_rewritten"] += 1

    # 7-exec. Mutations
    res_mut1 = await db.mutations.update_many(
        {"from_owner_id": {"$in": src_ids_found}},
        {"$set": {"from_owner_id": target_id}},
    )
    res_mut2 = await db.mutations.update_many(
        {"to_owner_id": {"$in": src_ids_found}},
        {"$set": {"to_owner_id": target_id}},
    )
    report["counts"]["mutations_rewritten"] = res_mut1.modified_count + res_mut2.modified_count

    # 8-exec. owner_payments si collection existe
    try:
        colls = await db.list_collection_names()
        if "owner_payments" in colls:
            res_pay = await db.owner_payments.update_many(
                {"owner_id": {"$in": src_ids_found}},
                {"$set": {"owner_id": target_id}},
            )
            report["counts"]["owner_payments_rewritten"] = res_pay.modified_count
    except Exception as e:
        report["actions"].append({"action": "owner_payments_skip", "reason": str(e)})

    # 9-exec. Update la cible
    await db.owners.update_one({"id": target_id}, {"$set": target_update})

    # 10-exec. Supprime les sources
    res_del = await db.owners.delete_many({"id": {"$in": src_ids_found}})
    report["counts"]["sources_deleted"] = res_del.deleted_count

    return report


async def _run(args):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Resolution des sources / target
    if args.auto_by_name:
        sources, target = await _resolve_targets_by_name(
            db, args.auto_by_name, args.keep_first,
        )
        source_ids = [s["id"] for s in sources]
        target_id = target["id"]
        print(f"[merge-owners] Auto-detection '{args.auto_by_name}' :")
        print(f"  Cible : {target['name']} ({target_id})")
        for s in sources:
            print(f"  Source -> {s['name']} ({s['id']})")
    else:
        if not args.sources or not args.target:
            raise SystemExit("Options requises : --sources ID1,ID2 --target ID3")
        source_ids = [s.strip() for s in args.sources.split(",") if s.strip()]
        target_id = args.target.strip()

    if target_id in source_ids:
        raise SystemExit("target ne peut pas etre dans sources")

    report = await _apply_merge(
        db, source_ids, target_id, args.new_name, execute=args.execute,
    )

    path = "/tmp/merge_owners_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[merge-owners] Rapport : {path}")
    print(f"[merge-owners] Executed : {report['executed']}")
    print(f"[merge-owners] Counts : {json.dumps(report['counts'], indent=2, ensure_ascii=False)}")
    if not args.execute:
        print("\n[merge-owners] DRY-RUN. Relance avec --execute pour appliquer.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", default="", help="IDs sources (virgule-separated)")
    p.add_argument("--target", default="", help="ID de la fiche cible (fusion vers)")
    p.add_argument("--new-name", default=None, help="Nom final pour la cible")
    p.add_argument("--auto-by-name", default="",
                   help="Auto-detection : cherche N fiches matchant ce nom")
    p.add_argument("--keep-first", action="store_true",
                   help="Auto : garde la 1re fiche comme cible (defaut: derniere)")
    p.add_argument("--execute", action="store_true",
                   help="Applique (defaut : dry-run)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
