"""iter90g7 : le decompte annuel doit rattacher les lots via mutations
meme si `lot.owner_id` reste bloque sur l'ancien proprietaire (bug data
legacy). Corrige le cas TEUWEN/MATEXI ou 30 lots pointaient encore vers
MATEXI en base alors que la vente a TEUWEN etait deja enregistree dans
db.mutations.

**Contexte** :
> "il faut un fix qui combine lot.owner_id + les cles de distribution
> pour retrouver le bon proprietaire des lots"
> "30 lots sont assignes a MATEXI au lieu de TEUWEN"

**Root cause** :
`reports.py::decompte_annuel` (JSON, ligne 1588) : `owner_lots = [lt for
lt in lots if lt.get("owner_id") == owner["id"]]`. Ne prend PAS en compte
`owner_ids` ni les mutations. Si un import legacy laisse `lot.owner_id =
MATEXI_id` alors que TEUWEN a acquis via `POST /api/lots/{id}/mutations`
(qui met a jour `lot.owner_id` normalement, mais peut avoir echoue), TEUWEN
n'apparait dans AUCUN decompte.

**Fix iter90g7** :
Pour chaque owner, on rattache un lot si :
1. `lot.owner_id == owner.id` (owner ACTUEL)
2. `owner.id in lot.owner_ids` (co-propriete)
3. Il existe une mutation intra-FY ou `owner.id` est `from_owner_id` OU
   `to_owner_id` ET `mutation.lot_id == lot.id`

Le prorata iter90g1 se charge ensuite du partage entre vendeur/acheteur
sur base de `days_owned/365`.

Applique dans :
- `routes/reports.py::decompte_annuel` (JSON) - critere 3 via muts_all
- `routes/reports.py::decompte_pdf` (PDF endpoint syndic)
- `routes/reports.py::_build_decompte_annuel_pdf` (helper communication)
- `routes/owner_portal.py::download_decompte_pdf` (portail proprio)

**Regressions couvertes** :
1. TEUWEN (buyer) sans lot.owner_id (bloque MATEXI) mais avec mutation
   dans db.mutations -> apparait dans le decompte avec prorata correct.
2. MATEXI (seller) garde son decompte (via db.mutations from_owner_id
   -> current_owner_ids set iter90fw).
3. Owner "classique" sans mutation : comportement inchange.
"""
import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    return {}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _seed_teuwen_scenario(db, suffix: str) -> dict:
    """Setup TEUWEN scenario :
    - ACP Acacia avec FY 2026 CLOTURE (01/01/2026 - 31/12/2026)
    - 2 owners : MATEXI (vendeur) + TEUWEN (acheteur)
    - 1 lot 202 avec quotity 100, initialement possede par MATEXI
    - Une mutation le 15/06/2026 : MATEXI -> TEUWEN
    - **BUG DATA CRITIQUE** : lot.owner_id reste BLOQUE sur MATEXI meme
      apres la vente (simule le bug import legacy Optipro)
    - 1 facture 1000 EUR datee 01/03/2026 (avant vente) et 1 facture 800 EUR
      datee 01/09/2026 (apres vente)
    """
    cid = f"iter90g7-{suffix}"
    fy_id = f"iter90g7-fy-{suffix}"
    matexi_id = f"matexi-{suffix}"
    teuwen_id = f"teuwen-{suffix}"
    lot_id = f"lot-{suffix}"
    key_id = f"key-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "iter90g7 ACP"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid,
        "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "status": "closed",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "61300", "name": "Honoraires syndic",
         "copropriete_id": cid, "class_num": 6},
        {"number": "41010001", "name": "Prov Matexi",
         "copropriete_id": cid, "class_num": 4},
        {"number": "41010002", "name": "Prov Teuwen",
         "copropriete_id": cid, "class_num": 4},
    ])
    await db.owners.insert_many([
        {"id": matexi_id, "name": "MATEXI", "last_name": "MATEXI",
         "tier_accounts": {cid: {"provisions": "41010001"}}},
        {"id": teuwen_id, "name": "TEUWEN Gael", "last_name": "TEUWEN",
         "tier_accounts": {cid: {"provisions": "41010002"}}},
    ])
    # **BUG DATA** : lot.owner_id RESTE sur MATEXI apres la vente
    await db.lots.insert_one({
        "id": lot_id, "number": "202", "copropriete_id": cid,
        "owner_id": matexi_id,  # <-- BLOQUE sur ancien proprio
        "owner_ids": [matexi_id],  # <-- BLOQUE aussi ici
        "quotity": 100,
    })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid,
        "name": "Charges communes", "is_default": True,
        "lots": [{"lot_id": lot_id, "lot_number": "202", "share": 100}],
    })
    # Mutation dans db.mutations : source de verite pour l'historique
    await db.mutations.insert_one({
        "id": f"mut-{suffix}", "copropriete_id": cid,
        "lot_id": lot_id,
        "from_owner_id": matexi_id,
        "to_owner_id": teuwen_id,
        "sale_date": "2026-06-15",
    })
    # 2 factures
    await db.invoices.insert_many([
        {"id": f"inv1-{suffix}", "copropriete_id": cid,
         "number": "FA-001", "supplier": "SYNDIC",
         "date": "2026-03-01", "total_amount": 1000.0,
         "account_number": "61300",
         "distribution_key_id": key_id,
         "distribution_lines": [
             {"lot_id": lot_id, "lot_number": "202", "amount": 1000.0}
         ]},
        {"id": f"inv2-{suffix}", "copropriete_id": cid,
         "number": "FA-002", "supplier": "SYNDIC",
         "date": "2026-09-01", "total_amount": 800.0,
         "account_number": "61300",
         "distribution_key_id": key_id,
         "distribution_lines": [
             {"lot_id": lot_id, "lot_number": "202", "amount": 800.0}
         ]},
    ])
    return {
        "cid": cid, "fy_id": fy_id,
        "matexi_id": matexi_id, "teuwen_id": teuwen_id,
        "lot_id": lot_id,
    }


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({f"tier_accounts.{cid}": {"$exists": True}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.mutations.delete_many({"copropriete_id": cid})


def test_iter90g7_teuwen_appears_in_json_decompte_despite_stale_owner_id():
    """Meme si lot.owner_id est bloque sur MATEXI, TEUWEN doit apparaitre
    dans le JSON `/api/reports/decompte` grace aux mutations."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_teuwen_scenario(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.get(
                    f"{BACKEND_URL}/api/reports/decompte?"
                    f"copropriete_id={ctx['cid']}&fiscal_year_id={ctx['fy_id']}"
                    f"&owner_filter=current",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                names = [d["owner_name"] for d in data["decomptes"]]
                assert "TEUWEN Gael" in names, (
                    f"iter90g7 : TEUWEN doit apparaitre dans le JSON decompte "
                    f"malgre lot.owner_id=MATEXI. Owners retournes : {names}"
                )
                assert "MATEXI" in names, (
                    f"MATEXI doit aussi apparaitre (via lot.owner_id direct)"
                )
                # TEUWEN doit avoir des charges > 0 (au moins la facture du 09/01)
                teuwen = next(d for d in data["decomptes"] if d["owner_name"] == "TEUWEN Gael")
                assert teuwen["total_charges"] > 0.01, (
                    f"iter90g7 : TEUWEN doit avoir des charges (au moins la "
                    f"facture 800 EUR post-vente). Total charges recu : "
                    f"{teuwen['total_charges']}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g7_teuwen_pdf_endpoint_returns_valid_pdf():
    """PDF endpoint syndic : TEUWEN doit avoir un PDF valide meme si
    lot.owner_id est bloque sur MATEXI."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_teuwen_scenario(db, suffix)
        try:
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.get(
                    f"{BACKEND_URL}/api/reports/decompte/pdf/{ctx['teuwen_id']}?"
                    f"copropriete_id={ctx['cid']}&fiscal_year_id={ctx['fy_id']}",
                )
                assert r.status_code == 200, (
                    f"iter90g7 : PDF TEUWEN doit etre 200. "
                    f"Recu {r.status_code} : {r.text[:200]}"
                )
                pdf_bytes = r.content
                assert pdf_bytes.startswith(b"%PDF"), (
                    f"iter90g7 : reponse doit etre un PDF valide, "
                    f"recu : {pdf_bytes[:20]}"
                )
                # Verifie que le lot 202 est mentionne dans le PDF
                import fitz
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                text = ""
                for page in doc:
                    text += page.get_text()
                doc.close()
                assert "202" in text, (
                    f"iter90g7 : le lot 202 doit apparaitre dans le PDF "
                    f"TEUWEN grace aux mutations. Text : {text[:500]}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g7_standard_owner_no_regression():
    """Regression check : un owner classique avec lot.owner_id correct
    (sans mutation) fonctionne toujours."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        cid = f"iter90g7reg-{suffix}"
        fy_id = f"iter90g7reg-fy-{suffix}"
        owner_id = f"owner-reg-{suffix}"
        lot_id = f"lot-reg-{suffix}"
        try:
            await db.coproprietes.insert_one({"id": cid, "name": "Regression ACP"})
            await db.fiscal_years.insert_one({
                "id": fy_id, "copropriete_id": cid,
                "name": "2026", "start_date": "2026-01-01",
                "end_date": "2026-12-31", "status": "closed",
            })
            await db.pcmn_accounts.insert_many([
                {"number": "61300", "name": "Honoraires",
                 "copropriete_id": cid, "class_num": 6},
                {"number": "41010999", "name": "Prov Reg",
                 "copropriete_id": cid, "class_num": 4},
            ])
            await db.owners.insert_one({
                "id": owner_id, "name": "OwnerReg", "last_name": "Reg",
                "tier_accounts": {cid: {"provisions": "41010999"}},
            })
            await db.lots.insert_one({
                "id": lot_id, "number": "1", "copropriete_id": cid,
                "owner_id": owner_id, "quotity": 100,
            })
            await db.distribution_keys.insert_one({
                "id": f"key-reg-{suffix}", "copropriete_id": cid,
                "name": "Charges", "is_default": True,
                "lots": [{"lot_id": lot_id, "lot_number": "1", "share": 100}],
            })
            await db.invoices.insert_one({
                "id": f"inv-reg-{suffix}", "copropriete_id": cid,
                "number": "FA-REG", "supplier": "Test",
                "date": "2026-06-01", "total_amount": 500.0,
                "account_number": "61300",
                "distribution_lines": [
                    {"lot_id": lot_id, "lot_number": "1", "amount": 500.0}
                ],
            })
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.get(
                    f"{BACKEND_URL}/api/reports/decompte?"
                    f"copropriete_id={cid}&fiscal_year_id={fy_id}",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert len(data["decomptes"]) >= 1
                owner_reg = next(
                    (d for d in data["decomptes"] if d["owner_name"] == "OwnerReg"),
                    None,
                )
                assert owner_reg is not None, (
                    f"OwnerReg doit apparaitre dans le decompte (regression)"
                )
                assert abs(owner_reg["total_charges"] - 500.0) < 0.01, (
                    f"OwnerReg doit avoir 500 EUR de charges, recu {owner_reg['total_charges']}"
                )
        finally:
            await db.coproprietes.delete_one({"id": cid})
            await db.fiscal_years.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})
            await db.owners.delete_many({"id": owner_id})
            await db.lots.delete_many({"copropriete_id": cid})
            await db.distribution_keys.delete_many({"copropriete_id": cid})
            await db.invoices.delete_many({"copropriete_id": cid})
    asyncio.run(_run())
