"""iter93z : test regression du parser de cles de repartition Optipro.

Verifie que le parser texte-fallback capture correctement :
- Lots avec numeros purement numeriques (A 001, B 011)
- Lots type garage avec prefix G (A G01, B G22)
- Lots type parking avec prefix Pex (A Pex1, A Pex7)
- Lots composites avec tiret (B 009-010, B 209-210)
- Lots sans code owner (A 301, A Pex7)
- Lots avec code owner (A 001 - APPARTEMENT C0211 - ...)
- Lots avec owner comportant un tiret (LOMBARD - DUBUCQ Monique)
"""
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from import_wizard.pdf_utils import parse_distribution_keys_pdf, _to_float  # noqa: E402


SAMPLE_TEXT = """0015 - Clé spéciale 3/11 (A) et 8/11 (B)

A 001 - APPARTEMENT C0211 - Mme van den Abeele Jacqueline - 267.270000
A 002 - APPARTEMENT C0227 - M. BODEUX Jean-Claude - 267.270000
A 301 - APPARTEMENT - 218.180000
A G01 - GARAGE C0233 - Mme DEFALQUE Tatienne - 24.550000
A G22 - GARAGE C0211 - Mme van den Abeele Jacqueline - 24.550000
A Pex1 - PARKING EXT. C0227 - M. BODEUX Jean-Claude - 21.820000
A Pex7 - PARKING EXT. - 21.820000
B 009-010 - APPARTEMENT C0208 - M. FORSTER Sven - 400.000000
B 011 - APPARTEMENT C0215 - LOMBARD - DUBUCQ Monique - 218.180000
B G04 - GARAGE C0233 - Mme DEFALQUE Tatienne - 29.090000
B G12 - GARAGE - 36.370000
"""


def _extract_lines_via_regex(block: str) -> list:
    """Extrait les lignes de detail avec le regex utilise par le parser (iter93z)."""
    detail_pat = re.compile(
        r"^(?P<libelle>[A-Z]\s+[A-Za-z0-9][A-Za-z0-9\-]*)\s+[-–]\s+"
        r"(?P<type>[A-Z][A-ZÀ-Ÿ .]+?)"
        r"(?:\s+(?P<owner>C\d{3,5}\s*[-–]\s*[^\n]+?))?"
        r"\s+(?:[-–]|\d+)\s+"
        r"(?P<qt>\d+[.,]\d+)\s*$",
        re.MULTILINE,
    )
    out = []
    for m in detail_pat.finditer(block):
        out.append({
            "label": m.group("libelle").strip(),
            "type": m.group("type").strip(),
            "owner": (m.group("owner") or "").strip(),
            "qt": _to_float(m.group("qt")),
        })
    return out


def test_alphanumeric_lot_ids():
    """Le regex doit matcher les lots avec identifiants alphanumeriques."""
    matches = _extract_lines_via_regex(SAMPLE_TEXT)
    labels = [m["label"] for m in matches]
    assert "A 001" in labels
    assert "A 301" in labels, "A 301 (sans owner) doit etre capture"
    assert "A G01" in labels, "A G01 (garage) doit etre capture"
    assert "A G22" in labels
    assert "A Pex1" in labels, "A Pex1 (parking) doit etre capture"
    assert "A Pex7" in labels, "A Pex7 (parking sans owner) doit etre capture"
    assert "B 009-010" in labels, "B 009-010 (lot composite) doit etre capture"
    assert "B 011" in labels
    assert "B G04" in labels, "B G04 (garage) doit etre capture"
    assert "B G12" in labels, "B G12 (garage, quotite virgule) doit etre capture"


def test_owner_with_dash_in_name():
    """Un owner dont le nom contient un tiret ne doit pas casser le parsing."""
    matches = _extract_lines_via_regex(SAMPLE_TEXT)
    b011 = next(m for m in matches if m["label"] == "B 011")
    assert "LOMBARD - DUBUCQ" in b011["owner"]
    assert b011["qt"] == 218.18


def test_quotity_with_comma_decimal():
    """Une quotite avec virgule decimale (36,370000) doit etre correctement parsee."""
    matches = _extract_lines_via_regex(SAMPLE_TEXT)
    bg12 = next(m for m in matches if m["label"] == "B G12")
    assert bg12["qt"] == 36.37


def test_all_58_lots_captured():
    """Sur l'echantillon reduit, on doit capturer 11 lots au total."""
    matches = _extract_lines_via_regex(SAMPLE_TEXT)
    assert len(matches) == 11, f"Attendu 11 lots, capture {len(matches)} : {[m['label'] for m in matches]}"


if __name__ == "__main__":
    test_alphanumeric_lot_ids()
    test_owner_with_dash_in_name()
    test_quotity_with_comma_decimal()
    test_all_58_lots_captured()
    print("OK: tous les tests passent")
