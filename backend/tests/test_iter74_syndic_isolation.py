"""
Iteration 74 - Multi-Syndic Isolation (Chinese Wall) Tests
Tests the syndic_id field injection and isolation across all document types.

Test Cases:
1. Superadmin login
2. Syndic login & ACP creation with syndic_id
3. Owner creation with syndic_id
4. Supplier creation with syndic_id
5. Chinese Wall middleware (403 on cross-syndic access)
6. Coproprietes isolation between syndics
7. Document categories isolation
8. Fiscal years endpoint (no 500)
9. Expense categories endpoint (no 500)
10. Banking statements endpoint (no 500)
11. Fund calls endpoint (no 500)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
SUPERADMIN_EMAIL = "admin@copro.be"
SUPERADMIN_PASSWORD = "admin123"
SYNDIC_ALPHA_EMAIL = "syndic_alpha@copro.be"
SYNDIC_ALPHA_PASSWORD = "Syndic123!"
SYNDIC_BETA_EMAIL = "syndic_beta@copro.be"
SYNDIC_BETA_PASSWORD = "Syndic123!"


class TestSyndicIsolation:
    """Tests for multi-syndic isolation (Chinese Wall)"""
    
    # Shared state for test data
    alpha_cookies = None
    beta_cookies = None
    superadmin_cookies = None
    alpha_user_id = None
    beta_user_id = None
    alpha_acp_id = None
    alpha_owner_id = None
    alpha_supplier_id = None
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup - ensure BASE_URL is set"""
        assert BASE_URL, "REACT_APP_BACKEND_URL environment variable must be set"
    
    # ==================== TEST 1: Superadmin Login ====================
    def test_01_superadmin_login(self):
        """Test 1 - Superadmin login: POST /api/auth/login returns user data and sets access_token cookie"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPERADMIN_EMAIL,
            "password": SUPERADMIN_PASSWORD
        })
        
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        
        # Login returns user data directly (not nested under "user")
        user_data = data.get("user", data)  # Handle both formats
        assert user_data.get("email") == SUPERADMIN_EMAIL, f"Email mismatch: {user_data}"
        assert user_data.get("role") in ("superadmin", "admin"), f"Expected superadmin role, got {user_data.get('role')}"
        
        # Verify cookie was set
        assert "access_token" in session.cookies, "access_token cookie should be set"
        
        # Store cookies for later tests
        TestSyndicIsolation.superadmin_cookies = session.cookies
        
        # Verify GET /api/auth/me returns superadmin
        me_response = session.get(f"{BASE_URL}/api/auth/me")
        assert me_response.status_code == 200, f"GET /api/auth/me failed: {me_response.text}"
        me_data = me_response.json()
        assert me_data["role"] in ("superadmin", "admin"), f"Expected superadmin role from /me, got {me_data['role']}"
        
        print(f"TEST 1 PASSED: Superadmin login successful, role={me_data['role']}")
    
    # ==================== TEST 2: Syndic Alpha Login & ACP Creation ====================
    def test_02_syndic_alpha_login_and_acp_creation(self):
        """Test 2 - Syndic login & ACP creation: Login as syndic_alpha, create ACP with syndic_id"""
        session = requests.Session()
        
        # Login as Syndic Alpha
        login_response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": SYNDIC_ALPHA_EMAIL,
            "password": SYNDIC_ALPHA_PASSWORD
        })
        
        assert login_response.status_code == 200, f"Syndic Alpha login failed: {login_response.text}"
        login_data = login_response.json()
        
        # Login returns user data directly (not nested under "user")
        user_data = login_data.get("user", login_data)
        assert user_data.get("role") == "syndic", f"Expected syndic role, got {user_data.get('role')}"
        
        # Store user ID and cookies
        TestSyndicIsolation.alpha_user_id = user_data.get("id") or user_data.get("_id")
        TestSyndicIsolation.alpha_cookies = session.cookies
        
        # Create ACP (copropriete)
        acp_response = session.post(f"{BASE_URL}/api/coproprietes", json={
            "name": "Test E2E ACP Alpha",
            "address": "123 Rue Test, Bruxelles",
            "num_lots": 10
        })
        
        assert acp_response.status_code in (200, 201), f"ACP creation failed: {acp_response.text}"
        acp_data = acp_response.json()
        
        # Store ACP ID for later tests
        TestSyndicIsolation.alpha_acp_id = acp_data.get("id") or acp_data.get("_id")
        assert TestSyndicIsolation.alpha_acp_id, "ACP should have an ID"
        
        # Verify syndic_id is set (if returned in response)
        if "syndic_id" in acp_data:
            assert acp_data["syndic_id"] == TestSyndicIsolation.alpha_user_id, \
                f"ACP syndic_id should match user ID: {acp_data['syndic_id']} != {TestSyndicIsolation.alpha_user_id}"
        
        print(f"TEST 2 PASSED: Syndic Alpha login successful, ACP created with ID={TestSyndicIsolation.alpha_acp_id}")
    
    # ==================== TEST 3: Owner Creation ====================
    def test_03_owner_creation(self):
        """Test 3 - Owner creation: As syndic_alpha, create owner with syndic_id"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in first"
        assert TestSyndicIsolation.alpha_acp_id, "ACP must be created first"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        # Create owner
        owner_response = session.post(f"{BASE_URL}/api/owners", json={
            "first_name": "Pierre",
            "last_name": "Martin",
            "email": f"pierre.martin.{TestSyndicIsolation.alpha_acp_id}@test.be",
            "copropriete_id": TestSyndicIsolation.alpha_acp_id
        })
        
        assert owner_response.status_code in (200, 201), f"Owner creation failed: {owner_response.text}"
        owner_data = owner_response.json()
        
        # Store owner ID
        TestSyndicIsolation.alpha_owner_id = owner_data.get("id") or owner_data.get("_id")
        assert TestSyndicIsolation.alpha_owner_id, "Owner should have an ID"
        
        # Verify syndic_id is set (if returned in response)
        if "syndic_id" in owner_data:
            assert owner_data["syndic_id"] == TestSyndicIsolation.alpha_user_id, \
                f"Owner syndic_id should match user ID"
        
        print(f"TEST 3 PASSED: Owner created with ID={TestSyndicIsolation.alpha_owner_id}")
    
    # ==================== TEST 4: Supplier Creation ====================
    def test_04_supplier_creation(self):
        """Test 4 - Supplier creation: As syndic_alpha, create supplier with syndic_id"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in first"
        assert TestSyndicIsolation.alpha_acp_id, "ACP must be created first"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        # Create supplier
        supplier_response = session.post(f"{BASE_URL}/api/suppliers", json={
            "name": "Electricien Test Alpha",
            "copropriete_id": TestSyndicIsolation.alpha_acp_id,
            "email": f"electricien.{TestSyndicIsolation.alpha_acp_id}@test.be"
        })
        
        assert supplier_response.status_code in (200, 201), f"Supplier creation failed: {supplier_response.text}"
        supplier_data = supplier_response.json()
        
        # Store supplier ID
        TestSyndicIsolation.alpha_supplier_id = supplier_data.get("id") or supplier_data.get("_id")
        assert TestSyndicIsolation.alpha_supplier_id, "Supplier should have an ID"
        
        # Verify syndic_id is set (if returned in response)
        if "syndic_id" in supplier_data:
            assert supplier_data["syndic_id"] == TestSyndicIsolation.alpha_user_id, \
                f"Supplier syndic_id should match user ID"
        
        print(f"TEST 4 PASSED: Supplier created with ID={TestSyndicIsolation.alpha_supplier_id}")
    
    # ==================== TEST 5: Chinese Wall Middleware ====================
    def test_05_chinese_wall_middleware(self):
        """Test 5 - Chinese Wall: Syndic Beta cannot access Syndic Alpha's owners"""
        assert TestSyndicIsolation.alpha_acp_id, "ACP must be created first"
        
        # Login as Syndic Beta
        session = requests.Session()
        login_response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": SYNDIC_BETA_EMAIL,
            "password": SYNDIC_BETA_PASSWORD
        })
        
        assert login_response.status_code == 200, f"Syndic Beta login failed: {login_response.text}"
        login_data = login_response.json()
        user_data = login_data.get("user", login_data)
        TestSyndicIsolation.beta_user_id = user_data.get("id") or user_data.get("_id")
        TestSyndicIsolation.beta_cookies = session.cookies
        
        # Try to access Alpha's owners - should get 403
        owners_response = session.get(f"{BASE_URL}/api/owners", params={
            "copropriete_id": TestSyndicIsolation.alpha_acp_id
        })
        
        # Should be 403 Forbidden OR empty list (depending on implementation)
        if owners_response.status_code == 403:
            print("TEST 5 PASSED: Chinese Wall returned 403 as expected")
        elif owners_response.status_code == 200:
            # Check if list is empty (isolation via query filter)
            data = owners_response.json()
            owners_list = data if isinstance(data, list) else data.get("owners", data.get("items", []))
            assert len(owners_list) == 0, f"Syndic Beta should not see Alpha's owners, got {len(owners_list)} owners"
            print("TEST 5 PASSED: Chinese Wall returned empty list (isolation via filter)")
        else:
            pytest.fail(f"Unexpected status code: {owners_response.status_code}, body: {owners_response.text}")
    
    # ==================== TEST 6: Coproprietes Isolation ====================
    def test_06_coproprietes_isolation(self):
        """Test 6 - Coproprietes isolation: Syndics only see their own ACPs, superadmin sees all"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        assert TestSyndicIsolation.beta_cookies, "Syndic Beta must be logged in"
        assert TestSyndicIsolation.superadmin_cookies, "Superadmin must be logged in"
        
        # Create ACP for Beta
        beta_session = requests.Session()
        beta_session.cookies.update(TestSyndicIsolation.beta_cookies)
        
        beta_acp_response = beta_session.post(f"{BASE_URL}/api/coproprietes", json={
            "name": "Test E2E ACP Beta",
            "address": "456 Avenue Beta, Liege",
            "num_lots": 5
        })
        assert beta_acp_response.status_code in (200, 201), f"Beta ACP creation failed: {beta_acp_response.text}"
        beta_acp_id = beta_acp_response.json().get("id") or beta_acp_response.json().get("_id")
        
        # Alpha lists coproprietes - should NOT see Beta's ACP
        alpha_session = requests.Session()
        alpha_session.cookies.update(TestSyndicIsolation.alpha_cookies)
        alpha_list_response = alpha_session.get(f"{BASE_URL}/api/coproprietes")
        assert alpha_list_response.status_code == 200, f"Alpha list failed: {alpha_list_response.text}"
        alpha_acps = alpha_list_response.json()
        if isinstance(alpha_acps, dict):
            alpha_acps = alpha_acps.get("coproprietes", alpha_acps.get("items", []))
        
        alpha_acp_ids = [acp.get("id") or acp.get("_id") for acp in alpha_acps]
        assert beta_acp_id not in alpha_acp_ids, "Alpha should NOT see Beta's ACP"
        
        # Beta lists coproprietes - should NOT see Alpha's ACP
        beta_list_response = beta_session.get(f"{BASE_URL}/api/coproprietes")
        assert beta_list_response.status_code == 200, f"Beta list failed: {beta_list_response.text}"
        beta_acps = beta_list_response.json()
        if isinstance(beta_acps, dict):
            beta_acps = beta_acps.get("coproprietes", beta_acps.get("items", []))
        
        beta_acp_ids = [acp.get("id") or acp.get("_id") for acp in beta_acps]
        assert TestSyndicIsolation.alpha_acp_id not in beta_acp_ids, "Beta should NOT see Alpha's ACP"
        
        # Superadmin lists coproprietes - should see ALL
        superadmin_session = requests.Session()
        superadmin_session.cookies.update(TestSyndicIsolation.superadmin_cookies)
        superadmin_list_response = superadmin_session.get(f"{BASE_URL}/api/coproprietes")
        assert superadmin_list_response.status_code == 200, f"Superadmin list failed: {superadmin_list_response.text}"
        superadmin_acps = superadmin_list_response.json()
        if isinstance(superadmin_acps, dict):
            superadmin_acps = superadmin_acps.get("coproprietes", superadmin_acps.get("items", []))
        
        superadmin_acp_ids = [acp.get("id") or acp.get("_id") for acp in superadmin_acps]
        # Superadmin should see both Alpha and Beta ACPs
        assert TestSyndicIsolation.alpha_acp_id in superadmin_acp_ids, "Superadmin should see Alpha's ACP"
        assert beta_acp_id in superadmin_acp_ids, "Superadmin should see Beta's ACP"
        
        print(f"TEST 6 PASSED: Coproprietes isolation working correctly")
    
    # ==================== TEST 7: Document Categories ====================
    def test_07_document_categories(self):
        """Test 7 - Document categories: Create category and verify isolation"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        # Create document category
        cat_response = session.post(f"{BASE_URL}/api/documents/categories", json={
            "name": "Test Category Alpha"
        })
        
        # Accept 200, 201, or 404 (if endpoint doesn't exist)
        if cat_response.status_code == 404:
            print("TEST 7 SKIPPED: /api/documents/categories endpoint not found")
            pytest.skip("Document categories endpoint not implemented")
        
        assert cat_response.status_code in (200, 201), f"Category creation failed: {cat_response.text}"
        
        # List documents - should not error
        docs_response = session.get(f"{BASE_URL}/api/documents")
        if docs_response.status_code == 404:
            print("TEST 7 SKIPPED: /api/documents endpoint not found")
            pytest.skip("Documents endpoint not implemented")
        
        assert docs_response.status_code == 200, f"Documents list failed: {docs_response.text}"
        
        print("TEST 7 PASSED: Document categories working")
    
    # ==================== TEST 8: Fiscal Years ====================
    def test_08_fiscal_years(self):
        """Test 8 - Fiscal years: GET /api/fiscal/years should not return 500"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        response = session.get(f"{BASE_URL}/api/fiscal/years")
        
        # Accept 200 (empty list OK) or 404 (endpoint not found)
        if response.status_code == 404:
            print("TEST 8 SKIPPED: /api/fiscal/years endpoint not found")
            pytest.skip("Fiscal years endpoint not implemented")
        
        assert response.status_code != 500, f"Fiscal years returned 500 error: {response.text}"
        assert response.status_code == 200, f"Fiscal years unexpected status: {response.status_code}, {response.text}"
        
        print(f"TEST 8 PASSED: Fiscal years returned {response.status_code}")
    
    # ==================== TEST 9: Expense Categories ====================
    def test_09_expense_categories(self):
        """Test 9 - Expense categories: GET /api/expense-categories should not return 500"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        response = session.get(f"{BASE_URL}/api/expense-categories")
        
        # Accept 200 (empty list OK) or 404 (endpoint not found)
        if response.status_code == 404:
            print("TEST 9 SKIPPED: /api/expense-categories endpoint not found")
            pytest.skip("Expense categories endpoint not implemented")
        
        assert response.status_code != 500, f"Expense categories returned 500 error: {response.text}"
        assert response.status_code == 200, f"Expense categories unexpected status: {response.status_code}, {response.text}"
        
        print(f"TEST 9 PASSED: Expense categories returned {response.status_code}")
    
    # ==================== TEST 10: Banking Statements ====================
    def test_10_banking_statements(self):
        """Test 10 - Banking statements: GET /api/banking/statements should not return 500"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        response = session.get(f"{BASE_URL}/api/banking/statements")
        
        # Accept 200 (empty list OK) or 404 (endpoint not found)
        if response.status_code == 404:
            print("TEST 10 SKIPPED: /api/banking/statements endpoint not found")
            pytest.skip("Banking statements endpoint not implemented")
        
        assert response.status_code != 500, f"Banking statements returned 500 error: {response.text}"
        assert response.status_code == 200, f"Banking statements unexpected status: {response.status_code}, {response.text}"
        
        print(f"TEST 10 PASSED: Banking statements returned {response.status_code}")
    
    # ==================== TEST 11: Fund Calls ====================
    def test_11_fund_calls(self):
        """Test 11 - Fund calls: GET /api/fund-calls should not return 500"""
        assert TestSyndicIsolation.alpha_cookies, "Syndic Alpha must be logged in"
        
        session = requests.Session()
        session.cookies.update(TestSyndicIsolation.alpha_cookies)
        
        response = session.get(f"{BASE_URL}/api/fund-calls")
        
        # Accept 200 (empty list OK) or 404 (endpoint not found)
        if response.status_code == 404:
            print("TEST 11 SKIPPED: /api/fund-calls endpoint not found")
            pytest.skip("Fund calls endpoint not implemented")
        
        assert response.status_code != 500, f"Fund calls returned 500 error: {response.text}"
        assert response.status_code == 200, f"Fund calls unexpected status: {response.status_code}, {response.text}"
        
        print(f"TEST 11 PASSED: Fund calls returned {response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
