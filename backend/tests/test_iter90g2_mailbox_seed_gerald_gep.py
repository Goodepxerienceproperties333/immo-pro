"""iter90g2 : seed mailbox welcome@goodexperienceproperties.be pour gerald@gep.be.

Corrige le bug "Graph 404 ErrorInvalidUser: The requested user 'gerald@gep.be'
is invalid" en s'assurant qu'au moins UNE mailbox valide (welcome@...) est
presente dans authorized_mailboxes pour ce syndic. La migration est executee
au startup du backend (`@app.on_event("startup")`) et est idempotente.

Regressions couvertes :
1. Un syndic gerald@gep.be sans authorized_mailboxes -> la boite est ajoutee
   ET marquee default (aucune autre boite par defaut existante).
2. Un syndic avec deja welcome@... -> aucune modification (idempotent).
3. Un syndic avec deja une AUTRE boite marquee default -> la nouvelle est
   ajoutee mais reste non-default (respect de la config existante).
4. Un syndic gerald@gep.be inexistant -> la migration ne crash pas.
"""
import asyncio
import os

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


async def _run_seed(db):
    """Repond a la logique de server.py:startup iter90g2. Duplique la logique
    pour la tester en isolation (sans redemarrer le serveur)."""
    gep_user = await db.users.find_one(
        {"email": "gerald@gep.be"}, {"_id": 1, "authorized_mailboxes": 1}
    )
    if not gep_user:
        return "no_user"
    boxes = list(gep_user.get("authorized_mailboxes") or [])
    wanted_addr = "welcome@goodexperienceproperties.be"
    has_wanted = any(
        (b.get("address") or "").lower() == wanted_addr for b in boxes
    )
    if has_wanted:
        return "already_present"
    has_default = any(b.get("default", False) for b in boxes)
    boxes.append({
        "address": wanted_addr,
        "display_name": "GEP - Good Experience Properties",
        "active": True,
        "default": not has_default,
    })
    await db.users.update_one(
        {"_id": gep_user["_id"]},
        {"$set": {"authorized_mailboxes": boxes}},
    )
    return "seeded"


@pytest.fixture
async def db():
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    _db = client["test_iter90g2_mailbox_seed"]
    yield _db
    await client.drop_database("test_iter90g2_mailbox_seed")


def _run(coro):
    return asyncio.run(coro)


def test_seed_adds_mailbox_when_absent():
    """Cas 1 : syndic sans authorized_mailboxes -> boite ajoutee + default."""
    async def _t():
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g2_case1"]
        try:
            await db.users.insert_one({"email": "gerald@gep.be", "role": "syndic"})
            result = await _run_seed(db)
            assert result == "seeded"
            u = await db.users.find_one({"email": "gerald@gep.be"})
            assert u.get("authorized_mailboxes") is not None
            boxes = u["authorized_mailboxes"]
            assert len(boxes) == 1
            assert boxes[0]["address"] == "welcome@goodexperienceproperties.be"
            assert boxes[0]["default"] is True
            assert boxes[0]["active"] is True
        finally:
            await client.drop_database("test_iter90g2_case1")
    _run(_t())


def test_seed_is_idempotent():
    """Cas 2 : welcome@... deja present -> aucune modification."""
    async def _t():
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g2_case2"]
        try:
            await db.users.insert_one({
                "email": "gerald@gep.be",
                "role": "syndic",
                "authorized_mailboxes": [
                    {
                        "address": "welcome@goodexperienceproperties.be",
                        "display_name": "Custom Name",
                        "active": True,
                        "default": True,
                    }
                ],
            })
            result = await _run_seed(db)
            assert result == "already_present"
            u = await db.users.find_one({"email": "gerald@gep.be"})
            boxes = u["authorized_mailboxes"]
            assert len(boxes) == 1
            # Aucune modification du display_name existant
            assert boxes[0]["display_name"] == "Custom Name"
        finally:
            await client.drop_database("test_iter90g2_case2")
    _run(_t())


def test_seed_respects_existing_default():
    """Cas 3 : autre boite deja default -> la nouvelle est ajoutee non-default."""
    async def _t():
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g2_case3"]
        try:
            await db.users.insert_one({
                "email": "gerald@gep.be",
                "role": "syndic",
                "authorized_mailboxes": [
                    {
                        "address": "contact@gep.be",
                        "display_name": "Contact GEP",
                        "active": True,
                        "default": True,
                    }
                ],
            })
            result = await _run_seed(db)
            assert result == "seeded"
            u = await db.users.find_one({"email": "gerald@gep.be"})
            boxes = u["authorized_mailboxes"]
            assert len(boxes) == 2
            # L'existante conserve son default
            existing = next(b for b in boxes if b["address"] == "contact@gep.be")
            new_box = next(b for b in boxes if b["address"] == "welcome@goodexperienceproperties.be")
            assert existing["default"] is True
            assert new_box["default"] is False
            assert new_box["active"] is True
        finally:
            await client.drop_database("test_iter90g2_case3")
    _run(_t())


def test_seed_skips_if_user_missing():
    """Cas 4 : gerald@gep.be inexistant -> pas de crash."""
    async def _t():
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client["test_iter90g2_case4"]
        try:
            result = await _run_seed(db)
            assert result == "no_user"
        finally:
            await client.drop_database("test_iter90g2_case4")
    _run(_t())
