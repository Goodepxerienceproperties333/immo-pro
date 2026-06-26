"""Regression test - iter76 - Prorata mutation sur appels de provisions.

Bug : la mutation utilisait [call.date, call.due_date] (fenetre d'emission ~30j)
au lieu de la periode COUVERTE par l'appel (~3 mois pour trimestriel). Resultat :
le prorata n'etait applique que si la vente tombait DANS la fenetre d'emission,
soit moins de 10% des cas. Les autres ventes ne transferaient aucun prorata,
laissant la facturation au vendeur meme apres la mutation.

Fix :
1. Generation des appels : ajout des champs period_start / period_end (periode
   couverte) lors de la creation depuis le budget.
2. Resolution dans mutate_lot : utilise period_start/period_end si presents,
   sinon deduit depuis "X/N" + fiscal_year, sinon fallback +90j.
3. Migration script pour les appels existants.

Tests :
- _compute_period sur appels trimestriels -> bonnes bornes [Q-start, Q-end]
- _compute_period sur appel annuel -> [date, fy_end]
- Prorata applique correctement pour une vente au milieu d'un trimestre
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timedelta, date

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/scripts")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_compute_period_quarterly():
    """Pour 'Trimestriel 1/4' au 01/01/2026, periode = [01/01, 31/03]."""
    from migrate_fund_calls_periods import _compute_period
    call = {"name": "Trimestriel 1/4 - 2026", "date": "2026-01-01", "fiscal_year_id": "fy1"}
    fy_by_id = {"fy1": {"id": "fy1", "end_date": "2026-12-31"}}
    ps, pe = _compute_period(call, fy_by_id)
    assert ps == "2026-01-01", f"expected 2026-01-01, got {ps}"
    assert pe == "2026-03-31", f"expected 2026-03-31, got {pe}"


def test_compute_period_quarterly_q4_with_fy_end_bound():
    """Pour 'Trimestriel 4/4' au 01/10/2026, period_end = [01/10, 31/12] (fy_end)."""
    from migrate_fund_calls_periods import _compute_period
    call = {"name": "Trimestriel 4/4 - 2026", "date": "2026-10-01", "fiscal_year_id": "fy1"}
    fy_by_id = {"fy1": {"id": "fy1", "end_date": "2026-12-31"}}
    ps, pe = _compute_period(call, fy_by_id)
    assert ps == "2026-10-01"
    assert pe == "2026-12-31"


def test_compute_period_quarterly_fiscal_year_non_calendar():
    """Pour 'Trimestriel 1/4' au 01/10/2025, periode = [01/10/2025, 31/12/2025].
    Exercice fiscal 01/10/2025 - 30/09/2026."""
    from migrate_fund_calls_periods import _compute_period
    call = {"name": "Trimestriel 1/4", "date": "2025-10-01", "fiscal_year_id": "fy2"}
    fy_by_id = {"fy2": {"id": "fy2", "end_date": "2026-09-30"}}
    ps, pe = _compute_period(call, fy_by_id)
    assert ps == "2025-10-01"
    assert pe == "2025-12-31"


def test_compute_period_annual_no_x_y_pattern():
    """Appel annuel sans 'X/N' -> period = [date, fy_end]."""
    from migrate_fund_calls_periods import _compute_period
    call = {"name": "Appel exceptionnel 2026", "date": "2026-05-15", "fiscal_year_id": "fy1"}
    fy_by_id = {"fy1": {"id": "fy1", "end_date": "2026-12-31"}}
    ps, pe = _compute_period(call, fy_by_id)
    assert ps == "2026-05-15"
    assert pe == "2026-12-31"


def test_compute_period_no_fiscal_year_fallback():
    """Sans fiscal_year_id ni X/N -> fallback +90j."""
    from migrate_fund_calls_periods import _compute_period
    call = {"name": "Appel libre", "date": "2026-05-15"}
    ps, pe = _compute_period(call, {})
    assert ps == "2026-05-15"
    # +90 days from 2026-05-15 = 2026-08-13
    assert pe == "2026-08-13"


async def _test_mutation_prorata_e2e():
    """E2E : cree un lot, un proprietaire, un appel trimestriel avec distribution,
    puis effectue une mutation au milieu du trimestre et verifie le prorata."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Setup : ACP + 2 owners + lot + fiscal_year + fund_call avec distribution
    cid = f"itr76-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    fc_id = f"fc-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER76", "reference": "TEST-ITER76", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    # PCMN: account 410 must exist for the owner tier accounts
    await db.pcmn_accounts.insert_many([
        {"number": "410", "name": "Coproprietaires", "class_num": 4, "copropriete_id": cid},
        {"number": "100", "name": "Fonds de roulement", "class_num": 1, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Vendeur Vincent", "last_name": "Vendeur",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acquereur Alice", "last_name": "Acquereur",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # PCMN for the tier accounts
    await db.pcmn_accounts.insert_many([
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
        "copropriete_id": cid, "quotity": 1000.0,
    })
    # Fund call trimestriel Q1 2026 (01/01 - 31/03), montant vendeur 600 EUR
    await db.fund_calls.insert_one({
        "id": fc_id,
        "name": "Trimestriel 1/4 - Exercice 2026",
        "date": "2026-01-01",
        "due_date": "2026-01-31",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "fiscal_year_id": fy_id,
        "copropriete_id": cid,
        "call_type": "provisions",
        "total_amount": 600.0,
        "distribution": [
            {"owner_id": o1_id, "owner_name": "Vendeur Vincent", "share": 1000, "amount": 600.0, "paid": False},
        ],
    })

    # Mutation au 15/02/2026 (milieu de Q1)
    # Q1 = 90 jours, jour de vente J45. Days_after = 31 - 15 + 28 + 31 = 75 ? Let's compute:
    # period_start=2026-01-01, period_end=2026-03-31. sale_date=2026-02-15
    # total_days = (2026-03-31 - 2026-01-01).days + 1 = 89 + 1 = 90
    # days_after = (2026-03-31 - 2026-02-15).days + 1 = 44 + 1 = 45
    # prorata = 600 * (45 / 90) = 300.00
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    mutate_fn = None
    for r in router.routes:
        if r.path == "/api/lots/{lot_id}/mutate":
            mutate_fn = r.endpoint
            break
    assert mutate_fn is not None

    LotMutationInput = mutate_fn.__annotations__.get("data")
    assert LotMutationInput is not None
    payload = LotMutationInput(new_owner_id=o2_id, sale_date="2026-02-15", sale_price=200000.0)

    result = await mutate_fn(lot_id=lot_id, data=payload)
    mut = result["mutation"]

    # Cleanup avant l'assert pour eviter de polluer la base si echec
    try:
        assert abs(mut["prorata_provisions"] - 300.00) < 0.01, (
            f"Prorata attendu 300.00, recu {mut['prorata_provisions']}. "
            f"details={mut.get('prorata_details')}"
        )
        details = mut["prorata_details"]
        assert len(details) == 1, f"Expected 1 prorata detail, got {len(details)}"
        assert details[0]["period_start"] == "2026-01-01"
        assert details[0]["period_end"] == "2026-03-31"
        assert details[0]["total_days"] == 90
        assert details[0]["days_after"] == 45
    finally:
        # Cleanup
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.owners.delete_many({"id": {"$in": [o1_id, o2_id]}})
        await db.lots.delete_one({"id": lot_id})
        await db.fund_calls.delete_one({"id": fc_id})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_mutation_prorata_quarterly_midperiod():
    asyncio.run(_test_mutation_prorata_e2e())
