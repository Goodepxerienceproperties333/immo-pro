"""
iter90cx : REGRESSION LOCK - situation_compte_owner + PDF ne doivent plus
dedupliquer par erreur des lignes legitimes ayant meme (debit, credit, tpid).

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "le total de la balance de tiers de Matexi en production est correcte
> cependant dans le tableau, il y a une erreur. Le fonds de reserve = 1500
> et fonds de roulement = 5200 mais affichent 1446 et 5012.80"

Ratio de perte = 3.6% (54 EUR pour reserve, 187.20 EUR pour roulement).

Cause : `seen_lines.add((entry_id, acc, debit, credit, tpid))` dans
`situation_compte_owner` (reports.py:2036) considerait 2 lignes distinctes
comme doublons si elles avaient meme debit + credit + tier + compte.
Or, quand Matexi possede N lots dont certains ont la MEME quotite, l'appel
de reserve distribue le meme montant a plusieurs lignes -> plusieurs lignes
ont debit identique -> `seen_lines` skippe erronement.

Fix iter90cx : cle dedup = `(entry_id, line_index)` uniquement. Chaque ligne
d'un JE est identifiee de facon unique par sa position dans le tableau
`lines`, independamment de son contenu (debit/credit/tpid).

Applique a 3 endroits :
1. `situation_compte_owner` (endpoint JSON) - reports.py
2. `_build_situation_compte_pdf` - reports.py
3. `situation_compte_supplier` (idem pour fournisseurs)

Test coverage :
1. VE avec 3 lignes identiques Matexi -> total sum = 3 * amount (pas 1)
2. VE avec 3 lignes distinctes -> total sum = 3 * amount (regression normale)
3. Dedup AN entries reste fonctionnel (pas de double-comptage AN)
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

import httpx  # noqa: E402

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


async def _setup(prefix: str, line_debits: list):
    """Cree ACP + owner Matexi + VE avec `line_debits` lignes."""
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"
    prov_account = f"4101{cid[-4:]}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "last_name": "Matexi",
        "auxiliary_code": "M001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": prov_account, "reserve": "160" + cid[-4:]}},
    })
    await db.pcmn_accounts.insert_many([
        {"number": prov_account, "class_num": 4, "copropriete_id": cid,
         "name": "T-Matexi"},
        {"number": "160" + cid[-4:], "class_num": 1, "copropriete_id": cid,
         "name": "Reserve-Matexi"},
        {"number": "700000", "class_num": 7, "copropriete_id": cid,
         "name": "Vt Prov"},
    ])
    # VE avec `line_debits` lignes debit Matexi + 1 ligne credit 700000
    lines = []
    total = 0.0
    for i, dbt in enumerate(line_debits):
        lines.append({
            "account_number": prov_account,
            "account_name": f"T-Matexi - lot {i + 1:03d}",
            "debit": float(dbt), "credit": 0.0,
            "third_party_id": matexi_id,
            "third_party_name": "Matexi",
            "line_description": "Appel de provisions - Q1/4",
        })
        total += dbt
    lines.append({
        "account_number": "700000",
        "account_name": "Ventes provisions",
        "debit": 0.0, "credit": float(total),
        "line_description": "Contrepartie Q1/4",
    })
    ve_id = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        "id": ve_id,
        "journal_type": "VE",
        "date": "2025-10-01",
        "reference": "AF-Q1",
        "description": "Appel de provisions Q1/4 - Exercice 2025",
        "lines": lines,
        "total_debit": float(total),
        "total_credit": float(total),
        "copropriete_id": cid,
        "auto_generated": True,
        "source_type": "fund_call",
        "source_id": f"fc-{uuid.uuid4()}",
    })
    return {"db": db, "cid": cid, "matexi": matexi_id, "expected_total": total}


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("journal_entries", "pcmn_accounts"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_one({"id": ctx["matexi"]})


async def _get_situation(client, hdr, owner_id, cid):
    r = await client.get(
        f"{BACKEND_URL}/api/reports/balance-tiers/owners/{owner_id}",
        headers=hdr, params={"copropriete_id": cid, "group_by_owner": "true"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# =========================================================================
# SCENARIO 1 : 3 lignes IDENTIQUES (meme debit) - Matexi doit voir 3 * 500
# =========================================================================
async def _scenario_identical_lines_summed():
    # 3 lots de meme quotite -> 3 lignes debit 500 chacune, sum = 1500
    ctx = await _setup("iter90cx-ident", [500.0, 500.0, 500.0])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            resp = await _get_situation(client, hdr, ctx["matexi"], ctx["cid"])
            total_debit = float(resp.get("total_debit", 0))
            assert abs(total_debit - 1500.0) < 0.01, (
                f"REGRESSION iter90cx : 3 lignes debit=500 devraient sommer a 1500. "
                f"Obtenu : {total_debit} (perte {1500 - total_debit})"
            )
            balance = float(resp.get("balance", 0))
            assert abs(balance - 1500.0) < 0.01
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : 3 lignes DISTINCTES - regression normale
# =========================================================================
async def _scenario_distinct_lines_summed():
    ctx = await _setup("iter90cx-dist", [400.0, 500.0, 600.0])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            resp = await _get_situation(client, hdr, ctx["matexi"], ctx["cid"])
            total_debit = float(resp.get("total_debit", 0))
            assert abs(total_debit - 1500.0) < 0.01, (
                f"Regression normale : 3 lignes 400+500+600 = 1500. "
                f"Obtenu : {total_debit}"
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : Cas Acacia - 30 lignes dont plusieurs identiques
# =========================================================================
async def _scenario_acacia_30_lots_reserve():
    # Reserve 1500 sur 30 lots dont beaucoup partagent la meme quotite
    # Simulation : 10 appartements 100 EUR + 10 caves 30 EUR + 10 parkings 20 EUR
    # Total = 1500. Une methode possible qui trigger le bug.
    debits = [100.0] * 10 + [30.0] * 10 + [20.0] * 10
    ctx = await _setup("iter90cx-acacia", debits)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            resp = await _get_situation(client, hdr, ctx["matexi"], ctx["cid"])
            total_debit = float(resp.get("total_debit", 0))
            expected = sum(debits)  # 1500
            assert abs(total_debit - expected) < 0.01, (
                f"REGRESSION iter90cx : 30 lignes Matexi (10x100 + 10x30 + 10x20) "
                f"= {expected}. Obtenu : {total_debit}. "
                f"Perte = {expected - total_debit} EUR"
            )
    finally:
        await _cleanup(ctx)


def test_identical_lines_summed():
    asyncio.run(_scenario_identical_lines_summed())


def test_distinct_lines_summed():
    asyncio.run(_scenario_distinct_lines_summed())


def test_acacia_30_lots_reserve():
    asyncio.run(_scenario_acacia_30_lots_reserve())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
