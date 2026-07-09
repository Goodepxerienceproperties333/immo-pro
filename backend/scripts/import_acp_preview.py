"""
iter90cq : Script d'import d'une ACP complete pour migration PROD -> PREVIEW.

Utilisation (a executer sur PREVIEW via la console Emergent) :

    cd /app/backend
    # Import avec remplacement complet (defaut) :
    python scripts/import_acp_preview.py --in /tmp/acp_acacia_export.json

    # Dry-run (aucune modification, verifie juste le fichier) :
    python scripts/import_acp_preview.py --in /tmp/acp_acacia_export.json --dry-run

    # Skip owners (garde ceux de preview) :
    python scripts/import_acp_preview.py --in /tmp/acp_acacia_export.json --skip-owners

Strategie d'import : **REMPLACEMENT COMPLET**
- Supprime l'ACP cible en PREVIEW (via son id) et toutes ses donnees liees
  (via copropriete_id)
- Reinsere les donnees du fichier JSON.
- Idempotent : peut etre rejoue sans doublon.

Les owners sont mis a jour via upsert par id. Ils gardent leurs autres
copropriete_ids si deja presents (merge non destructif).

SECURITE :
- Environnement PREVIEW UNIQUEMENT (verifie DB_NAME <> production).
- Le script AVERTIT si il detecte un environnement suspicieux.

Copyright : E1 - Feb 2026
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


ACP_SCOPED = [
    "fiscal_years", "pcmn_accounts", "distribution_keys",
    "lots", "mutations",
    "budgets", "fund_calls", "journal_entries",
    "bank_accounts", "bank_transactions", "bank_statements", "bank_statement_lines",
    "invoices", "invoice_templates", "invoice_bundle_sessions", "suppliers",
    "documents", "document_categories", "expense_categories",
    "meters", "meter_readings",
    "ag_meetings", "legal_documents", "legal_document_history", "legal_rgpd_register",
    "owner_notifications", "owner_access_audit",
    "syndic_configs", "release_notes_ack",
    "audit_log",
]


async def _safety_check(db):
    db_name = os.environ.get("DB_NAME", "")
    if "prod" in db_name.lower():
        print(f"[!] DB_NAME '{db_name}' semble etre PROD. Confirmez avec 'yes' :")
        ans = input("Continuer l'import ? [yes/N] : ").strip().lower()
        if ans != "yes":
            raise SystemExit("Import annule.")


async def _import(in_path: str, dry_run: bool, skip_owners: bool):
    if not os.path.isfile(in_path):
        raise SystemExit(f"Fichier introuvable : {in_path}")

    with open(in_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("meta") or {}
    coproprietes = payload.get("coproprietes") or []
    if not coproprietes:
        raise SystemExit("Fichier invalide : aucune ACP dans 'coproprietes'.")
    acp = coproprietes[0]
    cid = acp["id"]

    print(f"\n=== Import ACP : {acp.get('name')} (id={cid}) ===")
    print(f"Source (PROD)  : {meta.get('source_db','?')}")
    print(f"Exporte le     : {meta.get('exported_at','?')}")
    print(f"Cible PREVIEW  : {os.environ.get('DB_NAME','?')}")
    print(f"Mode           : {'DRY RUN' if dry_run else 'COMMIT'}\n")

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    if not dry_run:
        await _safety_check(db)

    # Rapport
    print(f"Contenu du fichier :")
    print(f"  coproprietes                              : 1 ({acp.get('name')})")
    owners = payload.get("owners") or []
    print(f"  owners                                    : {len(owners)} "
          f"{'(skip)' if skip_owners else ''}")
    stats = {}
    for coll_name in ACP_SCOPED:
        docs = payload.get(coll_name) or []
        stats[coll_name] = len(docs)
        print(f"  {coll_name:<42}: {len(docs)}")

    if dry_run:
        print("\nDRY RUN : aucune modification. Relancez avec --commit pour appliquer.")
        return

    # === REMPLACEMENT COMPLET ===
    print(f"\n--- Suppression donnees existantes de l'ACP en PREVIEW ---")

    # ACP elle-meme
    r = await db.coproprietes.delete_one({"id": cid})
    print(f"  coproprietes deleted                      : {r.deleted_count}")

    # Toutes collections scoped par copropriete_id
    for coll_name in ACP_SCOPED:
        r = await db[coll_name].delete_many({"copropriete_id": cid})
        print(f"  {coll_name:<42}: {r.deleted_count} deleted")

    # === REIMPORT ===
    print(f"\n--- Insertion des donnees importees ---")

    # ACP
    await db.coproprietes.insert_one(acp)
    print(f"  coproprietes                              : 1 inserted")

    # Owners (upsert par id)
    if not skip_owners:
        owners_up = 0
        for own in owners:
            oid = own.get("id")
            if not oid:
                continue
            # Merge non destructif de copropriete_ids
            existing = await db.owners.find_one({"id": oid}, {"_id": 0, "copropriete_ids": 1})
            if existing:
                existing_cids = set(existing.get("copropriete_ids") or [])
                incoming_cids = set(own.get("copropriete_ids") or [])
                merged = list(existing_cids | incoming_cids)
                # Preserve les autres champs du fichier (donnees fresh de PROD)
                own["copropriete_ids"] = merged
            await db.owners.update_one({"id": oid}, {"$set": own}, upsert=True)
            owners_up += 1
        print(f"  owners upserted                           : {owners_up}")
    else:
        print(f"  owners                                    : skip")

    # Collections ACP-scoped
    inserted_counts = {}
    for coll_name in ACP_SCOPED:
        docs = payload.get(coll_name) or []
        if not docs:
            inserted_counts[coll_name] = 0
            continue
        try:
            r = await db[coll_name].insert_many(docs, ordered=False)
            inserted_counts[coll_name] = len(r.inserted_ids)
        except Exception as e:
            inserted_counts[coll_name] = 0
            print(f"  [!] Erreur insertion {coll_name}: {e}")
    for coll_name in ACP_SCOPED:
        print(f"  {coll_name:<42}: {inserted_counts.get(coll_name, 0)} inserted")

    print(f"\n=== Import termine. ===")
    print(f"Verifiez dans l'UI PREVIEW : ACP '{acp.get('name')}' doit etre presente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import ACP JSON -> PREVIEW MongoDB")
    parser.add_argument("--in", dest="in_path", required=True, help="Fichier JSON exporte par export_acp_prod.py")
    parser.add_argument("--commit", action="store_true", help="Applique l'import (defaut dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Rapport seul (defaut)")
    parser.add_argument("--skip-owners", action="store_true", help="Ne pas importer les owners")
    args = parser.parse_args()

    dry_run = not args.commit
    asyncio.run(_import(
        in_path=args.in_path,
        dry_run=dry_run,
        skip_owners=args.skip_owners,
    ))
