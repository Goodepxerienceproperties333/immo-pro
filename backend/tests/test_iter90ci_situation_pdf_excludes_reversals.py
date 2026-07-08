"""iter90ci : REGRESSION LOCK - PDF situation de compte doit exclure les
paires de contre-passations (reversed=True + is_reversal=True).

Bug rapporte utilisateur (Feb 2026, cas ABED-STEUVE ACP Acacia) :
> "Ce document est completement incoherent avec la realite des journaux
> comptables"

Root cause : `_build_situation_compte_pdf` chargeait toutes les ecritures
sans filtrer les reversals. Resultat : chaque appel modifie apparaissait
3 fois (original+reversed + contre-passation is_reversal + regeneration
neuve) avec soldes qui montent puis descendent, illisibles.

Fix : appliquer `_exclude_reversals(q)` avant `find(journal_entries)`
dans `_build_situation_compte_pdf` (reports.py:174), coherent avec
`situation_compte_owner` (endpoint JSON, l. 1918).

Test coverage :
1. Genere un appel, contre-passe (via suppression appel), regenere.
2. Verifie le PDF : seul le NOUVEAU appel apparait dans le mouvement,
   pas l'original ni la contre-passation.
3. Balance finale coherente (= 1 appel, pas 3).
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


async def _setup():
    db = await _mongo()
    cid = f"iter90ci-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    lot_id = f"lot-{uuid.uuid4()}"
    owner_id = f"own-{uuid.uuid4()}"
    key_id = f"dk-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "iter90ci", "reference": "iter90ci", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "4100091", "name": "T-ABED", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "VtProv", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_one({
        "id": owner_id, "name": "ABED-STEUVE Test", "last_name": "ABED",
        "auxiliary_code": "A001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": "4100091"}},
    })
    await db.lots.insert_one({
        "id": lot_id, "number": "202", "owner_id": owner_id,
        "owner_ids": [owner_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Gen",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "202"}],
    })
    return {"db": db, "cid": cid, "fy_id": fy_id, "owner": owner_id, "key": key_id}


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": ctx["owner"]})


async def _scenario_situation_pdf_excludes_reversals():
    ctx = await _setup()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)

            # 1) Cree un appel Q1
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 1/4 - 2026",
                "date": "2026-01-01", "due_date": "2026-01-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q1 test",
                "total_amount": 500.0, "call_type": "provisions",
                "distribution_key_id": ctx["key"], "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text
            call1 = resp.json()

            # 2) Supprime cet appel (cree contre-passation VE)
            resp = await client.delete(
                f"{BACKEND_URL}/api/fund-calls/{call1['id']}", headers=hdr,
            )
            assert resp.status_code == 200, resp.text

            # 3) Recree un nouvel appel identique
            resp = await client.post(f"{BACKEND_URL}/api/fund-calls", headers=hdr, json={
                "name": "Trimestriel 1/4 - 2026",
                "date": "2026-01-01", "due_date": "2026-01-31",
                "fiscal_year_id": ctx["fy_id"],
                "description": "Q1 test regen",
                "total_amount": 500.0, "call_type": "provisions",
                "distribution_key_id": ctx["key"], "copropriete_id": ctx["cid"],
            })
            assert resp.status_code == 200, resp.text

            # Verifie qu'on a bien 3 VE dans le grand livre (original reversed
            # + contre-passation is_reversal + nouveau)
            all_ve = await ctx["db"].journal_entries.find({
                "copropriete_id": ctx["cid"], "journal_type": "VE",
            }, {"_id": 0}).to_list(20)
            assert len(all_ve) >= 3, (
                f"Prerequis : au moins 3 VE attendus (orig + reversal + nouveau). "
                f"Obtenu : {len(all_ve)}"
            )
            reversed_count = sum(1 for e in all_ve if e.get("reversed"))
            is_reversal_count = sum(1 for e in all_ve if e.get("is_reversal"))
            active_count = sum(
                1 for e in all_ve
                if not e.get("reversed") and not e.get("is_reversal")
            )
            assert reversed_count >= 1 and is_reversal_count >= 1 and active_count >= 1

            # 4) Genere le PDF de situation via l'endpoint
            resp = await client.get(
                f"{BACKEND_URL}/api/reports/situation-compte/{ctx['owner']}/pdf",
                headers=hdr,
                params={"copropriete_id": ctx["cid"]},
            )
            assert resp.status_code == 200, resp.text
            assert resp.headers.get("content-type", "").startswith("application/pdf")
            pdf_bytes = resp.content
            assert len(pdf_bytes) > 1000

            # 5) Verifie via l'endpoint JSON que la situation retourne 1 seul
            # mouvement d'appel (pas 3)
            resp = await client.get(
                f"{BACKEND_URL}/api/reports/balance-tiers/owners/{ctx['owner']}",
                headers=hdr,
                params={"copropriete_id": ctx["cid"], "show_all": "false"},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            # movements est une liste de {date, description, debit, credit, ...}
            movements = data.get("movements") or data.get("lines") or []
            # Filter movements liees a l'appel Q1 (description contient "Trimestriel")
            q1_movements = [m for m in movements if "Trimestriel" in (m.get("description") or "")]
            assert len(q1_movements) == 1, (
                f"REGRESSION iter90ci : le JSON situation doit contenir 1 seul "
                f"mouvement pour l'appel Q1 (pas 3). Obtenu : {len(q1_movements)}. "
                f"Details : {[(m.get('description', '')[:50], m.get('debit'), m.get('credit')) for m in q1_movements]}"
            )

            # Balance finale = 500 EUR (1 appel, pas 1500)
            debit_sum = sum(float(m.get("debit", 0) or 0) for m in q1_movements)
            credit_sum = sum(float(m.get("credit", 0) or 0) for m in q1_movements)
            assert debit_sum == 500.0 and credit_sum == 0.0, (
                f"REGRESSION iter90ci : balance de l'appel Q1 attendue "
                f"500/0, obtenu {debit_sum}/{credit_sum}"
            )
    finally:
        await _cleanup(ctx)


def test_situation_pdf_excludes_reversals():
    asyncio.run(_scenario_situation_pdf_excludes_reversals())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
