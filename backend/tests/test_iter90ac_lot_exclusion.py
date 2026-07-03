"""
Iter90ac : Concept d'exclusion sur les lots d'une cle de repartition.

Regle metier :
- Un lot peut etre explicitement exclu d'une cle (excluded=True).
- L'exclusion est distincte d'un share=0 : elle indique une non-participation
  metier (ex : lot commercial exclu des charges d'ascenseur).
- Le denominateur des cles ignore les lots exclus.
- Une mutation sur un lot exclu de la cle par defaut retourne
  roulement_quota=0.0 sans erreur.

Scenarios :
1. Preview mutation : lot A exclu (share=500 ignore), lot B share=1000 seul
   participe -> denominateur=1000 ; lot A -> roulement=0 sans HTTP 400.
2. Preview mutation : lot B (non exclu), denominateur ignore lot A ->
   3000 * 1000/1000 = 3000 EUR (et non 3000 * 1000/1500=2000 comme sans
   exclusion).
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
    cid = f"iter90ac-{uuid.uuid4()}"
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
    await db.lots.insert_many([
        {"id": lot_a, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 500.0},
        {"id": lot_b, "number": "B1", "owner_id": o1_id, "owner_ids": [o1_id],
         "copropriete_id": cid, "quotity": 1000.0},
    ])
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


async def _scenario_excluded_lot_mutation():
    """Mutation sur un lot EXCLU -> roulement_quota=0, pas d'erreur HTTP 400."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await _mk_ctx(db, "iter90ac_excluded_lot")
    try:
        # lot_a est EXCLU (excluded=True), lot_b actif
        await db.distribution_keys.insert_one({
            "id": f"dk-excl-{ctx['cid'][:8]}", "copropriete_id": ctx["cid"],
            "name": "Generale", "is_default": True, "key_type": "quotity",
            "lots": [
                {"lot_id": ctx["lot_a"], "share": 500.0, "excluded": True},
                {"lot_id": ctx["lot_b"], "share": 1000.0, "excluded": False},
            ],
        })
        preview_fn = _get_preview_fn(db)
        PreviewInput = preview_fn.__annotations__.get("data")
        # 1) Mutation du lot EXCLU -> 0 EUR sans erreur
        preview_a = await preview_fn(
            lot_id=ctx["lot_a"],
            data=PreviewInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"),
        )
        assert preview_a["lot_excluded_from_key"] is True, preview_a
        assert preview_a["lot_share_in_key"] == 0.0
        assert preview_a["roulement_quota"] == 0.0
        # denominateur = seulement lot_b (1000), pas 1500
        assert preview_a["key_total_quotity"] == 1000.0

        # 2) Mutation du lot NON exclu -> denominateur ignore lot_a
        preview_b = await preview_fn(
            lot_id=ctx["lot_b"],
            data=PreviewInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"),
        )
        assert preview_b["lot_excluded_from_key"] is False
        assert preview_b["lot_share_in_key"] == 1000.0
        assert preview_b["key_total_quotity"] == 1000.0
        # 3000 * 1000 / 1000 = 3000 EUR (car lot_a exclu du calcul)
        assert preview_b["roulement_quota"] == 3000.00, preview_b
    finally:
        await _cleanup(ctx)


async def _scenario_backward_compat_no_excluded_field():
    """Cles legacy sans champ 'excluded' -> comportement inchange (backward compat)."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await _mk_ctx(db, "iter90ac_legacy_compat")
    try:
        # Cle sans champ 'excluded' (comme les cles legacy avant iter90ac)
        await db.distribution_keys.insert_one({
            "id": f"dk-legacy-{ctx['cid'][:8]}", "copropriete_id": ctx["cid"],
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
        # Pas de exclusion -> tous les lots participent, denominateur = 1500
        assert preview["lot_excluded_from_key"] is False
        assert preview["key_total_quotity"] == 1500.0
        assert preview["lot_share_in_key"] == 500.0
        assert preview["roulement_quota"] == 1000.00
    finally:
        await _cleanup(ctx)


def test_excluded_lot_mutation_gives_zero():
    asyncio.run(_scenario_excluded_lot_mutation())


def test_legacy_keys_without_excluded_field_still_work():
    asyncio.run(_scenario_backward_compat_no_excluded_field())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
