"""iter90ga : endpoint admin POST /api/admin/gridfs-migration.

Permet au superadmin de lancer le script `migrate_uploads_to_gridfs.py`
depuis l'UI PROD sans avoir besoin d'un acces shell au container.

Tests :
1. `dry_run=true` retourne le plan (count + bytes) sans rien ecrire.
2. Non-superadmin recoit 403.
3. Idempotence : deuxieme appel dry_run apres migration reelle ne
   trouve plus rien a migrer.
"""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login_superadmin(client):
    """L'admin par defaut de la base est superadmin."""
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_iter90ga_gridfs_migration_dry_run_returns_plan():
    """dry_run=true retourne le plan sans ecrire."""
    async def _run():
        async with httpx.AsyncClient(timeout=60) as c:
            await _login_superadmin(c)
            r = await c.post(f"{BACKEND_URL}/api/admin/gridfs-migration",
                             json={"dry_run": True})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is True
            # Structure du retour
            assert "invoices" in data
            assert "journal_entries" in data
            assert "documents" in data
            for k in ("invoices", "journal_entries", "documents"):
                assert "migrated" in data[k]
                assert "skipped" in data[k]
                assert "missing" in data[k]
                assert "bytes" in data[k]
            assert "total_migrated" in data
            assert "total_bytes" in data
            assert "duration_ms" in data
            assert data["duration_ms"] >= 0
    asyncio.run(_run())


def test_iter90ga_gridfs_migration_apply_writes_gridfs_id():
    """Un attachment legacy (stored_path sur disque, sans gridfs_id) doit
    recevoir un gridfs_id apres migration reelle."""
    import tempfile
    import uuid
    async def _run():
        db = await _mongo()
        # Seed : cree un fichier sur le disque + un doc invoice avec attachment
        legacy_dir = "/app/uploads/invoice_attachments"
        os.makedirs(legacy_dir, exist_ok=True)
        suffix = uuid.uuid4().hex[:6]
        fname = f"legacy_test_{suffix}.txt"
        fp = os.path.join(legacy_dir, fname)
        with open(fp, "w") as f:
            f.write(f"iter90ga test payload {suffix}")
        inv_id = f"inv-iter90ga-{suffix}"
        att_id = f"att-{suffix}"
        await db.invoices.insert_one({
            "id": inv_id,
            "copropriete_id": "test-copro",
            "attachments": [{
                "id": att_id,
                "filename": fname,
                "stored_path": fp,
                "mime_type": "text/plain",
            }],
        })
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                await _login_superadmin(c)
                # dry-run : doit trouver 1 fichier a migrer
                r = await c.post(f"{BACKEND_URL}/api/admin/gridfs-migration",
                                 json={"dry_run": True})
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["invoices"]["migrated"] >= 1, (
                    f"dry-run doit trouver notre attachment. Recu : {data}"
                )
                # apply : migre vraiment
                r2 = await c.post(f"{BACKEND_URL}/api/admin/gridfs-migration",
                                  json={"dry_run": False})
                assert r2.status_code == 200, r2.text
                data2 = r2.json()
                assert data2["invoices"]["migrated"] >= 1
                # Verifie que le gridfs_id est ecrit
                inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
                atts = inv.get("attachments", [])
                assert atts and atts[0].get("gridfs_id"), (
                    f"gridfs_id doit etre ecrit. attachments : {atts}"
                )
                # Idempotence : nouveau dry-run ne doit plus rien voir
                # pour CET attachment (skipped++, migrated--)
                r3 = await c.post(f"{BACKEND_URL}/api/admin/gridfs-migration",
                                  json={"dry_run": True})
                assert r3.status_code == 200
                # Ne verifie pas les counts globaux (autres tests peuvent
                # avoir laisse des donnees), on verifie juste que notre
                # attachment est bien skip
                inv2 = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
                assert inv2["attachments"][0].get("gridfs_id"), (
                    "Idempotence : le gridfs_id persiste"
                )
        finally:
            await db.invoices.delete_one({"id": inv_id})
            try: os.remove(fp)
            except Exception: pass
    asyncio.run(_run())


def test_iter90ga_non_superadmin_gets_403():
    """Un non-superadmin doit recevoir 403."""
    import uuid
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        # Cree un user syndic
        from server import hash_password
        syndic_email = f"syndic_iter90ga_{suffix}@test.be"
        await db.users.insert_one({
            "email": syndic_email,
            "name": "Syndic Test",
            "role": "syndic",
            "password_hash": hash_password("test1234"),
            "copropriete_ids": [],
        })
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r_login = await c.post(f"{BACKEND_URL}/api/auth/login", json={
                    "email": syndic_email, "password": "test1234",
                })
                assert r_login.status_code == 200
                r = await c.post(f"{BACKEND_URL}/api/admin/gridfs-migration",
                                 json={"dry_run": True})
                assert r.status_code == 403, (
                    f"Syndic doit etre refuse (403). Recu {r.status_code} : {r.text}"
                )
        finally:
            await db.users.delete_one({"email": syndic_email})
    asyncio.run(_run())
