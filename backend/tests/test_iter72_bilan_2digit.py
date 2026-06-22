"""Regression test for bilan PDF parser (iter72quinquies).

Ensures that 2-digit PCMN accounts (e.g. "14 - Resultat exercice") are
correctly parsed and not filtered out by the anchor regex.

Bug context : a user-reported balance import showed
  Total Actif  = 23 981.64
  Total Passif = 23 956.18  (missing 25.46)
The missing amount was the "14 - Resultat exercice" line which was being
filtered out by the previous regex `^\\d{3,7}$` (3-7 digit anchors only).
"""
import os
import sys
sys.path.insert(0, "/app/backend")

import pytest


def _fetch_bilan_pdf() -> bytes | None:
    """Download the reference Bilan PDF used in iter72quinquies. Returns None
    if offline (test should be skipped)."""
    import urllib.request
    url = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/ysqzhnpg_Bilan%20comptable%20au%2031_12_2024.pdf"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


def test_parse_balance_pdf_includes_2digit_accounts():
    """The PDF parser must extract account '14 - Resultat exercice' (2 digits)
    on the Passif side. Without this fix, the bilan would be off by 25.46 EUR."""
    raw = _fetch_bilan_pdf()
    if raw is None:
        pytest.skip("Cannot reach customer assets - offline mode")

    from import_wizard.pdf_utils import parse_balance_pdf
    result = parse_balance_pdf(raw)

    assert result["total_actif"] == pytest.approx(23981.64, abs=0.01)
    assert result["total_passif"] == pytest.approx(23981.64, abs=0.01)
    assert result["balanced"] is True
    assert result["period_end_date"] == "31/12/2024"

    # Find the '14 - Resultat exercice' line on the Passif side
    passif_codes = {e["account"]: e for e in result["passif"]}
    assert "14" in passif_codes, "Account '14 - Resultat exercice' was filtered out!"
    assert passif_codes["14"]["amount"] == pytest.approx(25.46, abs=0.01)
    assert "ésultat" in passif_codes["14"]["label"] or "Result" in passif_codes["14"]["label"]
    assert passif_codes["14"]["is_subaccount"] is False

    # Sanity : '100 - Fonds de roulement' (3 digits) is also present
    assert "100" in passif_codes
    assert passif_codes["100"]["amount"] == pytest.approx(7500.00, abs=0.01)


def test_parse_balance_pdf_no_false_2digit_anchors():
    """Ensure that 2-digit numbers appearing inside amount fragments
    (e.g. '10' from '10 262,39') are NOT mistakenly picked up as anchors."""
    raw = _fetch_bilan_pdf()
    if raw is None:
        pytest.skip("Cannot reach customer assets - offline mode")

    from import_wizard.pdf_utils import parse_balance_pdf
    result = parse_balance_pdf(raw)

    # Counting only main 2-digit codes ; expected: only '14' (Resultat exercice)
    main_2digit_actif = [e for e in result["actif"] if not e["is_subaccount"] and len(e["account"]) == 2]
    main_2digit_passif = [e for e in result["passif"] if not e["is_subaccount"] and len(e["account"]) == 2]
    assert len(main_2digit_actif) == 0, f"Unexpected 2-digit Actif anchors: {main_2digit_actif}"
    assert len(main_2digit_passif) == 1, f"Expected exactly one 2-digit Passif anchor (14), got: {main_2digit_passif}"

    # No sub-account with len(code) == 2 should ever exist
    assert all(len(e["account"]) >= 3 for e in result["actif"] + result["passif"] if e["is_subaccount"])
