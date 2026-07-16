"""iter90gj : `commit-distribution-keys` marque automatiquement une cle
comme `is_default: true` si aucune ne l'est deja pour l'ACP.

**Ticket utilisateur** : "erreur lors de la mutation il considere qu'il
n'y a pas de cle de repartition alors qu'elle a ete cree dans le wizzard"

**Cause** : le wizard importait les cles avec `is_default=false` par
defaut. Le endpoint mutation exige une cle default pour calculer le
transfert du fonds de roulement -> HTTPException "Aucune cle par defaut".

**Fix** : apres insertion des cles dans commit-distribution-keys, verifier
qu'une cle default existe. Sinon, marquer automatiquement :
1. La cle "Charges communes" (code 0001 ou nom commencant par "Charges
   communes")
2. Sinon, la cle avec le plus grand nombre de lots.
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


async def _setup(db, suffix):
    cid = f"iter90gj-dk-{suffix}"
    sid = f"session-dk-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-dk-{suffix}"})
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active", "steps": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    # 2 lots (001, 101)
    for num in ["001", "101"]:
        await db.lots.insert_one({
            "id": f"lot-{num}-{suffix}", "copropriete_id": cid,
            "number": num, "description": f"{num} - APPARTEMENT", "quotity": 100,
        })
    return {"cid": cid, "sid": sid}


async def _cleanup(db, cid, sid):
    await db.coproprietes.delete_one({"id": cid})
    await db.import_sessions.delete_one({"id": sid})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})


async def test_charges_communes_auto_marked_default():
    """Une cle "Charges communes" est automatiquement marquee is_default=True."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-distribution-keys",
                json={"keys": [
                    {"code": "0001", "name": "Charges communes",
                     "lines": [{"lot_label": "001 - APPARTEMENT", "quotity": 100},
                                {"lot_label": "101 - APPARTEMENT", "quotity": 100}]},
                    {"code": "0002", "name": "Cle ascenseurs",
                     "lines": [{"lot_label": "101 - APPARTEMENT", "quotity": 100}]},
                ]},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inserted"] == 2
            assert "Charges communes" in body.get("default_key_auto", "")

            keys = await db.distribution_keys.find(
                {"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(10)
            defaults = [k for k in keys if k.get("is_default")]
            assert len(defaults) == 1, f"expected 1 default, got {len(defaults)}"
            assert defaults[0]["name"] == "Charges communes"
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"])


async def test_fallback_key_with_most_lots_when_no_charges_communes():
    """Sans "Charges communes", la cle avec le plus de lots devient default."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-distribution-keys",
                json={"keys": [
                    {"code": "0100", "name": "Petite cle",
                     "lines": [{"lot_label": "001", "quotity": 100}]},
                    {"code": "0200", "name": "Grande cle",
                     "lines": [{"lot_label": "001 - APPARTEMENT", "quotity": 100},
                                {"lot_label": "101 - APPARTEMENT", "quotity": 100}]},
                ]},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["inserted"] == 2
            keys = await db.distribution_keys.find(
                {"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(10)
            defaults = [k for k in keys if k.get("is_default")]
            assert len(defaults) == 1
            assert defaults[0]["name"] == "Grande cle", f"got {defaults[0]['name']}"
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"])


async def test_no_auto_default_if_one_already_set():
    """Si l'utilisateur a deja marque manuellement une cle par defaut avant
    re-import, on ne l'ecrase PAS.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    # Pre-insertion : une cle manuelle deja marquee default
    manual_id = f"manual-{suffix}"
    await db.distribution_keys.insert_one({
        "id": manual_id, "copropriete_id": ctx["cid"],
        "code": "MANUAL", "name": "Ma cle manuelle", "is_default": True,
        "lots": [], "lines": [], "total_quotities": 0, "key_type": "quotity",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-distribution-keys",
                json={"keys": [
                    {"code": "0001", "name": "Charges communes",
                     "lines": [{"lot_label": "001", "quotity": 100}]},
                ]},
            )
            assert r.status_code == 200
            keys = await db.distribution_keys.find(
                {"copropriete_id": ctx["cid"]}, {"_id": 0}).to_list(10)
            defaults = [k for k in keys if k.get("is_default")]
            assert len(defaults) == 1
            assert defaults[0]["id"] == manual_id, "manual default should not be overriden"
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"])


if __name__ == "__main__":
    asyncio.run(test_charges_communes_auto_marked_default())
    print("OK test_charges_communes_auto_marked_default")
    asyncio.run(test_fallback_key_with_most_lots_when_no_charges_communes())
    print("OK test_fallback_key_with_most_lots_when_no_charges_communes")
    asyncio.run(test_no_auto_default_if_one_already_set())
    print("OK test_no_auto_default_if_one_already_set")
