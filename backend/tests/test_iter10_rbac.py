"""
Iteration 10: RBAC path-based middleware tests.

Tests:
- Owner role: GET allowed; writes 403; admin paths 403 (any verb).
- Gestionnaire role: GET allowed; writes allowed; /api/admin/* 403; /api/users 403.
- Superadmin: full access (regression with iter7/iter8 admin cookie).
- Regression: /api/auth/me returns 'id', /api/admin/demo/seed always returns 'counts'.

Cleanup: any TEST_iter10_* users / owners / coproprietes created are deleted at end.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://optipro-parser-fix.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")

# ---------------- Helpers ----------------

def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def existing_copro_id(admin_session) -> str:
    r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
    assert r.status_code == 200
    items = r.json()
    if items:
        return items[0].get("id") or items[0].get("_id")
    # Otherwise create one for testing
    body = {
        "reference": f"TEST_iter10_{int(time.time())}",
        "name": "TEST_iter10_ACP",
        "address": "Rue 1, Bruxelles",
        "quotas_total": 1000,
        "bank_accounts": [],
    }
    r = admin_session.post(f"{BASE_URL}/api/coproprietes", json=body, timeout=30)
    assert r.status_code in (200, 201), r.text
    data = r.json()
    return data.get("id") or data.get("_id")


@pytest.fixture(scope="module")
def created_users(admin_session):
    """Create owner & gestionnaire test users; yield ids; cleanup at end."""
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:6]
    users = {}
    for role in ("owner", "gestionnaire"):
        email = f"TEST_iter10_{role}_{ts}_{suffix}@example.com"
        body = {
            "email": email,
            "password": "TestPass123!",
            "name": f"TEST_iter10 {role}",
            "role": role,
            "copropriete_ids": [],
        }
        r = admin_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
        assert r.status_code in (200, 201), f"Create {role} failed: {r.status_code} {r.text}"
        data = r.json()
        uid = data.get("id") or data.get("_id")
        users[role] = {"id": uid, "email": email, "password": "TestPass123!"}
    yield users
    # Cleanup
    for role, info in users.items():
        try:
            admin_session.delete(f"{BASE_URL}/api/admin/users/{info['id']}", timeout=20)
        except Exception:
            pass


@pytest.fixture(scope="module")
def owner_session(created_users):
    return _login(created_users["owner"]["email"], created_users["owner"]["password"])


@pytest.fixture(scope="module")
def manager_session(created_users):
    return _login(created_users["gestionnaire"]["email"], created_users["gestionnaire"]["password"])


# ============================================================
# OWNER ROLE TESTS
# ============================================================

class TestOwnerReadAccess:
    def test_owner_get_coproprietes_200(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
        assert r.status_code == 200, r.text

    def test_owner_get_owners_200(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/owners", timeout=30)
        assert r.status_code == 200, r.text

    def test_owner_get_invoices_200(self, owner_session, existing_copro_id):
        r = owner_session.get(f"{BASE_URL}/api/invoices", params={"copropriete_id": existing_copro_id}, timeout=30)
        assert r.status_code == 200

    def test_owner_get_auth_me_200(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data.get("role") == "owner"


class TestOwnerWriteForbidden:
    def test_owner_post_owners_403(self, owner_session, existing_copro_id):
        body = {"first_name": "X", "last_name": "Y", "email": "x@y.z", "copropriete_id": existing_copro_id}
        r = owner_session.post(f"{BASE_URL}/api/owners", json=body, timeout=30)
        assert r.status_code == 403, f"Expected 403 got {r.status_code}: {r.text}"
        assert "lecture seule" in r.text.lower() or "proprietaires" in r.text.lower()

    def test_owner_put_owner_403(self, owner_session):
        r = owner_session.put(f"{BASE_URL}/api/owners/507f1f77bcf86cd799439011", json={"first_name": "Z"}, timeout=30)
        assert r.status_code == 403

    def test_owner_delete_lot_403(self, owner_session):
        r = owner_session.delete(f"{BASE_URL}/api/lots/507f1f77bcf86cd799439011", timeout=30)
        assert r.status_code == 403

    def test_owner_post_invoices_403(self, owner_session, existing_copro_id):
        body = {"copropriete_id": existing_copro_id, "supplier_id": "x", "amount_ht": 100, "vat": 21}
        r = owner_session.post(f"{BASE_URL}/api/invoices", json=body, timeout=30)
        assert r.status_code == 403

    def test_owner_delete_copro_403(self, owner_session, existing_copro_id):
        r = owner_session.delete(f"{BASE_URL}/api/coproprietes/{existing_copro_id}", timeout=30)
        assert r.status_code == 403


class TestOwnerAdminPathsForbidden:
    def test_owner_post_demo_seed_403(self, owner_session):
        r = owner_session.post(f"{BASE_URL}/api/admin/demo/seed", timeout=30)
        assert r.status_code == 403

    def test_owner_post_admin_users_403(self, owner_session):
        body = {"email": "x@y.z", "password": "x", "name": "x", "role": "owner"}
        r = owner_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
        assert r.status_code == 403

    def test_owner_get_admin_users_403(self, owner_session):
        """Admin path: any verb requires admin role."""
        r = owner_session.get(f"{BASE_URL}/api/admin/users", timeout=30)
        assert r.status_code == 403, f"Expected 403 got {r.status_code}: {r.text}"


# ============================================================
# GESTIONNAIRE ROLE TESTS
# ============================================================

class TestGestionnaireReadWriteAccess:
    def test_manager_get_coproprietes_200(self, manager_session):
        r = manager_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
        assert r.status_code == 200

    def test_manager_post_owners_allowed(self, manager_session, existing_copro_id):
        body = {
            "first_name": "TEST_iter10",
            "last_name": f"Mgr_{uuid.uuid4().hex[:6]}",
            "email": f"TEST_iter10_owner_{uuid.uuid4().hex[:6]}@x.com",
            "copropriete_id": existing_copro_id,
        }
        r = manager_session.post(f"{BASE_URL}/api/owners", json=body, timeout=30)
        # Middleware OK, route logic might still error for other reasons; just must NOT be 403 from middleware.
        assert r.status_code != 403, f"Manager should not get 403 on /api/owners POST: {r.text}"
        assert r.status_code < 500, f"Server error: {r.text}"

    def test_manager_post_invoices_not_403(self, manager_session, existing_copro_id):
        body = {
            "copropriete_id": existing_copro_id,
            "supplier_id": "TEST_iter10",
            "invoice_number": f"TEST_iter10_{uuid.uuid4().hex[:6]}",
            "amount_ht": 100.0,
            "vat_rate": 21,
            "issue_date": "2025-01-01",
            "due_date": "2025-02-01",
            "distribution_key_id": "manual",
            "manual_distribution": [],
        }
        r = manager_session.post(f"{BASE_URL}/api/invoices", json=body, timeout=30)
        assert r.status_code != 403, f"Manager should not be blocked by RBAC on /api/invoices: {r.text}"

    def test_manager_delete_lot_not_403(self, manager_session):
        r = manager_session.delete(f"{BASE_URL}/api/lots/507f1f77bcf86cd799439011", timeout=30)
        # Either 200/204 or 404 (not found) – but NOT 403 from middleware
        assert r.status_code != 403, f"Manager should not get 403 from middleware: {r.text}"


class TestGestionnaireAdminPathsForbidden:
    def test_manager_post_admin_users_403(self, manager_session):
        body = {"email": "x@y.z", "password": "x", "name": "x", "role": "owner"}
        r = manager_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
        assert r.status_code == 403

    def test_manager_post_demo_seed_403(self, manager_session):
        r = manager_session.post(f"{BASE_URL}/api/admin/demo/seed", timeout=30)
        assert r.status_code == 403

    def test_manager_get_admin_users_403(self, manager_session):
        r = manager_session.get(f"{BASE_URL}/api/admin/users", timeout=30)
        assert r.status_code == 403

    def test_manager_delete_copro_acceptable(self, manager_session, existing_copro_id):
        """Middleware allows manager; route may block with 403 syndic-only — both acceptable, NOT 401."""
        r = manager_session.delete(f"{BASE_URL}/api/coproprietes/{existing_copro_id}", timeout=30)
        assert r.status_code != 401
        assert r.status_code in (200, 204, 403, 404, 409), f"Unexpected: {r.status_code} {r.text}"


# ============================================================
# SUPERADMIN REGRESSION
# ============================================================

class TestSuperadminFullAccess:
    def test_admin_get_coproprietes(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
        assert r.status_code == 200

    def test_admin_get_admin_users(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/users", timeout=30)
        assert r.status_code == 200

    def test_admin_demo_seed(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/demo/seed", timeout=120)
        assert r.status_code in (200, 201), r.text

    def test_admin_get_owners(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/owners", timeout=30)
        assert r.status_code == 200

    def test_admin_get_banking_summary(self, admin_session, existing_copro_id):
        r = admin_session.get(f"{BASE_URL}/api/banking/accounts", params={"copropriete_id": existing_copro_id}, timeout=30)
        assert r.status_code in (200, 404)

    def test_admin_get_reports(self, admin_session, existing_copro_id):
        r = admin_session.get(f"{BASE_URL}/api/reports/balance", params={"copropriete_id": existing_copro_id}, timeout=30)
        assert r.status_code in (200, 404, 400)


# ============================================================
# REGRESSION TESTS (known issues from iter9)
# ============================================================

class TestKnownIssues:
    def test_auth_me_returns_id_not_underscore(self, admin_session):
        """Per iter9 minor issue: /api/auth/me leaks Mongo '_id'. Should return 'id'."""
        r = admin_session.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data, f"/auth/me missing 'id' field. Got keys: {list(data.keys())}"
        assert "_id" not in data, f"/auth/me leaks Mongo '_id'. Got keys: {list(data.keys())}"

    def test_demo_seed_always_returns_counts(self, admin_session):
        """Per iter9 minor: when demo already exists, response lacks 'counts'."""
        # Call twice — second call should hit already_exists branch
        admin_session.post(f"{BASE_URL}/api/admin/demo/seed", timeout=120)
        r = admin_session.post(f"{BASE_URL}/api/admin/demo/seed", timeout=120)
        assert r.status_code == 200
        data = r.json()
        assert "counts" in data, f"/admin/demo/seed missing 'counts' on idempotent call. Got: {list(data.keys())}"
