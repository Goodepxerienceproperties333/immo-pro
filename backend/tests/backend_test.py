"""Backend tests for CoproManager - Belgian condominium management app.
Covers: Auth, Owners/Lots/Tenants, PCMN/Journals, Invoices/DistKeys, Meters, Banking, Documents.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://immo-pcmn.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_session(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return session


# ---------------- AUTH ----------------
class TestAuth:
    def test_login_success(self, session):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] in ("admin", "superadmin")
        # httpOnly cookie set
        assert "access_token" in session.cookies.get_dict()

    def test_login_invalid(self, session):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_register(self, session):
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        email = f"test_{uuid.uuid4().hex[:8]}@copro.be"
        r = s.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "pwd12345", "name": "Test User"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["email"] == email
        assert data["role"] == "owner"

    def test_dashboard_stats(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/dashboard/stats")
        assert r.status_code == 200
        d = r.json()
        for k in ["owners_count", "lots_count", "tenants_count", "invoices_count", "unpaid_invoices", "total_charges"]:
            assert k in d


# ---------------- OWNERS / LOTS / TENANTS ----------------
class TestOwnersLotsTenants:
    owner_id = None
    lot_id = None
    tenant_id = None

    def test_create_owner(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/owners", json={
            "name": "TEST_Owner_A", "email": "a@test.com", "phone": "0123", "address": "Brussels"
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["name"] == "TEST_Owner_A"
        assert "id" in d
        TestOwnersLotsTenants.owner_id = d["id"]

    def test_list_owners(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/owners")
        assert r.status_code == 200
        assert any(o["id"] == TestOwnersLotsTenants.owner_id for o in r.json())

    def test_update_owner(self, auth_session):
        oid = TestOwnersLotsTenants.owner_id
        r = auth_session.put(f"{BASE_URL}/api/owners/{oid}", json={"name": "TEST_Owner_A2", "email": "a@test.com"})
        assert r.status_code == 200
        assert r.json()["name"] == "TEST_Owner_A2"

    def test_create_lot(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/lots", json={
            "number": f"L-{uuid.uuid4().hex[:6]}", "lot_type": "apartment", "quotity": 50.0,
            "owner_id": TestOwnersLotsTenants.owner_id, "area": 80.0, "floor": 2
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["quotity"] == 50.0
        TestOwnersLotsTenants.lot_id = d["id"]

    def test_create_tenant(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/tenants", json={
            "name": "TEST_Tenant", "email": "t@test.com", "lot_id": TestOwnersLotsTenants.lot_id, "rent_amount": 1000.0
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["lot_id"] == TestOwnersLotsTenants.lot_id
        TestOwnersLotsTenants.tenant_id = d["id"]

    def test_delete_tenant(self, auth_session):
        r = auth_session.delete(f"{BASE_URL}/api/tenants/{TestOwnersLotsTenants.tenant_id}")
        assert r.status_code == 200


# ---------------- PCMN / JOURNALS ----------------
class TestAccounting:
    entry_id = None

    def test_list_pcmn(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/accounting/pcmn")
        assert r.status_code == 200
        accounts = r.json()
        assert len(accounts) >= 50, f"Expected pre-seeded PCMN accounts, got {len(accounts)}"

    def test_pcmn_search(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/accounting/pcmn", params={"search": "61"})
        assert r.status_code == 200

    def test_pcmn_filter_by_class(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/accounting/pcmn", params={"class_num": 6})
        assert r.status_code == 200
        for acc in r.json():
            assert acc.get("class_num") == 6

    def test_create_balanced_entry(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2026-01-15",
            "reference": "TEST-001",
            "description": "TEST entry balanced",
            "lines": [
                {"account_number": "610000", "debit": 100.0, "credit": 0.0, "description": "test debit"},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0, "description": "test credit"}
            ]
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total_debit"] == 100.0
        assert d["total_credit"] == 100.0
        TestAccounting.entry_id = d["id"]

    def test_create_unbalanced_entry_fails(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2026-01-15",
            "description": "unbalanced",
            "lines": [
                {"account_number": "610000", "debit": 100.0, "credit": 0.0},
                {"account_number": "440000", "debit": 0.0, "credit": 50.0}
            ]
        })
        assert r.status_code == 400

    def test_balance_endpoint(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/accounting/balance")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------- INVOICES / DIST KEYS ----------------
class TestInvoices:
    dk_id = None
    inv_id = None

    def test_create_dist_key(self, auth_session):
        lot_id = TestOwnersLotsTenants.lot_id or "dummy"
        r = auth_session.post(f"{BASE_URL}/api/distribution-keys", json={
            "name": "TEST_DK_general", "description": "tantieme general", "key_type": "quotity",
            "lots": [{"lot_id": lot_id, "lot_number": "L1", "share": 100.0}]
        })
        assert r.status_code == 200, r.text
        TestInvoices.dk_id = r.json()["id"]

    def test_list_dist_keys(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/distribution-keys")
        assert r.status_code == 200

    def test_create_invoice(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/invoices", json={
            "number": f"INV-{uuid.uuid4().hex[:6]}", "date": "2026-01-15",
            "supplier": "TEST_Supplier", "description": "Eau collective", "total_amount": 500.0,
            "vat_amount": 0.0, "distribution_key_id": TestInvoices.dk_id, "status": "unpaid"
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total_amount"] == 500.0
        # distribution_lines should be computed
        assert "distribution_lines" in d
        TestInvoices.inv_id = d["id"]

    def test_list_invoices(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/invoices")
        assert r.status_code == 200


# ---------------- METERS ----------------
class TestMeters:
    meter_id = None

    def test_create_meter(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/meters", json={
            "name": "TEST_Water_Meter", "meter_type": "water", "serial_number": "SN-001"
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["unit"] == "m3"  # auto-assigned
        TestMeters.meter_id = d["id"]

    def test_add_reading_first(self, auth_session):
        mid = TestMeters.meter_id
        r = auth_session.post(f"{BASE_URL}/api/meters/{mid}/readings", json={"date": "2026-01-01", "value": 100.0})
        assert r.status_code == 200
        assert r.json()["consumption"] == 0.0

    def test_add_reading_consumption(self, auth_session):
        mid = TestMeters.meter_id
        r = auth_session.post(f"{BASE_URL}/api/meters/{mid}/readings", json={"date": "2026-02-01", "value": 150.0})
        assert r.status_code == 200
        assert r.json()["consumption"] == 50.0

    def test_list_readings(self, auth_session):
        mid = TestMeters.meter_id
        r = auth_session.get(f"{BASE_URL}/api/meters/{mid}/readings")
        assert r.status_code == 200
        assert len(r.json()) == 2


# ---------------- BANKING ----------------
class TestBanking:
    stmt_id = None
    txn_id = None

    def test_create_statement(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"ST-{uuid.uuid4().hex[:6]}", "date": "2026-01-15",
            "account_number": "BE12345", "opening_balance": 1000.0, "closing_balance": 1500.0
        })
        assert r.status_code == 200, r.text
        TestBanking.stmt_id = r.json()["id"]

    def test_create_transaction(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": TestBanking.stmt_id, "date": "2026-01-16", "amount": 500.0,
            "counterparty_name": "TEST_Payer", "transaction_type": "credit"
        })
        assert r.status_code == 200, r.text
        TestBanking.txn_id = r.json()["id"]

    def test_lettrage(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/lettrage", json={
            "transaction_id": TestBanking.txn_id, "match_to_id": "dummy_inv", "match_type": "invoice"
        })
        assert r.status_code == 200


# ---------------- DOCUMENTS ----------------
class TestDocuments:
    cat_id = None
    doc_id = None

    def test_create_category(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/documents/categories", json={
            "name": f"TEST_Cat_{uuid.uuid4().hex[:6]}", "description": "AG docs"
        })
        assert r.status_code == 200, r.text
        TestDocuments.cat_id = r.json()["id"]

    def test_create_document(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/documents", json={
            "title": "TEST_PV_AG", "description": "PV", "category_id": TestDocuments.cat_id, "content": "Content"
        })
        assert r.status_code == 200, r.text
        TestDocuments.doc_id = r.json()["id"]

    def test_list_documents(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/documents")
        assert r.status_code == 200


# ---------------- CLEANUP ----------------
@pytest.fixture(scope="session", autouse=True)
def cleanup(auth_session):
    yield
    # Best-effort cleanup
    try:
        if TestOwnersLotsTenants.lot_id:
            auth_session.delete(f"{BASE_URL}/api/lots/{TestOwnersLotsTenants.lot_id}")
        if TestOwnersLotsTenants.owner_id:
            auth_session.delete(f"{BASE_URL}/api/owners/{TestOwnersLotsTenants.owner_id}")
        if TestAccounting.entry_id:
            auth_session.delete(f"{BASE_URL}/api/accounting/entries/{TestAccounting.entry_id}")
        if TestInvoices.inv_id:
            auth_session.delete(f"{BASE_URL}/api/invoices/{TestInvoices.inv_id}")
        if TestInvoices.dk_id:
            auth_session.delete(f"{BASE_URL}/api/distribution-keys/{TestInvoices.dk_id}")
        if TestMeters.meter_id:
            auth_session.delete(f"{BASE_URL}/api/meters/{TestMeters.meter_id}")
        if TestBanking.stmt_id:
            auth_session.delete(f"{BASE_URL}/api/banking/statements/{TestBanking.stmt_id}")
        if TestDocuments.doc_id:
            auth_session.delete(f"{BASE_URL}/api/documents/{TestDocuments.doc_id}")
        if TestDocuments.cat_id:
            auth_session.delete(f"{BASE_URL}/api/documents/categories/{TestDocuments.cat_id}")
    except Exception:
        pass
