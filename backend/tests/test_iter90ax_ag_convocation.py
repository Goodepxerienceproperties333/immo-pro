"""iter90ax : Tests du module Convocation AG (Code civil belge Livre 3).

Verifie :
- Creation d'une AG avec calcul auto du legal_check (delai 15j art. 3.87 §2)
- Ajout/modif/suppression de points a l'ordre du jour
- Reorder des points
- Refus de suppression une fois convocation envoyee
- Types de decision et bases legales
- Generation PDF convocation
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone, date

import httpx
import jwt
import pytest
from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _make_token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "email": f"u@t.be", "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_scene():
    """Cree un syndic + une copropriete + 2 owners."""
    db = await _mongo()
    syndic_id = ObjectId()
    copro_id = f"iter90ax-copro-{uuid.uuid4().hex[:8]}"
    owner1_id = f"iter90ax-o1-{uuid.uuid4().hex[:8]}"
    owner2_id = f"iter90ax-o2-{uuid.uuid4().hex[:8]}"

    await db.users.insert_one({
        "_id": syndic_id, "email": f"s-{uuid.uuid4().hex[:6]}@t.be",
        "role": "syndic", "name": "Syndic ax", "password_hash": "$2b$12$x",
        "authorized_mailboxes": [
            {"address": "syndic@cabinet.be", "active": True, "default": True},
        ],
    })
    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP iter90ax", "reference": "AX-01",
        "address": "Rue Test 1", "postal_code": "1000", "city": "Bruxelles",
        "syndic_user_id": str(syndic_id),
    })
    await db.owners.insert_many([
        {"id": owner1_id, "name": "Owner 1", "email": "o1@t.be", "copropriete_ids": [copro_id]},
        {"id": owner2_id, "name": "Owner 2", "email": "o2@t.be", "copropriete_ids": [copro_id]},
    ])
    await db.lots.insert_many([
        {"id": f"l1-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
         "number": "A1", "quotity": 100, "owner_id": owner1_id},
        {"id": f"l2-{uuid.uuid4().hex[:6]}", "copropriete_id": copro_id,
         "number": "A2", "quotity": 100, "owner_id": owner2_id},
    ])
    return str(syndic_id), copro_id, [owner1_id, owner2_id]


async def _teardown(syndic_id, copro_id, owner_ids):
    db = await _mongo()
    await db.users.delete_one({"_id": ObjectId(syndic_id)})
    await db.coproprietes.delete_one({"id": copro_id})
    await db.owners.delete_many({"id": {"$in": owner_ids}})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.ag_meetings.delete_many({"copropriete_id": copro_id})


# ==================== Tests ====================

async def _scenario_create_ag_legal_check_ok():
    """AG cree avec date +30j -> legal_check.on_time = True."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        future_date = (date.today() + timedelta(days=30)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            r = await c.post("/api/ag/meetings",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={"copropriete_id": copro_id,
                                   "type": "ordinaire",
                                   "scheduled_date": future_date,
                                   "location": "Salle X"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["type"] == "ordinaire"
            assert data["status"] == "draft"
            assert data["legal_check"]["on_time"] is True
            assert data["legal_check"]["days_until_ag"] >= 15
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_create_ag_legal_check_ok():
    asyncio.run(_scenario_create_ag_legal_check_ok())


async def _scenario_create_ag_legal_check_late():
    """AG cree avec date +5j -> legal_check.on_time = False + warning."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        late_date = (date.today() + timedelta(days=5)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            r = await c.post("/api/ag/meetings",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={"copropriete_id": copro_id,
                                   "scheduled_date": late_date,
                                   "location": "Salle X"})
            assert r.status_code == 200
            data = r.json()
            assert data["legal_check"]["on_time"] is False
            assert "Delai legal" in data["legal_check"]["warning"]
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_create_ag_legal_check_late():
    asyncio.run(_scenario_create_ag_legal_check_late())


async def _scenario_agenda_crud_reorder():
    """Ajout, modif, delete, reorder de points."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        future = (date.today() + timedelta(days=30)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            hd = {"Authorization": f"Bearer {_make_token(syndic_id)}"}
            r = await c.post("/api/ag/meetings", headers=hd,
                             json={"copropriete_id": copro_id, "scheduled_date": future, "location": "L"})
            mid = r.json()["id"]

            # Ajoute 3 points
            item_ids = []
            for i, title in enumerate(["Approbation PV", "Travaux facade", "Divers"]):
                r = await c.post(f"/api/ag/meetings/{mid}/agenda-items", headers=hd,
                                 json={"title": title, "decision_type": ["simple", "2_3", "info"][i]})
                assert r.status_code == 200
                item_ids.append(r.json()["id"])

            # Verifie liste
            r = await c.get(f"/api/ag/meetings/{mid}", headers=hd)
            assert len(r.json()["agenda_items"]) == 3

            # Modifie le 2eme
            r = await c.put(f"/api/ag/meetings/{mid}/agenda-items/{item_ids[1]}", headers=hd,
                            json={"title": "Travaux ravalement (modif)", "decision_type": "2_3"})
            assert r.status_code == 200
            assert r.json()["title"] == "Travaux ravalement (modif)"

            # Reorder : inverse (Divers d'abord)
            reversed_order = list(reversed(item_ids))
            r = await c.post(f"/api/ag/meetings/{mid}/reorder-agenda", headers=hd,
                             json=reversed_order)
            assert r.status_code == 200
            new_items = sorted(r.json()["agenda_items"], key=lambda x: x["order"])
            assert new_items[0]["id"] == item_ids[2]  # Divers en premier

            # Supprime le 1er
            r = await c.delete(f"/api/ag/meetings/{mid}/agenda-items/{item_ids[0]}", headers=hd)
            assert r.status_code == 200
            r = await c.get(f"/api/ag/meetings/{mid}", headers=hd)
            assert len(r.json()["agenda_items"]) == 2
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_agenda_crud_reorder():
    asyncio.run(_scenario_agenda_crud_reorder())


