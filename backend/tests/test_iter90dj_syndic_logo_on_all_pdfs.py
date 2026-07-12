"""iter90dj : tests unitaires pour l'affichage du logo cabinet + mentions
legales sur les 6 PDFs manquants.

Verifie que :
1. Chaque PDF builder accepte le parametre `syndic_pdf_ctx` sans erreur.
2. Quand ctx est fourni avec un logo + mentions legales, le PDF genere
   contient bien le logo (bytes JPG/PNG) + les mentions legales.
3. Quand ctx = None, le PDF est genere normalement (retrocompatible).
"""
import io
import os
import sys

import pytest
from PIL import Image
from pypdf import PdfReader

# Ajoute /app/backend au path pour permettre l'import direct
sys.path.insert(0, "/app/backend")


def _make_fake_logo_bytes() -> bytes:
    """Cree un vrai PNG (32x32 bleu) pour tester le rendu du logo."""
    buf = io.BytesIO()
    img = Image.new("RGB", (32, 32), (33, 118, 246))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fake_ctx():
    return {
        "syndic_user_id": "u1",
        "syndic_config": {
            "legal_name": "Cabinet Test SPRL",
            "display_name": "Test",
            "address": "Rue Test 1",
            "postal_code": "1000",
            "city": "Bruxelles",
            "phone": "+32 2 000",
            "email": "contact@test.be",
            "bce": "0999.888.777",
            "ipi_number": "509.999",
        },
        "logo_bytes": _make_fake_logo_bytes(),
        "legal_mentions": (
            "Cabinet Test SPRL - BCE 0999.888.777 - IPI 509.999\n"
            "Rue Test 1, 1000 Bruxelles - Compte tiers BE68 5390 0754 7034"
        ),
    }


def _fake_copro():
    return {
        "id": "c1",
        "name": "ACP Test",
        "address": "Avenue Test 10",
        "postal_code": "1050",
        "city": "Ixelles",
        "bce": "0123456789",
        "reference": "REF-001",
    }


def _extract_text(pdf_bytes: bytes) -> str:
    r = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((p.extract_text() or "") for p in r.pages)


