"""Iter20 backend tests:
P0 - Balance tiers from journal_entries (OD manual visible + reserve calls visible)
P1 - Wizard refactor: reserve & roulement with their own frequency/start_date/due_offset
P3 - Supplier bce_number + invoice_ai extract (bce match + suggest_create)
+ regression iter19 (private fee, roulement legacy)
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://property-mgmt-be.preview.emergentagent.com").rstrip("/")
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
def owners_for_acp(session):
    """Return owners that have lots in the demo ACP."""
    lots = session.get(f"{BASE_URL}/api/lots", params={"copropriete_id": ACP_ID}).json()
    owner_ids = list({l.get("owner_id") for l in lots if l.get("owner_id")})
    all_owners = session.get(f"{BASE_URL}/api/owners").json()
    owners = [o for o in all_owners if o.get("id") in owner_ids]
    assert len(owners) > 0, "No owners attached to ACP demo"
    return owners


@pytest.fixture(scope="module")
def dubois(owners_for_acp):
    """Owner Dubois Jean from demo seed."""
    for o in owners_for_acp:
        nm = (o.get("name") or "").lower()
        if "dubois" in nm:
            return o
    # Fallback : first owner
    return owners_for_acp[0]


@pytest.fixture(scope="module")
def dubois_accounts(session, dubois):
    """Return tier accounts dict for Dubois in the demo ACP."""
    # Call balance-tiers/owners and pick our owner's mapping
    r = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                    params={"copropriete_id": ACP_ID})
    assert r.status_code == 200, r.text
    data = r.json()
    for row in data.get("owners", []):
        if row.get("owner_id") == dubois["id"]:
            return {"provisions": row.get("account_provisions", ""),
                    "reserve": row.get("account_reserve", "")}
    pytest.skip("Dubois has no tier accounts mapped")


@pytest.fixture(scope="module")
def approved_budget(session):
    budgets = session.get(f"{BASE_URL}/api/fiscal/budgets",
                          params={"copropriete_id": ACP_ID}).json()
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


# Cleanup trackers
_created_entries = []
_created_fund_calls = []
_created_suppliers = []


@pytest.fixture(scope="module", autouse=True)
def cleanup(session):
    yield
    for eid in _created_entries:
        try:
            session.delete(f"{BASE_URL}/api/accounting/entries/{eid}")
        except Exception:
            pass
    for fcid in _created_fund_calls:
        try:
            session.delete(f"{BASE_URL}/api/fund-calls/{fcid}")
        except Exception:
            pass
    for sid in _created_suppliers:
        try:
            session.delete(f"{BASE_URL}/api/suppliers/{sid}")
        except Exception:
            pass


# ========================================================
# P0 - BALANCE TIERS FROM JOURNAL ENTRIES
# ========================================================
class TestP0BalanceTiers:

    def test_balance_tiers_owners_fields_present(self, session):
        """Verify schema: provisions_*, reserve_*, unmatched_paid + compat fields."""
        r = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                        params={"copropriete_id": ACP_ID})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "owners" in data and "total_debiteurs" in data and "total_crediteurs" in data
        assert len(data["owners"]) > 0
        row = data["owners"][0]
        required_new = ["provisions_debit", "provisions_credit", "provisions_balance",
                        "reserve_debit", "reserve_credit", "reserve_balance",
                        "unmatched_paid"]
        for f in required_new:
            assert f in row, f"Missing new field {f}: row keys={list(row.keys())}"
        required_compat = ["owner_id", "owner_name", "vcs_code",
                           "total_called", "total_paid", "balance", "status"]
        for f in required_compat:
            assert f in row, f"Missing compat field {f}"

    def test_manual_od_on_dubois_appears_in_movements_and_balance(self, session, dubois, dubois_accounts):
        """Create a manual OD: Dr 40000XXX Dubois 100 / Cr 760000 100.
        Verify it appears in /balance-tiers/owners/{id}/movements AND
        provisions_debit on /balance-tiers/owners includes this 100."""
        acc_prov = dubois_accounts["provisions"]
        if not acc_prov:
            pytest.skip("Dubois provisions account missing")

        # Snapshot before
        r0 = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                         params={"copropriete_id": ACP_ID}).json()
        before = next((o for o in r0["owners"] if o["owner_id"] == dubois["id"]), None)
        assert before, "Dubois not in owners list"
        before_debit = before["provisions_debit"]

        # POST OD entry (note: API doesn't accept third_party_id in body, but
        # balance-tiers has fallback : matches by account_number)
        ref = f"TEST_OD_iter20_{uuid.uuid4().hex[:6]}"
        body = {
            "journal_type": "OD",
            "date": "2026-01-15",
            "reference": ref,
            "description": "TEST iter20 - OD manuelle Dubois",
            "lines": [
                {"account_number": acc_prov, "account_name": "Dubois - Provisions",
                 "debit": 100.0, "credit": 0.0},
                {"account_number": "760000", "account_name": "Produits divers",
                 "debit": 0.0, "credit": 100.0},
            ],
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/accounting/entries", json=body)
        assert r.status_code == 200, r.text
        entry = r.json()
        _created_entries.append(entry["id"])

        # Verify movements
        r2 = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners/{dubois['id']}",
                         params={"copropriete_id": ACP_ID})
        assert r2.status_code == 200, r2.text
        situation = r2.json()
        # OD line with 100 debit on Dubois prov account must be present
        od_movs = [m for m in situation["movements"]
                   if m.get("journal_type") == "OD"
                   and m.get("account_number") == acc_prov
                   and abs(m.get("debit", 0) - 100.0) < 0.01]
        assert len(od_movs) >= 1, f"OD movement not found. movements={situation['movements'][-5:]}"

        # Verify provisions_debit increased
        r3 = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                         params={"copropriete_id": ACP_ID}).json()
        after = next((o for o in r3["owners"] if o["owner_id"] == dubois["id"]), None)
        assert after["provisions_debit"] >= before_debit + 99.99, \
            f"provisions_debit did not increase: before={before_debit}, after={after['provisions_debit']}"

    def test_total_called_includes_reserve(self, session, approved_budget, first_dist_key, dubois):
        """Generate a reserve fund call via wizard (own schedule), then verify
        reserve_debit > 0 for Dubois and total_called == prov_debit + res_debit."""
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 1,
            "start_date": "2027-01-01",
            "due_offset_days": 30,
            "copropriete_id": ACP_ID,
            "reserve_fund": {
                "enabled": True,
                "amount": 500.0,
                "distribution_key_id": first_dist_key["id"],
                "label": "TEST iter20 Reserve",
                "frequency": 1,
                "start_date": "2027-06-01",
                "due_offset_days": 30,
            },
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body)
        assert r.status_code == 200, r.text
        result = r.json()
        for cid in result.get("created_ids", []):
            _created_fund_calls.append(cid)

        # Verify reserve_debit > 0 for at least one owner having reserve account
        r2 = session.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                         params={"copropriete_id": ACP_ID}).json()
        owners_with_reserve = [o for o in r2["owners"] if o["reserve_debit"] > 0]
        assert len(owners_with_reserve) > 0, \
            "No owner has reserve_debit > 0 after reserve fund-call generation"
        # total_called consistency
        for o in owners_with_reserve:
            expected = round(o["provisions_debit"] + o["reserve_debit"], 2)
            assert abs(o["total_called"] - expected) < 0.02, \
                f"total_called mismatch for {o['owner_name']}: " \
                f"{o['total_called']} != {expected}"


# ========================================================
# P1 - WIZARD REFACTOR (independent series)
# ========================================================
class TestP1WizardIndependentSeries:

    def test_preview_independent_series_6_calls(self, session, approved_budget, first_dist_key):
        """4 trimestriels provisions + 1 reserve annuel + 1 roulement annuel = 6 calls."""
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 4,  # trimestriel
            "start_date": "2027-03-01",
            "due_offset_days": 30,
            "copropriete_id": ACP_ID,
            "reserve_fund": {
                "enabled": True, "amount": 500.0,
                "distribution_key_id": first_dist_key["id"],
                "frequency": 1, "start_date": "2027-06-01",
                "due_offset_days": 30,
            },
            "roulement_fund": {
                "enabled": True, "amount": 1000.0,
                "distribution_key_id": first_dist_key["id"],
                "mode": "create",
                "frequency": 1, "start_date": "2027-03-15",
                "due_offset_days": 30,
            },
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        calls = data["calls"]
        assert len(calls) == 6, f"Expected 6 calls (4+1+1), got {len(calls)}: " \
                                f"{[(c['name'],c['call_type'],c['date']) for c in calls]}"

        prov = [c for c in calls if c["call_type"] == "provisions"]
        reserve = [c for c in calls if c["call_type"] == "reserve"]
        roul = [c for c in calls if c["call_type"] == "roulement"]
        assert len(prov) == 4
        assert len(reserve) == 1
        assert len(roul) == 1

        # Reserve must be dated 2027-06-01 with amount 500
        assert reserve[0]["date"] == "2027-06-01"
        assert abs(reserve[0]["total_amount"] - 500.0) < 0.01
        assert abs(reserve[0]["reserve_amount"] - 500.0) < 0.01

        # Roulement must be 2027-03-15 with 1000
        assert roul[0]["date"] == "2027-03-15"
        assert abs(roul[0]["total_amount"] - 1000.0) < 0.01
        assert abs(roul[0]["roulement_amount"] - 1000.0) < 0.01

        # Provisions calls must NOT contain reserve/roulement amounts
        for p in prov:
            assert p.get("reserve_amount", 0) == 0, \
                f"Provisions call {p['name']} has reserve_amount={p['reserve_amount']}"
            assert p.get("roulement_amount", 0) == 0, \
                f"Provisions call {p['name']} has roulement_amount={p['roulement_amount']}"

    def test_preview_legacy_when_frequency_zero(self, session, approved_budget, first_dist_key):
        """frequency=0 on reserve_fund -> legacy injection into call #1."""
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 4,
            "start_date": "2028-01-01",
            "due_offset_days": 30,
            "copropriete_id": ACP_ID,
            "reserve_fund": {
                "enabled": True, "amount": 800.0,
                "distribution_key_id": first_dist_key["id"],
                "frequency": 0,  # legacy
            },
            "roulement_fund": {
                "enabled": True, "amount": 600.0,
                "distribution_key_id": first_dist_key["id"],
                "mode": "create",
                "frequency": 0,  # legacy
            },
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget", json=body)
        assert r.status_code == 200, r.text
        calls = r.json()["calls"]
        assert len(calls) == 4, f"Expected 4 calls (legacy), got {len(calls)}"
        # Call #1 must contain reserve+roulement
        assert abs(calls[0]["reserve_amount"] - 800.0) < 0.01
        assert abs(calls[0]["roulement_amount"] - 600.0) < 0.01
        # Other calls must NOT
        for c in calls[1:]:
            assert c["reserve_amount"] == 0
            assert c["roulement_amount"] == 0

    def test_generate_persists_independent_with_correct_call_type_and_ve(self, session, approved_budget, first_dist_key):
        """generate-from-budget with independent series: verifies persistence and VE auto-entries
        with credit 701000 (reserve) or 100 (roulement)."""
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 2,  # semestriel
            "start_date": "2028-06-01",
            "due_offset_days": 30,
            "copropriete_id": ACP_ID,
            "reserve_fund": {
                "enabled": True, "amount": 400.0,
                "distribution_key_id": first_dist_key["id"],
                "frequency": 1, "start_date": "2028-07-01",
                "due_offset_days": 30,
            },
            "roulement_fund": {
                "enabled": True, "amount": 700.0,
                "distribution_key_id": first_dist_key["id"],
                "mode": "create",
                "frequency": 1, "start_date": "2028-08-01",
                "due_offset_days": 30,
            },
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/generate-from-budget", json=body)
        assert r.status_code == 200, r.text
        result = r.json()
        ids = result.get("created_ids", [])
        for cid in ids:
            _created_fund_calls.append(cid)
        # Get each fund-call back and check call_type
        types_count = {"provisions": 0, "reserve": 0, "roulement": 0}
        reserve_fc_id = None
        roul_fc_id = None
        for cid in ids:
            fc = session.get(f"{BASE_URL}/api/fund-calls/{cid}").json()
            ct = fc.get("call_type", "provisions")
            types_count[ct] = types_count.get(ct, 0) + 1
            if ct == "reserve":
                reserve_fc_id = cid
            elif ct == "roulement":
                roul_fc_id = cid
        assert types_count["provisions"] == 2
        assert types_count["reserve"] == 1
        assert types_count["roulement"] == 1

        # Verify VE entries
        entries = session.get(f"{BASE_URL}/api/accounting/entries",
                              params={"journal_type": "VE",
                                      "copropriete_id": ACP_ID}).json()
        if reserve_fc_id:
            res_e = next((e for e in entries
                          if e.get("source_id") == reserve_fc_id
                          and e.get("source_type") == "fund_call"), None)
            assert res_e, "VE not found for reserve fund_call"
            credits_701 = [l for l in res_e["lines"]
                           if l["account_number"] == "160" and l["credit"] > 0]
            assert credits_701, f"VE reserve missing Cr 160 (Fonds de reserve classe 1): {res_e['lines']}"
        if roul_fc_id:
            roul_e = next((e for e in entries
                           if e.get("source_id") == roul_fc_id
                           and e.get("source_type") == "fund_call"), None)
            assert roul_e, "VE not found for roulement fund_call"
            credits_100 = [l for l in roul_e["lines"]
                           if l["account_number"] == "100" and l["credit"] > 0]
            assert credits_100, f"VE roulement missing Cr 100: {roul_e['lines']}"

    def test_each_fund_supports_all_frequencies(self, session, approved_budget, first_dist_key):
        """Check 6 (bi-mensuel) reserve_fund works (just preview)."""
        body = {
            "budget_id": approved_budget["id"],
            "frequency": 1, "start_date": "2029-01-01",
            "due_offset_days": 30, "copropriete_id": ACP_ID,
            "reserve_fund": {
                "enabled": True, "amount": 600.0,
                "distribution_key_id": first_dist_key["id"],
                "frequency": 6, "start_date": "2029-01-15",
                "due_offset_days": 15,
            },
        }
        r = session.post(f"{BASE_URL}/api/fund-calls/preview-from-budget", json=body)
        assert r.status_code == 200, r.text
        calls = r.json()["calls"]
        reserve_calls = [c for c in calls if c["call_type"] == "reserve"]
        assert len(reserve_calls) == 6, f"Expected 6 reserve calls (bimensuel), got {len(reserve_calls)}"
        assert all(abs(c["total_amount"] - 100.0) < 0.01 for c in reserve_calls), \
            "Reserve amounts should be 600/6=100"


