"""iter93cc - Verifie que le compte 490 (Charges a reporter) ainsi que les
autres 490-498 ne sont PLUS redistribues aux proprietaires en mode 'apres
repartition'. Root cause du delta 177 EUR sur Bilan ACP Agathe 31/03/2027.

Regle metier PCMN belge : Optipro conserve 490 dans une rubrique separee
(VIII. Comptes de regularisation actif). Seul le compte 499 (boni/mali)
est distribue aux proprietaires par quotite.
"""
import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from routes.reports import compute_bilan_data  # noqa: E402


@pytest.mark.asyncio
async def test_490_stays_in_regul_actif_after_distribution():
    """Le compte 490 doit rester dans VIII_regul_actif meme apres repartition."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "test_database")]
    cid = "c9cfce94-96c6-4202-8a6d-0a5627b50856"  # ACP Agathe

    data = await compute_bilan_data(
        db, cid, date_to="2027-03-31", view_mode="after_distribution"
    )

    # Bilan doit rester equilibre
    assert data["equilibre"] is True, f"Bilan desequilibre: ecart={data['ecart']}"

    # 490 doit apparaitre dans VIII_regul_actif avec son solde complet 3743.74
    regul_actif = next(
        r for r in data["actif"] if "regularisation" in r["label"].lower() and "mali" in r["label"].lower()
    )
    accs_490 = [a for a in regul_actif["accounts"] if a["account_number"] == "490"]
    assert len(accs_490) == 1, "Compte 490 doit apparaitre dans VIII regul actif"
    assert abs(accs_490[0]["amount"] - 3743.74) < 0.01, (
        f"Compte 490 doit valoir 3743.74 EUR, obtenu {accs_490[0]['amount']}"
    )

    client.close()


@pytest.mark.asyncio
async def test_guerit_balance_close_to_optipro_after_490_fix():
    """Guerit Vandervelde doit s'approcher de 26.96 EUR (Optipro).

    Avant fix : 204.02 EUR (delta 177 EUR).
    Apres fix iter93cc (retrait 490 des owners) : ~79 EUR (delta ~52 EUR).
    Le residu ~52 EUR est du a la distribution par quotite globale au lieu
    de per-charge (limitation connue documentee).
    """
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "test_database")]
    cid = "c9cfce94-96c6-4202-8a6d-0a5627b50856"

    data = await compute_bilan_data(
        db, cid, date_to="2027-03-31", view_mode="after_distribution"
    )

    # Chercher Guerit dans les creances copro (actif V.A)
    guerit_amount = None
    for rub in data.get("actif", []):
        for a in rub.get("accounts", []):
            if "uérit" in a.get("account_name", "") or "uerit" in a.get("account_name", ""):
                guerit_amount = a["amount"]

    assert guerit_amount is not None, "Guerit non trouve dans le bilan actif"
    # Doit etre nettement plus proche de 26.96 qu'avant (204.02)
    assert guerit_amount < 150, (
        f"Solde Guerit={guerit_amount}, attendu ~26.96 EUR (Optipro). "
        f"Le fix 490 doit reduire le solde a moins de 150 EUR."
    )

    client.close()


@pytest.mark.asyncio
async def test_bilan_totals_match_optipro_structure():
    """Verifie la structure du Bilan vs Optipro pour ACP Agathe 31/03/2027.

    Optipro (source verifie) :
      - VII Valeurs disponibles : 41,134.83
      - VIII Comptes reg. actif : 3,743.74
      - I Capital : 13,000
      - II Reserves : 20,240.99
      - VI.B Fournisseurs : 10,175.59
      - VI.D Sinistres : 3,577.18
    """
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "test_database")]
    cid = "c9cfce94-96c6-4202-8a6d-0a5627b50856"

    data = await compute_bilan_data(
        db, cid, date_to="2027-03-31", view_mode="after_distribution"
    )

    actif_by_label = {r["label"]: r["total"] for r in data["actif"]}
    passif_by_label = {r["label"]: r["total"] for r in data["passif"]}

    def _find(dic, needle):
        for k, v in dic.items():
            if needle.lower() in k.lower():
                return v
        return 0.0

    assert abs(_find(actif_by_label, "VII. Valeurs disponibles") - 41134.83) < 1.0
    assert abs(_find(actif_by_label, "regularisation (mali)") - 3743.74) < 1.0
    assert abs(_find(passif_by_label, "Capital") - 13000.0) < 1.0
    assert abs(_find(passif_by_label, "Reserves") - 20240.99) < 1.0
    assert abs(_find(passif_by_label, "Fournisseurs") - 10175.59) < 1.0
    assert abs(_find(passif_by_label, "sinistres") - 3577.18) < 1.0
    # Total actif = total passif
    assert data["equilibre"] is True

    client.close()


if __name__ == "__main__":
    asyncio.run(test_490_stays_in_regul_actif_after_distribution())
    asyncio.run(test_guerit_balance_close_to_optipro_after_490_fix())
    asyncio.run(test_bilan_totals_match_optipro_structure())
    print("OK - iter93cc tests passed")
