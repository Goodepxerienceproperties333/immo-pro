"""iter90g4 : la regularisation d'exercice DOIT exclure du total
`provisions_called` les fonds de reserve ET les fonds de roulement.

**Contexte utilisateur (PROD Acacia TER)** :
> "Il ne faut pas inclure les fonds de reserves et les fonds de roulement
> dans les provisions appelees. Il n'est pas correct qu'ils soient repris
> dans le tableau clotures. La cloture ne doit reprendre que les provisions
> pour charges ordinaires."

Le bug (avant iter90g4) : les provisions apparaissaient a ~24k EUR au lieu
des 19k EUR reels, gonflees par 2000 EUR de fonds de reserve + 3000 EUR
de fonds de roulement (Acacia TER).

**Causes racines identifiees** :
1. Un appel de type `reserve` (call_type='reserve') genere des lignes sur
   le compte tier `4100XXXX` (RESERVE_PREFIX). `is_provisions_account()`
   retourne False pour ces comptes -> deja filtre correctement.
2. Un appel de type `roulement` (call_type='roulement') genere des lignes
   sur le MEME compte tier `4101XXXX` que les provisions. Ces lignes
   passaient le filtre `is_provisions_account` et etaient comptees en
   trop.
3. Un appel MIXTE (provisions + roulement dans la meme VE) genere 2 lignes
   sur le meme compte tier `4101XXXX`, discriminees uniquement par
   `account_name` ("Prov. charges - X" vs "Fonds roulement - X") et
   `line_description` ("Appel de provisions - ..." vs
   "Appel fonds de roulement - ...").

**Fix iter90g4** (`routes/fiscal.py::regularize_fiscal_year`) :
1. Precharge les `fund_calls` references par les VE (via `source_id`).
2. Si `fund_call.call_type in ('reserve', 'roulement')` -> VE entierement
   ignoree.
3. Pour les VE mixtes (call_type='provisions' ou vide), exclure ligne
   par ligne celles dont `account_name` commence par "Fonds roulement"
   ou dont `line_description` commence par "Appel fonds de roulement".

**Regressions couvertes** :
1. Appel PROVISIONS pur -> compte dans `provisions_called_total`.
2. Appel RESERVE pur -> exclu completement (compte 4100XXXX pas dans
   la boucle, ET call_type='reserve' court-circuite la VE).
3. Appel ROULEMENT pur -> exclu completement (compte 4101XXXX mais
   call_type='roulement' court-circuite la VE).
4. Appel MIXTE (provisions + roulement) -> seule la ligne provisions
   est comptee, la ligne roulement est filtree via account_name.
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


async def _seed_base(db, suffix: str) -> dict:
    """Setup minimal : 1 ACP, 1 FY 2026, 2 proprios avec comptes tiers,
    2 lots equi-quotites. Aucune facture pour isoler la logique de
    filtrage des appels de fonds."""
    cid = f"iter90g4-{suffix}"
    fy_id = f"iter90g4-fy-{suffix}"
    o1 = f"o1-{suffix}"
    o2 = f"o2-{suffix}"
    lot1 = f"lot1-{suffix}"
    lot2 = f"lot2-{suffix}"
    key_id = f"key-{suffix}"

    await db.coproprietes.insert_one({"id": cid, "name": "iter90g4 ACP"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid,
        "name": "2026", "start_date": "2026-01-01",
        "end_date": "2026-12-31", "status": "open",
    })
    await db.pcmn_accounts.insert_many([
        {"number": "700000", "name": "Provisions", "copropriete_id": cid, "class_num": 7},
        {"number": "610000", "name": "Charges", "copropriete_id": cid, "class_num": 6},
        {"number": "41010001", "name": "Prov O1", "copropriete_id": cid, "class_num": 4},
        {"number": "41010002", "name": "Prov O2", "copropriete_id": cid, "class_num": 4},
        {"number": "41000001", "name": "Reserve O1", "copropriete_id": cid, "class_num": 4},
        {"number": "41000002", "name": "Reserve O2", "copropriete_id": cid, "class_num": 4},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "Prop1", "last_name": "Prop1",
         "tier_accounts": {cid: {"provisions": "41010001", "reserve": "41000001"}}},
        {"id": o2, "name": "Prop2", "last_name": "Prop2",
         "tier_accounts": {cid: {"provisions": "41010002", "reserve": "41000002"}}},
    ])
    await db.lots.insert_many([
        {"id": lot1, "number": "1", "copropriete_id": cid, "owner_id": o1, "quotity": 100},
        {"id": lot2, "number": "2", "copropriete_id": cid, "owner_id": o2, "quotity": 100},
    ])
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid,
        "name": "Charges communes", "is_default": True,
        "lots": [
            {"lot_id": lot1, "lot_number": "1", "share": 100},
            {"lot_id": lot2, "lot_number": "2", "share": 100},
        ],
    })
    return {
        "cid": cid, "fy_id": fy_id,
        "o1": o1, "o2": o2, "key_id": key_id,
    }


async def _cleanup(db, cid: str):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.owners.delete_many({f"tier_accounts.{cid}": {"$exists": True}})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.fund_calls.delete_many({"copropriete_id": cid})


def _insert_ve_and_fund_call(db, suffix: str, ctx: dict, call_type: str,
                              fc_amount: float, prov_lines_amt: float,
                              roul_lines_amt: float = 0.0,
                              date: str = "2026-03-01"):
    """Insere une VE + son fund_call source (comme le fait auto_entries.py).
    - call_type = 'provisions' / 'reserve' / 'roulement'
    - `prov_lines_amt` : montant SUR compte tier 4101XXXX libelle "Prov. charges"
    - `roul_lines_amt` : montant SUR compte tier 4101XXXX libelle "Fonds roulement"
    Repartis a parts egales entre o1 et o2.
    """
    fc_id = f"fc-{call_type}-{suffix}"
    ve_id = f"ve-{call_type}-{suffix}"
    o1 = ctx["o1"]
    o2 = ctx["o2"]
    cid = ctx["cid"]

    async def _do():
        await db.fund_calls.insert_one({
            "id": fc_id, "copropriete_id": cid,
            "date": date, "name": f"Appel {call_type} {suffix}",
            "call_type": call_type,
            "total_amount": fc_amount,
            "reserve_amount": fc_amount if call_type == "reserve" else 0.0,
            "roulement_amount": (
                fc_amount if call_type == "roulement" else roul_lines_amt * 2
            ),
            "distribution": [
                {"owner_id": o1, "amount": fc_amount / 2},
                {"owner_id": o2, "amount": fc_amount / 2},
            ],
        })
        lines = []
        # Repartition par proprietaire
        per_o = prov_lines_amt / 2
        per_o_roul = roul_lines_amt / 2 if roul_lines_amt else 0.0
        if call_type == "reserve":
            # Sur compte 4100XXXX (RESERVE_PREFIX)
            lines = [
                {"account_number": "41000001", "account_name": "Fonds reserve - Prop1",
                 "debit": fc_amount / 2, "credit": 0.0,
                 "third_party_id": o1, "third_party_name": "Prop1",
                 "line_description": f"Appel fonds de reserve - {suffix}"},
                {"account_number": "41000002", "account_name": "Fonds reserve - Prop2",
                 "debit": fc_amount / 2, "credit": 0.0,
                 "third_party_id": o2, "third_party_name": "Prop2",
                 "line_description": f"Appel fonds de reserve - {suffix}"},
                {"account_number": "160", "account_name": "Fonds de reserve",
                 "debit": 0.0, "credit": fc_amount},
            ]
        elif call_type == "roulement":
            # Sur compte 4101XXXX (PROVISIONS_PREFIX) - MEME compte que provisions
            lines = [
                {"account_number": "41010001",
                 "account_name": "Fonds roulement - Prop1",
                 "debit": fc_amount / 2, "credit": 0.0,
                 "third_party_id": o1, "third_party_name": "Prop1",
                 "line_description": f"Appel fonds de roulement - {suffix}"},
                {"account_number": "41010002",
                 "account_name": "Fonds roulement - Prop2",
                 "debit": fc_amount / 2, "credit": 0.0,
                 "third_party_id": o2, "third_party_name": "Prop2",
                 "line_description": f"Appel fonds de roulement - {suffix}"},
                {"account_number": "100", "account_name": "Fonds de roulement",
                 "debit": 0.0, "credit": fc_amount},
            ]
        else:
            # Provisions (ou mixte avec roulement injecte)
            if per_o > 0.001:
                lines.append({"account_number": "41010001",
                              "account_name": "Prov. charges - Prop1",
                              "debit": per_o, "credit": 0.0,
                              "third_party_id": o1, "third_party_name": "Prop1",
                              "line_description": f"Appel de provisions - {suffix}"})
                lines.append({"account_number": "41010002",
                              "account_name": "Prov. charges - Prop2",
                              "debit": per_o, "credit": 0.0,
                              "third_party_id": o2, "third_party_name": "Prop2",
                              "line_description": f"Appel de provisions - {suffix}"})
            if per_o_roul > 0.001:
                lines.append({"account_number": "41010001",
                              "account_name": "Fonds roulement - Prop1",
                              "debit": per_o_roul, "credit": 0.0,
                              "third_party_id": o1, "third_party_name": "Prop1",
                              "line_description": f"Appel fonds de roulement - {suffix}"})
                lines.append({"account_number": "41010002",
                              "account_name": "Fonds roulement - Prop2",
                              "debit": per_o_roul, "credit": 0.0,
                              "third_party_id": o2, "third_party_name": "Prop2",
                              "line_description": f"Appel fonds de roulement - {suffix}"})
            # Contrepartie 700000
            lines.append({"account_number": "700000",
                          "account_name": "Provisions",
                          "debit": 0.0, "credit": prov_lines_amt + roul_lines_amt})
        await db.journal_entries.insert_one({
            "id": ve_id, "copropriete_id": cid,
            "journal_type": "VE", "date": date,
            "reference": f"AF-{suffix}",
            "description": f"Appel {call_type}",
            "source_type": "fund_call",
            "source_id": fc_id,
            "lines": lines,
        })
        return ve_id
    return _do()


def test_iter90g4_pure_provisions_call_is_counted():
    """Regression check : un appel de provisions PUR (call_type='provisions')
    est bien pris dans provisions_called_total."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_base(db, suffix)
        try:
            await _insert_ve_and_fund_call(
                db, suffix, ctx, call_type="provisions",
                fc_amount=2000.0, prov_lines_amt=2000.0,
            )
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    f"?dry_run=true",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                total = data["summary"]["total_provisions_called"]
                assert abs(total - 2000.0) < 0.01, (
                    f"iter90g4 : appel provisions pur doit etre compte a "
                    f"2000, recu {total}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g4_pure_reserve_call_excluded():
    """Un appel de type 'reserve' pur (compte 4100XXXX) est totalement
    exclu de provisions_called (etait deja OK avant iter90g4 grace a
    is_provisions_account, mais iter90g4 durcit via call_type)."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_base(db, suffix)
        try:
            # Appel de reserve 3000 EUR + provisions 1000 EUR (VE separee)
            await _insert_ve_and_fund_call(
                db, suffix, ctx, call_type="reserve",
                fc_amount=3000.0, prov_lines_amt=0.0,
                date="2026-02-01",
            )
            await _insert_ve_and_fund_call(
                db, suffix + "p", ctx, call_type="provisions",
                fc_amount=1000.0, prov_lines_amt=1000.0,
                date="2026-03-01",
            )
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    f"?dry_run=true",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                total = data["summary"]["total_provisions_called"]
                # Seul l'appel provisions (1000) est compte
                assert abs(total - 1000.0) < 0.01, (
                    f"iter90g4 : reserve exclue, provisions_called_total "
                    f"doit valoir 1000 (pas 4000 avec reserve), recu {total}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g4_pure_roulement_call_excluded():
    """Un appel de type 'roulement' pur (compte 4101XXXX MAIS
    call_type='roulement') est EXCLU. C'est la cause principale du bug
    (comptes 4100XX vs 4101XX se ressemblent mais 4101XX est aussi
    utilise pour provisions -> discrimination par call_type)."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_base(db, suffix)
        try:
            await _insert_ve_and_fund_call(
                db, suffix, ctx, call_type="roulement",
                fc_amount=5000.0, prov_lines_amt=0.0,
                date="2026-02-01",
            )
            await _insert_ve_and_fund_call(
                db, suffix + "p", ctx, call_type="provisions",
                fc_amount=1500.0, prov_lines_amt=1500.0,
                date="2026-03-01",
            )
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    f"?dry_run=true",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                total = data["summary"]["total_provisions_called"]
                assert abs(total - 1500.0) < 0.01, (
                    f"iter90g4 : roulement exclu, provisions_called_total "
                    f"doit valoir 1500 (pas 6500 avec roulement), recu {total}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())


def test_iter90g4_mixed_call_only_provisions_lines_counted():
    """Un appel MIXTE (call_type='provisions' + roulement_amount>0) genere
    des lignes provisions ET roulement sur le MEME compte tier 4101XXXX,
    discriminees uniquement par account_name/line_description. iter90g4
    doit ne compter QUE les lignes provisions."""
    async def _run():
        db = await _mongo()
        suffix = uuid.uuid4().hex[:6]
        ctx = await _seed_base(db, suffix)
        try:
            # Un seul appel mixte : 2000 provisions + 800 roulement dans la
            # meme VE, meme compte 4101XXXX. Discrimine par account_name.
            await _insert_ve_and_fund_call(
                db, suffix, ctx, call_type="provisions",
                fc_amount=2800.0,
                prov_lines_amt=2000.0,
                roul_lines_amt=800.0,
            )
            async with httpx.AsyncClient() as c:
                await _login(c)
                r = await c.post(
                    f"{BACKEND_URL}/api/fiscal/years/{ctx['fy_id']}/regularize"
                    f"?dry_run=true",
                )
                assert r.status_code == 200, r.text
                data = r.json()
                total = data["summary"]["total_provisions_called"]
                # 2000 EUR de provisions comptees, 800 EUR roulement exclus
                assert abs(total - 2000.0) < 0.01, (
                    f"iter90g4 : appel mixte, seul le montant PROVISIONS "
                    f"(2000) doit etre compte, pas le total avec roulement "
                    f"(2800), recu {total}"
                )
        finally:
            await _cleanup(db, ctx["cid"])
    asyncio.run(_run())
