"""iter90ed : Apprentissage automatique de la repartition
occupant/proprietaire par nature de depense.

Quand une facture est enregistree avec un occupant_pct explicite pour une
nature, `default_occupant_pct` de la nature est mis a jour. La prochaine
facture avec cette nature aura ce split pre-rempli.
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
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_setup(db, cid_suffix: str) -> dict:
    """Cree une ACP + un compte PCMN classe 6 + une nature de depense
    avec default 0% occupant / 100% proprio."""
    cid = f"iter90ed-cid-{cid_suffix}"
    cat_id = f"iter90ed-cat-{cid_suffix}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "iter90ed ACP",
    })
    await db.pcmn_accounts.insert_one({
        "id": str(uuid.uuid4()), "number": "61300",
        "name": "Entretien - iter90ed",
        "copropriete_id": cid, "class_num": 6, "active": True,
    })
    await db.expense_categories.insert_one({
        "id": cat_id, "name": "Entretien iter90ed",
        "account_number": "61300",
        "copropriete_id": cid,
        "default_occupant_pct": 0.0,
        "default_proprietaire_pct": 100.0,
    })
    # Fiscal year ouvert couvrant 2026 (necessaire pour ensure_period_open)
    await db.fiscal_years.insert_one({
        "id": f"iter90ed-fy-{cid_suffix}",
        "copropriete_id": cid,
        "name": "2026",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "status": "open",
    })
    return {"cid": cid, "cat_id": cat_id}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})


def test_new_invoice_with_explicit_split_updates_category_default():
    """Facture avec occupant_pct=30 explicite -> default de la nature -> 30."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_setup(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]

                # Verifie default initial
                cat_before = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                assert cat_before["default_occupant_pct"] == 0.0

                # Cree une facture avec occupant_pct=30 explicite
                r = await client.post(
                    f"{BACKEND_URL}/api/invoices",
                    json={
                        "supplier": "Test iter90ed",
                        "date": "2026-03-01",
                        "total_amount": 100.0,
                        "vat_amount": 0.0,
                        "expense_category_id": ctx["cat_id"],
                        "account_number": "61300",
                        "copropriete_id": ctx["cid"],
                        "occupant_pct": 30.0,
                        "status": "unpaid",
                        "description": "iter90ed test learning",
                        "number": f"INV-A-{uuid.uuid4().hex[:6]}",
                    },
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                inv = r.json()
                assert inv["occupant_pct"] == 30.0
                assert inv["proprietaire_pct"] == 70.0

                # Verifie que le default a ete MAJ
                cat_after = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                assert cat_after["default_occupant_pct"] == 30.0
                assert cat_after["default_proprietaire_pct"] == 70.0
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_new_invoice_without_explicit_split_does_not_update_default():
    """Facture SANS occupant_pct (heritage) -> default reste inchange."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_setup(db, uuid.uuid4().hex[:6])
        # Force default a une valeur non-nulle
        await db.expense_categories.update_one(
            {"id": ctx["cat_id"]},
            {"$set": {"default_occupant_pct": 50.0, "default_proprietaire_pct": 50.0}},
        )
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                r = await client.post(
                    f"{BACKEND_URL}/api/invoices",
                    json={
                        "supplier": "Test iter90ed",
                        "date": "2026-03-02",
                        "total_amount": 100.0,
                        "vat_amount": 0.0,
                        "expense_category_id": ctx["cat_id"],
                        "account_number": "61300",
                        "copropriete_id": ctx["cid"],
                        # PAS d'occupant_pct -> heritage du default
                        "status": "unpaid",
                        "description": "iter90ed heritage pur",
                        "number": f"INV-B-{uuid.uuid4().hex[:6]}",
                    },
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                # Le default doit rester a 50/50
                cat_after = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                assert cat_after["default_occupant_pct"] == 50.0
                assert cat_after["default_proprietaire_pct"] == 50.0
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_update_invoice_learns_new_split():
    """Modifier occupant_pct sur une facture existante -> MAJ du default."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_setup(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                # Cree
                inv_number = f"INV-C-{uuid.uuid4().hex[:6]}"
                r = await client.post(
                    f"{BACKEND_URL}/api/invoices",
                    json={
                        "supplier": "Test iter90ed",
                        "date": "2026-03-03",
                        "total_amount": 100.0,
                        "vat_amount": 0.0,
                        "expense_category_id": ctx["cat_id"],
                        "account_number": "61300",
                        "copropriete_id": ctx["cid"],
                        "occupant_pct": 20.0,
                        "status": "unpaid",
                        "description": "iter90ed create",
                        "number": inv_number,
                    },
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                inv_id = r.json()["id"]

                # Verifie default = 20
                cat_a = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                assert cat_a["default_occupant_pct"] == 20.0

                # Update avec 45%
                r2 = await client.put(
                    f"{BACKEND_URL}/api/invoices/{inv_id}",
                    json={
                        "supplier": "Test iter90ed",
                        "date": "2026-03-03",
                        "total_amount": 100.0,
                        "vat_amount": 0.0,
                        "expense_category_id": ctx["cat_id"],
                        "account_number": "61300",
                        "copropriete_id": ctx["cid"],
                        "occupant_pct": 45.0,
                        "status": "unpaid",
                        "description": "iter90ed updated",
                        "number": inv_number,
                    },
                    headers=hdr,
                )
                assert r2.status_code == 200, r2.text

                # Verifie default = 45
                cat_b = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                assert cat_b["default_occupant_pct"] == 45.0
                assert cat_b["default_proprietaire_pct"] == 55.0
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_multi_line_invoice_does_not_update_defaults():
    """Facture multi-lignes : split global -> apprentissage skip par nature."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_setup(db, uuid.uuid4().hex[:6])
        # Nature 2 pour verifier qu'aucune n'est modifiee
        cat2_id = f"iter90ed-cat2-{uuid.uuid4().hex[:6]}"
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "number": "61600",
            "name": "Autres frais",
            "copropriete_id": ctx["cid"], "class_num": 6, "active": True,
        })
        await db.expense_categories.insert_one({
            "id": cat2_id, "name": "Nature 2",
            "account_number": "61600",
            "copropriete_id": ctx["cid"],
            "default_occupant_pct": 0.0,
            "default_proprietaire_pct": 100.0,
        })
        # Distribution key requis pour multi-lignes
        dk_id = f"iter90ed-dk-{uuid.uuid4().hex[:6]}"
        # Cree un lot puis une cle
        lot_id = f"iter90ed-lot-{uuid.uuid4().hex[:6]}"
        await db.lots.insert_one({
            "id": lot_id, "number": "L1",
            "copropriete_id": ctx["cid"],
        })
        await db.distribution_keys.insert_one({
            "id": dk_id, "name": "K1",
            "copropriete_id": ctx["cid"],
            "lots": [{"lot_id": lot_id, "lot_number": "L1", "share": 100.0}],
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                r = await client.post(
                    f"{BACKEND_URL}/api/invoices",
                    json={
                        "supplier": "Test iter90ed multi",
                        "date": "2026-03-04",
                        "total_amount": 200.0,
                        "vat_amount": 0.0,
                        "copropriete_id": ctx["cid"],
                        "occupant_pct": 60.0,
                        "status": "unpaid",
                        "description": "multi-line",
                        "number": f"INV-M-{uuid.uuid4().hex[:6]}",
                        "lines": [
                            {"expense_category_id": ctx["cat_id"],
                             "account_number": "61300",
                             "amount": 100.0, "description": "L1",
                             "distribution_key_id": dk_id},
                            {"expense_category_id": cat2_id,
                             "account_number": "61600",
                             "amount": 100.0, "description": "L2",
                             "distribution_key_id": dk_id},
                        ],
                    },
                    headers=hdr,
                )
                assert r.status_code == 200, r.text

                # Aucune des 2 natures ne doit avoir ete modifiee
                cat_a = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                cat_b = await db.expense_categories.find_one({"id": cat2_id})
                assert cat_a["default_occupant_pct"] == 0.0
                assert cat_b["default_occupant_pct"] == 0.0
        finally:
            await db.expense_categories.delete_one({"id": cat2_id})
            await db.pcmn_accounts.delete_many({"number": "61600", "copropriete_id": ctx["cid"]})
            await db.distribution_keys.delete_one({"id": dk_id})
            await db.lots.delete_one({"id": lot_id})
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_learning_is_scoped_per_category():
    """Apprentissage sur nature A n'affecte pas nature B."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_setup(db, uuid.uuid4().hex[:6])
        cat_b_id = f"iter90ed-catB-{uuid.uuid4().hex[:6]}"
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "number": "61701",
            "name": "Nature B",
            "copropriete_id": ctx["cid"], "class_num": 6, "active": True,
        })
        await db.expense_categories.insert_one({
            "id": cat_b_id, "name": "Nature B",
            "account_number": "61701",
            "copropriete_id": ctx["cid"],
            "default_occupant_pct": 15.0,
            "default_proprietaire_pct": 85.0,
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                # Facture sur A avec 80% occupant
                r = await client.post(
                    f"{BACKEND_URL}/api/invoices",
                    json={
                        "supplier": "T", "date": "2026-03-05",
                        "total_amount": 50.0, "vat_amount": 0.0,
                        "expense_category_id": ctx["cat_id"],
                        "account_number": "61300",
                        "copropriete_id": ctx["cid"],
                        "occupant_pct": 80.0, "status": "unpaid",
                        "description": "scope test",
                        "number": f"INV-S-{uuid.uuid4().hex[:6]}",
                    },
                    headers=hdr,
                )
                assert r.status_code == 200, r.text

                cat_a = await db.expense_categories.find_one({"id": ctx["cat_id"]})
                cat_b = await db.expense_categories.find_one({"id": cat_b_id})
                # A a change
                assert cat_a["default_occupant_pct"] == 80.0
                # B est inchange
                assert cat_b["default_occupant_pct"] == 15.0
        finally:
            await db.expense_categories.delete_one({"id": cat_b_id})
            await db.pcmn_accounts.delete_many({"number": "61701", "copropriete_id": ctx["cid"]})
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())
