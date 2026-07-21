"""Tests for the compte 499 fix (Belgian accounting: class 70 only for provisions)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://copro-belge-app.preview.emergentagent.com").rstrip("/")
ACP_MALI = "138cfd69-cc07-46bb-ad17-a98debee7a3a"  # Auto 3 Maria - Mali expected
ACP_BONI = "34a2fe77-2137-4e32-81eb-387273d3f242"  # ACPMaria Auto3 - Boni expected


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


def test_bilan_mali_case_138(session):
    """ACP 138 - Mali case: 499 = 6500 - 6766.49 = -266.49 (Actif)."""
    data = _get_bilan(session, ACP_MALI)
    print(f"Mali case keys: {list(data.keys())}")

    # New fields presence
    assert "provisions_appelees" in data, "provisions_appelees field missing"
    assert "total_charges" in data, "total_charges field missing"
    assert "compte_499" in data, "compte_499 field missing"
    assert "produits_hors_provisions" in data, "produits_hors_provisions field missing"

    assert data["provisions_appelees"] == pytest.approx(6500.0, abs=0.01), \
        f"provisions_appelees={data['provisions_appelees']}"
    assert data["total_charges"] == pytest.approx(6766.49, abs=0.01), \
        f"total_charges={data['total_charges']}"
    assert data["compte_499"] == pytest.approx(-266.49, abs=0.01), \
        f"compte_499={data['compte_499']} (expected -266.49)"
    assert data["produits_hors_provisions"] == pytest.approx(3.67, abs=0.01), \
        f"produits_hors_provisions={data['produits_hors_provisions']}"

    # Balance
    assert data.get("equilibre") is True, f"Bilan not balanced: {data.get('total_actif')} vs {data.get('total_passif')}"


def test_bilan_boni_case_34a(session):
    """ACP 34a - Boni case: 499 = 6500 - 5333.51 = +1166.49 (Passif)."""
    data = _get_bilan(session, ACP_BONI)
    print(f"Boni case: prov={data.get('provisions_appelees')}, charges={data.get('total_charges')}, 499={data.get('compte_499')}")

    assert data["provisions_appelees"] == pytest.approx(6500.0, abs=0.01)
    assert data["total_charges"] == pytest.approx(5333.51, abs=0.01)
    assert data["compte_499"] == pytest.approx(1166.49, abs=0.01), \
        f"compte_499={data['compte_499']} (expected +1166.49)"
    assert data.get("equilibre") is True


def test_bilan_after_distribution_34a(session):
    """After distribution: boni distributed to owners, 499 should disappear, produits_hors_provisions remain."""
    data = _get_bilan(session, ACP_BONI, view_mode="after_distribution")
    print(f"After dist: 499={data.get('compte_499')}, produits_hp={data.get('produits_hors_provisions')}, equilibre={data.get('equilibre')}")

    # After distribution, the 499 must no longer appear in actif/passif buckets
    for section in data.get("passif", []):
        label = section.get("label", "")
        if "regularisation" in label.lower() and "boni" in label.lower():
            # Only produits_hors_provisions may remain (3.67)
            assert abs(section.get("total", 0) - 3.67) < 0.01, \
                f"Passif regul should only contain produits_hors_provisions (3.67), got {section.get('total')}"
            for acc in section.get("accounts", []):
                assert not str(acc.get("account_number", "")).startswith("499"), \
                    f"499 must not appear after distribution, found: {acc}"
    for section in data.get("actif", []):
        label = section.get("label", "")
        if "regularisation" in label.lower():
            for acc in section.get("accounts", []):
                assert not str(acc.get("account_number", "")).startswith("499"), \
                    f"499 must not appear after distribution, found: {acc}"
    # Produits hors provisions (3.67) should still appear
    assert data.get("produits_hors_provisions", 0) == pytest.approx(3.67, abs=0.01)
    assert data.get("equilibre") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
