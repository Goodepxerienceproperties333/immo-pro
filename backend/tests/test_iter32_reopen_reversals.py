"""iter32: Test reopening fiscal year creates contre-passation entries,
GET /entries filters reversed/reversal by default, include_reversals=true returns all."""
import os
import requests
import pytest

def _load_frontend_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return None

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _load_frontend_env() or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
COPRO_ID = "6748ca1a-216d-4002-8417-799287238736"
FY_ID = "eaf8b2c8-aad7-4614-ab2f-4664e04f0956"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def headers():
    return {"X-Copropriete-Id": COPRO_ID}


def _get_fy_status(session, headers):
    r = session.get(f"{BASE_URL}/api/fiscal/years",
                    params={"copropriete_id": COPRO_ID}, headers=headers)
    assert r.status_code == 200
    fy = next((f for f in r.json() if f["id"] == FY_ID), None)
    assert fy, "FY not found"
    return fy


def test_setup_open(session, headers):
    fy = _get_fy_status(session, headers)
    if fy.get("status") == "closed":
        r = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/reopen", headers=headers)
        assert r.status_code == 200
    fy = _get_fy_status(session, headers)
    assert fy["status"] == "open"


def test_reopen_open_fy_returns_400(session, headers):
    r = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/reopen", headers=headers)
    assert r.status_code == 400
    assert "cloture" in (r.json().get("detail", "")).lower()


def test_reopen_nonexistent_fy_returns_404(session, headers):
    r = session.post(f"{BASE_URL}/api/fiscal/years/nonexistent-id-xyz/reopen", headers=headers)
    assert r.status_code == 404


def test_close_then_reopen_cycle_creates_reversals(session, headers):
    r0 = session.get(f"{BASE_URL}/api/accounting/entries",
                     params={"copropriete_id": COPRO_ID}, headers=headers)
    assert r0.status_code == 200
    count_before_default = len(r0.json())

    r0all = session.get(f"{BASE_URL}/api/accounting/entries",
                        params={"copropriete_id": COPRO_ID, "include_reversals": "true"},
                        headers=headers)
    assert r0all.status_code == 200
    count_before_all = len(r0all.json())
    # Note: count_before_default may be < count_before_all if previous test
    # cycles left reversed entries on this FY (these are kept for audit).
    # We only require: default never exceeds all.
    assert count_before_default <= count_before_all

    rc = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/close", headers=headers)
    assert rc.status_code == 200, rc.text
    fy = _get_fy_status(session, headers)
    assert fy["status"] == "closed"

    r1 = session.get(f"{BASE_URL}/api/accounting/entries",
                     params={"copropriete_id": COPRO_ID}, headers=headers)
    count_after_close = len(r1.json())
    assert count_after_close > count_before_default, \
        f"close should add entries; before={count_before_default} after={count_after_close}"

    r1all = session.get(f"{BASE_URL}/api/accounting/entries",
                        params={"copropriete_id": COPRO_ID, "include_reversals": "true"},
                        headers=headers)
    closing_entries = [e for e in r1all.json()
                       if e.get("fiscal_year_id") == FY_ID
                       and (e.get("is_regularization") or e.get("journal_type") == "AN")
                       and not e.get("is_reversal")
                       and not e.get("reversed")]
    n_closing = len(closing_entries)
    assert n_closing >= 1, "expected at least 1 closing entry"

    rr = session.post(f"{BASE_URL}/api/fiscal/years/{FY_ID}/reopen", headers=headers)
    assert rr.status_code == 200, rr.text
    payload = rr.json()
    assert payload["reversed_entries"] == n_closing

    fy2 = _get_fy_status(session, headers)
    assert fy2["status"] == "open"

    r2 = session.get(f"{BASE_URL}/api/accounting/entries",
                     params={"copropriete_id": COPRO_ID}, headers=headers)
    default_entries = r2.json()
    for e in default_entries:
        assert not e.get("reversed"), f"reversed visible in default: {e.get('reference')}"
        assert not e.get("is_reversal"), f"reversal visible in default: {e.get('reference')}"
    assert len(default_entries) == count_before_default, \
        f"after reopen default count should equal initial; got {len(default_entries)} vs {count_before_default}"

    r2all = session.get(f"{BASE_URL}/api/accounting/entries",
                        params={"copropriete_id": COPRO_ID, "include_reversals": "true"},
                        headers=headers)
    all_entries = r2all.json()
    reversed_originals = [e for e in all_entries if e.get("reversed") and e.get("fiscal_year_id") == FY_ID]
    reversal_entries = [e for e in all_entries if e.get("is_reversal") and e.get("fiscal_year_id") == FY_ID]
    # There may be leftover reversed/reversal entries from prior test cycles.
    assert len(reversed_originals) >= n_closing
    assert len(reversal_entries) >= n_closing

    rev = reversal_entries[0]
    assert rev.get("reverses_entry_id")
    assert rev["reference"].startswith("EXT-"), f"reversal ref should start EXT-: {rev['reference']}"
    orig = next((e for e in all_entries if e.get("id") == rev["reverses_entry_id"]), None)
    assert orig
    assert orig.get("reversed") is True
    assert orig.get("reversed_by_entry_id") == rev["id"]
    assert abs(float(orig["total_debit"]) - float(rev["total_credit"])) < 0.01
    assert abs(float(orig["total_credit"]) - float(rev["total_debit"])) < 0.01

    fy_final = _get_fy_status(session, headers)
    assert fy_final["status"] == "open"
