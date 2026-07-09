"""
iter90cp : Script de reparation automatique de l'ownership pour l'ACP Acacia
sur PROD.

Utilisation via la console Emergent Production :
    cd /app/backend && python scripts/repair_iter90cp_acacia_ownership.py [--dry-run|--commit]

Ce script :
1. Trouve l'ACP Acacia (ou celle passee via --copropriete-id / --name)
2. Auto-detecte le fondateur (proprietaire majoritaire dans la chaine d'ownership)
3. Auto-detecte founder_start_date = 1ere FY.start_date de l'ACP
4. Detecte les 3 cas de reparation :
    - A : lots avec chaine muts[0].from != Matexi
    - B : lots sans mutation dont owner_id != Matexi
    - C : lots orphelins (owner_id vide)
5. Affiche un rapport complet
6. Applique la reparation UNIQUEMENT si --commit est passe (sinon dry_run)

Post-repair : les futurs appels de fonds attribueront correctement les charges
a Matexi pour les lots restaures (fonds de reserve/roulement Q4 2025 = 100%
Matexi si Matexi possede tous les lots au 01.10.2025).

SECURITE :
- Superadmin uniquement (protection deja dans l'endpoint).
- Idempotent : peut etre relance sans risque de doublon.
- Dry-run par defaut.
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date as _date_cls, datetime, timezone
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _find_acacia(db, copropriete_id: str = None, name: str = None):
    if copropriete_id:
        doc = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not doc:
            raise SystemExit(f"ACP {copropriete_id} introuvable")
        return doc
    q = {"name": {"$regex": name or "acacia", "$options": "i"}}
    doc = await db.coproprietes.find_one(q, {"_id": 0})
    if not doc:
        raise SystemExit(
            f"Aucune ACP trouvee via name~{name or 'acacia'}. "
            f"Passez --copropriete-id ou --name explicite."
        )
    return doc


async def _run(dry_run: bool, copropriete_id: str = None, name: str = None,
               founder_owner_id: str = None, founder_start_date: str = None,
               fix_orphans: bool = True):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    acp = await _find_acacia(db, copropriete_id, name)
    cid = acp["id"]
    print(f"\n=== iter90cp : Reparation ownership ACP : {acp.get('name')} ({cid}) ===")
    print(f"Mode : {'DRY RUN (aucune modification)' if dry_run else 'COMMIT (modifications appliquees)'}\n")

    # 1) Detection du fondateur
    lots = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(5000)
    print(f"Lots totaux : {len(lots)}")

    if not founder_owner_id:
        counter = Counter()
        for lot in lots:
            muts_sorted = sorted(
                lot.get("mutations") or [], key=lambda x: x.get("date") or "",
            )
            if muts_sorted:
                ff = (muts_sorted[0].get("old_owner_id")
                      or muts_sorted[0].get("from_owner_id") or "")
                if ff:
                    counter[ff] += 1
            elif lot.get("owner_id"):
                counter[lot["owner_id"]] += 1
        if not counter:
            raise SystemExit(
                "Auto-detection impossible : aucun lot n'a d'owner_id ni de mutations"
            )
        founder_owner_id, votes = counter.most_common(1)[0]
        print(f"Fondateur auto-detecte : {founder_owner_id} ({votes} votes)")
    else:
        print(f"Fondateur fourni : {founder_owner_id}")

    founder = await db.owners.find_one({"id": founder_owner_id}, {"_id": 0})
    if not founder:
        raise SystemExit(f"Fondateur {founder_owner_id} introuvable dans db.owners")
    print(f"Fondateur : {founder.get('name')} ({founder.get('last_name', '')})")

    # 2) Fallback founder_start_date
    if not founder_start_date:
        first_fy = await db.fiscal_years.find_one(
            {"copropriete_id": cid},
            {"_id": 0, "start_date": 1, "name": 1},
            sort=[("start_date", 1)],
        )
        if not first_fy:
            raise SystemExit(
                "Aucun fiscal_year defini pour cette ACP. Passez --founder-start-date manuellement."
            )
        founder_start_date = first_fy["start_date"]
        print(f"founder_start_date auto : {founder_start_date} (FY {first_fy.get('name')})")
    _date_cls.fromisoformat(founder_start_date)

    # 3) Detection des cas
    cases_a, cases_b, cases_c = [], [], []
    for lot in lots:
        muts_sorted = sorted(
            lot.get("mutations") or [], key=lambda x: x.get("date") or "",
        )
        current_owner = lot.get("owner_id", "") or ""

        if not current_owner and not muts_sorted:
            if fix_orphans:
                cases_c.append(lot)
            continue

        if muts_sorted:
            first_from = muts_sorted[0].get("old_owner_id") or ""
            if first_from and first_from != founder_owner_id:
                cases_a.append(lot)
            elif not first_from and current_owner != founder_owner_id:
                cases_b.append(lot)
        else:
            if current_owner and current_owner != founder_owner_id:
                cases_b.append(lot)

    print(f"\n--- Rapport ---")
    print(f"Case A (chain cassee muts[0].from != Matexi)  : {len(cases_a)} lots")
    for lot in cases_a[:20]:
        print(f"  - Lot {lot.get('number')}: muts[0].from = {sorted(lot.get('mutations',[]), key=lambda x: x.get('date',''))[0].get('old_owner_id','')[:20]}")
    if len(cases_a) > 20:
        print(f"  ... +{len(cases_a) - 20} autres")

    print(f"Case B (aucune mutation + owner != Matexi)    : {len(cases_b)} lots")
    for lot in cases_b[:20]:
        print(f"  - Lot {lot.get('number')}: owner_id = {lot.get('owner_id','')[:20]}")
    if len(cases_b) > 20:
        print(f"  ... +{len(cases_b) - 20} autres")

    print(f"Case C (lots orphelins, owner_id vide)         : {len(cases_c)} lots")
    for lot in cases_c[:20]:
        print(f"  - Lot {lot.get('number')}")
    if len(cases_c) > 20:
        print(f"  ... +{len(cases_c) - 20} autres")

    total = len(cases_a) + len(cases_b) + len(cases_c)
    print(f"\nTotal a reparer : {total} lots\n")

    if dry_run:
        print("DRY RUN : aucune modification appliquee. Relancez avec --commit pour appliquer.")
        return

    # 4) Application
    applied = {"a": 0, "b": 0, "c": 0, "errors": []}
    now_iso = datetime.now(timezone.utc).isoformat()

    for lot in cases_a:
        try:
            first_from = sorted(lot.get("mutations",[]), key=lambda x: x.get("date",""))[0].get("old_owner_id") or ""
            mut_id = str(uuid.uuid4())
            foundation_mut = {
                "id": mut_id,
                "date": founder_start_date,
                "old_owner_id": founder_owner_id,
                "new_owner_id": first_from,
                "sale_price": 0.0, "roulement_quota": 0.0,
                "current_period_prorata": 0.0, "prorata_provisions": 0.0,
                "total_transfer": 0.0, "journal_entry_ids": [],
                "created_at": now_iso,
                "foundation_mutation": True, "iter90cm_bis_repair": True,
                "iter90cp_repair": True,
            }
            await db.lots.update_one(
                {"id": lot["id"]},
                {"$push": {"mutations": {"$each": [foundation_mut], "$position": 0}}},
            )
            await db.mutations.update_one(
                {"id": mut_id},
                {"$set": {
                    **foundation_mut,
                    "copropriete_id": cid,
                    "lot_id": lot["id"],
                    "from_owner_id": founder_owner_id,
                    "to_owner_id": first_from,
                    "sale_date": founder_start_date,
                }},
                upsert=True,
            )
            applied["a"] += 1
        except Exception as e:
            applied["errors"].append({"lot": lot.get("number"), "err": str(e)})

    for lot in cases_b:
        try:
            mut_id = str(uuid.uuid4())
            current_owner = lot.get("owner_id", "")
            foundation_mut = {
                "id": mut_id,
                "date": founder_start_date,
                "old_owner_id": founder_owner_id,
                "new_owner_id": current_owner,
                "sale_price": 0.0, "roulement_quota": 0.0,
                "current_period_prorata": 0.0, "prorata_provisions": 0.0,
                "total_transfer": 0.0, "journal_entry_ids": [],
                "created_at": now_iso,
                "foundation_mutation": True, "iter90cm_bis_repair": True,
                "iter90cp_repair": True,
            }
            await db.lots.update_one(
                {"id": lot["id"]},
                {"$push": {"mutations": {"$each": [foundation_mut], "$position": 0}}},
            )
            await db.mutations.update_one(
                {"id": mut_id},
                {"$set": {
                    **foundation_mut,
                    "copropriete_id": cid,
                    "lot_id": lot["id"],
                    "from_owner_id": founder_owner_id,
                    "to_owner_id": current_owner,
                    "sale_date": founder_start_date,
                }},
                upsert=True,
            )
            applied["b"] += 1
        except Exception as e:
            applied["errors"].append({"lot": lot.get("number"), "err": str(e)})

    for lot in cases_c:
        try:
            await db.lots.update_one(
                {"id": lot["id"]},
                {"$set": {
                    "owner_id": founder_owner_id,
                    "owner_ids": [founder_owner_id],
                    "iter90cp_repaired_orphan": True,
                    "iter90cp_repaired_at": now_iso,
                }},
            )
            applied["c"] += 1
        except Exception as e:
            applied["errors"].append({"lot": lot.get("number"), "err": str(e)})

    # Assign founder to copropriete_ids if missing
    if cid not in (founder.get("copropriete_ids") or []):
        await db.owners.update_one(
            {"id": founder_owner_id},
            {"$addToSet": {"copropriete_ids": cid}},
        )

    print(f"\n--- Applied ---")
    print(f"Case A applique : {applied['a']}")
    print(f"Case B applique : {applied['b']}")
    print(f"Case C applique : {applied['c']}")
    print(f"Erreurs         : {len(applied['errors'])}")
    if applied["errors"]:
        for e in applied["errors"]:
            print(f"  - Lot {e['lot']}: {e['err']}")

    print("\n=== Termine. Verifiez avec l'audit ownership dans l'UI. ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iter90cp - Reparation ownership retroactive ACP Acacia")
    parser.add_argument("--commit", action="store_true", help="Applique les modifications (par defaut dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Rapport sans modifications (defaut)")
    parser.add_argument("--copropriete-id", type=str, default=None, help="ID ACP explicite")
    parser.add_argument("--name", type=str, default="acacia", help="Nom ACP (regex, defaut 'acacia')")
    parser.add_argument("--founder-owner-id", type=str, default=None, help="ID Matexi (defaut auto-detect)")
    parser.add_argument("--founder-start-date", type=str, default=None, help="Date debut ISO (defaut 1ere FY)")
    parser.add_argument("--no-fix-orphans", action="store_true", help="Ne pas reparer les orphelins (case C)")
    args = parser.parse_args()

    dry_run = not args.commit  # commit override dry-run
    asyncio.run(_run(
        dry_run=dry_run,
        copropriete_id=args.copropriete_id,
        name=args.name,
        founder_owner_id=args.founder_owner_id,
        founder_start_date=args.founder_start_date,
        fix_orphans=not args.no_fix_orphans,
    ))
