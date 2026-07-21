"""Tests for iter90k: import wizard preview endpoint + supplier isolation."""
import os
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ACP_ID = "138cfd69-cc07-46bb-ad17-a98debee7a3a"
SESSION_ID = "1dc39652-e7f1-4599-8237-16620e16b940"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


def test_preview_invoices_returns_control_table(client):
    payload = {
        "invoices": [
            {
                "supplier_aux_code": "F0001",
                "supplier_name": "SRL Finlead",
                "account_number": "61300",
                "montant_tvac": 753.99,
                "date": "2026-03-01",
                "external_ref": "260081",
            }
        ]
    }
    r = client.post(f"{BASE}/api/import-wizard/sessions/{SESSION_ID}/preview-invoices",
                    json=payload, timeout=30)
    assert r.status_code == 200, f"preview failed: {r.status_code} {r.text}"
    body = r.json()
    assert "preview" in body
    assert body["count"] == 1
    row = body["preview"][0]
    # Required control-table columns
    for col in ("index", "supplier_name", "supplier_aux_code",
                "supplier_vat", "account_number", "montant_tvac", "status"):
        assert col in row, f"missing column {col} in preview row: {row}"
    assert row["index"] == 0
    assert row["supplier_aux_code"] == "F0001"
    assert row["supplier_name"] == "SRL Finlead"
    assert row["account_number"] == "61300"
    assert abs(row["montant_tvac"] - 753.99) < 1e-6
    assert row["status"] in ("matched", "to_create")


def test_preview_multiple_rows_independent_resolution(client):
    payload = {
        "invoices": [
            {"supplier_aux_code": "F0001", "supplier_name": "SRL Finlead",
             "account_number": "61300", "montant_tvac": 100.0,
             "date": "2026-03-01", "external_ref": "R1"},
            {"supplier_aux_code": "F9999", "supplier_name": "TEST_NewSupplier_ZZ",
             "account_number": "61300", "montant_tvac": 50.0,
             "date": "2026-03-01", "external_ref": "R2"},
        ]
    }
    r = client.post(f"{BASE}/api/import-wizard/sessions/{SESSION_ID}/preview-invoices",
                    json=payload, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert body["matched"] + body["to_create"] == 2
    # F9999 should not exist -> to_create
    row_f9999 = next(r for r in body["preview"] if r["supplier_aux_code"] == "F9999")
    assert row_f9999["status"] == "to_create"
    assert row_f9999["supplier_vat"] == ""


def test_preview_unknown_session_returns_404(client):
    r = client.post(f"{BASE}/api/import-wizard/sessions/does-not-exist/preview-invoices",
                    json={"invoices": []}, timeout=15)
    assert r.status_code == 404


def test_suppliers_scoped_to_acp_chinese_wall(client):
    """Verify all suppliers currently in the ACP are scoped correctly."""
    r = client.get(f"{BASE}/api/suppliers?copropriete_id={ACP_ID}", timeout=20)
    assert r.status_code == 200, r.text
    sups = r.json()
    for s in sups:
        assert s.get("copropriete_id") == ACP_ID, \
            f"Supplier {s.get('name')} has wrong copropriete_id: {s.get('copropriete_id')}"


def test_bilan_regression_499_and_equilibre(client):
    """Regression: after commit, bilan must remain balanced and 499 = 1170.16."""
    r = client.get(f"{BASE}/api/reports/bilan?copropriete_id={ACP_ID}", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("equilibre") is True, f"Bilan desequilibre: {body.get('ecart')}"
    assert abs(body["total_actif"] - body["total_passif"]) < 0.01
    assert abs(body.get("compte_499", 0) - 1170.16) < 0.01, \
        f"499 attendu 1170.16, obtenu {body.get('compte_499')}"
