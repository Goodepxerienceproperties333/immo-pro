from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import uuid
import json
import shutil
from auto_entries import generate_purchase_entry, _delete_auto_entries

INVOICE_ATTACHMENTS_DIR = Path("/app/uploads/invoice_attachments")
INVOICE_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
INVOICE_BUNDLES_DIR = Path("/app/uploads/invoice_attachments/_bundles")
INVOICE_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)


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
    # Repartition occupant/proprietaire (pour decompte locataire).
    # Defaut : herite de la catégorie de dépense si non fourni.
    # Somme doit etre 100.
    occupant_pct: Optional[float] = None  # None = inherit from category
    proprietaire_pct: Optional[float] = None


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
    async def list_invoices(
        request: Request,
        status: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        supplier: Optional[str] = None,
        reference: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ):
        """Chinese walls STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id). Sans scope ACP -> liste vide.
        Filtres optionnels : status, supplier (regex insensible), reference
        (regex insensible), start_date/end_date (ISO YYYY-MM-DD), min/max amount.
        Note: `date_from` et `date_to` sont des alias pour `start_date`/`end_date`."""
        # Alias compat
        start_date = start_date or date_from
        end_date = end_date or date_to
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        query = {"copropriete_id": copropriete_id}
        if status:
            query["status"] = status
        if supplier:
            import re as _re
            query["supplier"] = {"$regex": _re.escape(supplier), "$options": "i"}
        if reference:
            import re as _re2
            query["number"] = {"$regex": _re2.escape(reference), "$options": "i"}
        if start_date or end_date:
            query["date"] = {}
            if start_date:
                query["date"]["$gte"] = start_date
            if end_date:
                query["date"]["$lte"] = end_date
        if min_amount is not None or max_amount is not None:
            query["total_amount"] = {}
            if min_amount is not None:
                query["total_amount"]["$gte"] = float(min_amount)
            if max_amount is not None:
                query["total_amount"]["$lte"] = float(max_amount)
        invoices = await db.invoices.find(query, {"_id": 0}).sort("date", -1).to_list(2000)
        return invoices

    @router.post("/invoices")
    async def create_invoice(data: InvoiceInput):
        from fiscal_lock import ensure_period_open
        # Verrou fiscal : la date de la facture doit etre dans une periode ouverte
        await ensure_period_open(db, data.copropriete_id or "", data.date, context="facture")
        # If expense_category_id provided, derive/override account_number
        account_number = data.account_number
        cat_default_occupant = None
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
            if cat:
                cat_default_occupant = float(cat.get("default_occupant_pct") or 0)
        # Resoudre occupant_pct : valeur fournie ou heritee de la categorie, sinon 0%
        occupant_pct = data.occupant_pct if data.occupant_pct is not None else (cat_default_occupant or 0.0)
        occupant_pct = max(0.0, min(100.0, float(occupant_pct)))
        proprietaire_pct = round(100.0 - occupant_pct, 2)
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

        # Generer une reference interne auto-incrementee par ACP (FA-YYYY-NNNN)
        year = (data.date or datetime.now(timezone.utc).date().isoformat())[:4]
        prefix = f"FA-{year}-"
        cnt = await db.invoices.count_documents({
            "copropriete_id": data.copropriete_id or "",
            "internal_reference": {"$regex": f"^{prefix}"}
        })
        internal_reference = f"{prefix}{(cnt + 1):04d}"
        # Eviter doublon en cas de concurrence
        while await db.invoices.find_one({"copropriete_id": data.copropriete_id or "", "internal_reference": internal_reference}, {"_id": 0, "id": 1}):
            cnt += 1
            internal_reference = f"{prefix}{(cnt + 1):04d}"

        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "internal_reference": internal_reference,
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
            # Repartition occupant/proprietaire pour decompte locataire
            "occupant_pct": occupant_pct,
            "proprietaire_pct": proprietaire_pct,
            "occupant_amount": round(data.total_amount * occupant_pct / 100, 2),
            "proprietaire_amount": round(data.total_amount * proprietaire_pct / 100, 2),
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
        from fiscal_lock import ensure_period_open
        existing_for_lock = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if existing_for_lock:
            # Verrou : la date d'origine ET la nouvelle doivent etre dans un exercice ouvert
            await ensure_period_open(db, existing_for_lock.get("copropriete_id", ""), existing_for_lock.get("date"), context="facture")
        await ensure_period_open(db, data.copropriete_id or (existing_for_lock or {}).get("copropriete_id", ""), data.date, context="facture")
        account_number = data.account_number
        cat_default_occupant = None
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
            if cat:
                cat_default_occupant = float(cat.get("default_occupant_pct") or 0)
        # Si occupant_pct fourni : on l'utilise. Sinon : on garde l'existant
        # (et si pas d'existant : on prend la categorie ou 0%)
        existing = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if data.occupant_pct is not None:
            occupant_pct = float(data.occupant_pct)
        elif existing and existing.get("occupant_pct") is not None:
            occupant_pct = float(existing.get("occupant_pct") or 0)
        else:
            occupant_pct = cat_default_occupant or 0.0
        occupant_pct = max(0.0, min(100.0, occupant_pct))
        proprietaire_pct = round(100.0 - occupant_pct, 2)
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
            "occupant_pct": occupant_pct,
            "proprietaire_pct": proprietaire_pct,
            "occupant_amount": round(data.total_amount * occupant_pct / 100, 2),
            "proprietaire_amount": round(data.total_amount * proprietaire_pct / 100, 2),
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
        from fiscal_lock import ensure_period_open
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        # Verrou : refuse la suppression si la facture est dans un exercice cloture
        await ensure_period_open(db, inv.get("copropriete_id", ""), inv.get("date"), context="facture")
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

    # ---- INVOICE BUNDLE (Regroupement de PDFs Optipro) ----
    @router.post("/invoices/bundle-analyze")
    async def bundle_analyze(
        request: Request,
        file: UploadFile = File(...),
        copropriete_id: Optional[str] = Form(None),
    ):
        """Etape 1 : Recoit un PDF "Regroupement de documents" (factures concatenees).
        Detecte chaque facture (page_range), extrait les metadonnees et propose un
        matching avec les factures existantes de l'ACP.
        Retourne un session_id (qui pointe sur le PDF stocke temporairement) +
        la liste des blocks avec un suggested_match optionnel."""
        from import_wizard.pdf_invoices_bundle import parse_invoice_bundle, match_invoice

        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or ""
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis pour l'analyse du bundle")

        ext = Path(file.filename or "bundle.pdf").suffix.lower()
        if ext != ".pdf":
            raise HTTPException(400, "Format autorise : PDF uniquement")

        raw = await file.read()
        if not raw or len(raw) < 100:
            raise HTTPException(400, "Fichier vide ou invalide")

        # Parse PDF (slow operation, can take 10s for 100+ pages)
        try:
            info = parse_invoice_bundle(raw)
        except Exception as e:
            raise HTTPException(500, f"Echec analyse PDF : {e}")

        # Persist bundle file for later page extraction
        session_id = str(uuid.uuid4())
        session_dir = INVOICE_BUNDLES_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = session_dir / "bundle.pdf"
        with open(bundle_path, "wb") as f:
            f.write(raw)

        # Load candidate invoices for this ACP (no need for attachments in matcher)
        invoices = await db.invoices.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "id": 1, "number": 1, "internal_reference": 1,
             "supplier": 1, "total_amount": 1, "date": 1, "attachments": 1},
        ).sort("date", -1).to_list(5000)
        # Map to matcher candidate format (supplier_name expected)
        cands = [{
            "id": inv["id"],
            "number": inv.get("number") or "",
            "internal_reference": inv.get("internal_reference") or "",
            "supplier_name": inv.get("supplier") or "",
            "total_amount": float(inv.get("total_amount") or 0),
            "date": inv.get("date") or "",
            "has_attachment": bool(inv.get("attachments")),
        } for inv in invoices]

        blocks_out = []
        for i, blk in enumerate(info.get("blocks", [])):
            match = match_invoice(blk, cands)
            blocks_out.append({
                "block_id": f"blk-{i}",
                "page_range": blk.get("page_range", []),
                "page_count": blk.get("page_count", 0),
                "invoice_number": blk.get("invoice_number", ""),
                "date_iso": blk.get("date_iso", ""),
                "date_display": blk.get("date_display", ""),
                "supplier_hint": blk.get("supplier_hint", ""),
                "supplier_tva": blk.get("supplier_tva", ""),
                "total_amount": blk.get("total_amount", 0.0),
                "raw_text_preview": blk.get("raw_text_preview", ""),
                "suggested_match": (
                    {
                        "invoice_id": match["id"],
                        "invoice_number": match.get("number"),
                        "internal_reference": match.get("internal_reference"),
                        "supplier": match.get("supplier_name"),
                        "total_amount": match.get("total_amount"),
                        "date": match.get("date"),
                        "has_attachment": match.get("has_attachment", False),
                        "confidence": match.get("match_confidence", 0),
                        "method": match.get("match_method", ""),
                    } if match else None
                ),
            })

        # Persist session metadata for the commit step
        meta = {
            "session_id": session_id,
            "copropriete_id": copropriete_id,
            "total_pages": info.get("total_pages", 0),
            "invoice_count": info.get("invoice_count", 0),
            "blocks": blocks_out,
            "filename": file.filename or "bundle.pdf",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(session_dir / "meta.json", "w") as f:
            json.dump(meta, f)

        return {
            "session_id": session_id,
            "total_pages": info.get("total_pages", 0),
            "invoice_count": info.get("invoice_count", 0),
            "blocks": blocks_out,
            "filename": file.filename or "bundle.pdf",
        }

    @router.post("/invoices/bundle-commit")
    async def bundle_commit(payload: dict):
        """Etape 2 : Pour chaque block, extrait les pages correspondantes du PDF
        bundle et les attache a la facture choisie (ou cree une nouvelle facture
        si mode='create').
        Body : {
          session_id: str,
          assignments: [
            { block_id, page_range, mode: 'attach'|'create'|'skip',
              invoice_id?: str,  # mode=attach
              invoice_data?: {...}  # mode=create (full InvoiceInput payload)
            }, ...
          ]
        }"""
        from import_wizard.pdf_invoices_bundle import extract_block_pdf
        from fiscal_lock import ensure_period_open

        session_id = payload.get("session_id") or ""
        assignments = payload.get("assignments") or []
        if not session_id or not assignments:
            raise HTTPException(400, "session_id et assignments requis")

        session_dir = INVOICE_BUNDLES_DIR / session_id
        bundle_path = session_dir / "bundle.pdf"
        meta_path = session_dir / "meta.json"
        if not bundle_path.exists() or not meta_path.exists():
            raise HTTPException(404, "Session bundle introuvable ou expiree")

        with open(meta_path) as f:
            meta = json.load(f)
        copropriete_id = meta.get("copropriete_id", "")
        with open(bundle_path, "rb") as f:
            raw = f.read()

        attached = 0
        created = 0
        skipped = 0
        errors = []
        results = []

        for assignment in assignments:
            block_id = assignment.get("block_id", "")
            page_range = assignment.get("page_range") or []
            mode = assignment.get("mode", "skip")

            if mode == "skip":
                skipped += 1
                results.append({"block_id": block_id, "status": "skipped"})
                continue

            if not page_range:
                errors.append({"block_id": block_id, "error": "page_range vide"})
                continue

            invoice_id = assignment.get("invoice_id") or ""
            try:
                # Mode 'create' : create the invoice first using InvoiceInput
                if mode == "create":
                    inv_data = assignment.get("invoice_data") or {}
                    if not inv_data.get("number") or not inv_data.get("date") or not inv_data.get("supplier"):
                        errors.append({"block_id": block_id, "error": "Donnees facture incompletes (number, date, supplier requis)"})
                        continue
                    inv_data["copropriete_id"] = inv_data.get("copropriete_id") or copropriete_id
                    # Build InvoiceInput-compatible payload
                    try:
                        invoice_input = InvoiceInput(**inv_data)
                    except Exception as e:
                        errors.append({"block_id": block_id, "error": f"Validation : {e}"})
                        continue
                    new_inv = await create_invoice(invoice_input)
                    invoice_id = new_inv["id"]
                    created += 1

                if not invoice_id:
                    errors.append({"block_id": block_id, "error": "invoice_id manquant"})
                    continue

                inv = await db.invoices.find_one({"id": invoice_id, "copropriete_id": copropriete_id}, {"_id": 0})
                if not inv:
                    errors.append({"block_id": block_id, "error": "Facture introuvable dans l'ACP"})
                    continue

                # Verrou fiscal : ne pas attacher dans un exercice cloture
                try:
                    await ensure_period_open(db, inv.get("copropriete_id", ""), inv.get("date"), context="piece jointe")
                except Exception as e:
                    errors.append({"block_id": block_id, "error": f"Exercice cloture : {e}"})
                    continue

                # Extract pages and save as new attachment
                pdf_bytes = extract_block_pdf(raw, page_range)
                att_id = str(uuid.uuid4())
                stored_name = f"{att_id}.pdf"
                stored_path = INVOICE_ATTACHMENTS_DIR / stored_name
                with open(stored_path, "wb") as f:
                    f.write(pdf_bytes)
                pages_label = f"p{page_range[0]}-{page_range[-1]}" if len(page_range) > 1 else f"p{page_range[0]}"
                attachment = {
                    "id": att_id,
                    "filename": f"bundle-{pages_label}.pdf",
                    "stored_path": str(stored_path),
                    "mime_type": "application/pdf",
                    "size": len(pdf_bytes),
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "bundle",
                    "bundle_session_id": session_id,
                    "bundle_page_range": page_range,
                }
                await db.invoices.update_one(
                    {"id": invoice_id},
                    {"$push": {"attachments": attachment}},
                )
                attached += 1
                results.append({
                    "block_id": block_id,
                    "status": "created_and_attached" if mode == "create" else "attached",
                    "invoice_id": invoice_id,
                    "attachment_id": att_id,
                })
            except HTTPException as e:
                errors.append({"block_id": block_id, "error": e.detail})
            except Exception as e:
                errors.append({"block_id": block_id, "error": str(e)})

        # Cleanup session if all assignments are processed (no rollback strategy
        # needed: each block is independent). Only remove the bundle PDF, keep
        # meta.json for audit until manual cleanup.
        try:
            shutil.rmtree(session_dir)
        except Exception:
            pass

        return {
            "attached": attached,
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    return router
