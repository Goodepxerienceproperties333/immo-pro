"""Iter35: When invoice has no distribution_lines, auto-split by tantiemes (lot.quotity)."""
import sys
import os
sys.path.insert(0, '/app/backend')

from pdf_decompte import build_decompte_pdf  # noqa: E402


def _pdf_text(pdf_bytes):
    import io
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _base():
    return dict(
        owner={'id': 'O1', 'name': 'Test Owner', 'vcs_code': '+++100/1234/56782+++'},
        copropriete={'id': 'C1', 'name': 'ACP',
                     'bank_accounts': [{'iban': 'BE00', 'is_default': True}]},
        fiscal_year={'id': 'FY1', 'name': '2026', 'start_date': '2026-01-01',
                     'end_date': '2026-12-31', 'status': 'closed'},
        owner_lots=[{'id': 'L1', 'number': 'A1', 'quotity': 1000.0}],
        all_lots=[
            {'id': 'L1', 'number': 'A1', 'quotity': 1000.0},
            {'id': 'L2', 'number': 'A2', 'quotity': 2000.0},
            {'id': 'L3', 'number': 'A3', 'quotity': 2000.0},
        ],
        distribution_keys=[],  # no key defined
        fund_calls=[], payments=[],
        expense_accounts_map={'61060': 'Entretien jardins'},
    )


def test_no_distribution_lines_auto_split_by_tantiemes():
    """Invoice without distribution_lines & without distribution_key is split by lot quotity."""
    args = _base()
    args['invoices'] = [{
        'id': 'I1', 'date': '2026-06-15', 'supplier': 'Garden srl',
        'description': 'Entretien', 'total_amount': 1500.0,
        'account_number': '61060', 'distribution_key_id': None,
        'occupant_pct': 100,
        'distribution_lines': [],
    }]
    pdf = build_decompte_pdf(**args)
    text = _pdf_text(pdf)
    # The owner L1 should get 1000/(1000+2000+2000) = 20% of 1500 = 300 EUR
    assert 'Garden srl' in text
    # Sum total of all quotities should appear ("5000")
    assert '5000.00' in text or '5 000' in text
    # Owner share = 300 EUR
    assert '300,00' in text or '300.00' in text
    # Auto-split flag
    assert 'repartition auto' in text or 'auto (tantiemes)' in text
    # NOT the empty message
    assert 'Aucune charge ne vous concerne' not in text


def test_no_distribution_lines_uses_explicit_key_quotities():
    """Invoice without distribution_lines but WITH a distribution_key_id uses that key's lots."""
    args = _base()
    args['distribution_keys'] = [{
        'id': 'KEY-CHAUF', 'name': 'Chauffage',
        # Only 2 lots in this key, owner's L1 gets 30%
        'lots': [
            {'lot_id': 'L1', 'share': 300},
            {'lot_id': 'L2', 'share': 700},
        ],
    }]
    args['invoices'] = [{
        'id': 'I1', 'date': '2026-06-15', 'supplier': 'Heating Co',
        'description': 'Chaudiere', 'total_amount': 1000.0,
        'account_number': '614000', 'distribution_key_id': 'KEY-CHAUF',
        'occupant_pct': 0,
        'distribution_lines': [],
    }]
    pdf = build_decompte_pdf(**args)
    text = _pdf_text(pdf)
    # Owner L1 = 300/1000 * 1000 = 300 EUR
    assert 'Heating Co' in text
    assert '300,00' in text or '300.00' in text
    # Should use the explicit key name, not "Tantiemes par defaut"
    assert 'Chauffage' in text


def test_explicit_distribution_lines_priority():
    """When distribution_lines exist, they take priority over the key/quotity fallback."""
    args = _base()
    args['distribution_keys'] = [{
        'id': 'K1', 'name': 'Gen', 'lots': [{'lot_id': 'L1', 'share': 1000}],
    }]
    args['invoices'] = [{
        'id': 'I1', 'date': '2026-06-15', 'supplier': 'Sup',
        'description': 'X', 'total_amount': 1000.0,
        'account_number': '61060', 'distribution_key_id': 'K1',
        'occupant_pct': 0,
        # Manually set L1 to 250 (not the full 1000)
        'distribution_lines': [{'lot_id': 'L1', 'amount': 250.0}],
    }]
    pdf = build_decompte_pdf(**args)
    text = _pdf_text(pdf)
    # Should respect explicit 250, not auto-recompute to 1000
    assert '250,00' in text or '250.00' in text
    # Should NOT show the "auto split" flag
    assert 'repartition auto' not in text
