"""
iter90cg : Script de reparation retroactive pour les appels emis en RETARD
apres une mutation posterieure a la periode reelle.

Contexte : `_compute_period` (migrate_fund_calls_periods) utilisait
`call.date` comme `period_start`. Si le syndic a cree un appel Q3 physiquement
en retard (call.date = 20/10 apres mutation 01/10), la periode calculee etait
[20/10, 31/12] au lieu de [01/07, 30/09] -> owner_id incorrect (nouvel
acheteur au lieu du vendeur).

Le fix iter90cg :
- Corrige `_compute_period` pour parser le numerateur X dans "X/N".
- Recalcule les periodes des appels existants.
- Reconstruit distribution.owner_id via rebind sur `min(call_date, period_end)`.
- Regenere la VE (delete + insert).

Usage :
  # Dry-run (aucune modification) :
  python /app/backend/scripts/repair_iter90cg_late_calls.py --dry-run

  # Applique reellement les corrections :
  python /app/backend/scripts/repair_iter90cg_late_calls.py --apply

  # Scope a une ACP :
  python /app/backend/scripts/repair_iter90cg_late_calls.py --dry-run --acp-id=<uuid>
"""
import argparse
import asyncio
import os
import sys
from datetime import date as _date_cls

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

from migrate_fund_calls_periods import _compute_period  # noqa: E402


def _effective_date(call_date_iso: str, period_end_iso: str) -> str:
    if not period_end_iso or not call_date_iso:
        return call_date_iso
    return period_end_iso if period_end_iso < call_date_iso else call_date_iso


async def _rebind_call(db, call: dict, dry_run: bool) -> dict | None:
    """Recalcule period et rebind owner_id. Retourne les diffs ou None si aucun changement."""
    copro_id = call.get("copropriete_id", "")
    if not copro_id:
        return None
    # Recalcule la periode via la formule iter90cg
    fy = None
    if call.get("fiscal_year_id"):
        fy = await db.fiscal_years.find_one({"id": call["fiscal_year_id"]}, {"_id": 0})
    fy_by_id = {fy["id"]: fy} if fy else {}
    new_ps, new_pe = _compute_period(call, fy_by_id)
    if not new_ps or not new_pe:
        return None
    old_ps = call.get("period_start", "")
    old_pe = call.get("period_end", "")

    # Charge les mutations de l'ACP
    muts_all = await db.mutations.find(
        {"copropriete_id": copro_id}, {"_id": 0}
    ).to_list(10000)
    muts_by_lot: dict = {}
    for m in muts_all:
        lid = m.get("lot_id")
        if lid and m.get("sale_date") and m.get("from_owner_id") and m.get("to_owner_id"):
            muts_by_lot.setdefault(lid, []).append(m)
    for lid in muts_by_lot:
        muts_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

    call_date = call.get("date", "")
    effective = _effective_date(call_date, new_pe)
    try:
        target = _date_cls.fromisoformat(effective)
    except Exception:
        return None

    # Charge lots + owners
    lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10000)
    lots_by_id = {lt["id"]: lt for lt in lots}
    owners = await db.owners.find({}, {"_id": 0}).to_list(10000)
    owners_map = {o["id"]: o for o in owners}

    changes = []
    new_distribution = []
    for entry in (call.get("distribution") or []):
        lid = entry.get("lot_id") or ""
        muts = muts_by_lot.get(lid, [])
        if not muts:
            new_distribution.append(entry)
            continue
        fallback = entry.get("owner_id") or (lots_by_id.get(lid, {}) or {}).get("owner_id", "")
        current = muts[0].get("from_owner_id") or fallback
        for m in muts:
            try:
                sd = _date_cls.fromisoformat(m.get("sale_date") or "")
            except Exception:
                continue
            if sd <= target:
                current = m.get("to_owner_id") or current
            else:
                break
        if current and current != entry.get("owner_id"):
            own = owners_map.get(current) or {}
            changes.append({
                "lot": entry.get("lot_number", "?"),
                "old_owner": entry.get("owner_name", "?"),
                "new_owner": own.get("name", "?"),
                "amount": entry.get("amount", 0),
            })
            new_distribution.append({
                **entry,
                "owner_id": current,
                "owner_name": own.get("name", ""),
                "vcs_code": own.get("vcs_code", ""),
            })
        else:
            new_distribution.append(entry)

    period_changed = (new_ps != old_ps or new_pe != old_pe)
    if not changes and not period_changed:
        return None

    result = {
        "call_id": call.get("id"),
        "name": call.get("name", ""),
        "date": call_date,
        "old_period": f"[{old_ps}, {old_pe}]",
        "new_period": f"[{new_ps}, {new_pe}]",
        "effective_date": effective,
        "changes": changes,
    }

    if not dry_run and (changes or period_changed):
        # Verifie que l'appel n'a aucune ligne payee (protection historique)
        any_paid = any(d.get("paid") for d in new_distribution)
        if any_paid:
            result["skipped"] = "some rows paid - historical data preserved"
            return result

        await db.fund_calls.update_one(
            {"id": call["id"]},
            {"$set": {
                "period_start": new_ps,
                "period_end": new_pe,
                "distribution": new_distribution,
            }},
        )
        # Regenere la VE
        try:
            sys.path.insert(0, "/app/backend")
            from auto_entries import _delete_auto_entries, generate_sale_entry  # noqa: E402
            await _delete_auto_entries(db, "fund_call", call["id"])
            fresh = await db.fund_calls.find_one({"id": call["id"]}, {"_id": 0})
            if fresh:
                await generate_sale_entry(db, fresh)
                result["ve_regenerated"] = True
        except Exception as e:
            result["ve_regen_error"] = str(e)

    return result


