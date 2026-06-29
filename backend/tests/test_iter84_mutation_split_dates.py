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
    """Vente AVANT le Q1 (31/12/2025) : pas de prorata appel en cours.
    iter85 : 2 ecritures attendues - FR datee sale_date + 1 OD future_call
    datee Q1 (l'appel Q1 devient un appel FUTUR car period_start > sale_date).
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
        # iter85 : FR + future_call Q1 (appel futur car period_start > sale_date)
        assert len(ids) == 2, f"Attendu 2 OD (roulement + future Q1), recu {len(ids)}"
        kinds = sorted([e["kind"] for e in (mut.get("entries_created") or [])])
        assert kinds == ["fonds_roulement", "future_call"]
        by_kind = {e["kind"]: e for e in mut.get("entries_created", [])}
        assert by_kind["fonds_roulement"]["date"] == "2025-12-31"
        assert by_kind["future_call"]["date"] == "2026-01-15"
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


async def _test_quarterly_mutation_5_ods():
    """Scenario iter85 : mutation le 15/02/2026, 4 appels trimestriels existants
    aux 01.01, 01.04, 01.07, 01.10. Quote-part du lot = 500 EUR par appel.

    Attendu : 5 ecritures OD :
      - 1 OD fonds_roulement datee sale_date (15/02/2026)
      - 1 OD prorata datee 01/01/2026 (Q1 contient sale_date)
      - 3 OD future_call datees 01/04, 01/07, 01/10 (Q2/Q3/Q4 futurs)
    La distribution des appels futurs reste au vendeur (volonte iter85).
    La balance de tier vendeur doit montrer 5 credits, acheteur 5 debits.
    """
    ctx = await _setup_acp()
    db = ctx["db"]
    try:
        await _insert_fr_balance(db, ctx["cid"], 2000.0)
        # 4 appels trimestriels (Q1 contient sale_date, Q2/Q3/Q4 sont futurs)
        for i, (d, ps, pe) in enumerate([
            ("2026-01-01", "2026-01-01", "2026-03-31"),
            ("2026-04-01", "2026-04-01", "2026-06-30"),
            ("2026-07-01", "2026-07-01", "2026-09-30"),
            ("2026-10-01", "2026-10-01", "2026-12-31"),
        ]):
            await db.fund_calls.insert_one({
                "id": f"fc-{i+1}-{uuid.uuid4()}",
                "name": f"Trimestriel {i+1}/4 - 2026",
                "date": d, "due_date": d,
                "period_start": ps, "period_end": pe,
                "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
                "call_type": "provisions",
                "total_amount": 500.0,
                "distribution": [
                    {"lot_id": ctx["lot_id"], "lot_number": "A1",
                     "owner_id": ctx["o1"], "owner_name": "Vendeur V",
                     "share": 500, "amount": 500.0, "paid": False},
                ],
            })

        mutate_fn = _get_endpoint(db, "/api/lots/{lot_id}/mutate")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        payload = LotMutationInput(
            new_owner_id=ctx["o2"], sale_date="2026-02-15", sale_price=200000.0,
        )
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut = result["mutation"]

        ids = mut.get("journal_entry_ids") or []
        assert len(ids) == 5, f"Attendu 5 OD (FR + prorata Q1 + 3 futures), recu {len(ids)}"

        # Verification dates et kinds
        entries = mut.get("entries_created") or []
        by_date = {e["date"]: e for e in entries}
        assert by_date["2026-02-15"]["kind"] == "fonds_roulement"
        assert by_date["2026-01-01"]["kind"] == "prorata"
        assert by_date["2026-04-01"]["kind"] == "future_call"
        assert by_date["2026-07-01"]["kind"] == "future_call"
        assert by_date["2026-10-01"]["kind"] == "future_call"

        # Quote-part du lot par appel futur = 500 EUR
        for d in ("2026-04-01", "2026-07-01", "2026-10-01"):
            assert abs(by_date[d]["amount"] - 500.0) < 0.01, (
                f"Future call {d} : attendu 500.0, recu {by_date[d]['amount']}"
            )

        # Verifie en DB que les lignes OD sont bien DR acheteur / CR vendeur
        jes = await db.journal_entries.find(
            {"source_id": ctx["lot_id"], "source_type": "lot_mutation"}, {"_id": 0}
        ).to_list(10)
        assert len(jes) == 5
        for je in jes:
            debit_line = next(ln for ln in je["lines"] if ln["debit"] > 0)
            credit_line = next(ln for ln in je["lines"] if ln["credit"] > 0)
            assert debit_line["account_number"] == "4100002", "DR doit etre acheteur (4100002)"
            assert debit_line["third_party_id"] == ctx["o2"]
            assert credit_line["account_number"] == "4100001", "CR doit etre vendeur (4100001)"
            assert credit_line["third_party_id"] == ctx["o1"]

        # La distribution des appels futurs n'est PAS modifiee : owner reste vendeur
        for fc in await db.fund_calls.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(10):
            for d in fc["distribution"]:
                if d["lot_id"] == ctx["lot_id"]:
                    assert d["owner_id"] == ctx["o1"], (
                        f"Distribution {fc['name']} : owner doit rester vendeur (iter85 - "
                        f"pas de migration owner_id), recu {d['owner_id']}"
                    )

        # regenerated_calls est neutralise
        regen = mut.get("regenerated_calls") or {}
        assert regen.get("fixed") == 0

        # Balance de tier : 5 mouvements pour vendeur (5 credits) et acheteur (5 debits)
        from routes.reports import create_reports_router
        rep_router = create_reports_router(db)
        sit_owner_fn = None
        for r in rep_router.routes:
            if r.path == "/api/reports/balance-tiers/owners/{owner_id}":
                sit_owner_fn = r.endpoint
                break
        assert sit_owner_fn is not None

        # Mock minimal Request (chinese wall override via copropriete_id query)
        class _Req:
            state = type("S", (), {"copropriete_id": ctx["cid"]})()
        seller_stmt = await sit_owner_fn(
            owner_id=ctx["o1"], request=_Req(),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        # Seller : doit avoir 5 credits aux 5 dates
        seller_credits_by_date = {}
        for m in seller_stmt.get("movements", []):
            if m.get("source_type") == "lot_mutation" or m.get("reference", "").startswith("MUT-"):
                seller_credits_by_date.setdefault(m["date"], 0.0)
                seller_credits_by_date[m["date"]] += float(m.get("credit", 0))
        # Doit contenir les 5 dates avec credit > 0
        for d in ("2026-01-01", "2026-02-15", "2026-04-01", "2026-07-01", "2026-10-01"):
            assert d in seller_credits_by_date, f"Date {d} manquante dans situation vendeur"
            assert seller_credits_by_date[d] > 0, f"Credit attendu sur {d} pour vendeur"

        buyer_stmt = await sit_owner_fn(
            owner_id=ctx["o2"], request=_Req(),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        buyer_debits_by_date = {}
        for m in buyer_stmt.get("movements", []):
            if m.get("source_type") == "lot_mutation" or m.get("reference", "").startswith("MUT-"):
                buyer_debits_by_date.setdefault(m["date"], 0.0)
                buyer_debits_by_date[m["date"]] += float(m.get("debit", 0))
        for d in ("2026-01-01", "2026-02-15", "2026-04-01", "2026-07-01", "2026-10-01"):
            assert d in buyer_debits_by_date, f"Date {d} manquante dans situation acheteur"
            assert buyer_debits_by_date[d] > 0, f"Debit attendu sur {d} pour acheteur"
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


def test_iter85_quarterly_mutation_5_ods():
    asyncio.run(_test_quarterly_mutation_5_ods())
