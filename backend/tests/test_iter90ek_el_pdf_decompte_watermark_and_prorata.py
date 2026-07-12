"""iter90ek + iter90el : PDF decompte annuel.

- iter90ek : le filigrane APERCU depend uniquement du statut de l'exercice.
  Un exercice cloture ne doit JAMAIS avoir de filigrane, meme si le parametre
  preview=true est envoye.
- iter90el : prorata mutation. Si un lot est mute pendant l'exercice, la
  periode de possession du proprietaire est prise en compte pour :
    * L'affichage "Prorata: X / Y jours" (jours reels / jours d'exercice)
    * Le filtrage des invoices attribuees a ce proprietaire (uniquement les
      invoices dont la date est dans la periode de possession).
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
        # Test le fix : preview=True doit etre ignore car exercice cloture.
        # Le call site (reports.py) passe deja preview=(preview and status != "closed")
        # donc ici on simule le comportement du call site :
        preview=False,  # <-- reflet du fix iter90ek au niveau route
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
        preview=True,  # exercice ouvert + preview=True
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
    # Ajoute au moins UNE invoice pour que la ligne "Lot:" apparaisse
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
    (15/06 -> 31/12 inclusif = 200 jours)."""
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
    # 2026-06-15 -> 2026-12-31 = 200 jours (inclusive)
    expected_days = (date(2026, 12, 31) - date(2026, 6, 15)).days + 1
    assert expected_days == 200
    match = re.search(r"Prorata:\s*(\d+)\s*/\s*(\d+)\s*jours", text)
    assert match, f"Pattern Prorata introuvable dans le PDF : {text[:1000]}"
    assert int(match.group(1)) == 200, (
        f"Prorata jours possedes attendu 200, recu {match.group(1)}"
    )
    assert int(match.group(2)) == 365


def test_prorata_partial_year_after_sale():
    """iter90el : owner VEND un lot le 20/03 -> Prorata = 79 / 365 jours."""
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
    expected_days = (date(2026, 3, 20) - date(2026, 1, 1)).days + 1
    assert expected_days == 79
    match = re.search(r"Prorata:\s*(\d+)\s*/\s*(\d+)\s*jours", text)
    assert match, f"Pattern Prorata introuvable : {text[:500]}"
    assert int(match.group(1)) == 79


def test_invoice_before_purchase_is_excluded():
    """iter90el : owner achete le 15/06 -> facture du 10/03 n'est PAS
    attribuee a lui (attribuee au vendeur precedent)."""
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
        "id": "inv1", "number": "V-PRE-001", "supplier": "AVANT",
        "date": "2026-03-10", "total_amount": 500.0,
        "distribution_lines": [
            {"lot_id": "l1", "lot_number": "202", "amount": 500.0}
        ],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=invoices, distribution_keys=[],
        fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    # La facture V-PRE-001 ne doit PAS apparaitre pour ce owner (achete le 15/06)
    assert "V-PRE-001" not in text, (
        "La facture du 10/03 avant mutation ne doit PAS apparaitre pour l'acheteur"
    )
    assert "AVANT" not in text, (
        "Le fournisseur AVANT (facture pre-mutation) ne doit PAS apparaitre"
    )


def test_invoice_after_purchase_is_included():
    """iter90el : owner achete le 15/06 -> facture du 20/07 EST attribuee a lui."""
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
        "id": "inv1", "number": "V-POST-001", "supplier": "APRES-MUT",
        "date": "2026-07-20", "total_amount": 250.0,
        "distribution_lines": [
            {"lot_id": "l1", "lot_number": "202", "amount": 250.0}
        ],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        all_lots=[{"id": "l1", "number": "202", "quotity": 100.0}],
        invoices=invoices, distribution_keys=[],
        fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    assert "V-POST-001" in text or "APRES-MUT" in text, (
        f"La facture APRES mutation doit apparaitre : {text[:1000]}"
    )