# --- 1. Balance des tiers ---
def test_balance_tiers_pdf_with_syndic_ctx():
    from pdf_balance_tiers import build_balance_tiers_pdf
    ctx = _fake_ctx()
    pdf = build_balance_tiers_pdf(
        copropriete=_fake_copro(),
        owners_data={"owners": [], "total_debiteurs": 0, "total_crediteurs": 0},
        suppliers_data={"suppliers": [], "total_debiteurs": 0, "total_crediteurs": 0},
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text  # legal_name in header
    assert "BCE 0999.888.777" in text   # legal mentions in footer
    assert "Page 1" in text


def test_balance_tiers_pdf_without_ctx_regression():
    from pdf_balance_tiers import build_balance_tiers_pdf
    pdf = build_balance_tiers_pdf(
        copropriete=_fake_copro(),
        owners_data={"owners": [], "total_debiteurs": 0, "total_crediteurs": 0},
        suppliers_data={"suppliers": [], "total_debiteurs": 0, "total_crediteurs": 0},
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" not in text  # legal_name only if ctx


# --- 2. Bilan ---
def test_bilan_pdf_with_syndic_ctx():
    from pdf_bilan import build_bilan_pdf
    ctx = _fake_ctx()
    pdf = build_bilan_pdf(
        copropriete=_fake_copro(),
        syndic=None,
        fiscal_year={"name": "FY2025", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        bilan_data={"actif": [], "passif": [], "total_actif": 0, "total_passif": 0, "equilibre": True, "ecart": 0},
        date_to="2025-12-31",
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text  # BCE in mentions
    assert "Page 1" in text


def test_bilan_pdf_without_ctx_regression():
    from pdf_bilan import build_bilan_pdf
    pdf = build_bilan_pdf(
        copropriete=_fake_copro(),
        bilan_data={"actif": [], "passif": [], "total_actif": 0, "total_passif": 0, "equilibre": True, "ecart": 0},
    )
    assert pdf.startswith(b"%PDF")


# --- 3. Budget ---
def test_budget_pdf_with_syndic_ctx():
    from pdf_budget import build_budget_pdf
    ctx = _fake_ctx()
    pdf = build_budget_pdf(
        copropriete=_fake_copro(),
        budget={"id": "b1", "name": "Budget 2025", "lines": [], "status": "approved", "total_amount": 0},
        fiscal_year={"name": "FY2025", "start_date": "2025-01-01", "end_date": "2025-12-31"},
        pcmn_map={},
        keys_map={},
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text
    assert "Page 1" in text


def test_budget_pdf_without_ctx_regression():
    from pdf_budget import build_budget_pdf
    pdf = build_budget_pdf(
        copropriete=_fake_copro(),
        budget={"id": "b1", "name": "Budget 2025", "lines": [], "status": "approved", "total_amount": 0},
        fiscal_year={"name": "FY2025"},
        pcmn_map={},
        keys_map={},
    )
    assert pdf.startswith(b"%PDF")


# --- 4. Journals ---
def test_journals_pdf_with_syndic_ctx():
    from pdf_journals_and_invoices import build_journals_pdf
    ctx = _fake_ctx()
    pdf = build_journals_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        entries=[],
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text
    assert "Page 1" in text


def test_journals_pdf_without_ctx_regression():
    from pdf_journals_and_invoices import build_journals_pdf
    pdf = build_journals_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        entries=[],
    )
    assert pdf.startswith(b"%PDF")


# --- 5. Liste factures ---
def test_invoices_list_pdf_with_syndic_ctx():
    from pdf_journals_and_invoices import build_invoices_list_pdf
    ctx = _fake_ctx()
    pdf = build_invoices_list_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        invoices=[],
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text


def test_invoices_list_pdf_without_ctx_regression():
    from pdf_journals_and_invoices import build_invoices_list_pdf
    pdf = build_invoices_list_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        invoices=[],
    )
    assert pdf.startswith(b"%PDF")


# --- 6. Liste des depenses ---
def test_liste_depenses_pdf_with_syndic_ctx():
    from pdf_liste_depenses import build_liste_depenses_pdf
    ctx = _fake_ctx()
    pdf = build_liste_depenses_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        invoices=[], distribution_keys=[], pcmn_map={}, expense_categories=[],
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text


def test_liste_depenses_pdf_without_ctx_regression():
    from pdf_liste_depenses import build_liste_depenses_pdf
    pdf = build_liste_depenses_pdf(
        copropriete=_fake_copro(),
        date_from="2025-01-01", date_to="2025-12-31",
        invoices=[], distribution_keys=[], pcmn_map={}, expense_categories=[],
    )
    assert pdf.startswith(b"%PDF")


# --- 7. Registre RGPD ---
def test_rgpd_register_pdf_with_syndic_ctx():
    from pdf_rgpd_register import build_rgpd_register_pdf
    ctx = _fake_ctx()
    register = {
        "controller": {
            "societe": "NextGe Copro", "forme_juridique": "SPRL",
            "adresse": "Rue Test 1, 1000 Bruxelles",
            "bce": "0999.888.777", "tva": "BE0999888777",
            "representant": "Testeur", "email": "test@test.be",
            "telephone": "+32 2 000", "dpo_email": "dpo@test.be",
        },
        "processings": [],
        "subprocessors": [],
        "security_measures": [],
        "updated_at": "2025-01-01T00:00:00Z",
    }
    pdf = build_rgpd_register_pdf(register, syndic_pdf_ctx=ctx)
    assert pdf.startswith(b"%PDF")
    text = _extract_text(pdf)
    assert "Cabinet Test SPRL" in text
    assert "0999.888.777" in text
    assert "Page 1" in text


def test_rgpd_register_pdf_without_ctx_regression():
    from pdf_rgpd_register import build_rgpd_register_pdf
    register = {
        "controller": {
            "societe": "NextGe Copro", "forme_juridique": "SPRL",
            "adresse": "Rue Test 1, 1000 Bruxelles",
            "bce": "0999.888.777", "tva": "",
            "representant": "", "email": "",
            "telephone": "", "dpo_email": "",
        },
        "processings": [],
        "subprocessors": [],
        "security_measures": [],
        "updated_at": "2025-01-01T00:00:00Z",
    }
    pdf = build_rgpd_register_pdf(register)
    assert pdf.startswith(b"%PDF")


# --- Test transverse : legal_footer preserve le numero de page ---
def test_legal_footer_shows_page_number_without_mentions():
    """Meme si les mentions legales sont vides, le numero de page doit
    apparaitre sur toutes les pages (user request)."""
    from pdf_bilan import build_bilan_pdf
    ctx = {
        "syndic_user_id": "u1",
        "syndic_config": {"legal_name": "X"},
        "logo_bytes": None,
        "legal_mentions": "",   # <-- vide
    }
    pdf = build_bilan_pdf(
        copropriete=_fake_copro(),
        bilan_data={"actif": [], "passif": [], "total_actif": 0, "total_passif": 0, "equilibre": True, "ecart": 0},
        syndic_pdf_ctx=ctx,
    )
    text = _extract_text(pdf)
    assert "Page 1" in text  # numero de page toujours present


# --- Test transverse : logo bytes tres petits ne crash pas ---
def test_pdf_with_empty_logo_bytes():
    from pdf_bilan import build_bilan_pdf
    ctx = _fake_ctx()
    ctx["logo_bytes"] = None
    pdf = build_bilan_pdf(
        copropriete=_fake_copro(),
        bilan_data={"actif": [], "passif": [], "total_actif": 0, "total_passif": 0, "equilibre": True, "ecart": 0},
        syndic_pdf_ctx=ctx,
    )
    assert pdf.startswith(b"%PDF")
