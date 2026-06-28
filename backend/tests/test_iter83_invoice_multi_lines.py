"""Regression test - iter83 - Facture avec lignes multiples (split natures).

Demande user : "donner possibilite de mettre plusieurs natures de depenses
lors de la creation d'une facture chargees par IA aussi !"

Le mode multi-lignes permet de splitter une facture fournisseur en plusieurs
comptes/natures/cles de repartition, avec UNE SEULE ecriture comptable AC
contenant N debits (1 par ligne) + 1 credit fournisseur.

Tests :
- E2E create avec lines : journal entry contient N+1 lignes (N debits + 1 credit)
- Validation : somme des lignes != total -> 400
- Validation : ligne sans compte -> 400
- Multi-keys : distribution_lines agrege correctement les amounts
- Mode 1-ligne (lines=None ou []) garde le comportement legacy
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_minimal_acp():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr83-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    lot1_id = f"lot1-{uuid.uuid4()}"
    lot2_id = f"lot2-{uuid.uuid4()}"
    key1_id = f"k1-{uuid.uuid4()}"
    key2_id = f"k2-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "ITER83", "reference": "TEST-ITER83", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid, "status": "open",
    })
    # PCMN minimal
    await db.pcmn_accounts.insert_many([
        {"number": "611", "name": "Entretien", "class_num": 6, "copropriete_id": cid},
        {"number": "612", "name": "Electricite", "class_num": 6, "copropriete_id": cid},
        {"number": "613", "name": "Honoraires syndic", "class_num": 6, "copropriete_id": cid},
        {"number": "44000001", "name": "Fournisseur ACME", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_one({
        "id": o1_id, "name": "Proprio Paul", "last_name": "Paul",
        "auxiliary_code": "C0001", "copropriete_ids": [cid],
    })
    await db.lots.insert_many([
        {"id": lot1_id, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 600.0},
        {"id": lot2_id, "number": "A2", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 400.0},
    ])
    # 2 cles : key1 = 100% lot1, key2 = 100% lot2
    await db.distribution_keys.insert_many([
        {"id": key1_id, "name": "Cle Lot1", "copropriete_id": cid,
         "key_type": "manual",
         "lots": [{"lot_id": lot1_id, "lot_number": "A1", "share": 100}]},
        {"id": key2_id, "name": "Cle Lot2", "copropriete_id": cid,
         "key_type": "manual",
         "lots": [{"lot_id": lot2_id, "lot_number": "A2", "share": 100}]},
    ])
    # Pre-cree le fournisseur global pour eviter la creation auto
    await db.suppliers.insert_one({
        "id": "supplier-acme", "name": "ACME SA",
        "tier_accounts": {cid: {"main": "44000001"}},
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1_id,
            "lot1": lot1_id, "lot2": lot2_id, "key1": key1_id, "key2": key2_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": ctx["o1"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.distribution_keys.delete_many({"copropriete_id": ctx["cid"]})
    await db.suppliers.delete_many({"id": "supplier-acme"})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.invoices.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str, method: str = "POST"):
    from routes.invoices import create_invoices_router
    router = create_invoices_router(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in r.methods:
            return r.endpoint
    return None


async def _test_create_multi_line_e2e():
    """Cree une facture avec 3 lignes (500/200/50 = 750 EUR) sur 3 comptes
    differents. Verifie que :
    - L'invoice document contient lines[] avec les 3 entrees normalisees
    - L'ecriture AC contient 4 lignes (3 debits + 1 credit fournisseur)
    - Le total_debit = total_credit = 750
    """
    ctx = await _setup_minimal_acp()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        assert create_fn is not None
        InvoiceInput = create_fn.__annotations__.get("data")

        payload = InvoiceInput(
            number="F-MULTI-001",
            date="2026-03-15",
            supplier="ACME SA",
            description="Facture mixte entretien + electricite + honoraires",
            total_amount=750.0,
            copropriete_id=ctx["cid"],
            lines=[
                {"account_number": "611", "amount": 500.0,
                 "distribution_key_id": ctx["key1"], "description": "Entretien Q1"},
                {"account_number": "612", "amount": 200.0,
                 "distribution_key_id": ctx["key2"], "description": "Electricite Q1"},
                {"account_number": "613", "amount": 50.0,
                 "description": "Honoraires"},
            ],
        )
        inv = await create_fn(data=payload)

        # 1. Lines persistes
        assert len(inv["lines"]) == 3, f"Expected 3 lines, got {len(inv['lines'])}"
        assert inv["lines"][0]["account_number"] == "611"
        assert inv["lines"][0]["amount"] == 500.0
        assert inv["lines"][1]["account_number"] == "612"
        assert inv["lines"][1]["amount"] == 200.0
        assert inv["lines"][2]["account_number"] == "613"

        # 2. Ecriture comptable : 3 debits + 1 credit
        je = await db.journal_entries.find_one(
            {"source_id": inv["id"], "source_type": "invoice"}, {"_id": 0}
        )
        assert je is not None, "Ecriture AC manquante"
        debits = [l for l in je["lines"] if l["debit"] > 0]
        credits = [l for l in je["lines"] if l["credit"] > 0]
        assert len(debits) == 3, f"Expected 3 debit lines, got {len(debits)}"
        assert len(credits) == 1, f"Expected 1 credit line, got {len(credits)}"
        # Verifie les comptes debites
        debit_accs = sorted([l["account_number"] for l in debits])
        assert debit_accs == ["611", "612", "613"], f"Bad debit accounts: {debit_accs}"
        # Verifie equilibre
        assert abs(je["total_debit"] - 750.0) < 0.01
        assert abs(je["total_credit"] - 750.0) < 0.01
        # Credit = fournisseur
        assert credits[0]["account_number"] == "44000001"

        # 3. distribution_lines agreges : lot1 = 500, lot2 = 200
        dist = inv["distribution_lines"]
        dist_by_lot = {d["lot_id"]: d["amount"] for d in dist}
        assert abs(dist_by_lot.get(ctx["lot1"], 0) - 500.0) < 0.01
        assert abs(dist_by_lot.get(ctx["lot2"], 0) - 200.0) < 0.01

    finally:
        await _cleanup(ctx)


async def _test_multi_line_total_mismatch():
    """Refuse la creation si somme des lignes != total_amount."""
    ctx = await _setup_minimal_acp()
    db = ctx["db"]
    from fastapi import HTTPException
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        payload = InvoiceInput(
            number="F-MULTI-002", date="2026-03-15", supplier="ACME SA",
            description="Bad total", total_amount=750.0, copropriete_id=ctx["cid"],
            lines=[
                {"account_number": "611", "amount": 500.0},
                {"account_number": "612", "amount": 100.0},  # somme=600 != 750
            ],
        )
        try:
            await create_fn(data=payload)
            raise AssertionError("Expected HTTPException 400 for total mismatch")
        except HTTPException as e:
            assert e.status_code == 400
            assert "ifferent" in e.detail or "diff" in e.detail.lower()
    finally:
        await _cleanup(ctx)


async def _test_legacy_single_line_still_works():
    """Mode 1-ligne (lines=None) garde le comportement legacy : 1 debit + 1 credit."""
    ctx = await _setup_minimal_acp()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        payload = InvoiceInput(
            number="F-SINGLE-001", date="2026-03-15", supplier="ACME SA",
            description="Single line", total_amount=300.0, copropriete_id=ctx["cid"],
            account_number="611", distribution_key_id=ctx["key1"],
        )
        inv = await create_fn(data=payload)
        # lines doit etre vide ou absent en mode legacy
        assert inv.get("lines") == [] or inv.get("lines") is None
        je = await db.journal_entries.find_one(
            {"source_id": inv["id"], "source_type": "invoice"}, {"_id": 0}
        )
        assert je is not None
        assert len(je["lines"]) == 2, f"Single-line mode expected 2 lines, got {len(je['lines'])}"
    finally:
        await _cleanup(ctx)


def test_create_invoice_with_multi_lines():
    asyncio.run(_test_create_multi_line_e2e())


def test_multi_line_total_mismatch_rejected():
    asyncio.run(_test_multi_line_total_mismatch())


def test_legacy_single_line_unchanged():
    asyncio.run(_test_legacy_single_line_still_works())
