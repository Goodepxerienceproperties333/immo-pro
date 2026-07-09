"""Iter90ck : BACKFILL retroactif de l'OD MUT-R et correction du prorata
lors de la generation d'appels post-mutation.

Contexte : quand un budget est vote APRES une mutation, ou quand la mutation
a ete passee AVANT le fix iter90cj (roulement_quota=0 fige en base malgre le
budget engage), aucune OD MUT-R (fonds de roulement) n'est jamais creee. Le
capital roulement engage n'est donc jamais transfere du vendeur a l'acquereur.

Fix (iter90ck) : lorsque `generate_prorata_mut_ods_for_call` est appele (qu'il
soit declenche par POST /fund-calls ou par le wizard `_generate_from_budget`),
pour chaque mutation detectee comme touchant les lots de l'appel :
1. Verifier si une OD MUT-R (source_subtype='fonds_roulement') existe pour ce
   lot × cette mutation.
2. Si NON et que le budget approuve pour la FY couvrant sale_date contient
   `roulement_fund_amount > 0`, creer retroactivement l'OD MUT-R avec
   date=sale_date et montant = roulement_fund_amount × lot_share/key_total.
3. Si le budget lookup echoue, raise HTTPException 400 avec message clair
   (option user "raise a visible error instead of silently booking EUR 0").

Scenario reel (ACP Acacia, Feb 2026) :
- Budget vote : provisions 19000 (18800 commune + 200 ascenseur speciale),
  roulement 5200, reserve 1500.
- 3 lots proprietaire Matexi : Appt 202 (1009 commune + 1361 ascenseur),
  Cave C08 (13 commune), Parking PE07 (34 commune). Total quotity commune :
  1056/10000.
- Mutation Matexi -> DEGRANDE le 18/11/2025.
- Q4 provisions genere par wizard APRES mutation : distribution ces 3 lots
  agrege 503 EUR (18800/4 * 1056/10000 + 200/4 * 1361/10000 = 496.32 + 6.81).
- Prorata buyer (44 jours sur 92) : 503 * 44/92 = 240.57 EUR.
- OD MUT-R attendue : 5200 * 1056/10000 = 549.12 EUR datee 18/11.
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
    r = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    r.raise_for_status()
    tok = r.json().get("access_token") or r.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup_acacia_scenario(prefix: str):
    """Cree ACP + FY 2025 + 4 lots (Appt 202, Cave C08, Parking PE07, Other)
    + 2 cles (commune + ascenseur) + budget approuve avec 2 lignes."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot202 = f"lot202-{uuid.uuid4()}"
    lotC08 = f"lotC08-{uuid.uuid4()}"
    lotPE07 = f"lotPE07-{uuid.uuid4()}"
    lot_other = f"other-{uuid.uuid4()}"
    matexi = f"matexi-{uuid.uuid4()}"
    buyer = f"degrande-{uuid.uuid4()}"
    other_owner = f"other-o-{uuid.uuid4()}"
    key_commune = f"key-com-{uuid.uuid4()}"
    key_ascen = f"key-asc-{uuid.uuid4()}"

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
        {"number": "400000", "name": "Prov clients", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Ventes prov", "class_num": 7, "copropriete_id": cid},
        {"number": "61", "name": "Charges communes generales", "class_num": 6, "copropriete_id": cid},
        {"number": "61ASC", "name": "Charges ascenseur", "class_num": 6, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier DEGRANDE", "class_num": 4, "copropriete_id": cid},
        {"number": "4100003", "name": "Tier Other", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": matexi, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": buyer, "name": "DEGRANDE", "last_name": "DEGRANDE",
         "auxiliary_code": "D001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
        {"id": other_owner, "name": "Autre", "last_name": "Autre",
         "auxiliary_code": "O001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100003"}}},
    ])
    await db.lots.insert_many([
        {"id": lot202, "number": "202", "owner_id": matexi,
         "owner_ids": [matexi], "copropriete_id": cid, "quotity": 1009.0},
        {"id": lotC08, "number": "C08", "owner_id": matexi,
         "owner_ids": [matexi], "copropriete_id": cid, "quotity": 13.0},
        {"id": lotPE07, "number": "PE07", "owner_id": matexi,
         "owner_ids": [matexi], "copropriete_id": cid, "quotity": 34.0},
        {"id": lot_other, "number": "OTHER", "owner_id": other_owner,
         "owner_ids": [other_owner], "copropriete_id": cid, "quotity": 8944.0},
    ])
    # Cle "commune" : les 4 lots, total 10000
    await db.distribution_keys.insert_one({
        "id": key_commune, "copropriete_id": cid, "name": "Charges communes",
        "is_default": True, "key_type": "quotity",
        "lots": [
            {"lot_id": lot202, "share": 1009.0, "lot_number": "202"},
            {"lot_id": lotC08, "share": 13.0, "lot_number": "C08"},
            {"lot_id": lotPE07, "share": 34.0, "lot_number": "PE07"},
            {"lot_id": lot_other, "share": 8944.0, "lot_number": "OTHER"},
        ],
    })
    # Cle "ascenseur" : uniquement les appartements
    await db.distribution_keys.insert_one({
        "id": key_ascen, "copropriete_id": cid, "name": "Ascenseur",
        "is_default": False, "key_type": "quotity",
        "lots": [
            {"lot_id": lot202, "share": 1361.0, "lot_number": "202"},
            {"lot_id": lot_other, "share": 8639.0, "lot_number": "OTHER"},
        ],
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id,
        "lot202": lot202, "lotC08": lotC08, "lotPE07": lotPE07, "lot_other": lot_other,
        "matexi": matexi, "buyer": buyer, "other_owner": other_owner,
        "key_commune": key_commune, "key_ascen": key_ascen,
    }


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "mutation_records", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["matexi"], ctx["buyer"], ctx["other_owner"]]}})


