"""iter90gk : Nettoyage des lignes AC/AN orphelines dans les journaux comptables.

CONTEXTE :
Le wizard Optipro creait auparavant les ecritures AC (achats) avec un compte
tier CALCULE depuis l'aux_code Optipro ("4400" + aux[1:].zfill(3)) au lieu
d'utiliser le compte tier canonique declare dans la fiche fournisseur. De
plus, aucun `third_party_id` n'etait pose sur la ligne du compte tier.

RESULTAT : le meme fournisseur pouvait avoir plusieurs comptes tier
differents (44000005 canonique + 44000216 oriente Optipro pour Baloise, par
ex.). Consequence :
  - Le Bilan (qui aggrege par acc.startswith("440")) voyait ces 2 comptes,
    l'un cote actif et l'autre cote passif -> **bilan desequilibre** avec un
    499 (mali) inflate.
  - La Balance des Tiers (qui filtre acc.startswith("44000") + tpid) ratait
    les orphelins -> **soldes fournisseurs errones**.

CE SCRIPT REPARE UNIQUEMENT LES DONNEES EXISTANTES (le fix code est dans
`import_wizard.py`, iter90gk).

Strategie :
  1. Pour chaque ACP, liste tous les fournisseurs + leur tier_account canonique
  2. Parcourt les journal_entries lignes acc.startswith("440")
  3. Si ligne acc != tier_canonique du fournisseur (match par nom OU par
     source_invoice_id.supplier_id), REECRIT la ligne :
       - account_number <- tier_canonique
       - third_party_id <- supplier.id
       - third_party_type <- "supplier"
  4. Log toutes les modifications (dry-run par defaut)

Usage :
  python -m migrations.iter90gk_cleanup_orphan_tiers                # dry-run
  python -m migrations.iter90gk_cleanup_orphan_tiers --apply       # applique
  python -m migrations.iter90gk_cleanup_orphan_tiers --copro-id ID # scope 1 ACP
"""
import asyncio
import argparse
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Allow imports from /app/backend
sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


def _norm_name(value: str) -> str:
    """Copie locale de routes.suppliers._norm_name (evite import cycle)."""
    import re
    from routes.suppliers import _norm_name as f
    return f(value)


def _norm_name_candidates(value: str) -> set:
    from routes.suppliers import _norm_name_candidates as f
    return f(value)


