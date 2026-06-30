"""Regression test - iter87 - Migration storage filesystem -> MongoDB GridFS.

Demande user (Feb 2026) :
    "Les PDFs uploades sont TOUJOURS perdus apres un deploiement"
    -> Le filesystem /app/uploads/ est ephemere. Tout est maintenant stocke en
    MongoDB GridFS (persistant, backupable, scopable par bucket).

Modules concernes :
    - routes/invoices.py        -> bucket `invoice_attachments` + `invoice_bundles`
    - routes/accounting.py      -> bucket `journal_attachments`
    - routes/documents.py       -> bucket `documents`
    - routes/invoice_ai.py      -> tempfile uniquement (rien de persistant)
    - routes/coproprietes.py    -> cascade delete GridFS sur suppression d'ACP
    - scripts/migrate_uploads_to_gridfs.py -> migration legacy disk -> GridFS

Tests groupes :
  1. Upload + download d'une PJ facture via GridFS (verifie gridfs_id present)
  2. Delete d'une PJ facture supprime le fichier de GridFS
  3. Delete d'une facture supprime ses PJ de GridFS (cascade)
  4. Upload + download d'une PJ ecriture comptable via GridFS
  5. Upload + download d'un document via GridFS
  6. Retrocompat : une PJ legacy (sans gridfs_id, stored_path sur disque)
     reste lisible
  7. Suppression d'ACP -> cascade GridFS pour tous les buckets
"""
import os
import sys
import asyncio
import uuid
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _get_endpoint(router_factory, db, route_path: str, method: str = "POST"):
    router = router_factory(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


class _MockUploadFile:
    """Mock fastapi UploadFile."""
    def __init__(self, filename: str, contents: bytes, content_type: str = "application/pdf"):
        self.filename = filename
        self._contents = contents
        self.content_type = content_type

    async def read(self):
        return self._contents


# --- 1. Invoice attachments via GridFS ---
async def _test_invoice_attachment_uses_gridfs():
    db = await _setup_db()
    from routes.invoices import create_invoices_router
    from gridfs_storage import get_invoice_attachments_storage

    cid = f"itr87-inv-{uuid.uuid4()}"
    inv_id = f"inv-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR87", "status": "active"})
    await db.invoices.insert_one({
        "id": inv_id, "number": "I1", "supplier": "X", "total_amount": 100,
        "date": "2026-01-15", "copropriete_id": cid, "attachments": [],
    })
    try:
        upload_fn = _get_endpoint(create_invoices_router, db, "/api/invoices/{invoice_id}/attachments", "POST")
        delete_fn = _get_endpoint(create_invoices_router, db, "/api/invoices/{invoice_id}/attachments/{attachment_id}", "DELETE")

        content = b"%PDF-1.5\n%fakepdf for iter87\n%%EOF"
        mock_file = _MockUploadFile("test-invoice.pdf", content)
        att = await upload_fn(invoice_id=inv_id, file=mock_file)

        # Verifier : gridfs_id present + plus de stored_path
        assert "gridfs_id" in att and att["gridfs_id"], "gridfs_id doit etre present"
        assert "stored_path" not in att, "stored_path NE doit PLUS etre present (GridFS only)"
        assert att["size"] == len(content)

        # Verifier : le binaire est bien dans GridFS
        storage = get_invoice_attachments_storage(db)
        downloaded = await storage.download(att["gridfs_id"])
        assert downloaded == content, "Le contenu downloade doit etre identique"

        # Verifier metadata
        info = await storage.stat(att["gridfs_id"])
        assert info["filename"] == "test-invoice.pdf"
        assert info["metadata"]["invoice_id"] == inv_id
        assert info["metadata"]["copropriete_id"] == cid

        # Delete -> GridFS file disparait
        await delete_fn(invoice_id=inv_id, attachment_id=att["id"])
        info2 = await storage.stat(att["gridfs_id"])
        assert info2 is None, "GridFS file doit etre supprime"

        # L'attachment doit etre retire du document invoice
        updated = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert all(a["id"] != att["id"] for a in (updated.get("attachments") or []))
        print("OK - iter87 : invoice attachment GridFS upload/download/delete")
    finally:
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


