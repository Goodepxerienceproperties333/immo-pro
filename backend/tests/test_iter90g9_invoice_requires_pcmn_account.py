"""iter90g9 : verrou PCMN strict - IMPOSSIBLE de creer une facture sans
compte comptable affecte. Rebond du user "comment est-il possible qu'une
facture n'ait pas de compte comptable affecte ? Cette situation ne doit
jamais arriver !"

**Analyse des chemins d'entree (avant iter90g9)** :
1. `POST /api/invoices` (creation manuelle) : `InvoiceInput.account_number:
   Optional[str] = ""` permettait un enregistrement sans compte si aucune
   category n'etait selectionnee.
2. `PUT /api/invoices/{id}` (modification) : idem, permettait de vider le
   compte comptable d'une facture existante.
3. `import_wizard.py::_commit_invoices` (import Optipro/CODA) : inserait
   un doc avec `account_number = ""` si le fichier source n'avait pas
   fourni ce champ ET qu'aucune nature de depense n'etait matchee.

**Fix iter90g9** :
1. Dans `create_invoice` : apres derivation via category / is_private_fee /
   resolved_lines, si `account_number` toujours vide en mode 1-ligne ->
   HTTP 400 avec message explicite PCMN.
2. Dans `update_invoice` : meme verrou. Impossible de vider retroactivement
   le compte d'une facture.
3. Dans `import_wizard._commit_invoices` :
   - Enrichissement : si `account_number` absent MAIS `nature_code` matche
     une expense_category qui a un `account_number`, derive automatiquement.
   - Verrou : si toujours vide -> ajoute dans `errors[]` avec message
     explicite et SKIP l'insertion (au lieu de creer une facture orpheline).

**Retro-compatibilite (private fees, multi-line)** :
- `is_private_fee=True` force account_number=643 (deja gere).
- Mode multi-lignes : chaque ligne DOIT avoir son propre account_number
  (deja valide par `_resolve_invoice_lines` ligne 819).
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_acp(db, suffix: str) -> dict:
    """Setup minimal : 1 ACP, 1 nature de depense liee au compte 6140."""
    cid = f"iter90g9-{suffix}"
    cat_id = f"cat-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": "iter90g9 ACP"})
    await db.pcmn_accounts.insert_many([
        {"number": "6140", "name": "Assurance incendie",
         "copropriete_id": cid, "class_num": 6},
        {"number": "61300", "name": "Honoraires syndic",
         "copropriete_id": cid, "class_num": 6},
    ])
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid,
        "name": "Assurance incendie", "account_number": "6140",
    })
    # Exercice fiscal ouvert autour de 2026 pour permettre la saisie
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid,
        "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "status": "open",
    })
    return {"cid": cid, "cat_id": cat_id}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_iter90g9_create_invoice_without_account_number_rejected():
    """POST /api/invoices sans account_number ni expense_category_id ni
    is_private_fee -> 400 avec message explicite PCMN."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-BAD-001",
                    "date": "2026-03-15",
                    "supplier": "Test Supplier",
                    "description": "Facture SANS compte",
                    "total_amount": 100.0,
                    "copropriete_id": ctx["cid"],
                    # <-- account_number ABSENT
                    # <-- expense_category_id ABSENT
                    # <-- is_private_fee ABSENT
                })
                assert r.status_code == 400, (
                    f"iter90g9 : creation sans account_number doit etre "
                    f"refusee (400). Recu {r.status_code} : {r.text}"
                )
                # Le message doit etre explicite (mentionne PCMN)
                detail = r.json().get("detail", "").lower()
                assert "compte" in detail and "pcmn" in detail, (
                    f"Le message d'erreur doit mentionner 'compte' et 'PCMN'. "
                    f"Recu : {detail}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g9_create_invoice_with_account_number_accepted():
    """POST /api/invoices avec account_number explicite -> 200."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-OK-001",
                    "date": "2026-03-15",
                    "supplier": "Test Supplier",
                    "description": "Facture avec compte",
                    "total_amount": 100.0,
                    "account_number": "6140",  # <-- OK
                    "copropriete_id": ctx["cid"],
                })
                assert r.status_code == 200, r.text
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g9_create_invoice_with_category_accepted():
    """POST /api/invoices avec expense_category_id (qui derive 6140)
    -> 200 et account_number persiste = 6140."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-CAT-001",
                    "date": "2026-03-15",
                    "supplier": "Test Supplier",
                    "description": "Facture via nature",
                    "total_amount": 100.0,
                    "expense_category_id": ctx["cat_id"],  # <-- derive 6140
                    "copropriete_id": ctx["cid"],
                })
                assert r.status_code == 200, r.text
                data = r.json()
                assert data.get("account_number") == "6140", (
                    f"account_number doit etre derive de la nature. "
                    f"Recu : {data.get('account_number')}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g9_update_invoice_cannot_clear_account_number():
    """PUT /api/invoices/{id} ne doit PAS permettre de vider
    account_number d'une facture existante."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                # Cree une facture valide
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-UPD-001",
                    "date": "2026-03-15",
                    "supplier": "Test Supplier",
                    "description": "Facture initiale",
                    "total_amount": 100.0,
                    "account_number": "6140",
                    "copropriete_id": ctx["cid"],
                })
                assert r.status_code == 200
                inv_id = r.json()["id"]
                # Tente de la modifier en vidant account_number
                r2 = await c.put(f"{BACKEND_URL}/api/invoices/{inv_id}", json={
                    "number": "FA-UPD-001",
                    "date": "2026-03-15",
                    "supplier": "Test Supplier",
                    "description": "Facture modifiee",
                    "total_amount": 100.0,
                    "account_number": "",  # <-- TENTATIVE de vider
                    "copropriete_id": ctx["cid"],
                })
                assert r2.status_code == 400, (
                    f"iter90g9 : PUT sans account_number doit etre refuse "
                    f"(400). Recu {r2.status_code} : {r2.text}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g9_private_fee_forces_643_no_error():
    """Une facture is_private_fee=True force account_number=643 :
    doit passer meme sans account_number ni category explicites."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        # Ajoute un lot + owner pour le private_fee_owner_id
        owner_id = f"owner-{suffix}"
        lot_id = f"lot-{suffix}"
        await db.owners.insert_one({"id": owner_id, "name": "PrivateOwner"})
        await db.lots.insert_one({"id": lot_id, "number": "1",
                                  "copropriete_id": ctx["cid"],
                                  "owner_id": owner_id, "quotity": 100})
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-PRIV-001",
                    "date": "2026-03-15",
                    "supplier": "Plombier",
                    "description": "Reparation privative",
                    "total_amount": 250.0,
                    "is_private_fee": True,
                    "private_fee_owner_id": owner_id,
                    # <-- pas d'account_number, mais 643 forced
                    "copropriete_id": ctx["cid"],
                })
                assert r.status_code == 200, r.text
                assert r.json().get("account_number") == "643", (
                    f"Frais privatif doit forcer 643. Recu : {r.json().get('account_number')}"
                )
        finally:
            await db.owners.delete_one({"id": owner_id})
            await db.lots.delete_one({"id": lot_id})
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g9_multiline_invoice_no_header_account_accepted():
    """Une facture multi-lignes (lines=[...]) sans account_number
    au header MAIS avec un account_number sur chaque ligne : accepte."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_acp(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/invoices", json={
                    "number": "FA-MULTI-001",
                    "date": "2026-03-15",
                    "supplier": "Multi Supplier",
                    "description": "Facture multi-lignes",
                    "total_amount": 300.0,
                    "copropriete_id": ctx["cid"],
                    # header account_number ABSENT (OK en mode multi-lignes)
                    "lines": [
                        {"account_number": "6140", "amount": 200.0,
                         "description": "Assurance"},
                        {"account_number": "61300", "amount": 100.0,
                         "description": "Honoraires"},
                    ],
                })
                assert r.status_code == 200, (
                    f"Facture multi-lignes avec comptes DANS chaque ligne "
                    f"doit etre acceptee. Recu {r.status_code} : {r.text}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())
