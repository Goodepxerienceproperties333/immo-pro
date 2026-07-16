"""iter90gj : verrou PCMN sur commit-invoices - le compte comptable est
obligatoire (heritage iter90g9). Le message d'erreur est explicite pour
les factures issues du PDF tabulaire "Factures fournisseurs" (pas de
compte dans le PDF).

**Ticket utilisateur** : "erreur lors de l'import des factures" (le PDF
tabulaire ne fournit pas les comptes -> toutes les factures rejetees).

Le fix cote frontend :
- Datalist PCMN complet dans la colonne "Cpte" de l'InvoicesPreview.
- Bandeau + bouton "Appliquer un compte a toutes les factures sans compte"
- Blocage du commit tant que des comptes manquent.

Cote backend : le commit renvoie les erreurs par ligne pour permettre
au syndic de corriger dans la preview.
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
    cid = f"iter90gj-inv-{suffix}"
    sid = f"session-inv-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-inv-{suffix}"})
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active",
        "steps": {"fiscal_year": {"fiscal_year_id": f"fy-{suffix}"}},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return {"cid": cid, "sid": sid}


async def _cleanup(db, cid, sid):
    await db.coproprietes.delete_one({"id": cid})
    await db.import_sessions.delete_one({"id": sid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"import_session_id": sid})
    await db.journal_entries.delete_many({"import_session_id": sid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})


async def test_commit_rejects_invoices_without_account():
    """Une facture sans account_number est rejetee avec un message clair."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-invoices",
                json={"invoices": [
                    {"date": "2026-06-14", "supplier_aux_code": "F0001",
                     "supplier_name": "Finlead", "external_ref": "V-260654",
                     "internal_ref_optipro": "0005",
                     "account_number": "",  # MANQUANT
                     "libelle": "2T2026", "montant_ht": 678.99, "montant_tvac": 678.99},
                ]},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inserted"] == 0
            assert len(body["errors"]) == 1
            assert "compte comptable" in body["errors"][0]["error"].lower()
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"])


async def test_commit_accepts_invoices_with_valid_account():
    """Une facture avec account_number valide est inseree correctement."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    ctx = await _setup(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=20) as client:
            await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{ctx['sid']}/commit-invoices",
                json={"invoices": [
                    {"date": "2026-06-14", "supplier_aux_code": "F0001",
                     "supplier_name": "Finlead", "external_ref": "V-260654",
                     "internal_ref_optipro": "0005",
                     "account_number": "61000", "account_label": "Charges",
                     "libelle": "2T2026", "montant_ht": 678.99, "montant_tvac": 678.99},
                ]},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inserted"] == 1
            assert len(body["errors"]) == 0
    finally:
        await _cleanup(db, ctx["cid"], ctx["sid"])


if __name__ == "__main__":
    asyncio.run(test_commit_rejects_invoices_without_account())
    print("OK test_commit_rejects_invoices_without_account")
    asyncio.run(test_commit_accepts_invoices_with_valid_account())
    print("OK test_commit_accepts_invoices_with_valid_account")
