"""iter90gl : Data-heal pour les Notes de Credit (NC) importees sans ecriture AC.

Contexte : le wizard `commit_invoices` skipait les factures avec `total_amount <= 0`
donc la Note de Credit Engie FA-2026-0005 (-57.03 EUR) etait persistee comme
invoice mais SANS ecriture comptable. Consequence : le remboursement bancaire
(57.03 EUR) creait un solde fictif de +57.03 EUR "a payer" au compte tier Engie.

Ce script :
  1. Trouve toutes les invoices avec `total_amount < 0` sans journal_entry associee
  2. Cree l'ecriture AC manquante avec le sens NC (DEBIT compte tier / CREDIT charge)
  3. Marque l'invoice avec `is_credit_note=True`

Usage :
  python -m migrations.iter90gl_heal_credit_notes                # dry-run
  python -m migrations.iter90gl_heal_credit_notes --apply       # applique
  python -m migrations.iter90gl_heal_credit_notes --copro-id ID # scope
"""
import asyncio
import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


async def heal_credit_notes(db, copro_id: str, apply: bool = False) -> dict:
    report = {"copro_id": copro_id, "healed": 0, "skipped": 0, "details": []}

    invoices = await db.invoices.find({
        "copropriete_id": copro_id,
        "total_amount": {"$lt": 0},
    }, {"_id": 0}).to_list(1000)

    for inv in invoices:
        inv_id = inv["id"]
        # A journal_entry linked to this invoice ?
        existing_je = await db.journal_entries.find_one(
            {"source_invoice_id": inv_id, "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
            {"_id": 0, "id": 1},
        )
        if existing_je:
            report["skipped"] += 1
            report["details"].append({
                "invoice_id": inv_id, "ref": inv.get("internal_reference"),
                "amount": inv.get("total_amount"),
                "reason": "je_exists", "je_id": existing_je["id"],
            })
            continue

        # Resolve supplier tier account
        supplier_id = inv.get("supplier_id") or ""
        supplier_doc = None
        if supplier_id:
            supplier_doc = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not supplier_doc:
            supplier_name = inv.get("supplier") or ""
            supplier_doc = await db.suppliers.find_one({
                "name": supplier_name,
                "$or": [
                    {"copropriete_id": copro_id},
                    {f"tier_accounts.{copro_id}": {"$exists": True}},
                ],
            }, {"_id": 0})
        if not supplier_doc:
            report["skipped"] += 1
            report["details"].append({
                "invoice_id": inv_id, "ref": inv.get("internal_reference"),
                "amount": inv.get("total_amount"),
                "reason": "no_supplier_fiche",
            })
            continue

        sup_pcmn = ((supplier_doc.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
        if not sup_pcmn:
            report["skipped"] += 1
            report["details"].append({
                "invoice_id": inv_id, "ref": inv.get("internal_reference"),
                "amount": inv.get("total_amount"),
                "reason": "no_canonical_tier",
                "supplier": supplier_doc.get("name"),
            })
            continue

        account_num = (inv.get("account_number") or "").strip()
        if not account_num:
            report["skipped"] += 1
            report["details"].append({
                "invoice_id": inv_id, "ref": inv.get("internal_reference"),
                "amount": inv.get("total_amount"),
                "reason": "no_expense_account",
            })
            continue

        # Create the missing AC entry (NC sign : D compte tier / C charge)
        amount = abs(float(inv.get("total_amount") or 0))
        je_id = str(uuid.uuid4())
        je_doc = {
            "id": je_id,
            "journal_type": "AC",
            "date": inv.get("date"),
            "reference": inv.get("internal_reference"),
            "description": f"{supplier_doc.get('name','')} - {(inv.get('description') or '').strip()}".strip(" -"),
            "lines": [
                {
                    "account_number": account_num,
                    "account_name": "",
                    "debit": 0.0,
                    "credit": amount,
                    "description": (inv.get("description") or "").strip(),
                    "occupant_pct": float(inv.get("occupant_pct") or 100.0),
                    "proprietaire_pct": float(inv.get("proprietaire_pct") or 0.0),
                },
                {
                    "account_number": sup_pcmn,
                    "account_name": supplier_doc.get("name", ""),
                    "third_party_id": supplier_doc["id"],
                    "third_party_type": "supplier",
                    "debit": amount,
                    "credit": 0.0,
                    "description": f"NC {inv.get('internal_reference')}",
                    "occupant_pct": None,
                    "proprietaire_pct": None,
                },
            ],
            "total_debit": amount,
            "total_credit": amount,
            "copropriete_id": copro_id,
            "source_invoice_id": inv_id,
            "is_credit_note": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        report["healed"] += 1
        report["details"].append({
            "invoice_id": inv_id, "ref": inv.get("internal_reference"),
            "amount": inv.get("total_amount"),
            "supplier": supplier_doc.get("name"),
            "sup_pcmn": sup_pcmn,
            "expense_acc": account_num,
            "reason": "heal_ac_entry",
        })
        if apply:
            await db.journal_entries.insert_one(je_doc)
            # Also mark invoice as credit note
            await db.invoices.update_one(
                {"id": inv_id},
                {"$set": {"is_credit_note": True, "journal_entry_id": je_id}},
            )

    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copro-id", type=str, default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    if args.copro_id:
        copro_ids = [args.copro_id]
    else:
        copros = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
        copro_ids = [c["id"] for c in copros]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== iter90gl heal credit notes ({mode}) - {len(copro_ids)} ACP(s) ===\n")

    total_healed = 0
    for copro_id in copro_ids:
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            continue
        r = await heal_credit_notes(db, copro_id, apply=args.apply)
        if r["healed"] > 0 or (r["details"] and any(d["reason"] != "je_exists" for d in r["details"])):
            print(f"\n--- {copro['name']} ---")
            for d in r["details"]:
                print(f"  {d['reason']:20s} ref={d.get('ref')} amount={d.get('amount')} supplier={d.get('supplier','')}")
            print(f"  healed={r['healed']}  skipped={r['skipped']}")
            total_healed += r["healed"]

    print(f"\n=== TOTAL healed: {total_healed} ===")
    if not args.apply and total_healed > 0:
        print("Dry-run - re-run with --apply")


if __name__ == "__main__":
    asyncio.run(main())
