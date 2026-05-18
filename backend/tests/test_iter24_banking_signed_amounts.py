"""Iter24 backend tests: banking signed amounts + statement edit + add-lines IBAN inheritance.

Covers 3 bugs fix:
1. PUT /api/banking/statements/{id} : new endpoint to edit statement.
2. POST/PUT /api/banking/transactions : transaction_type='debit' -> amount stored NEGATIVE.
3. POST /api/banking/statements/{id}/add-lines : account_number inherits from statement IBAN.

Plus regression on iter21/22/23 endpoints.
"""
import os
import uuid
import pytest
import requests

_be = os.environ.get("REACT_APP_BACKEND_URL")
if not _be:
    # Read from frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    _be = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass
BASE_URL = (_be or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
ACP_ID = "6748ca1a-216d-4002-8417-799287238736"
EXISTING_STMT_ID = "a4476514-cb35-4117-995a-893a2774f152"
IBAN = "BE68539007547034"
EXPECTED_PCMN = "55103400"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    # Login
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def fresh_stmt(api):
    """Create a fresh draft statement for tests; clean up after."""
    payload = {
        "number": f"TEST_ITER24_{uuid.uuid4().hex[:6]}",
        "date": "2025-03-01",
        "account_number": IBAN,
        "opening_balance": 1000.0,
        "closing_balance": 1000.0,
        "copropriete_id": ACP_ID,
    }
    r = api.post(f"{BASE_URL}/api/banking/statements", json=payload)
    assert r.status_code == 200, r.text
    stmt = r.json()
    sid = stmt["id"]
    yield stmt
    # cleanup
    try:
        api.delete(f"{BASE_URL}/api/banking/statements/{sid}")
    except Exception:
        pass


# ----------- BUG 1: transaction signed amount on POST ----------

class TestSignedAmountOnPost:
    def test_debit_stored_negative(self, api, fresh_stmt):
        r = api.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-02",
            "amount": 150,
            "transaction_type": "debit",
            "counterparty_name": "TEST_ITER24_debit",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert r.status_code == 200, r.text
        txn = r.json()
        assert txn["amount"] == -150, f"debit must be stored negative, got {txn['amount']}"
        assert txn["transaction_type"] == "debit"
        # Verify via GET
        g = api.get(f"{BASE_URL}/api/banking/transactions", params={"copropriete_id": ACP_ID})
        assert g.status_code == 200
        found = next((t for t in g.json() if t["id"] == txn["id"]), None)
        assert found is not None
        assert found["amount"] == -150
        api.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")

    def test_debit_with_already_negative_amount(self, api, fresh_stmt):
        """Sending amount=-150 with type='debit' must still result in -150 (abs then negate)."""
        r = api.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-02",
            "amount": -150,
            "transaction_type": "debit",
            "counterparty_name": "TEST_ITER24_neg_in",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert r.status_code == 200
        assert r.json()["amount"] == -150
        api.delete(f"{BASE_URL}/api/banking/transactions/{r.json()['id']}")

    def test_credit_stored_positive(self, api, fresh_stmt):
        r = api.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-03",
            "amount": 200,
            "transaction_type": "credit",
            "counterparty_name": "TEST_ITER24_credit",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert r.status_code == 200
        txn = r.json()
        assert txn["amount"] == 200, f"credit must be positive, got {txn['amount']}"
        api.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")


# ----------- BUG 1: transaction signed amount on PUT ----------

class TestSignedAmountOnPut:
    def test_credit_to_debit_inverts_sign(self, api, fresh_stmt):
        # Create as credit 300
        r = api.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-04",
            "amount": 300,
            "transaction_type": "credit",
            "counterparty_name": "TEST_ITER24_flip",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert r.status_code == 200
        txn = r.json()
        assert txn["amount"] == 300
        # Update to debit
        u = api.put(f"{BASE_URL}/api/banking/transactions/{txn['id']}", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-04",
            "amount": 300,
            "transaction_type": "debit",
            "counterparty_name": "TEST_ITER24_flip",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert u.status_code == 200, u.text
        assert u.json()["amount"] == -300, f"after flip to debit, amount must be -300, got {u.json()['amount']}"
        assert u.json()["transaction_type"] == "debit"
        # And flip back
        u2 = api.put(f"{BASE_URL}/api/banking/transactions/{txn['id']}", json={
            "statement_id": fresh_stmt["id"],
            "date": "2025-03-04",
            "amount": 300,
            "transaction_type": "credit",
            "counterparty_name": "TEST_ITER24_flip",
            "account_number": IBAN,
            "copropriete_id": ACP_ID,
        })
        assert u2.status_code == 200
        assert u2.json()["amount"] == 300
        api.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")


# ----------- BUG 3: add-lines inherits IBAN + signed amount ----------

class TestAddLinesSignedAndIBAN:
    def test_add_lines_signs_and_account(self, api, fresh_stmt):
        payload = {
            "lines": [
                {"date": "2025-03-05", "amount": 100, "transaction_type": "credit",
                 "counterparty_name": "TEST_ITER24_AL_c"},
                {"date": "2025-03-05", "amount": 80, "transaction_type": "debit",
                 "counterparty_name": "TEST_ITER24_AL_d"},
                {"date": "2025-03-05", "amount": 50, "transaction_type": "debit",
                 "counterparty_name": "TEST_ITER24_AL_d2"},
            ],
            "copropriete_id": ACP_ID,
        }
        r = api.post(f"{BASE_URL}/api/banking/statements/{fresh_stmt['id']}/add-lines", json=payload)
        assert r.status_code == 200, r.text
        assert r.json().get("count") == 3
        # Fetch all txns of statement
        g = api.get(f"{BASE_URL}/api/banking/transactions", params={
            "statement_id": fresh_stmt["id"],
            "copropriete_id": ACP_ID,
        })
        assert g.status_code == 200
        added = [t for t in g.json() if t.get("counterparty_name", "").startswith("TEST_ITER24_AL_")]
        assert len(added) == 3
        by_name = {t["counterparty_name"]: t for t in added}
        assert by_name["TEST_ITER24_AL_c"]["amount"] == 100
        assert by_name["TEST_ITER24_AL_d"]["amount"] == -80
        assert by_name["TEST_ITER24_AL_d2"]["amount"] == -50
        # account_number must be statement IBAN, NOT empty
        for t in added:
            assert t.get("account_number") == IBAN, \
                f"add-lines txn must inherit IBAN '{IBAN}', got '{t.get('account_number')}'"
        # cleanup
        for t in added:
            api.delete(f"{BASE_URL}/api/banking/transactions/{t['id']}")


# ----------- BUG 2: PUT /statements/{id} edit ----------

class TestUpdateStatement:
    def test_put_statement_persists(self, api):
        # Create
        r = api.post(f"{BASE_URL}/api/banking/statements", json={
            "number": "TEST_ITER24_PUT_orig",
            "date": "2025-02-01",
            "account_number": "BE00000000000000",
            "opening_balance": 100.0,
            "closing_balance": 100.0,
            "copropriete_id": ACP_ID,
        })
        assert r.status_code == 200
        sid = r.json()["id"]
        try:
            u = api.put(f"{BASE_URL}/api/banking/statements/{sid}", json={
                "number": "TEST_ITER24_PUT_edited",
                "date": "2025-02-15",
                "account_number": IBAN,
                "opening_balance": 500.0,
                "closing_balance": 700.0,
                "copropriete_id": ACP_ID,
            })
            assert u.status_code == 200, u.text
            # GET and verify persistence
            g = api.get(f"{BASE_URL}/api/banking/statements/{sid}")
            assert g.status_code == 200
            stmt = g.json()
            assert stmt["number"] == "TEST_ITER24_PUT_edited"
            assert stmt["date"] == "2025-02-15"
            assert stmt["account_number"] == IBAN
            assert float(stmt["opening_balance"]) == 500.0
            assert float(stmt["closing_balance"]) == 700.0
        finally:
            api.delete(f"{BASE_URL}/api/banking/statements/{sid}")

    def test_put_statement_works_on_posted(self, api):
        """No backend restriction: posted statements CAN be edited via PUT."""
        # Create balanced draft
        r = api.post(f"{BASE_URL}/api/banking/statements", json={
            "number": "TEST_ITER24_POSTED",
            "date": "2025-02-10",
            "account_number": IBAN,
            "opening_balance": 0.0,
            "closing_balance": 0.0,
            "copropriete_id": ACP_ID,
        })
        sid = r.json()["id"]
        try:
            p = api.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert p.status_code == 200, p.text
            # Now PUT (should succeed - frontend handles lock)
            u = api.put(f"{BASE_URL}/api/banking/statements/{sid}", json={
                "number": "TEST_ITER24_POSTED_edit",
                "date": "2025-02-11",
                "account_number": IBAN,
                "opening_balance": 0.0,
                "closing_balance": 0.0,
                "copropriete_id": ACP_ID,
            })
            assert u.status_code == 200, f"PUT on posted must succeed (no backend guard), got {u.status_code} {u.text}"
            g = api.get(f"{BASE_URL}/api/banking/statements/{sid}")
            assert g.json()["number"] == "TEST_ITER24_POSTED_edit"
        finally:
            api.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ----------- computed_closing with signed amounts ----------

class TestComputedClosing:
    def test_computed_closing_with_signed_amounts(self, api):
        """opening=1000, credit=200, debit(stored=-150) -> computed_closing=1050."""
        r = api.post(f"{BASE_URL}/api/banking/statements", json={
            "number": "TEST_ITER24_CC",
            "date": "2025-02-20",
            "account_number": IBAN,
            "opening_balance": 1000.0,
            "closing_balance": 1050.0,
            "copropriete_id": ACP_ID,
        })
        sid = r.json()["id"]
        try:
            api.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": sid, "date": "2025-02-21", "amount": 200,
                "transaction_type": "credit", "account_number": IBAN,
                "counterparty_name": "TEST_ITER24_CC_c", "copropriete_id": ACP_ID,
            })
            api.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": sid, "date": "2025-02-22", "amount": 150,
                "transaction_type": "debit", "account_number": IBAN,
                "counterparty_name": "TEST_ITER24_CC_d", "copropriete_id": ACP_ID,
            })
            g = api.get(f"{BASE_URL}/api/banking/statements/{sid}")
            stmt = g.json()
            assert stmt["computed_closing"] == 1050.0, \
                f"computed_closing must be 1050, got {stmt['computed_closing']}"
            assert stmt["is_balanced"] is True
            assert abs(stmt["balance_diff"]) < 0.01
        finally:
            api.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ----------- Regression iter23: post/unpost workflow ----------

