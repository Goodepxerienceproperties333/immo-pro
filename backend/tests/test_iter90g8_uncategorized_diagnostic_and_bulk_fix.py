"""iter90g8 : diagnostic + bulk-fix des factures sans compte PCMN valide
(cas legacy import Optipro/CODA ou creation manuelle sans nature).

**Contexte utilisateur (PROD Acacia TER)** :
Le decompte TEUWEN montre 2 factures B'Cover (Assurance immeuble 997,97
EUR + Prime B'Property 149,81 EUR) sous la rubrique "Autres charges"
alors qu'Optipro les classe correctement sous "6140 - Assurance incendie"
(919,12 EUR) et "6141 - Responsabilite civile" (228,66 EUR).

Cause : `invoice.account_number = ""` chez nous (import legacy sans
mapping). Le decompte tombe sur le fallback `"_other"` -> "Autres
charges" au lieu d'agreger avec les autres factures d'assurance.

**Fix iter90g8** :
1. `GET /api/invoices/uncategorized-diagnostic` : recherche toutes les
   factures avec `account_number` vide dans une ACP (optionnellement une
   FY), les groupe par supplier, et propose un `account_number` +
   `expense_category_id` base sur les factures DEJA classifiees du meme
   supplier.
2. `POST /api/invoices/bulk-assign-account` : applique un remap batch
   sur une liste d'invoice_ids, regenere l'ecriture AC associee. Chinese
   walls stricts.

Regressions couvertes :
1. Diagnostic identifie les factures sans account_number et propose une
   suggestion basee sur l'historique du supplier.
2. Bulk-assign met a jour account_number + regenere l'ecriture.
3. Chinese wall : refuse une facture d'une autre ACP.
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


async def _seed_bcover_scenario(db, suffix: str) -> dict:
    """Setup : ACP avec 2 factures B'Cover.
    - Facture 1 : legacy import, account_number VIDE
    - Facture 2 : deja classifiee sur compte 6140
    """
    cid = f"iter90g8-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": "iter90g8 ACP"})
    await db.pcmn_accounts.insert_many([
        {"number": "6140", "name": "Assurance incendie",
         "copropriete_id": cid, "class_num": 6},
    ])
    await db.expense_categories.insert_one({
        "id": f"cat-{suffix}", "copropriete_id": cid,
        "name": "Assurance incendie", "account_number": "6140",
    })
    # Facture legacy sans account_number
    await db.invoices.insert_one({
        "id": f"inv-legacy-{suffix}", "copropriete_id": cid,
        "number": "FA-LEGACY", "supplier": "B'Cover",
        "date": "2026-01-15", "total_amount": 997.97,
        "account_number": "",  # <-- VIDE
        "status": "unpaid",
    })
    # Facture deja bien classifiee
    await db.invoices.insert_one({
        "id": f"inv-ok-{suffix}", "copropriete_id": cid,
        "number": "FA-OK", "supplier": "B'Cover",
        "date": "2026-03-15", "total_amount": 149.81,
        "account_number": "6140",
        "expense_category_id": f"cat-{suffix}",
        "status": "unpaid",
    })
    return {"cid": cid, "cat_id": f"cat-{suffix}",
            "legacy_id": f"inv-legacy-{suffix}",
            "ok_id": f"inv-ok-{suffix}"}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_iter90g8_diagnostic_finds_uncategorized_and_suggests():
    """Le diagnostic liste les factures sans account_number ET propose
    une suggestion basee sur l'historique du supplier."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_bcover_scenario(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.get(
                    f"{BACKEND_URL}/api/invoices/uncategorized-diagnostic?"
                    f"copropriete_id={ctx['cid']}",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["total_uncategorized"] == 1, (
                    f"1 facture legacy attendue, recu {data['total_uncategorized']}"
                )
                # Le supplier B'Cover doit apparaitre avec une suggestion
                by_sup = data["by_supplier"]
                assert len(by_sup) >= 1
                bcover = by_sup[0]
                assert bcover["supplier"] == "B'Cover"
                assert bcover["invoice_count"] == 1
                assert abs(bcover["total_amount"] - 997.97) < 0.01
                assert bcover["suggestion"] is not None, (
                    f"Une suggestion doit etre proposee (historique existe)"
                )
                sug = bcover["suggestion"]
                assert sug["account_number"] == "6140", (
                    f"Suggestion account_number attendue '6140', recu {sug}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g8_bulk_assign_updates_and_returns_count():
    """Le bulk-assign applique bien le remap et retourne le count updated."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_bcover_scenario(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/invoices/bulk-assign-account",
                    json={
                        "invoice_ids": [ctx["legacy_id"]],
                        "account_number": "6140",
                        "expense_category_id": ctx["cat_id"],
                        "copropriete_id": ctx["cid"],
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["updated"] == 1, (
                    f"1 facture doit etre mise a jour, recu {data['updated']}"
                )
                # Verifie en base
                updated = await db.invoices.find_one({"id": ctx["legacy_id"]}, {"_id": 0})
                assert updated["account_number"] == "6140"
                assert updated["expense_category_id"] == ctx["cat_id"]
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g8_bulk_assign_refuses_other_acp():
    """Chinese wall : bulk-assign refuse une facture d'une autre ACP."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        # ACP 1
        ctx1 = await _seed_bcover_scenario(db, suffix + "a")
        # ACP 2 : une autre ACP
        other_cid = f"iter90g8-other-{suffix}"
        other_inv_id = f"inv-other-{suffix}"
        await db.coproprietes.insert_one({"id": other_cid, "name": "Other ACP"})
        await db.invoices.insert_one({
            "id": other_inv_id, "copropriete_id": other_cid,
            "supplier": "AutreSupplier", "total_amount": 100.0,
            "date": "2026-01-01", "account_number": "",
        })
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                # On tente de modifier la facture other_inv_id en passant
                # copropriete_id de l'ACP1 (chinese wall attack)
                r = await c.post(
                    f"{BACKEND_URL}/api/invoices/bulk-assign-account",
                    json={
                        "invoice_ids": [other_inv_id],
                        "account_number": "6140",
                        "copropriete_id": ctx1["cid"],  # <-- ACP different
                    },
                )
                assert r.status_code == 403, (
                    f"Chinese wall doit refuser (403), recu {r.status_code}"
                )
        finally:
            await _cleanup(db, ctx1["cid"])
            await db.coproprietes.delete_one({"id": other_cid})
            await db.invoices.delete_many({"copropriete_id": other_cid})
    asyncio.run(_run())
