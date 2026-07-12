"""iter90ds : REPAIR SCRIPT

Realigne `internal_reference` des factures existantes pour qu'elles soient :
- Strictement sequentielles (aucun trou, aucun doublon)
- Ordonnees par (date ASC, created_at ASC, id ASC) - deterministe
- Grouppees par exercice fiscal via le prefixe defini sur fiscal_years
- Fallback : `FA-YYYY-` quand aucune FY n'a de prefixe custom

USAGE :
  # DRY RUN (recommande - affiche les changements sans les appliquer)
  python -m scripts.repair_iter90ds_invoice_facturier --dry-run

  # APPLY (execute vraiment - IRREVERSIBLE)
  python -m scripts.repair_iter90ds_invoice_facturier --apply

  # Cibler une seule ACP (--copro-id)
  python -m scripts.repair_iter90ds_invoice_facturier --apply --copro-id XXX

L'ancienne reference est preservee dans `internal_reference_legacy` pour
tracabilite d'audit.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def _get_prefix_for_invoice(db, inv: dict) -> str:
    """Recupere le prefixe applicable a une facture (via FY couvrant sa date)."""
    inv_date = (inv.get("date") or "")[:10]
    copro_id = inv.get("copropriete_id") or ""
    fy = None
    if inv_date and copro_id:
        fy = await db.fiscal_years.find_one(
            {
                "copropriete_id": copro_id,
                "start_date": {"$lte": inv_date},
                "end_date": {"$gte": inv_date},
            },
            {"_id": 0, "invoice_number_prefix": 1},
        )
    prefix_conf = (fy or {}).get("invoice_number_prefix") or ""
    if prefix_conf:
        return prefix_conf
    year = inv_date[:4] if inv_date else datetime.now(timezone.utc).strftime("%Y")
    return f"FA-{year}-"


async def repair(dry_run: bool = True, copro_id_filter: str = None):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Filtre facultatif par ACP
    q = {}
    if copro_id_filter:
        q["copropriete_id"] = copro_id_filter

    # Recupere toutes les factures, triees par (date, created_at, id)
    invoices = await db.invoices.find(q, {"_id": 0}).to_list(500000)
    invoices.sort(key=lambda i: (
        (i.get("date") or ""),
        (i.get("created_at") or ""),
        (i.get("id") or ""),
    ))

    # Groupe par (copro_id, prefixe applicable)
    groups: dict[tuple[str, str], list[dict]] = {}
    for inv in invoices:
        prefix = await _get_prefix_for_invoice(db, inv)
        copro = inv.get("copropriete_id", "") or ""
        key = (copro, prefix)
        groups.setdefault(key, []).append(inv)

    total_changes = 0
    total_scanned = 0
    per_group_summary: list[str] = []

    for (copro, prefix), items in sorted(groups.items()):
        # Assigne 0001, 0002, ... dans l'ordre chronologique
        seq_pat = re.compile(f"^{re.escape(prefix)}([0-9]+)$")
        changes_in_group = 0
        for idx, inv in enumerate(items, start=1):
            total_scanned += 1
            expected_ref = f"{prefix}{idx:04d}"
            current_ref = inv.get("internal_reference", "") or ""
            if current_ref == expected_ref:
                continue
            changes_in_group += 1
            total_changes += 1
            if dry_run:
                print(
                    f"  [DRY] {inv.get('id','')[:8]} date={inv.get('date','')} "
                    f"'{current_ref}' -> '{expected_ref}' (copro={copro[:8]})"
                )
            else:
                # Applique le renommage + preserve l'ancienne ref
                update = {"$set": {"internal_reference": expected_ref}}
                if current_ref:
                    update["$set"]["internal_reference_legacy"] = current_ref
                await db.invoices.update_one({"id": inv["id"]}, update)
        per_group_summary.append(
            f"  {copro[:8]} prefix='{prefix}' : {len(items)} factures, {changes_in_group} renumerotees"
        )

    print("=" * 60)
    for line in per_group_summary:
        print(line)
    print("=" * 60)
    action = "TROUVEES" if dry_run else "APPLIQUEES"
    print(f"Total : {total_scanned} scannees, {total_changes} corrections {action}")
    if dry_run and total_changes > 0:
        print("\n>> Relancez avec --apply pour effectuer les changements.")


def main():
    parser = argparse.ArgumentParser(description="Realigne internal_reference des factures")
    parser.add_argument("--dry-run", action="store_true", help="Simule sans modifier (par defaut)")
    parser.add_argument("--apply", action="store_true", help="Applique reellement les changements")
    parser.add_argument("--copro-id", type=str, default=None, help="Cible une seule ACP")
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        args.dry_run = True
    if args.apply and args.dry_run:
        print("ERREUR : choisir --apply OU --dry-run, pas les deux.")
        sys.exit(1)

    asyncio.run(repair(dry_run=not args.apply, copro_id_filter=args.copro_id))


if __name__ == "__main__":
    main()
