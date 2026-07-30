"""iter93cg AUDIT INTEGRATION TEST
Ferme reellement l'exercice fiscal Agathe, verifie que l'AN generee reflete
EXACTEMENT le bilan preview apres repartition, puis rouvre l'exercice pour
restaurer l'etat initial (pas de pollution des donnees prod).
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or open(
    "/app/frontend/.env"
).read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()

CID_AGATHE = "c9cfce94-96c6-4202-8a6d-0a5627b50856"
FY_ID = "43c8b088-fbd9-4ee3-8a39-466bed4d246c"
GUERIT_ACC = "41010016"  # Guerit fonds de roulement


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@copro.be", "password": "admin123"},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.text}"
    return s


def test_audit_an_matches_bilan_preview_then_reopen(session):
    """AUDIT : Close -> verify AN(41010016 Guerit) == bilan preview amount
    -> reopen (restore state).
    """
    # 1. Bilan preview BEFORE close (baseline expected AN)
    r = session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={
            "copropriete_id": CID_AGATHE,
            "date_to": "2027-03-31",
            "view_mode": "after_distribution",
        },
        timeout=30,
    )
    assert r.status_code == 200
    bilan = r.json()
    guerit_bilan = None
    for rub in bilan["actif"] + bilan["passif"]:
        for a in rub["accounts"]:
            if a.get("account_number") == GUERIT_ACC or "uérit" in a.get(
                "account_name", ""
            ):
                guerit_bilan = a["amount"]
                break
    assert guerit_bilan is not None, "Guerit absent du bilan preview"
    print(f"[BASELINE] Guerit bilan preview after_distribution = {guerit_bilan}")

    # 2. Close the fiscal year
    r = session.post(
        f"{BASE_URL}/api/fiscal/years/{FY_ID}/close", timeout=120
    )
    assert r.status_code == 200, f"Cloture failed: {r.status_code} {r.text}"
    close_result = r.json()
    print(f"[CLOSE] {close_result}")

    try:
        # 3. Fetch AN entry & find Guerit
        from motor.motor_asyncio import AsyncIOMotorClient
        import asyncio

        async def fetch_an():
            db = AsyncIOMotorClient(
                os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            )[os.environ.get("DB_NAME", "test_database")]
            an = await db.journal_entries.find_one(
                {
                    "copropriete_id": CID_AGATHE,
                    "fiscal_year_id": FY_ID,
                    "journal_type": "AN",
                },
                {"_id": 0},
            )
            return an

        an = asyncio.get_event_loop().run_until_complete(fetch_an())
        assert an is not None, "AN entry not generated"
        print(
            f"[AN] {len(an['lines'])} lignes, total_debit={an['total_debit']}, "
            f"total_credit={an['total_credit']}"
        )
        # Verify AN is balanced
        assert abs(an["total_debit"] - an["total_credit"]) < 0.02, (
            f"AN desequilibree: D={an['total_debit']} C={an['total_credit']}"
        )

        # Find Guerit line in AN
        guerit_line = next(
            (l for l in an["lines"] if l["account_number"] == GUERIT_ACC), None
        )
        assert guerit_line is not None, (
            f"Guerit {GUERIT_ACC} absent de l'AN"
        )
        an_amount = guerit_line["debit"] - guerit_line["credit"]
        print(
            f"[AUDIT] Guerit AN = {an_amount} (D={guerit_line['debit']}, "
            f"C={guerit_line['credit']})  |  Bilan preview = {guerit_bilan}"
        )

        # AUDIT: AN must match bilan preview (both sides may have sign convention)
        assert abs(abs(an_amount) - abs(guerit_bilan)) < 0.10, (
            f"AN ({an_amount}) diverge du bilan preview ({guerit_bilan})"
        )
    finally:
        # 4. ROLLBACK : reopen fiscal year (extourne les OD + AN)
        r_reopen = session.post(
            f"{BASE_URL}/api/fiscal/years/{FY_ID}/reopen", timeout=60
        )
        print(f"[REOPEN] status={r_reopen.status_code} body={r_reopen.text[:200]}")
        assert r_reopen.status_code == 200, (
            f"Reopen failed - manual cleanup required! {r_reopen.text}"
        )
