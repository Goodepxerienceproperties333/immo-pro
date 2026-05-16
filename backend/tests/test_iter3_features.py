"""Iteration 3 backend tests:
- Balance de Tiers (owners + suppliers) with situation de compte
- Banking add-lines (inline batch entry on existing statement)
- Banking VCS lookup
- Copropriete archive/unarchive + show_archived filter
- Lettrage with match_type='supplier_payment'
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://immo-pcmn.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"
PREFIX = "TEST3_"


@pytest.fixture(scope="session")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


# ---------------- Balance de Tiers OWNERS ----------------
class TestBalanceTiersOwners:
    def test_list_owners_balance(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reports/balance-tiers/owners")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "owners" in data
        assert "total_debiteurs" in data
        assert "total_crediteurs" in data
        # Each owner row has the expected status keys
        for owner in data["owners"][:5]:
            assert "owner_id" in owner
            assert "balance" in owner
            assert owner["status"] in ("debiteur", "crediteur", "solde")
            assert "movements" in owner

    def test_owner_situation_compte(self, auth_session):
        # Create owner, query situation
        r = auth_session.post(f"{BASE_URL}/api/owners", json={"name": f"{PREFIX}BTOwner"})
        assert r.status_code == 200
        oid = r.json()["id"]
        try:
            r2 = auth_session.get(f"{BASE_URL}/api/reports/balance-tiers/owners/{oid}")
            assert r2.status_code == 200, r2.text
            d = r2.json()
            assert d["owner"]["id"] == oid
            assert "movements" in d
            assert "total_debit" in d and "total_credit" in d
            assert d["status"] in ("debiteur", "crediteur", "solde")
            # new owner with no activity => zero balance
            assert d["total_debit"] == 0 and d["total_credit"] == 0
            assert d["status"] == "solde"
        finally:
            auth_session.delete(f"{BASE_URL}/api/owners/{oid}")

    def test_owner_situation_404(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reports/balance-tiers/owners/nonexistent-id-zzz")
        assert r.status_code == 404


# ---------------- Balance de Tiers SUPPLIERS ----------------
class TestBalanceTiersSuppliers:
    def test_list_suppliers_balance(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers")
        assert r.status_code == 200
        d = r.json()
        assert "suppliers" in d
        assert "total_a_payer" in d
        for s in d["suppliers"][:5]:
            assert s["status"] in ("crediteur", "debiteur", "solde")

    def test_supplier_situation_compte(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/suppliers", json={
            "name": f"{PREFIX}BTSupplier", "vat_number": "BE0999000111"
        })
        assert r.status_code == 200
        sid = r.json()["id"]
        try:
            r2 = auth_session.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers/{sid}")
            assert r2.status_code == 200, r2.text
            d = r2.json()
            assert d["supplier"]["id"] == sid
            assert "movements" in d
            assert d["status"] in ("crediteur", "debiteur", "solde")
        finally:
            auth_session.delete(f"{BASE_URL}/api/suppliers/{sid}")


# ---------------- Banking: Add Lines ----------------
class TestBankingAddLines:
    stmt_id = None

    def test_create_statement(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"{PREFIX}STMT-001",
            "date": "2025-03-01",
            "opening_balance": 1000.0,
            "closing_balance": 1500.0
        })
        assert r.status_code == 200, r.text
        TestBankingAddLines.stmt_id = r.json()["id"]

    def test_add_lines(self, auth_session):
        assert TestBankingAddLines.stmt_id
        payload = {
            "lines": [
                {"date": "2025-03-02", "amount": 250.0, "counterparty_name": "Owner A",
                 "communication": "+++001/0000/12345+++", "transaction_type": "credit"},
                {"date": "2025-03-03", "amount": -100.0, "counterparty_name": "Acme SPRL",
                 "communication": "Facture F-001", "transaction_type": "debit"},
                # zero amount line should be skipped server-side
                {"date": "2025-03-04", "amount": 0.0, "counterparty_name": "skip",
                 "communication": "", "transaction_type": "credit"},
            ]
        }
        r = auth_session.post(f"{BASE_URL}/api/banking/statements/{TestBankingAddLines.stmt_id}/add-lines", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 2, f"Expected 2 lines (zero skipped), got {data}"

    def test_get_statement_with_txns(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/banking/statements/{TestBankingAddLines.stmt_id}")
        assert r.status_code == 200
        d = r.json()
        assert len(d.get("transactions", [])) == 2

    def test_add_lines_404(self, auth_session):
        r = auth_session.post(
            f"{BASE_URL}/api/banking/statements/nonexistent-zzz/add-lines",
            json={"lines": [{"date": "2025-03-01", "amount": 10.0}]}
        )
        assert r.status_code == 404

    def test_zz_cleanup(self, auth_session):
        if TestBankingAddLines.stmt_id:
            auth_session.delete(f"{BASE_URL}/api/banking/statements/{TestBankingAddLines.stmt_id}")


# ---------------- Banking: VCS Lookup ----------------
class TestVCSLookup:
    owner_id = None
    vcs = None

    def test_create_owner_with_vcs(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/owners", json={"name": f"{PREFIX}VCSOwner"})
        assert r.status_code == 200
        d = r.json()
        TestVCSLookup.owner_id = d["id"]
        TestVCSLookup.vcs = d.get("vcs_code", "")
        assert TestVCSLookup.vcs.startswith("+++")

    def test_vcs_lookup_full_format(self, auth_session):
        # full VCS string
        r = auth_session.get(f"{BASE_URL}/api/banking/vcs-lookup", params={"communication": TestVCSLookup.vcs})
        assert r.status_code == 200
        d = r.json()
        assert d.get("owner") is not None, f"Expected owner returned for {TestVCSLookup.vcs}, got {d}"
        assert d["owner"]["id"] == TestVCSLookup.owner_id

    def test_vcs_lookup_empty(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/banking/vcs-lookup", params={"communication": ""})
        assert r.status_code == 200
        assert r.json().get("owner") is None

    def test_vcs_lookup_nomatch(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/banking/vcs-lookup", params={"communication": "+++999/9999/99999+++"})
        assert r.status_code == 200
        # should not match our owner
        d = r.json()
        if d.get("owner"):
            assert d["owner"]["id"] != TestVCSLookup.owner_id

    def test_zz_cleanup_owner(self, auth_session):
        if TestVCSLookup.owner_id:
            auth_session.delete(f"{BASE_URL}/api/owners/{TestVCSLookup.owner_id}")


# ---------------- Copropriete Archive ----------------
class TestCoproArchive:
    copro_id = None

    def test_create(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/coproprietes", json={
            "name": f"{PREFIX}Archive Test", "address": "1 rue archive"
        })
        assert r.status_code == 200
        TestCoproArchive.copro_id = r.json()["id"]

    def test_appears_in_active_list(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/coproprietes")
        assert r.status_code == 200
        assert any(c["id"] == TestCoproArchive.copro_id for c in r.json())

    def test_archive(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/coproprietes/{TestCoproArchive.copro_id}/archive")
        assert r.status_code == 200, r.text

    def test_archived_hidden_by_default(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/coproprietes")
        assert r.status_code == 200
        assert not any(c["id"] == TestCoproArchive.copro_id for c in r.json()), \
            "Archived copro should not appear in default list"

    def test_archived_visible_with_show_archived(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/coproprietes", params={"show_archived": "true"})
        assert r.status_code == 200
        items = r.json()
        found = [c for c in items if c["id"] == TestCoproArchive.copro_id]
        assert found, "Archived copro should appear when show_archived=true"
        assert found[0].get("status") == "archived"

    def test_unarchive(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/coproprietes/{TestCoproArchive.copro_id}/unarchive")
        assert r.status_code == 200

    def test_visible_again_after_unarchive(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/coproprietes")
        assert r.status_code == 200
        assert any(c["id"] == TestCoproArchive.copro_id for c in r.json())

    def test_archive_404(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/coproprietes/nonexistent-zzz/archive")
        assert r.status_code == 404

    def test_zz_cleanup(self, auth_session):
        if TestCoproArchive.copro_id:
            auth_session.delete(f"{BASE_URL}/api/coproprietes/{TestCoproArchive.copro_id}")


# ---------------- Lettrage supplier_payment ----------------
class TestLettrageSupplierPayment:
    stmt_id = None
    txn_id = None

    def test_setup_stmt_and_txn(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"{PREFIX}STMT-LET", "date": "2025-03-10"
        })
        assert r.status_code == 200
        TestLettrageSupplierPayment.stmt_id = r.json()["id"]
        r2 = auth_session.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": TestLettrageSupplierPayment.stmt_id,
            "date": "2025-03-10", "amount": -500.0, "counterparty_name": "Acme",
            "communication": "Payment INV-001", "transaction_type": "debit"
        })
        assert r2.status_code == 200
        TestLettrageSupplierPayment.txn_id = r2.json()["id"]

    def test_lettrage_supplier_payment(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/banking/lettrage", json={
            "transaction_id": TestLettrageSupplierPayment.txn_id,
            "match_to_id": "some-supplier-id",
            "match_type": "supplier_payment"
        })
        assert r.status_code == 200, r.text
        # verify state
        r2 = auth_session.get(f"{BASE_URL}/api/banking/transactions",
                              params={"statement_id": TestLettrageSupplierPayment.stmt_id})
        txns = r2.json()
        ours = next(t for t in txns if t["id"] == TestLettrageSupplierPayment.txn_id)
        assert ours["matched"] is True
        assert ours["match_type"] == "supplier_payment"
        assert ours["matched_to"] == "some-supplier-id"

    def test_zz_cleanup(self, auth_session):
        if TestLettrageSupplierPayment.stmt_id:
            auth_session.delete(f"{BASE_URL}/api/banking/statements/{TestLettrageSupplierPayment.stmt_id}")
