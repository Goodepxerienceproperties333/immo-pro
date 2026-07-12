"""iter90dz - Portail proprietaire : fallback lot_number pour distribution_lines phantoms.

User bug (Feb 2026, PROD ACP Acacia):
> "cote proprietaire on ne voit toujours pas les charges pour l'ACP acacia
> en production"

Cause : les invoices sur ACP Acacia ont des `distribution_lines` avec des
lot_ids QUI N'EXISTENT PLUS (lots re-imported post-Optipro avec nouveaux
UUID). Le check `dl.lot_id in my_lot_ids` echoue -> aucune charge visible.

Fix : match par lot_number normalise en fallback (comme iter90du pour cles).
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

BACKEND_URL = "http://localhost:8001"


async def _login_owner(client, email: str, password: str = "owner123"):
    """Login en tant que proprietaire."""
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": email, "password": password,
    })
    resp.raise_for_status()
    return dict(resp.cookies)


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_acp_with_phantom_invoice(tag: str):
    """Configure une ACP Acacia-like avec :
    - Owner TEUWEN (User account)
    - 1 lot 001 (NEW lot_id, quotity 0)
    - 1 invoice avec distribution_lines pointant vers ancien phantom lot_id
      MAIS lot_number = "001" (matchable)
    """
    from bson import ObjectId
    db = await _mongo()
    cid = f"iter90dz-{tag}-{uuid.uuid4()}"
    email = f"teuwen-{uuid.uuid4().hex[:6]}@test.be"

    # 1) Owner + user
    owner_id = f"o-{uuid.uuid4().hex[:6]}"
    from passlib.hash import bcrypt as _bc
    user_pw_hash = _bc.hash("owner123")
    user_oid = ObjectId()
    await db.users.insert_one({
        "_id": user_oid,
        "email": email, "password_hash": user_pw_hash,
        "role": "owner", "owner_id": owner_id,
        "name": "Gael TEUWEN",
        "first_name": "Gael", "last_name": "TEUWEN",
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "TEUWEN Gael", "auxiliary_code": "T001",
        "email": email,
        "copropriete_ids": [cid],
    })
    await db.coproprietes.insert_one({"id": cid, "name": "Acacia T", "status": "active"})

    # 2) NEW lot "001" (nouveau lot_id apres re-import)
    new_lot_id = f"lot-{uuid.uuid4().hex[:6]}"
    await db.lots.insert_one({
        "id": new_lot_id, "number": "001", "copropriete_id": cid,
        "owner_id": owner_id, "owner_ids": [owner_id],
        "quotity": 100.0,
    })

    # 3) Invoice avec distribution_lines pointant vers PHANTOM lot_id
    phantom_lot_id = f"phantom-{uuid.uuid4()}"
    invoice_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        "id": invoice_id, "number": "FA-2026-0001",
        "copropriete_id": cid, "date": "2026-01-15",
        "supplier": "Normec BTV",
        "description": "Controle periodique ascenseur",
        "total_amount": 154.60, "status": "paye",
        "category": "0010 Ascenseurs",
        "distribution_lines": [
            {"lot_id": phantom_lot_id, "lot_number": "001",
             "owner_name": "TEUWEN", "share": 100.0, "amount": 154.60},
        ],
    })

    return {"db": db, "cid": cid, "email": email, "owner_id": owner_id,
            "user_oid": user_oid, "new_lot_id": new_lot_id,
            "invoice_id": invoice_id, "phantom_lot_id": phantom_lot_id}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    await db.users.delete_one({"_id": ctx["user_oid"]})
    await db.owners.delete_one({"id": ctx["owner_id"]})
    for coll in ("lots", "invoices"):
        await db[coll].delete_many({"copropriete_id": cid})


async def _test_owner_sees_charges_via_lot_number_fallback():
    """Owner TEUWEN doit voir la facture 154.60E meme si distribution_lines
    pointe vers un lot_id phantom (matching par lot_number 001)."""
    ctx = await _setup_acp_with_phantom_invoice("owner-charges")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login_owner(client, ctx["email"])
            r = await client.get(
                f"{BACKEND_URL}/api/owner/invoices",
                cookies=cookies,
                params={"copropriete_id": ctx["cid"]},
            )
            assert r.status_code == 200, r.text
            charges = r.json()
            assert len(charges) == 1, (
                f"iter90dz : le proprietaire doit voir 1 facture (via fallback "
                f"lot_number). Obtenu {len(charges)} charges."
            )
            c = charges[0]
            assert c["id"] == ctx["invoice_id"]
            assert c["my_amount"] == 154.60
    finally:
        await _cleanup(ctx)


async def _test_download_attachment_via_lot_number_fallback():
    """Le download d'attachment doit aussi autoriser via lot_number match."""
    ctx = await _setup_acp_with_phantom_invoice("owner-dl")
    try:
        # Ajoute un attachment fictif a la facture
        att_id = str(uuid.uuid4())
        await ctx["db"].invoices.update_one(
            {"id": ctx["invoice_id"]},
            {"$set": {"attachments": [{
                "id": att_id, "filename": "test.pdf",
                "mime_type": "application/pdf",
                "stored_path": "/tmp/nonexistent.pdf",  # sera erreur 404 sur download reel
            }]}},
        )
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login_owner(client, ctx["email"])
            r = await client.get(
                f"{BACKEND_URL}/api/owner/invoices/{ctx['invoice_id']}"
                f"/attachments/{att_id}/download",
                cookies=cookies,
            )
            # Le fallback lot_number doit permettre d'atteindre l'etape download
            # (le fichier lui-meme n'existe pas -> 404 "Fichier introuvable")
            # MAIS PAS 403 "Vous n'avez pas de part"
            assert r.status_code in (200, 404), (
                f"iter90dz : le fallback lot_number doit autoriser l'acces. "
                f"Obtenu {r.status_code}. Body: {r.text[:200]}"
            )
            assert "part" not in r.text.lower() or r.status_code != 403, r.text
    finally:
        await _cleanup(ctx)


