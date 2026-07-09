"""
iter90cp : REGRESSION LOCK - Endpoint repair-founder-ownership etendu avec :
- Auto-detection du fondateur (mode le plus frequent des muts[0].from_owner_id
  ou lot.owner_id).
- Auto-fallback founder_start_date = 1ere FY.start_date de l'ACP.
- Nouveau Case C : lots orphelins (owner_id vide + aucune mutation) sont
  assignes au fondateur.

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "Le code considere que matexi ne serait pas assigne en tant que proprietaire
> a certains lot non identifies. Ce n'est pas logique il faut considerer que
> le premier proprietaire d'un lot l'est toujours au premier jour de
> l'exercice. Tiens compte de ca et corrige retroactivement les erreurs"

Fix iter90cp :
- Cas A : lot avec chain cassee (muts[0].from != Matexi) -> foundation mutation
- Cas B : lot sans mutation + owner != Matexi -> foundation mutation
- Cas C : lot orphelin (owner_id vide + aucune mutation) -> assignation Matexi
  (lot.owner_id = Matexi + owner_ids = [Matexi], marque iter90cp_repaired_orphan)
- Auto-detect founder si non fourni.
- Auto-fallback founder_start_date = min(fiscal_years.start_date).
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _base_setup(prefix: str):
    """Setup ACP + FY + fondateur Matexi + 2 owners + PCMN + cle."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"
    dewinter_id = f"dew-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2025", "start_date": "2025-01-01",
        "end_date": "2025-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.owners.insert_many([
        {"id": matexi_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid]},
        {"id": dewinter_id, "name": "Dewinter", "last_name": "Dewinter",
         "auxiliary_code": "D001", "copropriete_ids": [cid]},
    ])
    return {
        "db": db, "cid": cid, "fy_id": fy_id,
        "matexi": matexi_id, "dewinter": dewinter_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "lots", "mutations"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["matexi"], ctx["dewinter"]]}})


