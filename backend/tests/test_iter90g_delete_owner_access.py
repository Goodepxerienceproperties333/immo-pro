"""iter90g - Suppression complete de l'acces proprietaire (DELETE).

Tests :
- DELETE supprime le user account, detache la fiche owner, log audit
- DELETE idempotent (200 OK + deleted:false si rien a supprimer)
- DELETE preserve les comptes non-owner (syndic/admin partage)
- Invalide les tokens reset actifs
- Lien de session : ancien JWT du proprio devient 401/403 apres suppression
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport
from bson import ObjectId

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90g-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db, hash_password  # noqa: E402


async def _wipe():
    await db.owners.delete_many({"id": {"$regex": "^o-iter90g-"}})
    await db.lots.delete_many({"id": {"$regex": "^l-iter90g-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90g-"}})
    await db.users.delete_many({"email": {"$regex": "@iter90g\\.local$"}})
    await db.owner_access_audit.delete_many({"owner_id": {"$regex": "^o-iter90g-"}})


async def _login_admin(c: AsyncClient):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _create_owner_with_lot(email: str):
    uid = uuid.uuid4().hex[:8]
    cid = f"c-iter90g-{uid}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP iter90g",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    oid = f"o-iter90g-{uid}"
    await db.owners.insert_one({
        "id": oid, "name": "Test Owner",
        "first_name": "Test", "last_name": "Owner",
        "email": email, "copropriete_ids": [cid],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.lots.insert_one({
        "id": f"l-iter90g-{uid}", "number": "A-01",
        "owner_id": oid, "copropriete_id": cid,
    })
    return oid


# === T1 : DELETE deletes the user + detach owner.user_id + audit log ===
async def _t_delete_full_flow():
    await _wipe()
    email = "delfull@iter90g.local"
    oid = await _create_owner_with_lot(email)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{oid}/grant-access")
        # User exists
        user = await db.users.find_one({"email": email})
        assert user is not None
        uid_str = str(user["_id"])
        # DELETE
        r = await c.delete(f"/api/owners/{oid}/access")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted"] is True
        assert body["status"]["status"] == "none"
        assert body["status"]["user_id"] is None
        # User is gone from DB
        gone = await db.users.find_one({"_id": ObjectId(uid_str)})
        assert gone is None
        # Owner.user_id is unset
        owner = await db.owners.find_one({"id": oid})
        assert owner.get("user_id") is None
        # Audit log has 'delete' entry
        audit = await db.owner_access_audit.find_one({
            "owner_id": oid, "action": "delete"
        })
        assert audit is not None
        assert audit["target_user_email"] == email


# === T2 : DELETE is idempotent ===
async def _t_delete_idempotent():
    await _wipe()
    oid = await _create_owner_with_lot("idem@iter90g.local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        # No access yet -> DELETE should return 200 with deleted:false
        r = await c.delete(f"/api/owners/{oid}/access")
        assert r.status_code == 200
        assert r.json()["deleted"] is False


# === T3 : DELETE preserves non-owner accounts (shared email with syndic) ===
async def _t_delete_preserves_non_owner_account():
    await _wipe()
    email = "shared@iter90g.local"
    # Create a syndic user with this email
    res = await db.users.insert_one({
        "email": email, "name": "Shared Syndic",
        "password_hash": hash_password("syndicpwd"),
        "role": "syndic", "copropriete_ids": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    syndic_uid = str(res.inserted_id)
    oid = await _create_owner_with_lot(email)
    # Manually link owner -> syndic user (as if grant-access linked them)
    await db.owners.update_one({"id": oid}, {"$set": {"user_id": syndic_uid}})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        r = await c.delete(f"/api/owners/{oid}/access")
        assert r.status_code == 200, r.text
        body = r.json()
        # User NOT deleted (role != owner), but detached
        assert body["deleted"] is False
        assert body["detached"] is True
        # User still exists in DB
        still = await db.users.find_one({"_id": ObjectId(syndic_uid)})
        assert still is not None
        assert still.get("role") == "syndic"
        # Owner detached
        owner = await db.owners.find_one({"id": oid})
        assert owner.get("user_id") is None


# === T4 : DELETE invalidates pending reset tokens ===
async def _t_delete_invalidates_reset_tokens():
    await _wipe()
    email = "tokens@iter90g.local"
    oid = await _create_owner_with_lot(email)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login_admin(c)
        await c.post(f"/api/owners/{oid}/grant-access")
        user = await db.users.find_one({"email": email})
        # Manually insert an unconsumed reset token
        await db.password_reset_tokens.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": str(user["_id"]),
            "token_hash": "fake-hash-" + uuid.uuid4().hex,
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "consumed_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # DELETE
        r = await c.delete(f"/api/owners/{oid}/access")
        assert r.status_code == 200
        # All reset tokens for that user are now consumed
        unconsumed = await db.password_reset_tokens.count_documents({
            "user_id": str(user["_id"]), "consumed_at": None
        })
        assert unconsumed == 0


# === T5 : after DELETE, existing JWT session is rejected (user not found = 401) ===
async def _t_delete_invalidates_session():
    await _wipe()
    email = "session@iter90g.local"
    oid = await _create_owner_with_lot(email)
    transport_a = ASGITransport(app=app)
    async with AsyncClient(transport=transport_a, base_url="http://test") as admin:
        await _login_admin(admin)
        await admin.post(f"/api/owners/{oid}/grant-access")
        # Set a real password so the owner can login
        owner = await db.owners.find_one({"id": oid})
        await db.users.update_one(
            {"_id": ObjectId(owner["user_id"])},
            {"$set": {"must_change_password": False,
                      "password_hash": hash_password("realpwd9")}}
        )
        # Owner logs in
        transport_o = ASGITransport(app=app)
        async with AsyncClient(transport=transport_o, base_url="http://test") as owner_c:
            r = await owner_c.post("/api/auth/login",
                                   json={"email": email, "password": "realpwd9"})
            assert r.status_code == 200
            # /me works
            r = await owner_c.get("/api/auth/me")
            assert r.status_code == 200
            # Admin DELETE
            r = await admin.delete(f"/api/owners/{oid}/access")
            assert r.status_code == 200
            # Owner's existing JWT now returns 401 (user not found in DB)
            r = await owner_c.get("/api/auth/me")
            assert r.status_code == 401, r.text


async def _run_all():
    await _t_delete_full_flow()
    await _t_delete_idempotent()
    await _t_delete_preserves_non_owner_account()
    await _t_delete_invalidates_reset_tokens()
    await _t_delete_invalidates_session()


def test_iter90g_delete_access_all_flows():
    asyncio.run(_run_all())
