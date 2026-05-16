from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid


class CoproprieteInput(BaseModel):
    name: str
    address: Optional[str] = ""
    description: Optional[str] = ""
    bank_account: Optional[str] = ""


def create_coproprietes_router(db):
    router = APIRouter(prefix="/api/coproprietes")

    async def _get_manager(request):
        from server import get_current_user, can_manage
        user = await get_current_user(request)
        if not can_manage(user.get("role", "")):
            raise HTTPException(403, "Acces refuse")
        return user

    @router.get("")
    async def list_coproprietes(request: Request):
        from server import get_current_user, is_admin_role
        user = await get_current_user(request)
        if is_admin_role(user.get("role", "")):
            copros = await db.coproprietes.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        else:
            user_copro_ids = user.get("copropriete_ids", [])
            if user_copro_ids:
                copros = await db.coproprietes.find({"id": {"$in": user_copro_ids}}, {"_id": 0}).sort("name", 1).to_list(1000)
            else:
                copros = []
        return copros

    @router.post("")
    async def create_copropriete(data: CoproprieteInput, request: Request):
        user = await _get_manager(request)
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "address": data.address,
            "description": data.description,
            "bank_account": data.bank_account,
            "created_by": user.get("_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.coproprietes.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/{copro_id}")
    async def update_copropriete(copro_id: str, data: CoproprieteInput, request: Request):
        await _get_manager(request)
        update = {
            "name": data.name, "address": data.address,
            "description": data.description, "bank_account": data.bank_account
        }
        result = await db.coproprietes.update_one({"id": copro_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        return await db.coproprietes.find_one({"id": copro_id}, {"_id": 0})

    @router.delete("/{copro_id}")
    async def delete_copropriete(copro_id: str, request: Request):
        from server import get_current_user, is_admin_role
        user = await get_current_user(request)
        if not is_admin_role(user.get("role", "")):
            raise HTTPException(403, "Seul le super admin peut supprimer une copropriete")
        result = await db.coproprietes.delete_one({"id": copro_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        return {"message": "Copropriete supprimee"}

    return router
