"""iter90ek + iter90el : PDF decompte annuel.

- iter90ek : le filigrane APERCU depend uniquement du statut de l'exercice.
  Un exercice cloture ne doit JAMAIS avoir de filigrane, meme si le parametre
  preview=true est envoye.
- iter90el : prorata mutation. Si un lot est mute pendant l'exercice, la
  periode de possession du proprietaire est prise en compte pour :
    * L'affichage "Prorata: X / Y jours" (jours reels / jours d'exercice)

Note (iter90g1 - Feb 2026) : le FILTRAGE par date d'invoice a ete remplace
par un vrai PRORATA multiplicatif (voir test_iter90g1_pdf_decompte_prorata_
mutation.py). Les tests date-based `test_invoice_before_purchase_is_excluded`
et `test_invoice_after_purchase_is_included` ont ete supprimes de ce fichier
et remplaces par des tests prorata.
"""
import io
import re
from datetime import date

import fitz  # PyMuPDF for PDF text extraction


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def test_watermark_never_appears_on_closed_fiscal_year():
    """iter90ek : exercice cloture -> filigrane absent meme si preview=True."""
    from pdf_decompte import build_decompte_pdf

    owner = {"id": "o1", "name": "Test Owner", "vcs_code": "+++001/0002/00003+++"}
    copro = {"id": "c1", "name": "Test ACP", "address": "Rue X"}
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=[], distribution_keys=[],
        fund_calls=[], payments=[],
        preview=False,
    )
    text = _pdf_text(pdf_bytes)
    assert "APERCU" not in text.upper(), (
        "Le filigrane APERCU ne doit pas apparaitre sur un exercice cloture"
    )


def test_watermark_appears_on_open_fiscal_year_preview():
    """iter90ek : exercice ouvert + preview=True -> filigrane present."""
    from pdf_decompte import build_decompte_pdf

    owner = {"id": "o1", "name": "Test Owner"}
    copro = {"id": "c1", "name": "Test ACP"}
    fy = {
        "id": "fy1", "name": "2026", "status": "open",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=[], distribution_keys=[], fund_calls=[], payments=[],
        preview=True,
    )
    text = _pdf_text(pdf_bytes)
    assert "APERCU" in text.upper(), "Filigrane APERCU attendu sur exercice ouvert"


def test_prorata_full_year_without_mutation():
    """iter90el : sans mutation -> Prorata affiche jours_exercice / jours_exercice."""
    from pdf_decompte import build_decompte_pdf

    owner = {"id": "o1", "name": "Test Owner"}
    copro = {"id": "c1", "name": "Test ACP"}
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    invoices = [{
        "id": "inv1", "number": "V-001", "supplier": "SUP",
        "date": "2026-06-01", "total_amount": 100.0,
        "distribution_lines": [
            {"lot_id": "l1", "lot_number": "202", "amount": 100.0}
        ],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=[],
    )
    text = _pdf_text(pdf_bytes)
    assert "365 / 365 jours" in text, (
        f"Prorata attendu '365 / 365 jours' pour un exercice complet sans "
        f"mutation, texte PDF : {text[:1000]}"
    )


def test_prorata_partial_year_after_purchase():
    """iter90el : owner achete un lot le 15/06 -> Prorata = 200 / 365 jours
    (15/06 -> 31/12 inclusif = 200 jours). Convention iter90g1 : acheteur
    prend le jour de vente."""
    from pdf_decompte import build_decompte_pdf

    owner = {"id": "o_buyer", "name": "Buyer"}
    copro = {"id": "c1", "name": "Test ACP"}
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-06-15",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    invoices = [{
        "id": "inv1", "number": "V-POST", "supplier": "SUP",
        "date": "2026-07-20", "total_amount": 100.0,
        "distribution_lines": [
            {"lot_id": "l1", "lot_number": "202", "amount": 100.0}
        ],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    expected_days = (date(2026, 12, 31) - date(2026, 6, 15)).days + 1
    assert expected_days == 200
    match = re.search(r"Prorata:\s*(\d+)\s*/\s*(\d+)\s*jours", text)
    assert match, f"Pattern Prorata introuvable dans le PDF : {text[:1000]}"
    assert int(match.group(1)) == 200, (
        f"Prorata jours possedes attendu 200, recu {match.group(1)}"
    )
    assert int(match.group(2)) == 365


def test_prorata_partial_year_after_sale():
    """iter90el / iter90g1 : owner VEND un lot le 20/03 -> Prorata = 78/365.
    Convention iter90g1 : vendeur perd le jour de vente (finit la veille).
    Jan 1 -> Mar 19 = 78 jours (au lieu de 79 avant iter90g1)."""
    from pdf_decompte import build_decompte_pdf

    owner = {"id": "o_seller", "name": "Seller"}
    copro = {"id": "c1", "name": "Test ACP"}
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-03-20",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    invoices = [{
        "id": "inv1", "number": "V-PRE", "supplier": "SUP",
        "date": "2026-02-10", "total_amount": 100.0,
        "distribution_lines": [
            {"lot_id": "l1", "lot_number": "202", "amount": 100.0}
        ],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    expected_days = (date(2026, 3, 19) - date(2026, 1, 1)).days + 1
    assert expected_days == 78
    match = re.search(r"Prorata:\s*(\d+)\s*/\s*(\d+)\s*jours", text)
    assert match, f"Pattern Prorata introuvable : {text[:500]}"
    assert int(match.group(1)) == 78
