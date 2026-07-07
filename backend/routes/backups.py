"""iter90ax : Router admin pour la gestion des backups quotidiens des ACP +
export archive complet par le syndic.

Endpoints admin (superadmin only) :
- GET  /api/admin/backups                                : liste des backups
- POST /api/admin/backups/trigger                        : declenche un backup manuel
- GET  /api/admin/backups/{backup_id}/download           : DL le ZIP
- DELETE /api/admin/backups/{backup_id}                  : suppression manuelle
- GET  /api/admin/backups/runs                           : historique des jobs
- POST /api/admin/backups/restore/{backup_id}            : restauration (dry-run)

Endpoint syndic :
- POST /api/coproprietes/{id}/archive-download           : genere l'archive ZIP
  structuree par annee (retourne le fichier en streaming).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backup_service import (
    ACP_SCOPED_COLLECTIONS,
    build_acp_archive_zip,
    create_backup_all_acps,
    dump_acp_to_zip,
)

logger = logging.getLogger("backups")


class RestorePayload(BaseModel):
    dry_run: bool = True
    collections: Optional[list[str]] = None  # None = tout


def create_backups_router(db):
    router = APIRouter(prefix="/api")

    async def _require_superadmin(request: Request):
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        u = await db.users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("role") not in ("superadmin", "admin"):
            raise HTTPException(403, "Reserve au superadmin")
        return u

    async def _require_syndic_scope(request: Request, copropriete_id: str):
        """Verifie que le user courant gere cette ACP (syndic ou son gestionnaire)."""
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        u = await db.users.find_one({"_id": ObjectId(uid)})
        if not u:
            raise HTTPException(401, "Utilisateur introuvable")
        role = u.get("role")
        if role in ("admin", "superadmin"):
            return u
        # Verifie que copro appartient au scope
        allowed_copros = set(u.get("copropriete_ids") or [])
        if role == "gestionnaire" and u.get("parent_syndic_id"):
            parent = await db.users.find_one({"_id": ObjectId(u["parent_syndic_id"])})
            if parent:
                allowed_copros |= set(parent.get("copropriete_ids") or [])
        if copropriete_id not in allowed_copros:
            raise HTTPException(403, "Cette ACP ne fait pas partie de votre portefeuille")
        return u

    # ============ Endpoints admin ============

    @router.get("/admin/backups")
    async def list_backups(
        request: Request,
        copropriete_id: Optional[str] = None,
        limit: int = Query(default=100, le=1000),
    ):
        await _require_superadmin(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        docs = await db.backups_index.find(q).sort("created_at", -1).limit(limit).to_list(limit)
        for d in docs:
            d["_id"] = str(d.get("_id", ""))
        return {"backups": docs}

    @router.get("/admin/backups/runs")
    async def list_runs(request: Request, limit: int = Query(default=30, le=200)):
        await _require_superadmin(request)
        docs = await db.backups_runs.find().sort("created_at", -1).limit(limit).to_list(limit)
        for d in docs:
            d["_id"] = str(d.get("_id", ""))
        return {"runs": docs}

    @router.post("/admin/backups/trigger")
    async def trigger_backup(request: Request):
        """Declenche un backup manuel de toutes les ACP maintenant."""
        await _require_superadmin(request)
        summary = await create_backup_all_acps(db, source="manual")
        return summary

    @router.post("/admin/backups/trigger/{copropriete_id}")
    async def trigger_backup_one(copropriete_id: str, request: Request):
        """Declenche un backup manuel pour une ACP specifique."""
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        try:
            data, manifest = await dump_acp_to_zip(db, copropriete_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        filename = f"{copropriete_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_manual.zip"
        file_id = await storage.upload(
            filename=filename, contents=data,
            metadata={"copropriete_id": copropriete_id, "manifest": manifest,
                      "type": "manual", "source": "manual"},
        )
        idx = {
            "backup_id": file_id, "copropriete_id": copropriete_id,
            "copropriete_name": manifest.get("copropriete_name", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": len(data), "type": "manual",
            "owners_count": manifest.get("owners_count", 0),
            "collections_counts": manifest.get("collections", {}),
        }
        await db.backups_index.insert_one(idx)
        idx["_id"] = str(idx.get("_id", ""))
        return {"success": True, "backup": idx}

    @router.get("/admin/backups/{backup_id}/download")
    async def download_backup(backup_id: str, request: Request):
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            data = await storage.download(backup_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Erreur GridFS : {e}") from e
        filename = f"backup_{info.get('copropriete_name','acp')}_{info['created_at'][:10]}.zip"
        return StreamingResponse(
            iter([data]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.delete("/admin/backups/{backup_id}")
    async def delete_backup(backup_id: str, request: Request):
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            await storage.delete(backup_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Erreur suppression GridFS %s : %s", backup_id, e)
        await db.backups_index.delete_one({"backup_id": backup_id})
        return {"success": True}

    @router.post("/admin/backups/restore/{backup_id}")
    async def restore_backup(backup_id: str, payload: RestorePayload, request: Request):
        """Restaure un backup en base. En dry-run par defaut : retourne juste ce
        qui serait fait. En mode non-dry-run : ecrase les documents avec meme
        id + insere ceux qui manquent (upsert).

        !! Attention !! Utiliser avec precaution. Un backup precedent l'appel
        est fortement recommande.
        """
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        import io, json, zipfile
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        data = await storage.download(backup_id)
        summary = {"backup_id": backup_id, "dry_run": payload.dry_run,
                   "collections": {}, "would_touch": {}}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            copropriete_id = manifest["copropriete_id"]

            # 1. Copropriete
            copro_doc = json.loads(z.read("copropriete.json").decode("utf-8"))
            summary["copropriete_id"] = copropriete_id
            if not payload.dry_run:
                await db.coproprietes.update_one(
                    {"id": copropriete_id}, {"$set": copro_doc}, upsert=True,
                )
            summary["collections"]["coproprietes"] = 1

            # 2. Owners
            owners = json.loads(z.read("owners.json").decode("utf-8"))
            summary["collections"]["owners"] = len(owners)
            if not payload.dry_run:
                for o in owners:
                    if o.get("id"):
                        await db.owners.update_one({"id": o["id"]}, {"$set": o}, upsert=True)

            # 3. Collections scopees
            target_colls = payload.collections or ACP_SCOPED_COLLECTIONS
            for coll_name in target_colls:
                if coll_name not in ACP_SCOPED_COLLECTIONS:
                    continue
                path = f"collections/{coll_name}.jsonl"
                try:
                    raw = z.read(path).decode("utf-8")
                except KeyError:
                    continue
                if not raw.strip():
                    summary["collections"][coll_name] = 0
                    continue
                docs = [json.loads(l) for l in raw.strip().split("\n") if l.strip()]
                summary["collections"][coll_name] = len(docs)
                if not payload.dry_run:
                    # Strategie : upsert par 'id' si present, sinon insert
                    for d in docs:
                        if d.get("id"):
                            await db[coll_name].update_one(
                                {"id": d["id"]}, {"$set": d}, upsert=True,
                            )
                        else:
                            await db[coll_name].insert_one(d)
        return {"success": True, **summary}

    # ============ Endpoint syndic : archive telechargement ============

    @router.post("/coproprietes/{copropriete_id}/archive-download")
    async def download_archive(
        copropriete_id: str, request: Request,
        include_pdfs: bool = Query(default=True),
    ):
        """Genere un ZIP structure PAR ANNEE FISCALE. Reserve aux syndics
        gestionnaires de l'ACP + admin.

        Le status de l'ACP n'est pas force a 'archived' - le syndic peut aussi
        DL une archive intermediaire. Un flag `require_archived=True` peut etre
        active plus tard si necessaire.
        """
        await _require_syndic_scope(request, copropriete_id)
        try:
            data, filename = await build_acp_archive_zip(
                db, copropriete_id, include_pdfs=include_pdfs,
            )
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        # Log audit
        try:
            uid = getattr(request.state, "user_id", None)
            await db.acp_archive_downloads.insert_one({
                "copropriete_id": copropriete_id,
                "user_id": uid,
                "size_bytes": len(data),
                "include_pdfs": include_pdfs,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        return StreamingResponse(
            iter([data]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
