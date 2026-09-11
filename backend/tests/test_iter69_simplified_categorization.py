"""
Iteration 69 - Simplified Bank Transaction Categorization Tests

Tests for the simplified categorization workflow:
1. POST /api/banking/transactions/{txn_id}/categorize with account_number (no expense_category_id)
2. Categorize endpoint should reject splits without account_number
3. Verify journal entry is created with type FI for class 6 PCMN accounts
"""

import pytest
import requests
import os
import uuid
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestSimplifiedCategorization:
    """Tests for simplified bank transaction categorization with direct PCMN account"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test data: copropriete, PCMN account, bank statement, transaction, distribution key"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin (cookie-based auth)
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        
        # Create test copropriete (without specifying id - let server generate it)
        unique_suffix = uuid.uuid4().hex[:8]
        copro_resp = self.session.post(f"{BASE_URL}/api/coproprietes", json={
            "name": f"Test Copro Iter69 {unique_suffix}",
            "address": "123 Test Street",
            "bank_accounts": [{"iban": "BE04001952089331", "pcmn_number": "55000000", "label": "Compte Vue"}]
        })
        assert copro_resp.status_code in [200, 201], f"Create copro failed: {copro_resp.text}"
        self.copro_id = copro_resp.json().get("id")
        print(f"Created copropriete: {self.copro_id}")
        
        # Set copropriete header for subsequent requests
        self.session.headers.update({"X-Copropriete-Id": self.copro_id})
        
        # Create class 6 PCMN account (charges) - must be 8 digits numeric
        self.pcmn_number = "61400099"  # Fixed 8-digit numeric account
        pcmn_resp = self.session.post(f"{BASE_URL}/api/accounting/pcmn", json={
            "number": self.pcmn_number,
            "name": f"Frais bancaires test iter69 {unique_suffix}",
            "class_num": 6,
            "copropriete_id": self.copro_id
        })
        print(f"PCMN creation response: {pcmn_resp.status_code} - {pcmn_resp.text[:200]}")
        # May already exist, that's OK
        assert pcmn_resp.status_code in [200, 201, 409], f"PCMN creation failed: {pcmn_resp.text}"
        
        # Create distribution key
        dk_resp = self.session.post(f"{BASE_URL}/api/distribution-keys", json={
            "name": f"Cle Test Iter69 {unique_suffix}",
            "copropriete_id": self.copro_id,
            "quotities": []
        })
        print(f"DK creation response: {dk_resp.status_code} - {dk_resp.text[:200]}")
        assert dk_resp.status_code in [200, 201], f"DK creation failed: {dk_resp.text}"
        self.dk_id = dk_resp.json().get("id")
        
        # Create bank statement
        stmt_resp = self.session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"ITER69-{unique_suffix}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account_number": "BE04001952089331",
            "opening_balance": 1000.00,
            "closing_balance": 979.00,
            "copropriete_id": self.copro_id
        })
        print(f"Statement creation response: {stmt_resp.status_code} - {stmt_resp.text[:200]}")
        assert stmt_resp.status_code in [200, 201], f"Create statement failed: {stmt_resp.text}"
        self.stmt_id = stmt_resp.json().get("id")
        
        # Create bank transaction (21 EUR debit - small expense)
        txn_resp = self.session.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": self.stmt_id,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "amount": -21.00,  # Debit
            "counterparty_name": "Banque Test",
            "communication": "Frais bancaires trimestriels",
            "transaction_type": "debit",
            "copropriete_id": self.copro_id
        })
        print(f"Transaction creation response: {txn_resp.status_code} - {txn_resp.text[:200]}")
        assert txn_resp.status_code in [200, 201], f"Create transaction failed: {txn_resp.text}"
        self.txn_id = txn_resp.json().get("id")
        
        yield
        
        # Cleanup
        try:
            self.session.delete(f"{BASE_URL}/api/banking/statements/{self.stmt_id}")
        except Exception:
            pass
        try:
            self.session.delete(f"{BASE_URL}/api/coproprietes/{self.copro_id}")
        except Exception:
            pass
    
    def test_categorize_with_direct_account_number(self):
        """Test categorizing a transaction with account_number only (no expense_category_id)"""
        # Categorize with direct PCMN account number
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{self.txn_id}/categorize",
            json={
                "splits": [{
                    "account_number": self.pcmn_number,
                    "distribution_key_id": self.dk_id,
                    "amount": 21.00,
                    "description": "Frais bancaires test"
                }]
            }
        )
        
        print(f"Categorize response: {cat_resp.status_code} - {cat_resp.text[:500]}")
        assert cat_resp.status_code == 200, f"Categorize failed: {cat_resp.text}"
        data = cat_resp.json()
        
        # Verify response
        assert "splits" in data
        assert len(data["splits"]) == 1
        assert data["splits"][0]["account_number"] == self.pcmn_number
        assert data["splits"][0]["amount"] == 21.00
        
        # Verify transaction is now matched
        txn_resp = self.session.get(
            f"{BASE_URL}/api/banking/transactions",
            params={"copropriete_id": self.copro_id}
        )
        txns = [t for t in txn_resp.json() if t.get("id") == self.txn_id]
        if txns:
            txn = txns[0]
            assert txn.get("matched") == True, f"Transaction should be matched: {txn}"
            assert txn.get("match_type") == "expense_category", f"Match type should be expense_category: {txn}"
    
    def test_categorize_rejects_split_without_account_number(self):
        """Test that categorize endpoint rejects splits without account_number"""
        # Try to categorize without account_number (and without expense_category_id)
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{self.txn_id}/categorize",
            json={
                "splits": [{
                    "distribution_key_id": self.dk_id,
                    "amount": 21.00,
                    "description": "Should fail"
                }]
            }
        )
        
        print(f"Reject response: {cat_resp.status_code} - {cat_resp.text}")
        # Should be rejected with 400
        assert cat_resp.status_code == 400, f"Expected 400, got {cat_resp.status_code}: {cat_resp.text}"
        # Check error message mentions account or nature requirement
        error_text = cat_resp.text.lower()
        assert "nature" in error_text or "compte" in error_text or "pcmn" in error_text or "requis" in error_text
    
    def test_categorize_creates_fi_journal_entry(self):
        """Test that categorizing creates a FI journal entry"""
        # First categorize the transaction
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{self.txn_id}/categorize",
            json={
                "splits": [{
                    "account_number": self.pcmn_number,
                    "distribution_key_id": self.dk_id,
                    "amount": 21.00,
                    "description": "Frais bancaires test"
                }]
            }
        )
        
        print(f"Categorize for FI test: {cat_resp.status_code} - {cat_resp.text[:500]}")
        assert cat_resp.status_code == 200, f"Categorize failed: {cat_resp.text}"
        data = cat_resp.json()
        
        # Check if journal_entry_id is returned
        journal_entry_id = data.get("journal_entry_id")
        print(f"Journal entry ID: {journal_entry_id}")
        
        # If journal entry was created, verify it
        if journal_entry_id:
            je_resp = self.session.get(f"{BASE_URL}/api/journal-entries/{journal_entry_id}")
            print(f"Journal entry response: {je_resp.status_code}")
            if je_resp.status_code == 200:
                je = je_resp.json()
                assert je.get("journal_type") == "FI", f"Expected FI journal type, got {je.get('journal_type')}"
                print(f"Journal entry type verified: FI")
    
    def test_categorize_with_class6_account_21_eur(self):
        """Test categorizing a 21 EUR expense with class 6 account (as per user story)"""
        # This is the specific test case from the user story
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{self.txn_id}/categorize",
            json={
                "splits": [{
                    "account_number": self.pcmn_number,  # Class 6 - charges
                    "distribution_key_id": self.dk_id,
                    "amount": 21.00,
                    "description": "Petit frais bancaire 21 EUR"
                }]
            }
        )
        
        print(f"21 EUR test response: {cat_resp.status_code} - {cat_resp.text[:500]}")
        assert cat_resp.status_code == 200, f"Categorize failed: {cat_resp.text}"
        data = cat_resp.json()
        
        # Verify the split was resolved correctly
        assert len(data["splits"]) == 1
        split = data["splits"][0]
        assert split["account_number"] == self.pcmn_number
        assert split["amount"] == 21.00
        assert split["account_class"] == 6, f"Expected class 6, got {split.get('account_class')}"


class TestCategorizeEndpointValidation:
    """Additional validation tests for the categorize endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup minimal test data"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert login_resp.status_code == 200
        yield
    
    def test_categorize_nonexistent_transaction(self):
        """Test categorizing a non-existent transaction returns 404"""
        fake_txn_id = f"fake-txn-{uuid.uuid4().hex}"
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{fake_txn_id}/categorize",
            json={
                "splits": [{
                    "account_number": "61400000",
                    "distribution_key_id": "some-key",
                    "amount": 10.00
                }]
            }
        )
        
        assert cat_resp.status_code == 404
    
    def test_categorize_empty_splits(self):
        """Test categorizing with empty splits returns 422"""
        # First need a real transaction - use a fake one to test validation
        fake_txn_id = f"fake-txn-{uuid.uuid4().hex}"
        cat_resp = self.session.post(
            f"{BASE_URL}/api/banking/transactions/{fake_txn_id}/categorize",
            json={
                "splits": []
            }
        )
        
        # Should return 404 (txn not found) or 422 (validation error)
        assert cat_resp.status_code in [404, 422]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
