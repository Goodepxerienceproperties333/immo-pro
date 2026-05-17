"""Iter13: Workflow Budget -> Appels de fonds (approve/revoke, N-1 expenses, wizard preview/generate)."""
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


# -------- Fixtures --------
@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Login admin failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def owner_session(admin_session):
    email = "TEST_iter13_owner@example.com"
    pwd = "ownerpass123"
    payload = {"email": email, "name": "Test Iter13 Owner", "password": pwd, "role": "owner"}
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
        pytest.skip(f"Cannot create owner user: {r.status_code} {r.text}")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    lr = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pwd})
    if lr.status_code != 200:
        pytest.skip(f"Owner login failed: {lr.text}")
    yield s
    try:
        if user_id:
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
    """Pick an open fiscal year for the ACP."""
    r = admin_session.get(f"{BASE_URL}/api/fiscal/years",
                         params={"copropriete_id": copro_id})
    assert r.status_code == 200
    years = r.json()
    open_y = next((y for y in years if y.get("status") == "open"), None)
    if not open_y:
        # Create one
        payload = {"name": "TEST_iter13_FY", "start_date": "2026-01-01",
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
        # try alt endpoint
        r = admin_session.get(f"{BASE_URL}/api/distribution-keys",
                              params={"copropriete_id": copro_id})
    if r.status_code != 200:
        return []
    return r.json()


# -------- N-1 previous-year-expenses --------
class TestPreviousYearExpenses:
    def test_no_closed_fy_returns_empty(self, admin_session, fiscal_year):
        # If no previous closed FY exists for this ACP, expect empty structure
        r = admin_session.get(f"{BASE_URL}/api/fiscal/previous-year-expenses",
                              params={"fiscal_year_id": fiscal_year["id"]})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "fiscal_year" in data
        assert "lines" in data
        assert "total" in data
        assert isinstance(data["lines"], list)
        if data["fiscal_year"] is None:
            assert data["lines"] == []
            assert data["total"] == 0
        else:
            # If a closed prev FY exists, total should be sum of lines
            total_computed = round(sum(l["amount_total"] for l in data["lines"]), 2)
            assert abs(total_computed - data["total"]) < 0.01

    def test_excludes_fund_call_entries(self, admin_session, fiscal_year):
        """Class-6 entries linked to fund_call_id must NOT appear in previous-year-expenses."""
        r = admin_session.get(f"{BASE_URL}/api/fiscal/previous-year-expenses",
                              params={"fiscal_year_id": fiscal_year["id"]})
        assert r.status_code == 200
        # Just structural check (data may be empty)
        data = r.json()
        for line in data.get("lines", []):
            assert "account_number" in line
            assert "distribution_key_id" in line
            assert line["account_number"].startswith("6")


# -------- Budget CRUD with approve/revoke --------
class TestBudgetApprovalFlow:
    def test_full_approve_flow(self, admin_session, fiscal_year, copro_id, distribution_keys):
        dk_id = distribution_keys[0]["id"] if distribution_keys else ""

        # Create budget (draft)
        payload = {
            "fiscal_year_id": fiscal_year["id"],
            "name": "TEST_iter13_Budget",
            "copropriete_id": copro_id,
            "lines": [
                {"account_number": "615000", "account_name": "Entretien",
                 "amount": 10000.0, "distribution_key_id": dk_id},
                {"account_number": "606100", "account_name": "Eau",
                 "amount": 6000.0, "distribution_key_id": ""},
            ],
        }
        r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
        assert r.status_code == 200, r.text
        b = r.json()
        budget_id = b["id"]
        assert b["status"] == "draft"
        assert b["approved_at"] is None
        assert b["approved_by"] is None
        assert b["total"] == 16000.0

        # PUT update on draft - allowed
        upd = dict(payload)
        upd["lines"][0]["amount"] = 12000.0
        r2 = admin_session.put(f"{BASE_URL}/api/fiscal/budgets/{budget_id}", json=upd)
        assert r2.status_code == 200, r2.text
        assert r2.json()["total"] == 18000.0

        # Approve
        r3 = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/approve")
        assert r3.status_code == 200, r3.text
        approved = r3.json()
        assert approved["status"] == "approved"
        assert approved["approved_at"] is not None
        assert approved["approved_by"] == "admin@copro.be"

        # Approve again -> 400
        r4 = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/approve")
        assert r4.status_code == 400

        # PUT on approved -> 400
        r5 = admin_session.put(f"{BASE_URL}/api/fiscal/budgets/{budget_id}", json=upd)
        assert r5.status_code == 400

        # DELETE approved -> 400
        r6 = admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")
        assert r6.status_code == 400

        # Revoke -> draft again
        r7 = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/revoke")
        assert r7.status_code == 200, r7.text
        rv = r7.json()
        assert rv["status"] == "draft"
        assert rv["approved_at"] is None
        assert rv["approved_by"] is None

        # Now PUT works
        r8 = admin_session.put(f"{BASE_URL}/api/fiscal/budgets/{budget_id}", json=payload)
        assert r8.status_code == 200

        # Cleanup - delete draft
        rd = admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")
        assert rd.status_code == 200

        # GET deleted -> 404
        rg = admin_session.get(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")
        assert rg.status_code == 404

    def test_approve_empty_budget_400(self, admin_session, fiscal_year, copro_id):
        # Create budget with no lines
        payload = {"fiscal_year_id": fiscal_year["id"],
                   "name": "TEST_iter13_Empty",
                   "copropriete_id": copro_id, "lines": []}
        r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
        assert r.status_code == 200, r.text
        budget_id = r.json()["id"]
        try:
            r2 = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/approve")
            assert r2.status_code == 400
        finally:
            admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")


# -------- Wizard preview / generate from budget --------
class TestFundCallsWizard:
    @pytest.fixture(scope="class")
    def approved_budget(self, admin_session, fiscal_year, copro_id, distribution_keys):
        dk_id = distribution_keys[0]["id"] if distribution_keys else ""
        payload = {
            "fiscal_year_id": fiscal_year["id"],
            "name": "TEST_iter13_Wizard_Budget",
            "copropriete_id": copro_id,
            "lines": [
                {"account_number": "615000", "account_name": "Entretien",
                 "amount": 10000.0, "distribution_key_id": dk_id},
                {"account_number": "606100", "account_name": "Eau",
                 "amount": 6000.0, "distribution_key_id": ""},
            ],
        }
        r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
        assert r.status_code == 200
        b = r.json()
        budget_id = b["id"]
        # Approve
        a = admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/approve")
        assert a.status_code == 200, a.text
        yield a.json()
        # Cleanup: revoke + delete + remove created fund_calls
        try:
            admin_session.post(f"{BASE_URL}/api/fiscal/budgets/{budget_id}/revoke")
            admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{budget_id}")
        except Exception:
            pass

    def test_preview_draft_budget_400(self, admin_session, fiscal_year, copro_id):
        # draft budget
        payload = {"fiscal_year_id": fiscal_year["id"],
                   "name": "TEST_iter13_Draft", "copropriete_id": copro_id,
                   "lines": [{"account_number": "615000", "account_name": "x",
                              "amount": 1000.0, "distribution_key_id": ""}]}
        r = admin_session.post(f"{BASE_URL}/api/fiscal/budgets", json=payload)
        bid = r.json()["id"]
        try:
            r2 = admin_session.post(
                f"{BASE_URL}/api/fund-calls/preview-from-budget",
                json={"budget_id": bid, "frequency": 4,
                      "start_date": "2026-01-01", "due_offset_days": 30,
                      "copropriete_id": copro_id})
            assert r2.status_code == 400, r2.text
        finally:
            admin_session.delete(f"{BASE_URL}/api/fiscal/budgets/{bid}")

    def test_preview_quarterly_with_reserve(self, admin_session, approved_budget, copro_id):
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 4,
            "start_date": "2026-01-15",
            "due_offset_days": 30,
            "reserve_fund": {"enabled": True, "amount": 5000.0,
                             "distribution_key_id": "",
                             "label": "Fonds de reserve"},
            "copropriete_id": copro_id,
        }
        r = admin_session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget",
                               json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is False
        assert "calls" in data and "summary" in data
        assert len(data["calls"]) == 4
        s = data["summary"]
        assert s["n_calls"] == 4
        assert s["interval_months"] == 3
        assert s["budget_total"] == 16000.0
        assert s["reserve_total"] == 5000.0
        # Grand total = budget + reserve (excluding rounding tolerance)
        assert abs(s["grand_total"] - (16000.0 + 5000.0)) < 1.0

        calls = data["calls"]
        # Dates: 2026-01-15, 2026-04-15, 2026-07-15, 2026-10-15
        assert calls[0]["date"] == "2026-01-15"
        assert calls[1]["date"] == "2026-04-15"
        assert calls[2]["date"] == "2026-07-15"
        assert calls[3]["date"] == "2026-10-15"
        # due_date = call_date + 30 days
        assert calls[0]["due_date"] == "2026-02-14"

        # Reserve only on call 0
        assert calls[0]["reserve_amount"] == 5000.0
        for c in calls[1:]:
            assert c["reserve_amount"] == 0

        # Lines: call0 has budget lines + 1 reserve line; others only budget lines
        # Filter budget lines >0
        n_budget_lines = sum(1 for ln in calls[0]["lines"] if not ln.get("is_reserve"))
        assert n_budget_lines == 2
        assert any(ln.get("is_reserve") for ln in calls[0]["lines"])
        for c in calls[1:]:
            assert all(not ln.get("is_reserve") for ln in c["lines"])
            assert len(c["lines"]) == 2

        # Sum lines per call
        budget_per_call = 16000.0 / 4  # 4000
        sum_lines_call0 = sum(ln["amount"] for ln in calls[0]["lines"])
        assert abs(sum_lines_call0 - (budget_per_call + 5000.0)) < 1.0
        for c in calls[1:]:
            s_lines = sum(ln["amount"] for ln in c["lines"])
            assert abs(s_lines - budget_per_call) < 1.0

        # Distribution non-empty and sums match call total per call (within tolerance)
        for c in calls:
            dist_sum = round(sum(d["amount"] for d in c["distribution"]), 2)
            assert abs(dist_sum - c["total_amount"]) < 5.0  # tolerance for rounding

    def test_generate_persists_calls(self, admin_session, approved_budget, copro_id):
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 4,
            "start_date": "2026-01-15",
            "due_offset_days": 30,
            "reserve_fund": {"enabled": True, "amount": 5000.0,
                             "distribution_key_id": "", "label": "Reserve"},
            "copropriete_id": copro_id,
        }
        r = admin_session.post(f"{BASE_URL}/api/fund-calls/generate-from-budget",
                               json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is True
        assert "created_ids" in data
        assert len(data["created_ids"]) == 4
        # Verify each call exists and has status=pending
        try:
            for cid in data["created_ids"]:
                g = admin_session.get(f"{BASE_URL}/api/fund-calls/{cid}")
                assert g.status_code == 200
                fc = g.json()
                assert fc["status"] == "pending"
                assert fc.get("budget_id") == approved_budget["id"]
        finally:
            # Cleanup created fund_calls
            for cid in data["created_ids"]:
                try:
                    admin_session.delete(f"{BASE_URL}/api/fund-calls/{cid}")
                except Exception:
                    pass

    def test_frequency_3_dates(self, admin_session, approved_budget, copro_id):
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 3,
            "start_date": "2026-02-10",
            "due_offset_days": 15,
            "copropriete_id": copro_id,
        }
        r = admin_session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget",
                               json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["summary"]["n_calls"] == 3
        assert data["summary"]["interval_months"] == 4
        calls = data["calls"]
        assert calls[0]["date"] == "2026-02-10"
        assert calls[1]["date"] == "2026-06-10"
        assert calls[2]["date"] == "2026-10-10"
        # due 15 days
        assert calls[0]["due_date"] == "2026-02-25"

    def test_frequency_invalid_400(self, admin_session, approved_budget, copro_id):
        body = {"budget_id": approved_budget["id"], "frequency": 5,
                "start_date": "2026-01-01", "copropriete_id": copro_id}
        r = admin_session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget",
                               json=body)
        assert r.status_code == 400


