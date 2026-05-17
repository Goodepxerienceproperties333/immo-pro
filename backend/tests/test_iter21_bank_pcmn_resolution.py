"""Iter21 - generate_bank_entry resolves IBAN -> pcmn_number via copro.bank_accounts.

Tests:
 - POST /api/banking/transactions + POST /api/banking/lettrage with known IBAN
   produces a FI entry whose lines use pcmn_number (55103400 / 55076900), NOT the IBAN.
 - account_name is the PCMN name (not generic "Banque").
 - Unknown IBAN -> fallback 550000.
 - No IBAN at all -> fallback 550000.
 - Credit direction (owner_payment): Dr bank / Cr 40000XXX owner.
 - Debit direction (supplier_payment): Dr 44000XXX / Cr bank.
 - Regression iter20 : balance-tiers/owners returns expected fields.
"""
import os
import time
import uuid
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
COPRO_ID = "6748ca1a-216d-4002-8417-799287238736"
IBAN_COURANT = "BE68539007547034"          # -> 55103400
IBAN_EPARGNE = "BE71096123456769"          # -> 55076900
PCMN_COURANT = "55103400"
PCMN_EPARGNE = "55076900"
PCMN_FALLBACK = "550000"
DUBOIS_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"
DUBOIS_PROV = "40000002"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="session")
def supplier_id(client):
    """Get-or-create a TEST supplier for supplier_payment lettrage."""
    name = f"TEST_ITER21_FOURN_{uuid.uuid4().hex[:6]}"
    r = client.post(f"{BASE_URL}/api/suppliers",
                    json={"name": name, "copropriete_id": COPRO_ID})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    yield sid, name
    # cleanup
    client.delete(f"{BASE_URL}/api/suppliers/{sid}")


