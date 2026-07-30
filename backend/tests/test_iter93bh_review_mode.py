"""iter93bh: Backend tests for review mode (import_session_id filter + ASC sort).

Validates:
 - GET /api/invoices?copropriete_id=X&import_session_id=Y returns only invoices
   from that session and sorted ASC by date.
 - Without import_session_id, default DESC ordering preserved.
"""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

ADMIN_EMAIL = "admin@copro.be"
ADMIN_PW = "admin123"

COPRO_ID = "56c54c9f-436e-4842-b511-a6913c23c6d2"
SESSION_ID = "d249d7ba-f22c-4a0b-a7e9-4d2fa9c752dc"


def _session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PW},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return s


def test_login_ok():
    s = _session()
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    assert r.status_code == 200
    assert r.json().get("email") == ADMIN_EMAIL


def test_invoices_filter_by_import_session_id_asc():
    s = _session()
    headers = {"X-Copropriete-Id": COPRO_ID}
    r = s.get(
        f"{BASE_URL}/api/invoices",
        params={"copropriete_id": COPRO_ID, "import_session_id": SESSION_ID},
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200, r.text
    invoices = r.json()
    assert isinstance(invoices, list)
    # Expect ~33 invoices from the seeded session
    assert len(invoices) >= 1, "Expected at least 1 invoice for session"
    # All items belong to session
    for inv in invoices:
        assert inv.get("import_session_id") == SESSION_ID
        assert inv.get("copropriete_id") == COPRO_ID
    # Sorted ASC by date
    dates = [inv.get("date", "") for inv in invoices]
    assert dates == sorted(dates), f"Expected ASC dates, got {dates[:5]}..."


def test_invoices_default_sort_desc_without_session():
    s = _session()
    headers = {"X-Copropriete-Id": COPRO_ID}
    r = s.get(
        f"{BASE_URL}/api/invoices",
        params={"copropriete_id": COPRO_ID},
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200, r.text
    invoices = r.json()
    assert isinstance(invoices, list)
    if len(invoices) >= 2:
        dates = [inv.get("date", "") for inv in invoices]
        assert dates == sorted(dates, reverse=True), "Expected DESC default sort"


def test_invoices_session_scoped_has_private_fee():
    """At least 1 invoice in the seeded session must be flagged is_private_fee."""
    s = _session()
    headers = {"X-Copropriete-Id": COPRO_ID}
    r = s.get(
        f"{BASE_URL}/api/invoices",
        params={"copropriete_id": COPRO_ID, "import_session_id": SESSION_ID},
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200
    invs = r.json()
    private_fee = [i for i in invs if i.get("is_private_fee")]
    assert len(private_fee) >= 1, "Expected at least 1 private-fee (compte 643) invoice"
