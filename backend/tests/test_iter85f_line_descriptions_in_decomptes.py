"""Regression test - iter85f - Descriptions de lignes propagees aux decomptes.

Demande user : "permettre d'ajouter un commentaire sur les lignes de factures
qui seront visibles dans la description de la liste de dépenses et décomptes"

Le champ existe deja en saisie (InvoicesPage) et la liste des depenses
(fiscal.py::list_expenses) priorise deja `line.description or inv.description`.
Cette iteration etend la propagation aux DECOMPTES :
- `pdf_decompte.py` : si la facture a des lignes touchant le compte agrege,
  on affiche les descriptions de lignes (jointes par " - ") au lieu de la
  description globale facture.
- `reports.py::decompte_annuel` (JSON) : pareil.

Tests :
  1. Facture multi-lignes : decompte JSON expose les descriptions de lignes.
  2. Facture single-line legacy : description globale conservee.
  3. fiscal.py::list_expenses : description ligne prioritaire (regression).
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
    cid = f"itr85f-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    lot1 = f"lot1-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "DESC85F", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "614000", "name": "Honoraires", "class_num": 6, "copropriete_id": cid},
        {"number": "612000", "name": "Nettoyage", "class_num": 6, "copropriete_id": cid},
    ])
    await db.owners.insert_one({
        "id": o1, "name": "DUPONT", "last_name": "DUPONT", "first_name": "Jean",
        "auxiliary_code": "C0001", "copropriete_ids": [cid], "vcs_code": "+++111+++",
    })
    await db.lots.insert_one({
        "id": lot1, "number": "A1", "owner_id": o1, "owner_ids": [o1],
        "copropriete_id": cid, "quotity": 1000,
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1, "lot1": lot1}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_one({"id": ctx["o1"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.invoices.delete_many({"copropriete_id": ctx["cid"]})


async def _test_decompte_annuel_uses_line_descriptions():
    """Decompte annuel JSON : description = descriptions des lignes."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        # Facture multi-lignes : 2 lignes avec descriptions distinctes
        await db.invoices.insert_one({
            "id": str(uuid.uuid4()), "number": "FA-2026-001",
            "date": "2026-03-15", "supplier": "ENTREPRISE X",
            "description": "Description globale (sera ecrasee)",
            "total_amount": 500.0,
            "copropriete_id": ctx["cid"],
            "lines": [
                {"account_number": "614000", "amount": 300.0,
                 "description": "Honoraires reunion AG du 12/03"},
                {"account_number": "612000", "amount": 200.0,
                 "description": "Nettoyage hall - operation ponctuelle"},
            ],
            # distribution_lines pre-aggregee par lot (tantiemes)
            "distribution_lines": [
                {"lot_id": ctx["lot1"], "amount": 500.0, "share": 1000}
            ],
        })

        from routes.reports import create_reports_router
        router = create_reports_router(db)
        decompte_fn = None
        for r in router.routes:
            if r.path == "/api/reports/decompte" and "GET" in (r.methods or set()):
                decompte_fn = r.endpoint
                break
        assert decompte_fn is not None

        class _Req:
            state = type("S", (), {"copropriete_id": ctx["cid"]})()
            headers = {}

        result = await decompte_fn(
            request=_Req(),
            fiscal_year_id=ctx["fy_id"],
            date_from=None, date_to=None,
            copropriete_id=ctx["cid"],
        )
        decomptes = result.get("decomptes", [])
        owner_dec = next((d for d in decomptes if d["owner_id"] == ctx["o1"]), None)
        assert owner_dec is not None, "Owner doit avoir un decompte"

        charges = owner_dec.get("charges", [])
        assert len(charges) >= 1
        # Au moins une charge doit contenir les descriptions de lignes joints
        descs = [c.get("description", "") for c in charges]
        joined = "|".join(descs)
        assert "Honoraires reunion AG du 12/03" in joined, (
            f"Description ligne 1 doit etre visible. Recu: {descs}"
        )
        assert "Nettoyage hall - operation ponctuelle" in joined, (
            f"Description ligne 2 doit etre visible. Recu: {descs}"
        )
        assert "Description globale" not in joined, (
            "La description globale ne doit pas apparaitre (multi-lignes)"
        )
        print("OK - decompte JSON expose descriptions de lignes")
    finally:
        await _cleanup(ctx)


