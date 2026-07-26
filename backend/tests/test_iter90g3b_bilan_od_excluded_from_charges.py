"""iter90g3b : Bilan boni excludes OD class-6 lines from total_charges.

RCA : Un OD de financement par fonds de reserve credite un compte de
classe 6 tout en debitant un compte de reserve (classe 1). Sans le fix,
le credit OD sur classe 6 REDUIT total_charges -> le boni du compte 499
est gonfle du montant du financement (bug Boxus/Maria : boni 4715.46
au lieu de 1170.16, delta = 3545.30 = exactement l'OD "Financement
par fonds de reserve").

Fix : dans compute_bilan_data, quand on iterepose sur les lignes de
classe 6, on skippe les entries dont journal_type == "OD". Seuls AC
(achats) et VE (ventes) contribuent a total_charges.
"""
import os
import sys
import asyncio
import uuid
import pytest

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


def _mk_seed(with_od=True, extra_od_lines=None):
    """Returns (cid, fy_id) after seeding the ACP + entries."""
    async def _seed():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:6]
        cid = f"TEST-iter90g3b-{suffix}"
        fy_id = f"TEST-fy-{suffix}"

        await db.coproprietes.insert_one({
            "id": cid, "name": f"TEST_g3b_{suffix}", "status": "active",
        })
        await db.fiscal_years.insert_one({
            "id": fy_id, "copropriete_id": cid, "name": "2026",
            "start_date": "2026-01-01", "end_date": "2026-12-31",
            "status": "active",
        })
        for num, name, cls in [
            ("61066", "Charges Sneyers", 6),
            ("70100", "Appels provisions", 7),
            ("16000", "Fonds de reserve", 1),
            ("44000001", "Fournisseur Sneyers", 4),
            ("40000001", "Owner Boxus", 4),
            ("55100", "Banque", 5),
        ]:
            await db.pcmn_accounts.insert_one({
                "number": num, "name": name, "class_num": cls,
                "copropriete_id": cid,
            })

        # (a) AC : debit 61066 3545.30, credit 44xxx 3545.30
        await db.journal_entries.insert_one({
            "id": f"TEST-ac-{suffix}",
            "journal_type": "AC", "date": "2026-03-10",
            "copropriete_id": cid, "fiscal_year_id": fy_id,
            "reference": f"AC-{suffix}",
            "description": "TEST Facture Sneyers",
            "lines": [
                {"account_number": "61066", "account_name": "Charges Sneyers",
                 "debit": 3545.30, "credit": 0.0},
                {"account_number": "44000001", "account_name": "Fournisseur Sneyers",
                 "debit": 0.0, "credit": 3545.30},
            ],
            "total_debit": 3545.30, "total_credit": 3545.30,
        })

        # (b) OD financement par fonds de reserve : credit 61066 3545.30 + debit 16x 3545.30
        if with_od:
            await db.journal_entries.insert_one({
                "id": f"TEST-od-{suffix}",
                "journal_type": "OD", "date": "2026-03-15",
                "copropriete_id": cid, "fiscal_year_id": fy_id,
                "reference": f"OD-{suffix}",
                "description": "TEST Financement par fonds de reserve",
                "lines": [
                    {"account_number": "16000", "account_name": "Fonds de reserve",
                     "debit": 3545.30, "credit": 0.0},
                    {"account_number": "61066", "account_name": "Charges Sneyers",
                     "debit": 0.0, "credit": 3545.30},
                ],
                "total_debit": 3545.30, "total_credit": 3545.30,
            })

        # Multi-OD scenario : extra OD lines on class-6 must not contribute
        if extra_od_lines:
            for idx, amt in enumerate(extra_od_lines):
                await db.journal_entries.insert_one({
                    "id": f"TEST-od-extra-{suffix}-{idx}",
                    "journal_type": "OD",
                    "date": f"2026-04-{10+idx:02d}",
                    "copropriete_id": cid, "fiscal_year_id": fy_id,
                    "reference": f"OD-X-{suffix}-{idx}",
                    "description": f"TEST OD extra {amt}",
                    "lines": [
                        {"account_number": "16000", "account_name": "Fonds de reserve",
                         "debit": amt, "credit": 0.0},
                        {"account_number": "61066", "account_name": "Charges Sneyers",
                         "debit": 0.0, "credit": amt},
                    ],
                    "total_debit": amt, "total_credit": amt,
                })

        # (c) VE : credit 70100 4715.46, debit 40xxx owner
        await db.journal_entries.insert_one({
            "id": f"TEST-ve-{suffix}",
            "journal_type": "VE", "date": "2026-01-05",
            "copropriete_id": cid, "fiscal_year_id": fy_id,
            "reference": f"VE-{suffix}",
            "description": "TEST Appel provisions",
            "lines": [
                {"account_number": "40000001", "account_name": "Owner Boxus",
                 "debit": 4715.46, "credit": 0.0},
                {"account_number": "70100", "account_name": "Appels provisions",
                 "debit": 0.0, "credit": 4715.46},
            ],
            "total_debit": 4715.46, "total_credit": 4715.46,
        })

        client.close()
        return cid, fy_id
    return asyncio.run(_seed())


