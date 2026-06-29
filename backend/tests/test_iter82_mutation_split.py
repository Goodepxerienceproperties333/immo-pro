"""Regression test - iter82 - Decompte de mutation separe en 3 blocs.

Demande user : "lors d'une mutation, separer le transfert de fonds de roulement
des appels de provisions futurs a prevoir selon la periodicite du budget."

Le decompte de mutation expose maintenant 3 sections distinctes :

1. **Fonds de roulement** (capital permanent) - JAMAIS au prorata temporel,
   quote-part calculee sur lot_quotity / total_quotity.

2. **Prorata appel en cours** (provisions) - portion APRES la vente, transferee
   via OD du compte acquereur vers le compte vendeur.

3. **Appels de provisions futurs a prevoir** - liste des appels dont period_start
   est posterieure a sale_date. Information uniquement (l'acquereur les paiera
   normalement apres reaffectation du lot, NON inclus dans l'OD).

L'ECRITURE OD ne contient QUE roulement_quota + current_period_prorata.

Tests :
- E2E : vente milieu Q1 + Q2/Q3/Q4 futurs -> 1 dans current, 3 dans future.
- Le fonds de roulement reste base sur les quotites (pas de prorata temporel).
- future_calls_total bien calcule.
- budget_frequency_label = "Trimestriel".
- Le mutate-preview retourne la meme structure (sans persistance).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/scripts")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_acp_with_quarterly_calls():
    """Cree une ACP avec 2 owners, 1 lot et 4 appels trimestriels Q1-Q4 2026
    distribues integralement sur le vendeur. Retourne le dict d'IDs et la db."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr82-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER82", "reference": "TEST-ITER82", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds de roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Coproprietaires", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Vendeur Vincent", "last_name": "Vendeur",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acquereur Alice", "last_name": "Acquereur",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # 1 lot avec quotite 500/1000 (50%)
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
        "copropriete_id": cid, "quotity": 500.0,
    })
    # Lot "fantome" 500/1000 (50%) sans proprietaire pour le total_quotity
    await db.lots.insert_one({
        "id": f"ghost-{uuid.uuid4()}", "number": "B1",
        "copropriete_id": cid, "quotity": 500.0,
    })
    # Solde fonds de roulement = 2000 EUR (Cr 2000)
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()),
        "journal_type": "OD",
        "date": "2026-01-01",
        "copropriete_id": cid,
        "lines": [
            {"account_number": "410", "debit": 2000.0, "credit": 0.0},
            {"account_number": "100", "debit": 0.0, "credit": 2000.0},
        ],
        "total_debit": 2000.0, "total_credit": 2000.0,
    })
    # 4 appels trimestriels Q1-Q4 2026, chaque appel = 600 EUR vendeur
    quarters = [
        ("Q1", "2026-01-01", "2026-03-31", "2026-01-31"),
        ("Q2", "2026-04-01", "2026-06-30", "2026-04-30"),
        ("Q3", "2026-07-01", "2026-09-30", "2026-07-31"),
        ("Q4", "2026-10-01", "2026-12-31", "2026-10-31"),
    ]
    fc_ids = []
    for idx, (lbl, ps, pe, dd) in enumerate(quarters, start=1):
        fc_id = f"fc-{uuid.uuid4()}"
        fc_ids.append(fc_id)
        await db.fund_calls.insert_one({
            "id": fc_id,
            "name": f"Trimestriel {idx}/4 - Exercice 2026",
            "date": ps,
            "due_date": dd,
            "period_start": ps,
            "period_end": pe,
            "fiscal_year_id": fy_id,
            "copropriete_id": cid,
            "call_type": "provisions",
            "total_amount": 600.0,
            "distribution": [
                {"owner_id": o1_id, "owner_name": "Vendeur Vincent",
                 "share": 500, "amount": 600.0, "paid": False},
            ],
        })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1_id, "o2": o2_id,
            "lot_id": lot_id, "fc_ids": fc_ids}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_mutate_endpoint(db, route_path: str):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == route_path:
            return r.endpoint
    return None


