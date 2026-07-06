"""
Iter90aq : Template appris par fournisseur - extraction rapide sans IA.

Flow :
1. User uploade PDF facture -> extract IA remplit les champs (Sonnet 4.6).
2. User corrige et sauvegarde. Frontend appelle POST /invoice-templates/learn
   avec {supplier_id, raw_text, user_values}.
3. Backend memorise les anchors pour chaque champ.
4. Prochaine facture du meme fournisseur : detection via BCE dans texte ->
   template applique -> si les 3 champs critiques (number+date+total_amount) sont
   remplis, on skip totalement l'IA.

Scenarios testes :
1. Learn : pattern extraction pour date DD/MM/YYYY belge -> retrouve DD/MM/YYYY dans texte.
2. Learn : pattern extraction pour montant "1.234,56" avec espaces milliers.
3. Learn : premier learn -> insert. Deuxieme learn -> update + increment sample_count.
4. Apply : anchor "Date facture" trouve la valeur ISO correcte dans un nouveau texte.
5. Apply : anchor introuvable -> champ omis (pas d'erreur).
6. Endpoint /learn : POST 200 + fields learned.
7. Endpoint /learn : sans supplier_id -> 400.
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


# ---------- Unit tests des helpers ----------

def test_extract_pattern_for_date_be_format():
    """iter90aq : trouve une date DD/MM/YYYY belge dans le texte brut et memorise l'anchor."""
    from routes.invoice_templates import _extract_pattern_for_field
    text = "Facture N degres  F-2026-001\nDate facture : 15/06/2026\nMontant : 1200,00"
    result = _extract_pattern_for_field(text, "date", "2026-06-15")
    assert result is not None
    assert "facture" in result["anchor"].lower()
    assert result["example"] == "2026-06-15"


def test_extract_pattern_for_amount_european_format():
    """iter90aq : montant 1234.56 -> variante '1.234,56' avec point milliers reperee."""
    from routes.invoice_templates import _extract_pattern_for_field
    text = "Total TTC 1.234,56 EUR TVA 21%"
    result = _extract_pattern_for_field(text, "total_amount", 1234.56)
    assert result is not None
    assert result["example"] == "1234.56"


def test_extract_pattern_missing_value_returns_none():
    """Valeur absente du texte -> None."""
    from routes.invoice_templates import _extract_pattern_for_field
    text = "Rien d'utile ici"
    result = _extract_pattern_for_field(text, "total_amount", 999.99)
    assert result is None


def test_apply_template_date_extraction():
    """iter90aq : anchor apprise sur ancien texte doit fonctionner sur nouveau texte."""
    from routes.invoice_templates import apply_template
    patterns = {
        "date": {"anchor": "Date facture", "example": "2026-06-15", "sample_count": 1},
    }
    new_text = "Facture ELECTRO SA\nDate facture 10/07/2026\nMontant 500,00"
    result = apply_template(new_text, patterns)
    assert result.get("date") == "2026-07-10"


def test_apply_template_amount_extraction():
    """iter90aq : anchor sur montant belge (comma decimal)."""
    from routes.invoice_templates import apply_template
    patterns = {
        "total_amount": {"anchor": "Total TTC", "example": "1234.56", "sample_count": 2},
    }
    text = "Detail invoice...\nTotal TTC 850,00 EUR\nMerci"
    result = apply_template(text, patterns)
    assert result.get("total_amount") == 850.00


def test_apply_template_no_anchor_match():
    """Anchor absent du nouveau texte -> champ omis, pas d'erreur."""
    from routes.invoice_templates import apply_template
    patterns = {
        "date": {"anchor": "Date facture", "example": "2026-06-15", "sample_count": 1},
    }
    text_no_anchor = "Rien de tel ici"
    result = apply_template(text_no_anchor, patterns)
    assert "date" not in result


# ---------- E2E tests via HTTP ----------

async def _run_learn_endpoint_creates_template():
    """POST /invoice-templates/learn cree le template pour un nouveau supplier."""
    db = await _mongo()
    cid = f"iter90aq-{uuid.uuid4()}"
    sup_id = f"sup-{uuid.uuid4()}"
    await db.suppliers.insert_one({
        "id": sup_id, "name": "ELECTRO TEST",
        "tier_accounts": {cid: {"main": "440001"}},
    })
    try:
        c = await _admin_client()
        try:
            raw_text = (
                "ELECTRO TEST SPRL\n"
                "Rue de test 5, 1000 Bruxelles\n"
                "BE 0123.456.789\n"
                "Facture N degres  F-2026-777\n"
                "Date facture : 15/06/2026\n"
                "Total TTC 1.234,56 EUR\n"
            )
            r = await c.post("/api/invoice-templates/learn", json={
                "supplier_id": sup_id,
                "supplier_name": "ELECTRO TEST",
                "copropriete_id": cid,
                "raw_text": raw_text,
                "user_values": {
                    "number": "F-2026-777",
                    "date": "2026-06-15",
                    "total_amount": 1234.56,
                },
            })
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["learned"] >= 2  # au moins date + total_amount
            assert "date" in data["fields"]
            assert "total_amount" in data["fields"]

            # Le template doit exister en base
            r2 = await c.get(f"/api/invoice-templates/{sup_id}?copropriete_id={cid}")
            assert r2.status_code == 200
            tpl = r2.json()
            assert tpl["found"] is True
            assert "patterns" in tpl
            assert "date" in tpl["patterns"]
            assert tpl["patterns"]["date"]["sample_count"] == 1
        finally:
            await c.aclose()
    finally:
        await db.suppliers.delete_one({"id": sup_id})
        await db.invoice_templates.delete_many({"supplier_id": sup_id})


async def _run_learn_endpoint_updates_sample_count():
    """Deuxieme learn du meme supplier -> update + increment sample_count."""
    db = await _mongo()
    cid = f"iter90aq-upd-{uuid.uuid4()}"
    sup_id = f"sup-{uuid.uuid4()}"
    await db.suppliers.insert_one({"id": sup_id, "name": "S", "tier_accounts": {cid: {"main": "440001"}}})
    try:
        c = await _admin_client()
        try:
            raw = "Date facture : 15/06/2026\nTotal TTC 500,00 EUR"
            for _ in range(3):
                r = await c.post("/api/invoice-templates/learn", json={
                    "supplier_id": sup_id, "copropriete_id": cid,
                    "raw_text": raw,
                    "user_values": {"date": "2026-06-15", "total_amount": 500.0},
                })
                assert r.status_code == 200

            r = await c.get(f"/api/invoice-templates/{sup_id}?copropriete_id={cid}")
            tpl = r.json()
            # sample_count doit refleter les 3 apprentissages
            assert tpl["patterns"]["date"]["sample_count"] == 3
        finally:
            await c.aclose()
    finally:
        await db.suppliers.delete_one({"id": sup_id})
        await db.invoice_templates.delete_many({"supplier_id": sup_id})


async def _run_learn_endpoint_400_without_supplier():
    c = await _admin_client()
    try:
        r = await c.post("/api/invoice-templates/learn", json={
            "supplier_id": "",
            "raw_text": "some text",
            "user_values": {"date": "2026-06-15"},
        })
        assert r.status_code == 400
    finally:
        await c.aclose()


def test_learn_endpoint_creates_template():
    asyncio.run(_run_learn_endpoint_creates_template())


def test_learn_endpoint_updates_sample_count():
    asyncio.run(_run_learn_endpoint_updates_sample_count())


def test_learn_endpoint_400_without_supplier():
    asyncio.run(_run_learn_endpoint_400_without_supplier())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
