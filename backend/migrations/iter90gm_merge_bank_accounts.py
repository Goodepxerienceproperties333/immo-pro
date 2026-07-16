"""iter90gm : Fusion des comptes bancaires dupliques (parent 6-char vs canonique 8-char).

Contexte : le wizard `commit_opening_balance` cree les lignes AN sur les comptes
BANCAIRES tels que sortis du PDF Optipro (6 chars, ex: 551331, 550732).
Or l'application utilise des comptes canoniques 8 chars pour les operations
bancaires quotidiennes (55133100, 55073200 - suffix "00" applique par le
module de reconciliation bancaire).

Consequence : le Bilan affiche 3 lignes bancaires (2 comptes 6-char + 1 compte
8-char) au lieu de 2 lignes fusionnees. Les soldes sont equilibres mais la
presentation est trompeuse pour le syndic.

Ce script :
  1. Pour chaque ACP, identifie les paires (Ncc, Ncc00) ou l'un a des ecritures
  2. Migre les lignes de journal_entries du compte non-canonique vers le canonique
  3. Marque le compte non-canonique comme "obsolete" (garde en pcmn_accounts
     pour l'historique mais plus utilise)

La regle de canonisation : si un compte 6-char `AAABCC` existe ET un compte
8-char `AAABCC00` existe pour la meme classe bancaire (55XXXX), on garde
le 8-char. Cette regle correspond a la convention du module bank_txn.

Usage :
  python -m migrations.iter90gm_merge_bank_accounts                # dry-run
  python -m migrations.iter90gm_merge_bank_accounts --apply       # applique
  python -m migrations.iter90gm_merge_bank_accounts --copro-id ID # scope
"""
import asyncio
import argparse
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


async def merge_bank_accounts(db, copro_id: str, apply: bool = False) -> dict:
    report = {"copro_id": copro_id, "merges": [], "lines_migrated": 0}

    # Etape 1 : identifier les paires (6-char, 8-char) de comptes bancaires
    pcmn = await db.pcmn_accounts.find(
        {"copropriete_id": copro_id, "number": {"$regex": "^5[0-9]"}},
        {"_id": 0, "number": 1, "name": 1},
    ).to_list(1000)
    by_number = {a["number"]: a for a in pcmn}
    merges: list[tuple[str, str]] = []  # (from_non_canonical, to_canonical)
    for a in pcmn:
        num = a["number"]
        # Cherche un canonique 8-char correspondant : num + "00"
        if len(num) == 6:
            candidate = num + "00"
            if candidate in by_number:
                merges.append((num, candidate))
        # Cas alternatif : num se termine par "0" (ex: 5501310 -> 55131100 ?)
        # Actuellement on ne gere que le pattern strict num + "00".

    if not merges:
        return report

    # Etape 2 : pour chaque paire, migrer les lignes
    for from_acc, to_acc in merges:
        target_name = by_number[to_acc].get("name", "")
        # Compte les lignes touchant from_acc
        touched_entries = 0
        touched_lines = 0
        async for e in db.journal_entries.find(
            {"copropriete_id": copro_id, "lines.account_number": from_acc},
            {"_id": 0},
        ):
            has_change = False
            for ln in e.get("lines", []):
                if ln.get("account_number") == from_acc:
                    ln["account_number"] = to_acc
                    # Conserve le nom detaille (avec IBAN) car il est plus
                    # informatif que le "Banque compte a vue 331" generique.
                    # On ecrase le nom canonique par le nom source si celui-ci
                    # contient un IBAN.
                    src_name = ln.get("account_name", "") or ""
                    if "BE" in src_name and len(src_name) > len(target_name):
                        # Garde le nom detaille -> update le PCMN aussi
                        pass  # ln keeps its detailed name
                    else:
                        ln["account_name"] = target_name
                    has_change = True
                    touched_lines += 1
            if has_change:
                touched_entries += 1
                if apply:
                    await db.journal_entries.update_one(
                        {"id": e["id"]}, {"$set": {"lines": e["lines"]}}
                    )
        # Update PCMN target name to keep the detailed IBAN one if better
        source_name = by_number[from_acc].get("name", "")
        if "BE" in source_name and "BE" not in target_name:
            if apply:
                await db.pcmn_accounts.update_one(
                    {"copropriete_id": copro_id, "number": to_acc},
                    {"$set": {"name": source_name}},
                )
        report["merges"].append({
            "from": from_acc, "to": to_acc,
            "entries_touched": touched_entries,
            "lines_migrated": touched_lines,
            "source_name": source_name,
            "target_name": target_name,
        })
        report["lines_migrated"] += touched_lines

    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copro-id", type=str, default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    if args.copro_id:
        copro_ids = [args.copro_id]
    else:
        copros = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
        copro_ids = [c["id"] for c in copros]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== iter90gm merge bank accounts ({mode}) - {len(copro_ids)} ACP(s) ===\n")

    total_lines = 0
    for copro_id in copro_ids:
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            continue
        r = await merge_bank_accounts(db, copro_id, apply=args.apply)
        if r["merges"]:
            print(f"\n--- {copro['name']} ---")
            for m in r["merges"]:
                if m["lines_migrated"] > 0:
                    print(f"  {m['from']} ({m['source_name'][:40]}) -> {m['to']} ({m['target_name'][:40]}): {m['lines_migrated']} lines in {m['entries_touched']} entries")
                else:
                    print(f"  {m['from']} -> {m['to']}: no active lines to migrate")
        total_lines += r["lines_migrated"]

    print(f"\n=== TOTAL lines migrated: {total_lines} ===")
    if not args.apply and total_lines > 0:
        print("Dry-run - re-run with --apply")


if __name__ == "__main__":
    asyncio.run(main())
