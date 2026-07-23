"""
fix_matexi_balance.py — Diagnostic + Correction du propriétaire Matexi
=======================================================================
ACP : Gaura (identifiee automatiquement par le script)
Probleme : Matexi apparait dans le journal d'ouverture (compte 41010986)
           mais pas dans la balance des tiers ni le bilan.

Causes possibles :
  1. Le champ `tier_accounts.{acp}.provisions` de Matexi ne contient pas 41010986
  2. Les lignes du journal d'ouverture sur 41010986 n'ont pas de `third_party_id`
     pointant vers l'ID de Matexi

Ce script corrige les deux.

Usage :
  cd /app/backend && python scripts/fix_matexi_balance.py [--dry-run]
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY_RUN = "--dry-run" in sys.argv

async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "copro_db")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    mode = "DRY-RUN (aucune modification)" if DRY_RUN else "CORRECTION ACTIVE"
    print(f"\n{'='*70}")
    print(f"  fix_matexi_balance.py — Mode : {mode}")
    print(f"{'='*70}\n")

    # ============================================================
    # ETAPE 1 : Trouver l'ACP Gaura
    # ============================================================
    gaura = await db.coproprietes.find_one(
        {"name": {"$regex": "gaura", "$options": "i"}},
        {"_id": 0, "id": 1, "name": 1},
    )
    if not gaura:
        # Fallback : chercher dans toutes les ACPs
        all_acps = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)
        print("ACP 'Gaura' non trouvee. ACPs disponibles :")
        for a in all_acps:
            print(f"  - {a['name']} (id: {a['id']})")
        print("\nModifiez le script pour cibler la bonne ACP.")
        return
    copro_id = gaura["id"]
    print(f"[1] ACP trouvee : {gaura['name']} (id: {copro_id})")

    # ============================================================
    # ETAPE 2 : Trouver le proprietaire Matexi
    # ============================================================
    matexi = await db.owners.find_one(
        {"name": {"$regex": "matexi", "$options": "i"}},
        {"_id": 0},
    )
    if not matexi:
        # Chercher aussi dans les owners avec tier_accounts pour cette ACP
        all_owners = await db.owners.find(
            {f"tier_accounts.{copro_id}": {"$exists": True}},
            {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1},
        ).to_list(1000)
        print("\nProprietaire 'Matexi' non trouve. Owners avec tier_accounts pour cette ACP :")
        for o in all_owners:
            tier = o.get("tier_accounts", {}).get(copro_id, {})
            print(f"  - {o['name']} (id: {o['id']}) => provisions={tier.get('provisions','')}, reserve={tier.get('reserve','')}")
        # Chercher aussi les owners sans tier_accounts
        print("\nTous les owners dans la base (par nom contenant 'mat') :")
        mats = await db.owners.find(
            {"name": {"$regex": "mat", "$options": "i"}},
            {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1},
        ).to_list(100)
        for o in mats:
            print(f"  - {o['name']} (id: {o['id']})")
        return

    matexi_id = matexi["id"]
    matexi_name = matexi.get("name", "?")
    tier_accounts = (matexi.get("tier_accounts") or {}).get(copro_id, {}) or {}
    current_prov = tier_accounts.get("provisions", "")
    current_res = tier_accounts.get("reserve", "")

    print(f"[2] Proprietaire trouve : {matexi_name} (id: {matexi_id})")
    print(f"    tier_accounts[{copro_id}] :")
    print(f"      provisions = '{current_prov}'")
    print(f"      reserve    = '{current_res}'")
    print(f"    VCS code     = '{matexi.get('vcs_code', '')}'")

    # ============================================================
    # ETAPE 3 : Trouver les ecritures avec compte 41010986
    # ============================================================
    TARGET_ACC = "41010986"
    entries_with_target = await db.journal_entries.find(
        {
            "copropriete_id": copro_id,
            "lines.account_number": TARGET_ACC,
        },
        {"_id": 0},
    ).to_list(10000)

    print(f"\n[3] Ecritures contenant le compte {TARGET_ACC} dans {gaura['name']} : {len(entries_with_target)}")

    lines_without_tp = []
    lines_with_wrong_tp = []
    lines_ok = []

    for e in entries_with_target:
        for i, ln in enumerate(e.get("lines", [])):
            if ln.get("account_number") != TARGET_ACC:
                continue
            tpid = ln.get("third_party_id", "")
            entry_info = {
                "entry_id": e.get("id"),
                "journal_type": e.get("journal_type", ""),
                "date": e.get("date", ""),
                "reference": e.get("reference", ""),
                "description": e.get("description", ""),
                "line_idx": i,
                "debit": ln.get("debit", 0),
                "credit": ln.get("credit", 0),
                "third_party_id": tpid,
            }
            if not tpid:
                lines_without_tp.append(entry_info)
            elif tpid != matexi_id:
                lines_with_wrong_tp.append(entry_info)
            else:
                lines_ok.append(entry_info)

    print(f"    Lignes avec third_party_id = Matexi (OK)   : {len(lines_ok)}")
    print(f"    Lignes SANS third_party_id                  : {len(lines_without_tp)}")
    print(f"    Lignes avec third_party_id DIFFERENT        : {len(lines_with_wrong_tp)}")

    for ln in lines_without_tp:
        print(f"      MANQUANT : {ln['journal_type']} {ln['date']} ref={ln['reference']} "
              f"D={ln['debit']:.2f} C={ln['credit']:.2f} entry={ln['entry_id']}")
    for ln in lines_with_wrong_tp:
        print(f"      MAUVAIS  : {ln['journal_type']} {ln['date']} ref={ln['reference']} "
              f"D={ln['debit']:.2f} C={ln['credit']:.2f} tp={ln['third_party_id']} entry={ln['entry_id']}")

    # ============================================================
    # ETAPE 4 : Verification tier_accounts
    # ============================================================
    print(f"\n[4] Verification tier_accounts")
    needs_tier_fix = False
    if current_prov != TARGET_ACC:
        print(f"    PROBLEME : provisions = '{current_prov}' au lieu de '{TARGET_ACC}'")
        needs_tier_fix = True
    else:
        print(f"    OK : provisions = '{current_prov}'")

    # Verifier aussi qu'il y a un compte reserve (41000986 typiquement)
    expected_res = TARGET_ACC.replace("41010", "41000")
    if not current_res:
        print(f"    INFO : reserve non renseigne (optionnel, attendu: '{expected_res}')")
    elif current_res != expected_res:
        print(f"    INFO : reserve = '{current_res}' (vs attendu '{expected_res}')")
    else:
        print(f"    OK : reserve = '{current_res}'")

    # ============================================================
    # ETAPE 5 : CORRECTIONS
    # ============================================================
    print(f"\n[5] CORRECTIONS {'(DRY-RUN)' if DRY_RUN else ''}")
    corrections = 0

    # 5a) Corriger tier_accounts si necessaire
    if needs_tier_fix:
        new_tier = dict(tier_accounts)
        new_tier["provisions"] = TARGET_ACC
        if not new_tier.get("reserve"):
            new_tier["reserve"] = expected_res
        print(f"    -> Mise a jour tier_accounts[{copro_id}] = {new_tier}")
        if not DRY_RUN:
            await db.owners.update_one(
                {"id": matexi_id},
                {"$set": {f"tier_accounts.{copro_id}": new_tier}},
            )
            corrections += 1
            print(f"       FAIT.")
        else:
            print(f"       (dry-run, pas de modification)")

    # 5b) Corriger third_party_id manquants
    for ln in lines_without_tp:
        entry = await db.journal_entries.find_one({"id": ln["entry_id"]}, {"_id": 0})
        if not entry:
            continue
        new_lines = list(entry.get("lines", []))
        changed = False
        for line in new_lines:
            if line.get("account_number") == TARGET_ACC and not line.get("third_party_id"):
                line["third_party_id"] = matexi_id
                changed = True
        if changed:
            print(f"    -> Ajout third_party_id={matexi_id} sur {ln['journal_type']} "
                  f"{ln['date']} (entry {ln['entry_id']})")
            if not DRY_RUN:
                await db.journal_entries.update_one(
                    {"id": ln["entry_id"]},
                    {"$set": {"lines": new_lines}},
                )
                corrections += 1
                print(f"       FAIT.")
            else:
                print(f"       (dry-run, pas de modification)")

    # 5c) Corriger third_party_id incorrects (pointing to wrong owner)
    for ln in lines_with_wrong_tp:
        entry = await db.journal_entries.find_one({"id": ln["entry_id"]}, {"_id": 0})
        if not entry:
            continue
        new_lines = list(entry.get("lines", []))
        changed = False
        for line in new_lines:
            if (line.get("account_number") == TARGET_ACC
                    and line.get("third_party_id") != matexi_id):
                old_tp = line["third_party_id"]
                line["third_party_id"] = matexi_id
                changed = True
                print(f"    -> Correction third_party_id {old_tp} -> {matexi_id} sur "
                      f"{ln['journal_type']} {ln['date']} (entry {ln['entry_id']})")
        if changed:
            if not DRY_RUN:
                await db.journal_entries.update_one(
                    {"id": ln["entry_id"]},
                    {"$set": {"lines": new_lines}},
                )
                corrections += 1
                print(f"       FAIT.")
            else:
                print(f"       (dry-run, pas de modification)")

    # Verifier aussi le compte reserve (41000986) si existant
    RESERVE_ACC = expected_res
    entries_res = await db.journal_entries.find(
        {"copropriete_id": copro_id, "lines.account_number": RESERVE_ACC},
        {"_id": 0},
    ).to_list(10000)
    if entries_res:
        print(f"\n    --- Verification compte reserve {RESERVE_ACC} ---")
        for e in entries_res:
            new_lines = list(e.get("lines", []))
            changed = False
            for line in new_lines:
                if line.get("account_number") == RESERVE_ACC and not line.get("third_party_id"):
                    line["third_party_id"] = matexi_id
                    changed = True
                    print(f"    -> Ajout third_party_id={matexi_id} sur reserve {e.get('journal_type')} "
                          f"{e.get('date')} (entry {e.get('id')})")
            if changed:
                if not DRY_RUN:
                    await db.journal_entries.update_one(
                        {"id": e["id"]},
                        {"$set": {"lines": new_lines}},
                    )
                    corrections += 1
                    print(f"       FAIT.")
                else:
                    print(f"       (dry-run, pas de modification)")

    # ============================================================
    # ETAPE 6 : VERIFICATION FINALE
    # ============================================================
    print(f"\n[6] VERIFICATION FINALE")
    if DRY_RUN:
        print("    (Dry-run - re-executer sans --dry-run pour appliquer)")
    else:
        # Recharger et verifier
        matexi_fresh = await db.owners.find_one({"id": matexi_id}, {"_id": 0})
        tier_fresh = (matexi_fresh.get("tier_accounts") or {}).get(copro_id, {}) or {}
        print(f"    tier_accounts apres correction : {tier_fresh}")

        # Calculer le solde
        je_q = {
            "copropriete_id": copro_id,
            "lines.third_party_id": matexi_id,
        }
        entries_final = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)
        total_debit = 0.0
        total_credit = 0.0
        for e in entries_final:
            for ln in e.get("lines", []):
                if ln.get("third_party_id") == matexi_id:
                    total_debit += float(ln.get("debit", 0) or 0)
                    total_credit += float(ln.get("credit", 0) or 0)
        balance = round(total_debit - total_credit, 2)
        print(f"    Solde recalcule (Debit - Credit) : {total_debit:.2f} - {total_credit:.2f} = {balance:.2f} EUR")
        expected = 1869.83
        if abs(balance - expected) < 0.01:
            print(f"    ✓ MATCH avec le montant attendu de {expected:.2f} EUR")
        else:
            print(f"    ⚠ Ecart avec le montant attendu ({expected:.2f} EUR). Verifiez manuellement.")

    print(f"\n{'='*70}")
    print(f"  RESUME : {corrections} correction(s) appliquee(s)")
    if DRY_RUN:
        print(f"  → Re-executer SANS --dry-run pour appliquer les modifications")
    else:
        print(f"  → Rafraichir la Balance des Tiers dans l'application")
    print(f"{'='*70}\n")

    client.close()

if __name__ == "__main__":
    asyncio.run(main())
