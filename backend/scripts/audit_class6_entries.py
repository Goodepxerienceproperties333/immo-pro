#!/usr/bin/env python3
"""
audit_class6_entries.py — Audit des ecritures de classe 6
=========================================================

Detecte les ecritures probablement en doublon :
- Meme fournisseur (ou description similaire)
- Meme montant
- Date identique ou tres proche (±3 jours)
- Sur deux comptes PCMN differents

Ne SUPPRIME rien : produit un rapport pour validation manuelle.

Usage :
    python audit_class6_entries.py                          # toutes ACPs
    python audit_class6_entries.py --copro <ID>             # une ACP
    python audit_class6_entries.py --copro <ID> --delete    # supprime les doublons (apres validation)
"""
import asyncio
import sys
import os
import re
import unicodedata
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "nextge_copro")

DELETE_MODE = "--delete" in sys.argv
COPRO_FILTER = None
if "--copro" in sys.argv:
    idx = sys.argv.index("--copro")
    COPRO_FILTER = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None


def norm(s):
    if not s:
        return ""
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def date_close(d1_str, d2_str, days=3):
    try:
        d1 = datetime.strptime(d1_str[:10], "%Y-%m-%d")
        d2 = datetime.strptime(d2_str[:10], "%Y-%m-%d")
        return abs((d1 - d2).days) <= days
    except Exception:
        return False


async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    mode_label = "DELETE" if DELETE_MODE else "AUDIT"
    print(f"\n{'='*60}")
    print(f"  AUDIT ECRITURES CLASSE 6 — {mode_label}")
    print(f"{'='*60}\n")

    # Load all invoices (source of class 6 entries)
    inv_q = {}
    if COPRO_FILTER:
        inv_q["copropriete_id"] = COPRO_FILTER
    invoices = await db.invoices.find(
        inv_q,
        {"_id": 0, "id": 1, "number": 1, "date": 1, "supplier": 1,
         "total_amount": 1, "account_number": 1, "copropriete_id": 1,
         "description": 1, "status": 1, "journal_entry_id": 1}
    ).to_list(50000)
    print(f"  Factures chargees : {len(invoices)}")

    # Group by (copro, supplier_norm, amount, ~date)
    groups = defaultdict(list)
    for inv in invoices:
        if not (inv.get("account_number") or "").startswith("6"):
            continue
        key_supplier = norm(inv.get("supplier", ""))
        key_amount = round(float(inv.get("total_amount", 0)), 2)
        if key_amount <= 0:
            continue
        groups[(inv.get("copropriete_id", ""), key_supplier, key_amount)].append(inv)

    # Find groups with same supplier+amount but different accounts
    suspect_groups = []
    for key, invs in groups.items():
        if len(invs) < 2:
            continue
        accounts_used = set(inv.get("account_number", "") for inv in invs)
        if len(accounts_used) < 2:
            continue
        # Further filter: dates must be close
        for i, a in enumerate(invs):
            for b in invs[i+1:]:
                if a.get("account_number") == b.get("account_number"):
                    continue
                if date_close(a.get("date", ""), b.get("date", ""), 3):
                    suspect_groups.append((a, b))

    print(f"  Paires suspectes (meme fournisseur, meme montant, comptes differents, dates proches) : {len(suspect_groups)}\n")

    deleted = 0
    for i, (a, b) in enumerate(suspect_groups, 1):
        print(f"  [{i}] DOUBLON POTENTIEL :")
        print(f"       A: {a.get('date','')} | {a.get('number','')} | {a.get('supplier','')} | "
              f"{a.get('total_amount',0):.2f}E | Cpte {a.get('account_number','')} | {a.get('description','')[:60]}")
        print(f"       B: {b.get('date','')} | {b.get('number','')} | {b.get('supplier','')} | "
              f"{b.get('total_amount',0):.2f}E | Cpte {b.get('account_number','')} | {b.get('description','')[:60]}")

        if DELETE_MODE:
            # Keep the one on the "standard" account (shorter number)
            keep, remove = (a, b) if len(a.get("account_number", "")) <= len(b.get("account_number", "")) else (b, a)
            print(f"       → Garder {keep.get('number','')} (cpte {keep.get('account_number','')}), "
                  f"supprimer {remove.get('number','')} (cpte {remove.get('account_number','')})")

            # Delete the invoice
            res = await db.invoices.delete_one({"id": remove["id"]})
            if res.deleted_count:
                deleted += 1
                # Also delete its journal entry if any
                je_id = remove.get("journal_entry_id")
                if je_id:
                    await db.journal_entries.delete_one({"id": je_id})
                    print(f"       → Facture + ecriture supprimees")
                else:
                    print(f"       → Facture supprimee (pas d'ecriture liee)")
        print()

    print(f"{'='*60}")
    if DELETE_MODE:
        print(f"  Doublons supprimes : {deleted}")
    else:
        print(f"  Relancez avec --delete pour supprimer les doublons.")
        print(f"  ATTENTION : verifiez chaque paire avant de supprimer !")
    print(f"{'='*60}\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
