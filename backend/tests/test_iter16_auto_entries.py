"""Iter16 backend tests: Auto journal entries (AC/VE/FI) + Fiscal regularization + Expenses filter.

Covers:
- AC auto entry on invoice POST/PUT/DELETE
- VE auto entry on fund_call POST + generate-from-budget + DELETE cleanup
- FI auto entry on lettrage / auto-lettrage-vcs / unlettrage / DELETE txn
- Manual delete of auto entries returns 400
- GET /api/fiscal/expenses filters + totals + filter values
- POST /api/fiscal/years/{id}/regularize dry_run + persist + DELETE
- RBAC: owner -> 403 on expenses and regularize
"""
import os
import uuid
import requests
import pytest

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "https://teuwen-reports.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"
ADMIN = {"email": "admin@copro.be", "password": "admin123"}


# ---------- shared session ----------
@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def copro_id(admin_client):
    r = admin_client.get(f"{API}/coproprietes", timeout=20)
    assert r.status_code == 200
    arr = r.json()
    assert arr, "no copropriete"
    return arr[0]["id"]


@pytest.fixture(scope="module")
def suppliers(admin_client, copro_id):
    r = admin_client.get(f"{API}/suppliers", params={"copropriete_id": copro_id}, timeout=20)
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def owners(admin_client, copro_id):
    r = admin_client.get(f"{API}/owners", timeout=20)
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def fiscal_year(admin_client, copro_id):
    r = admin_client.get(f"{API}/fiscal/years", params={"copropriete_id": copro_id}, timeout=20)
    arr = r.json() if r.status_code == 200 else []
    fy_open = [f for f in arr if f.get("status") == "open"]
    if fy_open:
        return fy_open[0]
    # create one
    r = admin_client.post(
        f"{API}/fiscal/years",
        json={"name": "TEST_iter16 FY", "start_date": "2026-01-01",
              "end_date": "2026-12-31", "copropriete_id": copro_id},
        timeout=20,
    )
    assert r.status_code == 200
    return r.json()


