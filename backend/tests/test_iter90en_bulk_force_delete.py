"""iter90en : Suppression bulk d'ecritures cote admin.

L'utilisateur PROD demande la possibilite de nettoyer les doublons/erreurs
via multi-select + bouton Supprimer + Supprimer TOUT. Distinct du bouton
"extourne" (contre-passation traceable) : SUPPRESSION DEFINITIVE avec copie
dans `deleted_entries` pour audit trail.
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
    """Login admin@copro.be qui a role=superadmin."""
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return {}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_bulk_force_delete_removes_entries_and_archives():
    """POST bulk-force-delete supprime N ecritures et les archive dans deleted_entries."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90en-cid-{suffix}"
        entry_ids = [f"iter90en-je-{suffix}-{i}" for i in range(3)]
        for i, eid in enumerate(entry_ids):
            await db.journal_entries.insert_one({
                "id": eid, "copropriete_id": cid,
                "journal_type": "OD", "date": "2026-01-01",
                "reference": f"TEST-{i}",
                "description": f"Test entry {i}",
                "lines": [
                    {"account_number": "700000", "debit": 100, "credit": 0},
                    {"account_number": "610000", "debit": 0, "credit": 100},
                ],
                "total_debit": 100.0, "total_credit": 100.0,
            })
        try:
            async with httpx.AsyncClient() as c:
                await _login_superadmin(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                    json={
                        "entry_ids": entry_ids,
                        "reason": "Test bulk delete iter90en - doublons legacy",
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["deleted_count"] == 3

                # Ecritures supprimees
                remaining = await db.journal_entries.count_documents(
                    {"id": {"$in": entry_ids}}
                )
                assert remaining == 0

                # Archives dans deleted_entries
                archived = await db.deleted_entries.count_documents(
                    {"id": {"$in": entry_ids}}
                )
                assert archived == 3

                # Verifie le champ bulk_delete + delete_reason
                sample = await db.deleted_entries.find_one({"id": entry_ids[0]})
                assert sample.get("bulk_delete") is True
                assert "iter90en" in sample.get("delete_reason", "")
        finally:
            await db.journal_entries.delete_many({"id": {"$in": entry_ids}})
            await db.deleted_entries.delete_many({"id": {"$in": entry_ids}})

    asyncio.run(_run())


def test_bulk_force_delete_requires_justification():
    """Sans justification (<10 char) -> 400."""
    async def _run():
        async with httpx.AsyncClient() as c:
            await _login_superadmin(c)
            r = await c.post(
                f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                json={"entry_ids": ["any"], "reason": "short"},
            )
            assert r.status_code == 400
            assert "justification" in r.json().get("detail", "").lower()

    asyncio.run(_run())


def test_bulk_force_delete_requires_ids():
    """Sans entry_ids -> 400."""
    async def _run():
        async with httpx.AsyncClient() as c:
            await _login_superadmin(c)
            r = await c.post(
                f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                json={"entry_ids": [], "reason": "Motif valide 10 chars"},
            )
            assert r.status_code == 400

    asyncio.run(_run())


def test_bulk_force_delete_returns_404_if_none_found():
    """Si aucun id ne matche -> 404 (aucune ecriture)."""
    async def _run():
        async with httpx.AsyncClient() as c:
            await _login_superadmin(c)
            r = await c.post(
                f"{BACKEND_URL}/api/admin/journal-entries/bulk-force-delete",
                json={
                    "entry_ids": ["ghost-id-1", "ghost-id-2"],
                    "reason": "Test bulk delete no match",
                },
            )
            assert r.status_code == 404

    asyncio.run(_run())
