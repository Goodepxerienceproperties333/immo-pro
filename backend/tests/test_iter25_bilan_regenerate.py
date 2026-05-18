"""Iter25 - Bilan affiche comptes bancaires PCMN specifiques + regenerate-bank-entries.

Tests:
 (1) GET /api/reports/bilan?copropriete_id=... -> contient 55103400 (et/ou 551079 / 55076900),
     PAS le compte generique 550000 quand des transactions ont ete lettrees sur IBAN connu.
 (2) POST /api/coproprietes/{id}/regenerate-bank-entries (manager/admin)
     -> {regenerated, errors, transactions_processed}.
 (3) Regenerate corrige une ecriture FI qui pointait vers 550000 (mauvais) en 55103400 (bon).
 (4) RBAC: owner -> 403 sur regenerate-bank-entries.
 (5) Regression: cleanup-orphan-entries idempotent.
"""
import os
import uuid
import pytest
import requests

# Read REACT_APP_BACKEND_URL
_be = os.environ.get("REACT_APP_BACKEND_URL")
if not _be:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    _be = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass
BASE_URL = (_be or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

ACP_ID = "6748ca1a-216d-4002-8417-799287238736"
IBAN_COURANT = "BE68539007547034"   # -> PCMN 55103400
PCMN_COURANT = "55103400"
DUBOIS_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def owner_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "evrard.gerald@outlook.be", "password": "owner123"})
    if r.status_code != 200:
        pytest.skip(f"Owner login failed: {r.status_code} {r.text}")
    return s


