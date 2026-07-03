"""
Iter90ab : Fonds de roulement en mutation calcule sur la cle de repartition
GENERALE (is_default=true) et non plus sur lot.quotity.

Regle metier :
- Provisions -> _compute_lot_amount_in_call (INCHANGE, cle par ligne budget)
- Roulement/reserve -> cle par defaut (celle avec is_default=true)
- lot.quotity n'est plus la source de verite

Trois scenarios :
1. Le lot a une quotity 1000 mais dans la cle par defaut sa share=500 sur
   1500. Solde roulement = 3000 EUR. Attendu : 3000 * 500/1500 = 1000 EUR
   (et NON 3000 * 1000/1500 = 2000 EUR qui serait le calcul via lot.quotity).
2. Absence de cle par defaut -> 400 avec message explicite.
3. Lot absent de la cle par defaut -> 400 avec message explicite.
"""
import asyncio
import os
import sys
import uuid
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _mk_ctx(db, name: str):
    cid = f"iter90ab-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    lot_a = f"lot-a-{uuid.uuid4()}"
    lot_b = f"lot-b-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": name, "reference": "T-" + name, "status": "active"})
    await db.fiscal_years.insert_one({"id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid})
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "V", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "A", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Vendeur", "last_name": "V", "auxiliary_code": "C0001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acheteur", "last_name": "A", "auxiliary_code": "C0002",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # lot A : quotity=1000, lot B : quotity=500 (mais share dans la cle sera differente)
    await db.lots.insert_many([
        {"id": lot_a, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 1000.0},
        {"id": lot_b, "number": "B1", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 500.0},
    ])
    # Solde fonds de roulement = 3000 EUR
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01",
        "copropriete_id": cid,
        "lines": [{"account_number": "4100001", "debit": 3000.0, "credit": 0.0},
                  {"account_number": "100", "debit": 0.0, "credit": 3000.0}],
        "total_debit": 3000.0, "total_credit": 3000.0,
    })
    return {"db": db, "cid": cid, "fy_id": fy_id,
            "o1": o1_id, "o2": o2_id, "lot_a": lot_a, "lot_b": lot_b}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "owners", "lots",
                 "journal_entries", "distribution_keys", "fund_calls",
                 "mutation_records"):
        await db[coll].delete_many({"copropriete_id": cid})


def _get_preview_fn(db):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == "/api/lots/{lot_id}/mutate-preview":
            return r.endpoint
    return None


async def _scenario_default_key():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await _mk_ctx(db, "iter90ab_default_key")
    try:
        await db.distribution_keys.insert_one({
            "id": f"dk-default-{ctx['cid'][:8]}", "copropriete_id": ctx["cid"],
            "name": "Generale", "is_default": True, "key_type": "quotity",
            "lots": [
                {"lot_id": ctx["lot_a"], "share": 500.0},
                {"lot_id": ctx["lot_b"], "share": 1000.0},
            ],
        })
        preview_fn = _get_preview_fn(db)
        PreviewInput = preview_fn.__annotations__.get("data")
        preview = await preview_fn(
            lot_id=ctx["lot_a"],
            data=PreviewInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"),
        )
        assert preview["default_key_name"] == "Generale", preview
        assert preview["lot_share_in_key"] == 500.0
        assert preview["key_total_quotity"] == 1500.0
        # 3000 * 500 / 1500 = 1000.00 EUR (et NON 3000*1000/1500=2000)
        assert preview["roulement_quota"] == 1000.00, f"attendu 1000, obtenu {preview['roulement_quota']}"
        # Verifie que l'ancien champ n'est plus retourne
        assert "lot_quotity" not in preview, "lot_quotity ne doit plus etre expose"
        assert "total_quotity" not in preview, "total_quotity ne doit plus etre expose"
    finally:
        await _cleanup(ctx)


async def _scenario_no_default_key():
    from fastapi import HTTPException
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await _mk_ctx(db, "iter90ab_no_default")
    try:
        # Une cle mais SANS is_default
        await db.distribution_keys.insert_one({
            "id": f"dk-no-default-{ctx['cid'][:8]}", "copropriete_id": ctx["cid"],
            "name": "Ascenseurs", "is_default": False, "key_type": "quotity",
            "lots": [{"lot_id": ctx["lot_a"], "share": 1.0}],
        })
        preview_fn = _get_preview_fn(db)
        PreviewInput = preview_fn.__annotations__.get("data")
        try:
            await preview_fn(
                lot_id=ctx["lot_a"],
                data=PreviewInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"),
            )
            assert False, "HTTPException 400 attendue"
        except HTTPException as e:
            assert e.status_code == 400, e
            assert "cle de repartition par defaut" in e.detail.lower(), e.detail
    finally:
        await _cleanup(ctx)


async def _scenario_lot_absent_from_default_key():
    from fastapi import HTTPException
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await _mk_ctx(db, "iter90ab_lot_missing")
    try:
        # cle par defaut SANS lot_a
        await db.distribution_keys.insert_one({
            "id": f"dk-missing-{ctx['cid'][:8]}", "copropriete_id": ctx["cid"],
            "name": "Generale", "is_default": True, "key_type": "quotity",
            "lots": [{"lot_id": ctx["lot_b"], "share": 100.0}],
        })
        preview_fn = _get_preview_fn(db)
        PreviewInput = preview_fn.__annotations__.get("data")
        try:
            await preview_fn(
                lot_id=ctx["lot_a"],
                data=PreviewInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"),
            )
            assert False, "HTTPException 400 attendue"
        except HTTPException as e:
            assert e.status_code == 400, e
            assert "n'est pas dans la cle par defaut" in e.detail.lower(), e.detail
    finally:
        await _cleanup(ctx)


def test_roulement_uses_default_key_not_quotity():
    asyncio.run(_scenario_default_key())


def test_roulement_400_when_no_default_key():
    asyncio.run(_scenario_no_default_key())


def test_roulement_400_when_lot_absent_from_default_key():
    asyncio.run(_scenario_lot_absent_from_default_key())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
