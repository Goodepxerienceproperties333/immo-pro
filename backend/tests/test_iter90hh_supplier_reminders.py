"""iter90hh : Test de l'endpoint /api/reminders/supplier-late-payments.

Verifie que le nouvel endpoint fournisseurs :
- Ne renvoie que les factures unpaid/partially_paid dont due_date est depassee.
- Applique correctement les filtres date_from / date_to / grace_days.
- Exclut les avoirs (is_credit_note=True).
- Calcule correctement `amount` = total - deja paye.
- Assigne la bonne severite selon les jours de retard.
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
    await db.invoices.delete_many({"copropriete_id": copro_id})
    today = datetime.now(timezone.utc).date()
    old_due = (today - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_due = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    veryold_due = (today - timedelta(days=200)).strftime("%Y-%m-%d")
    docs = [
        # Facture impayee 60j (severite urgent)
        {
            "id": f"inv-old-{copro_id}", "copropriete_id": copro_id,
            "number": "INV-001", "due_date": old_due,
            "supplier_id": "s1", "supplier_name": "ABC Chauffage",
            "total_amount": 500.0, "paid_amount": 0.0,
            "status": "unpaid",
        },
        # Facture partiellement payee 200j (severite critique)
        {
            "id": f"inv-part-{copro_id}", "copropriete_id": copro_id,
            "number": "INV-002", "due_date": veryold_due,
            "supplier_id": "s2", "supplier_name": "XYZ Electricite",
            "total_amount": 300.0, "paid_amount": 100.0,
            "status": "partially_paid",
        },
        # Facture recente 5j -> exclue avec grace 7
        {
            "id": f"inv-recent-{copro_id}", "copropriete_id": copro_id,
            "number": "INV-003", "due_date": recent_due,
            "supplier_id": "s3", "supplier_name": "Recent SPRL",
            "total_amount": 150.0, "paid_amount": 0.0,
            "status": "unpaid",
        },
        # Facture payee -> exclue
        {
            "id": f"inv-paid-{copro_id}", "copropriete_id": copro_id,
            "number": "INV-004", "due_date": old_due,
            "supplier_id": "s4", "supplier_name": "Paid Corp",
            "total_amount": 200.0, "paid_amount": 200.0,
            "status": "paid",
        },
        # Avoir (credit note) -> exclu
        {
            "id": f"inv-avoir-{copro_id}", "copropriete_id": copro_id,
            "number": "AV-005", "due_date": old_due,
            "supplier_id": "s5", "supplier_name": "Credit SPRL",
            "total_amount": 100.0, "paid_amount": 0.0,
            "status": "unpaid", "is_credit_note": True,
        },
    ]
    await db.invoices.insert_many(docs)


async def _cleanup(db, copro_id: str):
    await db.invoices.delete_many({"copropriete_id": copro_id})


async def _call(copro_id: str, **kwargs):
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.exports import create_reminders_router

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await _seed(db, copro_id)
    try:
        router = create_reminders_router(db)
        handler = None
        for r in router.routes:
            if getattr(r, "path", "") == "/api/reminders/supplier-late-payments":
                handler = r.endpoint
                break
        assert handler is not None
        return await handler(copropriete_id=copro_id, **kwargs)
    finally:
        await _cleanup(db, copro_id)
        client.close()


def test_returns_only_unpaid_and_excludes_paid_and_credit_notes():
    """Seules les factures unpaid/partially_paid non-avoir en retard sont
    retournees. Verifie aussi que amount = total - paid_amount."""
    copro_id = f"acp-iter90hh-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_call(copro_id, grace_days=0))
    # inv-old (60j), inv-part (200j), inv-recent (5j) = 3 en retard (>0j)
    # exclus : inv-paid (status=paid), inv-avoir (is_credit_note)
    assert data["summary"]["total_count"] == 3
    numbers = {i["invoice_number"] for i in data["late_payments"]}
    assert numbers == {"INV-001", "INV-002", "INV-003"}
    # Verifier amount = total - paid pour inv-part
    inv_part = next(i for i in data["late_payments"] if i["invoice_number"] == "INV-002")
    assert abs(inv_part["amount"] - 200.0) < 0.01
    assert abs(inv_part["amount_total"] - 300.0) < 0.01
    assert abs(inv_part["amount_paid"] - 100.0) < 0.01


def test_grace_days_filters_recent():
    """grace_days=7 exclut l'inv-recent (5j)."""
    copro_id = f"acp-iter90hh-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_call(copro_id, grace_days=7))
    assert data["summary"]["total_count"] == 2
    numbers = {i["invoice_number"] for i in data["late_payments"]}
    assert numbers == {"INV-001", "INV-002"}


def test_severity_assignment():
    """Verifie l'assignation critique/urgent/rappel."""
    copro_id = f"acp-iter90hh-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_call(copro_id, grace_days=0))
    for item in data["late_payments"]:
        if item["invoice_number"] == "INV-001":  # 60j
            assert item["severity"] == "urgent"
        elif item["invoice_number"] == "INV-002":  # 200j
            assert item["severity"] == "critique"
        elif item["invoice_number"] == "INV-003":  # 5j
            assert item["severity"] == "rappel1"


def test_date_period_filter():
    """Filtrer par date_from/date_to sur due_date."""
    copro_id = f"acp-iter90hh-{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).date()
    df = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    dt = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    data = asyncio.run(_call(copro_id, date_from=df, date_to=dt))
    # Seul INV-001 (due=60j en arriere) tombe dans [90j..30j]
    assert data["summary"]["total_count"] == 1
    assert data["late_payments"][0]["invoice_number"] == "INV-001"


def test_endpoint_registered():
    """Verifie la presence de l'endpoint dans le router."""
    from routes.exports import create_reminders_router
    # Utilise un DB stub minimaliste : le routing ne touche pas la DB.
    router = create_reminders_router(None)
    paths = [getattr(r, "path", "") for r in router.routes]
    assert "/api/reminders/supplier-late-payments" in paths
    assert "/api/reminders/late-payments" in paths
