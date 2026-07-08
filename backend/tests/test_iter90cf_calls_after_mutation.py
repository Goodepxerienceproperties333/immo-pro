"""
Iter90cf : REGRESSION LOCK - Appels crees APRES une mutation genere l'OD MUT-P
retroactivement (bug rapporte par utilisateur sur ACP Acacia, mutation Matexi
-> DEGRANDE au 10/06/2026).

Contexte du bug :
- Mutation Matexi -> DEGRANDE le 10/06/2026 realisee via /api/lots/{id}/mutate.
- A ce moment, les appels Q2 2026 (01/04-30/06) et Q3 2026 (01/07-30/09)
  n'existaient pas encore -> aucune OD MUT-P ni MUT-F creee.
- Plus tard, le syndic genere les appels via /api/fund-calls/generate-from-budget.
- L'appel Q2 (01/04, periode 01/04-30/06) chevauche la mutation :
  - owner-at-call-date = Matexi (car 01/04 < 10/06) -> distribution 100% Matexi.
  - MAIS : les 21 jours du 10/06 au 30/06 doivent aller a DEGRANDE.
  - Bug : aucune OD MUT-P n'est creee -> DEGRANDE n'est jamais debite de sa
    quote-part de Q2 -> total balance de tiers faux.

Root cause secondaire identifiee : mutate_lot ecrivait uniquement dans
`lot.mutations` array, mais `_rebind_owner_at_call_date` (fund_calls.py) lit
dans `db.mutations` collection. Ces mutations n'etaient donc jamais vues.
Fix : double-ecriture dans mutate_lot + sync au startup.

Test coverage :
1. Mutation puis appel provisions dont la periode chevauche : OD MUT-P creee.
2. Mutation puis appel provisions dont la periode est ENTIEREMENT apres :
   distribution.owner = buyer (via rebind), aucune OD MUT-P (rien a proratiser).
3. Mutation puis appel provisions dont la periode est ENTIEREMENT avant :
   distribution.owner = seller, aucune OD MUT-P.
4. Idempotence : appeler generate_prorata_mut_ods_for_call deux fois ne cree
   pas de doublons.
5. Sync db.mutations : les mutations creees via mutate_lot apparaissent dans
   db.mutations collection.
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

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _base_setup(prefix: str):
    """ACP + FY 2026 + 1 lot + vendeur + acheteur + cle par defaut."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    seller_id = f"matexi-{uuid.uuid4()}"
    buyer_id = f"degrande-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"
    budget_id = f"bud-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier DEGRANDE", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": buyer_id, "name": "DEGRANDE", "last_name": "DEGRANDE",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "302", "owner_id": seller_id,
        "owner_ids": [seller_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "302"}],
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
        "seller": seller_id, "buyer": buyer_id, "key": key_id,
        "budget_id": budget_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "mutation_records", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"]]}})