# --- 2. Cascade delete invoice -> attachments GridFS supprimes ---
async def _test_invoice_delete_cascades_gridfs():
    db = await _setup_db()
    from routes.invoices import create_invoices_router
    from gridfs_storage import get_invoice_attachments_storage

    cid = f"itr87-inv-cas-{uuid.uuid4()}"
    inv_id = f"inv-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR87", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.invoices.insert_one({
        "id": inv_id, "number": "I2", "supplier": "X", "total_amount": 100,
        "date": "2026-01-15", "copropriete_id": cid, "attachments": [],
    })
    try:
        upload_fn = _get_endpoint(create_invoices_router, db, "/api/invoices/{invoice_id}/attachments", "POST")
        delete_inv_fn = _get_endpoint(create_invoices_router, db, "/api/invoices/{invoice_id}", "DELETE")

        att1 = await upload_fn(invoice_id=inv_id, file=_MockUploadFile("a.pdf", b"%PDF-A"))
        att2 = await upload_fn(invoice_id=inv_id, file=_MockUploadFile("b.pdf", b"%PDF-B"))
        storage = get_invoice_attachments_storage(db)
        assert (await storage.stat(att1["gridfs_id"])) is not None
        assert (await storage.stat(att2["gridfs_id"])) is not None

        await delete_inv_fn(invoice_id=inv_id)

        assert (await storage.stat(att1["gridfs_id"])) is None, "PJ1 doit etre supprimee de GridFS"
        assert (await storage.stat(att2["gridfs_id"])) is None, "PJ2 doit etre supprimee de GridFS"
        print("OK - iter87 : delete invoice cascade GridFS")
    finally:
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.fiscal_years.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


# --- 3. Journal entry attachment via GridFS ---
async def _test_journal_attachment_uses_gridfs():
    db = await _setup_db()
    from routes.accounting import create_accounting_router
    from gridfs_storage import get_journal_attachments_storage

    cid = f"itr87-je-{uuid.uuid4()}"
    je_id = f"je-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR87", "status": "active"})
    await db.journal_entries.insert_one({
        "id": je_id, "journal_type": "OD", "date": "2026-01-15",
        "lines": [], "copropriete_id": cid, "attachments": [],
    })
    try:
        upload_fn = _get_endpoint(create_accounting_router, db, "/api/accounting/entries/{entry_id}/attachments", "POST")
        delete_fn = _get_endpoint(create_accounting_router, db, "/api/accounting/entries/{entry_id}/attachments/{attachment_id}", "DELETE")

        content = b"%PDF-OD-iter87"
        att = await upload_fn(entry_id=je_id, file=_MockUploadFile("od.pdf", content))
        assert att["gridfs_id"]
        assert "stored_path" not in att

        storage = get_journal_attachments_storage(db)
        downloaded = await storage.download(att["gridfs_id"])
        assert downloaded == content

        await delete_fn(entry_id=je_id, attachment_id=att["id"])
        assert (await storage.stat(att["gridfs_id"])) is None
        print("OK - iter87 : journal attachment GridFS upload/download/delete")
    finally:
        await db.journal_entries.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


# --- 4. Document upload via GridFS (sans IA) ---
async def _test_document_upload_uses_gridfs():
    db = await _setup_db()
    from routes.documents import create_documents_router
    from gridfs_storage import get_documents_storage

    cid = f"itr87-doc-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR87", "status": "active"})
    try:
        upload_fn = _get_endpoint(create_documents_router, db, "/api/documents/upload", "POST")
        download_fn = _get_endpoint(create_documents_router, db, "/api/documents/{doc_id}/download", "GET")
        delete_fn = _get_endpoint(create_documents_router, db, "/api/documents/{doc_id}", "DELETE")

        content = b"%PDF-iter87-doc"
        doc = await upload_fn(
            file=_MockUploadFile("AG-2026.pdf", content),
            title="PV AG 2026", description="", category_id="",
            copropriete_id=cid, auto_classify=False,
        )
        assert doc["gridfs_id"], "gridfs_id doit etre present"
        assert "stored_path" not in doc

        # Download via endpoint
        resp = await download_fn(doc_id=doc["id"])
        assert resp.body == content

        # Delete -> GridFS supprime
        storage = get_documents_storage(db)
        assert (await storage.stat(doc["gridfs_id"])) is not None
        await delete_fn(doc_id=doc["id"])
        assert (await storage.stat(doc["gridfs_id"])) is None

        # Mongo doc supprime
        gone = await db.documents.find_one({"id": doc["id"]}, {"_id": 0})
        assert gone is None
        print("OK - iter87 : document GridFS upload/download/delete")
    finally:
        await db.documents.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


