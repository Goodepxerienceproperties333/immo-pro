"""SEC-audit hotfix (2026-02) : coherence balance tiers vs detail.

Bug client LEFRANCQ : BELKHIR affichait -334.33 EUR crediteur dans la
balance de tiers, mais la situation de compte (detail) montrait +100 EUR
debiteur au meme 31/12/2025.

Cause : le detail calculait `real_balance` uniquement sur les ecritures
DE LA PERIODE (variation nette), tandis que la liste utilise le SOLDE
CUMULATIF (avec report du solde d'ouverture).

Test :
- Creer une ACP + owner
- 2 tier_accounts (provisions 40000010, reserve 40010010)
- Une ecriture AN d'ouverture (is_opening_balance=True) 2025-01-01 :
  BELKHIR debit 200 EUR (report du solde d'ouverture au 31/12/2024)
- Une VE (appel 2025) : debit 1200 EUR
- Une FI (paiement 2025) : credit 1534.33 EUR
- Expected : balance (liste) == balance (detail) == 200 + 1200 - 1534.33
  = -134.33 (crediteur 134.33)
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@copro.be")
ADMIN_PWD = os.environ["ADMIN_PASSWORD"]


async def _seed(db):
    tag = uuid.uuid4().hex[:8]
    copro_id = f"test-balcoherence-{tag}"
    owner_id = f"owner-{tag}"
    lot_id = f"lot-{tag}"
    acc_prov = "40000010"
    acc_res = "40010010"

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "TEST balance coherence",
        "bank_accounts": [{"iban": "BE12", "pcmn_number": "55000000",
                           "account_type": "vue", "label": "Bank"}],
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "BELKHIR Abdellah",
        "copropriete_id": copro_id, "vcs_code": "+++527/1399/18952+++",
        "vcs_digits": "527139918952",
        "tier_accounts": {copro_id: {"provisions": acc_prov, "reserve": acc_res}},
    })
    await db.lots.insert_one({
        "id": lot_id, "copropriete_id": copro_id, "owner_id": owner_id,
        "number": "1", "type": "appartement",
    })
    await db.pcmn_accounts.insert_many([
        {"copropriete_id": copro_id, "number": acc_prov, "name": "Provisions BELKHIR", "active": True},
        {"copropriete_id": copro_id, "number": acc_res, "name": "Reserve BELKHIR", "active": True},
    ])

    # AN d'ouverture 2025-01-01 : BELKHIR doit 200 EUR
    await db.journal_entries.insert_one({
        "id": f"an-{tag}", "copropriete_id": copro_id,
        "date": "2025-01-01", "journal_type": "AN",
        "reference": "AN-2025-OPEN", "is_opening_balance": True,
        "description": "Report solde d'ouverture 2025",
        "lines": [
            {"account_number": acc_prov, "third_party_id": owner_id,
             "debit": 200.0, "credit": 0.0, "line_description": "Report solde 31/12/2024"},
        ],
    })
    # VE : appel 1200 le 01/07
    await db.journal_entries.insert_one({
        "id": f"ve-{tag}", "copropriete_id": copro_id,
        "date": "2025-07-01", "journal_type": "VE",
        "reference": "VE-2025-01",
        "description": "Appel de provisions",
        "lines": [
            {"account_number": acc_prov, "third_party_id": owner_id,
             "debit": 1200.0, "credit": 0.0, "line_description": "Appel provisions"},
            {"account_number": "70000000", "debit": 0.0, "credit": 1200.0},
        ],
    })
    # FI : paiement 1534.33 le 15/10
    await db.journal_entries.insert_one({
        "id": f"fi-{tag}", "copropriete_id": copro_id,
        "date": "2025-10-15", "journal_type": "FI",
        "reference": "FI-2025-01",
        "description": "Paiement recu BELKHIR",
        "lines": [
            {"account_number": "55000000", "debit": 1534.33, "credit": 0.0},
            {"account_number": acc_prov, "third_party_id": owner_id,
             "debit": 0.0, "credit": 1534.33, "line_description": "Paiement recu"},
        ],
    })
    return copro_id, owner_id


async def _cleanup(db, copro_id):
    await db.coproprietes.delete_one({"id": copro_id})
    await db.owners.delete_many({"copropriete_id": copro_id})
    await db.lots.delete_many({"copropriete_id": copro_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
    await db.journal_entries.delete_many({"copropriete_id": copro_id})


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    copro_id, owner_id = await _seed(db)
    try:
        async with httpx.AsyncClient(base_url=API, timeout=15.0) as h:
            # Login superadmin
            r = await h.post("/api/auth/login",
                             json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
            assert r.status_code == 200, r.text
            token = r.cookies.get("access_token")
            hdr = {"Authorization": f"Bearer {token}", "X-Copropriete-Id": copro_id}

            params = {"copropriete_id": copro_id,
                      "start_date": "2025-01-01", "end_date": "2025-12-31"}

            # LIST endpoint
            r = await h.get("/api/reports/balance-tiers/owners",
                            params=params, headers=hdr)
            assert r.status_code == 200, r.text
            owners = r.json()["owners"]
            b = next((o for o in owners if o["owner_id"] == owner_id), None)
            assert b, f"BELKHIR not found: {owners}"
            list_balance = b["balance"]
            print(f"LIST balance: {list_balance}")

            # DETAIL endpoint
            r = await h.get(f"/api/reports/balance-tiers/owners/{owner_id}",
                            params=params, headers=hdr)
            assert r.status_code == 200, r.text
            data = r.json()
            detail_balance = data["balance"]
            print(f"DETAIL balance: {detail_balance}")
            print(f"DETAIL movements: {len(data['movements'])}")
            for m in data["movements"]:
                print(f"  {m['date']} {m['description'][:50]} D={m['debit']} C={m['credit']} run={m.get('running_balance')}")

            # Attendu : 200 + 1200 - 1534.33 = -134.33
            expected = round(200 + 1200 - 1534.33, 2)
            assert abs(list_balance - expected) < 0.01, f"List: expected {expected}, got {list_balance}"
            assert abs(detail_balance - expected) < 0.01, f"Detail: expected {expected}, got {detail_balance}"
            assert abs(list_balance - detail_balance) < 0.01, "LIST vs DETAIL diverge"
            print(f"\nPASS : list={list_balance} == detail={detail_balance} == expected={expected}")

    finally:
        await _cleanup(db, copro_id)
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
