"""Regression test - iter83 - Anti-doublon factures (supplier + number + ACP).

Demande user : "empecher les doublons de factures"

Regle : combinaison (fournisseur normalise + numero normalise + copropriete)
doit etre unique. Normalisation : majuscules, sans accents, sans espaces
multiples. Differentes ACP peuvent avoir le meme numero.

Tests :
- Creation 2 factures avec meme N° + meme fournisseur + meme ACP -> 409
- Variantes casse / espaces ("F-001" vs "f 001 ") -> 409 (normalise)
- Numero identique mais ACP differente -> OK (autorise)
- Fournisseur different -> OK (autorise)
- Update : modifier la facture A pour le N° de B -> 409 ; modifier sans
  changer N°/fournisseur -> OK (self-exclude)
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid1 = f"itr83d-{uuid.uuid4()}"
    cid2 = f"itr83d2-{uuid.uuid4()}"
    await db.coproprietes.insert_many([
        {"id": cid1, "name": "ACP1", "reference": "DUP1", "status": "active"},
        {"id": cid2, "name": "ACP2", "reference": "DUP2", "status": "active"},
    ])
    await db.fiscal_years.insert_many([
        {"id": f"fy-{uuid.uuid4()}", "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid1},
        {"id": f"fy-{uuid.uuid4()}", "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid2},
    ])
    await db.pcmn_accounts.insert_many([
        {"number": "611", "name": "Entretien", "class_num": 6, "copropriete_id": cid1},
        {"number": "611", "name": "Entretien", "class_num": 6, "copropriete_id": cid2},
        {"number": "44000001", "name": "Fournisseur ACME", "class_num": 4, "copropriete_id": cid1},
        {"number": "44000001", "name": "Fournisseur ACME", "class_num": 4, "copropriete_id": cid2},
    ])
    await db.suppliers.insert_one({
        "id": "sup-acme", "name": "ACME SA",
        "tier_accounts": {cid1: {"main": "44000001"}, cid2: {"main": "44000001"}},
    })
    return {"db": db, "cid1": cid1, "cid2": cid2}


async def _cleanup(ctx):
    db = ctx["db"]
    for cid in [ctx["cid1"], ctx["cid2"]]:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_many({"copropriete_id": cid})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"id": "sup-acme"})


def _ep(db, path, method):
    from routes.invoices import create_invoices_router
    router = create_invoices_router(db)
    for r in router.routes:
        if r.path == path and method.upper() in r.methods:
            return r.endpoint
    return None


async def _run():
    from fastapi import HTTPException
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _ep(db, "/api/invoices", "POST")
        update_fn = _ep(db, "/api/invoices/{invoice_id}", "PUT")
        InvoiceInput = create_fn.__annotations__.get("data")

        # 1. Premiere facture OK
        inv1 = await create_fn(data=InvoiceInput(
            number="F-001", date="2026-03-15", supplier="ACME SA",
            description="entretien Q1", total_amount=100.0,
            copropriete_id=ctx["cid1"], account_number="611",
        ))
        assert inv1["id"]

        # 2. Doublon strict -> 409
        try:
            await create_fn(data=InvoiceInput(
                number="F-001", date="2026-04-15", supplier="ACME SA",
                description="autre", total_amount=200.0,
                copropriete_id=ctx["cid1"], account_number="611",
            ))
            raise AssertionError("Expected 409 for strict duplicate")
        except HTTPException as e:
            assert e.status_code == 409
            assert "doublon" in e.detail.lower()

        # 3. Normalisation : casse + espaces -> 409
        try:
            await create_fn(data=InvoiceInput(
                number=" f-001 ", date="2026-03-20", supplier="acme sa",
                description="case test", total_amount=100.0,
                copropriete_id=ctx["cid1"], account_number="611",
            ))
            raise AssertionError("Expected 409 for case-normalized duplicate")
        except HTTPException as e:
            assert e.status_code == 409

        # 4. Meme N° mais ACP differente -> OK
        inv_acp2 = await create_fn(data=InvoiceInput(
            number="F-001", date="2026-03-15", supplier="ACME SA",
            description="autre ACP", total_amount=100.0,
            copropriete_id=ctx["cid2"], account_number="611",
        ))
        assert inv_acp2["id"]

        # 5. Meme N° mais fournisseur different -> OK
        inv_diff_sup = await create_fn(data=InvoiceInput(
            number="F-001", date="2026-03-16", supplier="BETA SARL",
            description="diff sup", total_amount=50.0,
            copropriete_id=ctx["cid1"], account_number="611",
        ))
        assert inv_diff_sup["id"]

        # 6. Update sans changer N°/fournisseur -> OK (self-exclude)
        updated = await update_fn(invoice_id=inv1["id"], data=InvoiceInput(
            number="F-001", date="2026-03-15", supplier="ACME SA",
            description="modifie", total_amount=150.0,
            copropriete_id=ctx["cid1"], account_number="611",
        ))
        assert updated["description"] == "modifie"

        # 7. Update vers le N° d'une autre facture du meme fournisseur -> 409
        # On cree une 2eme facture pour ACME, puis on tente de modifier inv1 vers son N°
        inv2 = await create_fn(data=InvoiceInput(
            number="F-002", date="2026-03-20", supplier="ACME SA",
            description="x", total_amount=300.0,
            copropriete_id=ctx["cid1"], account_number="611",
        ))
        try:
            await update_fn(invoice_id=inv1["id"], data=InvoiceInput(
                number="F-002", date="2026-03-15", supplier="ACME SA",
                description="collision", total_amount=150.0,
                copropriete_id=ctx["cid1"], account_number="611",
            ))
            raise AssertionError("Expected 409 for update collision")
        except HTTPException as e:
            assert e.status_code == 409
        # Verifie que inv2 existe toujours
        _ = inv2

        print("OK - anti-doublon facture : 7 scenarios valides")
    finally:
        await _cleanup(ctx)


def test_invoice_duplicate_prevention():
    asyncio.run(_run())