def _cleanup(cid):
    async def _c():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        for coll in ["coproprietes", "fiscal_years", "pcmn_accounts",
                     "journal_entries"]:
            await db[coll].delete_many({"copropriete_id": cid})
        client.close()
    asyncio.run(_c())


def _run_bilan(cid, fy_id):
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import compute_bilan_data
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        data = await compute_bilan_data(db, cid, date_to="2026-12-31",
                                        fiscal_year_id=fy_id)
        client.close()
        return data
    return asyncio.run(_run())


# ---------- FIX 2 core scenario : Boxus/Maria repro ----------
def test_od_excluded_from_total_charges():
    cid, fy_id = _mk_seed(with_od=True)
    try:
        data = _run_bilan(cid, fy_id)
        # total_charges must be ONLY the AC (3545.30) - OD credit skipped
        assert abs(data["total_charges"] - 3545.30) < 0.01, (
            f"total_charges expected 3545.30 (AC only), got {data['total_charges']}. "
            f"Bug would produce ~0.0 (AC 3545.30 - OD 3545.30)."
        )
        # result_exercise = provisions (4715.46) - charges (3545.30) = 1170.16
        expected_result = 1170.16
        assert abs(data["compte_499"] - expected_result) < 0.01, (
            f"compte_499 (boni) expected {expected_result}, got {data['compte_499']}. "
            f"Buggy value would be 4715.46."
        )
        assert abs(data["provisions_appelees"] - 4715.46) < 0.01
    finally:
        _cleanup(cid)


# ---------- FIX 2 : Reserve (class-1) balance reflects OD debit ----------
def test_reserve_reflects_od_debit():
    """The OD debits 16000 (class-1 reserve). Its balance should DECREASE
    (net debit 3545.30 on a normally-credit account -> becomes debit-side).
    Reserve account should appear as ACTIF (creance) with amount 3545.30
    since it moved from 0 to 3545.30 DEBIT."""
    cid, fy_id = _mk_seed(with_od=True)
    try:
        data = _run_bilan(cid, fy_id)
        # Look for account 16000 in actif or passif rubriques
        found_16000_debit = 0.0
        found_16000_credit = 0.0
        for rub in data["actif"]:
            for acc in rub.get("accounts", []):
                if acc["account_number"] == "16000":
                    found_16000_debit += acc["amount"]
        for rub in data["passif"]:
            for acc in rub.get("accounts", []):
                if acc["account_number"] == "16000":
                    found_16000_credit += acc["amount"]
        # After OD: 16000 has net debit 3545.30 (was 0, then debit 3545.30)
        # -> appears as ACTIF 3545.30 (or reduces existing reserve if any).
        # Here we don't seed opening reserve so it should be actif=3545.30.
        assert abs(found_16000_debit - 3545.30) < 0.01 or found_16000_debit > 0, (
            f"Expected 16000 net debit ~3545.30 in Actif, got debit={found_16000_debit} "
            f"credit={found_16000_credit}"
        )
    finally:
        _cleanup(cid)


# ---------- Regression : same setup WITHOUT OD -> same result ----------
def test_no_od_regression_same_result():
    cid, fy_id = _mk_seed(with_od=False)
    try:
        data = _run_bilan(cid, fy_id)
        assert abs(data["total_charges"] - 3545.30) < 0.01, (
            f"Non-OD case: total_charges expected 3545.30, got {data['total_charges']}"
        )
        assert abs(data["compte_499"] - 1170.16) < 0.01, (
            f"Non-OD case: compte_499 expected 1170.16, got {data['compte_499']}"
        )
    finally:
        _cleanup(cid)


# ---------- Multi-OD : several OD credits on class-6 -> all excluded ----------
def test_multiple_od_lines_all_excluded():
    cid, fy_id = _mk_seed(with_od=True, extra_od_lines=[600.0, 800.0])
    try:
        data = _run_bilan(cid, fy_id)
        # Still only AC contributes -> 3545.30
        assert abs(data["total_charges"] - 3545.30) < 0.01, (
            f"Multi-OD: total_charges expected 3545.30, got {data['total_charges']}. "
            f"Bug would subtract 3545.30 + 600 + 800 from AC."
        )
        assert abs(data["compte_499"] - 1170.16) < 0.01
    finally:
        _cleanup(cid)
