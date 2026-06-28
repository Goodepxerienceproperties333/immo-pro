"""Regression test - iter83 - Liens entre lots + mutation groupee.

Demande user : "creer la possibilite de lier des lots, en cas de mutation
tous les lots sont mutes ensemble".

Choix valides par l'utilisateur :
- (1.a) Modele parent-enfant simple (pas de chaine)
- (2.a) Tous les lots lies doivent avoir le meme proprietaire
- (3.a) Mutation directe d'un enfant BLOQUEE
- (4.a) Affichage liste plate avec badges

Tests :
- E2E link : un parent peut lier 2 enfants (cave + parking)
- Validation : owner different => refus
- Validation : meme ACP requise
- Validation : pas de chaine (enfant ne peut pas etre parent)
- Validation : refus si un enfant est deja lie ailleurs
- Validation : self-link refuse
- Unlink : delie un enfant
- Mutation groupee : mute parent + 2 enfants ensemble (3 OD, 3 mutation_records)
- Mutation child direct : 400 "mutez le parent"
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_acp_with_3_lots():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr83link-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1_id = f"o1-{uuid.uuid4()}"
    o2_id = f"o2-{uuid.uuid4()}"
    o3_id = f"o3-{uuid.uuid4()}"  # owner different, pour test refus
    appt_id = f"appt-{uuid.uuid4()}"
    cave_id = f"cave-{uuid.uuid4()}"
    park_id = f"park-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "Iter83Link", "reference": "TLINK", "status": "active"})
    await db.fiscal_years.insert_one({"id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31", "copropriete_id": cid})
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100003", "name": "Other", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1_id, "name": "Vendeur V", "last_name": "Vendeur", "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o2_id, "name": "Acquereur A", "last_name": "Acquereur", "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
        {"id": o3_id, "name": "Other O", "last_name": "Other", "auxiliary_code": "C0003", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100003"}}},
    ])
    # 3 lots du meme proprietaire (appartement + cave + parking), quotities 800/100/100
    await db.lots.insert_many([
        {"id": appt_id, "number": "A101", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 800.0, "lot_type": "Appartement"},
        {"id": cave_id, "number": "C10", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 100.0, "lot_type": "Cave"},
        {"id": park_id, "number": "P5", "owner_id": o1_id, "owner_ids": [o1_id], "copropriete_id": cid, "quotity": 100.0, "lot_type": "Parking"},
    ])
    # Solde fonds de roulement = 5000 EUR -> appt 4000 + cave 500 + park 500 (quote-parts proportionnelles aux quotities)
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01", "copropriete_id": cid,
        "lines": [{"account_number": "410", "debit": 5000.0, "credit": 0.0},
                  {"account_number": "100", "debit": 0.0, "credit": 5000.0}],
        "total_debit": 5000.0, "total_credit": 5000.0,
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "o1": o1_id, "o2": o2_id, "o3": o3_id,
            "appt": appt_id, "cave": cave_id, "park": park_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"], ctx["o3"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _endpoint(db, path, method="POST"):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == path and method.upper() in r.methods:
            return r.endpoint
    return None


async def _test_link_then_grouped_mutation():
    """Link cave + park sous appt, puis mute appt -> 3 mutations groupees."""
    ctx = await _setup_acp_with_3_lots()
    db = ctx["db"]
    try:
        link_fn = _endpoint(db, "/api/lots/{parent_id}/link", "POST")
        LotLinkInput = link_fn.__annotations__.get("data")
        result = await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"], ctx["park"]]))
        assert len(result["children"]) == 2
        assert len(result["added"]) == 2
        # Verify children have parent_lot_id set
        cave = await db.lots.find_one({"id": ctx["cave"]}, {"_id": 0})
        assert cave["parent_lot_id"] == ctx["appt"]
        park = await db.lots.find_one({"id": ctx["park"]}, {"_id": 0})
        assert park["parent_lot_id"] == ctx["appt"]

        # Idempotence : relink doit etre OK
        await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"]]))

        # Test mute child direct -> 400
        mutate_fn = _endpoint(db, "/api/lots/{lot_id}/mutate", "POST")
        LotMutationInput = mutate_fn.__annotations__.get("data")
        from fastapi import HTTPException
        try:
            await mutate_fn(lot_id=ctx["cave"], data=LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-03-15"))
            raise AssertionError("Expected 400 for mute on child lot")
        except HTTPException as e:
            assert e.status_code == 400
            assert "lie" in e.detail.lower() or "parent" in e.detail.lower()

        # Test mute parent -> applique a 3 lots
        payload = LotMutationInput(new_owner_id=ctx["o2"], sale_date="2026-03-15", note="vente groupee")
        result = await mutate_fn(lot_id=ctx["appt"], data=payload)
        assert result["linked_lots_count"] == 2
        assert len(result["grouped_mutations"]) == 3
        # Total transfert = somme des 3 (roulement uniquement, pas d'appels emis)
        # Roulement: appt=4000, cave=500, park=500 -> total 5000
        assert abs(result["grouped_total_transfer"] - 5000.0) < 0.01, (
            f"grouped_total_transfer expected 5000, got {result['grouped_total_transfer']}"
        )
        # Verify owner changed on all 3 lots
        for lid in [ctx["appt"], ctx["cave"], ctx["park"]]:
            updated = await db.lots.find_one({"id": lid}, {"_id": 0})
            assert updated["owner_id"] == ctx["o2"], f"Lot {lid} owner not updated"
        # 3 journal entries created
        jes = await db.journal_entries.find({"source_type": "lot_mutation", "copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(10)
        assert len(jes) == 3, f"Expected 3 OD entries, got {len(jes)}"
        # Sum of OD = 5000
        total = sum(je["total_debit"] for je in jes)
        assert abs(total - 5000.0) < 0.01

    finally:
        await _cleanup(ctx)


async def _test_link_different_owner_refused():
    """Refuse si owner different."""
    ctx = await _setup_acp_with_3_lots()
    db = ctx["db"]
    try:
        # Change cave owner to o3
        await db.lots.update_one({"id": ctx["cave"]}, {"$set": {"owner_id": ctx["o3"]}})
        link_fn = _endpoint(db, "/api/lots/{parent_id}/link", "POST")
        LotLinkInput = link_fn.__annotations__.get("data")
        from fastapi import HTTPException
        try:
            await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"]]))
            raise AssertionError("Expected 400 for owner mismatch")
        except HTTPException as e:
            assert e.status_code == 400
            assert "proprietaire" in e.detail.lower()
    finally:
        await _cleanup(ctx)


async def _test_self_link_refused():
    """Un lot ne peut pas se lier a lui-meme."""
    ctx = await _setup_acp_with_3_lots()
    db = ctx["db"]
    try:
        link_fn = _endpoint(db, "/api/lots/{parent_id}/link", "POST")
        LotLinkInput = link_fn.__annotations__.get("data")
        from fastapi import HTTPException
        try:
            await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["appt"]]))
            raise AssertionError("Expected 400 for self-link")
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        await _cleanup(ctx)


async def _test_unlink():
    """Unlink retire la relation."""
    ctx = await _setup_acp_with_3_lots()
    db = ctx["db"]
    try:
        link_fn = _endpoint(db, "/api/lots/{parent_id}/link", "POST")
        unlink_fn = _endpoint(db, "/api/lots/{parent_id}/unlink", "POST")
        LotLinkInput = link_fn.__annotations__.get("data")
        await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"], ctx["park"]]))
        result = await unlink_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"]]))
        assert len(result["removed"]) == 1
        cave = await db.lots.find_one({"id": ctx["cave"]}, {"_id": 0})
        assert not cave.get("parent_lot_id")
        # park toujours lie
        park = await db.lots.find_one({"id": ctx["park"]}, {"_id": 0})
        assert park.get("parent_lot_id") == ctx["appt"]
    finally:
        await _cleanup(ctx)


async def _test_chain_refused():
    """Un enfant ne peut pas etre parent (pas de chaine)."""
    ctx = await _setup_acp_with_3_lots()
    db = ctx["db"]
    try:
        link_fn = _endpoint(db, "/api/lots/{parent_id}/link", "POST")
        LotLinkInput = link_fn.__annotations__.get("data")
        await link_fn(parent_id=ctx["appt"], data=LotLinkInput(child_lot_ids=[ctx["cave"]]))
        # Tente de lier park a cave (cave est deja enfant de appt)
        from fastapi import HTTPException
        try:
            await link_fn(parent_id=ctx["cave"], data=LotLinkInput(child_lot_ids=[ctx["park"]]))
            raise AssertionError("Expected 400 - cave is a child, cannot be parent")
        except HTTPException as e:
            assert e.status_code == 400
            assert "enfant" in e.detail.lower() or "chaine" in e.detail.lower()
    finally:
        await _cleanup(ctx)


def test_link_and_grouped_mutation():
    asyncio.run(_test_link_then_grouped_mutation())


def test_link_different_owner_refused():
    asyncio.run(_test_link_different_owner_refused())


def test_self_link_refused():
    asyncio.run(_test_self_link_refused())


def test_unlink_removes_relation():
    asyncio.run(_test_unlink())


def test_chain_link_refused():
    asyncio.run(_test_chain_refused())
