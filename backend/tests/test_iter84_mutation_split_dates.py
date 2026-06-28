"""Regression test - iter84 - Mutation : split des ecritures OD par date.

Demande user : "Lors des mutations garde les dates des appels dans les situations
de compte, seul le transfert du fond de roulement doit etre a la date de la vente,
le reste conserve la date des appels relatifs au budget."

Comportement attendu :
  - Fonds de roulement -> 1 ecriture OD datee `sale_date` (mutation du capital).
  - Prorata appel en cours -> 1 ecriture OD par DATE D'APPEL d'origine
    (agregee par date si plusieurs appels meme date).
  - Cancellation : doit supprimer TOUTES les ecritures liees a la mutation.

Tests :
  1. Mutation milieu Q1 -> 2 ecritures (sale_date + Q1 date)
  2. Mutation pas de roulement -> 1 ecriture (prorata uniquement, date appel)
  3. Mutation pas de prorata -> 1 ecriture (roulement, date sale)
  4. Cancellation supprime les 2 ecritures correctement
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_acp():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr84-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER84", "reference": "TEST-ITER84", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds de roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Vendeur V", "last_name": "Vendeur",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acquereur A", "last_name": "Acquereur",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
        "copropriete_id": cid, "quotity": 500.0,
    })
    await db.lots.insert_one({
        "id": f"ghost-{uuid.uuid4()}", "number": "B1",
        "copropriete_id": cid, "quotity": 500.0,
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1_id, "o2": o2_id, "lot_id": lot_id}


async def _insert_fr_balance(db, cid, amount=2000.0):
    """Cree le solde fonds de roulement = `amount` EUR (Cr)."""
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()),
        "journal_type": "OD",
        "date": "2026-01-01",
        "copropriete_id": cid,
        "lines": [
            {"account_number": "4100001", "debit": amount, "credit": 0.0},
            {"account_number": "100", "debit": 0.0, "credit": amount},
        ],
        "total_debit": amount, "total_credit": amount,
    })


async def _insert_q1_call(db, cid, fy_id, o1_id, call_date="2026-01-15"):
    fc_id = f"fc-{uuid.uuid4()}"
    await db.fund_calls.insert_one({
        "id": fc_id,
        "name": "Trimestriel 1/4 - 2026",
        "date": call_date,        # date originale de l'appel (utilisee par OD prorata)
        "due_date": call_date,
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "fiscal_year_id": fy_id,
        "copropriete_id": cid,
        "call_type": "provisions",
        "total_amount": 600.0,
        "distribution": [
            {"owner_id": o1_id, "owner_name": "Vendeur V",
             "share": 500, "amount": 600.0, "paid": False},
        ],
    })
    return fc_id


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == route_path:
            return r.endpoint
    return None


async def _test_split_dates():
    """Mutation milieu Q1 :
      - 1 OD fonds_roulement datee sale_date (1000 EUR au 15/02/2026)
      - 1 OD prorata datee Q1 call_date (300 EUR au 15/01/2026)
    """
    ctx = await _setup_acp()
    db = ctx["db"]
    try:
        await _insert_fr_balance(db, ctx["cid"], 2000.0)
        await _insert_q1_call(db, ctx["cid"], ctx["fy_id"], ctx["o1"], call_date="2026-01-15")

        mutate_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        payload = LotMutationInput(
            new_owner_id=ctx["o2"], sale_date="2026-02-15", sale_price=180000.0,
        )
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut = result["mutation"]

        # mutation_record contient journal_entry_ids (liste)
        ids = mut.get("journal_entry_ids") or []
        assert len(ids) == 2, f"Attendu 2 journal_entry_ids, recu {len(ids)}"

        # entries_created expose le breakdown
        entries = mut.get("entries_created") or []
        assert len(entries) == 2
        by_kind = {e["kind"]: e for e in entries}
        assert "fonds_roulement" in by_kind and "prorata" in by_kind

        # Date fonds de roulement = sale_date
        assert by_kind["fonds_roulement"]["date"] == "2026-02-15", (
            f"FR doit etre datee sale_date, recu {by_kind['fonds_roulement']['date']}"
        )
        assert abs(by_kind["fonds_roulement"]["amount"] - 1000.0) < 0.01

        # Date prorata = call_date (et NON sale_date)
        assert by_kind["prorata"]["date"] == "2026-01-15", (
            f"Prorata doit etre datee call_date (2026-01-15), recu {by_kind['prorata']['date']}"
        )
        assert abs(by_kind["prorata"]["amount"] - 300.0) < 0.01

        # Verifie les ecritures persistees en DB
        jes = await db.journal_entries.find(
            {"source_id": ctx["lot_id"], "source_type": "lot_mutation"}, {"_id": 0}
        ).to_list(10)
        assert len(jes) == 2
        dates = sorted([j["date"] for j in jes])
        assert dates == ["2026-01-15", "2026-02-15"], f"Dates OD recues : {dates}"

        # Le sous-type est bien marque
        subtypes = sorted([j.get("source_subtype") for j in jes])
        assert subtypes == ["fonds_roulement", "prorata"]

        # current_period_details enrichi avec call_date
        details = mut.get("current_period_details") or []
        assert details and details[0].get("call_date") == "2026-01-15"
    finally:
        await _cleanup(ctx)


async def _test_split_only_roulement_when_no_prorata():
    """Vente AVANT le Q1 (31/12/2025) : pas de prorata, mais roulement.
    -> 1 seule ecriture OD fonds_roulement datee sale_date.
    """
    ctx = await _setup_acp()
    db = ctx["db"]
    try:
        await _insert_fr_balance(db, ctx["cid"], 2000.0)
        await _insert_q1_call(db, ctx["cid"], ctx["fy_id"], ctx["o1"], call_date="2026-01-15")

        mutate_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2025-12-31")
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut = result["mutation"]
        ids = mut.get("journal_entry_ids") or []
        assert len(ids) == 1, f"Attendu 1 seule OD (roulement), recu {len(ids)}"
        entries = mut.get("entries_created") or []
        assert entries[0]["kind"] == "fonds_roulement"
        assert entries[0]["date"] == "2025-12-31"
    finally:
        await _cleanup(ctx)


async def _test_split_only_prorata_when_no_roulement():
    """Solde fonds de roulement = 0 : seule l'ecriture prorata (date call_date) est creee."""
    ctx = await _setup_acp()
    db = ctx["db"]
    try:
        # PAS de fonds de roulement
        await _insert_q1_call(db, ctx["cid"], ctx["fy_id"], ctx["o1"], call_date="2026-01-15")

        mutate_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-02-15")
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut = result["mutation"]
        ids = mut.get("journal_entry_ids") or []
        assert len(ids) == 1, f"Attendu 1 seule OD (prorata), recu {len(ids)}"
        entries = mut.get("entries_created") or []
        assert entries[0]["kind"] == "prorata"
        assert entries[0]["date"] == "2026-01-15"
        assert abs(entries[0]["amount"] - 300.0) < 0.01
    finally:
        await _cleanup(ctx)


