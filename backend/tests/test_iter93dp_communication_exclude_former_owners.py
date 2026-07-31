"""iter93dp - Verify /api/communication/owners-balances excludes former owners
while /api/reports/balance-tiers/owners still includes them (regression check).

ACP Agathe (id=c9cfce94-96c6-4202-8a6d-0a5627b50856): CALLENS Bernadette is a
former owner (is_former_owner=true). She must NOT appear in communication
endpoint but MUST appear in the balance-tiers accounting report.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
ACP_ID = "c9cfce94-96c6-4202-8a6d-0a5627b50856"


@pytest.fixture(scope="module")
def auth_client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    # Auth via session cookie (no bearer token returned)
    return s


# ============================================================
# Communication endpoint - must EXCLUDE former owners
# ============================================================

class TestCommunicationOwnersBalancesExcludesFormer:
    def test_no_callens_in_communication(self, auth_client):
        r = auth_client.get(
            f"{BASE_URL}/api/communication/owners-balances",
            params={"copropriete_id": ACP_ID}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        owners = data.get("owners") or []
        assert len(owners) > 0, "Expected some active owners"
        # No former owner flag
        formers = [o for o in owners if o.get("is_former_owner")]
        assert formers == [], f"Found former owners in communication endpoint: {formers}"
        # No CALLENS by name
        callens = [o for o in owners
                   if "CALLENS" in (o.get("owner_name") or o.get("name") or "").upper()]
        assert callens == [], f"CALLENS should not appear: {callens}"

    def test_totals_coherent_with_active_owners(self, auth_client):
        r = auth_client.get(
            f"{BASE_URL}/api/communication/owners-balances",
            params={"copropriete_id": ACP_ID}, timeout=60)
        assert r.status_code == 200
        data = r.json()
        owners = data.get("owners") or []
        expected_deb = round(sum(o["balance"] for o in owners if o["balance"] > 0), 2)
        expected_cred = round(sum(abs(o["balance"]) for o in owners if o["balance"] < 0), 2)
        assert abs(data.get("total_debiteurs", 0) - expected_deb) < 0.02, \
            f"total_debiteurs mismatch: got {data.get('total_debiteurs')} vs {expected_deb}"
        assert abs(data.get("total_crediteurs", 0) - expected_cred) < 0.02, \
            f"total_crediteurs mismatch: got {data.get('total_crediteurs')} vs {expected_cred}"

    def test_with_period_filter_still_excludes_former(self, auth_client):
        r = auth_client.get(
            f"{BASE_URL}/api/communication/owners-balances",
            params={"copropriete_id": ACP_ID,
                    "start_date": "2026-04-01",
                    "end_date": "2026-07-31"},
            timeout=90)
        assert r.status_code == 200, r.text
        data = r.json()
        owners = data.get("owners") or []
        formers = [o for o in owners if o.get("is_former_owner")]
        assert formers == [], f"Found former owners with period filter: {formers}"
        callens = [o for o in owners
                   if "CALLENS" in (o.get("owner_name") or o.get("name") or "").upper()]
        assert callens == [], f"CALLENS should not appear with period filter: {callens}"
        # period echo
        assert data.get("period", {}).get("start_date") == "2026-04-01"
        assert data.get("period", {}).get("end_date") == "2026-07-31"
        # Coherent totals recomputed
        expected_deb = round(sum(o["balance"] for o in owners if o["balance"] > 0.01), 2)
        expected_cred = round(sum(-o["balance"] for o in owners if o["balance"] < -0.01), 2)
        assert abs(data.get("total_debiteurs", 0) - expected_deb) < 0.02
        assert abs(data.get("total_crediteurs", 0) - expected_cred) < 0.02


# ============================================================
# Balance-tiers accounting report - MUST still include former owners
# ============================================================

class TestBalanceTiersOwnersIncludesFormer:
    def test_former_owner_still_present_in_balance_tiers(self, auth_client):
        """Regression: balance-tiers/owners must still include is_former_owner=true entries."""
        r = auth_client.get(
            f"{BASE_URL}/api/reports/balance-tiers/owners",
            params={"copropriete_id": ACP_ID}, timeout=90)
        assert r.status_code == 200, r.text
        data = r.json()
        owners = data.get("owners") or []
        assert len(owners) > 0
        formers = [o for o in owners if o.get("is_former_owner")]
        assert len(formers) >= 1, (
            f"Regression: at least one former owner must appear in balance-tiers/owners "
            f"(total_owners={len(owners)})"
        )
        # Log which former owners are still visible for accounting integrity
        print(f"Former owners visible in balance-tiers: "
              f"{[(o.get('owner_name'), o.get('balance')) for o in formers]}")

    def test_balance_tiers_owner_count_ge_communication(self, auth_client):
        """Balance-tiers must have >= owners than communication (since it includes formers)."""
        r1 = auth_client.get(
            f"{BASE_URL}/api/reports/balance-tiers/owners",
            params={"copropriete_id": ACP_ID}, timeout=90)
        r2 = auth_client.get(
            f"{BASE_URL}/api/communication/owners-balances",
            params={"copropriete_id": ACP_ID}, timeout=60)
        assert r1.status_code == 200 and r2.status_code == 200
        n_bt = len(r1.json().get("owners") or [])
        n_com = len(r2.json().get("owners") or [])
        assert n_bt >= n_com, f"balance-tiers={n_bt} should be >= communication={n_com}"
        assert n_bt > n_com, (
            f"Expected at least 1 former owner difference: bt={n_bt} com={n_com}"
        )
