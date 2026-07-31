from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import uuid
import os
import json
import tempfile
from gridfs_storage import get_documents_storage


# Default categories created on ACP creation (mentioned by user)
DEFAULT_CATEGORIES = [
    "Reglement d'ordre interieur",
    "Acte de base",
    "Statuts",
    "PV d'AG",
    "Contrats",
    "Polices d'assurance",
    "Factures fournisseurs",
    "Decomptes",
    "Rapports techniques",
    "Autres",
]


# Legacy path - kept ONLY for backward-compat fallback reads (iter87 migration).
UPLOAD_DIR = Path("/app/uploads/documents")
try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


class CategoryInput(BaseModel):
    name: str
    description: Optional[str] = ""
    copropriete_id: Optional[str] = ""


class DocumentInput(BaseModel):
    title: str
    description: Optional[str] = ""
    category_id: Optional[str] = ""
    content: Optional[str] = ""
    copropriete_id: Optional[str] = ""


async def _extract_pdf_text(file_path: str, max_chars: int = 6000) -> str:
    """Extract text from PDF using pypdf. Returns up to max_chars chars."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            if len(text) > max_chars:
                break
        return text[:max_chars].strip()
    except Exception as e:
        print(f"[PDF extract failed]: {e}")
        return ""


async def _classify_with_ai(file_path: str, mime_type: str, available_categories: list) -> dict:
    """Classify a document using Claude Sonnet (text-only via PDF extraction).
    Returns {category, document_type, doc_date, summary, parties}.
    Falls back to empty dict on error - never blocks the upload."""
    try:
        # Only PDF supported for now (Claude is text-only via Emergent key)
        if mime_type != "application/pdf":
            return {}
        text = await _extract_pdf_text(file_path)
        if not text:
            return {}

        from emergentintegrations.llm.chat import LlmChat, UserMessage
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            return {}
        cats_str = ", ".join(f'"{c}"' for c in available_categories)
        chat = LlmChat(
            api_key=api_key,
            session_id=f"doc-{uuid.uuid4().hex[:8]}",
            system_message=(
                "You are a Belgian condominium document classifier. "
                f"Choose ONE category from this list: {cats_str}. "
                "Reply ONLY with strict JSON, no markdown, no code fences. Schema: "
                '{"category": "<exact name from list>", "document_type": "<short type>", '
                '"doc_date": "<YYYY-MM-DD or empty>", "summary": "<3-line max>", '
                '"parties": ["<party1>", "<party2>"]}. Use empty strings/arrays if unknown.'
            ),
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")

        msg = UserMessage(text=f"Classify this Belgian condominium document and extract metadata. Return only the JSON object.\n\nDocument content:\n{text}")
        response = await chat.send_message(msg)
        txt = response.strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1] if "```" in txt[3:] else txt[3:]
            if txt.startswith("json"):
                txt = txt[4:]
            txt = txt.strip("` \n")
        data = json.loads(txt)
        return data
    except Exception as e:
        print(f"[AI classification skipped]: {e}")
        return {}


def create_documents_router(db):
    router = APIRouter(prefix="/api/documents")

    # ---- CATEGORIES ----
    @router.get("/categories")
    async def list_categories(request: Request, copropriete_id: Optional[str] = None):
        from syndic_scope import syndic_query
        q = {**syndic_query(request)}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        cats = await db.document_categories.find(q, {"_id": 0}).sort("name", 1).to_list(100)
        return cats

    @router.post("/categories")
    async def create_category(data: CategoryInput, request: Request):
        from syndic_scope import inject_syndic
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "description": data.description,
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        inject_syndic(doc, request)
        await db.document_categories.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.post("/categories/seed-defaults")
    async def seed_default_categories(copropriete_id: str):
        """Create the default Belgian copropriete categories for a given ACP."""
        existing = await db.document_categories.find({"copropriete_id": copropriete_id}, {"_id": 0, "name": 1}).to_list(100)
        existing_names = {c["name"] for c in existing}
        to_create = []
        for name in DEFAULT_CATEGORIES:
            if name in existing_names:
                continue
            to_create.append({
                "id": str(uuid.uuid4()),
                "name": name,
                "description": "",
                "copropriete_id": copropriete_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        if to_create:
            await db.document_categories.insert_many(to_create)
        return {"created": len(to_create), "total": len(to_create) + len(existing_names)}

    @router.put("/categories/{cat_id}")
    async def update_category(cat_id: str, data: CategoryInput):
        result = await db.document_categories.update_one(
            {"id": cat_id}, {"$set": {"name": data.name, "description": data.description}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Categorie non trouvee")
        return await db.document_categories.find_one({"id": cat_id}, {"_id": 0})

    @router.delete("/categories/{cat_id}")
    async def delete_category(cat_id: str):
        result = await db.document_categories.delete_one({"id": cat_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Categorie non trouvee")
        return {"message": "Categorie supprimee"}

    # ---- DOCUMENTS ----
    @router.get("")
    async def list_documents(request: Request, category_id: Optional[str] = None, copropriete_id: Optional[str] = None):
        from syndic_scope import syndic_query
        query = {**syndic_query(request)}
        if category_id:
            query["category_id"] = category_id
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        docs = await db.documents.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
        return docs

    @router.post("")
    async def create_document(data: DocumentInput, request: Request):
        from syndic_scope import inject_syndic
        doc = {
            "id": str(uuid.uuid4()),
            "title": data.title,
            "description": data.description,
            "category_id": data.category_id,
            "content": data.content,
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        inject_syndic(doc, request)
        await db.documents.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.post("/upload")
    async def upload_document(
        file: UploadFile = File(...),
        title: Optional[str] = Form(""),
        description: Optional[str] = Form(""),
        category_id: Optional[str] = Form(""),
        copropriete_id: Optional[str] = Form(""),
        auto_classify: Optional[bool] = Form(True),
    ):
        """Upload a document file and optionally auto-classify it with AI.

        iter87 : binary stored in MongoDB GridFS bucket `documents` (persistent).
        AI classification still needs a local file path -> uses tempfile (cleaned).
        """
        ext = Path(file.filename or "file").suffix.lower()
        if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"}:
            raise HTTPException(400, "Format non supporte. PDF ou image uniquement.")
        doc_id = str(uuid.uuid4())
        stored_name = f"{doc_id}{ext}"
        content = await file.read()

        # Upload to GridFS first
        docs_storage = get_documents_storage(db)
        gridfs_id = await docs_storage.upload(
            filename=file.filename or stored_name,
            contents=content,
            metadata={
                "document_id": doc_id,
                "copropriete_id": copropriete_id or "",
                "category_id": category_id or "",
                "mime_type": file.content_type or "application/octet-stream",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Optional AI classification (uses a temp file because the AI helper
        # expects a file path - we delete it immediately after).
        ai_result = {}
        if auto_classify and copropriete_id:
            mime_map = {
                ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".heic": "image/heic", ".heif": "image/heif",
            }
            mime_type = mime_map.get(ext, "application/octet-stream")
            cats = await db.document_categories.find({"copropriete_id": copropriete_id}, {"_id": 0, "id": 1, "name": 1}).to_list(100)
            if cats:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                try:
                    tmp.write(content)
                    tmp.flush()
                    tmp.close()
                    ai_result = await _classify_with_ai(tmp.name, mime_type, [c["name"] for c in cats])
                finally:
                    try:
                        os.unlink(tmp.name)
                    except Exception:
                        pass
                # Map AI category name to category_id
                if ai_result.get("category") and not category_id:
                    matched = next((c for c in cats if c["name"].lower() == ai_result["category"].lower()), None)
                    if matched:
                        category_id = matched["id"]

        doc = {
            "id": doc_id,
            "title": title or ai_result.get("summary", "")[:80] or file.filename,
            "description": description or ai_result.get("summary", ""),
            "category_id": category_id or "",
            "filename": file.filename,
            "gridfs_id": gridfs_id,
            "stored_name": stored_name,
            "mime_type": file.content_type,
            "size_bytes": len(content),
            "copropriete_id": copropriete_id or "",
            "ai_classification": ai_result,
            "doc_date": ai_result.get("doc_date", ""),
            "document_type": ai_result.get("document_type", ""),
            "parties": ai_result.get("parties", []),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.documents.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{doc_id}/download")
    async def download_document(doc_id: str, inline: int = 0):
        """Return the document file.

        - Default: `Content-Disposition: attachment` (forces download).
        - `?inline=1`: `Content-Disposition: inline` (embed in iframe / <img>).
          Utilise par le composant DocumentViewerModal cote syndic ET
          proprietaire pour afficher PDF et images directement dans l'app.
        """
        doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Document non trouve")
        media_type = doc.get("mime_type") or "application/octet-stream"
        filename = doc.get("filename") or doc.get("stored_name") or "document"
        disposition = "inline" if inline else "attachment"
        # iter87 : prefer GridFS (new), fallback to disk (legacy)
        gid = doc.get("gridfs_id")
        if gid:
            docs_storage = get_documents_storage(db)
            try:
                data = await docs_storage.download(gid)
            except Exception:
                raise HTTPException(404, "Fichier introuvable dans GridFS")
            safe_name = filename.replace('"', "")
            return Response(
                content=data,
                media_type=media_type,
                headers={"Content-Disposition": f'{disposition}; filename="{safe_name}"'},
            )
        path = doc.get("stored_path", "")
        if not path or not os.path.exists(path):
            raise HTTPException(404, "Fichier supprime du disque")
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
            content_disposition_type=disposition,
        )

    @router.put("/{doc_id}")
    async def update_document(doc_id: str, data: DocumentInput):
        update = {
            "title": data.title, "description": data.description,
            "category_id": data.category_id, "content": data.content
        }
        result = await db.documents.update_one({"id": doc_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Document non trouve")
        return await db.documents.find_one({"id": doc_id}, {"_id": 0})

    @router.delete("/{doc_id}")
    async def delete_document(doc_id: str):
        doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Document non trouve")
        # iter87 : remove from GridFS (new) AND legacy disk (best-effort)
        gid = doc.get("gridfs_id")
        if gid:
            try:
                docs_storage = get_documents_storage(db)
                await docs_storage.delete(gid)
            except Exception:
                pass
        if doc.get("stored_path") and os.path.exists(doc["stored_path"]):
            try:
                os.remove(doc["stored_path"])
            except Exception:
                pass
        await db.documents.delete_one({"id": doc_id})
        return {"message": "Document supprime"}

    return router