async def _test_cancel_deletes_all_entries():
    """L'annulation doit supprimer les 2 ecritures (FR + prorata)."""
    ctx = await _setup_acp()
    db = ctx["db"]
    try:
        await _insert_fr_balance(db, ctx["cid"], 2000.0)
        await _insert_q1_call(db, ctx["cid"], ctx["fy_id"], ctx["o1"], call_date="2026-01-15")

        mutate_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate")
        cancel_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate/{mutation_id}")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-02-15")
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut_id = result["mutation"]["id"]
        # Avant annulation : 2 ecritures
        n_before = await db.journal_entries.count_documents(
            {"source_id": ctx["lot_id"], "source_type": "lot_mutation"}
        )
        assert n_before == 2

        await cancel_fn(lot_id=ctx["lot_id"], mutation_id=mut_id)
        # Apres annulation : 0
        n_after = await db.journal_entries.count_documents(
            {"source_id": ctx["lot_id"], "source_type": "lot_mutation"}
        )
        assert n_after == 0, f"Toutes les OD doivent etre supprimees, restant {n_after}"

        # Owner restaure
        lt = await db.lots.find_one({"id": ctx["lot_id"]}, {"_id": 0})
        assert lt["owner_id"] == ctx["o1"]
    finally:
        await _cleanup(ctx)


def test_iter84_split_dates():
    asyncio.run(_test_split_dates())


def test_iter84_only_roulement_when_no_prorata():
    asyncio.run(_test_split_only_roulement_when_no_prorata())


def test_iter84_only_prorata_when_no_roulement():
    asyncio.run(_test_split_only_prorata_when_no_roulement())


def test_iter84_cancel_deletes_all_entries():
    asyncio.run(_test_cancel_deletes_all_entries())
