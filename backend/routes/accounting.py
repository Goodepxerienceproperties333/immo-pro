from fastapi import APIRouter, HTTPException, UploadFile, File, Request
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
    class_num: Optional[int] = None  # auto-derived from first digit if absent
    parent: Optional[str] = None
    type: Optional[str] = None  # auto-derived (balance for 1-5, result for 6-7)
    copropriete_id: Optional[str] = ""
    active: Optional[bool] = True  # custom accounts default to active


class PCMNToggleInput(BaseModel):
    active: bool


class PCMNUpdateInput(BaseModel):
    name: Optional[str] = None
    class_num: Optional[int] = None
    parent: Optional[str] = None
    type: Optional[str] = None
    active: Optional[bool] = None
    copropriete_id: Optional[str] = ""


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
        if not data.number or not data.number.isdigit():
            raise HTTPException(400, "Le numero de compte doit etre numerique")
        q = {"number": data.number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        existing = await db.pcmn_accounts.find_one(q)
        if existing:
            raise HTTPException(400, "Ce numero de compte existe deja dans cette ACP")
        class_num = data.class_num if data.class_num else int(data.number[0])
        typ = data.type or ("balance" if class_num <= 5 else "result")
        doc = {
            "number": data.number,
            "name": data.name,
            "class_num": class_num,
            "parent": data.parent,
            "type": typ,
            "copropriete_id": data.copropriete_id or "",
            "active": data.active if data.active is not None else True,
            "is_custom": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.pcmn_accounts.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/pcmn/{number}")
    async def update_pcmn_account(number: str, data: PCMNUpdateInput):
        q = {"number": number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        update_doc = {}
        for fld in ("name", "class_num", "parent", "type", "active"):
            val = getattr(data, fld)
            if val is not None:
                update_doc[fld] = val
        if not update_doc:
            raise HTTPException(400, "Rien a modifier")
        result = await db.pcmn_accounts.update_one(q, {"$set": update_doc})
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
    async def delete_pcmn_account(number: str, copropriete_id: Optional[str] = None, force: Optional[bool] = False):
        q = {"number": number}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        # Trouver le compte pour verifier son statut
        acc = await db.pcmn_accounts.find_one(q, {"_id": 0})
        if not acc:
            raise HTTPException(404, "Compte non trouve")
        # Protections
        if acc.get("is_tier_account"):
            raise HTTPException(400, "Ce compte tiers (auto) ne peut etre supprime. Supprimez le tiers concerne.")
        if not force and not acc.get("is_custom"):
            raise HTTPException(400, "Ce compte fait partie du PCMN officiel. Utilisez ?force=true pour supprimer quand meme.")
        # Verifier qu'il n'est pas utilise
        copro_filter = {"copropriete_id": copropriete_id} if copropriete_id else {}
        used_entries = await db.journal_entries.count_documents(
            {**copro_filter, "lines.account_number": number}
        )
        used_invoices = await db.invoices.count_documents(
            {**copro_filter, "$or": [{"account_number": number}, {"lines.account_number": number}]}
        )
        used_cats = await db.expense_categories.count_documents(
            {**copro_filter, "account_number": number}
        )
        if used_entries + used_invoices + used_cats > 0:
            raise HTTPException(
                409,
                f"Compte utilise: {used_entries} ecriture(s), {used_invoices} facture(s), {used_cats} nature(s) de depense"
            )
        await db.pcmn_accounts.delete_one(q)
        return {"message": "Compte supprime", "number": number}

    # ---- JOURNAL ENTRIES ----
    @router.get("/entries")
    async def list_entries(
        request: Request,
        journal_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        search: Optional[str] = None,
        reference: Optional[str] = None,
        account_number: Optional[str] = None,
    ):
        """Chinese walls STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id). Sans scope ACP -> liste vide."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        query = {"copropriete_id": copropriete_id}
        if journal_type:
            query["journal_type"] = journal_type
        if date_from:
            query["date"] = {"$gte": date_from}
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to
        if reference:
            import re as _re
            query["reference"] = {"$regex": _re.escape(reference), "$options": "i"}
        if search:
            import re as _re2
            rgx = {"$regex": _re2.escape(search), "$options": "i"}
            query["$or"] = [{"description": rgx}, {"reference": rgx}, {"lines.account_name": rgx}, {"lines.third_party_name": rgx}]
        if account_number:
            query["lines.account_number"] = account_number
        entries = await db.journal_entries.find(query, {"_id": 0}).sort("date", -1).to_list(2000)
        return entries

    @router.post("/entries")
    async def create_entry(data: JournalEntryInput, request: Request):
        copro_id = (data.copropriete_id or "").strip() or (request.headers.get("X-Copropriete-Id") or "").strip()
        if not copro_id or copro_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            raise HTTPException(400, f"Ecriture non equilibree: Debit={total_debit:.2f}, Credit={total_credit:.2f}")
        # Verify each account_number actually belongs to the ACP's PCMN (no leak)
        accs_used = {l.account_number for l in data.lines if l.account_number}
        if accs_used:
            existing = await db.pcmn_accounts.find(
                {"copropriete_id": copro_id, "number": {"$in": list(accs_used)}}, {"_id": 0, "number": 1}
            ).to_list(2000)
            existing_set = {p["number"] for p in existing}
            missing = accs_used - existing_set
            if missing:
                raise HTTPException(
                    400,
                    f"Comptes PCMN absents de cette ACP : {sorted(missing)}. "
                    "Chaque ACP dispose de son propre plan comptable - aucun melange autorise."
                )
        doc = {
            "id": str(uuid.uuid4()),
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": [l.model_dump() for l in data.lines],
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "copropriete_id": copro_id,
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
        existing = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Ecriture non trouvee")
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            raise HTTPException(400, "Ecriture non equilibree")
        update = {
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": [l.model_dump() for l in data.lines],
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
        }
        # Mark as manually edited if originally auto-generated
        if existing.get("auto_generated"):
            update["manually_edited"] = True
            update["manually_edited_at"] = datetime.now(timezone.utc).isoformat()
        await db.journal_entries.update_one({"id": entry_id}, {"$set": update})
        return await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})

    @router.delete("/entries/{entry_id}")
    async def delete_entry(entry_id: str):
        # Also remove attachment files from disk
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if entry and entry.get("auto_generated") and not entry.get("manually_edited"):
            raise HTTPException(400, "Ecriture auto-generee - supprimez la source (facture, appel, banque) ou modifiez-la d'abord pour la detacher")
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
