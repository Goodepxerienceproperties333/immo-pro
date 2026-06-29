"""Regression test - iter84 - Regen appels apres mutation + regles fonds permanents.

Demande user :
  1) "Une fois qu'une mutation est faite il faut regenerer les ecritures
     d'appels pour les provisions pour charges sur toute la periode comptable"
  2) "Si un fonds de reserve est appele avant la vente l'acheteur ne doit pas
     le payer, donc si des appels sont generes par la suite, le fonds de
     reserve reste au vendeur, il n'y a pas d'appel pour les lots concernes"
  3) "Idem pour les appels-augmentation fonds de roulement, c'est le vendeur
     qui les a payes via l'appel et l'acheteur le rembourse dans le cadre de
     la mutation, il ne faut pas creer de double ecriture"

Comportement attendu apres mutation (date 15/06) :
  - PROVISIONS Q3, Q4 (futurs) : owner remplace par new_owner sur ce lot
  - RESERVE Q3 (futur) si reserve Q1 existait AVANT mutation : lot exclu
  - ROULEMENT Q3 (futur) si roulement Q1 existait AVANT mutation : lot exclu
  - Appels avec rows deja payees : preserves (pas de regression)
  - Ecritures comptables regenerees automatiquement

Tests :
  1. Provisions futures : owner migre new_owner
  2. Reserve future apres reserve pre-vente : lot exclu + total reduit
  3. Roulement future apres roulement pre-vente : lot exclu + total reduit
  4. Reserve future SANS reserve pre-vente : owner migre new_owner (buyer paye)
  5. Provisions passees : NON touchees (historique preserve)
  6. Appel avec row paye : skipped (preservation)
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
    cid = f"itr84m-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o_seller = f"os-{uuid.uuid4()}"
    o_buyer = f"ob-{uuid.uuid4()}"
    o_other = f"oo-{uuid.uuid4()}"
    lot_target = f"lt-{uuid.uuid4()}"
    lot_other = f"lo-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "MIGR84", "status": "active"})
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


async def _insert_call(db, ctx, name, date, call_type, lot_amounts: dict):
    """Cree un appel avec distribution {lot_id -> amount} (assume 1 owner/lot)."""
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
    await db.fund_calls.insert_one({
        "id": call_id, "name": name, "date": date, "due_date": date,
        "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
        "call_type": call_type, "total_amount": sum(lot_amounts.values()),
        "distribution": distribution,
    })
    return call_id


def _get_mutate_fn(db):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == "/api/lots/{lot_id}/mutate":
            return r.endpoint
    return None


async def _do_mutation(ctx, sale_date="2026-06-15"):
    mutate_fn = _get_mutate_fn(ctx["db"])
    LotMutationInput = mutate_fn.__annotations__.get("data")
    payload = LotMutationInput(
        new_owner_id=ctx["o_buyer"], sale_date=sale_date, sale_price=200000.0,
    )
    return await mutate_fn(lot_id=ctx["lot_target"], data=payload)


async def _test_provisions_future_migrate_owner():
    """Provisions Q1 (Jan, payee) + Q2 (Apr) + Q3 (Jul) + Q4 (Oct).
    Mutation 15/06. Q3 et Q4 doivent etre migres vers buyer.
    """
    ctx = await _setup()
    db = ctx["db"]
    try:
        # Provisions Q1 (avant mutation, PAYEE par seller)
        q1 = await _insert_call(db, ctx, "Prov Q1", "2026-01-15", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0})
        await db.fund_calls.update_one(
            {"id": q1},
            {"$set": {"distribution.$[el].paid": True, "distribution.$[el].paid_date": "2026-01-20"}},
            array_filters=[{"el.lot_id": ctx["lot_target"]}],
        )
        # Provisions Q2 (avant mutation, NON payee)
        q2 = await _insert_call(db, ctx, "Prov Q2", "2026-04-15", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0})
        # Provisions Q3 (futur, NON payee)
        q3 = await _insert_call(db, ctx, "Prov Q3", "2026-07-15", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0})
        # Provisions Q4 (futur)
        q4 = await _insert_call(db, ctx, "Prov Q4", "2026-10-15", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0})

        result = await _do_mutation(ctx)
        mut = result["mutation"]
        regen = mut.get("regenerated_calls") or {}

        # Q1 et Q2 : passes/payes/avant-vente -> non touches
        q1_after = await db.fund_calls.find_one({"id": q1}, {"_id": 0})
        assert next(d for d in q1_after["distribution"] if d["lot_id"] == ctx["lot_target"])["owner_id"] == ctx["o_seller"]
        q2_after = await db.fund_calls.find_one({"id": q2}, {"_id": 0})
        # Q2 est AVANT mutation, ne doit PAS etre migre
        assert next(d for d in q2_after["distribution"] if d["lot_id"] == ctx["lot_target"])["owner_id"] == ctx["o_seller"]

        # Q3, Q4 : owner migre vers buyer pour lot_target uniquement
        for call_id in (q3, q4):
            after = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
            target_row = next(d for d in after["distribution"] if d["lot_id"] == ctx["lot_target"])
            assert target_row["owner_id"] == ctx["o_buyer"], f"Q3/Q4 lot_target doit etre migre vers buyer"
            assert target_row["owner_name"] == "Acheteur"
            other_row = next(d for d in after["distribution"] if d["lot_id"] == ctx["lot_other"])
            assert other_row["owner_id"] == ctx["o_other"], "Autre lot doit rester inchange"

        # regen summary contient Q3 et Q4
        assert regen["fixed"] >= 2
        assert any(c["id"] == q3 for c in regen.get("migrated_owner", []))
        assert any(c["id"] == q4 for c in regen.get("migrated_owner", []))
        print("OK - Provisions Q3/Q4 migrees vers buyer")
    finally:
        await _cleanup(ctx)


async def _test_reserve_excluded_when_prior_call():
    """Reserve Q1 (avant vente) + Reserve Q3 (futur) -> Q3 exclut le lot."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        res1 = await _insert_call(db, ctx, "Reserve Q1", "2026-02-01", "reserve",
                                   {ctx["lot_target"]: 500.0, ctx["lot_other"]: 500.0})
        res3 = await _insert_call(db, ctx, "Reserve Q3", "2026-08-01", "reserve",
                                   {ctx["lot_target"]: 300.0, ctx["lot_other"]: 300.0})

        result = await _do_mutation(ctx)
        regen = result["mutation"].get("regenerated_calls") or {}

        # Reserve Q1 : avant vente, non touche
        res1_after = await db.fund_calls.find_one({"id": res1}, {"_id": 0})
        assert len(res1_after["distribution"]) == 2

        # Reserve Q3 : lot_target EXCLU
        res3_after = await db.fund_calls.find_one({"id": res3}, {"_id": 0})
        ids = [d["lot_id"] for d in res3_after["distribution"]]
        assert ctx["lot_target"] not in ids, "Lot vendu doit etre EXCLU de la reserve future"
        assert ctx["lot_other"] in ids
        # Total reduit de 300
        assert abs(res3_after["total_amount"] - 300.0) < 0.01

        assert regen["had_prior_reserve"] is True
        assert any(c["id"] == res3 and c["excluded_amount"] == 300.0
                   for c in regen.get("excluded_capital", []))
        print("OK - Reserve Q3 exclut lot_target (vendeur a deja paye reserve Q1)")
    finally:
        await _cleanup(ctx)


