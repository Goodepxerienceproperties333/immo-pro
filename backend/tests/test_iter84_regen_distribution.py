"""Regression test - iter84 - Regeneration de la distribution des appels.

Bug reporte par l'utilisateur :
"Il manque les appels de provisions pour charges jusqu'a la fin de l'annee,
il y a encore des appels en janvier, avril et juillet !! Ou sont-ils?"

Cause : Q2/Q3/Q4 d'une serie trimestrielle se retrouvent avec
        `distribution = []` alors que les `lines` budget sont presentes.

Fix : 2 endpoints
  - POST /api/fund-calls/{id}/regenerate-distribution
  - POST /api/fund-calls/regenerate-empty-distributions?copropriete_id=X

Tests :
  1. Regen single call avec distribution vide -> distribution recalculee
  2. Bulk regen -> ne touche pas les appels avec paiements (history protection)
  3. Bulk regen -> compte fixed/skipped correctement
  4. Refuse single regen si paiements presents
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr84r-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1, o2, o3 = (f"o-{uuid.uuid4()}" for _ in range(3))
    lot1, lot2, lot3 = (f"l-{uuid.uuid4()}" for _ in range(3))
    key_id = f"k-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "RGN", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.owners.insert_many([
        {"id": o1, "name": "Alpha A", "first_name": "Alpha", "last_name": "A",
         "copropriete_id": cid, "vcs_code": f"+++111/2222/{uuid.uuid4().hex[:5]}+++"},
        {"id": o2, "name": "Beta B", "first_name": "Beta", "last_name": "B",
         "copropriete_id": cid, "vcs_code": f"+++333/4444/{uuid.uuid4().hex[:5]}+++"},
        {"id": o3, "name": "Gamma G", "first_name": "Gamma", "last_name": "G",
         "copropriete_id": cid, "vcs_code": f"+++555/6666/{uuid.uuid4().hex[:5]}+++"},
    ])
    await db.lots.insert_many([
        {"id": lot1, "number": "A1", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 100},
        {"id": lot2, "number": "A2", "owner_id": o2, "owner_ids": [o2],
         "copropriete_id": cid, "quotity": 200},
        {"id": lot3, "number": "B1", "owner_id": o3, "owner_ids": [o3],
         "copropriete_id": cid, "quotity": 300},
    ])
    await db.distribution_keys.insert_one({
        "id": key_id, "name": "Charges generales", "copropriete_id": cid,
        "lots": [
            {"lot_id": lot1, "share": 100},
            {"lot_id": lot2, "share": 200},
            {"lot_id": lot3, "share": 300},
        ],
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1, "o2": o2, "o3": o3,
            "lots": [lot1, lot2, lot3], "key_id": key_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"copropriete_id": ctx["cid"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.distribution_keys.delete_one({"id": ctx["key_id"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str):
    from routes.fund_calls import create_fund_calls_router
    router = create_fund_calls_router(db)
    for r in router.routes:
        if r.path == route_path:
            return r.endpoint
    return None


async def _make_broken_call(ctx, name="Q2 - 2026", date="2026-04-01", total=600.0):
    """Cree un appel avec `lines` mais SANS distribution (le bug)."""
    db = ctx["db"]
    call_id = f"c-{uuid.uuid4()}"
    await db.fund_calls.insert_one({
        "id": call_id, "name": name, "date": date, "due_date": date,
        "period_start": "2026-04-01", "period_end": "2026-06-30",
        "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
        "call_type": "provisions", "total_amount": total,
        "lines": [{
            "account_number": "6100",
            "account_name": "Charges generales",
            "amount": total,
            "distribution_key_id": ctx["key_id"],
            "distribution_key_name": "Charges generales",
        }],
        "distribution": [],  # VIDE - le bug
    })
    return call_id


async def _test_single_regen():
    ctx = await _setup()
    db = ctx["db"]
    try:
        call_id = await _make_broken_call(ctx, total=600.0)
        endpoint = _get_endpoint(db, "/api/fund-calls/{call_id}/regenerate-distribution")
        assert endpoint is not None
        result = await endpoint(call_id=call_id)
        assert result["distribution_count"] == 3
        assert abs(result["recalculated_total"] - 600.0) < 0.01
        # Verifie persistence
        call_after = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        dist = call_after.get("distribution") or []
        assert len(dist) == 3
        amounts = sorted(d["amount"] for d in dist)
        # Shares 100/200/300 = 1/6, 1/3, 1/2 of 600 = 100, 200, 300
        assert amounts == sorted([100.0, 200.0, 300.0])
        # Verifie le mapping owner correct
        by_lot = {d["lot_number"]: d for d in dist}
        assert by_lot["A1"]["owner_id"] == ctx["o1"]
        assert by_lot["A2"]["owner_id"] == ctx["o2"]
        assert by_lot["B1"]["owner_id"] == ctx["o3"]
        print("OK - single regen rebuilds distribution from lines+key")
    finally:
        await _cleanup(ctx)


async def _test_bulk_regen_scope_and_skip_paid():
    ctx = await _setup()
    db = ctx["db"]
    try:
        # 3 broken calls Q2/Q3/Q4
        c2 = await _make_broken_call(ctx, "Q2", "2026-04-01")
        c3 = await _make_broken_call(ctx, "Q3", "2026-07-01")
        c4 = await _make_broken_call(ctx, "Q4", "2026-10-01")
        # 1 broken-with-payment (doit etre skipped)
        c_paid_id = f"c-{uuid.uuid4()}"
        await db.fund_calls.insert_one({
            "id": c_paid_id, "name": "Q1 paid", "date": "2026-01-01",
            "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
            "call_type": "provisions", "total_amount": 600.0,
            "lines": [{
                "account_number": "6100", "amount": 600.0,
                "distribution_key_id": ctx["key_id"],
            }],
            "distribution": [{
                "lot_id": ctx["lots"][0], "owner_id": ctx["o1"],
                "amount": 0, "share": 0, "paid": True, "paid_date": "2026-01-15",
            }],
        })
        # 1 OK call (deja correct, ne doit pas etre touche)
        c_ok_id = f"c-{uuid.uuid4()}"
        await db.fund_calls.insert_one({
            "id": c_ok_id, "name": "Q5 OK", "date": "2026-12-01",
            "fiscal_year_id": ctx["fy_id"], "copropriete_id": ctx["cid"],
            "call_type": "provisions", "total_amount": 600.0,
            "lines": [{"account_number": "6100", "amount": 600.0, "distribution_key_id": ctx["key_id"]}],
            "distribution": [{
                "lot_id": ctx["lots"][0], "owner_id": ctx["o1"],
                "amount": 600.0, "share": 100, "paid": False,
            }],
        })

        endpoint = _get_endpoint(db, "/api/fund-calls/regenerate-empty-distributions")
        # Mock minimal request
        class _Req:
            headers = {}
        result = await endpoint(_Req(), copropriete_id=ctx["cid"])
        assert result["fixed_count"] == 3, f"Attendu 3 fixes (Q2/Q3/Q4), recu {result['fixed_count']}"
        assert len(result["skipped_paid"]) == 1, f"Attendu 1 skipped paid, recu {len(result['skipped_paid'])}"
        # Verifie que Q5 OK n'a pas ete touche
        c_ok = await db.fund_calls.find_one({"id": c_ok_id}, {"_id": 0})
        assert len(c_ok["distribution"]) == 1
        # Verifie que les 3 broken sont fixes
        for cid in (c2, c3, c4):
            c = await db.fund_calls.find_one({"id": cid}, {"_id": 0})
            assert len(c["distribution"]) == 3
            assert sum(d["amount"] for d in c["distribution"]) == 600.0
        # Verifie que le paid n'a pas ete touche
        c_paid = await db.fund_calls.find_one({"id": c_paid_id}, {"_id": 0})
        assert len(c_paid["distribution"]) == 1
        assert c_paid["distribution"][0]["paid"] is True
        print("OK - bulk regen fixes Q2/Q3/Q4 + skip paid + preserve OK")
    finally:
        await _cleanup(ctx)


async def _test_single_regen_refuses_paid():
    ctx = await _setup()
    db = ctx["db"]
    try:
        from fastapi import HTTPException
        call_id = f"c-{uuid.uuid4()}"
        await db.fund_calls.insert_one({
            "id": call_id, "name": "paid call", "date": "2026-01-01",
            "copropriete_id": ctx["cid"], "call_type": "provisions", "total_amount": 600.0,
            "lines": [{"account_number": "6100", "amount": 600.0, "distribution_key_id": ctx["key_id"]}],
            "distribution": [{
                "lot_id": ctx["lots"][0], "owner_id": ctx["o1"],
                "amount": 600.0, "share": 100, "paid": True,
            }],
        })
        endpoint = _get_endpoint(db, "/api/fund-calls/{call_id}/regenerate-distribution")
        try:
            await endpoint(call_id=call_id)
            assert False, "Doit lever HTTPException"
        except HTTPException as e:
            assert e.status_code == 400
            assert "paiement" in e.detail.lower()
        print("OK - single regen refuse si paiements presents")
    finally:
        await _cleanup(ctx)


async def _test_bulk_regen_chinese_wall():
    """copropriete_id obligatoire pour chinese wall strict."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        from fastapi import HTTPException
        endpoint = _get_endpoint(db, "/api/fund-calls/regenerate-empty-distributions")

        class _Req:
            headers = {}
        try:
            await endpoint(_Req(), copropriete_id=None)
            assert False
        except HTTPException as e:
            assert e.status_code == 400
            assert "copropriete_id" in e.detail.lower() or "chinese wall" in e.detail.lower()
        # Reject "all" too
        try:
            await endpoint(_Req(), copropriete_id="all")
            assert False
        except HTTPException as e:
            assert e.status_code == 400
        print("OK - chinese wall strict (copropriete_id requis)")
    finally:
        await _cleanup(ctx)


def test_iter84_single_regen():
    asyncio.run(_test_single_regen())


def test_iter84_bulk_regen():
    asyncio.run(_test_bulk_regen_scope_and_skip_paid())


def test_iter84_regen_refuses_paid():
    asyncio.run(_test_single_regen_refuses_paid())


def test_iter84_bulk_regen_chinese_wall():
    asyncio.run(_test_bulk_regen_chinese_wall())
