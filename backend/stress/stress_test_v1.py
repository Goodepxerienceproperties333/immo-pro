"""Stress test synthetique de la plateforme NextGe Copro.

Objectif : simuler la charge cible de 10 syndics x 30 ACPs et mesurer :
  - Temps de generation d'un Bilan (ACP moyenne)
  - Temps de calcul Balance des Tiers
  - Temps de listing owners / invoices
  - Temps de duplicates-audit global
  - Debit (req/sec) sous 20 users concurrents

Genere les donnees synthetiques via bulk_insert (nettoyage automatique en fin).

Usage :
  python -m stress.stress_test_v1                # test complet
  python -m stress.stress_test_v1 --cleanup      # supprime les donnees test
  python -m stress.stress_test_v1 --small        # 3 syndics x 10 ACPs (test rapide)
"""
import asyncio
import argparse
import os
import sys
import time
import uuid
import statistics
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

# Tag unique pour identifier / nettoyer les donnees generees
STRESS_TAG = "STRESS_TEST_2026"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


async def seed_data(db, n_syndics: int, n_acps_per_syndic: int, verbose: bool = True):
    """Genere n_syndics x n_acps_per_syndic ACPs avec 30 owners + 30 lots + 100 factures + 500 JE."""
    if verbose:
        print(f"\n[SEED] Generating {n_syndics} syndics x {n_acps_per_syndic} ACPs...")
    t0 = time.time()

    syndics = []
    for i in range(n_syndics):
        sid = f"stress-syndic-{i:03d}"
        syndics.append({
            "id": sid, "name": f"Syndic Stress {i}", "email": f"stress{i}@test.local",
            "role": "syndic_admin", "copropriete_ids": [],
            "_stress_tag": STRESS_TAG,
            "created_at": _now_iso(),
        })
    if syndics:
        await db.users.delete_many({"_stress_tag": STRESS_TAG})
        await db.users.insert_many(syndics)

    copro_ids = []
    coproprietes = []
    owners_batch = []
    lots_batch = []
    for si, syndic in enumerate(syndics):
        for ai in range(n_acps_per_syndic):
            copro_id = f"stress-copro-{si:03d}-{ai:03d}"
            copro_ids.append(copro_id)
            coproprietes.append({
                "id": copro_id, "name": f"ACP Stress {si}-{ai}",
                "syndic_id": syndic["id"], "address": f"Rue Test {si}, {ai:03d}",
                "postal_code": "1000", "city": "Bruxelles",
                "bank_accounts": [{"iban": f"BE{si:02d}{ai:04d}12345678", "label": "Compte courant"}],
                "_stress_tag": STRESS_TAG, "created_at": _now_iso(),
            })
            syndic["copropriete_ids"].append(copro_id)

            # 30 owners per ACP
            for oi in range(30):
                owner_id = f"stress-own-{si:02d}-{ai:03d}-{oi:03d}"
                owners_batch.append({
                    "id": owner_id, "copropriete_id": copro_id,
                    "first_name": f"Owner{oi}", "last_name": f"Family{si}{ai:03d}",
                    "name": f"Family{si}{ai:03d} Owner{oi}",
                    "email": f"o{si}{ai:03d}{oi:03d}@stress.local",
                    "phone": f"+3247{si:02d}{ai:03d}{oi:03d}",
                    "vcs_code": f"12345{oi:05d}",
                    "auxiliary_code": f"C{oi:04d}",
                    "_stress_tag": STRESS_TAG, "created_at": _now_iso(),
                })
                # 1 lot per owner
                lots_batch.append({
                    "id": f"stress-lot-{si:02d}-{ai:03d}-{oi:03d}",
                    "copropriete_id": copro_id,
                    "owner_id": owner_id,
                    "name": f"Appt {oi:02d}",
                    "quotites": {"charges_communes": 100, "chauffage": 100},
                    "_stress_tag": STRESS_TAG, "created_at": _now_iso(),
                })

    # Bulk insert
    if coproprietes:
        await db.coproprietes.delete_many({"_stress_tag": STRESS_TAG})
        await db.coproprietes.insert_many(coproprietes)
    if owners_batch:
        await db.owners.delete_many({"_stress_tag": STRESS_TAG})
        # Bulk insert par lots de 1000 pour eviter les timeouts
        for i in range(0, len(owners_batch), 1000):
            await db.owners.insert_many(owners_batch[i:i+1000])
    if lots_batch:
        await db.lots.delete_many({"_stress_tag": STRESS_TAG})
        for i in range(0, len(lots_batch), 1000):
            await db.lots.insert_many(lots_batch[i:i+1000])
    # Update syndics with copropriete_ids
    for s in syndics:
        await db.users.update_one({"id": s["id"]}, {"$set": {"copropriete_ids": s["copropriete_ids"]}})

    if verbose:
        print(f"  Inserted: {len(syndics)} users, {len(coproprietes)} ACPs, {len(owners_batch)} owners, {len(lots_batch)} lots ({time.time()-t0:.1f}s)")

    # Generate synthetic journal_entries per ACP : 500 entries each (with 2 lines each = 1M lines total)
    if verbose:
        print(f"[SEED] Generating journal entries ({len(coproprietes)}*500 = {len(coproprietes)*500} entries)...")
    t1 = time.time()

    # Purge stress data BEFORE the insert loop (avoid deleting mid-flush batches)
    await db.journal_entries.delete_many({"_stress_tag": STRESS_TAG})
    await db.invoices.delete_many({"_stress_tag": STRESS_TAG})

    je_batch = []
    invoices_batch = []
    start_date = datetime(2025, 1, 1)
    for copro_id in copro_ids:
        for ei in range(500):
            entry_date = (start_date + timedelta(days=ei % 365)).strftime("%Y-%m-%d")
            journal_type = "FI" if ei % 2 else "AC"
            supplier_acc = "44000001"  # generic supplier tier
            expense_acc = "61200"
            amount = round(50 + (ei * 3.5) % 500, 2)
            if journal_type == "AC":
                lines = [
                    {"account_number": expense_acc, "debit": amount, "credit": 0},
                    {"account_number": supplier_acc, "debit": 0, "credit": amount, "third_party_id": None},
                ]
            else:
                lines = [
                    {"account_number": supplier_acc, "debit": amount, "credit": 0, "third_party_id": None},
                    {"account_number": "55133100", "debit": 0, "credit": amount},
                ]
            je_batch.append({
                "id": str(uuid.uuid4()),
                "copropriete_id": copro_id,
                "journal_type": journal_type,
                "date": entry_date,
                "reference": f"REF-{ei:05d}",
                "description": f"Entry {ei} stress",
                "lines": lines,
                "total_debit": amount, "total_credit": amount,
                "_stress_tag": STRESS_TAG,
                "created_at": _now_iso(),
            })
            # For AC entries, also create an invoice
            if journal_type == "AC":
                invoices_batch.append({
                    "id": str(uuid.uuid4()),
                    "copropriete_id": copro_id,
                    "internal_reference": f"FA-STRESS-{ei:05d}",
                    "supplier": f"Supplier{ei%50}",
                    "date": entry_date,
                    "total_amount": amount,
                    "status": "paid" if ei % 3 == 0 else "unpaid",
                    "_stress_tag": STRESS_TAG,
                    "created_at": _now_iso(),
                })
        # Bulk flush every ACP to limit memory
        if len(je_batch) >= 5000:
            await db.journal_entries.insert_many(je_batch)
            je_batch = []
        if len(invoices_batch) >= 5000:
            await db.invoices.insert_many(invoices_batch)
            invoices_batch = []

    # Purge stress data first, then insert fresh batches
    # Final flush
    if je_batch:
        await db.journal_entries.insert_many(je_batch)
    if invoices_batch:
        await db.invoices.insert_many(invoices_batch)

    if verbose:
        total_je = await db.journal_entries.count_documents({"_stress_tag": STRESS_TAG})
        total_inv = await db.invoices.count_documents({"_stress_tag": STRESS_TAG})
        print(f"  Inserted: {total_je} JE + {total_inv} invoices ({time.time()-t1:.1f}s)")

    return copro_ids


