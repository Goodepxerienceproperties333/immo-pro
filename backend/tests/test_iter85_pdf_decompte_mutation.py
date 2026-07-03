"""Regression test - iter85 - Endpoint PDF Decompte de mutation.

Demande user : "P1 : Endpoint GET /api/lots/{lot_id}/mutations/{mutation_id}/
decompte.pdf + bouton frontend (le builder PDF est deja complet, manque juste
l'exposition route)."

Tests :
  1. Mutation -> GET decompte.pdf -> 200 + magic bytes %PDF
  2. mutation_id == "last" -> reprend la derniere mutation
  3. lot_id invalide -> 404
  4. mutation_id invalide -> 404
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr85pdf-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o_seller = f"os-{uuid.uuid4()}"
    o_buyer = f"ob-{uuid.uuid4()}"
    lot_id = f"lt-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "Residence Test PDF",
        "reference": "REF-PDF", "address": "Rue de Test 1, 1000 Bruxelles",
        "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds de roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "4100001", "name": "Vendeur", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Acquereur", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o_seller, "name": "DUPONT Jean", "last_name": "DUPONT",
         "first_name": "Jean", "auxiliary_code": "C0001",
         "address": "Avenue Vendeur 5", "postal_code": "1050", "city": "Ixelles",
         "vcs_code": "+++111/0000/00001+++",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}},
        {"id": o_buyer, "name": "MARTIN Marie", "last_name": "MARTIN",
         "first_name": "Marie", "auxiliary_code": "C0002",
         "address": "Boulevard Acheteur 10", "postal_code": "1180", "city": "Uccle",
         "vcs_code": "+++222/0000/00002+++",
         "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A3", "owner_id": o_seller, "owner_ids": [o_seller],
        "copropriete_id": cid, "quotity": 500.0,
        "description": "Appartement 3eme etage",
    })
    await db.lots.insert_one({
        "id": f"ghost-{uuid.uuid4()}", "number": "B3",
        "copropriete_id": cid, "quotity": 500.0,
    })
    # iter90ab : cle de repartition par defaut (obligatoire pour mutation)
    await db.distribution_keys.insert_one({
        "id": f"dk-iter85-{cid[:8]}", "copropriete_id": cid, "name": "Generale",
        "is_default": True, "key_type": "quotity",
        "lots": [{"lot_id": lot_id, "share": 500.0}],
    })
    # Solde fonds de roulement et appel Q1 pour avoir un prorata
    await db.journal_entries.insert_one({
        "id": str(uuid.uuid4()), "journal_type": "OD", "date": "2026-01-01",
        "copropriete_id": cid,
        "lines": [
            {"account_number": "4100001", "debit": 2000.0, "credit": 0.0},
            {"account_number": "100", "debit": 0.0, "credit": 2000.0},
        ],
        "total_debit": 2000.0, "total_credit": 2000.0,
    })
    await db.fund_calls.insert_one({
        "id": f"fc-{uuid.uuid4()}", "name": "Trimestriel 1/4 - 2026",
        "date": "2026-01-15", "due_date": "2026-01-15",
        "period_start": "2026-01-01", "period_end": "2026-03-31",
        "fiscal_year_id": fy_id, "copropriete_id": cid, "call_type": "provisions",
        "total_amount": 600.0,
        "distribution": [
            {"lot_id": lot_id, "lot_number": "A3", "owner_id": o_seller,
             "owner_name": "DUPONT Jean", "share": 500, "amount": 600.0, "paid": False},
        ],
    })
    return {"db": db, "cid": cid, "fy_id": fy_id,
            "o_seller": o_seller, "o_buyer": o_buyer, "lot_id": lot_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"copropriete_ids": ctx["cid"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})
    await db.distribution_keys.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str):
    from routes.properties import create_properties_router
    router = create_properties_router(db)
    for r in router.routes:
        if r.path == route_path:
            return r.endpoint
    return None


async def _do_mutation(ctx):
    mutate_fn = _get_endpoint(ctx["db"], "/api/lots/{lot_id}/mutate")
    LotMutationInput = mutate_fn.__annotations__.get("data")
    payload = LotMutationInput(
        new_owner_id=ctx["o_buyer"], sale_date="2026-02-15", sale_price=185000.0,
    )
    return await mutate_fn(lot_id=ctx["lot_id"], data=payload)


async def _read_streaming_pdf(resp) -> bytes:
    """Lit le contenu d'une StreamingResponse FastAPI."""
    chunks = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        chunks.append(chunk)
    return b"".join(chunks)


