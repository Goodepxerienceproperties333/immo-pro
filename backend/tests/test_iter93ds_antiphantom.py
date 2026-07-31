"""iter93ds - Anti-phantom lockdown for owners balance/tiers endpoints.

Verifie que :
- /api/communication/owners-balances retourne exactement 36 owners pour Agathe
- /api/reports/balance-tiers/owners retourne 36 owners (aucun fantome)
- meme avec include_former=true, aucun fantome (CALLENS/CANTERO) ne remonte
- un owner injecte via tier_accounts SANS lot ne remonte PAS
- autre ACP: verification pas de regression (liste coherente)
"""
import os
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
AGATHE_ID = "c9cfce94-96c6-4202-8a6d-0a5627b50856"
EXPECTED_OWNERS = 36

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def headers():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    # auth via cookie access_token
    token = s.cookies.get("access_token")
    assert token, f"no access_token cookie: {s.cookies}"
    return {"Authorization": f"Bearer {token}", "Cookie": f"access_token={token}"}


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


def _names_of(owners_list):
    return [ (o.get("owner_name") or o.get("name") or "").upper() for o in owners_list ]


def _ids_of(owners_list):
    return [ (o.get("owner_id") or o.get("id") or "") for o in owners_list ]


# Known phantom owners (created_from_orphan_account) that must NOT appear
PHANTOM_OWNER_IDS = {
    "29034c0d-7a2b-4805-a228-abe8086c8441",  # M. et Mme CANTERO DIAZ - VARGAS BAQUERO (orphan)
}