async def _scenario_convocation_pdf_download():
    """L'endpoint /convocation/pdf renvoie un vrai PDF."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        future = (date.today() + timedelta(days=30)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            hd = {"Authorization": f"Bearer {_make_token(syndic_id)}"}
            r = await c.post("/api/ag/meetings", headers=hd,
                             json={"copropriete_id": copro_id, "scheduled_date": future, "location": "L"})
            mid = r.json()["id"]
            await c.post(f"/api/ag/meetings/{mid}/agenda-items", headers=hd,
                         json={"title": "Approbation PV", "decision_type": "simple"})
            r = await c.get(f"/api/ag/meetings/{mid}/convocation/pdf", headers=hd)
            assert r.status_code == 200
            assert r.content[:4] == b"%PDF"
            assert r.headers.get("content-type", "").startswith("application/pdf")
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_convocation_pdf_download():
    asyncio.run(_scenario_convocation_pdf_download())


async def _scenario_send_convocation_dry_run():
    """L'envoi dry-run (MAIL_ENABLED != true) marque status = convocation_sent."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        future = (date.today() + timedelta(days=30)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=20) as c:
            hd = {"Authorization": f"Bearer {_make_token(syndic_id)}"}
            r = await c.post("/api/ag/meetings", headers=hd,
                             json={"copropriete_id": copro_id, "scheduled_date": future, "location": "L"})
            mid = r.json()["id"]
            await c.post(f"/api/ag/meetings/{mid}/agenda-items", headers=hd,
                         json={"title": "Point 1", "decision_type": "simple"})
            r = await c.post(f"/api/ag/meetings/{mid}/convocation/send"
                             f"?from_mailbox=syndic@cabinet.be", headers=hd)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is True
            # 2 owners with emails -> 2 envois attendus
            assert data["sent"] + len(data.get("failed") or []) == 2

            # Verifie status a jour
            r = await c.get(f"/api/ag/meetings/{mid}", headers=hd)
            assert r.json()["status"] == "convocation_sent"
            assert r.json()["convocation_sent_at"]
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_send_convocation_dry_run():
    asyncio.run(_scenario_send_convocation_dry_run())


async def _scenario_delete_convoquee_blocked():
    """Une AG deja convoquee ne peut plus etre supprimee."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        future = (date.today() + timedelta(days=30)).isoformat()
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=20) as c:
            hd = {"Authorization": f"Bearer {_make_token(syndic_id)}"}
            r = await c.post("/api/ag/meetings", headers=hd,
                             json={"copropriete_id": copro_id, "scheduled_date": future, "location": "L"})
            mid = r.json()["id"]
            await c.post(f"/api/ag/meetings/{mid}/agenda-items", headers=hd,
                         json={"title": "Point 1", "decision_type": "simple"})
            await c.post(f"/api/ag/meetings/{mid}/convocation/send"
                         f"?from_mailbox=syndic@cabinet.be", headers=hd)
            r = await c.delete(f"/api/ag/meetings/{mid}", headers=hd)
            assert r.status_code == 400
            assert "convoquee" in r.text.lower() or "tenue" in r.text.lower()
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_delete_convoquee_blocked():
    asyncio.run(_scenario_delete_convoquee_blocked())


async def _scenario_decision_types_endpoint():
    """Le referentiel legal est bien expose."""
    syndic_id, copro_id, owner_ids = await _setup_scene()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            r = await c.get("/api/ag/decision-types",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 200
            data = r.json()
            assert "simple" in data["decision_types"]
            assert "2_3" in data["decision_types"]
            assert "4_5" in data["decision_types"]
            assert "unanimite" in data["decision_types"]
            assert data["legal"]["min_days_convocation"] == 15
    finally:
        await _teardown(syndic_id, copro_id, owner_ids)


def test_decision_types_endpoint():
    asyncio.run(_scenario_decision_types_endpoint())
