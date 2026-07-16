"""iter90gj : regroupement des lignes de detail multi-ligne + detection
automatique des frais privatifs (comptes 643xxx) lors de l'import Optipro.

**Ticket utilisateur** :
> "attention lors de l'import des facture tu dedoubles les factures ayant
> le meme nr de facture."
> "en cas de frais privatifs ajouter une invite pour pouvoir selectionner
> le ou les proprietaire(s) concerne(s)"

**Contexte** : Optipro exporte 1 ligne CSV/PDF par ligne comptable. Une
facture avec 2 comptes (61300 + 6160) genere 2 lignes ayant la meme
ref. interne (0038). Sans regroupement, on cree 2 factures avec le meme
n° externe -> doublon apparent pour le syndic.

**Fix Phase 1** : regroupement automatique dans `commit-invoices` par
`internal_ref_optipro` (fallback : ext_ref + supplier + date).
**Fix Phase 2** : `is_private_fee=True` auto-detecte pour tout compte
commencant par "643". `private_fee_owner_id` reste vide -> a assigner
via la modale post-mutations (Phase 3, a implementer).
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


async def _setup_acp(db, suffix: str) -> dict:
    """Cree ACP + FY + supplier + nature + import session."""
    cid = f"iter90gj-{suffix}"
    sid = f"session-gj-{suffix}"
    await db.coproprietes.insert_one({
        "id": cid, "name": f"iter90gj-{suffix}", "reference": f"REF-{suffix}",
    })
    await db.fiscal_years.insert_one({
        "id": f"fy-{suffix}", "copropriete_id": cid, "name": f"2026-{suffix}",
        "start_date": "2026-01-01", "end_date": "2026-12-31", "status": "open",
    })
    # supplier F0038 (Finlead)
    await db.suppliers.insert_one({
        "id": f"sup-{suffix}", "copropriete_id": cid, "name": "SRL Finlead",
        "auxiliary_code": "F0038",
    })
    # nature liee au compte 61300 (fictive)
    await db.expense_categories.insert_one({
        "id": f"cat-{suffix}", "copropriete_id": cid, "name": "Honoraires",
        "code": "6100", "account_number": "61300",
    })
    # nature liee au compte 643 (privatif)
    await db.expense_categories.insert_one({
        "id": f"cat643-{suffix}", "copropriete_id": cid,
        "name": "Frais privatifs", "code": "6430", "account_number": "643",
    })
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active", "steps": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return {"cid": cid, "sid": sid}


async def _cleanup(db, cid: str, sid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.import_sessions.delete_one({"id": sid})
    await db.invoices.delete_many({"import_session_id": sid})
    await db.journal_entries.delete_many({"import_session_id": sid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})


async def test_multi_line_invoice_grouped_by_internal_ref():
    """Une facture Finlead 0038 avec 2 lignes de detail (61300 + 6160)
    doit produire UNE SEULE facture avec `distribution_lines` de 2 entrees.
    """
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    setup = await _setup_acp(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=30) as client:
            await _login(client)
            payload = {"invoices": [
                {  # ligne 1 : compte 61300 - honoraires syndic
                    "date": "2026-06-14", "supplier_aux_code": "F0038",
                    "supplier_name": "SRL Finlead", "external_ref": "V-260701",
                    "internal_ref_optipro": "0038", "account_number": "61300",
                    "account_label": "Honoraires syndic",
                    "montant_ht": 900.00, "montant_tvac": 900.00,
                    "libelle": "Honoraires juin 2026",
                },
                {  # ligne 2 : compte 6160 - frais administration (meme ref 0038)
                    "date": "2026-06-14", "supplier_aux_code": "F0038",
                    "supplier_name": "SRL Finlead", "external_ref": "V-260701",
                    "internal_ref_optipro": "0038", "account_number": "6160",
                    "account_label": "Frais admin",
                    "montant_ht": 169.29, "montant_tvac": 169.29,
                    "libelle": "Frais admin juin 2026",
                },
            ]}
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{setup['sid']}/commit-invoices",
                json=payload,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inserted"] == 1, f"expected 1 invoice, got {body['inserted']}"
            assert body["grouped"] == 1, f"expected 1 grouped line, got {body['grouped']}"

            # Verifie en base : 1 facture, distribution_lines a 2 entrees, total = 1069.29
            invoices = await db.invoices.find({"import_session_id": setup['sid']}, {"_id": 0}).to_list(10)
            assert len(invoices) == 1, f"expected 1 db doc, got {len(invoices)}"
            inv = invoices[0]
            assert inv["number"] == "V-260701"
            assert abs(inv["total_amount"] - 1069.29) < 0.01, f"total={inv['total_amount']}"
            assert len(inv["distribution_lines"]) == 2, f"lines={inv['distribution_lines']}"
            accs = sorted(l["account_number"] for l in inv["distribution_lines"])
            assert accs == ["61300", "6160"], f"accs={accs}"
    finally:
        await _cleanup(db, setup["cid"], setup["sid"])


async def test_private_fee_643_auto_detected():
    """Une facture avec compte 643xxx doit avoir `is_private_fee=True`."""
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    setup = await _setup_acp(db, suffix)
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=30) as client:
            await _login(client)
            payload = {"invoices": [
                {  # frais privatif
                    "date": "2026-05-10", "supplier_aux_code": "F0038",
                    "supplier_name": "SRL Finlead", "external_ref": "V-260500",
                    "internal_ref_optipro": "0100", "account_number": "643001",
                    "account_label": "Frais privatifs copro",
                    "montant_ht": 100.00, "montant_tvac": 121.00,
                    "libelle": "Frais privatif appt 001",
                },
                {  # facture standard (compte 61300)
                    "date": "2026-05-11", "supplier_aux_code": "F0038",
                    "supplier_name": "SRL Finlead", "external_ref": "V-260501",
                    "internal_ref_optipro": "0101", "account_number": "61300",
                    "account_label": "Honoraires",
                    "montant_ht": 200.00, "montant_tvac": 242.00,
                    "libelle": "Honoraires",
                },
            ]}
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{setup['sid']}/commit-invoices",
                json=payload,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["inserted"] == 2
            assert body["private_fees_detected"] == 1, \
                f"expected 1 privatif, got {body['private_fees_detected']}"

            invs = await db.invoices.find({"import_session_id": setup['sid']}, {"_id": 0}).to_list(10)
            by_num = {i["number"]: i for i in invs}
            assert by_num["V-260500"]["is_private_fee"] is True
            assert by_num["V-260500"]["private_fee_owner_id"] == ""
            assert by_num["V-260500"]["private_fee_allocations"] == []
            assert by_num["V-260501"]["is_private_fee"] is False
    finally:
        await _cleanup(db, setup["cid"], setup["sid"])


if __name__ == "__main__":
    asyncio.run(test_multi_line_invoice_grouped_by_internal_ref())
    print("OK test_multi_line_invoice_grouped_by_internal_ref")
    asyncio.run(test_private_fee_643_auto_detected())
    print("OK test_private_fee_643_auto_detected")
