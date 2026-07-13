"""iter90ep : Interdiction de supprimer contre-passations et extournes.

Meme via bulk-force-delete superadmin, aucune ecriture avec is_reversal=True
ou reversed=True ne doit pouvoir etre supprimee (audit trail legal PCMN).
Le backend doit renvoyer 400 en identifiant les references bloquees.
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


async def _login_superadmin(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _mk_entry(cid, eid, ref, **extra):
    doc = {
        "id": eid,
        "copropriete_id": cid,
        "journal_type": "OD",
        "date": "2026-01-01",
        "reference": ref,
        "description": f"Test {ref}",
        "lines": [
            {"account_number": "700000", "debit": 100, "credit": 0},
            {"account_number": "610000", "debit": 0, "credit": 100},
        ],
        "total_debit": 100.0,
        "total_credit": 100.0,
    }
    doc.update(extra)
    return doc


def test_bulk_delete_blocks_reversal_entry():
    """Une ecriture is_reversal=True ne peut jamais etre bulk-deletee."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90ep-cid-{suffix}"
        eid_ok = f"iter90ep-ok-{suffix}"
        eid_rev = f"iter90ep-rev-{suffix}"
        await db.journal_entries.insert_one(_mk_entry(cid, eid_ok, "OK-1"))
        await db.journal_entries.insert_one(
            _mk_entry(cid, eid_rev, "EXT-1", is_reversal=True)
        )
        try:
            async with httpx.AsyncClient() as c:
                await _login_superadmin(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                    json={
                        "entry_ids": [eid_ok, eid_rev],
                        "reason": "Tentative purge y compris contre-passation",
                    },
                )
                assert r.status_code == 400, r.text
                detail = r.json().get("detail", "").lower()
                assert "contre-passation" in detail or "extourne" in detail
                # Rien n'a du etre supprime (atomique)
                remaining = await db.journal_entries.count_documents(
                    {"id": {"$in": [eid_ok, eid_rev]}}
                )
                assert remaining == 2
        finally:
            await db.journal_entries.delete_many({"id": {"$in": [eid_ok, eid_rev]}})
            await db.deleted_entries.delete_many({"id": {"$in": [eid_ok, eid_rev]}})

    asyncio.run(_run())


def test_bulk_delete_blocks_reversed_entry():
    """Une ecriture reversed=True (source extournee) ne peut pas etre supprimee."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90ep-cid-{suffix}"
        eid = f"iter90ep-src-{suffix}"
        await db.journal_entries.insert_one(
            _mk_entry(cid, eid, "SRC-1", reversed=True)
        )
        try:
            async with httpx.AsyncClient() as c:
                await _login_superadmin(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                    json={
                        "entry_ids": [eid],
                        "reason": "Tentative purge ecriture extournee",
                    },
                )
                assert r.status_code == 400, r.text
                remaining = await db.journal_entries.count_documents({"id": eid})
                assert remaining == 1
        finally:
            await db.journal_entries.delete_many({"id": eid})
            await db.deleted_entries.delete_many({"id": eid})

    asyncio.run(_run())


def test_bulk_delete_allows_normal_entries():
    """Regression : entrees normales toujours supprimables."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90ep-cid-{suffix}"
        eids = [f"iter90ep-norm-{suffix}-{i}" for i in range(2)]
        for eid in eids:
            await db.journal_entries.insert_one(_mk_entry(cid, eid, eid))
        try:
            async with httpx.AsyncClient() as c:
                await _login_superadmin(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                    json={
                        "entry_ids": eids,
                        "reason": "Test suppression entrees normales OK",
                    },
                )
                assert r.status_code == 200, r.text
                assert r.json()["deleted_count"] == 2
        finally:
            await db.journal_entries.delete_many({"id": {"$in": eids}})
            await db.deleted_entries.delete_many({"id": {"$in": eids}})

    asyncio.run(_run())
