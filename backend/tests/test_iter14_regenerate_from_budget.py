"""Iter14: POST /api/fund-calls/regenerate-from-budget endpoint tests.

Covers:
- 404 if budget does not exist
- 400 if budget not approved
- Deletes future unpaid fund_calls linked to budget then regenerates schedule
- Preserves calls with at least one paid distribution row
- Doesn't touch calls with date < start_date
- Idempotence: second call deletes all created from first run
- RBAC: owner gets 403
- Regression: generate-from-budget still works with lines/reserve_amount/budget_id
"""
import os
import pytest
import requests
from pathlib import Path


def _load_backend_url():
    url = os.environ.get('REACT_APP_BACKEND_URL')
    if url:
        return url.rstrip('/')
    envp = Path('/app/frontend/.env')
    if envp.exists():
        for line in envp.read_text().splitlines():
            if line.startswith('REACT_APP_BACKEND_URL='):
                return line.split('=', 1)[1].strip().rstrip('/')
    raise RuntimeError("REACT_APP_BACKEND_URL not set")


BASE_URL = _load_backend_url()


# ---------- Fixtures ----------
@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def owner_session(admin_session):
    email = "TEST_iter14_owner@example.com"
    pwd = "ownerpass123"
    payload = {"email": email, "name": "Test Iter14 Owner",
               "password": pwd, "role": "owner"}
    r = admin_session.post(f"{BASE_URL}/api/admin/users", json=payload)
    user_id = None
    if r.status_code in (200, 201):
        user_id = r.json().get("id")
    elif r.status_code == 400 and "existe" in (r.text or "").lower():
        u = admin_session.get(f"{BASE_URL}/api/admin/users")
        if u.status_code == 200:
            for x in u.json():
                if x.get("email") == email:
                    user_id = x.get("id")
                    break
    else:
        pytest.skip(f"Cannot create owner: {r.status_code} {r.text}")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    lr = s.post(f"{BASE_URL}/api/auth/login",
                json={"email": email, "password": pwd})
    if lr.status_code != 200:
        pytest.skip(f"Owner login failed: {lr.text}")
    yield s
    if user_id:
        try:
            admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}")
        except Exception:
            pass


