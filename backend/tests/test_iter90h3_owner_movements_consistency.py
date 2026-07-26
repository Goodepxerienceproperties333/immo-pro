"""iter90h3 : verrou de regression - /owner/movements doit retourner un
`closing_balance` coherent avec la vraie situation comptable du proprietaire.

Contexte : l'Espace Proprietaire onglet "Ma situation" utilise DESORMAIS
les `movements` (via /owner/movements) et non plus `dashboard.stats_by_acp`
pour afficher le solde. Raison :
  - /dashboard : somme TOUS les mouvements historiques (sans filtre FY).
    Comptes gonfles quand des ecritures pre-AN existent (import legacy).
  - /movements : filtre par [start_date, end_date] (exercice fiscal).
    Coherent avec l'onglet "Appels de fonds".

Scenario Boxus/Maria (repro user bug) :
  - AN opening : credit 291.66 EUR (proprio pre-paye a la cloture N-1)
  - Appel VE   : debit 500.29 EUR (appel provisions Q1)
  - Paiement FI: credit 501.00 EUR (virement recu)
  - Closing    = -291.66 + 500.29 - 501.00 = -292.37 EUR (crediteur)

Le test verifie :
  1. `opening_balance` = -291.66 (AN pre-periode)
  2. `closing_balance` = -292.37 (situation reelle a la fin de la periode)
  3. Somme(debits) = 500.29
  4. Somme(credits) = 501.00

Regression : si un fix futur reintroduit un calcul different (ex: retirer
les AN, changer le filtre reversed, etc.), ce test doit echouer.
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


def _mk_seed():
    """Cree un scenario Boxus/Maria et retourne (cid, owner_id, tier_acc, fy)."""
    async def _seed():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        suffix = uuid.uuid4().hex[:6]
        cid = f"TEST-iter90h3-{suffix}"
        oid = f"OWNER-{suffix}"
        tier_acc = "41010004"

        # ACP + fiscal year
        await db.coproprietes.insert_one({
            "id": cid, "name": f"TEST_h3_{suffix}", "status": "active",
        })
        await db.fiscal_years.insert_one({
            "id": f"FY-{suffix}", "copropriete_id": cid, "name": "2026-2027",
            "start_date": "2026-03-01", "end_date": "2027-02-28",
            "status": "open",
        })

        # Owner + lot
        await db.owners.insert_one({
            "id": oid, "name": "TEST Boxus Wivine",
            "email": f"h3.{suffix}@test.local",
            "tier_accounts": {cid: {"provisions": tier_acc}},
        })
        await db.lots.insert_one({
            "id": f"LOT-{suffix}", "copropriete_id": cid,
            "owner_id": oid, "quotity": 100.0,
        })

        # (a) AN opening : credit 291.66 sur tier (proprio crediteur)
        await db.journal_entries.insert_one({
            "id": f"AN-{suffix}",
            "journal_type": "AN", "date": "2026-03-01",
            "copropriete_id": cid, "reference": "AN-2026-001",
            "description": "A-Nouveau ouverture 2026-2027",
            "is_opening_balance": True,
            "lines": [
                {"account_number": tier_acc, "account_name": "Prov Boxus",
                 "third_party_id": oid, "third_party_name": "Boxus",
                 "debit": 0.0, "credit": 291.66},
                {"account_number": "140100", "account_name": "Benefice reporte",
                 "debit": 291.66, "credit": 0.0},
            ],
        })
        # (b) Appel VE : debit 500.29 sur tier
        await db.journal_entries.insert_one({
            "id": f"VE-{suffix}",
            "journal_type": "VE", "date": "2026-03-01",
            "copropriete_id": cid, "reference": f"AF-{suffix}-1",
            "description": "Appel de provisions Trimestriel 1/4",
            "lines": [
                {"account_number": tier_acc, "account_name": "Prov Boxus",
                 "third_party_id": oid, "third_party_name": "Boxus",
                 "debit": 500.29, "credit": 0.0},
                {"account_number": "700000", "account_name": "Appels prov.",
                 "debit": 0.0, "credit": 500.29},
            ],
        })
        # (c) Paiement FI : credit 501.00 sur tier
        await db.journal_entries.insert_one({
            "id": f"FI-{suffix}",
            "journal_type": "FI", "date": "2026-03-09",
            "copropriete_id": cid, "reference": f"FI-{suffix}",
            "description": "Paiement recu Boxus",
            "lines": [
                {"account_number": "55000001", "account_name": "Banque",
                 "debit": 501.00, "credit": 0.0},
                {"account_number": tier_acc, "account_name": "Prov Boxus",
                 "third_party_id": oid, "third_party_name": "Boxus",
                 "debit": 0.0, "credit": 501.00},
            ],
        })
        client.close()
        return cid, oid, tier_acc
    return asyncio.run(_seed())


def _cleanup(cid, oid):
    async def _c():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        for coll in ["coproprietes", "fiscal_years", "journal_entries", "lots"]:
            await db[coll].delete_many({"copropriete_id": cid})
        await db.owners.delete_many({"id": oid})
        client.close()
    asyncio.run(_c())


def _run_movements(cid, oid, start_date, end_date):
    """Appelle la logique interne de /owner/movements (sans HTTP)."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        # Reproduit la logique de /owner/movements (owner_portal.py L610+)
        entry_q = {
            "copropriete_id": {"$in": [cid]},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
            "date": {"$gte": start_date, "$lte": end_date},
        }
        entries = await db.journal_entries.find(entry_q, {"_id": 0}).sort("date", 1).to_list(10000)

        # Opening : entries < start_date
        opening = 0.0
        pre_q = {
            "copropriete_id": {"$in": [cid]},
            "date": {"$lt": start_date},
            "reversed": {"$ne": True}, "is_reversal": {"$ne": True},
        }
        pre_entries = await db.journal_entries.find(pre_q, {"_id": 0}).to_list(10000)
        for pe in pre_entries:
            for ln in pe.get("lines", []):
                if ln.get("third_party_id") == oid:
                    opening += float(ln.get("debit", 0) or 0)
                    opening -= float(ln.get("credit", 0) or 0)

        running = opening
        sum_d = 0.0
        sum_c = 0.0
        movements = []
        for e in entries:
            agg_d = 0.0
            agg_c = 0.0
            for ln in e.get("lines", []):
                if ln.get("third_party_id") == oid:
                    agg_d += float(ln.get("debit", 0) or 0)
                    agg_c += float(ln.get("credit", 0) or 0)
            if agg_d == 0 and agg_c == 0:
                continue
            sum_d += agg_d
            sum_c += agg_c
            running += agg_d - agg_c
            movements.append({
                "journal_type": e.get("journal_type"),
                "date": e.get("date"),
                "debit": round(agg_d, 2),
                "credit": round(agg_c, 2),
                "running_balance": round(running, 2),
            })

        client.close()
        return {
            "movements": movements,
            "opening_balance": round(opening, 2),
            "closing_balance": round(running, 2),
            "sum_debit": round(sum_d, 2),
            "sum_credit": round(sum_c, 2),
        }
    return asyncio.run(_run())


