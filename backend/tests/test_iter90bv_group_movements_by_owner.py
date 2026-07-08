"""iter90bv - Regroupement des lignes multi-lots dans la situation de compte proprietaire.

Regle metier : quand un proprietaire possede plusieurs lots dans une ACP, un meme
appel de fonds (VE) genere N lignes debit sur son compte tier (une par lot).
Pour la lisibilite proprietaire non-comptable, le helper `_group_movements_by_owner`
doit cumuler ces lignes en UNE SEULE par (ref, date, account, description, journal).
"""
from routes.reports import _group_movements_by_owner


def test_group_movements_merges_multi_lot_lines_for_same_fund_call():
    """3 lots du meme proprietaire dans la meme VE => 1 ligne cumulee."""
    movements = [
        # Owner A, lot 1
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1 2026",
            "reference": "FC-2026-Q1",
            "account_number": "41010001",
            "debit": 100.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        # Owner A, lot 2 - meme VE, meme description, meme compte tier
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1 2026",
            "reference": "FC-2026-Q1",
            "account_number": "41010001",
            "debit": 200.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        # Owner A, lot 3
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1 2026",
            "reference": "FC-2026-Q1",
            "account_number": "41010001",
            "debit": 150.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
    ]
    result = _group_movements_by_owner(movements)
    assert len(result) == 1, f"Attendu 1 ligne apres fusion, obtenu {len(result)}"
    assert result[0]["debit"] == 450.00
    assert result[0]["credit"] == 0.0
    assert result[0]["description"] == "[VE] Appel de provisions - Q1 2026"
    assert result[0]["reference"] == "FC-2026-Q1"


def test_group_movements_keeps_different_accounts_separate():
    """Meme fund_call, mais provisions vs reserve => 2 lignes distinctes."""
    movements = [
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1",
            "reference": "FC-Q1",
            "account_number": "41010001",  # provisions
            "debit": 300.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        {
            "date": "2026-01-01",
            "description": "[VE] Appel fonds de reserve - Q1",
            "reference": "FC-Q1",
            "account_number": "41000001",  # reserve
            "debit": 50.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
    ]
    result = _group_movements_by_owner(movements)
    assert len(result) == 2
    accs = sorted([r["account_number"] for r in result])
    assert accs == ["41000001", "41010001"]


def test_group_movements_keeps_different_fund_calls_separate():
    """Q1 vs Q2 sur meme compte tier => 2 lignes distinctes (references differentes)."""
    movements = [
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1",
            "reference": "FC-Q1",
            "account_number": "41010001",
            "debit": 100.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        {
            "date": "2026-04-01",
            "description": "[VE] Appel de provisions - Q2",
            "reference": "FC-Q2",
            "account_number": "41010001",
            "debit": 100.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
    ]
    result = _group_movements_by_owner(movements)
    assert len(result) == 2
    refs = sorted([r["reference"] for r in result])
    assert refs == ["FC-Q1", "FC-Q2"]


def test_group_movements_preserves_payment_credits():
    """Un paiement FI (credit) ne doit pas etre fusionne avec un appel VE (debit)."""
    movements = [
        {
            "date": "2026-01-01",
            "description": "[VE] Appel de provisions - Q1",
            "reference": "FC-Q1",
            "account_number": "41010001",
            "debit": 300.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        {
            "date": "2026-01-15",
            "description": "[FI] Paiement Q1",
            "reference": "FI-1",
            "account_number": "41010001",
            "debit": 0.0,
            "credit": 300.00,
            "journal_type": "FI",
        },
    ]
    result = _group_movements_by_owner(movements)
    assert len(result) == 2
    total_debit = sum(r["debit"] for r in result)
    total_credit = sum(r["credit"] for r in result)
    assert total_debit == 300.00
    assert total_credit == 300.00


def test_group_movements_empty_returns_empty():
    assert _group_movements_by_owner([]) == []


def test_group_movements_fallback_on_entry_id_when_no_reference():
    """Si reference vide, la cle doit utiliser entry_id comme secours."""
    movements = [
        {
            "date": "2026-01-01",
            "description": "Appel",
            "reference": "",
            "entry_id": "e1",
            "account_number": "41010001",
            "debit": 100.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        {
            "date": "2026-01-01",
            "description": "Appel",
            "reference": "",
            "entry_id": "e1",
            "account_number": "41010001",
            "debit": 200.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
        # Entree distincte (entry_id different)
        {
            "date": "2026-01-01",
            "description": "Appel",
            "reference": "",
            "entry_id": "e2",
            "account_number": "41010001",
            "debit": 999.00,
            "credit": 0.0,
            "journal_type": "VE",
        },
    ]
    result = _group_movements_by_owner(movements)
    assert len(result) == 2, "Deux entries distincts doivent rester separes"
    debits = sorted([r["debit"] for r in result])
    assert debits == [300.00, 999.00]
