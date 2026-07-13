"""iter90eu : bug critique - la preview mutation groupee doit exposer
correctement le total du fonds de roulement agrege sur TOUS les lots
(principal + lies + additionnels), afin que le Bloc 1 du frontend
n'affiche pas seulement le lot principal.

Scenario reproduit du ticket utilisateur :
- Lot 001 : quotity 898, roulement quota = 466.96
- Lot C01 : quotity 11,  roulement quota = 5.72
- Lot pe01 : quotity 34,  roulement quota = 17.68
- Bloc 1 attendu : 490.36 EUR (= 466.96 + 5.72 + 17.68)
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
    cid = f"iter90eu-cid-{suffix}"
    o_sell = f"o-sell-{suffix}"
    o_buy = f"o-buy-{suffix}"
    l_app = f"l-app-{suffix}"
    l_cave = f"l-cave-{suffix}"
    l_park = f"l-park-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "Iter90euACP", "reference": "T90eu", "status": "active"})
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Copro tiers", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o_sell, "name": "Matexi Vendor", "last_name": "Vendor", "auxiliary_code": "S001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o_buy, "name": "Teluwen G", "last_name": "Buyer", "auxiliary_code": "B001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # 3 lots du meme proprietaire : quotities 898 / 11 / 34
    await db.lots.insert_many([
        {"id": l_app, "number": "001", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 898.0, "lot_type": "Appartement"},
        {"id": l_cave, "number": "C01", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 11.0, "lot_type": "Cave"},
        {"id": l_park, "number": "pe01", "owner_id": o_sell, "owner_ids": [o_sell],
         "copropriete_id": cid, "quotity": 34.0, "lot_type": "Parking"},
    ])
    # Cle par defaut : quotities 898 + 11 + 34 = 943 / 10000
    await db.distribution_keys.insert_one({
        "id": f"dk-90eu-{suffix}", "copropriete_id": cid, "name": "Charges communes generales",
        "is_default": True, "key_type": "quotity",
        "total_quotity": 10000.0,
        "lots": [{"lot_id": l_app, "share": 898.0},
                 {"lot_id": l_cave, "share": 11.0},
                 {"lot_id": l_park, "share": 34.0}],
    })
    # Solde fonds de roulement ACP = 5200 EUR
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01", "copropriete_id": cid,
        "lines": [{"account_number": "410", "debit": 5200.0, "credit": 0.0},
                  {"account_number": "100", "debit": 0.0, "credit": 5200.0}],
        "total_debit": 5200.0, "total_credit": 5200.0,
    })
    return {"db": db, "cid": cid, "o_sell": o_sell, "o_buy": o_buy,
            "l_app": l_app, "l_cave": l_cave, "l_park": l_park}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o_sell"], ctx["o_buy"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.distribution_keys.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _endpoint(db, path, method="POST"):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == path and method.upper() in r.methods:
            return r.endpoint
    return None


def test_preview_grouped_mutation_returns_total_roulement_across_all_lots():
    async def _run():
        ctx = await _setup()
        db = ctx["db"]
        try:
            preview_fn = _endpoint(db, "/api/lots/{lot_id}/mutate-preview", "POST")
            assert preview_fn, "Endpoint mutate-preview not found"
            PreviewIn = preview_fn.__annotations__.get("data")
            # Preview mutation groupee : lot principal 001 + additional_lot_ids=[C01, pe01]
            payload = PreviewIn(
                new_owner_id=ctx["o_buy"],
                sale_date="2026-06-15",
                additional_lot_ids=[ctx["l_cave"], ctx["l_park"]],
            )
            result = await preview_fn(lot_id=ctx["l_app"], data=payload)

            per_lot = result.get("per_lot_breakdowns") or []
            assert len(per_lot) == 3, f"Expected 3 lots, got {len(per_lot)}"
            # Sum roulement individuels
            sum_roul = sum(float(b.get("roulement_quota", 0) or 0) for b in per_lot)
            grouped_total = float(result.get("grouped_total_roulement", 0) or 0)
            assert abs(grouped_total - sum_roul) < 0.01, (
                f"grouped_total_roulement ({grouped_total}) != sum of per_lot ({sum_roul})"
            )
            # Sanity checks : chaque lot doit avoir un roulement > 0
            assert all(float(b["roulement_quota"]) > 0 for b in per_lot), (
                "Chaque lot doit avoir sa quote-part roulement calculee"
            )
            # Le lot principal (001) doit avoir la plus grosse quote-part
            main = next(b for b in per_lot if b["lot_number"] == "001")
            others = [b for b in per_lot if b["lot_number"] != "001"]
            assert main["roulement_quota"] > sum(b["roulement_quota"] for b in others)
            # Confirme que le total groupe est significativement > lot principal seul
            assert grouped_total > main["roulement_quota"], (
                f"grouped_total ({grouped_total}) doit etre > main only ({main['roulement_quota']}) "
                "-> preuve que les lots secondaires sont bien ajoutes."
            )
        finally:
            await _cleanup(ctx)

    asyncio.run(_run())


if __name__ == "__main__":
    test_preview_grouped_mutation_returns_total_roulement_across_all_lots()
    print("OK")
