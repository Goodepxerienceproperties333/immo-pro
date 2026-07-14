"""
iter90fo : REGRESSION LOCK - Fix du CRASH P0 Cloudflare 502 sur
GET /api/dashboard/health-audit (`compute_health_audit`).

Bug rapporte utilisateur (Feb 2026) :
> "erreur critique plus d'accès à production The origin web server sent a
> response that Cloudflare could not parse... Sante comptable indisponible...
> cette situation disparait après quelques minutes mais à un énorme impact
> sur mes clients"

Root cause identifiee :
1. `db.owners.find({})` et `db.suppliers.find({})` SANS filtre copropriete_id
   -> full scan de TOUTE la base multi-tenant (tous les clients) a CHAQUE
   appel du dashboard.
2. Boucle imbriquee O(lignes_ecritures * nb_owners) pour calculer le solde
   de chaque proprietaire -> avec des annees d'historique + beaucoup de
   lots, prend plusieurs MINUTES et, en mode uvicorn --workers 1, GELE
   TOUTE l'application (single event loop bloque) -> timeout Cloudflare 502
   qui impacte TOUS les clients pendant que ca tourne.
3. journal_entries et invoices fetches en double (2 requetes identiques).

Fix (`health_audit.py`) :
- Une seule requete journal_entries et une seule requete invoices.
- owners/suppliers filtres par copropriete (plus de full-DB scan).
- Remplacement de la boucle O(lignes*owners) par une map de lookup O(1)
  (account_number -> owner_id).
- Index Mongo ajoutes sur copropriete_id (journal_entries, invoices,
  suppliers, fund_calls) et copropriete_ids (owners) au demarrage.
- Timeout de garde-fou (20s) sur l'endpoint FastAPI.

Ce test verifie :
A) La correction fonctionnelle du calcul (orphelins, solde owner) est
   preservee malgre le refactor.
B) L'isolation multi-tenant : des owners/suppliers d'UNE AUTRE copropriete
   n'impactent JAMAIS le resultat (garde-fou contre une regression du bug
   "full scan sans filtre").
C) La performance reste bornee (< 5s) meme avec un volume de donnees
   consequent (garde-fou anti-regression perf).
"""
import asyncio
import os
import sys
import time
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

from health_audit import compute_health_audit  # noqa: E402


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(prefix: str):
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    return {"db": db, "cid": cid}


async def _cleanup(ctx, extra_cids=None):
    db = ctx["db"]
    cids = [ctx["cid"]] + list(extra_cids or [])
    await db.coproprietes.delete_many({"id": {"$in": cids}})
    await db.invoices.delete_many({"copropriete_id": {"$in": cids}})
    await db.journal_entries.delete_many({"copropriete_id": {"$in": cids}})
    await db.owners.delete_many({"copropriete_id": {"$in": cids}})
    await db.suppliers.delete_many({"copropriete_id": {"$in": cids}})
    await db.fund_calls.delete_many({"copropriete_id": {"$in": cids}})


def _get_anomaly(result, category):
    for a in result.get("anomalies") or []:
        if a.get("category") == category:
            return a
    return None