async def _test_breakdown_e2e():
    """Vente au 15/02/2026 (milieu Q1) :
    - Fonds de roulement : 50% de 2000 = 1000 EUR (PAS au prorata temporel)
    - Prorata appel Q1 : 600 * 45/90 = 300 EUR (jours apres vente)
    - Future calls : Q2, Q3, Q4 (3 appels) total 1800 EUR
    - Total OD = 1000 + 300 = 1300 EUR (les futurs ne sont PAS dans l'OD)
    """
    ctx = await _setup_acp_with_quarterly_calls()
    db = ctx["db"]
    try:
        # Test mutate-preview (pas de persistance)
        preview_fn = _get_mutate_endpoint(db, "/api/lots/{lot_id}/mutate-preview")
        assert preview_fn is not None
        LotMutationInput = preview_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-02-15", sale_price=200000.0)
        preview = await preview_fn(lot_id=ctx["lot_id"], data=payload)

        # 1. Fonds de roulement
        assert abs(preview["fonds_roulement_total"] - 2000.0) < 0.01, (
            f"fonds_roulement_total attendu 2000, recu {preview['fonds_roulement_total']}"
        )
        assert preview["lot_quotity"] == 500.0
        assert preview["total_quotity"] == 1000.0
        assert abs(preview["roulement_quota"] - 1000.0) < 0.01, (
            f"roulement_quota attendu 1000 (quote-part 50%), recu {preview['roulement_quota']}"
        )

        # 2. Prorata appel en cours (Q1)
        assert abs(preview["current_period_prorata"] - 300.0) < 0.01, (
            f"current_period_prorata attendu 300, recu {preview['current_period_prorata']}"
        )
        assert len(preview["current_period_details"]) == 1
        assert preview["current_period_details"][0]["fund_call_name"].startswith("Trimestriel 1/4")
        # Alias retrocompatibles
        assert preview["prorata_provisions"] == preview["current_period_prorata"]
        assert preview["prorata_details"] == preview["current_period_details"]

        # 3. Appels futurs (Q2, Q3, Q4)
        assert len(preview["future_calls"]) == 3, (
            f"Attendu 3 appels futurs, recu {len(preview['future_calls'])}"
        )
        assert abs(preview["future_calls_total"] - 1800.0) < 0.01, (
            f"future_calls_total attendu 1800, recu {preview['future_calls_total']}"
        )
        # Verifier l'ordre et les periodes
        fc_periods = [(fc["period_start"], fc["period_end"]) for fc in preview["future_calls"]]
        assert ("2026-04-01", "2026-06-30") in fc_periods
        assert ("2026-07-01", "2026-09-30") in fc_periods
        assert ("2026-10-01", "2026-12-31") in fc_periods
        # Chaque appel futur = 600 EUR (montant complet, NON proraté)
        for fc in preview["future_calls"]:
            assert abs(fc["amount"] - 600.0) < 0.01

        # 4. Frequency detection
        assert preview["budget_frequency"] == 4
        assert preview["budget_frequency_label"] == "Trimestriel"

        # 5. Total OD = roulement + prorata appel en cours (PAS les futurs)
        assert abs(preview["total_transfer"] - 1300.0) < 0.01, (
            f"total_transfer attendu 1300 (1000+300, futurs exclus), recu {preview['total_transfer']}"
        )

        # Test mutate (avec persistance) : verifie que mutation_record contient la meme structure
        mutate_fn = _get_mutate_endpoint(db, "/api/lots/{lot_id}/mutate")
        assert mutate_fn is not None
        result = await mutate_fn(lot_id=ctx["lot_id"], data=payload)
        mut = result["mutation"]

        assert abs(mut["roulement_quota"] - 1000.0) < 0.01
        assert abs(mut["current_period_prorata"] - 300.0) < 0.01
        assert abs(mut["prorata_provisions"] - 300.0) < 0.01  # alias legacy
        assert len(mut["future_calls"]) == 3
        assert abs(mut["future_calls_total"] - 1800.0) < 0.01
        assert mut["budget_frequency"] == 4
        assert mut["budget_frequency_label"] == "Trimestriel"
        assert abs(mut["total_transfer"] - 1300.0) < 0.01

        # Verifie les ecritures OD : depuis iter85, l'OD est ECLATEE en 5 :
        #   - 1 entry "fonds_roulement" datee sale_date (1000 EUR)
        #   - 1 entry "prorata" datee call_date originale Q1 (300 EUR)
        #   - 3 entries "future_call" datees Q2/Q3/Q4 (600 EUR chacune)
        # Total cumule = 1000 + 300 + 1800 = 3100 EUR (FR + prorata + futurs)
        jes = await db.journal_entries.find(
            {"source_id": ctx["lot_id"], "source_type": "lot_mutation"}, {"_id": 0}
        ).to_list(10)
        assert len(jes) == 5, f"Attendu 5 ecritures OD (iter85 incl. futures), recu {len(jes)}"
        sum_debit = sum(j.get("total_debit", 0) for j in jes)
        assert abs(sum_debit - 3100.0) < 0.01, (
            f"Somme OD attendue 3100 (FR 1000 + prorata 300 + 3 futures 1800), recu {sum_debit}"
        )

    finally:
        await _cleanup(ctx)