async def _test_reserve_migrated_when_no_prior():
    """Pas de reserve avant vente -> Reserve Q3 (futur) est migre owner (buyer paye)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        # AUCUN appel reserve avant vente
        res3 = await _insert_call(db, ctx, "Reserve Q3", "2026-08-01", "reserve",
                                   {ctx["lot_target"]: 300.0, ctx["lot_other"]: 300.0})

        await _do_mutation(ctx)

        res3_after = await db.fund_calls.find_one({"id": res3}, {"_id": 0})
        target_row = next(d for d in res3_after["distribution"] if d["lot_id"] == ctx["lot_target"])
        assert target_row["owner_id"] == ctx["o_buyer"], "Sans reserve anterieure, lot doit etre migre buyer"
        assert len(res3_after["distribution"]) == 2  # Pas d'exclusion
        print("OK - Reserve Q3 migre buyer (pas de reserve anterieure)")
    finally:
        await _cleanup(ctx)


async def _test_roulement_excluded_when_prior_call():
    """Augmentation roulement Q1 (avant) + Q3 (futur) -> Q3 exclut le lot."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        r1 = await _insert_call(db, ctx, "Aug. Roulement Q1", "2026-02-01", "roulement",
                                 {ctx["lot_target"]: 1000.0, ctx["lot_other"]: 1000.0})
        r3 = await _insert_call(db, ctx, "Aug. Roulement Q3", "2026-08-01", "roulement",
                                 {ctx["lot_target"]: 500.0, ctx["lot_other"]: 500.0})

        result = await _do_mutation(ctx)
        regen = result["mutation"].get("regenerated_calls") or {}

        # R1 : avant vente, intact
        r1_after = await db.fund_calls.find_one({"id": r1}, {"_id": 0})
        assert len(r1_after["distribution"]) == 2

        # R3 : lot EXCLU (vendeur a paye R1 -> mutation gere via fonds_roulement)
        r3_after = await db.fund_calls.find_one({"id": r3}, {"_id": 0})
        ids = [d["lot_id"] for d in r3_after["distribution"]]
        assert ctx["lot_target"] not in ids
        assert abs(r3_after["total_amount"] - 500.0) < 0.01
        assert regen["had_prior_roulement"] is True
        print("OK - Augmentation roulement Q3 exclut lot (anti-double-ecriture)")
    finally:
        await _cleanup(ctx)


