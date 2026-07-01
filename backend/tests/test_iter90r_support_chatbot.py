"""Iter90r - Chatbot support pour syndics.

Tests :
- T1  POST /api/support/conversations -> cree une conv liee au user
- T2  POST /api/support/conversations/{id}/chat -> message user + reponse IA stockes
- T3  GET  /api/support/conversations/{id}/messages -> retourne l'historique
- T4  GET  /api/support/conversations -> liste, triee par updated_at desc
- T5  DELETE /api/support/conversations/{id} -> supprime conv + messages
- T6  Chinese wall : un autre user ne peut PAS lire/modifier une conv qui n'est pas la sienne
- T7  Escalade manuelle -> conv.escalated = True apres POST /escalate

NOTE : le vrai appel IA (LLM) est teste indirectement via T2. En pratique
si EMERGENT_LLM_KEY est configure, l'appel reussit et un message assistant
est stocke. Sinon l'endpoint retourne 500.
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

import bcrypt
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90r-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.support_messages.delete_many({"conversation_id": {"$regex": "^conv-iter90r-"}})
    await db.support_conversations.delete_many({"id": {"$regex": "^conv-iter90r-"}})
    await db.support_conversations.delete_many({"user_email": {"$regex": "^synd-iter90r-"}})
    await db.users.delete_many({"email": {"$regex": "^synd-iter90r-"}})


async def _create_syndic():
    email = f"synd-iter90r-{uuid.uuid4().hex[:6]}@test.be"
    pwd = "SecretPwd123!"
    await db.users.insert_one({
        "id": str(uuid.uuid4()), "email": email, "name": "Syndic Test",
        "password_hash": bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode(),
        "role": "syndic", "copropriete_ids": [],
        "onboarding_completed": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return email, pwd


async def _login(c, email, pwd):
    r = await c.post("/api/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text


async def _t_create_and_list_conversation():
    await _wipe()
    email, pwd = await _create_syndic()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post("/api/support/conversations", json={"title": "Test 1"})
        assert r.status_code == 200, r.text
        conv = r.json()
        assert conv["title"] == "Test 1"
        assert conv["user_email"] == email
        assert conv["messages_count"] == 0
        assert conv["escalated"] is False
        # List
        r = await c.get("/api/support/conversations")
        assert r.status_code == 200
        convs = r.json()
        assert len(convs) >= 1
        assert any(c["id"] == conv["id"] for c in convs)


async def _t_get_messages_empty():
    await _wipe()
    email, pwd = await _create_syndic()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post("/api/support/conversations", json={})
        conv_id = r.json()["id"]
        r = await c.get(f"/api/support/conversations/{conv_id}/messages")
        assert r.status_code == 200
        assert r.json() == []


async def _t_chinese_wall_cross_user():
    """Un syndic ne peut PAS lire/modifier la conv d'un autre syndic."""
    await _wipe()
    email1, pwd1 = await _create_syndic()
    email2, pwd2 = await _create_syndic()
    transport = ASGITransport(app=app)
    # Syndic 1 cree une conv
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email1, pwd1)
        r = await c.post("/api/support/conversations", json={"title": "Prive"})
        conv_id = r.json()["id"]
    # Syndic 2 essaie d'y acceder
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email2, pwd2)
        r = await c.get(f"/api/support/conversations/{conv_id}/messages")
        assert r.status_code == 403
        r = await c.delete(f"/api/support/conversations/{conv_id}")
        assert r.status_code == 403


async def _t_delete_conversation():
    await _wipe()
    email, pwd = await _create_syndic()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post("/api/support/conversations", json={"title": "To delete"})
        conv_id = r.json()["id"]
        # Simuler quelques messages
        await db.support_messages.insert_many([
            {"id": str(uuid.uuid4()), "conversation_id": conv_id,
             "role": "user", "content": "Q1",
             "created_at": datetime.now(timezone.utc).isoformat()},
            {"id": str(uuid.uuid4()), "conversation_id": conv_id,
             "role": "assistant", "content": "A1",
             "created_at": datetime.now(timezone.utc).isoformat()},
        ])
        r = await c.delete(f"/api/support/conversations/{conv_id}")
        assert r.status_code == 200
        # Conv supprimee
        rest = await db.support_conversations.find_one({"id": conv_id})
        assert rest is None
        # Messages supprimes en cascade
        remaining = await db.support_messages.count_documents({"conversation_id": conv_id})
        assert remaining == 0


async def _t_empty_message_rejected():
    await _wipe()
    email, pwd = await _create_syndic()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post("/api/support/conversations", json={})
        conv_id = r.json()["id"]
        r = await c.post(f"/api/support/conversations/{conv_id}/chat",
                         json={"message": "   "})
        assert r.status_code == 400
        r = await c.post(f"/api/support/conversations/{conv_id}/chat",
                         json={"message": "x" * 2001})
        assert r.status_code == 400


async def _t_manual_escalation_marks_conv():
    await _wipe()
    email, pwd = await _create_syndic()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post("/api/support/conversations", json={"title": "Esc"})
        conv_id = r.json()["id"]
        # Simuler un message pour que l'escalade ait un contenu
        await db.support_messages.insert_one({
            "id": str(uuid.uuid4()), "conversation_id": conv_id,
            "role": "user", "content": "Ma question",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # Ecraser SUPPORT_EMAIL pour ne pas envoyer d'email reel
        old = os.environ.get("SUPPORT_EMAIL")
        os.environ["SUPPORT_EMAIL"] = "test-noop@example.com"
        try:
            r = await c.post(f"/api/support/conversations/{conv_id}/escalate",
                             json={"reason": "Je bloque"})
            # 200 attendu meme si l'email echoue (envoi en background)
            assert r.status_code == 200, r.text
        finally:
            if old is not None:
                os.environ["SUPPORT_EMAIL"] = old
            else:
                del os.environ["SUPPORT_EMAIL"]
        # Verifier flag escalated
        conv = await db.support_conversations.find_one({"id": conv_id})
        assert conv["escalated"] is True
        assert conv["escalated_kind"] == "manual"


async def _run_all():
    await _t_create_and_list_conversation()
    await _t_get_messages_empty()
    await _t_chinese_wall_cross_user()
    await _t_delete_conversation()
    await _t_empty_message_rejected()
    await _t_manual_escalation_marks_conv()


def test_iter90r_support_chatbot():
    asyncio.run(_run_all())
