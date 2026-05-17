from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import uuid
from auto_entries import generate_purchase_entry, _delete_auto_entries

INVOICE_ATTACHMENTS_DIR = Path("/app/uploads/invoice_attachments")
INVOICE_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


class DistKeyLot(BaseModel):
    lot_id: str
    lot_number: str
    share: float


class DistKeyInput(BaseModel):
    name: str
    description: Optional[str] = ""
    key_type: Optional[str] = "quotity"  # quotity, equal, custom
    lots: Optional[List[DistKeyLot]] = []
    copropriete_id: Optional[str] = ""


class InvoiceInput(BaseModel):
    number: str
    date: str
    due_date: Optional[str] = ""
    supplier: str
    description: str
    total_amount: float
    vat_amount: Optional[float] = 0.0
    account_number: Optional[str] = ""
    expense_category_id: Optional[str] = ""
    distribution_key_id: Optional[str] = ""
    status: Optional[str] = "unpaid"
    copropriete_id: Optional[str] = ""
    # Frais privatifs: la facture est imputee a UN seul proprietaire
    # via le compte 643. Si is_private_fee=true, distribution_key_id est ignore
    # et account_number force a 643.
    is_private_fee: Optional[bool] = False
    private_fee_owner_id: Optional[str] = ""


def create_invoices_router(db):
    router = APIRouter(prefix="/api")

    # ---- DISTRIBUTION KEYS ----
    @router.get("/distribution-keys")
    async def list_dist_keys(copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        keys = await db.distribution_keys.find(q, {"_id": 0}).sort("name", 1).to_list(1000)
        return keys

    @router.post("/distribution-keys")
    async def create_dist_key(data: DistKeyInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "description": data.description,
            "key_type": data.key_type,
            "lots": [l.model_dump() for l in data.lots],
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.distribution_keys.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/distribution-keys/{key_id}/usage")
    async def dist_key_usage(key_id: str):
        """Return list of resources linked to this distribution key."""
        invoices = await db.invoices.find({"distribution_key_id": key_id}, {"_id": 0, "id": 1, "number": 1, "date": 1, "supplier": 1, "total_amount": 1}).to_list(10000)
        budgets = await db.budgets.find({"lines.distribution_key_id": key_id}, {"_id": 0, "id": 1, "name": 1, "status": 1}).to_list(1000)
        fund_calls = await db.fund_calls.find({"distribution_key_id": key_id}, {"_id": 0, "id": 1, "name": 1, "date": 1, "total_amount": 1}).to_list(1000)
        return {
            "invoices": invoices,
            "budgets": budgets,
            "fund_calls": fund_calls,
            "total": len(invoices) + len(budgets) + len(fund_calls),
        }

    @router.put("/distribution-keys/{key_id}")
    async def update_dist_key(key_id: str, data: DistKeyInput, force: Optional[bool] = False):
        """Update a key. If `force=true`, detach all invoices using this key first.
        Otherwise returns 409 if invoices are linked."""
        existing = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Cle non trouvee")
        linked_inv = await db.invoices.count_documents({"distribution_key_id": key_id})
        if linked_inv > 0 and not force:
            raise HTTPException(
                409,
                f"{linked_inv} facture(s) utilisent cette cle. Detachez-les avant ou utilisez ?force=true",
            )
        update = {
            "name": data.name, "description": data.description,
            "key_type": data.key_type, "lots": [l.model_dump() for l in data.lots]
        }
        await db.distribution_keys.update_one({"id": key_id}, {"$set": update})
        if force and linked_inv > 0:
            # Detach: set distribution_key_id="" and clear distribution_lines
            await db.invoices.update_many(
                {"distribution_key_id": key_id},
                {"$set": {"distribution_key_id": "", "distribution_lines": []}}
            )
        return {"updated": True, "detached_invoices": linked_inv if force else 0,
                "key": await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})}

    @router.delete("/distribution-keys/{key_id}")
    async def delete_dist_key(key_id: str):
        linked_inv = await db.invoices.count_documents({"distribution_key_id": key_id})
        if linked_inv > 0:
            raise HTTPException(400, f"{linked_inv} facture(s) liees - detachez-les d'abord")
        result = await db.distribution_keys.delete_one({"id": key_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Cle non trouvee")
        return {"message": "Cle supprimee"}

    # ---- INVOICES ----
    @router.get("/invoices")
    async def list_invoices(status: Optional[str] = None, copropriete_id: Optional[str] = None):
        query = {}
        if status:
            query["status"] = status
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        invoices = await db.invoices.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return invoices

    @router.post("/invoices")
    async def create_invoice(data: InvoiceInput):
        # If expense_category_id provided, derive/override account_number
        account_number = data.account_number
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
        # Frais privatif: force compte 643, ignore distribution_key
        if data.is_private_fee:
            if not data.private_fee_owner_id:
                raise HTTPException(400, "Un proprietaire doit etre selectionne pour un frais privatif")
            owner = await db.owners.find_one({"id": data.private_fee_owner_id}, {"_id": 0})
            if not owner:
                raise HTTPException(404, "Proprietaire non trouve")
            account_number = "643"
        # Compute distribution lines if key provided (skipped for private fees)
        distribution_lines = []
        if data.distribution_key_id and not data.is_private_fee:
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
            "account_number": account_number,
            "expense_category_id": data.expense_category_id or "",
            "distribution_key_id": "" if data.is_private_fee else data.distribution_key_id,
            "distribution_lines": distribution_lines,
            "status": data.status,
            "copropriete_id": data.copropriete_id or "",
            "is_private_fee": bool(data.is_private_fee),
            "private_fee_owner_id": data.private_fee_owner_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.invoices.insert_one(doc)
        clean = {k: v for k, v in doc.items() if k != "_id"}
        try:
            await generate_purchase_entry(db, clean)
        except Exception as e:
            print(f"[auto-entry] purchase failed: {e}")
        return clean

    @router.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        return inv

    @router.put("/invoices/{invoice_id}")
    async def update_invoice(invoice_id: str, data: InvoiceInput):
        account_number = data.account_number
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
        if data.is_private_fee:
            if not data.private_fee_owner_id:
                raise HTTPException(400, "Un proprietaire doit etre selectionne pour un frais privatif")
            owner = await db.owners.find_one({"id": data.private_fee_owner_id}, {"_id": 0})
            if not owner:
                raise HTTPException(404, "Proprietaire non trouve")
            account_number = "643"
        update = {
            "number": data.number, "date": data.date, "due_date": data.due_date,
            "supplier": data.supplier, "description": data.description,
            "total_amount": data.total_amount, "vat_amount": data.vat_amount,
            "account_number": account_number,
            "expense_category_id": data.expense_category_id or "",
            "distribution_key_id": "" if data.is_private_fee else data.distribution_key_id,
            "status": data.status,
            "is_private_fee": bool(data.is_private_fee),
            "private_fee_owner_id": data.private_fee_owner_id or "",
        }
        # If switching to private fee, clear distribution_lines
        if data.is_private_fee:
            update["distribution_lines"] = []
        result = await db.invoices.update_one({"id": invoice_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        try:
            await generate_purchase_entry(db, inv)
        except Exception as e:
            print(f"[auto-entry] purchase update failed: {e}")
        return inv

    @router.delete("/invoices/{invoice_id}")
    async def delete_invoice(invoice_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if inv:
            for att in inv.get("attachments", []) or []:
                try:
                    p = att.get("stored_path")
                    if p and Path(p).exists():
                        Path(p).unlink()
                except Exception:
                    pass
            try:
                await _delete_auto_entries(db, "invoice", invoice_id)
            except Exception:
                pass
        result = await db.invoices.delete_one({"id": invoice_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        return {"message": "Facture supprimee"}

    # ---- INVOICE ATTACHMENTS ----
    @router.post("/invoices/{invoice_id}/attachments")
    async def upload_invoice_attachment(invoice_id: str, file: UploadFile = File(...)):
        """Attach a PDF or image scan of the supplier invoice."""
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        ext = Path(file.filename or "file").suffix.lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "Format autorise: PDF, PNG, JPG")
        att_id = str(uuid.uuid4())
        stored_name = f"{att_id}{ext}"
        stored_path = INVOICE_ATTACHMENTS_DIR / stored_name
        content = await file.read()
        with open(stored_path, "wb") as f:
            f.write(content)
        attachment = {
            "id": att_id,
            "filename": file.filename,
            "stored_path": str(stored_path),
            "mime_type": file.content_type or "application/octet-stream",
            "size": len(content),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$push": {"attachments": attachment}}
        )
        return attachment

    @router.get("/invoices/{invoice_id}/attachments/{attachment_id}/download")
    async def download_invoice_attachment(invoice_id: str, attachment_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        for att in inv.get("attachments", []) or []:
            if att.get("id") == attachment_id:
                path = att.get("stored_path", "")
                if not path or not Path(path).exists():
                    raise HTTPException(404, "Fichier introuvable")
                return FileResponse(path, media_type=att.get("mime_type", "application/pdf"),
                                    filename=att.get("filename", "facture.pdf"))
        raise HTTPException(404, "Piece jointe non trouvee")

    @router.delete("/invoices/{invoice_id}/attachments/{attachment_id}")
    async def delete_invoice_attachment(invoice_id: str, attachment_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        target = None
        for att in inv.get("attachments", []) or []:
            if att.get("id") == attachment_id:
                target = att
                break
        if not target:
            raise HTTPException(404, "Piece jointe non trouvee")
        try:
            p = target.get("stored_path")
            if p and Path(p).exists():
                Path(p).unlink()
        except Exception:
            pass
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$pull": {"attachments": {"id": attachment_id}}}
        )
        return {"message": "Piece jointe supprimee"}

    return router
