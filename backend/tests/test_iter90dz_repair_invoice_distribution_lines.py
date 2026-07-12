"""iter90dz - Endpoint de reparation retroactive des invoice.distribution_lines phantoms.

POST /api/invoices/repair-phantom-distribution-lines
- dry_run=true : rapporte les factures a reparer sans modifier
- dry_run=false : re-mappe les lot_ids phantoms vers les lot_ids actuels par lot_number
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return dict(resp.cookies)


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(tag: str):
    db = await _mongo()
    cid = f"iter90dz-repair-{tag}-{uuid.uuid4()}"
    owner_id = f"o-{uuid.uuid4().hex[:6]}"

    await db.coproprietes.insert_one({"id": cid, "name": "T", "status": "active"})
    await db.owners.insert_one({
        "id": owner_id, "name": "TEUWEN", "auxiliary_code": "T",
        "copropriete_ids": [cid],
    })

    # 2 lots reels : "001" et "002"
    L1_id = f"lot-{uuid.uuid4().hex[:6]}"
    L2_id = f"lot-{uuid.uuid4().hex[:6]}"
    await db.lots.insert_many([
        {"id": L1_id, "number": "001", "copropriete_id": cid,
         "owner_id": owner_id, "owner_ids": [owner_id], "quotity": 100.0},
        {"id": L2_id, "number": "002", "copropriete_id": cid,
         "owner_id": owner_id, "owner_ids": [owner_id], "quotity": 200.0},
    ])

    # Facture #1 : distribution_lines phantoms matchables (lot_number 001, 002)
    inv1_id = str(uuid.uuid4())
    phantom_l1 = f"phantom-{uuid.uuid4()}"
    phantom_l2 = f"phantom-{uuid.uuid4()}"
    await db.invoices.insert_one({
        "id": inv1_id, "number": "F001", "copropriete_id": cid,
        "date": "2026-01-01", "supplier": "S1", "total_amount": 300.0,
        "distribution_lines": [
            {"lot_id": phantom_l1, "lot_number": "001", "share": 100.0, "amount": 100.0},
            {"lot_id": phantom_l2, "lot_number": "002", "share": 200.0, "amount": 200.0},
        ],
    })

    # Facture #2 : distribution_lines deja OK (no phantom)
    inv2_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        "id": inv2_id, "number": "F002", "copropriete_id": cid,
        "date": "2026-01-02", "supplier": "S2", "total_amount": 100.0,
        "distribution_lines": [
            {"lot_id": L1_id, "lot_number": "001", "share": 100.0, "amount": 100.0},
        ],
    })

    # Facture #3 : phantom avec lot_number IRRESOLVABLE ("999" n'existe pas)
    inv3_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        "id": inv3_id, "number": "F003", "copropriete_id": cid,
        "date": "2026-01-03", "supplier": "S3", "total_amount": 50.0,
        "distribution_lines": [
            {"lot_id": f"phantom-{uuid.uuid4()}", "lot_number": "999",
             "share": 100.0, "amount": 50.0},
        ],
    })

    return {"db": db, "cid": cid, "owner_id": owner_id,
            "L1": L1_id, "L2": L2_id,
            "inv1_id": inv1_id, "inv2_id": inv2_id, "inv3_id": inv3_id,
            "phantom_l1": phantom_l1, "phantom_l2": phantom_l2}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    await db.owners.delete_one({"id": ctx["owner_id"]})
    for coll in ("lots", "invoices"):
        await db[coll].delete_many({"copropriete_id": cid})


async def _test_dry_run_reports_phantoms():
    ctx = await _setup("dry")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/repair-phantom-distribution-lines",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "dry_run": True,
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is True
            assert data["invoices_scanned"] == 3
            assert data["invoices_with_phantoms"] == 2  # inv1 + inv3
            assert data["invoices_repaired"] == 0  # dry-run
            # Verifie que la DB n'a pas ete modifiee
            inv1 = await ctx["db"].invoices.find_one({"id": ctx["inv1_id"]}, {"_id": 0})
            assert inv1["distribution_lines"][0]["lot_id"] == ctx["phantom_l1"]
    finally:
        await _cleanup(ctx)


async def _test_commit_repairs_resolvable_phantoms():
    ctx = await _setup("cmt")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/invoices/repair-phantom-distribution-lines",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"],
                    "dry_run": False,
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is False
            # Inv1 : 2 phantoms resolvables -> reparee
            # Inv3 : 1 phantom irresolvable (lot 999 n'existe pas) -> pas reparee
            assert data["invoices_repaired"] == 1
            assert data["lines_repaired_total"] == 2

            # Verifie Inv1 : lot_ids rebindees
            inv1 = await ctx["db"].invoices.find_one({"id": ctx["inv1_id"]}, {"_id": 0})
            dls = inv1["distribution_lines"]
            by_num = {d["lot_number"]: d for d in dls}
            assert by_num["001"]["lot_id"] == ctx["L1"]
            assert by_num["002"]["lot_id"] == ctx["L2"]
            assert "iter90dz_rebound_at" in by_num["001"]
            assert by_num["001"]["iter90dz_previous_lot_id"] == ctx["phantom_l1"]
            assert "iter90dz_repaired_at" in inv1

            # Verifie Inv2 : inchangee (n'avait pas de phantom)
            inv2 = await ctx["db"].invoices.find_one({"id": ctx["inv2_id"]}, {"_id": 0})
            assert inv2["distribution_lines"][0]["lot_id"] == ctx["L1"]
            assert "iter90dz_repaired_at" not in inv2

            # Verifie Inv3 : lot_id 999 non-resolvable -> lot_id INCHANGE
            inv3 = await ctx["db"].invoices.find_one({"id": ctx["inv3_id"]}, {"_id": 0})
            assert inv3["distribution_lines"][0]["lot_id"].startswith("phantom-")
    finally:
        await _cleanup(ctx)


async def _test_idempotence():
    ctx = await _setup("idem")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            cookies = await _login(client)
            # 1er commit
            r1 = await client.post(
                f"{BACKEND_URL}/api/invoices/repair-phantom-distribution-lines",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"], "dry_run": False,
                },
            )
            assert r1.status_code == 200
            # 2eme commit : rien a reparer sauf inv3 (irresolvable, non-modifie)
            r2 = await client.post(
                f"{BACKEND_URL}/api/invoices/repair-phantom-distribution-lines",
                cookies=cookies, json={
                    "copropriete_id": ctx["cid"], "dry_run": False,
                },
            )
            data = r2.json()
            # Inv1 est deja reparee -> plus de phantom sur elle
            # Inv3 reste irresolvable -> apparait dans invoices_with_phantoms
            assert data["invoices_repaired"] == 0
            assert data["lines_repaired_total"] == 0
            assert data["invoices_with_phantoms"] == 1  # inv3 only
    finally:
        await _cleanup(ctx)


def test_dry_run_reports_phantoms():
    asyncio.run(_test_dry_run_reports_phantoms())


def test_commit_repairs_resolvable_phantoms():
    asyncio.run(_test_commit_repairs_resolvable_phantoms())


def test_idempotence():
    asyncio.run(_test_idempotence())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
