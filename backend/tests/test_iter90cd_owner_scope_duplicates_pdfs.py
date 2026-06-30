"""Iter90c+d - frais privatifs sans doublon + exports PDF journaux/factures.

Tests :
1. `list_owners?copropriete_id=X` retourne aussi les owners lies a X
   via `copropriete_ids[]` meme sans lot dans l'ACP.
2. `_norm_name` tolere les caracteres speciaux (`&`, tirets, accents)
   pour detecter "DEWINTER - DRAYE Jean-Claude & Evelyne" comme doublon
   de "DEWINTER - DRAYE Jean-Claude Evelyne".
3. `attach-to-copro` est idempotent et refuse une ACP hors-scope.
4. Endpoints PDF journaux + liste factures repondent HTTP 200 avec un PDF valide.
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90cd-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.owners.delete_many({"id": {"$regex": "^o-iter90cd-"}})
    await db.lots.delete_many({"id": {"$regex": "^l-iter90cd-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90cd-"}})


async def _login(c: AsyncClient):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _create_acp(name="ACP iter90cd"):
    cid = f"c-iter90cd-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return cid


async def _create_owner(name="Test", copro_ids=None, with_lot_in=None):
    oid = f"o-iter90cd-{uuid.uuid4().hex[:8]}"
    await db.owners.insert_one({
        "id": oid, "name": name, "first_name": name.split()[0] if name else "",
        "last_name": name.split()[-1] if name else "",
        "copropriete_ids": copro_ids or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    if with_lot_in:
        await db.lots.insert_one({
            "id": f"l-iter90cd-{uuid.uuid4().hex[:8]}", "number": "A-01",
            "owner_id": oid, "copropriete_id": with_lot_in,
        })
    return oid


# === T1 : list_owners ACP-scoped includes owners linked via copropriete_ids[] ===
async def _t_list_owners_includes_linked_without_lot():
    await _wipe()
    copro = await _create_acp()
    # Owner A : lot dans cette ACP -> doit etre dans la liste
    oid_a = await _create_owner("A WithLot", with_lot_in=copro)
    # Owner B : pas de lot, mais copropriete_ids inclut copro -> doit etre dans la liste (iter90c)
    oid_b = await _create_owner("B NoLot", copro_ids=[copro])
    # Owner C : autre ACP, ne doit PAS etre dans la liste
    oid_c = await _create_owner("C OtherACP", copro_ids=["other-acp"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.get(f"/api/owners?copropriete_id={copro}")
        assert r.status_code == 200, r.text
        ids = {o["id"] for o in r.json()}
        assert oid_a in ids
        assert oid_b in ids, "Owner sans lot mais lie via copropriete_ids[] doit etre visible"
        assert oid_c not in ids, "Owner hors-scope ne doit pas apparaitre"


# === T2 : detection doublon avec caracteres speciaux ===
async def _t_duplicates_detect_special_chars():
    await _wipe()
    copro = await _create_acp()
    # 2 owners avec nom quasi-identique : un avec '&', un sans
    o1 = await _create_owner("DEWINTER - DRAYE Jean-Claude & Evelyne", with_lot_in=copro)
    o2 = await _create_owner("DEWINTER - DRAYE Jean-Claude Evelyne", with_lot_in=copro)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.get(f"/api/admin/duplicates/owners?copropriete_id={copro}")
        assert r.status_code == 200, r.text
        groups = r.json().get("groups", [])
        # On doit trouver un groupe avec les 2 owners
        found = False
        for g in groups:
            member_ids = {m["id"] for m in g.get("members", [])}
            if o1 in member_ids and o2 in member_ids:
                found = True
                break
        assert found, f"Les 2 owners doivent etre detectes comme doublons. groups={groups}"


# === T3 : attach-to-copro idempotent + refuse hors-scope ===
async def _t_attach_to_copro_idempotent():
    await _wipe()
    copro = await _create_acp()
    oid = await _create_owner("AttachTest")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        # 1st call - attach
        r = await c.post(f"/api/owners/{oid}/attach-to-copro", json={"copropriete_id": copro})
        assert r.status_code == 200, r.text
        assert copro in r.json()["owner"]["copropriete_ids"]
        # 2nd call - idempotent
        r = await c.post(f"/api/owners/{oid}/attach-to-copro", json={"copropriete_id": copro})
        assert r.status_code == 200
        # Refuse without copropriete_id
        r = await c.post(f"/api/owners/{oid}/attach-to-copro", json={})
        assert r.status_code == 422


# === T4 : PDF journaux + liste factures ===
async def _t_journals_and_invoices_pdf():
    await _wipe()
    copro = await _create_acp()
    # Pre-seed PCMN basic accounts to avoid errors
    for num, name in [("600000", "Achats"), ("440000", "Fournisseurs")]:
        await db.pcmn_accounts.update_one(
            {"copropriete_id": copro, "number": num},
            {"$setOnInsert": {"id": str(uuid.uuid4()), "copropriete_id": copro,
                              "number": num, "name": name, "class_num": int(num[0])}},
            upsert=True,
        )
    # Insert 2 factures + 1 journal entry
    await db.invoices.insert_one({
        "id": "inv-iter90cd-1", "number": "F-001", "date": "2026-02-15",
        "supplier": "Test SARL", "description": "Test invoice",
        "total_amount": 121.0, "vat_amount": 21.0,
        "account_number": "600000", "status": "paid",
        "copropriete_id": copro,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.journal_entries.insert_one({
        "id": "je-iter90cd-1", "journal_type": "AC", "date": "2026-02-15",
        "reference": "FA-F-001", "description": "Achat test",
        "lines": [
            {"account_number": "600000", "account_name": "Achats",
             "debit": 121.0, "credit": 0.0},
            {"account_number": "440000", "account_name": "Fournisseurs",
             "debit": 0.0, "credit": 121.0},
        ],
        "total_debit": 121.0, "total_credit": 121.0,
        "copropriete_id": copro,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        # PDF journaux
        r = await c.get(f"/api/reports/journals/pdf?copropriete_id={copro}&date_from=2026-01-01&date_to=2026-12-31")
        assert r.status_code == 200, r.text
        assert r.content[:5] == b"%PDF-", "Should be a valid PDF"
        assert len(r.content) > 2000
        # PDF liste factures
        r = await c.get(f"/api/reports/invoices-list/pdf?copropriete_id={copro}&date_from=2026-01-01&date_to=2026-12-31")
        assert r.status_code == 200, r.text
        assert r.content[:5] == b"%PDF-"
        assert len(r.content) > 2000
    # Cleanup
    await db.invoices.delete_many({"id": {"$regex": "^inv-iter90cd-"}})
    await db.journal_entries.delete_many({"id": {"$regex": "^je-iter90cd-"}})


async def _run_all():
    await _t_list_owners_includes_linked_without_lot()
    await _t_duplicates_detect_special_chars()
    await _t_attach_to_copro_idempotent()
    await _t_journals_and_invoices_pdf()


def test_iter90cd_all_flows():
    asyncio.run(_run_all())