async def _test_download_pdf_returns_valid_pdf():
    ctx = await _setup()
    try:
        result = await _do_mutation(ctx)
        mut_id = result["mutation"]["id"]

        dl_fn = _get_endpoint(ctx["db"], "/api/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf")
        assert dl_fn is not None, "Endpoint decompte.pdf doit etre enregistre"

        resp = await dl_fn(lot_id=ctx["lot_id"], mutation_id=mut_id)
        # FastAPI StreamingResponse exposes media_type + headers + body_iterator
        assert resp.media_type == "application/pdf"
        assert "Content-Disposition" in resp.headers
        assert "decompte_mutation_lot_A3_20260215.pdf" in resp.headers["Content-Disposition"]

        pdf_bytes = await _read_streaming_pdf(resp)
        assert pdf_bytes.startswith(b"%PDF-"), (
            f"Magic bytes attendus %PDF-, recu {pdf_bytes[:10]!r}"
        )
        assert len(pdf_bytes) > 2000, f"PDF trop petit ({len(pdf_bytes)} bytes)"

        # Verifie que le PDF contient des elements cles du decompte
        # (recherche brute dans les bytes ; ReportLab encode parfois en latin-1)
        text_blob = pdf_bytes.decode("latin-1", errors="ignore")
        assert "Decompte" in text_blob or "mutation" in text_blob.lower()
        print(f"OK - PDF genere ({len(pdf_bytes)} bytes)")
    finally:
        await _cleanup(ctx)


async def _test_mutation_id_last_resolves_to_latest():
    ctx = await _setup()
    try:
        await _do_mutation(ctx)
        dl_fn = _get_endpoint(ctx["db"], "/api/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf")
        resp = await dl_fn(lot_id=ctx["lot_id"], mutation_id="last")
        assert resp.media_type == "application/pdf"
        pdf_bytes = await _read_streaming_pdf(resp)
        assert pdf_bytes.startswith(b"%PDF-")
        print("OK - mutation_id='last' resolu correctement")
    finally:
        await _cleanup(ctx)


async def _test_invalid_lot_returns_404():
    from fastapi import HTTPException
    ctx = await _setup()
    try:
        dl_fn = _get_endpoint(ctx["db"], "/api/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf")
        try:
            await dl_fn(lot_id="lot-inexistant", mutation_id="any")
            assert False, "Devrait lever HTTPException 404"
        except HTTPException as exc:
            assert exc.status_code == 404
        print("OK - lot inexistant -> 404")
    finally:
        await _cleanup(ctx)


async def _test_invalid_mutation_returns_404():
    from fastapi import HTTPException
    ctx = await _setup()
    try:
        await _do_mutation(ctx)
        dl_fn = _get_endpoint(ctx["db"], "/api/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf")
        try:
            await dl_fn(lot_id=ctx["lot_id"], mutation_id="mutation-inexistante")
            assert False, "Devrait lever HTTPException 404"
        except HTTPException as exc:
            assert exc.status_code == 404
        print("OK - mutation_id inexistant -> 404")
    finally:
        await _cleanup(ctx)


def test_iter85_pdf_decompte_valid_pdf():
    asyncio.run(_test_download_pdf_returns_valid_pdf())


def test_iter85_pdf_decompte_mutation_id_last():
    asyncio.run(_test_mutation_id_last_resolves_to_latest())


def test_iter85_pdf_decompte_invalid_lot_404():
    asyncio.run(_test_invalid_lot_returns_404())


def test_iter85_pdf_decompte_invalid_mutation_404():
    asyncio.run(_test_invalid_mutation_returns_404())
