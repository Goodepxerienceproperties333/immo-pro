"""iter90hk / iter90hl : Tests pour :

1. iter90hk - `/api/reminders/late-payments` se base sur le SOLDE REEL du
   tier account du proprietaire (via `_compute_balance_tiers_for_ui`), pas
   uniquement sur le flag `distribution[].paid`. Un proprietaire crediteur
   (lettrage bancaire) n'apparait plus dans les rappels meme si le flag
   `paid` n'a pas ete mis a jour manuellement.

2. iter90hl - La lettre PDF `/api/reminders/owner/{id}/letter` filtre selon
   `date_from`/`date_to` et exclut les appels dont l'echeance est future.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed_owner_and_calls(db, copro_id: str, owner_id: str,
                                 owner_credit_via_bank: bool = False):
    """Cree un proprietaire, ses lots et 3 fund_calls (passe, tres passe, futur).
    Si `owner_credit_via_bank=True`, ajoute une transaction bancaire non lettree
    au VCS du proprietaire pour un montant qui couvre TOUS les appels
    -> proprietaire crediteur, ne doit plus apparaitre en retard."""
    await db.owners.delete_many({"id": owner_id})
    await db.fund_calls.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.bank_transactions.delete_many({"copropriete_id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP Test iter90hk",
        "reference": "TEST-HK", "status": "active",
    })
    vcs = "+++111/2222/33333+++"
    vcs_digits = "111222233333"
    await db.owners.insert_one({
        "id": owner_id,
        "name": "Alice Testeuse",
        "first_name": "Alice",
        "address": "Rue du Test 1",
        "postal_code": "1000",
        "city": "Bruxelles",
        "vcs_code": vcs,
        "vcs_digits": vcs_digits,
        "copropriete_ids": [copro_id],
        "tier_accounts": {
            copro_id: {"provisions": "41010001", "reserve": "41000001"},
        },
    })
    await db.lots.insert_one({
        "id": f"lot-{owner_id}", "copropriete_id": copro_id,
        "owner_id": owner_id, "reference": "LOT01",
    })
    today = datetime.now(timezone.utc).date()
    past_due = (today - timedelta(days=100)).strftime("%Y-%m-%d")
    veryold_due = (today - timedelta(days=200)).strftime("%Y-%m-%d")
    future_due = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    fund_calls_docs = [
        {
            "id": f"fc-past-{copro_id}", "copropriete_id": copro_id,
            "name": "Trimestriel 1/4 - Past", "due_date": past_due,
            "distribution": [{"owner_id": owner_id, "owner_name": "Alice",
                              "vcs_code": vcs, "amount": 300.0, "paid": False}],
        },
        {
            "id": f"fc-veryold-{copro_id}", "copropriete_id": copro_id,
            "name": "Trimestriel 4/4 - VeryOld", "due_date": veryold_due,
            "distribution": [{"owner_id": owner_id, "owner_name": "Alice",
                              "vcs_code": vcs, "amount": 400.0, "paid": False}],
        },
        {
            "id": f"fc-future-{copro_id}", "copropriete_id": copro_id,
            "name": "Trimestriel Future", "due_date": future_due,
            "distribution": [{"owner_id": owner_id, "owner_name": "Alice",
                              "vcs_code": vcs, "amount": 200.0, "paid": False}],
        },
    ]
    await db.fund_calls.insert_many(fund_calls_docs)
    # JE : debit sur le compte tier proprietaire = 700 EUR total (appels)
    for fc in fund_calls_docs:
        if "future" in fc["id"]:
            continue  # future = pas encore emise
        amt = fc["distribution"][0]["amount"]
        await db.journal_entries.insert_one({
            "id": f"je-{fc['id']}", "copropriete_id": copro_id,
            "date": fc["due_date"], "journal_type": "OD",
            "description": fc["name"],
            "lines": [
                {"account_number": "41010001", "third_party_id": owner_id,
                 "debit": amt, "credit": 0, "account_name": "Appel provisions"},
                {"account_number": "70000001", "debit": 0, "credit": amt,
                 "account_name": "Produit"},
            ],
        })
    if owner_credit_via_bank:
        # Transaction bancaire non lettree matchee par VCS
        # -> 1000 EUR de paiement, largement > 700 EUR d'appels
        await db.bank_transactions.insert_one({
            "id": f"txn-{copro_id}", "copropriete_id": copro_id,
            "date": (today - timedelta(days=50)).strftime("%Y-%m-%d"),
            "amount": 1000.0, "matched": False,
            "communication": vcs_digits,
            "counterparty_name": "Alice",
        })


async def _cleanup(db, copro_id: str, owner_id: str):
    await db.owners.delete_many({"id": owner_id})
    await db.fund_calls.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.bank_transactions.delete_many({"copropriete_id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})


async def _call_late(copro_id: str, **kwargs):
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.exports import create_reminders_router
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        router = create_reminders_router(db)
        handler = next(r.endpoint for r in router.routes
                       if getattr(r, "path", "") == "/api/reminders/late-payments")
        return await handler(copropriete_id=copro_id, **kwargs)
    finally:
        client.close()


def test_debtor_owner_appears_in_reminders():
    """Un proprietaire debiteur (700 EUR d'appels, 0 paiement) apparait.
    iter90ho : agregation par owner -> 1 ligne unique avec tier_balance=700."""
    copro_id = f"acp-hk-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hk-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=False)
        try:
            data = await _call_late(copro_id)
            # iter90ho : 1 seule ligne agregee par owner (au lieu de 2 fund_calls)
            assert data["summary"]["total_count"] == 1
            item = data["late_payments"][0]
            # tier_balance = 700 EUR (2 appels echus, pas de paiement)
            assert abs(item["tier_balance"] - 700.0) < 0.01
            # amount = tier_balance (source de verite)
            assert abs(item["amount"] - 700.0) < 0.01
            # total_called_period = somme des appels dans la periode
            assert item.get("total_called_period", 0) == 700.0
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_creditor_owner_via_bank_payment_is_skipped():
    """iter90hk : un proprietaire ayant paye via bank_transactions non lettrees
    (VCS match) devient crediteur -> disparait des rappels meme si les fund_calls
    ne sont pas marques `paid=True`.
    Scenario type : Boxus Wivine paye 1618 EUR au VCS +++103/7451/75185+++
    mais les fund_calls[.distribution[].paid] restent False."""
    copro_id = f"acp-hk-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hk-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=True)
        try:
            data = await _call_late(copro_id)
            # Paiement bancaire de 1000 EUR > 700 EUR d'appels
            # -> proprietaire crediteur -> AUCUN rappel
            assert data["summary"]["total_count"] == 0, (
                f"Expected 0 reminders (creditor), got {data['summary']}"
            )
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_letter_pdf_filters_by_period():
    """iter90hl : la lettre PDF filtre les appels par date_from/date_to
    et exclut les echeances futures."""
    copro_id = f"acp-hl-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hl-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.exports import create_reminders_router
        import fitz  # PyMuPDF
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=False)
        try:
            router = create_reminders_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reminders/owner/{owner_id}/letter")
            today = datetime.now(timezone.utc).date()
            # Sans filtre : PDF genere avec 2 appels echus (future exclu)
            resp = await handler(owner_id=owner_id, copropriete_id=copro_id)
            body_all = b"".join([chunk async for chunk in resp.body_iterator])
            txt_all = fitz.open(stream=body_all, filetype="pdf")[0].get_text()
            assert "Trimestriel 1/4 - Past" in txt_all
            assert "Trimestriel 4/4 - VeryOld" in txt_all
            # Filtre 120..80 jours en arriere : uniquement fc-past (100j retard)
            df = (today - timedelta(days=120)).strftime("%Y-%m-%d")
            dt = (today - timedelta(days=80)).strftime("%Y-%m-%d")
            resp2 = await handler(owner_id=owner_id, copropriete_id=copro_id,
                                  date_from=df, date_to=dt)
            body_period = b"".join([chunk async for chunk in resp2.body_iterator])
            txt_period = fitz.open(stream=body_period, filetype="pdf")[0].get_text()
            # fc-past (100j) est dans [120..80] -> present
            assert "Trimestriel 1/4 - Past" in txt_period
            # fc-veryold (200j) est HORS [120..80] -> absent
            assert "Trimestriel 4/4 - VeryOld" not in txt_period
            # Mention de periode dans le texte
            assert "echeance se situe" in txt_period
            assert df.split("-")[2] + "/" + df.split("-")[1] in txt_period or \
                datetime.strptime(df, "%Y-%m-%d").strftime("%d/%m/%Y") in txt_period
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())



def test_letter_pdf_creditor_shows_situation_en_regle():
    """iter90hn : proprietaire crediteur -> PDF titre 'SITUATION EN REGLE',
    pas de section 'Appels de fonds concernes', mais bien la section
    'Paiements pris en compte' pour la transparence."""
    copro_id = f"acp-hn-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hn-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.exports import create_reminders_router
        import fitz
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        # Seed : 700 EUR d'appels + 1000 EUR de paiement bancaire = crediteur 300
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=True)
        try:
            router = create_reminders_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reminders/owner/{owner_id}/letter")
            resp = await handler(owner_id=owner_id, copropriete_id=copro_id)
            body = b"".join([chunk async for chunk in resp.body_iterator])
            txt = fitz.open(stream=body, filetype="pdf")[0].get_text()
            # Titre adapte au statut crediteur
            assert "SITUATION EN REGLE" in txt
            assert "RAPPEL DE PAIEMENT" not in txt
            # Message de confirmation
            assert "situation de compte" in txt and "en regle" in txt
            # Solde en faveur (300 EUR = 1000 paiement - 700 appels)
            assert "en votre faveur" in txt
            # Pas de section "Appels de fonds concernes" (rien du)
            assert "Appels de fonds concernes" not in txt
            # Pas de VCS de virement (pas de virement necessaire)
            assert "communication structuree obligatoire" not in txt
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_letter_pdf_debtor_shows_payments_and_calls():
    """iter90hn : proprietaire debiteur avec paiements partiels ->
    PDF affiche BOTH le tableau des appels ET le tableau des paiements
    (transparence) + le solde restant du."""
    copro_id = f"acp-hn-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hn-{uuid.uuid4().hex[:8]}"

    async def _run_and_seed():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.exports import create_reminders_router
        import fitz
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        # Setup manuel : 700 EUR appels + 500 EUR paiement JE (pas bank txn)
        # -> debiteur 200 EUR
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=False)
        today = datetime.now(timezone.utc).date()
        # Ajouter un paiement JE FI dans la periode
        await db.journal_entries.insert_one({
            "id": f"je-pay-{copro_id}", "copropriete_id": copro_id,
            "date": (today - timedelta(days=40)).strftime("%Y-%m-%d"),
            "journal_type": "FI",
            "description": "Paiement recu banque Alice",
            "lines": [
                {"account_number": "55000001", "debit": 500, "credit": 0},
                {"account_number": "41010001", "third_party_id": owner_id,
                 "debit": 0, "credit": 500},
            ],
        })
        try:
            router = create_reminders_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reminders/owner/{owner_id}/letter")
            resp = await handler(owner_id=owner_id, copropriete_id=copro_id)
            body = b"".join([chunk async for chunk in resp.body_iterator])
            txt = fitz.open(stream=body, filetype="pdf")[0].get_text()
            # Titre debiteur
            assert "RAPPEL DE PAIEMENT" in txt
            assert "SITUATION EN REGLE" not in txt
            # Section appels presente
            assert "Appels de fonds concernes" in txt
            assert "Trimestriel 1/4 - Past" in txt
            # Section paiements presente (transparence)
            assert "Paiements pris en compte" in txt
            assert "Paiement recu banque Alice" in txt
            # Solde restant du affiche (debit-credit = 700-500=200)
            assert "Solde restant du" in txt
            assert "200.00" in txt or "200,00" in txt
            # VCS de virement present
            assert "communication structuree obligatoire" in txt
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run_and_seed())


def test_late_payments_uses_date_to_as_balance_cutoff():
    """iter90hn : le calcul du solde tier utilise date_to comme cutoff.
    Un proprietaire debiteur globalement mais crediteur a date_to ne
    doit PAS apparaitre dans la liste."""
    copro_id = f"acp-hn-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-hn-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await _seed_owner_and_calls(db, copro_id, owner_id, owner_credit_via_bank=True)
        try:
            # Query avec date_to = il y a 100 jours (avant le paiement bancaire
            # date -50 jours) -> le proprio est DEBITEUR a cette date
            today = datetime.now(timezone.utc).date()
            df = (today - timedelta(days=210)).strftime("%Y-%m-%d")
            dt_early = (today - timedelta(days=100)).strftime("%Y-%m-%d")
            data_early = await _call_late(copro_id, date_from=df, date_to=dt_early)
            # A cette date, la fc-veryold (200j) est deja echue mais pas le paiement
            # -> le proprio DOIT apparaitre (debiteur avant paiement)
            assert data_early["summary"]["total_count"] >= 1

            # Query avec date_to = aujourd'hui -> apres le paiement -> crediteur
            data_now = await _call_late(copro_id, date_from=df,
                                        date_to=today.strftime("%Y-%m-%d"))
            assert data_now["summary"]["total_count"] == 0
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())
