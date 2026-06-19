"""Tests for iter33: Refonte du PDF Decompte selon le modele Finlead
- Structure: Lot -> Cle de repartition -> Compte (nature)
- Colonnes: Designation | Quotites | Montant a repartir | Part proprietaire | Part occupant
- Section dediee 'Recapitulatif des charges locataire'
"""
import sys
import os
import pytest

sys.path.insert(0, '/app/backend')

from pdf_decompte import build_decompte_pdf  # noqa: E402


@pytest.fixture
def base_data():
    owner = {
        'id': 'OWNER-1',
        'name': 'Dubois Jean',
        'address': 'Avenue des Tilleuls 25',
        'postal_code': '1180',
        'city': 'Uccle',
        'country': 'Belgique',
        'vcs_code': '+++100/1234/56782+++',
        'vcs_digits': '100123456782',
    }
    copro = {
        'id': 'COPRO-1',
        'name': 'Demo - Les Tilleuls',
        'address': 'Avenue des Tilleuls 25',
        'postal_code': '1180',
        'city': 'Uccle',
        'bce': '0876.543.210',
        'bank_accounts': [
            {'iban': 'BE68539007547034', 'label': 'Compte courant', 'default': True}
        ],
    }
    fy = {
        'id': 'FY-1',
        'name': 'Exercice 2026',
        'start_date': '2026-01-01',
        'end_date': '2026-12-31',
        'status': 'closed',
    }
    owner_lots = [
        {'id': 'LOT-A002', 'number': 'A002', 'description': 'App 3ch 1er etage',
         'quotity': 1200},
    ]
    all_lots = [
        {'id': 'LOT-A001', 'number': 'A001', 'quotity': 850},
        {'id': 'LOT-A002', 'number': 'A002', 'quotity': 1200},
        {'id': 'LOT-A003', 'number': 'A003', 'quotity': 900},
    ]
    distribution_keys = [
        {
            'id': 'KEY-G',
            'name': 'Charges communes generales',
            'copropriete_id': 'COPRO-1',
            'lots': [
                {'lot_id': 'LOT-A001', 'lot_number': 'A001', 'share': 850},
                {'lot_id': 'LOT-A002', 'lot_number': 'A002', 'share': 1200},
                {'lot_id': 'LOT-A003', 'lot_number': 'A003', 'share': 900},
            ],
        },
    ]
    return owner, copro, fy, owner_lots, all_lots, distribution_keys


def _make_inv(inv_id, total, acc, key, occ_pct, owner_amt):
    return {
        'id': inv_id,
        'date': '2026-03-15',
        'supplier': 'Test Supplier',
        'description': 'Test',
        'total_amount': total,
        'account_number': acc,
        'distribution_key_id': key,
        'occupant_pct': occ_pct,
        'proprietaire_pct': 100 - occ_pct,
        'distribution_lines': [
            {'lot_id': 'LOT-A002', 'amount': owner_amt},
        ],
    }


def _pdf_text(pdf_bytes):
    """Lightweight text extraction from PDF for assertion."""
    import io
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except ImportError:
        return pdf_bytes.decode('latin-1', errors='ignore')


def test_pdf_grouping_lot_key_account(base_data):
    """Verifie la hierarchie Lot -> Cle -> Compte dans le PDF."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [
        _make_inv('I1', 265.0, '614000', 'KEY-G', 0, 50.08),
        _make_inv('I2', 540.0, '612000', 'KEY-G', 100, 102.05),
    ]
    nature_map = {'614000': 'Honoraires syndic', '612000': 'Energie'}
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=[], payments=[], expense_accounts_map=nature_map,
    )
    assert isinstance(pdf, bytes) and len(pdf) > 1000

    text = _pdf_text(pdf)
    # Headers
    assert 'Designation' in text
    assert 'Quotites' in text or 'Quotités' in text
    assert 'Montant' in text and 'repartir' in text
    assert 'Part' in text and ('proprietaire' in text or 'occupant' in text)

    # Lot header
    assert 'Lot: A002' in text
    assert 'Prorata' in text  # "(Prorata: 365 / 365 jours)"

    # Cle name
    assert 'Charges communes generales' in text

    # Account names (PCMN labels)
    assert 'Honoraires syndic' in text
    assert 'Energie' in text

    # Totals labels
    assert 'Total Lot' in text
    assert 'Totaux generaux' in text


def test_pdf_quotites_displayed_correctly(base_data):
    """Verifie que les quotites s'affichent comme 'lot_q / total_q' (et non '0.00/0.00')."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [_make_inv('I1', 265.0, '614000', 'KEY-G', 0, 50.08)]
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=[], payments=[], expense_accounts_map={'614000': 'Honoraires'},
    )
    text = _pdf_text(pdf)
    # Lot A002 quotity = 1200, sum = 850+1200+900 = 2950
    # Must contain "1200.00 / 2950.00" (formatted with space thousand sep)
    assert '1200.00' in text or '1 200.00' in text
    assert '2950.00' in text or '2 950.00' in text
    # Must NOT contain "0.00 / 0.00" as a Quotites line
    # (acceptable if appears as an amount, so check the pattern of zero/zero)
    assert '0.00 / 0.00' not in text


