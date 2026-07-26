"""iter90g0 - Owner Portal Charges: include OD entries touching class-6 accounts."""
import os
import uuid
import asyncio
import pytest
import requests
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

BOXUS_EMAIL = "evrard.gerald@outlook.be"
MARIA_COPRO_ID = "a0eef7c0-1db2-45ae-9053-040fbee71be5"
ALPHA_COPRO_ID = "b6fe4e32"  # partial - we'll query full below
TEST_PWD = "TestIter90g0!42"
TAG = "iter90g0_test"


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def db(event_loop):
    client = AsyncIOMotorClient(MONGO_URL)
    return client[DB_NAME]


@pytest.fixture(scope="module")
def alpha_copro_id(event_loop, db):
    async def _find():
        doc = await db.coproprietes.find_one({"id": {"$regex": "^b6fe4e32"}}, {"_id": 0, "id": 1})
        if not doc:
            doc = await db.coproprietes.find_one({"name": {"$regex": "Alpha", "$options": "i"}}, {"_id": 0, "id": 1})
        return (doc or {}).get("id")
    return event_loop.run_until_complete(_find())


@pytest.fixture(scope="module")
def boxus_session(event_loop, db):
    async def _setup():
        u = await db.users.find_one({"email": BOXUS_EMAIL})
        if not u:
            pytest.skip(f"User {BOXUS_EMAIL} not found")
        old_hash = u["password_hash"]
        new_hash = bcrypt.hashpw(TEST_PWD.encode(), bcrypt.gensalt()).decode()
        await db.users.update_one({"_id": u["_id"]}, {"$set": {
            "password_hash": new_hash, "must_change_password": False, "is_suspended": False,
        }})
        return old_hash, u["_id"]

    old_hash, uid = event_loop.run_until_complete(_setup())
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": BOXUS_EMAIL, "password": TEST_PWD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    yield s

    async def _restore():
        await db.users.update_one({"_id": uid}, {"$set": {"password_hash": old_hash}})
    event_loop.run_until_complete(_restore())


@pytest.fixture(autouse=True)
def cleanup_after(event_loop, db):
    yield
    async def _c():
        await db.journal_entries.delete_many({"test_tag": TAG})
    event_loop.run_until_complete(_c())


# --------- TESTS ---------

class TestODInclusion:
    def test_expects_8_rows_with_od_row(self, boxus_session):
        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices", params={"copropriete_id": MARIA_COPRO_ID})
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 8, f"Expected 8 rows, got {len(rows)}: {[(x.get('date'),x.get('supplier'),x.get('source')) for x in rows]}"

        od_rows = [x for x in rows if x.get("source") == "od"]
        assert len(od_rows) == 1, f"Expected 1 OD row, got {len(od_rows)}"
        od = od_rows[0]
        assert od["id"].startswith("od-")
        assert od["account_number"] == "61066"
        assert od["date"].startswith("2026-07-26")
        assert "Senyers" in (od.get("description") or "")
        assert abs(od["total_amount"] - (-3545.30)) < 0.02, od
        assert abs(od["my_amount"] - (-1091.95)) < 0.05, od
        assert od["distribution_key_name"] == "Charges communes"
        assert od["computed_share"] is True
        assert 30.5 <= od["my_share_pct"] <= 31.0

    def test_total_sum(self, boxus_session):
        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices", params={"copropriete_id": MARIA_COPRO_ID})
        assert r.status_code == 200
        rows = r.json()
        total = round(sum(x["my_amount"] for x in rows), 2)
        assert abs(total - 538.77) < 0.05, f"Total {total} != 538.77"

    def test_sort_desc_od_before_sneyers(self, boxus_session):
        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices", params={"copropriete_id": MARIA_COPRO_ID})
        rows = r.json()
        od_idx = next(i for i, x in enumerate(rows) if x.get("source") == "od")
        sneyers_idx = next(i for i, x in enumerate(rows) if x.get("supplier") == "Sneyers Philippe SRL")
        assert od_idx < sneyers_idx, f"OD (idx {od_idx}) must come before Sneyers (idx {sneyers_idx})"