# =========================================================================
# SCENARIO 1 : Auto-detection du fondateur + fallback start_date + case A/B
# =========================================================================
async def _scenario_auto_detect_and_repair():
    ctx = await _base_setup("iter90cp-auto")
    try:
        db = ctx["db"]
        # Lot 1 : owner = Matexi, aucune mutation -> baseline
        # Lot 2 : owner = Matexi, aucune mutation -> baseline
        # Lot 3 : owner = Matexi, aucune mutation -> baseline
        # Lot 4 : owner = Dewinter, aucune mutation -> cas B (fondation Matexi->Dewinter)
        # Lot 5 : mutation matexi->dewinter (from = Matexi, chain ok) -> baseline
        # Lot 6 : mutation dewinter->matexi (from_owner_id != Matexi) -> cas A
        # Matexi apparait comme "candidat fondateur" 4x (L1+L2+L3 owner + L5 first_from)
        # Dewinter apparait 2x (L4 owner + L6 first_from) -> Matexi gagne
        await db.lots.insert_many([
            {"id": "L1", "number": "1", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L2", "number": "2", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L3", "number": "3", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L4", "number": "4", "owner_id": ctx["dewinter"],
             "owner_ids": [ctx["dewinter"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L5", "number": "5", "owner_id": ctx["dewinter"],
             "owner_ids": [ctx["dewinter"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0,
             "mutations": [
                 {"id": "M-L5", "date": "2025-03-01",
                  "old_owner_id": ctx["matexi"],
                  "new_owner_id": ctx["dewinter"], "sale_price": 100000.0},
             ]},
            {"id": "L6", "number": "6", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0,
             "mutations": [
                 {"id": "M-L6", "date": "2025-06-01",
                  "old_owner_id": ctx["dewinter"],
                  "new_owner_id": ctx["matexi"], "sale_price": 100000.0},
             ]},
        ])

        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)

            # DRY RUN sans fournir founder_owner_id ni founder_start_date
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={"copropriete_id": ctx["cid"], "dry_run": True},
            )
            assert r.status_code == 200, r.text
            payload = r.json()

            # Auto-detection : Matexi apparait 2x (L1 owner + L3 mut) contre
            # Dewinter 1x (L2 owner). Fondateur detecte = Matexi.
            assert payload["founder_auto_detected"] is True
            assert payload["founder_owner_id"] == ctx["matexi"], (
                f"Auto-detect devrait choisir Matexi. Obtenu : "
                f"{payload['founder_owner_id']}, name={payload.get('founder_owner_name')}"
            )
            assert payload["founder_start_date"] == "2025-01-01"  # 1ere FY

            assert payload["cases_a_count"] == 1, (
                f"L6 attendu en case A. Obtenu : {payload['cases_a_count']}"
            )
            assert payload["cases_b_count"] == 1, (
                f"L4 attendu en case B. Obtenu : {payload['cases_b_count']}"
            )
            assert payload["cases_c_count"] == 0
            assert payload["applied"]["cases_a"] == 0  # dry_run

            # REAL RUN
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={"copropriete_id": ctx["cid"], "dry_run": False},
            )
            assert r.status_code == 200, r.text
            applied = r.json()["applied"]
            assert applied["cases_a"] == 1 and applied["cases_b"] == 1

            # Verifie que L4 a maintenant une foundation mutation
            l4 = await db.lots.find_one({"id": "L4"}, {"_id": 0})
            l4_muts = l4.get("mutations", [])
            assert len(l4_muts) == 1
            assert l4_muts[0]["old_owner_id"] == ctx["matexi"]
            assert l4_muts[0]["new_owner_id"] == ctx["dewinter"]
            assert l4_muts[0].get("foundation_mutation") is True

            # Verifie que L6 a une foundation mutation en tete
            l6 = await db.lots.find_one({"id": "L6"}, {"_id": 0})
            l6_muts = sorted(l6.get("mutations", []), key=lambda x: x.get("date"))
            assert len(l6_muts) == 2
            assert l6_muts[0]["old_owner_id"] == ctx["matexi"]
            assert l6_muts[0]["new_owner_id"] == ctx["dewinter"]
            assert l6_muts[0].get("foundation_mutation") is True
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Case C - lots orphelins assignes au fondateur
# =========================================================================
async def _scenario_case_c_orphans():
    ctx = await _base_setup("iter90cp-orphan")
    try:
        db = ctx["db"]
        # L1 : Matexi (baseline)
        # L2 : ORPHELIN (owner_id vide, aucune mutation) -> case C
        # L3 : ORPHELIN (owner_id="", aucune mutation) -> case C
        await db.lots.insert_many([
            {"id": "L1", "number": "1", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L2-orphan", "number": "2", "owner_id": None,
             "owner_ids": [], "copropriete_id": ctx["cid"], "quotity": 1000.0},
            {"id": "L3-orphan", "number": "3", "owner_id": "",
             "owner_ids": [], "copropriete_id": ctx["cid"], "quotity": 1000.0},
        ])

        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)

            # DRY RUN avec founder explicite
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                    "dry_run": True,
                },
            )
            assert r.status_code == 200, r.text
            payload = r.json()
            assert payload["cases_c_count"] == 2, (
                f"L2 + L3 attendus en case C. Obtenu : {payload['cases_c_count']}"
            )
            assert payload["applied"]["cases_c"] == 0  # dry_run

            # REAL RUN
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                    "dry_run": False,
                },
            )
            assert r.status_code == 200, r.text
            applied = r.json()["applied"]
            assert applied["cases_c"] == 2

            # Verifie que les orphelins ont maintenant owner_id = Matexi
            l2 = await db.lots.find_one({"id": "L2-orphan"}, {"_id": 0})
            l3 = await db.lots.find_one({"id": "L3-orphan"}, {"_id": 0})
            assert l2["owner_id"] == ctx["matexi"]
            assert l2["owner_ids"] == [ctx["matexi"]]
            assert l2.get("iter90cp_repaired_orphan") is True
            assert l3["owner_id"] == ctx["matexi"]
            assert l3["owner_ids"] == [ctx["matexi"]]
            assert l3.get("iter90cp_repaired_orphan") is True

            # Aucune mutation creee pour les orphelins (assignation initiale)
            assert not l2.get("mutations")
            assert not l3.get("mutations")
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : fix_orphans=False -> case C ignore
# =========================================================================
async def _scenario_disable_orphans():
    ctx = await _base_setup("iter90cp-noorphan")
    try:
        db = ctx["db"]
        await db.lots.insert_one(
            {"id": "L-orphan", "number": "1", "owner_id": "",
             "owner_ids": [], "copropriete_id": ctx["cid"], "quotity": 1000.0},
        )
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                    "fix_orphans": False,
                    "dry_run": False,
                },
            )
            assert r.status_code == 200, r.text
            payload = r.json()
            assert payload["cases_c_count"] == 0, (
                f"fix_orphans=False doit ignorer orphelins. cases_c_count={payload['cases_c_count']}"
            )
            assert payload["applied"]["cases_c"] == 0

            # Le lot reste orphelin
            lot = await db.lots.find_one({"id": "L-orphan"}, {"_id": 0})
            assert not lot.get("owner_id")
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : Idempotence - execute 2x = pas de doublon
# =========================================================================
async def _scenario_idempotence():
    ctx = await _base_setup("iter90cp-idem")
    try:
        db = ctx["db"]
        await db.lots.insert_many([
            {"id": "L1", "number": "1", "owner_id": ctx["matexi"],
             "owner_ids": [ctx["matexi"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L2", "number": "2", "owner_id": ctx["dewinter"],
             "owner_ids": [ctx["dewinter"]], "copropriete_id": ctx["cid"],
             "quotity": 1000.0},
            {"id": "L3-orphan", "number": "3", "owner_id": "",
             "owner_ids": [], "copropriete_id": ctx["cid"], "quotity": 1000.0},
        ])
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)

            # 1er run
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                    "dry_run": False,
                },
            )
            assert r.status_code == 200
            first = r.json()["applied"]
            assert first["cases_b"] == 1 and first["cases_c"] == 1

            # 2e run : rien a faire
            r = await client.post(
                f"{BACKEND_URL}/api/lots/repair-founder-ownership",
                headers=hdr, json={
                    "copropriete_id": ctx["cid"],
                    "founder_owner_id": ctx["matexi"],
                    "dry_run": False,
                },
            )
            assert r.status_code == 200
            second = r.json()
            assert second["cases_b_count"] == 0, (
                f"Idempotence violee : cases_b_count devrait etre 0 au 2e run. "
                f"Obtenu : {second['cases_b_count']}"
            )
            assert second["cases_c_count"] == 0, (
                f"Idempotence violee : cases_c_count devrait etre 0 au 2e run. "
                f"Obtenu : {second['cases_c_count']}"
            )
            assert second["cases_a_count"] == 0

            # Verifie que L2 n'a QU'UNE seule foundation mutation
            l2 = await db.lots.find_one({"id": "L2"}, {"_id": 0})
            assert len(l2.get("mutations", [])) == 1
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_auto_detect_and_repair():
    asyncio.run(_scenario_auto_detect_and_repair())


def test_case_c_orphans_assigned_to_founder():
    asyncio.run(_scenario_case_c_orphans())


def test_fix_orphans_false_disables_case_c():
    asyncio.run(_scenario_disable_orphans())


def test_idempotence_no_duplicates():
    asyncio.run(_scenario_idempotence())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