def test_communication_owners_balances_count(headers):
    r = requests.get(
        f"{BASE_URL}/api/communication/owners-balances",
        params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    owners = data.get("owners", [])
    assert isinstance(owners, list)
    names = _names_of(owners)
    ids = set(_ids_of(owners))
    print(f"[communication/owners-balances] count={len(owners)}")
    # No phantom
    assert not any("CALLENS" in n for n in names), f"Phantom CALLENS found: {names}"
    assert not (PHANTOM_OWNER_IDS & ids), f"Phantom orphan CANTERO id found: {PHANTOM_OWNER_IDS & ids}"
    assert len(owners) == EXPECTED_OWNERS, f"Expected {EXPECTED_OWNERS}, got {len(owners)}: {names}"


def test_balance_tiers_owners_count_default(headers):
    r = requests.get(
        f"{BASE_URL}/api/reports/balance-tiers/owners",
        params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    owners = data.get("owners", [])
    names = _names_of(owners)
    ids = set(_ids_of(owners))
    print(f"[balance-tiers/owners default] count={len(owners)}")
    assert not any("CALLENS" in n for n in names), "Phantom CALLENS found"
    assert not (PHANTOM_OWNER_IDS & ids), f"Phantom orphan id found: {PHANTOM_OWNER_IDS & ids}"
    assert len(owners) == EXPECTED_OWNERS, f"Expected {EXPECTED_OWNERS}, got {len(owners)}: {names}"


def test_balance_tiers_owners_include_former_true(headers):
    r = requests.get(
        f"{BASE_URL}/api/reports/balance-tiers/owners",
        params={"copropriete_id": AGATHE_ID, "include_former": "true"},
        headers=headers, timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    owners = data.get("owners", [])
    names = _names_of(owners)
    ids = set(_ids_of(owners))
    print(f"[balance-tiers/owners include_former=true] count={len(owners)}")
    # Meme avec include_former=true, aucun phantome ne doit remonter
    assert not any("CALLENS" in n for n in names), "Phantom CALLENS with include_former=true"
    assert not (PHANTOM_OWNER_IDS & ids), f"Phantom orphan id with include_former=true: {PHANTOM_OWNER_IDS & ids}"


def test_balance_tiers_totals_agathe(headers):
    r = requests.get(
        f"{BASE_URL}/api/reports/balance-tiers/owners",
        params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60,
    )
    assert r.status_code == 200
    data = r.json()
    td = data.get("total_debiteurs", 0)
    tc = data.get("total_crediteurs", 0)
    print(f"[totals] debiteurs={td} crediteurs={tc}")
    # Tolerance 5% around expected values
    assert 38000 <= td <= 43000, f"total_debiteurs={td} hors plage attendue ~40462"
    assert 0 <= tc <= 500, f"total_crediteurs={tc} hors plage attendue ~168"


def test_expected_active_owners_present(headers):
    r = requests.get(
        f"{BASE_URL}/api/reports/balance-tiers/owners",
        params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60,
    )
    data = r.json()
    names = " | ".join(_names_of(data.get("owners", [])))
    for expected in ["BROUWERS", "CLEENEWERCK", "COLLIN", "DE BIEVRE", "GUERIT"]:
        assert expected in names, f"Missing active owner '{expected}' in list: {names}"


def test_phantom_injection_blocked(headers, db):
    """Injecte directement un owner + tier_account orphelin pour Agathe et
    verifie qu'il ne remonte NI dans /communication/owners-balances NI dans
    /reports/balance-tiers/owners. C'est le test critique du verrouillage."""
    phantom_id = f"TEST_PHANTOM_{uuid.uuid4().hex[:8]}"
    phantom_name = f"TEST_PHANTOM_{uuid.uuid4().hex[:6]}"
    try:
        # Owner
        db.owners.insert_one({
            "id": phantom_id,
            "name": phantom_name,
            "email": "phantom@test.local",
        })
        # tier_account orphelin sur Agathe (source polluee)
        db.tier_accounts.insert_one({
            "id": f"TEST_TA_{phantom_id}",
            "copropriete_id": AGATHE_ID,
            "owner_id": phantom_id,
            "third_party_id": phantom_id,
            "account_number": "40000TEST",
            "balance": 999.99,
        })
        # journal_entry orphelin (sans lot_id)
        db.journal_entries.insert_one({
            "id": f"TEST_JE_{phantom_id}",
            "copropriete_id": AGATHE_ID,
            "third_party_id": phantom_id,
            "journal_type": "OD",
            "account_number": "40000TEST",
            "debit": 999.99,
            "credit": 0,
            "date": "2025-01-01",
        })

        # Communication endpoint
        r1 = requests.get(f"{BASE_URL}/api/communication/owners-balances",
                          params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60)
        assert r1.status_code == 200
        names1 = _names_of(r1.json().get("owners", []))
        assert phantom_name.upper() not in names1, f"PHANTOM remonte dans communication: {phantom_name}"

        # balance-tiers default
        r2 = requests.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                          params={"copropriete_id": AGATHE_ID}, headers=headers, timeout=60)
        assert r2.status_code == 200
        names2 = _names_of(r2.json().get("owners", []))
        assert phantom_name.upper() not in names2, f"PHANTOM remonte dans balance-tiers: {phantom_name}"

        # balance-tiers include_former=true (verrouillage doit tenir)
        r3 = requests.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                          params={"copropriete_id": AGATHE_ID, "include_former": "true"},
                          headers=headers, timeout=60)
        assert r3.status_code == 200
        names3 = _names_of(r3.json().get("owners", []))
        assert phantom_name.upper() not in names3, f"PHANTOM remonte avec include_former=true: {phantom_name}"

        print("[phantom_injection] PASS - phantom bloque dans tous les endpoints")
    finally:
        db.owners.delete_many({"id": phantom_id})
        db.tier_accounts.delete_many({"owner_id": phantom_id})
        db.journal_entries.delete_many({"third_party_id": phantom_id})


def test_other_acp_regression(headers, db):
    """Autre ACP : verifie que l'endpoint fonctionne toujours."""
    other = db.coproprietes.find_one({"id": {"$ne": AGATHE_ID}}, {"_id": 0, "id": 1, "name": 1})
    if not other:
        pytest.skip("Aucune autre ACP disponible")
    r = requests.get(f"{BASE_URL}/api/reports/balance-tiers/owners",
                     params={"copropriete_id": other["id"]}, headers=headers, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    print(f"[other ACP {other.get('name')}] owners={len(data.get('owners', []))} td={data.get('total_debiteurs')}")
    assert isinstance(data.get("owners"), list)
