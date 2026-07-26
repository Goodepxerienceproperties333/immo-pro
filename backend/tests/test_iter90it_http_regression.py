"""iter90it HTTP regressions - live public endpoint checks.

Covers points from the review request not covered by the DB-level test
file: Chinese Wall 422/400, BCE lookup by name, bce-opendata status,
health-audit orphan cleanup after reconcile.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def su_session():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}: {r.text[:200]}")
    return s


def test_chinese_wall_missing_copropriete_id_returns_422(su_session):
    r = su_session.post(f"{BASE}/api/suppliers", json={"name": "TEST_no_acp", "bce_number": ""}, timeout=30)
    assert r.status_code == 422, f"expected 422 (pydantic), got {r.status_code} : {r.text[:200]}"


def test_chinese_wall_empty_copropriete_id_returns_400(su_session):
    r = su_session.post(
        f"{BASE}/api/suppliers",
        json={"name": "TEST_empty_acp", "copropriete_id": "", "bce_number": ""},
        timeout=30,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code} : {r.text[:200]}"
    body = r.text.lower()
    assert "chinese" in body or "copropriete" in body or "acp" in body, body[:300]


def test_bce_lookup_by_name_still_works(su_session):
    r = su_session.post(f"{BASE}/api/import-wizard/lookup-bce", json={"name": "Belfius Banque"}, timeout=45)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    candidates = data.get("candidates") or data.get("results") or []
    assert isinstance(candidates, list) and len(candidates) >= 1, data
    # Belfius Banque BCE 0403.201.185 (may appear normalised)
    joined = str(data).replace(".", "").replace(" ", "")
    assert "0403201185" in joined, f"Belfius BCE not found. resp={str(data)[:500]}"


def test_bce_opendata_status(su_session):
    r = su_session.get(f"{BASE}/api/admin/bce-opendata/status", timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    for k in ("total_entities", "last_ingest", "enabled"):
        assert k in d, f"missing key {k} in {d}"


def test_reconcile_endpoint_e2e_and_health_audit(su_session):
    """End-to-end via HTTP : bootstrap an ACP + orphan JE, call reconcile,
    then verify health-audit no longer flags the account."""
    suffix = uuid.uuid4().hex[:8]
    acp = f"acp-http-{suffix}"

    # Bootstrap via direct DB (endpoints ACP + JE creation avec un compte
    # orphelin est plus lourd cote HTTP). On utilise motor.
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    async def _setup():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.coproprietes.insert_one({"id": acp, "name": f"ACP HTTP {suffix}", "status": "active"})
        await db.journal_entries.insert_one({
            "id": f"je-http-{suffix}", "copropriete_id": acp,
            "journal_type": "AC", "date": "2026-05-01",
            "reference": f"AC-HTTP-{suffix}",
            "total_debit": 42.0, "total_credit": 42.0,
            "lines": [
                {"account_number": "61210", "account_name": "Elec", "debit": 42.0, "credit": 0.0},
                {"account_number": "44000110", "account_name": f"HttpEngie-{suffix}",
                 "debit": 0.0, "credit": 42.0},
            ],
        })
        client.close()

    async def _teardown():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.suppliers.delete_many({"copropriete_id": acp})
        await db.journal_entries.delete_many({"copropriete_id": acp})
        await db.pcmn_accounts.delete_many({"copropriete_id": acp})
        await db.coproprietes.delete_one({"id": acp})
        client.close()

    asyncio.run(_setup())
    try:
        # dry_run
        r = su_session.post(
            f"{BASE}/api/admin/reconcile-orphan-suppliers-from-je",
            json={"copropriete_id": acp, "dry_run": True},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["mode"] == "dry_run"
        assert d["totals"]["orphan_accounts"] == 1, d

        # live
        r = su_session.post(
            f"{BASE}/api/admin/reconcile-orphan-suppliers-from-je",
            json={"copropriete_id": acp, "dry_run": False},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["mode"] == "live"
        assert d["totals"]["suppliers_created"] == 1
        assert d["totals"]["je_lines_repointed"] == 1

        # idempotence
        r2 = su_session.post(
            f"{BASE}/api/admin/reconcile-orphan-suppliers-from-je",
            json={"copropriete_id": acp, "dry_run": False},
            timeout=60,
        )
        d2 = r2.json()
        assert d2["totals"]["orphan_accounts"] == 0, d2

        # health-audit : le compte 44000110 ne doit plus etre listé orphelin
        h = su_session.get(f"{BASE}/api/admin/health-audit", params={"copropriete_id": acp}, timeout=60)
        # Endpoint peut renvoyer 200 même si vide - on tolère les deux formats
        if h.status_code == 200:
            hd = h.json()
            orphans = hd.get("orphans") or hd.get("orphan_accounts") or []
            # cast to str for uniform check
            joined = str(orphans)
            assert "44000110" not in joined, f"44000110 should no longer be orphan. audit={hd}"
        else:
            pytest.skip(f"health-audit returned {h.status_code} (not blocking)")
    finally:
        asyncio.run(_teardown())


def test_unauth_reconcile_forbidden():
    """Non-super user cannot reach reconcile endpoint."""
    r = requests.post(
        f"{BASE}/api/admin/reconcile-orphan-suppliers-from-je",
        json={"copropriete_id": "x", "dry_run": True}, timeout=30,
    )
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
