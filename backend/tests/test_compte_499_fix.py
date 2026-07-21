"""Tests for the compte 499 fix (Belgian accounting: class 70 only for provisions).

After the AC dedup filter fix (iter90 subsequent): both ACPs 138 and 34a now show
the correct Boni case (charges=5333.51, 499=+1170.16 at Passif).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://copro-belge-app.preview.emergentagent.com").rstrip("/")
ACP_138 = "138cfd69-cc07-46bb-ad17-a98debee7a3a"  # Auto 3 Maria
ACP_34A = "34a2fe77-2137-4e32-81eb-387273d3f242"  # ACPMaria Auto3


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@copro.be", "password": "admin123"},
        timeout=30,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


def _get_bilan(session, acp_id, view_mode=None):
    params = {"copropriete_id": acp_id}
    if view_mode:
        params["view_mode"] = view_mode
    r = session.get(f"{BASE_URL}/api/reports/bilan", params=params, timeout=60)
    assert r.status_code == 200, f"Bilan failed: {r.status_code} {r.text[:500]}"
    return r.json()


def _find_supplier_amount(data, needle):
    """Find a supplier by name substring across passif buckets (dettes fournisseurs)."""
    needle_low = needle.lower()
    for section in data.get("passif", []) or []:
        for acc in section.get("accounts", []) or []:
            name = (acc.get("account_name") or "") + " " + (acc.get("label") or "")
            if needle_low in name.lower():
                return acc.get("amount", 0)
    # Also try actif in case of debit balance
    for section in data.get("actif", []) or []:
        for acc in section.get("accounts", []) or []:
            name = (acc.get("account_name") or "") + " " + (acc.get("label") or "")
            if needle_low in name.lower():
                return acc.get("amount", 0)
    return None


def test_bilan_acp138_boni_after_dedup(session):
    """ACP 138 - After AC dedup: charges=5333.51, 499=+1170.16 (Boni at Passif)."""
    data = _get_bilan(session, ACP_138)
    print(f"ACP138: prov={data.get('provisions_appelees')}, charges={data.get('total_charges')}, 499={data.get('compte_499')}")

    assert data["provisions_appelees"] == pytest.approx(6500.0, abs=0.01)
    assert data["total_charges"] == pytest.approx(5333.51, abs=0.01), \
        f"total_charges={data['total_charges']} (expected 5333.51 after dedup)"
    assert data["compte_499"] == pytest.approx(1170.16, abs=0.01), \
        f"compte_499={data['compte_499']} (expected +1170.16 Boni)"
    assert data.get("equilibre") is True, \
        f"Bilan not balanced: actif={data.get('total_actif')} passif={data.get('total_passif')}"


def test_bilan_acp34a_boni(session):
    """ACP 34a - Boni: charges=5333.51, 499=+1170.16."""
    data = _get_bilan(session, ACP_34A)
    print(f"ACP34a: prov={data.get('provisions_appelees')}, charges={data.get('total_charges')}, 499={data.get('compte_499')}")

    assert data["provisions_appelees"] == pytest.approx(6500.0, abs=0.01)
    assert data["total_charges"] == pytest.approx(5333.51, abs=0.01)
    assert data["compte_499"] == pytest.approx(1170.16, abs=0.01)
    assert data.get("equilibre") is True


def test_bilan_after_distribution_138(session):
    """After distribution ACP138: 499 removed from rubriques, bilan equilibre."""
    data = _get_bilan(session, ACP_138, view_mode="after_distribution")
    print(f"ACP138 after_dist: 499={data.get('compte_499')}, equilibre={data.get('equilibre')}")

    # 499 must not appear in either actif or passif buckets
    for section in (data.get("passif") or []) + (data.get("actif") or []):
        for acc in section.get("accounts", []) or []:
            assert not str(acc.get("account_number", "")).startswith("499"), \
                f"499 must not appear after distribution: {acc}"

    assert data.get("equilibre") is True, \
        f"After-dist bilan not balanced: actif={data.get('total_actif')} passif={data.get('total_passif')}"


def test_bilan_after_distribution_34a(session):
    """After distribution ACP34a: 499 removed, bilan equilibre."""
    data = _get_bilan(session, ACP_34A, view_mode="after_distribution")
    print(f"ACP34a after_dist: 499={data.get('compte_499')}, equilibre={data.get('equilibre')}")

    for section in (data.get("passif") or []) + (data.get("actif") or []):
        for acc in section.get("accounts", []) or []:
            assert not str(acc.get("account_number", "")).startswith("499"), \
                f"499 must not appear after distribution: {acc}"

    assert data.get("equilibre") is True


def test_suppliers_no_duplicates_acp138(session):
    """After AC dedup: SRL Finlead = 639.69 (not 2072.67), Sneyers = 3545.30."""
    data = _get_bilan(session, ACP_138)
    finlead = _find_supplier_amount(data, "finlead")
    sneyers = _find_supplier_amount(data, "sneyers")
    print(f"Suppliers ACP138: Finlead={finlead}, Sneyers={sneyers}")

    assert finlead is not None, "SRL Finlead not found in bilan"
    assert finlead == pytest.approx(639.69, abs=0.02), \
        f"SRL Finlead expected 639.69, got {finlead}"

    assert sneyers is not None, "Sneyers not found in bilan"
    assert sneyers == pytest.approx(3545.30, abs=0.02), \
        f"Sneyers expected 3545.30, got {sneyers}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
