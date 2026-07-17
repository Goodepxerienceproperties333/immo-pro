"""iter90ia : Test invariant critique - la liste "Balance des tiers proprietaires"
et la Situation de compte doivent afficher les MEMES totaux.

Bug reporte par le user (Boxus Wivine) :
 - Situation PDF : 638.75 EUR (correct)
 - Liste balance-tiers : 930.41 EUR (incorrect - +291.66 EUR)

Cause : l'endpoint `/reports/balance-tiers/owners` INCLUAIT les entrees
AN de CLOTURE (celles sans is_opening_balance=True), qui sont des
duplicatas techniques du solde de fin d'exercice N-1 servant a alimenter
l'exercice N. Le bilan et la Situation les filtrent deja correctement.

Fix : appliquer le meme filtre AN que la Situation :
  { "$or": [{"journal_type": {"$ne": "AN"}}, {"journal_type": "AN", "is_opening_balance": True}] }
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed_boxus_like_scenario(db, copro_id, owner_id, acc_prov, acc_res):
    """Reproduit le scenario user Boxus Wivine :
    - Un compte tier provisions (4100959) + reserve (41000031)
    - Appels et paiements taggues avec third_party_id
    - Une AN opening_balance=True (a inclure)
    - Une AN closing (sans is_opening_balance) (a EXCLURE)
    """
    await db.owners.delete_many({"id": owner_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})

    await db.coproprietes.insert_one({"id": copro_id, "name": "ACP ia test"})
    await db.owners.insert_one({
        "id": owner_id, "name": "Boxus Test",
        "tier_accounts": {copro_id: {"provisions": acc_prov, "reserve": acc_res}},
    })
    await db.lots.insert_one({
        "id": f"lot-{owner_id}", "copropriete_id": copro_id,
        "owner_id": owner_id, "number": "01",
    })
    # Lignes taggees tpid=Boxus : simule les 4 provisions T1-T4 + 1 reserve
    # + 1 imputation charges + paiements. Total = 4259.88 Dr / 3329.47 Cr
    # -> net (Dr-Cr) = 930.41 EUR
    for i, (date, acc, dr, cr) in enumerate([
        ("2026-03-01", acc_prov, 500.29, 0),
        ("2026-06-01", acc_prov, 500.29, 0),
        ("2026-09-01", acc_prov, 500.29, 0),
        ("2026-12-01", acc_prov, 500.29, 0),
        ("2026-05-01", acc_res, 616.00, 0),
        ("2027-02-28", acc_prov, 1642.72, 0),
        ("2026-03-15", acc_prov, 0, 500.29),
        ("2026-06-20", acc_prov, 0, 800.00),
        ("2026-09-10", acc_prov, 0, 500.00),
        ("2026-12-05", acc_prov, 0, 800.00),
        ("2027-01-15", acc_prov, 0, 729.18),
    ]):
        await db.journal_entries.insert_one({
            "id": f"e-{i}-{owner_id}", "journal_type": "OD" if dr > 0 else "FI",
            "copropriete_id": copro_id, "date": date,
            "lines": [
                {"account_number": acc, "debit": dr, "credit": cr,
                 "third_party_id": owner_id},
                {"account_number": "550000", "debit": cr, "credit": dr},
            ],
            "total_debit": max(dr, cr), "total_credit": max(dr, cr),
        })
    # AN d'ouverture = Boxus a un CREDIT de 291.66 en debut d'exercice
    # (paye trop l'annee precedente) -> DOIT etre inclus dans le solde
    await db.journal_entries.insert_one({
        "id": f"an-open-{owner_id}", "journal_type": "AN",
        "copropriete_id": copro_id, "date": "2026-03-01",
        "reference": "AN-2026-001",
        "is_opening_balance": True,  # <-- Clef
        "lines": [
            {"account_number": acc_prov, "debit": 0, "credit": 291.66},
            {"account_number": "690000", "debit": 291.66, "credit": 0},
        ],
        "total_debit": 291.66, "total_credit": 291.66,
    })
    # AN de cloture (duplicata technique) - dans le passe cette entree
    # etait comptee 2x -> Boxus paraissait 291.66 de plus debiteur.
    # DOIT etre EXCLU (is_opening_balance != True).
    await db.journal_entries.insert_one({
        "id": f"an-close-{owner_id}", "journal_type": "AN",
        "copropriete_id": copro_id, "date": "2027-03-01",
        "reference": "AN-03.2026 - 02.2027",
        # is_opening_balance ABSENT -> doit etre filtre
        "lines": [
            {"account_number": acc_prov, "debit": 314.41, "credit": 0},
            {"account_number": acc_res, "debit": 616.00, "credit": 0},
            {"account_number": "690000", "debit": 0, "credit": 930.41},
        ],
        "total_debit": 930.41, "total_credit": 930.41,
    })


async def _cleanup(db, copro_id, owner_id):
    await db.owners.delete_many({"id": owner_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})


def test_balance_tiers_list_excludes_closing_an_matches_situation():
    """iter90ia : la liste balance-tiers/owners doit exclure les AN de
    cloture (sans is_opening_balance=True) et donc afficher le meme solde
    que la Situation de compte : 638.75 EUR pour Boxus (au lieu de 930.41)."""
    copro_id = f"acp-ia-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-ia-{uuid.uuid4().hex[:8]}"
    acc_prov = "4100959"
    acc_res = "41000031"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import create_reports_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_boxus_like_scenario(db, copro_id, owner_id, acc_prov, acc_res)
            # Recuperer l'endpoint balance-tiers/owners
            router = create_reports_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/reports/balance-tiers/owners":
                    handler = r.endpoint
                    break
            assert handler is not None, "Endpoint balance-tiers/owners introuvable"

            # Simule request minimal
            class _S:
                pass

            class _Req:
                def __init__(self):
                    self.state = _S()
                    self.state.copropriete_id = copro_id
            data = await handler(copropriete_id=copro_id)
            boxus = next((o for o in data["owners"] if o["owner_id"] == owner_id), None)
            assert boxus is not None, "Boxus test doit apparaitre dans le resultat"
            # Balance attendue = 638.75 EUR
            # (4259.88 debit tpid - 3329.47 credit tpid - 291.66 credit AN opening)
            expected = round(4259.88 - 3329.47 - 291.66, 2)
            assert expected == 638.75, f"Sanity check: {expected}"
            assert abs(boxus["balance"] - 638.75) < 0.01, (
                f"Balance liste attendue 638.75 (comme Situation), got {boxus['balance']}. "
                f"L'AN de cloture doit etre exclu (is_opening_balance != True)."
            )
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_balance_tiers_list_includes_opening_an():
    """iter90ia : les AN avec is_opening_balance=True DOIVENT etre inclus
    (reprise comptable des exercices anterieurs)."""
    copro_id = f"acp-ia-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-ia-{uuid.uuid4().hex[:8]}"
    acc_prov = "4100999"
    acc_res = "41010999"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import create_reports_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})

            await db.coproprietes.insert_one({"id": copro_id, "name": "ACP ia-2"})
            await db.owners.insert_one({
                "id": owner_id, "name": "Owner test",
                "tier_accounts": {copro_id: {"provisions": acc_prov, "reserve": acc_res}},
            })
            await db.lots.insert_one({
                "id": f"lot-{owner_id}", "copropriete_id": copro_id,
                "owner_id": owner_id, "number": "01",
            })
            # AN opening avec Dr 100 -> le proprio a un solde initial debiteur de 100
            await db.journal_entries.insert_one({
                "id": f"an-open-{owner_id}", "journal_type": "AN",
                "copropriete_id": copro_id, "date": "2026-01-01",
                "reference": "AN-2026",
                "is_opening_balance": True,
                "lines": [
                    {"account_number": acc_prov, "debit": 100.0, "credit": 0,
                     "third_party_id": owner_id},
                    {"account_number": "690000", "debit": 0, "credit": 100.0},
                ],
                "total_debit": 100, "total_credit": 100,
            })
            router = create_reports_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reports/balance-tiers/owners")

            class _S:
                pass

            class _Req:
                def __init__(self):
                    self.state = _S()
                    self.state.copropriete_id = copro_id
            data = await handler(copropriete_id=copro_id)
            owner = next((o for o in data["owners"] if o["owner_id"] == owner_id), None)
            assert owner is not None
            # L'AN d'ouverture DOIT etre compte -> balance = 100
            assert abs(owner["balance"] - 100.0) < 0.01, (
                f"AN opening_balance doit etre inclus. Balance attendue 100, got {owner['balance']}"
            )
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())