@pytest.fixture(scope="module")
def copro_id(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/coproprietes")
    assert r.status_code == 200
    coprs = r.json()
    if not coprs:
        pytest.skip("No coproprietes available")
    return coprs[0]["id"]


@pytest.fixture(scope="module")
def fiscal_year(admin_session, copro_id):
    r = admin_session.get(f"{BASE_URL}/api/fiscal/years",
                          params={"copropriete_id": copro_id})
    assert r.status_code == 200
    years = r.json()
    open_y = next((y for y in years if y.get("status") == "open"), None)
    if not open_y:
        payload = {"name": "TEST_iter14_FY", "start_date": "2026-01-01",
                   "end_date": "2026-12-31", "copropriete_id": copro_id}
        cr = admin_session.post(f"{BASE_URL}/api/fiscal/years", json=payload)
        assert cr.status_code == 200, cr.text
        open_y = cr.json()
    return open_y


@pytest.fixture(scope="module")
def distribution_keys(admin_session, copro_id):
    r = admin_session.get(f"{BASE_URL}/api/properties/distribution-keys",
                          params={"copropriete_id": copro_id})
    if r.status_code != 200:
        r = admin_session.get(f"{BASE_URL}/api/distribution-keys",
                              params={"copropriete_id": copro_id})
    return r.json() if r.status_code == 200 else []


def _cleanup_fund_calls_for_budget(admin_session, budget_id):
    """Remove every fund_call linked to budget_id."""
    r = admin_session.get(f"{BASE_URL}/api/fund-calls")
    if r.status_code != 200:
        return
    for c in r.json():
        if c.get("budget_id") == budget_id:
            try:
                admin_session.delete(f"{BASE_URL}/api/fund-calls/{c['id']}")
            except Exception:
                pass


def _create_approved_budget(admin_session, fiscal_year, copro_id,
                            distribution_keys, name="TEST_iter14_Budget"):
    dk_id = distribution_keys[0]["id"] if distribution_keys else ""
    payload = {
        "fiscal_year_id": fiscal_year["id"],
        "name": name,
        "copropriete_id": copro_id,
        "lines": [
            {"account_number": "615000", "account_name": "Entretien",
             "amount": 8000.0, "distribution_key_id": dk_id},
            {"account_number": "606100", "account_name": "Eau",
             "amount": 4000.0, "distribution_key_id": ""},
        ],
    }
    r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    a = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{bid}/approve")
    assert a.status_code == 200, a.text
    return a.json()


def _delete_budget(admin_session, budget_id):
    try:
        admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/revoke")
        admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")
    except Exception:
        pass


# ---------- Error cases ----------
class TestRegenerateErrors:
    def test_404_budget_inexistant(self, admin_session, copro_id):
        body = {"budget_id": "non-existent-budget-id-xyz",
                "frequency": 4, "start_date": "2026-03-15",
                "due_offset_days": 30, "copropriete_id": copro_id}
        r = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget", json=body)
        assert r.status_code == 404, r.text

    def test_400_budget_non_approuve(self, admin_session, fiscal_year, copro_id):
        # Create draft budget
        payload = {"fiscal_year_id": fiscal_year["id"],
                   "name": "TEST_iter14_DraftRegen",
                   "copropriete_id": copro_id,
                   "lines": [{"account_number": "615000",
                              "account_name": "x", "amount": 1000.0,
                              "distribution_key_id": ""}]}
        r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
        assert r.status_code == 200
        bid = r.json()["id"]
        try:
            r2 = admin_session.post(
                f"{BASE_URL}/api/fund-calls/regenerate-from-budget",
                json={"budget_id": bid, "frequency": 4,
                      "start_date": "2026-03-15",
                      "copropriete_id": copro_id})
            assert r2.status_code == 400, r2.text
        finally:
            admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{bid}")


# ---------- Main regenerate flow ----------
class TestRegenerateFlow:
    @pytest.fixture(scope="class")
    def approved_budget(self, admin_session, fiscal_year, copro_id,
                        distribution_keys):
        b = _create_approved_budget(admin_session, fiscal_year, copro_id,
                                    distribution_keys,
                                    name="TEST_iter14_RegenBudget")
        yield b
        _cleanup_fund_calls_for_budget(admin_session, b["id"])
        _delete_budget(admin_session, b["id"])

    def test_regenerate_response_shape(self, admin_session, approved_budget, copro_id):
        # First generate 4 quarterly calls
        body = {"budget_id": approved_budget["id"], "frequency": 4,
                "start_date": "2026-03-15", "due_offset_days": 30,
                "copropriete_id": copro_id}
        g = admin_session.post(
            f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body)
        assert g.status_code == 200, g.text
        gen_ids = g.json()["created_ids"]
        assert len(gen_ids) == 4

        # Regenerate now (no payments yet) -> deletes all 4, recreates 4
        r = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is True
        assert "calls" in data
        assert "summary" in data
        assert "created_ids" in data
        assert "deleted_count" in data
        assert "preserved_count" in data
        assert data["deleted_count"] == 4
        assert data["preserved_count"] == 0
        assert len(data["created_ids"]) == 4
        # Old ids must no longer be retrievable
        for cid in gen_ids:
            g2 = admin_session.get(f"{BASE_URL}/api/fund-calls/{cid}")
            assert g2.status_code == 404
        # New ids should be retrievable with budget_id + lines + reserve_amount
        for cid in data["created_ids"]:
            fc = admin_session.get(f"{BASE_URL}/api/fund-calls/{cid}").json()
            assert fc["budget_id"] == approved_budget["id"]
            assert "lines" in fc
            assert "reserve_amount" in fc

        # cleanup
        _cleanup_fund_calls_for_budget(admin_session, approved_budget["id"])

    def test_preserve_paid_call(self, admin_session, approved_budget, copro_id):
        body = {"budget_id": approved_budget["id"], "frequency": 4,
                "start_date": "2026-03-15", "due_offset_days": 30,
                "copropriete_id": copro_id}
        g = admin_session.post(
            f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body)
        assert g.status_code == 200
        created = g.json()["created_ids"]
        assert len(created) == 4

        # Mark first owner of call #2 as paid
        # Fetch call to find an owner
        fc2 = admin_session.get(f"{BASE_URL}/api/fund-calls/{created[1]}").json()
        dist = fc2.get("distribution", [])
        assert dist, "No distribution on call 2"
        owner_id = dist[0]["owner_id"]
        mp = admin_session.post(
            f"{BASE_URL}/api/fund-calls/{created[1]}/mark-paid",
            params={"owner_id": owner_id})
        assert mp.status_code == 200, mp.text

        # Regenerate - should preserve call 2, delete the other 3
        r = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["deleted_count"] == 3, f"deleted={data}"
        assert data["preserved_count"] == 1
        assert len(data["created_ids"]) == 4

        # Verify preserved call still exists
        kept = admin_session.get(f"{BASE_URL}/api/fund-calls/{created[1]}")
        assert kept.status_code == 200
        # Other old ones gone
        for cid in (created[0], created[2], created[3]):
            assert admin_session.get(
                f"{BASE_URL}/api/fund-calls/{cid}").status_code == 404

        _cleanup_fund_calls_for_budget(admin_session, approved_budget["id"])

    def test_calls_before_start_date_untouched(self, admin_session,
                                               approved_budget, copro_id):
        """Calls with date < start_date must NEVER be deleted/preserved/counted."""
        # Generate calls starting early in year
        body_early = {"budget_id": approved_budget["id"], "frequency": 4,
                      "start_date": "2026-01-15", "due_offset_days": 30,
                      "copropriete_id": copro_id}
        g = admin_session.post(
            f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body_early)
        assert g.status_code == 200
        early_ids = g.json()["created_ids"]
        # Dates: 01-15, 04-15, 07-15, 10-15
        # Regenerate from 2026-07-01: should touch only calls dated >= 2026-07-01
        body_regen = {"budget_id": approved_budget["id"], "frequency": 2,
                      "start_date": "2026-07-01", "due_offset_days": 30,
                      "copropriete_id": copro_id}
        r = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget",
            json=body_regen)
        assert r.status_code == 200, r.text
        data = r.json()
        # Only 07-15 and 10-15 fall in window -> delete 2, preserve 0
        assert data["deleted_count"] == 2
        assert data["preserved_count"] == 0

        # The first two (01-15, 04-15) still in DB
        for cid in early_ids[:2]:
            assert admin_session.get(
                f"{BASE_URL}/api/fund-calls/{cid}").status_code == 200
        # Last two gone
        for cid in early_ids[2:]:
            assert admin_session.get(
                f"{BASE_URL}/api/fund-calls/{cid}").status_code == 404

        _cleanup_fund_calls_for_budget(admin_session, approved_budget["id"])

    def test_idempotence(self, admin_session, approved_budget, copro_id):
        body = {"budget_id": approved_budget["id"], "frequency": 4,
                "start_date": "2026-03-15", "due_offset_days": 30,
                "copropriete_id": copro_id}
        r1 = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget", json=body)
        assert r1.status_code == 200, r1.text
        n_created_1 = len(r1.json()["created_ids"])
        # Run a 2nd time with identical params - all freshly created are unpaid
        r2 = admin_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget", json=body)
        assert r2.status_code == 200, r2.text
        data2 = r2.json()
        assert data2["deleted_count"] == n_created_1, \
            f"Expected to delete {n_created_1}, got {data2['deleted_count']}"
        assert data2["preserved_count"] == 0

        _cleanup_fund_calls_for_budget(admin_session, approved_budget["id"])


