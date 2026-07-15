"""
iter90g0 : PDF decompte - fallback quotites via la cle generale
`distribution_keys[is_default=True].lots[].share` quand le champ direct
`lot.quotity` est vide/nul.

Ticket utilisateur (Feb 2026, PROD Acacia TER, TEUWEN Gael) :
> "les quotites sont vides ! et nous avons encore des enormes differences
> entre optipro et Ma plateforme"

Screenshot fourni : le decompte affichait "Lot 001 - (0 tantiemes)",
"Lot C01 - (0 tantiemes)", "Lot pe01 - (0 tantiemes)" et une quote-part
globale "0.00% (0/1 tantiemes)".

Cause : dans `pdf_decompte.py`, les 3 endroits suivants utilisaient
`l.get('quotity', 0)` directement, sans fallback sur la cle generale :
- `owner_quotity = sum(l.get('quotity', 0) for l in owner_lots)`
- `total_quotity = sum(l.get('quotity', 0) for l in all_lots) or 1`
- `_lot_line(l)` : affichage "{quotity} tantiemes"

Sur ACP Acacia TER (import legacy), lot.quotity est vide - les tantiemes
vivent uniquement dans `distribution_keys[is_default].lots[].share`. Le
meme fallback etait deja applique dans compute_bilan_data (iter90ft), le
decompte annuel endpoint (iter90ej), et le portail proprietaire (iter90cz).

Fix iter90g0 : introduction d'un helper local `_lot_quotity(l)` dans
`build_decompte_pdf` qui applique la meme regle de priorite - 1. lot.quotity
direct - 2. default_key.lots[lot_id].share. Utilise partout ou lot.quotity
etait utilise pour affichage/calcul de quote-part globale.

Aucun impact sur les calculs de charges (les distribution_lines des
invoices restent la source de verite - ils ont ete pre-calcules a la
creation de la facture via key['lots'][i]['share'], donc corrects meme
quand lot.quotity=0).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_decompte import build_decompte_pdf  # noqa: E402


def test_quotity_fallback_via_default_distribution_key():
    """Lots avec quotity=0 + cle generale contenant les shares -> PDF
    affiche les tantiemes de la cle et une quote-part globale non-nulle."""
    owner = {
        "id": "o-teuwen", "name": "TEUWEN Gael",
        "email": "teuwen@test.local",
        "vcs_code": "+++000/0000/11720+++",
        "address": "1 rue Test", "postal_code": "1000", "city": "Bruxelles",
        "country": "Belgique",
    }
    copropriete = {"id": "acp-acacia", "name": "ACP Acacia TER",
                   "address": "-", "postal_code": "1000", "city": "Bruxelles"}
    fiscal_year = {"id": "fy-1", "name": "Exercice 2025-2026",
                   "start_date": "2025-10-01", "end_date": "2026-09-30"}
    # Owner possede 3 lots avec quotity=0 (legacy import Acacia TER)
    owner_lots = [
        {"id": "l001", "number": "001", "description": "APPARTEMENT", "quotity": 0},
        {"id": "lC01", "number": "C01", "description": "CAVE", "quotity": 0},
        {"id": "lpe01", "number": "pe01", "description": "PARKING EXT", "quotity": 0},
    ]
    # ACP total 10000 tantiemes, TEUWEN detient 898 + 11 + 34 = 943 (9.43%)
    all_lots = owner_lots + [
        {"id": "l101", "number": "101", "description": "Autre", "quotity": 0},
    ]
    distribution_keys = [
        {
            "id": "dk-gen", "name": "Cle generale", "code": "GEN",
            "is_default": True,
            "lots": [
                {"lot_id": "l001", "share": 898, "excluded": False},
                {"lot_id": "lC01", "share": 11, "excluded": False},
                {"lot_id": "lpe01", "share": 34, "excluded": False},
                {"lot_id": "l101", "share": 9057, "excluded": False},
            ],
        },
    ]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=[], distribution_keys=distribution_keys,
        fund_calls=[], payments=[], preview=True,
    )
    assert pdf_bytes and len(pdf_bytes) > 1000

    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    # REGRESSION check : PDF ne doit PLUS contenir "(0 tantiemes)" - le
    # match exact avec parentheses evite le faux-positif avec "10 tantiemes",
    # "100 tantiemes", etc.
    assert "(0 tantiemes)" not in text, (
        f"REGRESSION iter90g0 : PDF affiche encore '(0 tantiemes)' pour "
        f"un lot dont quotity direct est vide. Extrait :\n{text[:1500]}"
    )
    # Doit contenir les tantiemes issus de la cle generale
    assert "898 tantiemes" in text, (
        f"Lot 001 doit afficher 898 tantiemes (via fallback default_key). "
        f"Extrait :\n{text[:1500]}"
    )
    assert "(11 tantiemes)" in text, "Lot C01 devrait afficher 11 tantiemes"
    assert "(34 tantiemes)" in text, "Lot pe01 devrait afficher 34 tantiemes"
    # Quote-part globale : 943/10000 = 9,43%
    assert "9,43" in text or "9.43" in text, (
        f"Quote-part globale devrait afficher 9,43% (943/10000). "
        f"Extrait :\n{text[:1500]}"
    )
    # REGRESSION : ne doit PAS afficher "0.00%" ni "(0/1 tantiemes)"
    assert "(0/1 tantiemes)" not in text and "0.00%" not in text, (
        f"REGRESSION iter90g0 : la quote-part globale est encore a 0. Extrait :\n{text[:1500]}"
    )


def test_direct_quotity_still_takes_priority():
    """Si lot.quotity direct > 0, il prime sur la cle generale (retro-compat
    pour les ACPs deja correctement configurees)."""
    owner = {"id": "o", "name": "X", "vcs_code": "+++111+++",
             "address": "-", "postal_code": "-", "city": "-", "country": "-"}
    copropriete = {"id": "acp", "name": "-", "address": "-",
                   "postal_code": "-", "city": "-"}
    fiscal_year = {"id": "fy", "name": "2025",
                   "start_date": "2025-01-01", "end_date": "2025-12-31"}
    owner_lots = [{"id": "l1", "number": "1", "quotity": 5000.0}]  # direct
    all_lots = owner_lots + [{"id": "l2", "number": "2", "quotity": 5000.0}]
    distribution_keys = [
        {
            "id": "dk-gen", "is_default": True,
            "lots": [
                {"lot_id": "l1", "share": 100, "excluded": False},  # different
                {"lot_id": "l2", "share": 900, "excluded": False},
            ],
        },
    ]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=[], distribution_keys=distribution_keys,
        fund_calls=[], payments=[], preview=True,
    )
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    # Doit utiliser 5000 (direct), pas 100 (cle generale)
    assert "5000 tantiemes" in text, (
        f"Direct quotity=5000 doit primer sur cle_generale.share=100. "
        f"Extrait :\n{text[:1500]}"
    )
    # Quote-part : 5000/10000 = 50%
    assert "50,00" in text or "50.00" in text or "50 %" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