class TestRegressionIter23Post:
    def test_post_unpost_workflow(self, api):
        r = api.post(f"{BASE_URL}/api/banking/statements", json={
            "number": "TEST_ITER24_REG23",
            "date": "2025-02-25",
            "account_number": IBAN,
            "opening_balance": 0.0,
            "closing_balance": 100.0,
            "copropriete_id": ACP_ID,
        })
        sid = r.json()["id"]
        try:
            # Unbalanced -> 400
            p = api.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert p.status_code == 400
            # Add credit 100 to balance
            api.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": sid, "date": "2025-02-26", "amount": 100,
                "transaction_type": "credit", "account_number": IBAN,
                "counterparty_name": "TEST_ITER24_REG23_c", "copropriete_id": ACP_ID,
            })
            p2 = api.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert p2.status_code == 200, p2.text
            # Verify posted
            assert api.get(f"{BASE_URL}/api/banking/statements/{sid}").json()["status"] == "posted"
            # Re-post fails
            assert api.post(f"{BASE_URL}/api/banking/statements/{sid}/post").status_code == 400
            # Unpost
            up = api.post(f"{BASE_URL}/api/banking/statements/{sid}/unpost")
            assert up.status_code == 200
            assert api.get(f"{BASE_URL}/api/banking/statements/{sid}").json()["status"] == "draft"
        finally:
            api.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ----------- Regression iter21: IBAN -> PCMN resolution via generate_bank_entry ----------

