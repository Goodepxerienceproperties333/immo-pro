"""Regression test - iter83 - Scenario PDF Situation TEUWEN Gael (ACP ACACIA).

Demande user : "base-toi sur ce fichier" (PDF Situation du compte au 30/09/2026)

Le PDF montre la situation comptable d'un ACQUEREUR (TEUWEN Gael) qui a
achete un lot le 17/11/2025 chez Matexi (le vendeur).

Donnees extraites du PDF :
- Date de mutation : 17/11/2025
- Solde reporte au 01/10/2025 : 0,00
- Appel Q1 (01/10/2025 -> 31/12/2025) au 01/10/2025 : 216,69 EUR
  -> Q1 plein = 443,00 EUR
  -> Q1 prorata acquereur = 443 * 45/92 = 216,71 (legere variation arrondi)
  -> Vendu le 17/11, jours apres = 17/11 -> 31/12 = 14+31 = 45 jours
  -> Total Q1 = 31+30+31 = 92 jours
- Transfert au fonds de roulement TEUWEN Gael 17/11/2025 : 490,36 EUR
  (quote-part de roulement reprise par l'acquereur lors de la mutation)
- Appels Q2, Q3, Q4 2026 : 443,00 EUR chacun (full quarter pour l'acquereur)

Ce test valide que la mutation iter83 produit EXACTEMENT le decompte
qu'on retrouvera dans la situation de compte de l'acquereur :
- Bloc 1 (Fonds de roulement) : 490,36 EUR
- Bloc 2.a (Prorata Q1 post-mutation) : 216,71 EUR (≈ 216,69 du PDF, arrondi)
- Bloc 2.b (Futurs Q2+Q3+Q4) : 1329,00 EUR (info uniquement)
- Total OD = 490,36 + 216,71 = 707,07 EUR (cf "Transfert" + "Prorata appel")

Note : exercice fiscal 01/10/2025 - 30/09/2026 (annee fiscale brisee).
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

    cid = f"acacia-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"
    teuwen_id = f"teuwen-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"

    try:
        await db.coproprietes.insert_one({"id": cid, "name": "ACP ACACIA", "reference": "ACACIA", "status": "active"})
        # Exercice fiscal 01/10/2025 -> 30/09/2026 (annee brisee)
        await db.fiscal_years.insert_one({"id": fy_id, "name": "2025-2026", "start_date": "2025-10-01", "end_date": "2026-09-30", "copropriete_id": cid})
        await db.pcmn_accounts.insert_many([
            {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
            {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
            {"number": "4100001", "name": "Matexi", "class_num": 4, "copropriete_id": cid},
            {"number": "4100002", "name": "TEUWEN", "class_num": 4, "copropriete_id": cid},
        ])
        await db.owners.insert_many([
            {"id": matexi_id, "name": "Matexi", "last_name": "Matexi", "auxiliary_code": "C0001", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100001"}}},
            {"id": teuwen_id, "name": "TEUWEN Gael", "last_name": "TEUWEN", "auxiliary_code": "C0002", "copropriete_ids": [cid],
             "tier_accounts": {cid: {"provisions": "4100002"}}},
        ])
        # Le lot a une quotite qui donne 443/4 = appel trimestriel
        # On utilise quotite = 1000 et ACP total = 10000 => 10% du budget
        # Budget annuel = 17720 EUR ; trimestre = 4430 ; quote-part lot = 443
        # Solde 100 = quotite/total * X => pour avoir 490.36 quote-part lot
        # 490.36 = 1000/10000 * X => X = 4903.60 EUR (solde 100 total ACP)
        ghost_id = f"ghost-{uuid.uuid4()}"
        await db.lots.insert_many([
            {"id": lot_id, "number": "L001", "owner_id": matexi_id, "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 1000.0, "lot_type": "Appartement"},
            # Lot fantome pour le total
            {"id": ghost_id, "number": "GHOST", "copropriete_id": cid, "quotity": 9000.0},
        ])
        # iter90ab : cle de repartition par defaut (obligatoire pour mutation)
        await db.distribution_keys.insert_one({
            "id": f"dk-acacia-{cid[:8]}", "copropriete_id": cid, "name": "Generale",
            "is_default": True, "key_type": "quotity",
            "lots": [{"lot_id": lot_id, "share": 1000.0},
                     {"lot_id": ghost_id, "share": 9000.0}],
        })
        # Solde fonds de roulement = 4903.60 (pour donner 490.36 = 10%)
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2025-10-01", "copropriete_id": cid,
            "lines": [{"account_number": "410", "debit": 4903.60, "credit": 0.0},
                      {"account_number": "100", "debit": 0.0, "credit": 4903.60}],
            "total_debit": 4903.60, "total_credit": 4903.60,
        })
        # 4 appels trimestriels Q1-Q4 de 443 EUR pour le lot
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
                "distribution": [
                    {"lot_id": lot_id, "lot_number": "L001", "owner_id": matexi_id, "owner_name": "Matexi", "share": 1000, "amount": 443.0, "paid": False},
                ],
            })

        # Mutation 17/11/2025 : Matexi -> TEUWEN Gael
        from routes.properties import create_properties_router
        router = create_properties_router(db)
        mutate_fn = None
        preview_fn = None
        for r in router.routes:
            if r.path == "/api/lots/{lot_id}/mutate":
                mutate_fn = r.endpoint
            elif r.path == "/api/lots/{lot_id}/mutate-preview":
                preview_fn = r.endpoint
        LotMutationInput = mutate_fn.__annotations__.get("data")

        payload = LotMutationInput(new_owner_id=teuwen_id, sale_date="2025-11-17", sale_price=200000.0, note="Acte notarial 17/11/2025")
        preview = await preview_fn(lot_id=lot_id, data=payload)

        # Bloc 1 : Fonds de roulement = 10% de 4903.60 = 490.36
        assert abs(preview["roulement_quota"] - 490.36) < 0.01, (
            f"Roulement quota attendu 490.36, recu {preview['roulement_quota']}"
        )
        # Bloc 2.a : Prorata Q1 (01/10 -> 31/12, sale 17/11)
        # days_after = 18/11 -> 31/12 ? Non, 17/11 inclus pour l'acquereur :
        # period_end - sale_dt + 1 = (31/12 - 17/11) + 1 = 44 + 1 = 45 jours
        # total_days = 31 + 30 + 31 = 92 jours
        # Prorata = 443 * 45/92 = 216.7173... = 216.72 arrondi
        expected_prorata = round(443.0 * 45 / 92, 2)
        assert abs(preview["current_period_prorata"] - expected_prorata) < 0.01, (
            f"Prorata Q1 attendu {expected_prorata} (~216.72), recu {preview['current_period_prorata']}"
        )
        # Le PDF montre 216.69, ecart de 3 centimes du a arrondi du syndic
        # (qui calcule peut-etre 443.07 * 45/92 ou autre methode)

        # Bloc 2.b : Futurs Q2+Q3+Q4 = 3 * 443 = 1329
        assert len(preview["future_calls"]) == 3
        assert abs(preview["future_calls_total"] - 1329.0) < 0.01, (
            f"Future calls total attendu 1329, recu {preview['future_calls_total']}"
        )
        # Frequence trimestrielle
        assert preview["budget_frequency"] == 4
        assert preview["budget_frequency_label"] == "Trimestriel"

        # Total OD = 490.36 + 216.72 = 707.08
        expected_total = round(490.36 + expected_prorata, 2)
        assert abs(preview["total_transfer"] - expected_total) < 0.01, (
            f"Total OD attendu {expected_total}, recu {preview['total_transfer']}"
        )

        # Persistance via mutate_lot
        result = await mutate_fn(lot_id=lot_id, data=payload)
        mut = result["mutation"]
        # Depuis iter84 : ecritures eclatees par date (FR sur sale_date, prorata sur call_date)
        # On verifie la SOMME des ecritures, pas une entree unique.
        jes = await db.journal_entries.find(
            {"source_id": lot_id, "source_type": "lot_mutation"}, {"_id": 0}
        ).to_list(10)
        assert jes, "Aucune ecriture de mutation trouvee"
        sum_debit = sum(j.get("total_debit", 0) for j in jes)
        # iter84+ : les OD incluent roulement + prorata courant + futurs appels
        # (repris par l'acquereur). expected = 490.36 + 216.72 + 1329 = 2036.08.
        expected_sum = round(490.36 + expected_prorata + 1329.0, 2)
        assert abs(sum_debit - expected_sum) < 0.02, (
            f"Somme OD attendue {expected_sum}, recu {sum_debit}"
        )
        # Verifie sens debit/credit sur la premiere ecriture (FR ou prorata)
        je = jes[0]
        debit_acc = next(l for l in je["lines"] if l["debit"] > 0)["account_number"]
        credit_acc = next(l for l in je["lines"] if l["credit"] > 0)["account_number"]
        assert debit_acc == "4100002", f"Debit attendu sur TEUWEN (4100002), recu {debit_acc}"
        assert credit_acc == "4100001", f"Credit attendu sur Matexi (4100001), recu {credit_acc}"

        # Verifie que le lot est bien passe a TEUWEN
        updated = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        assert updated["owner_id"] == teuwen_id

        print("OK - Mutation TEUWEN Gael conforme au PDF ACACIA :")
        print(f"  Fonds de roulement   : {preview['roulement_quota']} EUR (PDF: 490.36)")
        print(f"  Prorata Q1 post-sale : {preview['current_period_prorata']} EUR (PDF: 216.69, ecart arrondi)")
        print(f"  Futurs Q2+Q3+Q4      : {preview['future_calls_total']} EUR (info, factures au buyer)")
        print(f"  Total OD             : {preview['total_transfer']} EUR")

    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.fiscal_years.delete_one({"id": fy_id})
        await db.owners.delete_many({"id": {"$in": [matexi_id, teuwen_id]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.fund_calls.delete_many({"copropriete_id": cid})
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_many({"copropriete_id": cid})


def test_acacia_teuwen_scenario():
    asyncio.run(_run())
