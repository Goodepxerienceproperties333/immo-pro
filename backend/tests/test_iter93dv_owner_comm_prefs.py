"""iter93dv - Tests des preferences de communication des proprietaires.

Feature: comm_preference (email/courrier/recommande) + ag_convocation_mode
(email/recommande) editables cote syndic ET cote proprio self-service.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"
OWNER_EMAIL = "evrard.gerald@outlook.be"
OWNER_PASSWORD = "TestOwnerIter93CK!"


def _login_session(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login {email} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_headers():
    return _login_session(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def owner_headers():
    return _login_session(OWNER_EMAIL, OWNER_PASSWORD)


@pytest.fixture(scope="module")
def created_owner_id(admin_headers):
    """Cree un owner de test via POST /api/owners avec les 2 nouvelles prefs."""
    payload = {
        "first_name": "TEST_iter93dv",
        "last_name": "PrefsOwner",
        "email": "TEST_iter93dv_prefs@example.com",
        "comm_preference": "courrier",
        "ag_convocation_mode": "recommande",
    }
    r = admin_headers.post(f"{BASE_URL}/api/owners", json=payload)
    assert r.status_code in (200, 201), f"Create owner failed: {r.status_code} {r.text}"
    data = r.json()
    oid = data.get("id")
    assert oid, f"No id in create response: {data}"
    yield oid
    # cleanup
    admin_headers.delete(f"{BASE_URL}/api/owners/{oid}")


class TestSyndicOwnerCommPrefs:
    """POST/PUT/GET /api/owners cote syndic"""

    def test_create_persists_both_fields(self, admin_headers, created_owner_id):
        r = admin_headers.get(f"{BASE_URL}/api/owners/{created_owner_id}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("comm_preference") == "courrier", f"comm_preference wrong: {d.get('comm_preference')}"
        assert d.get("ag_convocation_mode") == "recommande", f"ag_conv wrong: {d.get('ag_convocation_mode')}"

    def test_put_updates_comm_preference_only(self, admin_headers, created_owner_id):
        # Fetch current values first
        cur = admin_headers.get(f"{BASE_URL}/api/owners/{created_owner_id}").json()
        payload = {**cur, "comm_preference": "email"}
        # Remove _id if any
        payload.pop("_id", None)
        r = admin_headers.put(f"{BASE_URL}/api/owners/{created_owner_id}", json=payload)
        assert r.status_code == 200, r.text
        got = admin_headers.get(f"{BASE_URL}/api/owners/{created_owner_id}").json()
        assert got.get("comm_preference") == "email"
        assert got.get("ag_convocation_mode") == "recommande", "ag mode should be preserved"

    def test_put_updates_ag_convocation_only(self, admin_headers, created_owner_id):
        cur = admin_headers.get(f"{BASE_URL}/api/owners/{created_owner_id}").json()
        payload = {**cur, "ag_convocation_mode": "email"}
        payload.pop("_id", None)
        r = admin_headers.put(f"{BASE_URL}/api/owners/{created_owner_id}", json=payload)
        assert r.status_code == 200, r.text
        got = admin_headers.get(f"{BASE_URL}/api/owners/{created_owner_id}").json()
        assert got.get("ag_convocation_mode") == "email"
        assert got.get("comm_preference") == "email"

    def test_list_owners_includes_new_fields(self, admin_headers, created_owner_id):
        r = admin_headers.get(f"{BASE_URL}/api/owners")
        assert r.status_code == 200
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        me = next((o for o in items if o.get("id") == created_owner_id), None)
        assert me, "Created owner not found in GET /api/owners"
        assert "comm_preference" in me, f"comm_preference missing in list: {list(me.keys())}"
        assert "ag_convocation_mode" in me, f"ag_convocation_mode missing in list"


class TestOwnerPortalSelfService:
    """GET/PUT /api/owner/me cote proprietaire"""

    def test_get_me_returns_both_fields(self, owner_headers):
        r = owner_headers.get(f"{BASE_URL}/api/owner/me")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "comm_preference" in d, f"comm_preference missing in /me: {list(d.keys())}"
        assert "ag_convocation_mode" in d, f"ag_convocation_mode missing in /me"

    def test_put_me_updates_both_fields(self, owner_headers):
        # Set to known values
        r = owner_headers.put(f"{BASE_URL}/api/owner/me",
                         json={"comm_preference": "recommande", "ag_convocation_mode": "email"})
        assert r.status_code == 200, r.text
        body = r.json()
        # Verify via GET
        got = owner_headers.get(f"{BASE_URL}/api/owner/me").json()
        assert got.get("comm_preference") == "recommande"
        assert got.get("ag_convocation_mode") == "email"
        # Check response body optionally
        if body.get("updated") and "changes" in body:
            labels = [c.get("label") for c in body.get("changes", [])]
            # At least labels should be human
            assert any("communication" in (l or "").lower() or "convocation" in (l or "").lower()
                       for l in labels), f"Human labels missing in changes: {labels}"

    def test_put_me_partial_preserves_existing(self, owner_headers):
        # First set both
        owner_headers.put(f"{BASE_URL}/api/owner/me",
                     json={"comm_preference": "courrier", "ag_convocation_mode": "recommande"})
        before = owner_headers.get(f"{BASE_URL}/api/owner/me").json()
        assert before.get("comm_preference") == "courrier"
        assert before.get("ag_convocation_mode") == "recommande"
        # PUT without these fields (only e.g. phone) should NOT overwrite
        r = owner_headers.put(f"{BASE_URL}/api/owner/me",
                         json={"phone": before.get("phone", "") or "+32000000000"})
        assert r.status_code == 200
        after = owner_headers.get(f"{BASE_URL}/api/owner/me").json()
        assert after.get("comm_preference") == "courrier", "comm_preference was overwritten!"
        assert after.get("ag_convocation_mode") == "recommande", "ag_convocation_mode was overwritten!"

    def test_put_me_notification_persisted(self, owner_headers):
        # Trigger a real change to force notification
        owner_headers.put(f"{BASE_URL}/api/owner/me",
                     json={"comm_preference": "email", "ag_convocation_mode": "recommande"})
        r = owner_headers.put(f"{BASE_URL}/api/owner/me",
                         json={"comm_preference": "recommande", "ag_convocation_mode": "email"})
        assert r.status_code == 200
        body = r.json()
        # If updated, expect changes to contain both fields with labels
        if body.get("updated"):
            changes = body.get("changes", [])
            fields_changed = {c.get("field") for c in changes}
            assert "comm_preference" in fields_changed
            assert "ag_convocation_mode" in fields_changed
            labels = {c.get("field"): c.get("label") for c in changes}
            assert labels.get("comm_preference") == "Mode de communication prefere"
            assert labels.get("ag_convocation_mode") == "Mode de convocation AG"
