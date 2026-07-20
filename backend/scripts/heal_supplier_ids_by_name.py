"""iter90ix : Ré-attribution du third_party_id + account_number des lignes JE
440xxx en se basant sur le NOM du fournisseur.

Contexte
--------
Après le refactor "Chinese Wall" (iter90is) chaque fournisseur est LOCAL a
UNE ACP et porte un `tier_account_number` canonique 8 chars ("44000XXX").
Or, les imports historiques (Optipro, CODA, wizard PDF) ont laissé dans
`journal_entries.lines` des lignes de compte 440xxx :
  1. Sans `third_party_id` (orphelines)
  2. Avec un `third_party_id` pointant vers une ANCIENNE fiche globale
     (dédupliquée depuis)
  3. Avec un `account_number` en 7 chars ("4400015") non canonique

Ce script parcourt les JE, matche chaque ligne 440xxx à sa fiche fournisseur
via le NOM (dans la même ACP), puis ré-écrit `third_party_id`,
`third_party_type` et `account_number` (forcé en 8 chars canoniques).

Usage
-----
    python -m scripts.heal_supplier_ids_by_name
    python -m scripts.heal_supplier_ids_by_name --execute
    python -m scripts.heal_supplier_ids_by_name --copropriete-id CID --execute

Sortie
------
Rapport JSON : /tmp/heal_supplier_ids_by_name_report.json
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.suppliers import _norm_name_candidates  # noqa: E402
from tier_accounts import (  # noqa: E402
    canonize_supplier_tier_account,
    assign_supplier_account,
)


def _pick_line_name(line: dict) -> str:
    """Récupère le meilleur nom possible pour matcher un fournisseur.

    Ordre de priorité : `third_party_name` > `account_name` > `label`
    > `description`. On skip les valeurs vides / génériques ("Fournisseurs").
    """
    for k in ("third_party_name", "account_name", "label", "description"):
        v = (line.get(k) or "").strip()
        if not v:
            continue
        # Filtre les libellés génériques trop courts / bruités
        if v.lower() in ("fournisseurs", "fournisseur", "440", "440000"):
            continue
        return v
    return ""


def _canonical_tier(sup: dict, copro_id: str) -> str:
    """Retourne le compte tier canonique 8 chars d'un supplier pour l'ACP.

    Priorité : `tier_account_number` (post iter90is) > `tier_accounts[cp].main`
    (legacy). Systématiquement passé par `canonize_supplier_tier_account`
    (verrou 8 chars, non-négociable).
    """
    num = (sup.get("tier_account_number") or "").strip()
    if not num:
        num = ((sup.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
    return canonize_supplier_tier_account(num)


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # 0) Pré-pass : auto-assign des tier_account_number manquants (idempotent).
    # Sans cela, les fiches sans `tier_account_number` (fiches manuelles pré-
    # iter90is, ou fiches issues d'un import qui n'a pas touché à ce champ)
    # seraient ignorées par le healing et les orphelins subsisteraient.
    if not args.no_auto_assign:
        sup_missing_query: dict = {
            "$or": [
                {"tier_account_number": {"$exists": False}},
                {"tier_account_number": ""},
                {"tier_account_number": None},
            ],
        }
        if args.copropriete_id:
            sup_missing_query["copropriete_id"] = args.copropriete_id
        missing = await db.suppliers.find(sup_missing_query, {"_id": 0}).to_list(50000)
        assigned = 0
        for s in missing:
            cid = (s.get("copropriete_id") or "").strip()
            if not cid:
                continue  # chinese wall strict - skip fiches sans ACP
            if args.execute:
                await assign_supplier_account(db, s, cid)
                assigned += 1
        if args.execute:
            print(f"[heal] Pré-pass : {assigned} fiches ont reçu un tier_account_number.")
        else:
            print(f"[heal] Pré-pass DRY : {len(missing)} fiches sans tier_account_number seraient auto-assignées.")

    # 1) Charge tous les suppliers (par ACP)
    sup_query: dict = {}
    if args.copropriete_id:
        sup_query["copropriete_id"] = args.copropriete_id
    all_suppliers = await db.suppliers.find(
        sup_query,
        {"_id": 0, "id": 1, "name": 1, "copropriete_id": 1,
         "tier_account_number": 1, "tier_accounts": 1},
    ).to_list(50000)

    # Index par ACP : { copro_id -> [ (name_cands_set, canonical_account, supplier_id, supplier_name) ] }
    idx_by_copro: dict[str, list[tuple[frozenset, str, str, str]]] = {}
    skipped_no_account = 0
    for s in all_suppliers:
        cid = (s.get("copropriete_id") or "").strip()
        if not cid:
            # Chinese wall strict : on ignore les fiches sans copropriete_id
            continue
        canonical = _canonical_tier(s, cid)
        if not canonical or not canonical.startswith("440"):
            skipped_no_account += 1
            continue
        # Verrou 8 chars strict : refuse tout compte < 8 chars après canonisation
        if len(canonical) != 8:
            print(f"[WARN] Supplier {s['id']} ({s.get('name','')}) : compte tier '{canonical}' non-canonique après normalisation, ignoré.")
            continue
        cands = _norm_name_candidates(s.get("name", ""))
        if not cands:
            continue
        idx_by_copro.setdefault(cid, []).append(
            (frozenset(cands), canonical, s["id"], s.get("name", ""))
        )

    total_supp_indexed = sum(len(v) for v in idx_by_copro.values())
    print(f"[heal] {total_supp_indexed} fournisseurs indexés sur {len(idx_by_copro)} ACP(s).")
    if skipped_no_account:
        print(f"[heal] {skipped_no_account} fiches ignorées (pas de tier_account_number).")

    # 2) Parcours des JE et détection des lignes à corriger
    je_query: dict = {"lines.account_number": {"$regex": "^440"}}
    if args.copropriete_id:
        je_query["copropriete_id"] = args.copropriete_id

    stats: Counter = Counter()
    updates: list[dict] = []
    # Pour l'update, on doit repérer précisément quelle ligne du JE modifier
    async for je in db.journal_entries.find(je_query, {
        "_id": 0, "id": 1, "copropriete_id": 1, "journal_type": 1,
        "date": 1, "reference": 1, "lines": 1,
    }):
        cid = (je.get("copropriete_id") or "").strip()
        sup_index = idx_by_copro.get(cid) or []
        for idx, ln in enumerate(je.get("lines") or []):
            acc = (ln.get("account_number") or "").strip()
            if not acc.startswith("440"):
                continue
            stats["lines_440_total"] += 1
            # Verrou 8 chars : détection des comptes non-canoniques
            canonical_acc_line = canonize_supplier_tier_account(acc)
            need_acc_rewrite = (canonical_acc_line != acc) or (len(acc) != 8)

            name = _pick_line_name(ln)
            if not name:
                stats["no_name"] += 1
                # Si l'ancien third_party_id est renseigné, on peut essayer
                # de retrouver via lui pour au moins canoniser le compte.
                if need_acc_rewrite and ln.get("third_party_id"):
                    updates.append({
                        "je_id": je["id"], "line_idx": idx,
                        "copro_id": cid,
                        "date": je.get("date", ""),
                        "reference": je.get("reference", ""),
                        "old_account": acc, "new_account": canonical_acc_line,
                        "old_tp_id": ln.get("third_party_id"),
                        "new_tp_id": ln.get("third_party_id"),
                        "reason": "canonize_only",
                        "supplier_name": "",
                    })
                    stats["canonize_only"] += 1
                continue
            # Match par nom (candidats normalisés)
            ln_cands = _norm_name_candidates(name)
            if not ln_cands:
                stats["no_norm_cands"] += 1
                continue
            match = None
            for cands, canonical, sup_id, sup_name in sup_index:
                if ln_cands & cands:
                    match = (canonical, sup_id, sup_name)
                    break
            if not match:
                stats["no_supplier_match"] += 1
                continue
            canonical, sup_id, sup_name = match
            # Décide s'il y a un vrai changement à écrire
            cur_tp_id = ln.get("third_party_id") or None
            cur_acc = acc
            change_tp = (cur_tp_id != sup_id)
            change_acc = (cur_acc != canonical)
            if not (change_tp or change_acc or need_acc_rewrite):
                stats["already_ok"] += 1
                continue
            updates.append({
                "je_id": je["id"],
                "line_idx": idx,
                "copro_id": cid,
                "date": je.get("date", ""),
                "reference": je.get("reference", ""),
                "old_account": cur_acc,
                "new_account": canonical,
                "old_tp_id": cur_tp_id,
                "new_tp_id": sup_id,
                "reason": (
                    "tp_and_acc" if (change_tp and change_acc)
                    else "tp_only" if change_tp else "acc_only"
                ),
                "supplier_name": sup_name,
                "matched_by_name": name,
            })
            stats["will_heal"] += 1

    print(f"[heal] {stats['lines_440_total']} lignes 440xxx scannées :")
    print(f"       - {stats['will_heal']} à corriger (tp_id ± account)")
    print(f"       - {stats['canonize_only']} canonisation compte seule (nom introuvable)")
    print(f"       - {stats['already_ok']} déjà OK")
    print(f"       - {stats['no_supplier_match']} sans fiche fournisseur (nom non retrouvé)")
    print(f"       - {stats['no_name']} sans nom exploitable")
    print(f"       - {stats['no_norm_cands']} sans candidats normalisés")

    if updates:
        print("\n[heal] --- Aperçu (top 25) ---")
        print(f"{'JE ID':<36} {'Line':<5} {'Old acc':<9} -> {'New acc':<9} {'Reason':<16} {'Fournisseur':<40}")
        print("-" * 140)
        for u in updates[:25]:
            print(f"{u['je_id']:<36} {u['line_idx']:<5} {u['old_account']:<9} -> {u['new_account']:<9} "
                  f"{u['reason']:<16} {(u.get('supplier_name') or '')[:39]:<40}")
        if len(updates) > 25:
            print(f"... et {len(updates) - 25} autres.")

    # 3) Exécution
    if not args.execute:
        print("\n[heal] DRY-RUN : aucune écriture DB. Relance avec --execute.")
        _write_report(updates, stats, executed=False)
        return 0

    print("\n[heal] EXECUTION : application des changements...")
    modified_lines = 0
    modified_docs = 0
    errors: list[str] = []
    # Regroupe les updates par JE pour ne faire qu'un update par doc
    per_je: dict[str, list[dict]] = {}
    for u in updates:
        per_je.setdefault(u["je_id"], []).append(u)
    for je_id, ulist in per_je.items():
        je = await db.journal_entries.find_one({"id": je_id}, {"_id": 0, "lines": 1})
        if not je:
            errors.append(f"JE {je_id} introuvable")
            continue
        lines = list(je.get("lines") or [])
        changed = False
        for u in ulist:
            i = u["line_idx"]
            if i < 0 or i >= len(lines):
                errors.append(f"JE {je_id} : line_idx {i} out-of-range")
                continue
            ln = dict(lines[i])
            if u["new_account"] and ln.get("account_number") != u["new_account"]:
                ln["account_number"] = u["new_account"]
                changed = True
                modified_lines += 1
            if u["new_tp_id"] and ln.get("third_party_id") != u["new_tp_id"]:
                ln["third_party_id"] = u["new_tp_id"]
                ln["third_party_type"] = "supplier"
                changed = True
            if u.get("supplier_name") and not (ln.get("third_party_name") or "").strip():
                ln["third_party_name"] = u["supplier_name"]
            lines[i] = ln
        if changed:
            r = await db.journal_entries.update_one(
                {"id": je_id}, {"$set": {"lines": lines}},
            )
            if r.modified_count:
                modified_docs += 1

    print(f"[heal] Terminé : {modified_lines} lignes réécrites dans {modified_docs} JE.")
    if errors:
        print(f"[heal] {len(errors)} erreurs : {errors[:5]} ...")
    _write_report(updates, stats, executed=True,
                  modified_lines=modified_lines, modified_docs=modified_docs,
                  errors=errors)
    return 0


def _write_report(rows, stats, executed: bool,
                  modified_lines: int = 0, modified_docs: int = 0,
                  errors: list | None = None):
    out = {
        "executed": executed,
        "stats": dict(stats),
        "rows_count": len(rows),
        "modified_lines": modified_lines,
        "modified_docs": modified_docs,
        "errors": errors or [],
        "rows": rows,
    }
    path = "/tmp/heal_supplier_ids_by_name_report.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[heal] Rapport JSON complet écrit dans {path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true",
                   help="Applique réellement les changements (défaut : dry-run)")
    p.add_argument("--copropriete-id", default="",
                   help="Restreint à une seule ACP (défaut : toutes)")
    p.add_argument("--no-auto-assign", action="store_true",
                   help="Désactive l'auto-assignation des tier_account_number manquants (défaut : ON)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
