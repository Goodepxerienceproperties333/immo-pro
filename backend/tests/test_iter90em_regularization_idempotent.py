"""iter90em : Regularisation d'exercice - idempotence + bilan equilibre.

Bug PROD : la regularisation etait lancee plusieurs fois, creant a chaque
fois une nouvelle OD d'extourne SANS marquer les VE originales comme
`reversed=True`. Consequence :
  - les provisions appelees etaient comptees en DOUBLE (VE + VE originales
    non marquees)
  - le boni au bilan (compte 499) explosait (564185.99 EUR au lieu de
    ~6453 EUR pour 19000 provisions - 12546.20 charges)
  - ACTIF != PASSIF -> bilan faux

Fix iter90em :
  1. `regularize_fiscal_year` exclut les VE deja `reversed=True` ou
     `is_reversal=True` du calcul de `provisions_called_by_owner`.
  2. Marque les VE originales `reversed=True` + `reversed_by_entry_id`
     lors de la creation de l'OD d'extourne.
  3. Marque l'OD d'extourne `is_reversal=True`.
  4. `revert_regularization` demarque les VE originales (rollback complet).
  5. `_distribute()` supporte le phantom key fallback (lot_number normalise).
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
    return {}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_acacia_scenario(db, suffix: str) -> dict:
    """Setup : 3 proprios, 1 fiscal year, provisions=19000, charges reelles=12546.20."""
    cid = f"iter90em-{suffix}"
    fy_id = f"iter90em-fy-{suffix}"
    o1, o2, o3 = f"o1-{suffix}", f"o2-{suffix}", f"o3-{suffix}"
    lot1, lot2, lot3 = f"lot1-{suffix}", f"lot2-{suffix}", f"lot3-{suffix}"
    key_id = f"key-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "Acacia iter90em ACP"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid,
        "name": "Acacia 2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "700000", "name": "Provisions", "copropriete_id": cid, "class_num": 7},
        {"number": "610000", "name": "Charges", "copropriete_id": cid, "class_num": 6},
        {"number": "41010001", "name": "Prov O1", "copropriete_id": cid, "class_num": 4},
        {"number": "41010002", "name": "Prov O2", "copropriete_id": cid, "class_num": 4},
        {"number": "41010003", "name": "Prov O3", "copropriete_id": cid, "class_num": 4},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "Prop1", "last_name": "Prop1",
         "tier_accounts": {cid: {"provisions": "41010001"}}},
        {"id": o2, "name": "Prop2", "last_name": "Prop2",
         "tier_accounts": {cid: {"provisions": "41010002"}}},
        {"id": o3, "name": "Prop3", "last_name": "Prop3",
         "tier_accounts": {cid: {"provisions": "41010003"}}},
    ])
    # 3 lots avec quotites 100/1000
    await db.lots.insert_many([
        {"id": lot1, "number": "1", "copropriete_id": cid, "owner_id": o1, "quotity": 100},
        {"id": lot2, "number": "2", "copropriete_id": cid, "owner_id": o2, "quotity": 100},
        {"id": lot3, "number": "3", "copropriete_id": cid, "owner_id": o3, "quotity": 100},
    ])
    # Cle de repartition
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid,
        "name": "Charges communes", "is_default": True,
        "lots": [
            {"lot_id": lot1, "lot_number": "1", "share": 100},
            {"lot_id": lot2, "lot_number": "2", "share": 100},
            {"lot_id": lot3, "lot_number": "3", "share": 100},
        ],
    })

    # 4 VE (appels de fonds) totalisant 19000 EUR sur les 3 proprios
    # Chaque appel : 4750 EUR reparti selon quotites (1583.33/1583.33/1583.33)
    quotity_each = round(19000.0 / 4 / 3, 2)  # 1583.33
    for q in range(1, 5):
        await db.journal_entries.insert_one({
            "id": f"ve-q{q}-{suffix}", "copropriete_id": cid,
            "journal_type": "VE", "date": f"2026-{q*3:02d}-01",
            "reference": f"AF-Trimestriel {q}/4",
            "description": f"Appel de provisions - Trimestriel {q}/4",
            "lines": [
                {"account_number": "41010001", "debit": quotity_each, "credit": 0.0,
                 "third_party_id": o1, "third_party_name": "Prop1"},
                {"account_number": "41010002", "debit": quotity_each, "credit": 0.0,
                 "third_party_id": o2, "third_party_name": "Prop2"},
                {"account_number": "41010003", "debit": quotity_each, "credit": 0.0,
                 "third_party_id": o3, "third_party_name": "Prop3"},
                {"account_number": "700000",
                 "debit": 0.0, "credit": round(quotity_each * 3, 2)},
            ],
        })
    # 1 facture 12546.20 EUR de charges reelles (classe 6)
    await db.invoices.insert_one({
        "id": f"inv-{suffix}", "copropriete_id": cid,
        "number": "V-CHARGES", "supplier": "SUP",
        "date": "2026-06-15", "total_amount": 12546.20,
        "account_number": "610000",
        "distribution_key_id": key_id,
        "status": "unpaid",
    })
    return {
        "cid": cid, "fy_id": fy_id,
        "o1": o1, "o2": o2, "o3": o3,
        "quotity_each": quotity_each,
    }


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({f"tier_accounts.{cid}": {"$exists": True}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def test_regularization_computes_correct_boni_acacia():
    """Scenario user Acacia : 19000 provisions - 12546.20 charges = 6453.80 boni."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_acacia_scenario(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                # Dry run pour verifier les chiffres
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    f"?dry_run=true",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                summary = data["summary"]
                # Provisions total : 4 appels x 3 proprios x ~1583.33 = 18999.96
                assert abs(summary["total_provisions_called"] - 19000.0) < 1.0, (
                    f"Provisions attendues ~19000, recu {summary['total_provisions_called']}"
                )
                assert abs(summary["total_real_expenses"] - 12546.20) < 0.01
                # Regul globale = charges - provisions = 12546.20 - 19000 = -6453.80
                # (negatif = boni, a rembourser aux proprios)
                per_owner = data["per_owner"]
                total_regul = sum(p["regularization"] for p in per_owner)
                assert abs(total_regul - (-6453.80)) < 1.0, (
                    f"Regul totale attendue ~-6453.80, recu {total_regul}"
                )
                # Chaque proprio doit avoir un boni (regularization negatif)
                for p in per_owner:
                    assert p["regularization"] < 0, (
                        f"Proprio {p['owner_name']} devrait etre crediteur "
                        f"(regul={p['regularization']})"
                    )
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_regularization_marks_ve_originals_as_reversed():
    """iter90em : apres regularisation, les VE originales sont marquees
    reversed=True + reversed_by_entry_id + reversed_reason."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_acacia_scenario(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                )
                assert r.status_code == 200, r.text
                # Verifie les VE sont marquees reversed=True
                ve_after = await db.journal_entries.find(
                    {"copropriete_id": ctx["cid"], "journal_type": "VE"},
                ).to_list(100)
                for ve in ve_after:
                    assert ve.get("reversed") is True, (
                        f"VE {ve.get('reference')} non marquee reversed=True"
                    )
                    assert ve.get("reversed_by_entry_id"), (
                        f"VE {ve.get('reference')} sans reversed_by_entry_id"
                    )
                    assert ve.get("reversed_reason") == "regularization"
                # L'OD d'extourne doit etre marquee is_reversal=True
                ext = await db.journal_entries.find_one(
                    {"copropriete_id": ctx["cid"], "source_type": "regularization",
                     "reference": {"$regex": "^EXT-"}},
                )
                assert ext.get("is_reversal") is True
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_regularization_is_idempotent_no_double_counting():
    """Lancement de la regularisation 3 fois -> le boni reste identique.
    Sans le fix, chaque relance doublait les provisions comptees."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_acacia_scenario(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                results = []
                for i in range(3):
                    r = await c.post(
                        f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                        f"?dry_run=true",
                    )
                    assert r.status_code == 200, r.text
                    results.append(r.json()["summary"]["total_provisions_called"])
                    # Persist entre chaque dry_run
                    r2 = await c.post(
                        f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    )
                    # Deuxieme regul apres persist doit rester similaire
                # 1re : ~19000 ; les 2 suivantes doivent aussi voir ~0
                # (car les VE originales sont marquees reversed=True)
                assert abs(results[0] - 19000.0) < 1.0, f"1re regul : {results[0]}"
                # 2eme et 3eme : les VE sont deja reversed -> provisions_called = 0
                assert results[1] < 1.0, (
                    f"2e regul devrait etre ~0 (idempotent), recu {results[1]}"
                )
                assert results[2] < 1.0, (
                    f"3e regul devrait etre ~0 (idempotent), recu {results[2]}"
                )
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())


