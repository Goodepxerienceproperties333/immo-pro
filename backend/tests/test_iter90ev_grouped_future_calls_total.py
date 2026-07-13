"""iter90ev : bug critique lie a iter90eu - la somme des appels futurs
sur mutation groupee doit refleter la quote-part cumulee de tous les
lots, pas seulement le lot principal.

Scenario ticket utilisateur :
- Budget cle generale = 18800 EUR/an
- Cle generale = 10000 quotites total
- 3 lots meme owner : quotity 898 + 11 + 34 = 943
- Trimestre = 18800/4 = 4700 EUR par appel ACP
- Quote-part totale = 943/10000 * 4700 = 443.21 EUR par trimestre
- Bug : affichait 422.06 (= 898/10000 * 4700, main lot only)
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

    suffix = uuid.uuid4().hex[:6]
    cid = f"iter90ev-cid-{suffix}"
    fy_id = f"fy-{suffix}"
    o_sell = f"o-sell-{suffix}"
    o_buy = f"o-buy-{suffix}"
    l_app = f"l-app-{suffix}"
    l_cave = f"l-cave-{suffix}"
    l_park = f"l-park-{suffix}"
    dk_id = f"dk-{suffix}"
    budget_id = f"budg-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "Iter90evACP", "reference": "T90ev", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": "2025-2026",
        "start_date": "2025-10-01", "end_date": "2026-09-30", "status": "active"
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Copro tiers", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o_sell, "name": "Matexi Vendor", "last_name": "V", "auxiliary_code": "S001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o_buy, "name": "Teluwen G", "last_name": "B", "auxiliary_code": "B001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    await db.lots.insert_many([
        {"id": l_app, "number": "001", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 898.0, "lot_type": "Appartement"},
        {"id": l_cave, "number": "C01", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 11.0, "lot_type": "Cave"},
        {"id": l_park, "number": "pe01", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 34.0, "lot_type": "Parking"},
    ])
    await db.distribution_keys.insert_one({
        "id": dk_id, "copropriete_id": cid, "name": "Charges communes generales",
        "is_default": True, "key_type": "quotity", "total_quotity": 10000.0,
        "lots": [{"lot_id": l_app, "share": 898.0},
                 {"lot_id": l_cave, "share": 11.0},
                 {"lot_id": l_park, "share": 34.0}],
    })
    # Budget 18800 EUR/an - trimestriel -> 4 appels de 4700
    await db.budgets.insert_one({
        "id": budget_id, "copropriete_id": cid, "fiscal_year_id": fy_id,
        "distribution_key_id": dk_id, "type": "ordinary",
        "amount_total": 18800.0, "frequency": "quarterly",
        "start_date": "2025-10-01",
    })
    # Ajout d'un 4eme lot "OtherOwner" pour amener le total quotity du
    # denominateur a 10000 (comme en production).
    other_owner_id = f"o-oth-{suffix}"
    l_other = f"l-oth-{suffix}"
    await db.owners.insert_one({
        "id": other_owner_id, "name": "OtherOwner", "last_name": "OO",
        "auxiliary_code": "O001",
        "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "410"}},
    })
    await db.lots.insert_one({
        "id": l_other, "number": "OTH", "owner_id": other_owner_id, "owner_ids": [other_owner_id],
        "copropriete_id": cid, "quotity": 9057.0, "lot_type": "Autre",
    })
    await db.distribution_keys.update_one(
        {"id": dk_id},
        {"$push": {"lots": {"lot_id": l_other, "share": 9057.0}}},
    )

    # 4 appels trimestriels sur ACP : 4700 EUR chacun
    # Sale date : 2025-11-17 (mutation en cours du T1 2025-2026)
    # T1 : 2025-10-01 -> 2025-12-31 (en cours -> prorata)
    # T2 : 2026-01-01 -> 2026-03-31 (futur)
    # T3 : 2026-04-01 -> 2026-06-30 (futur)
    # T4 : 2026-07-01 -> 2026-09-30 (futur)
    for idx, (start, end, due) in enumerate([
        ("2025-10-01", "2025-12-31", "2025-10-01"),
        ("2026-01-01", "2026-03-31", "2026-01-31"),
        ("2026-04-01", "2026-06-30", "2026-05-01"),
        ("2026-07-01", "2026-09-30", "2026-07-31"),
    ]):
        await db.fund_calls.insert_one({
            "id": f"fc-{suffix}-{idx}", "copropriete_id": cid, "fiscal_year_id": fy_id,
            "budget_id": budget_id, "distribution_key_id": dk_id,
            "name": f"Trimestriel {idx + 1}/4 - Exercice 2025-2026",
            "date": start, "due_date": due, "period_start": start, "period_end": end,
            "amount": 4700.0, "call_type": "provisions", "status": "issued",
            # Le calcul lot_amount passe par les `lines` (cas 2 de _compute_lot_amount_in_call)
            "lines": [{"distribution_key_id": dk_id, "amount": 4700.0}],
            "total_amount": 4700.0,
        })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o_sell": o_sell, "o_buy": o_buy,
            "l_app": l_app, "l_cave": l_cave, "l_park": l_park,
            "dk_id": dk_id, "budget_id": budget_id, "suffix": suffix}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["o_sell"], ctx["o_buy"]]}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.budgets.delete_many({"copropriete_id": cid})
    await db.fund_calls.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def _endpoint(db, path, method="POST"):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == path and method.upper() in r.methods:
            return r.endpoint
    return None


def test_preview_grouped_future_calls_amount_equals_cumulated_quotities():
    """Chaque appel futur doit refleter 943/10000 * 4700 = 443.21 EUR quand
    on somme les per_lot_breakdowns[].future_calls."""
    async def _run():
        ctx = await _setup()
        db = ctx["db"]
        try:
            preview_fn = _endpoint(db, "/api/lots/{lot_id}/mutate-preview", "POST")
            PreviewIn = preview_fn.__annotations__.get("data")
            payload = PreviewIn(
                new_owner_id=ctx["o_buy"],
                sale_date="2025-11-17",
                additional_lot_ids=[ctx["l_cave"], ctx["l_park"]],
            )
            result = await preview_fn(lot_id=ctx["l_app"], data=payload)

            per_lot = result.get("per_lot_breakdowns") or []
            assert len(per_lot) == 3

            # Le lot principal expose ses future_calls (main only) - 422.06 attendu par appel
            main = next(b for b in per_lot if b["lot_number"] == "001")
            main_fut = main.get("future_calls") or []
            assert len(main_fut) >= 3, f"Main should have >=3 future calls, got {len(main_fut)}"
            for fc in main_fut:
                assert abs(fc["amount"] - 422.06) < 0.02, (
                    f"Main lot 001 appel futur attendu 422.06, got {fc['amount']}"
                )

            # Aggregation manuelle : main + cave + park par fund_call_id
            byid = {}
            for b in per_lot:
                for fc in (b.get("future_calls") or []):
                    key = fc.get("fund_call_id")
                    byid.setdefault(key, 0.0)
                    byid[key] += fc["amount"]
            expected = 943.0 / 10000.0 * 4700.0  # 443.21
            for k, total in byid.items():
                assert abs(total - expected) < 0.02, (
                    f"Appel {k} : attendu {expected:.2f} (cumule 943/10000), got {total:.2f}"
                )
            # Total agrege backend
            grouped_total = float(result.get("grouped_total_future", 0))
            expected_sum = expected * len(byid)
            assert abs(grouped_total - expected_sum) < 0.02, (
                f"grouped_total_future attendu {expected_sum:.2f}, got {grouped_total:.2f}"
            )
        finally:
            await _cleanup(ctx)

    asyncio.run(_run())


if __name__ == "__main__":
    test_preview_grouped_future_calls_amount_equals_cumulated_quotities()
    print("OK")
