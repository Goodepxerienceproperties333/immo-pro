"""
Iteration 70: Test purge syndic data endpoint
DELETE /api/admin/syndic/{user_id}/purge-data

Tests:
1. Returns 404 for invalid user_id
2. Returns 400 if confirm_email doesn't match the syndic's email
3. Returns message 'aucune ACP' if syndic has no copropriete_ids
4. Full purge flow - create syndic, copropriete, data, then purge and verify deletion
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Admin credentials
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin_session():
    """Get authenticated admin session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login as admin
    resp = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if resp.status_code != 200:
        pytest.skip(f"Admin login failed: {resp.status_code} - {resp.text}")
    
    return session


class TestPurgeEndpointValidation:
    """Test validation and error cases for purge endpoint"""
    
    def test_purge_returns_404_for_invalid_user_id(self, admin_session):
        """DELETE /api/admin/syndic/{user_id}/purge-data returns 404 for invalid user_id"""
        # Use a valid ObjectId format but non-existent
        fake_user_id = "000000000000000000000000"
        
        resp = admin_session.delete(
            f"{BASE_URL}/api/admin/syndic/{fake_user_id}/purge-data",
            json={"confirm_email": "test@test.com"}
        )
        
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "non trouve" in data.get("detail", "").lower() or "not found" in data.get("detail", "").lower()
        print(f"PASS: Returns 404 for non-existent user_id")
    
    def test_purge_returns_404_for_malformed_user_id(self, admin_session):
        """DELETE /api/admin/syndic/{user_id}/purge-data returns 404 for malformed user_id"""
        # Use an invalid ObjectId format
        invalid_user_id = "not-a-valid-objectid"
        
        resp = admin_session.delete(
            f"{BASE_URL}/api/admin/syndic/{invalid_user_id}/purge-data",
            json={"confirm_email": "test@test.com"}
        )
        
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: Returns 404 for malformed user_id")
    
    def test_purge_returns_400_if_confirm_email_mismatch(self, admin_session):
        """DELETE /api/admin/syndic/{user_id}/purge-data returns 400 if confirm_email doesn't match"""
        # First, create a test syndic
        test_email = f"test_purge_mismatch_{uuid.uuid4().hex[:8]}@test.com"
        
        create_resp = admin_session.post(f"{BASE_URL}/api/admin/users", json={
            "email": test_email,
            "name": "Test Syndic Mismatch",
            "role": "syndic",
            "password": "testpass123"
        })
        
        if create_resp.status_code != 200:
            pytest.skip(f"Could not create test syndic: {create_resp.text}")
        
        syndic_id = create_resp.json()["id"]
        
        try:
            # Try to purge with wrong email
            resp = admin_session.delete(
                f"{BASE_URL}/api/admin/syndic/{syndic_id}/purge-data",
                json={"confirm_email": "wrong@email.com"}
            )
            
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert "confirmation" in data.get("detail", "").lower() or "retapez" in data.get("detail", "").lower()
            print(f"PASS: Returns 400 when confirm_email doesn't match syndic email")
        finally:
            # Cleanup: delete the test syndic
            admin_session.delete(f"{BASE_URL}/api/admin/users/{syndic_id}")
    
    def test_purge_returns_message_when_no_acps(self, admin_session):
        """DELETE /api/admin/syndic/{user_id}/purge-data returns 'aucune ACP' message if syndic has no copropriete_ids"""
        # Create a syndic without any ACPs
        test_email = f"test_purge_no_acp_{uuid.uuid4().hex[:8]}@test.com"
        
        create_resp = admin_session.post(f"{BASE_URL}/api/admin/users", json={
            "email": test_email,
            "name": "Test Syndic No ACP",
            "role": "syndic",
            "password": "testpass123"
        })
        
        if create_resp.status_code != 200:
            pytest.skip(f"Could not create test syndic: {create_resp.text}")
        
        syndic_id = create_resp.json()["id"]
        
        try:
            # Try to purge syndic with no ACPs
            resp = admin_session.delete(
                f"{BASE_URL}/api/admin/syndic/{syndic_id}/purge-data",
                json={"confirm_email": test_email}
            )
            
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert "aucune acp" in data.get("message", "").lower(), f"Expected 'aucune ACP' in message, got: {data}"
            print(f"PASS: Returns 'aucune ACP' message when syndic has no copropriete_ids")
        finally:
            # Cleanup: delete the test syndic
            admin_session.delete(f"{BASE_URL}/api/admin/users/{syndic_id}")


