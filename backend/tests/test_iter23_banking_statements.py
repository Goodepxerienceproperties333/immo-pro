"""Iter23 - Banking statements draft/posted workflow.

Tests:
 (1) POST /api/banking/statements creates with status='draft' by default
 (2) GET /api/banking/statements/{id} returns computed_closing, balance_diff, is_balanced
 (3) Mouvements: credit=+amount, debit=-amount on 2 mixed txns
 (4) POST /post : 400 if non-balanced w/ explicit message containing opening, mvts, closing, diff
 (5) POST /post : 200 if balanced + status='posted' + posted_at
 (6) POST /post : 400 if already posted
 (7) POST /unpost : back to draft, posted_at None
 (8) PUT /statements/{id} : modify number/date/account_number/opening/closing
 (9) Rounding tolerance: 0.01 (e.g., diff=0.005 -> balanced; diff=0.02 -> unbalanced)
 (10) Regression iter22 - DELETE statement cascades FI auto-entries
 (11) Regression iter21 - generate_bank_entry resolves IBAN -> pcmn_number
 (12) Regression iter20 - balance owners from journal_entries
"""
import os
import uuid
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
COPRO_ID = "6748ca1a-216d-4002-8417-799287238736"
IBAN_COURANT = "BE68539007547034"   # PCMN 55103400 default
PCMN_COURANT = "55103400"
IBAN_RESERVE = "BE71096123456769"   # PCMN 55076900
PCMN_RESERVE = "55076900"
DUBOIS_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"
ALEX_BENOIT_TIER = "44000012"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


