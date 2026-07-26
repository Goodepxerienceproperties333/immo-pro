"""iter90gz : Bilan - les OD classe 6 DOIVENT reduire total_charges pour
preserver l'equilibre Actif = Passif.

RCA (bug rapporte par user) :
Un OD "Financement par fonds de reserve" ecrit :
  - Debit  16000 (fonds de reserve, classe 1) 3545.30
  - Credit 61066 (charges Sneyers, classe 6)  3545.30

Impact bilan attendu (double partie preservee) :
  - PASSIF : reserve (13X/16X) baisse de 3545.30
  - PASSIF : boni 499 augmente de 3545.30 (Produits - Charges nettes)
  - -> Equilibre Actif = Passif preserve.

Bug precedent (iter90g3b) : le code excluait les OD du calcul de
`total_charges` -> le boni 499 ne compensait pas la baisse de la reserve
-> Bilan DESEQUILIBRE (ecart = montant du financement).

Fix : integrer TOUTES les lignes classe 6 dans total_charges,
independamment du journal_type.
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


def _mk_seed(with_od=True, extra_od_lines=None):
    async def _seed():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:6]
        cid = f"TEST-iter90gz-{suffix}"
        fy_id = f"TEST-fy-{suffix}"

        await db.coproprietes.insert_one({
            "id": cid, "name": f"TEST_gz_{suffix}", "status": "active",
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

        # (a) AC : facture Sneyers - debit 61066 3545.30, credit 44xxx 3545.30
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

        # (b) OD financement par fonds de reserve : credit 61066 + debit 16x
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

        # (c) VE : appel de provisions 4715.46
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


def test_od_included_in_total_charges():
    """AC 3545.30 + OD credit -3545.30 => total_charges = 0."""
    cid, fy_id = _mk_seed(with_od=True)
    try:
        data = _run_bilan(cid, fy_id)
        assert abs(data["total_charges"] - 0.0) < 0.01, (
            f"total_charges expected 0.00 (AC 3545.30 - OD 3545.30), "
            f"got {data['total_charges']}"
        )
        # 499 = provisions (4715.46) - charges (0) = 4715.46 (boni)
        assert abs(data["compte_499"] - 4715.46) < 0.01, (
            f"compte_499 (boni) expected 4715.46, got {data['compte_499']}"
        )
        assert abs(data["provisions_appelees"] - 4715.46) < 0.01
    finally:
        _cleanup(cid)


def test_bilan_is_balanced_with_od_financing():
    """L'equilibre Actif = Passif DOIT etre preserve avec OD financement."""
    cid, fy_id = _mk_seed(with_od=True)
    try:
        data = _run_bilan(cid, fy_id)
        assert data["equilibre"] is True, (
            f"Bilan DESEQUILIBRE : Actif={data['total_actif']} vs "
            f"Passif={data['total_passif']} (ecart={data['ecart']})"
        )
        assert abs(data["ecart"]) < 0.01
    finally:
        _cleanup(cid)


def test_no_od_regression_boni_matches_ac_only():
    """Sans OD : 499 = provisions - AC = 4715.46 - 3545.30 = 1170.16."""
    cid, fy_id = _mk_seed(with_od=False)
    try:
        data = _run_bilan(cid, fy_id)
        assert abs(data["total_charges"] - 3545.30) < 0.01
        assert abs(data["compte_499"] - 1170.16) < 0.01
        assert data["equilibre"] is True
    finally:
        _cleanup(cid)


def test_multiple_od_lines_all_included():
    """Multi-OD : chaque credit OD classe 6 reduit total_charges."""
    cid, fy_id = _mk_seed(with_od=True, extra_od_lines=[600.0, 800.0])
    try:
        data = _run_bilan(cid, fy_id)
        # AC 3545.30 - (OD 3545.30 + 600 + 800) = -1400
        assert abs(data["total_charges"] - (-1400.0)) < 0.01, (
            f"total_charges expected -1400.00, got {data['total_charges']}"
        )
        # 499 = 4715.46 - (-1400) = 6115.46
        assert abs(data["compte_499"] - 6115.46) < 0.01
        assert data["equilibre"] is True
    finally:
        _cleanup(cid)
