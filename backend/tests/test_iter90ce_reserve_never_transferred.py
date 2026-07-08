"""
Iter90ce : REGRESSION LOCK - Les fonds de reserve ne sont JAMAIS transferes
lors d'une mutation entre vendeur et acheteur.

Cette regle metier est absolue :
- Les fonds de reserve sont classe 1 permanent, ils suivent le LOT et non le
  proprietaire au sens comptable. Cependant, en droit belge (art. 3.86 CC),
  toute quote-part deja versee au fonds de reserve reste ACQUISE au syndicat.
  Un vendeur ne peut pas la reprendre, mais un acheteur n'en herite pas
  comptablement via ecriture de mutation.
- Consequence : lors d'une mutation, seuls le fonds de ROULEMENT et le prorata
  des PROVISIONS de l'exercice en cours sont transferes. Le fonds de reserve
  reste 100% chez le vendeur (sur le compte tier historique).
- Cas legacy (data pre-iter90ai) : un appel call_type='provisions' peut avoir
  un reserve_amount > 0 injecte. Meme dans ce cas, la portion "reserve" ne doit
  PAS etre proratisee ni transferee.

Ce test verrouille le comportement au niveau du :
1. Endpoint /api/lots/{id}/mutate-preview (calculs)
2. Endpoint /api/lots/{id}/mutate (ecritures OD reellement passees)
3. Champ mutation_record.total_transfer

Chaque scenario est INDEPENDANT et cleanup complet en cas d'echec.
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


async def _login_admin(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _base_setup(prefix: str):
    """ACP + FY + 1 lot + vendeur/acheteur + cle par defaut."""
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
        "id": fy_id, "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "copropriete_id": cid, "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "160", "name": "Reserve", "class_num": 1, "copropriete_id": cid},
        {"number": "400000", "name": "Prov", "class_num": 4, "copropriete_id": cid},
        {"number": "401000", "name": "Reserve appel", "class_num": 4, "copropriete_id": cid},
        {"number": "4100001", "name": "Tier Seller", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Tier Buyer", "class_num": 4, "copropriete_id": cid},
        {"number": "700000", "name": "Vt Prov", "class_num": 7, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": seller_id, "name": "Vendeur", "last_name": "Vendeur",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": buyer_id, "name": "Acheteur", "last_name": "Acheteur",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "L001", "owner_id": seller_id,
        "owner_ids": [seller_id], "copropriete_id": cid, "quotity": 1000.0,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 1000.0, "lot_number": "L001"}],
    })
    return {
        "db": db, "cid": cid, "fy_id": fy_id, "lot": lot_id,
        "seller": seller_id, "buyer": buyer_id, "key": key_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]; cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    for coll in ("fiscal_years", "pcmn_accounts", "lots", "fund_calls",
                 "journal_entries", "distribution_keys", "mutations",
                 "mutation_records", "budgets"):
        await db[coll].delete_many({"copropriete_id": cid})
    await db.owners.delete_many({"id": {"$in": [ctx["seller"], ctx["buyer"]]}})


# =========================================================================
# SCENARIO 1 : Standalone RESERVE call (avant ET apres mutation) -> zero transfert
# =========================================================================
async def _scenario_standalone_reserve_calls_never_transferred():
    """Deux appels standalone call_type='reserve' :
       - Un avant la mutation (dans la periode courante).
       - Un apres la mutation (futur).
       Attendu : preview + mutate genere ZERO prorata et ZERO OD sur ces appels.
       Seul le fonds de roulement (si present) est transfere.
    """
    ctx = await _base_setup("iter90ce-standalone")
    try:
        # Appel reserve Q1 (couvre la sale_date)
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-r1-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Reserve Q1", "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "reserve", "total_amount": 3000.0,
            "reserve_amount": 3000.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 3000.0, "share": 1000.0, "paid": False,
            }],
        })
        # Appel reserve futur Q3 (post-mutation)
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-r2-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Reserve Q3", "date": "2026-07-01",
            "period_start": "2026-07-01", "period_end": "2026-09-30",
            "call_type": "reserve", "total_amount": 2000.0,
            "reserve_amount": 2000.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 2000.0, "share": 1000.0, "paid": False,
            }],
        })

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login_admin(client)

            # 1) Preview
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate-preview",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-02-15"},
            )
            assert resp.status_code == 200, resp.text
            preview = resp.json()
            assert preview["current_period_prorata"] == 0.0, (
                f"REGRESSION : appel reserve standalone ne doit generer AUCUN "
                f"prorata. Obtenu : {preview['current_period_prorata']}. "
                f"Details : {preview.get('current_period_details')}"
            )
            assert preview["future_calls_total"] == 0.0, (
                f"REGRESSION : appel reserve standalone futur ne doit generer "
                f"AUCUN transfert. Obtenu : {preview['future_calls_total']}. "
                f"Futures : {preview.get('future_calls')}"
            )
            assert preview["total_transfer"] == 0.0, (
                f"REGRESSION : aucun transfert attendu (pas de roulement, que "
                f"des reserves). Obtenu : {preview['total_transfer']}"
            )

            # 2) Mutation reelle
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-02-15",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text

            # 3) Verifier journal_entries : aucune OD de mutation ne doit exister
            #    car aucun transfert n'a lieu (pas de roulement, pas de provisions).
            mut_entries = await ctx["db"].journal_entries.find(
                {"copropriete_id": ctx["cid"], "source_type": "lot_mutation"},
                {"_id": 0},
            ).to_list(100)
            assert len(mut_entries) == 0, (
                f"REGRESSION : aucune OD de mutation ne devrait exister quand "
                f"il n'y a que des appels de reserve. Obtenu : "
                f"{[(e.get('source_subtype'), e.get('total_debit')) for e in mut_entries]}"
            )

            # 4) Verifier la mutation record : total_transfer=0, pas de future_calls
            lot_doc = await ctx["db"].lots.find_one({"id": ctx["lot"]}, {"_id": 0})
            muts = lot_doc.get("mutations", [])
            assert len(muts) == 1
            mr = muts[0]
            assert mr["total_transfer"] == 0.0, (
                f"REGRESSION : mutation_record.total_transfer doit etre 0 quand "
                f"seuls des appels de reserve existent. Obtenu : {mr['total_transfer']}"
            )
            assert mr.get("future_calls_total", 0) == 0.0, (
                f"REGRESSION : future_calls_total doit etre 0. "
                f"Obtenu : {mr.get('future_calls_total')}"
            )
            assert mr.get("current_period_prorata", 0) == 0.0
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : Provisions call avec reserve_amount injecte (data legacy)
# =========================================================================
async def _scenario_provisions_with_injected_reserve_excludes_reserve_portion():
    """Un appel 'provisions' 1200 EUR contenant reserve_amount=200 :
       - amount_lot doit etre reduit a 1000 avant prorata (1200 * 1000/1200).
       - Prorata Q1 (76j/90j) = 844.44 (et non 1013.33).
       Cette regle empeche le reserve_amount injecte de contaminer le decompte.
    """
    ctx = await _base_setup("iter90ce-injected")
    try:
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-p-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Provisions Q1 avec reserve injectee",
            "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "provisions",
            "total_amount": 1200.0,
            "reserve_amount": 200.0,  # 200 EUR de reserve injectee (legacy)
            "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 1200.0, "share": 1000.0, "paid": False,
            }],
        })

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login_admin(client)

            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate-preview",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-01-15"},
            )
            assert resp.status_code == 200, resp.text
            preview = resp.json()
            # amount_lot ajuste = 1200 * ((1200-200)/1200) = 1000
            # Prorata acheteur = 1000 * 76/90 = 844.44
            expected = round(1000 * 76 / 90, 2)
            actual = round(preview["current_period_prorata"], 2)
            assert actual == expected, (
                f"REGRESSION : la portion reserve injectee doit etre EXCLUE du "
                f"prorata. Attendu {expected}, obtenu {actual}. "
                f"Details : {preview.get('current_period_details')}"
            )
            # Ecart avec le calcul sans exclusion = ~168.89 EUR de reserve non
            # transferee. Verifions aussi que ce n'est pas 1013.33 (bug).
            wrong_value = round(1200 * 76 / 90, 2)
            assert actual != wrong_value, (
                f"REGRESSION critique : le prorata inclut la portion reserve "
                f"(obtenu {actual} = {wrong_value} sans exclusion)."
            )
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : Mix provisions + reserve standalone -> seul provisions transfere
# =========================================================================
async def _scenario_mixed_calls_only_provisions_transferred():
    """Setup realiste : 1 appel provisions Q1 (600 EUR) + 1 appel reserve
    standalone Q1 (2000 EUR) + 1 appel reserve futur Q4 (1500 EUR).
    Mutation au 15/02. Attendu :
    - Prorata Q1 = 600 * 45/90 = 300 (uniquement sur les provisions)
    - Future calls total = 0 (l'appel Q4 reserve est ignore)
    """
    ctx = await _base_setup("iter90ce-mixed")
    try:
        # Provisions Q1 : 600 EUR
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-prov-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Provisions Q1", "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "provisions", "total_amount": 600.0,
            "reserve_amount": 0.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 600.0, "share": 1000.0, "paid": False,
            }],
        })
        # Reserve standalone Q1 : 2000 EUR (couvre sale_date)
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-res-Q1-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Reserve travaux Q1", "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "reserve", "total_amount": 2000.0,
            "reserve_amount": 2000.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 2000.0, "share": 1000.0, "paid": False,
            }],
        })
        # Reserve futur Q4 : 1500 EUR (apres mutation)
        await ctx["db"].fund_calls.insert_one({
            "id": f"fc-res-Q4-{uuid.uuid4()}", "copropriete_id": ctx["cid"],
            "name": "Reserve travaux Q4", "date": "2026-10-01",
            "period_start": "2026-10-01", "period_end": "2026-12-31",
            "call_type": "reserve", "total_amount": 1500.0,
            "reserve_amount": 1500.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 1500.0, "share": 1000.0, "paid": False,
            }],
        })

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login_admin(client)

            # Preview
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate-preview",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-02-15"},
            )
            assert resp.status_code == 200, resp.text
            preview = resp.json()
            # Prorata Q1 attendu : 600 * 45/90 = 300 (uniquement provisions)
            expected_prorata = round(600 * 45 / 90, 2)  # 300.00
            assert round(preview["current_period_prorata"], 2) == expected_prorata, (
                f"REGRESSION : prorata doit etre calcule UNIQUEMENT sur les "
                f"provisions. Attendu {expected_prorata}, obtenu "
                f"{preview['current_period_prorata']}."
            )
            # Aucun appel futur ne doit apparaitre (le seul futur est reserve)
            assert preview["future_calls_total"] == 0.0, (
                f"REGRESSION : appel reserve futur ne doit PAS apparaitre dans "
                f"future_calls_total. Obtenu : {preview['future_calls_total']}. "
                f"Details futures : {preview.get('future_calls')}"
            )
            # Details prorata : aucun call_type='reserve' ne doit y figurer
            for d in (preview.get("current_period_details") or []):
                name = (d.get("fund_call_name") or "").lower()
                assert "reserve" not in name, (
                    f"REGRESSION : un appel reserve apparait dans le prorata : "
                    f"{d}"
                )

            # Mutation reelle
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-02-15",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text

            # Verifier les ODs : aucune ne doit correspondre a un appel reserve
            mut_entries = await ctx["db"].journal_entries.find(
                {"copropriete_id": ctx["cid"], "source_type": "lot_mutation"},
                {"_id": 0},
            ).to_list(100)
            # On s'attend a EXACTEMENT 1 OD (prorata Q1 sur provisions).
            # Pas d'OD pour reserve Q1 ni pour reserve Q4.
            assert len(mut_entries) == 1, (
                f"REGRESSION : une seule OD attendue (prorata provisions Q1). "
                f"Obtenu : {[(e.get('source_subtype'), e.get('total_debit'), e.get('description')) for e in mut_entries]}"
            )
            entry = mut_entries[0]
            assert entry["source_subtype"] == "prorata"
            assert round(entry["total_debit"], 2) == expected_prorata

            # Verifier la mutation record
            lot_doc = await ctx["db"].lots.find_one({"id": ctx["lot"]}, {"_id": 0})
            mr = lot_doc["mutations"][0]
            assert round(mr["total_transfer"], 2) == expected_prorata
            assert mr["future_calls_total"] == 0.0
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : Verification que la RESERVE reste 100% avec le VENDEUR
# =========================================================================
async def _scenario_reserve_stays_100_percent_with_seller():
    """Verif inverse : apres mutation, le solde du compte tier VENDEUR doit
    contenir 100% de l'appel reserve pre-mutation (pas de scission).
    Le solde du compte tier ACHETEUR pour cet appel doit etre 0.
    """
    ctx = await _base_setup("iter90ce-solde")
    try:
        # Un appel reserve pre-mutation dont l'ecriture VE existe deja
        fc_id = f"fc-r-{uuid.uuid4()}"
        await ctx["db"].fund_calls.insert_one({
            "id": fc_id, "copropriete_id": ctx["cid"],
            "name": "Reserve travaux", "date": "2026-01-01",
            "period_start": "2026-01-01", "period_end": "2026-03-31",
            "call_type": "reserve", "total_amount": 5000.0,
            "reserve_amount": 5000.0, "distribution_key_id": ctx["key"],
            "distribution": [{
                "lot_id": ctx["lot"], "owner_id": ctx["seller"],
                "amount": 5000.0, "share": 1000.0, "paid": False,
            }],
        })
        # VE simulee de l'appel reserve : debit tier vendeur 5000 / credit 160
        await ctx["db"].journal_entries.insert_one({
            "id": f"ve-{uuid.uuid4()}", "journal_type": "VE",
            "date": "2026-01-01", "reference": f"AF-RESERVE",
            "description": "VE Reserve travaux",
            "lines": [
                {"account_number": "4100001", "debit": 5000.0, "credit": 0.0,
                 "third_party_id": ctx["seller"]},
                {"account_number": "160", "debit": 0.0, "credit": 5000.0},
            ],
            "total_debit": 5000.0, "total_credit": 5000.0,
            "copropriete_id": ctx["cid"], "auto_generated": True,
            "source_type": "fund_call", "source_id": fc_id,
        })

        async with httpx.AsyncClient(timeout=30) as client:
            hdr = await _login_admin(client)
            resp = await client.post(
                f"{BACKEND_URL}/api/lots/{ctx['lot']}/mutate",
                headers=hdr,
                json={"new_owner_id": ctx["buyer"], "sale_date": "2026-02-15",
                      "sale_price": 250000.0},
            )
            assert resp.status_code == 200, resp.text

            # Calcul soldes : compte 4100001 (vendeur) doit rester debiteur de 5000
            # Aucune OD de mutation ne doit venir mouvementer le compte reserve.
            entries = await ctx["db"].journal_entries.find(
                {"copropriete_id": ctx["cid"]}, {"_id": 0}
            ).to_list(1000)
            seller_debit = 0.0
            seller_credit = 0.0
            buyer_debit = 0.0
            buyer_credit = 0.0
            for e in entries:
                for line in e.get("lines", []):
                    tp = line.get("third_party_id")
                    if tp == ctx["seller"]:
                        seller_debit += float(line.get("debit", 0) or 0)
                        seller_credit += float(line.get("credit", 0) or 0)
                    elif tp == ctx["buyer"]:
                        buyer_debit += float(line.get("debit", 0) or 0)
                        buyer_credit += float(line.get("credit", 0) or 0)
            seller_balance = round(seller_debit - seller_credit, 2)
            buyer_balance = round(buyer_debit - buyer_credit, 2)
            # Vendeur : doit rester debiteur des 5000 EUR de la reserve
            assert seller_balance == 5000.0, (
                f"REGRESSION : le solde tier vendeur doit rester +5000 EUR "
                f"(reserve non transferee). Obtenu : {seller_balance}. "
                f"Debit total vendeur={seller_debit}, credit={seller_credit}"
            )
            # Acheteur : ne doit avoir AUCUN mouvement reserve
            assert buyer_balance == 0.0, (
                f"REGRESSION : l'acheteur ne doit avoir aucun mouvement reserve. "
                f"Obtenu balance={buyer_balance}, debit={buyer_debit}, credit={buyer_credit}"
            )
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_standalone_reserve_calls_never_transferred():
    asyncio.run(_scenario_standalone_reserve_calls_never_transferred())


def test_provisions_with_injected_reserve_excludes_reserve_portion():
    asyncio.run(_scenario_provisions_with_injected_reserve_excludes_reserve_portion())


def test_mixed_calls_only_provisions_transferred():
    asyncio.run(_scenario_mixed_calls_only_provisions_transferred())


def test_reserve_stays_100_percent_with_seller():
    asyncio.run(_scenario_reserve_stays_100_percent_with_seller())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
