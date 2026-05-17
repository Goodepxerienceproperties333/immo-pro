from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import uuid
import os

ATTACHMENTS_DIR = Path("/app/uploads/journal_attachments")
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


class JournalEntryLine(BaseModel):
    account_number: str
    account_name: Optional[str] = ""
    debit: Optional[float] = 0.0
    credit: Optional[float] = 0.0
    description: Optional[str] = ""


class JournalEntryInput(BaseModel):
    journal_type: str  # OD, AV, AP
    date: str
    reference: Optional[str] = ""
    description: str
    lines: List[JournalEntryLine]
    copropriete_id: Optional[str] = ""


class PCMNAccountInput(BaseModel):
    number: str
    name: str
    class_num: int
    parent: Optional[str] = None
    type: Optional[str] = "balance"
    copropriete_id: Optional[str] = ""
    active: Optional[bool] = False


class PCMNToggleInput(BaseModel):
    active: bool


def create_accounting_router(db):
    router = APIRouter(prefix="/api/accounting")

    # ---- PCMN ----
    @router.get("/pcmn")
    async def list_pcmn(search: Optional[str] = None, class_num: Optional[int] = None,
                        copropriete_id: Optional[str] = None, only_active: Optional[bool] = False):
        query = {}
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        if only_active:
            query["active"] = True
        if search:
            query["$or"] = [
                {"number": {"$regex": search, "$options": "i"}},
                {"name": {"$regex": search, "$options": "i"}}
            ]
        if class_num is not None:
            query["class_num"] = class_num
        accounts = await db.pcmn_accounts.find(query, {"_id": 0}).sort("number", 1).to_list(1000)
        return accounts

    @router.post("/pcmn")
    async def create_pcmn_account(data: PCMNAccountInput):
        q = {"number": data.number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        existing = await db.pcmn_accounts.find_one(q)
        if existing:
            raise HTTPException(400, "Ce numero de compte existe deja dans cette ACP")
        doc = {
            "number": data.number,
            "name": data.name,
            "class_num": data.class_num,
            "parent": data.parent,
            "type": data.type,
            "copropriete_id": data.copropriete_id or "",
            "active": data.active if data.active is not None else False,
        }
        await db.pcmn_accounts.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/pcmn/{number}")
    async def update_pcmn_account(number: str, data: PCMNAccountInput):
        q = {"number": number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        result = await db.pcmn_accounts.update_one(
            q,
            {"$set": {"name": data.name, "class_num": data.class_num, "parent": data.parent,
                      "type": data.type, "active": data.active if data.active is not None else False}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Compte non trouve")
        return await db.pcmn_accounts.find_one(q, {"_id": 0})

    @router.patch("/pcmn/{number}/toggle-active")
    async def toggle_pcmn_active(number: str, data: PCMNToggleInput, copropriete_id: Optional[str] = None):
        q = {"number": number}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        result = await db.pcmn_accounts.update_one(q, {"$set": {"active": data.active}})
        if result.matched_count == 0:
            raise HTTPException(404, "Compte non trouve")
        return await db.pcmn_accounts.find_one(q, {"_id": 0})

    @router.delete("/pcmn/{number}")
    async def delete_pcmn_account(number: str, copropriete_id: Optional[str] = None):
        q = {"number": number}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        result = await db.pcmn_accounts.delete_one(q)
        if result.deleted_count == 0:
            raise HTTPException(404, "Compte non trouve")
        return {"message": "Compte supprime"}

    # ---- JOURNAL ENTRIES ----
    @router.get("/entries")
    async def list_entries(journal_type: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        query = {}
        if journal_type:
            query["journal_type"] = journal_type
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        if date_from:
            query["date"] = {"$gte": date_from}
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to
        entries = await db.journal_entries.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return entries

    @router.post("/entries")
    async def create_entry(data: JournalEntryInput):
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            raise HTTPException(400, f"Ecriture non equilibree: Debit={total_debit:.2f}, Credit={total_credit:.2f}")
        doc = {
            "id": str(uuid.uuid4()),
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": [l.model_dump() for l in data.lines],
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.journal_entries.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/entries/{entry_id}")
    async def get_entry(entry_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        return entry

    @router.put("/entries/{entry_id}")
    async def update_entry(entry_id: str, data: JournalEntryInput):
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            raise HTTPException(400, f"Ecriture non equilibree")
        update = {
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": [l.model_dump() for l in data.lines],
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
        }
        result = await db.journal_entries.update_one({"id": entry_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Ecriture non trouvee")
        return await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})

    @router.delete("/entries/{entry_id}")
    async def delete_entry(entry_id: str):
        # Also remove attachment files from disk
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if entry:
            for att in entry.get("attachments", []) or []:
                try:
                    p = att.get("stored_path")
                    if p and Path(p).exists():
                        Path(p).unlink()
                except Exception:
                    pass
        result = await db.journal_entries.delete_one({"id": entry_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Ecriture non trouvee")
        return {"message": "Ecriture supprimee"}

    # ---- ATTACHMENTS for journal entries ----
    @router.post("/entries/{entry_id}/attachments")
    async def upload_entry_attachment(entry_id: str, file: UploadFile = File(...)):
        """Attach a PDF document to a journal entry (Operations Diverses or any entry)."""
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        ext = Path(file.filename or "file").suffix.lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "Format autorise: PDF, PNG, JPG")
        att_id = str(uuid.uuid4())
        stored_name = f"{att_id}{ext}"
        stored_path = ATTACHMENTS_DIR / stored_name
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
        await db.journal_entries.update_one(
            {"id": entry_id},
            {"$push": {"attachments": attachment}}
        )
        return attachment

    @router.get("/entries/{entry_id}/attachments/{attachment_id}/download")
    async def download_entry_attachment(entry_id: str, attachment_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        for att in entry.get("attachments", []) or []:
            if att.get("id") == attachment_id:
                path = att.get("stored_path", "")
                if not path or not Path(path).exists():
                    raise HTTPException(404, "Fichier introuvable sur le disque")
                return FileResponse(path, media_type=att.get("mime_type", "application/pdf"),
                                    filename=att.get("filename", "attachment.pdf"))
        raise HTTPException(404, "Piece jointe non trouvee")

    @router.delete("/entries/{entry_id}/attachments/{attachment_id}")
    async def delete_entry_attachment(entry_id: str, attachment_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        target = None
        for att in entry.get("attachments", []) or []:
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
        await db.journal_entries.update_one(
            {"id": entry_id},
            {"$pull": {"attachments": {"id": attachment_id}}}
        )
        return {"message": "Piece jointe supprimee"}

    # ---- BALANCE / BILAN ----
    @router.get("/balance")
    async def get_balance(copropriete_id: Optional[str] = None):
        """Get trial balance (balance des comptes) scoped by ACP."""
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(10000)
        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "total_debit": 0, "total_credit": 0}
                balances[acc]["total_debit"] += line.get("debit", 0)
                balances[acc]["total_credit"] += line.get("credit", 0)
        result = []
        for acc in sorted(balances.keys()):
            b = balances[acc]
            b["balance"] = round(b["total_debit"] - b["total_credit"], 2)
            b["total_debit"] = round(b["total_debit"], 2)
            b["total_credit"] = round(b["total_credit"], 2)
            result.append(b)
        return result

    return router