async def _test_decompte_annuel_single_line_legacy_uses_inv_description():
    """Decompte annuel : facture single-line conserve description globale."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await db.invoices.insert_one({
            "id": str(uuid.uuid4()), "number": "FA-LEG-001",
            "date": "2026-03-15", "supplier": "FOURNISSEUR Y",
            "description": "Description facture legacy single-line",
            "total_amount": 100.0, "copropriete_id": ctx["cid"],
            "account_number": "614000",
            # Pas de lines[], facture en mode 1-ligne legacy
            "distribution_lines": [
                {"lot_id": ctx["lot1"], "amount": 100.0, "share": 1000}
            ],
        })

        from routes.reports import create_reports_router
        router = create_reports_router(db)
        decompte_fn = next(
            (r.endpoint for r in router.routes if r.path == "/api/reports/decompte" and "GET" in (r.methods or set())),
            None,
        )

        class _Req:
            state = type("S", (), {"copropriete_id": ctx["cid"]})()
            headers = {}

        result = await decompte_fn(
            request=_Req(), fiscal_year_id=ctx["fy_id"],
            date_from=None, date_to=None, copropriete_id=ctx["cid"],
        )
        owner_dec = next(d for d in result["decomptes"] if d["owner_id"] == ctx["o1"])
        descs = [c.get("description", "") for c in owner_dec.get("charges", [])]
        assert any("Description facture legacy" in d for d in descs), (
            f"Description globale facture legacy doit etre visible. Recu: {descs}"
        )
        print("OK - decompte JSON single-line preserve description globale")
    finally:
        await _cleanup(ctx)


async def _test_pdf_decompte_line_descriptions_in_bytes():
    """Le PDF doit contenir les descriptions de lignes pour les factures multi."""
    from pdf_decompte import build_decompte_pdf
    from io import BytesIO
    from pypdf import PdfReader

    ctx = await _setup()
    db = ctx["db"]
    try:
        invoice_id = str(uuid.uuid4())
        await db.invoices.insert_one({
            "id": invoice_id, "number": "FA-MULTI-001",
            "date": "2026-03-15", "supplier": "ENTREPRISE X",
            "description": "Multi-description-globale-NON-utilisee",
            "total_amount": 600.0, "copropriete_id": ctx["cid"],
            "account_number": "614000",
            "lines": [
                {"account_number": "614000", "amount": 600.0,
                 "description": "LIBELLE-LIGNE-AG-MARS2026"},
            ],
            "distribution_lines": [
                {"lot_id": ctx["lot1"], "amount": 600.0, "share": 1000}
            ],
        })

        owner_doc = await db.owners.find_one({"id": ctx["o1"]}, {"_id": 0})
        copro_doc = await db.coproprietes.find_one({"id": ctx["cid"]}, {"_id": 0})
        fy_doc = await db.fiscal_years.find_one({"id": ctx["fy_id"]}, {"_id": 0})
        owner_lots = await db.lots.find({"copropriete_id": ctx["cid"], "owner_id": ctx["o1"]}, {"_id": 0}).to_list(10)
        all_lots = await db.lots.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(100)
        all_invoices = await db.invoices.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(100)
        all_keys = await db.distribution_keys.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(50)
        all_cats = await db.expense_categories.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(50)
        all_accs = await db.pcmn_accounts.find({"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(50)
        fund_calls = []
        bank_txns = []

        pdf = build_decompte_pdf(
            copropriete=copro_doc, fiscal_year=fy_doc, owner=owner_doc,
            owner_lots=owner_lots, all_lots=all_lots,
            invoices=all_invoices, fund_calls=fund_calls,
            payments=[],
            distribution_keys=all_keys,
        )
        assert pdf.startswith(b"%PDF-")
        reader = PdfReader(BytesIO(pdf))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        assert "LIBELLE-LIGNE-AG-MARS2026" in text, (
            f"Description ligne doit etre presente dans le PDF. Extracted:\n{text[:1500]}"
        )
        print("OK - PDF decompte expose description de ligne")
    finally:
        await _cleanup(ctx)


def test_iter85f_decompte_annuel_uses_line_descriptions():
    asyncio.run(_test_decompte_annuel_uses_line_descriptions())


def test_iter85f_decompte_annuel_single_line_legacy():
    asyncio.run(_test_decompte_annuel_single_line_legacy_uses_inv_description())


def test_iter85f_pdf_decompte_includes_line_description():
    asyncio.run(_test_pdf_decompte_line_descriptions_in_bytes())
