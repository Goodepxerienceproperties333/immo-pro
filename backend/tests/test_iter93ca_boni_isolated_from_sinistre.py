"""iter93ca : Isolation des sinistres 499XXX de la redistribution du boni.

Contexte utilisateur : "Le calcul du boni a repartir doit se limiter
EXCLUSIVEMENT au compte 499 (Compte de regularisation). Interdiction
d'inclure les comptes de sinistres (ex: 499603) dans la ventilation
globale des charges de l'ACP. Ces montants doivent rester isoles sur
leurs comptes respectifs."

Fix :
1. Loop 49X redistribution (reports.py:1315) : skip acc.startswith("499")
   au lieu de acc == "499" -> les sous-comptes 499XXX (sinistres) ne sont
   PLUS distribues.
2. Nouvelle rubrique passif VI.D "Provisions et dettes sur sinistres" :
   accueille les 499XXX (hors 499 exact). Ils restent isoles visuellement.

Tests :
- Bilan avant repartition : VII = 40 485 (499 seul), VI.D = 3 577 (499603).
- Bilan apres repartition : 40 485 reparti aux proprios, 3 577 isole.
- Bilan reste equilibre dans les 2 modes.
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

from routes.reports import compute_bilan_data


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed(db, suffix, boni_499_amount, sinistre_499603_amount):
    """Cree une ACP avec un boni 499 issu de mouvements cl.6/cl.7 reels et
    une provision sinistre 499603 (dette isolee).

    - Provisions cl.70 credites (revenus copro) = boni + charges + sinistre
    - Charges cl.6 debites = charges + sinistre
    - Sinistre 499603 credit = sinistre (provision)
    - Solde 499 (via result_exercise) = boni
    """
    cid = f"iter93ca-{suffix}"
    oid1 = f"o1-{suffix}"
    oid2 = f"o2-{suffix}"
    lot1 = f"lot1-{suffix}"
    lot2 = f"lot2-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter93ca-{suffix}"})
    await db.owners.insert_many([
        {"id": oid1, "name": "Owner A", "copropriete_ids": [cid]},
        {"id": oid2, "name": "Owner B", "copropriete_ids": [cid]},
    ])
    await db.lots.insert_many([
        {"id": lot1, "copropriete_id": cid, "number": "L01", "owner_id": oid1, "quotity": 50},
        {"id": lot2, "copropriete_id": cid, "number": "L02", "owner_id": oid2, "quotity": 50},
    ])
    await db.pcmn_accounts.insert_many([
        {"number": "700", "name": "Provisions appelees", "copropriete_id": cid, "class_num": 7},
        {"number": "6110", "name": "Charges communes", "copropriete_id": cid, "class_num": 6},
        {"number": "499", "name": "Compte de regularisation", "copropriete_id": cid, "class_num": 4},
        {"number": "499603", "name": "Sinistre G.4.0.2", "copropriete_id": cid, "class_num": 4},
        {"number": "550", "name": "Banque", "copropriete_id": cid, "class_num": 5},
    ])
    # Provisions appelees = boni (result exercice) - pas de charges reelles.
    # Contrepartie sur 550 pour equilibre.
    await db.journal_entries.insert_one({
        "id": f"ve-{suffix}",
        "journal_type": "VE",
        "copropriete_id": cid,
        "date": "2026-06-15",
        "reference": f"VE-{suffix}",
        "total_debit": boni_499_amount,
        "total_credit": boni_499_amount,
        "lines": [
            {"account_number": "550", "debit": boni_499_amount, "credit": 0},
            {"account_number": "700", "debit": 0, "credit": boni_499_amount},
        ],
        "created_at": "2026-06-15T00:00:00",
    })
    # Provision sinistre : dette envers assurance/expert, credit sur 499603
    await db.journal_entries.insert_one({
        "id": f"od-sin-{suffix}",
        "journal_type": "OD",
        "copropriete_id": cid,
        "date": "2026-08-20",
        "reference": f"OD-SIN-{suffix}",
        "total_debit": sinistre_499603_amount,
        "total_credit": sinistre_499603_amount,
        "lines": [
            {"account_number": "550", "debit": sinistre_499603_amount, "credit": 0},
            {"account_number": "499603", "debit": 0, "credit": sinistre_499603_amount},
        ],
        "created_at": "2026-08-20T00:00:00",
    })
    return cid, oid1, oid2


async def _cleanup(db, cid):
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"copropriete_ids": cid})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.coproprietes.delete_one({"id": cid})


async def test_boni_499_isolated_from_sinistre_499603():
    """Le boni redistribue = 499 SEUL (pas VII total avec 499603)."""
    db = await _mongo()
    sfx = uuid.uuid4().hex[:8]
    boni = 40485.00
    sinistre = 3577.18
    cid, oid1, oid2 = await _seed(db, sfx, boni, sinistre)
    try:
        # Mode BEFORE : 499 et 499603 doivent etre dans des rubriques SEPAREES
        bilan = await compute_bilan_data(db, cid, view_mode="before_distribution")
        rubs = {r["label"]: r for r in bilan["passif"]}
        vii = next((r for r in bilan["passif"]
                    if r["label"].startswith("VII.") and r["total"] > 0.005), None)
        assert vii is not None, "Rubrique VII manquante"
        assert abs(vii["total"] - boni) < 0.01, (
            f"VII total devrait etre {boni} (499 seul), got {vii['total']}"
        )
        vi_d = next((r for r in bilan["passif"]
                     if r["label"].startswith("VI.D") and r["total"] > 0.005), None)
        assert vi_d is not None, "Rubrique VI.D Sinistres manquante"
        assert abs(vi_d["total"] - sinistre) < 0.01, (
            f"VI.D devrait etre {sinistre} (499603), got {vi_d['total']}"
        )
        # 499603 NE DOIT PAS etre dans VII
        vii_accs = {a["account_number"] for a in vii.get("accounts", [])}
        assert "499603" not in vii_accs, "499603 ne doit PAS etre dans VII"
        assert "499" in vii_accs, "499 doit etre dans VII"

        # Mode AFTER : le boni 40485 est distribue, le sinistre reste isole
        bilan_after = await compute_bilan_data(db, cid, view_mode="after_distribution")
        vii_after = next((r for r in bilan_after["passif"]
                          if r["label"].startswith("VII.") and r["total"] > 0.005), None)
        # VII doit etre vide (boni redistribue)
        assert vii_after is None or vii_after["total"] < 0.01, (
            f"VII apres repartition devrait etre vide, got {vii_after}"
        )
        # VI.D doit toujours contenir le sinistre (isole, non redistribue)
        vi_d_after = next((r for r in bilan_after["passif"]
                           if r["label"].startswith("VI.D") and r["total"] > 0.005), None)
        assert vi_d_after is not None, "VI.D absent apres repartition"
        assert abs(vi_d_after["total"] - sinistre) < 0.01, (
            f"Sinistre 499603 doit rester isole a {sinistre}, got {vi_d_after['total']}"
        )
        # Les proprietaires doivent avoir recu le boni (VI.A > 0)
        vi_a_after = next((r for r in bilan_after["passif"]
                           if r["label"].startswith("VI.A") and r["total"] > 0.005), None)
        assert vi_a_after is not None, "VI.A Coproprietaires crediteurs devrait etre non vide"
        assert abs(vi_a_after["total"] - boni) < 0.05, (
            f"Boni redistribue devrait totaliser {boni}, got {vi_a_after['total']}"
        )
        # Bilan reste equilibre
        assert bilan["equilibre"], f"Avant repartition desequilibre : {bilan['ecart']}"
        assert bilan_after["equilibre"], f"Apres repartition desequilibre : {bilan_after['ecart']}"
    finally:
        await _cleanup(db, cid)


if __name__ == "__main__":
    asyncio.run(test_boni_499_isolated_from_sinistre_499603())
    print("OK test_boni_499_isolated_from_sinistre_499603")
    print("\n=== ALL 1 TEST PASSED ===")
