"""
iter90cf : Script de reparation retroactive des OD MUT-P manquantes.

Contexte : Historiquement, les mutations etaient enregistrees dans
`lot.mutations` array mais pas dans `db.mutations` collection. Les appels
generes APRES une mutation n'ont donc jamais eu leur OD "Mutation - Prorata"
creee, laissant des ecarts dans les balances de tiers.

Usage :
  # Dry-run (ne modifie rien, affiche le rapport) :
  python -m scripts.repair_post_mutation_prorata --dry-run

  # Applique reellement les corrections :
  python -m scripts.repair_post_mutation_prorata --apply

  # Scope a une ACP specifique :
  python -m scripts.repair_post_mutation_prorata --dry-run --acp-id=<uuid>

Fonctionnement :
1. Sync db.mutations depuis lot.mutations (idempotent).
2. Pour chaque appel de provisions de chaque ACP :
   - Verifie si sa periode chevauche une ou plusieurs mutations
   - Si oui, calcule les prorata attendus et compare aux OD existantes
   - Genere les OD manquantes (uniquement en mode --apply)
"""
import argparse
import asyncio
import os
import sys
from datetime import date as _date_cls

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


async def _sync_mutations(db) -> int:
    """Copie lot.mutations -> db.mutations (idempotent)."""
    synced = 0
    async for lot_doc in db.lots.find(
        {"mutations": {"$exists": True, "$ne": []}},
        {"_id": 0, "id": 1, "copropriete_id": 1, "mutations": 1},
    ):
        for mr in (lot_doc.get("mutations") or []):
            if not mr.get("id") or not mr.get("date"):
                continue
            doc = {
                "id": mr["id"],
                "copropriete_id": lot_doc.get("copropriete_id", ""),
                "lot_id": lot_doc["id"],
                "from_owner_id": mr.get("old_owner_id", ""),
                "to_owner_id": mr.get("new_owner_id", ""),
                "sale_date": mr.get("date", ""),
                "roulement_quota": mr.get("roulement_quota", 0.0),
                "current_period_prorata": mr.get(
                    "current_period_prorata", mr.get("prorata_provisions", 0.0)
                ),
                "total_transfer": mr.get("total_transfer", 0.0),
                "journal_entry_ids": mr.get("journal_entry_ids") or (
                    [mr.get("journal_entry_id")] if mr.get("journal_entry_id") else []
                ),
                "created_at": mr.get("created_at", ""),
            }
            await db.mutations.update_one({"id": mr["id"]}, {"$set": doc}, upsert=True)
            synced += 1
    return synced


