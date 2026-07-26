"""iter90fz — Owner Portal Charges dynamic projection tests.

Tests that GET /api/owner/invoices returns:
  * legacy invoices projected via Distribution Key (Boxus in Maria copro)
  * regression on Tilleuls (distribution_lines path, computed_share=False)
  * DK missing / total_quotities=0 edge cases (no 500, row skipped)
"""
import os
import asyncio
import pytest
import requests
import bcrypt
import copy
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load backend env (MONGO_URL, DB_NAME)
load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # fallback: read from frontend .env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

BOXUS_OWNER_ID = "ca41e444-ef4a-4955-81b0-293d6742f648"
BOXUS_EMAIL = "evrard.gerald@outlook.be"
MARIA_COPRO_ID = "a0eef7c0-1db2-45ae-9053-040fbee71be5"
TILLEULS_COPRO_ID = "cb5bc07b-be17-4c09-9823-df5631052e6a"
TILLEULS_LOT_A001 = "316dc377-8552-4db9-8bfb-365de63bbae9"
TILLEULS_OWNER_ID = "7f4e453b-e625-4c78-a2ff-040f98a98f48"
TILLEULS_EMAIL = "sophie.martin@example.be"

TEST_PWD = "TestIter90fz!42"

EXPECTED_INVOICES = {
    # date -> (supplier, total, expected_my)
    ("2026-06-14", "SRL Finlead", 678.99): 208.92,
    ("2026-06-02", "Baloise Insurance", 60.76): 18.71,
    ("2026-05-04", "Euromex", 200.00): 61.60,
    ("2026-05-03", "Engie", -57.03): -17.57,
    ("2026-03-01", "Engie", 114.00): 35.11,
    ("2026-03-01", "SRL Finlead", 753.99): 232.00,
    ("2026-07-02", "Sneyers Philippe SRL", 3545.30): 1091.95,
}
EXPECTED_TOTAL = 1630.72


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
def boxus_session(event_loop, db):
    """Restaure le password_hash de l'utilisateur Boxus a la fin.

    On sauvegarde l'ancien hash, on set un connu, on login, on rend la
    session. Cleanup restore.
    """
    async def _setup():
        u = await db.users.find_one({"email": BOXUS_EMAIL})
        if not u:
            pytest.skip(f"User for Boxus email {BOXUS_EMAIL} not found")
        old_hash = u["password_hash"]
        new_hash = bcrypt.hashpw(TEST_PWD.encode(), bcrypt.gensalt()).decode()
        await db.users.update_one({"_id": u["_id"]}, {"$set": {
            "password_hash": new_hash,
            "must_change_password": False,
            "is_suspended": False,
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


@pytest.fixture(scope="module")
def tilleuls_session(event_loop, db):
    """Cree un user temporaire pour Martin Sophie (Tilleuls). Cleanup."""
    async def _setup():
        # tag on user so cleanup finds it
        new_hash = bcrypt.hashpw(TEST_PWD.encode(), bcrypt.gensalt()).decode()
        existing = await db.users.find_one({"email": TILLEULS_EMAIL})
        if existing:
            return existing["_id"], False
        res = await db.users.insert_one({
            "email": TILLEULS_EMAIL,
            "password_hash": new_hash,
            "name": "Martin Sophie [iter90fz_test]",
            "role": "owner",
            "copropriete_ids": [TILLEULS_COPRO_ID],
            "is_suspended": False,
            "must_change_password": False,
            "test_tag": "iter90fz_test",
        })
        return res.inserted_id, True

    uid, created = event_loop.run_until_complete(_setup())

    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": TILLEULS_EMAIL, "password": TEST_PWD})
    if r.status_code != 200:
        pytest.skip(f"Tilleuls login failed: {r.status_code} {r.text}")
    yield s

    async def _cleanup():
        if created:
            await db.users.delete_one({"_id": uid, "test_tag": "iter90fz_test"})
    event_loop.run_until_complete(_cleanup())


# ------------------ TESTS ------------------

def _norm_key(inv):
    return (inv["date"][:10], inv["supplier"], round(float(inv["total_amount"]), 2))


class TestBoxusMariaProjection:
    """BACKEND — Charges dynamic projection for legacy invoices (Boxus/Maria)."""

    def test_returns_7_invoices_with_projected_shares(self, boxus_session):
        r = boxus_session.get(
            f"{BASE_URL}/api/owner/invoices",
            params={"copropriete_id": MARIA_COPRO_ID},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        assert len(rows) == 7, f"Expected 7 rows, got {len(rows)}: {[(x['date'],x['supplier'],x['total_amount'],x['my_amount']) for x in rows]}"

        # Verify each expected invoice is present with correct my_amount
        got_map = {_norm_key(r): r for r in rows}
        for key, expected_my in EXPECTED_INVOICES.items():
            assert key in got_map, f"Missing invoice {key} in response. Got: {list(got_map.keys())}"
            row = got_map[key]
            assert abs(row["my_amount"] - expected_my) < 0.02, (
                f"my_amount mismatch for {key}: got {row['my_amount']}, expected {expected_my}"
            )
            assert row["computed_share"] is True, f"computed_share should be True for {key}"
            assert 30.0 <= row["my_share_pct"] <= 31.5, (
                f"my_share_pct out of range for {key}: {row['my_share_pct']}"
            )
            assert row["distribution_key_name"] in {"Charges communes", "Honoraires et frais de syndic"}, (
                f"Unexpected dk name for {key}: {row['distribution_key_name']}"
            )

    def test_total_my_amount_matches_expected(self, boxus_session):
        r = boxus_session.get(
            f"{BASE_URL}/api/owner/invoices",
            params={"copropriete_id": MARIA_COPRO_ID},
        )
        assert r.status_code == 200
        total = round(sum(row["my_amount"] for row in r.json()), 2)
        assert abs(total - EXPECTED_TOTAL) < 0.05, f"Sum {total} != expected {EXPECTED_TOTAL}"


class TestTilleulsRegression:
    """BACKEND — Regression: Tilleuls invoices via distribution_lines path (computed_share=False)."""

    def test_tilleuls_uses_distribution_lines_not_dk(self, tilleuls_session):
        r = tilleuls_session.get(
            f"{BASE_URL}/api/owner/invoices",
            params={"copropriete_id": TILLEULS_COPRO_ID},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) > 0, "Tilleuls owner should have at least one invoice"
        # All rows: computed_share should be False (data has distribution_lines)
        wrong = [row for row in rows if row.get("computed_share") is True]
        assert not wrong, f"Some Tilleuls rows are computed_share=True (should be False): {wrong}"
        # Specifically verify the Otis Belgium SA 450.00 invoice -> my_amount 60.24
        otis = [row for row in rows if row["supplier"] == "Otis Belgium SA" and abs(row["total_amount"] - 450.0) < 0.01]
        if otis:
            assert abs(otis[0]["my_amount"] - 60.24) < 0.02, f"Otis my_amount {otis[0]['my_amount']} != 60.24"
            assert otis[0]["computed_share"] is False


class TestEdgeCases:
    """BACKEND — DK edge cases: missing DK / total_quotities=0 / owner without share.

    Uses temp invoices tagged with test_tag=iter90fz_test for cleanup.
    """

    def test_dk_zero_quotities_and_missing_dk_skip_rows(self, event_loop, db, boxus_session):
        # Insert 2 synthetic invoices in Maria copro:
        #  (a) distribution_key_id points to nothing -> skipped
        #  (b) distribution_key_id has total_quotities=0 -> skipped
        import uuid
        inv_a_id = str(uuid.uuid4())
        inv_b_id = str(uuid.uuid4())
        dk_zero_id = str(uuid.uuid4())

        async def _seed():
            await db.distribution_keys.insert_one({
                "id": dk_zero_id,
                "name": "Test Zero DK",
                "copropriete_id": MARIA_COPRO_ID,
                "total_quotities": 0,
                "lines": [],
                "test_tag": "iter90fz_test",
            })
            await db.invoices.insert_many([
                {
                    "id": inv_a_id,
                    "copropriete_id": MARIA_COPRO_ID,
                    "date": "2026-08-01",
                    "supplier": "TEST_MissingDK",
                    "total_amount": 100.0,
                    "distribution_key_id": "does-not-exist-" + inv_a_id,
                    "distribution_lines": [],
                    "test_tag": "iter90fz_test",
                },
                {
                    "id": inv_b_id,
                    "copropriete_id": MARIA_COPRO_ID,
                    "date": "2026-08-02",
                    "supplier": "TEST_ZeroTotalQ",
                    "total_amount": 100.0,
                    "distribution_key_id": dk_zero_id,
                    "distribution_lines": [],
                    "test_tag": "iter90fz_test",
                },
            ])

        async def _cleanup():
            await db.invoices.delete_many({"test_tag": "iter90fz_test"})
            await db.distribution_keys.delete_many({"test_tag": "iter90fz_test"})

        event_loop.run_until_complete(_seed())
        try:
            r = boxus_session.get(
                f"{BASE_URL}/api/owner/invoices",
                params={"copropriete_id": MARIA_COPRO_ID},
            )
            assert r.status_code == 200, r.text
            ids = {row["id"] for row in r.json()}
            assert inv_a_id not in ids, "Missing-DK invoice should be skipped (my_amount=0)"
            assert inv_b_id not in ids, "Zero-total-quotities DK invoice should be skipped"
            # Ensure count still equals original 7 (edge cases were skipped)
            assert len(r.json()) == 7, f"Expected still 7 real rows, got {len(r.json())}"
        finally:
            event_loop.run_until_complete(_cleanup())

    def test_owner_not_in_dk_returns_zero_and_row_skipped(self, event_loop, db, boxus_session):
        """DK exists but does not include any of the owner's lots -> row skipped."""
        import uuid
        dk_id = str(uuid.uuid4())
        inv_id = str(uuid.uuid4())

        async def _seed():
            await db.distribution_keys.insert_one({
                "id": dk_id,
                "name": "Test Ascenseur (excludes Boxus lots)",
                "copropriete_id": MARIA_COPRO_ID,
                "total_quotities": 1000,
                # No lines referencing any of Boxus's lot_ids/numbers
                "lines": [
                    {"lot_id": "fake-lot-xyz", "lot_number": "ZZZ999", "share": 1000},
                ],
                "test_tag": "iter90fz_test",
            })
            await db.invoices.insert_one({
                "id": inv_id,
                "copropriete_id": MARIA_COPRO_ID,
                "date": "2026-08-03",
                "supplier": "TEST_NoShare",
                "total_amount": 500.0,
                "distribution_key_id": dk_id,
                "distribution_lines": [],
                "test_tag": "iter90fz_test",
            })

        async def _cleanup():
            await db.invoices.delete_many({"test_tag": "iter90fz_test"})
            await db.distribution_keys.delete_many({"test_tag": "iter90fz_test"})

        event_loop.run_until_complete(_seed())
        try:
            r = boxus_session.get(
                f"{BASE_URL}/api/owner/invoices",
                params={"copropriete_id": MARIA_COPRO_ID},
            )
            assert r.status_code == 200
            ids = {row["id"] for row in r.json()}
            assert inv_id not in ids, "Invoice on DK without owner share must be skipped"
        finally:
            event_loop.run_until_complete(_cleanup())
