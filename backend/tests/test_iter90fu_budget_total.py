"""iter90fu : BUG 1 - le total du budget n'apparaissait pas dans la liste
'Exercices Comptables > Budgets' apres import via le wizard.

Root cause : le wizard ecrivait `total_amount`, mais FiscalYearPage.js lit
`total`. Deux fixes :
  (a) commit_budget ecrit maintenant `total` ET `total_amount`.
  (b) list_budgets self-heal les budgets legacy (backfill/recompute + persistence).

Tests :
  1. Commit via wizard remplit total ET total_amount = 12345.67.
  2. Legacy backfill : total_amount seul -> GET renvoie total + persiste.
  3. Legacy recompute : ni total ni total_amount -> GET recalcule depuis lines.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

ALPHA_ACP_ID = "b6fe4e32-555a-435b-84cd-1f080cdd47c6"
ALPHA_SYNDIC_ID = "6a65b6396daad720f9dcbfa6"
ALPHA_CREDS = {"email": "syndic_alpha@copro.be", "password": "Syndic123!"}


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture()
def alpha_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ALPHA_CREDS, timeout=15)
    assert r.status_code == 200, f"login failed: {r.text}"
    return s


def test_wizard_commit_budget_writes_total_and_total_amount(db, alpha_session):
    """Bug1(a): commit-budget doit ecrire total ET total_amount = 12345.67."""
    session_id = None
    created_budget_id = None
    try:
        r = alpha_session.post(
            f"{API}/import-wizard/sessions",
            json={"copropriete_id": ALPHA_ACP_ID, "source_system": "Optipro"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        session_id = r.json()["id"]

        payload = {
            "fiscal_year_id": "",  # backend resout via ACP open FY
            "sections": [
                {
                    "key_code": "TESTFU01",
                    "key_label": "Test iter90fu S1",
                    "lines": [
                        {"account": "610000", "libelle": "TEST_L1", "amount": 1000.00},
                        {"account": "610100", "libelle": "TEST_L2", "amount": 2345.67},
                    ],
                },
                {
                    "key_code": "TESTFU02",
                    "key_label": "Test iter90fu S2",
                    "lines": [
                        {"account": "620000", "libelle": "TEST_L3", "amount": 4000.00},
                        {"account": "620100", "libelle": "TEST_L4", "amount": 5000.00},
                    ],
                },
            ],
        }
        r = alpha_session.post(
            f"{API}/import-wizard/sessions/{session_id}/commit-budget",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["inserted"] == 4
        assert abs(body["total_amount"] - 12345.67) < 0.01

        # Recharge doc from Mongo
        doc = db.budgets.find_one({"import_session_id": session_id}, {"_id": 0})
        assert doc is not None, "Budget not persisted"
        created_budget_id = doc["id"]
        assert doc.get("total") is not None, f"total missing: {doc}"
        assert doc.get("total_amount") is not None, f"total_amount missing: {doc}"
        assert abs(doc["total"] - 12345.67) < 0.01
        assert abs(doc["total_amount"] - 12345.67) < 0.01
    finally:
        if created_budget_id:
            db.budgets.delete_one({"id": created_budget_id})
        if session_id:
            db.import_sessions.delete_one({"id": session_id})
        db.distribution_keys.delete_many({
            "copropriete_id": ALPHA_ACP_ID,
            "code": {"$in": ["TESTFU01", "TESTFU02"]},
        })


def test_list_budgets_backfills_missing_total_from_total_amount(db, alpha_session):
    """Bug1(b): total_amount seul -> GET renvoie total + persiste."""
    budget_id = f"TEST_iter90fu_backfill_{uuid.uuid4().hex[:8]}"
    try:
        db.budgets.insert_one({
            "id": budget_id,
            "fiscal_year_id": "TEST_FY_938b7658-7f45-4e00-8ed8-7b281d204f3c",
            "copropriete_id": ALPHA_ACP_ID,
            "name": "TEST_legacy_backfill",
            "lines": [
                {"account_number": "610000", "label": "TEST_A", "amount": 3000.0},
                {"account_number": "610100", "label": "TEST_B", "amount": 3500.0},
            ],
            "total_amount": 6500.0,
            # PAS de champ 'total'
            "status": "draft",
            "syndic_id": ALPHA_SYNDIC_ID,
            "created_at": "2025-01-15T00:00:00+00:00",
        })
        r = alpha_session.get(
            f"{API}/fiscal/budgets",
            params={"copropriete_id": ALPHA_ACP_ID}, timeout=15,
        )
        assert r.status_code == 200, r.text
        match = next((b for b in r.json() if b["id"] == budget_id), None)
        assert match is not None, "Test budget missing from list"
        assert match.get("total") == 6500.0, f"total not backfilled: {match}"
        assert match.get("total_amount") == 6500.0

        raw = db.budgets.find_one({"id": budget_id}, {"_id": 0})
        assert raw.get("total") == 6500.0, f"total not persisted: {raw}"
        assert raw.get("total_amount") == 6500.0
    finally:
        db.budgets.delete_one({"id": budget_id})


def test_list_budgets_recomputes_when_both_missing(db, alpha_session):
    """Bug1(c): ni total ni total_amount -> GET recalcule depuis lines (=400)."""
    budget_id = f"TEST_iter90fu_recompute_{uuid.uuid4().hex[:8]}"
    try:
        db.budgets.insert_one({
            "id": budget_id,
            "fiscal_year_id": "TEST_FY_938b7658-7f45-4e00-8ed8-7b281d204f3c",
            "copropriete_id": ALPHA_ACP_ID,
            "name": "TEST_legacy_recompute",
            "lines": [
                {"account_number": "610000", "label": "TEST_R1", "amount": 100.0},
                {"account_number": "610100", "label": "TEST_R2", "amount": 250.0},
                {"account_number": "610200", "label": "TEST_R3", "amount": 50.0},
            ],
            "status": "draft",
            "syndic_id": ALPHA_SYNDIC_ID,
            "created_at": "2025-01-15T00:00:00+00:00",
        })
        r = alpha_session.get(
            f"{API}/fiscal/budgets",
            params={"copropriete_id": ALPHA_ACP_ID}, timeout=15,
        )
        assert r.status_code == 200, r.text
        match = next((b for b in r.json() if b["id"] == budget_id), None)
        assert match is not None
        assert match.get("total") == 400.0, f"total not recomputed: {match}"
        assert match.get("total_amount") == 400.0

        raw = db.budgets.find_one({"id": budget_id}, {"_id": 0})
        assert raw.get("total") == 400.0
        assert raw.get("total_amount") == 400.0
    finally:
        db.budgets.delete_one({"id": budget_id})
