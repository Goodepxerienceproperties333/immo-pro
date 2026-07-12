"""iter90eg : Balance des tiers - libelles clairs pour les mouvements FI.

Meme regle que iter90dv (PDF Situation) etendue a l'endpoint UI
`/reports/balance-tiers/owners/{owner_id}` et au portail proprietaire
`/owner-portal/movements` :
- Ligne FI au CREDIT du compte tier proprietaire -> "Paiement recu"
- Ligne FI au DEBIT  du compte tier proprietaire -> "Votre remboursement"
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


async def _seed(db, suffix: str) -> dict:
    """Un ACP + 1 owner avec tier 400xxx + 2 ecritures FI (credit et debit)."""
    cid = f"iter90eg-{suffix}"
    oid = f"iter90eg-o-{suffix}"
    tier_prov = "40000123"
    await db.coproprietes.insert_one({"id": cid, "name": "iter90eg ACP"})
    await db.owners.insert_one({
        "id": oid, "name": "Matexi Group", "last_name": "Matexi",
        "vcs_code": "+++123/4567/89012+++",
        "tier_accounts": {cid: {"provisions": tier_prov, "reserve": ""}},
    })
    # Ecriture FI 1 : Matexi paie l'ACP -> credit 400xxx
    await db.journal_entries.insert_one({
        "id": f"je-cred-{suffix}", "copropriete_id": cid,
        "journal_type": "FI", "date": "2026-04-10",
        "reference": "FI-42",
        "description": "Extrait bancaire n42",
        "lines": [
            {"account_number": "550000", "debit": 6253.00, "credit": 0.0},
            {"account_number": tier_prov, "debit": 0.0, "credit": 6253.00,
             "third_party_id": oid, "third_party_name": "Matexi Group",
             "counterparty_name": "Matexi Group",
             "line_description": "Matexi"},
        ],
    })
    # Ecriture FI 2 : ACP rembourse Matexi -> debit 400xxx
    await db.journal_entries.insert_one({
        "id": f"je-deb-{suffix}", "copropriete_id": cid,
        "journal_type": "FI", "date": "2026-05-05",
        "reference": "FI-51",
        "description": "Remboursement propriete Matexi",
        "lines": [
            {"account_number": tier_prov, "debit": 1500.00, "credit": 0.0,
             "third_party_id": oid, "third_party_name": "Matexi Group",
             "counterparty_name": "Matexi Group",
             "line_description": "Matexi"},
            {"account_number": "550000", "debit": 0.0, "credit": 1500.00},
        ],
    })
    return {"cid": cid, "oid": oid, "tier_prov": tier_prov}


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.owners.delete_many({"tier_accounts." + cid: {"$exists": True}})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_balance_tiers_endpoint_labels_credit_as_paiement_recu():
    """Endpoint UI : ligne FI au CREDIT -> description 'Paiement recu'."""
    async def _run():
        db = await _mongo()
        ctx = await _seed(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                r = await client.get(
                    f"{BACKEND_URL}/api/reports/balance-tiers/owners/{ctx['oid']}",
                    params={"copropriete_id": ctx["cid"], "group_by_owner": False},
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                movs = data.get("movements", [])
                # Trouve la ligne credit (paiement du proprio)
                cred_movs = [m for m in movs if m.get("credit", 0) > 0
                              and m.get("journal_type") == "FI"]
                assert len(cred_movs) >= 1, f"Aucun mouvement FI credit trouve : {movs}"
                desc = cred_movs[0]["description"]
                assert "Paiement recu" in desc, (
                    f"Le libelle du mouvement FI credit doit contenir 'Paiement recu',"
                    f" recu : {desc}"
                )
                # Ne doit PAS contenir "remboursement"
                assert "remboursement" not in desc.lower(), (
                    f"Un mouvement CREDIT ne doit PAS etre etiquete 'remboursement' : {desc}"
                )
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_balance_tiers_endpoint_labels_debit_as_votre_remboursement():
    """Endpoint UI : ligne FI au DEBIT -> description 'Votre remboursement'."""
    async def _run():
        db = await _mongo()
        ctx = await _seed(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                r = await client.get(
                    f"{BACKEND_URL}/api/reports/balance-tiers/owners/{ctx['oid']}",
                    params={"copropriete_id": ctx["cid"], "group_by_owner": False},
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                movs = data.get("movements", [])
                deb_movs = [m for m in movs if m.get("debit", 0) > 0
                             and m.get("journal_type") == "FI"]
                assert len(deb_movs) >= 1, f"Aucun mouvement FI debit trouve : {movs}"
                desc = deb_movs[0]["description"]
                assert "Votre remboursement" in desc, (
                    f"Le libelle du mouvement FI debit doit contenir 'Votre remboursement',"
                    f" recu : {desc}"
                )
                # Ne doit PAS contenir "Paiement recu"
                assert "Paiement recu" not in desc, (
                    f"Un mouvement DEBIT ne doit PAS etre etiquete 'Paiement recu' : {desc}"
                )
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_non_fi_movements_are_not_relabeled():
    """Une ecriture non-FI (ex OD, VE, AC) conserve son libelle originel."""
    async def _run():
        db = await _mongo()
        ctx = await _seed(db, uuid.uuid4().hex[:6])
        # Ajoute une OD au debit sur le tier
        await db.journal_entries.insert_one({
            "id": f"je-od-{uuid.uuid4().hex[:6]}",
            "copropriete_id": ctx["cid"],
            "journal_type": "OD", "date": "2026-06-01",
            "reference": "OD-TEST",
            "description": "Test OD manuelle",
            "lines": [
                {"account_number": ctx["tier_prov"], "debit": 100.0, "credit": 0.0,
                 "third_party_id": ctx["oid"], "third_party_name": "Matexi",
                 "line_description": "OD test manuelle"},
                {"account_number": "700000", "debit": 0.0, "credit": 100.0},
            ],
        })
        try:
            async with httpx.AsyncClient() as client:
                hdr = await _login(client)
                hdr["X-Copropriete-Id"] = ctx["cid"]
                r = await client.get(
                    f"{BACKEND_URL}/api/reports/balance-tiers/owners/{ctx['oid']}",
                    params={"copropriete_id": ctx["cid"], "group_by_owner": False},
                    headers=hdr,
                )
                assert r.status_code == 200, r.text
                data = r.json()
                od_movs = [m for m in data.get("movements", [])
                           if m.get("journal_type") == "OD"]
                assert len(od_movs) >= 1
                # OD conserve son libelle originel
                desc = od_movs[0]["description"]
                assert "OD test manuelle" in desc, (
                    f"Le libelle OD ne doit PAS etre reetiquete FI : {desc}"
                )
                assert "Paiement recu" not in desc
                assert "Votre remboursement" not in desc
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())
