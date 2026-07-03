"""Regression test - iter83 - Mutation prorata utilise le lot et non l'owner.

Demande user : "Lors du transfert tu ne dois transferer que le prorata des
quotites generales et pas le total general des appels, donc uniquement les
quotites des lots pour lequel la mutation a lieu."

Bug : lorsque le vendeur possede PLUSIEURS lots dans la meme ACP et donc
plusieurs lignes dans la distribution d'un appel, l'ancien code faisait
`next(d for d in dist if d.owner_id == old_owner)` qui ne ramenait qu'UNE
des lignes (la premiere). Le prorata etait donc faux (sous-calcule ou
sur-calcule selon le lot mute vs le premier matche).

Fix : on filtre `d.lot_id == lot_id_this` pour ne prendre QUE la quote-part
du lot mute (qui peut etre 1 sur N lots du vendeur).

Test : vendeur Matexi possede 3 lots (A101 quotity 600, A102 quotity 300,
A103 quotity 100, total 1000). Un appel Q1 de 1000 EUR est distribue
600/300/100 sur ces lots. On mute uniquement A102 (quotity 300).
Le prorata du lot A102 doit etre base sur 300 EUR (sa quote-part dans
l'appel) et NON sur 1000 EUR (total Matexi).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _run():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr83lp-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"  # Matexi (3 lots)
    o2_id = f"o2-{uuid.uuid4()}"  # Acquereur
    a101 = f"a101-{uuid.uuid4()}"
    a102 = f"a102-{uuid.uuid4()}"  # Lot mute
    a103 = f"a103-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "LotProrata", "reference": "TLP", "status": "active"})
    await db.fiscal_years.insert_one({"id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid})
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Matexi", "last_name": "Matexi", "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acquereur", "last_name": "Acquereur", "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # Matexi possede 3 lots dans la meme ACP
    await db.lots.insert_many([
        {"id": a101, "number": "A101", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 600.0},
        {"id": a102, "number": "A102", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 300.0},
        {"id": a103, "number": "A103", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 100.0},
    ])
    # iter90ab : cle par defaut obligatoire pour mutation
    await db.distribution_keys.insert_one({
        "id": f"dk-prorata-{cid[:8]}", "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": a101, "share": 600.0},
                 {"lot_id": a102, "share": 300.0},
                 {"lot_id": a103, "share": 100.0}],
    })
    # 1 appel Q1 (jan-mar 2026) avec distribution PAR LOT
    await db.fund_calls.insert_one({
        "id": f"fc-{uuid.uuid4()}",
        "name": "Trimestriel 1/4 - 2026",
        "date": "2026-01-01", "due_date": "2026-01-31",
        "period_start": "2026-01-01", "period_end": "2026-03-31",
        "fiscal_year_id": fy_id, "copropriete_id": cid,
        "call_type": "provisions", "total_amount": 1000.0,
        # Distribution par lot (comme genere par fund_calls.py)
        "distribution": [
            {"lot_id": a101, "lot_number": "A101", "owner_id": o1_id, "owner_name": "Matexi", "share": 600, "amount": 600.0, "paid": False},
            {"lot_id": a102, "lot_number": "A102", "owner_id": o1_id, "owner_name": "Matexi", "share": 300, "amount": 300.0, "paid": False},
            {"lot_id": a103, "lot_number": "A103", "owner_id": o1_id, "owner_name": "Matexi", "share": 100, "amount": 100.0, "paid": False},
        ],
    })

    try:
        from routes.properties import create_properties_router
        router = create_properties_router(db)
        endpoints = {(r.path, tuple(sorted(r.methods))): r.endpoint for r in router.routes}
        # Find the preview endpoint
        preview_fn = None
        for r in router.routes:
            if r.path == "/api/lots/{lot_id}/mutate-preview":
                preview_fn = r.endpoint
                break
        LotMutationInput = preview_fn.__annotations__.get("data")

        # Mute A102 (quotity 300) au 15/02/2026 (milieu Q1, 45 jours apres debut)
        payload = LotMutationInput(new_owner_id=o2_id, sale_date="2026-02-15")
        preview = await preview_fn(lot_id=a102, data=payload)

        # Prorata attendu : 300 (quote-part du lot A102 dans l'appel) * (45/90)
        # Jours apres sale_date (15 fev a 31 mars inclus) = 45 jours
        # Total Q1 = 90 jours (jan-mar 2026 non-bissextile)
        # Prorata = 300 * 44/90 = 146.67 (verifions le calcul exact)
        # Days from 15/02 to 31/03 inclus = 14 + 31 = 45 jours (15..28 fev = 14 + 31 mars)
        # Actually: 28 - 15 + 1 = 14 jours fev + 31 mars = 45 jours
        # Total Q1: 31 + 28 + 31 = 90 jours
        # Prorata = 300 * 45/90 = 150.00 EUR
        expected_prorata = round(300.0 * 45 / 90, 2)  # 150.00
        actual_prorata = preview["current_period_prorata"]
        assert abs(actual_prorata - expected_prorata) < 0.01, (
            f"Prorata attendu {expected_prorata} (basé sur quote-part lot A102=300 EUR), "
            f"recu {actual_prorata}. Si ~487 ou ~500, le code utilise encore le total Matexi (1000)."
        )

        # Verifier que dans details, on a bien lot_amount = 300 (et non 1000)
        assert len(preview["current_period_details"]) == 1
        detail = preview["current_period_details"][0]
        assert abs(detail["lot_amount"] - 300.0) < 0.01, (
            f"Detail lot_amount attendu 300 (A102 seul), recu {detail['lot_amount']}"
        )
        # owner_amount alias retro-compat doit aussi etre la quote-part lot
        assert abs(detail["owner_amount"] - 300.0) < 0.01

        print("OK - prorata calcule sur quote-part du LOT mute (300 EUR), pas du proprietaire (1000 EUR)")
        print(f"  Prorata A102 = {actual_prorata} EUR (15/02 -> 31/03 = 45/90 jours)")

    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.owners.delete_many({"id": {"$in": [o1_id, o2_id]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})


def test_prorata_uses_lot_share_not_owner_total():
    asyncio.run(_run())
