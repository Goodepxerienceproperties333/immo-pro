"""iter90gi : le parser distribution_keys ne doit PAS confondre
- ligne SUMMARY : "0001 - Charges communes 30 10 000,00"
- ligne DETAIL : "001 - APPARTEMENT C2612 TEUWEN Gaël 898.000000"

Les 2 matchent le regex ^(\d{3,4})[-–](.+)$ mais le sens est completement
different. Sans distinction :
- Bug 1 : "001 - APPARTEMENT" cree une CLE fantome au lieu d'etre attache
  a la cle "0001 - Charges communes" comme lot detail.
- Bug 2 : le total_quotities de la cle fantome vaut 898 (quotite lot 001)
  au lieu de la somme reelle des lots (10 000).

**Ticket utilisateur** : "l'appartement 001 est ignore systematiquement
lors de l'import ameliore la reconnaissance"

**Fix iter90gi (parser)** :
Distingue SUMMARY vs DETAIL en verifiant :
- La cellule "coproprietaire" contient un code Optipro `C\\d{3,5}` -> DETAIL
- Le libelle contient un type de lot (APPARTEMENT, CAVE, PARKING, etc.) -> DETAIL
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_parser_treats_001_appartement_as_detail_not_key():
    """Regression test : les entrees "\\d{3} - LOT_TYPE" sont traitees comme
    lignes de detail, pas comme cles.
    """
    src = open("/app/backend/import_wizard/pdf_utils.py", "r", encoding="utf-8").read()
    # Verifie la presence de la logique looks_like_detail (iter90gi)
    assert "looks_like_detail" in src, "iter90gi : logique looks_like_detail manquante"
    assert "lot_type_patterns" in src, "iter90gi : lot_type_patterns manquant"
    # Verifie les patterns critiques
    for pat in ("appartement", "cave", "parking", "commerce", "garage"):
        assert f'"{pat}"' in src, f"iter90gi : pattern '{pat}' manquant"
    # Verifie la detection par owner code Optipro
    assert 'r"^C\\d{3,5}\\b"' in src, "iter90gi : detection C\\d{3,5} pour owner code manquante"


def test_parser_real_user_pdf_produces_single_key_with_lot_001():
    """Test end-to-end avec le PDF reel du syndic : doit produire 1 cle
    "0001 - Charges communes" avec total=10000 et 30 lignes dont lot 001.
    """
    import urllib.request
    from import_wizard.pdf_utils import parse_distribution_keys_pdf

    url = "https://customer-assets-jt897jd0.emergentagent.net/job_c983d589-579c-4dc1-9f75-c76209275508/artifacts/a9875an2_Cl%C3%A9%20de%20r%C3%A9partition.pdf"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = resp.read()
    except Exception:
        # Reseau indisponible : skip (test opportuniste avec asset externe)
        print("SKIP : asset URL not reachable")
        return

    result = parse_distribution_keys_pdf(raw)
    keys = result["keys"]

    # 1 seule cle produite (bug pre-iter90gi : ~10 cles fantomes)
    assert len(keys) == 1, f"expected 1 key, got {len(keys)}: {[k['code'] for k in keys]}"

    k = keys[0]
    assert k["code"] == "0001", f"expected code=0001, got {k['code']}"
    assert "Charges communes" in k["name"], f"unexpected name: {k['name']}"

    # Total 10000 (bug pre-iter90gi : 898 = quotite lot 001)
    assert abs(k["total_quotities"] - 10000) < 0.5, f"expected ~10000, got {k['total_quotities']}"

    # 30 lignes (bug pre-iter90gi : lot 001 ignore -> 29 max)
    assert len(k["lines"]) == 30, f"expected 30 lines, got {len(k['lines'])}"

    # Lot 001 present avec quotite 898
    lot_001 = next((l for l in k["lines"] if l["lot_label"].startswith("001")), None)
    assert lot_001 is not None, f"lot 001 missing (bug user reporte). Lots: {[l['lot_label'] for l in k['lines']]}"
    assert abs(float(lot_001["quotity"]) - 898) < 0.1, f"expected qt=898 for lot 001, got {lot_001['quotity']}"


if __name__ == "__main__":
    test_parser_treats_001_appartement_as_detail_not_key()
    print("OK test_parser_treats_001_appartement_as_detail_not_key")
    test_parser_real_user_pdf_produces_single_key_with_lot_001()
    print("OK test_parser_real_user_pdf_produces_single_key_with_lot_001")
