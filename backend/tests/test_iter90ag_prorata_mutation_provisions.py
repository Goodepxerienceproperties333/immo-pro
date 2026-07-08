"""
Iter90ag : Prorata mutation pour provisions de charges.

Regle metier (validee par user) :
- Provisions (call_type='provisions') = split prorata TEMPORIS entre vendeur
  et acheteur si mutation dans la periode de l'appel.
- Reserve / roulement : PAS de split (one-shot injections).

Exemple canonique :
- Mutation V -> A le 2026-03-15.
- Appel Q1 : periode 2026-01-01 -> 2026-03-31 (90 jours).
- Amount total lot = 900 EUR.
- Attendu : V doit 73/90 * 900 = 730 EUR (01-01 -> 14-03 inclus).
             A doit 17/90 * 900 = 170 EUR (15-03 -> 31-03 inclus).

Tests via appel direct de _generate_from_budget (route interne).
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


async def _setup(name: str, mutation_date: str = None):
    """Setup : ACP + 1 lot + vendeur V + acheteur A + budget + optionnellement mutation."""
    db = await _mongo()
    cid = f"iter90ag-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    budget_id = f"bg-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    v_id = f"v-{uuid.uuid4()}"
    a_id = f"a-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": name, "reference": "T-" + name[:10], "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "600", "name": "Charges", "class_num": 6, "copropriete_id": cid},
        {"number": "410", "name": "Coprop", "class_num": 4, "copropriete_id": cid},
        {"number": "70", "name": "Ventes", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": v_id, "name": "Vendeur V", "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": a_id, "name": "Acheteur A", "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    # Le lot appartient DEJA a l'acheteur (mutation deja executee dans le passe)
    current_owner = a_id if mutation_date else v_id
    await db.lots.insert_one({
        "id": lot_id, "number": "L101", "owner_id": current_owner, "owner_ids": [current_owner],
        "copropriete_id": cid, "quotity": 100.0,
    })
    # Cle par defaut avec le lot
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale", "is_default": True,
        "key_type": "quotity", "lots": [{"lot_id": lot_id, "share": 100.0}],
    })
    # Budget approved : 1 ligne de 3600 EUR
    await db.budgets.insert_one({
        "id": budget_id, "fiscal_year_id": fy_id, "copropriete_id": cid,
        "name": "Budget 2026", "status": "approved",
        "lines": [{"account_number": "600", "account_name": "Charges", "amount": 3600,
                   "distribution_key_id": key_id}],
        "total_amount": 3600,
    })
    if mutation_date:
        await db.mutations.insert_one({
            "id": f"mut-{uuid.uuid4()}", "copropriete_id": cid,
            "lot_id": lot_id, "from_owner_id": v_id, "to_owner_id": a_id,
            "sale_date": mutation_date, "created_at": mutation_date,
        })
    return {"db": db, "cid": cid, "budget_id": budget_id,
            "lot": lot_id, "v": v_id, "a": a_id, "key": key_id, "fy_id": fy_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "budgets", "fund_calls", "journal_entries",
                 "lots", "distribution_keys", "pcmn_accounts", "mutations"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["v"], ctx["a"]]}})


async def _preview_calls(ctx, frequency: int):
    """Appelle POST /api/fund-calls/preview-from-budget et retourne les appels."""
    payload = {
        "budget_id": ctx["budget_id"],
        "copropriete_id": ctx["cid"],
        "frequency": frequency,
        "start_date": "2026-01-01",
        "due_offset_days": 30,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _get_admin_token(client)
        resp = await client.post(
            f"{BACKEND_URL}/api/fund-calls/preview-from-budget",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _scenario_prorata_mutation_mid_q1():
    """iter90cd : mutation le 2026-03-15 pendant la periode Q1 (01-01 -> 31-03).
    Nouvelle regle metier belge : l'appel emis le 01-01 (AVANT la mutation) est
    entierement impute au proprietaire a la DATE D'EMISSION (le Vendeur).
    Aucune ventilation entre vendeur et acquereur n'est effectuee au niveau de
    l'appel. La repartition prorata temporis est realisee SEPAREMENT par l'OD
    "Mutation Prorata" lors de la mutation (properties.py) - non testee ici.

    Q2/Q3/Q4 : emis apres la mutation -> 100% Acheteur.
    """
    ctx = await _setup("iter90ag_mid_q1", mutation_date="2026-03-15")
    try:
        result = await _preview_calls(ctx, frequency=4)  # Trimestriel
        calls = result if isinstance(result, list) else result.get("calls", [])
        assert len(calls) == 4, f"Attendu 4 appels, obtenu {len(calls)}"

        # Q1 emis 01-01 (avant mutation 15-03) -> 100% Vendeur
        q1 = calls[0]
        assert q1["date"] == "2026-01-01"
        assert q1["period_end"] == "2026-03-31"
        q1_entries = q1["distribution"]
        by_owner = {e["owner_id"]: e for e in q1_entries}
        assert ctx["v"] in by_owner, f"Vendeur present (100%): {q1_entries}"
        assert ctx["a"] not in by_owner, (
            f"Acheteur ABSENT en Q1 (nouvelle regle iter90cd) : {q1_entries}"
        )
        assert by_owner[ctx["v"]]["amount"] == 900.00, by_owner

        # Q2 emis 01-04 (apres mutation) -> 100% Acheteur
        q2 = calls[1]
        by_owner_q2 = {e["owner_id"]: e for e in q2["distribution"]}
        assert ctx["v"] not in by_owner_q2, "Vendeur ne doit plus apparaitre en Q2"
        assert by_owner_q2[ctx["a"]]["amount"] == 900.00
    finally:
        await _cleanup(ctx)


async def _scenario_mutation_before_period():
    """Mutation avant Q1 (2025-12-15). Toute la periode 2026 = Acheteur seul.
    Aucune ligne prorata en distribution."""
    ctx = await _setup("iter90ag_before", mutation_date="2025-12-15")
    try:
        result = await _preview_calls(ctx, frequency=4)
        calls = result if isinstance(result, list) else result.get("calls", [])
        for c in calls:
            by_owner = {e["owner_id"]: e for e in c["distribution"]}
            assert ctx["v"] not in by_owner, f"Vendeur ne doit pas apparaitre : {by_owner}"
            assert by_owner[ctx["a"]]["amount"] == 900.00
    finally:
        await _cleanup(ctx)


async def _scenario_no_mutation():
    """Sans mutation : distribution = 1 entree par lot avec le current_owner (V ici)."""
    ctx = await _setup("iter90ag_none", mutation_date=None)
    # Sans mutation, on garde V comme current owner via _setup
    try:
        result = await _preview_calls(ctx, frequency=1)  # Annuel
        calls = result if isinstance(result, list) else result.get("calls", [])
        assert len(calls) == 1
        entries = calls[0]["distribution"]
        assert len(entries) == 1
        assert entries[0]["owner_id"] == ctx["v"]
        assert entries[0]["amount"] == 3600.00
        assert "prorata_days" not in entries[0]
    finally:
        await _cleanup(ctx)


def test_prorata_mutation_mid_q1():
    asyncio.run(_scenario_prorata_mutation_mid_q1())


def test_mutation_before_period_no_prorata():
    asyncio.run(_scenario_mutation_before_period())


def test_no_mutation_single_entry():
    asyncio.run(_scenario_no_mutation())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
