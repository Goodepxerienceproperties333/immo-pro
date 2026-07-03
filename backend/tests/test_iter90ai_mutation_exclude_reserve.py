"""
Iter90ai : Fonds de reserve exclu du decompte de mutation.

Regles metier :
A) Fonds de reserve vote avant la date de mutation -> 100% a charge vendeur,
   l'acheteur ne paie jamais.
B) Fonds de reserve : JAMAIS de transfert vendeur/acheteur (ni via OD mutation
   MUT-P, ni via prorata, ni via MUT-F futur).
C) Fonds de roulement : transfert unique a la date de mutation, jamais
   proratise (deja conforme via bloc 1 = MUT-R).
D) Seules les provisions pour charges sont proratisees par date de mutation.

Bug initial : quand un call_type='provisions' contient reserve_amount > 0
(cas legacy : injection reserve dans un appel provisions), la part reserve
etait incluse dans les OD MUT-P (prorata) et MUT-F (futurs).

Scenarios de test :
1. Appel provisions pur (reserve_amount=0) -> comportement inchange.
2. Appel provisions avec reserve_amount injecte -> quote-part lot reduite
   par ((total-reserve)/total) avant prorata et avant appel futur.
3. Appel autonome call_type='reserve' -> deja exclu au filtre L893.
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


async def _get_admin_token(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return resp.json().get("access_token") or resp.json().get("token")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(name: str, reserve_amount: float = 0.0):
    """Setup : ACP + 1 lot + vendeur V + acheteur A + 1 appel provisions avec
    ou sans reserve_amount injecte. Le sale_dt est fixe au 2026-01-15 (avant
    la periode Q2 pour les appels futurs)."""
    db = await _mongo()
    cid = f"iter90ai-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    v_id = f"v-{uuid.uuid4()}"
    a_id = f"a-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": name, "reference": "T-" + name[:10], "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier V", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier A", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": v_id, "name": "Vendeur", "auxiliary_code": "C0001",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": a_id, "name": "Acheteur", "auxiliary_code": "C0002",
         "copropriete_ids": [cid], "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # Lot appartient actuellement au vendeur (mutation pas encore effectuee)
    await db.lots.insert_one({
        "id": lot_id, "number": "L001", "owner_id": v_id, "owner_ids": [v_id],
        "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "L001"}],
    })
    # Un appel provisions Q1 2026 (01-01 -> 31-03, 90 jours) - couvre sale_date 15-01
    # Total 1200 EUR dont reserve_amount potentiellement injectee.
    await db.fund_calls.insert_one({
        "id": f"fc-{uuid.uuid4()}", "copropriete_id": cid, "budget_id": None,
        "name": "Q1 2026", "date": "2026-01-01",
        "period_start": "2026-01-01", "period_end": "2026-03-31",
        "call_type": "provisions",
        "total_amount": 1200.0,
        "reserve_amount": reserve_amount,
        "distribution_key_id": key_id,
        "distribution": [{
            "lot_id": lot_id, "owner_id": v_id, "amount": 1200.0,
            "share": 1000.0, "paid": False,
        }],
    })
    # Fonds de roulement inexistant (evite d'interferer)
    return {"db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
            "v": v_id, "a": a_id, "key": key_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "mutation_records"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["v"], ctx["a"]]}})


async def _mutate_preview(ctx):
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _get_admin_token(client)
        resp = await client.post(
            f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate-preview",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_owner_id": ctx["a"], "sale_date": "2026-01-15"},
        )
    return resp


async def _scenario_pure_provisions_no_change():
    """reserve_amount=0 -> comportement inchange (regression).
    Convention prorata: sale_dt inclus cote acheteur -> days_after = 76 jours
    (Jan 15 au 31 Mars inclus)."""
    ctx = await _setup("iter90ai_pure", reserve_amount=0.0)
    try:
        resp = await _mutate_preview(ctx)
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        # Prorata Q1 : total_days = 90, days_after = 76 (sale_dt inclus)
        # Amount lot = 1200, prorata acheteur = 1200 * 76/90 = 1013.33
        expected = round(1200 * 76 / 90, 2)  # 1013.33
        assert round(preview["current_period_prorata"], 2) == expected, (
            f"Attendu {expected}, obtenu {preview.get('current_period_prorata')}. Preview={preview}"
        )
    finally:
        await _cleanup(ctx)


async def _scenario_provisions_with_reserve_excluded():
    """reserve_amount=200 sur total 1200 -> quote-part = 1000 avant prorata.
    Prorata acheteur = 1000 * 76/90 = 844.44 (pas 1013.33)."""
    ctx = await _setup("iter90ai_reserve", reserve_amount=200.0)
    try:
        resp = await _mutate_preview(ctx)
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        # amount_lot ajuste = 1200 * (1200-200)/1200 = 1000
        # prorata acheteur = 1000 * 76/90 = 844.44
        expected = round(1000 * 76 / 90, 2)  # 844.44
        actual = round(preview["current_period_prorata"], 2)
        assert actual == expected, (
            f"Attendu {expected}, obtenu {actual}. "
            f"La part reserve n'a pas ete exclue. Preview={preview}"
        )
    finally:
        await _cleanup(ctx)


async def _scenario_standalone_reserve_ignored():
    """Appel autonome call_type='reserve' -> aucune ecriture de mutation
    (deja filtre au niveau L893)."""
    ctx = await _setup("iter90ai_standalone", reserve_amount=0.0)
    try:
        # Retirer l'appel provisions et le remplacer par un appel reserve pur
        await ctx["db"].fund_calls.delete_many({"copropriete_id": ctx["cid"]})
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-r-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Reserve travaux", "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "reserve",
            "total_amount": 5000.0, "reserve_amount": 5000.0,
            "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["v"], "amount": 5000.0,
                "share": 1000.0, "paid": False,
            }],
        })
        resp = await _mutate_preview(ctx)
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        # Prorata current call = 0 (aucun appel provisions)
        assert preview["current_period_prorata"] == 0.0, (
            f"Appel reserve standalone ne doit generer aucun prorata. Obtenu: {preview}"
        )
        # Aucun appel futur
        assert preview.get("future_calls_total", 0) == 0.0
    finally:
        await _cleanup(ctx)


def test_pure_provisions_unchanged():
    asyncio.run(_scenario_pure_provisions_no_change())


def test_provisions_with_reserve_excluded():
    asyncio.run(_scenario_provisions_with_reserve_excluded())


def test_standalone_reserve_ignored():
    asyncio.run(_scenario_standalone_reserve_ignored())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
