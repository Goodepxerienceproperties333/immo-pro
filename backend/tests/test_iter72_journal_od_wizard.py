"""Regression test for iter72nonies : Journal OD format support.

The Optipro PDF "Journal comptable - OD" has explicit balanced double-entries
(charge + counterpart already defined per line). This is preferred over the
"Liste des depenses" format which requires manual counterpart selection.

This test verifies that :
1. Format auto-detection works (journal_od vs expense_list)
2. All entries are parsed with their full lines
3. Closing entries (Cloture - ...) are excluded by default
4. Sinistre closings (... - cloture) are NOT excluded
5. Each entry is balanced
"""
import sys
sys.path.insert(0, "/app/backend")
import pytest


def _fetch_pdf() -> bytes | None:
    import urllib.request
    url = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/jov2h1oc_Journal%20comptable%20_%20OD%20du%2001_01_2025%20au%2031_12_2025%20%2822_06_2026%29.pdf"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


def test_journal_od_format_detection():
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    assert result["format"] == "od_journal"
    assert result["total_count"] == 35
    assert result["period_start"] == "01/01/2025"
    assert result["period_end"] == "31/12/2025"


def test_journal_od_entries_balanced():
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    for e in result["entries"]:
        sum_d = round(sum(ln["debit"] for ln in e["lines"]), 2)
        sum_c = round(sum(ln["credit"] for ln in e["lines"]), 2)
        assert abs(sum_d - sum_c) < 0.01, f"Entry {e['reference']} unbalanced: D={sum_d} C={sum_c}"
        assert e["balanced"] is True


def test_journal_od_closing_entries_excluded():
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    # Closing entries must be excluded
    closing = [e for e in result["entries"] if e["reference"] in ("000466", "000467")]
    assert len(closing) == 2
    for e in closing:
        assert e["included"] is False
        assert "cloture" in e["exclusion_reason"].lower()
    # Sinistre closures must NOT be excluded
    sinistre_closures = [
        e for e in result["entries"]
        if "loture" in e["description"].lower() and "Sinistre" in e["description"]
    ]
    for e in sinistre_closures:
        assert e["included"] is True, f"False positive on {e['reference']}: {e['description']}"


def test_journal_od_auxiliary_info_extraction():
    """Lines with aux info like 'Coproprietaires | C1996 M. brumagne' should
    be split into account_name + auxiliary_info."""
    raw = _fetch_pdf()
    if raw is None:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    # Find entry 000092 (Transfert solde crediteur Mme Burmagne)
    e = next(x for x in result["entries"] if x["reference"] == "000092")
    aux_lines = [ln for ln in e["lines"] if ln["auxiliary_info"]]
    assert len(aux_lines) >= 1
    found_c1996 = False
    for ln in aux_lines:
        if ln["auxiliary_info"].startswith("C1996"):
            found_c1996 = True
            assert ln["account_name"] == "Copropriétaires"
            assert ln["account_number"] == "41001996"
            break
    assert found_c1996


def test_expense_list_format_still_works():
    """Fallback to old format should still work when uploading 'Liste des depenses'."""
    import urllib.request
    url = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/a539vex7_Liste%20des%20d%C3%A9penses%20du%2001_01_2025%20au%2031_12_2025%20%2822_06_2026%29.pdf"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read()
    except Exception:
        pytest.skip("Offline mode")
    from import_wizard.pdf_utils import parse_od_entries_pdf
    result = parse_od_entries_pdf(raw)
    assert result["format"] == "expense_list"
    assert result["total_count"] == 33
