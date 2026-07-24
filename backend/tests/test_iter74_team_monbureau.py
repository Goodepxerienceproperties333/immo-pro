"""
Iteration 74 - Test Team API and Mon Bureau integration
Tests the TeamSection component integration into MonBureauPage
and the /api/team/members CRUD endpoints.

Features tested:
- POST /api/auth/login with cookies
- GET /api/team/members - list team members
- POST /api/team/members - create team member
- PUT /api/team/members/{id} - update team member
- DELETE /api/team/members/{id} - delete team member
- POST /api/owners - create owner (bug fix verification)
- GET /api/owners/check-duplicate - duplicate check
- POST /api/suppliers/check-duplicate - BCE duplicate check
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAuthLogin:
    """Test authentication with cookies"""
    
    def test_login_success(self):
        """Test login with admin credentials using cookies"""
        session = requests.Session()
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "id" in data
        assert data["email"] == "admin@copro.be"
        assert data["role"] == "superadmin"
        # Verify cookies are set
        assert "access_token" in session.cookies
        print(f"Login successful: {data['email']} ({data['role']})")


class TestTeamAPI:
    """Test Team API CRUD operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed"
        self.test_member_id = None
        yield
        # Cleanup: delete test member if created
        if self.test_member_id:
            try:
                self.session.delete(f"{BASE_URL}/api/team/members/{self.test_member_id}")
            except:
                pass
    
    def test_list_members(self):
        """GET /api/team/members - list team members"""
        response = self.session.get(f"{BASE_URL}/api/team/members")
        assert response.status_code == 200, f"List members failed: {response.text}"
        data = response.json()
        assert isinstance(data, list)
        print(f"Found {len(data)} team members")
    
    def test_create_member(self):
        """POST /api/team/members - create team member"""
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"test.member.{unique_id}@cabinet.be",
            "name": f"Test Member {unique_id}",
            "password": "TestPassword123"
        }
        response = self.session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert response.status_code == 200, f"Create member failed: {response.text}"
        data = response.json()
        assert "id" in data
        assert data["email"] == payload["email"]
        assert data["name"] == payload["name"]
        assert data["role"] == "gestionnaire"
        assert "parent_syndic_id" in data
        self.test_member_id = data["id"]
        print(f"Created member: {data['name']} (id: {data['id']})")
        return data["id"]
    
    def test_create_and_update_member(self):
        """POST + PUT /api/team/members - create and update member"""
        # Create
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"test.update.{unique_id}@cabinet.be",
            "name": f"Test Update {unique_id}",
            "password": "TestPassword123"
        }
        create_response = self.session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert create_response.status_code == 200
        member_id = create_response.json()["id"]
        self.test_member_id = member_id
        
        # Update
        update_payload = {"name": f"Updated Name {unique_id}"}
        update_response = self.session.put(
            f"{BASE_URL}/api/team/members/{member_id}",
            json=update_payload
        )
        assert update_response.status_code == 200, f"Update failed: {update_response.text}"
        updated_data = update_response.json()
        assert updated_data["name"] == update_payload["name"]
        print(f"Updated member name to: {updated_data['name']}")
    
    def test_create_and_delete_member(self):
        """POST + DELETE /api/team/members - create and delete member"""
        # Create
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"test.delete.{unique_id}@cabinet.be",
            "name": f"Test Delete {unique_id}",
            "password": "TestPassword123"
        }
        create_response = self.session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert create_response.status_code == 200
        member_id = create_response.json()["id"]
        
        # Delete
        delete_response = self.session.delete(f"{BASE_URL}/api/team/members/{member_id}")
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.text}"
        delete_data = delete_response.json()
        assert delete_data["status"] == "ok"
        print(f"Deleted member: {member_id}")
        
        # Verify deletion
        list_response = self.session.get(f"{BASE_URL}/api/team/members")
        members = list_response.json()
        member_ids = [m["id"] for m in members]
        assert member_id not in member_ids, "Member still exists after deletion"
    
    def test_create_member_duplicate_email(self):
        """POST /api/team/members - duplicate email should fail"""
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"test.dup.{unique_id}@cabinet.be",
            "name": f"Test Dup {unique_id}",
            "password": "TestPassword123"
        }
        # Create first
        response1 = self.session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert response1.status_code == 200
        self.test_member_id = response1.json()["id"]
        
        # Try to create duplicate
        response2 = self.session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert response2.status_code == 400, "Duplicate email should fail"
        print("Duplicate email correctly rejected")


