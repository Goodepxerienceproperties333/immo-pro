"""iter90ax : Tests du systeme de backup ACP.

Verifie :
1. dump_acp_to_zip produit un ZIP valide avec les collections attendues
2. build_acp_archive_zip produit une archive structuree par annee fiscale
3. Les endpoints admin exigent superadmin (403 pour syndic/gestionnaire)
4. Le trigger manuel /api/admin/backups/trigger cree bien un index et un GridFS file
5. La restauration en dry-run n'ecrit rien en base
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _make_token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": f"u-{sub}@t.be", "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_test_acp():
    """Cree une ACP test avec 2 owners et quelques ecritures."""
    db = await _mongo()
    copro_id = f"iter90ax-{uuid.uuid4().hex[:8]}"
    owner_id = f"o-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "Backup Test ACP", "reference": "BAKUP-01",
        "status": "active",
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "Owner Test", "email": "owner@t.be",
        "copropriete_ids": [copro_id],
    })
    await db.lots.insert_one({
        "id": f"l-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "owner_id": owner_id, "number": "1A", "quotity": 100,
    })
    await db.journal_entries.insert_one({
        "id": f"je-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "date": "2025-06-15", "description": "Test entry", "reference": "TEST01",
        "journal_type": "ACHAT",
        "lines": [
            {"account_number": "6100", "debit": 100, "credit": 0},
            {"account_number": "4400", "debit": 0, "credit": 100},
        ],
    })
    await db.fiscal_years.insert_one({
        "id": f"fy-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
        "name": "2025", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "status": "closed",
    })
    return copro_id, owner_id


async def _teardown_acp(copro_id: str, owner_id: str):
    db = await _mongo()
    await db.coproprietes.delete_one({"id": copro_id})
    await db.owners.delete_one({"id": owner_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})
    await db.fiscal_years.delete_many({"copropriete_id": copro_id})
    await db.backups_index.delete_many({"copropriete_id": copro_id})


# ============ Test unit dump ============

async def _scenario_dump_acp_zip():
    from backup_service import dump_acp_to_zip
    copro_id, owner_id = await _setup_test_acp()
    try:
        db = await _mongo()
        data, manifest = await dump_acp_to_zip(db, copro_id)
        assert len(data) > 500, "ZIP trop petit"
        assert manifest["copropriete_id"] == copro_id
        assert manifest["owners_count"] == 1
        assert manifest["collections"]["lots"] == 1
        assert manifest["collections"]["journal_entries"] == 1
        # Verifie la structure interne
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            assert "manifest.json" in names
            assert "copropriete.json" in names
            assert "owners.json" in names
            assert "collections/lots.jsonl" in names
    finally:
        await _teardown_acp(copro_id, owner_id)


def test_dump_acp_zip():
    asyncio.run(_scenario_dump_acp_zip())


# ============ Test unit archive syndic ============

async def _scenario_build_archive_zip():
    from backup_service import build_acp_archive_zip
    copro_id, owner_id = await _setup_test_acp()
    try:
        db = await _mongo()
        data, filename = await build_acp_archive_zip(db, copro_id, include_pdfs=False)
        assert filename.startswith("archive_")
        assert filename.endswith(".zip")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            # Contient metadata + CSVs par annee
            assert any("metadata.json" in n for n in names)
            assert any("owners.csv" in n for n in names)
            assert any("2025/journal_entries.csv" in n for n in names)
    finally:
        await _teardown_acp(copro_id, owner_id)


def test_build_archive_zip():
    asyncio.run(_scenario_build_archive_zip())


# ============ Test endpoints admin ============

async def _get_superadmin_id():
    db = await _mongo()
    admin = await db.users.find_one({"role": {"$in": ["superadmin", "admin"]}})
    assert admin, "Aucun superadmin dans la DB"
    return str(admin["_id"])


async def _scenario_endpoints_require_superadmin():
    """Les endpoints /api/admin/backups exigent superadmin."""
    db = await _mongo()
    # Cree un syndic bidon
    syndic = ObjectId()
    await db.users.insert_one({
        "_id": syndic, "email": f"s-{uuid.uuid4().hex[:6]}@t.be",
        "role": "syndic", "password_hash": "$2b$12$x",
    })
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Syndic -> 403
            r = await c.get("/api/admin/backups",
                            headers={"Authorization": f"Bearer {_make_token(str(syndic))}"})
            assert r.status_code == 403, r.text

            # Superadmin -> 200
            admin_id = await _get_superadmin_id()
            r = await c.get("/api/admin/backups",
                            headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200, r.text
    finally:
        await db.users.delete_one({"_id": syndic})


def test_endpoints_require_superadmin():
    asyncio.run(_scenario_endpoints_require_superadmin())


async def _scenario_manual_trigger_creates_backup():
    """Le trigger manuel pour une ACP cree bien un index + GridFS."""
    copro_id, owner_id = await _setup_test_acp()
    try:
        admin_id = await _get_superadmin_id()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
            r = await c.post(f"/api/admin/backups/trigger/{copro_id}",
                             headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["success"] is True
            backup_id = body["backup"]["backup_id"]

            # Verifie que l'index existe
            r = await c.get(f"/api/admin/backups?copropriete_id={copro_id}",
                            headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200
            backups = r.json()["backups"]
            assert any(b["backup_id"] == backup_id for b in backups), "Backup absent de l'index"

            # DL
            r = await c.get(f"/api/admin/backups/{backup_id}/download",
                            headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/zip")

            # Delete
            r = await c.delete(f"/api/admin/backups/{backup_id}",
                               headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200
    finally:
        await _teardown_acp(copro_id, owner_id)


def test_manual_trigger_creates_backup():
    asyncio.run(_scenario_manual_trigger_creates_backup())


async def _scenario_restore_dry_run():
    """La restauration en dry-run n'ecrit rien en base."""
    copro_id, owner_id = await _setup_test_acp()
    try:
        admin_id = await _get_superadmin_id()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30) as c:
            # Cree un backup
            r = await c.post(f"/api/admin/backups/trigger/{copro_id}",
                             headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
            assert r.status_code == 200
            backup_id = r.json()["backup"]["backup_id"]

            # Delete l'owner en base pour verifier que dry_run ne le restaure PAS
            db = await _mongo()
            await db.owners.delete_one({"id": owner_id})
            assert await db.owners.find_one({"id": owner_id}) is None

            # Restore dry-run
            r = await c.post(
                f"/api/admin/backups/restore/{backup_id}",
                headers={"Authorization": f"Bearer {_make_token(admin_id)}"},
                json={"dry_run": True},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["dry_run"] is True
            assert body["collections"]["owners"] == 1

            # Verifie que dry_run n'a PAS restaure
            assert await db.owners.find_one({"id": owner_id}) is None

            # Cleanup
            await c.delete(f"/api/admin/backups/{backup_id}",
                           headers={"Authorization": f"Bearer {_make_token(admin_id)}"})
    finally:
        await _teardown_acp(copro_id, owner_id)


def test_restore_dry_run():
    asyncio.run(_scenario_restore_dry_run())
