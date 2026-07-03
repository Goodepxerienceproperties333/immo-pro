"""
Iter90ak : Balance des tiers affiche les comptes tiers orphelins (sans owner rattache)

Bug initial (PROD Acacia) :
- Un ancien proprietaire (ex. Matexi) a un solde non lettre sur son compte tier
  (4100017 = 850 EUR debit) apres avoir ete supprime de la collection owners,
  ou son tier_accounts n'est plus mappe sur ce compte dans cette ACP.
- La Sante comptable detecte "1 compte(s) tier orphelin(s)" mais la Balance
  des tiers n'affiche AUCUNE ligne pour ce compte, invisibilite totale.

Fix (iter90ak) :
- Apres la boucle principale d'agregation par owner, iterer sur `cumul_per_acc`
  (residual accounts non rattaches a aucun owner).
- Pour chaque compte 4100xxx/4000xxx/4001xxx avec solde non nul, ajouter une
  ligne synthetique avec :
    - owner_id = "" (empty)
    - owner_name = nom du compte PCMN OU "Ancien proprietaire (compte XXX)"
    - is_orphan_account = True
    - is_former_owner = True
    - balance calcule normalement
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


async def _admin_client():
    c = httpx.AsyncClient(timeout=30, base_url=BACKEND_URL)
    r = await c.post("/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    r.raise_for_status()
    return c


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_orphan_account():
    """Simule un ancien proprietaire dont le compte tier a un solde debiteur
    mais qui n'est plus dans owners (ou dont tier_accounts est vide pour l'ACP)."""
    db = await _mongo()
    cid = f"iter90ak-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    current_oid = f"o-current-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia-orphan", "reference": "ACA-o", "status": "active"})
    await db.pcmn_accounts.insert_many([
        {"number": "400001", "name": "Compte prov current", "class_num": 4, "copropriete_id": cid},
        {"number": "400002", "name": "Compte prov Matexi (orphelin)", "class_num": 4, "copropriete_id": cid},
        {"number": "410001", "name": "Reserve current", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_one({
        "id": current_oid, "name": "TEUWEN Gael", "auxiliary_code": "C0002",
        "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "400001", "reserve": "410001"}},
    })
    await db.lots.insert_one({
        "id": lot_id, "number": "001", "owner_id": current_oid, "owner_ids": [current_oid],
        "copropriete_id": cid, "quotity": 1000.0,
    })

    # Ecriture VE ancienne avec un compte 400002 (ex-Matexi) NON rattache a un owner.
    # Note : pas de third_party_id ici pour simuler un cas legacy.
    await db.journal_entries.insert_one({
        "id": f"je-{uuid.uuid4()}", "copropriete_id": cid,
        "date": "2025-10-01", "journal_type": "VE",
        "reference": "VE-ORPH-001", "description": "Legacy VE Matexi",
        "total_debit": 850.0, "total_credit": 850.0,
        "lines": [
            {"account_number": "400002", "account_name": "Compte prov Matexi (orphelin)",
             "debit": 850.0, "credit": 0.0},
            {"account_number": "700000", "account_name": "Prov",
             "debit": 0.0, "credit": 850.0},
        ],
    })
    # Ecriture VE normale pour le current owner TEUWEN
    await db.journal_entries.insert_one({
        "id": f"je-{uuid.uuid4()}", "copropriete_id": cid,
        "date": "2025-11-20", "journal_type": "VE",
        "reference": "VE-TEUWEN-001", "description": "VE current owner",
        "total_debit": 1200.0, "total_credit": 1200.0,
        "lines": [
            {"account_number": "400001", "account_name": "Compte prov current",
             "debit": 1200.0, "credit": 0.0, "third_party_id": current_oid},
            {"account_number": "700000", "account_name": "Prov",
             "debit": 0.0, "credit": 1200.0},
        ],
    })
    return {"db": db, "cid": cid, "current_oid": current_oid, "lot": lot_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("pcmn_accounts", "lots", "journal_entries",
                 "distribution_keys", "mutations", "fund_calls",
                 "budgets", "bank_transactions"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["current_oid"]})


async def _run_orphan_visible():
    ctx = await _setup_orphan_account()
    try:
        c = await _admin_client()
        try:
            r = await c.get(f"/api/reports/balance-tiers/owners?copropriete_id={ctx['cid']}")
            assert r.status_code == 200, r.text
            data = r.json()
            owners = data.get("owners", [])
            # Doit contenir current owner TEUWEN (debiteur 1200)
            current = [o for o in owners if o.get("owner_id") == ctx["current_oid"]]
            assert len(current) == 1, f"Current owner absent : {owners}"
            assert abs(current[0]["balance"] - 1200.0) < 0.01

            # Doit AUSSI contenir la ligne orpheline pour compte 400002 (850 EUR)
            orphans = [o for o in owners if o.get("is_orphan_account")]
            assert len(orphans) == 1, (
                f"Orphelin non affiche : {[(o.get('owner_name'), o.get('balance')) for o in owners]}"
            )
            orph = orphans[0]
            assert orph["owner_id"] == ""
            assert orph["account_provisions"] == "400002"
            assert abs(orph["balance"] - 850.0) < 0.01
            assert orph["status"] == "debiteur"
            assert orph["is_former_owner"] is True
            # Nom du compte issu de PCMN
            assert "Matexi" in orph["owner_name"] or "orphelin" in orph["owner_name"].lower()

            # Total debiteurs = 1200 (TEUWEN) + 850 (orphelin) = 2050
            assert abs(data["total_debiteurs"] - 2050.0) < 0.01, (
                f"total_debiteurs={data['total_debiteurs']}, attendu 2050"
            )
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


async def _run_no_orphan_regression():
    """Aucun compte orphelin -> comportement inchange, pas de ligne synthetique."""
    ctx = await _setup_orphan_account()
    try:
        # Retirer l'ecriture orpheline
        await ctx["db"].journal_entries.delete_many({
            "copropriete_id": ctx["cid"],
            "reference": "VE-ORPH-001",
        })
        c = await _admin_client()
        try:
            r = await c.get(f"/api/reports/balance-tiers/owners?copropriete_id={ctx['cid']}")
            assert r.status_code == 200
            data = r.json()
            orphans = [o for o in data["owners"] if o.get("is_orphan_account")]
            assert orphans == [], f"Aucun orphelin attendu, obtenu {orphans}"
        finally:
            await c.aclose()
    finally:
        await _cleanup(ctx)


def test_orphan_account_visible_in_balance_tiers():
    asyncio.run(_run_orphan_visible())


def test_no_orphan_regression():
    asyncio.run(_run_no_orphan_regression())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