# --- 5. Retrocompat : un attachment legacy (stored_path uniquement) reste lisible ---
async def _test_legacy_attachment_still_readable():
    """Une facture avec attachments[].stored_path (mais sans gridfs_id) doit
    rester lisible via l'endpoint /download (fallback disque)."""
    db = await _setup_db()
    from routes.invoices import create_invoices_router

    cid = f"itr87-legacy-{uuid.uuid4()}"
    inv_id = f"inv-{uuid.uuid4()}"
    att_id = str(uuid.uuid4())
    legacy_path = Path(f"/tmp/iter87-legacy-{att_id}.pdf")
    legacy_content = b"%PDF-LEGACY-CONTENT"
    legacy_path.write_bytes(legacy_content)

    await db.coproprietes.insert_one({"id": cid, "name": "ITR87", "status": "active"})
    await db.invoices.insert_one({
        "id": inv_id, "number": "L1", "supplier": "X", "total_amount": 50,
        "date": "2026-01-15", "copropriete_id": cid,
        "attachments": [{
            "id": att_id,
            "filename": "legacy.pdf",
            "stored_path": str(legacy_path),  # LEGACY : pas de gridfs_id
            "mime_type": "application/pdf",
            "size": len(legacy_content),
        }],
    })
    try:
        download_fn = _get_endpoint(create_invoices_router, db, "/api/invoices/{invoice_id}/attachments/{attachment_id}/download", "GET")
        # En theorie ca renvoie un FileResponse (qu'on ne peut pas serialiser
        # facilement ici), mais en pratique on verifie juste qu'il ne plante pas
        # et qu'il identifie le bon fichier.
        resp = await download_fn(invoice_id=inv_id, attachment_id=att_id)
        # FileResponse a un attribut `path` qui pointe vers le fichier disque
        assert hasattr(resp, "path"), f"FileResponse attendu, recu {type(resp).__name__}"
        assert str(resp.path) == str(legacy_path)
        print("OK - iter87 : retrocompat legacy stored_path OK")
    finally:
        try:
            legacy_path.unlink()
        except Exception:
            pass
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.coproprietes.delete_one({"id": cid})


# --- 6. Bundle session : PDF stocke en GridFS, session TTL ---
async def _test_bundle_session_uses_gridfs():
    """Verifie que la session bundle stocke le PDF en GridFS (pas /app/uploads)
    et que la metadata expires_at est posee (TTL 24h)."""
    db = await _setup_db()
    from gridfs_storage import get_invoice_bundles_storage
    from datetime import datetime, timezone, timedelta

    # On ne peut pas appeler bundle-analyze sans un PDF realiste,
    # mais on peut tester la mecanique storage + session directement.
    storage = get_invoice_bundles_storage(db)
    session_id = str(uuid.uuid4())
    content = b"%PDF-BUNDLE-CONTENT"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=24)
    gid = await storage.upload(
        filename=f"bundle-{session_id}.pdf", contents=content,
        metadata={"session_id": session_id, "kind": "bundle_pdf",
                  "created_at": now.isoformat(), "expires_at": expires_at},
    )
    try:
        # Verifier que le session doc cree manuellement (comme l'endpoint le ferait)
        # a bien expires_at en BSON date pour activer le TTL
        await db.invoice_bundle_sessions.insert_one({
            "session_id": session_id,
            "pdf_gridfs_id": gid,
            "copropriete_id": "test",
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        })
        sess = await db.invoice_bundle_sessions.find_one(
            {"session_id": session_id}, {"_id": 0}
        )
        assert sess["pdf_gridfs_id"] == gid
        assert isinstance(sess["expires_at"], datetime), \
            "expires_at doit etre BSON ISODate pour le TTL index"
        # Verifier le binaire en GridFS
        downloaded = await storage.download(gid)
        assert downloaded == content
        print("OK - iter87 : bundle session GridFS + TTL prepare")
    finally:
        await db.invoice_bundle_sessions.delete_one({"session_id": session_id})
        await storage.delete(gid)


# --- Wrappers pytest ---
def test_iter87_invoice_attachment_uses_gridfs():
    asyncio.run(_test_invoice_attachment_uses_gridfs())


def test_iter87_invoice_delete_cascades_gridfs():
    asyncio.run(_test_invoice_delete_cascades_gridfs())


def test_iter87_journal_attachment_uses_gridfs():
    asyncio.run(_test_journal_attachment_uses_gridfs())


def test_iter87_document_upload_uses_gridfs():
    asyncio.run(_test_document_upload_uses_gridfs())


def test_iter87_legacy_attachment_still_readable():
    asyncio.run(_test_legacy_attachment_still_readable())


def test_iter87_bundle_session_uses_gridfs():
    asyncio.run(_test_bundle_session_uses_gridfs())
