"""Regression test - iter83 - Annulation de mutation groupee.

Demande user : "quand on annule une mutation, tous les lots child sont aussi
annules"

Comportement attendu :
- Annuler la mutation depuis le LOT PARENT annule aussi tous les enfants
  (meme date, meme grouped_parent_lot_id)
- Annuler depuis un lot enfant est REFUSE (400) avec message demandant de
  passer par le parent
- Les ecritures OD de TOUS les lots du groupe sont supprimees
- Les proprietaires originels sont restaures sur les 3 lots
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _run_grouped_cancel():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr83c-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    appt = f"appt-{uuid.uuid4()}"
    cave = f"cave-{uuid.uuid4()}"
    park = f"park-{uuid.uuid4()}"

    try:
        await db.coproprietes.insert_one({"id": cid, "name": "Cancel", "reference": "TC", "status": "active"})
        await db.fiscal_years.insert_one({"id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid})
        await db.pcmn_accounts.insert_many([
            {"number": "100", "name": "Roul", "class_num": 1, "copropriete_id": cid},
            {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
            {"number": "4100001", "name": "V", "class_num": 4, "copropriete_id": cid},
            {"number": "4100002", "name": "A", "class_num": 4, "copropriete_id": cid},
        ])
        await db.owners.insert_many([
            {"id": o1_id, "name": "Vendeur", "last_name": "V", "auxiliary_code": "C0001", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100001"}}},
            {"id": o2_id, "name": "Acquereur", "last_name": "A", "auxiliary_code": "C0002", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100002"}}},
        ])
        await db.lots.insert_many([
            {"id": appt, "number": "A1", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 800.0},
            {"id": cave, "number": "C1", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 100.0, "parent_lot_id": appt},
            {"id": park, "number": "P1", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 100.0, "parent_lot_id": appt},
        ])
        # iter90ab : cle de repartition par defaut (obligatoire pour mutation)
        await db.distribution_keys.insert_one({
            "id": f"dk-iter83-{cid[:8]}", "copropriete_id": cid, "name": "Generale",
            "is_default": True, "key_type": "quotity",
            "lots": [
                {"lot_id": appt, "share": 800.0},
                {"lot_id": cave, "share": 100.0},
                {"lot_id": park, "share": 100.0},
            ],
        })
        # Solde 100 = 1000 EUR
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01", "copropriete_id": cid,
            "lines": [{"account_number": "410", "debit": 1000.0, "credit": 0.0},
                      {"account_number": "100", "debit": 0.0, "credit": 1000.0}],
            "total_debit": 1000.0, "total_credit": 1000.0,
        })

        from routes.properties import create_properties_router
        router = create_properties_router(db)
        mutate_fn = None
        cancel_fn = None
        for r in router.routes:
            if r.path == "/api/lots/{lot_id}/mutate" and "POST" in r.methods:
                mutate_fn = r.endpoint
            elif r.path == "/api/lots/{lot_id}/mutate/{mutation_id}" and "DELETE" in r.methods:
                cancel_fn = r.endpoint
        LotMutationInput = mutate_fn.__annotations__.get("data")

        # 1) Mutation groupee : appt + cave + park
        result = await mutate_fn(lot_id=appt, data=LotMutationInput(new_owner_id=o2_id, sale_date="2026-03-15"))
        assert result["linked_lots_count"] == 2
        assert len(result["grouped_mutations"]) == 3
        # 3 OD entries
        jes_before = await db.journal_entries.count_documents({"source_type": "lot_mutation", "copropriete_id": cid})
        assert jes_before == 3, f"Expected 3 OD entries after mutation, got {jes_before}"
        # All 3 lots now owned by o2
        for lid in [appt, cave, park]:
            lot = await db.lots.find_one({"id": lid}, {"_id": 0})
            assert lot["owner_id"] == o2_id

        # 2) Tente d'annuler depuis le lot ENFANT cave : doit etre REFUSE
        from fastapi import HTTPException
        try:
            await cancel_fn(lot_id=cave, mutation_id="last")
            raise AssertionError("Expected 400 - annulation depuis enfant refusee")
        except HTTPException as e:
            assert e.status_code == 400
            assert "parent" in e.detail.lower()
        # Verifie qu'aucune annulation n'a eu lieu
        jes_after_attempt = await db.journal_entries.count_documents({"source_type": "lot_mutation", "copropriete_id": cid})
        assert jes_after_attempt == 3, "OD ne doit pas avoir ete touchee"

        # 3) Annule depuis le lot PARENT appt : doit annuler les 3
        result = await cancel_fn(lot_id=appt, mutation_id="last")
        assert result["cancelled_count"] == 3, f"Expected 3 cancelled, got {result['cancelled_count']}"
        assert "groupee" in result["message"].lower()
        # 0 OD entries restantes
        jes_after = await db.journal_entries.count_documents({"source_type": "lot_mutation", "copropriete_id": cid})
        assert jes_after == 0, f"Expected 0 OD entries after group cancel, got {jes_after}"
        # All 3 lots restored to o1
        for lid in [appt, cave, park]:
            lot = await db.lots.find_one({"id": lid}, {"_id": 0})
            assert lot["owner_id"] == o1_id, f"Lot {lid} owner not restored to o1"
            # mutations array vide
            assert len(lot.get("mutations") or []) == 0

        print("OK - annulation groupee : 3 lots restaures + 3 OD supprimees")

    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.owners.delete_many({"id": {"$in": [o1_id, o2_id]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_grouped_mutation_cancel_cascades_to_children():
    asyncio.run(_run_grouped_cancel())