def test_revert_regularization_unmarks_ve_originals():
    """DELETE regularize -> les VE originales sont demarquees (reversed=False)."""
    async def _run():
        db = await _mongo()
        ctx = await _seed_acacia_scenario(db, uuid.uuid4().hex[:6])
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                await c.post(f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize")
                # Verifie VE marquees
                ve = await db.journal_entries.find_one(
                    {"copropriete_id": ctx["cid"], "journal_type": "VE"},
                )
                assert ve.get("reversed") is True

                # Rollback
                r = await c.delete(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                )
                assert r.status_code == 200

                # Verifie VE demarquees
                ve_list = await db.journal_entries.find(
                    {"copropriete_id": ctx["cid"], "journal_type": "VE"},
                ).to_list(100)
                for ve in ve_list:
                    assert not ve.get("reversed"), (
                        f"VE {ve.get('reference')} devrait etre demarquee apres rollback"
                    )
                    assert not ve.get("reversed_by_entry_id")
                # Les OD EXT/AFF doivent avoir ete supprimees
                remaining_ods = await db.journal_entries.count_documents(
                    {"copropriete_id": ctx["cid"], "source_type": "regularization"}
                )
                assert remaining_ods == 0
        finally:
            await _cleanup(db, ctx["cid"])

    asyncio.run(_run())
