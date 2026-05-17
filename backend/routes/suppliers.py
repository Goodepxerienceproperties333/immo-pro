from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid
from tier_accounts import assign_supplier_account


class SupplierInput(BaseModel):
    name: str
    vat_number: Optional[str] = ""
    bce_number: Optional[str] = ""
    address: Optional[str] = ""
    postal_code: Optional[str] = ""
    city: Optional[str] = ""
    country: Optional[str] = "Belgique"
    phone: Optional[str] = ""
    email: Optional[str] = ""
    iban: Optional[str] = ""
    bic: Optional[str] = ""
    default_account: Optional[str] = ""
    notes: Optional[str] = ""
    copropriete_id: Optional[str] = ""


def create_suppliers_router(db):
    router = APIRouter(prefix="/api/suppliers")

    @router.get("")
    async def list_suppliers(search: Optional[str] = None, copropriete_id: Optional[str] = None):
        q = {}
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"vat_number": {"$regex": search, "$options": "i"}},
                {"bce_number": {"$regex": search, "$options": "i"}},
            ]
        suppliers = await db.suppliers.find(q, {"_id": 0}).sort("name", 1).to_list(1000)
        return suppliers

    @router.post("")
    async def create_supplier(request: Request, data: SupplierInput):
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or ""
        doc = {
            "id": str(uuid.uuid4()),
            **data.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.suppliers.insert_one(doc)
        if copro_id:
            await assign_supplier_account(db, doc, copro_id)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{supplier_id}")
    async def get_supplier(supplier_id: str):
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not s:
            raise HTTPException(404, "Fournisseur non trouve")
        return s

    @router.put("/{supplier_id}")
    async def update_supplier(supplier_id: str, data: SupplierInput, request: Request):
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or ""
        result = await db.suppliers.update_one({"id": supplier_id}, {"$set": data.model_dump()})
        if result.matched_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if copro_id:
            await assign_supplier_account(db, s, copro_id)
            s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        return s

    @router.delete("/{supplier_id}")
    async def delete_supplier(supplier_id: str):
        result = await db.suppliers.delete_one({"id": supplier_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        return {"message": "Fournisseur supprime"}

    return router
