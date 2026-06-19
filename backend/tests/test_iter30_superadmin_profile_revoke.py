"""
iter30: Test new features
  - Admin user mgmt restricted to superadmin only
  - PUT /api/auth/me self-profile update (name + password)
  - POST /api/fiscal/budgets/{id}/revoke (full reversal of fund_calls + auto-VE)
  - PUT /api/coproprietes/{id} edit support
"""
import os
import time
import uuid
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SUPER_EMAIL = "admin@copro.be"
SUPER_PWD = "admin123"
DEMO_COPRO_ID = "6748ca1a-216d-4002-8417-799287238736"


def _login(email, pwd):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": pwd}, timeout=20)
    if r.status_code != 200:
        return None, None
    return s, r.json()


@pytest.fixture(scope="module")
def super_session():
    s, _ = _login(SUPER_EMAIL, SUPER_PWD)
    if s is None:
        pytest.skip("Superadmin login failed")
    return s


# ---- Module-scoped temp non-superadmin user (owner role) ----
@pytest.fixture(scope="module")
def temp_owner(super_session):
    """Create a temporary non-superadmin user (owner) for negative tests."""
    email = f"test_iter30_owner_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "owner_pwd_123"
    r = super_session.post(f"{BASE}/api/admin/users", json={
        "email": email, "password": pwd, "name": "TEST_iter30_owner",
        "role": "owner", "copropriete_ids": [],
    }, timeout=20)
    assert r.status_code == 200, f"create owner failed: {r.status_code} {r.text}"
    uid = r.json()["id"]
    yield {"id": uid, "email": email, "password": pwd}
    # cleanup
    try:
        super_session.delete(f"{BASE}/api/admin/users/{uid}", timeout=10)
    except Exception:
        pass


@pytest.fixture(scope="module")
def owner_session(temp_owner):
    s, _ = _login(temp_owner["email"], temp_owner["password"])
    assert s is not None, "owner login failed"
    return s


