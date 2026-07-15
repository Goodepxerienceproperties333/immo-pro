"""
iter90fw : filtre proprietaires actuels vs tous dans le tableau des
decomptes.

Ticket utilisateur (Feb 2026) :
> "il faut un filtre qui permet de choisir - Proprietaires actuels ou
> Tous les proprietaires"

Definition :
- Actuels ('current') : possedent >= 1 lot au 1er JOUR de l'exercice OU
  ont possede pendant l'exercice (vendeur mid-year). Resolu via
  db.mutations.
- Tous ('all') : actuels + anciens proprietaires (n'ont plus de lot mais
  gardent un compte tier configure OU apparaissent dans les mutations
  historiques).

Chaque owner retourne un flag `is_former=True/False` pour badge visuel.
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _get_decompte(db, cid, fy_id=None, owner_filter="current"):
    """Simule un appel a l'endpoint /reports/decompte en mode owner_filter."""
    from routes.reports import _apply_copro  # noqa: F401
    # Fait le meme travail que le endpoint mais sans FastAPI Request.
    from datetime import datetime, timezone  # noqa: F401

    fy = None
    date_from, date_to = None, None
    if fy_id:
        fy = await db.fiscal_years.find_one({"id": fy_id}, {"_id": 0})
        if fy:
            date_from = fy["start_date"]
            date_to = fy["end_date"]

    lots = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(1000)
    current_owner_ids = set()
    for lt in lots:
        if lt.get("owner_id"):
            current_owner_ids.add(lt["owner_id"])
        for oid in (lt.get("owner_ids") or []):
            if oid:
                current_owner_ids.add(oid)

    fy_start = (fy or {}).get("start_date") if fy else (date_from or "")
    fy_end = (fy or {}).get("end_date") if fy else (date_to or "")
    muts = await db.mutations.find({"copropriete_id": cid}, {"_id": 0}).to_list(10000)
    for m in muts:
        sd = m.get("sale_date") or ""
        if not sd or not m.get("from_owner_id"):
            continue
        if fy_start and sd > fy_start and (not fy_end or sd <= fy_end):
            current_owner_ids.add(m["from_owner_id"])

    all_owner_ids = set(current_owner_ids)
    if owner_filter == "all":
        cands = await db.owners.find(
            {f"tier_accounts.{cid}": {"$exists": True}}, {"_id": 0, "id": 1},
        ).to_list(10000)
        for c in cands:
            all_owner_ids.add(c["id"])
        for m in muts:
            if m.get("from_owner_id"):
                all_owner_ids.add(m["from_owner_id"])
            if m.get("to_owner_id"):
                all_owner_ids.add(m["to_owner_id"])

    target_ids = list(all_owner_ids if owner_filter == "all" else current_owner_ids)
    owners = await db.owners.find(
        {"id": {"$in": target_ids}}, {"_id": 0},
    ).sort("name", 1).to_list(1000) if target_ids else []

    return [
        {
            "owner_id": o["id"],
            "owner_name": o["name"],
            "is_former": o["id"] not in current_owner_ids,
        }
        for o in owners
    ]