class TestFullPurgeFlow:
    """Test full purge flow with data creation and verification"""
    
    def test_full_purge_flow(self, admin_session):
        """
        Full purge flow:
        1. Create a test syndic
        2. Login as syndic and create a copropriete
        3. Add data (lots, owners, suppliers, invoices, journal_entries)
        4. Purge as admin
        5. Verify all data is deleted
        """
        # Step 1: Create a test syndic
        test_email = f"test_purge_full_{uuid.uuid4().hex[:8]}@test.com"
        test_password = "testpass123"
        
        create_resp = admin_session.post(f"{BASE_URL}/api/admin/users", json={
            "email": test_email,
            "name": "Test Syndic Full Purge",
            "role": "syndic",
            "password": test_password
        })
        
        if create_resp.status_code != 200:
            pytest.skip(f"Could not create test syndic: {create_resp.text}")
        
        syndic_id = create_resp.json()["id"]
        print(f"Created test syndic: {syndic_id}")
        
        try:
            # Step 2: Login as syndic
            syndic_session = requests.Session()
            syndic_session.headers.update({"Content-Type": "application/json"})
            
            login_resp = syndic_session.post(f"{BASE_URL}/api/auth/login", json={
                "email": test_email,
                "password": test_password
            })
            
            if login_resp.status_code != 200:
                pytest.skip(f"Syndic login failed: {login_resp.text}")
            
            print(f"Logged in as syndic")
            
            # Step 3: Create a copropriete
            copro_resp = syndic_session.post(f"{BASE_URL}/api/coproprietes", json={
                "name": f"Test Copro Purge {uuid.uuid4().hex[:8]}",
                "address": "123 Test Street",
                "reference": f"TEST-{uuid.uuid4().hex[:6]}"
            })
            
            if copro_resp.status_code not in [200, 201]:
                pytest.skip(f"Could not create copropriete: {copro_resp.text}")
            
            copro_id = copro_resp.json()["id"]
            print(f"Created copropriete: {copro_id}")
            
            # Set copropriete header for subsequent requests
            syndic_session.headers.update({"X-Copropriete-Id": copro_id})
            
            # Step 4: Create test data
            # Create an owner
            owner_resp = syndic_session.post(f"{BASE_URL}/api/owners", json={
                "name": "Test Owner Purge",
                "email": f"owner_purge_{uuid.uuid4().hex[:8]}@test.com"
            })
            owner_id = None
            if owner_resp.status_code in [200, 201]:
                owner_id = owner_resp.json().get("id")
                print(f"Created owner: {owner_id}")
            
            # Create a lot
            lot_resp = syndic_session.post(f"{BASE_URL}/api/lots", json={
                "number": f"LOT-{uuid.uuid4().hex[:6]}",
                "description": "Test Lot for Purge",
                "quotites": {"general": 100}
            })
            lot_id = None
            if lot_resp.status_code in [200, 201]:
                lot_id = lot_resp.json().get("id")
                print(f"Created lot: {lot_id}")
            
            # Create a supplier
            supplier_resp = syndic_session.post(f"{BASE_URL}/api/suppliers", json={
                "name": f"Test Supplier Purge {uuid.uuid4().hex[:8]}",
                "bce": f"BE{uuid.uuid4().hex[:10]}"
            })
            supplier_id = None
            if supplier_resp.status_code in [200, 201]:
                supplier_id = supplier_resp.json().get("id")
                print(f"Created supplier: {supplier_id}")
            
            # Create an invoice
            invoice_resp = syndic_session.post(f"{BASE_URL}/api/invoices", json={
                "supplier": "Test Supplier Purge",
                "date": "2025-01-15",
                "total_amount": 100.00,
                "description": "Test Invoice for Purge"
            })
            invoice_id = None
            if invoice_resp.status_code in [200, 201]:
                invoice_id = invoice_resp.json().get("id")
                print(f"Created invoice: {invoice_id}")
            
            # Step 5: Verify syndic has copropriete_ids
            users_resp = admin_session.get(f"{BASE_URL}/api/admin/users")
            if users_resp.status_code == 200:
                users = users_resp.json()
                syndic_user = next((u for u in users if u["id"] == syndic_id), None)
                if syndic_user:
                    assert len(syndic_user.get("copropriete_ids", [])) > 0, "Syndic should have copropriete_ids"
                    print(f"Syndic has {len(syndic_user['copropriete_ids'])} ACP(s)")
            
            # Step 6: Purge the syndic data
            purge_resp = admin_session.delete(
                f"{BASE_URL}/api/admin/syndic/{syndic_id}/purge-data",
                json={"confirm_email": test_email}
            )
            
            assert purge_resp.status_code == 200, f"Purge failed: {purge_resp.status_code} - {purge_resp.text}"
            purge_data = purge_resp.json()
            
            print(f"Purge response: {purge_data}")
            
            # Verify purge response structure
            assert "message" in purge_data, "Response should contain 'message'"
            assert "deleted" in purge_data, "Response should contain 'deleted'"
            assert "copropriete_ids_purged" in purge_data, "Response should contain 'copropriete_ids_purged'"
            
            # Verify copropriete was in the purged list
            assert copro_id in purge_data["copropriete_ids_purged"], "Created copropriete should be in purged list"
            
            # Verify some data was deleted
            deleted = purge_data["deleted"]
            assert "coproprietes" in deleted, "Should report deleted coproprietes"
            assert deleted["coproprietes"] >= 1, "At least 1 copropriete should be deleted"
            
            print(f"PASS: Full purge flow completed successfully")
            print(f"Deleted counts: {deleted}")
            
            # Step 7: Verify data is actually deleted
            # Try to get the copropriete - should fail
            verify_copro = syndic_session.get(f"{BASE_URL}/api/coproprietes/{copro_id}")
            assert verify_copro.status_code in [404, 403, 401], f"Copropriete should be deleted, got {verify_copro.status_code}"
            
            # Verify syndic's copropriete_ids is now empty
            users_resp = admin_session.get(f"{BASE_URL}/api/admin/users")
            if users_resp.status_code == 200:
                users = users_resp.json()
                syndic_user = next((u for u in users if u["id"] == syndic_id), None)
                if syndic_user:
                    assert len(syndic_user.get("copropriete_ids", [])) == 0, "Syndic copropriete_ids should be empty after purge"
                    print(f"Verified: Syndic copropriete_ids is now empty")
            
            print(f"PASS: Verified all data was deleted")
            
        finally:
            # Cleanup: delete the test syndic
            admin_session.delete(f"{BASE_URL}/api/admin/users/{syndic_id}")
            print(f"Cleaned up test syndic")


class TestPurgeWithoutBody:
    """Test purge endpoint behavior without proper body"""
    
    def test_purge_without_confirm_email(self, admin_session):
        """DELETE /api/admin/syndic/{user_id}/purge-data returns 400 without confirm_email"""
        # Create a test syndic
        test_email = f"test_purge_nobody_{uuid.uuid4().hex[:8]}@test.com"
        
        create_resp = admin_session.post(f"{BASE_URL}/api/admin/users", json={
            "email": test_email,
            "name": "Test Syndic No Body",
            "role": "syndic",
            "password": "testpass123"
        })
        
        if create_resp.status_code != 200:
            pytest.skip(f"Could not create test syndic: {create_resp.text}")
        
        syndic_id = create_resp.json()["id"]
        
        try:
            # Try to purge without body
            resp = admin_session.delete(
                f"{BASE_URL}/api/admin/syndic/{syndic_id}/purge-data",
                json={}  # Empty body
            )
            
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            print(f"PASS: Returns 400 when confirm_email is missing")
        finally:
            # Cleanup
            admin_session.delete(f"{BASE_URL}/api/admin/users/{syndic_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
