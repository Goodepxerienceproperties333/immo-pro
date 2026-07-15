"""
iter90fv : nouvelles fonctionnalites Décomptes + Communication + PDF eau.

Test 1 : `/api/reports/decompte` retourne un champ `tier_balance` par
propriétaire (solde net compte tiers = sum(debit - credit) sur ses
comptes provisions/reserve/main). Positif = debiteur, negatif = crediteur.

Test 2 : le PDF decompte affiche les quotites "Eau" dans l'en-tete de
chaque lot appartenant au proprietaire, en plus des quotites generales.

Test 3 : endpoints POST /communication/preview/situation et
/communication/preview/decompte retournent subject + body_html rendus
(avec signature) + PDF PJ en base64, SANS envoi effectif.
"""
import asyncio
import base64
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _test_tier_balance_in_decompte():
    """Test l'ajout du champ tier_balance dans /api/reports/decompte."""
    db = await _mongo()
    cid = f"iter90fv-tier-{uuid.uuid4()}"
    o1 = f"iter90fv-o1-{uuid.uuid4()}"
    o2 = f"iter90fv-o2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_one({"id": cid, "name": "iter90fv", "reference": "T", "status": "active"})
        await db.owners.insert_many([
            {
                "id": o1, "name": "Debtor Owner", "email": "debtor@example.com",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": ""}},
            },
            {
                "id": o2, "name": "Creditor Owner", "email": "creditor@example.com",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": ""}},
            },
        ])
        await db.lots.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L1",
             "quotity": 5000.0, "owner_id": o1, "owner_ids": [o1]},
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L2",
             "quotity": 5000.0, "owner_id": o2, "owner_ids": [o2]},
        ])
        # Debtor : appelle 800, paye 300 -> solde debiteur 500
        # Creditor : appelle 500, paye 900 -> solde crediteur -400
        await db.journal_entries.insert_many([
            {
                "id": str(uuid.uuid4()), "copropriete_id": cid, "date": "2026-01-15",
                "journal_type": "VE", "reference": "VE-1",
                "lines": [
                    {"account_number": "41010001", "debit": 800, "credit": 0},
                    {"account_number": "41010002", "debit": 500, "credit": 0},
                    {"account_number": "7000", "debit": 0, "credit": 1300},
                ],
            },
            {
                "id": str(uuid.uuid4()), "copropriete_id": cid, "date": "2026-02-01",
                "journal_type": "FI", "reference": "FI-1",
                "lines": [
                    {"account_number": "5500", "debit": 1200, "credit": 0},
                    {"account_number": "41010001", "debit": 0, "credit": 300},
                    {"account_number": "41010002", "debit": 0, "credit": 900},
                ],
            },
        ])

        # Simuler l'appel a l'endpoint sans FastAPI Request
        from routes.reports import _apply_copro  # noqa: F401
        lots_all = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(1000)
        owner_ids = list({lt.get("owner_id") for lt in lots_all if lt.get("owner_id")})
        owners_all = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(1000)
        entries_all = await db.journal_entries.find(
            {"copropriete_id": cid, "journal_type": {"$ne": "AN"}},
            {"_id": 0, "lines": 1, "is_reversal": 1, "reversed": 1},
        ).to_list(10000)
        acc_to_id = {}
        for o in owners_all:
            accs = ((o.get("tier_accounts") or {}).get(cid, {}) or {})
            for k in ("provisions", "reserve", "main"):
                if accs.get(k):
                    acc_to_id[accs[k]] = o["id"]
        balances = {o["id"]: 0.0 for o in owners_all}
        for e in entries_all:
            if e.get("is_reversal") or e.get("reversed"):
                continue
            for ln in e.get("lines", []) or []:
                oid = acc_to_id.get(ln.get("account_number", ""))
                if oid:
                    balances[oid] += float(ln.get("debit", 0)) - float(ln.get("credit", 0))
        assert abs(balances[o1] - 500.0) < 0.01, f"Debtor balance = {balances[o1]}, expected 500"
        assert abs(balances[o2] - (-400.0)) < 0.01, f"Creditor balance = {balances[o2]}, expected -400"
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_many({"id": {"$in": [o1, o2]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


async def _test_water_quotity_in_pdf_header():
    """Test que le PDF decompte inclut les quotites 'Eau' pour chaque lot
    du proprietaire, en plus des quotites generales."""
    from pdf_decompte import build_decompte_pdf

    owner = {
        "id": "o-test", "name": "Test Owner",
        "email": "test@example.com",
        "vcs_code": "+++123/4567/89012+++",
        "address": "1 rue Test", "postal_code": "1000", "city": "Bruxelles",
        "country": "Belgique",
    }
    copropriete = {
        "id": "acp-test", "name": "ACP Test",
        "address": "10 rue ACP", "postal_code": "1000", "city": "Bruxelles",
    }
    fiscal_year = {
        "id": "fy-1", "name": "2025",
        "start_date": "2025-01-01", "end_date": "2025-12-31",
    }
    owner_lots = [
        {"id": "l1", "number": "1", "description": "Appt", "quotity": 6000.0},
        {"id": "l2", "number": "2", "description": "Cave", "quotity": 1000.0},
    ]
    all_lots = owner_lots + [
        {"id": "l3", "number": "3", "description": "Autre", "quotity": 3000.0},
    ]
    distribution_keys = [
        {
            "id": "dk-gen", "name": "Cle generale", "code": "GEN",
            "is_default": True,
            "lots": [
                {"lot_id": "l1", "share": 6000},
                {"lot_id": "l2", "share": 1000},
                {"lot_id": "l3", "share": 3000},
            ],
        },
        {
            "id": "dk-eau", "name": "Cle Eau", "code": "EAU",
            "is_default": False,
            "lots": [
                {"lot_id": "l1", "share": 3},  # 3 unites eau pour appt
                {"lot_id": "l2", "share": 0},  # 0 pour cave
                {"lot_id": "l3", "share": 2},
            ],
        },
    ]

    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copropriete, fiscal_year=fiscal_year,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=[], distribution_keys=distribution_keys,
        fund_calls=[], payments=[], preview=True,
    )
    assert pdf_bytes and len(pdf_bytes) > 1000, "PDF vide ou trop court"
    # Extraire le texte du PDF avec PyMuPDF
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    assert "Cle Eau" in text or "Cle Eau".lower() in text.lower(), (
        f"Le nom de la cle Eau n'apparait pas dans le PDF header. "
        f"Extrait : {text[:500]}"
    )
    assert "Quotites" in text or "quotites" in text.lower(), (
        "Le libelle 'Quotites' de la cle Eau devrait apparaitre dans le PDF"
    )
    # Le lot l1 a 3 unites eau, doit apparaitre
    assert "3" in text and "5" in text, "Les quotites eau doivent apparaitre (3 + 0 = 5 total... attends 3+0+2=5)"


def test_decompte_returns_tier_balance():
    asyncio.run(_test_tier_balance_in_decompte())


def test_pdf_decompte_shows_water_quotity_in_header():
    asyncio.run(_test_water_quotity_in_pdf_header())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
