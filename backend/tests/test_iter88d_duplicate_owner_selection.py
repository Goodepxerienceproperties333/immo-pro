"""Regression test - iter88d - /owners/check-duplicate renvoie owner_id + details.

Demande user (Feb 2026, avec screenshot) :
    "En cas de doublons de proprietaire detecte, permettre de le selectionner"

Avant iter88d, le endpoint /owners/check-duplicate renvoyait :
    {field, value, owner_name, copropriete_id}
... ce qui ne permettait PAS au frontend de proposer de "selectionner" le
doublon (pas d'owner_id). L'utilisateur etait force soit d'annuler et de
chercher manuellement le proprio, soit de creer un doublon en DB.

Apres iter88d, la response inclut owner_id + email/phone/vcs/first_name/
last_name + copropriete_ids -> le frontend peut basculer en mode edition
sur le doublon en 1 click.

Tests :
  1. Match par email -> owner_id + tous les details retournes
  2. Match par phone -> idem
  3. Un meme owner qui match sur email ET phone -> dedup possible cote frontend
     (seen_ids cote backend) -> 2 entrees backend MAX 1 entree de plus
  4. Sans match -> liste vide
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _get_endpoint(router_factory, db, route_path: str, method: str = "GET"):
    router = router_factory(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


class _Req:
    """Mock fastapi Request - headers vides (mode superadmin via mock)."""
    def __init__(self):
        self.headers = {}


async def _patch_user_scope(scope_value):
    """Mock le user_scope via server.get_current_user (closure locale dans
    routes.properties impossible a monkeypatch directement).
    `scope_value` = (is_super: bool, copros: list).
    Si is_super=True, renvoie role=superadmin (allowed_copros=None applique
    par _get_user_scope). Sinon role=syndic avec copropriete_ids fournis.
    """
    import server as srv_mod
    original = srv_mod.get_current_user
    is_super, copros = scope_value

    async def _mock(_req):
        if is_super:
            return {"id": "test", "role": "superadmin", "email": "t@t", "copropriete_ids": []}
        return {"id": "test", "role": "syndic", "email": "t@t", "copropriete_ids": copros or []}

    srv_mod.get_current_user = _mock
    return lambda: setattr(srv_mod, "get_current_user", original)


async def _test_email_match_returns_owner_id_and_details():
    db = await _setup_db()
    from routes.properties import create_properties_router
    cid = f"itr88d-{uuid.uuid4()}"
    oid = f"o-{uuid.uuid4()}"
    test_email = f"selimabed-{uuid.uuid4().hex[:8]}@protonmail.com"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR88D", "status": "active"})
    await db.owners.insert_one({
        "id": oid, "name": "ABED - STEUVE Selim & Elise",
        "first_name": "Selim & Elise", "last_name": "ABED - STEUVE",
        "email": test_email, "phone": "+32475...",
        "vcs_code": "+++100/0001/00001+++",
        "copropriete_id": cid, "copropriete_ids": [cid],
    })
    restore = await _patch_user_scope((True, []))
    try:
        check = _get_endpoint(create_properties_router, db, "/api/owners/check-duplicate", "GET")
        result = await check(request=_Req(), email=test_email, phone=None)
        assert result["has_duplicates"] is True
        dups = result["duplicates"]
        assert len(dups) == 1
        d = dups[0]
        # iter88d : NOUVEAUX champs (criteres du fix)
        assert d.get("owner_id") == oid, "owner_id doit etre present"
        assert d["owner_email"] == test_email
        assert d["owner_first_name"] == "Selim & Elise"
        assert d["owner_last_name"] == "ABED - STEUVE"
        assert d["owner_vcs_code"] == "+++100/0001/00001+++"
        assert d["owner_phone"] == "+32475..."
        # Champs legacy conserves
        assert d["field"] == "email"
        assert d["value"] == test_email
        assert "ABED" in d["owner_name"]
        # ACPs
        assert cid in d["copropriete_ids"]
        print("OK - iter88d : email match retourne owner_id + tous les details")
    finally:
        restore()
        await db.owners.delete_one({"id": oid})
        await db.coproprietes.delete_one({"id": cid})


async def _test_phone_match_returns_owner_id():
    db = await _setup_db()
    from routes.properties import create_properties_router
    oid = f"o-{uuid.uuid4()}"
    test_phone = f"+32487{uuid.uuid4().hex[:6]}"
    await db.owners.insert_one({
        "id": oid, "name": "Test Phone", "first_name": "P", "last_name": "T",
        "email": "", "phone": test_phone, "copropriete_ids": [],
    })
    restore = await _patch_user_scope((True, []))
    try:
        check = _get_endpoint(create_properties_router, db, "/api/owners/check-duplicate", "GET")
        result = await check(request=_Req(), email=None, phone=test_phone)
        assert result["has_duplicates"] is True
        # filter to our owner_id (in case other tests leftover)
        ours = [d for d in result["duplicates"] if d["owner_id"] == oid]
        assert len(ours) == 1
        d = ours[0]
        assert d["field"] == "phone"
        assert d["owner_phone"] == test_phone
        print("OK - iter88d : phone match retourne owner_id")
    finally:
        restore()
        await db.owners.delete_one({"id": oid})


async def _test_dedup_when_both_email_and_phone_match():
    """Quand le meme owner match sur email ET phone, le backend dedup deja
    (seen_ids) -> 1 seule entree dans duplicates."""
    db = await _setup_db()
    from routes.properties import create_properties_router
    oid = f"o-{uuid.uuid4()}"
    test_email = f"dual-{uuid.uuid4().hex[:8]}@test.com"
    test_phone = f"+32499{uuid.uuid4().hex[:6]}"
    await db.owners.insert_one({
        "id": oid, "name": "Dual Match", "first_name": "D", "last_name": "M",
        "email": test_email, "phone": test_phone, "copropriete_ids": [],
    })
    restore = await _patch_user_scope((True, []))
    try:
        check = _get_endpoint(create_properties_router, db, "/api/owners/check-duplicate", "GET")
        result = await check(request=_Req(), email=test_email, phone=test_phone)
        assert result["has_duplicates"] is True
        # filter to our owner_id
        ours = [d for d in result["duplicates"] if d["owner_id"] == oid]
        # 1 seule entree (dedup via seen_ids)
        assert len(ours) == 1, f"Doit etre 1 (dedup), trouve {len(ours)}"
        print("OK - iter88d : dedup quand meme owner match sur email + phone")
    finally:
        restore()
        await db.owners.delete_one({"id": oid})


async def _test_no_match():
    db = await _setup_db()
    from routes.properties import create_properties_router
    restore = await _patch_user_scope((True, []))
    try:
        check = _get_endpoint(create_properties_router, db, "/api/owners/check-duplicate", "GET")
        result = await check(request=_Req(), email="inexistant-iter88d@nope.zzz", phone=None)
        assert result["has_duplicates"] is False
        assert result["duplicates"] == []
        print("OK - iter88d : pas de match -> liste vide")
    finally:
        restore()


def test_iter88d_email_match_returns_owner_id_and_details():
    asyncio.run(_test_email_match_returns_owner_id_and_details())


def test_iter88d_phone_match_returns_owner_id():
    asyncio.run(_test_phone_match_returns_owner_id())


def test_iter88d_dedup_email_and_phone_match():
    asyncio.run(_test_dedup_when_both_email_and_phone_match())


def test_iter88d_no_match():
    asyncio.run(_test_no_match())
