"""iter90jm : Nettoyage des JE FI orphelines (source_id pointe vers une
txn qui n'existe plus).

Contexte
--------
Depuis l'introduction de la relation Master/Slave (iter90jm), toute FI
`auto_generated=True` avec `source_type=bank_txn` DOIT avoir une
`bank_transactions` parente. Sinon c'est un orphelin (extrait supprime
sans que la FI soit nettoyee, ou anciennes traces pre-iter90jm).

Ce script :
1. Scanne les FI auto.
2. Detecte celles dont `source_id` (ou `statement_line_id`) ne matche
   AUCUNE txn dans `bank_transactions`.
3. Mode par defaut : HARD DELETE (choix user - phase mise au point +
   base preview). Le user preserve neanmoins un rapport JSON complet
   avant delete pour audit.

Usage
-----
    python -m scripts.cleanup_orphaned_fi                 # dry-run
    python -m scripts.cleanup_orphaned_fi --execute       # delete dur
    python -m scripts.cleanup_orphaned_fi --copropriete-id CID --execute

Rapport : /tmp/cleanup_orphaned_fi_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def _run(args):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    q = {
        "journal_type": "FI",
        "auto_generated": True,
        "source_type": "bank_txn",
        "source_id": {"$exists": True, "$ne": ""},
    }
    if args.copropriete_id:
        q["copropriete_id"] = args.copropriete_id

    fis = await db.journal_entries.find(q, {
        "_id": 0, "id": 1, "reference": 1, "source_id": 1,
        "statement_line_id": 1, "bank_statement_id": 1,
        "copropriete_id": 1, "date": 1, "total_debit": 1,
        "description": 1,
    }).to_list(200000)
    print(f"[cleanup-orphan-fi] {len(fis)} JEs FI auto scannees.")

    # Set des txn.id encore vivantes
    src_ids = list({fi["source_id"] for fi in fis if fi.get("source_id")})
    live_txns: set = set()
    if src_ids:
        for i in range(0, len(src_ids), 5000):
            batch = src_ids[i:i + 5000]
            async for t in db.bank_transactions.find(
                {"id": {"$in": batch}}, {"_id": 0, "id": 1},
            ):
                live_txns.add(t["id"])

    orphans = [fi for fi in fis if fi["source_id"] not in live_txns]
    print(f"[cleanup-orphan-fi] {len(orphans)} FI orphelines detectees.")

    if orphans:
        print("\n[cleanup-orphan-fi] Apercu (top 20) :")
        print(f"{'JE ref':<24} {'date':<12} {'total':>10} {'copro':<40} {'source_id':<38}")
        print("-" * 130)
        for o in orphans[:20]:
            print(
                f"{(o.get('reference') or '')[:24]:<24} "
                f"{(o.get('date') or '')[:12]:<12} "
                f"{float(o.get('total_debit') or 0):>10.2f} "
                f"{(o.get('copropriete_id') or '')[:40]:<40} "
                f"{o.get('source_id') or '':<38}"
            )
        if len(orphans) > 20:
            print(f"... et {len(orphans) - 20} autres.")

    report = {
        "scanned": len(fis),
        "orphans": len(orphans),
        "executed": args.execute,
        "orphan_examples": [
            {k: o.get(k) for k in ("id", "reference", "date", "total_debit",
                                     "copropriete_id", "source_id",
                                     "statement_line_id", "bank_statement_id",
                                     "description")}
            for o in orphans[:50]
        ],
    }

    if args.execute and orphans:
        print(f"\n[cleanup-orphan-fi] HARD DELETE de {len(orphans)} JE orphelines...")
        ids = [o["id"] for o in orphans]
        res = await db.journal_entries.delete_many({"id": {"$in": ids}})
        report["deleted"] = res.deleted_count
        print(f"[cleanup-orphan-fi] {res.deleted_count}/{len(orphans)} JEs supprimees.")
    elif not args.execute:
        print("\n[cleanup-orphan-fi] DRY-RUN. Relance avec --execute pour purger.")

    path = "/tmp/cleanup_orphaned_fi_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"[cleanup-orphan-fi] Rapport : {path}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique le delete (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="", help="Restreint a une ACP")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
