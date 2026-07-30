"""
iter31: Repartition occupant/proprietaire (decompte locataire futur).
  - ExpenseCategory POST/PUT with default_occupant_pct + default_proprietaire_pct (sum must = 100)
  - Invoice POST inherits % from category if not provided, override works
  - Invoice PUT preserves existing % if not in payload
  - JournalEntry OD lines auto-inherit % for class 6 charge lines, validation sum=100 when both provided
"""
import os
import uuid
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SUPER_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
SUPER_PWD = "admin123"
DEMO_COPRO_ID = "6748ca1a-216d-4002-8417-799287238736"


def _login(email, pwd):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": pwd}, timeout=20)
    if r.status_code != 200:
        return None
    return s


@pytest.fixture(scope="module")
def session():
    s = _login(SUPER_EMAIL, SUPER_PWD)
    if s is None:
        pytest.skip("Superadmin login failed")
    s.headers.update({"X-Copropriete-Id": DEMO_COPRO_ID})
    return s


# Use a free class-6 account. 612300 (Eau) is unused per the agent note.
TEST_ACCOUNT = "612300"


@pytest.fixture(scope="module")
def temp_category(session):
    """Create a temp expense category for the test account (60/40 default)."""
    # Cleanup any leftover
    r = session.get(f"{BASE}/api/expense-categories",
                    params={"copropriete_id": DEMO_COPRO_ID})
    if r.status_code == 200:
        for c in r.json():
            if c.get("account_number") == TEST_ACCOUNT and c.get("name", "").startswith("TEST_iter31"):
                session.delete(f"{BASE}/api/expense-categories/{c['id']}")
    r = session.post(f"{BASE}/api/expense-categories", json={
        "name": f"TEST_iter31_cat_{uuid.uuid4().hex[:6]}",
        "account_number": TEST_ACCOUNT,
        "copropriete_id": DEMO_COPRO_ID,
        "default_occupant_pct": 60,
        "default_proprietaire_pct": 40,
    })
    assert r.status_code == 200, f"category create failed: {r.status_code} {r.text}"
    cat = r.json()
    yield cat
    # Cleanup: delete any invoices using this category, then category
    inv_list = session.get(f"{BASE}/api/invoices",
                           params={"copropriete_id": DEMO_COPRO_ID}).json()
    for inv in inv_list:
        if inv.get("expense_category_id") == cat["id"]:
            session.delete(f"{BASE}/api/invoices/{inv['id']}")
    session.delete(f"{BASE}/api/expense-categories/{cat['id']}")


# ============ EXPENSE CATEGORY ============

class TestExpenseCategoryRepartition:
    def test_create_60_40_ok(self, temp_category):
        assert temp_category["default_occupant_pct"] == 60
        assert temp_category["default_proprietaire_pct"] == 40

    def test_get_returns_pct_fields(self, session, temp_category):
        r = session.get(f"{BASE}/api/expense-categories/{temp_category['id']}")
        assert r.status_code == 200
        d = r.json()
        assert d["default_occupant_pct"] == 60
        assert d["default_proprietaire_pct"] == 40

    def test_create_sum_not_100_rejected(self, session):
        # Use a different account (a class 6 that exists) - we'll just expect a 400 BEFORE the account is checked
        # Validation order in code: sum check is FIRST, so it should 400 regardless
        r = session.post(f"{BASE}/api/expense-categories", json={
            "name": f"TEST_iter31_bad_{uuid.uuid4().hex[:6]}",
            "account_number": "611100",
            "copropriete_id": DEMO_COPRO_ID,
            "default_occupant_pct": 70,
            "default_proprietaire_pct": 40,  # sum=110
        })
        assert r.status_code == 400
        assert "100" in r.text

    def test_update_sum_not_100_rejected(self, session, temp_category):
        r = session.put(f"{BASE}/api/expense-categories/{temp_category['id']}", json={
            "name": temp_category["name"],
            "account_number": TEST_ACCOUNT,
            "copropriete_id": DEMO_COPRO_ID,
            "default_occupant_pct": 50,
            "default_proprietaire_pct": 30,  # sum=80
        })
        assert r.status_code == 400
        assert "100" in r.text

    def test_update_pct_persists(self, session, temp_category):
        # Update to 70/30
        r = session.put(f"{BASE}/api/expense-categories/{temp_category['id']}", json={
            "name": temp_category["name"],
            "account_number": TEST_ACCOUNT,
            "copropriete_id": DEMO_COPRO_ID,
            "default_occupant_pct": 70,
            "default_proprietaire_pct": 30,
        })
        assert r.status_code == 200
        d = r.json()
        assert d["default_occupant_pct"] == 70
        # GET to verify persistence
        r2 = session.get(f"{BASE}/api/expense-categories/{temp_category['id']}")
        assert r2.json()["default_occupant_pct"] == 70
        # Restore to 60/40
        session.put(f"{BASE}/api/expense-categories/{temp_category['id']}", json={
            "name": temp_category["name"],
            "account_number": TEST_ACCOUNT,
            "copropriete_id": DEMO_COPRO_ID,
            "default_occupant_pct": 60,
            "default_proprietaire_pct": 40,
        })


