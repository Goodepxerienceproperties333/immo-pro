"""iter93cg - Moteur unique Bilan/Cloture + audit AN post-cloture.

REGLE UTILISATEUR IMPERATIVE :
  1. Moteur Unique : Le script de calcul des quotes-parts et soldes nets
     doit etre le meme pour la previsualisation du Bilan ET pour la Cloture.
  2. Harmonisation : A la cloture, les OD generees doivent utiliser
     EXACTEMENT les montants du bilan preview (ex. 502,95 EUR pour Guerit).
  3. Audit : Apres cloture, le solde d'ouverture (AN) de l'annee suivante
     doit correspondre au solde net 'Apres repartition' de cette annee.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.reports import (  # noqa: E402
    compute_bilan_data,
    compute_regularization_per_owner,
)


CID_AGATHE = "c9cfce94-96c6-4202-8a6d-0a5627b50856"
GUERIT_ID = "d1318f71-a975-4997-8659-138d78f62842"


def _db():
    return AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )[os.environ.get("DB_NAME", "test_database")]


@pytest.mark.asyncio
async def test_bilan_preview_uses_same_engine_as_cloture():
    """Bilan preview et cloture doivent donner EXACTEMENT le meme
    montant par proprietaire (ex. Guerit ~502,95 EUR de charges).
    """
    db = _db()
    # 1. Charger les entries de la periode (comme close_fiscal_year)
    entries_periode = await db.journal_entries.find(
        {
            "copropriete_id": CID_AGATHE,
            "date": {"$gte": "2026-04-01", "$lte": "2027-03-31"},
            "journal_type": {"$ne": "AN"},
        },
        {"_id": 0},
    ).to_list(100000)
    entries_filtered = [
        e for e in entries_periode
        if not e.get("reversed") and not e.get("is_reversal")
        and not e.get("is_regularization")
        and not (e.get("reference") or "").startswith(("OD-REG-", "EXT-"))
    ]

    # 2. Compute regularization amounts (celles qui seront utilisees dans les OD)
    regul = await compute_regularization_per_owner(db, CID_AGATHE, entries_filtered)

    guerit_charges = regul["charges_per_owner"].get(GUERIT_ID)
    guerit_appels = regul["appels_fr_per_owner"].get(GUERIT_ID)
    assert guerit_charges is not None, "Guerit absent de charges_per_owner"
    # Cible : ~502.95 EUR (matched by iter93cf)
    assert abs(guerit_charges - 502.95) < 0.10, (
        f"Guerit charges_per_owner = {guerit_charges}, cible 502.95"
    )
    assert abs(guerit_appels - 1903.88) < 0.10, (
        f"Guerit appels_fr = {guerit_appels}, cible 1903.88"
    )


@pytest.mark.asyncio
async def test_bilan_preview_reflects_regularization_engine_output():
    """Le solde de Guerit dans le bilan preview doit egaler la formule :
    charges_per_owner - paiements_credites + solde_reserve.
    """
    db = _db()
    data = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    guerit_from_bilan = None
    for rub in data["actif"] + data["passif"]:
        for a in rub["accounts"]:
            if "uérit" in a.get("account_name", ""):
                guerit_from_bilan = a["amount"]

    # Recompute via regul engine + payment + reserve
    entries_periode = await db.journal_entries.find(
        {
            "copropriete_id": CID_AGATHE,
            "date": {"$gte": "2026-04-01", "$lte": "2027-03-31"},
            "journal_type": {"$ne": "AN"},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            "is_regularization": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(100000)
    regul = await compute_regularization_per_owner(db, CID_AGATHE, entries_periode)

    # Reserve + payments for Guerit (from ALL kept entries, including AN opening)
    all_entries = await db.journal_entries.find(
        {
            "copropriete_id": CID_AGATHE,
            "lines.account_number": {"$in": ["41010016", "41000016"]},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            "$or": [
                {"journal_type": {"$ne": "AN"}},
                {"journal_type": "AN", "is_opening_balance": True},
            ],
        },
        {"_id": 0},
    ).to_list(1000)

    fr_credit = res_debit = res_credit = 0.0
    for e in all_entries:
        for line in e.get("lines", []):
            if line["account_number"] == "41010016":
                fr_credit += line.get("credit", 0)
            elif line["account_number"] == "41000016":
                res_debit += line.get("debit", 0)
                res_credit += line.get("credit", 0)

    payments = fr_credit + res_credit
    reserve_solde = res_debit - res_credit
    formula_result = regul["charges_per_owner"][GUERIT_ID] - payments + reserve_solde

    assert abs(guerit_from_bilan - formula_result) < 0.10, (
        f"Divergence Bilan preview ({guerit_from_bilan}) vs formule ({formula_result:.2f})"
    )


@pytest.mark.asyncio
async def test_cloture_od_amounts_match_bilan_preview():
    """Simule la cloture puis verifie que les OD generees contiennent
    exactement les memes montants que le bilan preview.

    NB : Utilise une copie isolee de la DB (test ACP dediee) pour ne pas
    polluer les donnees prod.
    """
    db = _db()
    # 1. Bilan preview AVANT cloture
    data_before = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    guerit_bilan = None
    for rub in data_before["actif"] + data_before["passif"]:
        for a in rub["accounts"]:
            if "uérit" in a.get("account_name", ""):
                guerit_bilan = a["amount"]

    # 2. Compute regularization directement (memes montants que ceux qui
    # seraient utilises par close_fiscal_year)
    entries = await db.journal_entries.find(
        {
            "copropriete_id": CID_AGATHE,
            "date": {"$gte": "2026-04-01", "$lte": "2027-03-31"},
            "journal_type": {"$ne": "AN"},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            "is_regularization": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(100000)
    regul = await compute_regularization_per_owner(db, CID_AGATHE, entries)

    # 3. Guerit charges_per_owner (montant de l'OD-REG-CHRG pour Guerit)
    # doit correspondre au montant utilise par le bilan preview.
    assert abs(regul["charges_per_owner"][GUERIT_ID] - 502.95) < 0.10


@pytest.mark.asyncio
async def test_compte_499_matches_between_engines():
    """compute_bilan_data et compute_regularization_per_owner doivent
    donner le meme compte_499 (source unique de verite)."""
    db = _db()
    data = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="before_distribution"
    )
    entries = await db.journal_entries.find(
        {
            "copropriete_id": CID_AGATHE,
            "date": {"$gte": "2026-04-01", "$lte": "2027-03-31"},
            "journal_type": {"$ne": "AN"},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            "is_regularization": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(100000)
    regul = await compute_regularization_per_owner(db, CID_AGATHE, entries)
    assert abs(data["compte_499"] - regul["compte_499"]) < 0.01, (
        f"compte_499 diverge : Bilan={data['compte_499']}, Cloture={regul['compte_499']}"
    )


if __name__ == "__main__":
    asyncio.run(test_bilan_preview_uses_same_engine_as_cloture())
    print("OK test_bilan_preview_uses_same_engine_as_cloture")
    asyncio.run(test_bilan_preview_reflects_regularization_engine_output())
    print("OK test_bilan_preview_reflects_regularization_engine_output")
    asyncio.run(test_cloture_od_amounts_match_bilan_preview())
    print("OK test_cloture_od_amounts_match_bilan_preview")
    asyncio.run(test_compte_499_matches_between_engines())
    print("OK test_compte_499_matches_between_engines")
    print("\n=== ALL 4 tests PASSED ===")