async def cleanup_orphans(db, copro_id: str, apply: bool = False) -> dict:
    """Nettoie les lignes AC/AN orphelines dans une ACP donnee.

    Retourne un rapport {rewritten, unmatched, entries_touched}.
    """
    report = {
        "copro_id": copro_id,
        "rewritten": 0,
        "unmatched": 0,
        "entries_touched": 0,
        "details": [],
    }

    # 1) Index fournisseurs de l'ACP par tier canonique + par candidats de nom
    suppliers = await db.suppliers.find(
        {"$or": [
            {"copropriete_id": copro_id},
            {f"tier_accounts.{copro_id}": {"$exists": True}},
        ]}, {"_id": 0}
    ).to_list(20000)
    tier_by_supplier: dict[str, str] = {}
    supplier_by_tier: dict[str, dict] = {}
    supplier_by_name_cand: dict[str, dict] = {}
    for s in suppliers:
        tier = ((s.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
        if tier:
            tier_by_supplier[s["id"]] = tier
            supplier_by_tier[tier] = s
        for cand in _norm_name_candidates(s.get("name", "")):
            supplier_by_name_cand[cand] = s

    # 2) Parcourt toutes les entries de l'ACP touchant un compte 440XXX
    entries = await db.journal_entries.find(
        {"copropriete_id": copro_id, "lines.account_number": {"$regex": "^440"}},
        {"_id": 0},
    ).to_list(200000)

    invoices_cache: dict[str, dict] = {}

    for e in entries:
        touched = False
        for ln in e.get("lines", []):
            acc = ln.get("account_number", "")
            if not acc.startswith("440"):
                continue
            existing_tpid = ln.get("third_party_id")
            # Ligne deja correctement liee ? On skip.
            if existing_tpid and existing_tpid in tier_by_supplier:
                if tier_by_supplier[existing_tpid] == acc:
                    continue  # OK, canonical - do not touch

            # Determine le "vrai" fournisseur :
            # (a) via third_party_id existant (si present dans nos suppliers)
            # (b) via account_number == tier canonique d'un fournisseur
            # (c) via source_invoice_id -> invoice.supplier_id
            # (d) via matching par nom sur account_name / line_description
            target_sup = None
            if existing_tpid:
                target_sup = next((s for s in suppliers if s["id"] == existing_tpid), None)
            if not target_sup and acc in supplier_by_tier:
                target_sup = supplier_by_tier[acc]
            if not target_sup and e.get("source_invoice_id"):
                inv_id = e["source_invoice_id"]
                if inv_id not in invoices_cache:
                    invoices_cache[inv_id] = await db.invoices.find_one(
                        {"id": inv_id}, {"_id": 0, "supplier_id": 1, "supplier": 1}
                    ) or {}
                inv = invoices_cache[inv_id]
                sid = inv.get("supplier_id")
                if sid:
                    target_sup = next((s for s in suppliers if s["id"] == sid), None)
                if not target_sup and inv.get("supplier"):
                    inv_cands = _norm_name_candidates(inv["supplier"])
                    for cand in inv_cands:
                        if cand in supplier_by_name_cand:
                            target_sup = supplier_by_name_cand[cand]
                            break
            if not target_sup:
                label = ln.get("account_name") or ln.get("line_description") or ""
                lbl_cands = _norm_name_candidates(label)
                for cand in lbl_cands:
                    if cand in supplier_by_name_cand:
                        target_sup = supplier_by_name_cand[cand]
                        break

            if not target_sup:
                report["unmatched"] += 1
                report["details"].append({
                    "entry_id": e["id"], "ref": e.get("reference"), "acc": acc,
                    "reason": "no_matching_supplier",
                    "name": ln.get("account_name", ""),
                })
                continue

            canonical_tier = tier_by_supplier.get(target_sup["id"], "")
            if not canonical_tier:
                # Fournisseur trouve mais pas de tier canonique dans cette ACP.
                # On assign un tier maintenant si on applique.
                if apply:
                    from tier_accounts import assign_supplier_account
                    updated = await assign_supplier_account(db, target_sup, copro_id)
                    canonical_tier = ((updated.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
                if not canonical_tier:
                    report["unmatched"] += 1
                    report["details"].append({
                        "entry_id": e["id"], "ref": e.get("reference"), "acc": acc,
                        "reason": "no_canonical_tier",
                        "supplier": target_sup.get("name"),
                    })
                    continue

            # Reecriture necessaire : acc different OU tpid different
            if acc != canonical_tier or existing_tpid != target_sup["id"]:
                report["rewritten"] += 1
                report["details"].append({
                    "entry_id": e["id"], "ref": e.get("reference"),
                    "from_acc": acc, "to_acc": canonical_tier,
                    "supplier": target_sup.get("name"),
                    "supplier_id": target_sup["id"],
                    "D": ln.get("debit", 0), "C": ln.get("credit", 0),
                })
                ln["account_number"] = canonical_tier
                ln["account_name"] = target_sup.get("name", ln.get("account_name", ""))
                ln["third_party_id"] = target_sup["id"]
                ln["third_party_type"] = "supplier"
                touched = True

        if touched:
            report["entries_touched"] += 1
            if apply:
                await db.journal_entries.update_one(
                    {"id": e["id"]},
                    {"$set": {"lines": e["lines"]}},
                )

    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copro-id", type=str, default="", help="Copropriete ID (default: all)")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    if args.copro_id:
        copro_ids = [args.copro_id]
    else:
        copros = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
        copro_ids = [c["id"] for c in copros]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== iter90gk cleanup ({mode}) - {len(copro_ids)} ACP(s) ===\n")

    global_rewritten = 0
    global_unmatched = 0
    for copro_id in copro_ids:
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            print(f"  [SKIP] copro_id={copro_id} not found")
            continue
        report = await cleanup_orphans(db, copro_id, apply=args.apply)
        if report["rewritten"] > 0 or report["unmatched"] > 0:
            print(f"\n--- ACP: {copro['name']} ({copro_id}) ---")
            print(f"  Entries touched: {report['entries_touched']}")
            print(f"  Lines rewritten: {report['rewritten']}")
            print(f"  Lines unmatched: {report['unmatched']}")
            for d in report["details"]:
                if "to_acc" in d:
                    print(f"    REWRITE ref={d.get('ref','')} {d['from_acc']} -> {d['to_acc']} [{d['supplier']}] D={d['D']} C={d['C']}")
                else:
                    print(f"    SKIP    ref={d.get('ref','')} acc={d['acc']} reason={d['reason']} name={d.get('name') or d.get('supplier','')}")
            global_rewritten += report["rewritten"]
            global_unmatched += report["unmatched"]

    print(f"\n=== TOTAL: {global_rewritten} rewrites, {global_unmatched} unmatched ===")
    if not args.apply and global_rewritten > 0:
        print("Dry-run only. Re-run with --apply to persist changes.")


if __name__ == "__main__":
    asyncio.run(main())