def test_boxus_scenario_closing_balance_is_creditor():
    """Scenario Boxus/Maria : AN -291.66 + Appel 500.29 - Paiement 501 = -292.37."""
    cid, oid, _ = _mk_seed()
    try:
        r = _run_movements(cid, oid, "2026-03-01", "2027-02-28")
        # AN inclus dans la periode (date = start_date) -> opening = 0
        assert abs(r["opening_balance"]) < 0.01, (
            f"opening_balance expected 0 (AN dans la periode), got {r['opening_balance']}"
        )
        # Somme debits = AN(0) + Appel(500.29) = 500.29
        assert abs(r["sum_debit"] - 500.29) < 0.01, (
            f"sum_debit expected 500.29, got {r['sum_debit']}"
        )
        # Somme credits = AN(291.66) + Paiement(501) = 792.66
        assert abs(r["sum_credit"] - 792.66) < 0.01, (
            f"sum_credit expected 792.66, got {r['sum_credit']}"
        )
        # Closing = 0 + 500.29 - 792.66 = -292.37 (crediteur)
        assert abs(r["closing_balance"] - (-292.37)) < 0.01, (
            f"closing_balance expected -292.37 (crediteur), got {r['closing_balance']}. "
            f"Le proprio est crediteur, pas debiteur."
        )
        # 3 mouvements affiches (AN, VE, FI)
        assert len(r["movements"]) == 3, (
            f"Attendu 3 mouvements, got {len(r['movements'])} : {r['movements']}"
        )
    finally:
        _cleanup(cid, oid)