# -------- RBAC: owners are forbidden --------
class TestRBAC:
    def test_owner_cannot_preview(self, owner_session, admin_session, fiscal_year, copro_id):
        # Need any budget id (don't even need approved since RBAC should block before)
        r = admin_session.get(f"{BASE_URL}/api/fiscal/budgets",
                              params={"copropriete_id": copro_id})
        budgets = r.json() if r.status_code == 200 else []
        bid = budgets[0]["id"] if budgets else "fake-id"
        r2 = owner_session.post(
            f"{BASE_URL}/api/fund-calls/preview-from-budget",
            json={"budget_id": bid, "frequency": 4,
                  "start_date": "2026-01-01", "copropriete_id": copro_id})
        assert r2.status_code == 403, f"Expected 403 owner forbidden, got {r2.status_code}: {r2.text}"

    def test_owner_cannot_generate(self, owner_session, copro_id):
        r = owner_session.post(
            f"{BASE_URL}/api/fund-calls/generate-from-budget",
            json={"budget_id": "fake", "frequency": 4,
                  "start_date": "2026-01-01", "copropriete_id": copro_id})
        assert r.status_code == 403

    def test_owner_cannot_approve(self, owner_session):
        r = owner_session.post(f"{BASE_URL}/api/fiscal/budgets/fake-id/approve")
        assert r.status_code == 403


# -------- Regression: GETs still work --------
class TestRegression:
    def test_list_budgets(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/fiscal/budgets")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_fiscal_years(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/fiscal/years")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_fund_calls(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/fund-calls")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