def _create_txn(client, *, iban, amount, txn_type="credit", communication=""):
    payload = {
        "statement_id": "",
        "date": "2026-01-15",
        "amount": amount,
        "counterparty_name": "TEST_ITER21",
        "communication": communication or f"TEST_ITER21_{uuid.uuid4().hex[:6]}",
        "transaction_type": txn_type,
        "account_number": iban,
        "copropriete_id": COPRO_ID,
    }
    r = client.post(f"{BASE_URL}/api/banking/transactions", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _do_lettrage(client, txn_id, match_to_id, match_type):
    r = client.post(f"{BASE_URL}/api/banking/lettrage", json={
        "transaction_id": txn_id,
        "match_to_id": match_to_id,
        "match_type": match_type,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


def _fetch_fi_entry(client, txn_id, retries=5):
    """Find the auto FI journal_entry for this bank txn."""
    for _ in range(retries):
        r = client.get(f"{BASE_URL}/api/accounting/entries",
                       params={"copropriete_id": COPRO_ID})
        if r.status_code == 200:
            for e in r.json():
                if e.get("source_type") == "bank_txn" and e.get("source_id") == txn_id:
                    return e
        time.sleep(0.3)
    return None


# ---- Cleanup fixture ----
@pytest.fixture(autouse=True)
def cleanup_after():
    yield
    # Remove TEST_ITER21 txns + their auto FI entries
    try:
        from pymongo import MongoClient
        m = MongoClient(os.environ["MONGO_URL"])
        d = m[os.environ["DB_NAME"]]
        ids = [t["id"] for t in d.bank_transactions.find(
            {"counterparty_name": "TEST_ITER21"}, {"id": 1})]
        if ids:
            d.bank_transactions.delete_many({"id": {"$in": ids}})
            d.journal_entries.delete_many({"source_type": "bank_txn",
                                           "source_id": {"$in": ids}})
        m.close()
    except Exception as e:
        print(f"[cleanup] {e}")


# ============================================================
# CORE BUG FIX TESTS (iter21)
# ============================================================
class TestIter21BankPcmnResolution:

    def test_credit_known_iban_courant_uses_pcmn_55103400(self, client):
        txn = _create_txn(client, iban=IBAN_COURANT, amount=250.0, txn_type="credit")
        _do_lettrage(client, txn["id"], DUBOIS_ID, "owner_payment")
        e = _fetch_fi_entry(client, txn["id"])
        assert e is not None, "FI entry not generated"
        assert e["journal_type"] == "FI"
        lines = e["lines"]
        assert len(lines) == 2
        # Dr bank / Cr owner
        dr = next(l for l in lines if l["debit"] > 0)
        cr = next(l for l in lines if l["credit"] > 0)
        # Bug fix : bank line must be pcmn_number 55103400, NOT IBAN
        assert dr["account_number"] == PCMN_COURANT, \
            f"Expected {PCMN_COURANT}, got {dr['account_number']}"
        assert not dr["account_number"].startswith("BE")
        assert len(dr["account_number"]) <= 8
        assert dr["account_number"].isdigit()
        # PCMN name must be the real PCMN name, not generic "Banque"
        assert "7034" in (dr["account_name"] or "") or "courant" in (dr["account_name"] or "").lower()
        assert dr["account_name"] != "Banque"
        # Counterpart = Dubois provisions
        assert cr["account_number"] == DUBOIS_PROV
        assert cr["third_party_id"] == DUBOIS_ID

    def test_credit_known_iban_epargne_uses_pcmn_55076900(self, client):
        txn = _create_txn(client, iban=IBAN_EPARGNE, amount=400.0, txn_type="credit")
        _do_lettrage(client, txn["id"], DUBOIS_ID, "owner_payment")
        e = _fetch_fi_entry(client, txn["id"])
        assert e is not None
        dr = next(l for l in e["lines"] if l["debit"] > 0)
        assert dr["account_number"] == PCMN_EPARGNE
        assert not dr["account_number"].startswith("BE")
        # Name
        assert dr["account_name"] != "Banque"
        assert "6769" in (dr["account_name"] or "") or "epargne" in (dr["account_name"] or "").lower()

    def test_unknown_iban_falls_back_to_550000(self, client):
        # IBAN not in copro.bank_accounts
        txn = _create_txn(client, iban="BE99999999999999", amount=100.0, txn_type="credit")
        _do_lettrage(client, txn["id"], DUBOIS_ID, "owner_payment")
        e = _fetch_fi_entry(client, txn["id"])
        assert e is not None
        dr = next(l for l in e["lines"] if l["debit"] > 0)
        assert dr["account_number"] == PCMN_FALLBACK

    def test_no_iban_falls_back_to_550000(self, client):
        txn = _create_txn(client, iban="", amount=80.0, txn_type="credit")
        _do_lettrage(client, txn["id"], DUBOIS_ID, "owner_payment")
        e = _fetch_fi_entry(client, txn["id"])
        assert e is not None
        dr = next(l for l in e["lines"] if l["debit"] > 0)
        assert dr["account_number"] == PCMN_FALLBACK

    def test_debit_supplier_payment_dr_supplier_cr_bank_pcmn(self, client, supplier_id):
        sid, sname = supplier_id
        # Negative amount or transaction_type=debit
        txn = _create_txn(client, iban=IBAN_COURANT, amount=-150.0, txn_type="debit")
        _do_lettrage(client, txn["id"], sid, "supplier_payment")
        e = _fetch_fi_entry(client, txn["id"])
        assert e is not None
        lines = e["lines"]
        dr = next(l for l in lines if l["debit"] > 0)
        cr = next(l for l in lines if l["credit"] > 0)
        # Dr 44000XXX supplier / Cr bank_pcmn
        assert dr["account_number"].startswith("44000")
        assert dr["third_party_id"] == sid
        assert cr["account_number"] == PCMN_COURANT
        assert not cr["account_number"].startswith("BE")
        # Bank line third_party_id must be None (bank, not tier)
        assert cr["third_party_id"] is None

    def test_account_name_is_real_pcmn_name(self, client):
        txn = _create_txn(client, iban=IBAN_COURANT, amount=300.0, txn_type="credit")
        _do_lettrage(client, txn["id"], DUBOIS_ID, "owner_payment")
        e = _fetch_fi_entry(client, txn["id"])
        dr = next(l for l in e["lines"] if l["debit"] > 0)
        # PCMN 55103400 name is "Banque compte courant 7034"
        assert dr["account_name"] == "Banque compte courant 7034"


# ============================================================
# REGRESSION iter20 - balance-tiers/owners
# ============================================================
class TestRegressionIter20:
    def test_balance_tiers_owners_returns_expected_fields(self, client):
        r = client.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                       params={"copropriete_id": COPRO_ID})
        assert r.status_code == 200, r.text
        data = r.json()
        # Returns dict with owners list (per iter20)
        owners = data.get("owners", data) if isinstance(data, dict) else data
        assert isinstance(owners, list)
        assert len(owners) > 0
        # Find Dubois
        dub = next((o for o in owners if o.get("owner_id") == DUBOIS_ID), None)
        assert dub is not None, "Dubois owner missing"
        for f in ("owner_id", "owner_name", "total_called", "total_paid", "balance",
                  "provisions_balance", "reserve_balance"):
            assert f in dub, f"Missing field {f} in balance-tiers/owners"


# ============================================================
# REGRESSION iter18 - PCMN belge 337 comptes
# ============================================================
class TestRegressionIter18:
    def test_pcmn_count_337(self, client):
        r = client.get(f"{BASE_URL}/api/accounting/pcmn", params={"copropriete_id": COPRO_ID})
        assert r.status_code == 200
        accs = r.json()
        # 337 seed + some tier accounts dynamically created
        assert len(accs) >= 337, f"Expected >=337 PCMN accounts, got {len(accs)}"