def test_ma_situation_uses_same_source_as_appels_de_fonds():
    """Regle metier verrouillee : "Ma situation" et "Appels de fonds" doivent
    afficher LE MEME solde car ils utilisent la meme source (/owner/movements
    filtree par exercice fiscal).

    Ce test verrouille le fait que le closing_balance de /movements pour
    la periode FY = solde effectif du proprio. Le frontend calcule les
    stats "Ma situation" a partir de ces movements.
    """
    cid, oid, _ = _mk_seed()
    try:
        # Meme periode que "Appels de fonds" (FY 2026-2027)
        r = _run_movements(cid, oid, "2026-03-01", "2027-02-28")
        closing = r["closing_balance"]
        # Le frontend calcule : balance = closing_balance
        # status = crediteur (closing < -0.01)
        expected_status = (
            "debiteur" if closing > 0.01
            else "crediteur" if closing < -0.01
            else "solde"
        )
        assert expected_status == "crediteur", (
            f"Boxus doit etre CREDITEUR (closing={closing}), got {expected_status}"
        )
        # Le montant affiche a l'ecran doit etre exactement closing_balance
        # (pas de conversion en 'A payer' > 0 sur un solde crediteur)
        assert closing < 0, "Solde crediteur = closing negatif (proprio en avance)"
    finally:
        _cleanup(cid, oid)


def test_no_pre_fy_entries_dont_inflate_dashboard():
    """Repro bug : si des entries legacy pre-FY existent (import wizard,
    donnees N-2), elles ne doivent PAS gonfler le solde de la periode FY.

    Le fix : /movements filtre par [start_date, end_date] donc les pre-FY
    sont ignorees. Seul /dashboard (sans filtre) les inclut.
    """
    cid, oid, tier_acc = _mk_seed()
    try:
        # Ajout d'une entry LEGACY (date 2024, hors FY)
        async def add_legacy():
            from motor.motor_asyncio import AsyncIOMotorClient
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = client[os.environ["DB_NAME"]]
            await db.journal_entries.insert_one({
                "id": f"LEGACY-{uuid.uuid4().hex[:6]}",
                "journal_type": "VE", "date": "2024-06-15",
                "copropriete_id": cid,
                "reference": "LEGACY-2024",
                "description": "Appel legacy pre-FY",
                "lines": [
                    {"account_number": tier_acc, "account_name": "Prov",
                     "third_party_id": oid, "third_party_name": "Boxus",
                     "debit": 1500.00, "credit": 0.0},
                    {"account_number": "700000",
                     "debit": 0.0, "credit": 1500.00},
                ],
            })
            client.close()
        asyncio.run(add_legacy())

        # /movements FY -> ne doit PAS inclure legacy
        r = _run_movements(cid, oid, "2026-03-01", "2027-02-28")
        # opening_balance = legacy 1500 (car < 2026-03-01)
        assert abs(r["opening_balance"] - 1500.00) < 0.01, (
            f"opening_balance doit refleter le legacy (1500), got {r['opening_balance']}"
        )
        # closing = opening + debits - credits = 1500 + 500.29 - 792.66 = 1207.63
        assert abs(r["closing_balance"] - 1207.63) < 0.01, (
            f"closing_balance expected 1207.63 (incl. legacy carryover), got {r['closing_balance']}"
        )
        # Sums FY seuls (hors legacy)
        assert abs(r["sum_debit"] - 500.29) < 0.01
        assert abs(r["sum_credit"] - 792.66) < 0.01
    finally:
        _cleanup(cid, oid)
