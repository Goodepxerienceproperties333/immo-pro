"""iter90jb : Normalise TOUS les IBAN persistes en base au format canonique
(uppercase, sans espace/tiret/point).

Cibles :
1. `coproprietes.bank_accounts[].iban` : liste de comptes bancaires par ACP.
2. `bank_statements.account_number` : IBAN utilise sur chaque extrait.
3. `owners.iban` (si present) : IBAN de compte personnel.
4. `suppliers.iban` (si present) : IBAN fournisseur.

Sans cette normalisation, la refonte Banque (iter90ja) affiche des DOUBLONS
visuels dans le menu deroulant "Compte bancaire" (une entree par variante
d'espace, ex: "BE04 0019 5208 9331" et "BE04001952089331").

Usage :
    python -m scripts.normalize_ibans          # dry-run
    python -m scripts.normalize_ibans --execute
    python -m scripts.normalize_ibans --copropriete-id CID --execute

Rapport JSON : /tmp/normalize_ibans_report.json
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
from iban_utils import normalize_iban  # noqa: E402


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    stats = {
        "coproprietes_updated": 0,
        "coproprietes_bank_accounts_normalized": 0,
        "coproprietes_bank_accounts_deduped": 0,
        "statements_updated": 0,
        "owners_updated": 0,
        "suppliers_updated": 0,
    }
    updates: list[dict] = []

    # 1) coproprietes.bank_accounts[].iban
    q_cop: dict = {}
    if args.copropriete_id:
        q_cop["id"] = args.copropriete_id
    async for cop in db.coproprietes.find(q_cop, {"_id": 0, "id": 1, "name": 1, "bank_accounts": 1}):
        bas = list(cop.get("bank_accounts") or [])
        if not bas:
            continue
        norm_bas: list[dict] = []
        seen: set[str] = set()
        touched = False
        cop_diff: list[dict] = []
        for ba in bas:
            raw_original = ba.get("iban") or ""
            n = normalize_iban(raw_original)
            if raw_original != n:
                touched = True
                cop_diff.append({"before": raw_original, "after": n})
            if n and n in seen:
                # Doublon apres normalisation -> on skip la 2e occurrence
                touched = True
                stats["coproprietes_bank_accounts_deduped"] += 1
                continue
            if n:
                seen.add(n)
            new_ba = {**ba, "iban": n}
            norm_bas.append(new_ba)
        if touched:
            stats["coproprietes_updated"] += 1
            stats["coproprietes_bank_accounts_normalized"] += len(cop_diff)
            updates.append({
                "collection": "coproprietes",
                "id": cop["id"],
                "name": cop.get("name", ""),
                "diff": cop_diff,
                "deduped_count": len(bas) - len(norm_bas),
            })
            if args.execute:
                await db.coproprietes.update_one(
                    {"id": cop["id"]}, {"$set": {"bank_accounts": norm_bas}},
                )

    # 2) bank_statements.account_number
    q_stmt: dict = {}
    if args.copropriete_id:
        q_stmt["copropriete_id"] = args.copropriete_id
    async for st in db.bank_statements.find(q_stmt, {"_id": 0, "id": 1, "copropriete_id": 1, "account_number": 1}):
        raw = st.get("account_number") or ""
        n = normalize_iban(raw)
        if raw != n and n:
            stats["statements_updated"] += 1
            updates.append({
                "collection": "bank_statements",
                "id": st["id"],
                "copropriete_id": st.get("copropriete_id", ""),
                "before": raw,
                "after": n,
            })
            if args.execute:
                await db.bank_statements.update_one(
                    {"id": st["id"]}, {"$set": {"account_number": n}},
                )

    # 3) owners.iban (si champ existe)
    q_own: dict = {"iban": {"$exists": True, "$ne": ""}}
    async for o in db.owners.find(q_own, {"_id": 0, "id": 1, "name": 1, "iban": 1}):
        raw = o.get("iban") or ""
        n = normalize_iban(raw)
        if raw != n and n:
            stats["owners_updated"] += 1
            updates.append({
                "collection": "owners", "id": o["id"],
                "name": o.get("name", ""),
                "before": raw, "after": n,
            })
            if args.execute:
                await db.owners.update_one({"id": o["id"]}, {"$set": {"iban": n}})

    # 4) suppliers.iban (si champ existe)
    async for s in db.suppliers.find({"iban": {"$exists": True, "$ne": ""}}, {"_id": 0, "id": 1, "name": 1, "iban": 1}):
        raw = s.get("iban") or ""
        n = normalize_iban(raw)
        if raw != n and n:
            stats["suppliers_updated"] += 1
            updates.append({
                "collection": "suppliers", "id": s["id"],
                "name": s.get("name", ""),
                "before": raw, "after": n,
            })
            if args.execute:
                await db.suppliers.update_one({"id": s["id"]}, {"$set": {"iban": n}})

    print("[normalize_ibans] stats :", json.dumps(stats, indent=2))
    print(f"[normalize_ibans] {len(updates)} modifications " + ("APPLIQUEES" if args.execute else "detectees (dry-run)"))
    if updates and not args.execute:
        print("\nAperçu (top 15) :")
        for u in updates[:15]:
            print(f"  [{u['collection']}] {u.get('name') or u.get('id')} : {u.get('before') or u.get('diff')}"
                  f" -> {u.get('after') or ''}")
    report = {"executed": args.execute, "stats": stats, "count": len(updates), "updates": updates}
    with open("/tmp/normalize_ibans_report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("[normalize_ibans] Rapport ecrit : /tmp/normalize_ibans_report.json")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique les modifications (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="", help="Restreint a une seule ACP (defaut : toutes)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
