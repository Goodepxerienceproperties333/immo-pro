"""iter90ji : Nettoyage des doublons FI auto-generes vs FI importes.

Contexte du bug
---------------
Le user importe ses ecritures financieres via le wizard Optipro (source of
truth). Or, quand il comptabilise AUSSI ses extraits bancaires (POST
/statements/{id}/post), `generate_bank_entry` cree un NOUVEAU JE FI qui
DOUBLONNE le JE importe (meme montant, meme date, meme compte tier).

Consequence : la balance des tiers affiche 2 debits pour un seul paiement.

Detection
---------
Un JE FI auto (`auto_generated=True`) est considere COMME doublon d'un JE FI
importe/manuel (`auto_generated!=True` ou `import_session_id` non nul) si :
- Meme ACP (`copropriete_id`)
- Meme date (`date`)
- Meme montant total (`total_debit` == autre.total_debit)
- Un compte tier commun (440XXXXX / 4100XXXX / 4101XXXX)

Action
------
Contre-passe (reverse_auto_entries) le JE FI auto - le JE importe reste la
source of truth. Cela preserve l'audit trail (contrairement a un delete
brut) et permet un rollback via unpost si necessaire.

Usage
-----
    python -m scripts.cleanup_duplicate_auto_fi
    python -m scripts.cleanup_duplicate_auto_fi --execute
    python -m scripts.cleanup_duplicate_auto_fi --copropriete-id CID --execute

Rapport : /tmp/cleanup_duplicate_auto_fi_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from journal_reversals import reverse_auto_entries  # noqa: E402


TIER_PREFIXES = ("440", "4100", "4101", "400")  # comptes fournisseurs + owners


def _tier_accounts_of(je: dict) -> list[str]:
    """Retourne la liste des comptes tier presents dans le JE."""
    out = []
    for ln in je.get("lines", []):
        acc = (ln.get("account_number") or "").strip()
        if any(acc.startswith(p) for p in TIER_PREFIXES):
            out.append(acc)
    return out


def _sig(je: dict) -> tuple:
    """Signature "canonique" d'un JE FI pour detection de doublon.

    (copropriete_id, date, montant_total_arrondi, frozenset_comptes_tier)
    """
    total = round(float(je.get("total_debit") or je.get("total_credit") or 0), 2)
    return (
        (je.get("copropriete_id") or "").strip(),
        (je.get("date") or "").strip(),
        total,
        frozenset(_tier_accounts_of(je)),
    )


async def _run(args) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    q: dict = {
        "journal_type": "FI",
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
    }
    if args.copropriete_id:
        q["copropriete_id"] = args.copropriete_id

    fis = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
    print(f"[cleanup-dup-fi] {len(fis)} JEs FI actifs scannes.")

    # Groupe par signature (mais deux comptes tier partages suffisent)
    by_sig: dict[tuple, list[dict]] = defaultdict(list)
    for je in fis:
        sig = _sig(je)
        if not sig[3]:  # aucun compte tier -> pas concerne
            continue
        by_sig[sig].append(je)

    # iter90jl : PASS 2 - detection fuzzy des doublons legacy dont les
    # comptes tier ne sont PAS strictement identiques (differences dues aux
    # migrations 4400XXX -> 44000XXX, ou 4001XXX -> 41010XXX). On regroupe
    # par (copro, date, amount) et on merge les groupes qui partagent au
    # moins UN compte tier ou UN meme third_party_id.
    def _tp_ids_of(je):
        return {ln.get("third_party_id") for ln in je.get("lines", []) if ln.get("third_party_id")}

    by_key: dict[tuple, list[list[dict]]] = defaultdict(list)
    for sig, group in by_sig.items():
        key = (sig[0], sig[1], sig[2])  # copro + date + amount
        by_key[key].append(group)
    # Merge des groupes partageant un tier account OU un third_party_id
    merged_groups: list[list[dict]] = []
    for _, groups in by_key.items():
        if len(groups) == 1:
            merged_groups.append(groups[0])
            continue
        used = [False] * len(groups)
        for i in range(len(groups)):
            if used[i]:
                continue
            cluster = list(groups[i])
            cluster_accs = set()
            cluster_tps = set()
            for je in cluster:
                for ln in je.get("lines", []):
                    acc = (ln.get("account_number") or "").strip()
                    if any(acc.startswith(p) for p in TIER_PREFIXES):
                        cluster_accs.add(acc)
                    if ln.get("third_party_id"):
                        cluster_tps.add(ln["third_party_id"])
            used[i] = True
            # Cherche a merger d'autres groupes du meme key
            changed = True
            while changed:
                changed = False
                for j in range(len(groups)):
                    if used[j]:
                        continue
                    g_accs = set()
                    g_tps = set()
                    for je in groups[j]:
                        for ln in je.get("lines", []):
                            acc = (ln.get("account_number") or "").strip()
                            if any(acc.startswith(p) for p in TIER_PREFIXES):
                                g_accs.add(acc)
                            if ln.get("third_party_id"):
                                g_tps.add(ln["third_party_id"])
                    if (cluster_accs & g_accs) or (cluster_tps & g_tps):
                        cluster.extend(groups[j])
                        cluster_accs |= g_accs
                        cluster_tps |= g_tps
                        used[j] = True
                        changed = True
            merged_groups.append(cluster)

    to_reverse: list[dict] = []
    kept: list[dict] = []
    for group in merged_groups:
        if len(group) < 2:
            continue
        # Priorite du "keeper" (celui qu'on garde) :
        #   1. imported (`import_session_id` present et non nul)
        #   2. manuel (`auto_generated` False et pas d'import_session)
        #   3. auto-genere en dernier (celui qu'on contre-passe)
        def _priority(je):
            if je.get("import_session_id"):
                return 0
            if not je.get("auto_generated"):
                return 1
            return 2
        group_sorted = sorted(group, key=_priority)
        keeper = group_sorted[0]
        # On contre-passe UNIQUEMENT les auto-generes (jamais l'importe / manuel)
        losers = [j for j in group_sorted[1:] if j.get("auto_generated")]
        if not losers:
            continue
        # Signature representative du cluster (utilise la 1re du groupe)
        sig_repr = _sig(keeper)
        kept.append({
            "sig": {"date": sig_repr[1], "total": sig_repr[2], "acc": sorted(sig_repr[3])},
            "keeper_ref": keeper.get("reference"),
            "keeper_source": (
                "imported" if keeper.get("import_session_id")
                else ("manual" if not keeper.get("auto_generated") else "auto")
            ),
            "losers_count": len(losers),
        })
        for loser in losers:
            to_reverse.append({
                "je_id": loser["id"],
                "je_ref": loser.get("reference"),
                "sig": sig_repr,
                "keeper_ref": keeper.get("reference"),
                "keeper_source": (
                    "imported" if keeper.get("import_session_id") else "manual"
                ),
                "source_type": loser.get("source_type"),
                "source_id": loser.get("source_id"),
            })

    print(f"[cleanup-dup-fi] {len(to_reverse)} FI auto doublons detectes dans {len(kept)} groupes.")

    if to_reverse:
        print("\n[cleanup-dup-fi] Aperçu (top 15) :")
        print(f"{'JE ref':<20} {'keeps':<20} {'src':<10} {'date':<12} {'total':>10}")
        print("-" * 80)
        for r in to_reverse[:15]:
            print(f"{r['je_ref']:<20} {r['keeper_ref']:<20} {r['keeper_source']:<10} {r['sig'][1]:<12} {r['sig'][2]:>10}")
        if len(to_reverse) > 15:
            print(f"... et {len(to_reverse) - 15} autres.")

    if not args.execute:
        print("\n[cleanup-dup-fi] DRY-RUN. Relance avec --execute.")
        _write_report(kept, to_reverse, executed=False)
        return 0

    print("\n[cleanup-dup-fi] EXECUTION : contre-passation des doublons auto...")
    reversed_ok = 0
    errors: list[dict] = []
    for r in to_reverse:
        try:
            # Utilise reverse_auto_entries si le JE a source_type/source_id
            # (traçabilite). Sinon, contre-passation manuelle directe.
            if r["source_type"] and r["source_id"]:
                await reverse_auto_entries(
                    db, r["source_type"], r["source_id"],
                    reason=f"iter90ji_dup_of_{r['keeper_ref']}",
                )
                reversed_ok += 1
            else:
                # Fallback : contre-passation manuelle du JE precis
                await _manual_reverse(db, r["je_id"], reason=f"iter90ji_dup_of_{r['keeper_ref']}")
                reversed_ok += 1
        except Exception as e:
            errors.append({"je_id": r["je_id"], "je_ref": r["je_ref"], "error": str(e)})

    print(f"[cleanup-dup-fi] {reversed_ok}/{len(to_reverse)} doublons contre-passes. {len(errors)} erreurs.")
    _write_report(kept, to_reverse, executed=True, reversed_ok=reversed_ok, errors=errors)
    return 0


async def _manual_reverse(db, je_id: str, reason: str):
    """Contre-passation manuelle d'un JE unique (fallback quand source_type/id absent)."""
    from datetime import datetime, timezone
    import uuid
    je = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
    if not je or je.get("reversed") or je.get("is_reversal"):
        return
    # Cree une contre-passation
    rev_lines = []
    for ln in je.get("lines", []):
        rev_lines.append({**ln, "debit": ln.get("credit", 0), "credit": ln.get("debit", 0)})
    rev_doc = {
        "id": str(uuid.uuid4()),
        "journal_type": je.get("journal_type"),
        "date": je.get("date"),
        "reference": f"REV-{je.get('reference','')}"[:32],
        "description": f"Contre-passation ({reason})",
        "lines": rev_lines,
        "total_debit": je.get("total_credit", 0),
        "total_credit": je.get("total_debit", 0),
        "copropriete_id": je.get("copropriete_id"),
        "is_reversal": True,
        "reverses_je_id": je_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(rev_doc)
    await db.journal_entries.update_one(
        {"id": je_id}, {"$set": {"reversed": True, "reversed_by": rev_doc["id"], "reversed_reason": reason}},
    )


def _write_report(kept, to_reverse, executed: bool, reversed_ok: int = 0, errors: list | None = None):
    out = {
        "executed": executed,
        "groups_with_dup": len(kept),
        "to_reverse_count": len(to_reverse),
        "reversed_ok": reversed_ok,
        "errors": errors or [],
        "kept_examples": kept[:30],
        "to_reverse_examples": [
            {"je_ref": r["je_ref"], "keeper_ref": r["keeper_ref"], "keeper_source": r["keeper_source"], "sig_date": r["sig"][1], "sig_total": r["sig"][2]}
            for r in to_reverse[:30]
        ],
    }
    path = "/tmp/cleanup_duplicate_auto_fi_report.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"[cleanup-dup-fi] Rapport ecrit : {path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true", help="Applique (defaut : dry-run)")
    p.add_argument("--copropriete-id", default="", help="Restreint a une ACP")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
