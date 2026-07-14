"""
iter90fr : REGRESSION LOCK - bilan "apres repartition" DESEQUILIBRE
(Actif != Passif) quand le boni/mali (compte 499) n'a pas pu etre
redistribue aux proprietaires.

Ticket utilisateur (Feb 2026, PRODUCTION, ACP Acacia TER) :
> "dans le bilan après réparition il faut que le compte de régularisation
> 499 Compte de regularisation - Boni a repartir 7053.57 soit réparti sur
> les propriétaires sur base de leurs quotités la tu as supprimé le poste
> sans le répartir donc ton bilan est faux actif n'est pas égal à passif!
> c'est une règle stricte dans les bilan après répartition, les comptes de
> régularisation sont répartis sur les propriétaires!"

Reproduction : PDF production montrait Total Actif = 16210,17€ / Total
Passif = 9156,60€, ecart = 7053,57€ = EXACTEMENT le montant du boni. Le
compte 499 avait bien ete retire de l'affichage (mode after_distribution
correctement route depuis iter90fq) MAIS jamais recredite a aucun
proprietaire.

Root cause (2 aspects) :
1. La repartition par quotite (`routes/reports.py::compute_bilan_data`)
   n'utilisait QUE `lot.owner_id` (singulier). Or de nombreux lots
   (co-propriete/co-indivision, import legacy) n'ont PAS `owner_id` rempli
   et utilisent uniquement `owner_ids` (liste) - meme convention deja
   utilisee ailleurs dans l'app (`duplicates.py`, `banking.py` : `$or
   owner_id/owner_ids`). Ces lots etaient silencieusement IGNORES du
   calcul de `total_quotities`, pouvant le faire chuter a 0 (aucune
   redistribution possible) ou a une valeur trop faible.
2. Aucun garde-fou : quand `total_quotities` finissait a 0 (donnees de
   lots incompletes pour une ACP), le code supprimait quand meme
   l'affichage du 499 (puisqu'on est dans la branche "after_distribution")
   SANS jamais recrediter personne -> le montant disparaissait purement
   et simplement, cassant l'equation Actif = Passif.

Fix :
- `owner_ids` (liste) est desormais pris en compte en plus de `owner_id`
  (singulier), avec partage egal de la quotite du lot entre co-proprietaires
  quand plusieurs owner_ids sont presents sur un meme lot.
- GARDE-FOU : si `total_quotities` reste a 0 malgre tout (aucun lot/quotite
  exploitable pour cette ACP), le compte 499 est REAFFICHE (comportement
  "avant repartition" pour ce montant precis, avec mention explicite
  "quotites manquantes") plutot que d'etre silencieusement supprime -
  garantit l'invariant strict Actif == Passif dans TOUS les cas.

Ce test verifie :
A) Un lot en co-propriete (owner_id vide, owner_ids=[o1, o2]) redistribue
   correctement le boni 50/50 entre les 2 proprietaires, bilan equilibre.
B) Une ACP sans AUCUN lot exploitable (garde-fou) garde le bilan equilibre
   en reaffichant le 499 au lieu de le faire disparaitre.
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

from routes.reports import compute_bilan_data  # noqa: E402


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _get_anomaly_amounts(bilan, names):
    found = []
    for side in ("actif", "passif"):
        for r in bilan[side]:
            for a in r["accounts"]:
                if a["account_name"] in names:
                    found.append((a["account_name"], a["amount"]))
    return found


async def _scenario_co_ownership_via_owner_ids():
    db = await _mongo()
    cid = f"iter90fr-multi-{uuid.uuid4()}"
    o1, o2 = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fr-multi", "reference": "M", "status": "active"})
        await db.owners.insert_many([
            {"id": o1, "name": "Owner A iter90fr", "tier_accounts": {}},
            {"id": o2, "name": "Owner B iter90fr", "tier_accounts": {}},
        ])
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "001",
            "quotity": 1000.0, "owner_id": "", "owner_ids": [o1, o2],
        })
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "VE", "date": "2025-11-01",
            "reference": "VE-1", "total_debit": 1000.0, "total_credit": 1000.0,
            "lines": [
                {"account_number": "55000", "debit": 1000.0, "credit": 0},
                {"account_number": "70000", "debit": 0, "credit": 1000.0},
            ],
        })
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"], f"REGRESSION iter90fr : bilan desequilibre {after}"
        amounts = _get_anomaly_amounts(after, {"Owner A iter90fr", "Owner B iter90fr"})
        assert len(amounts) == 2, (
            f"REGRESSION iter90fr : les 2 co-proprietaires (owner_ids) "
            f"doivent etre credites du boni, trouve {amounts}"
        )
        for name, amount in amounts:
            assert abs(amount - 500.0) < 0.01, f"{name} devrait recevoir 500.0 (partage egal), trouve {amount}"
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [o1, o2]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _scenario_no_lots_safety_net_keeps_balance():
    db = await _mongo()
    cid = f"iter90fr-nolots-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fr-nolots", "reference": "N", "status": "active"})
        # AUCUN lot cree -> total_quotities = 0, reproduit le bug production
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "VE", "date": "2025-11-01",
            "reference": "VE-1", "total_debit": 1000.0, "total_credit": 1000.0,
            "lines": [
                {"account_number": "55000", "debit": 1000.0, "credit": 0},
                {"account_number": "70000", "debit": 0, "credit": 1000.0},
            ],
        })
        before = await compute_bilan_data(db, cid, view_mode="before_distribution")
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert before["equilibre"]
        assert after["equilibre"], (
            f"REGRESSION iter90fr NON CORRIGEE : bilan 'apres repartition' "
            f"desequilibre quand aucune quotite n'est disponible - "
            f"actif={after['total_actif']} passif={after['total_passif']}"
        )
        assert after["total_actif"] == before["total_actif"], (
            "Le garde-fou doit reafficher le 499 (memes totaux qu'avant-repartition) "
            "quand la redistribution est impossible"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_co_ownership_via_owner_ids_list():
    asyncio.run(_scenario_co_ownership_via_owner_ids())


def test_no_lots_safety_net_keeps_balance():
    asyncio.run(_scenario_no_lots_safety_net_keeps_balance())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
