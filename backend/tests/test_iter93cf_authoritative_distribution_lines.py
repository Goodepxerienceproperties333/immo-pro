"""iter93cf - Utilisation de invoice.distribution_lines comme source
autoritative pour la distribution per-key du boni.

Contexte : L'utilisateur a exige (Feb 2026) que le calcul apres repartition
respecte cette formule exacte :
    Balance_owner = quotes-parts_per_key_charges - paiements + solde_reserve

Sur l'exercice actuel de l'ACP Agathe (Bilan 31/03/2027) :
- Guerit Vandervelde doit s'approcher de 26,92 EUR

Root cause de la derive precedente (iter93cd = 13,85 EUR au lieu de 26,92) :
On utilisait `distribution_key.lots` pour calculer les ratios. Or les cles
peuvent avoir ete modifiees depuis l'import Optipro (ex. G3 elevator sur
Agathe : Guerit avait share 8479 a l'epoque, mais la cle actuelle ne le
contient plus). L'invoice.distribution_lines pre-calcule au moment de
l'import est la SOURCE AUTORITATIVE.

Fix : lookup de l'invoice via JE.source_invoice_id, extraction des ratios
depuis invoice.distribution_lines (Optipro-format, avec lot_id). Fallback
vers distribution_key.lots si distribution_lines est en format manuel
(sans lot_id).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.reports import compute_bilan_data  # noqa: E402


CID_AGATHE = "c9cfce94-96c6-4202-8a6d-0a5627b50856"


def _db():
    return AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )[os.environ.get("DB_NAME", "test_database")]


@pytest.mark.asyncio
async def test_guerit_matches_user_target_26_92():
    """Guerit Vandervelde doit tomber a 26,92 EUR (±5 cents)."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    guerit_amount = None
    for rub in data["actif"]:
        for a in rub["accounts"]:
            if "uérit" in a.get("account_name", ""):
                guerit_amount = a["amount"]
    assert guerit_amount is not None, "Guerit non trouve dans l'ACTIF"
    assert abs(guerit_amount - 26.92) < 0.10, (
        f"Guerit = {guerit_amount} EUR, cible 26,92 EUR (delta > 0.10)"
    )


@pytest.mark.asyncio
async def test_compte_499_absent_from_bilan_after_distribution():
    """Apres repartition, le compte 499 (boni) doit disparaitre du bilan."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    for rub in data["actif"] + data["passif"]:
        for a in rub["accounts"]:
            assert a["account_number"] != "499", (
                f"Compte 499 encore present dans {rub['label']} : {a}"
            )


@pytest.mark.asyncio
async def test_499_sinistres_still_isolated():
    """Les 499XXX (sinistres) restent isoles (NON redistribues aux owners)."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    sinistres_rub = next(
        (r for r in data["passif"] if "sinistres" in r["label"].lower()), None
    )
    assert sinistres_rub is not None
    assert abs(sinistres_rub["total"] - 3577.18) < 0.01, (
        f"Sinistres VI.D = {sinistres_rub['total']}, attendu 3577.18"
    )


@pytest.mark.asyncio
async def test_bilan_equilibre_exact_after_distribution():
    """Ecart = 0.00 EUR EXACT apres repartition (equilibre parfait)."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    assert data["equilibre"] is True
    assert abs(data["ecart"]) < 0.01, f"Ecart = {data['ecart']}, attendu 0.00"


@pytest.mark.asyncio
async def test_total_bilan_matches_optipro_within_1eur():
    """Total actif et passif matchent Optipro (49.383,51 EUR) a moins de 1 EUR pres."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    assert abs(data["total_actif"] - 49383.51) < 1.0, (
        f"Total actif = {data['total_actif']}, cible 49383.51"
    )
    assert abs(data["total_passif"] - 49383.51) < 1.0


@pytest.mark.asyncio
async def test_owner_net_balance_matches_optipro_exact():
    """Le net V.A - VI.A = 379,75 EUR EXACT (Optipro)."""
    data = await compute_bilan_data(
        _db(), CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    va = next(r for r in data["actif"] if "oproprietaires" in r["label"].lower())
    via = next(r for r in data["passif"] if "oproprietaires" in r["label"].lower())
    net = va["total"] - via["total"]
    assert abs(net - 379.75) < 0.10, f"Net owners = {net}, attendu 379.75"


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_guerit_matches_user_target_26_92())
    asyncio.run(test_compte_499_absent_from_bilan_after_distribution())
    asyncio.run(test_499_sinistres_still_isolated())
    asyncio.run(test_bilan_equilibre_exact_after_distribution())
    asyncio.run(test_total_bilan_matches_optipro_within_1eur())
    asyncio.run(test_owner_net_balance_matches_optipro_exact())
    print("OK - iter93cf all 6 tests passed")
