"""iter90jc : Repare les JEs financiers (FI) abandonnes sur compte 499 000
"Decaissement/Encaissement non identifie" apres qu'un lettrage tardif ait ete
effectue sur la transaction bancaire source.

Contexte du bug
---------------
Flow qui produit un JE "orphelin sur 499" :
1. User importe l'extrait bancaire (bank_transaction non lettree).
2. User COMPTABILISE l'extrait (POST /statements/{id}/post) AVANT le lettrage :
   `generate_bank_entry` cree un FI sur compte d'attente 499000
   (`Decaissement non identifie - ...`) car la txn n'a pas de contrepartie.
3. User RENTRE la facture AC (via wizard Optipro / manuel) : `Cr 44000042
   Baloise 60.76` -> la dette Baloise est correctement enregistree.
4. User lettre MANUELLEMENT la txn a la facture APRES coup. MAIS le
   POST /lettrage a bien contre-passe l'ancien FI 499 et cree un nouveau
   FI Dr 44000042 / Cr 550 - EN THEORIE.

Ce script GENERALISE : pour toute FI active (non reversal) qui contient
une ligne sur `499000` avec `source_type='bank_txn'`, on verifie si la
txn source est maintenant lettree. Si oui, on :
- Contre-passe le FI 499 (via `reverse_auto_entries`).
- Regenere le FI avec la vraie contrepartie via `generate_bank_entry`.

Idempotent : ne touche pas les FI deja bien branches (compte 44000XXX / 400XXXX).

Usage
-----
    python -m scripts.heal_499_bank_entries
    python -m scripts.heal_499_bank_entries --execute
    python -m scripts.heal_499_bank_entries --copropriete-id CID --execute

Rapport : /tmp/heal_499_bank_entries_report.json
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
from auto_entries import generate_bank_entry  # noqa: E402
from journal_reversals import reverse_auto_entries  # noqa: E402


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    q: dict = {
        "journal_type": "FI",
        "auto_generated": True,
        "manually_edited": {"$ne": True},
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
        "source_type": "bank_txn",
        "lines.account_number": "499000",
    }
    if args.copropriete_id:
        q["copropriete_id"] = args.copropriete_id
    fi_entries = await db.journal_entries.find(q, {"_id": 0}).to_list(10000)
    print(f"[heal-499] {len(fi_entries)} FI 499 actives detectees.")

    stats = {"scanned": len(fi_entries), "regenerated": 0, "skipped_still_orphan": 0,
             "skipped_no_txn": 0, "regen_errors": 0}
    details: list[dict] = []

    for je in fi_entries:
        src_id = je.get("source_id", "")
        if not src_id:
            stats["skipped_no_txn"] += 1
            continue
        txn = await db.bank_transactions.find_one({"id": src_id}, {"_id": 0})
        if not txn:
            stats["skipped_no_txn"] += 1
            details.append({"je_ref": je.get("reference"), "reason": "txn_not_found", "txn_id": src_id})
            continue
        # La txn a-t-elle desormais un match/counterparty exploitable ?
        has_match = bool(txn.get("matched"))
        has_cp = bool((txn.get("counterparty_id") or "").strip()) and \
                 (txn.get("counterparty_type") or "") in ("owner", "supplier")
        has_cat = bool(txn.get("category_splits"))
        if not (has_match or has_cp or has_cat):
            stats["skipped_still_orphan"] += 1
            details.append({
                "je_ref": je.get("reference"),
                "je_desc": je.get("description", "")[:80],
                "reason": "still_orphan",
                "hint": "Lettrez d'abord la transaction bancaire pour permettre le heal.",
            })
            continue
        # Action : contre-passe + regenere
        if not args.execute:
            details.append({
                "je_ref": je.get("reference"),
                "je_desc": je.get("description", "")[:80],
                "action": "would_reverse_and_regen",
                "match_type": txn.get("match_type", ""),
                "matched_to": (txn.get("matched_to") or "")[:12],
            })
            stats["regenerated"] += 1
            continue
        try:
            await reverse_auto_entries(
                db, "bank_txn", src_id,
                reason="heal_499_after_lettrage",
            )
            result = await generate_bank_entry(db, txn)
            if result:
                stats["regenerated"] += 1
                new_ref = result.get("reference", "")
                new_cp = ""
                for ln in result.get("lines", []):
                    acc = ln.get("account_number", "")
                    if acc and not acc.startswith("55") and not acc.startswith("499"):
                        new_cp = f"{acc} {ln.get('account_name','')[:30]}"
                        break
                details.append({
                    "je_ref": je.get("reference"),
                    "je_desc": je.get("description", "")[:80],
                    "action": "reversed_and_regenerated",
                    "new_ref": new_ref,
                    "new_counterparty": new_cp,
                })
            else:
                stats["regen_errors"] += 1
                details.append({"je_ref": je.get("reference"), "error": "generate_bank_entry returned None"})
        except Exception as e:
            stats["regen_errors"] += 1
            details.append({"je_ref": je.get("reference"), "error": str(e)})

    print(f"[heal-499] stats : {json.dumps(stats, indent=2)}")
    if not args.execute:
        print("\n[heal-499] DRY-RUN - top 10 actions :")
        for d in details[:10]:
            print(f"   {d}")
        print("\n[heal-499] Relance avec --execute pour appliquer.")
    with open("/tmp/heal_499_bank_entries_report.json", "w") as f:
        json.dump({"executed": args.execute, "stats": stats, "details": details}, f, indent=2, ensure_ascii=False)
    print("[heal-499] Rapport JSON : /tmp/heal_499_bank_entries_report.json")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="", help="Restreint a une ACP")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
