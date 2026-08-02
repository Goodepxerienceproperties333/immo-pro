"""iter121 - Retest fix persistence comm_preference & ag_convocation_mode
sur POST /api/owners et PUT /api/owners/{id} apres fix iter93dv-post-fix.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"
COPRO_ID = "c9cfce94-96c6-4202-8a6d-0a5627b50856"  # Agathe

OWNER_PORTAL_EMAIL = "evrard.gerald@outlook.be"
OWNER_PORTAL_PASSWORD = "TestOwnerIter93CK!"


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login {email} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture
def cleanup_owners(admin):
    created = []
    yield created
    for oid in created:
        try:
            admin.delete(f"{BASE_URL}/api/owners/{oid}")
        except Exception:
            pass


def _unique_suffix():
    return uuid.uuid4().hex[:8]


class TestPostOwnersPersistence:
    def test_post_with_both_fields_persists(self, admin, cleanup_owners):
        suf = _unique_suffix()
        payload = {
            "name": f"TEST_iter121 {suf}",
            "first_name": f"T{suf}",
            "last_name": f"Est{suf}",
            "copropriete_id": COPRO_ID,
            "comm_preference": "courrier",
            "ag_convocation_mode": "recommande",
        }
        r = admin.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code in (200, 201), f"POST failed: {r.status_code} {r.text}"
        oid = r.json().get("id")
        assert oid
        cleanup_owners.append(oid)

        got = admin.get(f"{BASE_URL}/api/owners/{oid}").json()
        assert got.get("comm_preference") == "courrier", f"comm_preference: {got.get('comm_preference')}"
        assert got.get("ag_convocation_mode") == "recommande", f"ag: {got.get('ag_convocation_mode')}"

    def test_post_without_fields_defaults_email(self, admin, cleanup_owners):
        suf = _unique_suffix()
        payload = {
            "name": f"TEST_iter121def {suf}",
            "first_name": f"D{suf}",
            "last_name": f"Ef{suf}",
            "copropriete_id": COPRO_ID,
        }
        r = admin.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code in (200, 201), f"POST failed: {r.status_code} {r.text}"
        oid = r.json().get("id")
        assert oid
        cleanup_owners.append(oid)

        got = admin.get(f"{BASE_URL}/api/owners/{oid}").json()
        assert got.get("comm_preference") == "email", f"default comm: {got.get('comm_preference')}"
        assert got.get("ag_convocation_mode") == "email", f"default ag: {got.get('ag_convocation_mode')}"


class TestPutOwnersPersistence:
    @pytest.fixture
    def owner_id(self, admin, cleanup_owners):
        suf = _unique_suffix()
        payload = {
            "name": f"TEST_iter121put {suf}",
            "first_name": f"P{suf}",
            "last_name": f"Ut{suf}",
            "copropriete_id": COPRO_ID,
            "comm_preference": "courrier",
            "ag_convocation_mode": "recommande",
        }
        r = admin.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code in (200, 201), r.text
        oid = r.json()["id"]
        cleanup_owners.append(oid)
        return oid

    def test_put_only_comm_preference_preserves_ag(self, admin, owner_id):
        r = admin.put(f"{BASE_URL}/api/owners/{owner_id}",
                      json={"comm_preference": "recommande"})
        assert r.status_code == 200, r.text
        got = admin.get(f"{BASE_URL}/api/owners/{owner_id}").json()
        assert got.get("comm_preference") == "recommande"
        assert got.get("ag_convocation_mode") == "recommande", \
            f"ag_convocation_mode should be preserved, got {got.get('ag_convocation_mode')}"

    def test_put_only_ag_convocation_preserves_comm(self, admin, owner_id):
        r = admin.put(f"{BASE_URL}/api/owners/{owner_id}",
                      json={"ag_convocation_mode": "email"})
        assert r.status_code == 200, r.text
        got = admin.get(f"{BASE_URL}/api/owners/{owner_id}").json()
        assert got.get("ag_convocation_mode") == "email"
        assert got.get("comm_preference") == "courrier", \
            f"comm_preference should be preserved, got {got.get('comm_preference')}"

    def test_put_without_either_field_preserves_both(self, admin, owner_id):
        # PUT with only phone -> comm & ag must stay unchanged
        r = admin.put(f"{BASE_URL}/api/owners/{owner_id}",
                      json={"phone": "+32000123456"})
        assert r.status_code == 200, r.text
        got = admin.get(f"{BASE_URL}/api/owners/{owner_id}").json()
        assert got.get("comm_preference") == "courrier", \
            f"comm_preference overwritten! got {got.get('comm_preference')}"
        assert got.get("ag_convocation_mode") == "recommande", \
            f"ag_convocation_mode overwritten! got {got.get('ag_convocation_mode')}"


class TestOwnerMeRegression:
    """Regression iter93dv : /api/owner/me toujours OK."""

    def test_get_and_put_owner_me(self):
        try:
            s = _login(OWNER_PORTAL_EMAIL, OWNER_PORTAL_PASSWORD)
        except AssertionError:
            pytest.skip("Owner portal creds not available")
        r = s.get(f"{BASE_URL}/api/owner/me")
        assert r.status_code == 200, r.text
        d = r.json()
        assert "comm_preference" in d
        assert "ag_convocation_mode" in d

        # PUT change
        r2 = s.put(f"{BASE_URL}/api/owner/me",
                   json={"comm_preference": "email", "ag_convocation_mode": "email"})
        assert r2.status_code == 200, r2.text
        got = s.get(f"{BASE_URL}/api/owner/me").json()
        assert got.get("comm_preference") == "email"
        assert got.get("ag_convocation_mode") == "email"

        # Partial PUT preserves
        r3 = s.put(f"{BASE_URL}/api/owner/me",
                   json={"comm_preference": "courrier"})
        assert r3.status_code == 200
        got2 = s.get(f"{BASE_URL}/api/owner/me").json()
        assert got2.get("comm_preference") == "courrier"
        assert got2.get("ag_convocation_mode") == "email", "ag should be preserved"
