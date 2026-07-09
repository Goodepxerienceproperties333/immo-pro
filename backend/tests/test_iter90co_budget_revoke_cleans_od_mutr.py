"""
iter90co : REGRESSION LOCK - Suppression / devalidation d'un budget doit
contre-passer AUSSI les OD MUT-R backfillees (fonds de roulement) creees
par iter90ck sur les appels du budget.

Bug rapporte utilisateur (Feb 2026) :
> "Quand le budget est repasse en brouillon les operations diverses doit
> concernant les appels doivent aussi etre supprimes"

Contexte : iter90ch avait deja resolu le nettoyage des OD MUT-P
(prorata_post_mutation). Mais les OD MUT-R backfillees par iter90ck
(source_type='lot_mutation' + source_subtype='fonds_roulement' +
fund_call_id=call_id + backfilled_by_iter90ck=True) N'ETAIENT PAS
contre-passees lors du delete/revoke du budget parent -> orphelins
comptables (fonds de roulement transfere alors que l'appel qui l'a
declenche n'existe plus).

Fix iter90co :
- Etend `reverse_post_mutation_ods_for_call` a `source_subtype IN
  ('prorata_post_mutation', 'fonds_roulement')` (filtre fund_call_id
  preserve la securite : les OD MUT-R d'origine mutate_lot n'ont pas
  de fund_call_id).
- Reset db.mutations.roulement_quota=0 si tous les JE de la mutation
  sont contre-passes.

Test coverage :
1. Suppression budget -> OD MUT-R backfillee contre-passee
2. Devalidation budget -> OD MUT-R backfillee contre-passee
3. Suppression appel isolee -> OD MUT-R backfillee contre-passee
4. Verifie que l'OD MUT-R d'origine (mutate_lot, sans fund_call_id)
   N'EST PAS impactee par le cleanup.
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


async def _base_setup(prefix: str):
    """Setup ACP + FY 2025 + 1 lot Matexi + 1 buyer + budget approuve via HTTP.

    Meme structure qu'iter90ck pour garantir que le backfill iter90ck se
    declenche (mutation legacy sans OD MUT-R + budget approuve avec
    roulement_fund_amount).
    """
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    seller_id = f"seller-{uuid.uuid4()}"
    buyer_id = f"buyer-{uuid.uuid4()}"
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
        {"number": "4100021", "name": "T-Seller", "class_num": 4, "copropriete_id": cid},
        {"number": "4100022", "name": "T-Buyer", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
        {"number": "61", "name": "Charges communes", "class_num": 6, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100021"}}},
        {"id": buyer_id, "name": "DEGRANDE", "last_name": "DEGRANDE",
         "auxiliary_code": "D001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100022"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": seller_id,
        "owner_ids": [seller_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "A1"}],
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
        "seller": seller_id, "buyer": buyer_id, "key": key_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"]]}})


async def _create_approved_budget(client, hdr, ctx, roulement_amount: float = 5200.0):
    """Cree et approuve un budget via HTTP (pour setter approved_at, verrous, etc.)."""
    r = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
        "fiscal_year_id": ctx["fy_id"],
        "name": "Budget 2025",
        "copropriete_id": ctx["cid"],
        "lines": [
            {"account_number": "61", "account_name": "Charges communes",
             "amount": 4000.0, "distribution_key_id": ctx["key"]},
        ],
        "reserve_fund_amount": 0.0,
        "reserve_fund_key_id": ctx["key"],
        "roulement_fund_amount": roulement_amount,
        "roulement_fund_key_id": ctx["key"],
    })
    assert r.status_code == 200, r.text
    budget = r.json()
    r = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/approve", headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _create_legacy_mutation(ctx, sale_date: str = "2025-05-15"):
    """Insere une mutation en etat legacy (roulement_quota=0 fige, aucune OD
    MUT-R) directement en DB. Reproduit le cas Acacia pre-iter90cj."""
    db = ctx["db"]
    mut_id = str(uuid.uuid4())
    now = f"{sale_date}T10:00:00Z"
    mr = {
        "id": mut_id,
        "date": sale_date,
        "old_owner_id": ctx["seller"],
        "new_owner_id": ctx["buyer"],
        "sale_price": 250000.0,
        "roulement_quota": 0.0,  # etat legacy
        "current_period_prorata": 0.0,
        "prorata_provisions": 0.0,
        "total_transfer": 0.0,
        "journal_entry_ids": [],
        "created_at": now,
    }
    await db.lots.update_one(
        {"id": ctx["lot"]},
        {"$set": {"owner_id": ctx["buyer"], "owner_ids": [ctx["buyer"]]},
         "$push": {"mutations": mr}},
    )
    await db.mutations.insert_one({
        **mr,
        "copropriete_id": ctx["cid"], "lot_id": ctx["lot"],
        "from_owner_id": ctx["seller"], "to_owner_id": ctx["buyer"],
        "sale_date": sale_date,
    })
    return mut_id


async def _generate_calls_from_budget(client, hdr, ctx, budget):
    """Genere les appels via generate-from-budget (declenche iter90ck backfill
    sur les mutations pre-existantes non transferees)."""
    r = await client.post(f"{BACKEND_URL}/api/fund-calls/generate-from-budget",
                          headers=hdr, json={
        "budget_id": budget["id"],
        "copropriete_id": ctx["cid"],
        "frequency": 4,
        "start_date": "2025-01-01",
        "due_offset_days": 30,
        "reserve_fund": {"enabled": False, "amount": 0.0},
        "roulement_fund": {"enabled": False, "amount": 0.0},
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _count_active_mut_r_backfilled(db, cid):
    return await db.journal_entries.count_documents({
        "copropriete_id": cid,
        "source_subtype": "fonds_roulement",
        "backfilled_by_iter90ck": True,
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
    })


async def _count_reversed_mut_r_backfilled(db, cid):
    return await db.journal_entries.count_documents({
        "copropriete_id": cid,
        "source_subtype": "fonds_roulement",
        "backfilled_by_iter90ck": True,
        "reversed": True,
    })


# =========================================================================
# SCENARIO 1 : DELETE budget -> OD MUT-R backfillee contre-passee
# =========================================================================
async def _scenario_delete_budget_reverses_od_mutr():
    ctx = await _base_setup("iter90co-del")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            budget = await _create_approved_budget(client, hdr, ctx)
            await _create_legacy_mutation(ctx, sale_date="2025-05-15")
            await _generate_calls_from_budget(client, hdr, ctx, budget)

            active_before = await _count_active_mut_r_backfilled(ctx["db"], ctx["cid"])
            assert active_before >= 1, (
                f"Prerequis : au moins 1 OD MUT-R backfillee attendue apres "
                f"generation appels post-mutation. Obtenu : {active_before}."
            )

            resp = await client.delete(
                f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            active_after = await _count_active_mut_r_backfilled(ctx["db"], ctx["cid"])
            reversed_count = await _count_reversed_mut_r_backfilled(ctx["db"], ctx["cid"])
            assert active_after == 0, (
                f"REGRESSION iter90co : {active_after} OD MUT-R backfillee(s) "
                f"active(s) restante(s) apres suppression du budget. "
                f"Attendu : 0. reversed_count={reversed_count}"
            )
            assert reversed_count >= active_before, (
                f"REGRESSION iter90co : contre-passations manquantes. "
                f"Actives avant={active_before}, reversed apres={reversed_count}."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : REVOKE budget -> OD MUT-R backfillee contre-passee + reset mutations
# =========================================================================
async def _scenario_revoke_budget_reverses_od_mutr():
    ctx = await _base_setup("iter90co-rev")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            budget = await _create_approved_budget(client, hdr, ctx)
            await _create_legacy_mutation(ctx, sale_date="2025-05-15")
            await _generate_calls_from_budget(client, hdr, ctx, budget)

            active_before = await _count_active_mut_r_backfilled(ctx["db"], ctx["cid"])
            assert active_before >= 1

            resp = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/revoke",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            budget_after = await ctx["db"].budgets.find_one(
                {"id": budget["id"]}, {"_id": 0}
            )
            assert budget_after and budget_after.get("status") == "draft", (
                f"Budget doit etre en draft apres revoke. "
                f"status={budget_after.get('status') if budget_after else None}"
            )

            active_after = await _count_active_mut_r_backfilled(ctx["db"], ctx["cid"])
            assert active_after == 0, (
                f"REGRESSION iter90co : {active_after} OD MUT-R backfillee(s) "
                f"active(s) apres devalidation du budget. Attendu : 0."
            )

            # iter90co : verifie que db.mutations.roulement_quota a ete reset
            muts = await ctx["db"].mutations.find(
                {"lot_id": ctx["lot"]}, {"_id": 0}
            ).to_list(100)
            assert muts, "La mutation doit toujours exister apres revoke"
            for mut in muts:
                assert (mut.get("roulement_quota") or 0.0) == 0.0, (
                    f"REGRESSION iter90co : mutation.roulement_quota devrait "
                    f"etre 0 apres revoke. Obtenu : {mut.get('roulement_quota')}"
                )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : DELETE appel de fonds isole -> OD MUT-R backfillee contre-passee
# =========================================================================
async def _scenario_delete_fund_call_reverses_od_mutr():
    ctx = await _base_setup("iter90co-fcdel")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            budget = await _create_approved_budget(client, hdr, ctx)
            await _create_legacy_mutation(ctx, sale_date="2025-05-15")
            resp = await _generate_calls_from_budget(client, hdr, ctx, budget)
            calls = resp.get("calls") or []
            assert calls, f"Aucun appel genere : {resp}"

            active_before = await _count_active_mut_r_backfilled(ctx["db"], ctx["cid"])
            assert active_before >= 1

            # Trouve l'appel qui a declenche le backfill (celui dont periode
            # couvre 2025-05-15 -> Q2 provisions)
            q2_call = None
            for c in calls:
                # Recupere le call en base pour verifier le fund_call_id des OD
                fc_doc = await ctx["db"].fund_calls.find_one(
                    {"id": c.get("id") or ""}, {"_id": 0, "id": 1, "name": 1,
                                                 "period_start": 1, "period_end": 1}
                )
                if not fc_doc:
                    # Le call n'est peut-etre pas encore persiste dans la reponse (variant)
                    fc_doc = await ctx["db"].fund_calls.find_one(
                        {"copropriete_id": ctx["cid"], "name": c.get("name", "")},
                        {"_id": 0}
                    )
                if fc_doc and fc_doc.get("period_start") <= "2025-05-15" <= fc_doc.get("period_end", ""):
                    q2_call = fc_doc
                    break

            assert q2_call, f"Appel Q2 couvrant 2025-05-15 introuvable parmi {[c.get('name') for c in calls]}"

            resp = await client.delete(
                f"{BACKEND_URL}/api/fund-calls/{q2_call['id']}",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            remaining_for_call = await ctx["db"].journal_entries.count_documents({
                "copropriete_id": ctx["cid"],
                "fund_call_id": q2_call["id"],
                "source_subtype": "fonds_roulement",
                "backfilled_by_iter90ck": True,
                "reversed": {"$ne": True},
                "is_reversal": {"$ne": True},
            })
            assert remaining_for_call == 0, (
                f"REGRESSION iter90co : {remaining_for_call} OD MUT-R backfillee(s) "
                f"active(s) pour l'appel supprime {q2_call['id']}. Attendu : 0."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : L'OD MUT-R d'ORIGINE (mutate_lot, SANS fund_call_id) doit
# rester intacte lors du cleanup d'un appel/budget qui la reference PAS.
# =========================================================================
async def _scenario_origin_od_mutr_untouched_on_revoke():
    ctx = await _base_setup("iter90co-origin")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            budget = await _create_approved_budget(client, hdr, ctx)

            # Inserer directement une OD MUT-R d'ORIGINE (sans fund_call_id,
            # sans backfilled_by_iter90ck) - simule le comportement de
            # mutate_lot iter90cj apres une mutation normale.
            origin_od_id = str(uuid.uuid4())
            await ctx["db"].journal_entries.insert_one({
                "id": origin_od_id,
                "journal_type": "OD",
                "date": "2025-05-15",
                "reference": "MUT-A1-R",
                "description": "Mutation lot A1 - Fonds de roulement (origine)",
                "lines": [
                    {"account_number": "4100022", "account_name": "T-Buyer",
                     "debit": 520.0, "credit": 0.0,
                     "third_party_id": ctx["buyer"], "third_party_name": "DEGRANDE"},
                    {"account_number": "4100021", "account_name": "T-Seller",
                     "debit": 0.0, "credit": 520.0,
                     "third_party_id": ctx["seller"], "third_party_name": "Matexi"},
                ],
                "total_debit": 520.0, "total_credit": 520.0,
                "copropriete_id": ctx["cid"],
                "auto_generated": False, "manually_edited": True,
                "source_type": "lot_mutation",
                "source_id": ctx["lot"],
                "source_subtype": "fonds_roulement",
                # PAS de fund_call_id, PAS de backfilled_by_iter90ck
                "created_at": "2025-05-15T10:00:00Z",
            })
            # Insere la mutation associee
            await _create_legacy_mutation(ctx, sale_date="2025-05-15")

            # Revoke le budget (aucun appel encore genere)
            resp = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/revoke",
                headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            # Verifie que l'OD MUT-R d'origine EST TOUJOURS active
            doc = await ctx["db"].journal_entries.find_one(
                {"id": origin_od_id}, {"_id": 0}
            )
            assert doc, "OD MUT-R d'origine disparue"
            assert doc.get("reversed") is not True, (
                "REGRESSION iter90co : OD MUT-R d'origine (sans fund_call_id) "
                "a ete contre-passee par erreur lors du revoke budget."
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_delete_budget_reverses_od_mutr():
    asyncio.run(_scenario_delete_budget_reverses_od_mutr())


def test_revoke_budget_reverses_od_mutr():
    asyncio.run(_scenario_revoke_budget_reverses_od_mutr())


def test_delete_fund_call_reverses_od_mutr():
    asyncio.run(_scenario_delete_fund_call_reverses_od_mutr())


def test_origin_od_mutr_untouched_on_revoke():
    asyncio.run(_scenario_origin_od_mutr_untouched_on_revoke())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
