"""iter90ib : test que le fix d'exclusion des titres de politesse (Mme, M., etc.)
dans le matching partiel de counterparty_name ne cree plus de faux positifs.

Bug rapporte : 3 lignes avec counterparty_name 'Mme Puttemans Victoria',
'Mme Degeest Marianne', 'Mme Woillard Monique' matchent toutes vers
'Mme Degeest Marianne' via le token 'Mme'.

Ce test seed 3 owners fictifs, cree une txn par owner, et verifie que
`suggested_match_label` est le bon owner (ou vide) et pas un autre.
Test egalement : match VCS reste prioritaire (regression).
"""
import os
import uuid
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") \
    if os.environ.get("REACT_APP_BACKEND_URL") \
    else "https://optipro-parser-fix.preview.emergentagent.com"
# fallback: read from frontend/.env
if "REACT_APP_BACKEND_URL" not in os.environ:
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


TEST_PREFIX = "TEST_iter90ib_"
_TAG = uuid.uuid4().hex[:8].upper()
NAME_PUTT = f"Mme Puttemansxyz{_TAG} Victoriaxyz"
NAME_DEG = f"Mme Degeestxyz{_TAG} Mariannexyz"
NAME_WOIL = f"Mme Woillardxyz{_TAG} Moniquexyz"
LAST_PUTT = f"Puttemansxyz{_TAG}"
LAST_DEG = f"Degeestxyz{_TAG}"
LAST_WOIL = f"Woillardxyz{_TAG}"
COPRO_ID = f"{TEST_PREFIX}copro_" + uuid.uuid4().hex[:8]
STMT_ID = f"{TEST_PREFIX}stmt_" + uuid.uuid4().hex[:8]
OWNER_PUTT = f"{TEST_PREFIX}own_putt_" + uuid.uuid4().hex[:6]
OWNER_DEG = f"{TEST_PREFIX}own_deg_" + uuid.uuid4().hex[:6]
OWNER_WOIL = f"{TEST_PREFIX}own_woil_" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def api():
    """Session with cookie-based auth (access_token cookie set by /api/auth/login)."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    assert "access_token" in s.cookies, f"no access_token cookie set: {s.cookies}"
    return s


@pytest.fixture(scope="module", autouse=True)
def seed_and_cleanup():
    """Seed test copropriete, statement, and 3 owners; cleanup after."""
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Copropriete
        await db.coproprietes.insert_one({
            "id": COPRO_ID,
            "name": f"{TEST_PREFIX}ACP",
            "bank_accounts": [{"iban": "BE00000000000000"}],
        })
        # Owners - all with title 'Mme' in name but different last_name
        await db.owners.insert_many([
            {
                "id": OWNER_PUTT,
                "name": NAME_PUTT,
                "last_name": LAST_PUTT,
                "first_name": "Victoriaxyz",
                "vcs_code": "111/1111/11111",
                "vcs_digits": "111111111111",
                "copropriete_id": COPRO_ID,
            },
            {
                "id": OWNER_DEG,
                "name": NAME_DEG,
                "last_name": LAST_DEG,
                "first_name": "Mariannexyz",
                "vcs_code": "222/2222/22222",
                "vcs_digits": "222222222222",
                "copropriete_id": COPRO_ID,
            },
            {
                "id": OWNER_WOIL,
                "name": NAME_WOIL,
                "last_name": LAST_WOIL,
                "first_name": "Moniquexyz",
                "vcs_code": "333/3333/33333",
                "vcs_digits": "333333333333",
                "copropriete_id": COPRO_ID,
            },
        ])
        # Bank statement (draft) - required for txn creation to have context
        await db.bank_statements.insert_one({
            "id": STMT_ID,
            "number": f"{TEST_PREFIX}stmt",
            "date": "2025-01-15",
            "account_number": "BE00000000000000",
            "opening_balance": 0.0,
            "closing_balance": 0.0,
            "copropriete_id": COPRO_ID,
            "status": "draft",
        })
        client.close()

    async def _cleanup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.coproprietes.delete_many({"id": COPRO_ID})
        await db.owners.delete_many({"id": {"$in": [OWNER_PUTT, OWNER_DEG, OWNER_WOIL]}})
        await db.bank_statements.delete_many({"id": STMT_ID})
        await db.bank_transactions.delete_many({"statement_id": STMT_ID})
        await db.bank_transactions.delete_many({"copropriete_id": COPRO_ID})
        client.close()

    asyncio.get_event_loop().run_until_complete(_seed())
    yield
    asyncio.get_event_loop().run_until_complete(_cleanup())


def _create_txn(api, counterparty_name, communication="", amount=100.0):
    payload = {
        "statement_id": STMT_ID,
        "date": "2025-01-15",
        "amount": amount,
        "counterparty_name": counterparty_name,
        "counterparty_account": "",
        "communication": communication,
        "transaction_type": "credit",
        "copropriete_id": COPRO_ID,
    }
    r = api.post(f"{BASE_URL}/api/banking/transactions",
                 json=payload,
                 headers={"X-Copropriete-Id": COPRO_ID})
    assert r.status_code == 200, f"create txn failed: {r.status_code} {r.text}"
    return r.json()


# --- iter90ib : Bug fix - titre 'Mme' ne doit pas creer de faux match ---

def test_puttemans_no_false_match_via_mme_token(api):
    """Mme Puttemans... doit matcher son owner (name_exact) - PAS Degeest."""
    txn = _create_txn(api, NAME_PUTT)
    assert txn.get("suggested_match_to") == OWNER_PUTT, \
        f"Expected {OWNER_PUTT} (Puttemans), got {txn.get('suggested_match_to')} " \
        f"label={txn.get('suggested_match_label')}"
    assert LAST_PUTT in (txn.get("suggested_match_label") or "")


def test_woillard_no_false_match_via_mme_token(api):
    """Mme Woillard... doit matcher son owner (name_exact) - PAS Degeest."""
    txn = _create_txn(api, NAME_WOIL)
    assert txn.get("suggested_match_to") == OWNER_WOIL, \
        f"Expected {OWNER_WOIL} (Woillard), got {txn.get('suggested_match_to')} " \
        f"label={txn.get('suggested_match_label')}"
    assert LAST_WOIL in (txn.get("suggested_match_label") or "")


def test_degeest_still_matches_exact(api):
    """Regression : Mme Degeest... matche toujours son owner (name_exact)."""
    txn = _create_txn(api, NAME_DEG)
    assert txn.get("suggested_match_to") == OWNER_DEG, \
        f"Expected {OWNER_DEG}, got {txn.get('suggested_match_to')}"


def test_unknown_mme_does_not_match_any(api):
    """Un counterparty 'Mme Inconnuexyz{TAG} Xyz' ne DOIT PAS matcher un
    autre owner via le seul token 'Mme'."""
    txn = _create_txn(api, f"Mme Inconnuexyz{_TAG} Xyz")
    match_to = txn.get("suggested_match_to") or ""
    match_label = txn.get("suggested_match_label") or ""
    assert match_to not in (OWNER_DEG, OWNER_PUTT, OWNER_WOIL), \
        f"BUG NON CORRIGE: matched to {match_to} ({match_label}) via titre 'Mme'"


def test_vcs_priority_over_name(api):
    """Regression : le VCS dans la communication prime sur le
    counterparty_name. VCS 222222222222 (Degeest) doit matcher Degeest
    meme si counterparty_name='Mme Puttemans...'."""
    txn = _create_txn(api, NAME_PUTT,
                      communication="+++222/2222/22222+++")
    assert txn.get("suggested_match_to") == OWNER_DEG, \
        f"VCS regression: expected {OWNER_DEG} (Degeest via VCS), " \
        f"got {txn.get('suggested_match_to')} ({txn.get('suggested_match_label')})"


def test_only_title_no_name_no_match(api):
    """counterparty_name = 'Mme' seul ne DOIT matcher aucun test-owner."""
    txn = _create_txn(api, "Mme")
    match_to = txn.get("suggested_match_to") or ""
    assert match_to not in (OWNER_DEG, OWNER_PUTT, OWNER_WOIL), \
        f"BUG: 'Mme' seul a matche {match_to}"


def test_msieur_variant_also_excluded(api):
    """'M.' est aussi exclu (pas seulement 'Mme')."""
    txn = _create_txn(api, "M. Xyz")
    match_to = txn.get("suggested_match_to") or ""
    assert match_to not in (OWNER_DEG, OWNER_PUTT, OWNER_WOIL), \
        f"BUG: 'M. Xyz' a matche {match_to}"
