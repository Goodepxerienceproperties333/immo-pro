"""GridFS storage helper - persistent file storage via MongoDB.

Replaces the ephemeral filesystem (/app/uploads/...) which is wiped on every
container redeploy. Files are stored as BSON chunks in MongoDB and survive
redeploys, are included in backups, and don't require any external service.

Usage:
    from gridfs_storage import GridFSStorage

    storage = GridFSStorage(db, bucket_name="invoice_attachments")
    file_id = await storage.upload(filename="scan.pdf", contents=bytes, metadata={...})
    bytes_data = await storage.download(file_id)
    await storage.delete(file_id)
    info = await storage.stat(file_id)  # {filename, length, uploadDate, metadata}

Bucket naming convention:
    - "invoice_attachments"  : factures (PDFs joints aux factures)
    - "invoice_bundles"      : sessions d'import multi-pages temporaires
    - "documents"            : documents legaux (AG, contrats, etc.)
    - "journal_attachments"  : pieces justificatives d'ecritures comptables
    - "invoice_ai_tmp"       : PDFs temporaires pour OCR/IA

Each bucket creates 2 collections: `{name}.files` and `{name}.chunks`.
"""
from io import BytesIO
from typing import Optional
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket


class GridFSStorage:
    """Async GridFS wrapper. One instance per bucket."""

    def __init__(self, db, bucket_name: str = "fs"):
        self.db = db
        self.bucket_name = bucket_name
        self.bucket = AsyncIOMotorGridFSBucket(db, bucket_name=bucket_name)

    async def upload(self, filename: str, contents: bytes, metadata: Optional[dict] = None) -> str:
        """Stockage de bytes dans GridFS. Retourne l'id stringifie."""
        meta = dict(metadata or {})
        file_id = await self.bucket.upload_from_stream(
            filename=filename,
            source=contents,
            metadata=meta,
        )
        return str(file_id)

    async def download(self, file_id: str) -> bytes:
        """Lit le contenu du fichier. Leve gridfs.errors.NoFile si introuvable."""
        oid = ObjectId(file_id) if not isinstance(file_id, ObjectId) else file_id
        stream = await self.bucket.open_download_stream(oid)
        try:
            return await stream.read()
        finally:
            # GridOut.close() is sync in motor's wrapper, not awaitable
            try:
                stream.close()
            except Exception:
                pass

    async def delete(self, file_id: str) -> None:
        """Supprime le fichier. Idempotent (silencieux si deja absent)."""
        try:
            oid = ObjectId(file_id) if not isinstance(file_id, ObjectId) else file_id
            await self.bucket.delete(oid)
        except Exception:
            # Idempotent : NoFile ou autre, on log pas mais on swallow
            pass

    async def stat(self, file_id: str) -> Optional[dict]:
        """Retourne les metadonnees du fichier ou None si introuvable."""
        try:
            oid = ObjectId(file_id) if not isinstance(file_id, ObjectId) else file_id
            doc = await self.db[f"{self.bucket_name}.files"].find_one({"_id": oid})
            if doc is None:
                return None
            return {
                "id": str(doc["_id"]),
                "filename": doc.get("filename", ""),
                "length": doc.get("length", 0),
                "upload_date": doc.get("uploadDate"),
                "metadata": doc.get("metadata") or {},
            }
        except Exception:
            return None

    async def exists(self, file_id: str) -> bool:
        return (await self.stat(file_id)) is not None

    def stream_download(self, file_id: str):
        """Helper pour StreamingResponse FastAPI. Retourne un async generator."""
        async def _gen():
            oid = ObjectId(file_id) if not isinstance(file_id, ObjectId) else file_id
            stream = await self.bucket.open_download_stream(oid)
            try:
                while True:
                    chunk = await stream.readchunk()
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
        return _gen()


def get_invoice_attachments_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="invoice_attachments")


def get_invoice_bundles_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="invoice_bundles")


def get_documents_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="documents")


def get_journal_attachments_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="journal_attachments")


def get_invoice_ai_tmp_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="invoice_ai_tmp")
