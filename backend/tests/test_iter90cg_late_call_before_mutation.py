"""
Iter90cg : REGRESSION LOCK - Appel emis EN RETARD apres une mutation
posterieure a la periode de l'appel.

Bug rapporte par utilisateur (Feb 2026) :
> "Matexi vend le 01.10.2025. Les appels Q3 (01.07-30.09) et fonds de reserve
> generes APRES la mutation attribuent le montant au NOUVEL ACHETEUR alors
> qu'ils devraient revenir a Matexi (proprietaire pendant Q3)."

Root cause : `_rebind_owner_at_call_date` rebindait sur `call_date` (date
d'emission). Or si l'appel est emis EN RETARD (`call_date > period_end`) apres
qu'une mutation soit intervenue APRES la periode, l'owner-at-call-date est le
nouvel acheteur -- attribution incorrecte.

Fix : rebind sur `effective_date = min(call_date, period_end)`.

Cas couverts par ce fichier :
1. Provisions : appel Q3 2025 emis le 20/10/2025 apres mutation 01/10/2025.
   -> owner = Matexi (vendeur, proprietaire durant Q3).
2. Fonds de reserve : idem cas 1 avec call_type='reserve'.
3. Fonds de roulement : idem cas 1 avec call_type='roulement'.
4. Cas nominal (call_date <= period_end) : comportement inchange.
5. Appel qui straddle la mutation : provisions -> owner-at-call-date (Matexi
   si call_date < sale_date), OD MUT-P separee.
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
    """iter90cg : cache le token au niveau module pour eviter le rate-limit."""
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
    """ACP + FY 2025 + 1 lot + Matexi (vendeur) + NouvelAcheteur + cle."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    seller_id = f"matexi-{uuid.uuid4()}"
    buyer_id = f"acheteur-{uuid.uuid4()}"
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
        {"number": "4100011", "name": "Tier Matexi", "class_num": 4, "copropriete_id": cid},
        {"number": "4100012", "name": "Tier Nouvel", "class_num": 4, "copropriete_id": cid},
        {"number": "4100111", "name": "Tier Matexi Res", "class_num": 4, "copropriete_id": cid},
        {"number": "4100112", "name": "Tier Nouvel Res", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Matexi", "last_name": "Matexi",
         "auxiliary_code": "M001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100011", "reserve": "4100111"}}},
        {"id": buyer_id, "name": "NouvelAcheteur", "last_name": "NouvelAcheteur",
         "auxiliary_code": "N001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100012", "reserve": "4100112"}}},
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
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"]]}})


async def _do_mutation(ctx, sale_date: str):
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


async def _post_call(ctx, name, call_date, total, call_type, period_hint=None):
    """Cree un appel via POST /api/fund-calls et retourne le doc."""
    async with httpx.AsyncClient(timeout=30) as client:
        hdr = await _login(client)
        resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
            "name": name,
            "date": call_date,
            "due_date": call_date,
            "fiscal_year_id": ctx["fy_id"],
            "description": name,
            "total_amount": total,
            "call_type": call_type,
            "distribution_key_id": ctx["key"],
            "copropriete_id": ctx["cid"],
        })
        assert resp.status_code == 200, resp.text
        return resp.json()


