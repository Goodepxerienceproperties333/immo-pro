"""Iter15 - Tier accounts auto-assignment + migration + balance-tiers enhancements."""
import os
import re
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")
PCMN_URL = f"{BASE_URL}/api/accounting/pcmn"

ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")
TEST_PREFIX = "TEST_iter15"

# Track created resources for cleanup
_created_owner_ids = []
_created_supplier_ids = []
_created_invoice_ids = []
_created_pcmn_numbers = []  # (copropriete_id, number)
_created_user_ids = []


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def acp_id(admin_client):
    r = admin_client.get(f"{BASE_URL}/api/coproprietes")
    assert r.status_code == 200
    coproprietes = r.json()
    assert len(coproprietes) > 0, "No ACP found"
    return coproprietes[0]["id"]


@pytest.fixture(scope="module", autouse=True)
def cleanup(admin_client):
    yield
    # Delete invoices
    for iid in _created_invoice_ids:
        try:
            admin_client.delete(f"{BASE_URL}/api/invoices/{iid}")
        except Exception:
            pass
    for sid in _created_supplier_ids:
        try:
            admin_client.delete(f"{BASE_URL}/api/suppliers/{sid}")
        except Exception:
            pass
    for oid in _created_owner_ids:
        try:
            admin_client.delete(f"{BASE_URL}/api/owners/{oid}")
        except Exception:
            pass
    for uid in _created_user_ids:
        try:
            admin_client.delete(f"{BASE_URL}/api/admin/users/{uid}")
        except Exception:
            pass


# -------- MIGRATION ENDPOINT --------
class TestMigration:
    def test_migrate_endpoint_returns_expected_shape(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/admin/migrate/tier-accounts")
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        data = r.json()
        assert "owners_processed" in data
        assert "suppliers_processed" in data
        assert "acps" in data
        assert isinstance(data["owners_processed"], int)
        assert isinstance(data["suppliers_processed"], int)
        assert isinstance(data["acps"], list)

    def test_migrate_is_idempotent(self, admin_client, acp_id):
        # Snapshot tier accounts before
        r1 = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        assert r1.status_code == 200
        before = [a for a in r1.json() if a.get("is_tier_account")]
        before_set = {a["number"] for a in before}

        # Re-run migration
        r2 = admin_client.post(f"{BASE_URL}/api/admin/migrate/tier-accounts")
        assert r2.status_code == 200

        # Snapshot after
        r3 = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        after = [a for a in r3.json() if a.get("is_tier_account")]
        after_set = {a["number"] for a in after}

        assert before_set == after_set, f"Migration is not idempotent. Diff: {after_set ^ before_set}"

    def test_migrated_accounts_have_correct_attributes(self, admin_client, acp_id):
        r = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        assert r.status_code == 200
        tier = [a for a in r.json() if a.get("is_tier_account")]
        assert len(tier) > 0, "Should have tier accounts after migration"
        for a in tier:
            assert a.get("type") == "balance", f"Account {a['number']} type != 'balance'"
            assert a.get("class_num") == 4, f"Account {a['number']} class_num != 4"
            assert a.get("copropriete_id") == acp_id
            assert a.get("is_tier_account") is True
            # Number format: prefix + 3 digits
            assert re.match(r"^(40000|40010|44000)\d{3}$", a["number"]), f"Bad number format: {a['number']}"


# -------- OWNER CREATION WITH TIER ACCOUNTS --------
class TestOwnerTierAccounts:
    def test_create_owner_auto_assigns_tier_accounts(self, admin_client, acp_id):
        payload = {
            "first_name": "Alice",
            "last_name": f"{TEST_PREFIX}_OwnerA",
            "email": f"{TEST_PREFIX}_ownerA_{uuid.uuid4().hex[:6]}@example.com",
            "copropriete_id": acp_id,
        }
        r = admin_client.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code == 200, f"Create owner failed: {r.status_code} {r.text}"
        owner = r.json()
        _created_owner_ids.append(owner["id"])

        # Verify tier_accounts populated
        ta = owner.get("tier_accounts", {})
        assert acp_id in ta, f"tier_accounts missing acp key: {ta}"
        assert "provisions" in ta[acp_id]
        assert "reserve" in ta[acp_id]
        prov = ta[acp_id]["provisions"]
        res = ta[acp_id]["reserve"]
        assert re.match(r"^40000\d{3}$", prov), f"Bad provisions number: {prov}"
        assert re.match(r"^40010\d{3}$", res), f"Bad reserve number: {res}"

        # Verify accounts exist in PCMN
        r2 = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        nums = {a["number"]: a for a in r2.json()}
        assert prov in nums
        assert res in nums
        assert nums[prov]["is_tier_account"] is True
        assert nums[prov]["class_num"] == 4
        assert nums[prov]["type"] == "balance"
        assert nums[res]["is_tier_account"] is True

    def test_owner_assign_is_idempotent(self, admin_client, acp_id):
        """Re-calling tier assignment must NOT create a duplicate account."""
        payload = {
            "first_name": "Bob",
            "last_name": f"{TEST_PREFIX}_OwnerB",
            "email": f"{TEST_PREFIX}_ownerB_{uuid.uuid4().hex[:6]}@example.com",
            "copropriete_id": acp_id,
        }
        r = admin_client.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code == 200
        owner = r.json()
        _created_owner_ids.append(owner["id"])
        prov1 = owner["tier_accounts"][acp_id]["provisions"]
        res1 = owner["tier_accounts"][acp_id]["reserve"]

        # Count tier accounts before update
        before = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}").json()
        before_count = len([a for a in before if a.get("is_tier_account")])

        # Now update owner with same copropriete_id (re-triggers assign_owner_accounts via migration)
        r2 = admin_client.post(f"{BASE_URL}/api/admin/migrate/tier-accounts")
        assert r2.status_code == 200

        # Re-fetch owner and pcmn accounts
        r3 = admin_client.get(f"{BASE_URL}/api/owners?copropriete_id={acp_id}")
        owner2 = next((o for o in r3.json() if o["id"] == owner["id"]), None)
        assert owner2 is not None
        assert owner2["tier_accounts"][acp_id]["provisions"] == prov1
        assert owner2["tier_accounts"][acp_id]["reserve"] == res1

        after = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}").json()
        after_count = len([a for a in after if a.get("is_tier_account")])
        assert after_count == before_count, "Migration created duplicate tier accounts"


