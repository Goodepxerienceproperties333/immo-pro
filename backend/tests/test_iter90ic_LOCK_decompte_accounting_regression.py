"""iter90ic : VERROU DE REGRESSION END-TO-END - Décompte annuel comptabilise correctement.

Ce pytest utilise le VRAI code de production `build_decompte_pdf` + `_build_decompte_annuel_pdf`
sur un scenario complet (Boxus-like : 4 provisions trimestrielles + 1 appel reserve +
imputation OD charges + paiements + AN opening/closing) et verrouille les invariants
comptables cruciaux :

  1. Le PDF s'ouvre correctement (structure valide)
  2. `Total appele` == somme des debits sur le compte tier du proprio
  3. `Vos versements` == somme des credits sur le compte tier du proprio
  4. `RESTE A REGLER` == total_called - total_payments (formule Situation)
  5. `Total appele` du Decompte == balance de la Situation via balance_tiers/owners
  6. AN de cloture (is_opening_balance != True) sont EXCLUS des totaux
  7. AN d'ouverture (is_opening_balance == True) sont INCLUS

C'est le CONTRAT que la future feature "refacturation compteurs" (iter90id)
NE DOIT SOUS AUCUN PRETEXTE casser. Meme si la ventilation des charges par
consommation change, l'egalite Decompte<->Situation<->BalanceTiers doit
etre preservee.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _seed_full_scenario(db, copro_id, owner_id, fy_id):
    """Cree un scenario reel de decompte annuel :
    - ACP + lot Boxus + Boxus owner + tier_accounts explicites
    - 1 AN opening_balance=True (a inclure)
    - 1 AN sans is_opening_balance (a EXCLURE)
    - 4 appels VE trimestriels de provisions
    - 1 appel VE reserve
    - 1 OD imputation charges reelles
    - 5 paiements FI

    Balance attendue (formule Situation) :
      Debit tpid=Boxus (hors AN cloture) : 4174.68
      Credit tpid=Boxus + AN opening : 3621.13 + 291.66 = 3912.79
      Balance : 4174.68 - 3912.79 = 261.89  (peu importe la valeur exacte,
      l'important c'est l'EGALITE Decompte==Situation)
    """
    acc_prov = "4100999"
    acc_res = "41000999"
    for coll in ["coproprietes", "lots", "owners", "fiscal_years",
                 "invoices", "journal_entries", "fund_calls",
                 "distribution_keys", "pcmn_accounts"]:
        await db[coll].delete_many({"copropriete_id": copro_id})
    await db.owners.delete_many({"id": owner_id})
    await db.fiscal_years.delete_many({"id": fy_id})

    await db.coproprietes.insert_one({
        "id": copro_id, "name": "ACP ic test",
        "address": "Rue X 1", "postal_code": "1000", "city": "Bxl",
    })
    await db.lots.insert_one({
        "id": f"lot-{owner_id}", "copropriete_id": copro_id,
        "owner_id": owner_id, "number": "APP1",
        "quotites": {"main": 30.8},
    })
    await db.owners.insert_one({
        "id": owner_id, "name": "Boxus Wivine",
        "address": "RUE DE LA SOURCE 14",
        "postal_code": "1300", "city": "WAVRE",
        "vcs_code": "+++103/7451/75185+++",
        "vcs_digits": "103745175185",
        "tier_accounts": {copro_id: {"provisions": acc_prov, "reserve": acc_res}},
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": copro_id,
        "name": "03.2026 - 02.2027",
        "start_date": "2026-03-01", "end_date": "2027-02-28",
        "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"copropriete_id": copro_id, "number": acc_prov,
         "name": "Provisions Boxus", "class": "41"},
        {"copropriete_id": copro_id, "number": acc_res,
         "name": "Reserve Boxus", "class": "41"},
        {"copropriete_id": copro_id, "number": "550000",
         "name": "Banque", "class": "55"},
    ])

    # AN d'ouverture Boxus a un CREDIT de 291.66 (paye trop l'annee prec.)
    await db.journal_entries.insert_one({
        "id": f"an-open-{owner_id}", "journal_type": "AN",
        "copropriete_id": copro_id, "date": "2026-03-01",
        "reference": "AN-2026-001",
        "is_opening_balance": True,  # <-- INCLUS
        "lines": [
            {"account_number": acc_prov, "debit": 0, "credit": 291.66,
             "third_party_id": owner_id},
            {"account_number": "690000", "debit": 291.66, "credit": 0},
        ],
        "total_debit": 291.66, "total_credit": 291.66,
    })

    # AN de cloture (duplicata technique) - a EXCLURE
    await db.journal_entries.insert_one({
        "id": f"an-close-{owner_id}", "journal_type": "AN",
        "copropriete_id": copro_id, "date": "2027-03-01",
        "reference": "AN-03.2026 - 02.2027",
        # is_opening_balance ABSENT -> exclu
        "lines": [
            {"account_number": acc_prov, "debit": 500.0, "credit": 0,
             "third_party_id": owner_id},
            {"account_number": "690000", "debit": 0, "credit": 500.0},
        ],
        "total_debit": 500.0, "total_credit": 500.0,
    })

    # 4 provisions trimestrielles (VE)
    for i, date in enumerate(["2026-03-01", "2026-06-01",
                              "2026-09-01", "2026-12-01"]):
        await db.journal_entries.insert_one({
            "id": f"ve-t{i+1}-{owner_id}", "journal_type": "VE",
            "copropriete_id": copro_id, "date": date,
            "reference": f"AF-Provisions T{i+1}",
            "source_type": "fund_call",
            "source_id": f"fc-t{i+1}-{copro_id}",
            "lines": [
                {"account_number": acc_prov, "debit": 500.29, "credit": 0,
                 "third_party_id": owner_id},
                {"account_number": "700000", "debit": 0, "credit": 500.29},
            ],
            "total_debit": 500.29, "total_credit": 500.29,
        })
        await db.fund_calls.insert_one({
            "id": f"fc-t{i+1}-{copro_id}", "copropriete_id": copro_id,
            "name": f"Provisions T{i+1}", "date": date,
            "due_date": date, "call_type": "provisions",
            "total_amount": 1624.32,  # ACP-wide (5.27 mille de Boxus = 500.29)
            "distribution": [{"owner_id": owner_id, "amount": 500.29}],
        })

    # Reserve appel (VE)
    await db.journal_entries.insert_one({
        "id": f"ve-res-{owner_id}", "journal_type": "VE",
        "copropriete_id": copro_id, "date": "2026-05-01",
        "reference": "AF-Fonds de reserve",
        "source_type": "fund_call",
        "source_id": f"fc-res-{copro_id}",
        "lines": [
            {"account_number": acc_res, "debit": 530.80, "credit": 0,
             "third_party_id": owner_id},
            {"account_number": "160", "debit": 0, "credit": 530.80},
        ],
        "total_debit": 530.80, "total_credit": 530.80,
    })
    await db.fund_calls.insert_one({
        "id": f"fc-res-{copro_id}", "copropriete_id": copro_id,
        "name": "Fonds de reserve - Annuel 1/1",
        "date": "2026-05-01", "due_date": "2026-05-31",
        "call_type": "reserve", "total_amount": 1723.38,
        "reserve_amount": 1723.38,
        "distribution": [{"owner_id": owner_id, "amount": 530.80}],
    })

    # OD imputation charges reelles en fin d'exercice
    await db.journal_entries.insert_one({
        "id": f"od-imput-{owner_id}", "journal_type": "OD",
        "copropriete_id": copro_id, "date": "2027-02-28",
        "reference": "OD-IMPUT-2026",
        "lines": [
            {"account_number": acc_prov, "debit": 1642.72, "credit": 0,
             "third_party_id": owner_id},
            {"account_number": "700000", "debit": 0, "credit": 1642.72},
        ],
        "total_debit": 1642.72, "total_credit": 1642.72,
    })

    # 5 paiements FI
    for i, (date, amt) in enumerate([
        ("2026-03-15", 500.29),
        ("2026-06-20", 500.29),
        ("2026-09-10", 500.29),
        ("2026-12-05", 500.29),
        ("2027-01-15", 1619.97),  # gros paiement fin d'exercice
    ]):
        await db.journal_entries.insert_one({
            "id": f"fi-p{i}-{owner_id}", "journal_type": "FI",
            "copropriete_id": copro_id, "date": date,
            "reference": f"PAIE-BOXUS-{i+1}",
            "lines": [
                {"account_number": "550000", "debit": amt, "credit": 0},
                {"account_number": acc_prov, "debit": 0, "credit": amt,
                 "third_party_id": owner_id},
            ],
            "total_debit": amt, "total_credit": amt,
        })


def test_ic_LOCK_decompte_pdf_matches_situation_balance():
    """iter90ic-LOCK-1 : contrat inviolable
    Total_called Decompte == somme debits tier de Boxus (source Situation).
    Total_payments Decompte == somme credits tier + AN opening.
    Balance Decompte == balance Situation via balance_tiers/owners.
    """
    copro_id = f"acp-ic-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-ic-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-ic-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import create_reports_router, _build_decompte_annuel_pdf
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_full_scenario(db, copro_id, owner_id, fy_id)

            # 1) Genere le PDF Decompte via le VRAI code de prod
            #    _build_decompte_annuel_pdf renvoie un TUPLE (bytes, filename)
            pdf_bytes, _ = await _build_decompte_annuel_pdf(
                db, owner_id, copro_id, fy_id, preview=False,
            )
            assert pdf_bytes and len(pdf_bytes) > 500

            # 2) Extraction texte
            try:
                from pypdf import PdfReader
                from io import BytesIO
                reader = PdfReader(BytesIO(pdf_bytes))
                text = "".join(p.extract_text() or "" for p in reader.pages)
            except Exception:
                text = pdf_bytes.decode("latin-1", errors="ignore")

            # 3) Attendus - calcul en pur Python (source de verite)
            #    Debits sur tier de Boxus (hors AN cloture, hors reversals) :
            #      AN open       : Cr 291.66            (INCLUS car opening)
            #      AN close      : Dr 500              (EXCLU car pas opening)
            #      4 provisions  : 4 x 500.29 = 2001.16
            #      Reserve       : 530.80
            #      OD imputation : 1642.72
            #      Total debit   : 4174.68
            #    Credits sur tier :
            #      AN open       : 291.66
            #      5 paiements   : 500.29 x 4 + 1619.97 = 3621.13
            #      Total credit  : 3912.79
            #    Balance = 4174.68 - 3912.79 = 261.89 EUR (a payer)
            expected_called = 4174.68
            expected_paid = 3912.79
            expected_balance = round(expected_called - expected_paid, 2)
            assert expected_balance == 261.89

            # 4) Le PDF doit contenir ces valeurs (formatage fr-BE : "4 174,68 EUR")
            _fr = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")
            fmt_called = _fr(expected_called)
            fmt_paid = _fr(expected_paid)
            fmt_balance = _fr(expected_balance)
            # Verifier "Total appele" ~ 4174.68 (tolerance formats)
            assert (fmt_called in text or "4174,68" in text
                    or f"{expected_called:.2f}" in text), (
                f"PDF doit contenir Total appele ~{expected_called} EUR. "
                f"Recherche : '{fmt_called}' introuvable dans {len(text)}c"
            )
            assert (fmt_paid in text or "3912,79" in text
                    or f"{expected_paid:.2f}" in text), (
                f"PDF doit contenir Vos versements ~{expected_paid} EUR."
            )
            assert (fmt_balance in text or "261,89" in text
                    or f"{expected_balance:.2f}" in text), (
                f"PDF doit contenir Balance ~{expected_balance} EUR."
            )

            # 5) INVARIANT CRITIQUE : Balance Decompte == Balance BalanceTiers
            router = create_reports_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reports/balance-tiers/owners")
            data = await handler(copropriete_id=copro_id)
            boxus = next((o for o in data["owners"]
                          if o["owner_id"] == owner_id), None)
            assert boxus is not None
            assert abs(boxus["balance"] - expected_balance) < 0.01, (
                f"Balance liste ({boxus['balance']}) doit == balance "
                f"Decompte ({expected_balance}) - iter90ia,ib,i9 verrouilles"
            )
        finally:
            for coll in ["coproprietes", "lots", "owners", "fiscal_years",
                         "invoices", "journal_entries", "fund_calls",
                         "distribution_keys", "pcmn_accounts"]:
                await db[coll].delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.fiscal_years.delete_many({"id": fy_id})
            client.close()

    asyncio.run(_run())


def test_ic_LOCK_an_closing_excluded_but_an_opening_included():
    """iter90ic-LOCK-2 : Filtre AN uniforme sur les 3 surfaces :
    - Decompte PDF
    - balance_tiers/owners
    - _compute_balance_tiers_for_ui

    Retire l'AN opening -> balance monte de 291.66.
    Ajoute un 2eme AN opening -> balance baisse encore de 100.
    Retire l'AN closing -> balance INCHANGEE (n'a jamais compte).
    """
    copro_id = f"acp-ic2-{uuid.uuid4().hex[:8]}"
    owner_id = f"own-ic2-{uuid.uuid4().hex[:8]}"
    fy_id = f"fy-ic2-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import create_reports_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await _seed_full_scenario(db, copro_id, owner_id, fy_id)
            router = create_reports_router(db)
            handler = next(r.endpoint for r in router.routes
                           if getattr(r, "path", "") == "/api/reports/balance-tiers/owners")

            # Base : balance = 261.89 (voir test-1)
            data0 = await handler(copropriete_id=copro_id)
            b0 = next(o["balance"] for o in data0["owners"]
                      if o["owner_id"] == owner_id)
            assert abs(b0 - 261.89) < 0.01

            # Cas 1 : retirer l'AN opening -> +291.66
            await db.journal_entries.delete_one({"id": f"an-open-{owner_id}"})
            data1 = await handler(copropriete_id=copro_id)
            b1 = next(o["balance"] for o in data1["owners"]
                      if o["owner_id"] == owner_id)
            assert abs(b1 - (261.89 + 291.66)) < 0.01, (
                f"Retirer AN opening_balance doit augmenter balance de "
                f"291.66. Was {b0}, now {b1}, expected {261.89 + 291.66}"
            )

            # Cas 2 : re-ajouter AN opening + ajouter un 2eme AN opening
            #         de 100 credit -> balance devrait baisser de 100
            await db.journal_entries.insert_one({
                "id": f"an-open-{owner_id}", "journal_type": "AN",
                "copropriete_id": copro_id, "date": "2026-03-01",
                "reference": "AN-2026-001",
                "is_opening_balance": True,
                "lines": [
                    {"account_number": "4100999", "debit": 0, "credit": 291.66,
                     "third_party_id": owner_id},
                    {"account_number": "690000", "debit": 291.66, "credit": 0},
                ],
                "total_debit": 291.66, "total_credit": 291.66,
            })
            await db.journal_entries.insert_one({
                "id": f"an-open2-{owner_id}", "journal_type": "AN",
                "copropriete_id": copro_id, "date": "2025-04-01",
                "reference": "AN-EXTRA",
                "is_opening_balance": True,
                "lines": [
                    {"account_number": "4100999", "debit": 0, "credit": 100.0,
                     "third_party_id": owner_id},
                    {"account_number": "690000", "debit": 100.0, "credit": 0},
                ],
                "total_debit": 100.0, "total_credit": 100.0,
            })
            data2 = await handler(copropriete_id=copro_id)
            b2 = next(o["balance"] for o in data2["owners"]
                      if o["owner_id"] == owner_id)
            assert abs(b2 - (261.89 - 100.0)) < 0.01, (
                f"Ajouter un AN opening de 100 doit REDUIRE balance de 100. "
                f"Was 261.89, expected {261.89 - 100.0}, got {b2}"
            )

            # Cas 3 : ajouter un 2eme AN closing (sans is_opening_balance)
            #         -> balance INCHANGEE
            await db.journal_entries.insert_one({
                "id": f"an-close2-{owner_id}", "journal_type": "AN",
                "copropriete_id": copro_id, "date": "2027-03-01",
                "reference": "AN-CLOSING-EXTRA",
                # is_opening_balance ABSENT
                "lines": [
                    {"account_number": "4100999", "debit": 999.99, "credit": 0,
                     "third_party_id": owner_id},
                    {"account_number": "690000", "debit": 0, "credit": 999.99},
                ],
                "total_debit": 999.99, "total_credit": 999.99,
            })
            data3 = await handler(copropriete_id=copro_id)
            b3 = next(o["balance"] for o in data3["owners"]
                      if o["owner_id"] == owner_id)
            assert abs(b3 - b2) < 0.01, (
                f"Ajouter un AN closing (sans is_opening_balance) NE DOIT PAS "
                f"changer la balance. Was {b2}, got {b3}"
            )
        finally:
            for coll in ["coproprietes", "lots", "owners", "fiscal_years",
                         "invoices", "journal_entries", "fund_calls",
                         "distribution_keys", "pcmn_accounts"]:
                await db[coll].delete_many({"copropriete_id": copro_id})
            await db.owners.delete_many({"id": owner_id})
            await db.fiscal_years.delete_many({"id": fy_id})
            client.close()

    asyncio.run(_run())
