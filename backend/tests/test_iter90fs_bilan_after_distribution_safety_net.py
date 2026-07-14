"""
iter90fs : REGRESSION LOCK - safety net renforcee sur le bilan "apres
repartition" quand un lot refere un `owner_id` (ou `owner_ids`) qui n'existe
plus dans la collection `owners` (fiche supprimee/renommee/donnees corrompues).

Ticket utilisateur (Feb 2026, PROD) :
> "Au niveau du bilan il y a 2 vues il y a une vue avant répartition ...
> avant répartition il est bon par contre après répartition il est mauvais
> je te demande de vérifier pourquoi et de corriger"

Symptome PROD : total_actif != total_passif, avec ecart = boni exactement.
Le compte 499 avait bien ete retire de l'affichage mais son montant n'avait
ete recredite a AUCUN proprietaire.

Cause residuelle apres iter90fr : le code faisait un `continue` silencieux
si `owner_doc = next(... if o["id"] == oid, None)` retournait None (owner
supprime en base mais lot pas mis a jour). La quote-part de cet owner
etait alors perdue, causant un ecart Actif != Passif = somme des parts
perdues.

Fix iter90fs :
1. Tracker `actual_boni_distributed` dans la boucle de repartition.
2. En fin de calcul, comparer avec `result_exercise`. Si difference > 0.01,
   REAFFICHER le residu sur le compte 499 avec un libelle "non reparti
   (proprietaire(s) introuvable(s))" - garantit strict Actif = Passif dans
   TOUS les cas, meme donnees corrompues.
3. Corollaire : suppression de l'ancien garde-fou `total_quotities <= 0`
   qui devient un cas particulier de la safety net generale.

Bonus : correction du numero de compte affiche pour les nouveaux
proprietaires (sans activite prealable) crees a l'occasion de la
redistribution -> `display_account` desormais renseigne depuis
`owner_primary_acc`.

Ce fichier verifie 3 scenarios :
A) `owner_id` refere une fiche supprimee -> quote-part reaffichee sur 499
   fallback, bilan reste equilibre.
B) `owner_ids` en co-indivision refere une fiche disparue -> meme fallback.
C) Owner sans activite prealable recevant sa part de redistribution -> son
   `display_account` est bien renseigne (pas de ligne blanche dans le PDF).
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


async def _find_account(bilan, side, account_number):
    for r in bilan[side]:
        for a in r["accounts"]:
            if a["account_number"] == account_number:
                return a
    return None


async def _sum_side(bilan, side, predicate):
    total = 0.0
    for r in bilan[side]:
        for a in r["accounts"]:
            if predicate(a):
                total += a["amount"]
    return total


async def _scenario_owner_id_points_to_deleted_owner():
    """Bilan reste equilibre meme si un lot pointe vers un owner disparu."""
    db = await _mongo()
    cid = f"iter90fs-ghost-{uuid.uuid4()}"
    alice_id = f"iter90fs-alice-{uuid.uuid4()}"
    ghost_id = f"iter90fs-ghost-{uuid.uuid4()}"  # never created
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fs ACP", "reference": "G", "status": "active"})
        # Alice : owner reel
        await db.owners.insert_one({
            "id": alice_id, "name": "Alice iter90fs",
            "copropriete_ids": [cid],
            "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
        })
        # Alice possede 60%
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L1",
            "quotity": 6000.0, "owner_id": alice_id, "owner_ids": [alice_id],
        })
        # Ghost possede 40% - MAIS n'existe PAS en base (data corruption)
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L2",
            "quotity": 4000.0, "owner_id": ghost_id, "owner_ids": [ghost_id],
        })
        # Boni de 700 EUR (produits 1000 - charges 300)
        await db.journal_entries.insert_many([
            {
                "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "AC",
                "date": "2026-06-15", "reference": "AC-1",
                "lines": [
                    {"account_number": "6100", "account_name": "Charges", "debit": 300, "credit": 0},
                    {"account_number": "44000001", "account_name": "Fournisseur", "debit": 0, "credit": 300},
                ],
            },
            {
                "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "OD",
                "date": "2026-06-15", "reference": "OD-1",
                "lines": [
                    {"account_number": "5500", "account_name": "Banque", "debit": 1000, "credit": 0},
                    {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 1000},
                ],
            },
        ])
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        # STRICT : bilan doit rester equilibre coute que coute
        assert after["equilibre"], (
            f"REGRESSION iter90fs : bilan desequilibre quand un owner_id "
            f"pointe vers une fiche disparue. actif={after['total_actif']} "
            f"passif={after['total_passif']} ecart={after['ecart']}"
        )
        # Alice doit avoir recu sa quote-part de 60% du boni = 420 EUR (credit)
        alice_line = await _find_account(after, "passif", "41010001")
        assert alice_line is not None, (
            "Alice devrait apparaitre en creditrice apres redistribution "
            "(elle a paye la totalite de ses provisions et recoit 60% du boni)"
        )
        assert abs(alice_line["amount"] - 420.0) < 0.01, (
            f"Alice devrait recevoir 420 EUR (60% de 700 boni), trouve {alice_line['amount']}"
        )
        # Le residu (40% * 700 = 280) doit apparaitre sur le compte 499 avec
        # un libelle explicite mentionnant "non reparti"
        residual = await _find_account(after, "passif", "499")
        assert residual is not None, (
            "REGRESSION iter90fs : la quote-part de l'owner fantome doit "
            "etre reaffichee sur le compte 499 (safety net) et non "
            "silencieusement supprimee du bilan"
        )
        assert abs(residual["amount"] - 280.0) < 0.01, (
            f"Le residu 499 devrait valoir 280 EUR (40% de 700), "
            f"trouve {residual['amount']}"
        )
        assert "non reparti" in residual["account_name"].lower() or \
               "introuvable" in residual["account_name"].lower(), (
            f"Le libelle du 499 residuel doit alerter le syndic sur la "
            f"donnee incomplete, trouve: {residual['account_name']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": alice_id})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _scenario_owner_ids_partial_ghost():
    """Co-indivision : un des 2 owner_ids existe, l'autre est fantome."""
    db = await _mongo()
    cid = f"iter90fs-coind-{uuid.uuid4()}"
    real_id = f"iter90fs-real-{uuid.uuid4()}"
    ghost_id = f"iter90fs-partial-ghost-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fs coind", "reference": "P", "status": "active"})
        await db.owners.insert_one({
            "id": real_id, "name": "Real Owner",
            "copropriete_ids": [cid],
            "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
        })
        # Lot en co-indivision (2 proprietaires, partage egal), l'un existe, l'autre pas
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L1",
            "quotity": 10000.0, "owner_id": "", "owner_ids": [real_id, ghost_id],
        })
        # Boni 500 EUR
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "OD",
            "date": "2026-06-15", "reference": "OD-1",
            "lines": [
                {"account_number": "5500", "account_name": "Banque", "debit": 500, "credit": 0},
                {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 500},
            ],
        })
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"], (
            f"REGRESSION iter90fs : bilan desequilibre en co-indivision "
            f"avec un owner disparu. ecart={after['ecart']}"
        )
        # Real owner recoit 50% = 250
        real_line = await _find_account(after, "passif", "41010001")
        assert real_line is not None and abs(real_line["amount"] - 250.0) < 0.01, (
            f"Real owner devrait recevoir 250 (moitie du boni 500), "
            f"trouve {real_line and real_line['amount']}"
        )
        # Le residu 250 reste sur 499
        residual = await _find_account(after, "passif", "499")
        assert residual is not None and abs(residual["amount"] - 250.0) < 0.01, (
            f"Residu 499 devrait valoir 250, trouve {residual and residual['amount']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": real_id})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _scenario_new_owner_display_account_populated():
    """Un proprietaire sans activite prealable qui recoit sa part du boni
    doit apparaitre avec son numero de compte reel (41010XXX), pas une
    ligne blanche."""
    db = await _mongo()
    cid = f"iter90fs-newowner-{uuid.uuid4()}"
    alice = f"iter90fs-alice-{uuid.uuid4()}"
    carol = f"iter90fs-carol-{uuid.uuid4()}"  # pas d'activite prealable
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fs newowner", "reference": "N", "status": "active"})
        await db.owners.insert_many([
            {
                "id": alice, "name": "Alice",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
            },
            {
                "id": carol, "name": "Carol",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": "41000002"}},
            },
        ])
        # Alice : 1 lot, activite (paye provisions)
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L1",
            "quotity": 6000.0, "owner_id": alice, "owner_ids": [alice],
        })
        # Carol : 1 lot, AUCUNE activite
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L2",
            "quotity": 4000.0, "owner_id": carol, "owner_ids": [carol],
        })
        # Boni 1000 EUR (via banque + produits, sans toucher les comptes owner)
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "OD",
            "date": "2026-06-15", "reference": "OD-1",
            "lines": [
                {"account_number": "5500", "account_name": "Banque", "debit": 1000, "credit": 0},
                {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 1000},
            ],
        })
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"], f"REGRESSION iter90fs : bilan desequilibre, ecart={after['ecart']}"
        # Carol doit apparaitre avec son compte 41010002 (pas vide !)
        carol_line = await _find_account(after, "passif", "41010002")
        assert carol_line is not None, (
            "REGRESSION iter90fs : Carol (sans activite prealable) recoit sa "
            "part du boni mais apparait sans numero de compte dans le bilan "
            "(display_account manquant)"
        )
        assert abs(carol_line["amount"] - 400.0) < 0.01, (
            f"Carol devrait recevoir 400 (40% de 1000), trouve {carol_line['amount']}"
        )
        # Verifier qu'aucune ligne avec account_number vide n'existe
        for side in ("actif", "passif"):
            for r in after[side]:
                for a in r["accounts"]:
                    assert a["account_number"], (
                        f"REGRESSION iter90fs : ligne sans numero de compte "
                        f"dans le bilan ({side}, name={a['account_name']}, "
                        f"amount={a['amount']})"
                    )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [alice, carol]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_ghost_owner_id_safety_net_keeps_bilan_balanced():
    asyncio.run(_scenario_owner_id_points_to_deleted_owner())


def test_co_indivision_partial_ghost_owner_ids():
    asyncio.run(_scenario_owner_ids_partial_ghost())


def test_new_owner_display_account_is_populated():
    asyncio.run(_scenario_new_owner_display_account_populated())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