class TestODEdgeCases:
    def test_no_charge_od_skipped(self, event_loop, db, boxus_session):
        """OD with no class-6 lines must not appear."""
        je_id = str(uuid.uuid4())

        async def _seed():
            await db.journal_entries.insert_one({
                "id": je_id,
                "copropriete_id": MARIA_COPRO_ID,
                "journal_type": "OD",
                "date": "2026-08-15",
                "description": "TEST no-charge OD",
                "lines": [
                    {"account_number": "550000", "account_name": "Banque", "debit": 100.0, "credit": 0},
                    {"account_number": "100000", "account_name": "Reserve", "debit": 0, "credit": 100.0},
                ],
                "test_tag": TAG,
            })
        event_loop.run_until_complete(_seed())

        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices", params={"copropriete_id": MARIA_COPRO_ID})
        rows = r.json()
        assert not any(x["id"] == f"od-{je_id}-550000" or je_id in x["id"] for x in rows), "no-charge OD leaked"
        assert len(rows) == 8

    def test_reversal_od_skipped(self, event_loop, db, boxus_session):
        je_id = str(uuid.uuid4())
        je_id2 = str(uuid.uuid4())

        async def _seed():
            await db.journal_entries.insert_many([
                {
                    "id": je_id, "copropriete_id": MARIA_COPRO_ID, "journal_type": "OD",
                    "date": "2026-08-20", "description": "TEST reversed OD",
                    "reversed": True,
                    "lines": [{"account_number": "61066", "account_name": "Travaux", "debit": 500, "credit": 0}],
                    "test_tag": TAG,
                },
                {
                    "id": je_id2, "copropriete_id": MARIA_COPRO_ID, "journal_type": "OD",
                    "date": "2026-08-21", "description": "TEST is_reversal OD",
                    "is_reversal": True,
                    "lines": [{"account_number": "61066", "account_name": "Travaux", "debit": 500, "credit": 0}],
                    "test_tag": TAG,
                },
            ])
        event_loop.run_until_complete(_seed())

        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices", params={"copropriete_id": MARIA_COPRO_ID})
        rows = r.json()
        for x in rows:
            assert je_id not in x["id"], "reversed=True OD leaked"
            assert je_id2 not in x["id"], "is_reversal=True OD leaked"

    def test_chinese_wall_alpha_od_not_visible_to_boxus(self, event_loop, db, boxus_session, alpha_copro_id):
        if not alpha_copro_id:
            pytest.skip("ACP Alpha not found")
        # check Boxus does not own any lot in Alpha
        async def _check():
            u = await db.users.find_one({"email": BOXUS_EMAIL})
            oids = [u["_id"], u.get("id")]
            oids = [o for o in oids if o]
            lot = await db.lots.find_one({
                "copropriete_id": alpha_copro_id,
                "$or": [{"owner_id": {"$in": oids}}, {"owner_ids": {"$in": oids}}],
            })
            return lot
        lot = event_loop.run_until_complete(_check())
        if lot:
            pytest.skip("Boxus actually owns a lot in Alpha - can't test wall")

        je_id = str(uuid.uuid4())
        async def _seed():
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": alpha_copro_id, "journal_type": "OD",
                "date": "2026-08-25", "description": "TEST alpha OD wall",
                "lines": [{"account_number": "61066", "account_name": "Travaux", "debit": 1000, "credit": 0}],
                "test_tag": TAG,
            })
        event_loop.run_until_complete(_seed())

        # No copro filter => all Boxus copros. Alpha OD must not appear.
        r = boxus_session.get(f"{BASE_URL}/api/owner/invoices")
        assert r.status_code == 200
        rows = r.json()
        assert not any(je_id in x["id"] for x in rows), "Alpha OD leaked to Boxus"
