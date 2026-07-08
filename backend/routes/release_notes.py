"""Release notes management (iter90br).

Fonctionnalites :
- CRUD sur les notes de version (superadmin only pour create/update/delete)
- Endpoint /unread : notes de version que le user courant n'a pas acquittees
- Endpoint /{note_id}/acknowledge : marque une note comme vue par le user
- Endpoint /all : liste complete (lecture pour tous les users authentifies)

Le frontend affichera automatiquement un popup au login si /unread renvoie
des notes non-lues (voir ReleaseNotesModal.js).

Categories : "feature" | "fix" | "improvement" | "security" | "breaking"
Roles cibles (roles_target) : liste de roles autorises a voir la note.
  ["all"] => tous les users ; ["syndic", "admin"] => seulement ces roles.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class ReleaseNoteInput(BaseModel):
    version: str  # ex "1.2.3" ou "iter90bq"
    title: str
    category: str = "improvement"  # feature|fix|improvement|security|breaking
    description: str = ""
    roles_target: List[str] = ["all"]
    published: bool = True


def create_release_notes_router(db):
    router = APIRouter(prefix="/api/release-notes", tags=["release-notes"])

    async def _get_user_id(request: Request) -> str:
        from server import get_current_user
        user = await get_current_user(request)
        return str(user["_id"] if "_id" in user else user.get("id", ""))

    async def _is_superadmin(request: Request) -> bool:
        from server import get_current_user
        user = await get_current_user(request)
        return user.get("role") == "superadmin"

    def _visible_to(note: dict, role: str) -> bool:
        targets = note.get("roles_target") or ["all"]
        if "all" in targets:
            return True
        return role in targets

    @router.get("/all")
    async def list_all(request: Request):
        """Historique complet des notes (visibles au role du user courant)."""
        from server import get_current_user
        user = await get_current_user(request)
        role = user.get("role", "")
        cursor = db.release_notes.find(
            {"published": True}, {"_id": 0}
        ).sort([("date", -1), ("created_at", -1)])
        notes = [n for n in await cursor.to_list(500) if _visible_to(n, role)]
        return notes

    @router.get("/unread")
    async def list_unread(request: Request):
        """Notes de version non acquittees par le user courant.
        Le frontend appelle cet endpoint au login pour afficher le popup."""
        from server import get_current_user
        user = await get_current_user(request)
        role = user.get("role", "")
        user_id = str(user.get("_id") or user.get("id"))
        # Notes deja acquittees par ce user
        ack_docs = await db.release_notes_ack.find(
            {"user_id": user_id}, {"_id": 0, "note_id": 1}
        ).to_list(1000)
        ack_ids = {a["note_id"] for a in ack_docs}
        cursor = db.release_notes.find(
            {"published": True}, {"_id": 0}
        ).sort([("date", -1), ("created_at", -1)])
        all_notes = await cursor.to_list(200)
        unread = [
            n for n in all_notes
            if n["id"] not in ack_ids and _visible_to(n, role)
        ]
        return unread

    @router.post("/{note_id}/acknowledge")
    async def acknowledge(note_id: str, request: Request):
        """Marque une note comme lue par le user courant. Idempotent."""
        note = await db.release_notes.find_one({"id": note_id}, {"_id": 0})
        if not note:
            raise HTTPException(404, "Note introuvable")
        user_id = await _get_user_id(request)
        await db.release_notes_ack.update_one(
            {"user_id": user_id, "note_id": note_id},
            {"$set": {
                "user_id": user_id, "note_id": note_id,
                "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        return {"success": True}

    @router.post("/acknowledge-all")
    async def acknowledge_all(request: Request):
        """Raccourci : marque toutes les notes non-lues comme acquittees.
        Utilise par le popup quand l'user clique 'OK, j'ai lu'."""
        from server import get_current_user
        user = await get_current_user(request)
        user_id = str(user.get("_id") or user.get("id"))
        role = user.get("role", "")
        all_notes = await db.release_notes.find(
            {"published": True}, {"_id": 0, "id": 1, "roles_target": 1}
        ).to_list(500)
        now_iso = datetime.now(timezone.utc).isoformat()
        for n in all_notes:
            if not _visible_to(n, role):
                continue
            await db.release_notes_ack.update_one(
                {"user_id": user_id, "note_id": n["id"]},
                {"$set": {
                    "user_id": user_id, "note_id": n["id"],
                    "acknowledged_at": now_iso,
                }},
                upsert=True,
            )
        return {"success": True, "count": len(all_notes)}

    @router.post("")
    async def create_note(data: ReleaseNoteInput, request: Request):
        if not await _is_superadmin(request):
            raise HTTPException(403, "Superadmin requis")
        note_id = str(uuid.uuid4())
        doc = {
            "id": note_id,
            "version": data.version,
            "title": data.title,
            "category": data.category,
            "description": data.description,
            "roles_target": data.roles_target or ["all"],
            "published": data.published,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.release_notes.insert_one(doc)
        return doc

    @router.put("/{note_id}")
    async def update_note(note_id: str, data: ReleaseNoteInput,
                          request: Request):
        if not await _is_superadmin(request):
            raise HTTPException(403, "Superadmin requis")
        existing = await db.release_notes.find_one({"id": note_id})
        if not existing:
            raise HTTPException(404, "Note introuvable")
        await db.release_notes.update_one(
            {"id": note_id},
            {"$set": {
                "version": data.version,
                "title": data.title,
                "category": data.category,
                "description": data.description,
                "roles_target": data.roles_target or ["all"],
                "published": data.published,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return await db.release_notes.find_one({"id": note_id}, {"_id": 0})

    @router.delete("/{note_id}")
    async def delete_note(note_id: str, request: Request):
        if not await _is_superadmin(request):
            raise HTTPException(403, "Superadmin requis")
        result = await db.release_notes.delete_one({"id": note_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Note introuvable")
        # Nettoyer les acknowledgements orphelins
        await db.release_notes_ack.delete_many({"note_id": note_id})
        return {"success": True}

    return router