async def _test_no_future_calls_when_sale_after_all_quarters():
    """Vente au 31/12/2026 (apres Q4) :
    - Prorata Q4 = 600 * 1/92 ~= 6.52 EUR
    - Future calls = [] (aucun appel apres sale_dt)
    """
    ctx = await _setup_acp_with_quarterly_calls()
    db = ctx["db"]
    try:
        preview_fn = _get_mutate_endpoint(db, "/api/lots/{lot_id}/mutate-preview")
        LotMutationInput = preview_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-12-31")
        preview = await preview_fn(lot_id=ctx["lot_id"], data=payload)

        assert len(preview["future_calls"]) == 0, (
            f"Aucun appel futur attendu, recu {len(preview['future_calls'])}"
        )
        assert preview["future_calls_total"] == 0.0
        # Q4 couvre 2026-12-31 -> prorata ~ 6.52
        assert len(preview["current_period_details"]) == 1
        assert preview["current_period_prorata"] > 0
    finally:
        await _cleanup(ctx)


async def _test_only_future_calls_when_sale_before_first_quarter():
    """Vente au 31/12/2025 (avant Q1 2026) :
    - Aucun appel en cours (sale_dt < tous les period_start)
    - Future calls = Q1, Q2, Q3, Q4 (4 appels)
    """
    ctx = await _setup_acp_with_quarterly_calls()
    db = ctx["db"]
    try:
        preview_fn = _get_mutate_endpoint(db, "/api/lots/{lot_id}/mutate-preview")
        LotMutationInput = preview_fn.__annotations__.get("data")
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2025-12-31")
        preview = await preview_fn(lot_id=ctx["lot_id"], data=payload)

        assert len(preview["current_period_details"]) == 0
        assert preview["current_period_prorata"] == 0.0
        assert len(preview["future_calls"]) == 4
        assert abs(preview["future_calls_total"] - 2400.0) < 0.01
        # Total OD = uniquement le roulement (les futurs sont info-only)
        assert abs(preview["total_transfer"] - preview["roulement_quota"]) < 0.01
    finally:
        await _cleanup(ctx)


def test_mutation_breakdown_three_sections():
    asyncio.run(_test_breakdown_e2e())


def test_mutation_no_future_calls():
    asyncio.run(_test_no_future_calls_when_sale_after_all_quarters())


def test_mutation_only_future_calls():
    asyncio.run(_test_only_future_calls_when_sale_before_first_quarter())
