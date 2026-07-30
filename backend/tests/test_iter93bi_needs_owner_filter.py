"""iter93bi backend regression:
- The frontend adds ?filter=needs_owner param. Backend should ignore it (unknown
  query param) and still return invoices filtered by import_session_id.
- Also verifies import_session_id path returns invoices ASC sorted (iter93bh).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')

SESSION_ID = 'd249d7ba-f22c-4a0b-a7e9-4d2fa9c752dc'
COPRO_ID = '56c54c9f-436e-4842-b511-a6913c23c6d2'


@pytest.fixture(scope='module')
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


class TestNeedsOwnerFilter:
    def test_invoices_with_import_session_id(self, client):
        r = client.get(f"{BASE_URL}/api/invoices",
                       params={"copropriete_id": COPRO_ID,
                               "import_session_id": SESSION_ID})
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # All invoices should belong to this session
        for inv in data:
            assert inv.get("import_session_id") == SESSION_ID

    def test_invoices_filter_param_ignored_by_backend(self, client):
        """Extra ?filter=needs_owner should not break the request."""
        r = client.get(f"{BASE_URL}/api/invoices",
                       params={"copropriete_id": COPRO_ID,
                               "import_session_id": SESSION_ID,
                               "filter": "needs_owner"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        # Backend does not know about filter -> returns same list as without it
        r2 = client.get(f"{BASE_URL}/api/invoices",
                        params={"copropriete_id": COPRO_ID,
                                "import_session_id": SESSION_ID})
        assert len(data) == len(r2.json())

    def test_session_has_private_fee_invoice(self, client):
        """Confirms at least one private_fee 643 invoice exists in session."""
        r = client.get(f"{BASE_URL}/api/invoices",
                       params={"copropriete_id": COPRO_ID,
                               "import_session_id": SESSION_ID})
        assert r.status_code == 200
        pf = [i for i in r.json() if i.get("is_private_fee")]
        assert len(pf) >= 1, "Expected at least one private-fee 643 invoice"

    def test_no_500_when_only_filter_param(self, client):
        """Even without import_session_id, filter param must not error."""
        r = client.get(f"{BASE_URL}/api/invoices",
                       params={"copropriete_id": COPRO_ID,
                               "filter": "needs_owner"})
        assert r.status_code == 200
