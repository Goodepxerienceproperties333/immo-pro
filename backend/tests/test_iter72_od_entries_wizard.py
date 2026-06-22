"""Regression test for iter72octies : OD year-end wizard parser.

The Optipro "Liste des depenses" PDF contains rows where N° piece = "-"
which are year-end OD (Operations Diverses) adjustments. These were
previously NOT imported by the wizard, causing ~767 EUR diff with the
official Optipro total.

This test verifies that :
1. The parser extracts only OD rows (N° piece = "-")
2. Compte 650 (Frais bancaires) is filtered out (handled by FI)
3. Auto-detection of counterpart accounts works for standard libelles
4. Period dates are extracted from the header
"""
import sys
sys.path.insert(0, "/app/backend")

import pytest


def _fetch_pdf() -> bytes | None:
    import urllib.request
    url = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/a539vex7_Liste%20des%20d%C3%A9penses%20du%2001_01_2025%20au%2031_12_2025%20%2822_06_2026%29.pdf"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


def test_parse_od_entries_extracts_only_dash_ref():
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    # Expected : 33 entries totaling 767.02 EUR
    assert result["total_count"] == 33
    assert result["total_amount"] == pytest.approx(767.02, abs=0.01)
    assert result["period_start"] == "01/01/2025"
    assert result["period_end"] == "31/12/2025"
    # NO entry should be on compte 650 (filtered out)
    assert all(e["account_number"] != "650" for e in result["entries"])


def test_parse_od_entries_auto_detects_counterparts():
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    # Group counterparts by confidence
    from collections import Counter
    conf_counts = Counter(e["suggested_counterpart"]["confidence"] for e in result["entries"])
    # Most should be auto-detected with high confidence
    assert conf_counts.get("high", 0) >= 28
    # No 'none' confidence (all entries must have at least a fallback)
    assert conf_counts.get("none", 0) == 0
    # Check specific entries
    annul = next(e for e in result["entries"] if "Annulation" in e["libelle"] and "Ascenseur" in e["libelle"])
    assert annul["suggested_counterpart"]["account"] == "490"
    assert annul["suggested_counterpart"]["confidence"] == "high"
    assert annul["amount"] == pytest.approx(4141.40, abs=0.01)
    # Find FAR ENGIE entry
    far = next(e for e in result["entries"] if "FAR" in e["libelle"].upper() and "ENGIE" in e["libelle"].upper())
    assert far["suggested_counterpart"]["account"] == "444"
    # Nettoyage de bilan
    nett = next(e for e in result["entries"] if "Nettoyage" in e["libelle"])
    assert nett["suggested_counterpart"]["account"] == "417"
    assert nett["amount"] == pytest.approx(7342.97, abs=0.01)


def test_suggest_counterpart_643_always_410():
    """Compte 643 (Frais privatifs) is ALWAYS paired with 410."""
    from import_wizard.pdf_utils import _suggest_od_counterpart
    # Empty libelle but compte 643 -> 410
    s = _suggest_od_counterpart("", "643", -13.75)
    assert s["account"] == "410"
    assert s["confidence"] == "high"
    # Even with a misleading libelle like "sinistre" -> 643 wins -> 410
    s = _suggest_od_counterpart("sinistre quelconque", "643", -100)
    assert s["account"] == "410"


def test_suggest_counterpart_accent_normalization():
    """Keyword matching must be accent-insensitive (e.g. 'à reporter' matches)."""
    from import_wizard.pdf_utils import _suggest_od_counterpart
    s = _suggest_od_counterpart("Charge à reporter ascenseurs", "61011", -4234.26)
    assert s["account"] == "490"
    assert s["confidence"] == "high"

    s = _suggest_od_counterpart("Annulation charges à reporter (Ascenseurs)", "61011", 4141.40)
    assert s["account"] == "490"
    assert s["confidence"] == "high"
