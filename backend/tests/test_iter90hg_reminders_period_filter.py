"""iter90hg : Test filtre par periode sur /api/reminders/late-payments.

Verifie que les params optionnels `date_from` / `date_to` filtrent correctement
les appels de fonds sur leur `due_date`. Utile pour generer les rappels par
mois / trimestre / annee.

Utilise le pattern iter77 : asyncio.run() + AsyncIOMotorClient direct + appel
en direct de la handler function (pas de TestClient pour eviter les mismatchs
d'event loop entre motor et starlette TestClient).
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


async def _seed(db, copro_id: str):
    await db.fund_calls.delete_many({"copropriete_id": copro_id})
    await db.owners.delete_many({"copropriete_ids": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    today = datetime.now(timezone.utc).date()
    old_due = (today - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_due = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    veryold_due = (today - timedelta(days=200)).strftime("%Y-%m-%d")
    # iter90hk : creer 3 proprietaires DEBITEURS (JE debit sur leurs
    # comptes tiers) pour que list_late_payments les retourne comme
    # "en retard" via `_compute_balance_tiers_for_ui`.
    owners = [
        ("o1", "Alice", 100.0, "acc-o1"),
        ("o2", "Bob", 200.0, "acc-o2"),
        ("o3", "Carol", 300.0, "acc-o3"),
    ]
    for oid, name, amt, acc in owners:
        await db.owners.insert_one({
            "id": oid, "name": name, "copropriete_ids": [copro_id],
            "tier_accounts": {copro_id: {"provisions": acc}},
        })
        await db.lots.insert_one({
            "id": f"lot-{oid}", "copropriete_id": copro_id,
            "owner_id": oid, "reference": f"L-{oid}",
        })
        # JE debit sur le compte tier -> proprietaire debiteur
        await db.journal_entries.insert_one({
            "id": f"je-{oid}", "copropriete_id": copro_id,
            "date": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
            "journal_type": "OD",
            "lines": [
                {"account_number": acc, "third_party_id": oid,
                 "debit": amt, "credit": 0},
                {"account_number": "70000001", "debit": 0, "credit": amt},
            ],
        })
    await db.fund_calls.insert_many([
        {
            "id": f"fc-old-{copro_id}", "copropriete_id": copro_id,
            "name": "Vieux appel", "due_date": old_due,
            "distribution": [
                {"owner_id": "o1", "owner_name": "Alice", "vcs_code": "V1",
                 "amount": 100.0, "paid": False},
            ],
        },
        {
            "id": f"fc-recent-{copro_id}", "copropriete_id": copro_id,
            "name": "Recent", "due_date": recent_due,
            "distribution": [
                {"owner_id": "o2", "owner_name": "Bob", "vcs_code": "V2",
                 "amount": 200.0, "paid": False},
            ],
        },
        {
            "id": f"fc-veryold-{copro_id}", "copropriete_id": copro_id,
            "name": "Tres vieux", "due_date": veryold_due,
            "distribution": [
                {"owner_id": "o3", "owner_name": "Carol", "vcs_code": "V3",
                 "amount": 300.0, "paid": False},
            ],
        },
    ])


async def _cleanup(db, copro_id: str):
    await db.fund_calls.delete_many({"copropriete_id": copro_id})
    await db.owners.delete_many({"copropriete_ids": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})


async def _call_list_late(copro_id: str, **kwargs):
    """Instancie le router et appelle la handler function directement."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.exports import create_reminders_router

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await _seed(db, copro_id)
    try:
        router = create_reminders_router(db)
        # Trouve la fonction list_late_payments dans les routes
        handler = None
        for r in router.routes:
            if getattr(r, "path", "") == "/api/reminders/late-payments":
                handler = r.endpoint
                break
        assert handler is not None, "handler introuvable"
        return await handler(copropriete_id=copro_id, **kwargs)
    finally:
        await _cleanup(db, copro_id)
        client.close()


def test_no_period_returns_all_late():
    """Sans date_from/date_to, tous les appels en retard sont retournes."""
    copro_id = f"acp-iter90hg-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_call_list_late(copro_id))
    assert data["summary"]["total_count"] == 3
    assert abs(data["summary"]["total_amount"] - 600.0) < 0.01


def test_date_from_excludes_older():
    """date_from filtre les appels dont due_date est anterieur."""
    copro_id = f"acp-iter90hg-{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).date()
    df = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    data = asyncio.run(_call_list_late(copro_id, date_from=df))
    # fc-old (60j) + fc-recent (5j). fc-veryold (200j) exclu.
    assert data["summary"]["total_count"] == 2
    names = {i["fund_call_name"] for i in data["late_payments"]}
    assert names == {"Vieux appel", "Recent"}


def test_date_to_excludes_newer():
    """date_to filtre les appels dont due_date est posterieur."""
    copro_id = f"acp-iter90hg-{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).date()
    dt = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    data = asyncio.run(_call_list_late(copro_id, date_to=dt))
    # fc-old (60j) + fc-veryold (200j). fc-recent (5j) exclu.
    assert data["summary"]["total_count"] == 2
    names = {i["fund_call_name"] for i in data["late_payments"]}
    assert names == {"Vieux appel", "Tres vieux"}


def test_period_isolates_middle_bucket():
    """Combinaison date_from + date_to isole le bucket du milieu."""
    copro_id = f"acp-iter90hg-{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).date()
    df = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    dt = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    data = asyncio.run(_call_list_late(copro_id, date_from=df, date_to=dt))
    assert data["summary"]["total_count"] == 1
    assert data["late_payments"][0]["fund_call_name"] == "Vieux appel"


def test_invalid_date_format_ignored():
    """Un format de date invalide est ignore (pas de crash)."""
    copro_id = f"acp-iter90hg-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_call_list_late(copro_id, date_from="not-a-date", date_to="nope"))
    assert data["summary"]["total_count"] == 3
