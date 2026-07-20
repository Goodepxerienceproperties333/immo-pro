"""iter90iu HTTP regression via public REACT_APP_BACKEND_URL.

- POST /api/suppliers without bce_number -> must still succeed (iter90it).
- POST /api/suppliers without copropriete_id -> must return 422 (Chinese Wall).
- POST /api/suppliers ok -> supplier persisted with tier_account_number (8 chars)
  AND legacy tier_accounts[copro].main filled (iter90iu-1 compat write).
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def acp_id(session):
    """Reuse an existing ACP if any (Chinese Wall superadmin scope)."""
    r = session.get(f"{BASE}/api/coproprietes")
    assert r.status_code == 200, r.text[:200]
    items = r.json()
    assert isinstance(items, list) and len(items) > 0, "No ACP available on preview to run supplier tests"
    return items[0]["id"]


def test_supplier_create_without_bce_succeeds(session, acp_id):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "name": f"TEST_iu_nobce_{suffix}",
        "copropriete_id": acp_id,
        "bce_number": "",
    }
    r = session.post(f"{BASE}/api/suppliers", json=payload)
    assert r.status_code in (200, 201), f"expected 2xx got {r.status_code}: {r.text[:200]}"
    data = r.json()
    sup_id = data.get("id")
    try:
        assert data.get("copropriete_id") == acp_id
        # iter90iu-1 : field plat rempli
        tan = data.get("tier_account_number") or ""
        assert tan.startswith("44000") and len(tan) == 8, f"tier_account_number malformed: {tan!r}"
        # iter90iu-1 : dict legacy egalement rempli
        legacy = data.get("tier_accounts") or {}
        assert acp_id in legacy, f"legacy tier_accounts missing for acp: {legacy}"
        assert legacy[acp_id].get("main") == tan, f"legacy main != flat: {legacy[acp_id]} vs {tan}"
    finally:
        if sup_id:
            session.delete(f"{BASE}/api/suppliers/{sup_id}")


def test_supplier_create_without_copropriete_returns_422(session):
    suffix = uuid.uuid4().hex[:8]
    payload = {"name": f"TEST_iu_noacp_{suffix}", "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}"}
    r = session.post(f"{BASE}/api/suppliers", json=payload)
    assert r.status_code == 422, f"expected 422 got {r.status_code}: {r.text[:300]}"