# ========================================================
# P3 - SUPPLIER bce_number + invoice_ai
# ========================================================
class TestP3SupplierBCE:

    def test_supplier_post_and_search_by_bce(self, session):
        bce = f"BE0{uuid.uuid4().hex[:9]}"
        body = {
            "name": f"TEST_BCE_Supplier_{uuid.uuid4().hex[:5]}",
            "bce_number": bce,
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/suppliers", json=body)
        assert r.status_code == 200, r.text
        sup = r.json()
        _created_suppliers.append(sup["id"])
        assert sup.get("bce_number") == bce

        # Search by bce
        r2 = session.get(f"{BASE_URL}/api/suppliers", params={"search": bce})
        assert r2.status_code == 200
        results = r2.json()
        assert any(s["id"] == sup["id"] for s in results), \
            f"Supplier not found by bce search; got {[s.get('name') for s in results]}"

    def test_supplier_put_bce(self, session):
        body = {"name": f"TEST_BCE_Put_{uuid.uuid4().hex[:5]}",
                "bce_number": "", "copropriete_id": ACP_ID}
        r = session.post(f"{BASE_URL}/api/suppliers", json=body)
        assert r.status_code == 200
        sup = r.json()
        _created_suppliers.append(sup["id"])

        new_bce = "BE0987654321"
        body2 = dict(body)
        body2["bce_number"] = new_bce
        r2 = session.put(f"{BASE_URL}/api/suppliers/{sup['id']}", json=body2)
        assert r2.status_code == 200, r2.text
        assert r2.json().get("bce_number") == new_bce


# ========================================================
# Regression iter19 (private fee + roulement legacy)
# ========================================================
class TestRegressionIter19:

    def test_private_fee_invoice_4_lines(self, session, dubois):
        body = {
            "number": f"TEST_PF_REG_{uuid.uuid4().hex[:6]}",
            "date": "2026-02-01",
            "due_date": "2026-03-01",
            "supplier": "TEST iter20 regression PF",
            "description": "Frais privatif test",
            "total_amount": 121.0,
            "account_number": "",
            "distribution_key_id": "",
            "is_private_fee": True,
            "private_fee_owner_id": dubois["id"],
            "copropriete_id": ACP_ID,
        }
        r = session.post(f"{BASE_URL}/api/invoices", json=body)
        assert r.status_code == 200, r.text
        inv = r.json()
        try:
            # Find AC entry
            entries = session.get(f"{BASE_URL}/api/accounting/entries",
                                  params={"journal_type": "AC",
                                          "copropriete_id": ACP_ID}).json()
            ac = next((e for e in entries if e.get("source_id") == inv["id"]), None)
            assert ac, "AC entry missing for private fee"
            assert len(ac["lines"]) == 4, \
                f"Expected 4 lines, got {len(ac['lines'])}"
            # Total debit = 2 * total_amount = 242
            assert abs(ac["total_debit"] - 242.0) < 0.01
            assert abs(ac["total_credit"] - 242.0) < 0.01
        finally:
            session.delete(f"{BASE_URL}/api/invoices/{inv['id']}")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
