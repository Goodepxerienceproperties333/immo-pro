"""iter90dv - PDF Situation de compte : distinction "Paiement recu" vs "Remboursement effectue".

User bug (Feb 2026):
> "lors d'un paiement a un proprietaire il faut mentionner
> 'remboursement effectue' au lieu de paiement recu"

Contexte : le PDF Situation de compte utilisait "Paiement recu :" pour TOUS
les mouvements de journal FI/BANK sur le compte tier proprietaire, meme
quand le sens etait un DEBIT (= la copro rembourse le proprio, ex trop-percu).

Fix : `_humanize_label` accepte debit/credit et choisit :
- CREDIT > DEBIT sur tier proprio : "Paiement recu" (proprio a paye la copro)
- DEBIT > CREDIT sur tier proprio : "Remboursement effectue" (copro a rembourse le proprio)
"""
from pdf_situation_compte import _humanize_label


def test_payment_received_from_owner_labeled_correctly():
    """CREDIT sur tier proprio = argent recu du proprio -> 'Paiement recu'."""
    label = _humanize_label(
        description="Matexi",
        reference="RE-2026-001",
        journal_type="FI",
        debit=0,
        credit=1000.0,
    )
    assert "Paiement recu" in label, label
    assert "Remboursement" not in label, label


def test_refund_to_owner_labeled_as_reimbursement():
    """DEBIT sur tier proprio = argent verse au proprio -> 'Remboursement effectue'."""
    label = _humanize_label(
        description="Matexi",
        reference="RE-2026-002",
        journal_type="FI",
        debit=9000.0,
        credit=0,
    )
    assert "Remboursement effectue" in label, label
    assert "Paiement recu" not in label, label


def test_bank_journal_type_treated_same_as_fi():
    """Journal BANK doit se comporter comme FI (meme distinction)."""
    label_recu = _humanize_label(
        description="Matexi", reference="", journal_type="BANK",
        debit=0, credit=500,
    )
    label_remb = _humanize_label(
        description="Matexi", reference="", journal_type="BANK",
        debit=500, credit=0,
    )
    assert "Paiement recu" in label_recu
    assert "Remboursement effectue" in label_remb


def test_backward_compat_no_debit_credit_defaults_to_payment_received():
    """Si debit/credit non fournis (compat retro), defaut = paiement recu."""
    label = _humanize_label(
        description="Matexi",
        reference="RE-2026-001",
        journal_type="FI",
    )
    assert "Paiement recu" in label


def test_other_journal_types_unchanged():
    """VE, AC, OD, AN ne sont pas impactes."""
    assert "Appel de fonds" in _humanize_label(
        "Provisions", "", "VE", debit=1000, credit=0,
    )
    assert "Facture" in _humanize_label(
        "Fournisseur X", "", "AC", debit=0, credit=500,
    )
    assert "Operation" in _humanize_label(
        "Ajustement", "", "OD", debit=100, credit=0,
    )
    assert "Solde reporte" in _humanize_label(
        "Report 2025", "", "AN", debit=0, credit=0,
    )
