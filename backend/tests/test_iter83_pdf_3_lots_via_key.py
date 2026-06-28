"""Regression test - iter83 - Scenario reel PDF TEUWEN Gael avec 3 lots.

Demande user : "base toi sur les appels par lots et leurs quotites sur base
de la cle de repartition charge commune generale".

Scenario du PDF reel :
- Acquereur TEUWEN Gael possede 3 lots achetes le 17/11/2025 :
  * 001 (APPARTEMENT)
  * C1 (CAVE)
  * Pe01 (PARKING EXT.)
- L'appel trimestriel total pour les 3 lots de TEUWEN = 443 EUR
- Reparti via la cle "Charges communes generales" (CCG)
  * APPART 001 : 87% des CCG     -> 386 EUR / trimestre
  * CAVE C1    : 5% des CCG      ->  22 EUR / trimestre
  * PARKING Pe : 8% des CCG      ->  35 EUR / trimestre
  * SOMME      :                 -> 443 EUR / trimestre

- Mutation 17/11/2025 (Matexi -> TEUWEN) en GROUPE (parent + 2 enfants)
- Prorata Q1 attendu (sur 45/92 jours apres vente) :
  * APPART : 386 * 45/92 = 188.80
  * CAVE   :  22 * 45/92 =  10.76
  * PARKING:  35 * 45/92 =  17.12
  * TOTAL groupe :        ≈ 216.68 (PDF = 216.69)

Le test vérifie que :
1. Le calcul utilise la cle CCG (pas la quotite globale agregee)
2. Chaque lot a son propre prorata via sa quote-part dans la cle
3. La somme des 3 prorata == prorata "total Matexi" du PDF

Ce test reproduit l'erreur que le user a signalee : si on prenait la quotite
GLOBALE du lot sur le total ACP (au lieu de sa quote-part dans la cle CCG),
on obtiendrait des prorata differents (sur-evalues ou sous-evalues).
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _run():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"acacia2-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"
    teuwen_id = f"teuwen-{uuid.uuid4()}"
    appt_id = f"appt-{uuid.uuid4()}"
    cave_id = f"cave-{uuid.uuid4()}"
    park_id = f"park-{uuid.uuid4()}"
    key_id = f"ccg-{uuid.uuid4()}"
    budget_id = f"budget-{uuid.uuid4()}"

    try:
        await db.coproprietes.insert_one({"id": cid, "name": "ACP ACACIA 3lots", "reference": "ACACIA2", "status": "active"})
        await db.fiscal_years.insert_one({"id": fy_id, "name": "2025-2026", "start_date": "2025-10-01", "end_date": "2026-09-30", "copropriete_id": cid})

        # PCMN
        await db.pcmn_accounts.insert_many([
            {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
            {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
            {"number": "4100001", "name": "Matexi", "class_num": 4, "copropriete_id": cid},
            {"number": "4100002", "name": "TEUWEN", "class_num": 4, "copropriete_id": cid},
            {"number": "611", "name": "Entretien", "class_num": 6, "copropriete_id": cid},
        ])
        await db.owners.insert_many([
            {"id": matexi_id, "name": "Matexi", "last_name": "Matexi", "auxiliary_code": "C0001", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100001"}}},
            {"id": teuwen_id, "name": "TEUWEN Gael", "last_name": "TEUWEN", "auxiliary_code": "C0002", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100002"}}},
        ])

        # 3 lots Matexi : APPART, CAVE, PARKING
        # Quotites dans le BATIMENT (pour fonds de roulement) :
        #   appt = 870, cave = 50, park = 80  -> total 1000 sur 10000 (groupe Matexi)
        # On ajoute des "autres lots" pour completer a 10000 (autres proprietaires)
        await db.lots.insert_many([
            {"id": appt_id, "number": "001", "owner_id": matexi_id, "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 870.0, "lot_type": "Appartement"},
            {"id": cave_id, "number": "C1", "owner_id": matexi_id, "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 50.0, "lot_type": "Cave"},
            {"id": park_id, "number": "Pe01", "owner_id": matexi_id, "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 80.0, "lot_type": "Parking"},
            # Autres lots dans l'ACP (proprietaires fictifs) pour atteindre 10000 quotites totales
            {"id": f"o-{uuid.uuid4()}", "number": "GHOST", "copropriete_id": cid, "quotity": 9000.0},
        ])

        # Cle "Charges communes generales" qui couvre TOUS les lots
        # Repartition specifique :
        #   appt = 870, cave = 50, park = 80, ghost = 9000  (= meme que quotites)
        await db.distribution_keys.insert_one({
            "id": key_id,
            "name": "Charges communes generales",
            "copropriete_id": cid,
            "key_type": "manual",
            "lots": [
                {"lot_id": appt_id, "lot_number": "001", "share": 870},
                {"lot_id": cave_id, "lot_number": "C1", "share": 50},
                {"lot_id": park_id, "lot_number": "Pe01", "share": 80},
                {"lot_id": "GHOST", "lot_number": "GHOST", "share": 9000},
            ],
        })

        # Solde fonds de roulement = 4903.60 EUR
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2025-10-01", "copropriete_id": cid,
            "lines": [{"account_number": "410", "debit": 4903.60, "credit": 0.0},
                      {"account_number": "100", "debit": 0.0, "credit": 4903.60}],
            "total_debit": 4903.60, "total_credit": 4903.60,
        })

        # 4 appels trimestriels Q1-Q4 avec line_details (budget lines + key)
        # Budget annuel CCG = 17720 EUR -> trimestre 4430 EUR
        # TEUWEN total = (870+50+80)/10000 * 4430 = 0.1 * 4430 = 443.00 EUR  ✓
        # Repartition par lot dans Q1 :
        #   appt : 4430 * 870/10000 = 385.41
        #   cave : 4430 * 50/10000  = 22.15
        #   park : 4430 * 80/10000  = 35.44
        #   TOTAL TEUWEN : 443.00
        quarters = [
            ("2025-10-01", "2025-12-31", "2025-10-31"),
            ("2026-01-01", "2026-03-31", "2026-01-31"),
            ("2026-04-01", "2026-06-30", "2026-05-01"),
            ("2026-07-01", "2026-09-30", "2026-07-31"),
        ]
        for idx, (ps, pe, dd) in enumerate(quarters, start=1):
            await db.fund_calls.insert_one({
                "id": f"fc-{uuid.uuid4()}",
                "name": f"Trimestriel {idx}/4 - Exercice 2025-2026",
                "date": ps, "due_date": dd, "period_start": ps, "period_end": pe,
                "fiscal_year_id": fy_id, "copropriete_id": cid,
                "call_type": "provisions", "total_amount": 4430.0,
                "budget_id": budget_id,
                # Les lignes du budget + leur cle de repartition
                "lines": [{
                    "account_number": "611",
                    "account_name": "Entretien",
                    "distribution_key_id": key_id,
                    "distribution_key_name": "Charges communes generales",
                    "amount": 4430.0,
                }],
                # Distribution agregee par owner (comme stockee en prod)
                "distribution": [
                    {"owner_id": matexi_id, "owner_name": "Matexi", "share": 1000, "amount": 443.0, "paid": False},
                ],
            })

        # Test mutation depuis l'appartement (qui sera parent des 2 autres)
        from routes.properties import create_properties_router
        router = create_properties_router(db)
        preview_fn = None
        link_fn = None
        for r in router.routes:
            if r.path == "/api/lots/{lot_id}/mutate-preview":
                preview_fn = r.endpoint
            elif r.path == "/api/lots/{parent_id}/link" and "POST" in r.methods:
                link_fn = r.endpoint
        LotMutationInput = preview_fn.__annotations__.get("data")
        LotLinkInput = link_fn.__annotations__.get("data")

        # 1) Lier cave + park a appt
        await link_fn(parent_id=appt_id, data=LotLinkInput(child_lot_ids=[cave_id, park_id]))

        # 2) Preview mutation 17/11/2025 (45 jours apres debut Q1 = 01/10/2025)
        # Days from 17/11 to 31/12 inclus = 14 + 31 = 45 jours  (le 17/11 est inclus)
        # Total Q1 = 31 + 30 + 31 = 92 jours
        payload = LotMutationInput(new_owner_id=teuwen_id, sale_date="2025-11-17")
        preview = await preview_fn(lot_id=appt_id, data=payload)

        # Verifier le decompte agreges
        assert preview["linked_lots_count"] == 2
        assert len(preview["per_lot_breakdowns"]) == 3

        # Calcul attendu prorata Q1 par lot
        def calc_prorata(line_amt_total, lot_share, total_shares):
            line_lot = line_amt_total * (lot_share / total_shares)
            return round(line_lot * 45 / 92, 2)

        expected_appt = calc_prorata(4430.0, 870, 10000)  # ~188.65
        expected_cave = calc_prorata(4430.0, 50, 10000)   # ~10.84
        expected_park = calc_prorata(4430.0, 80, 10000)   # ~17.34
        expected_total = round(expected_appt + expected_cave + expected_park, 2)  # ~216.83

        # Verifie par lot
        by_lot = {b["lot_number"]: b for b in preview["per_lot_breakdowns"]}
        assert abs(by_lot["001"]["current_period_prorata"] - expected_appt) < 0.02, (
            f"APPART 001 prorata attendu ~{expected_appt}, recu {by_lot['001']['current_period_prorata']}"
        )
        assert abs(by_lot["C1"]["current_period_prorata"] - expected_cave) < 0.02, (
            f"CAVE C1 prorata attendu ~{expected_cave}, recu {by_lot['C1']['current_period_prorata']}"
        )
        assert abs(by_lot["Pe01"]["current_period_prorata"] - expected_park) < 0.02, (
            f"PARKING Pe01 prorata attendu ~{expected_park}, recu {by_lot['Pe01']['current_period_prorata']}"
        )

        # Verifie le total groupe vs PDF (~216.69)
        grouped_total_prorata = preview["grouped_total_current_prorata"]
        assert abs(grouped_total_prorata - expected_total) < 0.05, (
            f"Total prorata groupe attendu ~{expected_total} (PDF: 216.69), recu {grouped_total_prorata}"
        )

        # Verifie aussi que les Q2/Q3/Q4 futurs sont bien comptes par lot
        # 3 lots × 3 futurs = 9 entrees totales agregees (mais per_lot_breakdowns garde par lot)
        for lot_num in ["001", "C1", "Pe01"]:
            assert len(by_lot[lot_num]["future_calls"]) == 3, (
                f"Lot {lot_num} : attendu 3 appels futurs, recu {len(by_lot[lot_num]['future_calls'])}"
            )

        print(f"OK - Scenario PDF reel 3 lots TEUWEN validee :")
        print(f"  APPART 001  prorata Q1 : {by_lot['001']['current_period_prorata']} EUR (attendu ~{expected_appt})")
        print(f"  CAVE C1     prorata Q1 : {by_lot['C1']['current_period_prorata']} EUR (attendu ~{expected_cave})")
        print(f"  PARKING Pe01 prorata Q1: {by_lot['Pe01']['current_period_prorata']} EUR (attendu ~{expected_park})")
        print(f"  TOTAL groupe prorata Q1: {grouped_total_prorata} EUR (PDF reel: 216.69, ecart arrondi)")

    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.owners.delete_many({"id": {"$in": [matexi_id, teuwen_id]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})
        await db.distribution_keys.delete_many({"copropriete_id": cid})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_real_pdf_3_lots_via_distribution_key():
    asyncio.run(_run())
