"""iter91a - HOMONYMES : test que 2 proprietaires partageant le meme nom
de famille (ex: Dupont Paul et Dupont Marie) NE sont PAS auto-matches au
hasard.

Le fix `_disambiguate_owner_candidates` (banking.py) applique :
  1) VCS (unique, deja teste iter90ib)
  2) Discriminant `first_name` si present dans cp_name
  3) Discriminant `montant` (solde debiteur ouvert d'un seul candidat)
  4) Sinon: pas de match automatique + `ambiguous_owner_candidates`
     stockes sur la txn pour rapprochement manuel.
"""
import os
import uuid
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"
try:
    with open("/app/backend/.env") as f:
        for line in f:
            if line.startswith("MONGO_URL="):
                MONGO_URL = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("DB_NAME="):
                DB_NAME = line.split("=", 1)[1].strip().strip('"')
except Exception:
    pass


TEST_PREFIX = "TEST_iter91a_"
_TAG = uuid.uuid4().hex[:8].upper()
# 2 vrais homonymes : meme last_name, prenoms differents
LAST_NAME = f"Dupontxyz{_TAG}"
NAME_PAUL = f"{LAST_NAME} Paulxyz{_TAG}"
NAME_MARIE = f"{LAST_NAME} Mariexyz{_TAG}"
COPRO_ID = f"{TEST_PREFIX}copro_" + uuid.uuid4().hex[:8]
STMT_ID = f"{TEST_PREFIX}stmt_" + uuid.uuid4().hex[:8]
OWNER_PAUL = f"{TEST_PREFIX}own_paul_" + uuid.uuid4().hex[:6]
OWNER_MARIE = f"{TEST_PREFIX}own_marie_" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module", autouse=True)
def seed_and_cleanup():
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.coproprietes.insert_one({
            "id": COPRO_ID, "name": f"{TEST_PREFIX}ACP",
            "bank_accounts": [{"iban": "BE00000000000000"}],
        })
        # 2 owners avec exactement le meme last_name
        await db.owners.insert_many([
            {
                "id": OWNER_PAUL, "name": NAME_PAUL,
                "last_name": LAST_NAME, "first_name": f"Paulxyz{_TAG}",
                "vcs_code": "444/4444/44444", "vcs_digits": "444444444444",
                "copropriete_id": COPRO_ID,
            },
            {
                "id": OWNER_MARIE, "name": NAME_MARIE,
                "last_name": LAST_NAME, "first_name": f"Mariexyz{_TAG}",
                "vcs_code": "555/5555/55555", "vcs_digits": "555555555555",
                "copropriete_id": COPRO_ID,
            },
        ])
        await db.bank_statements.insert_one({
            "id": STMT_ID, "number": f"{TEST_PREFIX}stmt",
            "date": "2025-01-15", "account_number": "BE00000000000000",
            "opening_balance": 0.0, "closing_balance": 0.0,
            "copropriete_id": COPRO_ID, "status": "draft",
        })
        # Solde ouvert : Paul doit 250 EUR, Marie doit 999 EUR
        # -> discriminant montant fonctionne avec txn de 250 (Paul) ou 999 (Marie)
        await db.journal_entries.insert_many([
            {
                "id": f"{TEST_PREFIX}je1_" + uuid.uuid4().hex[:6],
                "copropriete_id": COPRO_ID, "date": "2025-01-01",
                "lines": [
                    {"account_number": "410100", "third_party_id": OWNER_PAUL,
                     "debit": 250.0, "credit": 0},
                    {"account_number": "700000", "debit": 0, "credit": 250.0},
                ],
            },
            {
                "id": f"{TEST_PREFIX}je2_" + uuid.uuid4().hex[:6],
                "copropriete_id": COPRO_ID, "date": "2025-01-01",
                "lines": [
                    {"account_number": "410100", "third_party_id": OWNER_MARIE,
                     "debit": 999.0, "credit": 0},
                    {"account_number": "700000", "debit": 0, "credit": 999.0},
                ],
            },
        ])
        client.close()

    async def _cleanup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.coproprietes.delete_many({"id": COPRO_ID})
        await db.owners.delete_many({"id": {"$in": [OWNER_PAUL, OWNER_MARIE]}})
        await db.bank_statements.delete_many({"id": STMT_ID})
        await db.bank_transactions.delete_many({"copropriete_id": COPRO_ID})
        await db.journal_entries.delete_many({"copropriete_id": COPRO_ID})
        client.close()

    asyncio.get_event_loop().run_until_complete(_seed())
    yield
    asyncio.get_event_loop().run_until_complete(_cleanup())