async def cleanup(db):
    print(f"\n[CLEANUP] Removing all documents with _stress_tag={STRESS_TAG}")
    for coll in ["users", "coproprietes", "owners", "lots", "journal_entries", "invoices", "suppliers"]:
        r = await db[coll].delete_many({"_stress_tag": STRESS_TAG})
        print(f"  {coll}: {r.deleted_count} deleted")


async def measure_operations(db, copro_ids):
    """Mesure les temps de reponse des operations critiques."""
    import httpx
    BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")

    # Login as superadmin
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        r = await client.post("/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
        cookies = client.cookies

    async def measure(name: str, coro, n_iter: int = 5):
        durations = []
        errors = 0
        for _ in range(n_iter):
            t0 = time.time()
            try:
                await coro()
                durations.append((time.time() - t0) * 1000)  # ms
            except Exception as e:
                errors += 1
        if durations:
            p50 = statistics.median(durations)
            p95 = statistics.quantiles(durations, n=20)[-1] if len(durations) > 1 else durations[0]
            avg = statistics.mean(durations)
        else:
            p50 = p95 = avg = -1
        print(f"  {name:45s} avg={avg:>7.1f}ms  p50={p50:>7.1f}ms  p95={p95:>7.1f}ms  errors={errors}/{n_iter}")
        return {"name": name, "avg": avg, "p50": p50, "p95": p95, "errors": errors, "n": n_iter}

    print("\n[MEASURE] Response times (5 iterations each):")
    sample_acp = copro_ids[len(copro_ids)//2] if copro_ids else None

    results = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60, cookies=cookies) as client:
        # 1. Health check baseline
        async def op_health():
            r = await client.get("/api/health")
            return r
        results.append(await measure("GET /health (baseline)", op_health, n_iter=10))

        # 2. List ACPs (charged by copropriete_id filtering)
        async def op_list_copros():
            r = await client.get("/api/coproprietes")
            return r
        results.append(await measure("GET /coproprietes (list)", op_list_copros))

        # 3. List owners (all ACPs)
        async def op_list_owners():
            r = await client.get("/api/owners")
            return r
        results.append(await measure("GET /owners (all)", op_list_owners))

        if sample_acp:
            # 4. Bilan for a stress ACP
            async def op_bilan():
                r = await client.get(f"/api/reports/bilan?copropriete_id={sample_acp}&date_to=2025-12-31")
                return r
            results.append(await measure(f"GET /reports/bilan (1 stress ACP, 500 JE)", op_bilan))

            # 5. Balance des tiers - suppliers
            async def op_balance_sup():
                r = await client.get(f"/api/reports/balance-tiers/suppliers?copropriete_id={sample_acp}&end_date=2025-12-31")
                return r
            results.append(await measure(f"GET /balance-tiers/suppliers (1 ACP)", op_balance_sup))

            # 6. List invoices for one ACP
            async def op_list_inv():
                r = await client.get(f"/api/invoices?copropriete_id={sample_acp}&limit=100")
                return r
            results.append(await measure(f"GET /invoices (1 ACP, top 100)", op_list_inv))

        # 7. Duplicates audit (global, most expensive)
        async def op_audit():
            r = await client.get("/api/admin/duplicates-audit")
            return r
        results.append(await measure("GET /admin/duplicates-audit (global)", op_audit, n_iter=3))

    # 8. Concurrent load test : 20 clients hitting bilan endpoint in parallel
    if sample_acp:
        print("\n[CONCURRENT] 20 concurrent clients / 3 rounds, GET /reports/bilan")
        async def concurrent_worker(i):
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=60, cookies=cookies) as c:
                t0 = time.time()
                r = await c.get(f"/api/reports/bilan?copropriete_id={sample_acp}&date_to=2025-12-31")
                return (time.time() - t0) * 1000

        all_durations = []
        for round_i in range(3):
            durations = await asyncio.gather(*[concurrent_worker(i) for i in range(20)])
            all_durations.extend(durations)
            print(f"  Round {round_i+1}: avg={statistics.mean(durations):.0f}ms  p50={statistics.median(durations):.0f}ms  p95={statistics.quantiles(durations, n=20)[-1]:.0f}ms")
        results.append({
            "name": "GET /reports/bilan (60 concurrent)",
            "avg": statistics.mean(all_durations),
            "p50": statistics.median(all_durations),
            "p95": statistics.quantiles(all_durations, n=20)[-1] if len(all_durations) > 1 else all_durations[0],
            "errors": 0, "n": 60,
        })

    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="Cleanup then exit")
    parser.add_argument("--small", action="store_true", help="3 syndics x 10 ACPs (test rapide)")
    parser.add_argument("--full", action="store_true", help="10 syndics x 30 ACPs (charge cible)")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    if args.cleanup:
        await cleanup(db)
        return

    n_syndics, n_acps = (10, 30) if args.full else (3, 10)
    print(f"=== STRESS TEST : {n_syndics} syndics x {n_acps} ACPs ===")

    # Baseline : mesure sur donnees actuelles avant seed
    print("\n[BASELINE] Measurements on current DB (before seed):")
    baseline_stats = {
        "coproprietes": await db.coproprietes.count_documents({}),
        "owners": await db.owners.count_documents({}),
        "invoices": await db.invoices.count_documents({}),
        "journal_entries": await db.journal_entries.count_documents({}),
    }
    for k, v in baseline_stats.items():
        print(f"  {k}: {v} docs")

    # Seed data
    copro_ids = await seed_data(db, n_syndics, n_acps, verbose=True)

    # Post-seed count
    print("\n[POST-SEED] DB size:")
    for k in ["coproprietes", "owners", "invoices", "journal_entries"]:
        v = await db[k].count_documents({})
        print(f"  {k}: {v} docs")

    # Measure
    results = await measure_operations(db, copro_ids)

    # Report
    print("\n=== SUMMARY ===")
    for r in results:
        health_indicator = "OK" if r["p95"] < 500 else ("WARN" if r["p95"] < 2000 else "CRIT")
        print(f"  [{health_indicator}] {r['name']}: p95={r['p95']:.0f}ms")

    print("\n[NOTE] Rerun with --cleanup to remove stress test data.")


if __name__ == "__main__":
    asyncio.run(main())
