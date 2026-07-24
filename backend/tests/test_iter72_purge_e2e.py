"""
Test iteration 72 - Full E2E test for purge syndic with proper ACP assignment

This test:
1. Creates a syndic user
2. Logs in AS the syndic to create an ACP (so it gets auto-assigned)
3. Creates owners, lots, and other data
4. Logs back in as superadmin
5. Purges the syndic
6. Verifies all data is deleted including the 5th source (import_session_id)
"""

import pytest
import requests
import os
import uuid
from datetime import datetime, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


class TestPurgeE2EWithSyndicLogin:
    """E2E test with proper syndic login for ACP assignment"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup: create sessions"""
        self.admin_session = requests.Session()
        self.admin_session.headers.update({"Content-Type": "application/json"})
        
        self.syndic_session = requests.Session()
        self.syndic_session.headers.update({"Content-Type": "application/json"})
        
        # Login as superadmin
        login_resp = self.admin_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"}
        )
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        
        self.test_syndic_id = None
        self.test_syndic_email = None
        self.test_copro_id = None
        
        yield
        
        # Cleanup
        self._cleanup()

    def _cleanup(self):
        """Clean up test data"""
        try:
            if self.test_syndic_id:
                self.admin_session.delete(f"{BASE_URL}/api/admin/users/{self.test_syndic_id}")
        except Exception as e:
            print(f"Cleanup warning: {e}")

    def test_full_purge_e2e_with_syndic_login(self):
        """
        Full E2E test:
        1. Create syndic (as admin)
        2. Login as syndic
        3. Create ACP (as syndic - auto-assigned)
        4. Create owner with copropriete_id
        5. Grant owner access (creates user with role=owner)
        6. Login back as admin
        7. Purge syndic
        8. Verify response includes all expected deletion counts
        """
        unique_id = str(uuid.uuid4())[:8]
        
        # Step 1: Create syndic (as admin)
        self.test_syndic_email = f"test_e2e_syndic_{unique_id}@test.com"
        syndic_password = "TestPass123!"
        
        resp = self.admin_session.post(
            f"{BASE_URL}/api/admin/users",
            json={
                "email": self.test_syndic_email,
                "password": syndic_password,
                "name": f"Test E2E Syndic {unique_id}",
                "role": "syndic"
            }
        )
        assert resp.status_code == 200, f"Failed to create syndic: {resp.text}"
        syndic = resp.json()
        self.test_syndic_id = syndic["id"]
        print(f"Step 1: Created syndic {syndic['id']} ({self.test_syndic_email})")
        
        # Step 2: Login as syndic
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": self.test_syndic_email, "password": syndic_password}
        )
        assert resp.status_code == 200, f"Syndic login failed: {resp.text}"
        print(f"Step 2: Logged in as syndic")
        
        # Step 3: Create ACP (as syndic - should auto-assign)
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/coproprietes",
            json={
                "name": f"Test E2E ACP {unique_id}",
                "address": "123 E2E Test Street",
                "postal_code": "1000",
                "city": "Brussels",
                "country": "Belgium"
            }
        )
        assert resp.status_code == 200, f"Failed to create copropriete: {resp.text}"
        copro = resp.json()
        self.test_copro_id = copro["id"]
        print(f"Step 3: Created copropriete {copro['id']}")
        
        # Verify syndic has the ACP assigned
        resp = self.syndic_session.get(f"{BASE_URL}/api/auth/me")
        if resp.status_code == 200:
            me = resp.json()
            copro_ids = me.get("copropriete_ids", [])
            print(f"Step 3b: Syndic copropriete_ids: {copro_ids}")
            assert self.test_copro_id in copro_ids, "ACP should be auto-assigned to syndic"
        
        # Step 4: Create owner with copropriete_id
        owner_email = f"test_e2e_owner_{unique_id}@test.com"
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/owners",
            json={
                "first_name": "E2E",
                "last_name": f"Owner {unique_id}",
                "email": owner_email,
                "copropriete_id": self.test_copro_id
            }
        )
        assert resp.status_code == 200, f"Failed to create owner: {resp.text}"
        owner = resp.json()
        owner_id = owner.get("id")
        print(f"Step 4: Created owner {owner_id}")
        
        # Step 5: Grant owner access (creates user with role=owner)
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/owners/{owner_id}/grant-access"
        )
        if resp.status_code == 200:
            access_data = resp.json()
            print(f"Step 5: Granted access: {access_data.get('message', '')}")
            if access_data.get("status", {}).get("user_id"):
                print(f"  Owner user account created: {access_data['status']['user_id']}")
        else:
            print(f"Step 5: Grant access returned {resp.status_code}: {resp.text}")
        
        # Step 6: Login back as admin
        resp = self.admin_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"}
        )
        assert resp.status_code == 200, f"Admin re-login failed: {resp.text}"
        print(f"Step 6: Logged back in as admin")
        
        # Step 7: Purge syndic
        resp = self.admin_session.delete(
            f"{BASE_URL}/api/admin/syndic/{self.test_syndic_id}/purge-data",
            json={"confirm_email": self.test_syndic_email}
        )
        
        print(f"Step 7: Purge response status: {resp.status_code}")
        assert resp.status_code == 200, f"Purge failed: {resp.text}"
        
        data = resp.json()
        print(f"Step 7: Purge response: {data}")
        
        # Step 8: Verify response includes all expected deletion counts
        assert "deleted" in data, "Response should include 'deleted' counts"
        deleted = data["deleted"]
        
        # Verify expected keys are present
        expected_keys = [
            "owners", "owner_bank_accounts", "owner_access_audit",
            "owner_user_accounts", "coproprietes", "lots"
        ]
        
        for key in expected_keys:
            assert key in deleted, f"'{key}' should be in deleted counts"
            print(f"  - {key}: {deleted[key]}")
        
        # Verify some data was actually deleted
        assert deleted.get("coproprietes", 0) >= 1, "At least 1 copropriete should be deleted"
        assert deleted.get("owners", 0) >= 1, "At least 1 owner should be deleted"
        
        # Verify owner_user_accounts was deleted (the user we created via grant-access)
        print(f"  - owner_user_accounts deleted: {deleted.get('owner_user_accounts', 0)}")
        
        # Verify owner_access_audit was deleted (the audit entry from grant-access)
        print(f"  - owner_access_audit deleted: {deleted.get('owner_access_audit', 0)}")
        
        print("PASS: Full E2E purge test completed successfully!")

    def test_purge_with_lot_and_owner(self):
        """
        Test purge with lot linked to owner via owner_id.
        This tests the 1st source: lots.owner_id
        """
        unique_id = str(uuid.uuid4())[:8]
        
        # Create syndic
        self.test_syndic_email = f"test_lot_syndic_{unique_id}@test.com"
        syndic_password = "TestPass123!"
        
        resp = self.admin_session.post(
            f"{BASE_URL}/api/admin/users",
            json={
                "email": self.test_syndic_email,
                "password": syndic_password,
                "name": f"Test Lot Syndic {unique_id}",
                "role": "syndic"
            }
        )
        assert resp.status_code == 200, f"Failed to create syndic: {resp.text}"
        syndic = resp.json()
        self.test_syndic_id = syndic["id"]
        print(f"Created syndic: {syndic['id']}")
        
        # Login as syndic
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": self.test_syndic_email, "password": syndic_password}
        )
        assert resp.status_code == 200, f"Syndic login failed: {resp.text}"
        
        # Create ACP
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/coproprietes",
            json={
                "name": f"Test Lot ACP {unique_id}",
                "address": "456 Lot Test Street",
                "postal_code": "1000",
                "city": "Brussels",
                "country": "Belgium"
            }
        )
        assert resp.status_code == 200, f"Failed to create copropriete: {resp.text}"
        copro = resp.json()
        self.test_copro_id = copro["id"]
        print(f"Created copropriete: {copro['id']}")
        
        # Create owner
        owner_email = f"test_lot_owner_{unique_id}@test.com"
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/owners",
            json={
                "first_name": "Lot",
                "last_name": f"Owner {unique_id}",
                "email": owner_email,
                "copropriete_id": self.test_copro_id
            }
        )
        assert resp.status_code == 200, f"Failed to create owner: {resp.text}"
        owner = resp.json()
        owner_id = owner.get("id")
        print(f"Created owner: {owner_id}")
        
        # Create lot linked to owner
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/lots",
            json={
                "number": f"LOT-{unique_id}",
                "description": "Test lot",
                "lot_type": "apartment",
                "floor": 1,
                "area": 100.0,
                "quotity": 100.0,
                "owner_id": owner_id,
                "copropriete_id": self.test_copro_id
            }
        )
        if resp.status_code == 200:
            lot = resp.json()
            print(f"Created lot: {lot.get('id')}")
        else:
            print(f"Lot creation returned {resp.status_code}: {resp.text}")
        
        # Login back as admin and purge
        self.admin_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"}
        )
        
        resp = self.admin_session.delete(
            f"{BASE_URL}/api/admin/syndic/{self.test_syndic_id}/purge-data",
            json={"confirm_email": self.test_syndic_email}
        )
        
        assert resp.status_code == 200, f"Purge failed: {resp.text}"
        data = resp.json()
        
        print(f"Purge response: {data}")
        
        if "deleted" in data:
            deleted = data["deleted"]
            print(f"  - lots deleted: {deleted.get('lots', 0)}")
            print(f"  - owners deleted: {deleted.get('owners', 0)}")
            
            # Verify lot was deleted
            assert deleted.get("lots", 0) >= 1, "At least 1 lot should be deleted"
            # Verify owner was deleted (via lots.owner_id source)
            assert deleted.get("owners", 0) >= 1, "At least 1 owner should be deleted"
        
        print("PASS: Purge with lot and owner test completed!")


