"""iter93bx : Bilan PDF - layout adaptatif ACTIF/PASSIF.

Contexte utilisateur : "erreur interne du serveur" lors de la generation du
PDF Bilan pour ACP Agathe (47 lignes ACTIF, hauteur totale 824pt > 716pt de
la page A4). Root cause : le layout side-by-side (1 outer table 1 row x 2
cols) ne pouvait PAS split car reportlab refuse de split une cellule de
tableau. LayoutError.

Fix : layout adaptatif. Mesure la hauteur necessaire avant de decider.
- Si ACTIF et PASSIF tiennent tous deux < 700pt -> side-by-side classique
- Sinon -> STACKED (ACTIF pleine largeur puis PASSIF pleine largeur), avec
  `splitByRow=1` et `repeatRows=1` pour la propagation multi-pages.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_bilan import build_bilan_pdf


def _make_bilan(nb_sub_accounts: int) -> dict:
    """Construit un dict bilan avec N sous-comptes pour stresser le layout."""
    actif_rubrique = {
        "code": "V.A",
        "label": "Coproprietaires debiteurs (cl. 400)",
        "total": nb_sub_accounts * 100.0,
        "accounts": [
            {"number": f"41010{i:03d}", "name": f"Owner {i}", "amount": 100.0}
            for i in range(nb_sub_accounts)
        ],
    }
    passif_rubrique = {
        "code": "I",
        "label": "Capital / Fonds propre",
        "total": nb_sub_accounts * 100.0,
        "accounts": [
            {"number": "100", "name": "Fonds de roulement general", "amount": nb_sub_accounts * 100.0}
        ],
    }
    return {
        "actif": [actif_rubrique],
        "passif": [passif_rubrique],
        "total_actif": nb_sub_accounts * 100.0,
        "total_passif": nb_sub_accounts * 100.0,
        "equilibre": True,
        "ecart": 0.0,
    }


def _build(bilan):
    return build_bilan_pdf(
        copropriete={"name": "TEST", "reference": "R"},
        bilan_data=bilan,
        date_to="2026-12-31",
    )


def test_small_bilan_side_by_side():
    """5 sous-comptes ACTIF -> tient sur 1 page en side-by-side."""
    pdf_bytes = _build(_make_bilan(5))
    assert pdf_bytes[:4] == b"%PDF"
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        assert len(pdf.pages) == 1


def test_large_bilan_stacked_multipage():
    """50 sous-comptes ACTIF -> depasse la page, layout stacked multi-pages."""
    pdf_bytes = _build(_make_bilan(50))
    assert pdf_bytes[:4] == b"%PDF"
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        assert len(pdf.pages) >= 2, f"Attendu >=2 pages, got {len(pdf.pages)}"
        full_txt = " ".join(p.extract_text() or "" for p in pdf.pages)
        assert "ACTIF" in full_txt
        assert "PASSIF" in full_txt
        assert "TOTAL ACTIF" in full_txt
        assert "TOTAL PASSIF" in full_txt


if __name__ == "__main__":
    test_small_bilan_side_by_side()
    print("OK test_small_bilan_side_by_side")
    test_large_bilan_stacked_multipage()
    print("OK test_large_bilan_stacked_multipage")
    print("\n=== ALL 2 TESTS PASSED ===")
