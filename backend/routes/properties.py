from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone
import uuid


def create_properties_router(db):
    router = APIRouter(prefix="/api")

    # ---- OWNERS ----
    class OwnerInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        address: Optional[str] = ""
        copropriete_id: Optional[str] = ""

    @router.get("/owners")
    async def list_owners(request: Request, copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        owners = await db.owners.find(q, {"_id": 0}).sort("name", 1).to_list(1000)
        return owners

    @router.post("/owners")
    async def create_owner(data: OwnerInput):
        from server import generate_vcs
        vcs_code = await generate_vcs(db)
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "address": data.address,
            "vcs_code": vcs_code,
            "copropriete_id": data.copropriete_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.owners.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/owners/lookup-vcs")
    async def lookup_vcs(vcs: str = ""):
        """Lookup owner by VCS structured communication."""
        if not vcs:
            return []
        # Clean VCS - remove +++ and /
        clean = vcs.replace("+", "").replace("/", "").strip()
        results = await db.owners.find(
            {"$or": [
                {"vcs_code": {"$regex": clean, "$options": "i"}},
                {"vcs_code": {"$regex": vcs, "$options": "i"}},
                {"name": {"$regex": vcs, "$options": "i"}},
            ]},
            {"_id": 0}
        ).to_list(20)
        return results

    @router.get("/owners/{owner_id}")
    async def get_owner(owner_id: str):
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        return owner

    @router.put("/owners/{owner_id}")
    async def update_owner(owner_id: str, data: OwnerInput):
        result = await db.owners.update_one(
            {"id": owner_id},
            {"$set": {"name": data.name, "email": data.email, "phone": data.phone, "address": data.address}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        return await db.owners.find_one({"id": owner_id}, {"_id": 0})

    @router.delete("/owners/{owner_id}")
    async def delete_owner(owner_id: str):
        result = await db.owners.delete_one({"id": owner_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        return {"message": "Proprietaire supprime"}

    # ---- LOTS ----
    class LotInput(BaseModel):
        number: str
        description: Optional[str] = ""
        lot_type: Optional[str] = "apartment"
        floor: Optional[int] = 0
        area: Optional[float] = 0.0
        quotity: Optional[float] = 0.0
        owner_id: Optional[str] = ""

    @router.get("/lots")
    async def list_lots():
        lots = await db.lots.find({}, {"_id": 0}).sort("number", 1).to_list(1000)
        return lots

    @router.post("/lots")
    async def create_lot(data: LotInput):
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "description": data.description,
            "lot_type": data.lot_type,
            "floor": data.floor,
            "area": data.area,
            "quotity": data.quotity,
            "owner_id": data.owner_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.lots.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/lots/{lot_id}")
    async def update_lot(lot_id: str, data: LotInput):
        update = {
            "number": data.number, "description": data.description,
            "lot_type": data.lot_type, "floor": data.floor,
            "area": data.area, "quotity": data.quotity, "owner_id": data.owner_id
        }
        result = await db.lots.update_one({"id": lot_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return await db.lots.find_one({"id": lot_id}, {"_id": 0})

    @router.delete("/lots/{lot_id}")
    async def delete_lot(lot_id: str):
        result = await db.lots.delete_one({"id": lot_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return {"message": "Lot supprime"}

    # ---- TENANTS ----
    class TenantInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        lot_id: Optional[str] = ""
        lease_start: Optional[str] = ""
        lease_end: Optional[str] = ""
        rent_amount: Optional[float] = 0.0

    @router.get("/tenants")
    async def list_tenants():
        tenants = await db.tenants.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        return tenants

    @router.post("/tenants")
    async def create_tenant(data: TenantInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "lot_id": data.lot_id,
            "lease_start": data.lease_start,
            "lease_end": data.lease_end,
            "rent_amount": data.rent_amount,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.tenants.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/tenants/{tenant_id}")
    async def update_tenant(tenant_id: str, data: TenantInput):
        update = {
            "name": data.name, "email": data.email, "phone": data.phone,
            "lot_id": data.lot_id, "lease_start": data.lease_start,
            "lease_end": data.lease_end, "rent_amount": data.rent_amount
        }
        result = await db.tenants.update_one({"id": tenant_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return await db.tenants.find_one({"id": tenant_id}, {"_id": 0})

    @router.delete("/tenants/{tenant_id}")
    async def delete_tenant(tenant_id: str):
        result = await db.tenants.delete_one({"id": tenant_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return {"message": "Locataire supprime"}

    return router