# =========================================================================
# A) Solde owner + orphelins : la map O(1) reproduit le meme resultat que
#    l'ancienne boucle O(lignes * owners)
# =========================================================================
async def _scenario_owner_balance_and_orphans():
    ctx = await _setup("iter90fo-bal")
    try:
        db = ctx["db"]
        cid = ctx["cid"]
        owner_id = str(uuid.uuid4())
        supplier_id = str(uuid.uuid4())
        await db.owners.insert_one({
            "id": owner_id, "name": "Owner Test iter90fo", "copropriete_id": cid,
            "copropriete_ids": [cid],
            "tier_accounts": {cid: {"provisions": "40000010", "reserve": "40000011"}},
        })
        # Supplier SANS tier_accounts pour cette copro -> son compte 440 sera orphelin
        await db.suppliers.insert_one({
            "id": supplier_id, "name": "Supplier Test iter90fo", "copropriete_id": cid,
            "tier_accounts": {},
        })
        await db.journal_entries.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "VE",
             "date": "2025-01-15", "reference": "VE-1", "total_debit": 100.0,
             "total_credit": 100.0,
             "lines": [
                 {"account_number": "40000010", "debit": 100.0, "credit": 0},
                 {"account_number": "70000000", "debit": 0, "credit": 100.0},
             ]},
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "FI",
             "date": "2025-02-01", "reference": "FI-1", "total_debit": 30.0,
             "total_credit": 30.0,
             "lines": [
                 {"account_number": "100", "debit": 30.0, "credit": 0},
                 {"account_number": "40000010", "debit": 0, "credit": 30.0},
             ]},
            # Ecriture extournee -> NE DOIT PAS compter dans le solde owner
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "OD",
             "date": "2025-03-01", "reference": "OD-REV", "total_debit": 500.0,
             "total_credit": 500.0, "reversed": True,
             "lines": [
                 {"account_number": "40000010", "debit": 500.0, "credit": 0},
                 {"account_number": "61000", "debit": 0, "credit": 500.0},
             ]},
            # Ligne fournisseur orpheline (aucun supplier n'a ce compte 440)
            {"id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "AC",
             "date": "2025-01-20", "reference": "AC-1", "total_debit": 50.0,
             "total_credit": 50.0,
             "lines": [
                 {"account_number": "61000", "debit": 50.0, "credit": 0},
                 {"account_number": "44099999", "debit": 0, "credit": 50.0},
             ]},
        ])
        result = await compute_health_audit(db, cid)

        orphan_anomaly = _get_anomaly(result, "orphans")
        assert orphan_anomaly is not None, "Le compte 44099999 (aucun supplier) doit etre detecte orphelin"
        orphan_accounts = {item["account"] for item in orphan_anomaly["items"]}
        assert "44099999" in orphan_accounts

        # Solde owner attendu : 100 (VE debit) - 30 (FI credit) = 70 debiteur.
        # L'ecriture extournee (500) ne doit PAS etre comptee.
        fund_call_id = str(uuid.uuid4())
        await db.fund_calls.insert_one({
            "id": fund_call_id, "copropriete_id": cid, "name": "Test Appel",
            "due_date": "2024-01-01",
            "distribution": [{"owner_id": owner_id, "owner_name": "Owner Test iter90fo",
                               "amount": 70.0, "paid": False}],
        })
        result2 = await compute_health_audit(db, cid, days_threshold=1)
        late_anomaly = _get_anomaly(result2, "owners_late")
        assert late_anomaly is not None, "L'owner avec solde debiteur 70 (100-30, hors extourne) doit apparaitre en retard"
        matched = [it for it in late_anomaly["items"] if it["owner_id"] == owner_id]
        assert matched, f"Owner {owner_id} absent de owners_late : {late_anomaly}"
        assert abs(matched[0]["tier_balance"] - 70.0) < 0.01, (
            f"REGRESSION iter90fo : solde owner attendu 70.0 (100 VE - 30 FI, "
            f"extourne exclue), obtenu {matched[0]['tier_balance']}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# B) Isolation multi-tenant : owners/suppliers d'une AUTRE copropriete ne
#    doivent jamais impacter le resultat (garde-fou anti full-DB-scan)
# =========================================================================
async def _scenario_multi_tenant_isolation():
    ctx = await _setup("iter90fo-isoA")
    other_ctx = await _setup("iter90fo-isoB")
    try:
        db = ctx["db"]
        cid = ctx["cid"]
        other_cid = other_ctx["cid"]

        # Owner + compte 400 dans l'AUTRE copropriete, jamais reference dans cid
        await db.owners.insert_one({
            "id": str(uuid.uuid4()), "name": "Owner Autre ACP", "copropriete_id": other_cid,
            "copropriete_ids": [other_cid],
            "tier_accounts": {other_cid: {"provisions": "40099999"}},
        })
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "OD",
            "date": "2025-01-10", "reference": "OD-ISO", "total_debit": 40.0,
            "total_credit": 40.0,
            "lines": [
                # Meme numero de compte que celui de l'AUTRE copro (coincidence
                # possible) mais dans CE copro -> doit rester orphelin (pas
                # rattache a l'owner de l'autre ACP)
                {"account_number": "40099999", "debit": 40.0, "credit": 0},
                {"account_number": "70000000", "debit": 0, "credit": 40.0},
            ],
        })
        result = await compute_health_audit(db, cid)
        orphan_anomaly = _get_anomaly(result, "orphans")
        assert orphan_anomaly is not None
        orphan_accounts = {item["account"] for item in orphan_anomaly["items"]}
        assert "40099999" in orphan_accounts, (
            "REGRESSION iter90fo : le compte 40099999 ne doit PAS etre "
            "rattache a l'owner d'une AUTRE copropriete (isolation multi-tenant)"
        )
    finally:
        await _cleanup(ctx, extra_cids=[other_ctx["cid"]])


# =========================================================================
# C) Performance : garde-fou anti-regression sur le volume de donnees
# =========================================================================
async def _scenario_performance_bounded():
    ctx = await _setup("iter90fo-perf")
    try:
        db = ctx["db"]
        cid = ctx["cid"]
        owners_docs = []
        for i in range(150):
            oid = str(uuid.uuid4())
            owners_docs.append({
                "id": oid, "name": f"Owner {i}", "copropriete_id": cid,
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": f"400{i:05d}"}},
            })
        await db.owners.insert_many(owners_docs)

        entries_docs = []
        for i in range(300):
            owner_acc = owners_docs[i % 150]["tier_accounts"][cid]["provisions"]
            entries_docs.append({
                "id": str(uuid.uuid4()), "copropriete_id": cid, "journal_type": "VE",
                "date": "2025-01-01", "reference": f"VE-{i}",
                "total_debit": 10.0, "total_credit": 10.0,
                "lines": [
                    {"account_number": owner_acc, "debit": 10.0, "credit": 0},
                    {"account_number": "70000000", "debit": 0, "credit": 10.0},
                ],
            })
        await db.journal_entries.insert_many(entries_docs)

        start = time.monotonic()
        result = await compute_health_audit(db, cid)
        elapsed = time.monotonic() - start
        assert result["copropriete_id"] == cid
        assert elapsed < 5.0, (
            f"REGRESSION PERF iter90fo : compute_health_audit a pris {elapsed:.2f}s "
            f"pour 150 owners x 300 ecritures (doit rester < 5s, cause du crash "
            f"Cloudflare 502 en production)"
        )
    finally:
        await _cleanup(ctx)


def test_owner_balance_and_orphans_correctness():
    asyncio.run(_scenario_owner_balance_and_orphans())


def test_multi_tenant_isolation():
    asyncio.run(_scenario_multi_tenant_isolation())


def test_performance_bounded():
    asyncio.run(_scenario_performance_bounded())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
