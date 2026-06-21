"""Iter34 - Phase I Wizard Optipro: OD d'ouverture (Bilan comptable) E2E tests."""
import os
import io
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
COPRO_ID = "be6e826c-7e5b-4eda-9fc6-764a5c4d6d12"
BILAN_PDF_URL = "https://customer-assets.emergentagent.com/job_immo-pcmn/artifacts/8xwfejzs_Bilan%20comptable%20au%2031_12_2025.pdf"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token") or r.json().get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def bilan_pdf_bytes():
    r = requests.get(BILAN_PDF_URL, timeout=30)
    assert r.status_code == 200
    return r.content


# --- 1. parse-pdf standalone ---
def test_parse_pdf_balance_standalone(auth_session, bilan_pdf_bytes):
    files = {"file": ("bilan.pdf", io.BytesIO(bilan_pdf_bytes), "application/pdf")}
    r = auth_session.post(f"{BASE_URL}/api/import-wizard/parse-pdf",
                          files=files, data={"kind": "balance"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "actif" in data and "passif" in data
    assert isinstance(data["actif"], list) and isinstance(data["passif"], list)
    assert data.get("balanced") is True
    assert round(float(data["total_actif"]), 2) == 11855.84
    assert round(float(data["total_passif"]), 2) == 11855.84
    assert data.get("period_end_date") == "31/12/2025"


# --- 2. Session + sniff-pdf + commit-opening-balance ---
@pytest.fixture(scope="module")
def import_session(auth_session):
    r = auth_session.post(f"{BASE_URL}/api/import-wizard/sessions",
                          json={"copropriete_id": COPRO_ID})
    assert r.status_code == 200, r.text
    sess = r.json()
    sid = sess["id"]
    yield sid
    # teardown: rollback whatever remains
    try:
        auth_session.delete(f"{BASE_URL}/api/import-wizard/sessions/{sid}")
    except Exception:
        pass


def test_sniff_pdf_balance_in_session(auth_session, import_session, bilan_pdf_bytes):
    sid = import_session
    files = {"file": ("bilan.pdf", io.BytesIO(bilan_pdf_bytes), "application/pdf")}
    r = auth_session.post(f"{BASE_URL}/api/import-wizard/sessions/{sid}/sniff-pdf",
                          files=files, data={"kind": "balance"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("balanced") is True
    # save for later test
    pytest._sniff_balance = data


def test_commit_opening_balance_and_rollback(auth_session, bilan_pdf_bytes):
    # Fresh session so rollback assertions are clean
    r = auth_session.post(f"{BASE_URL}/api/import-wizard/sessions",
                          json={"copropriete_id": COPRO_ID})
    assert r.status_code == 200
    sid = r.json()["id"]
    try:
        # Sniff PDF
        files = {"file": ("bilan.pdf", io.BytesIO(bilan_pdf_bytes), "application/pdf")}
        r = auth_session.post(f"{BASE_URL}/api/import-wizard/sessions/{sid}/sniff-pdf",
                              files=files, data={"kind": "balance"})
        assert r.status_code == 200
        parsed = r.json()
        # Commit
        payload = {
            "actif": parsed.get("actif", []),
            "passif": parsed.get("passif", []),
            "period_end_date": parsed.get("period_end_date", "31/12/2025"),
            "fiscal_year_id": "",
        }
        r = auth_session.post(
            f"{BASE_URL}/api/import-wizard/sessions/{sid}/commit-opening-balance",
            json=payload)
        assert r.status_code == 200, r.text
        result = r.json()
        # a) total_debit = total_credit = 11855.84
        assert round(result["total_debit"], 2) == 11855.84
        assert round(result["total_credit"], 2) == 11855.84
        # c) 9 lines (3 actif leaves + 6 passif leaves)
        assert result["lines"] == 9, f"expected 9 leaf lines, got {result['lines']}"
        # d) entry_date = 2026-01-01
        assert result["entry_date"] == "2026-01-01"
        # e) AN entry created with reference AN-2026-001
        je_id = result["journal_entry_id"]
        assert je_id
        # Verify via journal_entries listing
        r = auth_session.get(f"{BASE_URL}/api/accounting/entries",
                             params={"copropriete_id": COPRO_ID, "journal_type": "AN"})
        assert r.status_code == 200
        ans = [e for e in r.json() if e.get("id") == je_id]
        assert len(ans) == 1
        an = ans[0]
        assert an["journal_type"] == "AN"
        assert an["reference"] == "AN-2026-001"
        assert an["date"] == "2026-01-01"
        # b) leaf filter: ensure 550472 is included (no children) and 410/440 excluded
        account_nums = [ln["account_number"] for ln in an["lines"]]
        assert "550472" in account_nums, f"550472 missing: {account_nums}"
        # parent codes 410 and 440 must NOT appear as ledger lines
        assert "410" not in account_nums
        assert "440" not in account_nums
        # Sub-accounts must appear
        assert any(a.startswith("4100") for a in account_nums)
        assert any(a.startswith("4400") for a in account_nums)
    finally:
        # Rollback
        r = auth_session.delete(f"{BASE_URL}/api/import-wizard/sessions/{sid}")
        assert r.status_code == 200, r.text
        report = r.json().get("report", {})
        # Should report exactly 1 journal entry deleted
        assert report.get("journal_entries", 0) == 1, f"rollback report: {report}"
