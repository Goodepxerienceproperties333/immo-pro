"""Iter90 — Platform access management for owners + forgot/reset password.

Tests:
- Owner access lifecycle: grant -> pending -> revoke -> suspended -> reactivate -> pending
- grant-access creates user with must_change_password=True and is_suspended=False
- Suspended user cannot log in (403)
- Suspended user's existing JWT is rejected by middleware (403)
- Forgot-password is enumeration-safe (200 with same generic message
  for known/unknown emails; for rate-limited too, no token created)
- Reset-password validates token (rejects invalid/expired/consumed)
- Reset-password updates password and logs the user in
- Reset-password invalidates other unused tokens for the same user

Pattern aligns with iter89 tests : no pytest_asyncio dependency, uses
`asyncio.run(_async_test())` and httpx.AsyncClient(ASGITransport).
"""
import os
import sys
import asyncio
import secrets
import uuid
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-iter90-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db, hash_password  # noqa: E402


async def _wipe():
    """Clean state. Keep admin user from seed."""
    await db.owners.delete_many({"id": {"$regex": "^o-test-iter90-"}})
    await db.lots.delete_many({"id": {"$regex": "^l-test-iter90-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-test-iter90-"}})
    await db.users.delete_many({"email": {"$regex": "@iter90test\\.local$"}})
    await db.password_reset_tokens.delete_many({})
    await db.password_reset_attempts.delete_many({})


