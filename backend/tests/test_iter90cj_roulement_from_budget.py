"""
Iter90cj : REGRESSION LOCK - 2 root causes identifiees sur les mutations avec
appels de fonds de reserve/roulement/provisions generes APRES la mutation.

ROOT CAUSE 1 (silent DB failure sur db.mutations) :
  properties.py::mutate_lot enveloppe db.mutations.insert_one dans un try/except
  qui logue mais ne remonte pas l'echec. Si le doc n'est pas ecrit, alors
  _resolve_owner_at_date (fund_calls.py) ne trouve rien et retourne
  fallback_owner_id = lot.owner_id = NOUVEL acheteur. Consequence : les appels
  emis pour une periode OU la mutation etait posterieure sont attribues au
  buyer au lieu du seller.

  Le fix ajoute un BACKFILL defensif : lorsque le lecteur ne trouve rien dans
  db.mutations mais que lot.mutations[] existe, il re-synchronise a la volee.

ROOT CAUSE 2 (roulement quota = 0 quand aucun appel roulement n'existe encore) :
  properties.py::_compute_mutation_breakdown calcule fonds_roul_total UNIQUEMENT
  depuis les journal_entries deja postees sur le compte 100. Si aucun appel
  roulement n'a ete emis avant la mutation, fonds_roul_total = 0 -> OD MUT-R = 0
  -> aucun transfert du capital roulement entre vendeur et acheteur.

  Le fix lit egalement l'engagement budgete (via le budget vote), et prend
  max(budgeted_roulement, posted_roulement) comme base.

Scenarios :
1. Backfill defensif : mutation OK, db.mutations manquant (simulation via
   delete), un appel roulement genere APRES la mutation doit rester chez le
   VENDEUR (pas le buyer).
2. Roulement quota depuis budget vote : mutation avant TOUT appel roulement,
   OD MUT-R doit refleter la quote-part du lot sur le roulement engage
   (5200 EUR budget -> ~2600 EUR pour un lot 50/50).
3. E2E cas Matexi Acacia : budget 19000/1500/5200, mutation avant toute
   generation, OD MUT-R > 0 (verification que le transfert n'est pas nul).
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

BACKEND_URL = "http://localhost:8001"
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _base_setup(prefix: str, seller_share: float = 500.0, buyer_share_lot_id=None):
    """ACP + FY 2025 + 1 lot chez seller + buyer + cle par defaut."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    lot2_id = f"lot2-{uuid.uuid4()}"  # 2e lot pour avoir un vrai denominateur
    other_owner_id = f"other-{uuid.uuid4()}"
    seller_id = f"matexi-{uuid.uuid4()}"
    buyer_id = f"degrande-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2025", "start_date": "2025-01-01",
        "end_date": "2025-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier DEGRANDE", "class_num": 4, "copropriete_id": cid},
        {"number": "4100003", "name": "Tier Other", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": buyer_id, "name": "DEGRANDE", "last_name": "DEGRANDE",
         "auxiliary_code": "D001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
        {"id": other_owner_id, "name": "Autre", "last_name": "Autre",
         "auxiliary_code": "O001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100003"}}},
    ])
    await db.lots.insert_many([
        {"id": lot_id, "number": "A1", "owner_id": seller_id,
         "owner_ids": [seller_id], "copropriete_id": cid, "quotity": seller_share},
        {"id": lot2_id, "number": "A2", "owner_id": other_owner_id,
         "owner_ids": [other_owner_id], "copropriete_id": cid, "quotity": 500.0},
    ])
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [
            {"lot_id": lot_id, "share": seller_share, "lot_number": "A1"},
            {"lot_id": lot2_id, "share": 500.0, "lot_number": "A2"},
        ],
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id, "lot2": lot2_id,
        "seller": seller_id, "buyer": buyer_id, "other": other_owner_id, "key": key_id,
        "seller_share": seller_share,
    }


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "mutation_records", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"], ctx["other"]]}})


async def _create_and_approve_budget(client, hdr, ctx, roulement_amount=5200.0,
                                     reserve_amount=1500.0, provisions_amount=19000.0):
    """Cree un budget vote (approved) avec les lignes de provisions +
    persistance roulement/reserve. Pour l'instant reserve_fund/roulement_fund
    ne sont pas persistes sur le budget -> ce sera l'un des fix requis."""
    resp = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
        "fiscal_year_id": ctx["fy_id"],
        "name": "Budget 2025",
        "lines": [
            {"account_number": "61", "account_name": "Charges", "amount": provisions_amount,
             "distribution_key_id": ctx["key"]},
        ],
        "copropriete_id": ctx["cid"],
        # Champs esperes apres fix : le budget doit persister l'engagement
        # reserve/roulement pour que _compute_mutation_breakdown puisse le lire.
        "reserve_fund_amount": reserve_amount,
        "reserve_fund_key_id": ctx["key"],
        "roulement_fund_amount": roulement_amount,
        "roulement_fund_key_id": ctx["key"],
    })
    assert resp.status_code == 200, resp.text
    budget = resp.json()

    # Approuve (vote AG)
    resp = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/approve", headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# =========================================================================
