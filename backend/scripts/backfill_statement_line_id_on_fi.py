"""iter90jm : Backfill des champs Master/Slave `statement_line_id` et
`bank_statement_id` sur les JE FI auto-generees existantes.

Contexte
--------
Depuis iter90jm, `auto_entries.generate_bank_entry` ecrit systematiquement
`statement_line_id` (= txn.id) et `bank_statement_id` (= stmt.id) sur les
FIs. Les FIs anciennes n'ont que `source_id` (= txn.id). Ce script rattrape
ces FIs :

1. Lit les FI auto (`auto_generated=True`, `source_type=bank_txn`).
2. Copie `source_id` -> `statement_line_id`.
3. Lit `bank_transactions.statement_id` pour peupler `bank_statement_id`.
4. Idempotent : relance -> 0 modification.

Usage
-----
    python -m scripts.backfill_statement_line_id_on_fi           # dry-run
    python -m scripts.backfill_statement_line_id_on_fi --execute

Rapport : /tmp/backfill_statement_line_id_on_fi_report.json
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
    fis = await db.journal_entries.find(q, {
        "_id": 0, "id": 1, "reference": 1, "source_id": 1,
        "statement_line_id": 1, "bank_statement_id": 1,
    }).to_list(200000)
    print(f"[backfill] {len(fis)} JEs FI auto scannees.")

    # Index txn -> statement_id (une seule requete)
    txn_ids = list({fi["source_id"] for fi in fis if fi.get("source_id")})
    txn_to_stmt: dict[str, str] = {}
    if txn_ids:
        # batch par 5000
        for i in range(0, len(txn_ids), 5000):
            batch = txn_ids[i:i + 5000]
            async for t in db.bank_transactions.find(
                {"id": {"$in": batch}}, {"_id": 0, "id": 1, "statement_id": 1},
            ):
                txn_to_stmt[t["id"]] = t.get("statement_id") or ""

    to_update: list[tuple[str, dict]] = []
    already_ok = 0
    orphan_txn = 0
    for fi in fis:
        sid = fi["source_id"]
        current_sl = fi.get("statement_line_id") or ""
        current_bs = fi.get("bank_statement_id") or ""
        target_sl = sid
        target_bs = txn_to_stmt.get(sid, "")
        if not target_bs:
            orphan_txn += 1
        if current_sl == target_sl and current_bs == target_bs:
            already_ok += 1
            continue
        upd = {}
        if current_sl != target_sl:
            upd["statement_line_id"] = target_sl
        if current_bs != target_bs and target_bs:
            upd["bank_statement_id"] = target_bs
        if upd:
            to_update.append((fi["id"], upd))

    print(f"[backfill] {len(to_update)} JEs a mettre a jour, {already_ok} deja OK, "
          f"{orphan_txn} JEs sans txn parent (verrou orphelin - cleanup_orphaned_fi).")

    report = {
        "scanned": len(fis),
        "to_update": len(to_update),
        "already_ok": already_ok,
        "orphan_txn": orphan_txn,
        "executed": args.execute,
        "samples": [{"id": i, "upd": u} for i, u in to_update[:20]],
    }

    if args.execute and to_update:
        print(f"[backfill] EXECUTION : {len(to_update)} $set operations...")
        modified = 0
        for je_id, upd in to_update:
            res = await db.journal_entries.update_one({"id": je_id}, {"$set": upd})
            modified += res.modified_count
        report["modified"] = modified
        print(f"[backfill] {modified}/{len(to_update)} JEs mises a jour.")
    elif not args.execute:
        print("[backfill] DRY-RUN. Relance avec --execute.")

    path = "/tmp/backfill_statement_line_id_on_fi_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"[backfill] Rapport : {path}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique (defaut : dry-run)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
