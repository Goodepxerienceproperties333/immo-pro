"""iter90gj : anti-doublon sur POST /api/accounting/entries.

**Ticket utilisateur** : "toutes les ecritures d'achat sont doublees"

**Cause** : le syndic a manuellement recreé les 5 factures via la modale
"Nouvelle ecriture" ALORS qu'elles avaient deja ete creees par le wizard
d'import (`commit-invoices`). Chaque manipulation (reversal + recreation)
a doublé les entrees -> 22 ecritures AC pour 6 factures reelles.

**Fix** : rejette (409) toute creation manuelle avec (copropriete_id,
journal_type, reference, date) matchant une ecriture existante non-extournee.
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
    cid = f"iter90gj-dup-{suffix}"
    fy_id = f"fy-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-dup-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    # Comptes minimum requis pour l'ecriture
    await db.pcmn_accounts.insert_many([
        {"id": f"pcmn-1-{suffix}", "copropriete_id": cid, "number": "61000", "name": "Charges"},
        {"id": f"pcmn-2-{suffix}", "copropriete_id": cid, "number": "44000001", "name": "Fournisseur test"},
    ])
    return {"cid": cid, "fy_id": fy_id}


async def _cleanup(db, cid, fy_id):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_one({"id": fy_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


async def test_duplicate_entry_rejected():
    """Une 2e ecriture avec meme (copropriete, journal, ref, date) est rejetee."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            payload = {
                "copropriete_id": ctx["cid"],
                "journal_type": "AC", "date": "2026-06-15",
                "reference": f"FA-DUP-{suffix}",
                "description": "Facture test",
                "lines": [
                    {"account_number": "61000", "debit": 100, "credit": 0},
                    {"account_number": "44000001", "debit": 0, "credit": 100},
                ],
            }
            # 1re : OK
            r1 = await client.post(f"{BACKEND_URL}/api/accounting/entries", json=payload)
            assert r1.status_code == 200, r1.text

            # 2e : 409 doublon
            r2 = await client.post(f"{BACKEND_URL}/api/accounting/entries", json=payload)
            assert r2.status_code == 409, f"expected 409, got {r2.status_code}: {r2.text}"
            assert "doublon" in r2.json()["detail"].lower()
    finally:
        await _cleanup(db, ctx["cid"], ctx["fy_id"])


async def test_different_date_allowed():
    """Meme reference mais date differente = pas de doublon."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            base = {
                "copropriete_id": ctx["cid"],
                "journal_type": "AC",
                "reference": f"FA-REF-{suffix}",
                "description": "Facture",
                "lines": [
                    {"account_number": "61000", "debit": 100, "credit": 0},
                    {"account_number": "44000001", "debit": 0, "credit": 100},
                ],
            }
            r1 = await client.post(f"{BACKEND_URL}/api/accounting/entries",
                                     json={**base, "date": "2026-06-15"})
            assert r1.status_code == 200
            r2 = await client.post(f"{BACKEND_URL}/api/accounting/entries",
                                     json={**base, "date": "2026-06-16"})  # date differente
            assert r2.status_code == 200
    finally:
        await _cleanup(db, ctx["cid"], ctx["fy_id"])


async def test_reversal_entries_ignored_in_dup_check():
    """Les ecritures marquees `is_reversal=True` ne bloquent pas la creation
    d'une nouvelle ecriture avec la meme reference (cas EXT-XXX -> XXX).
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    ref = f"FA-REV-{suffix}"
    # Seed : une ecriture existante marquee comme extourne
    await db.journal_entries.insert_one({
        "id": f"rev-{suffix}", "copropriete_id": ctx["cid"],
        "journal_type": "AC", "date": "2026-06-15",
        "reference": ref, "is_reversal": True,
        "lines": [], "total_debit": 100, "total_credit": 100,
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(f"{BACKEND_URL}/api/accounting/entries", json={
                "copropriete_id": ctx["cid"],
                "journal_type": "AC", "date": "2026-06-15",
                "reference": ref, "description": "Nouvelle",
                "lines": [
                    {"account_number": "61000", "debit": 100, "credit": 0},
                    {"account_number": "44000001", "debit": 0, "credit": 100},
                ],
            })
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(db, ctx["cid"], ctx["fy_id"])


if __name__ == "__main__":
    asyncio.run(test_duplicate_entry_rejected())
    print("OK test_duplicate_entry_rejected")
    asyncio.run(test_different_date_allowed())
    print("OK test_different_date_allowed")
    asyncio.run(test_reversal_entries_ignored_in_dup_check())
    print("OK test_reversal_entries_ignored_in_dup_check")
