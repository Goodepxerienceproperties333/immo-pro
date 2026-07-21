"""iter90ji-b : Fix KASH - lie le paiement bancaire KASH au proprietaire Goovaerts.

Contexte
--------
User a signale qu'un paiement bancaire KASH n'est pas rattache a la fiche du
proprietaire Goovaerts (probablement parce que la communication VCS/description
ne matche pas). Ce script identifie la/les bank_transaction(s) KASH par
description ou montant, et force le lien avec l'owner Goovaerts.

Usage
-----
    python -m scripts.fix_kash_goovaerts_link \
        --owner-name-regex 'goovaerts' \
        --description-regex 'KASH'
    Ajouter --execute pour appliquer.

Rapport : /tmp/fix_kash_goovaerts_link_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # 1) Localise l'owner Goovaerts
    q_owner = {"$or": [
        {"name": {"$regex": args.owner_name_regex, "$options": "i"}},
        {"last_name": {"$regex": args.owner_name_regex, "$options": "i"}},
    ]}
    if args.copropriete_id:
        q_owner["$or"].extend([
            {"copropriete_id": args.copropriete_id},
            {"copropriete_ids": args.copropriete_id},
        ])
    owners = await db.owners.find(q_owner, {"_id": 0, "id": 1, "name": 1, "last_name": 1, "first_name": 1, "copropriete_id": 1, "copropriete_ids": 1}).to_list(20)
    if not owners:
        print(f"[fix-kash] Aucun owner matchant '{args.owner_name_regex}' trouve.")
        return 1
    print(f"[fix-kash] {len(owners)} owner(s) potentiel(s) :")
    for o in owners:
        print(f"   - {o['id'][:12]} : {o.get('name') or (o.get('last_name','') + ' ' + o.get('first_name',''))}")
    if len(owners) > 1 and not args.pick_first:
        print("[fix-kash] Plusieurs owners matchant. Utilise --pick-first pour prendre le 1er ou affine --owner-name-regex.")
        return 1
    owner = owners[0]
    owner_id = owner["id"]
    print(f"[fix-kash] Owner cible : {owner_id[:12]} ({owner.get('name') or owner.get('last_name','')})")

    # 2) Localise les bank_transactions KASH non lettrees
    q_txn = {
        "$or": [
            {"description": {"$regex": args.description_regex, "$options": "i"}},
            {"communication": {"$regex": args.description_regex, "$options": "i"}},
            {"counterparty_name": {"$regex": args.description_regex, "$options": "i"}},
        ],
    }
    if args.copropriete_id:
        q_txn["copropriete_id"] = args.copropriete_id
    if args.amount:
        # Match sur montant absolu (avec tolerance 0.01)
        amt = float(args.amount)
        q_txn["$and"] = q_txn.get("$and", []) + [
            {"$or": [
                {"amount": {"$gte": amt - 0.01, "$lte": amt + 0.01}},
                {"amount": {"$gte": -amt - 0.01, "$lte": -amt + 0.01}},
            ]},
        ]
    txns = await db.bank_transactions.find(q_txn, {"_id": 0}).to_list(50)
    print(f"[fix-kash] {len(txns)} bank_transaction(s) matchant '{args.description_regex}'")
    for t in txns[:10]:
        matched = t.get("matched", False)
        cp = t.get("counterparty_id", "")
        print(f"   - {t.get('id','')[:12]} date={t.get('date')} amt={t.get('amount')} desc={(t.get('description','') or '')[:60]!r} matched={matched} cp={cp[:12] if cp else '-'}")

    if not txns:
        print("[fix-kash] Rien a corriger.")
        _write_report(owner, [], executed=False)
        return 0
    if not args.execute:
        print("\n[fix-kash] DRY-RUN. Relance avec --execute.")
        _write_report(owner, txns, executed=False)
        return 0

    updated = 0
    for t in txns:
        r = await db.bank_transactions.update_one(
            {"id": t["id"]},
            {"$set": {
                "counterparty_id": owner_id,
                "counterparty_type": "owner",
                "counterparty_name": (owner.get("name") or f"{owner.get('last_name','')} {owner.get('first_name','')}".strip()),
                "matched": True,
                "match_type": "owner",
                "matched_to": owner_id,
            }},
        )
        if r.modified_count:
            updated += 1
            # Regenere le JE FI avec la nouvelle contrepartie
            try:
                from auto_entries import generate_bank_entry
                await generate_bank_entry(db, {**t, "counterparty_id": owner_id, "counterparty_type": "owner",
                                                 "matched": True, "match_type": "owner", "matched_to": owner_id})
            except Exception as e:
                print(f"   [WARN] regen JE echouee pour {t['id']}: {e}")
    print(f"[fix-kash] {updated} bank_transactions lies a l'owner Goovaerts. JEs FI regeneres.")
    _write_report(owner, txns, executed=True, updated=updated)
    return 0


def _write_report(owner, txns, executed: bool, updated: int = 0):
    out = {
        "executed": executed,
        "owner": {"id": owner.get("id"), "name": owner.get("name")},
        "txn_count": len(txns),
        "updated": updated,
        "txns": [{"id": t.get("id"), "date": t.get("date"), "amount": t.get("amount"),
                  "description": t.get("description")} for t in txns],
    }
    with open("/tmp/fix_kash_goovaerts_link_report.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print("[fix-kash] Rapport ecrit : /tmp/fix_kash_goovaerts_link_report.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner-name-regex", default="goovaerts", help="Regex pour matcher l'owner (defaut : goovaerts)")
    p.add_argument("--description-regex", default="KASH", help="Regex pour matcher la bank_transaction (defaut : KASH)")
    p.add_argument("--amount", default="", help="Montant exact optionnel pour reduire les faux positifs")
    p.add_argument("--copropriete-id", default="", help="Restreint a une ACP")
    p.add_argument("--pick-first", action="store_true", help="Prend le 1er owner si plusieurs matchent")
    p.add_argument("--execute", action="store_true", help="Applique (defaut : dry-run)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