# =========================================================================
# SCENARIO 1 : PROVISIONS Q3 emis apres mutation 01/10
# =========================================================================
async def _scenario_late_provisions_call_returns_to_seller():
    """
    - Lot Matexi. Mutation Matexi -> NouvelAcheteur le 01/10/2025.
    - Puis appel Q3 2025 (name 'Trimestriel 3/4') date 20/10/2025.
      period_start=01/07, period_end=30/09 (deduit du nom).
    - Attendu : owner_id = Matexi (vendeur, proprietaire durant Q3).
    - Aucune OD MUT-P (mutation posterieure a period_end).
    """
    ctx = await _base_setup("iter90cg-prov")
    try:
        await _do_mutation(ctx, "2025-10-01")

        call_doc = await _post_call(
            ctx, "Trimestriel 3/4 - 2025", "2025-10-20", 372.0, "provisions"
        )

        dist = call_doc.get("distribution", [])
        assert len(dist) == 1
        assert dist[0]["owner_id"] == ctx["seller"], (
            f"REGRESSION iter90cg : appel Q3 emis en retard doit revenir au "
            f"VENDEUR (Matexi, proprietaire durant Q3). Obtenu owner_id="
            f"{dist[0]['owner_id']} (attendu {ctx['seller']}). "
            f"period_end={call_doc.get('period_end')}, "
            f"call_date={call_doc.get('date')}."
        )
        assert round(dist[0]["amount"], 2) == 372.0

        # Pas d'OD MUT-P (mutation sd=01/10 > period_end=30/09)
        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 0, (
            f"Aucune OD MUT-P attendue : mutation posterieure a la periode. "
            f"Obtenu : {[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : FONDS DE RESERVE emis apres mutation posterieure a la periode
# =========================================================================
async def _scenario_late_reserve_call_returns_to_seller():
    """
    - Lot Matexi. Mutation Matexi -> NouvelAcheteur le 01/10/2025.
    - Appel fonds de reserve annuel date 15/11/2025.
      period_end sera calcule via fiscal year (31/12/2025 sur derniere partie).
      MAIS le call_type=='reserve' avec un nom generique aura peut-etre pas de
      period_start/period_end deduit correctement. On teste donc le cas ou
      period_end < call_date pour valider le rebind sur period_end.

    Fallback : on force la date d'appel a etre APRES la mutation et la periode
    a etre AVANT la mutation. Comportement fixe : owner = vendeur.
    """
    ctx = await _base_setup("iter90cg-res")
    try:
        await _do_mutation(ctx, "2025-10-01")

        # Force un nom qui donne une periode Q3 : "Trimestriel 3/4"
        # Le _compute_period utilise l'heuristique du nom.
        call_doc = await _post_call(
            ctx, "Trimestriel 3/4 - Fonds reserve 2025", "2025-10-20", 500.0, "reserve"
        )

        dist = call_doc.get("distribution", [])
        assert len(dist) == 1
        assert dist[0]["owner_id"] == ctx["seller"], (
            f"REGRESSION iter90cg : appel reserve emis en retard sur periode "
            f"Q3 doit revenir au VENDEUR (Matexi). Obtenu owner_id="
            f"{dist[0]['owner_id']}. period_end={call_doc.get('period_end')}, "
            f"call_date={call_doc.get('date')}."
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : FONDS DE ROULEMENT emis apres mutation posterieure a la periode
# =========================================================================
async def _scenario_late_roulement_call_returns_to_seller():
    """Idem scenario 2 mais avec call_type='roulement'."""
    ctx = await _base_setup("iter90cg-roul")
    try:
        await _do_mutation(ctx, "2025-10-01")

        call_doc = await _post_call(
            ctx, "Trimestriel 3/4 - Fonds roulement 2025", "2025-10-20", 800.0, "roulement"
        )

        dist = call_doc.get("distribution", [])
        assert len(dist) == 1
        assert dist[0]["owner_id"] == ctx["seller"], (
            f"REGRESSION iter90cg : appel roulement Q3 emis apres mutation "
            f"01/10 doit revenir au VENDEUR (Matexi). Obtenu {dist[0]['owner_id']}. "
            f"period_end={call_doc.get('period_end')}, "
            f"call_date={call_doc.get('date')}."
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : NON-REGRESSION - cas nominal (call_date == period_start)
# =========================================================================
async def _scenario_nominal_case_unchanged():
    """
    - Mutation 01/10/2025 (Matexi -> NouvelAcheteur).
    - Appel Q4 2025 date 01/10/2025 (period 01/10-31/12).
    Attendu : owner = NouvelAcheteur (owner-at-call-date, cas iter90cd).
    Verifie que le fix iter90cg ne casse pas ce cas.
    """
    ctx = await _base_setup("iter90cg-nominal")
    try:
        await _do_mutation(ctx, "2025-10-01")

        call_doc = await _post_call(
            ctx, "Trimestriel 4/4 - 2025", "2025-10-01", 372.0, "provisions"
        )

        dist = call_doc.get("distribution", [])
        assert len(dist) == 1
        # 01/10 = jour de la mutation, acheteur devient proprietaire ce jour.
        # min(01/10, 31/12) = 01/10 -> owner = NouvelAcheteur.
        assert dist[0]["owner_id"] == ctx["buyer"], (
            f"REGRESSION : cas nominal casse. Attendu buyer={ctx['buyer']}, "
            f"obtenu {dist[0]['owner_id']}. call_date=2025-10-01, "
            f"period_end={call_doc.get('period_end')}."
        )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 5 : APPEL STRADDLING (fix iter90cf inchange)
# =========================================================================
async def _scenario_straddling_unchanged():
    """
    - Mutation 15/11/2025 (mi-Q4).
    - Appel Q4 2025 date 01/10/2025 (periode 01/10-31/12).
    Attendu :
    - owner-at-call-date = Matexi (01/10 < 15/11) -> 100% Matexi
    - OD MUT-P separee : 47j (15/11-31/12) transferes vers acheteur.
    """
    ctx = await _base_setup("iter90cg-straddle")
    try:
        await _do_mutation(ctx, "2025-11-15")

        call_doc = await _post_call(
            ctx, "Trimestriel 4/4 - 2025", "2025-10-01", 372.0, "provisions"
        )

        dist = call_doc.get("distribution", [])
        # min(01/10, 31/12) = 01/10 -> owner au 01/10 = Matexi (avant mutation)
        assert dist[0]["owner_id"] == ctx["seller"], (
            f"REGRESSION straddle : appel Q4 emis avant mutation Q4 doit avoir "
            f"owner = Matexi. Obtenu {dist[0]['owner_id']}."
        )

        mut_ods = await ctx["db"].journal_entries.find({
            "copropriete_id": ctx["cid"],
            "source_subtype": "prorata_post_mutation",
        }, {"_id": 0}).to_list(10)
        assert len(mut_ods) == 1, (
            f"REGRESSION iter90cf : OD MUT-P attendue pour appel straddling. "
            f"Obtenu : {[(o.get('description'), o.get('total_debit')) for o in mut_ods]}"
        )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_late_provisions_call_returns_to_seller():
    asyncio.run(_scenario_late_provisions_call_returns_to_seller())


def test_late_reserve_call_returns_to_seller():
    asyncio.run(_scenario_late_reserve_call_returns_to_seller())


def test_late_roulement_call_returns_to_seller():
    asyncio.run(_scenario_late_roulement_call_returns_to_seller())


def test_nominal_case_unchanged():
    asyncio.run(_scenario_nominal_case_unchanged())


def test_straddling_unchanged():
    asyncio.run(_scenario_straddling_unchanged())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
