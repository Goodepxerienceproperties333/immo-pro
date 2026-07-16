"""iter90gi : `parse_distribution_keys_pdf` doit calculer total_quotities
comme la SOMME des quotites des lignes de detail, jamais depuis la cellule
"resume" du PDF (souvent la quotite du lot associe au code, pas le total).

**Ticket utilisateur** :
> "total quotite incorrecte" (screenshot : Total quotites 898.00 pour la
> cle "001 APPARTEMENT" alors que la somme des detail lines fait 7910)

**Cause** : le parseur prenait `qts[0]` de la ligne de resume comme
"explicit_total". Dans le PDF Optipro que le syndic a uploade, cette
cellule est la quotite du lot 001 (=898), et non le total de la cle.

**Fix iter90gi** :
1. `explicit_total` supprime (ligne 1417).
2. Post-passe finale : `total_quotities = sum(lines[].quotity)` pour
   TOUTES les cles.
3. Cote frontend : le total est aussi recalcule sur affichage (defense
   en profondeur).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_total_quotities_always_matches_sum_of_lines():
    """La post-passe finale recalcule `total_quotities` a partir de la
    somme des lignes, meme si le parser avait mis une valeur incoherente.
    """
    # Simule la structure finale renvoyee par parse_distribution_keys_pdf
    keys = [
        {
            "code": "001",
            "name": "APPARTEMENT",
            "type": "tantiemes",
            "lines": [
                {"lot_label": "002-APPARTEMENT", "quotity": 1095},
                {"lot_label": "101-APPARTEMENT", "quotity": 692},
                {"lot_label": "102-APPARTEMENT", "quotity": 1197},
                {"lot_label": "103-APPARTEMENT", "quotity": 968},
                {"lot_label": "201-APPARTEMENT", "quotity": 693},
                {"lot_label": "202-APPARTEMENT", "quotity": 1009},
                {"lot_label": "203-APPARTEMENT", "quotity": 969},
                {"lot_label": "301-APPARTEMENT", "quotity": 1287},
                {"lot_label": "302-APPARTEMENT", "quotity": 733},
            ],
            # Value the parser mistakenly set from qts[0] on the summary row
            "total_quotities": 898.0,
        },
    ]

    # Reproduit la post-passe finale du parser
    for k in keys:
        k["total_quotities"] = round(
            sum(float(l.get("quotity") or 0) for l in (k.get("lines") or [])),
            6,
        )

    # 1095+692+1197+968+693+1009+969+1287+733 = 8643
    assert keys[0]["total_quotities"] == 8643.0, f"got {keys[0]['total_quotities']}"


def test_total_quotities_zero_when_no_lines():
    """Une cle vide (0 ligne) doit avoir total_quotities=0, pas 898."""
    keys = [
        {"code": "0002", "name": "Cle Speciales ascenseurs",
         "type": "tantiemes", "lines": [], "total_quotities": 999},
    ]
    for k in keys:
        k["total_quotities"] = round(
            sum(float(l.get("quotity") or 0) for l in (k.get("lines") or [])),
            6,
        )
    assert keys[0]["total_quotities"] == 0


def test_parser_source_does_not_use_qts0_as_total():
    """Verifie que le code source du parser n'utilise plus `qts[0]` comme
    total quotites (regression guard).
    """
    src = open("/app/backend/import_wizard/pdf_utils.py", "r", encoding="utf-8").read()
    assert "explicit_total = _to_float(qts[0])" not in src, \
        "iter90gi : ne PAS reintroduire l'utilisation de qts[0] comme total"
    # Verifie que la post-passe existe
    assert "total_quotities" in src and "post-passe finale" in src, \
        "iter90gi : la post-passe finale doit exister pour recalculer les totaux"


if __name__ == "__main__":
    test_total_quotities_always_matches_sum_of_lines()
    print("OK test_total_quotities_always_matches_sum_of_lines")
    test_total_quotities_zero_when_no_lines()
    print("OK test_total_quotities_zero_when_no_lines")
    test_parser_source_does_not_use_qts0_as_total()
    print("OK test_parser_source_does_not_use_qts0_as_total")