async def run(dry_run: bool = False, acp_id: str | None = None):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    q = {}
    if acp_id:
        q["copropriete_id"] = acp_id
    calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", 1).to_list(20000)

    print(f"\n{'DRY-RUN ' if dry_run else 'APPLY '}iter90cg repair : {len(calls)} appels a analyser")
    if acp_id:
        print(f"  Scope ACP : {acp_id}")

    fixed = []
    for c in calls:
        try:
            r = await _rebind_call(db, c, dry_run)
        except Exception as e:
            print(f"  [error] {c.get('name','?')} ({c.get('id','?')[:8]}): {e}")
            continue
        if r:
            fixed.append(r)

    print(f"\nAppels corrigés : {len(fixed)}")
    for r in fixed[:50]:
        print(f"\n  {r['name']} (date={r['date']}, id={r['call_id'][:8]})")
        print(f"    Periode : {r['old_period']} -> {r['new_period']}")
        print(f"    Date effective rebind : {r['effective_date']}")
        for ch in r["changes"]:
            print(f"    Lot {ch['lot']} : {ch['old_owner']} -> {ch['new_owner']} ({ch['amount']:.2f} EUR)")
        if r.get("skipped"):
            print(f"    SKIPPED : {r['skipped']}")
        if r.get("ve_regenerated"):
            print("    VE regeneree")
        if r.get("ve_regen_error"):
            print(f"    VE ERROR : {r['ve_regen_error']}")

    if len(fixed) > 50:
        print(f"\n  ... et {len(fixed) - 50} autres appels")

    if dry_run:
        print("\n[DRY-RUN] Aucune modification appliquee. Utilisez --apply pour executer.")
    else:
        print(f"\n[APPLY] {len(fixed)} appels mis a jour + VE regenerees.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", default=False)
    p.add_argument("--apply", action="store_true", default=False)
    p.add_argument("--acp-id", default=None)
    args = p.parse_args()
    if not args.dry_run and not args.apply:
        print("Specifiez --dry-run ou --apply")
        sys.exit(1)
    asyncio.run(run(dry_run=args.dry_run, acp_id=args.acp_id))