# -------- SUPPLIER CREATION WITH TIER ACCOUNT --------
class TestSupplierTierAccounts:
    def test_create_supplier_auto_assigns_tier_account(self, admin_client, acp_id):
        name = f"{TEST_PREFIX}_SupA_{uuid.uuid4().hex[:6]}"
        payload = {"name": name, "copropriete_id": acp_id}
        r = admin_client.post(f"{BASE_URL}/api/suppliers", json=payload)
        assert r.status_code == 200, f"Create supplier failed: {r.text}"
        supplier = r.json()
        _created_supplier_ids.append(supplier["id"])

        ta = supplier.get("tier_accounts", {})
        assert acp_id in ta, f"tier_accounts missing acp key: {ta}"
        main = ta[acp_id]["main"]
        assert re.match(r"^44000\d{3}$", main), f"Bad supplier account number: {main}"

        # Verify in PCMN
        r2 = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        nums = {a["number"]: a for a in r2.json()}
        assert main in nums
        assert nums[main]["is_tier_account"] is True
        assert nums[main]["class_num"] == 4
        assert nums[main]["type"] == "balance"

    def test_supplier_sequence_no_gap(self, admin_client, acp_id):
        """Sequence numbers must be allocated densely (no skips when no gaps)."""
        r = admin_client.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={acp_id}")
        nums = [a["number"] for a in r.json() if a.get("is_tier_account") and a["number"].startswith("44000")]
        seqs = sorted(int(n[5:]) for n in nums)
        assert seqs == list(range(1, len(seqs) + 1)), f"Sequence has gaps: {seqs}"


# -------- BALANCE TIERS OWNERS --------
class TestBalanceTiersOwners:
    def test_balance_tiers_owners_includes_tier_accounts(self, admin_client, acp_id):
        r = admin_client.get(f"{BASE_URL}/api/reports/balance-tiers/owners?copropriete_id={acp_id}")
        assert r.status_code == 200
        data = r.json()
        assert "owners" in data
        # Find at least one owner with both tier accounts populated
        owners_with_acc = [o for o in data["owners"] if o.get("account_provisions") and o.get("account_reserve")]
        assert len(owners_with_acc) > 0, "Should have at least one owner with tier accounts"
        for o in owners_with_acc:
            assert re.match(r"^40000\d{3}$", o["account_provisions"]), o["account_provisions"]
            assert re.match(r"^40010\d{3}$", o["account_reserve"]), o["account_reserve"]

    def test_balance_tiers_owners_response_fields(self, admin_client, acp_id):
        r = admin_client.get(f"{BASE_URL}/api/reports/balance-tiers/owners?copropriete_id={acp_id}")
        assert r.status_code == 200
        data = r.json()
        if data["owners"]:
            o = data["owners"][0]
            # account_provisions and account_reserve must be present (can be '')
            assert "account_provisions" in o
            assert "account_reserve" in o
            assert "owner_name" in o
            assert "balance" in o


