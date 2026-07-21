"""iter90jn : Nettoyage des factures orphelines (paid par une txn qui
n'existe plus dans bank_transactions).

Contexte
--------
Depuis iter90jn, `delete_statement` annule automatiquement les lettrages
avant de supprimer les txns. Mais les factures LEGACY (pre-iter90jn)
peuvent avoir `status="paid"` avec `paid_by_transaction_id(s)` pointant
vers des txns disparues (extraits supprimes sans cascade). Ce script :

1. Scanne les factures avec paid_by_transaction_id(s).
2. Verifie que CHAQUE txn referencee existe encore.
3. Si aucune ne reste : facture -> unpaid (unset paid_*).
4. Si certaines restent : recalcule le status (paid ou partially_paid).

Usage
-----
    python -m scripts.cleanup_orphaned_invoice_lettrage             # dry-run
    python -m scripts.cleanup_orphaned_invoice_lettrage --execute
    python -m scripts.cleanup_orphaned_invoice_lettrage --copropriete-id CID --execute

Rapport : /tmp/cleanup_orphaned_invoice_lettrage_report.json
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
        "$or": [
            {"paid_by_transaction_id": {"$exists": True, "$ne": ""}},
            {"paid_by_transaction_ids": {"$exists": True, "$ne": []}},
        ]
    }
    if args.copropriete_id:
        q["copropriete_id"] = args.copropriete_id

    invs = await db.invoices.find(q, {
        "_id": 0, "id": 1, "copropriete_id": 1, "status": 1,
        "paid_by_transaction_id": 1, "paid_by_transaction_ids": 1,
        "amount_ttc": 1, "total_amount": 1, "amount": 1,
        "amount_paid": 1,
    }).to_list(200000)
    print(f"[cleanup-orphan-invoice-lettrage] {len(invs)} factures avec lettrage scannees.")

    # Collect all referenced txn IDs
    all_tids: set = set()
    for inv in invs:
        if inv.get("paid_by_transaction_id"):
            all_tids.add(inv["paid_by_transaction_id"])
        for tid in (inv.get("paid_by_transaction_ids") or []):
            if tid:
                all_tids.add(tid)

    live_txns: set = set()
    tids_list = list(all_tids)
    for i in range(0, len(tids_list), 5000):
        batch = tids_list[i:i + 5000]
        async for t in db.bank_transactions.find(
            {"id": {"$in": batch}}, {"_id": 0, "id": 1, "amount": 1},
        ):
            live_txns.add(t["id"])

    changes: list[dict] = []
    for inv in invs:
        refs = []
        if inv.get("paid_by_transaction_id"):
            refs.append(inv["paid_by_transaction_id"])
        for tid in (inv.get("paid_by_transaction_ids") or []):
            if tid and tid not in refs:
                refs.append(tid)
        if not refs:
            continue
        surviving = [t for t in refs if t in live_txns]
        if len(surviving) == len(refs):
            continue  # all still alive
        # partial or full loss
        change = {
            "invoice_id": inv["id"],
            "copropriete_id": inv.get("copropriete_id"),
            "before_status": inv.get("status"),
            "before_refs": refs,
            "surviving_refs": surviving,
        }
        if not surviving:
            change["new_status"] = "unpaid"
            change["unset_all"] = True
        else:
            # Recompute total_paid from surviving txns
            total_paid = 0.0
            for tid in surviving:
                t = await db.bank_transactions.find_one({"id": tid}, {"_id": 0, "amount": 1})
                if t:
                    total_paid += abs(float(t.get("amount", 0) or 0))
            total_paid = round(total_paid, 2)
            inv_amount = round(float(
                inv.get("amount_ttc") or inv.get("total_amount") or inv.get("amount") or 0
            ), 2)
            is_full = inv_amount > 0 and abs(total_paid - inv_amount) < 0.01
            change["new_status"] = "paid" if is_full else "partially_paid"
            change["new_amount_paid"] = total_paid
        changes.append(change)

    print(f"[cleanup-orphan-invoice-lettrage] {len(changes)} factures a corriger.")

    if changes:
        print("\n[cleanup-orphan-invoice-lettrage] Apercu (top 20) :")
        for c in changes[:20]:
            print(f"  invoice {c['invoice_id'][:20]:<22} {c['before_status']:<15} -> {c['new_status']}")

    report = {
        "scanned": len(invs),
        "to_fix": len(changes),
        "executed": args.execute,
        "changes": changes[:200],
    }

    if args.execute and changes:
        print(f"\n[cleanup-orphan-invoice-lettrage] EXECUTION : {len(changes)} updates...")
        modified = 0
        for c in changes:
            if c.get("unset_all"):
                res = await db.invoices.update_one(
                    {"id": c["invoice_id"]},
                    {"$set": {"status": "unpaid"},
                     "$unset": {
                         "paid_at": "", "paid_by_transaction_id": "",
                         "paid_by_transaction_ids": "", "amount_paid": "",
                         "lettrage_code": "",
                     }},
                )
            else:
                res = await db.invoices.update_one(
                    {"id": c["invoice_id"]},
                    {"$set": {
                        "status": c["new_status"],
                        "amount_paid": c["new_amount_paid"],
                        "paid_by_transaction_ids": c["surviving_refs"],
                    }},
                )
            modified += res.modified_count
        report["modified"] = modified
        print(f"[cleanup-orphan-invoice-lettrage] {modified}/{len(changes)} factures corrigees.")
    elif not args.execute:
        print("[cleanup-orphan-invoice-lettrage] DRY-RUN. Relance avec --execute.")

    path = "/tmp/cleanup_orphaned_invoice_lettrage_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"[cleanup-orphan-invoice-lettrage] Rapport : {path}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="", help="Restreint a une ACP")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
