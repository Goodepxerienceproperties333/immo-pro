"""
iter90cq : Script d'export d'une ACP complete pour migration PROD -> PREVIEW.

Utilisation (a executer sur PROD via la console Emergent) :

    cd /app/backend
    # Export ACP Acacia (auto-detect par nom) :
    python scripts/export_acp_prod.py --name acacia --out /tmp/acp_acacia_export.json

    # Ou avec ID explicite :
    python scripts/export_acp_prod.py --copropriete-id <uuid> --out /tmp/acp_export.json

Le fichier JSON produit contient toutes les collections ACP-scoped filtrees
par `copropriete_id` + les `owners` lies via `copropriete_ids`. Copie 1:1
(aucune anonymisation).

IMPORTANT : Le fichier peut etre volumineux (plusieurs Mo). Telechargez-le
depuis la console Emergent puis importez-le en PREVIEW via `import_acp_preview.py`.

Collections exportees :
- coproprietes (l'ACP)
- fiscal_years, pcmn_accounts, distribution_keys
- owners (via copropriete_ids)
- lots, mutations
- budgets, fund_calls, journal_entries
- bank_accounts, bank_transactions, bank_statements, bank_statement_lines
- invoices, invoice_templates, invoice_bundle_sessions, suppliers
- documents, document_categories, expense_categories
- meters, meter_readings
- ag_meetings, legal_documents, legal_document_history, legal_rgpd_register
- owner_notifications, owner_access_audit, audit_log (filtre copropriete_id)
- syndic_configs, release_notes_ack

NON exporte :
- GridFS files (documents.chunks/files, invoice_attachments.chunks/files, etc.) :
  binaires trop volumineux, a extraire manuellement si necessaire.
- users, tenants, role_templates : system-wide, deja presents en PREVIEW.
- password_reset_*, login_history, backups_* : historique technique local.
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


# Collections ACP-scoped via `copropriete_id`
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
]

# Collections filtrees mais optionnelles (peuvent ne pas exister sur certains deploys)
OPTIONAL_ACP_SCOPED = ["audit_log"]

# Owners : cross-reference via copropriete_ids
OWNERS_COLL = "owners"


def _stringify(value):
    """Serialise datetime/ObjectId en str pour JSON."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


async def _find_acp(db, copropriete_id: str = None, name: str = None):
    if copropriete_id:
        doc = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not doc:
            raise SystemExit(f"ACP {copropriete_id} introuvable")
        return doc
    q = {"name": {"$regex": name or "acacia", "$options": "i"}}
    docs = await db.coproprietes.find(q, {"_id": 0}).to_list(10)
    if not docs:
        raise SystemExit(
            f"Aucune ACP trouvee via name~{name or 'acacia'}. "
            f"Utilisez --copropriete-id."
        )
    if len(docs) > 1:
        print(f"[!] Plusieurs ACPs trouvees pour '{name}' :")
        for d in docs:
            print(f"  - {d.get('id')} : {d.get('name')}")
        raise SystemExit("Precisez --copropriete-id.")
    return docs[0]


async def _export_acp(copropriete_id: str, name: str, out_path: str):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    acp = await _find_acp(db, copropriete_id, name)
    cid = acp["id"]
    print(f"\n=== Export ACP : {acp.get('name')} (id={cid}) ===")
    print(f"Timestamp : {datetime.now(timezone.utc).isoformat()}\n")

    payload = {
        "meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_db": os.environ.get("DB_NAME", ""),
            "copropriete_id": cid,
            "copropriete_name": acp.get("name", ""),
            "iter90cq_version": "1.0",
        },
        "coproprietes": [acp],
        "owners": [],
    }

    # Owners : ceux qui ont l'ACP dans copropriete_ids
    owners = await db.owners.find(
        {"copropriete_ids": cid}, {"_id": 0},
    ).to_list(50000)
    payload["owners"] = owners
    print(f"  owners (via copropriete_ids)              : {len(owners)}")

    # ACP-scoped
    for coll_name in ACP_SCOPED:
        docs = await db[coll_name].find(
            {"copropriete_id": cid}, {"_id": 0},
        ).to_list(1000000)
        payload[coll_name] = docs
        print(f"  {coll_name:<42}: {len(docs)}")

    # Optionnels
    for coll_name in OPTIONAL_ACP_SCOPED:
        try:
            docs = await db[coll_name].find(
                {"copropriete_id": cid}, {"_id": 0},
            ).to_list(1000000)
            payload[coll_name] = docs
            print(f"  {coll_name:<42}: {len(docs)} (optionnel)")
        except Exception as e:
            print(f"  {coll_name:<42}: skip ({e})")

    # Sauve
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=_stringify, indent=2)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\n>>> Exporte vers {out_path} ({size_mb:.2f} MB)")
    print(f">>> Prochaine etape : Telecharger le fichier puis executer")
    print(f">>>   python scripts/import_acp_preview.py --in {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export ACP complete PROD -> JSON")
    parser.add_argument("--copropriete-id", type=str, default=None, help="ID ACP explicite")
    parser.add_argument("--name", type=str, default="acacia", help="Regex nom (defaut 'acacia')")
    parser.add_argument("--out", type=str, default="/tmp/acp_export.json", help="Fichier de sortie")
    args = parser.parse_args()

    asyncio.run(_export_acp(
        copropriete_id=args.copropriete_id,
        name=args.name,
        out_path=args.out,
    ))
