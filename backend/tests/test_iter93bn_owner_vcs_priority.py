"""iter93bn : VCS = discriminateur PRINCIPAL de dedup.

Scenarios covered :
 1. parse_owners_pdf : PDF Optipro 12 lignes -> count=12, 12 VCS uniques,
    email placeholder partage.
 2. reuse_on_duplicate + meme email + VCS different -> 2 owners crees.
 3. reuse_on_duplicate + meme VCS -> _reused=True + _dup_field='vcs_code'.
 4. Idempotence e2e : import boucle POST /owners du PDF (12) 2x -> 12 owners.
 5. Multi-ACP consolidation : meme VCS avec copropriete_id different ->
    reuse + copropriete_ids consolidee.
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

from import_wizard.pdf_utils import parse_owners_pdf

BACKEND_URL = "http://localhost:8001"
PDF_PATH = "/tmp/owners_test.pdf"


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


# ---------------------------------------------------------------------------
# 1. parse_owners_pdf sanity check
# ---------------------------------------------------------------------------
def test_parse_owners_pdf_12_owners_unique_vcs():
    data = open(PDF_PATH, "rb").read()
    res = parse_owners_pdf(data)
    assert res.get("count") == 12, res
    owners = res["owners"]
    assert len(owners) == 12
    # All share the placeholder email
    emails = {o.get("email") for o in owners}
    assert emails == {"info@nextgecopro.be"}
    # But all VCS are unique
    vcs_codes = {o.get("vcs_code") for o in owners}
    assert len(vcs_codes) == 12
    # Each entry has required fields
    for o in owners:
        assert o.get("auxiliary_code", "").startswith("C")
        assert o.get("vcs_code", "").startswith("+++")
        assert o.get("last_name")
        # first_name may be empty for company/single-name entries; name always set
        assert o.get("name")
        assert o.get("identifier")


# ---------------------------------------------------------------------------
# 2. Same email but different VCS -> 2 owners created (not reused)
# ---------------------------------------------------------------------------
def test_same_email_diff_vcs_creates_two_owners():
    async def _run():
        db = await _mongo()
        cid = f"acp-{_tag()}"
        await _seed_copro(db, cid)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                shared_email = f"placeholder-{_tag()}@syndic.local"
                vcs_a = f"+++001/{_tag()[:4]}/{_tag()[:5]}+++"
                vcs_b = f"+++002/{_tag()[:4]}/{_tag()[:5]}+++"
                r1 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "AliceA", "last_name": "AlphaA",
                        "email": shared_email, "copropriete_id": cid,
                        "vcs_code": vcs_a,
                    },
                )
                assert r1.status_code == 200, r1.text
                o1 = r1.json()
                owners_created.append(o1["id"])
                assert not o1.get("_reused"), f"Expected fresh create, got: {o1}"

                r2 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "BobB", "last_name": "BetaB",
                        "email": shared_email, "copropriete_id": cid,
                        "vcs_code": vcs_b,
                    },
                )
                assert r2.status_code == 200, r2.text
                o2 = r2.json()
                owners_created.append(o2["id"])
                assert not o2.get("_reused"), (
                    f"Expected NEW owner (VCS unique), got _reused: {o2}"
                )
                assert o2["id"] != o1["id"], "Owners must be distinct"
        finally:
            await _cleanup(db, [cid], owners_created)
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Same VCS -> reused (idempotence par VCS)
# ---------------------------------------------------------------------------
def test_same_vcs_returns_reused():
    async def _run():
        db = await _mongo()
        cid = f"acp-{_tag()}"
        await _seed_copro(db, cid)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                vcs = f"+++555/{_tag()[:4]}/{_tag()[:5]}+++"
                r1 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "Carla", "last_name": "Charlie",
                        "email": f"c-{_tag()}@t.l", "copropriete_id": cid,
                        "vcs_code": vcs,
                    },
                )
                assert r1.status_code == 200, r1.text
                o1 = r1.json()
                owners_created.append(o1["id"])

                r2 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "Carla2", "last_name": "Charlie2",
                        "email": f"c2-{_tag()}@t.l", "copropriete_id": cid,
                        "vcs_code": vcs,
                    },
                )
                assert r2.status_code == 200, r2.text
                o2 = r2.json()
                assert o2.get("_reused") is True, f"Expected _reused=True: {o2}"
                assert o2.get("_dup_field") == "vcs_code", (
                    f"Expected _dup_field=vcs_code, got {o2.get('_dup_field')}"
                )
                assert o2["id"] == o1["id"]
        finally:
            await _cleanup(db, [cid], owners_created)
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. E2E : import 2x the same PDF (12 owners) -> 12 owners total in DB
# ---------------------------------------------------------------------------
def test_e2e_pdf_import_idempotent_12_owners():
    async def _run():
        db = await _mongo()
        cid = f"acp-{_tag()}"
        await _seed_copro(db, cid)
        parsed = parse_owners_pdf(open(PDF_PATH, "rb").read())
        assert parsed["count"] == 12
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                await _login(c)
                # Round 1 : create 12 owners
                created_ids = set()
                for o in parsed["owners"]:
                    payload = {
                        "first_name": o["first_name"],
                        "last_name": o["last_name"],
                        "email": o["email"],
                        "copropriete_id": cid,
                        "vcs_code": o["vcs_code"],
                        "auxiliary_code": o["auxiliary_code"],
                    }
                    r = await c.post(
                        f"{BACKEND_URL}/api/owners",
                        params={"reuse_on_duplicate": "true"},
                        json=payload,
                    )
                    assert r.status_code == 200, f"{payload} -> {r.text}"
                    body = r.json()
                    assert not body.get("_reused"), (
                        f"Round1 must CREATE, got _reused for {payload['vcs_code']}: {body}"
                    )
                    created_ids.add(body["id"])
                    owners_created.append(body["id"])
                assert len(created_ids) == 12, f"Expected 12 unique owners, got {len(created_ids)}"

                # Round 2 : re-import -> all 12 should be reused
                reused_count = 0
                for o in parsed["owners"]:
                    payload = {
                        "first_name": o["first_name"],
                        "last_name": o["last_name"],
                        "email": o["email"],
                        "copropriete_id": cid,
                        "vcs_code": o["vcs_code"],
                        "auxiliary_code": o["auxiliary_code"],
                    }
                    r = await c.post(
                        f"{BACKEND_URL}/api/owners",
                        params={"reuse_on_duplicate": "true"},
                        json=payload,
                    )
                    assert r.status_code == 200, r.text
                    body = r.json()
                    if body.get("_reused"):
                        reused_count += 1
                        assert body["id"] in created_ids
                assert reused_count == 12, f"Expected 12 reused, got {reused_count}"

                # Verify DB : exactly 12 owners for this ACP
                count = await db.owners.count_documents(
                    {"id": {"$in": list(created_ids)}}
                )
                assert count == 12, f"DB has {count} owners, expected 12"
        finally:
            await _cleanup(db, [cid], owners_created)
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Multi-ACP consolidation : same VCS across 2 ACPs -> single owner,
#    copropriete_ids merged
# ---------------------------------------------------------------------------
def test_multi_acp_consolidation_via_vcs():
    async def _run():
        db = await _mongo()
        cid_a, cid_b = f"acp-a-{_tag()}", f"acp-b-{_tag()}"
        await _seed_copro(db, cid_a)
        await _seed_copro(db, cid_b)
        owners_created = []
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                vcs = f"+++777/{_tag()[:4]}/{_tag()[:5]}+++"
                # Create in ACP A
                r1 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "MultiA", "last_name": "Consol",
                        "email": f"m-{_tag()}@t.l", "copropriete_id": cid_a,
                        "vcs_code": vcs,
                    },
                )
                assert r1.status_code == 200, r1.text
                o1 = r1.json()
                owners_created.append(o1["id"])

                # Re-import same VCS on ACP B
                r2 = await c.post(
                    f"{BACKEND_URL}/api/owners",
                    params={"reuse_on_duplicate": "true"},
                    json={
                        "first_name": "MultiA", "last_name": "Consol",
                        "email": f"m-{_tag()}@t.l", "copropriete_id": cid_b,
                        "vcs_code": vcs,
                    },
                )
                assert r2.status_code == 200, r2.text
                o2 = r2.json()
                assert o2.get("_reused") is True, f"Expected reuse: {o2}"
                assert o2["id"] == o1["id"]

                # DB : single owner, copropriete_ids should include both
                doc = await db.owners.find_one({"id": o1["id"]}, {"_id": 0})
                assert doc is not None
                cids = set(doc.get("copropriete_ids") or [])
                assert cid_a in cids and cid_b in cids, (
                    f"Expected both ACPs in copropriete_ids, got {cids}"
                )
        finally:
            await _cleanup(db, [cid_a, cid_b], owners_created)
    asyncio.run(_run())