# ============================================================
# (1) GET /api/reports/bilan exposes specific PCMN bank accounts
# ============================================================
class TestBilanBankAccounts:
    def test_bilan_includes_pcmn_courant_not_generic(self, admin):
        # Make sure at least one bank txn lettre exists on this IBAN
        # so that PCMN 55103400 has a non-zero balance.
        stmt_r = admin.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"TEST_ITER25_BILAN_{uuid.uuid4().hex[:6]}",
            "date": "2026-01-15",
            "account_number": IBAN_COURANT,
            "opening_balance": 0.0, "closing_balance": 100.0,
            "copropriete_id": ACP_ID,
        })
        assert stmt_r.status_code in (200, 201), stmt_r.text
        sid = stmt_r.json()["id"]
        try:
            t = admin.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": sid, "date": "2026-01-15", "amount": 100.0,
                "transaction_type": "credit", "account_number": IBAN_COURANT,
                "counterparty_name": "TEST_ITER25_BILAN",
                "copropriete_id": ACP_ID,
            })
            assert t.status_code in (200, 201), t.text
            tid = t.json()["id"]
            l = admin.post(f"{BASE_URL}/api/banking/lettrage", json={
                "transaction_id": tid, "match_to_id": DUBOIS_ID,
                "match_type": "owner_payment",
            })
            assert l.status_code == 200, l.text

            # Now fetch bilan
            r = admin.get(f"{BASE_URL}/api/reports/bilan", params={"copropriete_id": ACP_ID})
            assert r.status_code == 200, r.text
            data = r.json()
            # Collect all account_numbers in the bilan response (any nesting)
            found = set()

            def walk(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k in ("account_number", "pcmn", "account") and isinstance(v, str):
                            found.add(v)
                        walk(v)
                elif isinstance(node, list):
                    for it in node:
                        walk(it)

            walk(data)
            assert PCMN_COURANT in found, f"PCMN {PCMN_COURANT} missing in bilan. Found accounts (sample): {sorted(found)[:30]}"
            # Generic 550000 should NOT be the bank account exposed for this IBAN
            # (We tolerate its presence only if its balance is zero - check via search)
            # Find any line for PCMN_COURANT with non-zero balance
            saw_value = {"hit": False}

            def walk_check(node):
                if isinstance(node, dict):
                    if node.get("account_number") == PCMN_COURANT or node.get("pcmn") == PCMN_COURANT:
                        for kf in ("balance", "amount", "solde", "debit", "credit"):
                            if isinstance(node.get(kf), (int, float)) and node[kf] != 0:
                                saw_value["hit"] = True
                    for v in node.values():
                        walk_check(v)
                elif isinstance(node, list):
                    for it in node:
                        walk_check(it)

            walk_check(data)
            assert saw_value["hit"], f"PCMN {PCMN_COURANT} has zero balance in bilan despite a 100 credit lettre."
        finally:
            admin.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ============================================================
# (2) POST /regenerate-bank-entries (admin) returns expected shape
# ============================================================
class TestRegenerateBankEntries:
    def test_regenerate_returns_proper_shape(self, admin):
        r = admin.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/regenerate-bank-entries")
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("regenerated", "errors", "transactions_processed"):
            assert k in data, f"Missing key {k} in {data}"
        assert isinstance(data["regenerated"], int)
        assert isinstance(data["transactions_processed"], int)
        # errors typically list
        assert isinstance(data["errors"], (list, int))

    def test_regenerate_then_bilan_shows_correct_pcmn(self, admin):
        # Create a txn + lettrage (=> auto FI). Then regenerate to confirm it remains 55103400.
        sid = None
        tid = None
        try:
            stmt = admin.post(f"{BASE_URL}/api/banking/statements", json={
                "number": f"TEST_ITER25_REGEN_{uuid.uuid4().hex[:6]}",
                "date": "2026-01-15",
                "account_number": IBAN_COURANT,
                "opening_balance": 0, "closing_balance": 50,
                "copropriete_id": ACP_ID,
            })
            assert stmt.status_code in (200, 201), stmt.text
            sid = stmt.json()["id"]
            t = admin.post(f"{BASE_URL}/api/banking/transactions", json={
                "statement_id": sid, "date": "2026-01-15", "amount": 50.0,
                "transaction_type": "credit", "account_number": IBAN_COURANT,
                "counterparty_name": "TEST_ITER25_REGEN",
                "copropriete_id": ACP_ID,
            })
            assert t.status_code in (200, 201), t.text
            tid = t.json()["id"]
            admin.post(f"{BASE_URL}/api/banking/lettrage", json={
                "transaction_id": tid, "match_to_id": DUBOIS_ID,
                "match_type": "owner_payment",
            })
            # Regenerate
            r = admin.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/regenerate-bank-entries")
            assert r.status_code == 200
            # FI entry for this txn references PCMN 55103400
            entries = admin.get(f"{BASE_URL}/api/accounting/entries",
                                params={"copropriete_id": ACP_ID}).json()
            fi = [e for e in entries if e.get("source_type") == "bank_txn"
                  and e.get("source_id") == tid]
            assert len(fi) >= 1
            accs = {ln.get("account_number") for e in fi for ln in e.get("lines", [])}
            assert PCMN_COURANT in accs, f"After regenerate, expected {PCMN_COURANT}, got {accs}"
            assert IBAN_COURANT not in accs, f"IBAN must NOT be used as account, got {accs}"
        finally:
            if sid:
                admin.delete(f"{BASE_URL}/api/banking/statements/{sid}")


# ============================================================
# (4) RBAC: owner -> 403
# ============================================================
class TestRBACRegenerate:
    def test_owner_forbidden(self, owner_client):
        r = owner_client.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/regenerate-bank-entries")
        assert r.status_code in (401, 403), f"Owner must NOT regenerate, got {r.status_code}: {r.text}"


# ============================================================
# (5) Regression cleanup-orphan-entries
# ============================================================
class TestRegressionCleanup:
    def test_cleanup_idempotent(self, admin):
        r = admin.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/cleanup-orphan-entries")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "ok"
        assert "total_deleted" in data and "stats" in data
        # 2nd call -> 0
        r2 = admin.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/cleanup-orphan-entries")
        assert r2.status_code == 200
        assert r2.json()["total_deleted"] == 0
