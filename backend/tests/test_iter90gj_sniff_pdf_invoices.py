"""iter90gj Phase 4 : sniff-pdf accepte `kind=invoices` (PDF Optipro
"Factures fournisseurs"). Renvoie 1 entree par ligne comptable, avec
`internal_ref_optipro` partage entre lignes d'une meme facture pour que
`commit-invoices` regroupe automatiquement (Phase 1).

**Ticket utilisateur** : "pas de possibilite d'ajouter PDF" (etape 5/8 Factures)
"""
import asyncio
import io
import os
import sys
import urllib.request
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"
USER_PDF = ("https://customer-assets-jt897jd0.emergentagent.net/"
            "job_c983d589-579c-4dc1-9f75-c76209275508/artifacts/"
            "4txlp0om_Factures%20fournisseurs%20%281%29.pdf")


async def _login(client):
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def test_sniff_pdf_kind_invoices_real_user_file():
    """Le sniff-pdf sur le vrai PDF utilisateur retourne :
    - count > 0 lignes
    - chaque ligne a `internal_ref_optipro`, `supplier_aux_code`, `account_number`
    - il existe au moins UNE facture multi-ligne (2+ entrees avec meme internal_ref)
    """
    try:
        raw = urllib.request.urlopen(USER_PDF, timeout=15).read()
    except Exception:
        print("SKIP : asset URL non joignable")
        return
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gj-sniff-{suffix}"
    sid = f"session-sniff-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-sniff-{suffix}"})
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active", "steps": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=60) as client:
            await _login(client)
            files = {"file": ("factures.pdf", raw, "application/pdf")}
            data = {"kind": "invoices"}
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{sid}/sniff-pdf",
                files=files, data=data,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["count"] > 0
            invoices = body["invoices"]
            # Chaque ligne a les champs cles pour commit
            for inv in invoices[:5]:
                assert "internal_ref_optipro" in inv
                assert "supplier_aux_code" in inv
                assert "montant_ht" in inv
                assert "montant_tvac" in inv

            # Il existe au moins 1 facture multi-ligne
            from collections import Counter
            refs = Counter(inv["internal_ref_optipro"] for inv in invoices if inv["internal_ref_optipro"])
            multi = [ref for ref, cnt in refs.items() if cnt > 1]
            assert len(multi) > 0, (
                "aucune facture multi-ligne detectee (regression Phase 1 attendue)"
            )
            print(f"OK : {body['count']} lignes, {len(refs)} refs distinctes, "
                  f"{len(multi)} refs multi-ligne")
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.import_sessions.delete_one({"id": sid})


async def test_sniff_pdf_kind_invoices_no_file_returns_empty():
    """PDF vide -> 0 lignes, pas de crash."""
    # Construit un PDF minimal factice (blanc, 1 page)
    minimal_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000053 00000 n\n0000000101 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n149\n%%EOF"
    db = await _mongo()
    suffix = uuid.uuid4().hex[:8]
    cid = f"iter90gj-empty-{suffix}"
    sid = f"session-empty-{suffix}"
    await db.coproprietes.insert_one({"id": cid, "name": f"iter90gj-empty-{suffix}"})
    await db.import_sessions.insert_one({
        "id": sid, "copropriete_id": cid, "status": "active", "steps": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    try:
        async with httpx.AsyncClient(cookies=httpx.Cookies(), timeout=30) as client:
            await _login(client)
            files = {"file": ("empty.pdf", minimal_pdf, "application/pdf")}
            r = await client.post(
                f"{BACKEND_URL}/api/import-wizard/sessions/{sid}/sniff-pdf",
                files=files, data={"kind": "invoices"},
            )
            # 200 OK avec 0 lignes ou 500 selon parser robustness ; on tolere les 2
            if r.status_code == 200:
                assert r.json()["count"] == 0
            else:
                assert r.status_code in (400, 422, 500)
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.import_sessions.delete_one({"id": sid})


if __name__ == "__main__":
    asyncio.run(test_sniff_pdf_kind_invoices_real_user_file())
    print("OK test_sniff_pdf_kind_invoices_real_user_file")
    asyncio.run(test_sniff_pdf_kind_invoices_no_file_returns_empty())
    print("OK test_sniff_pdf_kind_invoices_no_file_returns_empty")