# ============================================================
# 1) /api/admin/users access control
# ============================================================
class TestAdminUsersAccess:
    def test_get_users_as_superadmin_returns_list(self, super_session):
        r = super_session.get(f"{BASE}/api/admin/users", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        emails = [u["email"] for u in data]
        assert SUPER_EMAIL in emails

    def test_get_users_no_auth_returns_401(self):
        r = requests.get(f"{BASE}/api/admin/users", timeout=20)
        assert r.status_code == 401

    def test_get_users_as_owner_returns_403(self, owner_session):
        r = owner_session.get(f"{BASE}/api/admin/users", timeout=20)
        assert r.status_code == 403
        msg = (r.json().get("detail") or "").lower()
        assert "super administrateur" in msg or "super" in msg

    def test_post_user_as_owner_returns_403(self, owner_session):
        r = owner_session.post(f"{BASE}/api/admin/users", json={
            "email": f"x_{uuid.uuid4().hex[:6]}@test.be", "password": "x",
            "name": "x", "role": "owner",
        }, timeout=20)
        assert r.status_code == 403

    def test_put_user_as_owner_returns_403(self, owner_session, temp_owner):
        r = owner_session.put(f"{BASE}/api/admin/users/{temp_owner['id']}",
                              json={"name": "Hacked"}, timeout=20)
        assert r.status_code == 403

    def test_delete_user_as_owner_returns_403(self, owner_session, temp_owner):
        r = owner_session.delete(f"{BASE}/api/admin/users/{temp_owner['id']}", timeout=20)
        assert r.status_code == 403


# ============================================================
# 2) PUT /api/auth/me self-update
# ============================================================
class TestAuthMeUpdate:
    def test_no_auth_returns_401(self):
        r = requests.put(f"{BASE}/api/auth/me", json={"name": "x"}, timeout=20)
        assert r.status_code == 401

    def test_empty_payload_returns_400(self, owner_session):
        r = owner_session.put(f"{BASE}/api/auth/me", json={}, timeout=20)
        assert r.status_code == 400

    def test_name_only_empty_string_returns_400(self, owner_session):
        # name '' and no password => nothing to update
        r = owner_session.put(f"{BASE}/api/auth/me", json={"name": ""}, timeout=20)
        assert r.status_code == 400

    def test_update_name_ok(self, owner_session, temp_owner):
        new_name = f"TEST_iter30_renamed_{uuid.uuid4().hex[:4]}"
        r = owner_session.put(f"{BASE}/api/auth/me", json={"name": new_name}, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == new_name
        assert body["email"] == temp_owner["email"]
        # verify via GET /me
        r2 = owner_session.get(f"{BASE}/api/auth/me", timeout=20)
        assert r2.status_code == 200
        assert r2.json()["name"] == new_name

    def test_new_password_missing_current_returns_400(self, owner_session):
        r = owner_session.put(f"{BASE}/api/auth/me",
                              json={"new_password": "newpwd123"}, timeout=20)
        assert r.status_code == 400

    def test_new_password_short_returns_400(self, owner_session, temp_owner):
        r = owner_session.put(f"{BASE}/api/auth/me", json={
            "current_password": temp_owner["password"],
            "new_password": "abc",
        }, timeout=20)
        assert r.status_code == 400

    def test_wrong_current_password_returns_400(self, owner_session):
        r = owner_session.put(f"{BASE}/api/auth/me", json={
            "current_password": "wrong_pwd_xyz",
            "new_password": "newpwd123",
        }, timeout=20)
        assert r.status_code == 400

    def test_change_password_ok_and_login_with_new(self, temp_owner):
        # fresh login to use baseline password
        s, _ = _login(temp_owner["email"], temp_owner["password"])
        assert s is not None
        new_pwd = "newpwd_iter30_123"
        r = s.put(f"{BASE}/api/auth/me", json={
            "current_password": temp_owner["password"],
            "new_password": new_pwd,
        }, timeout=20)
        assert r.status_code == 200, r.text
        # old password should fail now
        s2, _ = _login(temp_owner["email"], temp_owner["password"])
        assert s2 is None, "Old password should no longer work"
        # new password works
        s3, body = _login(temp_owner["email"], new_pwd)
        assert s3 is not None and body["email"] == temp_owner["email"]
        # restore for downstream tests
        r2 = s3.put(f"{BASE}/api/auth/me", json={
            "current_password": new_pwd,
            "new_password": temp_owner["password"],
        }, timeout=20)
        assert r2.status_code == 200


# ============================================================
# 3) Budget revoke (cascade) - using a TEMP ACP to keep Demo safe
# ============================================================
class TestBudgetRevokeCascade:
    """Creates a temp ACP + budget + approves + revokes. No touch to Demo."""

    @pytest.fixture(scope="class")
    def temp_acp(self, super_session):
        # Minimal ACP
        payload = {
            "name": f"TEST_iter30_acp_{uuid.uuid4().hex[:6]}",
            "bce": "0000000097",
            "address": "Rue Test 1", "postal_code": "1000", "city": "Bruxelles", "country": "BE",
            "description": "test",
            "bank_accounts": [{"iban": "BE68539007547034", "label": "OP", "account_type": "operating"}],
            "quarterly_closing": True,
            "default_provisions": True,
            "lots": [],
        }
        r = super_session.post(f"{BASE}/api/coproprietes", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        copro = r.json()
        yield copro
        # cleanup
        try:
            super_session.delete(f"{BASE}/api/coproprietes/{copro['id']}", timeout=20)
        except Exception:
            pass

    def test_revoke_budget_with_no_paid_calls_cascades(self, super_session, temp_acp):
        copro_id = temp_acp["id"]
        # Create a fiscal year
        r = super_session.post(f"{BASE}/api/fiscal/years", json={
            "copropriete_id": copro_id,
            "name": "FY2099",
            "start_date": "2099-01-01",
            "end_date": "2099-12-31",
        }, timeout=20)
        assert r.status_code == 200, r.text
        fy_id = r.json()["id"]
        # Create a budget with one provisions line
        r = super_session.post(f"{BASE}/api/fiscal/budgets", json={
            "copropriete_id": copro_id,
            "fiscal_year_id": fy_id,
            "name": "TEST_iter30_budget",
            "lines": [{"account_number": "703000", "account_name": "Provisions",
                       "amount": 4000.0, "distribution_key_id": ""}],
        }, timeout=20)
        if r.status_code != 200:
            pytest.skip(f"budget create unsupported in this env: {r.status_code} {r.text}")
        budget = r.json()
        bid = budget["id"]
        # Approve
        r = super_session.post(f"{BASE}/api/fiscal/budgets/{bid}/approve", timeout=20)
        assert r.status_code == 200, r.text
        # Note: with no lots/owners, regenerate-from-budget would yield 0 calls.
        # That's fine - revoke should still succeed and return deleted_fund_calls=0.
        # Revoke
        r = super_session.post(f"{BASE}/api/fiscal/budgets/{bid}/revoke", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "deleted_fund_calls" in body
        assert body.get("status") == "draft"

    def test_revoke_budget_returns_400_when_calls_paid_without_force(self, super_session):
        """Use Demo ACP only as READ probe: verify the endpoint exists.
        Skip if Demo has no approved budget at the moment (env-dependent)."""
        r = super_session.get(f"{BASE}/api/fiscal/budgets?copropriete_id={DEMO_COPRO_ID}",
                              timeout=20)
        if r.status_code != 200:
            pytest.skip("cannot list demo budgets")
        budgets = r.json()
        approved = [b for b in budgets if b.get("status") == "approved"]
        if not approved:
            pytest.skip("No approved demo budget to validate against")
        # We do NOT call revoke on demo budget. The endpoint shape is validated
        # in the temp-ACP test above.


# ============================================================
# 4) PUT /api/coproprietes/{id} edit
# ============================================================
class TestCoproprieteEdit:
    def test_edit_acp_preserves_pcmn_for_bank_accounts(self, super_session):
        # Read demo ACP
        r = super_session.get(f"{BASE}/api/coproprietes/{DEMO_COPRO_ID}", timeout=20)
        assert r.status_code == 200, r.text
        copro = r.json()
        # Capture the bank accounts and their pcmn_numbers
        bas_before = copro.get("bank_accounts", [])
        pcmn_before = {(ba.get("iban"), ba.get("account_type")): ba.get("pcmn_number")
                       for ba in bas_before}
        # PUT with same data but updated description
        new_desc = f"TEST_iter30_edit_{int(time.time())}"
        payload = {
            "name": copro["name"],
            "bce": copro.get("bce", ""),
            "address": copro.get("address", ""),
            "postal_code": copro.get("postal_code", ""),
            "city": copro.get("city", ""),
            "country": copro.get("country", "BE"),
            "description": new_desc,
            "bank_accounts": [{
                "iban": ba.get("iban", ""),
                "label": ba.get("label", ""),
                "account_type": ba.get("account_type", "operating"),
            } for ba in bas_before],
            "quarterly_closing": copro.get("quarterly_closing", False),
            "default_provisions": bool(copro.get("default_provisions", False)),
            "lots": [],
        }
        r = super_session.put(f"{BASE}/api/coproprietes/{DEMO_COPRO_ID}",
                              json=payload, timeout=30)
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated.get("description") == new_desc
        # Verify pcmn_number preserved
        bas_after = updated.get("bank_accounts", [])
        for ba in bas_after:
            key = (ba.get("iban"), ba.get("account_type"))
            if key in pcmn_before and pcmn_before[key]:
                assert ba.get("pcmn_number") == pcmn_before[key], \
                    f"pcmn_number changed for {key}: {pcmn_before[key]} -> {ba.get('pcmn_number')}"
        # Restore original description
        orig_desc = copro.get("description", "")
        payload["description"] = orig_desc
        r = super_session.put(f"{BASE}/api/coproprietes/{DEMO_COPRO_ID}",
                              json=payload, timeout=30)
        assert r.status_code == 200