async def _create_approved_budget(client, hdr, ctx):
    r = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
        "fiscal_year_id": ctx["fy_id"],
        "name": "Budget 2025 Acacia",
        "copropriete_id": ctx["cid"],
        "lines": [
            {"account_number": "61", "account_name": "Charges communes generales",
             "amount": 18800.0, "distribution_key_id": ctx["key_commune"]},
            {"account_number": "61ASC", "account_name": "Charges speciales ascenseur",
             "amount": 200.0, "distribution_key_id": ctx["key_ascen"]},
        ],
        "reserve_fund_amount": 1500.0,
        "reserve_fund_key_id": ctx["key_commune"],
        "roulement_fund_amount": 5200.0,
        "roulement_fund_key_id": ctx["key_commune"],
    })
    assert r.status_code == 200, r.text
    budget = r.json()
    r = await client.post(
        f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/approve", headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _create_legacy_mutation(ctx, sale_date: str = "2025-11-18"):
    """Simule une mutation en etat legacy (roulement_quota=0 fige) : la
    mutation existe en base mais sans OD MUT-R (contexte : mutation passee
    avant le fix iter90cj alors que roulement etait 0 dans budget)."""
    db = ctx["db"]
    mut_id = str(uuid.uuid4())
    now = "2025-11-18T10:00:00Z"
    # Insere la mutation dans lot.mutations[] et db.mutations avec roul=0
    for lot_id in (ctx["lot202"], ctx["lotC08"], ctx["lotPE07"]):
        mr = {
            "id": mut_id if lot_id == ctx["lot202"] else str(uuid.uuid4()),
            "date": sale_date,
            "old_owner_id": ctx["matexi"],
            "new_owner_id": ctx["buyer"],
            "sale_price": 250000.0,
            "roulement_quota": 0.0,    # <-- etat legacy
            "current_period_prorata": 0.0,
            "prorata_provisions": 0.0,
            "total_transfer": 0.0,
            "journal_entry_ids": [],
            "created_at": now,
        }
        await db.lots.update_one(
            {"id": lot_id},
            {"$set": {"owner_id": ctx["buyer"], "owner_ids": [ctx["buyer"]]},
             "$push": {"mutations": mr}},
        )
        await db.mutations.insert_one({
            **mr,
            "copropriete_id": ctx["cid"], "lot_id": lot_id,
            "from_owner_id": ctx["matexi"], "to_owner_id": ctx["buyer"],
            "sale_date": sale_date,
        })


# ============================================================================
# SCENARIO 1 : OD MUT-R backfillee lors de la generation d'appel post-mutation
# ============================================================================
async def _scenario_backfill_od_mutr_from_call_generation():
    ctx = await _setup_acacia_scenario("iter90ck-mutr-backfill")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            hdr = await _login(client)
            budget = await _create_approved_budget(client, hdr, ctx)
            await _create_legacy_mutation(ctx, sale_date="2025-11-18")

            # Verifie l'etat initial : aucune OD MUT-R n'existe (legacy)
            existing_mutr = await ctx["db"].journal_entries.count_documents({
                "copropriete_id": ctx["cid"],
                "source_type": "lot_mutation",
                "source_subtype": "fonds_roulement",
            })
            assert existing_mutr == 0, "Precondition : pas encore d'OD MUT-R"

            # Genere les 4 appels trimestriels via wizard (call_type=provisions).
            # Chaque appel a une periode ; Q4 (01/10 - 31/12) couvre la mutation.
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

            # Fetch tous les OD MUT-R + MUT-P generees
            mutr = await ctx["db"].journal_entries.find({
                "copropriete_id": ctx["cid"],
                "source_type": "lot_mutation",
                "source_subtype": "fonds_roulement",
            }, {"_id": 0}).to_list(100)

            # ASSERTION A : au moins 1 OD MUT-R doit etre creee, avec montant
            # correct = 549.12 total sur les 3 lots (Matexi's quotity 1056/10000)
            assert mutr, (
                "REGRESSION iter90ck : aucune OD MUT-R (fonds_roulement) creee "
                "malgre la generation d'appels post-mutation. Le backfill "
                "retroactif dans generate_prorata_mut_ods_for_call doit crediter "
                "le vendeur et debiter l'acheteur pour 5200 * 1056/10000 = 549.12 EUR."
            )
            total_mutr = round(sum(float(e["total_debit"]) for e in mutr), 2)
            assert total_mutr == 549.12, (
                f"REGRESSION iter90ck : OD MUT-R total attendu 549.12 EUR "
                f"(5200 * 1056/10000). Obtenu : {total_mutr} EUR. "
                f"Nb ecritures : {len(mutr)}."
            )
            # Toutes datees sale_date 18/11
            for e in mutr:
                assert e["date"] == "2025-11-18", (
                    f"REGRESSION iter90ck : OD MUT-R doit etre datee 18/11/2025 "
                    f"(sale_date). Obtenu : {e['date']}."
                )

            # ASSERTION B : OD MUT-P retroactive Q4 = 240.57
            # Le Q4 (01/10-31/12) straddle la mutation 18/11.
            # base per lot : 4700/n_calls (=commune 4700 pour ce Q) * quotity/10000
            #              + 50/n_calls (=asc 50 pour ce Q) * quotity_ascen/10000
            # Somme pour 3 lots Matexi = 496.32 + 6.81 = 503.13 EUR
            # buyer segment (44 jours sur 92) = 503.13 * 44/92 = 240.57 EUR
            mutp = await ctx["db"].journal_entries.find({
                "copropriete_id": ctx["cid"],
                "source_type": "lot_mutation",
                "source_subtype": "prorata_post_mutation",
            }, {"_id": 0}).to_list(100)
            total_mutp = round(sum(float(e["total_debit"]) for e in mutp), 2)
            assert abs(total_mutp - 240.57) < 0.5, (
                f"REGRESSION iter90ck : OD MUT-P Q4 attendu ~240.57 EUR "
                f"(503.13 * 44/92 base sur multi-cles commune+ascenseur). "
                f"Obtenu : {total_mutp} EUR (currently 579.01 reported by user). "
                f"Nb ecritures : {len(mutp)}. Details : {[(e['reference'], e['total_debit']) for e in mutp]}"
            )
    finally:
        await _cleanup(ctx)


# ============================================================================
# SCENARIO 2 : Erreur visible si budget sans roulement au moment de la generation
# ============================================================================
async def _scenario_visible_error_when_no_budget_roulement():
    """User requirement : 'if that budget lookup fails, raise a visible error
    instead of silently booking EUR 0'."""
    ctx = await _setup_acacia_scenario("iter90ck-no-budget-error")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            hdr = await _login(client)
            # Cree budget SANS roulement (roulement_fund_amount = 0)
            r = await client.post(f"{BACKEND_URL}/api/fiscal/budgets", headers=hdr, json={
                "fiscal_year_id": ctx["fy_id"],
                "name": "Budget 2025 no-roul",
                "copropriete_id": ctx["cid"],
                "lines": [
                    {"account_number": "61", "account_name": "Charges",
                     "amount": 18800.0, "distribution_key_id": ctx["key_commune"]},
                ],
                "reserve_fund_amount": 0.0,
                "roulement_fund_amount": 0.0,  # <-- pas d'engagement
            })
            assert r.status_code == 200
            budget = r.json()
            r = await client.post(
                f"{BACKEND_URL}/api/fiscal/budgets/{budget['id']}/approve", headers=hdr,
            )
            assert r.status_code == 200

            await _create_legacy_mutation(ctx, sale_date="2025-11-18")

            # Genere Q4 : doit renvoyer 400 (error visible) car aucune OD MUT-R
            # ne peut etre creee (roulement engage = 0 mais mutation existe).
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
            assert r.status_code == 400, (
                f"REGRESSION iter90ck : quand un appel post-mutation est genere "
                f"et qu'aucun budget approuve avec roulement > 0 ne couvre la "
                f"sale_date, l'endpoint doit renvoyer HTTPException 400 (visible "
                f"error). Obtenu : {r.status_code} - {r.text[:200]}"
            )
            detail = r.json().get("detail", "")
            assert "roulement" in detail.lower() or "MUT-R" in detail, (
                f"REGRESSION iter90ck : le message d'erreur doit mentionner le "
                f"probleme (roulement / MUT-R). Obtenu : {detail}"
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_backfill_od_mutr_from_call_generation():
    asyncio.run(_scenario_backfill_od_mutr_from_call_generation())


def test_visible_error_when_no_budget_roulement():
    asyncio.run(_scenario_visible_error_when_no_budget_roulement())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
