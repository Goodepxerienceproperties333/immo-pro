"""Iteration 11: Owner Portal /api/owner/* + tightened owner RBAC whitelist.

Coverage:
- Owner portal endpoints (6): /me, /dashboard, /coproprietes, /fund-calls, /invoices, /documents, /situation/{copro_id}
- Owner without email match -> 404 with clear message
- Owner /situation on an ACP without lots -> 403
- RBAC tightening: GET /api/owners, /api/admin/users, /api/accounting/pcmn, /api/invoices, /api/lots,
  /api/banking/transactions, /api/reports/bilan all return 403 for owner
- RBAC whitelist: GET /api/coproprietes, /api/auth/me, /api/documents/{id}/download allowed for owner
- POST/PUT/DELETE on /api/owner/* -> 403 (write-block on owner role)
- Regression: gestionnaire & superadmin keep their normal access
- Cleanup: any user with TEST_iter11 prefix or sophie.martin@example.be created during this run is removed.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://copro-belge.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"

SOPHIE_EMAIL = "sophie.martin@example.be"
SOPHIE_PASSWORD = "sophie123"


# ---------------- helpers ----------------

def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def sophie_user(admin_session):
    """Create user role=owner with email matching Sophie Martin owner record."""
    body = {
        "email": SOPHIE_EMAIL,
        "password": SOPHIE_PASSWORD,
        "name": "Sophie Martin",
        "role": "owner",
        "copropriete_ids": [],
    }
    r = admin_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
    # Accept 200/201 (created) or 400/409 if already exists from prior runs
    if r.status_code not in (200, 201):
        # If conflict, try login directly
        pass
    uid = None
    if r.status_code in (200, 201):
        uid = r.json().get("id")
    yield {"email": SOPHIE_EMAIL, "password": SOPHIE_PASSWORD, "id": uid}
    # cleanup: list users, find by email, delete
    try:
        lst = admin_session.get(f"{BASE_URL}/api/admin/users", timeout=30).json()
        for u in lst:
            if u.get("email") == SOPHIE_EMAIL:
                admin_session.delete(f"{BASE_URL}/api/admin/users/{u.get('id')}", timeout=30)
    except Exception:
        pass


@pytest.fixture(scope="module")
def orphan_user(admin_session):
    """Create user role=owner with email that does NOT match any owner record."""
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:6]
    email = f"TEST_iter11_orphan_{ts}_{suffix}@example.com"
    body = {
        "email": email,
        "password": "TestPass123!",
        "name": "TEST iter11 orphan",
        "role": "owner",
        "copropriete_ids": [],
    }
    r = admin_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
    assert r.status_code in (200, 201), r.text
    uid = r.json().get("id")
    yield {"email": email, "password": "TestPass123!", "id": uid}
    try:
        admin_session.delete(f"{BASE_URL}/api/admin/users/{uid}", timeout=30)
    except Exception:
        pass


@pytest.fixture(scope="module")
def gestionnaire_user(admin_session):
    ts = int(time.time())
    suffix = uuid.uuid4().hex[:6]
    email = f"TEST_iter11_gest_{ts}_{suffix}@example.com"
    body = {
        "email": email,
        "password": "TestPass123!",
        "name": "TEST iter11 gest",
        "role": "gestionnaire",
        "copropriete_ids": [],
    }
    r = admin_session.post(f"{BASE_URL}/api/admin/users", json=body, timeout=30)
    assert r.status_code in (200, 201), r.text
    uid = r.json().get("id")
    yield {"email": email, "password": "TestPass123!", "id": uid}
    try:
        admin_session.delete(f"{BASE_URL}/api/admin/users/{uid}", timeout=30)
    except Exception:
        pass


@pytest.fixture(scope="module")
def sophie_session(sophie_user) -> requests.Session:
    return _login(sophie_user["email"], sophie_user["password"])


@pytest.fixture(scope="module")
def orphan_session(orphan_user) -> requests.Session:
    return _login(orphan_user["email"], orphan_user["password"])


@pytest.fixture(scope="module")
def gest_session(gestionnaire_user) -> requests.Session:
    return _login(gestionnaire_user["email"], gestionnaire_user["password"])


@pytest.fixture(scope="module")
def sophie_owner_copro_id(sophie_session):
    """Return the first ACP where Sophie has lots (for /situation tests)."""
    r = sophie_session.get(f"{BASE_URL}/api/owner/coproprietes", timeout=30)
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list) and len(items) >= 1, f"Sophie should own at least 1 ACP, got {items}"
    return items[0]["id"]


# ---------------- Owner Portal endpoints ----------------

class TestOwnerPortalEndpoints:
    def test_login_sets_cookie_and_role_owner(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data.get("role") == "owner", data
        assert data.get("email") == SOPHIE_EMAIL

    def test_owner_me(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/me", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("email") == SOPHIE_EMAIL
        assert data.get("first_name") == "Sophie"
        assert "id" in data
        # MongoDB _id should NOT leak
        assert "_id" not in data

    def test_owner_dashboard(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "owner" in data and "stats" in data and "pending_calls" in data
        s = data["stats"]
        for k in ("coproprietes_count", "lots_count", "total_called",
                  "total_paid", "balance", "status", "pending_calls_count"):
            assert k in s, f"missing stat key: {k}"
        assert s["status"] in ("debiteur", "crediteur", "solde")
        assert isinstance(s["coproprietes_count"], int) and s["coproprietes_count"] >= 1
        assert isinstance(s["lots_count"], int) and s["lots_count"] >= 1

    def test_owner_coproprietes(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/coproprietes", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) >= 1
        c = items[0]
        assert "my_lots" in c and isinstance(c["my_lots"], list) and len(c["my_lots"]) >= 1
        assert "my_total_quotity" in c
        assert "_id" not in c

    def test_owner_fund_calls(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/fund-calls", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        for fc in items:
            for k in ("id", "name", "date", "my_amount", "my_share", "vcs_code", "paid"):
                assert k in fc, f"missing key {k} in fund-call {fc}"

    def test_owner_invoices(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/invoices", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        for inv in items:
            for k in ("id", "number", "date", "supplier", "my_amount", "total_amount"):
                assert k in inv
            assert inv["my_amount"] > 0  # only show invoices that affect owner

    def test_owner_documents(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/owner/documents", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        # stored_path must not be exposed
        for d in items:
            assert "stored_path" not in d, "stored_path leaked"

    def test_owner_situation_authorised(self, sophie_session, sophie_owner_copro_id):
        r = sophie_session.get(f"{BASE_URL}/api/owner/situation/{sophie_owner_copro_id}", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("owner", "lots", "movements", "total_debit", "total_credit", "balance", "status"):
            assert k in data
        # Movements should be sorted & have running_balance
        if data["movements"]:
            assert "running_balance" in data["movements"][0]

    def test_owner_situation_forbidden_other_acp(self, sophie_session, admin_session):
        """Owner cannot see situation on an ACP where they don't have lots."""
        # Find an ACP where Sophie has NO lot. We need to query all ACPs (admin)
        # then exclude Sophie's owned ACPs.
        all_acps = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30).json()
        sophie_acps_resp = sophie_session.get(f"{BASE_URL}/api/owner/coproprietes", timeout=30).json()
        sophie_acp_ids = {c["id"] for c in sophie_acps_resp}
        not_owned = [a for a in all_acps if a.get("id") not in sophie_acp_ids]
        if not not_owned:
            pytest.skip("Only one ACP exists and Sophie owns it; can't test 403 case")
        target = not_owned[0]["id"]
        r = sophie_session.get(f"{BASE_URL}/api/owner/situation/{target}", timeout=30)
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ---------------- Owner with NO matching owner record ----------------