def test_pdf_recap_locataire_section_present_when_occupant(base_data):
    """Verifie que la section 'Recapitulatif des charges locataire' est presente
    quand au moins une facture a un occupant_pct > 0."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [
        _make_inv('I1', 540.0, '612000', 'KEY-G', 100, 102.05),  # 100% occ
        _make_inv('I2', 280.0, '612200', 'KEY-G', 75, 52.94),    # 75% occ -> 39.70 occ
    ]
    nature_map = {'612000': 'Energie', '612200': 'Eau'}
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=[], payments=[], expense_accounts_map=nature_map,
    )
    text = _pdf_text(pdf)
    assert '2. Recapitulatif des charges locataire' in text
    assert 'TOTAL A REFACTURER AU LOCATAIRE' in text
    # Per-account labels should be visible
    assert 'Energie' in text
    assert 'Eau' in text
    # RD du 12/07/2024 note
    assert '12/07/2024' in text or 'charges locatives' in text.lower()


def test_pdf_recap_locataire_hidden_when_no_occupant(base_data):
    """Quand aucune facture n'a d'occupant_pct, la section recap doit etre masquee."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [_make_inv('I1', 1500.0, '614000', 'KEY-G', 0, 283.55)]
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=[], payments=[], expense_accounts_map={'614000': 'Honoraires'},
    )
    text = _pdf_text(pdf)
    assert '2. Recapitulatif des charges locataire' not in text
    assert 'TOTAL A REFACTURER AU LOCATAIRE' not in text


def test_pdf_columns_show_part_proprietaire_and_occupant(base_data):
    """Verifie la presence des colonnes Part proprietaire et Part occupant
    selon le modele Finlead (PAS Occupant/Proprio condenses)."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [_make_inv('I1', 540.0, '612000', 'KEY-G', 100, 102.05)]
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=[], payments=[], expense_accounts_map={'612000': 'Energie'},
    )
    text = _pdf_text(pdf)
    # Long-form Finlead-style labels
    assert 'Part' in text
    assert 'proprietaire' in text
    assert 'occupant' in text
    # PCMN code visible
    assert '612000' in text


def test_pdf_section_numbering(base_data):
    """Verifie la numerotation des sections : 1. Detail -> 2. Recap loc -> 3. Appels -> 4. Paiements."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    invoices = [_make_inv('I1', 540.0, '612000', 'KEY-G', 100, 102.05)]
    fund_calls = [{
        'id': 'FC1', 'date': '2026-01-01', 'name': 'T1', 'call_type': 'provisions',
        'due_date': '2026-01-31',
        'distribution': [{'owner_id': 'OWNER-1', 'amount': 188.98, 'paid': False}],
    }]
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks,
        fund_calls=fund_calls, payments=[], expense_accounts_map={'612000': 'Energie'},
    )
    text = _pdf_text(pdf)
    assert '1. Detail de vos charges' in text
    assert '2. Recapitulatif des charges locataire' in text
    assert '3. Vos appels de fonds' in text


def test_pdf_multiple_distribution_keys(base_data):
    """Verifie que plusieurs cles dans le meme lot s'affichent correctement avec leurs propres totaux."""
    owner, copro, fy, ow_lots, all_lots, dks = base_data
    dks2 = dks + [{
        'id': 'KEY-CH',
        'name': 'Chauffage',
        'copropriete_id': 'COPRO-1',
        'lots': [
            {'lot_id': 'LOT-A001', 'lot_number': 'A001', 'share': 100},
            {'lot_id': 'LOT-A002', 'lot_number': 'A002', 'share': 200},
        ],
    }]
    invoices = [
        _make_inv('I1', 1000.0, '614000', 'KEY-G', 0, 200.0),
        _make_inv('I2', 600.0, '612100', 'KEY-CH', 100, 400.0),
    ]
    pdf = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=ow_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=dks2,
        fund_calls=[], payments=[],
        expense_accounts_map={'614000': 'Honoraires', '612100': 'Chauffage'},
    )
    text = _pdf_text(pdf)
    # Both keys visible
    assert 'Charges communes generales' in text
    assert 'Chauffage' in text
    # Both lot quotities visible
    assert '1200.00' in text or '1 200.00' in text  # KEY-G lot share
    assert '200.00' in text  # KEY-CH lot share
