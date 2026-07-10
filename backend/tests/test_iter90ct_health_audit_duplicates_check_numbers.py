"""
iter90ct : REGRESSION LOCK - Le detecteur de doublons du dashboard ne doit
PAS flagger 2 factures avec des numeros DIFFERENTS meme si supplier + montant
+ date coincident.

Bug rapporte utilisateur (Feb 2026) :
> "si les nr de factures sont differents cela ne peut etre un doublons il
> faut corriger"

Contexte : PROD ACP affichait "1 doublon(s) potentiel(s) detecte(s)" pour
2 factures Good Experience Properties 15.00 EUR INV/0026 et INV/0028
(numeros differents). Selon la regle metier iter90bp : deux numeros de
facture differents = jamais des doublons.

Fix iter90ct (`health_audit.py::compute_health_audit`) :
- Ajoute filtre : si `n1 and n2 and n1 != n2` -> skip (pas doublon)
- Preserve le comportement quand au moins un numero est vide
  (cas import legacy Optipro sans numero)
- Preserve le comportement quand les numeros sont identiques
  (doublon confirme, meme supplier + amount + date proche)
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

# Import direct de la fonction (pas d'HTTP requis)
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


async def _cleanup(ctx):
    await ctx["db"].coproprietes.delete_one({"id": ctx["cid"]})
    await ctx["db"].invoices.delete_many({"copropriete_id": ctx["cid"]})


def _get_duplicates_anomaly(result):
    """Retourne l'anomalie 'duplicates' du result compute_health_audit()."""
    for a in result.get("anomalies") or []:
        if a.get("category") == "duplicates":
            return a
    return None


# =========================================================================
# SCENARIO 1 : 2 factures numeros DIFFERENTS -> pas doublons
# =========================================================================
async def _scenario_different_numbers_not_flagged():
    ctx = await _setup("iter90ct-diff")
    try:
        db = ctx["db"]
        await db.invoices.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "Good Experience Properties", "supplier_id": "sup-1",
             "number": "INV/0026", "date": "2025-06-15",
             "total_amount": 15.00, "status": "unpaid"},
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "Good Experience Properties", "supplier_id": "sup-1",
             "number": "INV/0028", "date": "2025-06-17",
             "total_amount": 15.00, "status": "unpaid"},
        ])
        result = await compute_health_audit(db, ctx["cid"])
        anomaly = _get_duplicates_anomaly(result)
        assert anomaly is None or anomaly.get("count", 0) == 0, (
            f"REGRESSION iter90ct : INV/0026 vs INV/0028 (numeros differents) "
            f"NE DEVRAIENT PAS etre flagges comme doublons. "
            f"Obtenu : {anomaly}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : 2 factures numeros IDENTIQUES -> flagges comme doublons
# =========================================================================
async def _scenario_identical_numbers_flagged():
    ctx = await _setup("iter90ct-same")
    try:
        db = ctx["db"]
        await db.invoices.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "ELECTRO", "supplier_id": "sup-e",
             "number": "F-2025-100", "date": "2025-06-15",
             "total_amount": 200.00, "status": "unpaid"},
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "ELECTRO", "supplier_id": "sup-e",
             "number": "F-2025-100", "date": "2025-06-17",
             "total_amount": 200.00, "status": "unpaid"},
        ])
        result = await compute_health_audit(db, ctx["cid"])
        anomaly = _get_duplicates_anomaly(result)
        assert anomaly and anomaly.get("count", 0) >= 1, (
            f"iter90ct : F-2025-100 x2 (numero IDENTIQUE) DOIT etre flag doublon. "
            f"Obtenu : {anomaly}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : 2 factures dont un numero VIDE (import legacy)
# -> flagges comme doublons potentiels (fallback safe)
# =========================================================================
async def _scenario_one_empty_number_flagged():
    ctx = await _setup("iter90ct-empty")
    try:
        db = ctx["db"]
        await db.invoices.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "OPTIPRO", "supplier_id": "sup-o",
             "number": "", "date": "2025-06-15",
             "total_amount": 500.00, "status": "unpaid"},
            {"id": str(uuid.uuid4()), "copropriete_id": ctx["cid"],
             "supplier": "OPTIPRO", "supplier_id": "sup-o",
             "number": "F-500", "date": "2025-06-17",
             "total_amount": 500.00, "status": "unpaid"},
        ])
        result = await compute_health_audit(db, ctx["cid"])
        anomaly = _get_duplicates_anomaly(result)
        # Import legacy : un numero vide -> on garde le flag (safe)
        assert anomaly and anomaly.get("count", 0) >= 1, (
            f"iter90ct : import legacy avec numero vide doit etre flagge. "
            f"Obtenu : {anomaly}"
        )
    finally:
        await _cleanup(ctx)


def test_different_numbers_not_flagged():
    asyncio.run(_scenario_different_numbers_not_flagged())


def test_identical_numbers_still_flagged():
    asyncio.run(_scenario_identical_numbers_flagged())


def test_empty_number_still_flagged():
    asyncio.run(_scenario_one_empty_number_flagged())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
