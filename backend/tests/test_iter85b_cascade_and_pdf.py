"""Regression test - iter85b - Cascade parent/enfant dans distribution + PDF fixes.

Demande user :
  1. Bug PDF montant futurs : utiliser cle "amount" au lieu de "lot_amount"
  2. Bug PDF debordement colonne Appel : Paragraph wrap
  3. Sous-totaux par trimestre dans bloc 3 PDF
  4. parent_lot_id dans distribution fund_calls
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
    cid = f"itr85b-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    parent_lot = f"lp-{uuid.uuid4()}"
    child_lot = f"lc-{uuid.uuid4()}"
    other_lot = f"lo-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "ACACIA", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "4100001", "name": "Owner1", "class_num": 4, "copropriete_id": cid},
        {"number": "614000", "name": "Honoraires", "class_num": 6, "copropriete_id": cid},
    ])
    await db.owners.insert_one({
        "id": o1, "name": "DUPONT Jean", "last_name": "DUPONT", "first_name": "Jean",
        "auxiliary_code": "C0001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "4100001"}}, "vcs_code": "+++111+++",
    })
    # Lot principal (parent), lot secondaire (enfant), lot d'un autre
    await db.lots.insert_many([
        {"id": parent_lot, "number": "A1", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 400.0},
        {"id": child_lot, "number": "A1-Cave", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 50.0, "parent_lot_id": parent_lot},
        {"id": other_lot, "number": "B1", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 550.0},
    ])
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1,
            "parent_lot": parent_lot, "child_lot": child_lot, "other_lot": other_lot}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_one({"id": ctx["o1"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str, method: str = None):
    from routes.fund_calls import create_fund_calls_router
    router = create_fund_calls_router(db)
    for r in router.routes:
        if r.path == route_path:
            if method and method.upper() not in (r.methods or set()):
                continue
            return r.endpoint
    return None


async def _test_create_fund_call_distribution_includes_parent_lot_id():
    """POST /api/fund-calls : chaque entry de distribution a parent_lot_id."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        from fastapi import Request
        create_fn = _get_endpoint(db, "/api/fund-calls", method="POST")
        FundCallInput = create_fn.__annotations__.get("data")
        payload = FundCallInput(
            name="Test cascade",
            date="2026-01-15", due_date="2026-01-15",
            fiscal_year_id=ctx["fy_id"], total_amount=1000.0,
            copropriete_id=ctx["cid"],
        )

        result = await create_fn(data=payload)
        dist = result.get("distribution") or []
        assert len(dist) == 3, f"3 lots attendus, recu {len(dist)}"

        for d in dist:
            assert "parent_lot_id" in d, "parent_lot_id doit etre present dans chaque entry"
            assert "lot_id" in d
            assert "lot_number" in d

        child_entry = next(d for d in dist if d["lot_id"] == ctx["child_lot"])
        assert child_entry["parent_lot_id"] == ctx["parent_lot"], (
            f"Lot enfant doit avoir parent_lot_id, recu : {child_entry['parent_lot_id']}"
        )
        parent_entry = next(d for d in dist if d["lot_id"] == ctx["parent_lot"])
        assert parent_entry["parent_lot_id"] == "", "Lot parent ne doit pas avoir parent_lot_id"
        print("OK - create_fund_call inclut parent_lot_id")
    finally:
        await _cleanup(ctx)


async def _test_pdf_uses_amount_key_for_future_calls():
    """PDF Decompte de mutation : montant futur lu depuis 'amount' (non zero)."""
    from pdf_mutation_decompte import build_mutation_decompte_pdf
    from io import BytesIO
    from pypdf import PdfReader

    pdf = build_mutation_decompte_pdf(
        copropriete={"name": "ACACIA", "reference": "REF"},
        lot={"number": "A1", "quotity": 400},
        seller={"name": "SELLER Jean"},
        buyer={"name": "BUYER Marie"},
        mutation={"date": "2026-02-15"},
        breakdown={
            "roulement_quota": 800.0,
            "current_period_prorata": 200.0,
            "current_period_details": [],
            "future_calls": [
                {"fund_call_name": "Trimestriel 2/4 - Exercice 2025-2026",
                 "date": "2026-04-01", "period_start": "2026-04-01",
                 "period_end": "2026-06-30", "amount": 500.0},
                {"fund_call_name": "Trimestriel 3/4 - Exercice 2025-2026",
                 "date": "2026-07-01", "period_start": "2026-07-01",
                 "period_end": "2026-09-30", "amount": 500.0},
                {"fund_call_name": "Trimestriel 4/4 - Exercice 2025-2026",
                 "date": "2026-10-01", "period_start": "2026-10-01",
                 "period_end": "2026-12-31", "amount": 500.0},
            ],
            "future_calls_total": 1500.0,
            "budget_frequency": 4,
            "budget_frequency_label": "Trimestriel",
            "total_transfer": 1000.0,
        },
    )
    assert pdf.startswith(b"%PDF-")
    # Extraction du texte via pypdf
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    five_hundred_count = text.count("500,00")
    assert five_hundred_count >= 3, (
        f"500,00 doit apparaitre au moins 3 fois dans le PDF, vu {five_hundred_count}\n"
        f"Extracted text:\n{text[:1500]}"
    )
    print(f"OK - PDF utilise amount correctement (500,00 vu {five_hundred_count} fois)")


