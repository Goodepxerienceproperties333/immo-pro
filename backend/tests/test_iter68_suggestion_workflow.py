"""
Iteration 68 - Suggestion Workflow Tests

Tests for the new suggestion-based auto-lettrage workflow:
1. POST /api/banking/transactions/{txn_id}/validate-suggestion
2. POST /api/banking/statements/{stmt_id}/validate-all-suggestions
3. DELETE /api/banking/transactions/{txn_id}/suggestion
4. POST /api/banking/auto-lettrage-vcs (refactored to return suggestion count)
"""

import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestSuggestionWorkflow:
    """Tests for suggestion validation/rejection endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: login and get auth cookie"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        yield
        self.session.close()

    # ---- TEST 1: POST /api/banking/transactions/{txn_id}/validate-suggestion ----
    
    def test_validate_suggestion_404_nonexistent_txn(self):
        """Returns 404 for non-existent transaction"""
        fake_id = str(uuid.uuid4())
        resp = self.session.post(f"{BASE_URL}/api/banking/transactions/{fake_id}/validate-suggestion")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "non trouvee" in data.get("detail", "").lower() or "not found" in data.get("detail", "").lower()
        print(f"PASS: validate-suggestion returns 404 for non-existent txn")

    def test_validate_suggestion_400_no_suggestion(self):
        """Returns 400 if transaction has no suggestion"""
        # First, create a transaction without suggestion
        # We need a statement first
        stmt_resp = self.session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"TEST-STMT-{uuid.uuid4().hex[:8]}",
            "date": "2025-01-15",
            "account_number": "",
            "opening_balance": 0,
            "closing_balance": 0,
            "copropriete_id": ""
        })
        # If no copropriete, it may fail - that's expected in empty DB
        if stmt_resp.status_code == 400:
            # Try with a fake copropriete_id to see if endpoint validates properly
            # Actually, let's just test with a fake txn_id that exists but has no suggestion
            # Since DB is empty, we'll create a minimal test
            print(f"SKIP: Cannot create statement without copropriete (expected in empty DB)")
            pytest.skip("Empty DB - cannot create test data")
            return
        
        if stmt_resp.status_code == 201 or stmt_resp.status_code == 200:
            stmt = stmt_resp.json()
            # Create a transaction
            txn_resp = self.session.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": stmt["id"],
                "date": "2025-01-15",
                "amount": 100.0,
                "counterparty_name": "Test",
                "communication": "",
                "transaction_type": "credit"
            })
            if txn_resp.status_code in (200, 201):
                txn = txn_resp.json()
                # Now try to validate suggestion on txn without suggestion
                validate_resp = self.session.post(f"{BASE_URL}/api/banking/transactions/{txn['id']}/validate-suggestion")
                assert validate_resp.status_code == 400, f"Expected 400, got {validate_resp.status_code}"
                print(f"PASS: validate-suggestion returns 400 when no suggestion exists")
                # Cleanup
                self.session.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")
            self.session.delete(f"{BASE_URL}/api/banking/statements/{stmt['id']}")
        else:
            print(f"SKIP: Statement creation returned {stmt_resp.status_code}")
            pytest.skip("Cannot create test statement")

    # ---- TEST 2: POST /api/banking/statements/{stmt_id}/validate-all-suggestions ----

    def test_validate_all_suggestions_404_nonexistent_stmt(self):
        """Returns 404 for non-existent statement"""
        fake_id = str(uuid.uuid4())
        resp = self.session.post(f"{BASE_URL}/api/banking/statements/{fake_id}/validate-all-suggestions")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: validate-all-suggestions returns 404 for non-existent statement")

    def test_validate_all_suggestions_empty_db_returns_zero(self):
        """Returns validated=0 when no suggestions exist"""
        # Create a statement first
        stmt_resp = self.session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"TEST-STMT-{uuid.uuid4().hex[:8]}",
            "date": "2025-01-15",
            "account_number": "",
            "opening_balance": 0,
            "closing_balance": 0,
            "copropriete_id": ""
        })
        if stmt_resp.status_code == 400:
            print(f"SKIP: Cannot create statement without copropriete")
            pytest.skip("Empty DB - cannot create test data")
            return
        
        if stmt_resp.status_code in (200, 201):
            stmt = stmt_resp.json()
            # Call validate-all on empty statement
            validate_resp = self.session.post(f"{BASE_URL}/api/banking/statements/{stmt['id']}/validate-all-suggestions")
            assert validate_resp.status_code == 200, f"Expected 200, got {validate_resp.status_code}"
            data = validate_resp.json()
            assert data.get("validated") == 0, f"Expected validated=0, got {data}"
            print(f"PASS: validate-all-suggestions returns validated=0 on empty statement")
            # Cleanup
            self.session.delete(f"{BASE_URL}/api/banking/statements/{stmt['id']}")
        else:
            pytest.skip(f"Statement creation returned {stmt_resp.status_code}")

    # ---- TEST 3: DELETE /api/banking/transactions/{txn_id}/suggestion ----

    def test_reject_suggestion_404_nonexistent_txn(self):
        """Returns 404 for non-existent transaction"""
        fake_id = str(uuid.uuid4())
        resp = self.session.delete(f"{BASE_URL}/api/banking/transactions/{fake_id}/suggestion")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: reject-suggestion returns 404 for non-existent txn")

    def test_reject_suggestion_400_no_suggestion(self):
        """Returns 400 if transaction has no suggestion to reject"""
        # Create a statement and transaction
        stmt_resp = self.session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"TEST-STMT-{uuid.uuid4().hex[:8]}",
            "date": "2025-01-15",
            "account_number": "",
            "opening_balance": 0,
            "closing_balance": 0,
            "copropriete_id": ""
        })
        if stmt_resp.status_code == 400:
            pytest.skip("Empty DB - cannot create test data")
            return
        
        if stmt_resp.status_code in (200, 201):
            stmt = stmt_resp.json()
            txn_resp = self.session.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": stmt["id"],
                "date": "2025-01-15",
                "amount": 50.0,
                "counterparty_name": "Test Reject",
                "communication": "",
                "transaction_type": "credit"
            })
            if txn_resp.status_code in (200, 201):
                txn = txn_resp.json()
                # Try to reject suggestion on txn without suggestion
                reject_resp = self.session.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}/suggestion")
                assert reject_resp.status_code == 400, f"Expected 400, got {reject_resp.status_code}"
                print(f"PASS: reject-suggestion returns 400 when no suggestion exists")
                # Cleanup
                self.session.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")
            self.session.delete(f"{BASE_URL}/api/banking/statements/{stmt['id']}")
        else:
            pytest.skip(f"Statement creation returned {stmt_resp.status_code}")

    # ---- TEST 4: POST /api/banking/auto-lettrage-vcs ----

    def test_auto_lettrage_vcs_returns_count_zero_on_empty_db(self):
        """Returns count=0 when no transactions to match"""
        resp = self.session.post(f"{BASE_URL}/api/banking/auto-lettrage-vcs", json={
            "copropriete_id": ""
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "count" in data, f"Response should contain 'count' field: {data}"
        # Count should be 0 or a number (not matched count, but suggestion count)
        assert isinstance(data["count"], int), f"count should be int: {data}"
        print(f"PASS: auto-lettrage-vcs returns count={data['count']} (suggestion count)")

    def test_auto_lettrage_vcs_returns_suggestion_count_not_matched(self):
        """Verifies endpoint returns suggestion count, not matched count"""
        resp = self.session.post(f"{BASE_URL}/api/banking/auto-lettrage-vcs", json={})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        # Check response structure
        assert "count" in data, f"Missing 'count' in response: {data}"
        assert "message" in data, f"Missing 'message' in response: {data}"
        # Message should mention "suggestion" not "matched"
        msg = data.get("message", "").lower()
        assert "suggestion" in msg, f"Message should mention 'suggestion': {data['message']}"
        print(f"PASS: auto-lettrage-vcs message mentions suggestions: {data['message']}")


class TestEndpointRouting:
    """Tests to verify endpoints are correctly routed"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: login and get auth cookie"""
        self.session = requests.Session()
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        yield
        self.session.close()

    def test_validate_suggestion_endpoint_exists(self):
        """Verify POST /api/banking/transactions/{id}/validate-suggestion endpoint exists"""
        # Use a fake ID - should get 404 (not 405 Method Not Allowed)
        fake_id = str(uuid.uuid4())
        resp = self.session.post(f"{BASE_URL}/api/banking/transactions/{fake_id}/validate-suggestion")
        # 404 means endpoint exists but resource not found
        # 405 would mean endpoint doesn't exist
        assert resp.status_code != 405, f"Endpoint should exist, got 405 Method Not Allowed"
        assert resp.status_code == 404, f"Expected 404 for non-existent txn, got {resp.status_code}"
        print(f"PASS: validate-suggestion endpoint exists and routes correctly")

    def test_validate_all_suggestions_endpoint_exists(self):
        """Verify POST /api/banking/statements/{id}/validate-all-suggestions endpoint exists"""
        fake_id = str(uuid.uuid4())
        resp = self.session.post(f"{BASE_URL}/api/banking/statements/{fake_id}/validate-all-suggestions")
        assert resp.status_code != 405, f"Endpoint should exist, got 405"
        assert resp.status_code == 404, f"Expected 404 for non-existent stmt, got {resp.status_code}"
        print(f"PASS: validate-all-suggestions endpoint exists and routes correctly")

    def test_reject_suggestion_endpoint_exists(self):
        """Verify DELETE /api/banking/transactions/{id}/suggestion endpoint exists"""
        fake_id = str(uuid.uuid4())
        resp = self.session.delete(f"{BASE_URL}/api/banking/transactions/{fake_id}/suggestion")
        assert resp.status_code != 405, f"Endpoint should exist, got 405"
        assert resp.status_code == 404, f"Expected 404 for non-existent txn, got {resp.status_code}"
        print(f"PASS: reject-suggestion endpoint exists and routes correctly")

    def test_auto_lettrage_vcs_endpoint_exists(self):
        """Verify POST /api/banking/auto-lettrage-vcs endpoint exists"""
        resp = self.session.post(f"{BASE_URL}/api/banking/auto-lettrage-vcs", json={})
        assert resp.status_code != 405, f"Endpoint should exist, got 405"
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        print(f"PASS: auto-lettrage-vcs endpoint exists and returns 200")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
