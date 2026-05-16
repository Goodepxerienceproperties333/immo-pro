from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class DistKeyLot(BaseModel):
    lot_id: str
    lot_number: str
    share: float


class DistKeyInput(BaseModel):
    name: str
    description: Optional[str] = ""
    key_type: Optional[str] = "quotity"  # quotity, equal, custom
    lots: Optional[List[DistKeyLot]] = []


class InvoiceInput(BaseModel):
    number: str
    date: str
    due_date: Optional[str] = ""
    supplier: str
    description: str
    total_amount: float
    vat_amount: Optional[float] = 0.0
    account_number: Optional[str] = ""
    distribution_key_id: Optional[str] = ""
    status: Optional[str] = "unpaid"


def create_invoices_router(db):
    router = APIRouter(prefix="/api")

    # ---- DISTRIBUTION KEYS ----
    @router.get("/distribution-keys")
    async def list_dist_keys():
        keys = await db.distribution_keys.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        return keys

    @router.post("/distribution-keys")
    async def create_dist_key(data: DistKeyInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "description": data.description,
            "key_type": data.key_type,
            "lots": [l.model_dump() for l in data.lots],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.distribution_keys.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/distribution-keys/{key_id}")
    async def update_dist_key(key_id: str, data: DistKeyInput):
        update = {
            "name": data.name, "description": data.description,
            "key_type": data.key_type, "lots": [l.model_dump() for l in data.lots]
        }
        result = await db.distribution_keys.update_one({"id": key_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Cle non trouvee")
        return await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})

    @router.delete("/distribution-keys/{key_id}")
    async def delete_dist_key(key_id: str):
        result = await db.distribution_keys.delete_one({"id": key_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Cle non trouvee")
        return {"message": "Cle supprimee"}

    # ---- INVOICES ----
    @router.get("/invoices")
    async def list_invoices(status: Optional[str] = None):
        query = {}
        if status:
            query["status"] = status
        invoices = await db.invoices.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return invoices

    @router.post("/invoices")
    async def create_invoice(data: InvoiceInput):
        # Compute distribution lines if key provided
        distribution_lines = []
        if data.distribution_key_id:
            key = await db.distribution_keys.find_one({"id": data.distribution_key_id}, {"_id": 0})
            if key:
                total_shares = sum(l["share"] for l in key["lots"]) if key["lots"] else 1
                for lot_entry in key["lots"]:
                    owner = await db.lots.find_one({"id": lot_entry["lot_id"]}, {"_id": 0})
                    owner_name = ""
                    if owner and owner.get("owner_id"):
                        owner_doc = await db.owners.find_one({"id": owner["owner_id"]}, {"_id": 0})
                        owner_name = owner_doc["name"] if owner_doc else ""
                    share_ratio = lot_entry["share"] / total_shares if total_shares > 0 else 0
                    distribution_lines.append({
                        "lot_id": lot_entry["lot_id"],
                        "lot_number": lot_entry["lot_number"],
                        "owner_name": owner_name,
                        "share": lot_entry["share"],
                        "amount": round(data.total_amount * share_ratio, 2)
                    })

        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "date": data.date,
            "due_date": data.due_date,
            "supplier": data.supplier,
            "description": data.description,
            "total_amount": data.total_amount,
            "vat_amount": data.vat_amount,
            "account_number": data.account_number,
            "distribution_key_id": data.distribution_key_id,
            "distribution_lines": distribution_lines,
            "status": data.status,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.invoices.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        return inv

    @router.put("/invoices/{invoice_id}")
    async def update_invoice(invoice_id: str, data: InvoiceInput):
        update = {
            "number": data.number, "date": data.date, "due_date": data.due_date,
            "supplier": data.supplier, "description": data.description,
            "total_amount": data.total_amount, "vat_amount": data.vat_amount,
            "account_number": data.account_number, "distribution_key_id": data.distribution_key_id,
            "status": data.status
        }
        result = await db.invoices.update_one({"id": invoice_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        return await db.invoices.find_one({"id": invoice_id}, {"_id": 0})

    @router.delete("/invoices/{invoice_id}")
    async def delete_invoice(invoice_id: str):
        result = await db.invoices.delete_one({"id": invoice_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        return {"message": "Facture supprimee"}

    return router
