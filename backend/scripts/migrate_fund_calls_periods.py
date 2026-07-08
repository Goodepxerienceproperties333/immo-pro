"""Migration : ajout des champs period_start / period_end aux fund_calls existants.

Pour chaque fund_call sans period_start / period_end, on calcule la periode
COUVERTE (vs la fenetre d'emission [date, due_date] qui est trop courte) :

1. Si name contient "X/N" (ex. "Trimestriel 1/4") -> deduit N appels par an
   et calcule period = [date, date + 12/N mois - 1 jour], borne a fy_end.
2. Sinon (appel annuel sans X/N) -> period = [date, fy_end].
3. Si pas de fiscal_year_id -> period = [date, date + 90j] (fallback).

Idempotent : skip si period_start ET period_end existent deja.

Usage :
    python /app/backend/scripts/migrate_fund_calls_periods.py [--dry-run]
"""
import os
import re
import sys
import asyncio
from datetime import datetime, timedelta
from calendar import monthrange

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient


def _compute_period(call: dict, fy_by_id: dict) -> tuple[str, str]:
    """Return (period_start, period_end) as ISO strings. None if cannot compute.

    iter90cg : pour un nom "Trimestriel X/N - <FY>" et une FY connue, calcule
    la periode CHRONOLOGIQUE reelle (X-eme intervalle de la FY) plutot que
    d'utiliser call.date comme point de depart. Cela corrige le cas ou le
    syndic cree un appel Q3 physiquement en retard (call.date = 20/10 mais
    Q3 = 01/07-30/09).
    """
    try:
        start_dt = datetime.strptime(call.get("date", ""), "%Y-%m-%d").date()
    except Exception:
        return None, None
    fy = fy_by_id.get(call.get("fiscal_year_id", ""))
    fy_start_dt = None
    fy_end_dt = None
    if fy:
        try:
            fy_start_dt = datetime.strptime(fy["start_date"], "%Y-%m-%d").date()
        except Exception:
            pass
        try:
            fy_end_dt = datetime.strptime(fy["end_date"], "%Y-%m-%d").date()
        except Exception:
            pass

    n_calls = None
    call_num = None
    m = re.search(r"(\d+)\s*/\s*(\d+)", call.get("name", "") or "")
    if m:
        try:
            call_num = int(m.group(1))
            n_calls = int(m.group(2))
            if n_calls not in (1, 2, 3, 4, 6, 12):
                n_calls = None
                call_num = None
            elif call_num < 1 or call_num > n_calls:
                call_num = None
        except ValueError:
            n_calls = None
            call_num = None

    period_start_dt = start_dt
    if n_calls and call_num and fy_start_dt:
        # iter90cg : periode chronologique X/N derivee de la FY.
        # Ex : Trimestriel 3/4 FY 2025 (01/01-31/12) -> period_start = 01/07/2025.
        interval = 12 // n_calls
        year = fy_start_dt.year
        month = fy_start_dt.month + (call_num - 1) * interval
        while month > 12:
            month -= 12
            year += 1
        try:
            period_start_dt = fy_start_dt.replace(year=year, month=month)
        except ValueError:
            last_day = monthrange(year, month)[1]
            period_start_dt = fy_start_dt.replace(
                year=year, month=month, day=min(fy_start_dt.day, last_day),
            )

    if n_calls and n_calls > 0:
        interval = 12 // n_calls
        year = period_start_dt.year
        month = period_start_dt.month + interval
        while month > 12:
            month -= 12
            year += 1
        try:
            next_start = period_start_dt.replace(year=year, month=month)
        except ValueError:
            last_day = monthrange(year, month)[1]
            next_start = period_start_dt.replace(year=year, month=month, day=min(period_start_dt.day, last_day))
        end_dt = next_start - timedelta(days=1)
    elif fy_end_dt:
        # Appel sans "X/N" -> annuel -> period = [start, fy_end]
        end_dt = fy_end_dt
    else:
        end_dt = period_start_dt + timedelta(days=90)

    # Borne max fy_end
    if fy_end_dt and end_dt > fy_end_dt:
        end_dt = fy_end_dt

    return period_start_dt.isoformat(), end_dt.isoformat()


async def run(dry_run: bool = False):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    fy_by_id = {}
    async for fy in db.fiscal_years.find({}, {"_id": 0}):
        fy_by_id[fy["id"]] = fy

    cur = db.fund_calls.find({}, {"_id": 0})
    total = 0
    updated = 0
    skipped = 0
    samples = []
    async for c in cur:
        total += 1
        if c.get("period_start") and c.get("period_end"):
            skipped += 1
            continue
        ps, pe = _compute_period(c, fy_by_id)
        if not ps or not pe:
            continue
        if len(samples) < 6:
            samples.append({
                "id": c["id"][:8],
                "name": c.get("name", "")[:45],
                "date": c.get("date"),
                "due_date": c.get("due_date"),
                "computed_period": f"[{ps}, {pe}]",
            })
        if not dry_run:
            await db.fund_calls.update_one(
                {"id": c["id"]},
                {"$set": {"period_start": ps, "period_end": pe}},
            )
        updated += 1

    print(f"\n{'DRY-RUN ' if dry_run else ''}Migration periodes fund_calls :")
    print(f"  Total fund_calls : {total}")
    print(f"  Skipped (deja migres) : {skipped}")
    print(f"  Updated : {updated}")
    print("\nExemples de periodes calculees :")
    for s in samples:
        print(f"  {s['id']}  {s['name']:45s}  date={s['date']}  due={s['due_date']}  ->  {s['computed_period']}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry))
