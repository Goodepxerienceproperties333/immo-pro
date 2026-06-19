"""Tests for iter34: Preview mode for Decompte PDF."""
import sys
import os

sys.path.insert(0, '/app/backend')

from pdf_decompte import build_decompte_pdf  # noqa: E402


def _pdf_text(pdf_bytes):
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def _base_args():
    return dict(
        owner={'id': 'O1', 'name': 'Test', 'vcs_code': '+++100/1234/56782+++'},
        copropriete={'id': 'C1', 'name': 'ACP', 'bank_accounts': [{'iban': 'BE00', 'is_default': True}]},
        fiscal_year={'id': 'FY1', 'name': '2026', 'start_date': '2026-01-01',
                     'end_date': '2026-12-31', 'status': 'open'},
        owner_lots=[{'id': 'L1', 'number': 'A1', 'quotity': 100}],
        all_lots=[{'id': 'L1', 'number': 'A1', 'quotity': 100}],
        invoices=[{'id': 'I1', 'date': '2026-03-01', 'supplier': 'Sup',
                   'description': 'T1', 'total_amount': 100.0,
                   'account_number': '612000', 'distribution_key_id': 'K1',
                   'occupant_pct': 50, 'proprietaire_pct': 50,
                   'distribution_lines': [{'lot_id': 'L1', 'amount': 100.0}]}],
        distribution_keys=[{'id': 'K1', 'name': 'Gen',
                            'lots': [{'lot_id': 'L1', 'share': 100}]}],
        fund_calls=[], payments=[],
        expense_accounts_map={'612000': 'Energie'},
    )


def test_preview_mode_adds_watermark():
    args = _base_args()
    pdf_preview = build_decompte_pdf(**args, preview=True)
    pdf_normal = build_decompte_pdf(**args, preview=False)

    assert isinstance(pdf_preview, bytes)
    assert isinstance(pdf_normal, bytes)
    # Preview PDF is bigger because of the watermark drawn on each page
    assert len(pdf_preview) > len(pdf_normal)

    text_preview = _pdf_text(pdf_preview)
    text_normal = _pdf_text(pdf_normal)

    # Watermark text must appear in preview, not in normal
    assert 'APERCU' in text_preview
    assert 'NON DEFINITIF' in text_preview
    assert 'APERCU' not in text_normal
    assert 'NON DEFINITIF' not in text_normal


def test_preview_default_is_false():
    """build_decompte_pdf default is preview=False (no watermark)."""
    args = _base_args()
    pdf = build_decompte_pdf(**args)
    text = _pdf_text(pdf)
    assert 'APERCU' not in text


def test_preview_with_closed_fy_still_works():
    """Even on a closed FY, preview=True is valid (just adds watermark)."""
    args = _base_args()
    args['fiscal_year']['status'] = 'closed'
    pdf = build_decompte_pdf(**args, preview=True)
    text = _pdf_text(pdf)
    assert 'APERCU' in text
