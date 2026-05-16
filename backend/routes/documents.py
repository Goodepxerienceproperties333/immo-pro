from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid


class CategoryInput(BaseModel):
    name: str
    description: Optional[str] = ""


class DocumentInput(BaseModel):
    title: str
    description: Optional[str] = ""
    category_id: Optional[str] = ""
    content: Optional[str] = ""  # text content or reference


def create_documents_router(db):
    router = APIRouter(prefix="/api/documents")

    # ---- CATEGORIES ----
    @router.get("/categories")
    async def list_categories():
        cats = await db.document_categories.find({}, {"_id": 0}).sort("name", 1).to_list(100)
        return cats

    @router.post("/categories")
    async def create_category(data: CategoryInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "description": data.description,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.document_categories.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

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
    async def list_documents(category_id: Optional[str] = None):
        query = {}
        if category_id:
            query["category_id"] = category_id
        docs = await db.documents.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
        return docs

    @router.post("")
    async def create_document(data: DocumentInput):
        doc = {
            "id": str(uuid.uuid4()),
            "title": data.title,
            "description": data.description,
            "category_id": data.category_id,
            "content": data.content,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.documents.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

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
        result = await db.documents.delete_one({"id": doc_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Document non trouve")
        return {"message": "Document supprime"}

    return router
