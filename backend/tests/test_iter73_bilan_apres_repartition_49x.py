"""Regression test - iter73 - Bilan "Apres repartition" : equilibre des comptes 49X.

Bug : en mode `view_mode=after_distribution`, les comptes de regularisation
(49X hors 499 synthetique) etaient distribues sur les proprietaires avec un signe
inverse (regul ACTIF -> owner CREDIT/passif, regul PASSIF -> owner DEBIT/actif).
Resultat : ecart = 2 * (|sum 49X actif - sum 49X passif|) sur le bilan.

Pour ACP Gaura (cas reel) :
    Total 49X actif (D-C > 0)  = 3369.38
    Total 49X passif (D-C < 0) = 14884.28
    Diff = 11514.90
    Ecart bilan = 2 * 11514.90 = 23029.80  (match exact du bug user)

Fix : la nature (debit/credit) du compte 49X est PRESERVEE sur le compte
proprietaire (changement de rubrique de presentation, pas re-affectation comptable).
    49X ACTIF  -> owner DEBIT  (reste cote ACTIF, rubrique V.A)
    49X PASSIF -> owner CREDIT (reste cote PASSIF, rubrique VI.A)
"""
import os
import sys
import asyncio

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _run():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.reports import create_reports_router

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    coproprietes = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(100)
    target_acps = []
    for c in coproprietes:
        n49 = await db.journal_entries.count_documents({
            "copropriete_id": c["id"],
            "lines.account_number": {"$regex": "^49"},
        })
        if n49 > 0:
            target_acps.append(c)

    if not target_acps:
        return  # rien a verifier

    router = create_reports_router(db)
    bilan_fn = None
    for route in router.routes:
        if route.path == "/api/reports/bilan":
            bilan_fn = route.endpoint
            break
    assert bilan_fn is not None, "Endpoint /api/reports/bilan introuvable"

    class _Req:
        headers = {}

    for c in target_acps:
        for vm in ("before_distribution", "after_distribution"):
            res = await bilan_fn(
                request=_Req(),
                date_to=None,
                copropriete_id=c["id"],
                fiscal_year_id=None,
                view_mode=vm,
            )
            assert abs(res["ecart"]) < 0.02, (
                f"Bilan {vm} desequilibre pour ACP {c['name']} : "
                f"actif={res['total_actif']} passif={res['total_passif']} "
                f"ecart={res['ecart']}"
            )
            assert res["equilibre"] is True, (
                f"equilibre=False pour ACP {c['name']} en mode {vm} "
                f"(actif={res['total_actif']} passif={res['total_passif']})"
            )


def test_bilan_apres_repartition_is_balanced():
    """Verifie que le bilan est equilibre en mode before et after pour toutes
    les ACPs qui contiennent des comptes 49X."""
    asyncio.run(_run())


if __name__ == "__main__":
    test_bilan_apres_repartition_is_balanced()
    print("OK : bilan equilibre sur toutes les ACPs ayant des comptes 49X")
