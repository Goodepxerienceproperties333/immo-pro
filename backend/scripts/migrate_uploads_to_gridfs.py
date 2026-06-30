"""iter87 - Migration one-shot des fichiers du filesystem vers MongoDB GridFS.

Contexte :
    Les pieces jointes (factures, ecritures comptables, documents) etaient
    stockees sur /app/uploads/ qui est EPHEMERE (efface a chaque redeploiement).
    A partir d'iter87, tout est stocke en MongoDB GridFS (persistant + backupable).

Ce script :
    1. Parcourt toutes les `db.invoices` avec `attachments[]` (legacy = `stored_path`
       sans `gridfs_id`) et upload chaque fichier dans le bucket
       `invoice_attachments`. Ecrit `gridfs_id` sur l'attachment et garde
       `stored_path` pour audit.
    2. Idem pour `db.journal_entries.attachments[]` -> bucket `journal_attachments`.
    3. Idem pour `db.documents` -> bucket `documents`.
    4. Idempotent : skip les attachments qui ont deja un `gridfs_id`.
    5. Logs progressifs + summary final.

Usage :
    python /app/backend/scripts/migrate_uploads_to_gridfs.py [--dry-run]

Options :
    --dry-run : n'ecrit rien, affiche juste le plan (count + total bytes)
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from gridfs_storage import (  # noqa: E402
    get_invoice_attachments_storage,
    get_journal_attachments_storage,
    get_documents_storage,
)


async def _migrate_attachments_collection(
    db,
    coll_name: str,
    storage,
    legacy_dir: Path,
    dry_run: bool,
):
    """Pour `db[coll_name]` avec `attachments[]`, migre chaque attachment
    sans `gridfs_id` vers le bucket cible. Retourne (migrated, skipped, missing, bytes_total).
    """
    migrated = skipped = missing = 0
    bytes_total = 0
    cursor = db[coll_name].find(
        {"attachments": {"$exists": True, "$ne": []}},
        {"_id": 0, "id": 1, "attachments": 1, "copropriete_id": 1}
    )
    docs = await cursor.to_list(100000)
    for doc in docs:
        doc_id = doc["id"]
        attachments = doc.get("attachments") or []
        updated = False
        for idx, att in enumerate(attachments):
            if att.get("gridfs_id"):
                skipped += 1
                continue
            sp = att.get("stored_path") or ""
            # Some legacy docs only have `stored_filename`
            if not sp:
                sf = att.get("stored_filename") or ""
                if sf:
                    sp = str(legacy_dir / sf)
            if not sp:
                missing += 1
                continue
            fp = Path(sp)
            if not fp.exists():
                missing += 1
                continue
            try:
                size = fp.stat().st_size
            except Exception:
                missing += 1
                continue
            bytes_total += size
            if dry_run:
                migrated += 1
                continue
            with open(fp, "rb") as f:
                contents = f.read()
            gridfs_id = await storage.upload(
                filename=att.get("filename") or fp.name,
                contents=contents,
                metadata={
                    "attachment_id": att.get("id") or "",
                    "doc_id": doc_id,
                    "copropriete_id": doc.get("copropriete_id", ""),
                    "mime_type": att.get("mime_type", "application/octet-stream"),
                    "migrated_from": str(fp),
                    "size": size,
                },
            )
            attachments[idx] = {**att, "gridfs_id": gridfs_id, "size": att.get("size") or size}
            updated = True
            migrated += 1
        if updated and not dry_run:
            await db[coll_name].update_one(
                {"id": doc_id}, {"$set": {"attachments": attachments}}
            )
    return migrated, skipped, missing, bytes_total


async def _migrate_documents(db, dry_run: bool):
    """Pour `db.documents` (1 fichier = 1 doc), migre chaque document sans
    `gridfs_id` vers le bucket `documents`."""
    storage = get_documents_storage(db)
    legacy_dirs = [Path("/app/uploads/documents"), Path("/app/uploads")]
    migrated = skipped = missing = 0
    bytes_total = 0
    cursor = db.documents.find({}, {"_id": 0, "id": 1, "gridfs_id": 1,
                                     "stored_path": 1, "stored_filename": 1,
                                     "filename": 1, "mime_type": 1,
                                     "copropriete_id": 1})
    docs = await cursor.to_list(100000)
    for doc in docs:
        if doc.get("gridfs_id"):
            skipped += 1
            continue
        sp = doc.get("stored_path") or ""
        fp = Path(sp) if sp else None
        if not fp or not fp.exists():
            sf = doc.get("stored_filename") or ""
            fp = None
            if sf:
                for ldir in legacy_dirs:
                    candidate = ldir / sf
                    if candidate.exists():
                        fp = candidate
                        break
        if not fp or not fp.exists():
            missing += 1
            continue
        try:
            size = fp.stat().st_size
        except Exception:
            missing += 1
            continue
        bytes_total += size
        if dry_run:
            migrated += 1
            continue
        with open(fp, "rb") as f:
            contents = f.read()
        gridfs_id = await storage.upload(
            filename=doc.get("filename") or fp.name,
            contents=contents,
            metadata={
                "document_id": doc["id"],
                "copropriete_id": doc.get("copropriete_id", ""),
                "mime_type": doc.get("mime_type", "application/octet-stream"),
                "migrated_from": str(fp),
                "size": size,
            },
        )
        await db.documents.update_one(
            {"id": doc["id"]}, {"$set": {"gridfs_id": gridfs_id}}
        )
        migrated += 1
    return migrated, skipped, missing, bytes_total


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


async def _create_bundles_ttl_index(db):
    """Cree un index TTL sur invoice_bundle_sessions.expires_at pour
    auto-cleanup des sessions expirees (24h)."""
    try:
        await db.invoice_bundle_sessions.create_index(
            "expires_at", expireAfterSeconds=0
        )
        print("OK - TTL index cree sur invoice_bundle_sessions.expires_at")
    except Exception as e:
        print(f"WARN - TTL index : {e}")


async def main(dry_run: bool):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    mode = "[DRY-RUN]" if dry_run else "[LIVE]"
    print(f"=== Migration uploads -> GridFS {mode} ===\n")

    inv_storage = get_invoice_attachments_storage(db)
    je_storage = get_journal_attachments_storage(db)

    print("1. Migration invoices.attachments[] -> invoice_attachments bucket")
    m, s, mi, b = await _migrate_attachments_collection(
        db, "invoices", inv_storage,
        Path("/app/uploads/invoice_attachments"), dry_run,
    )
    print(f"   migrated={m} skipped(deja GridFS)={s} missing(fichier absent)={mi} total={_human_bytes(b)}")

    print("\n2. Migration journal_entries.attachments[] -> journal_attachments bucket")
    m2, s2, mi2, b2 = await _migrate_attachments_collection(
        db, "journal_entries", je_storage,
        Path("/app/uploads/journal_attachments"), dry_run,
    )
    print(f"   migrated={m2} skipped(deja GridFS)={s2} missing(fichier absent)={mi2} total={_human_bytes(b2)}")

    print("\n3. Migration documents -> documents bucket")
    m3, s3, mi3, b3 = await _migrate_documents(db, dry_run)
    print(f"   migrated={m3} skipped(deja GridFS)={s3} missing(fichier absent)={mi3} total={_human_bytes(b3)}")

    if not dry_run:
        print("\n4. Index TTL sur invoice_bundle_sessions")
        await _create_bundles_ttl_index(db)

    print(f"\n=== SUMMARY {mode} ===")
    print(f"Total migrated: {m + m2 + m3}")
    print(f"Total skipped (already in GridFS): {s + s2 + s3}")
    print(f"Total missing (file gone from disk): {mi + mi2 + mi3}")
    print(f"Total bytes : {_human_bytes(b + b2 + b3)}")
    if dry_run:
        print("\nDRY-RUN : aucune modification ecrite. Relancez sans --dry-run pour appliquer.")


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    asyncio.run(main(is_dry))
