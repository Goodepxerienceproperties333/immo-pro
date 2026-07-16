"""iter90gg : creation ACP avec periode d'exercice + lots lies (parent_lot_id)
+ redirect intra-FY mutations.

**Ticket utilisateur** :
> "lors de la creation des lots demander de confirmer les proprietaires a
> la date du debut d'exercice ... inviter le syndic a mentionner la periode
> de l'exercice ... une invite demande 'y a t-il des lots lies entre eux ?'
> ... en fin de wizzard 'y a-t-il eu des ventes depuis le debut de
> l'exercice ?'"

**Fix iter90gg** (backend `routes/coproprietes.py`) :
1. `LotInlineInput.parent_lot_number: Optional[str]` : le client passe le
   NUMERO du lot parent (les ids ne sont pas stables cote client). Le
   backend fait un 2e pass pour resoudre `parent_lot_number` -> `parent_lot_id`.
2. `CoproprieteInput.fy_start / fy_end / fy_name: Optional[str]` : si
   fournis, cree un `fiscal_year` en base avec `status='open'`.

Regressions couvertes :
1. Creation ACP avec fy_start/fy_end -> fiscal_year cree en base.
2. Creation ACP avec lots dont parent_lot_number pointe vers un autre
   lot du batch -> parent_lot_id resolu correctement.
3. parent_lot_number invalide (pointe vers un lot inexistant) -> ignore
   sans crash (parent_lot_id absent).
4. Creation sans fy_start -> AUCUN fiscal_year cree (retro-compat).
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


def test_iter90gg_creation_acp_with_fy_period_creates_fiscal_year():
    """Creation ACP avec fy_start + fy_end -> fiscal_year cree automatiquement."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        acp_name = f"iter90gg-{suffix}"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/coproprietes", json={
                    "name": acp_name,
                    "address": "Rue Test 1", "postal_code": "1000",
                    "city": "Bruxelles", "country": "Belgique",
                    "fy_start": "2025-10-01",
                    "fy_end": "2026-09-30",
                    "fy_name": "2025-2026",
                })
                assert r.status_code == 200, r.text
                new_id = r.json()["id"]
                # Verifie que le fiscal_year existe
                fy = await db.fiscal_years.find_one(
                    {"copropriete_id": new_id}, {"_id": 0}
                )
                assert fy is not None, "fiscal_year doit etre cree"
                assert fy["start_date"] == "2025-10-01"
                assert fy["end_date"] == "2026-09-30"
                assert fy["name"] == "2025-2026"
                assert fy["status"] == "open"
        finally:
            copro = await db.coproprietes.find_one({"name": acp_name}, {"_id": 0, "id": 1})
            if copro:
                await db.coproprietes.delete_one({"id": copro["id"]})
                await db.fiscal_years.delete_many({"copropriete_id": copro["id"]})
                await db.lots.delete_many({"copropriete_id": copro["id"]})
    asyncio.run(_run())


def test_iter90gg_lots_with_parent_lot_number_resolved():
    """Lot 'B1' (parking) avec parent_lot_number='A1' -> parent_lot_id
    resolu vers l'id du lot A1 apres insertion en base."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        acp_name = f"iter90gg-link-{suffix}"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/coproprietes", json={
                    "name": acp_name,
                    "lots": [
                        {"number": "A1", "lot_type": "apartment",
                         "description": "Appartement 2 chambres"},
                        {"number": "B1", "lot_type": "parking",
                         "parent_lot_number": "A1"},
                        {"number": "C1", "lot_type": "cave",
                         "parent_lot_number": "A1"},
                    ],
                })
                assert r.status_code == 200, r.text
                new_id = r.json()["id"]
                lots = await db.lots.find(
                    {"copropriete_id": new_id}, {"_id": 0}
                ).sort("number", 1).to_list(10)
                a1 = next(l for l in lots if l["number"] == "A1")
                b1 = next(l for l in lots if l["number"] == "B1")
                c1 = next(l for l in lots if l["number"] == "C1")
                assert b1.get("parent_lot_id") == a1["id"], (
                    f"B1.parent_lot_id doit pointer vers A1. "
                    f"Recu : {b1.get('parent_lot_id')}, attendu : {a1['id']}"
                )
                assert c1.get("parent_lot_id") == a1["id"]
                # A1 ne doit PAS avoir de parent
                assert not a1.get("parent_lot_id")
        finally:
            copro = await db.coproprietes.find_one({"name": acp_name}, {"_id": 0, "id": 1})
            if copro:
                await db.coproprietes.delete_one({"id": copro["id"]})
                await db.lots.delete_many({"copropriete_id": copro["id"]})
    asyncio.run(_run())


def test_iter90gg_invalid_parent_lot_number_ignored():
    """parent_lot_number qui ne correspond a aucun lot du batch -> ignore
    sans crash. Le lot est cree sans parent_lot_id."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        acp_name = f"iter90gg-inv-{suffix}"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/coproprietes", json={
                    "name": acp_name,
                    "lots": [
                        {"number": "A1", "lot_type": "apartment"},
                        {"number": "B1", "lot_type": "parking",
                         "parent_lot_number": "INEXISTANT"},
                    ],
                })
                assert r.status_code == 200, r.text
                new_id = r.json()["id"]
                b1 = await db.lots.find_one(
                    {"copropriete_id": new_id, "number": "B1"}, {"_id": 0}
                )
                assert not b1.get("parent_lot_id"), (
                    f"parent_lot_number invalide doit etre ignore. "
                    f"Recu parent_lot_id={b1.get('parent_lot_id')}"
                )
        finally:
            copro = await db.coproprietes.find_one({"name": acp_name}, {"_id": 0, "id": 1})
            if copro:
                await db.coproprietes.delete_one({"id": copro["id"]})
                await db.lots.delete_many({"copropriete_id": copro["id"]})
    asyncio.run(_run())


def test_iter90gg_no_fy_period_no_fiscal_year_created():
    """Retro-compat : creation ACP SANS fy_start/fy_end -> aucun
    fiscal_year cree (comportement historique)."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        acp_name = f"iter90gg-noref-{suffix}"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await _login(c)
                r = await c.post(f"{BACKEND_URL}/api/coproprietes", json={
                    "name": acp_name,
                    # <-- pas de fy_start/fy_end
                })
                assert r.status_code == 200, r.text
                new_id = r.json()["id"]
                fy = await db.fiscal_years.find_one(
                    {"copropriete_id": new_id}, {"_id": 0}
                )
                assert fy is None, (
                    "Sans fy_start, aucun fiscal_year ne doit etre cree"
                )
        finally:
            copro = await db.coproprietes.find_one({"name": acp_name}, {"_id": 0, "id": 1})
            if copro:
                await db.coproprietes.delete_one({"id": copro["id"]})
    asyncio.run(_run())
