"""iter90i0 : Test du filtre par plage de dates sur GET
/api/owner/bank-accounts/{copropriete_id}.

Verifie que :
1. Sans filtre : tous les mouvements sont retournes (jusqu'a `limit`).
2. Avec `start_date` seul : les mouvements >= start_date.
3. Avec `end_date` seul : les mouvements <= end_date.
4. Avec les deux : uniquement les mouvements dans l'intervalle.
5. Le solde comptable reste INDEPENDANT du filtre (source de verite globale).
6. `movements_total_count` reflete le TOTAL sur la periode, meme si `limit`
   tronque la liste retournee.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed(db, copro_id, owner_id, pcmn):
    """Cree 5 mouvements bancaires etalees sur 5 mois + solde comptable
    coherent via journal_entries (700 EUR credit). iter90i2 : les
    transactions sont liees au compte via `bank_statements.account_number`
    (schema reel de la production, pas via un champ `iban` sur txn)."""
    await db.owners.delete_many({"id": owner_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})
    await db.bank_transactions.delete_many({"copropriete_id": copro_id})
    await db.bank_statements.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})

    iban_normal = "BE00 1111 2222 3333"
    iban_norm = iban_normal.replace(" ", "").upper()
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP i0", "status": "active",
        "bank_accounts": [
            {"iban": iban_normal, "bic": "TESTBEBB",
             "account_type": "vue", "is_default": True, "label": "Compte principal",
             "pcmn_number": pcmn},
        ],
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "Alice", "copropriete_ids": [copro_id],
    })
    await db.lots.insert_one({
        "id": f"lot-{owner_id}", "copropriete_id": copro_id,
        "owner_id": owner_id, "number": "01",
    })
    # iter90i2 : cree un statement lie au compte (account_number = IBAN)
    stmt_id = f"stmt-{copro_id}"
    await db.bank_statements.insert_one({
        "id": stmt_id, "copropriete_id": copro_id,
        "account_number": iban_norm, "date": "2024-01-01",
        "opening_balance": 0.0, "closing_balance": 0.0,
    })
    # 5 transactions bancaires : 2024-01-15, 2024-03-15, 2024-06-15,
    # 2024-09-15, 2024-12-15
    dates = ["2024-01-15", "2024-03-15", "2024-06-15", "2024-09-15", "2024-12-15"]
    for i, d in enumerate(dates):
        await db.bank_transactions.insert_one({
            "id": f"txn-{copro_id}-{i}", "copropriete_id": copro_id,
            "statement_id": stmt_id,  # iter90i2 : lien via statement
            "date": d, "amount": 100.0 + i * 10.0,
            "counterparty_name": f"CP {i}",
            "counterparty_account": f"BE99 2222 3333 444{i}",
            "communication": f"COMM-{i} facture n{i}",
            "transaction_type": "credit" if i % 2 == 0 else "debit",
            "matched": bool(i % 2),
        })
    # Journal entry : credit 700 sur pcmn -> solde comptable = -700
    await db.journal_entries.insert_one({
        "id": f"je-{copro_id}", "copropriete_id": copro_id,
        "date": "2024-06-01", "journal_type": "FI",
        "lines": [
            {"account_number": pcmn, "debit": 0, "credit": 700},
            {"account_number": "70000001", "debit": 700, "credit": 0},
        ],
    })


async def _cleanup(db, copro_id, owner_id):
    await db.owners.delete_many({"id": owner_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.coproprietes.delete_many({"id": copro_id})
    await db.bank_transactions.delete_many({"copropriete_id": copro_id})
    await db.bank_statements.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})


async def _call_bank_accounts(db, copro_id, owner_id, **kwargs):
    """Invoque l'endpoint owner_bank_accounts en shortcut via le router.
    Passe manuellement la resolution proprio (bypass auth pour le test)."""
    from routes.owner_portal import create_owner_portal_router
    router = create_owner_portal_router(db)
    handler = next(r.endpoint for r in router.routes
                   if getattr(r, "path", "") == "/api/owner/bank-accounts/{copropriete_id}")
    # Fake request avec state.user_id + monkeypatch de _resolve_owner_ids
    import routes.owner_portal as op

    async def _fake_resolve(_db, _request):
        return [owner_id], {"id": owner_id, "name": "Alice"}
    original = op._resolve_owner_ids
    op._resolve_owner_ids = _fake_resolve
    try:
        result = await handler(
            copropriete_id=copro_id, request=None, **kwargs,
        )
        return result
    finally:
        op._resolve_owner_ids = original


def test_bank_accounts_no_date_filter_returns_all_movements():
    """Baseline : sans filtre, on retourne les 5 mouvements."""
    copro_id = f"acp-i0-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i0-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, owner_id, pcmn)
            data = await _call_bank_accounts(db, copro_id, owner_id)
            assert len(data["bank_accounts"]) == 1
            ba = data["bank_accounts"][0]
            assert ba["movements_total_count"] == 5
            assert len(ba["recent_movements"]) == 5
            # Solde comptable = -700 (credit)
            assert abs(ba["balance"] + 700.0) < 0.01
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_bank_accounts_start_date_filter():
    """Avec start_date=2024-06-01 : 3 mouvements attendus (06/15, 09/15, 12/15)."""
    copro_id = f"acp-i0-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i0-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, owner_id, pcmn)
            data = await _call_bank_accounts(
                db, copro_id, owner_id, start_date="2024-06-01",
            )
            ba = data["bank_accounts"][0]
            assert ba["movements_total_count"] == 3
            # Le solde comptable reste inchange (pas de filtre)
            assert abs(ba["balance"] + 700.0) < 0.01
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_bank_accounts_end_date_filter():
    """Avec end_date=2024-04-01 : 2 mouvements attendus (01/15, 03/15)."""
    copro_id = f"acp-i0-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i0-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, owner_id, pcmn)
            data = await _call_bank_accounts(
                db, copro_id, owner_id, end_date="2024-04-01",
            )
            ba = data["bank_accounts"][0]
            assert ba["movements_total_count"] == 2
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_bank_accounts_range_filter_intersects():
    """Range [2024-03-01, 2024-09-30] : 3 mouvements (03/15, 06/15, 09/15)."""
    copro_id = f"acp-i0-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i0-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, owner_id, pcmn)
            data = await _call_bank_accounts(
                db, copro_id, owner_id,
                start_date="2024-03-01", end_date="2024-09-30",
            )
            ba = data["bank_accounts"][0]
            assert ba["movements_total_count"] == 3
            dates = [m["date"] for m in ba["recent_movements"]]
            for d in dates:
                assert "2024-03" in d or "2024-06" in d or "2024-09" in d
            # Reponse expose les bornes utilisees
            assert data["start_date"] == "2024-03-01"
            assert data["end_date"] == "2024-09-30"
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_bank_accounts_movement_details_are_complete():
    """iter90i2 : le detail de chaque transaction est expose au proprio
    avec les 7 champs attendus (date, amount, counterparty, IBAN
    contrepartie, communication, transaction_type, matched)."""
    copro_id = f"acp-i2-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i2-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed(db, copro_id, owner_id, pcmn)
            data = await _call_bank_accounts(db, copro_id, owner_id)
            ba = data["bank_accounts"][0]
            assert len(ba["recent_movements"]) == 5, (
                "5 mouvements attendus - le linking statement_id/iban "
                "est probablement casse si on obtient 0"
            )
            mv = ba["recent_movements"][0]
            # Verifier la presence des champs cles
            for field in ("date", "amount", "counterparty", "counterparty_account",
                          "communication", "matched", "transaction_type"):
                assert field in mv, f"Champ manquant : {field}"
            # Verifier que les valeurs ne sont pas nulles pour au moins 1 txn
            all_mvs = ba["recent_movements"]
            assert any(m["counterparty"] for m in all_mvs), (
                "Aucun mouvement n'a de counterparty_name -> mapping brise"
            )
            assert any(m["communication"] for m in all_mvs)
            assert any(m["counterparty_account"] for m in all_mvs)
            # Un mouvement a `transaction_type` (credit ou debit)
            for m in all_mvs:
                assert m["transaction_type"] in ("credit", "debit"), (
                    f"transaction_type invalide : {m['transaction_type']}"
                )
        finally:
            await _cleanup(db, copro_id, owner_id)
            client.close()

    asyncio.run(_run())


def test_bank_accounts_supports_legacy_iban_field_on_txn():
    """iter90i2 - compat retro : si une transaction a un champ `iban` explicite
    (ancien flow d'import), elle est aussi remontee meme sans statement lie."""
    copro_id = f"acp-i2-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-i2-{uuid.uuid4().hex[:8]}"
    pcmn = "551333100"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            iban_normal = "BE00 4444 5555 6666"
            iban_norm = iban_normal.replace(" ", "").upper()
            await db.coproprietes.delete_many({"id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.bank_transactions.delete_many({"copropriete_id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            await db.coproprietes.insert_one({
                "id": copro_id, "name": "ACP legacy", "status": "active",
                "bank_accounts": [
                    {"iban": iban_normal, "account_type": "vue",
                     "is_default": True, "pcmn_number": pcmn},
                ],
            })
            await db.owners.insert_one({
                "id": owner_id, "name": "Bob", "copropriete_ids": [copro_id],
            })
            await db.lots.insert_one({
                "id": f"lot-{owner_id}", "copropriete_id": copro_id,
                "owner_id": owner_id, "number": "01",
            })
            # Transaction LEGACY avec iban en direct (pas de statement)
            await db.bank_transactions.insert_one({
                "id": f"txn-legacy-{copro_id}", "copropriete_id": copro_id,
                "iban": iban_norm,  # legacy field
                "date": "2024-05-15", "amount": 42.42,
                "counterparty_name": "Legacy Payer",
                "communication": "Payment via old import",
                "matched": False,
            })
            data = await _call_bank_accounts(db, copro_id, owner_id)
            assert len(data["bank_accounts"]) == 1
            ba = data["bank_accounts"][0]
            assert ba["movements_total_count"] >= 1
            assert any(m["counterparty"] == "Legacy Payer"
                       for m in ba["recent_movements"])
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.bank_transactions.delete_many({"copropriete_id": copro_id})
            await db.lots.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())
