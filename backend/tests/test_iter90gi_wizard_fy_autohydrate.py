"""iter90gi : l'etape 'Exercice fiscal' est retiree du wizard.

**Ticket utilisateur** :
> "l'exercice est deja cree donc corrige la logique"
> "comme l'exercice est deja ouvert il n'est plus possible de l'ouvrir il
> faut donc supprimer cette etape du Wizzard"

**Contexte** : depuis iter90gg, l'Assistant de creation ACP force le syndic
a definir l'exercice fiscal en cours (etape 2). L'etape 'Exercice fiscal'
du wizard d'import Optipro est donc redondante et provoque une erreur
"L'exercice X existe deja".

**Fix iter90gi** :
1. Cote backend, `create_session` + `get_active_session` hydratent
   automatiquement `session.steps.fiscal_year.fiscal_year_id` a partir de
   l'exercice ouvert de l'ACP.
2. `commit-budget` et `commit-opening-balance` resolvent l'exercice via
   l'ACP si `fiscal_year_id` n'est pas fourni.
3. Cote frontend, l'etape `fiscal_year` est retiree du tableau STEPS.
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


async def test_create_session_hydrates_fiscal_year_from_acp():
    """A la creation, la session recupere automatiquement l'exercice ouvert
    de l'ACP dans `steps.fiscal_year.fiscal_year_id`.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gi-hydrate-{suffix}"
    fy_id = f"fy-{suffix}"
    await db.coproprietes.insert_one({
        "id": cid, "name": f"iter90gi-hydrate-{suffix}", "reference": f"REF-{suffix}",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": f"2025-2026-{suffix}",
        "start_date": "2025-10-01", "end_date": "2026-09-30", "status": "open",
        "created_at": "2026-07-16T00:00:00+00:00",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(f"{BACKEND_URL}/api/import-wizard/sessions",
                                   json={"copropriete_id": cid, "source_system": "Optipro"})
            assert r.status_code == 200, r.text
            s = r.json()
            fy_step = (s.get("steps") or {}).get("fiscal_year") or {}
            assert fy_step.get("fiscal_year_id") == fy_id, f"expected {fy_id}: {fy_step}"
            assert fy_step.get("auto_hydrated") is True
    finally:
        await db.import_sessions.delete_many({"copropriete_id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.coproprietes.delete_one({"id": cid})


async def test_get_active_session_self_heals_missing_fiscal_year():
    """Une session existante SANS `steps.fiscal_year` (pre-iter90gi) est
    auto-hydratee lors du GET.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gi-heal-{suffix}"
    fy_id = f"fy-heal-{suffix}"
    sid = f"session-heal-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gi-heal-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": f"HEAL-{suffix}",
        "start_date": "2025-10-01", "end_date": "2026-09-30", "status": "open",
    })
    # Session existante SANS fiscal_year (pre-iter90gi)
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active",
        "steps": {"suppliers": {"count": 5}},
        "created_at": "2026-07-01T00:00:00+00:00",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.get(f"{BACKEND_URL}/api/import-wizard/sessions/active",
                                  params={"copropriete_id": cid})
            assert r.status_code == 200, r.text
            s = r.json()
            fy_step = (s.get("steps") or {}).get("fiscal_year") or {}
            assert fy_step.get("fiscal_year_id") == fy_id, f"expected {fy_id}: {fy_step}"
            assert fy_step.get("auto_hydrated") is True
            # Verifie que la mise a jour est bien persistee en base
            persisted = await db.import_sessions.find_one({"id": sid}, {"_id": 0})
            assert (persisted.get("steps") or {}).get("fiscal_year", {}).get("fiscal_year_id") == fy_id
    finally:
        await db.import_sessions.delete_one({"id": sid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.coproprietes.delete_one({"id": cid})


if __name__ == "__main__":
    asyncio.run(test_create_session_hydrates_fiscal_year_from_acp())
    print("OK test_create_session_hydrates_fiscal_year_from_acp")
    asyncio.run(test_get_active_session_self_heals_missing_fiscal_year())
    print("OK test_get_active_session_self_heals_missing_fiscal_year")
