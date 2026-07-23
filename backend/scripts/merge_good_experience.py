"""
Script de fusion des fournisseurs dupliques "Good Experience".

Le fournisseur "SRL Good Experience" (44000009) a l'ACTIF et
"SRL Good Experience Properties" (44000016) au PASSIF doivent
etre fusionnes en un seul fournisseur avec un seul compte PCMN.

Strategie :
  1. Identifie les deux fiches fournisseur
  2. Garde la fiche avec le plus d'ecritures comme "survivante"
  3. Re-route toutes les ecritures de la fiche eliminee vers le compte de la survivante
  4. Met a jour les factures et bank_transactions pointant vers la fiche eliminee
  5. Supprime la fiche eliminee (ou la marque comme merged)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient


async def find_good_experience_dupes(db, copro_id: str) -> list[dict]:
    """Trouve toutes les fiches 'Good Experience' dans l'ACP."""
    import re
    suppliers = await db.suppliers.find(
        {"copropriete_id": copro_id,
         "name": {"$regex": re.compile(r"good\s*experience", re.IGNORECASE)}},
        {"_id": 0},
    ).to_list(100)
    return suppliers


async def merge_suppliers(db, keep: dict, remove: dict, copro_id: str, dry_run: bool = True):
    """Fusionne `remove` dans `keep`. Toutes les refs vers remove sont redirigees vers keep."""
    from tier_accounts import get_supplier_account

    keep_id = keep["id"]
    remove_id = remove["id"]
    keep_acc = get_supplier_account(keep, copro_id)
    remove_acc = get_supplier_account(remove, copro_id)

    if not keep_acc:
        print(f"  [ERREUR] Fournisseur survivant {keep['name']} n'a pas de compte PCMN!")
        return
    if not remove_acc:
        print(f"  [WARN] Fournisseur a supprimer {remove['name']} n'a pas de compte PCMN, rien a re-router.")

    print(f"\n  FUSION : garder '{keep['name']}' ({keep_acc}) / supprimer '{remove['name']}' ({remove_acc})")

    # 1) Re-router les lignes d'ecritures comptables
    if remove_acc:
        je_filter = {
            "copropriete_id": copro_id,
            "lines.account_number": remove_acc,
        }
        affected_entries = await db.journal_entries.find(je_filter, {"_id": 0, "id": 1, "lines": 1}).to_list(10000)
        print(f"  Ecritures comptables a re-router : {len(affected_entries)}")
        for entry in affected_entries:
            new_lines = []
            changed = False
            for ln in entry.get("lines", []):
                if ln.get("account_number") == remove_acc:
                    ln["account_number"] = keep_acc
                    ln["account_name"] = keep.get("name", ln.get("account_name", ""))
                    if ln.get("third_party_id") == remove_id:
                        ln["third_party_id"] = keep_id
                        ln["third_party_name"] = keep.get("name", "")
                    changed = True
                new_lines.append(ln)
            if changed and not dry_run:
                await db.journal_entries.update_one(
                    {"id": entry["id"]},
                    {"$set": {"lines": new_lines}},
                )

    # 2) Re-router les factures
    inv_filter = {"supplier_id": remove_id, "copropriete_id": copro_id}
    inv_count = await db.invoices.count_documents(inv_filter)
    print(f"  Factures a re-router : {inv_count}")
    if not dry_run and inv_count:
        await db.invoices.update_many(
            inv_filter,
            {"$set": {
                "supplier_id": keep_id,
                "supplier": keep.get("name", ""),
                "account_number": keep_acc,
            }},
        )

    # 3) Re-router les bank_transactions (si matched_to est un supplier)
    txn_filter = {"matched_to": remove_id, "copropriete_id": copro_id}
    txn_count = await db.bank_transactions.count_documents(txn_filter)
    print(f"  Bank transactions a re-router : {txn_count}")
    if not dry_run and txn_count:
        await db.bank_transactions.update_many(
            txn_filter,
            {"$set": {"matched_to": keep_id}},
        )

    # 4) Supprimer le compte PCMN orphelin
    if remove_acc and not dry_run:
        # Verifie qu'il n'y a plus de references
        remaining = await db.journal_entries.count_documents(
            {"copropriete_id": copro_id, "lines.account_number": remove_acc})
        if remaining == 0:
            await db.pcmn_accounts.delete_one(
                {"copropriete_id": copro_id, "number": remove_acc})
            print(f"  Compte PCMN {remove_acc} supprime (plus de references)")
        else:
            print(f"  [WARN] Compte PCMN {remove_acc} conserve ({remaining} references restantes)")

    # 5) Supprimer la fiche fournisseur eliminee
    if not dry_run:
        await db.suppliers.delete_one({"id": remove_id})
        print(f"  Fiche fournisseur '{remove['name']}' supprimee")

    action = "DRY RUN" if dry_run else "APPLIQUE"
    print(f"  [{action}] Fusion terminee.\n")


async def main():
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print("MODE DRY RUN (ajouter --apply pour executer)\n")

    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME", "copro")]

    # Trouver toutes les ACPs
    copros = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)
    if not copros:
        print("Aucune copropriete en base.")
        return

    for copro in copros:
        copro_id = copro["id"]
        print(f"=== ACP: {copro['name']} ({copro_id}) ===")
        dupes = await find_good_experience_dupes(db, copro_id)
        if len(dupes) < 2:
            print(f"  Pas de doublon Good Experience ({len(dupes)} fiche(s)).\n")
            continue
        print(f"  {len(dupes)} fiches trouvees :")
        for s in dupes:
            from tier_accounts import get_supplier_account
            acc = get_supplier_account(s, copro_id)
            count = await db.journal_entries.count_documents(
                {"copropriete_id": copro_id, "lines.account_number": acc})
            print(f"    - {s['name']} (id={s['id'][:8]}... acc={acc}) -> {count} ecritures")

        # Garder celle avec le plus d'ecritures
        from tier_accounts import get_supplier_account
        scored = []
        for s in dupes:
            acc = get_supplier_account(s, copro_id)
            count = await db.journal_entries.count_documents(
                {"copropriete_id": copro_id, "lines.account_number": acc})
            scored.append((count, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        keep = scored[0][1]
        for _, remove in scored[1:]:
            await merge_suppliers(db, keep, remove, copro_id, dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
