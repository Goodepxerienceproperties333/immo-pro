"""Iter19 backend tests:
1. Frais privatifs sur facture (4-line auto-entry)
2. Wizard budget: Fonds de roulement (5th step, credit 100)
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bilan-secure.preview.emergentagent.com").rstrip("/")
ACP_ID = "6748ca1a-216d-4002-8417-799287238736"


# ------------------------ fixtures ------------------------
@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def first_owner(session):
    owners = session.get(f"{BASE_URL}/api/owners").json()
    assert len(owners) > 0, "No owners in demo"
    return owners[0]


@pytest.fixture(scope="module")
def approved_budget(session):
    budgets = session.get(f"{BASE_URL}/api/fiscal/budgets", params={"copropriete_id": ACP_ID}).json()
    approved = [b for b in budgets if b.get("status") == "approved"]
    if not approved:
        pytest.skip("No approved budget in demo ACP")
    return approved[0]


@pytest.fixture(scope="module")
def first_dist_key(session):
    keys = session.get(f"{BASE_URL}/api/distribution-keys",
                       params={"copropriete_id": ACP_ID}).json()
    assert len(keys) > 0, "No distribution keys"
    return keys[0]


# Cleanup tracking
_created_invoice_ids = []
_created_fund_call_ids = []


@pytest.fixture(scope="module", autouse=True)
def cleanup(session):
    yield
    for iid in _created_invoice_ids:
        try:
            session.delete(f"{BASE_URL}/api/invoices/{iid}")
        except Exception:
            pass
    for cid in _created_fund_call_ids:
        try:
            session.delete(f"{BASE_URL}/api/fund-calls/{cid}")
        except Exception:
            pass


# ------------------------ Frais privatifs ------------------------
class TestPrivateFeeInvoice:
    def test_create_private_fee_invoice_4lines(self, session, first_owner):
        payload = {
            "number": f"TEST_PF_{uuid.uuid4().hex[:6]}",
            "date": "2025-03-15",
            "supplier": "TEST_PrivFournisseur",
            "description": "Reparation porte privative",
            "total_amount": 250.0,
            "vat_amount": 0,
            "account_number": "600000",  # should be forced to 643
            "distribution_key_id": "irrelevant-key",  # should be cleared
            "copropriete_id": ACP_ID,
            "is_private_fee": True,
            "private_fee_owner_id": first_owner["id"],
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code == 200, r.text
        inv = r.json()
        _created_invoice_ids.append(inv["id"])

        # Invoice fields
        assert inv["account_number"] == "643"
        assert inv["distribution_key_id"] == ""
        assert inv["distribution_lines"] == []
        assert inv["is_private_fee"] is True
        assert inv["private_fee_owner_id"] == first_owner["id"]

        # Find auto journal entry
        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"copropriete_id": ACP_ID}).json()
        ent = [e for e in entries if e.get("source_id") == inv["id"]]
        assert len(ent) == 1, f"Expected 1 auto entry, got {len(ent)}"
        e = ent[0]
        assert e["journal_type"] == "AC"
        assert e["total_debit"] == 500.0  # 2 * 250
        assert e["total_credit"] == 500.0
        assert len(e["lines"]) == 4

        # Validate the 4 lines pattern
        debits_643 = [l for l in e["lines"] if l["account_number"] == "643" and l["debit"] > 0]
        credits_643 = [l for l in e["lines"] if l["account_number"] == "643" and l["credit"] > 0]
        supplier_credits = [l for l in e["lines"]
                            if l["account_number"].startswith("44000") and l["credit"] > 0]
        owner_debits = [l for l in e["lines"]
                        if l["account_number"].startswith("40000") and l["debit"] > 0]
        assert len(debits_643) == 1 and debits_643[0]["debit"] == 250.0
        assert len(credits_643) == 1 and credits_643[0]["credit"] == 250.0
        assert len(supplier_credits) == 1 and supplier_credits[0]["credit"] == 250.0
        assert len(owner_debits) == 1 and owner_debits[0]["debit"] == 250.0
        # Owner line should have third_party_id = owner_id
        assert owner_debits[0]["third_party_id"] == first_owner["id"]

    def test_create_private_fee_missing_owner_400(self, session):
        payload = {
            "number": f"TEST_PF_FAIL_{uuid.uuid4().hex[:6]}",
            "date": "2025-03-15",
            "supplier": "TEST_X", "description": "x",
            "total_amount": 100.0,
            "copropriete_id": ACP_ID,
            "is_private_fee": True,
            "private_fee_owner_id": "",
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code == 400, r.text
        assert "proprietaire" in r.text.lower()

    def test_create_private_fee_invalid_owner_404(self, session):
        payload = {
            "number": f"TEST_PF_404_{uuid.uuid4().hex[:6]}",
            "date": "2025-03-15",
            "supplier": "TEST_X", "description": "x",
            "total_amount": 100.0,
            "copropriete_id": ACP_ID,
            "is_private_fee": True,
            "private_fee_owner_id": "nonexistent-owner-id-xyz",
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code == 404, r.text

    def test_update_normal_to_private_fee_regenerates_entry(self, session, first_owner, first_dist_key):
        # Create normal invoice with distribution_lines
        normal_payload = {
            "number": f"TEST_NORM2PF_{uuid.uuid4().hex[:6]}",
            "date": "2025-03-20",
            "supplier": "TEST_Fourn2", "description": "Normal then privative",
            "total_amount": 300.0,
            "account_number": "611000",
            "distribution_key_id": first_dist_key["id"],
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=normal_payload)
        assert r.status_code == 200, r.text
        inv = r.json()
        _created_invoice_ids.append(inv["id"])
        assert len(inv["distribution_lines"]) > 0  # had distribution
        assert inv["account_number"] == "611000"

        # Update to private fee
        upd = dict(normal_payload)
        upd["is_private_fee"] = True
        upd["private_fee_owner_id"] = first_owner["id"]
        r2 = session.put(f"{BASE_URL}/api/invoices/{inv['id']}", json=upd)
        assert r2.status_code == 200, r2.text
        updated = r2.json()
        assert updated["account_number"] == "643"
        assert updated["distribution_lines"] == []
        assert updated["distribution_key_id"] == ""
        assert updated["is_private_fee"] is True

        # Auto entry must now be 4 lines
        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"copropriete_id": ACP_ID}).json()
        ent = [e for e in entries if e.get("source_id") == inv["id"]]
        assert len(ent) == 1
        assert len(ent[0]["lines"]) == 4
        assert ent[0]["total_debit"] == 600.0

    def test_create_normal_invoice_regression_2lines(self, session, first_dist_key):
        payload = {
            "number": f"TEST_NORM_REG_{uuid.uuid4().hex[:6]}",
            "date": "2025-03-25",
            "supplier": "TEST_NormSupp",
            "description": "Normal regression",
            "total_amount": 480.0,
            "account_number": "614000",
            "distribution_key_id": first_dist_key["id"],
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code == 200, r.text
        inv = r.json()
        _created_invoice_ids.append(inv["id"])
        assert inv["account_number"] == "614000"
        assert inv["is_private_fee"] is False
        assert len(inv["distribution_lines"]) > 0

        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"copropriete_id": ACP_ID}).json()
        ent = [e for e in entries if e.get("source_id") == inv["id"]]
        assert len(ent) == 1
        assert len(ent[0]["lines"]) == 2  # standard 2-line
        assert ent[0]["total_debit"] == 480.0
        assert ent[0]["total_credit"] == 480.0


# ------------------------ Fonds de roulement (budget wizard) ------------------------
class TestRoulementBudgetWizard:
    def test_preview_roulement_only(self, session, approved_budget, first_dist_key):
        payload = {
            "budget_id": approved_budget["id"],
            "frequency": 4,
            "start_date": "2025-04-01",
            "due_offset_days": 30,
            "roulement_fund": {
                "enabled": True,
                "amount": 1000.0,
                "distribution_key_id": first_dist_key["id"],
                "label": "TEST_Roulement",
                "mode": "create",
            },
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget", json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["persisted"] is False
        assert data["summary"]["roulement_total"] == 1000.0
        assert len(data["calls"]) == 4
        # call#1 contient roulement
        assert data["calls"][0]["roulement_amount"] == 1000.0
        # calls 2..n zero
        for c in data["calls"][1:]:
            assert c["roulement_amount"] == 0
        # ligne is_roulement=true sur call#1
        roul_lines = [l for l in data["calls"][0]["lines"] if l.get("is_roulement")]
        assert len(roul_lines) == 1
        assert roul_lines[0]["roulement_mode"] == "create"
        assert roul_lines[0]["amount"] == 1000.0

    def test_preview_reserve_plus_roulement_sums(self, session, approved_budget, first_dist_key):
        payload = {
            "budget_id": approved_budget["id"],
            "frequency": 2,
            "start_date": "2025-04-01",
            "due_offset_days": 30,
            "reserve_fund": {
                "enabled": True, "amount": 500.0,
                "distribution_key_id": first_dist_key["id"],
                "label": "TEST_Reserve",
            },
            "roulement_fund": {
                "enabled": True, "amount": 800.0,
                "distribution_key_id": first_dist_key["id"],
                "label": "TEST_Roul", "mode": "increase",
            },
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["summary"]
        assert s["reserve_total"] == 500.0
        assert s["roulement_total"] == 800.0
        # grand_total = budget_total + reserve + roulement
        expected_grand = round(s["budget_total"] + s["reserve_total"] + s["roulement_total"], 2)
        assert abs(s["grand_total"] - expected_grand) < 0.01
        # Call 1 has all 3 categories (lines include is_reserve + is_roulement)
        call1 = d["calls"][0]
        has_reserve = any(l.get("is_reserve") for l in call1["lines"])
        has_roul = any(l.get("is_roulement") for l in call1["lines"])
        has_provisions = any(not l.get("is_reserve") and not l.get("is_roulement")
                             for l in call1["lines"])
        assert has_reserve and has_roul and has_provisions
        # Calls 2..n: only provisions
        for c in d["calls"][1:]:
            assert all(not l.get("is_reserve") and not l.get("is_roulement") for l in c["lines"])
            assert c["roulement_amount"] == 0
            assert c["reserve_amount"] == 0

    def test_generate_persists_and_ve_has_account_100(self, session, approved_budget, first_dist_key):
        payload = {
            "budget_id": approved_budget["id"],
            "frequency": 2,
            "start_date": "2026-01-01",  # future to avoid collision
            "due_offset_days": 30,
            "roulement_fund": {
                "enabled": True, "amount": 1200.0,
                "distribution_key_id": first_dist_key["id"],
                "label": "TEST_RoulGen", "mode": "create",
            },
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/generate-from-budget", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["persisted"] is True
        assert len(d["created_ids"]) == 2
        _created_fund_call_ids.extend(d["created_ids"])

        # Fetch first call from DB
        first_id = d["created_ids"][0]
        fc = session.get(f"{BASE_URL}/api/fund-calls/{first_id}").json()
        assert fc["roulement_amount"] == 1200.0

        # VE auto-entry must contain credit 100 + debits 40000XXX
        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"copropriete_id": ACP_ID}).json()
        ent = [e for e in entries if e.get("source_id") == first_id]
        assert len(ent) == 1, f"Expected 1 VE auto-entry, got {len(ent)}"
        e = ent[0]
        assert e["journal_type"] == "VE"
        # credit on 100 == roulement_amount
        credit_100 = [l for l in e["lines"] if l["account_number"] == "100" and l["credit"] > 0]
        assert len(credit_100) == 1, f"No credit on 100: {e['lines']}"
        assert abs(credit_100[0]["credit"] - 1200.0) < 0.5
        # at least one debit on 40000XXX
        owner_debits = [l for l in e["lines"]
                        if l["account_number"].startswith("40000") and l["debit"] > 0]
        assert len(owner_debits) >= 1

        # Second call: no roulement
        second_id = d["created_ids"][1]
        fc2 = session.get(f"{BASE_URL}/api/fund-calls/{second_id}").json()
        assert fc2["roulement_amount"] == 0
        ent2 = [e for e in entries if e.get("source_id") == second_id]
        if ent2:
            credit_100_2 = [l for l in ent2[0]["lines"]
                            if l["account_number"] == "100" and l["credit"] > 0]
            assert len(credit_100_2) == 0

    def test_generate_without_roulement_regression(self, session, approved_budget):
        payload = {
            "budget_id": approved_budget["id"],
            "frequency": 2,
            "start_date": "2027-01-01",  # far future, distinct
            "due_offset_days": 30,
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/generate-from-budget", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["persisted"] is True
        _created_fund_call_ids.extend(d["created_ids"])
        # grand_total = budget_total (+ reserve 0 + roulement 0)
        s = d["summary"]
        assert s["roulement_total"] == 0
        assert s["reserve_total"] == 0
        assert abs(s["grand_total"] - s["budget_total"]) < 0.01
        # No credit on 100 anywhere
        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"copropriete_id": ACP_ID}).json()
        for cid in d["created_ids"]:
            ent = [e for e in entries if e.get("source_id") == cid]
            if ent:
                credit_100 = [l for l in ent[0]["lines"]
                              if l["account_number"] == "100" and l["credit"] > 0]
                assert len(credit_100) == 0


# ------------------------ Other regressions ------------------------
class TestOtherRegression:
    def test_pcmn_toggle_active(self, session):
        # Get an active custom-ish account (we'll toggle and re-toggle)
        accs = session.get(f"{BASE_URL}/api/accounting/pcmn",
                           params={"copropriete_id": ACP_ID}).json()
        active_ones = [a for a in accs if a.get("active") and not a.get("is_tier_account")]
        assert len(active_ones) > 0
        target = active_ones[0]
        n = target["number"]
        r = session.patch(f"{BASE_URL}/api/accounting/pcmn/{n}/toggle-active",
                          params={"copropriete_id": ACP_ID},
                          json={"active": not target.get("active", True)})
        assert r.status_code == 200, r.text
        # toggle back
        r2 = session.patch(f"{BASE_URL}/api/accounting/pcmn/{n}/toggle-active",
                           params={"copropriete_id": ACP_ID},
                           json={"active": target.get("active", True)})
        assert r2.status_code == 200

    def test_pdf_depenses(self, session):
        r = session.get(f"{BASE_URL}/api/reports/depenses/pdf",
                        params={"copropriete_id": ACP_ID,
                                "date_from": "2024-01-01",
                                "date_to": "2026-12-31"})
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 500

    def test_reset_financial_data(self, session):
        # Just call dry-run-ish: we'll create a temp ACP and reset it.
        # Safer: skip if no permission. Use existing endpoint with a temp ACP.
        # Create an ephemeral ACP
        r = session.post(f"{BASE_URL}/api/coproprietes",
                         json={"name": f"TEST_iter19_reset_{uuid.uuid4().hex[:6]}",
                               "address": "x", "city": "x", "postal_code": "1000"})
        if r.status_code != 200:
            pytest.skip("Cannot create temp ACP")
        new_id = r.json()["id"]
        try:
            rr = session.post(f"{BASE_URL}/api/coproprietes/{new_id}/reset-financial-data")
            assert rr.status_code in (200, 204), rr.text
        finally:
            session.delete(f"{BASE_URL}/api/coproprietes/{new_id}")