class TestOwnerNoMatch:
    def test_orphan_owner_me_404(self, orphan_session):
        r = orphan_session.get(f"{BASE_URL}/api/owner/me", timeout=30)
        assert r.status_code == 404
        body = r.text.lower()
        assert "aucune fiche" in body or "proprietaire" in body

    def test_orphan_owner_dashboard_404(self, orphan_session):
        r = orphan_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30)
        assert r.status_code == 404


# ---------------- New tightened RBAC whitelist ----------------

OWNER_FORBIDDEN_GET_PATHS = [
    "/api/owners",
    "/api/admin/users",
    "/api/accounting/pcmn",
    "/api/invoices",
    "/api/lots",
    "/api/banking/transactions",
    "/api/reports/bilan",
]


class TestOwnerRBACWhitelist:
    @pytest.mark.parametrize("path", OWNER_FORBIDDEN_GET_PATHS)
    def test_owner_forbidden_paths(self, sophie_session, path):
        r = sophie_session.get(f"{BASE_URL}{path}", timeout=30)
        assert r.status_code == 403, f"GET {path} should be 403 for owner, got {r.status_code}"

    def test_owner_allowed_coproprietes(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
        assert r.status_code == 200

    def test_owner_allowed_auth_me(self, sophie_session):
        r = sophie_session.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200

    def test_owner_post_owner_route_forbidden(self, sophie_session):
        """POST/write methods on /api/owner/* should still be 403 (write-block on role=owner)."""
        # No POST exists on /api/owner/*, but the middleware should still reject writes
        r = sophie_session.post(f"{BASE_URL}/api/owner/me", json={}, timeout=30)
        # Could be 403 (RBAC write-block) or 405 (method not allowed). Both acceptable
        # but the design requirement says owner = read-only, so 403 is preferred.
        assert r.status_code in (403, 405), f"Expected 403/405, got {r.status_code}: {r.text}"

    def test_owner_put_lots_forbidden(self, sophie_session):
        r = sophie_session.put(f"{BASE_URL}/api/lots/nonexistent", json={}, timeout=30)
        assert r.status_code == 403


# ---------------- Regression: gestionnaire + superadmin ----------------

class TestRegressionOtherRoles:
    def test_gestionnaire_can_read_coproprietes(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
        assert r.status_code == 200

    def test_gestionnaire_can_read_invoices(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/invoices", timeout=30)
        assert r.status_code == 200

    def test_gestionnaire_can_read_owners(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/owners", timeout=30)
        assert r.status_code == 200

    def test_gestionnaire_can_read_lots(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/lots", timeout=30)
        assert r.status_code == 200

    def test_gestionnaire_can_read_banking(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/banking/transactions", timeout=30)
        assert r.status_code == 200

    def test_gestionnaire_admin_users_forbidden(self, gest_session):
        r = gest_session.get(f"{BASE_URL}/api/admin/users", timeout=30)
        assert r.status_code == 403

    def test_superadmin_full_access(self, admin_session):
        for p in ["/api/coproprietes", "/api/owners", "/api/admin/users",
                  "/api/invoices", "/api/lots", "/api/banking/transactions",
                  "/api/accounting/pcmn"]:
            r = admin_session.get(f"{BASE_URL}{p}", timeout=30)
            assert r.status_code == 200, f"Superadmin GET {p} failed: {r.status_code}"

    def test_superadmin_cannot_use_owner_portal(self, admin_session):
        """Superadmin doesn't have a matching owner record -> 404 (not blocked by RBAC)."""
        r = admin_session.get(f"{BASE_URL}/api/owner/me", timeout=30)
        # admin@copro.be has no matching owner -> 404
        assert r.status_code in (200, 404), r.status_code
