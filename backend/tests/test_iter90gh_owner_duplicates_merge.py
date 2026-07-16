"""iter90gh : detection + merge des fiches owner en doublon dans une ACP.

**Ticket utilisateur** :
> "dans la liste deroulante je retrouve matexi repete ce n'est pas normal"

**Cause** : imports Optipro/CODA repetes creent une nouvelle fiche owner
a chaque passage au lieu de matcher sur nom + BCE + email.

**Fix iter90gh** :
1. `GET /api/admin/owners/duplicates-diagnostic?copropriete_id=X` : detecte
   les groupes de doublons par nom normalise, propose un MASTER (le plus
   complet).
2. `POST /api/admin/owners/merge-duplicates` : fusionne N slaves dans 1
   master (propage sur lots, invoices, mutations, journal_entries,
   fund_calls, bank_accounts) puis supprime les slaves. Chinese wall.
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
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_dupes(db, suffix: str) -> dict:
    """Setup : 1 ACP + 3 fiches "Matexi" (1 master + 2 slaves) + 1 lot."""
    cid = f"iter90gh-{suffix}"
    master_id = f"master-{suffix}"
    slave1_id = f"slave1-{suffix}"
    slave2_id = f"slave2-{suffix}"
    lot_id = f"lot-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gh-{suffix}"})
    # Master : le plus complet (VCS + aux + email)
    await db.owners.insert_one({
        "id": master_id, "name": "Matexi", "last_name": "Matexi",
        "vcs_code": "+++123/4567/89012+++",
        "auxiliary_code": "AUX-001",
        "email": "matexi@example.be",
        "copropriete_ids": [cid],
    })
    # Slave 1 : moins complet (juste email)
    await db.owners.insert_one({
        "id": slave1_id, "name": "Matexi",
        "email": "matexi_dup@example.be",
        "copropriete_ids": [cid],
    })
    # Slave 2 : quasi vide
    await db.owners.insert_one({
        "id": slave2_id, "name": "MATEXI",  # <-- casse differente pour tester norm
        "copropriete_ids": [cid],
    })
    # Un lot avec le master
    await db.lots.insert_one({
        "id": lot_id, "number": "L1", "copropriete_id": cid,
        "owner_id": slave1_id,  # <-- point vers SLAVE 1 (a re-assigner au master)
        "quotity": 100,
    })
    return {"cid": cid, "master_id": master_id,
            "slave1_id": slave1_id, "slave2_id": slave2_id, "lot_id": lot_id}


async def _cleanup(db, cid: str, ids: list):
    await db.coproprietes.delete_one({"id": cid})
    await db.owners.delete_many({"id": {"$in": ids}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.mutations.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_iter90gh_diagnostic_detects_duplicates():
    """Le diagnostic detecte 1 groupe (Matexi) avec 2 slaves."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_dupes(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.get(
                    f"{BACKEND_URL}/api/admin/owners/duplicates-diagnostic"
                    f"?copropriete_id={ctx['cid']}"
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["total_groups"] == 1, (
                    f"1 groupe attendu, recu {data['total_groups']}"
                )
                assert data["total_slaves_to_merge"] == 2
                g = data["groups"][0]
                assert g["normalized_name"] == "matexi"
                assert g["master"]["id"] == ctx["master_id"], (
                    f"Master doit etre le plus complet. Recu : {g['master']}"
                )
                slave_ids = [s["id"] for s in g["slaves"]]
                assert ctx["slave1_id"] in slave_ids
                assert ctx["slave2_id"] in slave_ids
        finally:
            await _cleanup(db, ctx["cid"], [ctx["master_id"], ctx["slave1_id"], ctx["slave2_id"]])
    asyncio.run(_run())


def test_iter90gh_merge_dry_run_reports_counts_without_writing():
    """dry_run=True retourne les counts d'impact sans rien modifier."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_dupes(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/owners/merge-duplicates",
                    json={
                        "copropriete_id": ctx["cid"],
                        "master_id": ctx["master_id"],
                        "slave_ids": [ctx["slave1_id"], ctx["slave2_id"]],
                        "dry_run": True,
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["dry_run"] is True
                assert data["lots_updated"] == 1, (
                    f"1 lot doit etre impacte (owner_id=slave1). Recu {data}"
                )
                assert data["slaves_deleted"] == 0
                # Verifie que les slaves existent toujours en base
                s1 = await db.owners.find_one({"id": ctx["slave1_id"]})
                assert s1 is not None, "dry_run ne doit pas supprimer"
        finally:
            await _cleanup(db, ctx["cid"], [ctx["master_id"], ctx["slave1_id"], ctx["slave2_id"]])
    asyncio.run(_run())


def test_iter90gh_merge_apply_propagates_and_deletes():
    """dry_run=False propage sur lots + supprime les slaves."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_dupes(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/owners/merge-duplicates",
                    json={
                        "copropriete_id": ctx["cid"],
                        "master_id": ctx["master_id"],
                        "slave_ids": [ctx["slave1_id"], ctx["slave2_id"]],
                        "dry_run": False,
                    },
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["slaves_deleted"] == 2
                # Verifie que lot.owner_id pointe maintenant vers master
                lot = await db.lots.find_one({"id": ctx["lot_id"]}, {"_id": 0})
                assert lot["owner_id"] == ctx["master_id"], (
                    f"lot.owner_id doit etre migre vers master. Recu {lot['owner_id']}"
                )
                # Verifie que les slaves sont supprimes
                s1 = await db.owners.find_one({"id": ctx["slave1_id"]})
                s2 = await db.owners.find_one({"id": ctx["slave2_id"]})
                assert s1 is None and s2 is None, "Slaves doivent etre supprimes"
                # Master doit toujours exister
                m = await db.owners.find_one({"id": ctx["master_id"]})
                assert m is not None
        finally:
            await _cleanup(db, ctx["cid"], [ctx["master_id"], ctx["slave1_id"], ctx["slave2_id"]])
    asyncio.run(_run())


def test_iter90gh_merge_rejects_master_in_slaves():
    """400 si master_id est dans slave_ids."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_dupes(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/admin/owners/merge-duplicates",
                    json={
                        "copropriete_id": ctx["cid"],
                        "master_id": ctx["master_id"],
                        "slave_ids": [ctx["master_id"], ctx["slave1_id"]],
                        "dry_run": True,
                    },
                )
                assert r.status_code == 400
        finally:
            await _cleanup(db, ctx["cid"], [ctx["master_id"], ctx["slave1_id"], ctx["slave2_id"]])
    asyncio.run(_run())


def test_iter90gh_diagnostic_non_superadmin_403():
    """Non-superadmin refuse."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        from server import hash_password
        email = f"synd_gh_{suffix}@t.be"
        await db.users.insert_one({
            "email": email, "name": "S", "role": "syndic",
            "password_hash": hash_password("test1234"),
        })
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{BACKEND_URL}/api/auth/login",
                                 json={"email": email, "password": "test1234"})
                assert r.status_code == 200
                r2 = await c.get(
                    f"{BACKEND_URL}/api/admin/owners/duplicates-diagnostic"
                    f"?copropriete_id=fake"
                )
                assert r2.status_code == 403
        finally:
            await db.users.delete_one({"email": email})
    asyncio.run(_run())