# SCENARIO 1 : Root Cause 1 - Backfill defensif si db.mutations manquant
# =========================================================================
async def _scenario_defensive_backfill_on_missing_mutation_doc():
    """Simule un echec silencieux de db.mutations.insert_one :
    - Effectue la mutation via /api/lots/{id}/mutate (lot.mutations est peuple)
    - SUPPRIME manuellement le doc dans db.mutations (simulation de l'echec)
    - Cree un appel Q3 2025 (01/07-30/09) DATE = 20/10/2025 apres mutation 01/10
    - Verifie que la distribution est attribuee au VENDEUR (Matexi), pas au buyer

    Comportement actuel (bug) : sans le doc en db.mutations, _resolve_owner_at_date
    fallback au current owner (buyer) -> Matexi n'est plus proprietaire au moment
    du rebind -> attribution incorrecte.

    Comportement attendu (fix) : le lecteur doit backfill a la volee depuis
    lot.mutations[] et retourner le bon proprietaire (Matexi pour la periode Q3).
    """
    ctx = await _base_setup("iter90cj-backfill")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)

            # 1) Mutation Matexi -> DEGRANDE le 01/10/2025
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2025-10-01",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text

            # 2) SIMULATION echec silencieux : supprime le doc de db.mutations
            #    (lot.mutations[] reste peuple).
            deleted = await ctx["db"].mutations.delete_many({"copropriete_id": ctx["cid"]})
            assert deleted.deleted_count >= 1, "Le fixture doit avoir cree le doc"
            # Verifie que lot.mutations[] est bien peuple
            lot_doc = await ctx["db"].lots.find_one({"id": ctx["lot"]}, {"_id": 0})
            assert len(lot_doc.get("mutations", [])) == 1, (
                f"lot.mutations[] doit etre peuple. Obtenu : {lot_doc.get('mutations')}"
            )

            # 3) Cree un appel Q3 2025 (01/07-30/09) date=20/10 en retard, apres mutation.
            #    Regle metier : la periode couverte est Q3 (avant mutation) -> Matexi.
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 3/4 - 2025",
                "date": "2025-10-20",
                "due_date": "2025-11-19",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q3 2025 emis en retard apres mutation",
                "total_amount": 1000.0,
                "call_type": "roulement",  # roulement pour tester le rebind
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()

            # 4) Trouve la distribution du lot mute
            lot_row = next((d for d in call_doc.get("distribution", [])
                            if d.get("lot_id") == ctx["lot"]), None)
            assert lot_row is not None, "Le lot doit apparaitre dans la distribution"

            # Attendu (fix) : owner = Matexi (vendeur) car appel Q3 2025 concerne
            # la periode PRE-mutation. Actuel (bug) : owner = DEGRANDE (buyer).
            assert lot_row["owner_id"] == ctx["seller"], (
                f"REGRESSION iter90cj (Root Cause 1) : appel Q3 2025 (periode "
                f"avant mutation 01/10) doit etre attribue au vendeur Matexi. "
                f"Obtenu : owner_id={lot_row['owner_id']}. Cause : silent DB "
                f"failure sur db.mutations -> fallback owner incorrect. Le fix "
                f"defensif doit backfill depuis lot.mutations[]."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Root Cause 2 - OD MUT-R basee sur budget vote (pas 0)
# =========================================================================
async def _scenario_roulement_transfer_from_budget_amount():
    """Reproduit le cas Acacia :
    - Budget vote avec roulement_fund_amount=5200 EUR
    - AUCUN appel de roulement n'a encore ete emis (posted_roulement=0)
    - Mutation Matexi -> DEGRANDE le 18/11/2025
    - Attendu : OD MUT-R > 0 (base sur budget), lot A1 a 500/1000 -> 2600 EUR
    - Actuel (bug) : OD MUT-R = 0 (car calcul base sur journal_entries vides)
    """
    ctx = await _base_setup("iter90cj-budget-roul")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)

            # 1) Cree + approuve le budget avec roulement=5200
            await _create_and_approve_budget(
                client, hdr, ctx,
                roulement_amount=5200.0, reserve_amount=1500.0,
                provisions_amount=19000.0,
            )

            # 2) Verifie que le budget persiste bien le roulement_fund_amount.
            budget_doc = await ctx["db"].budgets.find_one(
                {"copropriete_id": ctx["cid"]}, {"_id": 0},
            )
            assert budget_doc is not None
            assert float(budget_doc.get("roulement_fund_amount", 0) or 0) == 5200.0, (
                f"REGRESSION iter90cj (Root Cause 2) : le budget doit persister "
                f"roulement_fund_amount pour que _compute_mutation_breakdown "
                f"puisse le lire. Obtenu : {budget_doc.get('roulement_fund_amount')}"
            )

            # 3) Verifie qu'aucun appel roulement n'existe encore
            calls = await ctx["db"].fund_calls.find(
                {"copropriete_id": ctx["cid"]}, {"_id": 0},
            ).to_list(100)
            assert len(calls) == 0, "Precondition : aucun appel ne doit exister"

            # 4) Effectue la mutation via l'endpoint live
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2025-11-18",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text
            mut_result = resp.json()

            # 5) Verifie que l'OD MUT-R (fonds de roulement) a ete creee avec le
            #    montant attendu = 5200 * 500/1000 = 2600 EUR
            r_quota = float(mut_result.get("mutation", {}).get("roulement_quota", 0))
            expected = round(5200.0 * ctx["seller_share"] / 1000.0, 2)  # 2600.00
            assert r_quota == expected, (
                f"REGRESSION iter90cj (Root Cause 2) : le roulement_quota doit "
                f"etre calcule sur le BUDGET VOTE (max(budgeted, posted)) et non "
                f"seulement sur les journal_entries deja postees. "
                f"Attendu : {expected} EUR (5200 * {ctx['seller_share']}/1000). "
                f"Obtenu : {r_quota} EUR."
            )

            # 6) Verifie qu'une ecriture OD fonds_roulement existe en base
            od = await ctx["db"].journal_entries.find_one({
                "copropriete_id": ctx["cid"],
                "source_type": "lot_mutation",
                "source_subtype": "fonds_roulement",
            }, {"_id": 0})
            assert od is not None, (
                "REGRESSION : une OD MUT-R (fonds_roulement) doit etre inseree "
                "dans journal_entries."
            )
            assert round(float(od.get("total_debit", 0)), 2) == expected, (
                f"REGRESSION : ecriture OD debit attendu {expected}, obtenu "
                f"{od.get('total_debit')}."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : E2E cas Matexi/Acacia - budget 19000/1500/5200
# =========================================================================
async def _scenario_e2e_acacia_matexi_549():
    """Cas concret user Feb 2026 :
    - Budget vote pour ACP Acacia : provisions 19000 + reserve 1500 + roulement 5200
    - Mutation Matexi -> nouvel acheteur le 18/11/2025
    - AUCUN appel de fonds de roulement encore emis a la date de mutation
    - Attendu : OD MUT-R non nulle (transfert du capital roulement engage)
    - Verification : le PDF de decompte de mutation reflete cette OD

    Ce test ne verifie pas la valeur EXACTE 549.12 EUR (depend de la structure
    de la cle Matexi reelle). Il verifie que le transfert n'est PAS zero.
    """
    ctx = await _base_setup("iter90cj-acacia-e2e", seller_share=105.6)  # ~10.56%
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)

            await _create_and_approve_budget(
                client, hdr, ctx,
                roulement_amount=5200.0, reserve_amount=1500.0,
                provisions_amount=19000.0,
            )

            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2025-11-18",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text
            mut = resp.json()

            # Le montant exact depend de la structure de la cle Matexi reelle,
            # mais doit etre strictement > 0 (le bug le mettait a 0).
            r_quota = float(mut.get("mutation", {}).get("roulement_quota", 0))
            assert r_quota > 0.01, (
                f"REGRESSION iter90cj (E2E Acacia) : OD MUT-R doit etre > 0 "
                f"quand un budget vote engage le roulement. Obtenu : {r_quota} EUR. "
                f"Cas reel user : mutation 18/11/2025 avant tout appel roulement, "
                f"le transfert doit refleter la quote-part du lot sur les 5200 EUR "
                f"engages par l'AG."
            )
            # Pour un lot 105.6/(105.6+500) = 17.44% : 5200 * 0.1744 ~= 906.6 EUR
            expected = round(5200.0 * 105.6 / 605.6, 2)
            assert abs(r_quota - expected) < 0.5, (
                f"OD MUT-R : attendu ~{expected} EUR, obtenu {r_quota} EUR."
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_defensive_backfill_on_missing_mutation_doc():
    asyncio.run(_scenario_defensive_backfill_on_missing_mutation_doc())


def test_roulement_transfer_from_budget_amount():
    asyncio.run(_scenario_roulement_transfer_from_budget_amount())


def test_e2e_acacia_matexi_budget_transfer():
    asyncio.run(_scenario_e2e_acacia_matexi_549())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
