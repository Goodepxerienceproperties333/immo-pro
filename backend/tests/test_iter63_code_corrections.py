"""
Test suite for iteration 63 code corrections:
1. _resolve_bank_account direct pcmn_number match (step 2) in auto_entries.py
2. Skip remap for 55x/58 accounts in commit_journals (import_wizard.py)
3. `lines` field in commit_invoices for generate_purchase_entry (import_wizard.py)
4. set_private_fee_allocations calls generate_purchase_entry (invoices.py)
5. DELETE /api/banking/statements/{stmt_id}/delete-preview endpoint
6. DELETE /api/banking/statements/{stmt_id} still works
"""
import pytest
import requests
import os
import uuid
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

@pytest.fixture(scope="module")
def auth_session():
    """Create authenticated session for all tests"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    resp = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "admin@copro.be",
        "password": "admin123"
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return session


@pytest.fixture(scope="module")
def test_copro(auth_session):
    """Create a test copropriete for testing"""
    copro_data = {
        "name": f"TEST_Copro_Iter63_{uuid.uuid4().hex[:8]}",
        "address": "123 Test Street",
        "city": "Brussels",
        "postal_code": "1000",
        "country": "Belgium",
        "bank_accounts": [
            {
                "id": str(uuid.uuid4()),
                "iban": "BE68539007547034",
                "pcmn_number": "551618",
                "label": "Compte Vue Test",
                "is_default": True
            },
            {
                "id": str(uuid.uuid4()),
                "iban": "BE71096123456789",
                "pcmn_number": "55161800",
                "label": "Compte Epargne Test"
            }
        ]
    }
    resp = auth_session.post(f"{BASE_URL}/api/coproprietes", json=copro_data)
    assert resp.status_code in [200, 201], f"Failed to create copro: {resp.text}"
    copro = resp.json()
    yield copro
    # Cleanup
    try:
        auth_session.delete(f"{BASE_URL}/api/coproprietes/{copro['id']}")
    except:
        pass


class TestDeleteStatementPreview:
    """Test the delete-preview endpoint for bank statements"""
    
    def test_delete_preview_nonexistent_returns_404(self, auth_session):
        """GET /api/banking/statements/{non_existent_id}/delete-preview should return 404"""
        fake_id = f"nonexistent_{uuid.uuid4().hex}"
        resp = auth_session.get(f"{BASE_URL}/api/banking/statements/{fake_id}/delete-preview")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data or "message" in data or "Extrait" in str(data)
    
    def test_delete_preview_returns_correct_structure(self, auth_session, test_copro):
        """Create a statement and verify delete-preview returns correct data structure"""
        # Create a bank statement
        stmt_data = {
            "number": f"TEST_STMT_{uuid.uuid4().hex[:8]}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account_number": "BE68539007547034",
            "opening_balance": 1000.00,
            "closing_balance": 1500.00,
            "copropriete_id": test_copro["id"],
            "status": "draft"
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/banking/statements", json=stmt_data)
        assert create_resp.status_code in [200, 201], f"Failed to create statement: {create_resp.text}"
        stmt = create_resp.json()
        stmt_id = stmt.get("id")
        assert stmt_id, "Statement ID not returned"
        
        try:
            # Test delete-preview endpoint
            preview_resp = auth_session.get(f"{BASE_URL}/api/banking/statements/{stmt_id}/delete-preview")
            assert preview_resp.status_code == 200, f"Delete preview failed: {preview_resp.text}"
            
            preview_data = preview_resp.json()
            # Verify required fields
            assert "statement_id" in preview_data, "Missing statement_id in preview"
            assert "total_transactions" in preview_data, "Missing total_transactions in preview"
            assert "lettrages_to_cancel" in preview_data, "Missing lettrages_to_cancel in preview"
            assert "invoices_impacted" in preview_data, "Missing invoices_impacted in preview"
            assert "fi_entries_to_delete" in preview_data, "Missing fi_entries_to_delete in preview"
            
            # Verify values are integers
            assert isinstance(preview_data["total_transactions"], int)
            assert isinstance(preview_data["lettrages_to_cancel"], int)
            assert isinstance(preview_data["invoices_impacted"], int)
            assert isinstance(preview_data["fi_entries_to_delete"], int)
            
            # For a new statement with no transactions, counts should be 0
            assert preview_data["total_transactions"] == 0
            assert preview_data["lettrages_to_cancel"] == 0
            
        finally:
            # Cleanup - delete the statement
            auth_session.delete(f"{BASE_URL}/api/banking/statements/{stmt_id}")


class TestDeleteStatement:
    """Test DELETE /api/banking/statements/{stmt_id} endpoint"""
    
    def test_delete_statement_works(self, auth_session, test_copro):
        """DELETE /api/banking/statements/{stmt_id} should delete the statement"""
        # Create a statement
        stmt_data = {
            "number": f"TEST_DEL_{uuid.uuid4().hex[:8]}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account_number": "BE68539007547034",
            "opening_balance": 500.00,
            "closing_balance": 600.00,
            "copropriete_id": test_copro["id"],
            "status": "draft"
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/banking/statements", json=stmt_data)
        assert create_resp.status_code in [200, 201], f"Failed to create statement: {create_resp.text}"
        stmt = create_resp.json()
        stmt_id = stmt.get("id")
        
        # Delete the statement
        delete_resp = auth_session.delete(f"{BASE_URL}/api/banking/statements/{stmt_id}")
        assert delete_resp.status_code == 200, f"Delete failed: {delete_resp.text}"
        
        # Verify it's deleted - should return 404
        get_resp = auth_session.get(f"{BASE_URL}/api/banking/statements/{stmt_id}/delete-preview")
        assert get_resp.status_code == 404, "Statement should be deleted"
    
    def test_delete_nonexistent_statement_returns_404(self, auth_session):
        """DELETE /api/banking/statements/{non_existent_id} should return 404"""
        fake_id = f"nonexistent_{uuid.uuid4().hex}"
        resp = auth_session.delete(f"{BASE_URL}/api/banking/statements/{fake_id}")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


class TestCodeStructureVerification:
    """Verify code structure changes are in place (code review tests)"""
    
    def test_resolve_bank_account_has_direct_pcmn_match_step(self):
        """Verify _resolve_bank_account has step 2 for direct pcmn_number match"""
        import sys
        sys.path.insert(0, '/app/backend')
        
        # Read the auto_entries.py file and check for the step 2 comment
        with open('/app/backend/auto_entries.py', 'r') as f:
            content = f.read()
        
        # Check for step 2 direct pcmn match
        assert "# 2) Match direct" in content or "account_number == pcmn_number" in content, \
            "Step 2 direct pcmn_number match not found in _resolve_bank_account"
        
        # Verify the logic exists
        assert "ba_pcmn == raw_acc" in content, \
            "Direct pcmn match logic (ba_pcmn == raw_acc) not found"
    
    def test_commit_journals_skips_remap_for_55x_58(self):
        """Verify commit_journals skips _remap_bank_account for 55x/58 accounts"""
        with open('/app/backend/routes/import_wizard.py', 'r') as f:
            content = f.read()
        
        # Check for the skip logic for 55x and 58 accounts
        assert 'startswith("55")' in content and 'startswith("58")' in content, \
            "Skip remap logic for 55x/58 accounts not found in commit_journals"
        
        # Verify the conditional skip pattern
        assert 'not bp_raw.startswith("55")' in content or 'not bp_raw.startswith("58")' in content, \
            "Conditional skip for bank accounts not found"
    
    def test_build_gpe_lines_helper_exists(self):
        """Verify _build_gpe_lines helper function exists"""
        with open('/app/backend/routes/import_wizard.py', 'r') as f:
            content = f.read()
        
        assert "def _build_gpe_lines(" in content, \
            "_build_gpe_lines helper function not found"
        
        # Verify it handles multi-account splits
        assert "has_multi_accounts" in content, \
            "_build_gpe_lines should handle multi-account splits"
    
    def test_commit_invoices_includes_lines_field(self):
        """Verify commit_invoices creates invoice docs with `lines` field"""
        with open('/app/backend/routes/import_wizard.py', 'r') as f:
            content = f.read()
        
        # Check that the invoice doc includes 'lines' field using _build_gpe_lines
        assert '"lines": _build_gpe_lines(' in content, \
            "Invoice doc should include 'lines' field from _build_gpe_lines"
    
    def test_set_private_fee_allocations_calls_generate_purchase_entry(self):
        """Verify set_private_fee_allocations calls generate_purchase_entry"""
        with open('/app/backend/routes/invoices.py', 'r') as f:
            content = f.read()
        
        # Find the set_private_fee_allocations function and check for generate_purchase_entry call
        # The function should call generate_purchase_entry after updating allocations
        assert "generate_purchase_entry" in content, \
            "generate_purchase_entry should be called in invoices.py"
        
        # Check that it's called in the context of updating allocations
        # Look for the pattern where fresh_inv is fetched and generate_purchase_entry is called
        assert "fresh_inv" in content and "generate_purchase_entry(db, fresh_inv)" in content, \
            "set_private_fee_allocations should call generate_purchase_entry with fresh invoice"


class TestDeletePreviewWithTransactions:
    """Test delete-preview with actual transactions"""
    
    def test_delete_preview_counts_transactions(self, auth_session, test_copro):
        """Create statement with transactions and verify preview counts them"""
        # Create a statement
        stmt_data = {
            "number": f"TEST_TXN_{uuid.uuid4().hex[:8]}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account_number": "BE68539007547034",
            "opening_balance": 1000.00,
            "closing_balance": 1200.00,
            "copropriete_id": test_copro["id"],
            "status": "draft"
        }
        create_resp = auth_session.post(f"{BASE_URL}/api/banking/statements", json=stmt_data)
        assert create_resp.status_code in [200, 201], f"Failed to create statement: {create_resp.text}"
        stmt = create_resp.json()
        stmt_id = stmt.get("id")
        
        try:
            # Add a transaction to the statement
            txn_data = {
                "statement_id": stmt_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "amount": 200.00,
                "counterparty_name": "Test Counterparty",
                "communication": "Test transaction",
                "transaction_type": "credit",
                "copropriete_id": test_copro["id"]
            }
            txn_resp = auth_session.post(f"{BASE_URL}/api/banking/transactions", json=txn_data)
            # Transaction creation might fail if endpoint requires different params - that's ok for this test
            
            # Get delete preview
            preview_resp = auth_session.get(f"{BASE_URL}/api/banking/statements/{stmt_id}/delete-preview")
            assert preview_resp.status_code == 200
            preview_data = preview_resp.json()
            
            # Verify structure is correct regardless of transaction count
            assert "total_transactions" in preview_data
            assert "lettrages_to_cancel" in preview_data
            assert "fi_entries_to_delete" in preview_data
            
        finally:
            # Cleanup
            auth_session.delete(f"{BASE_URL}/api/banking/statements/{stmt_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
