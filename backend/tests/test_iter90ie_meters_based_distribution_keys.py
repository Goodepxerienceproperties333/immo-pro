"""iter90ie : Feature Compteurs - repartition des charges basee sur les
consommations reelles (eau, gaz, electricite, chauffage, entretien chaudiere).

Verifie que :
  1. Le helper `compute_meter_key_lots` calcule correctement les shares
     depuis les releves index_debut / index_fin.
  2. Le fallback (`fallback_key_id`) est utilise pour les lots sans
     compteur ou sans releve.
  3. L'endpoint `POST /distribution-keys/{id}/compute-from-meters`
     retourne le rapport et met a jour la cle en mode live.
  4. Le decompte annuel utilise DYNAMIQUEMENT les shares meter (integration
     end-to-end via `build_decompte_pdf`).
  5. Une facture avec `distribution_key_id` pointant vers une cle meter
     est repartie au prorata des consommations reelles.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_ie_compute_meter_key_lots_basic():
    """Cas simple : 2 lots avec compteurs, consommation 10 et 30."""
    from meter_shares import compute_meter_key_lots

    all_lots = [
        {"id": "lot-A", "number": "A"},
        {"id": "lot-B", "number": "B"},
    ]
    meters = [
        {"id": "m-A", "meter_type": "water", "lot_id": "lot-A"},
        {"id": "m-B", "meter_type": "water", "lot_id": "lot-B"},
    ]
    # Bornes : releve 2025-12-31 et 2026-12-31
    readings = [
        # Lot A : 100 -> 110 (conso 10)
        {"meter_id": "m-A", "date": "2025-12-31", "value": 100, "consumption": 0},
        {"meter_id": "m-A", "date": "2026-12-31", "value": 110, "consumption": 10},
        # Lot B : 500 -> 530 (conso 30)
        {"meter_id": "m-B", "date": "2025-12-31", "value": 500, "consumption": 0},
        {"meter_id": "m-B", "date": "2026-12-31", "value": 530, "consumption": 30},
    ]
    r = compute_meter_key_lots(
        meter_type="water",
        meters=meters,
        readings=readings,
        all_lots=all_lots,
        start_date="2026-01-01",
        end_date="2026-12-31",
    )
    assert r["coverage"]["with_meter_reading"] == 2
    assert r["coverage"]["via_fallback"] == 0
    assert r["coverage"]["excluded"] == 0
    lot_shares = {l["lot_id"]: l["share"] for l in r["lots"]}
    assert abs(lot_shares["lot-A"] - 10.0) < 0.001
    assert abs(lot_shares["lot-B"] - 30.0) < 0.001
    assert abs(r["total"] - 40.0) < 0.001


def test_ie_meter_key_falls_back_for_missing_readings():
    """Lot C sans compteur -> tombe dans la cle fallback."""
    from meter_shares import compute_meter_key_lots

    all_lots = [
        {"id": "lot-A", "number": "A"},
        {"id": "lot-B", "number": "B"},
        {"id": "lot-C", "number": "C"},
    ]
    meters = [
        {"id": "m-A", "meter_type": "water", "lot_id": "lot-A"},
        {"id": "m-B", "meter_type": "water", "lot_id": "lot-B"},
    ]
    readings = [
        {"meter_id": "m-A", "date": "2026-01-01", "value": 100},
        {"meter_id": "m-A", "date": "2026-12-31", "value": 110},
        {"meter_id": "m-B", "date": "2026-01-01", "value": 500},
        {"meter_id": "m-B", "date": "2026-12-31", "value": 520},
    ]
    fallback_lots = [
        {"lot_id": "lot-A", "share": 100},
        {"lot_id": "lot-B", "share": 100},
        {"lot_id": "lot-C", "share": 50, "excluded": False},
    ]
    r = compute_meter_key_lots(
        meter_type="water",
        meters=meters,
        readings=readings,
        all_lots=all_lots,
        start_date="2026-01-01",
        end_date="2026-12-31",
        fallback_key_lots=fallback_lots,
    )
    assert r["coverage"]["with_meter_reading"] == 2
    assert r["coverage"]["via_fallback"] == 1  # Lot C via fallback
    lot_shares = {l["lot_id"]: (l["share"], l["source"]) for l in r["lots"]}
    assert lot_shares["lot-A"] == (10.0, "meter")
    assert lot_shares["lot-B"] == (20.0, "meter")
    assert lot_shares["lot-C"] == (50.0, "fallback")


def test_ie_meter_key_excluded_without_fallback():
    """Sans fallback : lots sans releve ne sont pas dans la cle."""
    from meter_shares import compute_meter_key_lots

    all_lots = [
        {"id": "lot-A", "number": "A"},
        {"id": "lot-B", "number": "B"},
    ]
    meters = [{"id": "m-A", "meter_type": "water", "lot_id": "lot-A"}]
    readings = [
        {"meter_id": "m-A", "date": "2026-01-01", "value": 100},
        {"meter_id": "m-A", "date": "2026-12-31", "value": 115},
    ]
    r = compute_meter_key_lots(
        meter_type="water",
        meters=meters,
        readings=readings,
        all_lots=all_lots,
        start_date="2026-01-01",
        end_date="2026-12-31",
    )
    assert r["coverage"]["with_meter_reading"] == 1
    assert r["coverage"]["via_fallback"] == 0
    assert r["coverage"]["excluded"] == 1  # Lot B
    assert len(r["lots"]) == 1
    assert r["lots"][0]["lot_id"] == "lot-A"


def test_ie_meter_key_meter_type_filter():
    """Un compteur d'un autre type doit etre ignore."""
    from meter_shares import compute_meter_key_lots

    all_lots = [
        {"id": "lot-A", "number": "A"},
        {"id": "lot-B", "number": "B"},
    ]
    meters = [
        {"id": "m-water-A", "meter_type": "water", "lot_id": "lot-A"},
        {"id": "m-gas-B", "meter_type": "gas", "lot_id": "lot-B"},
    ]
    readings = [
        {"meter_id": "m-water-A", "date": "2026-01-01", "value": 100},
        {"meter_id": "m-water-A", "date": "2026-12-31", "value": 110},
        {"meter_id": "m-gas-B", "date": "2026-01-01", "value": 200},
        {"meter_id": "m-gas-B", "date": "2026-12-31", "value": 250},
    ]
    # Filtre water : seul lot-A doit compter
    r = compute_meter_key_lots(
        meter_type="water",
        meters=meters,
        readings=readings,
        all_lots=all_lots,
        start_date="2026-01-01",
        end_date="2026-12-31",
    )
    assert r["coverage"]["with_meter_reading"] == 1
    lot_ids = {l["lot_id"] for l in r["lots"]}
    assert lot_ids == {"lot-A"}