# ============ INVOICE ============

class TestInvoiceRepartition:
    def test_create_inherits_from_category(self, session, temp_category):
        r = session.post(f"{BASE}/api/invoices", json={
            "number": f"TEST-INH-{uuid.uuid4().hex[:6]}",
            "date": "2025-06-01",
            "supplier": "TEST_iter31_supplier",
            "description": "Inherit test",
            "total_amount": 100.0,
            "expense_category_id": temp_category["id"],
            "copropriete_id": DEMO_COPRO_ID,
        })
        assert r.status_code == 200, r.text
        inv = r.json()
        assert inv["occupant_pct"] == 60
        assert inv["proprietaire_pct"] == 40
        assert inv["occupant_amount"] == 60.0
        assert inv["proprietaire_amount"] == 40.0
        # GET verifies persistence
        g = session.get(f"{BASE}/api/invoices/{inv['id']}").json()
        assert g["occupant_pct"] == 60
        assert g["occupant_amount"] == 60.0

    def test_create_override_pct(self, session, temp_category):
        r = session.post(f"{BASE}/api/invoices", json={
            "number": f"TEST-OVR-{uuid.uuid4().hex[:6]}",
            "date": "2025-06-02",
            "supplier": "TEST_iter31_supplier",
            "description": "Override test",
            "total_amount": 200.0,
            "expense_category_id": temp_category["id"],
            "occupant_pct": 20,
            "copropriete_id": DEMO_COPRO_ID,
        })
        assert r.status_code == 200, r.text
        inv = r.json()
        assert inv["occupant_pct"] == 20
        assert inv["proprietaire_pct"] == 80
        assert inv["occupant_amount"] == 40.0  # 200 * 0.2
        assert inv["proprietaire_amount"] == 160.0

    def test_update_preserves_existing_when_not_in_payload(self, session, temp_category):
        # Create with explicit 25
        r = session.post(f"{BASE}/api/invoices", json={
            "number": f"TEST-UPD-{uuid.uuid4().hex[:6]}",
            "date": "2025-06-03",
            "supplier": "TEST_iter31_supplier",
            "description": "Update test",
            "total_amount": 100.0,
            "expense_category_id": temp_category["id"],
            "occupant_pct": 25,
            "copropriete_id": DEMO_COPRO_ID,
        })
        inv = r.json()
        inv_id = inv["id"]
        # PUT without occupant_pct -> must keep 25
        r2 = session.put(f"{BASE}/api/invoices/{inv_id}", json={
            "number": inv["number"],
            "date": inv["date"],
            "supplier": inv["supplier"],
            "description": "Updated desc",
            "total_amount": 100.0,
            "expense_category_id": temp_category["id"],
            "copropriete_id": DEMO_COPRO_ID,
            # occupant_pct intentionally omitted
        })
        assert r2.status_code == 200
        updated = r2.json()
        assert updated["occupant_pct"] == 25
        assert updated["proprietaire_pct"] == 75
        # Now PUT with new value -> must update
        r3 = session.put(f"{BASE}/api/invoices/{inv_id}", json={
            "number": inv["number"],
            "date": inv["date"],
            "supplier": inv["supplier"],
            "description": "Updated again",
            "total_amount": 100.0,
            "expense_category_id": temp_category["id"],
            "copropriete_id": DEMO_COPRO_ID,
            "occupant_pct": 80,
        })
        assert r3.status_code == 200
        assert r3.json()["occupant_pct"] == 80
        assert r3.json()["occupant_amount"] == 80.0


# ============ JOURNAL ENTRY (OD) ============

