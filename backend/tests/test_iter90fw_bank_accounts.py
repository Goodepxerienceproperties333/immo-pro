"""iter90fw : Test GET /api/owner/bank-accounts/{copropriete_id} with the new
IBAN/PCMN in-Python matching loop.

Seeds ephemeral owners/users/lots to exercise the endpoint as an actual
owner session. All seed rows are cleaned up in teardown. No bank_statements
or bank_transactions are modified.
"""
import os
import uuid
import asyncio
from datetime import datetime, timezone

import pytest
import requests
import bcrypt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

MARIA_ID = "a0eef7c0-1db2-45ae-9053-040fbee71be5"
TILLEULS_ID = "cb5bc07b-be17-4c09-9823-df5631052e6a"
ALPHA_ID = "b6fe4e32"  # from request context; may be partial. We'll discover.

TEST_TAG = "iter90fw_test"


def _hash(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


@pytest.fixture(scope="module")
def db():
    loop = asyncio.new_event_loop()
    client = AsyncIOMotorClient(MONGO_URL)
    d = client[DB_NAME]
    yield loop, d
    loop.close()
    client.close()


@pytest.fixture(scope="module")
def seeded(db):
    """Create 3 owners/users/lots:
      - maria_user in Maria copro
      - tilleuls_user in Tilleuls copro
      - alpha_user in a resolved Alpha-style copro (any copro that is NOT Maria)
      - empty_copro : a brand new copro with bank_accounts but no statements,
        linked to maria_user for testing empty movements branch.
    """
    loop, d = db

    async def _setup():
        # Discover a non-Maria copro for chinese wall test
        alpha_copro = await d.coproprietes.find_one(
            {"id": {"$ne": MARIA_ID}},
            {"_id": 0, "id": 1, "name": 1},
        )
        assert alpha_copro, "Need at least one non-Maria copro"

        # Create a temp empty copro (bank_accounts only, no statements/txn)
        empty_copro_id = f"TEST_{TEST_TAG}_empty_{uuid.uuid4().hex[:8]}"
        await d.coproprietes.insert_one({
            "id": empty_copro_id,
            "name": f"TEST_{TEST_TAG}_EmptyCopro",
            "bank_accounts": [
                {"iban": "BE99999999999999", "pcmn_number": "55999999",
                 "account_type": "vue", "is_default": True, "label": "TEST empty"},
            ],
            "syndic_id": "test",
            "test_tag": TEST_TAG,
        })

        users = {}
        for key, copro_id, email in [
            ("maria", MARIA_ID, f"test_{TEST_TAG}_maria@copro.be"),
            ("tilleuls", TILLEULS_ID, f"test_{TEST_TAG}_tilleuls@copro.be"),
            ("alpha", alpha_copro["id"], f"test_{TEST_TAG}_alpha@copro.be"),
            ("empty", empty_copro_id, f"test_{TEST_TAG}_empty@copro.be"),
        ]:
            owner_id = str(uuid.uuid4())
            lot_id = str(uuid.uuid4())
            user_id_str = str(uuid.uuid4())
            # Insert owner (global)
            await d.owners.insert_one({
                "id": owner_id, "email": email, "name": f"TEST {key}",
                "first_name": "TEST", "last_name": key.upper(),
                "test_tag": TEST_TAG,
            })
            # Insert user with role=owner
            u_ins = await d.users.insert_one({
                "email": email,
                "password_hash": _hash("Owner123!"),
                "name": f"TEST {key} owner",
                "role": "owner",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "onboarding_completed": True,
                "must_change_password": False,
                "test_tag": TEST_TAG,
            })
            # Insert lot in the copro
            await d.lots.insert_one({
                "id": lot_id, "number": f"TEST-{key}-01",
                "copropriete_id": copro_id,
                "owner_id": owner_id,
                "owner_ids": [owner_id],
                "test_tag": TEST_TAG,
            })
            users[key] = {
                "email": email, "password": "Owner123!",
                "owner_id": owner_id, "lot_id": lot_id,
                "user_mongo_id": u_ins.inserted_id,
                "copropriete_id": copro_id,
            }
        return users, empty_copro_id, alpha_copro["id"]

    users, empty_copro_id, alpha_copro_id = loop.run_until_complete(_setup())

    yield {"users": users, "empty_copro_id": empty_copro_id, "alpha_copro_id": alpha_copro_id}

    async def _teardown():
        await d.owners.delete_many({"test_tag": TEST_TAG})
        await d.users.delete_many({"test_tag": TEST_TAG})
        await d.lots.delete_many({"test_tag": TEST_TAG})
        await d.coproprietes.delete_many({"test_tag": TEST_TAG})

    loop.run_until_complete(_teardown())


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"Login failed {email}: {r.status_code} {r.text}"
    return s


# ------- Tests -------

def test_maria_movements_via_pcmn_prefix(seeded):
    """Maria: IBAN=BE04001952089331 / PCMN=55133100 ; stored statements use
    truncated PCMN '551331'. The new loop must accept the prefix match and
    return the 18 seeded transactions (>=5 as spec)."""
    u = seeded["users"]["maria"]
    s = _login(u["email"], u["password"])
    r = s.get(f"{BASE_URL}/api/owner/bank-accounts/{MARIA_ID}", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    accounts = data.get("bank_accounts") or data.get("accounts") or data
    if isinstance(data, dict) and "accounts" in data:
        accounts = data["accounts"]
    elif isinstance(data, dict) and "bank_accounts" in data:
        accounts = data["bank_accounts"]
    else:
        accounts = data if isinstance(data, list) else data.get("accounts", [])
    # Find the vue account (pcmn 55133100)
    vue = next((a for a in accounts if a.get("pcmn_number") == "55133100"), None)
    assert vue is not None, f"Vue account not found in {accounts}"
    movs = vue.get("recent_movements", [])
    assert len(movs) >= 5, f"Expected >=5 movements, got {len(movs)}"
    for m in movs[:3]:
        assert "date" in m and m["date"]
        assert "amount" in m
        assert "counterparty" in m
        assert "communication" in m


def test_tilleuls_movements_via_iban_space_normalization(seeded):
    """Tilleuls: statement stored as 'BE68 5390 0754 7034' (with spaces).
    Loop must strip spaces + upper before compare."""
    u = seeded["users"]["tilleuls"]
    s = _login(u["email"], u["password"])
    r = s.get(f"{BASE_URL}/api/owner/bank-accounts/{TILLEULS_ID}", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    accounts = data.get("accounts") if isinstance(data, dict) and "accounts" in data else data
    if isinstance(data, dict) and "bank_accounts" in data:
        accounts = data["bank_accounts"]
    vue = next((a for a in accounts if a.get("pcmn_number") == "55103400"), None)
    assert vue is not None
    movs = vue.get("recent_movements", [])
    # 11 seeded transactions
    assert len(movs) >= 1, f"Expected movements via IBAN space-normalization, got 0"


def test_chinese_wall_403_on_maria_for_alpha_owner(seeded):
    """An owner with lot only in alpha_copro must get 403 for Maria."""
    u = seeded["users"]["alpha"]
    s = _login(u["email"], u["password"])
    r = s.get(f"{BASE_URL}/api/owner/bank-accounts/{MARIA_ID}", timeout=15)
    assert r.status_code == 403, f"Expected 403, got {r.status_code} {r.text}"


def test_date_filter_regression(seeded):
    """Filter start=2026-03-01 end=2026-03-31 on Maria. All returned movements
    must have date within that range."""
    u = seeded["users"]["maria"]
    s = _login(u["email"], u["password"])
    r = s.get(
        f"{BASE_URL}/api/owner/bank-accounts/{MARIA_ID}",
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    accounts = data.get("accounts") if isinstance(data, dict) and "accounts" in data else (
        data.get("bank_accounts") if isinstance(data, dict) else data)
    for acc in accounts:
        for m in acc.get("recent_movements", []):
            d = m.get("date", "")
            assert "2026-03-01" <= d <= "2026-03-31", f"Movement outside range: {d}"


def test_empty_bank_accounts_no_500(seeded):
    """Copro with bank_accounts entries but no statements/transactions must
    return movements=[] without exception."""
    u = seeded["users"]["empty"]
    empty_id = seeded["empty_copro_id"]
    s = _login(u["email"], u["password"])
    r = s.get(f"{BASE_URL}/api/owner/bank-accounts/{empty_id}", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    accounts = data.get("accounts") if isinstance(data, dict) and "accounts" in data else (
        data.get("bank_accounts") if isinstance(data, dict) else data)
    assert isinstance(accounts, list) and len(accounts) >= 1
    for acc in accounts:
        assert acc.get("recent_movements", []) == []
