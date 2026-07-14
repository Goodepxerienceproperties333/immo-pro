"""
iter90ft : REGRESSION LOCK - bilan "apres repartition" DESEQUILIBRE lorsque
les quotites vivent dans la cle de repartition generale (`distribution_keys`
avec `is_default=True`) plutot que directement sur `lots.quotity`.

Ticket utilisateur (Feb 2026, PROD, ACP Acacia TER, 3eme redite du bug) :
> "Il n'y a eu aucune répartition du compte de régularisation sur les
> propriétaires je veux que tu corriges cela à savoir que le compte de
> régularisation Boni ou Mali serait se répartit sur les soldes
> propriétaires selon leur quotité DANS LES CLÉS DE RÉPARTITION"

PDF PROD montre 10 proprietaires listes avec leurs comptes 4101XXXX +
le safety net iter90fs affichant "Compte de regularisation - Boni a
repartir (quotites manquantes - completer les lots) - 7053.57 EUR".

Root cause : sur ACP Acacia TER (et d'autres ACPs issues d'un import
legacy), le champ `quotity` n'est PAS peuple directement sur `lots`. Les
quotites vivent dans `distribution_keys[is_default=True].lots[].share`.
`compute_bilan_data` ne consultait QUE `lot.quotity` -> total_quotities=0
-> le safety net iter90fs kickait proprement (le bilan reste equilibre
grace au fallback 499) MAIS le boni n'etait PAS reparti sur les
proprietaires (comportement attendu par l'utilisateur).

Meme pattern deja existant ailleurs dans l'app :
- `routes/reports.py::_lot_share` (iter90ej, decompte annuel)
- `routes/owner_portal.py` (iter90cz, portail proprietaire)

Fix iter90ft : ajout d'un fallback `_lot_quotity(lot)` dans
`compute_bilan_data` qui consulte la cle generale (`is_default=True`) si
`lot.quotity` est vide/nul. Ordre de priorite identique aux 2 fonctions
existantes : direct d'abord, cle generale ensuite.
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


async def _scenario_quotities_only_in_default_key():
    """Reproduit ACP Acacia TER : lots.quotity=0, quotites dans cle
    generale."""
    db = await _mongo()
    cid = f"iter90ft-key-{uuid.uuid4()}"
    o1 = f"iter90ft-o1-{uuid.uuid4()}"
    o2 = f"iter90ft-o2-{uuid.uuid4()}"
    l1_id = f"iter90ft-l1-{uuid.uuid4()}"
    l2_id = f"iter90ft-l2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one(
            {"id": cid, "name": "iter90ft ACP", "reference": "F", "status": "active"}
        )
        await db.owners.insert_many([
            {
                "id": o1, "name": "Owner A iter90ft",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
            },
            {
                "id": o2, "name": "Owner B iter90ft",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": "41000002"}},
            },
        ])
        # LOTS SANS QUOTITY sur le document (comme Acacia TER en PROD)
        await db.lots.insert_many([
            {
                "id": l1_id, "copropriete_id": cid, "number": "L1",
                "quotity": 0, "owner_id": o1, "owner_ids": [o1],
            },
            {
                "id": l2_id, "copropriete_id": cid, "number": "L2",
                "quotity": 0, "owner_id": o2, "owner_ids": [o2],
            },
        ])
        # QUOTITES DANS LA CLE GENERALE
        await db.distribution_keys.insert_one({
            "id": f"iter90ft-key-{uuid.uuid4()}",
            "copropriete_id": cid,
            "code": "GEN", "name": "Cle generale",
            "key_type": "quotity",
            "is_default": True,
            "lots": [
                {"lot_id": l1_id, "share": 6000.0, "excluded": False},
                {"lot_id": l2_id, "share": 4000.0, "excluded": False},
            ],
            "total_quotities": 10000.0,
        })
        # Boni de 1000 EUR (produits 1000)
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "journal_type": "OD", "date": "2026-06-15", "reference": "OD-1",
            "lines": [
                {"account_number": "5500", "account_name": "Banque", "debit": 1000, "credit": 0},
                {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 1000},
            ],
        })

        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"], (
            f"REGRESSION iter90ft : bilan desequilibre malgre le fallback. "
            f"ecart={after['ecart']}"
        )
        # Owner A (60%) doit avoir 600 EUR credit sur 41010001
        oa_line = await _find_account(after, "passif", "41010001")
        assert oa_line is not None and abs(oa_line["amount"] - 600.0) < 0.01, (
            f"REGRESSION iter90ft : Owner A devrait recevoir 600 EUR (60% de 1000 boni "
            f"selon la cle generale), trouve {oa_line and oa_line['amount']}. "
            f"Le fallback vers distribution_keys[is_default] n'a pas fonctionne."
        )
        # Owner B (40%) doit avoir 400 EUR
        ob_line = await _find_account(after, "passif", "41010002")
        assert ob_line is not None and abs(ob_line["amount"] - 400.0) < 0.01, (
            f"REGRESSION iter90ft : Owner B devrait recevoir 400 EUR (40% de 1000 boni), "
            f"trouve {ob_line and ob_line['amount']}"
        )
        # Le compte 499 ne doit PLUS apparaitre (repartition complete)
        residual = await _find_account(after, "passif", "499")
        assert residual is None, (
            f"REGRESSION iter90ft : 499 ne devrait plus apparaitre apres repartition "
            f"reussie via la cle generale, trouve amount={residual['amount']} "
            f"libelle={residual['account_name']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [o1, o2]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _scenario_priority_direct_quotity_over_default_key():
    """Si le lot a `quotity > 0` ET une entree dans la cle generale, la
    valeur DIRECTE sur le lot doit primer (retro-compatibilite garantie
    pour les ACPs deja migrees / correctement configurees)."""
    db = await _mongo()
    cid = f"iter90ft-prio-{uuid.uuid4()}"
    o1 = f"iter90ft-prio-o1-{uuid.uuid4()}"
    l1_id = f"iter90ft-prio-l1-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one(
            {"id": cid, "name": "iter90ft prio", "reference": "P", "status": "active"}
        )
        await db.owners.insert_one({
            "id": o1, "name": "Sole Owner",
            "copropriete_ids": [cid],
            "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
        })
        # Lot avec quotity=10000 direct + cle generale disant 5000 (DIFFERENT)
        await db.lots.insert_one({
            "id": l1_id, "copropriete_id": cid, "number": "L1",
            "quotity": 10000.0, "owner_id": o1, "owner_ids": [o1],
        })
        await db.distribution_keys.insert_one({
            "id": str(uuid.uuid4()),
            "copropriete_id": cid, "code": "GEN", "name": "Cle generale",
            "key_type": "quotity", "is_default": True,
            "lots": [{"lot_id": l1_id, "share": 5000.0, "excluded": False}],
            "total_quotities": 5000.0,
        })
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "journal_type": "OD", "date": "2026-06-15", "reference": "OD-1",
            "lines": [
                {"account_number": "5500", "account_name": "Banque", "debit": 500, "credit": 0},
                {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 500},
            ],
        })
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"], f"REGRESSION : bilan desequilibre, ecart={after['ecart']}"
        # Sole owner recoit 100% (quotity 10000 > 0 -> ignore la cle generale)
        line = await _find_account(after, "passif", "41010001")
        assert line is not None and abs(line["amount"] - 500.0) < 0.01, (
            f"Sole owner devrait recevoir 100% du boni (500), trouve {line and line['amount']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": o1})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _scenario_default_key_excluded_lots_ignored():
    """Les lots marques `excluded=True` dans la cle generale ne comptent
    pas dans le total_quotities (regle metier existante iter90ej)."""
    db = await _mongo()
    cid = f"iter90ft-excl-{uuid.uuid4()}"
    o1 = f"iter90ft-excl-o1-{uuid.uuid4()}"
    o2 = f"iter90ft-excl-o2-{uuid.uuid4()}"
    l1_id = f"iter90ft-excl-l1-{uuid.uuid4()}"
    l2_id = f"iter90ft-excl-l2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one(
            {"id": cid, "name": "iter90ft excl", "reference": "X", "status": "active"}
        )
        await db.owners.insert_many([
            {
                "id": o1, "name": "Owner A",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}},
            },
            {
                "id": o2, "name": "Owner B (excluded)",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": "41000002"}},
            },
        ])
        await db.lots.insert_many([
            {"id": l1_id, "copropriete_id": cid, "number": "L1",
             "quotity": 0, "owner_id": o1, "owner_ids": [o1]},
            {"id": l2_id, "copropriete_id": cid, "number": "L2",
             "quotity": 0, "owner_id": o2, "owner_ids": [o2]},
        ])
        await db.distribution_keys.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "code": "GEN", "name": "Cle generale",
            "key_type": "quotity", "is_default": True,
            "lots": [
                {"lot_id": l1_id, "share": 6000.0, "excluded": False},
                {"lot_id": l2_id, "share": 4000.0, "excluded": True},  # EXCLUDED
            ],
            "total_quotities": 6000.0,
        })
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "journal_type": "OD", "date": "2026-06-15", "reference": "OD-1",
            "lines": [
                {"account_number": "5500", "account_name": "Banque", "debit": 300, "credit": 0},
                {"account_number": "7000", "account_name": "Provisions", "debit": 0, "credit": 300},
            ],
        })
        after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        assert after["equilibre"]
        # Owner A recoit 100% (Owner B est exclu de la cle generale)
        oa_line = await _find_account(after, "passif", "41010001")
        assert oa_line is not None and abs(oa_line["amount"] - 300.0) < 0.01, (
            f"Owner A devrait recevoir 100% du boni car Owner B exclu, "
            f"trouve {oa_line and oa_line['amount']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [o1, o2]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_bilan_uses_default_distribution_key_when_lot_quotity_missing():
    asyncio.run(_scenario_quotities_only_in_default_key())


def test_bilan_direct_lot_quotity_takes_priority_over_default_key():
    asyncio.run(_scenario_priority_direct_quotity_over_default_key())


def test_bilan_default_key_excluded_lots_are_ignored():
    asyncio.run(_scenario_default_key_excluded_lots_ignored())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