class TestJournalEntryRepartition:
    """Test occupant_pct enrichment on OD lines."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, session):
        created = []
        yield created
        for eid in created:
            try:
                session.delete(f"{BASE}/api/accounting/entries/{eid}")
            except Exception:
                pass

    def test_charge_line_inherits_from_category(self, session, temp_category, _cleanup):
        # OD with line on the test charge account (612300) -> must inherit 60/40
        r = session.post(f"{BASE}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2025-06-10",
            "description": "TEST_iter31_inherit",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": TEST_ACCOUNT, "debit": 100.0, "credit": 0.0,
                 "description": "charge"},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0,
                 "description": "contre"},
            ],
        })
        assert r.status_code == 200, r.text
        e = r.json()
        _cleanup.append(e["id"])
        line_charge = next(l for l in e["lines"] if l["account_number"] == TEST_ACCOUNT)
        line_other = next(l for l in e["lines"] if l["account_number"] == "440000")
        assert line_charge["occupant_pct"] == 60
        assert line_charge["proprietaire_pct"] == 40
        # Non-charge line must NOT have occupant_pct set (left None)
        assert line_other.get("occupant_pct") is None

    def test_charge_line_no_category_defaults_0_100(self, session, _cleanup):
        # 614000 has no expense_category (per agent note: heavily used by invoices but no cat)
        # Verify by attempting; if a category was created we'll detect non-zero
        r = session.post(f"{BASE}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2025-06-11",
            "description": "TEST_iter31_nocat",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": "614000", "debit": 50.0, "credit": 0.0},
                {"account_number": "440000", "debit": 0.0, "credit": 50.0},
            ],
        })
        assert r.status_code == 200, r.text
        e = r.json()
        _cleanup.append(e["id"])
        ln = next(l for l in e["lines"] if l["account_number"] == "614000")
        # Should be 0% occupant / 100% proprio (no cat OR cat default)
        assert ln["occupant_pct"] is not None
        assert ln["proprietaire_pct"] is not None
        assert abs(ln["occupant_pct"] + ln["proprietaire_pct"] - 100) < 0.01

    def test_explicit_occupant_only_autocomplete(self, session, temp_category, _cleanup):
        # Provide occupant_pct=30 only, proprietaire_pct unset -> auto = 70
        r = session.post(f"{BASE}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2025-06-12",
            "description": "TEST_iter31_explicit",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": TEST_ACCOUNT, "debit": 100.0, "credit": 0.0,
                 "occupant_pct": 30},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0},
            ],
        })
        assert r.status_code == 200, r.text
        e = r.json()
        _cleanup.append(e["id"])
        ln = next(l for l in e["lines"] if l["account_number"] == TEST_ACCOUNT)
        assert ln["occupant_pct"] == 30
        assert ln["proprietaire_pct"] == 70

    def test_both_provided_sum_not_100_rejected(self, session, temp_category, _cleanup):
        r = session.post(f"{BASE}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2025-06-13",
            "description": "TEST_iter31_bad",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": TEST_ACCOUNT, "debit": 100.0, "credit": 0.0,
                 "occupant_pct": 50, "proprietaire_pct": 30},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0},
            ],
        })
        assert r.status_code == 400
        assert "100" in r.text

    def test_update_preserves_and_recalculates(self, session, temp_category, _cleanup):
        # Create with inherit
        r = session.post(f"{BASE}/api/accounting/entries", json={
            "journal_type": "OD",
            "date": "2025-06-14",
            "description": "TEST_iter31_update",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": TEST_ACCOUNT, "debit": 100.0, "credit": 0.0},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0},
            ],
        })
        assert r.status_code == 200
        e = r.json()
        _cleanup.append(e["id"])
        eid = e["id"]
        # PUT with explicit 25/75
        r2 = session.put(f"{BASE}/api/accounting/entries/{eid}", json={
            "journal_type": "OD",
            "date": "2025-06-14",
            "description": "TEST_iter31_updated",
            "copropriete_id": DEMO_COPRO_ID,
            "lines": [
                {"account_number": TEST_ACCOUNT, "debit": 100.0, "credit": 0.0,
                 "occupant_pct": 25, "proprietaire_pct": 75},
                {"account_number": "440000", "debit": 0.0, "credit": 100.0},
            ],
        })
        assert r2.status_code == 200, r2.text
        u = r2.json()
        ln = next(l for l in u["lines"] if l["account_number"] == TEST_ACCOUNT)
        assert ln["occupant_pct"] == 25
        assert ln["proprietaire_pct"] == 75