async def _detect_missing_ods(db, acp_id_filter: str | None):
    """Retourne la liste des OD MUT-P manquantes.

    Chaque item : {acp_id, call_id, call_name, call_date, lot_id, lot_number,
                   from_owner_id, from_owner_name, to_owner_id, to_owner_name,
                   amount, days, total_days, existing_reference_expected}
    """
    q_calls = {"call_type": "provisions"}
    if acp_id_filter:
        q_calls["copropriete_id"] = acp_id_filter

    calls = await db.fund_calls.find(q_calls, {"_id": 0}).to_list(100000)
    missing = []

    for call in calls:
        period_start_iso = call.get("period_start")
        period_end_iso = call.get("period_end")
        if not period_start_iso or not period_end_iso:
            continue
        try:
            p_start = _date_cls.fromisoformat(period_start_iso)
            p_end = _date_cls.fromisoformat(period_end_iso)
        except Exception:
            continue
        if p_end <= p_start:
            continue
        total_days = (p_end - p_start).days + 1
        cid = call.get("copropriete_id", "")

        # Charge les mutations pour les lots de la distribution
        lot_ids = [e.get("lot_id") for e in (call.get("distribution") or []) if e.get("lot_id")]
        if not lot_ids:
            continue
        muts = await db.mutations.find(
            {"copropriete_id": cid, "lot_id": {"$in": lot_ids}}, {"_id": 0}
        ).to_list(10000)
        muts_by_lot: dict = {}
        for m in muts:
            muts_by_lot.setdefault(m["lot_id"], []).append(m)
        for lid in muts_by_lot:
            muts_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

        for entry in (call.get("distribution") or []):
            lot_id = entry.get("lot_id") or ""
            base_amount = float(entry.get("amount", 0) or 0)
            if base_amount <= 0.01 or not lot_id:
                continue
            lot_muts = muts_by_lot.get(lot_id, [])
            if not lot_muts:
                continue

            # Segments dans la periode
            in_period = []
            for m in lot_muts:
                try:
                    sd = _date_cls.fromisoformat(m["sale_date"])
                except Exception:
                    continue
                if p_start < sd <= p_end:
                    in_period.append((sd, m))
            if not in_period:
                continue

            first_mut = in_period[0][1]
            current_owner = first_mut.get("from_owner_id")
            segments: list = []
            cursor = p_start
            for sd, m in in_period:
                if sd <= cursor:
                    current_owner = m.get("to_owner_id") or current_owner
                    continue
                days_before = (sd - cursor).days
                if days_before > 0 and current_owner:
                    segments.append((current_owner, days_before))
                cursor = sd
                current_owner = m.get("to_owner_id")
            final_days = (p_end - cursor).days + 1
            if final_days > 0 and current_owner:
                segments.append((current_owner, final_days))

            if not segments:
                continue

            dist_owner_id = entry.get("owner_id") or ""
            transfers: dict = {}
            for seg_owner, seg_days in segments:
                if seg_owner == dist_owner_id:
                    continue
                seg_amount = round(base_amount * seg_days / total_days, 2)
                if seg_amount < 0.01:
                    continue
                key = (dist_owner_id, seg_owner)
                if key not in transfers:
                    transfers[key] = {"amount": 0.0, "days": 0}
                transfers[key]["amount"] += seg_amount
                transfers[key]["days"] += seg_days

            # Verifie idempotence : OD deja existante par reference ?
            for (from_owner, to_owner), tr in transfers.items():
                amount = round(tr["amount"], 2)
                if amount < 0.01:
                    continue
                ref = f"MUTP-POST-{entry.get('lot_number','')[:12]}-{call['id'][:8]}-{to_owner[:6]}"
                existing = await db.journal_entries.find_one({
                    "copropriete_id": cid, "reference": ref,
                }, {"_id": 0, "reversed": 1, "is_reversal": 1})
                if existing and not existing.get("reversed"):
                    continue  # deja creee et pas contre-passee
                # OD manquante
                missing.append({
                    "acp_id": cid,
                    "call_id": call["id"],
                    "call_name": call.get("name", ""),
                    "call_date": call.get("date", ""),
                    "period_start": period_start_iso,
                    "period_end": period_end_iso,
                    "lot_id": lot_id,
                    "lot_number": entry.get("lot_number", ""),
                    "from_owner_id": from_owner,
                    "to_owner_id": to_owner,
                    "amount": amount,
                    "days": tr["days"],
                    "total_days": total_days,
                    "expected_ref": ref,
                })
    return missing


async def _apply_fixes(db, missing: list) -> dict:
    """Genere reellement les OD manquantes via le helper du router."""
    # Import lazy
    sys.path.insert(0, "/app/backend")
    from routes.fund_calls import generate_prorata_mut_ods_for_call

    stats = {"processed_calls": 0, "created": 0, "skipped": 0, "errors": []}
    # Regrouper par call_id
    call_ids = sorted({m["call_id"] for m in missing})
    for cid in call_ids:
        call = await db.fund_calls.find_one({"id": cid}, {"_id": 0})
        if not call:
            stats["errors"].append(f"Appel {cid} introuvable")
            continue
        stats["processed_calls"] += 1
        try:
            res = await generate_prorata_mut_ods_for_call(db, call)
            stats["created"] += res.get("created", 0)
            stats["skipped"] += res.get("skipped", 0)
        except Exception as e:
            stats["errors"].append(f"Appel {cid}: {e}")
    return stats


