"""Regression test - iter75 - Budget PDF parser : cles speciales + fiscal year non-calendar.

Bug 1 : IndexError sur les budgets avec exercice fiscal non-calendar (ex. 01/10/2025
au 30/09/2026). Le header contient "Realise 2024-2025" + "Budget 2025-2026" = 4
year-tokens au lieu de 2, et les montants > 1000 (ex. "18 800,00") creent des
clusters de "fragment de milliers" supplementaires qui amenent a > 3 colonnes
detectees, ce qui provoque un IndexError au moment du mapping aux keys.

Fix : merge des clusters fragment (entiers 1-3 chiffres sans virgule) avec leur
voisin de droite (gap < 35px). Defensive : truncate amt_col_centers a len(keys).

Bug 2 : les cles speciales ("Cle Speciale ascenseurs") n'etaient pas reconnues.
Resultat : auto-creation comme cle generique (is_special=False) ce qui empechait
le traitement special en repartition (ne s'applique qu'aux lots concernes).

Fix : detection du flag is_special par regex sur le libelle de section.
"""
import sys
import urllib.request
import pytest

sys.path.insert(0, "/app/backend")


def _fetch_budget_pdf_fiscal_year() -> bytes | None:
    """Budget PDF avec exercice fiscal non-calendrier (01/10/2025 - 30/09/2026)
    contenant une section 'Cle Speciale ascenseurs'."""
    url = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/s5qzgmgn_Budget%20du%2001_10_2025%20au%2030_09_2026.pdf"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read()
    except Exception:
        return None


def test_budget_pdf_fiscal_year_no_crash_and_specials():
    raw = _fetch_budget_pdf_fiscal_year()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_budget_pdf
    res = parse_budget_pdf(raw)

    # Bug 1 : pas de crash + total correct
    assert res["total_global"] == pytest.approx(19000.00, abs=0.01), (
        f"Total budget attendu 19000, recu {res['total_global']}"
    )
    assert len(res["sections"]) == 2, f"Expected 2 sections, got {len(res['sections'])}"

    # Bug 2 : section 0002 'Cle Speciales ascenseurs' marquee is_special=True
    sec_by_code = {s["key_code"]: s for s in res["sections"]}
    assert "0001" in sec_by_code
    assert "0002" in sec_by_code

    assert sec_by_code["0001"]["is_special"] is False, (
        f"0001 'Charges communes' ne doit PAS etre tagge special"
    )
    assert "ommunes" in sec_by_code["0001"]["key_label"], (
        f"Libelle 0001 incorrect : {sec_by_code['0001']['key_label']!r}"
    )

    assert sec_by_code["0002"]["is_special"] is True, (
        f"0002 'Cle Speciales ascenseurs' DOIT etre tagge special. "
        f"Libelle = {sec_by_code['0002']['key_label']!r}"
    )
    assert "scenseur" in sec_by_code["0002"]["key_label"].lower(), (
        f"Libelle 0002 incorrect : {sec_by_code['0002']['key_label']!r}"
    )

    # Section 0001 : 19 detail lines, total budget_n = 18800
    assert len(sec_by_code["0001"]["lines"]) == 19
    s1_total = sum(l["budget_n"] for l in sec_by_code["0001"]["lines"])
    assert s1_total == pytest.approx(18800.00, abs=0.01)

    # Section 0002 : 1 detail line 61010 Controle ascenseurs = 200
    assert len(sec_by_code["0002"]["lines"]) == 1
    assert sec_by_code["0002"]["lines"][0]["account"] == "61010"
    assert sec_by_code["0002"]["lines"][0]["budget_n"] == pytest.approx(200.00, abs=0.01)
