"""iter92g - Backend tests: DELETE /api/coproprietes/{id}

Rules:
- syndic (role=syndic, email != whitelist) -> 403
- superadmin/admin -> 200 OK
- whitelist email (info@nextgecopro.be) -> 200 OK even if role=syndic
- POST /archive still available for syndic (regression)
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://optipro-parser-fix.preview.emergentagent.com").rstrip("/")

SUPERADMIN = {"email": "admin@copro.be", "password": "admin123"}
SYNDIC = {"email": "syndic_alpha@copro.be", "password": "admin123"}
WHITELIST = {"email": "info@nextgecopro.be", "password": "admin123"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"Login failed for {creds['email']}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def superadmin_session():
    return _login(SUPERADMIN)


@pytest.fixture(scope="module")
def syndic_session():
    return _login(SYNDIC)


@pytest.fixture(scope="module")
def whitelist_session():
    return _login(WHITELIST)


def _create_test_acp(session, suffix):
    name = f"TEST_iter92g_{suffix}_{uuid.uuid4().hex[:6]}"
    payload = {"name": name, "address": "1 rue test", "postal_code": "1000", "city": "Bruxelles"}
    r = session.post(f"{BASE_URL}/api/coproprietes", json=payload, timeout=30)
    assert r.status_code == 200, f"Create ACP failed: {r.status_code} {r.text}"
    acp = r.json()
    assert acp.get("id"), "No id in created ACP"
    return acp


class TestDeleteCoproIter92g:
    def test_syndic_cannot_delete_returns_403(self, superadmin_session, syndic_session):
        # Create ACP via superadmin so we control the ID
        acp = _create_test_acp(superadmin_session, "syndic_denied")
        try:
            r = syndic_session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
            body = r.json()
            detail = body.get("detail", "")
            assert "syndic" in detail.lower() and "supprimer" in detail.lower(), f"Detail mismatch: {detail}"
            assert "archiver" in detail.lower() or "archive" in detail.lower(), f"Should suggest archive: {detail}"
            assert "10 ans" in detail or "III.86" in detail, f"Should mention legal obligation: {detail}"
            # Verify ACP still exists
            r2 = superadmin_session.get(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)
            assert r2.status_code == 200, "ACP should still exist after failed delete"
        finally:
            # Cleanup via superadmin
            superadmin_session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)

    def test_superadmin_can_delete_returns_200(self, superadmin_session):
        acp = _create_test_acp(superadmin_session, "super_ok")
        r = superadmin_session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=30)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert "supprim" in body.get("message", "").lower()
        # Verify actually gone
        r2 = superadmin_session.get(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)
        assert r2.status_code == 404, f"Expected 404 after delete, got {r2.status_code}"

    def test_whitelist_email_can_delete_returns_200(self, superadmin_session, whitelist_session):
        # Whitelist user is role=syndic but email = info@nextgecopro.be
        acp = _create_test_acp(superadmin_session, "whitelist_ok")
        r = whitelist_session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=30)
        assert r.status_code == 200, f"Whitelist user should be able to delete, got {r.status_code}: {r.text}"
        # Verify gone
        r2 = superadmin_session.get(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)
        assert r2.status_code == 404


class TestArchiveRegression:
    def test_syndic_can_still_archive(self, superadmin_session, syndic_session):
        acp = _create_test_acp(superadmin_session, "archive_regress")
        try:
            # Attach ACP to syndic so syndic can access
            # syndic must be scoped: let's use the syndic to POST archive - endpoint uses _get_manager
            r = syndic_session.post(f"{BASE_URL}/api/coproprietes/{acp['id']}/archive", timeout=15)
            # syndic has manager role -> should be allowed
            assert r.status_code == 200, f"Syndic should archive, got {r.status_code}: {r.text}"
            body = r.json()
            assert "archiv" in body.get("message", "").lower()
        finally:
            superadmin_session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}", timeout=15)
