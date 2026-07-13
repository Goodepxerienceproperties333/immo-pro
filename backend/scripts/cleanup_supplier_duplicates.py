"""iter90fb : Script one-shot de nettoyage des doublons de fournisseurs
sur factures existantes.

Parcourt toutes les factures et, pour chaque `invoice.supplier` en texte
libre, verifie si une fiche fournisseur existe pour la meme cle
normalisee (incluant les candidats entre parentheses). Si oui,
remplace `invoice.supplier` par le nom canonique de la fiche.

Mode DEFAUT : dry-run (aucune ecriture DB). Ajouter --execute pour
appliquer reellement.

Usage :
    python -m scripts.cleanup_supplier_duplicates
    python -m scripts.cleanup_supplier_duplicates --execute
    python -m scripts.cleanup_supplier_duplicates --copropriete-id CID
    python -m scripts.cleanup_supplier_duplicates --execute --copropriete-id CID

Ce que le script fait :
1. Charge toutes les fiches suppliers (globales + copro).
2. Pour chaque facture, calcule les cles candidates de son supplier.
3. Si une fiche matche une des cles ET son nom canonique differe du
   texte libre, on prevoit un update.
4. Met aussi a jour les journal_entries associees (ligne credit
   compte fournisseur -> `third_party_name` = nouveau nom).
5. Affiche un rapport tabulaire + un JSON summary.

Le script est IDEMPOTENT : relance-le autant de fois que necessaire.
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.suppliers import _norm_name, _norm_name_candidates  # noqa: E402


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # 1. Toutes les fiches suppliers (globales + copro)
    sup_query = {}
    if args.copropriete_id:
        sup_query["$or"] = [
            {"copropriete_id": args.copropriete_id},
            {"is_global": True},
            {"copropriete_id": {"$in": [None, ""]}},
        ]
    suppliers = await db.suppliers.find(sup_query, {"_id": 0}).to_list(50000)
    # Index : norm_name -> canonical name.
    # Attention : si plusieurs fiches ont la meme cle normalisee (ce
    # qui est deja un doublon a corriger en amont), on privilegie
    # celle avec un BCE/IBAN renseigne.
    canonical_by_norm: dict[str, str] = {}
    for s in suppliers:
        n = _norm_name(s.get("name", ""))
        if not n:
            continue
        existing = canonical_by_norm.get(n)
        if not existing:
            canonical_by_norm[n] = s["name"]
            continue
        # Deja un candidat : on garde le plus "complet"
        # (avec BCE > IBAN > longueur du nom)
        current_score = _completeness_score(next(x for x in suppliers if x.get("name") == existing))
        new_score = _completeness_score(s)
        if new_score > current_score:
            canonical_by_norm[n] = s["name"]

    if not canonical_by_norm:
        print("[cleanup] Aucune fiche fournisseur trouvee, rien a faire.")
        return 0

    print(f"[cleanup] {len(canonical_by_norm)} fiches canoniques indexees.")

    # 2. Parcours des factures
    inv_query = {}
    if args.copropriete_id:
        inv_query["copropriete_id"] = args.copropriete_id

    total_inv = 0
    to_update: list[dict] = []
    stats = Counter()
    canonicals_used: Counter = Counter()

    async for inv in db.invoices.find(inv_query, {
        "_id": 0, "id": 1, "supplier": 1, "copropriete_id": 1,
        "number": 1, "date": 1, "total_amount": 1,
    }):
        total_inv += 1
        raw = (inv.get("supplier") or "").strip()
        if not raw:
            stats["empty_supplier"] += 1
            continue
        candidates = _norm_name_candidates(raw)
        # Cherche la premiere cle qui matche une fiche
        matched_canonical = None
        for c in candidates:
            if c in canonical_by_norm:
                matched_canonical = canonical_by_norm[c]
                break
        if matched_canonical is None:
            stats["no_match_freetext"] += 1
            continue
        if matched_canonical == raw:
            stats["already_canonical"] += 1
            continue
        stats["will_snap"] += 1
        canonicals_used[matched_canonical] += 1
        to_update.append({
            "invoice_id": inv["id"],
            "copropriete_id": inv.get("copropriete_id", ""),
            "number": inv.get("number", ""),
            "date": inv.get("date", ""),
            "old_supplier": raw,
            "new_supplier": matched_canonical,
        })

    print(f"[cleanup] {total_inv} factures scannees, "
          f"{stats['will_snap']} a corriger, "
          f"{stats['already_canonical']} deja canoniques, "
          f"{stats['no_match_freetext']} sans fiche matchante, "
          f"{stats['empty_supplier']} sans supplier.")

    if to_update:
        print("\n[cleanup] --- Rapport detail (top 50) ---")
        print(f"{'Inv#':<20} {'Date':<12} {'Ancien nom':<45} -> {'Nom canonique':<40}")
        print("-" * 130)
        for row in to_update[:50]:
            print(f"{row['number'][:19]:<20} {row['date'][:11]:<12} "
                  f"{row['old_supplier'][:44]:<45} -> {row['new_supplier'][:39]:<40}")
        if len(to_update) > 50:
            print(f"... et {len(to_update) - 50} autres.")
        print("\n[cleanup] --- Top canonicals utilises ---")
        for name, cnt in canonicals_used.most_common(20):
            print(f"  {cnt:>4} x {name}")

    # 3. Application
    if not args.execute:
        print("\n[cleanup] DRY-RUN : aucune ecriture en base.")
        print(f"[cleanup] Pour appliquer, relance avec --execute.")
        _write_report(to_update, stats, args.execute)
        return 0

    # Regroupement par copropriete pour update des journal_entries
    print("\n[cleanup] EXECUTION : application des changements...")
    inv_updated = 0
    je_updated = 0
    for row in to_update:
        # Update invoice
        r_inv = await db.invoices.update_one(
            {"id": row["invoice_id"]},
            {"$set": {"supplier": row["new_supplier"]}}
        )
        if r_inv.modified_count:
            inv_updated += 1
        # Update journal entries lies (source_id = invoice_id, ligne
        # credit compte 44xxxxx avec third_party_name = ancien nom)
        r_je = await db.journal_entries.update_many(
            {"source_id": row["invoice_id"],
             "lines.third_party_name": row["old_supplier"]},
            {"$set": {"lines.$[elem].third_party_name": row["new_supplier"]}},
            array_filters=[{"elem.third_party_name": row["old_supplier"]}],
        )
        je_updated += r_je.modified_count or 0

    print(f"[cleanup] Termine : {inv_updated} factures + "
          f"{je_updated} journal entries mises a jour.")
    _write_report(to_update, stats, args.execute)
    return 0


def _completeness_score(s: dict) -> int:
    """Score de completude d'une fiche pour departager les canonicals."""
    return (
        (5 if (s.get("bce_number") or "").strip() else 0)
        + (3 if (s.get("vat_number") or "").strip() else 0)
        + (2 if (s.get("iban") or "").strip() else 0)
        + (1 if (s.get("city") or "").strip() else 0)
        + len(s.get("name", ""))
    )


def _write_report(rows, stats, executed: bool):
    out = {
        "executed": executed,
        "stats": dict(stats),
        "rows": rows,
    }
    path = "/tmp/cleanup_supplier_duplicates_report.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[cleanup] Rapport JSON complet ecrit dans {path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true",
                   help="Applique reellement les changements (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="",
                   help="Restreint a une seule ACP (defaut : toutes)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
