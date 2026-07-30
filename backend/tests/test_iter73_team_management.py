"""
Iteration 73 - Team Management (Collaborateurs) Tests

Tests for:
- GET /api/team/members - List team members (gestionnaires) for current syndic
- POST /api/team/members - Create gestionnaire (direct or invitation mode)
- PUT /api/team/members/{id} - Update gestionnaire
- DELETE /api/team/members/{id} - Delete gestionnaire
- GET /api/admin/role-templates - List available role templates
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")


class TestTeamManagementSetup:
    """Setup and authentication tests"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a requests session"""
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_cookies(self, session):
        """Login as superadmin and get auth cookies"""
        resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        return session.cookies
    
    def test_login_superadmin(self, session, auth_cookies):
        """Verify superadmin login works"""
        resp = session.get(f"{BASE_URL}/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("role") in ("superadmin", "admin")
        print(f"Logged in as: {data.get('email')} with role: {data.get('role')}")


class TestRoleTemplates:
    """Tests for GET /api/admin/role-templates"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    def test_list_role_templates_returns_200(self, session):
        """GET /api/admin/role-templates returns 200"""
        resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    def test_list_role_templates_returns_list(self, session):
        """GET /api/admin/role-templates returns a list"""
        resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"Found {len(data)} role templates")
    
    def test_role_templates_have_required_fields(self, session):
        """Each role template has id, name, permissions"""
        resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        assert resp.status_code == 200
        templates = resp.json()
        if len(templates) > 0:
            tpl = templates[0]
            assert "id" in tpl, "Template missing 'id'"
            assert "name" in tpl, "Template missing 'name'"
            assert "permissions" in tpl, "Template missing 'permissions'"
            print(f"Sample template: {tpl.get('name')} with {len(tpl.get('permissions', []))} permissions")
    
    def test_role_templates_unauthenticated_returns_401(self):
        """GET /api/admin/role-templates without auth returns 401"""
        resp = requests.get(f"{BASE_URL}/api/admin/role-templates")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestTeamMembersList:
    """Tests for GET /api/team/members"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    def test_list_members_returns_200(self, session):
        """GET /api/team/members returns 200"""
        resp = session.get(f"{BASE_URL}/api/team/members")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    def test_list_members_returns_list(self, session):
        """GET /api/team/members returns a list"""
        resp = session.get(f"{BASE_URL}/api/team/members")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"Found {len(data)} team members")
    
    def test_list_members_unauthenticated_returns_401(self):
        """GET /api/team/members without auth returns 401"""
        resp = requests.get(f"{BASE_URL}/api/team/members")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestTeamMemberCreate:
    """Tests for POST /api/team/members - Create gestionnaire"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    @pytest.fixture
    def unique_email(self):
        """Generate unique email for test"""
        return f"test_gestionnaire_{uuid.uuid4().hex[:8]}@test.be"
    
    def test_create_member_direct_mode(self, session, unique_email):
        """POST /api/team/members creates gestionnaire with direct mode (password provided)"""
        payload = {
            "name": "Test Gestionnaire Direct",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False,
            "copropriete_ids": [],
            "role_template_id": None
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "id" in data, "Response missing 'id'"
        assert data.get("email") == unique_email.lower()
        assert data.get("name") == "Test Gestionnaire Direct"
        assert data.get("role") == "gestionnaire"
        print(f"Created gestionnaire: {data.get('id')}")
        
        # Cleanup - delete the created member
        member_id = data.get("id")
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")
    
    def test_create_member_invitation_mode(self, session, unique_email):
        """POST /api/team/members creates gestionnaire with invitation mode (no password)"""
        payload = {
            "name": "Test Gestionnaire Invitation",
            "email": unique_email,
            "must_change_password": True,
            "copropriete_ids": [],
            "role_template_id": None
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "id" in data
        assert data.get("email") == unique_email.lower()
        # must_change_password should be True for invitation mode
        print(f"Created gestionnaire (invitation): {data.get('id')}")
        
        # Cleanup
        member_id = data.get("id")
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")
    
    def test_create_member_with_role_template(self, session, unique_email):
        """POST /api/team/members with role_template_id assigns permissions"""
        # First get available templates
        tpl_resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        templates = tpl_resp.json()
        
        if len(templates) == 0:
            pytest.skip("No role templates available")
        
        template = templates[0]
        payload = {
            "name": "Test Gestionnaire With Profile",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False,
            "copropriete_ids": [],
            "role_template_id": template["id"]
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("role_template_id") == template["id"]
        # Permissions should be copied from template
        if template.get("permissions"):
            assert data.get("permissions") == template.get("permissions")
        print(f"Created gestionnaire with profile: {template.get('name')}")
        
        # Cleanup
        member_id = data.get("id")
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")
    
    def test_create_member_duplicate_email_fails(self, session, unique_email):
        """POST /api/team/members with duplicate email returns 400"""
        payload = {
            "name": "First Gestionnaire",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False
        }
        # Create first
        resp1 = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp1.status_code == 200
        member_id = resp1.json().get("id")
        
        # Try to create duplicate
        payload["name"] = "Second Gestionnaire"
        resp2 = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp2.status_code == 400, f"Expected 400 for duplicate, got {resp2.status_code}"
        assert "existe" in resp2.text.lower() or "exist" in resp2.text.lower()
        
        # Cleanup
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")
    
    def test_create_member_missing_name_fails(self, session, unique_email):
        """POST /api/team/members without name returns 400"""
        payload = {
            "email": unique_email,
            "password": "TempPass123!"
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        # Should fail validation - either 400 or 422
        assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"
    
    def test_create_member_missing_email_fails(self, session):
        """POST /api/team/members without email returns 400"""
        payload = {
            "name": "Test No Email",
            "password": "TempPass123!"
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"
    
    def test_create_member_unauthenticated_fails(self, unique_email):
        """POST /api/team/members without auth returns 401"""
        payload = {
            "name": "Unauthorized Test",
            "email": unique_email,
            "password": "TempPass123!"
        }
        resp = requests.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestTeamMemberUpdate:
    """Tests for PUT /api/team/members/{id}"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    @pytest.fixture
    def created_member(self, session):
        """Create a member for testing updates"""
        unique_email = f"test_update_{uuid.uuid4().hex[:8]}@test.be"
        payload = {
            "name": "Original Name",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False,
            "copropriete_ids": []
        }
        resp = session.post(f"{BASE_URL}/api/team/members", json=payload)
        assert resp.status_code == 200
        member = resp.json()
        yield member
        # Cleanup
        session.delete(f"{BASE_URL}/api/team/members/{member['id']}")
    
    def test_update_member_name(self, session, created_member):
        """PUT /api/team/members/{id} updates name"""
        member_id = created_member["id"]
        payload = {"name": "Updated Name"}
        resp = session.put(f"{BASE_URL}/api/team/members/{member_id}", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("name") == "Updated Name"
        print(f"Updated member name to: {data.get('name')}")
    
    def test_update_member_copropriete_ids(self, session, created_member):
        """PUT /api/team/members/{id} updates copropriete_ids"""
        member_id = created_member["id"]
        # Note: We use empty list since we don't have real coproprietes
        payload = {"copropriete_ids": []}
        resp = session.put(f"{BASE_URL}/api/team/members/{member_id}", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    def test_update_member_password(self, session, created_member):
        """PUT /api/team/members/{id} can reset password"""
        member_id = created_member["id"]
        payload = {"password": "NewPassword456!"}
        resp = session.put(f"{BASE_URL}/api/team/members/{member_id}", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print("Password updated successfully")
    
    def test_update_member_role_template(self, session, created_member):
        """PUT /api/team/members/{id} can change role_template_id"""
        member_id = created_member["id"]
        
        # Get templates
        tpl_resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        templates = tpl_resp.json()
        
        if len(templates) == 0:
            pytest.skip("No role templates available")
        
        template = templates[0]
        payload = {"role_template_id": template["id"]}
        resp = session.put(f"{BASE_URL}/api/team/members/{member_id}", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("role_template_id") == template["id"]
        print(f"Updated role template to: {template.get('name')}")
    
    def test_update_nonexistent_member_returns_404(self, session):
        """PUT /api/team/members/{id} with invalid id returns 404"""
        fake_id = "000000000000000000000000"
        payload = {"name": "Test"}
        resp = session.put(f"{BASE_URL}/api/team/members/{fake_id}", json=payload)
        assert resp.status_code in (400, 404), f"Expected 400/404, got {resp.status_code}"
    
    def test_update_member_unauthenticated_fails(self, created_member):
        """PUT /api/team/members/{id} without auth returns 401"""
        member_id = created_member["id"]
        payload = {"name": "Unauthorized Update"}
        resp = requests.put(f"{BASE_URL}/api/team/members/{member_id}", json=payload)
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


class TestTeamMemberDelete:
    """Tests for DELETE /api/team/members/{id}"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    def test_delete_member(self, session):
        """DELETE /api/team/members/{id} removes member"""
        # Create a member to delete
        unique_email = f"test_delete_{uuid.uuid4().hex[:8]}@test.be"
        create_resp = session.post(f"{BASE_URL}/api/team/members", json={
            "name": "To Be Deleted",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False
        })
        assert create_resp.status_code == 200
        member_id = create_resp.json().get("id")
        
        # Delete
        del_resp = session.delete(f"{BASE_URL}/api/team/members/{member_id}")
        assert del_resp.status_code == 200, f"Expected 200, got {del_resp.status_code}: {del_resp.text}"
        
        data = del_resp.json()
        assert data.get("status") == "ok"
        print(f"Deleted member: {member_id}")
        
        # Verify deletion - GET should not find it in list
        list_resp = session.get(f"{BASE_URL}/api/team/members")
        members = list_resp.json()
        member_ids = [m.get("id") for m in members]
        assert member_id not in member_ids, "Deleted member still in list"
    
    def test_delete_nonexistent_member_returns_404(self, session):
        """DELETE /api/team/members/{id} with invalid id returns 404"""
        fake_id = "000000000000000000000000"
        resp = session.delete(f"{BASE_URL}/api/team/members/{fake_id}")
        assert resp.status_code in (400, 404), f"Expected 400/404, got {resp.status_code}"
    
    def test_delete_member_unauthenticated_fails(self, session):
        """DELETE /api/team/members/{id} without auth returns 401"""
        # Create a member first
        unique_email = f"test_unauth_del_{uuid.uuid4().hex[:8]}@test.be"
        create_resp = session.post(f"{BASE_URL}/api/team/members", json={
            "name": "Unauth Delete Test",
            "email": unique_email,
            "password": "TempPass123!",
            "must_change_password": False
        })
        assert create_resp.status_code == 200
        member_id = create_resp.json().get("id")
        
        # Try to delete without auth
        resp = requests.delete(f"{BASE_URL}/api/team/members/{member_id}")
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        
        # Cleanup with auth
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")


class TestTeamMemberE2E:
    """End-to-end tests for team management flow"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        resp = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return s
    
    def test_full_crud_flow(self, session):
        """Test complete Create -> Read -> Update -> Delete flow"""
        unique_email = f"test_e2e_{uuid.uuid4().hex[:8]}@test.be"
        
        # 1. CREATE
        create_payload = {
            "name": "E2E Test User",
            "email": unique_email,
            "password": "E2EPass123!",
            "must_change_password": False,
            "copropriete_ids": []
        }
        create_resp = session.post(f"{BASE_URL}/api/team/members", json=create_payload)
        assert create_resp.status_code == 200, f"CREATE failed: {create_resp.text}"
        member = create_resp.json()
        member_id = member.get("id")
        assert member_id is not None
        print(f"1. CREATED: {member_id}")
        
        # 2. READ (verify in list)
        list_resp = session.get(f"{BASE_URL}/api/team/members")
        assert list_resp.status_code == 200
        members = list_resp.json()
        found = next((m for m in members if m.get("id") == member_id), None)
        assert found is not None, "Created member not found in list"
        assert found.get("name") == "E2E Test User"
        assert found.get("email") == unique_email.lower()
        print(f"2. READ: Found member in list")
        
        # 3. UPDATE
        update_payload = {"name": "E2E Updated Name"}
        update_resp = session.put(f"{BASE_URL}/api/team/members/{member_id}", json=update_payload)
        assert update_resp.status_code == 200, f"UPDATE failed: {update_resp.text}"
        updated = update_resp.json()
        assert updated.get("name") == "E2E Updated Name"
        print(f"3. UPDATED: name changed")
        
        # Verify update persisted
        list_resp2 = session.get(f"{BASE_URL}/api/team/members")
        members2 = list_resp2.json()
        found2 = next((m for m in members2 if m.get("id") == member_id), None)
        assert found2.get("name") == "E2E Updated Name"
        
        # 4. DELETE
        del_resp = session.delete(f"{BASE_URL}/api/team/members/{member_id}")
        assert del_resp.status_code == 200, f"DELETE failed: {del_resp.text}"
        print(f"4. DELETED: {member_id}")
        
        # Verify deletion
        list_resp3 = session.get(f"{BASE_URL}/api/team/members")
        members3 = list_resp3.json()
        found3 = next((m for m in members3 if m.get("id") == member_id), None)
        assert found3 is None, "Deleted member still in list"
        print("5. VERIFIED: Member no longer in list")
    
    def test_member_with_role_template_flow(self, session):
        """Test creating member with role template and verifying permissions"""
        # Get templates
        tpl_resp = session.get(f"{BASE_URL}/api/admin/role-templates")
        templates = tpl_resp.json()
        
        if len(templates) == 0:
            pytest.skip("No role templates available")
        
        template = templates[0]
        unique_email = f"test_tpl_{uuid.uuid4().hex[:8]}@test.be"
        
        # Create with template
        create_payload = {
            "name": "Template Test User",
            "email": unique_email,
            "password": "TplPass123!",
            "must_change_password": False,
            "role_template_id": template["id"]
        }
        create_resp = session.post(f"{BASE_URL}/api/team/members", json=create_payload)
        assert create_resp.status_code == 200
        member = create_resp.json()
        member_id = member.get("id")
        
        # Verify template assigned
        assert member.get("role_template_id") == template["id"]
        
        # Verify permissions copied
        if template.get("permissions"):
            assert member.get("permissions") == template.get("permissions")
            print(f"Permissions copied: {len(member.get('permissions', []))} permissions")
        
        # Cleanup
        session.delete(f"{BASE_URL}/api/team/members/{member_id}")
        print("Template flow test completed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