async def _login_admin(c: AsyncClient) -> None:
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"],
        "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _create_owner(email: str = "john.doe@iter90test.local"):
    uid = uuid.uuid4().hex[:8]
    copro_id = f"c-test-iter90-{uid}"
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP iter90", "created_at": datetime.now(timezone.utc).isoformat(),
    })
    owner_id = f"o-test-iter90-{uid}"
    await db.owners.insert_one({
        "id": owner_id, "name": "Doe John",
        "first_name": "John", "last_name": "Doe",
        "email": email, "copropriete_ids": [copro_id],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.lots.insert_one({
        "id": f"l-test-iter90-{uid}", "number": "A-01",
        "owner_id": owner_id, "copropriete_id": copro_id,
    })
    return owner_id, copro_id


# ============= ACCESS LIFECYCLE =============

async def _t_access_status_initially_none():
    await _wipe()
    owner_id, _ = await _create_owner("none@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        r = await c.get(f"/api/owners/{owner_id}/access-status")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["has_access"] is False
        assert data["status"] == "none"


async def _t_grant_access_creates_user_pending():
    await _wipe()
    owner_id, _ = await _create_owner("grant@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        r = await c.post(f"/api/owners/{owner_id}/grant-access")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["linked_existing_user"] is False
        assert data["status"]["status"] == "pending"
        assert data["status"]["must_change_password"] is True
        assert data["status"]["is_suspended"] is False
        owner = await db.owners.find_one({"id": owner_id})
        assert owner["user_id"] == data["status"]["user_id"]


async def _t_grant_links_existing_user():
    await _wipe()
    email = "existing@iter90test.local"
    res = await db.users.insert_one({
        "email": email, "name": "Existing", "role": "owner",
        "password_hash": hash_password("realpassword"),
        "must_change_password": False, "copropriete_ids": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    existing_uid = str(res.inserted_id)
    owner_id, _ = await _create_owner(email)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        r = await c.post(f"/api/owners/{owner_id}/grant-access")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["linked_existing_user"] is True
        assert data["status"]["user_id"] == existing_uid
        assert data["status"]["status"] == "active"
        count = await db.users.count_documents({"email": email})
        assert count == 1


async def _t_grant_requires_email():
    await _wipe()
    owner_id, _ = await _create_owner("")
    await db.owners.update_one({"id": owner_id}, {"$set": {"email": ""}})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        r = await c.post(f"/api/owners/{owner_id}/grant-access")
        assert r.status_code == 400, r.text


async def _t_revoke_then_reactivate():
    await _wipe()
    owner_id, _ = await _create_owner("revoke@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{owner_id}/grant-access")
        r = await c.post(f"/api/owners/{owner_id}/revoke-access")
        assert r.status_code == 200
        assert r.json()["status"]["status"] == "suspended"
        r = await c.post(f"/api/owners/{owner_id}/reactivate-access")
        assert r.status_code == 200
        assert r.json()["status"]["is_suspended"] is False


async def _t_resend_invitation_blocked_after_password_set():
    await _wipe()
    owner_id, _ = await _create_owner("resend@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{owner_id}/grant-access")
        r = await c.post(f"/api/owners/{owner_id}/resend-invitation")
        assert r.status_code == 200
        # Mark password as set
        owner = await db.owners.find_one({"id": owner_id})
        await db.users.update_one(
            {"_id": ObjectId(owner["user_id"])},
            {"$set": {"must_change_password": False, "password_hash": hash_password("realpwd")}}
        )
        r = await c.post(f"/api/owners/{owner_id}/resend-invitation")
        assert r.status_code == 400
        assert "mot de passe oublie" in r.json()["detail"].lower()


# ============= SUSPENDED USER =============

async def _t_suspended_cannot_login():
    await _wipe()
    owner_id, _ = await _create_owner("suspended@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{owner_id}/grant-access")
        owner = await db.owners.find_one({"id": owner_id})
        await db.users.update_one(
            {"_id": ObjectId(owner["user_id"])},
            {"$set": {"must_change_password": False, "password_hash": hash_password("realpwd1")}}
        )
        await c.post(f"/api/owners/{owner_id}/revoke-access")
    transport2 = ASGITransport(app=app)
    async with AsyncClient(transport=transport2, base_url="http://test") as c2:
        r = await c2.post("/api/auth/login",
                          json={"email": "suspended@iter90test.local", "password": "realpwd1"})
        assert r.status_code == 403
        assert "suspendu" in r.json()["detail"].lower()


async def _t_suspended_existing_session_blocked():
    await _wipe()
    owner_id, _ = await _create_owner("midblock@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as admin_c:
        await _login_admin(admin_c)
        await admin_c.post(f"/api/owners/{owner_id}/grant-access")
        owner = await db.owners.find_one({"id": owner_id})
        await db.users.update_one(
            {"_id": ObjectId(owner["user_id"])},
            {"$set": {"must_change_password": False, "password_hash": hash_password("realpwd2")}}
        )
        # Owner logs in with their own client
        transport2 = ASGITransport(app=app)
        async with AsyncClient(transport=transport2, base_url="http://test") as owner_c:
            r = await owner_c.post("/api/auth/login",
                                   json={"email": "midblock@iter90test.local", "password": "realpwd2"})
            assert r.status_code == 200, r.text
            r = await owner_c.get("/api/auth/me")
            assert r.status_code == 200
            # Admin suspends them
            await admin_c.post(f"/api/owners/{owner_id}/revoke-access")
            # Owner's existing session must be 403'd by middleware
            r = await owner_c.get("/api/auth/me")
            assert r.status_code == 403, r.text


# ============= FORGOT PASSWORD =============

async def _t_forgot_unknown_email_generic_200():
    await _wipe()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/forgot-password",
                         json={"email": "doesnotexist@iter90test.local"})
        assert r.status_code == 200
        assert "envoye" in r.json()["message"].lower()
        # No token created
        count = await db.password_reset_tokens.count_documents({})
        assert count == 0


async def _t_forgot_known_email_creates_token():
    await _wipe()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/forgot-password",
                         json={"email": os.environ["ADMIN_EMAIL"]},
                         headers={"X-Forwarded-For": "10.0.0.99"})
        assert r.status_code == 200
        token_doc = await db.password_reset_tokens.find_one({})
        assert token_doc is not None
        assert token_doc["consumed_at"] is None
        # MongoDB returns naive datetime ; normalize before compare
        exp = token_doc["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        assert exp > datetime.now(timezone.utc)
        assert len(token_doc["token_hash"]) == 64


async def _t_forgot_suspended_user_no_token():
    await _wipe()
    owner_id, _ = await _create_owner("noreset@iter90test.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{owner_id}/grant-access")
        await c.post(f"/api/owners/{owner_id}/revoke-access")
    await db.password_reset_tokens.delete_many({})
    transport2 = ASGITransport(app=app)
    async with AsyncClient(transport=transport2, base_url="http://test") as c2:
        r = await c2.post("/api/auth/forgot-password",
                          json={"email": "noreset@iter90test.local"})
        assert r.status_code == 200  # Always generic
        count = await db.password_reset_tokens.count_documents({})
        assert count == 0


async def _t_forgot_rate_limit():
    await _wipe()
    await db.password_reset_attempts.delete_many({})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for _ in range(7):
            r = await c.post("/api/auth/forgot-password",
                             json={"email": os.environ["ADMIN_EMAIL"]},
                             headers={"X-Forwarded-For": "9.9.9.9"})
            assert r.status_code == 200
        tokens = await db.password_reset_tokens.count_documents({})
        assert tokens == 5, f"Expected 5 tokens after rate-limit, got {tokens}"


# ============= RESET PASSWORD =============

async def _make_token_for(email: str, *, expires_minutes=60, consumed=False) -> str:
    user = await db.users.find_one({"email": email})
    assert user is not None
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    await db.password_reset_tokens.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": str(user["_id"]),
        "token_hash": token_hash,
        "expires_at": expires_at,
        "consumed_at": datetime.now(timezone.utc).isoformat() if consumed else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return raw


async def _t_reset_invalid_token():
    await _wipe()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/reset-password",
                         json={"token": "a" * 40, "new_password": "newpass123"})
        assert r.status_code == 400


async def _t_reset_expired_token():
    await _wipe()
    raw = await _make_token_for(os.environ["ADMIN_EMAIL"], expires_minutes=-5)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/reset-password",
                         json={"token": raw, "new_password": "newpass123"})
        assert r.status_code == 400
        assert "expir" in r.json()["detail"].lower()


async def _t_reset_consumed_token():
    await _wipe()
    raw = await _make_token_for(os.environ["ADMIN_EMAIL"], consumed=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/reset-password",
                         json={"token": raw, "new_password": "newpass123"})
        assert r.status_code == 400


async def _t_reset_updates_password_and_logs_in():
    await _wipe()
    raw = await _make_token_for(os.environ["ADMIN_EMAIL"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/reset-password",
                         json={"token": raw, "new_password": "freshpwd99"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == os.environ["ADMIN_EMAIL"]
        # Already logged in via cookies
        r = await c.get("/api/auth/me")
        assert r.status_code == 200
        # Old password rejected
        r = await c.post("/api/auth/login",
                         json={"email": os.environ["ADMIN_EMAIL"], "password": "admin123"})
        assert r.status_code == 401
        # New password works
        r = await c.post("/api/auth/login",
                         json={"email": os.environ["ADMIN_EMAIL"], "password": "freshpwd99"})
        assert r.status_code == 200
    # Restore admin password
    await db.users.update_one(
        {"email": os.environ["ADMIN_EMAIL"]},
        {"$set": {"password_hash": hash_password("admin123")}}
    )


async def _t_reset_invalidates_other_tokens():
    await _wipe()
    raw1 = await _make_token_for(os.environ["ADMIN_EMAIL"])
    raw2 = await _make_token_for(os.environ["ADMIN_EMAIL"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/reset-password",
                         json={"token": raw1, "new_password": "anewpwd00"})
        assert r.status_code == 200
        r = await c.post("/api/auth/reset-password",
                         json={"token": raw2, "new_password": "yetanother"})
        assert r.status_code == 400
    await db.users.update_one(
        {"email": os.environ["ADMIN_EMAIL"]},
        {"$set": {"password_hash": hash_password("admin123")}}
    )


# ============= PYTEST GLUE =============
# Single asyncio.run() to share a single event loop across all sub-tests
# (motor's AsyncIOMotorClient binds to the loop where it was first used).


async def _run_all():
    await _t_access_status_initially_none()
    await _t_grant_access_creates_user_pending()
    await _t_grant_links_existing_user()
    await _t_grant_requires_email()
    await _t_revoke_then_reactivate()
    await _t_resend_invitation_blocked_after_password_set()
    await _t_suspended_cannot_login()
    await _t_suspended_existing_session_blocked()
    await _t_forgot_unknown_email_generic_200()
    await _t_forgot_known_email_creates_token()
    await _t_forgot_suspended_user_no_token()
    await _t_forgot_rate_limit()
    await _t_reset_invalid_token()
    await _t_reset_expired_token()
    await _t_reset_consumed_token()
    await _t_reset_updates_password_and_logs_in()
    await _t_reset_invalidates_other_tokens()


def test_iter90_all_flows():
    """Single pytest entry that orchestrates all sub-tests under one event loop."""
    asyncio.run(_run_all())
