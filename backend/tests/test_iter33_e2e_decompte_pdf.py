"""End-to-end test for iter33: GET /api/reports/decompte/pdf/{owner_id}
Closes the fiscal year temporarily (and reopens it), then exercises the
restructured PDF endpoint via real HTTP. Verifies the new Finlead structure.
"""
import io
import os
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://pcmn-accounting.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"
ACP_ID = "252c2888-7c95-4a89-b19f-70de62f7bb4a"  # "Test" ACP (existing in DB)
OWNER_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"  # Dubois Jean (owns lot A4)
FY_ID = "1f4a82e1-c1d4-487d-adea-4cc777c6f8d9"  # Exercice 2026 (status=closed)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def closed_fy(session):
    """Ensure FY is closed; yield; then reopen to leave state intact."""
    # Check current status
    r = session.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={ACP_ID}")
    assert r.status_code == 200, f"List FY failed: {r.status_code} {r.text[:200]}"
    fys = r.json()
    fy = next((f for f in fys if f.get("id") == FY_ID), None)
    assert fy is not None, f"FY {FY_ID} not found"
    original_status = fy.get("status")
    print(f"[setup] FY current status = {original_status}")

    if original_status != "closed":
        rc = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/close")
        assert rc.status_code in (200, 201), f"Close FY failed: {rc.status_code} {rc.text[:300]}"
        print("[setup] FY closed.")

    yield FY_ID

    # Teardown: reopen if it was open before
    if original_status != "closed":
        rr = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/reopen")
        print(f"[teardown] FY reopen status={rr.status_code}")


def test_decompte_pdf_returns_valid_pdf(session, closed_fy):
    url = f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}?copropriete_id={ACP_ID}&fiscal_year_id={closed_fy}"
    r = session.get(url)
    assert r.status_code == 200, f"PDF endpoint failed: {r.status_code} {r.text[:300]}"
    assert r.content[:4] == b"%PDF", "Response is not a valid PDF"
    assert len(r.content) > 2000, f"PDF too small: {len(r.content)} bytes"
    # Save artefact for inspection
    out = "/app/test_reports/iter33_decompte_e2e.pdf"
    with open(out, "wb") as f:
        f.write(r.content)
    print(f"[ok] PDF saved at {out} ({len(r.content)} bytes)")


def test_decompte_pdf_has_finlead_columns_and_structure(session, closed_fy):
    """E2E: with the 'Test' ACP (no invoices in FY), we can still verify
    section structure and lot info. Column headers only appear if there are
    charges, so we conditionally assert them. Unit tests in test_iter33_decompte_finlead.py
    fully cover the column/structure layout with mocked data."""
    url = f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}?copropriete_id={ACP_ID}&fiscal_year_id={closed_fy}"
    r = session.get(url)
    assert r.status_code == 200
    text = _extract_pdf_text(r.content)

    # Always-present structure markers
    assert "Decompte annuel" in text, "Missing main title"
    assert "Dubois Jean" in text, "Missing owner name"
    assert "Lot A4" in text or "Lot: A4" in text, "Missing lot reference"
    assert "1. Detail de vos charges" in text, "Missing Section 1 header"

    # Conditional: if charges exist, columns must be there
    has_charges = "Aucune charge" not in text
    if has_charges:
        assert "Designation" in text
        assert ("Quotites" in text) or ("Quotités" in text)
        assert "Montant" in text and "repartir" in text
        assert "Part" in text and "proprietaire" in text
        assert "occupant" in text
        assert "Total Lot" in text
        assert "Totaux generaux" in text
        print("[ok] charges table present with full column structure")
    else:
        print("[info] No charges in FY -> empty-state message rendered (expected)")


def test_decompte_pdf_section_numbering_and_recap_locataire(session, closed_fy):
    url = f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}?copropriete_id={ACP_ID}&fiscal_year_id={closed_fy}"
    r = session.get(url)
    assert r.status_code == 200
    text = _extract_pdf_text(r.content)

    # Section 1 always present
    assert "1. Detail" in text

    # If section 2 (recap locataire) is present then "TOTAL A REFACTURER" must too,
    # else (no occupant invoices) it must be absent. Either is acceptable;
    # we just verify consistency.
    has_recap_header = "2. Recapitulatif des charges locataire" in text
    has_recap_total = "TOTAL A REFACTURER AU LOCATAIRE" in text
    assert has_recap_header == has_recap_total, (
        f"Inconsistent recap section: header={has_recap_header} total={has_recap_total}"
    )
    print(f"[info] Recap locataire section present: {has_recap_header}")


def test_decompte_pdf_400_when_fy_not_closed(session):
    """Endpoint must refuse non-closed FY. We don't toggle here; we look for
    any 'open' FY in the ACP and assert 400."""
    r = session.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={ACP_ID}")
    assert r.status_code == 200
    fys = r.json()
    open_fy = next((f for f in fys if f.get("status") != "closed"), None)
    if not open_fy:
        pytest.skip("No open FY available to test 400 case")
    url = f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}?copropriete_id={ACP_ID}&fiscal_year_id={open_fy['id']}"
    r = session.get(url)
    assert r.status_code == 400, f"Expected 400 for non-closed FY, got {r.status_code}: {r.text[:200]}"


def test_bulk_delete_fund_calls_endpoint_exists_and_validates(session):
    """NON-DESTRUCTIVE check: POST /api/fund-calls/delete-all without copropriete_id
    must return 400 (chinese-walls validation). This proves the route is wired."""
    r = session.post(f"{BASE_URL}/api/fund-calls/delete-all")
    print(f"[info] bulk delete (no copro) status={r.status_code} body={r.text[:200]}")
    # Must be 400 per route impl ; tolerate 422 if FastAPI validates query first.
    assert r.status_code in (400, 422), (
        f"Expected 400/422 validation, got {r.status_code}: {r.text[:200]}"
    )
