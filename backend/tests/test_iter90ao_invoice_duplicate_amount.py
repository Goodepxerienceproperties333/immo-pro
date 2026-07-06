"""
Iter90ao : Anti-doublon facture enrichi (montant + date proche).

Regle 1 (existante) : (fournisseur normalise, numero normalise, ACP) -> BLOCK
Regle 2 (nouvelle) : (fournisseur, montant a 0.01 EUR pres, ACP, date +/- 3 jours)
  -> BLOCK. Attrape le cas OCR qui mal-lit un chiffre du numero, ou
  saisie manuelle divergente.

Scenarios testes :
- Numero + fournisseur + ACP identiques -> 409 Rule 1
- Numeros differents + fournisseur + montant + date +/- 3 jours -> 409 Rule 2
- Meme fournisseur/montant mais date > 3 jours -> autorise
- Fournisseur different -> autorise (recurrence identique montant OK)
- Montant different -> autorise
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


async def _admin_client():
    c = httpx.AsyncClient(timeout=30, base_url=BACKEND_URL)
    r = await c.post("/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    r.raise_for_status()
    return c


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup():
    db = await _mongo()
    cid = f"iter90ao-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    sup_a = f"sup-A-{uuid.uuid4()}"
    sup_b = f"sup-B-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "Dup-check", "reference": "DUP-t", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "611000", "name": "Entretien", "class_num": 6, "copropriete_id": cid},
        {"number": "440001", "name": "Sup A", "class_num": 4, "copropriete_id": cid},
        {"number": "440002", "name": "Sup B", "class_num": 4, "copropriete_id": cid},
    ])
    await db.suppliers.insert_many([
        {"id": sup_a, "name": "ELECTRO SA", "auxiliary_code": "F0001",
         "tier_accounts": {cid: {"main": "440001"}}},
        {"id": sup_b, "name": "CLEAN SPRL", "auxiliary_code": "F0002",
         "tier_accounts": {cid: {"main": "440002"}}},
    ])
    return {"db": db, "cid": cid, "fy_id": fy_id, "sup_a": sup_a, "sup_b": sup_b}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "invoices", "journal_entries"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"id": {"$in": [ctx["sup_a"], ctx["sup_b"]]}})


def _base_invoice(cid, supplier_name, number, date, amount):
    return {
        "supplier": supplier_name,
        "number": number,
        "date": date,
        "due_date": date,
        "total_amount": amount,
        "amount_ht": amount,
        "vat_amount": 0,
        "vat_rate": 0,
        "copropriete_id": cid,
        "description": "test",
        "distribution": [],
        "expense_account": "611000",
    }


async def _create_invoice(c, cid, supplier_name, number, date, amount):
    return await c.post("/api/invoices", json=_base_invoice(cid, supplier_name, number, date, amount))


async def _run_rule1_same_number_supplier():
    """Rule 1 : meme fournisseur + meme numero -> 409."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-001", "2026-06-15", 1200.00)
            assert r1.status_code == 200, r1.text
            r2 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-001", "2026-06-15", 1200.00)
            assert r2.status_code == 409
            assert "numero identique" in r2.json()["detail"].lower()
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_rule2_same_amount_supplier_close_date():
    """Rule 2 : meme fournisseur + meme montant + date +/- 3 jours + numeros differents -> 409."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-100", "2026-06-15", 850.50)
            assert r1.status_code == 200, r1.text
            # Meme montant + fournisseur, numero different, date J+2
            r2 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-101", "2026-06-17", 850.50)
            assert r2.status_code == 409
            detail = r2.json()["detail"].lower()
            assert "montant" in detail and "date proche" in detail
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_rule2_not_triggered_when_date_far():
    """Rule 2 NE se declenche PAS si date > 3 jours d'ecart."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-200", "2026-06-15", 500.00)
            assert r1.status_code == 200, r1.text
            # Meme montant + fournisseur, mais 5 jours plus tard
            r2 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-201", "2026-06-20", 500.00)
            assert r2.status_code == 200, r2.text
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_no_dup_when_supplier_differs():
    """Meme montant + meme numero + meme date mais fournisseur different -> autorise."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-300", "2026-06-15", 300.00)
            assert r1.status_code == 200
            r2 = await _create_invoice(c, ctx["cid"], "CLEAN SPRL", "F-2026-300", "2026-06-15", 300.00)
            assert r2.status_code == 200, r2.text
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_no_dup_when_amount_differs():
    """Meme fournisseur + numero different + date proche mais montant different -> autorise."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-400", "2026-06-15", 100.00)
            assert r1.status_code == 200
            r2 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-401", "2026-06-16", 200.00)
            assert r2.status_code == 200, r2.text
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_rule2_boundary_3_days():
    """Rule 2 : la borne 3 jours est INCLUSIVE (|delta| = 3 -> BLOCK)."""
    ctx = await _setup()
    try:
        c = await _admin_client()
        try:
            r1 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-500", "2026-06-15", 750.00)
            assert r1.status_code == 200
            r2 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-501", "2026-06-18", 750.00)
            assert r2.status_code == 409, r2.text
            # J+4 -> autorise
            r3 = await _create_invoice(c, ctx["cid"], "ELECTRO SA", "F-2026-502", "2026-06-19", 750.00)
            assert r3.status_code == 200, r3.text
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


def test_rule1_same_number_supplier():
    asyncio.run(_run_rule1_same_number_supplier())


def test_rule2_same_amount_supplier_close_date():
    asyncio.run(_run_rule2_same_amount_supplier_close_date())


def test_rule2_not_triggered_when_date_far():
    asyncio.run(_run_rule2_not_triggered_when_date_far())


def test_no_dup_when_supplier_differs():
    asyncio.run(_run_no_dup_when_supplier_differs())


def test_no_dup_when_amount_differs():
    asyncio.run(_run_no_dup_when_amount_differs())


def test_rule2_boundary_3_days():
    asyncio.run(_run_rule2_boundary_3_days())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
