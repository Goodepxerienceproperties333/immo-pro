"""iter90gk E2E scenarios via HTTP - tests the 10 review scenarios."""
import os
import pytest
import requests
import uuid

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://optipro-parser-fix.preview.emergentagent.com").rstrip("/")
MARIA_ID = "ed728e70-1d0d-4057-a37a-d450cc9ac812"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def copro_id(admin_session):
    """Retrieve a valid copro id owned by admin. Fallback: create one temporarily?"""
    # Try list copros
    r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        if items:
            return items[0]["id"]
    return MARIA_ID


# ---- SCENARIO 1: BCE required on POST /api/suppliers ----
def test_s1_supplier_without_bce_returns_400(admin_session, copro_id):
    r = admin_session.post(
        f"{BASE_URL}/api/suppliers",
        json={"name": f"TEST_NoBCE_{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id},
        timeout=30,
    )
    assert r.status_code == 400, f"Expected 400 got {r.status_code}: {r.text[:300]}"
    body = r.text.lower()
    assert "bce" in body, f"Error message missing BCE: {r.text[:300]}"


# ---- SCENARIO 2: BCE unique globally across ACPs ----
def test_s2_bce_global_uniqueness(admin_session, copro_id):
    bce = f"BE0{uuid.uuid4().int % 10**9:09d}"
    n1 = f"TEST_S2A_{uuid.uuid4().hex[:6]}"
    r1 = admin_session.post(
        f"{BASE_URL}/api/suppliers",
        json={"name": n1, "bce_number": bce, "copropriete_id": copro_id},
        timeout=30,
    )
    assert r1.status_code in (200, 201), f"Create 1 failed: {r1.status_code} {r1.text[:200]}"
    sup1_id = r1.json().get("id")

    # Try creating in another copro (or same copro w/ different name) with same BCE
    r2 = admin_session.post(
        f"{BASE_URL}/api/suppliers",
        json={"name": f"TEST_S2B_{uuid.uuid4().hex[:6]}", "bce_number": bce, "copropriete_id": copro_id},
        timeout=30,
    )
    assert r2.status_code == 409, f"Expected 409, got {r2.status_code}: {r2.text[:300]}"
    assert "bce" in r2.text.lower(), r2.text[:300]

    # cleanup
    if sup1_id:
        admin_session.delete(f"{BASE_URL}/api/suppliers/{sup1_id}", timeout=15)


# ---- SCENARIO 3: Owner strict duplicate by EMAIL ----
def test_s3_owner_strict_email_duplicate(admin_session, copro_id):
    email = f"test_s3_{uuid.uuid4().hex[:6]}@example.com"
    p1 = {"first_name": "Jean", "last_name": f"S3A_{uuid.uuid4().hex[:4]}", "email": email, "copropriete_id": copro_id}
    r1 = admin_session.post(f"{BASE_URL}/api/owners", json=p1, timeout=30)
    assert r1.status_code in (200, 201), f"Create 1 failed: {r1.status_code} {r1.text[:300]}"
    o1_id = r1.json().get("id")

    p2 = {"first_name": "Marie", "last_name": f"S3B_{uuid.uuid4().hex[:4]}", "email": email, "copropriete_id": copro_id}
    r2 = admin_session.post(f"{BASE_URL}/api/owners", json=p2, timeout=30)
    assert r2.status_code == 409, f"Expected 409, got {r2.status_code}: {r2.text[:300]}"
    assert "STRICT" in r2.text, f"Expected 'STRICT' in message: {r2.text[:300]}"

    if o1_id:
        admin_session.delete(f"{BASE_URL}/api/owners/{o1_id}", timeout=15)


# ---- SCENARIO 4: Owner homonym (same name, diff email/phone) ----
def test_s4_owner_homonym_detection_and_force(admin_session, copro_id):
    ln = f"S4Homonym_{uuid.uuid4().hex[:6]}"
    fn = "Pierre"
    p1 = {"first_name": fn, "last_name": ln, "email": f"a_{uuid.uuid4().hex[:6]}@ex.com",
          "phone": f"04{uuid.uuid4().int % 10**8:08d}", "copropriete_id": copro_id}
    r1 = admin_session.post(f"{BASE_URL}/api/owners", json=p1, timeout=30)
    assert r1.status_code in (200, 201), f"Create 1: {r1.status_code} {r1.text[:300]}"
    o1_id = r1.json().get("id")

    p2 = {"first_name": fn, "last_name": ln, "email": f"b_{uuid.uuid4().hex[:6]}@ex.com",
          "phone": f"04{uuid.uuid4().int % 10**8:08d}", "copropriete_id": copro_id}
    r2 = admin_session.post(f"{BASE_URL}/api/owners", json=p2, timeout=30)
    assert r2.status_code == 409, f"Expected 409: {r2.status_code} {r2.text[:300]}"
    assert "Homonyme" in r2.text or "homonym" in r2.text.lower(), r2.text[:300]

    # Force create
    r3 = admin_session.post(
        f"{BASE_URL}/api/owners?force_create_despite_homonym=true", json=p2, timeout=30
    )
    assert r3.status_code in (200, 201), f"Force create failed: {r3.status_code} {r3.text[:300]}"
    o2_id = r3.json().get("id")

    if o1_id:
        admin_session.delete(f"{BASE_URL}/api/owners/{o1_id}", timeout=15)
    if o2_id:
        admin_session.delete(f"{BASE_URL}/api/owners/{o2_id}", timeout=15)


# ---- SCENARIO 5: /api/admin/duplicates-audit ----
def test_s5_duplicates_audit_json(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/duplicates-audit", timeout=90)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    data = r.json()
    assert "suppliers" in data and "owners" in data and "pcmn_accounts" in data
    assert "bce_duplicates" in data["suppliers"]
    assert "missing_bce_examples" in data["suppliers"]
    assert "email_duplicates" in data["owners"]
    assert "phone_duplicates" in data["owners"]
    assert "orphan_tier_accounts" in data["pcmn_accounts"]
    assert "duplicated_bank_accounts" in data["pcmn_accounts"]
    assert "credit_notes_without_entry" in data
    assert "summary" in data


def test_s5_duplicates_audit_csv(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/duplicates-audit?format=csv", timeout=90)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    ct = r.headers.get("content-type", "")
    assert "csv" in ct.lower() or "text" in ct.lower(), ct


# ---- SCENARIO 6: /api/suppliers/{id}/bce-candidates ----
def test_s6_bce_candidates_existing_supplier(admin_session, copro_id):
    # Create a supplier to test with
    bce = f"BE0{uuid.uuid4().int % 10**9:09d}"
    r = admin_session.post(
        f"{BASE_URL}/api/suppliers",
        json={"name": f"TEST_S6_{uuid.uuid4().hex[:6]}", "bce_number": bce, "copropriete_id": copro_id},
        timeout=30,
    )
    assert r.status_code in (200, 201), r.text[:300]
    sid = r.json()["id"]

    r2 = admin_session.get(f"{BASE_URL}/api/suppliers/{sid}/bce-candidates", timeout=30)
    assert r2.status_code == 200, f"{r2.status_code}: {r2.text[:300]}"
    data = r2.json()
    assert "supplier" in data and "candidates" in data

    admin_session.delete(f"{BASE_URL}/api/suppliers/{sid}", timeout=15)


def test_s6_bce_candidates_missing_supplier(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/suppliers/nonexistent-id-xyz/bce-candidates", timeout=30)
    assert r.status_code == 404, f"{r.status_code}: {r.text[:200]}"


# ---- SCENARIO 7: Bilan Maria equilibre ----
def test_s7_bilan_maria_equilibre(admin_session):
    r = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": MARIA_ID, "date_to": "2027-02-28"},
        timeout=60,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    data = r.json()
    total_actif = float(data.get("total_actif", data.get("totals", {}).get("actif", 0)) or 0)
    total_passif = float(data.get("total_passif", data.get("totals", {}).get("passif", 0)) or 0)
    equilibre = data.get("equilibre")
    assert abs(total_actif - total_passif) < 5.0, f"Not balanced: {total_actif} vs {total_passif} (delta={total_actif - total_passif})"
    if equilibre is not None:
        assert equilibre is True, f"equilibre={equilibre}"
    # tolerance +/- 5€ on 14351.48
    assert abs(total_actif - 14351.48) < 5.0, f"total_actif={total_actif} not near 14351.48"
