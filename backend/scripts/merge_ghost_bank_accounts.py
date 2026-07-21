"""iter90jj : Fusion des comptes bancaires fantomes vers les comptes officiels.

Contexte
--------
Les imports historiques ont cree des comptes PCMN "raccourcis" (551331, 550000,
550732) au lieu du format canonique 8 chars (55133100, 55073200). Le user a
2 comptes bancaires officiels dans son ACP mais 5 comptes PCMN parasites dans
son bilan.

Ce script :
1. Parcourt `journal_entries.lines[]` : reecrit `account_number` selon le mapping.
2. Reecrit `bank_statements.account_number` de la meme facon (rare).
3. Supprime les entrees `pcmn_accounts` des comptes fantomes.
4. Rapport JSON detaillant les modifications.

Usage
-----
    python -m scripts.merge_ghost_bank_accounts \
        --copropriete-id ed728e70... \
        --mapping "551331:55133100,550000:55133100,550732:55073200"
    Ajouter --execute pour appliquer.

Rapport : /tmp/merge_ghost_bank_accounts_report.json
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


def _parse_mapping(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"Mapping invalide (pas de ':') : '{pair}'")
        k, v = pair.split(":", 1)
        mapping[k.strip()] = v.strip()
    return mapping


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    if not args.copropriete_id:
        print("[merge-ghost-bank] --copropriete-id obligatoire.")
        return 2
    mapping = _parse_mapping(args.mapping)
    if not mapping:
        print("[merge-ghost-bank] --mapping vide. Ex: --mapping '551331:55133100'.")
        return 2

    print(f"[merge-ghost-bank] ACP={args.copropriete_id[:12]}...")
    print(f"[merge-ghost-bank] Mapping : {mapping}")

    stats = {
        "journal_lines_rewritten": 0,
        "journal_entries_touched": 0,
        "bank_statements_rewritten": 0,
        "pcmn_accounts_deleted": 0,
    }
    details: list[dict] = []

    # 1) journal_entries.lines[]
    q_je = {
        "copropriete_id": args.copropriete_id,
        "lines.account_number": {"$in": list(mapping.keys())},
    }
    async for je in db.journal_entries.find(q_je, {"_id": 0, "id": 1, "lines": 1, "reference": 1}):
        new_lines = []
        touched = False
        for ln in (je.get("lines") or []):
            acc = (ln.get("account_number") or "").strip()
            if acc in mapping:
                new_acc = mapping[acc]
                nl = {**ln, "account_number": new_acc}
                new_lines.append(nl)
                touched = True
                stats["journal_lines_rewritten"] += 1
            else:
                new_lines.append(ln)
        if touched:
            stats["journal_entries_touched"] += 1
            details.append({"type": "je", "id": je["id"], "ref": je.get("reference", "")})
            if args.execute:
                await db.journal_entries.update_one(
                    {"id": je["id"]}, {"$set": {"lines": new_lines}},
                )

    # 2) bank_statements.account_number (rare - IBAN canonique attendu, mais on
    # protege quand meme les cas historiques ou l'ancien pcmn etait persiste)
    for ghost, official in mapping.items():
        q_stmt = {"copropriete_id": args.copropriete_id, "account_number": ghost}
        cnt = await db.bank_statements.count_documents(q_stmt)
        if cnt:
            stats["bank_statements_rewritten"] += cnt
            details.append({"type": "bank_statement_batch", "count": cnt, "from": ghost, "to": official})
            if args.execute:
                await db.bank_statements.update_many(q_stmt, {"$set": {"account_number": official}})

    # 3) pcmn_accounts - supprime les fantomes de cette ACP
    for ghost in mapping.keys():
        q_pcmn = {"copropriete_id": args.copropriete_id, "number": ghost}
        existing = await db.pcmn_accounts.find_one(q_pcmn, {"_id": 0, "name": 1})
        if existing:
            stats["pcmn_accounts_deleted"] += 1
            details.append({"type": "pcmn_del", "number": ghost, "name": existing.get("name", "")})
            if args.execute:
                await db.pcmn_accounts.delete_many(q_pcmn)

    print(f"[merge-ghost-bank] Stats : {json.dumps(stats, indent=2)}")
    print(f"[merge-ghost-bank] {'APPLIQUE' if args.execute else 'DRY-RUN'}")
    if details:
        print("\n[merge-ghost-bank] Details (top 10) :")
        for d in details[:10]:
            print(f"   {d}")
    _write_report(mapping, stats, details, executed=args.execute)
    return 0


def _write_report(mapping, stats, details, executed):
    out = {"executed": executed, "mapping": mapping, "stats": stats, "details": details}
    with open("/tmp/merge_ghost_bank_accounts_report.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print("[merge-ghost-bank] Rapport : /tmp/merge_ghost_bank_accounts_report.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--copropriete-id", required=True)
    p.add_argument("--mapping", required=True,
                   help="Mapping ghost:official separes par virgule. Ex: '551331:55133100,550000:55133100'")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
