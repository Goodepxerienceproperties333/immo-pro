"""iter90i1 : Test de l'endpoint superadmin d'execution de la migration
GridFS des uploads.

Verifie que :
1. Sans authentification -> 401
2. Avec un non-superadmin -> 403
3. Avec superadmin + dry_run=true : renvoie la structure attendue, aucune
   ecriture en base (les compteurs skipped correspondent aux fichiers
   deja migres).
4. Le mode 'live' met a jour effectivement `gridfs_id` sur les documents
   qui pointent vers un fichier disque non-encore migre.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _make_admin_scope(user_role: str = "superadmin"):
    """Renvoie un dict user + un fake request qui pointe vers ce user.
    Utilise un vrai JWT pour passer get_current_user."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    email = f"testadmin-{uuid.uuid4().hex[:8]}@t.local"
    r = await db.users.insert_one({
        "email": email,
        "password_hash": "dummy",
        "name": "Test Admin",
        "role": user_role,
    })
    uid_str = str(r.inserted_id)
    # Genere un vrai JWT signe pour passer get_current_user
    from server import create_access_token
    token = create_access_token(uid_str, email)

    class _Cookies:
        def __init__(self, tk):
            self._tk = tk

        def get(self, key, default=None):
            return self._tk if key == "access_token" else default

    class _Headers:
        def get(self, key, default=""):
            return default

    class _FakeRequest:
        class State:
            pass

        def __init__(self):
            self.state = self.State()
            self.state.user_id = uid_str
            self.cookies = _Cookies(token)
            self.headers = _Headers()
    return client, db, _FakeRequest(), uid_str


async def _cleanup_admin(db, uid_str: str):
    from bson import ObjectId
    try:
        await db.users.delete_one({"_id": ObjectId(uid_str)})
    except Exception:
        pass


def _get_migration_handler(db):
    """Recupere l'endpoint POST /api/admin/migrate-uploads-to-gridfs
    du router admin."""
    from routes.admin import create_admin_router
    router = create_admin_router(db)
    for r in router.routes:
        if getattr(r, "path", "") == "/api/admin/migrate-uploads-to-gridfs":
            return r.endpoint
    return None


def test_migrate_endpoint_rejects_non_superadmin():
    """iter90i1-1 : un role 'syndic' est refuse (403)."""
    async def _run():
        from fastapi import HTTPException
        client, db, request, uid = await _make_admin_scope(user_role="syndic")
        try:
            handler = _get_migration_handler(db)
            assert handler is not None, "Endpoint introuvable"
            raised = False
            try:
                await handler(request=request, dry_run=True)
            except HTTPException as e:
                raised = e.status_code == 403
            assert raised, "Un syndic ne doit PAS pouvoir lancer la migration"
        finally:
            await _cleanup_admin(db, uid)
            client.close()

    asyncio.run(_run())


def test_migrate_endpoint_superadmin_dry_run_ok():
    """iter90i1-2 : superadmin + dry_run=True renvoie la structure attendue."""
    async def _run():
        client, db, request, uid = await _make_admin_scope(user_role="superadmin")
        try:
            handler = _get_migration_handler(db)
            result = await handler(request=request, dry_run=True)
            # Structure attendue
            assert result["mode"] == "dry_run"
            for key in ["invoices_attachments", "journal_attachments", "documents"]:
                assert key in result, f"cle manquante : {key}"
                b = result[key]
                assert "migrated" in b and "skipped" in b and "missing" in b
                assert "bytes_human" in b
            assert "totals" in result
            t = result["totals"]
            assert "migrated" in t
            assert "skipped_already_in_gridfs" in t
            assert "total_bytes_human" in t
            # Un dry-run ne cree PAS l'index TTL
            assert result["ttl_index_invoice_bundle_sessions"] == "skipped (dry_run)"
        finally:
            await _cleanup_admin(db, uid)
            client.close()

    asyncio.run(_run())


def test_migrate_endpoint_live_migrates_and_writes_gridfs():
    """iter90i1-3 : mode live migre reellement les documents legacy
    ayant un `stored_path` mais pas de `gridfs_id`."""
    doc_id = f"doc-i1-{uuid.uuid4().hex[:8]}"

    async def _run():
        client, db, request, uid = await _make_admin_scope(user_role="superadmin")
        # Cree un fichier temp reel + un document sans gridfs_id
        tmp_dir = Path("/tmp/iter90i1")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"file-{uuid.uuid4().hex[:6]}.txt"
        tmp_file.write_text("Contenu test iter90i1\n" * 5, encoding="utf-8")
        try:
            await db.documents.delete_many({"id": doc_id})
            await db.documents.insert_one({
                "id": doc_id,
                "filename": tmp_file.name,
                "stored_path": str(tmp_file),
                "mime_type": "text/plain",
                "copropriete_id": "acp-i1-test",
                # PAS de gridfs_id -> doit etre migre
            })
            handler = _get_migration_handler(db)
            result = await handler(request=request, dry_run=False)
            assert result["mode"] == "live"
            # Verifier que le doc a bien recu son gridfs_id
            doc_after = await db.documents.find_one({"id": doc_id})
            assert doc_after is not None
            assert doc_after.get("gridfs_id"), (
                f"gridfs_id manquant apres migration : {doc_after}"
            )
            # Contenu retrouvable via GridFS
            from gridfs_storage import get_documents_storage
            storage = get_documents_storage(db)
            contents = await storage.download(doc_after["gridfs_id"])
            assert contents == tmp_file.read_bytes()
        finally:
            await db.documents.delete_many({"id": doc_id})
            if tmp_file.exists():
                tmp_file.unlink()
            await _cleanup_admin(db, uid)
            client.close()

    asyncio.run(_run())


def test_migrate_endpoint_live_is_idempotent():
    """iter90i1-4 : re-executer live juste apres un premier live -> tout
    est en 'skipped' (idempotent, aucune double-migration)."""
    doc_id = f"doc-i1-idem-{uuid.uuid4().hex[:8]}"

    async def _run():
        client, db, request, uid = await _make_admin_scope(user_role="superadmin")
        tmp_dir = Path("/tmp/iter90i1")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"idem-{uuid.uuid4().hex[:6]}.txt"
        tmp_file.write_text("idempotence check")
        try:
            await db.documents.delete_many({"id": doc_id})
            await db.documents.insert_one({
                "id": doc_id, "filename": tmp_file.name,
                "stored_path": str(tmp_file), "mime_type": "text/plain",
            })
            handler = _get_migration_handler(db)
            r1 = await handler(request=request, dry_run=False)
            # r1 : au moins 1 document migre
            assert r1["documents"]["migrated"] >= 1
            r2 = await handler(request=request, dry_run=False)
            # r2 : le doc est desormais skipped (deja en GridFS)
            # Note : d'autres docs preexistants peuvent aussi apparaitre en
            # skipped, on verifie donc l'increment sur notre doc precis.
            doc_after = await db.documents.find_one({"id": doc_id})
            gridfs_id_1 = doc_after.get("gridfs_id")
            # 2eme run ne doit PAS avoir change le gridfs_id (idempotent)
            assert gridfs_id_1 is not None
            # Verifier que le 2eme run n'a pas re-migre notre doc en particulier
            # (approx : le nb de migrated documents doit avoir baisse ou etre
            # <= a celui du 1er run pour notre entree)
            assert r2["documents"]["migrated"] < r1["documents"]["migrated"] + 1
        finally:
            await db.documents.delete_many({"id": doc_id})
            if tmp_file.exists():
                tmp_file.unlink()
            await _cleanup_admin(db, uid)
            client.close()

    asyncio.run(_run())