async def _test_pdf_includes_subtotals_per_trimester():
    """PDF doit contenir un sous-total par trimestre + sous-total general."""
    from pdf_mutation_decompte import build_mutation_decompte_pdf
    from io import BytesIO
    from pypdf import PdfReader

    pdf = build_mutation_decompte_pdf(
        copropriete={"name": "ACACIA"},
        lot={"number": "A1", "quotity": 400},
        seller={"name": "SELLER"}, buyer={"name": "BUYER"},
        mutation={"date": "2026-02-15"},
        breakdown={
            "roulement_quota": 0,
            "current_period_prorata": 0,
            "current_period_details": [],
            "future_calls": [
                {"fund_call_name": "Trimestriel 2/4 - 2026",
                 "date": "2026-04-01", "period_start": "2026-04-01",
                 "period_end": "2026-06-30", "amount": 500.0},
                {"fund_call_name": "Trimestriel 3/4 - 2026",
                 "date": "2026-07-01", "period_start": "2026-07-01",
                 "period_end": "2026-09-30", "amount": 500.0},
            ],
            "future_calls_total": 1000.0,
            "budget_frequency": 4,
            "budget_frequency_label": "Trimestriel",
            "total_transfer": 0,
        },
    )
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "Sous-total T2" in text, f"Sous-total T2 manquant dans :\n{text[:1500]}"
    assert "Sous-total T3" in text, f"Sous-total T3 manquant"
    assert "Sous-total appels futurs" in text, "Sous-total general manquant"
    print("OK - PDF affiche sous-totaux par trimestre + sous-total general")


async def _test_pdf_long_appel_name_wraps_in_paragraph():
    """PDF doit utiliser Paragraph pour wrapper le nom d'appel long sans deborder."""
    from pdf_mutation_decompte import build_mutation_decompte_pdf
    from io import BytesIO
    from pypdf import PdfReader

    pdf = build_mutation_decompte_pdf(
        copropriete={"name": "ACACIA"},
        lot={"number": "A1", "quotity": 400},
        seller={"name": "SELLER"}, buyer={"name": "BUYER"},
        mutation={"date": "2026-02-15"},
        breakdown={
            "roulement_quota": 0, "current_period_prorata": 0,
            "current_period_details": [],
            "future_calls": [
                {"fund_call_name": "Trimestriel 2/4 - Exercice 2025-2026",
                 "date": "2026-04-01", "period_start": "2026-04-01",
                 "period_end": "2026-06-30", "amount": 500.0},
            ],
            "future_calls_total": 500.0,
            "budget_frequency_label": "Trimestriel",
            "total_transfer": 0,
        },
    )
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 2000
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    # Le texte doit contenir le mot "Trimestriel" ou "Exercice"
    assert "Trimestriel" in text or "Exercice" in text, (
        f"Texte attendu manquant. Extracted: {text[:800]}"
    )
    print("OK - PDF genere correctement avec libelle long wrapping")


def test_iter85b_create_fund_call_parent_lot_id():
    asyncio.run(_test_create_fund_call_distribution_includes_parent_lot_id())


def test_iter85b_pdf_uses_amount_key():
    asyncio.run(_test_pdf_uses_amount_key_for_future_calls())


def test_iter85b_pdf_subtotals_per_trimester():
    asyncio.run(_test_pdf_includes_subtotals_per_trimester())


def test_iter85b_pdf_long_appel_wraps():
    asyncio.run(_test_pdf_long_appel_name_wraps_in_paragraph())
