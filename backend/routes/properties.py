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
        first_name: Optional[str] = ""
        last_name: Optional[str] = ""
        name: Optional[str] = ""
        address: Optional[str] = ""
        postal_code: Optional[str] = ""
        city: Optional[str] = ""
        country: Optional[str] = "Belgique"
        email: Optional[str] = ""
        email2: Optional[str] = ""
        phone: Optional[str] = ""
        phone2: Optional[str] = ""
        copropriete_id: Optional[str] = ""

    @router.get("/owners")
    async def list_owners(request: Request, copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        owners = await db.owners.find(q, {"_id": 0}).sort("last_name", 1).to_list(1000)
        return owners

    @router.post("/owners")
    async def create_owner(data: OwnerInput):
        from server import generate_vcs
        vcs_code = await generate_vcs(db)
        vcs_digits = vcs_code.replace("+", "").replace("/", "")
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        doc = {
            "id": str(uuid.uuid4()),
            "first_name": data.first_name,
            "last_name": data.last_name,
            "name": full_name,
            "address": data.address,
            "postal_code": data.postal_code,
            "city": data.city,
            "country": data.country,
            "email": data.email,
            "email2": data.email2,
            "phone": data.phone,
            "phone2": data.phone2,
            "vcs_code": vcs_code,
            "vcs_digits": vcs_digits,
            "copropriete_id": data.copropriete_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.owners.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/owners/lookup-vcs")
    async def lookup_vcs(vcs: str = ""):
        if not vcs:
            return []
        clean = vcs.replace("+", "").replace("/", "").replace(" ", "").strip()
        results = await db.owners.find(
            {"$or": [
                {"vcs_code": {"$regex": vcs.replace("+", "\\+"), "$options": "i"}},
                {"vcs_digits": clean},
                {"name": {"$regex": vcs, "$options": "i"}},
                {"last_name": {"$regex": vcs, "$options": "i"}},
                {"first_name": {"$regex": vcs, "$options": "i"}},
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
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        result = await db.owners.update_one(
            {"id": owner_id},
            {"$set": {
                "first_name": data.first_name, "last_name": data.last_name, "name": full_name,
                "address": data.address, "postal_code": data.postal_code, "city": data.city,
                "country": data.country, "email": data.email, "email2": data.email2,
                "phone": data.phone, "phone2": data.phone2,
            }}
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
        owner_ids: Optional[List[str]] = []

    @router.get("/lots")
    async def list_lots():
        lots = await db.lots.find({}, {"_id": 0}).sort("number", 1).to_list(1000)
        return lots

    @router.post("/lots")
    async def create_lot(data: LotInput):
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "description": data.description,
            "lot_type": data.lot_type,
            "floor": data.floor,
            "area": data.area,
            "quotity": data.quotity,
            "owner_id": ids[0] if ids else "",
            "owner_ids": ids,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.lots.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/lots/{lot_id}")
    async def update_lot(lot_id: str, data: LotInput):
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        update = {
            "number": data.number, "description": data.description,
            "lot_type": data.lot_type, "floor": data.floor,
            "area": data.area, "quotity": data.quotity,
            "owner_id": ids[0] if ids else "",
            "owner_ids": ids,
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