class TestRegressionIter21IBAN:
    def test_lettrage_generates_bank_entry_with_pcmn(self, api):
        """Match a credit txn to an owner; FI entry must reference PCMN 55103400."""
        DUBOIS_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"
        # Create statement + credit txn
        r = api.post(f"{BASE_URL}/api/banking/statements", json={
            "number": "TEST_ITER24_REG21",
            "date": "2025-02-28",
            "account_number": IBAN,
            "opening_balance": 0.0,
            "closing_balance": 0.0,
            "copropriete_id": ACP_ID,
        })
        sid = r.json()["id"]
        t = api.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": sid,
            "date": "2025-02-28",
            "amount": 100.0,
            "transaction_type": "credit",
            "account_number": IBAN,
            "counterparty_name": "TEST_ITER24_REG21_owner",
            "copropriete_id": ACP_ID,
        })
        assert t.status_code == 200
        txn_id = t.json()["id"]
        try:
            l = api.post(f"{BASE_URL}/api/banking/lettrage", json={
                "transaction_id": txn_id,
                "match_to_id": DUBOIS_ID,
                "match_type": "owner_payment",
            })
            assert l.status_code == 200, l.text
            # Verify auto FI entry was generated with PCMN 55103400 line
            entries = api.get(f"{BASE_URL}/api/accounting/entries", params={"copropriete_id": ACP_ID}).json()
            fi = [e for e in entries if e.get("source_type") == "bank_txn" and e.get("source_id") == txn_id]
            assert len(fi) >= 1, f"No FI entry generated for txn {txn_id}"
            lines_accts = [line.get("account_number") for e in fi for line in e.get("lines", [])]
            assert EXPECTED_PCMN in lines_accts, \
                f"FI entry must reference PCMN {EXPECTED_PCMN} (resolved from IBAN {IBAN}), accounts found: {lines_accts}"
        finally:
            api.delete(f"{BASE_URL}/api/banking/transactions/{txn_id}")
            api.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ----------- Regression iter22: cleanup-orphan-entries ----------

class TestRegressionIter22Orphan:
    def test_cleanup_orphan_entries_endpoint(self, api):
        r = api.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/cleanup-orphan-entries")
        assert r.status_code == 200, r.text
        data = r.json()
        # Should return some structure
        assert isinstance(data, dict)
        assert "status" in data or "total_deleted" in data or "stats" in data or "deleted" in data or "count" in data or "message" in data
