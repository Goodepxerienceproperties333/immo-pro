from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone
import uuid


class UserCreateInput(BaseModel):
    email: str
    password: str
    name: str
    role: str  # superadmin, syndic, owner
    copropriete_ids: Optional[List[str]] = []


class UserUpdateInput(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    copropriete_ids: Optional[List[str]] = None
    password: Optional[str] = None


def create_admin_router(db):
    router = APIRouter(prefix="/api/admin")

    async def _get_admin_user(request):
        from server import get_current_user, is_admin_role, can_manage
        user = await get_current_user(request)
        if not can_manage(user.get("role", "")):
            raise HTTPException(403, "Acces refuse")
        return user

    @router.get("/users")
    async def list_users(request: Request):
        user = await _get_admin_user(request)
        from server import is_admin_role
        query = {}
        # Syndics can only see users in their coproprietes
        if not is_admin_role(user.get("role", "")):
            query["copropriete_ids"] = {"$in": user.get("copropriete_ids", [])}
        users = await db.users.find(query).sort("name", 1).to_list(1000)
        result = []
        for u in users:
            result.append({
                "id": str(u["_id"]),
                "email": u["email"],
                "name": u["name"],
                "role": u.get("role", "owner"),
                "copropriete_ids": u.get("copropriete_ids", []),
                "created_at": u.get("created_at", ""),
            })
        return result

    @router.post("/users")
    async def create_user(data: UserCreateInput, request: Request):
        user = await _get_admin_user(request)
        from server import is_admin_role, hash_password
        # Only superadmin can create superadmin/syndic
        if data.role in ("superadmin", "admin") and not is_admin_role(user.get("role", "")):
            raise HTTPException(403, "Seul le super admin peut creer des syndics")
        email = data.email.lower().strip()
        existing = await db.users.find_one({"email": email})
        if existing:
            raise HTTPException(400, "Cet email existe deja")
        doc = {
            "email": email,
            "password_hash": hash_password(data.password),
            "name": data.name,
            "role": data.role if data.role in ("superadmin", "syndic", "owner") else "owner",
            "copropriete_ids": data.copropriete_ids or [],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.users.insert_one(doc)
        return {
            "id": str(result.inserted_id),
            "email": email,
            "name": data.name,
            "role": doc["role"],
            "copropriete_ids": doc["copropriete_ids"],
        }

    @router.put("/users/{user_id}")
    async def update_user(user_id: str, data: UserUpdateInput, request: Request):
        admin = await _get_admin_user(request)
        from server import is_admin_role, hash_password
        target = await db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(404, "Utilisateur non trouve")
        # Syndics cannot change superadmin users
        if not is_admin_role(admin.get("role", "")) and target.get("role") in ("superadmin", "admin"):
            raise HTTPException(403, "Vous ne pouvez pas modifier un super administrateur")
        update = {}
        if data.name is not None:
            update["name"] = data.name
        if data.role is not None:
            if data.role in ("superadmin", "admin") and not is_admin_role(admin.get("role", "")):
                raise HTTPException(403, "Seul le super admin peut attribuer ce role")
            update["role"] = data.role
        if data.copropriete_ids is not None:
            update["copropriete_ids"] = data.copropriete_ids
        if data.password:
            update["password_hash"] = hash_password(data.password)
        if update:
            await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
        updated = await db.users.find_one({"_id": ObjectId(user_id)})
        return {
            "id": str(updated["_id"]),
            "email": updated["email"],
            "name": updated["name"],
            "role": updated.get("role", "owner"),
            "copropriete_ids": updated.get("copropriete_ids", []),
        }

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, request: Request):
        admin = await _get_admin_user(request)
        from server import is_admin_role
        if not is_admin_role(admin.get("role", "")):
            raise HTTPException(403, "Seul le super admin peut supprimer des utilisateurs")
        if str(admin["_id"]) == user_id:
            raise HTTPException(400, "Vous ne pouvez pas vous supprimer vous-meme")
        result = await db.users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            raise HTTPException(404, "Utilisateur non trouve")
        return {"message": "Utilisateur supprime"}

    return router
