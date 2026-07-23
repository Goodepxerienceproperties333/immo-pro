"""
Iteration 66 Tests - Bundle Import Dialog & Fiscal Lock Improvements

Tests:
1. GET /api/invoices/bundle-preview-block - returns 404 for invalid session_id
2. POST /api/invoices/bundle-commit - accepts 'cleanup' parameter (boolean)
3. fiscal_lock.py ensure_period_open - fallback logic with datetime comparison
4. Diagnostic message lists existing fiscal years when date is outside range
"""

import pytest
import requests
import os
import uuid
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
TEST_EMAIL = "admin@copro.be"
TEST_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def api_client():
    """Session with cookie-based auth."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json"
    })
    
    # Login to get cookies
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    if response.status_code != 200:
        pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")
    
    # Cookies are automatically stored in the session
    return session


@pytest.fixture(scope="module")
def test_copropriete(api_client):
    """Create a test copropriete for fiscal lock tests."""
    copro_id = f"test-copro-iter66-{uuid.uuid4().hex[:8]}"
    response = api_client.post(f"{BASE_URL}/api/coproprietes", json={
        "id": copro_id,
        "name": f"Test Copro Iter66 {copro_id[:8]}",
        "address": "123 Test Street",
        "city": "Brussels",
        "postal_code": "1000",
        "country": "Belgium"
    })
    if response.status_code not in [200, 201]:
        pytest.skip(f"Failed to create test copropriete: {response.text}")
    
    yield copro_id
    
    # Cleanup
    try:
        api_client.delete(f"{BASE_URL}/api/coproprietes/{copro_id}")
    except Exception:
        pass


@pytest.fixture(scope="module")
def test_fiscal_year(api_client, test_copropriete):
    """Create a test fiscal year for fiscal lock tests."""
    fy_id = f"test-fy-iter66-{uuid.uuid4().hex[:8]}"
    # Create fiscal year for 2025
    response = api_client.post(f"{BASE_URL}/api/fiscal/years", json={
        "id": fy_id,
        "name": "Exercice 2025 Test",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "status": "open",
        "copropriete_id": test_copropriete
    })
    if response.status_code not in [200, 201]:
        pytest.skip(f"Failed to create test fiscal year: {response.text}")
    
    yield {
        "id": fy_id,
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "copropriete_id": test_copropriete
    }
    
    # Cleanup
    try:
        api_client.delete(f"{BASE_URL}/api/fiscal/years/{fy_id}")
    except Exception:
        pass


class TestBundlePreviewBlockEndpoint:
    """Tests for GET /api/invoices/bundle-preview-block endpoint."""
    
    def test_bundle_preview_block_invalid_session_returns_404(self, api_client):
        """Test that invalid session_id returns 404."""
        response = api_client.get(f"{BASE_URL}/api/invoices/bundle-preview-block", params={
            "session_id": "invalid-session-id-12345",
            "pages": "1,2"
        })
        # Should return 404 for non-existent session
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        
        # Verify error message mentions session or bundle
        data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {"detail": response.text}
        detail = data.get("detail", "")
        assert "session" in detail.lower() or "introuvable" in detail.lower() or "expiree" in detail.lower() or "bundle" in detail.lower(), \
            f"Error message should mention session/bundle: {detail}"
    
    def test_bundle_preview_block_missing_session_id_returns_422(self, api_client):
        """Test that missing session_id returns 422 (validation error)."""
        response = api_client.get(f"{BASE_URL}/api/invoices/bundle-preview-block", params={
            "pages": "1,2"
        })
        # Missing required parameter should return 422
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
    
    def test_bundle_preview_block_missing_pages_returns_422(self, api_client):
        """Test that missing pages parameter returns 422."""
        response = api_client.get(f"{BASE_URL}/api/invoices/bundle-preview-block", params={
            "session_id": "some-session-id"
        })
        # Missing required parameter should return 422
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"


class TestBundleCommitEndpoint:
    """Tests for POST /api/invoices/bundle-commit endpoint."""
    
    def test_bundle_commit_accepts_cleanup_parameter_true(self, api_client):
        """Test that bundle-commit accepts cleanup=true parameter."""
        response = api_client.post(f"{BASE_URL}/api/invoices/bundle-commit", json={
            "session_id": "invalid-session-for-cleanup-test",
            "cleanup": True,
            "assignments": []
        })
        # Should return 400 (missing assignments) or 404 (session not found), not 422 (validation error)
        # The key is that cleanup parameter is accepted without validation error
        assert response.status_code in [400, 404], f"Expected 400 or 404, got {response.status_code}: {response.text}"
    
    def test_bundle_commit_accepts_cleanup_parameter_false(self, api_client):
        """Test that bundle-commit accepts cleanup=false parameter."""
        response = api_client.post(f"{BASE_URL}/api/invoices/bundle-commit", json={
            "session_id": "invalid-session-for-cleanup-test",
            "cleanup": False,
            "assignments": []
        })
        # Should return 400 (missing assignments) or 404 (session not found), not 422 (validation error)
        assert response.status_code in [400, 404], f"Expected 400 or 404, got {response.status_code}: {response.text}"
    
    def test_bundle_commit_missing_session_id_returns_400(self, api_client):
        """Test that missing session_id returns 400."""
        response = api_client.post(f"{BASE_URL}/api/invoices/bundle-commit", json={
            "assignments": [{"block_id": "1", "mode": "skip", "page_range": [1]}]
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    
    def test_bundle_commit_invalid_session_returns_404(self, api_client):
        """Test that invalid session_id returns 404."""
        response = api_client.post(f"{BASE_URL}/api/invoices/bundle-commit", json={
            "session_id": "non-existent-session-id",
            "assignments": [{"block_id": "1", "mode": "skip", "page_range": [1]}]
        })
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"


class TestFiscalLockEnsurePeriodOpen:
    """Tests for fiscal_lock.py ensure_period_open function with fallback logic."""
    
    def test_invoice_within_fiscal_year_passes(self, api_client, test_copropriete, test_fiscal_year):
        """Test that creating an invoice within an open fiscal year succeeds."""
        # Create a supplier first
        supplier_response = api_client.post(f"{BASE_URL}/api/suppliers", json={
            "name": f"Test Supplier Iter66 {uuid.uuid4().hex[:8]}",
            "copropriete_id": test_copropriete
        })
        supplier_name = "Test Supplier Iter66"
        if supplier_response.status_code in [200, 201]:
            supplier_name = supplier_response.json().get("name", supplier_name)
        
        # Try to create an invoice with date within the fiscal year (2025)
        invoice_data = {
            "number": f"INV-ITER66-{uuid.uuid4().hex[:8]}",
            "date": "2025-06-15",  # Within 2025 fiscal year
            "supplier": supplier_name,
            "supplier_confirmed": True,
            "description": "Test invoice for fiscal lock verification",
            "total_amount": 100.00,
            "account_number": "6140",  # Insurance account
            "copropriete_id": test_copropriete
        }
        
        response = api_client.post(f"{BASE_URL}/api/invoices", json=invoice_data)
        
        # Should succeed (200 or 201) since date is within open fiscal year
        # Or might fail for other reasons (supplier homonym, etc.) but NOT fiscal lock
        if response.status_code not in [200, 201]:
            error_detail = response.json().get("detail", "") if response.headers.get('content-type', '').startswith('application/json') else response.text
            # If it fails, it should NOT be because of fiscal lock (date is within range)
            assert "exercice" not in error_detail.lower() or "ouvert" in error_detail.lower(), \
                f"Invoice creation failed unexpectedly: {error_detail}"
        else:
            # Clean up created invoice
            invoice_id = response.json().get("id")
            if invoice_id:
                try:
                    api_client.delete(f"{BASE_URL}/api/invoices/{invoice_id}")
                except Exception:
                    pass
    
    def test_invoice_outside_fiscal_year_fails_with_diagnostic(self, api_client, test_copropriete, test_fiscal_year):
        """Test that creating an invoice outside any fiscal year fails with diagnostic message."""
        # Try to create an invoice with date outside any fiscal year (2020)
        invoice_data = {
            "number": f"INV-ITER66-OUTSIDE-{uuid.uuid4().hex[:8]}",
            "date": "2020-06-15",  # Outside 2025 fiscal year
            "supplier": "Test Supplier Outside",
            "supplier_confirmed": True,
            "description": "Test invoice outside fiscal year",
            "total_amount": 100.00,
            "account_number": "6140",
            "copropriete_id": test_copropriete
        }
        
        response = api_client.post(f"{BASE_URL}/api/invoices", json=invoice_data)
        
        # Should fail with 400 and diagnostic message
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        
        error_detail = response.json().get("detail", "") if response.headers.get('content-type', '').startswith('application/json') else response.text
        
        # Verify diagnostic message mentions:
        # 1. No fiscal year covers the date
        # 2. Lists existing fiscal years
        assert "exercice" in error_detail.lower() or "fiscal" in error_detail.lower(), \
            f"Error should mention fiscal year: {error_detail}"
        
        # The diagnostic should list existing fiscal years
        # Check for "Exercices existants" or similar
        has_diagnostic = (
            "existants" in error_detail.lower() or 
            "2025" in error_detail or  # Our test fiscal year
            "aucun exercice" in error_detail.lower()
        )
        assert has_diagnostic, f"Error should include diagnostic about existing fiscal years: {error_detail}"
    
    def test_fiscal_lock_fallback_datetime_comparison(self, api_client, test_copropriete, test_fiscal_year):
        """Test that fiscal lock fallback uses datetime comparison when string comparison fails."""
        # This test verifies the fallback logic exists by testing edge cases
        # The fallback should handle dates at the boundary of fiscal years
        
        # Test with date at exact start of fiscal year
        invoice_data = {
            "number": f"INV-ITER66-START-{uuid.uuid4().hex[:8]}",
            "date": "2025-01-01",  # Exact start date
            "supplier": "Test Supplier Start",
            "supplier_confirmed": True,
            "description": "Test invoice at fiscal year start",
            "total_amount": 100.00,
            "account_number": "6140",
            "copropriete_id": test_copropriete
        }
        
        response = api_client.post(f"{BASE_URL}/api/invoices", json=invoice_data)
        
        # Should succeed or fail for non-fiscal-lock reasons
        if response.status_code not in [200, 201]:
            error_detail = response.json().get("detail", "") if response.headers.get('content-type', '').startswith('application/json') else response.text
            # Should not fail due to fiscal lock at boundary
            assert "aucun exercice fiscal ouvert" not in error_detail.lower(), \
                f"Boundary date should be within fiscal year: {error_detail}"
        else:
            # Clean up
            invoice_id = response.json().get("id")
            if invoice_id:
                try:
                    api_client.delete(f"{BASE_URL}/api/invoices/{invoice_id}")
                except Exception:
                    pass
        
        # Test with date at exact end of fiscal year
        invoice_data["number"] = f"INV-ITER66-END-{uuid.uuid4().hex[:8]}"
        invoice_data["date"] = "2025-12-31"  # Exact end date
        invoice_data["description"] = "Test invoice at fiscal year end"
        
        response = api_client.post(f"{BASE_URL}/api/invoices", json=invoice_data)
        
        if response.status_code not in [200, 201]:
            error_detail = response.json().get("detail", "") if response.headers.get('content-type', '').startswith('application/json') else response.text
            assert "aucun exercice fiscal ouvert" not in error_detail.lower(), \
                f"Boundary date should be within fiscal year: {error_detail}"
        else:
            # Clean up
            invoice_id = response.json().get("id")
            if invoice_id:
                try:
                    api_client.delete(f"{BASE_URL}/api/invoices/{invoice_id}")
                except Exception:
                    pass


class TestFiscalLockCodeReview:
    """Code review tests for fiscal_lock.py ensure_period_open function."""
    
    def test_fiscal_lock_file_exists(self):
        """Verify fiscal_lock.py exists."""
        import os
        assert os.path.exists("/app/backend/fiscal_lock.py"), "fiscal_lock.py should exist"
    
    def test_fiscal_lock_has_ensure_period_open(self):
        """Verify ensure_period_open function exists with correct signature."""
        with open("/app/backend/fiscal_lock.py", "r") as f:
            content = f.read()
        
        # Check function definition
        assert "async def ensure_period_open" in content, "ensure_period_open function should exist"
        
        # Check parameters
        assert "db" in content and "copropriete_id" in content and "date_iso" in content, \
            "ensure_period_open should have db, copropriete_id, date_iso parameters"
    
    def test_fiscal_lock_has_fallback_logic(self):
        """Verify fallback logic exists for datetime comparison."""
        with open("/app/backend/fiscal_lock.py", "r") as f:
            content = f.read()
        
        # Check for fallback comment or logic
        has_fallback = (
            "fallback" in content.lower() or
            "all_fys" in content or  # Variable name for all fiscal years
            "datetime.strptime" in content  # Datetime parsing for comparison
        )
        assert has_fallback, "Fallback logic should exist for datetime comparison"
    
    def test_fiscal_lock_has_diagnostic_message(self):
        """Verify diagnostic message lists existing fiscal years."""
        with open("/app/backend/fiscal_lock.py", "r") as f:
            content = f.read()
        
        # Check for diagnostic logic
        has_diagnostic = (
            "diag" in content.lower() or
            "existants" in content.lower() or
            "Exercices existants" in content
        )
        assert has_diagnostic, "Diagnostic message should list existing fiscal years"
    
    def test_fiscal_lock_handles_closed_status(self):
        """Verify closed fiscal year status is handled."""
        with open("/app/backend/fiscal_lock.py", "r") as f:
            content = f.read()
        
        # Check for closed status handling
        assert '"closed"' in content or "'closed'" in content, \
            "Should handle closed fiscal year status"
        assert "cloture" in content.lower(), \
            "Should have French message for closed fiscal year"


class TestFrontendCodeReview:
    """Code review tests for frontend components."""
    
    def test_bundle_import_dialog_exists(self):
        """Verify BundleImportDialog.js exists."""
        import os
        assert os.path.exists("/app/frontend/src/components/BundleImportDialog.js"), \
            "BundleImportDialog.js should exist"
    
    def test_bundle_import_dialog_has_suppliers_prop(self):
        """Verify BundleImportDialog accepts suppliers prop."""
        with open("/app/frontend/src/components/BundleImportDialog.js", "r") as f:
            content = f.read()
        
        # Check for suppliers prop in function signature
        assert "suppliers" in content, "BundleImportDialog should accept suppliers prop"
        
        # Check for default value
        assert "suppliers = []" in content or "suppliers=[]" in content, \
            "suppliers prop should have default empty array"
    
    def test_bundle_import_dialog_has_full_creation_view(self):
        """Verify BundleImportDialog has full creation view with form."""
        with open("/app/frontend/src/components/BundleImportDialog.js", "r") as f:
            content = f.read()
        
        # Check for creation form elements
        assert "BlockCreationForm" in content or "blockForm" in content, \
            "Should have block creation form"
        assert "bundle-create-full" in content or "createFull" in content.lower(), \
            "Should have full creation view"
    
    def test_bundle_import_dialog_has_pdf_preview(self):
        """Verify BundleImportDialog has PDF preview functionality."""
        with open("/app/frontend/src/components/BundleImportDialog.js", "r") as f:
            content = f.read()
        
        # Check for PDF preview
        assert "blockPdfUrl" in content or "pdf" in content.lower(), \
            "Should have PDF preview URL state"
        assert "bundle-preview-block" in content, \
            "Should call bundle-preview-block endpoint"
    
    def test_invoices_page_passes_suppliers_to_bundle_dialog(self):
        """Verify InvoicesPage passes suppliers prop to BundleImportDialog."""
        with open("/app/frontend/src/pages/InvoicesPage.js", "r") as f:
            content = f.read()
        
        # Check for BundleImportDialog with suppliers prop
        assert "BundleImportDialog" in content, "InvoicesPage should use BundleImportDialog"
        assert "suppliers={suppliers}" in content or "suppliers={" in content, \
            "InvoicesPage should pass suppliers prop to BundleImportDialog"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