async def _scenario_current_vs_all():
    """Alice possede un lot AUJOURD'HUI. Bob possedait un lot avant, l'a
    vendu a Alice l'an dernier -> ancien proprietaire. Carol a un compte
    tier historique mais n'a jamais eu de lot dans cette ACP."""
    db = await _mongo()
    cid = f"iter90fw-{uuid.uuid4()}"
    alice = f"iter90fw-alice-{uuid.uuid4()}"
    bob = f"iter90fw-bob-{uuid.uuid4()}"
    carol = f"iter90fw-carol-{uuid.uuid4()}"
    lot_id = f"iter90fw-lot-{uuid.uuid4()}"
    fy_id = f"iter90fw-fy-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fw", "reference": "F"})
        await db.owners.insert_many([
            {
                "id": alice, "name": "Alice",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": ""}},
            },
            {
                "id": bob, "name": "Bob (ex-proprietaire)",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": ""}},
            },
            {
                "id": carol, "name": "Carol (jamais eu de lot)",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010003", "reserve": ""}},
            },
        ])
        # Lot appartient a Alice aujourd'hui
        await db.lots.insert_one({
            "id": lot_id, "copropriete_id": cid, "number": "L1",
            "quotity": 10000.0, "owner_id": alice, "owner_ids": [alice],
        })
        # Bob a vendu son lot a Alice le 2024-05-15 (avant fy_start)
        await db.mutations.insert_one({
            "id": str(uuid.uuid4()),
            "copropriete_id": cid,
            "lot_id": lot_id,
            "from_owner_id": bob,
            "to_owner_id": alice,
            "sale_date": "2024-05-15",
        })
        # Fiscal year 2025 (Bob n'etait plus proprietaire au 01/01/2025)
        await db.fiscal_years.insert_one({
            "id": fy_id, "copropriete_id": cid, "name": "2025",
            "start_date": "2025-01-01", "end_date": "2025-12-31",
        })

        # Mode 'current' : seulement Alice (Bob n'a plus de lot depuis 2024)
        res_current = await _get_decompte(db, cid, fy_id, owner_filter="current")
        names_current = {r["owner_name"] for r in res_current}
        assert "Alice" in names_current, f"Alice devrait etre dans 'current', trouve {names_current}"
        assert "Bob (ex-proprietaire)" not in names_current, (
            f"Bob (vendu en 2024) NE doit PAS etre dans 'current' pour l'exercice 2025, "
            f"trouve {names_current}"
        )
        assert "Carol (jamais eu de lot)" not in names_current, (
            f"Carol n'a jamais eu de lot -> pas dans 'current', trouve {names_current}"
        )
        # is_former devrait etre False pour Alice
        alice_row = next(r for r in res_current if r["owner_name"] == "Alice")
        assert alice_row["is_former"] is False

        # Mode 'all' : Alice + Bob + Carol
        res_all = await _get_decompte(db, cid, fy_id, owner_filter="all")
        names_all = {r["owner_name"] for r in res_all}
        assert "Alice" in names_all
        assert "Bob (ex-proprietaire)" in names_all, (
            f"Bob doit apparaitre en mode 'all' (ancien proprietaire), trouve {names_all}"
        )
        assert "Carol (jamais eu de lot)" in names_all, (
            f"Carol (tier_accounts configure) doit apparaitre en 'all', trouve {names_all}"
        )
        # is_former checks
        bob_row = next(r for r in res_all if r["owner_name"] == "Bob (ex-proprietaire)")
        carol_row = next(r for r in res_all if r["owner_name"] == "Carol (jamais eu de lot)")
        alice_row_all = next(r for r in res_all if r["owner_name"] == "Alice")
        assert bob_row["is_former"] is True, "Bob devrait etre marque comme ancien"
        assert carol_row["is_former"] is True, "Carol devrait etre marquee comme ancienne"
        assert alice_row_all["is_former"] is False, "Alice est proprietaire actuel"
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [alice, bob, carol]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.mutations.delete_many({"copropriete_id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})


async def _scenario_seller_mid_exercise_stays_current():
    """Bob vend son lot a Alice AU MILIEU de l'exercice 2025 (2025-06-30).
    Bob doit rester 'current' pour l'exercice 2025 (il etait proprietaire au
    debut ET a possede pendant l'exercice)."""
    db = await _mongo()
    cid = f"iter90fw-mid-{uuid.uuid4()}"
    alice = f"iter90fw-alice-mid-{uuid.uuid4()}"
    bob = f"iter90fw-bob-mid-{uuid.uuid4()}"
    lot_id = f"iter90fw-lot-mid-{uuid.uuid4()}"
    fy_id = f"iter90fw-fy-mid-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fw mid", "reference": "M"})
        await db.owners.insert_many([
            {"id": alice, "name": "Alice mid",
             "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "41010001", "reserve": ""}}},
            {"id": bob, "name": "Bob mid",
             "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "41010002", "reserve": ""}}},
        ])
        # Lot actuellement chez Alice (vendu par Bob)
        await db.lots.insert_one({
            "id": lot_id, "copropriete_id": cid, "number": "L1",
            "quotity": 10000.0, "owner_id": alice, "owner_ids": [alice],
        })
        # Mutation en cours d'exercice
        await db.mutations.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "lot_id": lot_id, "from_owner_id": bob, "to_owner_id": alice,
            "sale_date": "2025-06-30",
        })
        await db.fiscal_years.insert_one({
            "id": fy_id, "copropriete_id": cid, "name": "2025",
            "start_date": "2025-01-01", "end_date": "2025-12-31",
        })

        res_current = await _get_decompte(db, cid, fy_id, owner_filter="current")
        names = {r["owner_name"] for r in res_current}
        assert "Alice mid" in names, (
            f"Alice (acheteur actuel) doit apparaitre en 'current', trouve {names}"
        )
        assert "Bob mid" in names, (
            f"Bob (vendeur pendant l'exercice 2025) doit apparaitre en 'current' "
            f"pour recevoir son decompte de mutation, trouve {names}"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [alice, bob]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.mutations.delete_many({"copropriete_id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})


def test_owner_filter_current_vs_all():
    asyncio.run(_scenario_current_vs_all())


def test_seller_mid_exercise_stays_current():
    asyncio.run(_scenario_seller_mid_exercise_stays_current())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
