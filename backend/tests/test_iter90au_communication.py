"""iter90au : Tests du module de communication.

Approche e2e HTTP via httpx sur le backend en cours (pattern deja utilise
par test_iter90ae_merge_preview.py et co).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

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
        {
            "sub": sub,
            "email": f"user-{sub}@test.be",
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        os.environ.get("JWT_SECRET", "dev-secret-change-me"),
        algorithm="HS256",
    )


async def _mongo():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _setup_users():
    db = await _mongo()
    syndic_id = ObjectId()
    manager_id = ObjectId()
    await db.users.insert_one({
        "_id": syndic_id,
        "email": f"syndic-{uuid.uuid4().hex[:6]}@test.be",
        "name": "Syndic iter90au",
        "role": "syndic",
        "password_hash": "$2b$12$fake",
        "authorized_mailboxes": [
            {"address": "compta@cabinet.be", "display_name": "Comptabilite", "active": True},
            {"address": "gestion@cabinet.be", "display_name": "Gestion", "active": True},
        ],
        "signature_html": "<b>Syndic</b>",
    })
    await db.users.insert_one({
        "_id": manager_id,
        "email": f"manager-{uuid.uuid4().hex[:6]}@test.be",
        "name": "Manager iter90au",
        "role": "gestionnaire",
        "password_hash": "$2b$12$fake",
        "parent_syndic_id": str(syndic_id),
        "signature_html": "<b>Manager</b>",
    })
    return str(syndic_id), str(manager_id)


async def _teardown_users(syndic_id: str, manager_id: str):
    db = await _mongo()
    await db.users.delete_one({"_id": ObjectId(syndic_id)})
    await db.users.delete_one({"_id": ObjectId(manager_id)})


async def _scenario_mailbox_heritage():
    """Verifie que le gestionnaire herite des boites du syndic parent."""
    syndic_id, manager_id = await _setup_users()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Syndic voit ses boites
            r = await c.get("/api/communication/mailboxes",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 200, r.text
            addrs = [b["address"] for b in r.json()["mailboxes"]]
            assert "compta@cabinet.be" in addrs
            assert "gestion@cabinet.be" in addrs

            # Gestionnaire herite
            r = await c.get("/api/communication/mailboxes",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.status_code == 200, r.text
            addrs_m = [b["address"] for b in r.json()["mailboxes"]]
            assert "compta@cabinet.be" in addrs_m
    finally:
        await _teardown_users(syndic_id, manager_id)


def test_mailbox_heritage():
    asyncio.run(_scenario_mailbox_heritage())


async def _scenario_add_remove_mailbox():
    syndic_id, manager_id = await _setup_users()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            new_addr = f"box-{uuid.uuid4().hex[:6]}@cabinet.be"
            # Ajout par syndic : OK
            r = await c.post("/api/communication/mailboxes",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={"address": new_addr, "display_name": "Nouvelle"})
            assert r.status_code == 200, r.text
            assert any(b["address"] == new_addr for b in r.json()["mailboxes"])

            # Ajout par gestionnaire : 403
            r = await c.post("/api/communication/mailboxes",
                             headers={"Authorization": f"Bearer {_make_token(manager_id)}"},
                             json={"address": "hack@evil.be"})
            assert r.status_code == 403, r.text

            # Retrait par syndic : OK
            r = await c.delete(f"/api/communication/mailboxes?address={new_addr}",
                               headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.status_code == 200, r.text
            assert all(b["address"] != new_addr for b in r.json()["mailboxes"])
    finally:
        await _teardown_users(syndic_id, manager_id)


def test_add_remove_mailbox():
    asyncio.run(_scenario_add_remove_mailbox())


async def _scenario_signature_per_user():
    syndic_id, manager_id = await _setup_users()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Manager voit sa signature perso (pas celle du syndic)
            r = await c.get("/api/communication/signature",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.status_code == 200, r.text
            assert r.json()["signature_html"] == "<b>Manager</b>"

            # Modifie
            r = await c.put("/api/communication/signature",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"},
                            json={"signature_html": "<i>Nouvelle sig</i>"})
            assert r.status_code == 200, r.text

            # Verifie que la signature du syndic n'a PAS change
            r = await c.get("/api/communication/signature",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            assert r.json()["signature_html"] == "<b>Syndic</b>"

            # Manager relit
            r = await c.get("/api/communication/signature",
                            headers={"Authorization": f"Bearer {_make_token(manager_id)}"})
            assert r.json()["signature_html"] == "<i>Nouvelle sig</i>"
    finally:
        await _teardown_users(syndic_id, manager_id)


def test_signature_per_user():
    asyncio.run(_scenario_signature_per_user())


async def _scenario_send_situation_guards():
    syndic_id, manager_id = await _setup_users()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            # Boite non autorisee : 403
            r = await c.post("/api/communication/send/situation",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={
                                 "from_mailbox": "not-allowed@evil.be",
                                 "copropriete_id": "fake-id",
                                 "owner_ids": ["fake-owner"],
                             })
            assert r.status_code == 403, r.text

            # Owner list vide : 400
            r = await c.post("/api/communication/send/situation",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={
                                 "from_mailbox": "compta@cabinet.be",
                                 "copropriete_id": "fake-id",
                                 "owner_ids": [],
                             })
            assert r.status_code == 400, r.text
    finally:
        await _teardown_users(syndic_id, manager_id)


def test_send_situation_guards():
    asyncio.run(_scenario_send_situation_guards())


async def _scenario_owners_balances_requires_copro():
    syndic_id, manager_id = await _setup_users()
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as c:
            r = await c.get("/api/communication/owners-balances",
                            headers={"Authorization": f"Bearer {_make_token(syndic_id)}"})
            # Sans query param -> 422 (FastAPI validation)
            assert r.status_code in (400, 422), r.text
    finally:
        await _teardown_users(syndic_id, manager_id)


def test_owners_balances_requires_copro():
    asyncio.run(_scenario_owners_balances_requires_copro())


async def _scenario_send_situation_dry_run_e2e():
    """Cree un owner reel + copropriete + envoie situation en dry-run."""
    syndic_id, manager_id = await _setup_users()
    db = await _mongo()
    copro_id = f"iter90au-copro-{uuid.uuid4().hex[:8]}"
    owner_id = f"iter90au-owner-{uuid.uuid4().hex[:8]}"
    try:
        await db.coproprietes.insert_one({
            "id": copro_id, "name": "Test iter90au ACP", "reference": "T90AU",
            "status": "active", "syndic_name": "Cabinet Test",
            "bank_accounts": [{"iban": "BE68539007547034", "bic": "GKCCBEBB"}],
        })
        await db.owners.insert_one({
            "id": owner_id, "name": "Owner iter90au", "email": "owner@test.be",
            "copropriete_ids": [copro_id],
            "tier_accounts": {copro_id: {"provisions": "40000001", "reserve": "40010001"}},
        })
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=15) as c:
            r = await c.post("/api/communication/send/situation",
                             headers={"Authorization": f"Bearer {_make_token(syndic_id)}"},
                             json={
                                 "from_mailbox": "compta@cabinet.be",
                                 "copropriete_id": copro_id,
                                 "owner_ids": [owner_id],
                                 "subject": "Test iter90au",
                                 "body_html": "Bonjour",
                             })
            assert r.status_code == 200, r.text
            body = r.json()
            # En dry-run (MAIL_ENABLED != true), le PDF est genere mais pas envoye
            assert body["success"] is True
            # sent + failed doit couvrir le owner
            assert body["sent"] + len(body.get("failed") or []) == 1
    finally:
        await db.coproprietes.delete_one({"id": copro_id})
        await db.owners.delete_one({"id": owner_id})
        await _teardown_users(syndic_id, manager_id)


def test_send_situation_dry_run_e2e():
    asyncio.run(_scenario_send_situation_dry_run_e2e())