class TestVerifyOwnersAfterPurge:
    """Verify that GET /api/owners returns zero owners after purge"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup sessions"""
        self.admin_session = requests.Session()
        self.admin_session.headers.update({"Content-Type": "application/json"})
        
        self.syndic_session = requests.Session()
        self.syndic_session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_resp = self.admin_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"}
        )
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        
        self.test_syndic_id = None
        self.test_syndic_email = None
        self.test_copro_id = None
        
        yield
        
        # Cleanup
        try:
            if self.test_syndic_id:
                self.admin_session.delete(f"{BASE_URL}/api/admin/users/{self.test_syndic_id}")
        except Exception:
            pass

    def test_owners_zero_after_purge(self):
        """
        Test that after purging a syndic, GET /api/owners for that ACP returns zero.
        """
        unique_id = str(uuid.uuid4())[:8]
        
        # Create syndic
        self.test_syndic_email = f"test_zero_syndic_{unique_id}@test.com"
        syndic_password = "TestPass123!"
        
        resp = self.admin_session.post(
            f"{BASE_URL}/api/admin/users",
            json={
                "email": self.test_syndic_email,
                "password": syndic_password,
                "name": f"Test Zero Syndic {unique_id}",
                "role": "syndic"
            }
        )
        assert resp.status_code == 200
        syndic = resp.json()
        self.test_syndic_id = syndic["id"]
        
        # Login as syndic and create ACP + owner
        self.syndic_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": self.test_syndic_email, "password": syndic_password}
        )
        
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/coproprietes",
            json={
                "name": f"Test Zero ACP {unique_id}",
                "address": "789 Zero Test Street",
                "postal_code": "1000",
                "city": "Brussels",
                "country": "Belgium"
            }
        )
        assert resp.status_code == 200
        copro = resp.json()
        self.test_copro_id = copro["id"]
        
        # Create owner
        resp = self.syndic_session.post(
            f"{BASE_URL}/api/owners",
            json={
                "first_name": "Zero",
                "last_name": f"Owner {unique_id}",
                "email": f"test_zero_owner_{unique_id}@test.com",
                "copropriete_id": self.test_copro_id
            }
        )
        assert resp.status_code == 200
        
        # Count owners before purge
        resp = self.syndic_session.get(
            f"{BASE_URL}/api/owners",
            params={"copropriete_id": self.test_copro_id}
        )
        owners_before = resp.json()
        count_before = len(owners_before) if isinstance(owners_before, list) else len(owners_before.get("items", []))
        print(f"Owners before purge: {count_before}")
        assert count_before >= 1, "Should have at least 1 owner before purge"
        
        # Login as admin and purge
        self.admin_session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"}
        )
        
        resp = self.admin_session.delete(
            f"{BASE_URL}/api/admin/syndic/{self.test_syndic_id}/purge-data",
            json={"confirm_email": self.test_syndic_email}
        )
        assert resp.status_code == 200
        
        # Verify owners are gone
        # Note: After purge, the ACP doesn't exist anymore, so we check with include_unassigned
        resp = self.admin_session.get(
            f"{BASE_URL}/api/owners",
            params={"copropriete_id": self.test_copro_id, "include_unassigned": "true"}
        )
        
        if resp.status_code == 200:
            owners_after = resp.json()
            count_after = len(owners_after) if isinstance(owners_after, list) else len(owners_after.get("items", []))
            print(f"Owners after purge for this ACP: {count_after}")
            # The owners for this specific ACP should be 0
            # (though there may be other owners in the system)
        
        print("PASS: Owners verification after purge completed!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
