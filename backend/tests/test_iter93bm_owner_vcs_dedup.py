"""iter93bm : anti-doublon cross-ACP + VCS unique + preview-vcs endpoint.

Cover cases :
 1. POST /api/owners/preview-vcs -> 200 + unique VCS on each call.
 2. POST /api/owners with duplicate email cross-ACP -> 409 STRICT.
 3. POST /api/owners with duplicate VCS -> 409 STRICT.
 4. GET /api/owners/check-duplicate?vcs_code=... -> lists matches.
 5. POST /api/owners with duplicate aux_code same ACP -> 409 ; other ACP -> OK.
 6. Regression : POST /api/owners with fully unique data -> 200.
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    r = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    r.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_copro(db, cid):
    await db.coproprietes.insert_one({"id": cid, "name": cid})


async def _cleanup(db, cids, owner_ids):
    for cid in cids:
        await db.coproprietes.delete_many({"id": cid})
    for oid in owner_ids:
        await db.owners.delete_many({"id": oid})


def _tag():
    return uuid.uuid4().hex[:8]


def test_preview_vcs_uniqueness():
    async def _run():
        async with httpx.AsyncClient(timeout=30) as c:
            await _login(c)
            r1 = await c.post(f"{BACKEND_URL}/api/owners/preview-vcs")
            r2 = await c.post(f"{BACKEND_URL}/api/owners/preview-vcs")
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            j1, j2 = r1.json(), r2.json()
            assert "vcs_code" in j1 and "vcs_digits" in j1
            assert j1["vcs_code"].startswith("+++") and j1["vcs_code"].endswith("+++")
            assert j1["vcs_code"] != j2["vcs_code"], "VCS must be unique on each call"
            assert j1["vcs_digits"] != j2["vcs_digits"]
    asyncio.run(_run())


def test_create_owner_dedup_email_cross_acp():
    async def _run():
        db = await _mongo()
        cid_a, cid_b = f"acp-a-{_tag()}", f"acp-b-{_tag()}"
        await _seed_copro(db, cid_a)
        await _seed_copro(db, cid_b)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                email = f"dup-{_tag()}@test.local"
                # Create in ACP A
                r1 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Alice", "last_name": "Alpha",
                    "email": email, "copropriete_id": cid_a,
                })
                assert r1.status_code == 200, r1.text
                owners_created.append(r1.json()["id"])
                # Same email but ACP B -> should be BLOCKED (cross-ACP strict)
                r2 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Bob", "last_name": "Beta",
                    "email": email, "copropriete_id": cid_b,
                })
                assert r2.status_code == 409, f"Expected 409 got {r2.status_code}: {r2.text}"
                body = r2.json()
                detail = body.get("detail", "")
                assert "STRICT" in detail and "email" in detail, detail
        finally:
            await _cleanup(db, [cid_a, cid_b], owners_created)
    asyncio.run(_run())


def test_create_owner_dedup_vcs_cross_acp():
    async def _run():
        db = await _mongo()
        cid_a, cid_b = f"acp-a-{_tag()}", f"acp-b-{_tag()}"
        await _seed_copro(db, cid_a)
        await _seed_copro(db, cid_b)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                # First : create with auto-generated VCS in ACP A
                r1 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Carla", "last_name": "Charlie",
                    "email": f"c-{_tag()}@t.l", "copropriete_id": cid_a,
                })
                assert r1.status_code == 200, r1.text
                o1 = r1.json()
                owners_created.append(o1["id"])
                vcs = o1["vcs_code"]
                assert vcs and vcs.startswith("+++")
                # Second : reuse same VCS in ACP B (different email/name)
                r2 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Dan", "last_name": "Delta",
                    "email": f"d-{_tag()}@t.l", "copropriete_id": cid_b,
                    "vcs_code": vcs,
                })
                assert r2.status_code == 409, f"Expected 409 got {r2.status_code}: {r2.text}"
                detail = r2.json().get("detail", "")
                assert "STRICT" in detail and "VCS" in detail, detail
        finally:
            await _cleanup(db, [cid_a, cid_b], owners_created)
    asyncio.run(_run())


def test_check_duplicate_vcs_endpoint():
    async def _run():
        db = await _mongo()
        cid = f"acp-{_tag()}"
        await _seed_copro(db, cid)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r1 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Eve", "last_name": "Echo",
                    "email": f"e-{_tag()}@t.l", "copropriete_id": cid,
                })
                assert r1.status_code == 200, r1.text
                o1 = r1.json()
                owners_created.append(o1["id"])
                vcs = o1["vcs_code"]
                # Query check-duplicate?vcs_code=...
                r2 = await c.get(f"{BACKEND_URL}/api/owners/check-duplicate", params={"vcs_code": vcs})
                assert r2.status_code == 200, r2.text
                j = r2.json()
                assert j["has_duplicates"] is True
                assert any(d["owner_id"] == o1["id"] for d in j["duplicates"]), j
                # And that field == vcs_code
                assert any(d["field"] == "vcs_code" for d in j["duplicates"])
        finally:
            await _cleanup(db, [cid], owners_created)
    asyncio.run(_run())


def test_create_owner_aux_code_local_per_acp():
    async def _run():
        db = await _mongo()
        cid_a, cid_b = f"acp-a-{_tag()}", f"acp-b-{_tag()}"
        await _seed_copro(db, cid_a)
        await _seed_copro(db, cid_b)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                aux = f"C{_tag()[:4].upper()}"
                # Owner 1 in ACP A with aux_code
                r1 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Fay", "last_name": "Foxtrot",
                    "email": f"f-{_tag()}@t.l", "copropriete_id": cid_a,
                    "auxiliary_code": aux,
                })
                assert r1.status_code == 200, r1.text
                owners_created.append(r1.json()["id"])
                # Same aux in SAME ACP -> 409
                r2 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Gwen", "last_name": "Golf",
                    "email": f"g-{_tag()}@t.l", "copropriete_id": cid_a,
                    "auxiliary_code": aux,
                })
                assert r2.status_code == 409, f"Expected 409 got {r2.status_code}: {r2.text}"
                detail = r2.json().get("detail", "")
                assert "auxiliaire" in detail.lower() or "auxiliary" in detail.lower(), detail
                # Same aux in DIFFERENT ACP -> OK (local scope)
                r3 = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Hank", "last_name": "Hotel",
                    "email": f"h-{_tag()}@t.l", "copropriete_id": cid_b,
                    "auxiliary_code": aux,
                })
                assert r3.status_code == 200, f"Expected 200 got {r3.status_code}: {r3.text}"
                owners_created.append(r3.json()["id"])
        finally:
            await _cleanup(db, [cid_a, cid_b], owners_created)
    asyncio.run(_run())


def test_create_owner_unique_regression():
    async def _run():
        db = await _mongo()
        cid = f"acp-{_tag()}"
        await _seed_copro(db, cid)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/owners", json={
                    "first_name": "Ivy", "last_name": "India",
                    "email": f"i-{_tag()}@t.l",
                    "phone": f"+3247{_tag()[:7]}",
                    "copropriete_id": cid,
                })
                assert r.status_code == 200, r.text
                o = r.json()
                assert o.get("vcs_code", "").startswith("+++")
                assert o.get("id")
                owners_created.append(o["id"])
        finally:
            await _cleanup(db, [cid], owners_created)
    asyncio.run(_run())
