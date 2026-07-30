"""iter93cc - API endpoint regression tests.

Verifies:
- GET /api/reports/bilan?view_mode=after_distribution returns 200 JSON with 490 in regul_actif
- GET /api/reports/bilan?view_mode=before_distribution returns 200 balanced
- GET /api/reports/bilan/pdf?view_mode=after_distribution returns valid PDF
- Bilan is balanced (equilibre=True) across all copropriete_ids in DB
- Compte 499XXX (sinistres) stays isolated in VI.D
"""
import os
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to frontend .env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

CID_AGATHE = "c9cfce94-96c6-4202-8a6d-0a5627b50856"


@pytest.fixture(scope="module")
def headers():
    """Auth via cookie session (backend sets HttpOnly cookie on login)."""
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@copro.be", "password": "admin123"},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return s  # return session, tests will use it as "headers" fixture


def test_bilan_after_distribution_json(headers):
    r = headers.get(
        f"{BASE_URL}/api/reports/bilan",
        params={
            "view_mode": "after_distribution",
            "date_to": "2027-03-31",
            "copropriete_id": CID_AGATHE,
        },
        timeout=30,
    )
    assert r.status_code == 200, f"Status {r.status_code}: {r.text[:400]}"
    data = r.json()
    assert data.get("equilibre") is True, f"Bilan desequilibre: ecart={data.get('ecart')}"

    # 490 must appear in VIII_regul_actif (mali)
    found_490 = False
    for rub in data.get("actif", []):
        label = rub.get("label", "").lower()
        if "regularisation" in label and "mali" in label:
            for a in rub.get("accounts", []):
                if a.get("account_number") == "490":
                    assert abs(a["amount"] - 3743.74) < 0.01
                    found_490 = True
    assert found_490, "Compte 490 not found in VIII regul actif"


def test_bilan_before_distribution_json(headers):
    r = headers.get(
        f"{BASE_URL}/api/reports/bilan",
        params={
            "view_mode": "before_distribution",
            "date_to": "2027-03-31",
            "copropriete_id": CID_AGATHE,
        },
        timeout=30,
    )
    assert r.status_code == 200, f"Status {r.status_code}: {r.text[:400]}"
    data = r.json()
    assert data.get("equilibre") is True


def test_bilan_pdf_after_distribution(headers):
    r = headers.get(
        f"{BASE_URL}/api/reports/bilan/pdf",
        params={
            "view_mode": "after_distribution",
            "date_to": "2027-03-31",
            "copropriete_id": CID_AGATHE,
        },
        timeout=45,
    )
    assert r.status_code == 200, f"PDF status {r.status_code}: {r.text[:200]}"
    assert r.content[:4] == b"%PDF", "Response is not a valid PDF"
    assert len(r.content) > 2000


def test_sinistre_499xxx_isolated_in_VI_D(headers):
    r = headers.get(
        f"{BASE_URL}/api/reports/bilan",
        params={
            "view_mode": "after_distribution",
            "date_to": "2027-03-31",
            "copropriete_id": CID_AGATHE,
        },
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    # Find sinistres rubric in passif
    total_sinistres = 0.0
    for rub in data.get("passif", []):
        if "sinistre" in rub.get("label", "").lower():
            total_sinistres = rub.get("total", 0.0)
    assert abs(total_sinistres - 3577.18) < 1.0, f"Sinistres total={total_sinistres}, expected 3577.18"


def test_all_coproprietes_balanced_after_distribution(headers):
    """Regression: bilan balanced across all ACPs, both modes."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )
    db = client[os.environ.get("DB_NAME", "test_database")]

    async def get_ids():
        cur = db.coproprietes.find({}, {"id": 1})
        return [c["id"] async for c in cur if c.get("id")]

    loop = asyncio.new_event_loop()
    try:
        ids = loop.run_until_complete(get_ids())
    finally:
        loop.close()
    client.close()

    assert len(ids) > 0, "No coproprietes found"
    failures = []
    for cid in ids:
        for mode in ("before_distribution", "after_distribution"):
            r = headers.get(
                f"{BASE_URL}/api/reports/bilan",
                params={"view_mode": mode, "date_to": "2027-12-31", "copropriete_id": cid},
                        timeout=30,
            )
            if r.status_code != 200:
                failures.append(f"{cid}/{mode}: HTTP {r.status_code}")
                continue
            d = r.json()
            if not d.get("equilibre"):
                failures.append(f"{cid}/{mode}: ecart={d.get('ecart')}")
    assert not failures, f"Bilans desequilibres: {failures}"