# ---------- RBAC ----------
class TestRBAC:
    def test_owner_cannot_regenerate(self, owner_session, copro_id):
        r = owner_session.post(
            f"{BASE_URL}/api/fund-calls/regenerate-from-budget",
            json={"budget_id": "any-id", "frequency": 4,
                  "start_date": "2026-03-15", "copropriete_id": copro_id})
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ---------- Regression on generate-from-budget ----------
class TestRegressionGenerate:
    def test_generate_includes_lines_reserve_budget_id(
            self, admin_session, fiscal_year, copro_id, distribution_keys):
        b = _create_approved_budget(admin_session, fiscal_year, copro_id,
                                    distribution_keys,
                                    name="TEST_iter14_RegressGen")
        try:
            body = {"budget_id": b["id"], "frequency": 2,
                    "start_date": "2026-04-01", "due_offset_days": 30,
                    "reserve_fund": {"enabled": True, "amount": 2000.0,
                                     "distribution_key_id": "",
                                     "label": "Reserve test"},
                    "copropriete_id": copro_id}
            g = admin_session.post(
                f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body)
            assert g.status_code == 200, g.text
            data = g.json()
            assert data["persisted"] is True
            assert len(data["created_ids"]) == 2

            # Fetch and verify persisted fields
            ids = data["created_ids"]
            c1 = admin_session.get(f"{BASE_URL}/api/fund-calls/{ids[0]}").json()
            c2 = admin_session.get(f"{BASE_URL}/api/fund-calls/{ids[1]}").json()
            for fc in (c1, c2):
                assert fc.get("budget_id") == b["id"]
                assert "lines" in fc and isinstance(fc["lines"], list)
                assert "reserve_amount" in fc
            # Reserve on call 1 only
            assert c1["reserve_amount"] == 2000.0
            assert c2["reserve_amount"] == 0
            # First call has a reserve line
            assert any(ln.get("is_reserve") for ln in c1["lines"])
            assert all(not ln.get("is_reserve") for ln in c2["lines"])
        finally:
            _cleanup_fund_calls_for_budget(admin_session, b["id"])
            _delete_budget(admin_session, b["id"])
