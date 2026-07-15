"""
iter90fu : REGRESSION LOCK - health audit flaggait des proprietaires
CREDITEURS comme "en attente de traitement".

Ticket utilisateur (Feb 2026, PROD ACP Acacia TER) :
> "Les informations concernant matexi en tableau de bord sont fausses. La
> balance étant créditrice"

Screenshots produits :
1. Balance des Tiers : Matexi Solde=-1755.42 EUR (Crediteur, badge vert),
   appels=37010, paye=38765.42.
2. Dashboard "sante comptable" : anomalie "1 proprietaire avec appel non
   paye et solde tier non debiteur" -> Matexi Total du 20950.00 EUR, Nb
   appels 150, Retard 257j.

Root cause : `health_audit.py::compute_health_audit` classait les
proprietaires en 3 categories via la logique :
```python
target = late_owners if balance > 0.01 else pending_owners
```
Consequence : un proprietaire CREDITEUR (balance < -0.01) - qui a paye
PLUS que ce qui lui a ete appele - etait classe dans `pending_owners`
juste parce que certains flag `paid` des fund_calls historiques n'ont
jamais ete togglees (legacy/import). Le tableau de bord affichait alors
des dettes FANTOMES : "20950 EUR dues" alors que Matexi a un
solde CREDITEUR de -1755 EUR (elle a paye 38765 pour 37010 appeles).

Fix : filtrer explicitement les CREDITEURS AVANT le classement
late/pending. Si `balance < -0.01`, on ne les flaggue pas du tout - ils
ne peuvent pas etre en retard NI en attente. Les fund_calls avec flag
`paid` non mis a jour sont un probleme de "compliance de flag", pas une
anomalie comptable reelle.

Ce test verifie :
A) Un owner avec fund_call non paid + solde tier CREDITEUR est
   completement exclu du rapport (ni late, ni pending).
B) Un owner avec fund_call non paid + solde ~0 reste en pending (comportement
   inchange - retard non encore confirme au niveau comptable).
C) Un owner avec fund_call non paid + solde DEBITEUR reste en late
   (comportement inchange - retard confirme).
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

from health_audit import compute_health_audit  # noqa: E402


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_scenario(db, cid, owner_id, provisions_acc, ac_amount, fi_amount, fund_call_amount, prefix):
    """Cree une ACP avec 1 owner, 1 fund_call ancien non paye, et des
    ecritures qui creent le solde tier voulu.

    - ac_amount : ecriture VE (appel de fonds) = DEBIT sur tier owner
    - fi_amount : ecriture FI (encaissement) = CREDIT sur tier owner
    - solde = ac_amount - fi_amount (positif = debiteur, negatif = crediteur)
    """
    await db.owners.insert_one({
        "id": owner_id, "name": f"Owner {prefix}",
        "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": provisions_acc, "reserve": ""}},
    })
    if ac_amount > 0:
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "journal_type": "VE", "date": "2025-10-01", "reference": "VE-1",
            "total_debit": ac_amount, "total_credit": ac_amount,
            "lines": [
                {"account_number": provisions_acc, "debit": ac_amount, "credit": 0},
                {"account_number": "7000", "debit": 0, "credit": ac_amount},
            ],
        })
    if fi_amount > 0:
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "journal_type": "FI", "date": "2025-10-15", "reference": "FI-1",
            "total_debit": fi_amount, "total_credit": fi_amount,
            "lines": [
                {"account_number": "5500", "debit": fi_amount, "credit": 0},
                {"account_number": provisions_acc, "debit": 0, "credit": fi_amount},
            ],
        })
    # Fund_call OLD (echeance il y a 200 jours) NON marque comme paid
    from datetime import datetime, timedelta, timezone
    old_date = (datetime.now(timezone.utc).date() - timedelta(days=200)).isoformat()
    await db.fund_calls.insert_one({
        "id": str(uuid.uuid4()), "copropriete_id": cid,
        "name": "Appel Q3", "date": old_date, "due_date": old_date,
        "distribution": [
            {"owner_id": owner_id, "owner_name": f"Owner {prefix}",
             "amount": fund_call_amount, "paid": False}
        ],
    })


async def _scenario_creditor_not_flagged():
    """Owner crediteur (a paye plus qu'appelle) NE DOIT PAS apparaitre
    dans le rapport de sante comptable."""
    db = await _mongo()
    cid = f"iter90fu-cred-{uuid.uuid4()}"
    o1 = f"iter90fu-cred-o1-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fu", "reference": "C"})
        # AC=1000, FI=1500 -> solde net = -500 (crediteur, paye trop)
        # Le fund_call de 800 EUR est marque non paid (legacy)
        await _setup_scenario(db, cid, o1, "41010001",
                              ac_amount=1000, fi_amount=1500, fund_call_amount=800,
                              prefix="creditor")
        rep = await compute_health_audit(db, cid)
        pending_anomaly = next((a for a in rep["anomalies"]
                                if a["category"] == "owners_pending"), None)
        late_anomaly = next((a for a in rep["anomalies"]
                             if a["category"] == "owners_late"), None)
        assert pending_anomaly is None or all(
            it["owner_id"] != o1 for it in pending_anomaly.get("items", [])
        ), (
            f"REGRESSION iter90fu : proprietaire CREDITEUR (solde=-500) "
            f"apparait en 'attente de traitement' - il ne devrait pas etre "
            f"flagge du tout. Anomaly={pending_anomaly}"
        )
        assert late_anomaly is None or all(
            it["owner_id"] != o1 for it in late_anomaly.get("items", [])
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": o1})
        await db.journal_entries.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})


async def _scenario_debtor_still_late():
    """Owner debiteur reel doit toujours apparaitre en late_owners."""
    db = await _mongo()
    cid = f"iter90fu-deb-{uuid.uuid4()}"
    o1 = f"iter90fu-deb-o1-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fu deb", "reference": "D"})
        # AC=1000, FI=200 -> solde debiteur = +800 (debiteur reel)
        await _setup_scenario(db, cid, o1, "41010001",
                              ac_amount=1000, fi_amount=200, fund_call_amount=800,
                              prefix="debtor")
        rep = await compute_health_audit(db, cid)
        late_anomaly = next((a for a in rep["anomalies"]
                             if a["category"] == "owners_late"), None)
        assert late_anomaly is not None, "REGRESSION : debiteur reel absent des late_owners"
        assert any(it["owner_id"] == o1 for it in late_anomaly["items"]), (
            f"Debiteur (solde=+800) devrait etre dans late_owners, trouve "
            f"{[it['owner_id'] for it in late_anomaly['items']]}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": o1})
        await db.journal_entries.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})


async def _scenario_near_zero_stays_pending():
    """Owner avec solde ~= 0 (ex: fund_call pose mais VE pas encore
    generee) reste en pending_owners (comportement iter90ar preserve)."""
    db = await _mongo()
    cid = f"iter90fu-zero-{uuid.uuid4()}"
    o1 = f"iter90fu-zero-o1-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fu zero", "reference": "Z"})
        # AC=0, FI=0 -> solde = 0.0 (rien n'a bouge en compta)
        await _setup_scenario(db, cid, o1, "41010001",
                              ac_amount=0, fi_amount=0, fund_call_amount=500,
                              prefix="zero")
        rep = await compute_health_audit(db, cid)
        pending_anomaly = next((a for a in rep["anomalies"]
                                if a["category"] == "owners_pending"), None)
        assert pending_anomaly is not None, (
            "REGRESSION iter90ar : owner avec solde=0 et appel non paye "
            "doit etre en pending_owners (comportement pre-iter90fu preserve)"
        )
        assert any(it["owner_id"] == o1 for it in pending_anomaly["items"]), (
            f"Owner solde ~0 doit apparaitre en pending, trouve {pending_anomaly['items']}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": o1})
        await db.journal_entries.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})


def test_creditor_owner_not_flagged_in_health_audit():
    asyncio.run(_scenario_creditor_not_flagged())


def test_debtor_owner_still_late():
    asyncio.run(_scenario_debtor_still_late())


def test_near_zero_owner_stays_pending():
    asyncio.run(_scenario_near_zero_stays_pending())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
