"""
iter90cw : Script de cleanup des fund_calls avec distribution vide.

Contexte : Avant deploiement iter90cr sur PROD, les appels de fonds generes
sur une ACP avec des cles 100% phantom aboutissaient a `distribution=[]`.
En consequence, `generate_sale_entry` ne creait AUCUNE VE (Ventes) et donc
rien n'apparaissait dans le grand livre / balance des tiers.

Post-iter90cr, la generation fonctionne correctement (fallback quotites).
Mais les appels ORPHELINS deja crees restent en DB avec distribution vide.

Ce script :
1. Trouve les fund_calls avec `distribution == []` (ou len < N attendu) pour
   l'ACP cible.
2. Verifie qu'AUCUNE VE n'existe pour ces appels (safety).
3. Supprime les appels + les OD associees (via _delete_auto_entries).
4. Rapporte le nombre supprime.

L'utilisateur regenere ensuite les appels via BudgetWizard une fois iter90cr
deploye.

Usage (a executer sur PROD) :

    cd /app/backend
    # Rapport sans modification :
    python scripts/repair_iter90cw_empty_calls.py --name acacia --dry-run
    # Applique la suppression :
    python scripts/repair_iter90cw_empty_calls.py --name acacia --commit
"""
import argparse
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _find_acp(db, copropriete_id: str = None, name: str = None):
    if copropriete_id:
        doc = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not doc:
            raise SystemExit(f"ACP {copropriete_id} introuvable")
        return doc
    q = {"name": {"$regex": name or "acacia", "$options": "i"}}
    docs = await db.coproprietes.find(q, {"_id": 0}).to_list(10)
    if not docs:
        raise SystemExit(f"Aucune ACP trouvee via name~{name}. Utilisez --copropriete-id.")
    if len(docs) > 1:
        print("[!] Plusieurs ACPs :")
        for d in docs:
            print(f"  - {d.get('id')} : {d.get('name')}")
        raise SystemExit("Precisez --copropriete-id.")
    return docs[0]


async def _run(dry_run: bool, copropriete_id: str = None, name: str = None):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    acp = await _find_acp(db, copropriete_id, name)
    cid = acp["id"]

    print(f"\n=== iter90cw : Cleanup empty fund_calls ACP : {acp.get('name')} ===")
    print(f"Mode : {'DRY RUN' if dry_run else 'COMMIT'}\n")

    # 1) Cherche tous les fund_calls de l'ACP
    all_calls = await db.fund_calls.find(
        {"copropriete_id": cid}, {"_id": 0},
    ).to_list(10000)
    print(f"Appels totaux ACP : {len(all_calls)}\n")

    empty = []
    for c in all_calls:
        dist = c.get("distribution") or []
        # Vide OU somme des amounts == 0
        total_dist = sum(float(d.get("amount", 0) or 0) for d in dist)
        if not dist or total_dist < 0.01:
            empty.append(c)

    print(f"Appels avec distribution VIDE ou nulle : {len(empty)}")
    for c in empty:
        print(f"  - {c.get('name'):<50} date={c.get('date')} total={c.get('total_amount')} dist={len(c.get('distribution') or [])}")

    if not empty:
        print("\nRien a nettoyer.")
        return

    # 2) Verifie qu'AUCUNE VE (auto) n'existe pour ces appels
    empty_ids = [c["id"] for c in empty]
    ves = await db.journal_entries.find({
        "copropriete_id": cid,
        "source_type": "fund_call",
        "source_id": {"$in": empty_ids},
        "journal_type": "VE",
    }, {"_id": 0}).to_list(10000)
    if ves:
        print(f"\n[!] {len(ves)} VE(s) trouvees pour ces appels. Elles seront aussi supprimees :")
        for v in ves:
            print(f"  - VE {v.get('reference')} : {v.get('description', '')[:50]}")

    if dry_run:
        print("\nDRY RUN : aucune suppression. Relancez avec --commit.")
        return

    # 3) Suppression
    from auto_entries import _delete_auto_entries
    deleted_calls = 0
    deleted_ves = 0
    for c in empty:
        try:
            # Supprime les OD/VE auto liees
            n_del = await _delete_auto_entries(db, "fund_call", c["id"])
            deleted_ves += n_del if isinstance(n_del, int) else 0
            # Supprime l'appel
            r = await db.fund_calls.delete_one({"id": c["id"]})
            deleted_calls += r.deleted_count
        except Exception as e:
            print(f"  [!] Erreur suppression {c.get('name')}: {e}")

    print(f"\n--- Applied ---")
    print(f"Appels supprimes : {deleted_calls}")
    print(f"VE/OD associees supprimees : {deleted_ves}")
    print(f"\n=== Termine. Regenerez les appels via BudgetWizard (iter90cr appliquera le fallback). ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Applique la suppression (par defaut dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Rapport seul (defaut)")
    parser.add_argument("--copropriete-id", type=str, default=None)
    parser.add_argument("--name", type=str, default="acacia")
    args = parser.parse_args()

    dry_run = not args.commit
    asyncio.run(_run(dry_run=dry_run, copropriete_id=args.copropriete_id, name=args.name))
