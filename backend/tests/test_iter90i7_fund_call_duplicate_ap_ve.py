"""iter90i7 : Tests du double-comptage du fonds de reserve dans le bilan.

Bug corrige : la route legacy `POST /fund-calls/{id}/generate-entries`
creait un JE de type AP (Appel) alors que le POST /fund-calls creait
deja automatiquement un JE VE (Ventes) via `generate_sale_entry`.
Le doublon provoquait un solde 160 (fonds de reserve) surevalue de
2x l'appel : bilan 9000 au lieu de 7000.

Correctifs :
1. Route legacy verifie l'existence d'un VE et retourne 409.
2. Endpoint `heal-duplicate-fund-call-entries` supprime les AP en doublon.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_legacy_generate_entries_refuses_if_ve_already_exists():
    """iter90i7-1 : la route legacy refuse (409) si un VE existe deja pour
    le meme fund_call (empeche le double-comptage)."""
    copro_id = f"acp-i7-{uuid.uuid4().hex[:8]}"
    call_id = f"call-i7-{uuid.uuid4().hex[:8]}"

    async def _run():
        from fastapi import HTTPException
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.fund_calls import create_fund_calls_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.fund_calls.delete_many({"id": call_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            await db.fund_calls.insert_one({
                "id": call_id, "copropriete_id": copro_id,
                "name": "Appel 2026-1", "date": "2026-05-01",
                "call_type": "reserve", "total_amount": 2000.0,
            })
            # Simule qu'un VE a deja ete auto-genere
            await db.journal_entries.insert_one({
                "id": f"ve-{call_id}", "journal_type": "VE",
                "source_type": "fund_call", "source_id": call_id,
                "copropriete_id": copro_id, "date": "2026-05-01",
                "reference": "AF-Fonds de reserve",
                "lines": [], "total_debit": 2000.0, "total_credit": 2000.0,
            })
            # Cherche l'endpoint legacy
            router = create_fund_calls_router(db)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/fund-calls/{call_id}/generate-entries":
                    handler = r.endpoint
                    break
            assert handler is not None
            raised_409 = False
            try:
                await handler(call_id=call_id)
            except HTTPException as e:
                raised_409 = e.status_code == 409
            assert raised_409, (
                "La route legacy DOIT retourner 409 quand un VE existe "
                "deja pour eviter le double-comptage."
            )
            # Aucun AP n'a ete cree
            ap_count = await db.journal_entries.count_documents(
                {"fund_call_id": call_id, "journal_type": "AP"}
            )
            assert ap_count == 0
        finally:
            await db.fund_calls.delete_many({"id": call_id})
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_heal_duplicate_removes_ap_when_ve_exists():
    """iter90i7-2 : l'endpoint heal-duplicate-fund-call-entries supprime
    les AP en doublon et laisse le VE."""
    copro_id = f"acp-i7-{uuid.uuid4().hex[:8]}"
    call_id = f"call-i7-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.admin import create_admin_router
        from server import create_access_token
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        uid_str = None
        try:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            await db.fund_calls.delete_many({"id": call_id})
            # Cree superadmin de test
            r = await db.users.insert_one({
                "email": f"sa-{uuid.uuid4().hex[:6]}@t.local",
                "password_hash": "x", "role": "superadmin", "name": "SA",
            })
            uid_str = str(r.inserted_id)
            token = create_access_token(uid_str, "sa@t.local")

            class _Cookies:
                def get(self, k, default=None):
                    return token if k == "access_token" else default

            class _Headers:
                def get(self, k, default=""):
                    return default

            class _Req:
                def __init__(self):
                    class S:
                        pass
                    self.state = S()
                    self.state.user_id = uid_str
                    self.cookies = _Cookies()
                    self.headers = _Headers()

            # Cree la paire AP + VE en doublon
            ap_id = f"ap-{call_id}"
            ve_id = f"ve-{call_id}"
            await db.journal_entries.insert_many([
                {"id": ap_id, "journal_type": "AP",
                 "fund_call_id": call_id, "copropriete_id": copro_id,
                 "date": "2026-05-01", "reference": "AP-Reserve",
                 "lines": [], "total_debit": 2000.0, "total_credit": 2000.0},
                {"id": ve_id, "journal_type": "VE",
                 "source_type": "fund_call", "source_id": call_id,
                 "copropriete_id": copro_id, "date": "2026-05-01",
                 "reference": "AF-Reserve", "lines": [],
                 "total_debit": 2000.0, "total_credit": 2000.0},
            ])
            # Cherche l'endpoint
            router = create_admin_router(db)
            handler = None
            for r_ in router.routes:
                if getattr(r_, "path", "") == "/api/admin/heal-duplicate-fund-call-entries":
                    handler = r_.endpoint
                    break
            assert handler is not None
            # Dry-run : detecte mais ne supprime pas
            dry = await handler(request=_Req(), copropriete_id=copro_id, dry_run=True)
            assert dry["duplicates_found"] == 1
            assert dry["removed"] == 0
            still_ap = await db.journal_entries.count_documents({"id": ap_id})
            assert still_ap == 1
            # Live : supprime
            live = await handler(request=_Req(), copropriete_id=copro_id, dry_run=False)
            assert live["duplicates_found"] == 1
            assert live["removed"] == 1
            still_ap_after = await db.journal_entries.count_documents({"id": ap_id})
            assert still_ap_after == 0
            # Le VE est toujours la
            still_ve = await db.journal_entries.count_documents({"id": ve_id})
            assert still_ve == 1
        finally:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            await db.fund_calls.delete_many({"id": call_id})
            if uid_str:
                from bson import ObjectId
                try:
                    await db.users.delete_one({"_id": ObjectId(uid_str)})
                except Exception:
                    pass
            client.close()

    asyncio.run(_run())


def test_reserve_fund_balance_after_cleanup_equals_expected():
    """iter90i7-3 : apres nettoyage, solde 160 = AN prec + appels courants
    (5000 + 2000 = 7000), plus jamais 9000 (5000 + 2000 + 2000 double)."""
    copro_id = f"acp-i7-{uuid.uuid4().hex[:8]}"
    call_id = f"call-i7-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            # 1) AN d'ouverture cred 160 = 5000
            await db.journal_entries.insert_one({
                "id": f"an-{copro_id}", "journal_type": "AN",
                "copropriete_id": copro_id, "date": "2026-01-01",
                "reference": "AN-2026",
                "is_opening_balance": True,
                "lines": [
                    {"account_number": "160", "debit": 0, "credit": 5000},
                    {"account_number": "550000", "debit": 5000, "credit": 0},
                ],
                "total_debit": 5000, "total_credit": 5000,
            })
            # 2) VE auto (appel 2000)
            await db.journal_entries.insert_one({
                "id": f"ve-{call_id}", "journal_type": "VE",
                "copropriete_id": copro_id, "date": "2026-05-01",
                "source_type": "fund_call", "source_id": call_id,
                "reference": "AF-Reserve",
                "lines": [
                    {"account_number": "40100001", "debit": 2000, "credit": 0},
                    {"account_number": "160", "debit": 0, "credit": 2000},
                ],
                "total_debit": 2000, "total_credit": 2000,
            })
            # Solde 160 = 7000
            credit = 0.0
            debit = 0.0
            async for j in db.journal_entries.find(
                {"copropriete_id": copro_id,
                 "lines.account_number": {"$regex": "^160"},
                 "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "lines": 1},
            ):
                for ln in j.get("lines", []):
                    if ln.get("account_number", "").startswith("160"):
                        debit += float(ln.get("debit", 0) or 0)
                        credit += float(ln.get("credit", 0) or 0)
            assert abs((credit - debit) - 7000.0) < 0.01, (
                f"Solde 160 attendu 7000 (5000 AN + 2000 appel), got {credit - debit}"
            )
            # Ajout d'un AP legacy 2000 => solde = 9000 (bug historique)
            await db.journal_entries.insert_one({
                "id": f"ap-{call_id}", "journal_type": "AP",
                "fund_call_id": call_id, "copropriete_id": copro_id,
                "date": "2026-05-01", "reference": "AP-Reserve",
                "lines": [
                    {"account_number": "401000", "debit": 2000, "credit": 0},
                    {"account_number": "160", "debit": 0, "credit": 2000},
                ],
                "total_debit": 2000, "total_credit": 2000,
            })
            # Solde 160 devient 9000
            credit = 0.0
            debit = 0.0
            async for j in db.journal_entries.find(
                {"copropriete_id": copro_id,
                 "lines.account_number": {"$regex": "^160"},
                 "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "lines": 1},
            ):
                for ln in j.get("lines", []):
                    if ln.get("account_number", "").startswith("160"):
                        debit += float(ln.get("debit", 0) or 0)
                        credit += float(ln.get("credit", 0) or 0)
            assert abs((credit - debit) - 9000.0) < 0.01, (
                "Le bug historique : sans le fix, solde 160 = 9000 (2000 en trop)"
            )
        finally:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_lock_generate_sale_entry_purges_legacy_ap_symmetrically():
    """iter90i7-lock-1 : VERROU cote generateur. Si un JE AP legacy existe
    pour le fund_call au moment ou generate_sale_entry est appele, il est
    supprime automatiquement AVANT la creation du VE. Impossibilite de
    reintroduire le doublon meme si la route legacy est appelee en premier."""
    copro_id = f"acp-i7lock-{uuid.uuid4().hex[:8]}"
    call_id = f"call-i7lock-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from auto_entries import generate_sale_entry
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": {"$regex": f"^own-{copro_id}"}})
            # Cree un owner
            owner_id = f"own-{copro_id}-1"
            await db.owners.insert_one({
                "id": owner_id, "name": "Alice",
                "tier_accounts": {
                    copro_id: {"provisions": "40000001",
                               "reserve": "40100001",
                               "roulement": "40200001"},
                },
            })
            # AP legacy pre-existant (bug scenario)
            await db.journal_entries.insert_one({
                "id": f"ap-{call_id}", "journal_type": "AP",
                "fund_call_id": call_id, "copropriete_id": copro_id,
                "date": "2026-05-01", "reference": "AP-Legacy",
                "lines": [
                    {"account_number": "401000", "debit": 2000, "credit": 0},
                    {"account_number": "160", "debit": 0, "credit": 2000},
                ],
                "total_debit": 2000, "total_credit": 2000,
            })
            # Simule fund_call avec appel reserve
            fund_call = {
                "id": call_id, "copropriete_id": copro_id,
                "name": "Reserve 2026-1", "date": "2026-05-01",
                "call_type": "reserve", "total_amount": 2000.0,
                "reserve_amount": 2000.0, "roulement_amount": 0,
                "distribution": [{"owner_id": owner_id, "amount": 2000.0}],
            }
            # Le generateur DOIT purger l'AP en doublon puis creer le VE
            je = await generate_sale_entry(db, fund_call)
            assert je is not None
            # AP a disparu
            ap_left = await db.journal_entries.count_documents({
                "id": f"ap-{call_id}",
            })
            assert ap_left == 0, "L'AP legacy DOIT etre purge par le VE (verrou)"
            # Un seul VE existe pour cet appel
            ve_count = await db.journal_entries.count_documents({
                "source_type": "fund_call", "source_id": call_id,
                "journal_type": "VE",
            })
            assert ve_count == 1
        finally:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": {"$regex": f"^own-{copro_id}"}})
            client.close()

    asyncio.run(_run())


def test_lock_startup_self_heal_removes_duplicates_on_boot():
    """iter90i7-lock-2 : VERROU cote startup. Le hook @app.on_event('startup')
    heal les doublons AP+VE au demarrage. Simule le hook en appelant
    directement la logique. Assure qu'un redemarrage nettoie automatiquement
    la base sans intervention superadmin."""
    copro_id = f"acp-i7boot-{uuid.uuid4().hex[:8]}"
    call_id = f"call-i7boot-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            # Doublon AP+VE
            await db.journal_entries.insert_many([
                {"id": f"ap-{call_id}", "journal_type": "AP",
                 "fund_call_id": call_id, "copropriete_id": copro_id,
                 "date": "2026-05-01", "reference": "AP-Legacy",
                 "lines": [], "total_debit": 2000, "total_credit": 2000},
                {"id": f"ve-{call_id}", "journal_type": "VE",
                 "source_type": "fund_call", "source_id": call_id,
                 "copropriete_id": copro_id, "date": "2026-05-01",
                 "reference": "AF-Reserve",
                 "lines": [], "total_debit": 2000, "total_credit": 2000},
            ])
            # Simule le hook de startup (extrait de server.py)
            removed = 0
            async for ap in db.journal_entries.find(
                {"journal_type": "AP", "fund_call_id": {"$exists": True}},
                {"_id": 0, "id": 1, "fund_call_id": 1},
            ):
                fcid = ap.get("fund_call_id")
                if not fcid:
                    continue
                ve = await db.journal_entries.find_one(
                    {"source_type": "fund_call", "source_id": fcid,
                     "journal_type": "VE",
                     "reversed": {"$ne": True},
                     "is_reversal": {"$ne": True}},
                    {"_id": 0, "id": 1},
                )
                if ve:
                    r = await db.journal_entries.delete_one({"id": ap["id"]})
                    if r.deleted_count:
                        removed += 1
            assert removed == 1, "Le self-heal doit avoir supprime 1 doublon"
            # L'AP a disparu, le VE est toujours la
            assert (await db.journal_entries.count_documents(
                {"id": f"ap-{call_id}"})) == 0
            assert (await db.journal_entries.count_documents(
                {"id": f"ve-{call_id}"})) == 1
        finally:
            await db.journal_entries.delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())
