"""iter90ht : Edition d'une tuile document (titre, description, categorie).

Verifie que le PUT `/api/documents/{doc_id}` existe et met bien a jour les 4
champs modifiables. Ce test valide aussi que la page frontend expose le
bouton `doc-edit-{id}` et ouvre un dialog reutilisant l'endpoint.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_put_document_endpoint_updates_title_description_category():
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.documents import create_documents_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        doc_id = f"doc-iter90ht-{uuid.uuid4().hex[:8]}"
        try:
            # Seed doc + categorie de test
            cat_id_1 = f"cat1-{doc_id}"
            cat_id_2 = f"cat2-{doc_id}"
            await db.document_categories.insert_many([
                {"id": cat_id_1, "name": "Ancien", "description": "", "copropriete_id": "copro-ht"},
                {"id": cat_id_2, "name": "Nouveau", "description": "", "copropriete_id": "copro-ht"},
            ])
            await db.documents.insert_one({
                "id": doc_id, "title": "Titre initial",
                "description": "desc initial", "category_id": cat_id_1,
                "content": "note initial", "copropriete_id": "copro-ht",
                "filename": "test.pdf", "gridfs_id": "fake-gid",
                "created_at": "2026-01-01T00:00:00",
            })

            # Trouve la route PUT et l'appelle
            router = create_documents_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/documents/{doc_id}" and "PUT" in (r.methods or set()):
                    handler = r.endpoint
                    break
            assert handler is not None, "Endpoint PUT /api/documents/{doc_id} manquant"

            # Simule le call - DocumentInput est un modele Pydantic
            from routes.documents import DocumentInput
            new_data = DocumentInput(
                title="Titre modifie",
                description="desc modifiee",
                category_id=cat_id_2,
                content="note modifiee",
            )
            result = await handler(doc_id=doc_id, data=new_data)
            assert result["title"] == "Titre modifie"
            assert result["description"] == "desc modifiee"
            assert result["category_id"] == cat_id_2
            assert result["content"] == "note modifiee"
            # Verifier en DB
            in_db = await db.documents.find_one({"id": doc_id}, {"_id": 0})
            assert in_db["title"] == "Titre modifie"
            assert in_db["category_id"] == cat_id_2
            # Le fichier physique n'est pas touche
            assert in_db["gridfs_id"] == "fake-gid"
        finally:
            await db.documents.delete_one({"id": doc_id})
            await db.document_categories.delete_many({"copropriete_id": "copro-ht"})
            client.close()

    asyncio.run(_run())


def test_frontend_documents_page_has_edit_button():
    """Verifie la presence du bouton d'edition et du dialog en mode edition."""
    with open("/app/frontend/src/pages/DocumentsPage.js", "r", encoding="utf-8") as f:
        src = f.read()
    # data-testid={`doc-edit-${doc.id}`}
    assert "doc-edit-" in src, "Bouton edition manquant sur la tuile document"
    # onClick={() => openEditDoc(doc)}
    assert "openEditDoc" in src, "Handler openEditDoc manquant"
    # Dialog title change based on editingDoc
    assert "editingDoc ? 'Modifier le document'" in src, (
        "Titre du dialog doit changer entre creation et edition."
    )
    # PUT vers /documents/{id}
    assert "api.put(`/documents/${editingDoc.id}`" in src, (
        "L'appel API PUT doit cibler le doc en cours d'edition."
    )


def test_download_button_shown_for_gridfs_docs():
    """Le bouton download etait avant cache si stored_path absent.
    iter90ht : doit maintenant s'afficher aussi pour les docs gridfs_id
    (documents crees automatiquement depuis les Communications iter90hs)."""
    with open("/app/frontend/src/pages/DocumentsPage.js", "r", encoding="utf-8") as f:
        src = f.read()
    assert "doc.stored_path || doc.gridfs_id" in src, (
        "Le bouton download doit s'afficher aussi pour les docs GridFS."
    )