# -------- BALANCE TIERS SUPPLIERS (orphan + case insensitive) --------
class TestBalanceTiersSuppliers:
    def test_orphan_supplier_appears_with_orphan_flag(self, admin_client, acp_id):
        """Create invoice with supplier name that has NO Supplier doc -> orphan=True."""
        orphan_name = f"{TEST_PREFIX}_Orphan_{uuid.uuid4().hex[:6]}"
        inv_payload = {
            "number": f"INV-{uuid.uuid4().hex[:8]}",
            "date": "2026-01-15",
            "supplier": orphan_name,
            "total_amount": 123.45,
            "description": "Test orphan invoice",
            "copropriete_id": acp_id,
        }
        r = admin_client.post(f"{BASE_URL}/api/invoices", json=inv_payload)
        assert r.status_code == 200, f"Create invoice failed: {r.status_code} {r.text}"
        inv = r.json()
        _created_invoice_ids.append(inv["id"])

        r2 = admin_client.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers?copropriete_id={acp_id}")
        assert r2.status_code == 200
        data = r2.json()
        suppliers = data["suppliers"]
        # Find our orphan
        orphan = next((s for s in suppliers if s["supplier_name"].lower() == orphan_name.lower()), None)
        assert orphan is not None, f"Orphan supplier not in balance: names={[s['supplier_name'] for s in suppliers]}"
        assert orphan["orphan"] is True
        assert orphan["supplier_id"] == ""
        assert orphan["tier_account"] == ""
        assert orphan["invoice_count"] >= 1
        assert orphan["total_invoiced"] >= 123.45 - 0.01

    def test_case_insensitive_match_supplier(self, admin_client, acp_id):
        """Supplier 'VIVAQUA-XYZ' created with name UPPERCASE; invoice uses lowercase -> matches."""
        sup_name_upper = f"{TEST_PREFIX}_CASE_{uuid.uuid4().hex[:6].upper()}"
        sup_name_used = sup_name_upper.lower()  # use lowercase in invoice

        # Create supplier doc with UPPERCASE name
        r = admin_client.post(f"{BASE_URL}/api/suppliers", json={"name": sup_name_upper, "copropriete_id": acp_id})
        assert r.status_code == 200
        sup = r.json()
        _created_supplier_ids.append(sup["id"])
        tier_acc = sup["tier_accounts"][acp_id]["main"]

        # Create invoice with DIFFERENT CASE
        inv_payload = {
            "number": f"INV-{uuid.uuid4().hex[:8]}",
            "date": "2026-01-16",
            "supplier": sup_name_used,
            "total_amount": 50.00,
            "description": "Case match invoice",
            "copropriete_id": acp_id,
        }
        r2 = admin_client.post(f"{BASE_URL}/api/invoices", json=inv_payload)
        assert r2.status_code == 200
        _created_invoice_ids.append(r2.json()["id"])

        # Balance
        r3 = admin_client.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers?copropriete_id={acp_id}")
        data = r3.json()
        suppliers = data["suppliers"]
        match = next((s for s in suppliers if s["supplier_name"].lower() == sup_name_upper.lower()), None)
        assert match is not None, "Supplier not found"
        assert match["orphan"] is False, "Should NOT be orphan because supplier fiche exists"
        assert match["supplier_id"] == sup["id"]
        assert match["tier_account"] == tier_acc, f"Tier account mismatch: {match['tier_account']} vs {tier_acc}"
        assert match["invoice_count"] >= 1
        assert match["total_invoiced"] >= 50.00 - 0.01


# -------- RBAC --------
class TestRBAC:
    def test_owner_cannot_call_migrate(self, admin_client):
        # Create an owner user
        owner_email = f"{TEST_PREFIX}_rbac_{uuid.uuid4().hex[:6]}@example.com"
        r = admin_client.post(f"{BASE_URL}/api/admin/users", json={
            "email": owner_email,
            "password": "ownerpass123",
            "name": "RBAC Owner",
            "role": "owner",
        })
        assert r.status_code == 200, f"Cannot create owner user: {r.text}"
        _created_user_ids.append(r.json()["id"])

        # Login as owner
        owner_session = requests.Session()
        owner_session.headers.update({"Content-Type": "application/json"})
        rlogin = owner_session.post(f"{BASE_URL}/api/auth/login", json={"email": owner_email, "password": "ownerpass123"})
        assert rlogin.status_code == 200

        # Try migrate
        r2 = owner_session.post(f"{BASE_URL}/api/admin/migrate/tier-accounts")
        assert r2.status_code == 403, f"Expected 403 got {r2.status_code}: {r2.text}"


# -------- REGRESSION --------
class TestRegression:
    def test_owners_list(self, admin_client, acp_id):
        r = admin_client.get(f"{BASE_URL}/api/owners?copropriete_id={acp_id}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_suppliers_list(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/suppliers")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_balance_report(self, admin_client, acp_id):
        r = admin_client.get(f"{BASE_URL}/api/reports/balance?copropriete_id={acp_id}")
        assert r.status_code == 200

    def test_invoices_crud(self, admin_client, acp_id):
        inv_payload = {
            "number": f"INV-REG-{uuid.uuid4().hex[:8]}",
            "date": "2026-01-17",
            "supplier": f"{TEST_PREFIX}_regress",
            "total_amount": 99.99,
            "description": "Regression invoice",
            "copropriete_id": acp_id,
        }
        r = admin_client.post(f"{BASE_URL}/api/invoices", json=inv_payload)
        assert r.status_code == 200
        iid = r.json()["id"]
        _created_invoice_ids.append(iid)

        r2 = admin_client.get(f"{BASE_URL}/api/invoices/{iid}")
        assert r2.status_code == 200
        assert r2.json()["total_amount"] == 99.99

        r3 = admin_client.delete(f"{BASE_URL}/api/invoices/{iid}")
        assert r3.status_code == 200
        # Remove from cleanup list (already deleted)
        if iid in _created_invoice_ids:
            _created_invoice_ids.remove(iid)
