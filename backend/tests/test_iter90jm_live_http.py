"""iter90jm live HTTP tests via public URL: PUT/DELETE on posted statement
must return 200 (no more 409) with cascade behaviour."""
import os
import uuid
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def headers():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, r.text
    # Cookie-based auth: reuse the session and expose it as a dict-like
    class SessAdapter:
        def __init__(self, sess): self.s = sess
        def get(self, k, d=None): return None
    # Simpler: return the session's cookie header as dict
    cookies = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    return {"Cookie": cookies} if cookies else {}


@pytest.fixture(scope="module")
def acp_id(headers):
    r = requests.get(f"{BASE_URL}/api/coproprietes", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    items = r.json()
    assert items, "Need at least one copropriete"
    # Pick one that has a bank_account
    for it in items:
        if it.get("bank_accounts"):
            return it["id"], it["bank_accounts"][0].get("iban") or it["bank_accounts"][0].get("account_number")
    return items[0]["id"], "BE68539007547034"


def _create_stmt(headers, acp, iban, closing=100.0):
    body = {
        "number": f"TEST_ITER90JM_{uuid.uuid4().hex[:6]}",
        "date": "2026-03-15",
        "account_number": iban,
        "opening_balance": 0.0,
        "closing_balance": closing,
        "copropriete_id": acp,
    }
    r = requests.post(f"{BASE_URL}/api/banking/statements", json=body,
                      headers=headers, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _add_txn(headers, stmt_id, acp, amount=100.0):
    body = {
        "statement_id": stmt_id,
        "copropriete_id": acp,
        "date": "2026-03-15",
        "amount": amount,
        "counterparty_name": "iter90jm live",
        "communication": "test",
    }
    r = requests.post(f"{BASE_URL}/api/banking/transactions", json=body,
                      headers=headers, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_put_posted_statement_returns_200_not_409(headers, acp_id):
    acp, iban = acp_id
    stmt = _create_stmt(headers, acp, iban, closing=100.0)
    _add_txn(headers, stmt["id"], acp, amount=100.0)
    # Post it
    p = requests.post(f"{BASE_URL}/api/banking/statements/{stmt['id']}/post",
                      headers=headers, timeout=30)
    assert p.status_code == 200, p.text
    # PUT with new date - must succeed (no more 409)
    put_body = {
        "number": stmt["number"],
        "date": "2026-04-20",
        "account_number": iban,
        "opening_balance": 0.0,
        "closing_balance": 100.0,
        "copropriete_id": acp,
    }
    r = requests.put(f"{BASE_URL}/api/banking/statements/{stmt['id']}",
                     json=put_body, headers=headers, timeout=15)
    assert r.status_code == 200, f"PUT posted stmt must be 200 (not 409). Got {r.status_code}: {r.text}"
    j = r.json()
    assert j.get("date") == "2026-04-20"
    # Cleanup
    requests.delete(f"{BASE_URL}/api/banking/statements/{stmt['id']}",
                    headers=headers, timeout=15)


def test_delete_posted_statement_returns_200_with_cascade(headers, acp_id):
    acp, iban = acp_id
    stmt = _create_stmt(headers, acp, iban, closing=50.0)
    _add_txn(headers, stmt["id"], acp, amount=50.0)
    p = requests.post(f"{BASE_URL}/api/banking/statements/{stmt['id']}/post",
                      headers=headers, timeout=30)
    assert p.status_code == 200, p.text
    r = requests.delete(f"{BASE_URL}/api/banking/statements/{stmt['id']}",
                        headers=headers, timeout=15)
    assert r.status_code == 200, f"DELETE posted must be 200 (not 409). Got {r.status_code}: {r.text}"
    j = r.json()
    assert "txns_deleted" in j and "fi_deleted" in j, j
    assert j["txns_deleted"] >= 1
    # Verify gone
    g = requests.get(f"{BASE_URL}/api/banking/statements/{stmt['id']}",
                     headers=headers, timeout=15)
    assert g.status_code == 404