def test_ie_meter_key_intermediate_reading_for_mutation():
    """Releve intermediaire (mutation) : la conso doit etre calculee
    entre les 2 bornes de la periode, INCLUANT l'index a la vente."""
    from meter_shares import compute_meter_key_lots

    all_lots = [{"id": "lot-A", "number": "A"}]
    meters = [{"id": "m-A", "meter_type": "water", "lot_id": "lot-A"}]
    # Vendu au 30 juin, releve intermediaire ce jour-la
    readings = [
        {"meter_id": "m-A", "date": "2026-01-01", "value": 100},
        {"meter_id": "m-A", "date": "2026-06-30", "value": 120},  # intermediaire
        {"meter_id": "m-A", "date": "2026-12-31", "value": 155},
    ]
    # Periode vendeur : 01/01 -> 30/06 = 20 m3
    r_seller = compute_meter_key_lots(
        meter_type="water", meters=meters, readings=readings, all_lots=all_lots,
        start_date="2026-01-01", end_date="2026-06-30",
    )
    seller_share = next(l for l in r_seller["lots"] if l["lot_id"] == "lot-A")
    assert abs(seller_share["share"] - 20.0) < 0.001

    # Periode acheteur : 01/07 -> 31/12 = 35 m3
    r_buyer = compute_meter_key_lots(
        meter_type="water", meters=meters, readings=readings, all_lots=all_lots,
        start_date="2026-07-01", end_date="2026-12-31",
    )
    buyer_share = next(l for l in r_buyer["lots"] if l["lot_id"] == "lot-A")
    assert abs(buyer_share["share"] - 35.0) < 0.001


# ---- Integration : endpoint compute-from-meters ----