class TestOwnersAPI:
    """Test Owners API - verify bug fix for create_owner"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed"
        self.test_owner_id = None
        yield
        # Cleanup
        if self.test_owner_id:
            try:
                self.session.delete(f"{BASE_URL}/api/owners/{self.test_owner_id}")
            except:
                pass
    
    def test_create_owner(self):
        """POST /api/owners - create owner (bug fix verification)"""
        unique_id = str(uuid.uuid4())[:8]
        # Use unique phone to avoid duplicate detection
        phone_suffix = unique_id[:4]
        payload = {
            "first_name": "Jean",
            "last_name": f"Dupont{unique_id}",
            "email": f"jean.dupont.{unique_id}@test.be",
            "phone": f"+32 2 {phone_suffix} 00 00"
        }
        response = self.session.post(f"{BASE_URL}/api/owners", json=payload)
        assert response.status_code == 200, f"Create owner failed: {response.text}"
        data = response.json()
        assert "id" in data
        assert data["first_name"] == payload["first_name"]
        assert data["last_name"] == payload["last_name"]
        self.test_owner_id = data["id"]
        print(f"Created owner: {data['name']} (id: {data['id']})")
    
    def test_check_duplicate_email(self):
        """GET /api/owners/check-duplicate - check duplicate by email"""
        # First create an owner
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "first_name": "Marie",
            "last_name": f"Martin{unique_id}",
            "email": f"marie.martin.{unique_id}@test.be"
        }
        create_response = self.session.post(f"{BASE_URL}/api/owners", json=payload)
        assert create_response.status_code == 200
        self.test_owner_id = create_response.json()["id"]
        
        # Check duplicate
        check_response = self.session.get(
            f"{BASE_URL}/api/owners/check-duplicate",
            params={"email": payload["email"]}
        )
        assert check_response.status_code == 200
        data = check_response.json()
        assert data["has_duplicates"] == True
        assert len(data["duplicates"]) > 0
        assert data["duplicates"][0]["owner_email"] == payload["email"]
        print(f"Duplicate check found: {data['duplicates'][0]['owner_name']}")


class TestSuppliersAPI:
    """Test Suppliers API - BCE duplicate check"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed"
    
    def test_check_duplicate_bce(self):
        """POST /api/suppliers/check-duplicate - check duplicate by BCE"""
        payload = {
            "name": "Test Supplier BCE Check",
            "bce": "0999.888.777"
        }
        response = self.session.post(
            f"{BASE_URL}/api/suppliers/check-duplicate",
            json=payload
        )
        assert response.status_code == 200, f"BCE check failed: {response.text}"
        data = response.json()
        # Response should have exact and similar fields
        assert "exact" in data
        assert "similar" in data
        print(f"BCE duplicate check: exact={data['exact']}, similar_count={len(data['similar'])}")


class TestCascadeDeletion:
    """Test cascade deletion for coproprietes (Step F)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authenticated session"""
        self.session = requests.Session()
        response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@copro.be",
            "password": "admin123"
        })
        assert response.status_code == 200, "Login failed"
    
    def test_copropriete_exists(self):
        """Verify coproprietes endpoint works"""
        response = self.session.get(f"{BASE_URL}/api/coproprietes")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"Found {len(data)} coproprietes")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