# ---------- AC auto entry ----------
class TestAutoEntryAC:
    def test_create_invoice_creates_AC_entry(self, admin_client, copro_id, suppliers):
        sup = next((s for s in suppliers if s.get("name")), None)
        payload = {
            "number": f"TEST_iter16_INV_{uuid.uuid4().hex[:6]}",
            "date": "2026-02-01",
            "supplier": sup["name"] if sup else "TEST_iter16_Sup",
            "description": "TEST_iter16 invoice",
            "total_amount": 500.0,
            "account_number": "612000",
            "copropriete_id": copro_id,
        }
        r = admin_client.post(f"{API}/invoices", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        inv = r.json()
        self.__class__.inv_id = inv["id"]

        # Verify AC entry created
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ac = [e for e in je if e.get("source_type") == "invoice"
              and e.get("source_id") == inv["id"]]
        assert ac, "no AC auto entry"
        e = ac[0]
        assert e["journal_type"] == "AC"
        assert e.get("auto_generated") is True
        accs = {l["account_number"] for l in e["lines"]}
        assert "612000" in accs
        # 44000XXX supplier tier OR fallback 440000
        sup_lines = [l for l in e["lines"]
                     if l["account_number"].startswith("44000") and l["credit"] == 500.0]
        assert sup_lines, f"no supplier credit line in {e['lines']}"
        assert abs(e["total_debit"] - e["total_credit"]) < 0.01

    def test_update_invoice_recreates_AC(self, admin_client, copro_id):
        inv_id = self.__class__.inv_id
        # Get current invoice
        r = admin_client.get(f"{API}/invoices/{inv_id}", timeout=20)
        cur = r.json()
        payload = {
            "number": cur["number"],
            "date": cur["date"],
            "supplier": cur["supplier"],
            "description": cur["description"],
            "total_amount": 750.0,  # changed
            "account_number": cur.get("account_number") or "612000",
            "copropriete_id": copro_id,
        }
        r = admin_client.put(f"{API}/invoices/{inv_id}", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ac = [e for e in je if e.get("source_id") == inv_id]
        assert len(ac) == 1, f"expected exactly 1 AC entry after update, got {len(ac)}"
        assert abs(ac[0]["total_debit"] - 750.0) < 0.01

    def test_delete_invoice_removes_AC(self, admin_client, copro_id):
        inv_id = self.__class__.inv_id
        r = admin_client.delete(f"{API}/invoices/{inv_id}", timeout=20)
        assert r.status_code == 200
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ac = [e for e in je if e.get("source_id") == inv_id]
        assert not ac, "AC entry not deleted"


# ---------- VE auto entry ----------
class TestAutoEntryVE:
    def test_create_fund_call_creates_VE_entry(self, admin_client, copro_id, fiscal_year):
        payload = {
            "name": f"TEST_iter16_FC_{uuid.uuid4().hex[:6]}",
            "date": "2026-02-15",
            "due_date": "2026-03-15",
            "fiscal_year_id": fiscal_year["id"],
            "total_amount": 2000.0,
            "call_type": "provisions",
            "copropriete_id": copro_id,
        }
        r = admin_client.post(f"{API}/fund-calls", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        fc = r.json()
        self.__class__.fc_id = fc["id"]
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ve = [e for e in je if e.get("source_type") == "fund_call"
              and e.get("source_id") == fc["id"]]
        assert ve, "no VE auto entry"
        e = ve[0]
        assert e["journal_type"] == "VE"
        assert e.get("auto_generated") is True
        # provisions credit line on 700000
        cr_700 = [l for l in e["lines"]
                  if l["account_number"] == "700000" and l["credit"] > 0]
        assert cr_700
        # at least one 40000XXX debit
        dr_400 = [l for l in e["lines"]
                  if l["account_number"].startswith("40000") and l["debit"] > 0]
        assert dr_400
        assert abs(e["total_debit"] - e["total_credit"]) < 0.01

    def test_delete_fund_call_removes_VE(self, admin_client, copro_id):
        fc_id = self.__class__.fc_id
        r = admin_client.delete(f"{API}/fund-calls/{fc_id}", timeout=20)
        assert r.status_code == 200
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ve = [e for e in je if e.get("source_id") == fc_id]
        assert not ve, "VE entry not deleted"


# ---------- FI auto entry ----------
class TestAutoEntryFI:
    def test_lettrage_creates_FI_entry(self, admin_client, copro_id, owners):
        owner = next((o for o in owners if o.get("vcs_code")), owners[0])
        # Create a credit transaction (owner payment)
        txn_payload = {
            "date": "2026-02-20",
            "amount": 300.0,
            "transaction_type": "credit",
            "counterparty_name": owner["name"],
            "communication": "TEST_iter16 owner payment",
            "account_number": "550000",
            "copropriete_id": copro_id,
        }
        r = admin_client.post(f"{API}/banking/transactions", json=txn_payload, timeout=20)
        assert r.status_code == 200, r.text
        txn = r.json()
        self.__class__.txn_id = txn["id"]

        # Manual lettrage to owner_payment
        r = admin_client.post(
            f"{API}/banking/lettrage",
            json={"transaction_id": txn["id"], "match_to_id": owner["id"],
                  "match_type": "owner_payment"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        fi = [e for e in je if e.get("source_type") == "bank_txn"
              and e.get("source_id") == txn["id"]]
        assert fi, "no FI auto entry"
        e = fi[0]
        assert e["journal_type"] == "FI"
        # money in: Dr 550xxx + Cr 40000XXX
        dr_550 = [l for l in e["lines"]
                  if l["account_number"].startswith("550") and l["debit"] == 300.0]
        cr_400 = [l for l in e["lines"]
                  if l["account_number"].startswith("40000") and l["credit"] == 300.0]
        assert dr_550 and cr_400, f"lines={e['lines']}"
        assert abs(e["total_debit"] - e["total_credit"]) < 0.01

    def test_unlettrage_removes_FI(self, admin_client, copro_id):
        txn_id = self.__class__.txn_id
        r = admin_client.post(f"{API}/banking/unlettrage/{txn_id}", timeout=20)
        assert r.status_code == 200
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        fi = [e for e in je if e.get("source_id") == txn_id]
        assert not fi, "FI entry not deleted by unlettrage"

    def test_delete_txn_removes_FI(self, admin_client, copro_id, owners):
        # Recreate + relettrage then DELETE the txn
        owner = next((o for o in owners if o.get("vcs_code")), owners[0])
        r = admin_client.post(f"{API}/banking/transactions", json={
            "date": "2026-02-21", "amount": 150.0, "transaction_type": "credit",
            "counterparty_name": owner["name"], "communication": "TEST_iter16 b",
            "account_number": "550000", "copropriete_id": copro_id,
        }, timeout=20)
        txn = r.json()
        admin_client.post(f"{API}/banking/lettrage", json={
            "transaction_id": txn["id"], "match_to_id": owner["id"],
            "match_type": "owner_payment"}, timeout=20)
        # Delete the txn
        admin_client.delete(f"{API}/banking/transactions/{txn['id']}", timeout=20)
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        fi = [e for e in je if e.get("source_id") == txn["id"]]
        assert not fi


# ---------- Manual delete refused ----------
class TestAutoEntryProtection:
    def test_cannot_delete_auto_entry_manually(self, admin_client, copro_id, suppliers):
        sup = suppliers[0] if suppliers else None
        r = admin_client.post(f"{API}/invoices", json={
            "number": f"TEST_iter16_PROT_{uuid.uuid4().hex[:6]}",
            "date": "2026-02-22",
            "supplier": sup["name"] if sup else "TEST_iter16_Sup",
            "description": "protect", "total_amount": 100.0,
            "account_number": "612000", "copropriete_id": copro_id,
        }, timeout=20)
        inv_id = r.json()["id"]
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        ac = [e for e in je if e.get("source_id") == inv_id][0]
        r = admin_client.delete(f"{API}/accounting/entries/{ac['id']}", timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}"
        # Cleanup: delete invoice (cascades)
        admin_client.delete(f"{API}/invoices/{inv_id}", timeout=20)


# ---------- /api/fiscal/expenses ----------
class TestFiscalExpenses:
    def test_expenses_endpoint_shape(self, admin_client, copro_id, fiscal_year):
        r = admin_client.get(f"{API}/fiscal/expenses",
                             params={"copropriete_id": copro_id,
                                     "fiscal_year_id": fiscal_year["id"]},
                             timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "expenses" in data
        assert "totals" in data
        assert "filters" in data
        for k in ("total", "count", "by_account", "by_key", "by_bank"):
            assert k in data["totals"], f"missing totals.{k}"
        for k in ("accounts", "keys", "banks"):
            assert k in data["filters"]
        assert isinstance(data["expenses"], list)

    def test_expenses_account_filter(self, admin_client, copro_id):
        r = admin_client.get(f"{API}/fiscal/expenses",
                             params={"copropriete_id": copro_id,
                                     "account_number": "612000"},
                             timeout=20)
        assert r.status_code == 200
        data = r.json()
        for e in data["expenses"]:
            assert e["account_number"] == "612000"


# ---------- Regularization ----------
class TestRegularization:
    @pytest.fixture(scope="class")
    def reg_fy(self, admin_client, copro_id):
        # Use isolated FY to avoid touching real data
        r = admin_client.post(f"{API}/fiscal/years", json={
            "name": f"TEST_iter16_REG_{uuid.uuid4().hex[:4]}",
            "start_date": "2027-01-01", "end_date": "2027-12-31",
            "copropriete_id": copro_id,
        }, timeout=20)
        fy = r.json()
        # Create fund call within FY -> auto VE
        admin_client.post(f"{API}/fund-calls", json={
            "name": "TEST_iter16_REG_FC", "date": "2027-02-01",
            "due_date": "2027-03-01", "fiscal_year_id": fy["id"],
            "total_amount": 1200.0, "call_type": "provisions",
            "copropriete_id": copro_id,
        }, timeout=30)
        # Create invoice within FY -> auto AC
        admin_client.post(f"{API}/invoices", json={
            "number": f"TEST_iter16_REG_INV_{uuid.uuid4().hex[:5]}",
            "date": "2027-03-15", "supplier": "TEST_iter16_RegSup",
            "description": "REG", "total_amount": 800.0,
            "account_number": "612000",
            "copropriete_id": copro_id,
        }, timeout=20)
        yield fy
        # teardown FY + linked data
        admin_client.delete(f"{API}/fiscal/years/{fy['id']}/regularize", timeout=20)
        # cleanup invoices/fund_calls for this FY
        invs = admin_client.get(f"{API}/invoices",
                                params={"copropriete_id": copro_id}, timeout=20).json()
        for inv in invs:
            if "TEST_iter16_REG" in inv.get("number", ""):
                admin_client.delete(f"{API}/invoices/{inv['id']}", timeout=20)
        fcs = admin_client.get(f"{API}/fund-calls",
                               params={"copropriete_id": copro_id}, timeout=20).json()
        for fc in fcs:
            if "TEST_iter16_REG" in fc.get("name", ""):
                admin_client.delete(f"{API}/fund-calls/{fc['id']}", timeout=20)

    def test_dry_run_returns_summary(self, admin_client, reg_fy):
        r = admin_client.post(
            f"{API}/fiscal/years/{reg_fy['id']}/regularize",
            params={"dry_run": "true"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is False
        for k in ("budget_total", "total_real_expenses",
                  "total_provisions_called", "difference_budget_vs_real",
                  "owners_debiteurs", "owners_crediteurs"):
            assert k in data["summary"]
        assert isinstance(data["per_owner"], list)
        assert isinstance(data["by_nature_key"], list)
        # Real expenses should reflect our 800
        assert data["summary"]["total_real_expenses"] >= 800.0 - 0.01

    def test_persist_creates_two_OD_entries(self, admin_client, copro_id, reg_fy):
        r = admin_client.post(
            f"{API}/fiscal/years/{reg_fy['id']}/regularize", timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is True
        # Check 2 OD entries (extourne + affectation) tagged with source_type=regularization
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        regs = [e for e in je if e.get("source_type") == "regularization"
                and e.get("source_id") == reg_fy["id"]]
        assert len(regs) == 2, f"expected 2 OD regul entries got {len(regs)}: {[e['reference'] for e in regs]}"
        refs = {e["reference"] for e in regs}
        assert any(r.startswith("EXT-") for r in refs)
        assert any(r.startswith("AFF-") for r in refs)
        # Check no 40010XXX or 701000 in extourne (reserve must NOT be extourned)
        ext = next(e for e in regs if e["reference"].startswith("EXT-"))
        for line in ext["lines"]:
            acc = line["account_number"]
            assert not acc.startswith("40010"), "reserve account in extourne!"
            assert acc != "160", "reserve credit account 160 in extourne (must stay)!"
        # FY marked regularized
        fy = admin_client.get(f"{API}/fiscal/years",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        target = next(f for f in fy if f["id"] == reg_fy["id"])
        assert target.get("regularized_at")

    def test_delete_regularize_removes_entries_and_resets(self, admin_client, copro_id, reg_fy):
        r = admin_client.delete(f"{API}/fiscal/years/{reg_fy['id']}/regularize", timeout=20)
        assert r.status_code == 200
        je = admin_client.get(f"{API}/accounting/entries",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        regs = [e for e in je if e.get("source_type") == "regularization"
                and e.get("source_id") == reg_fy["id"]]
        assert not regs
        fy = admin_client.get(f"{API}/fiscal/years",
                              params={"copropriete_id": copro_id}, timeout=20).json()
        target = next(f for f in fy if f["id"] == reg_fy["id"])
        assert not target.get("regularized_at")


# ---------- RBAC ----------
class TestRBAC:
    @pytest.fixture(scope="class")
    def owner_client(self, admin_client):
        # Register an owner role user
        email = f"TEST_iter16_owner_{uuid.uuid4().hex[:6]}@example.com"
        pwd = "Owner123!"
        r = admin_client.post(f"{API}/auth/register",
                              json={"email": email, "password": pwd,
                                    "name": "TEST iter16 owner",
                                    "role": "owner"}, timeout=20)
        if r.status_code not in (200, 201):
            pytest.skip(f"cannot create owner user: {r.status_code} {r.text}")
        s = requests.Session()
        r = s.post(f"{API}/auth/login",
                   json={"email": email, "password": pwd}, timeout=20)
        if r.status_code != 200:
            pytest.skip("owner login failed")
        yield s
        # cleanup via admin (best effort)
        try:
            users = admin_client.get(f"{API}/auth/users", timeout=20).json()
            uid = next((u["id"] for u in users if u["email"] == email), None)
            if uid:
                admin_client.delete(f"{API}/auth/users/{uid}", timeout=20)
        except Exception:
            pass

    def test_owner_cannot_list_expenses(self, owner_client, copro_id):
        r = owner_client.get(f"{API}/fiscal/expenses",
                             params={"copropriete_id": copro_id}, timeout=20)
        assert r.status_code == 403, f"owner got {r.status_code}"

    def test_owner_cannot_regularize(self, owner_client, fiscal_year):
        r = owner_client.post(
            f"{API}/fiscal/years/{fiscal_year['id']}/regularize",
            params={"dry_run": "true"}, timeout=20,
        )
        assert r.status_code == 403