async def _seed_meter_scenario(db, copro_id, fy_id):
    """Cree une ACP avec 3 lots, 2 compteurs eau, 1 cle meter + 1 fallback."""
    for coll in ["coproprietes", "lots", "meters", "meter_readings",
                 "distribution_keys", "fiscal_years", "owners", "invoices"]:
        await db[coll].delete_many({"copropriete_id": copro_id})

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP ie test",
        "address": "Rue Test 1", "postal_code": "1000", "city": "Bxl",
    })
    for lot_id, num in [("lot-A", "A"), ("lot-B", "B"), ("lot-C", "C")]:
        await db.lots.insert_one({
            "id": lot_id, "copropriete_id": copro_id,
            "number": num, "quotity": 100.0,
        })
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": copro_id,
        "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "status": "open",
    })
    # Compteurs
    await db.meters.insert_many([
        {"id": "m-A", "copropriete_id": copro_id, "name": "Eau A",
         "meter_type": "water", "unit": "m3", "lot_id": "lot-A"},
        {"id": "m-B", "copropriete_id": copro_id, "name": "Eau B",
         "meter_type": "water", "unit": "m3", "lot_id": "lot-B"},
        # Pas de compteur pour lot-C -> fallback
    ])
    await db.meter_readings.insert_many([
        {"id": "r-A0", "meter_id": "m-A", "date": "2026-01-01", "value": 100, "consumption": 0},
        {"id": "r-A1", "meter_id": "m-A", "date": "2026-12-31", "value": 130, "consumption": 30},
        {"id": "r-B0", "meter_id": "m-B", "date": "2026-01-01", "value": 200, "consumption": 0},
        {"id": "r-B1", "meter_id": "m-B", "date": "2026-12-31", "value": 210, "consumption": 10},
    ])
    # Cle fallback (tantiemes standard)
    await db.distribution_keys.insert_one({
        "id": "key-fallback", "copropriete_id": copro_id,
        "name": "Tantiemes generaux", "key_type": "quotity",
        "code": "001", "is_default": True,
        "lots": [
            {"lot_id": "lot-A", "share": 333.33, "excluded": False, "lot_number": "A"},
            {"lot_id": "lot-B", "share": 333.33, "excluded": False, "lot_number": "B"},
            {"lot_id": "lot-C", "share": 333.34, "excluded": False, "lot_number": "C"},
        ],
    })
    # Cle meter
    await db.distribution_keys.insert_one({
        "id": "key-meter-water", "copropriete_id": copro_id,
        "name": "Eau (conso)", "key_type": "meter",
        "code": "010", "meter_type": "water",
        "fallback_key_id": "key-fallback",
        "lots": [],  # Sera calcule dynamiquement
    })


def test_ie_endpoint_compute_from_meters_dry_run_and_live():
    """Endpoint POST /api/distribution-keys/{id}/compute-from-meters :
    dry_run retourne rapport SANS ecrire, live persiste les shares."""
    copro_id = f"acp-ie-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-ie-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from routes.invoices import create_invoices_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_meter_scenario(db, copro_id, fy_id)

            app = FastAPI()
            app.include_router(create_invoices_router(db))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # Dry run
                r = await ac.post(
                    "/api/distribution-keys/key-meter-water/compute-from-meters",
                    json={"start_date": "2026-01-01", "end_date": "2026-12-31",
                          "dry_run": True},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["meter_type"] == "water"
                report = data["report"]
                assert report["coverage"]["with_meter_reading"] == 2
                assert report["coverage"]["via_fallback"] == 1  # Lot C
                shares = {l["lot_id"]: (l["share"], l["source"]) for l in report["lots"]}
                assert abs(shares["lot-A"][0] - 30.0) < 0.001
                assert shares["lot-A"][1] == "meter"
                assert abs(shares["lot-B"][0] - 10.0) < 0.001
                assert shares["lot-B"][1] == "meter"
                assert shares["lot-C"][1] == "fallback"
                # Rien n'a ete persiste
                stored = await db.distribution_keys.find_one(
                    {"id": "key-meter-water"}, {"_id": 0}
                )
                assert stored.get("lots") == []

                # Live
                r2 = await ac.post(
                    "/api/distribution-keys/key-meter-water/compute-from-meters",
                    json={"start_date": "2026-01-01", "end_date": "2026-12-31",
                          "dry_run": False},
                )
                assert r2.status_code == 200
                assert r2.json().get("applied") is True
                stored = await db.distribution_keys.find_one(
                    {"id": "key-meter-water"}, {"_id": 0}
                )
                assert len(stored["lots"]) == 3
                by_lot = {l["lot_id"]: l for l in stored["lots"]}
                assert abs(by_lot["lot-A"]["share"] - 30.0) < 0.01
                assert by_lot["lot-A"]["source"] == "meter"
                assert by_lot["lot-C"]["source"] == "fallback"
                assert stored.get("meter_computed_at")
        finally:
            for coll in ["coproprietes", "lots", "meters", "meter_readings",
                         "distribution_keys", "fiscal_years"]:
                await db[coll].delete_many({"copropriete_id": copro_id})
            client.close()

    asyncio.run(_run())


def test_ie_endpoint_rejects_non_meter_key():
    """Un endpoint compute-from-meters sur une cle NON-meter -> 400."""
    copro_id = f"acp-ie-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from routes.invoices import create_invoices_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.distribution_keys.insert_one({
                "id": "key-quotity-only", "copropriete_id": copro_id,
                "name": "Tantiemes", "key_type": "quotity",
                "lots": [],
            })
            app = FastAPI()
            app.include_router(create_invoices_router(db))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post(
                    "/api/distribution-keys/key-quotity-only/compute-from-meters",
                    json={"start_date": "2026-01-01", "end_date": "2026-12-31"},
                )
                assert r.status_code == 400
                assert "meter" in (r.json().get("detail") or "").lower()
        finally:
            await db.distribution_keys.delete_many({"id": "key-quotity-only"})
            client.close()

    asyncio.run(_run())


