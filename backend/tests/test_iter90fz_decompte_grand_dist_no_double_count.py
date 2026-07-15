"""
iter90fz : PDF decompte - correction du double-comptage "Montant a repartir".

Ticket utilisateur (Feb 2026, PROD Acacia TER) :
> "les decomptes divergents car il y a une erreur de calcul tu additions
> les frais repartis par lots alors qu'il ne s'agit pas d'une addition
> le total des depenses est de 11946.43 EUR donc tu ne calcule pas bien
> les decomptes - Revoir ta methode"

Contexte : un proprietaire ayant PLUSIEURS lots (ex: ABED - 3 lots 202,
C08, pe07) voyait dans son decompte un "Totaux generaux" de la colonne
"Montant a repartir" egal a la somme des sous-totaux PAR LOT. Or un
meme invoice touchant les 3 lots etait donc compte 3 fois dans le
grand total.

Cause : `grand_dist = sum(t[1] for t in lot_grand_totals)` (ligne 763
avant fix). Chaque `lot_grand_totals[i][1]` = `lot_total_dist` du lot i
= somme des inv_total sur les invoices touchant ce lot -> le meme
invoice compte N fois si N lots touches.

Cette erreur de reference n'affectait PAS "Part proprietaire" ni "Part
occupant" (chaque lot a sa quote-part fractionnelle qui s'additionne
correctement). Elle affectait uniquement la colonne "Montant a repartir"
qui doit representer la valeur totale des invoices HORS PROPRIETAIRE
(reference d'apercu au syndic).

Fix iter90fz : dedup au niveau OWNER via `owner_seen_inv_ids` set +
`owner_grand_dist_unique`. Aussi dedup au niveau LOT (`lot_seen_inv_ids`
+ `lot_dist_unique`) pour la coherence des sous-totaux lot.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_decompte import build_decompte_pdf  # noqa: E402


def test_multi_lot_owner_grand_dist_not_double_counted():
    """Owner avec 3 lots, invoice touchant les 3 -> grand_dist = inv_total
    UNE seule fois, pas 3 fois."""
    owner = {
        "id": "o-test", "name": "ABED Test",
        "email": "abed@test.local",
        "vcs_code": "+++123/4567/89012+++",
        "address": "1 rue Test", "postal_code": "1000", "city": "Bruxelles",
        "country": "Belgique",
    }
    copropriete = {
        "id": "acp-test", "name": "ACP Test",
        "address": "10 rue ACP", "postal_code": "1000", "city": "Bruxelles",
    }
    fiscal_year = {
        "id": "fy-1", "name": "2025",
        "start_date": "2025-01-01", "end_date": "2025-12-31",
    }
    # Owner possede 3 lots
    owner_lots = [
        {"id": "l1", "number": "202", "description": "Appt", "quotity": 500.0},
        {"id": "l2", "number": "C08", "description": "Cave", "quotity": 50.0},
        {"id": "l3", "number": "pe07", "description": "Parking", "quotity": 100.0},
    ]
    all_lots = owner_lots + [
        {"id": "l4", "number": "101", "description": "Autre", "quotity": 350.0},
    ]
    # Une SEULE invoice de 1000 EUR touchant les 3 lots de l'owner via une
    # cle de repartition standard (tantiemes).
    invoice_amount = 1000.0
    invoices = [
        {
            "id": "inv-1",
            "date": "2025-06-15",
            "supplier": "JAG SPRL",
            "description": "Nettoyage",
            "number": "FA-2025-001",
            "total_amount": invoice_amount,
            "occupant_pct": 100,  # 100% locataire
            "account_number": "610100",
            "distribution_key_id": "",
            "distribution_lines": [
                # Chaque lot recoit sa quote-part de 1000 selon tantiemes
                {"lot_id": "l1", "lot_number": "202", "amount": 500.0},
                {"lot_id": "l2", "lot_number": "C08", "amount": 50.0},
                {"lot_id": "l3", "lot_number": "pe07", "amount": 100.0},
                {"lot_id": "l4", "lot_number": "101", "amount": 350.0},
            ],
        },
    ]
    distribution_keys = []

    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=distribution_keys,
        fund_calls=[], payments=[], preview=True,
    )
    assert pdf_bytes and len(pdf_bytes) > 1000

    # Parse le PDF pour verifier le "Totaux generaux" de la colonne
    # "Montant a repartir".
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    # Trouve la ligne "Totaux generaux". La colonne Montant a repartir
    # est LA VALEUR ATTENDUE : 1000,00 EUR (une seule invoice de 1000
    # touchant les 3 lots de l'owner, doit apparaitre UNE SEULE fois
    # dans le grand total, pas 650 = somme des shares ni 3000 = 3x1000).
    assert "1 000,00" in text or "1000,00" in text, (
        f"REGRESSION iter90fz : le grand total 'Montant a repartir' devrait "
        f"contenir 1 000,00 EUR (invoice unique), pas la somme multi-comptee. "
        f"Extrait PDF :\n{text[:2000]}"
    )
    # Le double-comptage precedent aurait donne 3 * 1000 = 3000. Verifier
    # que cette valeur erronee n'apparait PAS.
    assert "3 000,00" not in text and "3000,00" not in text, (
        f"REGRESSION iter90fz : invoice comptee 3 fois dans le grand total. "
        f"Extrait PDF :\n{text[:2000]}"
    )


def test_multi_lot_owner_prop_and_occ_shares_are_correct():
    """La colonne Part proprietaire + Part occupant reste correcte comme
    somme de fractions (regression check sur les colonnes NON impactees
    par le fix iter90fz)."""
    owner = {
        "id": "o-test", "name": "ABED Test",
        "email": "abed@test.local", "vcs_code": "+++123+++",
        "address": "1 rue Test", "postal_code": "1000", "city": "Bruxelles",
        "country": "Belgique",
    }
    copropriete = {"id": "acp-test", "name": "ACP Test", "address": "-",
                   "postal_code": "1000", "city": "Bruxelles"}
    fiscal_year = {"id": "fy-1", "name": "2025",
                   "start_date": "2025-01-01", "end_date": "2025-12-31"}
    owner_lots = [
        {"id": "l1", "number": "202", "quotity": 500.0},
        {"id": "l2", "number": "C08", "quotity": 50.0},
    ]
    all_lots = owner_lots + [
        {"id": "l3", "number": "101", "quotity": 450.0},
    ]
    # Invoice 1000, 30% occupant, distribution lines :
    # l1=500, l2=50 (owner) + l3=450 (autre owner)
    invoices = [
        {
            "id": "inv-1", "date": "2025-06-15",
            "supplier": "JAG", "description": "Nettoyage",
            "total_amount": 1000.0, "occupant_pct": 30,
            "account_number": "610100",
            "distribution_lines": [
                {"lot_id": "l1", "amount": 500.0},
                {"lot_id": "l2", "amount": 50.0},
                {"lot_id": "l3", "amount": 450.0},
            ],
        },
    ]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=[],
        fund_calls=[], payments=[], preview=True,
    )
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    # Owner amount total = 500 + 50 = 550. Part occ = 30% * 550 = 165.
    # Part prop = 550 - 165 = 385.
    assert "550,00" in text, (
        f"Le total owner (500+50=550) devrait apparaitre. Extrait :\n{text[:2000]}"
    )
    assert "165,00" in text, (
        f"Part occupant (30% * 550 = 165) devrait apparaitre. Extrait :\n{text[:2000]}"
    )
    assert "385,00" in text, (
        f"Part proprietaire (70% * 550 = 385) devrait apparaitre. Extrait :\n{text[:2000]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
