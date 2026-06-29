"""Regression test - iter85 - Nouvelle logique appels futurs apres mutation.

Demande user iter85 :
  La mutation NE MODIFIE PLUS le owner_id dans la distribution des appels
  futurs. Elle cree une ecriture OD (DR acheteur / CR vendeur) a la date de
  chaque appel futur, pour la quote-part du lot mute. Cela permet a la
  balance de tier de refleter la mutation a chaque date d'appel (01.01,
  01.04, 01.07, 01.10 etc.) peu importe la date de mutation.

Tests :
  1. Distribution des appels futurs INTACTE (owner_id reste vendeur)
  2. Une OD est creee a la date de chaque appel futur (DR acheteur / CR vendeur)
  3. regenerated_calls.fixed == 0 (logique neutralisee)
  4. Cancellation de la mutation supprime aussi les OD futures
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr85m-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o_seller = f"os-{uuid.uuid4()}"
    o_buyer = f"ob-{uuid.uuid4()}"
    o_other = f"oo-{uuid.uuid4()}"
    lot_target = f"lt-{uuid.uuid4()}"
    lot_other = f"lo-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "MIGR85", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Fonds reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "4100001", "name": "Seller", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Buyer", "class_num": 4, "copropriete_id": cid},
        {"number": "4100003", "name": "Other", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Provisions", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o_seller, "name": "Vendeur", "last_name": "Vendeur", "first_name": "V",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}, "vcs_code": "+++111/0000/00001+++"},
        {"id": o_buyer, "name": "Acheteur", "last_name": "Acheteur", "first_name": "A",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}, "vcs_code": "+++222/0000/00002+++"},
        {"id": o_other, "name": "Autre", "last_name": "Autre", "first_name": "O",
         "auxiliary_code": "C0003", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100003"}}, "vcs_code": "+++333/0000/00003+++"},
    ])
    await db.lots.insert_many([
        {"id": lot_target, "number": "A1", "owner_id": o_seller, "owner_ids": [o_seller],
         "copropriete_id": cid, "quotity": 500},
        {"id": lot_other, "number": "B1", "owner_id": o_other, "owner_ids": [o_other],
         "copropriete_id": cid, "quotity": 500},
    ])
    return {
        "db": db, "cid": cid, "fy_id": fy_id,
        "o_seller": o_seller, "o_buyer": o_buyer, "o_other": o_other,
        "lot_target": lot_target, "lot_other": lot_other,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"copropriete_ids": ctx["cid"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


async def _insert_call(db, ctx, name, date, call_type, lot_amounts: dict,
                        period_start=None, period_end=None):
    """Cree un appel avec distribution {lot_id -> amount}."""
    distribution = []
    lots = await db.lots.find({"id": {"$in": list(lot_amounts.keys())}}, {"_id": 0}).to_list(10)
    owner_by_lot = {lt["id"]: lt["owner_id"] for lt in lots}
    owners_by_id = {o["id"]: o for o in await db.owners.find({"id": {"$in": list(owner_by_lot.values())}}, {"_id": 0}).to_list(10)}
    for lot_id, amt in lot_amounts.items():
        oid = owner_by_lot.get(lot_id)
        ow = owners_by_id.get(oid, {})
        distribution.append({
            "lot_id": lot_id,
            "lot_number": next((lt["number"] for lt in lots if lt["id"] == lot_id), ""),
            "owner_id": oid,
            "owner_name": ow.get("name", ""),
            "vcs_code": ow.get("vcs_code", ""),
            "share": 500.0,
            "amount": amt,
            "paid": False,
            "paid_date": "",
        })
    call_id = f"c-{uuid.uuid4()}"
    doc = {
        "id": call_id, "name": name, "date": date, "due_date": date,
        "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
        "call_type": call_type, "total_amount": sum(lot_amounts.values()),
        "distribution": distribution,
    }
    if period_start:
        doc["period_start"] = period_start
    if period_end:
        doc["period_end"] = period_end
    await db.fund_calls.insert_one(doc)
    return call_id


def _get_mutate_fn(db):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == "/api/lots/{lot_id}/mutate":
            return r.endpoint
    return None


def _get_cancel_fn(db):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == "/api/lots/{lot_id}/mutate/{mutation_id}":
            return r.endpoint
    return None


async def _do_mutation(ctx, sale_date="2026-06-15"):
    mutate_fn = _get_mutate_fn(ctx["db"])
    LotMutationInput = mutate_fn.__annotations__.get("data")
    payload = LotMutationInput(
        new_owner_id=ctx["o_buyer"], sale_date=sale_date, sale_price=200000.0,
    )
    return await mutate_fn(lot_id=ctx["lot_target"], data=payload)


async def _test_distribution_not_modified():
    """Apres mutation, la distribution des appels FUTURS reste au vendeur."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        # Provisions trimestrielles : Q3 + Q4 sont futurs apres 15/06
        q3 = await _insert_call(db, ctx, "Prov Q3", "2026-07-01", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                                 period_start="2026-07-01", period_end="2026-09-30")
        q4 = await _insert_call(db, ctx, "Prov Q4", "2026-10-01", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                                 period_start="2026-10-01", period_end="2026-12-31")

        await _do_mutation(ctx)

        # iter85 : distribution Q3/Q4 du lot_target DOIT rester au vendeur
        for call_id in (q3, q4):
            after = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
            target_row = next(d for d in after["distribution"] if d["lot_id"] == ctx["lot_target"])
            assert target_row["owner_id"] == ctx["o_seller"], (
                f"iter85 : owner du lot dans Q3/Q4 doit RESTER vendeur. Recu : {target_row['owner_id']}"
            )
            # Distribution complete (pas de retrait du lot)
            assert len(after["distribution"]) == 2
        print("OK - iter85 : distribution des appels futurs intacte")
    finally:
        await _cleanup(ctx)


