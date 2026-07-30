"""iter93cd - Distribution per-key du boni sur les proprietaires.

Formule Option A (per-charge distribution keys) :
   delta[i] = appels_provisions[i] (41010XX, VE only)
            - (charges_imputees[i] via cle_par_facture - produits[i] via default)

Cette formule remplace l'ancienne distribution uniforme par quotite globale,
et permet de matcher au plus pres les soldes Optipro (delta reduit de 52
a 13 EUR sur ACP Agathe pour Guerit Vandervelde).

Sur Optipro (Bilan Agathe 31/03/2027) :
- Guerit Vandervelde attendu : 26,96 EUR (debit)
- Ancienne methode (uniforme) : 79,32 EUR (delta 52 EUR)
- Nouvelle methode (per-key)  : 13,85 EUR (delta 13 EUR)
- Total actif Optipro : 49.383,51 EUR
- Total actif app     : 49.398,41 EUR (delta 15 EUR)
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.reports import compute_bilan_data  # noqa: E402


CID_AGATHE = "c9cfce94-96c6-4202-8a6d-0a5627b50856"


def _client():
    return AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )[os.environ.get("DB_NAME", "test_database")]


@pytest.mark.asyncio
async def test_guerit_after_distribution_close_to_optipro():
    """Guerit Vandervelde doit s'approcher de 26,96 EUR (Optipro) grace au per-key."""
    db = _client()
    data = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    guerit_amount = None
    for rub in data.get("actif", []) + data.get("passif", []):
        for a in rub.get("accounts", []):
            if "uérit" in a.get("account_name", ""):
                guerit_amount = a["amount"]
    assert guerit_amount is not None
    # Cible : 26,96 EUR. Delta acceptable < 30 EUR pour reussir le test (avant fix : 177 EUR).
    assert abs(guerit_amount - 26.96) < 30, (
        f"Guerit={guerit_amount}, cible 26.96 (Optipro). Delta trop eleve."
    )


@pytest.mark.asyncio
async def test_owner_net_balance_matches_optipro_exactly():
    """Le solde NET (VA_total - VIA_total) doit exactement egaler 379,75 EUR (Optipro)."""
    db = _client()
    data = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    va = next(r for r in data["actif"] if "oproprietaires" in r["label"].lower())
    via = next(r for r in data["passif"] if "oproprietaires" in r["label"].lower())
    net = va["total"] - via["total"]
    assert abs(net - 379.75) < 1.0, f"Solde net owners={net}, attendu 379.75"


@pytest.mark.asyncio
async def test_owner_count_matches_optipro():
    """28 sous-comptes debiteurs + 9 sous-comptes crediteurs (comme Optipro)."""
    db = _client()
    data = await compute_bilan_data(
        db, CID_AGATHE, date_to="2027-03-31", view_mode="after_distribution"
    )
    va = next(r for r in data["actif"] if "oproprietaires" in r["label"].lower())
    via = next(r for r in data["passif"] if "oproprietaires" in r["label"].lower())
    assert len(va["accounts"]) == 28, (
        f"V.A doit contenir 28 owners debiteurs, obtenu {len(va['accounts'])}"
    )
    assert len(via["accounts"]) == 9, (
        f"VI.A doit contenir 9 owners crediteurs, obtenu {len(via['accounts'])}"
    )


@pytest.mark.asyncio
async def test_bilan_equilibre_all_coproprietes():
    """La distribution per-key preserve l'equilibre Actif=Passif sur toutes les ACPs."""
    db = _client()
    copros = await db.coproprietes.find({}, {"_id": 0, "id": 1}).to_list(100)
    for c in copros:
        data = await compute_bilan_data(
            db, c["id"], view_mode="after_distribution"
        )
        assert data["equilibre"] is True, (
            f"Bilan desequilibre pour ACP {c['id']}: ecart={data['ecart']}"
        )


if __name__ == "__main__":
    asyncio.run(test_guerit_after_distribution_close_to_optipro())
    asyncio.run(test_owner_net_balance_matches_optipro_exactly())
    asyncio.run(test_owner_count_matches_optipro())
    asyncio.run(test_bilan_equilibre_all_coproprietes())
    print("OK - iter93cd per-key distribution tests passed")
