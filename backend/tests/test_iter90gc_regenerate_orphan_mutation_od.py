"""iter90gc : regeneration OD MUT-R (Fonds de roulement) pour mutations
legacy sans OD (cas Acacia TER : mutations importees sans passer par
POST /api/lots/{id}/mutate).

**Ticket utilisateur** :
> "les OD sont VIDES !!" (onglet Journaux Comptables > Operations Diverses
> vide sur PROD malgre des mutations enregistrees).

**Root cause identifie** :
`db.mutations` contient les enregistrements de vente (source de verite
historique), mais aucune `journal_entries` avec source_type='lot_mutation'
n'a ete generee. Cela peut arriver quand :
- Un import legacy Optipro insere directement dans db.mutations
- Un script SQL a modifie lot.owner_id sans passer par mutate_lot
- Une regeneration incomplete apres backup/restore

**Fix iter90gc** :
- Helper module-level `regenerate_orphan_mutation_od(db, mutation_doc)` :
  reconstruit l'OD MUT-R en utilisant `roulement_quota` deja stocke dans
  db.mutations + assign_owner_accounts + insertion dans journal_entries.
- Endpoint `POST /api/mutations/regenerate-orphan-od?copropriete_id=X&dry_run=true`
  parcourt db.mutations d'une ACP et rejoue le helper.
- Idempotent : detecte l'OD existante via (source_id=lot_id, sale_date,
  source_subtype='fonds_roulement').
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_orphan_mutation(db, suffix: str) -> dict:
    """Setup : 1 ACP + 2 owners + 1 lot + 1 mutation dans db.mutations
    SANS l'OD MUT-R correspondante (simule cas legacy Acacia TER)."""
    cid = f"iter90gc-{suffix}"
    from_owner_id = f"seller-{suffix}"
    to_owner_id = f"buyer-{suffix}"
    lot_id = f"lot-{suffix}"
    mut_id = f"mut-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "iter90gc"})
    # Comptes tiers doivent etre precrees pour assign_owner_accounts
    await db.pcmn_accounts.insert_many([
        {"number": "41010001", "name": "Seller",
         "copropriete_id": cid, "class_num": 4, "type": "balance"},
        {"number": "41010002", "name": "Buyer",
         "copropriete_id": cid, "class_num": 4, "type": "balance"},
    ])
    await db.owners.insert_many([
        {"id": from_owner_id, "name": "MATEXI", "last_name": "MATEXI",
         "tier_accounts": {cid: {"provisions": "41010001"}}},
        {"id": to_owner_id, "name": "TEUWEN Gael", "last_name": "TEUWEN",
         "tier_accounts": {cid: {"provisions": "41010002"}}},
    ])
    # Lot deja mute (owner_id = to_owner_id) mais pas d'OD generee
    await db.lots.insert_one({
        "id": lot_id, "number": "202", "copropriete_id": cid,
        "owner_id": to_owner_id, "quotity": 100,
    })
    # Mutation dans db.mutations
    await db.mutations.insert_one({
        "id": mut_id, "copropriete_id": cid,
        "lot_id": lot_id,
        "from_owner_id": from_owner_id,
        "to_owner_id": to_owner_id,
        "sale_date": "2025-11-17",
        "roulement_quota": 490.36,  # <-- montant TEUWEN reel
        "current_period_prorata": 0.0,
        "total_transfer": 490.36,
    })
    return {"cid": cid, "mut_id": mut_id, "lot_id": lot_id,
            "from_owner_id": from_owner_id, "to_owner_id": to_owner_id}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({f"tier_accounts.{cid}": {"$exists": True}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.mutations.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_iter90gc_dry_run_detects_orphan_mutation():
    """dry_run=true detecte 1 mutation orpheline (roulement 490.36 EUR)."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_orphan_mutation(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/mutations/regenerate-orphan-od?"
                    f"copropriete_id={ctx['cid']}&dry_run=true"
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["dry_run"] is True
                assert data["total_mutations"] == 1
                assert data["regenerated"] == 1, (
                    f"1 mutation orpheline attendue, recu {data}"
                )
                assert data["skipped"] == 0
                # Verifie que RIEN n'a ete cree en base
                count = await db.journal_entries.count_documents(
                    {"copropriete_id": ctx["cid"]}
                )
                assert count == 0, "dry_run ne doit rien creer"
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90gc_apply_creates_od_mut_r():
    """dry_run=false cree l'OD MUT-R avec debit acheteur / credit vendeur."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_orphan_mutation(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/mutations/regenerate-orphan-od?"
                    f"copropriete_id={ctx['cid']}&dry_run=false"
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["regenerated"] == 1
                # Verifie l'OD en base
                od = await db.journal_entries.find_one({
                    "copropriete_id": ctx["cid"],
                    "source_type": "lot_mutation",
                    "source_subtype": "fonds_roulement",
                }, {"_id": 0})
                assert od is not None, "OD MUT-R doit etre creee"
                assert od["journal_type"] == "OD"
                assert od["date"] == "2025-11-17"
                assert od["reference"] == "MUT-202-R"
                assert abs(od["total_debit"] - 490.36) < 0.01
                assert abs(od["total_credit"] - 490.36) < 0.01
                # 2 lines: DR buyer / CR seller
                lines = od["lines"]
                assert len(lines) == 2
                buyer_line = next(l for l in lines if l["debit"] > 0)
                seller_line = next(l for l in lines if l["credit"] > 0)
                assert buyer_line["third_party_id"] == ctx["to_owner_id"]
                assert seller_line["third_party_id"] == ctx["from_owner_id"]
                assert abs(buyer_line["debit"] - 490.36) < 0.01
                assert abs(seller_line["credit"] - 490.36) < 0.01
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90gc_idempotent_second_call_skips():
    """Deuxieme appel apres apply : skip (OD existe deja)."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_orphan_mutation(db, suffix)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                # Premier apply
                r1 = await c.post(
                    f"{BACKEND_URL}/api/mutations/regenerate-orphan-od?"
                    f"copropriete_id={ctx['cid']}&dry_run=false"
                )
                assert r1.json()["regenerated"] == 1
                # Deuxieme apply : doit skip
                r2 = await c.post(
                    f"{BACKEND_URL}/api/mutations/regenerate-orphan-od?"
                    f"copropriete_id={ctx['cid']}&dry_run=false"
                )
                data = r2.json()
                assert data["regenerated"] == 0, (
                    f"Idempotence : 2eme appel doit skip. Recu {data}"
                )
                assert data["skipped"] == 1
                # Une seule OD en base
                count = await db.journal_entries.count_documents({
                    "copropriete_id": ctx["cid"],
                    "source_subtype": "fonds_roulement",
                })
                assert count == 1
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90gc_non_superadmin_gets_403():
    """Non-superadmin recoit 403."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        from server import hash_password
        syndic_email = f"synd_gc_{suffix}@t.be"
        await db.users.insert_one({
            "email": syndic_email, "name": "S", "role": "syndic",
            "password_hash": hash_password("test1234"),
        })
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{BACKEND_URL}/api/auth/login", json={
                    "email": syndic_email, "password": "test1234"})
                assert r.status_code == 200
                r2 = await c.post(
                    f"{BACKEND_URL}/api/mutations/regenerate-orphan-od?"
                    f"copropriete_id=fake&dry_run=true"
                )
                assert r2.status_code == 403
        finally:
            await db.users.delete_one({"email": syndic_email})
    asyncio.run(_run())