async def _test_one_od_per_future_call_date():
    """1 OD (DR acheteur / CR vendeur) creee a chaque date d'appel futur."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await _insert_call(db, ctx, "Prov Q3", "2026-07-01", "provisions",
                            {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                            period_start="2026-07-01", period_end="2026-09-30")
        await _insert_call(db, ctx, "Prov Q4", "2026-10-01", "provisions",
                            {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                            period_start="2026-10-01", period_end="2026-12-31")

        result = await _do_mutation(ctx)
        mut = result["mutation"]
        entries = mut.get("entries_created") or []

        # 2 OD future_call attendues (Q3 et Q4)
        future_ods = [e for e in entries if e["kind"] == "future_call"]
        assert len(future_ods) == 2, f"Attendu 2 OD future_call, recu {len(future_ods)}"
        dates = sorted([e["date"] for e in future_ods])
        assert dates == ["2026-07-01", "2026-10-01"]
        for e in future_ods:
            assert abs(e["amount"] - 100.0) < 0.01, f"Quote-part 100 attendue, recu {e['amount']}"

        # En DB, ces OD ont DR acheteur (4100002) / CR vendeur (4100001)
        jes = await db.journal_entries.find(
            {"source_id": ctx["lot_target"], "source_type": "lot_mutation",
             "source_subtype": "future_call"}, {"_id": 0}
        ).to_list(10)
        assert len(jes) == 2
        for je in jes:
            dl = next(ln for ln in je["lines"] if ln["debit"] > 0)
            cl = next(ln for ln in je["lines"] if ln["credit"] > 0)
            assert dl["account_number"] == "4100002" and dl["third_party_id"] == ctx["o_buyer"]
            assert cl["account_number"] == "4100001" and cl["third_party_id"] == ctx["o_seller"]
        print("OK - iter85 : OD futures aux dates correctes avec DR/CR corrects")
    finally:
        await _cleanup(ctx)


async def _test_regenerate_is_neutralized():
    """`regenerated_calls.fixed` doit etre 0 (logique neutralisee iter85)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await _insert_call(db, ctx, "Prov Q3", "2026-07-01", "provisions",
                            {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                            period_start="2026-07-01", period_end="2026-09-30")

        result = await _do_mutation(ctx)
        regen = result["mutation"].get("regenerated_calls") or {}
        assert regen.get("fixed") == 0, (
            f"iter85 : regenerated_calls.fixed doit etre 0, recu {regen.get('fixed')}"
        )
        print("OK - iter85 : regenerated_calls neutralise")
    finally:
        await _cleanup(ctx)


async def _test_cancel_deletes_future_ods():
    """Annulation de la mutation doit supprimer aussi les OD futures."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await _insert_call(db, ctx, "Prov Q3", "2026-07-01", "provisions",
                            {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                            period_start="2026-07-01", period_end="2026-09-30")
        await _insert_call(db, ctx, "Prov Q4", "2026-10-01", "provisions",
                            {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0},
                            period_start="2026-10-01", period_end="2026-12-31")

        result = await _do_mutation(ctx)
        mut_id = result["mutation"]["id"]
        n_before = await db.journal_entries.count_documents(
            {"source_id": ctx["lot_target"], "source_type": "lot_mutation"}
        )
        assert n_before == 2, f"Attendu 2 ODs futures, recu {n_before}"

        cancel_fn = _get_cancel_fn(db)
        await cancel_fn(lot_id=ctx["lot_target"], mutation_id=mut_id)

        n_after = await db.journal_entries.count_documents(
            {"source_id": ctx["lot_target"], "source_type": "lot_mutation"}
        )
        assert n_after == 0, f"Toutes les OD doivent etre supprimees, restant {n_after}"
        # Owner restaure
        lt = await db.lots.find_one({"id": ctx["lot_target"]}, {"_id": 0})
        assert lt["owner_id"] == ctx["o_seller"]
        print("OK - iter85 : cancel supprime toutes les OD (incl. futures)")
    finally:
        await _cleanup(ctx)


def test_iter85_distribution_not_modified():
    asyncio.run(_test_distribution_not_modified())


def test_iter85_one_od_per_future_call_date():
    asyncio.run(_test_one_od_per_future_call_date())


def test_iter85_regenerate_is_neutralized():
    asyncio.run(_test_regenerate_is_neutralized())


def test_iter85_cancel_deletes_future_ods():
    asyncio.run(_test_cancel_deletes_future_ods())