async def _do_mutation(ctx, sale_date: str):
    """Effectue la mutation via l'endpoint /mutate et retourne le mutation_id."""
    async with httpx.AsyncClient(timeout=30) as client:
        hdr = await _login(client)
        resp = await client.post(
            f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
            headers=hdr,
            json={"new_owner_id": ctx["buyer"], "sale_date": sale_date,
                  "sale_price": 250000.0},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()


# =========================================================================
# SCENARIO 1 : Mutation puis appel STRADDLING (cas ACP Acacia DEGRANDE)
# =========================================================================
async def _scenario_call_straddling_after_mutation_generates_od():
    """Setup :
       - Mutation Matexi -> DEGRANDE le 10/06/2026 (aucun appel n'existe encore)
       - Puis creation d'un appel Q2 2026 (01/04, periode 01/04-30/06) via
         POST /api/fund-calls
       Attendu :
       - distribution.owner_id = Matexi (owner-at-call-date, 01/04 < 10/06)
       - Une OD MUT-P est creee : DR DEGRANDE / CR Matexi, montant = 372 * 21/91
    """
    ctx = await _base_setup("iter90cf-straddle")
    try:
        # 1) Mutation
        await _do_mutation(ctx, "2026-06-10")

        # 2) Verifie la sync : db.mutations doit contenir la mutation
        muts_in_db = await ctx["db"].mutations.find(
            {"copropriete_id": ctx["cid"]}, {"_id": 0}
        ).to_list(10)
        assert len(muts_in_db) == 1, (
            f"REGRESSION iter90cf : mutate_lot doit ecrire dans db.mutations. "
            f"Obtenu : {muts_in_db}"
        )
        assert muts_in_db[0]["from_owner_id"] == ctx["seller"]
        assert muts_in_db[0]["to_owner_id"] == ctx["buyer"]
        assert muts_in_db[0]["sale_date"] == "2026-06-10"

        # 3) Cree l'appel Q2 straddling
        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 2/4 - 2026",
                "date": "2026-04-01",
                "due_date": "2026-04-30",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q2 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()

        # 4) Verifie que distribution est bien 100% Matexi (owner-at-call-date)
        dist = call_doc.get("distribution", [])
        assert len(dist) == 1
        assert dist[0]["owner_id"] == ctx["seller"], (
            f"REGRESSION : distribution.owner_id doit etre le vendeur "
            f"(owner-at-call-date). Obtenu : {dist[0]['owner_id']}"
        )
        assert round(dist[0]["amount"], 2) == 372.0

        # 5) Verifie qu'une OD MUT-P a ete creee
        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 1, (
            f"REGRESSION iter90cf : une OD MUT-P doit etre creee retroactivement "
            f"lorsque la periode de l'appel chevauche une mutation. Obtenu : "
            f"{[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
        od = mut_ods[0]
        # Prorata : 372 * 21 / 91 = 85.85
        # Periode 01/04 - 30/06 = 91 jours, DEGRANDE detient du 10/06 au 30/06 = 21 jours
        expected_prorata = round(372.0 * 21 / 91, 2)
        assert round(od["total_debit"], 2) == expected_prorata, (
            f"REGRESSION : montant OD MUT-P attendu {expected_prorata}, "
            f"obtenu {od['total_debit']}"
        )
        # Verifie sens : DR DEGRANDE, CR Matexi
        debit_line = next((ln for ln in od["lines"] if float(ln["debit"]) > 0), None)
        credit_line = next((ln for ln in od["lines"] if float(ln["credit"]) > 0), None)
        assert debit_line and debit_line["third_party_id"] == ctx["buyer"], (
            f"REGRESSION : DR doit etre l'acheteur (DEGRANDE). Obtenu : {debit_line}"
        )
        assert credit_line and credit_line["third_party_id"] == ctx["seller"], (
            f"REGRESSION : CR doit etre le vendeur (Matexi). Obtenu : {credit_line}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Mutation puis appel ENTIEREMENT APRES la mutation
# =========================================================================
async def _scenario_call_entirely_after_mutation_no_od():
    """Setup :
       - Mutation Matexi -> DEGRANDE le 10/06/2026
       - Puis creation d'un appel Q3 2026 (01/07, periode 01/07-30/09) apres mutation
       Attendu :
       - distribution.owner_id = DEGRANDE (owner-at-call-date, 01/07 > 10/06)
       - Aucune OD MUT-P (rien a proratiser, tout est post-mutation)
    """
    ctx = await _base_setup("iter90cf-after")
    try:
        await _do_mutation(ctx, "2026-06-10")

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 3/4 - 2026",
                "date": "2026-07-01",
                "due_date": "2026-07-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q3 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()

        dist = call_doc.get("distribution", [])
        assert dist[0]["owner_id"] == ctx["buyer"], (
            f"REGRESSION : appel post-mutation doit avoir owner = acheteur "
            f"(DEGRANDE). Obtenu : {dist[0]['owner_id']}"
        )

        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 0, (
            f"REGRESSION : aucune OD MUT-P attendue quand la periode est "
            f"entierement post-mutation. Obtenu : "
            f"{[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : Mutation puis appel ENTIEREMENT AVANT la mutation
# =========================================================================
async def _scenario_call_entirely_before_mutation_no_od():
    """Setup :
       - Mutation Matexi -> DEGRANDE le 10/06/2026
       - Puis creation d'un appel Q1 2026 (01/01, periode 01/01-31/03) avant mutation
       Attendu :
       - distribution.owner_id = Matexi
       - Aucune OD MUT-P (periode entierement pre-mutation)
    """
    ctx = await _base_setup("iter90cf-before")
    try:
        await _do_mutation(ctx, "2026-06-10")

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 1/4 - 2026",
                "date": "2026-01-01",
                "due_date": "2026-01-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q1 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call_doc = resp.json()

        dist = call_doc.get("distribution", [])
        assert dist[0]["owner_id"] == ctx["seller"]

        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 0, (
            f"REGRESSION : appel entierement pre-mutation ne doit generer aucune "
            f"OD. Obtenu : {[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : Idempotence - re-generer un appel n'ajoute pas d'OD doublon
# =========================================================================
async def _scenario_regeneration_is_idempotent():
    """Suppression + recreation d'un appel straddling ne doit pas dupliquer les
    OD MUT-P retroactives (la suppression doit d'abord contre-passer, puis la
    recreation en cree une nouvelle mais pas de duplicat par reference).
    """
    ctx = await _base_setup("iter90cf-idem")
    try:
        await _do_mutation(ctx, "2026-06-10")

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)

            # 1er appel
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 2/4 - 2026",
                "date": "2026-04-01",
                "due_date": "2026-04-30",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q2 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200
            call1 = resp.json()

            # Supprime l'appel
            del_resp = await client.delete(
                f"{BACKEND_URL}/api/fund-calls/{call1['id']}", headers=hdr,
            )
            assert del_resp.status_code == 200, del_resp.text

            # Verifie que l'OD MUT-P a ete contre-passee
            reversed_ods = await ctx["db"].journal_entries.find({
                "copropriete_id": ctx["cid"],
                "source_subtype": "prorata_post_mutation",
                "reversed": True,
            }, {"_id": 0}).to_list(10)
            assert len(reversed_ods) == 1, (
                f"REGRESSION : l'OD MUT-P doit etre contre-passee lors de la "
                f"suppression de l'appel. Obtenu : {len(reversed_ods)}"
            )

            # Recree l'appel : nouvelle OD doit exister sans doublonner
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 2/4 - 2026 (recree)",
                "date": "2026-04-01",
                "due_date": "2026-04-30",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q2 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200

            active_ods = await ctx["db"].journal_entries.find({
                "copropriete_id": ctx["cid"],
                "source_subtype": "prorata_post_mutation",
                "reversed": {"$ne": True},
                "is_reversal": {"$ne": True},
            }, {"_id": 0}).to_list(10)
            assert len(active_ods) == 1, (
                f"REGRESSION : une seule OD MUT-P active attendue apres recreation. "
                f"Obtenu : {len(active_ods)}"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 5 : Chain de mutations dans la periode d'un appel
# =========================================================================
async def _scenario_multiple_mutations_in_period():
    """Setup : 2 mutations dans la periode d'un meme appel :
    - Mutation 1 : Matexi -> DEGRANDE le 15/04/2026
    - Mutation 2 : DEGRANDE -> Un tiers 3 le 20/05/2026
    - Appel Q2 2026 (01/04-30/06, 91j)
    Segments : Matexi 14j (01/04-14/04), DEGRANDE 35j (15/04-19/05),
               Tiers3 42j (20/05-30/06).
    Attendu :
    - distribution.owner = Matexi (owner-at-call-date 01/04)
    - 2 OD MUT-P : (Matexi -> DEGRANDE 35j de 91) et (Matexi -> Tiers3 42j de 91)
    """
    ctx = await _base_setup("iter90cf-multi")
    try:
        # Cree un 3e owner
        db = ctx["db"]
        tiers3_id = f"tiers3-{uuid.uuid4()}"
        await db.pcmn_accounts.insert_one({
            "number": "4100003", "name": "Tier3", "class_num": 4, "copropriete_id": ctx["cid"]
        })
        await db.owners.insert_one({
            "id": tiers3_id, "name": "Tiers3", "last_name": "Tiers3",
            "auxiliary_code": "C0003", "copropriete_ids": [ctx["cid"]],
            "tier_accounts": {ctx["cid"]: {"provisions": "4100003"}},
        })

        # Mutation 1 : Matexi -> DEGRANDE
        await _do_mutation(ctx, "2026-04-15")
        # Mutation 2 : DEGRANDE -> Tiers3 (via manipulation directe car mutate
        # exige new_owner different actuel qui vient de changer)
        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login(client)
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": tiers3_id, "sale_date": "2026-05-20",
                      "sale_price": 260000.0},
            )
            assert resp.status_code == 200

            # Cree l'appel Q2 straddling les 2 mutations
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 2/4 - 2026",
                "date": "2026-04-01",
                "due_date": "2026-04-30",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q2 2026",
                "total_amount": 372.0,
                "call_type": "provisions",
                "distribution_key_id": ctx["key"],
                "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200

        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 2, (
            f"REGRESSION : 2 OD MUT-P attendues (Matexi -> DEGRANDE et "
            f"Matexi -> Tiers3). Obtenu : "
            f"{[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
        # Verifier les montants
        # 372 * 35/91 = 143.08 pour DEGRANDE (mais elle a lettre puis retransfere)
        # 372 * 42/91 = 171.72 pour Tiers3
        # Total OD = 143.08 + 171.72 = 314.80
        total_ods = round(sum(float(o["total_debit"]) for o in mut_ods), 2)
        # 372 - 372*14/91 = 372 - 57.23 = 314.77 (ecart de 0.03 sur arrondi)
        assert 314.5 <= total_ods <= 315.0, (
            f"Total OD attendu ~314.80, obtenu {total_ods}"
        )
    finally:
        await ctx["db"].owners.delete_many({"name": "Tiers3"})
        await ctx["db"].pcmn_accounts.delete_many({"number": "4100003", "copropriete_id": ctx["cid"]})
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_call_straddling_after_mutation_generates_od():
    asyncio.run(_scenario_call_straddling_after_mutation_generates_od())


def test_call_entirely_after_mutation_no_od():
    asyncio.run(_scenario_call_entirely_after_mutation_no_od())


def test_call_entirely_before_mutation_no_od():
    asyncio.run(_scenario_call_entirely_before_mutation_no_od())


def test_regeneration_is_idempotent():
    asyncio.run(_scenario_regeneration_is_idempotent())


def test_multiple_mutations_in_period():
    asyncio.run(_scenario_multiple_mutations_in_period())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