async def _test_paid_call_preserved():
    """Provisions Q3 avec row deja PAYEE doit etre preservee (skipped)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        q3 = await _insert_call(db, ctx, "Prov Q3 paid", "2026-07-15", "provisions",
                                 {ctx["lot_target"]: 100.0, ctx["lot_other"]: 100.0})
        # Paid pre-mutation (cas hypothetique : pre-paiement)
        await db.fund_calls.update_one(
            {"id": q3},
            {"$set": {"distribution.$[el].paid": True, "distribution.$[el].paid_date": "2026-07-01"}},
            array_filters=[{"el.lot_id": ctx["lot_target"]}],
        )
        result = await _do_mutation(ctx)
        regen = result["mutation"].get("regenerated_calls") or {}

        q3_after = await db.fund_calls.find_one({"id": q3}, {"_id": 0})
        target_row = next(d for d in q3_after["distribution"] if d["lot_id"] == ctx["lot_target"])
        # Doit RESTER seller car deja paye
        assert target_row["owner_id"] == ctx["o_seller"]
        assert target_row["paid"] is True
        # Et figurer dans skipped_paid
        assert any(c["id"] == q3 for c in regen.get("skipped_paid", []))
        print("OK - Provisions Q3 deja payee preservee (skipped)")
    finally:
        await _cleanup(ctx)


def test_iter84_provisions_future_migrate():
    asyncio.run(_test_provisions_future_migrate_owner())


def test_iter84_reserve_excluded_when_prior():
    asyncio.run(_test_reserve_excluded_when_prior_call())


def test_iter84_reserve_migrated_when_no_prior():
    asyncio.run(_test_reserve_migrated_when_no_prior())


def test_iter84_roulement_excluded_when_prior():
    asyncio.run(_test_roulement_excluded_when_prior_call())


def test_iter84_paid_call_preserved():
    asyncio.run(_test_paid_call_preserved())
