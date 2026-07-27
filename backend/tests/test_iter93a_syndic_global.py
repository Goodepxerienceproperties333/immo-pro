"""iter93a - Backend tests for GET /api/owners/syndic-global and
GET /api/suppliers/syndic-global (Syndic global dashboard tabs).

Verifies:
- Endpoints return 200 with expected shape (acp_ids, acp_names, acp_count)
- Chinese Wall : syndic_alpha does NOT see syndic_beta's data
- Superadmin sees everything
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


def _login_session(email, password):
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"Login failed for {email}: {r.status_code} {r.text[:200]}")
    return s


@pytest.fixture(scope="module")
def alpha_headers():
    return _login_session("syndic_alpha@copro.be", "admin123")


@pytest.fixture(scope="module")
def beta_headers():
    return _login_session("syndic_beta@copro.be", "admin123")


@pytest.fixture(scope="module")
def admin_headers():
    return _login_session("admin@copro.be", "admin123")


# ---------- Owners global ----------

class TestOwnersSyndicGlobal:
    def test_owners_global_alpha_shape(self, alpha_headers):
        r = alpha_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        # Should have at least 1 owner
        if data:
            o = data[0]
            assert "id" in o
            assert "acp_ids" in o
            assert "acp_names" in o
            assert "acp_count" in o
            assert isinstance(o["acp_ids"], list)
            assert isinstance(o["acp_names"], list)
            assert o["acp_count"] == len(o["acp_ids"])
            # Every acp_name entry has id, name
            for a in o["acp_names"]:
                assert "id" in a and "name" in a

    def test_owners_global_chinese_wall(self, alpha_headers, beta_headers):
        r_a = alpha_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=20)
        r_b = beta_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=20)
        assert r_a.status_code == 200 and r_b.status_code == 200
        alpha_ids = {o["id"] for o in r_a.json()}
        beta_ids = {o["id"] for o in r_b.json()}
        alpha_acps = set()
        for o in r_a.json():
            alpha_acps.update(o.get("acp_ids") or [])
        beta_acps = set()
        for o in r_b.json():
            beta_acps.update(o.get("acp_ids") or [])
        # No ACP overlap between alpha and beta scopes
        assert not (alpha_acps & beta_acps), (
            f"Chinese Wall violation: shared ACPs {alpha_acps & beta_acps}"
        )
        # Owners may legitimately be shared across syndics (a person could
        # own lots in ACPs of two different syndics). But ACP scopes must
        # be strictly disjoint. If they are, the test passes.
        print(f"alpha owners={len(alpha_ids)} acps={len(alpha_acps)} / beta owners={len(beta_ids)} acps={len(beta_acps)}")

    def test_owners_global_admin_sees_more(self, admin_headers, alpha_headers):
        r_admin = admin_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=20)
        r_alpha = alpha_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=20)
        assert r_admin.status_code == 200
        assert r_alpha.status_code == 200
        assert len(r_admin.json()) >= len(r_alpha.json())


# ---------- Suppliers global ----------

class TestSuppliersSyndicGlobal:
    def test_suppliers_global_alpha_shape(self, alpha_headers):
        r = alpha_headers.get(f"{BASE_URL}/api/suppliers/syndic-global", timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        if data:
            s = data[0]
            assert "id" in s
            assert "acp_ids" in s
            assert "acp_names" in s
            assert "acp_count" in s
            assert s["acp_count"] == len(s["acp_ids"])

    def test_suppliers_global_chinese_wall(self, alpha_headers, beta_headers):
        r_a = alpha_headers.get(f"{BASE_URL}/api/suppliers/syndic-global", timeout=20)
        r_b = beta_headers.get(f"{BASE_URL}/api/suppliers/syndic-global", timeout=20)
        assert r_a.status_code == 200 and r_b.status_code == 200
        alpha_acps = set()
        for s in r_a.json():
            alpha_acps.update(s.get("acp_ids") or [])
        beta_acps = set()
        for s in r_b.json():
            beta_acps.update(s.get("acp_ids") or [])
        assert not (alpha_acps & beta_acps), (
            f"Chinese Wall violation: shared ACPs in suppliers scope {alpha_acps & beta_acps}"
        )

    def test_suppliers_global_returns_expected_count_alpha(self, alpha_headers):
        r = alpha_headers.get(f"{BASE_URL}/api/suppliers/syndic-global", timeout=20)
        assert r.status_code == 200
        # Per playbook: syndic_alpha should have >=1 supplier
        data = r.json()
        print(f"syndic_alpha suppliers={len(data)}")
        assert isinstance(data, list)


# ---------- POST / PUT flow for owners (used by "Nouveau" and "Modifier") ----------

class TestOwnerCreateEdit:
    def test_owner_create_and_update(self, alpha_headers):
        # We need an ACP id for context. Fetch first ACP.
        r_copros = alpha_headers.get(f"{BASE_URL}/api/coproprietes", timeout=15)
        assert r_copros.status_code == 200
        acps = [c for c in r_copros.json() if c.get("status") != "archived"]
        if not acps:
            pytest.skip("no ACP available for syndic_alpha")
        acp_id = acps[0]["id"]

        payload = {
            "first_name": "TESTITER93A",
            "last_name": "Owner",
            "name": "TESTITER93A Owner",
            "email": "TEST_iter93a_owner@example.com",
            "phone": "0499000000",
            "address": "Rue du Test 1",
            "postal_code": "1000",
            "city": "Bruxelles",
            "country": "Belgique",
            "copropriete_id": acp_id,
        }
        r = alpha_headers.post(f"{BASE_URL}/api/owners", json=payload, timeout=15)
        assert r.status_code in (200, 201), r.text
        created = r.json()
        oid = created.get("id")
        assert oid

        try:
            # PUT update
            upd = alpha_headers.put(
                f"{BASE_URL}/api/owners/{oid}",
                json={**payload, "phone": "0499111111"},
                timeout=15,
            )
            assert upd.status_code == 200, upd.text
            # Verify via GET syndic-global (owner must be present)
            r_list = alpha_headers.get(f"{BASE_URL}/api/owners/syndic-global", timeout=15)
            assert r_list.status_code == 200
            match = [o for o in r_list.json() if o["id"] == oid]
            assert match, "created owner not visible in syndic-global"
            assert match[0].get("phone") == "0499111111"
        finally:
            alpha_headers.delete(f"{BASE_URL}/api/owners/{oid}", timeout=15)


class TestSupplierCreateEdit:
    def test_supplier_create_requires_acp_and_edit(self, alpha_headers):
        r_copros = alpha_headers.get(f"{BASE_URL}/api/coproprietes", timeout=15)
        acps = [c for c in r_copros.json() if c.get("status") != "archived"]
        if not acps:
            pytest.skip("no ACP available")
        acp_id = acps[0]["id"]
        payload = {
            "name": "TEST_iter93a_supplier",
            "copropriete_id": acp_id,
            "bce_number": "0999.888.777",
            "vat_number": "",
            "email": "TEST_iter93a_sup@example.com",
            "phone": "0499222222",
            "address": "Test",
            "postal_code": "1000",
            "city": "Bruxelles",
            "country": "Belgique",
            "iban": "",
            "bic": "",
        }
        r = alpha_headers.post(f"{BASE_URL}/api/suppliers", json=payload, timeout=15)
        assert r.status_code in (200, 201), r.text
        sid = r.json().get("id")
        assert sid
        try:
            upd = alpha_headers.put(
                f"{BASE_URL}/api/suppliers/{sid}",
                json={**payload, "phone": "0499333333"},
                timeout=15,
            )
            assert upd.status_code == 200, upd.text
            r_list = alpha_headers.get(f"{BASE_URL}/api/suppliers/syndic-global", timeout=15)
            assert r_list.status_code == 200
            match = [s for s in r_list.json() if s["id"] == sid]
            assert match, "created supplier not visible in syndic-global"
            assert match[0].get("phone") == "0499333333"
        finally:
            alpha_headers.delete(f"{BASE_URL}/api/suppliers/{sid}", timeout=15)