def test_ie_decompte_pdf_uses_meter_shares_dynamically():
    """End-to-end : le decompte annuel repartit une facture eau selon les
    consommations reelles, pas les tantiemes generaux."""
    copro_id = f"acp-ie-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-ie-{uuid.uuid4().hex[:8]}"
    owner_a = f"own-A-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from pdf_decompte import build_decompte_pdf
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_meter_scenario(db, copro_id, fy_id)
            # Assigner lot-A au owner_a
            await db.lots.update_one({"id": "lot-A"}, {"$set": {"owner_id": owner_a}})
            await db.owners.insert_one({
                "id": owner_a, "name": "Owner A",
                "tier_accounts": {copro_id: {"provisions": "410-A"}},
            })

            # Facture eau 400 EUR sur la cle meter
            # Conso : A=30, B=10, C=fallback 333.34 -> total 373.34
            # -> Lot A = 30/373.34 * 400 = 32.14
            await db.invoices.insert_one({
                "id": "inv-eau-1", "copropriete_id": copro_id,
                "date": "2026-06-15", "number": "EAU-001",
                "supplier": "Vivaqua", "description": "Facture eau annuelle",
                "total_amount": 400.0, "vat_amount": 0.0,
                "account_number": "601000",
                "distribution_key_id": "key-meter-water",
                "status": "paid",
            })

            # Charger contexte comme le fait reports.py
            owner_lots = await db.lots.find({"copropriete_id": copro_id, "owner_id": owner_a}, {"_id": 0}).to_list(10)
            all_lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10)
            invoices = await db.invoices.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(100)
            dks = await db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(100)
            meters = await db.meters.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(100)
            meter_ids = [m["id"] for m in meters]
            readings = await db.meter_readings.find({"meter_id": {"$in": meter_ids}}, {"_id": 0}).to_list(1000)

            fy = await db.fiscal_years.find_one({"id": fy_id}, {"_id": 0})
            owner = await db.owners.find_one({"id": owner_a}, {"_id": 0})
            copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0})

            pdf = build_decompte_pdf(
                owner=owner, copropriete=copro, fiscal_year=fy,
                owner_lots=owner_lots, all_lots=all_lots,
                invoices=invoices, distribution_keys=dks,
                fund_calls=[], payments=[], expense_accounts_map={"601000": "Eau"},
                meters=meters, meter_readings=readings,
            )
            assert isinstance(pdf, bytes) and len(pdf) > 500

            # Verifier via extraction PDF : le total impute a A doit etre ~32.14
            import fitz
            doc = fitz.open(stream=pdf, filetype="pdf")
            all_text = ""
            for page in doc:
                all_text += page.get_text()
            doc.close()
            # 30 / (30+10+333.34) * 400 = 32.14
            # Format EUR : "32,14 EUR" (formatage FR avec virgule)
            assert ("32,14 EUR" in all_text or "32.14 EUR" in all_text or "32,14" in all_text), \
                f"Attendu 32.14 EUR dans le PDF, contenu : {all_text[:500]}"
        finally:
            for coll in ["coproprietes", "lots", "meters", "meter_readings",
                         "distribution_keys", "fiscal_years", "owners", "invoices"]:
                await db[coll].delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": owner_a})
            client.close()

    asyncio.run(_run())