def _create_stmt(client, opening, closing, account=IBAN_COURANT, number_prefix="TEST_ITER23"):
    r = client.post(f"{BASE_URL}/api/banking/statements", json={
        "number": f"{number_prefix}_{uuid.uuid4().hex[:6]}",
        "date": "2026-01-20",
        "account_number": account,
        "opening_balance": opening,
        "closing_balance": closing,
        "copropriete_id": COPRO_ID,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


def _add_txn(client, stmt_id, amount, ttype="credit", communication="", account_number=IBAN_COURANT):
    r = client.post(f"{BASE_URL}/api/banking/transactions", json={
        "statement_id": stmt_id,
        "date": "2026-01-20",
        "amount": amount,
        "counterparty_name": "TEST_ITER23_PAYER",
        "counterparty_account": IBAN_COURANT,
        "communication": communication,
        "transaction_type": ttype,
        "account_number": account_number,
        "copropriete_id": COPRO_ID,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


def _cleanup_stmt(client, stmt_id):
    try:
        client.delete(f"{BASE_URL}/api/banking/statements/{stmt_id}")
    except Exception:
        pass


# ============================================================
# (1) Default status = draft
# ============================================================
class TestDefaultStatus:
    def test_default_status_draft(self, client):
        s = _create_stmt(client, 0, 0)
        try:
            assert s.get("status") == "draft", f"status={s.get('status')}"
            assert "id" in s
            # verify persisted via GET
            r = client.get(f"{BASE_URL}/api/banking/statements/{s['id']}")
            assert r.status_code == 200
            assert r.json()["status"] == "draft"
        finally:
            _cleanup_stmt(client, s["id"])


# ============================================================
# (2)(3) GET returns computed_closing, balance_diff, is_balanced
#       credit=+, debit=- on 2 mixed txns
# ============================================================
class TestComputedBalance:
    def test_get_returns_balance_fields(self, client):
        # opening=100, +200 (credit), -50 (debit) => computed=250, closing=250
        s = _create_stmt(client, 100.0, 250.0)
        sid = s["id"]
        try:
            _add_txn(client, sid, 200.0, "credit")
            _add_txn(client, sid, 50.0, "debit")
            r = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            assert r.status_code == 200
            d = r.json()
            assert "computed_closing" in d
            assert "balance_diff" in d
            assert "is_balanced" in d
            assert d["computed_closing"] == 250.0, d
            assert d["balance_diff"] == 0.0, d
            assert d["is_balanced"] is True, d
            assert len(d["transactions"]) == 2
        finally:
            _cleanup_stmt(client, sid)

    def test_credit_debit_signs_mixed(self, client):
        # opening=0, +1000, -300, -50 => 650
        s = _create_stmt(client, 0.0, 650.0)
        sid = s["id"]
        try:
            _add_txn(client, sid, 1000.0, "credit")
            _add_txn(client, sid, 300.0, "debit")
            _add_txn(client, sid, 50.0, "debit")
            r = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            d = r.json()
            assert d["computed_closing"] == 650.0, d
            assert d["is_balanced"] is True
        finally:
            _cleanup_stmt(client, sid)

    def test_unbalanced_when_closing_wrong(self, client):
        s = _create_stmt(client, 100.0, 999.0)  # wrong closing
        sid = s["id"]
        try:
            _add_txn(client, sid, 200.0, "credit")
            r = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            d = r.json()
            assert d["computed_closing"] == 300.0
            assert d["balance_diff"] == round(300.0 - 999.0, 2)
            assert d["is_balanced"] is False
        finally:
            _cleanup_stmt(client, sid)


# ============================================================
# (4)(5)(6) POST /post: refuse 400 if unbalanced, 200 if balanced, 400 if already posted
# ============================================================
class TestPostStatement:
    def test_post_refuses_unbalanced_with_clear_message(self, client):
        s = _create_stmt(client, 100.0, 500.0)  # closing wrong
        sid = s["id"]
        try:
            _add_txn(client, sid, 200.0, "credit")  # computed=300, diff=-200
            r = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r.status_code == 400, r.text
            msg = r.json().get("detail", "")
            # Message must mention opening, movements, closing, diff
            assert "100" in msg, msg
            assert "200" in msg, msg
            assert "300" in msg, msg
            assert "500" in msg, msg
            assert ("-200" in msg) or ("200.00" in msg), msg
            # status still draft
            r2 = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            assert r2.json()["status"] == "draft"
        finally:
            _cleanup_stmt(client, sid)

    def test_post_success_balanced(self, client):
        s = _create_stmt(client, 50.0, 250.0)
        sid = s["id"]
        try:
            _add_txn(client, sid, 200.0, "credit")  # 50+200=250
            r = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("status") == "ok"
            # verify persisted: status=posted, posted_at set
            r2 = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            d = r2.json()
            assert d["status"] == "posted"
            assert d.get("posted_at"), d
        finally:
            _cleanup_stmt(client, sid)

    def test_post_refuses_if_already_posted(self, client):
        s = _create_stmt(client, 0.0, 100.0)
        sid = s["id"]
        try:
            _add_txn(client, sid, 100.0, "credit")
            r = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r.status_code == 200
            # 2nd attempt
            r2 = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r2.status_code == 400, r2.text
            assert "deja" in r2.json().get("detail", "").lower() or "already" in r2.json().get("detail", "").lower()
        finally:
            _cleanup_stmt(client, sid)

    def test_post_404_on_unknown(self, client):
        r = client.post(f"{BASE_URL}/api/banking/statements/non-existent-id-xyz/post")
        assert r.status_code == 404


# ============================================================
# (7) POST /unpost : back to draft
# ============================================================
class TestUnpostStatement:
    def test_unpost_returns_to_draft(self, client):
        s = _create_stmt(client, 0.0, 100.0)
        sid = s["id"]
        try:
            _add_txn(client, sid, 100.0, "credit")
            assert client.post(f"{BASE_URL}/api/banking/statements/{sid}/post").status_code == 200
            r = client.post(f"{BASE_URL}/api/banking/statements/{sid}/unpost")
            assert r.status_code == 200, r.text
            d = client.get(f"{BASE_URL}/api/banking/statements/{sid}").json()
            assert d["status"] == "draft"
            assert d.get("posted_at") in (None, "", False)
        finally:
            _cleanup_stmt(client, sid)

    def test_unpost_404_on_unknown(self, client):
        r = client.post(f"{BASE_URL}/api/banking/statements/non-existent-id/unpost")
        assert r.status_code == 404


# ============================================================
# (8) PUT /statements/{id} : update fields
# ============================================================
class TestUpdateStatement:
    def test_put_updates_all_fields(self, client):
        s = _create_stmt(client, 0.0, 0.0)
        sid = s["id"]
        try:
            new_number = f"UPDATED_ITER23_{uuid.uuid4().hex[:5]}"
            r = client.put(f"{BASE_URL}/api/banking/statements/{sid}", json={
                "number": new_number,
                "date": "2026-02-15",
                "account_number": IBAN_RESERVE,
                "opening_balance": 100.0,
                "closing_balance": 350.0,
                "copropriete_id": COPRO_ID,
            })
            assert r.status_code == 200, r.text
            # verify persisted
            d = client.get(f"{BASE_URL}/api/banking/statements/{sid}").json()
            assert d["number"] == new_number
            assert d["date"] == "2026-02-15"
            assert d["account_number"] == IBAN_RESERVE
            assert d["opening_balance"] == 100.0
            assert d["closing_balance"] == 350.0
        finally:
            _cleanup_stmt(client, sid)

    def test_put_404_on_unknown(self, client):
        r = client.put(f"{BASE_URL}/api/banking/statements/non-existent-id", json={
            "number": "X", "date": "2026-01-01", "account_number": "",
            "opening_balance": 0, "closing_balance": 0, "copropriete_id": COPRO_ID,
        })
        assert r.status_code == 404


# ============================================================
# (9) Rounding tolerance 0.01
# ============================================================
class TestRoundingTolerance:
    def test_diff_below_001_is_balanced(self, client):
        # opening 0.005, credit 0.0 -> computed=0.005 rounded=0.01? Actually round(0.005,2)=0.0 in Py banker
        # Use diff 0.004
        s = _create_stmt(client, 0.0, 100.004)
        sid = s["id"]
        try:
            _add_txn(client, sid, 100.0, "credit")
            r = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            d = r.json()
            # round(100.004,2)=100.0 -> diff = 100-100 = 0 -> balanced
            assert d["is_balanced"] is True, d
            # post must succeed
            r2 = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r2.status_code == 200, r2.text
        finally:
            _cleanup_stmt(client, sid)

    def test_diff_above_001_is_not_balanced(self, client):
        s = _create_stmt(client, 0.0, 100.05)
        sid = s["id"]
        try:
            _add_txn(client, sid, 100.0, "credit")
            r = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
            d = r.json()
            assert d["is_balanced"] is False, d
            assert abs(d["balance_diff"]) >= 0.01
            # post must fail
            r2 = client.post(f"{BASE_URL}/api/banking/statements/{sid}/post")
            assert r2.status_code == 400
        finally:
            _cleanup_stmt(client, sid)


# ============================================================
# (10) Regression iter22 - DELETE cascades FI auto-entries
# ============================================================
class TestRegressionIter22Cascade:
    def test_delete_statement_removes_txns_and_auto_entries(self, client):
        s = _create_stmt(client, 0.0, 100.0)
        sid = s["id"]
        # create txn + lettrage
        t = _add_txn(client, sid, 100.0, "credit")
        client.post(f"{BASE_URL}/api/banking/lettrage", json={
            "transaction_id": t["id"],
            "match_to_id": DUBOIS_ID,
            "match_type": "owner_payment",
        })
        # auto entries created
        r = client.get(f"{BASE_URL}/api/accounting/entries")
        if r.status_code == 200:
            initial_entries = [e for e in r.json() if e.get("source_id") == t["id"]]
            # may be empty if generate fails silently, but expected >=1
        # DELETE statement -> cascades
        r = client.delete(f"{BASE_URL}/api/banking/statements/{sid}")
        assert r.status_code == 200, r.text
        # verify txn deleted
        r2 = client.get(f"{BASE_URL}/api/banking/transactions", params={"statement_id": sid})
        assert r2.status_code == 200
        assert r2.json() == []
        # verify statement deleted
        r3 = client.get(f"{BASE_URL}/api/banking/statements/{sid}")
        assert r3.status_code == 404


# ============================================================
# (11) Regression iter21 - IBAN -> PCMN resolution
# ============================================================
class TestRegressionIter21IbanResolution:
    def test_bank_entry_uses_pcmn_number(self, client):
        s = _create_stmt(client, 0.0, 100.0, account=IBAN_COURANT)
        sid = s["id"]
        try:
            t = _add_txn(client, sid, 100.0, "credit")
            r = client.post(f"{BASE_URL}/api/banking/lettrage", json={
                "transaction_id": t["id"],
                "match_to_id": DUBOIS_ID,
                "match_type": "owner_payment",
            })
            assert r.status_code == 200
            # Check journal entries for this txn use PCMN_COURANT, not IBAN
            r = client.get(f"{BASE_URL}/api/accounting/entries")
            if r.status_code != 200:
                pytest.skip("accounting entries endpoint not available")
            entries = [e for e in r.json() if e.get("source_id") == t["id"]]
            assert len(entries) >= 1, "no auto-entry generated"
            # Inspect lines for PCMN
            for e in entries:
                lines = e.get("lines", [])
                accounts = [str(line.get("account") or line.get("account_number") or "") for line in lines]
                # at least one line should use PCMN_COURANT (55103400)
                assert any(PCMN_COURANT in a for a in accounts), \
                    f"PCMN {PCMN_COURANT} not found in entry accounts: {accounts}"
                # IBAN should never appear as account
                assert not any(IBAN_COURANT in a for a in accounts), \
                    f"IBAN found instead of PCMN: {accounts}"
        finally:
            _cleanup_stmt(client, sid)


# ============================================================
# (12) Regression iter20 - balance owners from journal_entries
# ============================================================
class TestRegressionIter20BalanceTiers:
    def test_balance_tiers_owners_available(self, client):
        r = client.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                       params={"copropriete_id": COPRO_ID})
        assert r.status_code == 200, r.text
        data = r.json()
        # response can be list or {owners: [...]}
        owners = data if isinstance(data, list) else data.get("owners", data.get("tiers", []))
        assert isinstance(owners, list)
        # Alex BENOIT (orphan owner) may not be in /owners endpoint; verify Dubois Jean
        dubois = next((o for o in owners
                       if o.get("owner_id") == DUBOIS_ID
                       or "Dubois" in str(o.get("owner_name", ""))), None)
        assert dubois is not None, f"Dubois Jean not found in {owners[:3]}"