async def _test_owner_still_sees_when_lot_id_matches_directly():
    """Non-regression : si distribution_lines a le bon lot_id, ca marche."""
    ctx = await _setup_acp_with_phantom_invoice("owner-direct")
    try:
        # Fix la distribution_lines pour utiliser le VRAI lot_id
        await ctx["db"].invoices.update_one(
            {"id": ctx["invoice_id"]},
            {"$set": {"distribution_lines": [{
                "lot_id": ctx["new_lot_id"], "lot_number": "001",
                "owner_name": "TEUWEN", "share": 100.0, "amount": 154.60,
            }]}},
        )
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login_owner(client, ctx["email"])
            r = await client.get(
                f"{BACKEND_URL}/api/owner/invoices",
                cookies=cookies,
                params={"copropriete_id": ctx["cid"]},
            )
            assert r.status_code == 200
            assert len(r.json()) == 1
    finally:
        await _cleanup(ctx)


async def _test_owner_still_denied_when_no_match():
    """Non-regression : owner DIFFERENT ne voit pas la facture."""
    ctx = await _setup_acp_with_phantom_invoice("owner-deny")
    try:
        # Cree un autre owner + user sur MEME ACP mais lot different
        from bson import ObjectId
        from passlib.hash import bcrypt as _bc
        other_email = f"other-{uuid.uuid4().hex[:6]}@test.be"
        other_owner_id = f"o-{uuid.uuid4().hex[:6]}"
        other_user_oid = ObjectId()
        await ctx["db"].users.insert_one({
            "_id": other_user_oid, "email": other_email,
            "password_hash": _bc.hash("owner123"),
            "role": "owner", "owner_id": other_owner_id,
            "name": "Other User",
        })
        await ctx["db"].owners.insert_one({
            "id": other_owner_id, "name": "Other", "email": other_email,
            "copropriete_ids": [ctx["cid"]],
        })
        # Lot 002 pour Other
        await ctx["db"].lots.insert_one({
            "id": f"lot-{uuid.uuid4().hex[:6]}", "number": "002",
            "copropriete_id": ctx["cid"],
            "owner_id": other_owner_id, "owner_ids": [other_owner_id],
        })
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                cookies = await _login_owner(client, other_email)
                r = await client.get(
                    f"{BACKEND_URL}/api/owner/invoices",
                    cookies=cookies,
                    params={"copropriete_id": ctx["cid"]},
                )
                assert r.status_code == 200
                charges = r.json()
                assert len(charges) == 0, (
                    "Other owner (lot 002) ne doit PAS voir la facture qui "
                    "concerne le lot 001. Obtenu %s" % charges
                )
        finally:
            await ctx["db"].users.delete_one({"_id": other_user_oid})
            await ctx["db"].owners.delete_one({"id": other_owner_id})
    finally:
        await _cleanup(ctx)


def test_owner_sees_charges_via_lot_number_fallback():
    asyncio.run(_test_owner_sees_charges_via_lot_number_fallback())


def test_download_attachment_via_lot_number_fallback():
    asyncio.run(_test_download_attachment_via_lot_number_fallback())


def test_owner_still_sees_when_lot_id_matches_directly():
    asyncio.run(_test_owner_still_sees_when_lot_id_matches_directly())


def test_owner_still_denied_when_no_match():
    asyncio.run(_test_owner_still_denied_when_no_match())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