def _create_txn(api, counterparty_name, communication="", amount=100.0):
    payload = {
        "statement_id": STMT_ID, "date": "2025-01-15",
        "amount": amount, "counterparty_name": counterparty_name,
        "counterparty_account": "", "communication": communication,
        "transaction_type": "credit", "copropriete_id": COPRO_ID,
    }
    r = api.post(f"{BASE_URL}/api/banking/transactions",
                 json=payload, headers={"X-Copropriete-Id": COPRO_ID})
    assert r.status_code == 200, f"create txn failed: {r.status_code} {r.text}"
    return r.json()


# ---- Tests homonymes ----

def test_last_name_only_ambiguous_no_auto_match(api):
    """cp_name = 'Dupont' seul (sans prenom, sans montant discriminant)
    -> AUCUN match automatique + ambiguous_owner_candidates non vide."""
    txn = _create_txn(api, LAST_NAME, amount=42.0)
    match_to = txn.get("suggested_match_to") or ""
    assert match_to not in (OWNER_PAUL, OWNER_MARIE), \
        f"HOMONYME BUG: match automatique errone vers {match_to}"
    # ambiguous_owner_candidates doit contenir les 2 candidats
    amb = txn.get("ambiguous_owner_candidates") or []
    ids = {c.get("id") for c in amb}
    assert OWNER_PAUL in ids and OWNER_MARIE in ids, \
        f"Expected both {OWNER_PAUL} and {OWNER_MARIE} in ambiguous list, got {ids}"


def test_first_name_discriminates_paul(api):
    """cp_name = 'Dupont Paul' -> matche Paul via first_name."""
    txn = _create_txn(api, f"{LAST_NAME} Paulxyz{_TAG}", amount=42.0)
    assert txn.get("suggested_match_to") == OWNER_PAUL, \
        f"Expected {OWNER_PAUL} (Paul), got {txn.get('suggested_match_to')}"


def test_first_name_discriminates_marie(api):
    """cp_name = 'Dupont Marie' -> matche Marie via first_name."""
    txn = _create_txn(api, f"{LAST_NAME} Mariexyz{_TAG}", amount=42.0)
    assert txn.get("suggested_match_to") == OWNER_MARIE, \
        f"Expected {OWNER_MARIE} (Marie), got {txn.get('suggested_match_to')}"


def test_amount_discriminates_paul_when_no_first_name(api):
    """cp_name = 'Dupont' + montant = 250 (solde ouvert de Paul)
    -> matche Paul via discriminant montant."""
    txn = _create_txn(api, LAST_NAME, amount=250.0)
    assert txn.get("suggested_match_to") == OWNER_PAUL, \
        f"Expected {OWNER_PAUL} via amount 250 EUR, got {txn.get('suggested_match_to')}"


def test_amount_discriminates_marie_when_no_first_name(api):
    """cp_name = 'Dupont' + montant = 999 (solde ouvert de Marie)
    -> matche Marie via discriminant montant."""
    txn = _create_txn(api, LAST_NAME, amount=999.0)
    assert txn.get("suggested_match_to") == OWNER_MARIE, \
        f"Expected {OWNER_MARIE} via amount 999 EUR, got {txn.get('suggested_match_to')}"


def test_vcs_still_overrides_homonym_ambiguity(api):
    """Le VCS reste prioritaire : VCS de Paul + cp_name 'Dupont' -> Paul."""
    txn = _create_txn(api, LAST_NAME,
                      communication="+++444/4444/44444+++", amount=42.0)
    assert txn.get("suggested_match_to") == OWNER_PAUL, \
        f"VCS priority regression: expected {OWNER_PAUL}, " \
        f"got {txn.get('suggested_match_to')}"