async def _load_names(db, missing: list):
    """Enrichit missing avec owner_name / acp_name pour l'affichage."""
    owner_ids = {m["from_owner_id"] for m in missing} | {m["to_owner_id"] for m in missing}
    acp_ids = {m["acp_id"] for m in missing}
    owners = await db.owners.find(
        {"id": {"$in": list(owner_ids)}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(10000)
    acps = await db.coproprietes.find(
        {"id": {"$in": list(acp_ids)}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(1000)
    o_by_id = {o["id"]: o["name"] for o in owners}
    a_by_id = {a["id"]: a["name"] for a in acps}
    for m in missing:
        m["from_owner_name"] = o_by_id.get(m["from_owner_id"], "?")
        m["to_owner_name"] = o_by_id.get(m["to_owner_id"], "?")
        m["acp_name"] = a_by_id.get(m["acp_id"], "?")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Affiche seulement (defaut)")
    parser.add_argument("--apply", action="store_true", help="Applique les corrections")
    parser.add_argument("--acp-id", type=str, default=None, help="Filtrer sur une ACP")
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        args.dry_run = True  # defaut safe

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print(f"\n{'='*70}")
    print("iter90cf : Reparation retroactive OD MUT-P manquantes")
    print(f"Mode : {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"ACP  : {args.acp_id or 'TOUTES'}")
    print(f"{'='*70}\n")

    # Etape 1 : Sync mutations
    print(">> Etape 1 : Sync db.mutations depuis lot.mutations")
    synced = await _sync_mutations(db)
    print(f"   {synced} mutations synchronisees (idempotent)\n")

    # Etape 2 : Detection
    print(">> Etape 2 : Detection des OD MUT-P manquantes")
    missing = await _detect_missing_ods(db, args.acp_id)
    await _load_names(db, missing)
    print(f"   {len(missing)} OD manquantes detectees\n")

    if not missing:
        print("Aucune correction necessaire, systeme coherent.")
        return

    # Rapport
    print(f"{'-'*70}")
    print(f"{'ACP':<20} {'Appel':<20} {'Lot':<8} {'De -> Vers':<40} {'Montant':>10} {'Jours':>8}")
    print(f"{'-'*70}")
    total_amount = 0.0
    for m in missing:
        line = (
            f"{m['acp_name'][:19]:<20} "
            f"{m['call_name'][:19]:<20} "
            f"{m['lot_number'][:7]:<8} "
            f"{(m['from_owner_name'] + ' -> ' + m['to_owner_name'])[:39]:<40} "
            f"{m['amount']:>9.2f}E "
            f"{m['days']:>4}/{m['total_days']:<3}"
        )
        print(line)
        total_amount += m["amount"]
    print(f"{'-'*70}")
    print(f"TOTAL A TRANSFERER : {total_amount:.2f} EUR sur {len(missing)} OD\n")

    # Etape 3 : Apply
    if args.apply:
        print(">> Etape 3 : APPLICATION")
        confirm = input("Confirmer l'application (tapez 'OUI') : ").strip()
        if confirm != "OUI":
            print("Annule.")
            return
        stats = await _apply_fixes(db, missing)
        print(f"\n{stats['created']} OD creees, {stats['skipped']} skipped, "
              f"{stats['processed_calls']} appels traites")
        if stats["errors"]:
            print("Erreurs :")
            for e in stats["errors"]:
                print(f"  - {e}")
    else:
        print(">> Mode DRY-RUN, aucune modification effectuee.")
        print("   Relancer avec --apply pour appliquer.")


if __name__ == "__main__":
    asyncio.run(main())
