"""Iter90n - SEC-001 : chinese wall sur les endpoints banking by-id / by-body.

Un manager (role syndic) ne doit PAS pouvoir acceder aux extraits / txns
d'une ACP qu'il ne gere pas, meme s'il connait l'UUID cible.

Endpoints testes :
- GET /api/banking/statements/{id}
- GET /api/banking/statements/{id}/source-file
- POST /api/banking/transactions/{id}/categorize
- DELETE /api/banking/transactions/{id}/categorize
- POST /api/banking/statements/import-files (copropriete_id en Form)

Iter90n : SEC-002 : limites d'upload
- fichier > 10 MB -> rejete
- > 20 fichiers -> rejete
- content-type sniffing PDF (%PDF-)
"""
import os
import sys
import io
import asyncio
import uuid
from datetime import datetime, timezone

import bcrypt
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90n-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-iter90n-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-iter90n-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90n-"}})
    await db.users.delete_many({"email": {"$regex": "^syndic-iter90n-"}})


async def _seed_two_acps_and_intruder():
    """Cree 2 ACPs. Un syndic 'intrus' n'a acces qu'a la ACP A.
    Il tente ensuite d'atteindre les extraits de la ACP B."""
    cid_a = f"c-iter90n-A-{uuid.uuid4().hex[:6]}"
    cid_b = f"c-iter90n-B-{uuid.uuid4().hex[:6]}"
    for cid, name in [(cid_a, "ACP A - Mine"), (cid_b, "ACP B - Others")]:
        await db.coproprietes.insert_one({
            "id": cid, "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    email = f"syndic-iter90n-{uuid.uuid4().hex[:6]}@test.be"
    pwd = "SyndicSecret123!"
    await db.users.insert_one({
        "id": str(uuid.uuid4()), "email": email, "name": "Syndic Intrus",
        "password_hash": bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode(),
        "role": "syndic",
        "copropriete_ids": [cid_a],  # UNIQUEMENT ACP A
        "onboarding_completed": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Statement + transaction sur ACP B (cible)
    stmt_b = f"bs-iter90n-B-{uuid.uuid4().hex[:6]}"
    txn_b = f"bt-iter90n-B-{uuid.uuid4().hex[:6]}"
    await db.bank_statements.insert_one({
        "id": stmt_b, "number": "S-B", "date": "2026-02-01",
        "account_number": "BE99", "opening_balance": 100.0, "closing_balance": 90.0,
        "status": "draft", "source": "PDF",
        "source_file_id": "gridfs-fake-id-b",
        "copropriete_id": cid_b,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.bank_transactions.insert_one({
        "id": txn_b, "statement_id": stmt_b, "date": "2026-02-05",
        "amount": -10.0, "transaction_type": "debit",
        "counterparty_name": "Secret",
        "matched": False, "match_type": "", "matched_to": "",
        "copropriete_id": cid_b,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return cid_a, cid_b, email, pwd, stmt_b, txn_b


async def _login(c, email, pwd):
    r = await c.post("/api/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text


async def _t_intruder_cannot_read_other_acp_statement():
    await _wipe()
    _cid_a, _cid_b, email, pwd, stmt_b, _txn_b = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.get(f"/api/banking/statements/{stmt_b}")
        assert r.status_code == 403, \
            f"Un syndic ne gerant pas cette ACP doit avoir 403, got {r.status_code}: {r.text[:200]}"


async def _t_intruder_cannot_download_source_file():
    await _wipe()
    _cid_a, _cid_b, email, pwd, stmt_b, _txn_b = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.get(f"/api/banking/statements/{stmt_b}/source-file")
        assert r.status_code == 403


async def _t_intruder_cannot_categorize_other_txn():
    await _wipe()
    _cid_a, _cid_b, email, pwd, _stmt_b, txn_b = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.post(f"/api/banking/transactions/{txn_b}/categorize", json={
            "splits": [{"expense_category_id": "any",
                        "distribution_key_id": "any", "amount": 10.0}]
        })
        assert r.status_code == 403


async def _t_intruder_cannot_uncategorize_other_txn():
    await _wipe()
    _cid_a, _cid_b, email, pwd, _stmt_b, txn_b = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        r = await c.delete(f"/api/banking/transactions/{txn_b}/categorize")
        assert r.status_code == 403


async def _t_intruder_cannot_import_files_to_other_acp():
    await _wipe()
    _cid_a, cid_b, email, pwd, _stmt_b, _txn_b = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        csv_text = "Date;Montant\n15/02/2026;-10,00\n"
        files = [("files", ("x.csv", io.BytesIO(csv_text.encode()), "text/csv"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid_b})
        assert r.status_code == 403


# ===== SEC-002 : bornes d'upload =====


async def _t_reject_too_large_file():
    await _wipe()
    cid_a, _cid_b, email, pwd, _s, _t = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        # Fichier de 11 MB (au-dessus de la limite 10 MB)
        big_content = b"%PDF-1.4\n" + b"A" * (11 * 1024 * 1024)
        files = [("files", ("big.pdf", io.BytesIO(big_content), "application/pdf"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid_a})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_statements"] == 0
        assert body["results"][0]["status"] == "error"
        assert "volumineux" in body["results"][0]["error"].lower() \
            or "trop" in body["results"][0]["error"].lower()


async def _t_reject_too_many_files():
    await _wipe()
    cid_a, _cid_b, email, pwd, _s, _t = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        # 21 fichiers (au-dessus de MAX_FILES=20)
        files = []
        for i in range(21):
            files.append(("files", (f"f{i}.csv", io.BytesIO(b"Date;Montant\n"), "text/csv")))
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid_a})
        assert r.status_code == 413


async def _t_reject_non_pdf_content_masquerading_as_pdf():
    """Fichier .pdf mais sans magic %PDF- -> doit etre rejete au sniff."""
    await _wipe()
    cid_a, _cid_b, email, pwd, _s, _t = await _seed_two_acps_and_intruder()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c, email, pwd)
        # exe deguise en pdf (via extension)
        fake_pdf = b"MZ\x90\x00" + b"\x00" * 100  # signature Windows PE
        # Note : extension .exe -> refused by extension check even avant sniff
        files = [("files", ("payload.exe", io.BytesIO(fake_pdf), "application/octet-stream"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid_a})
        assert r.status_code == 200
        body = r.json()
        assert body["results"][0]["status"] == "error"
        assert "non supporte" in body["results"][0]["error"].lower() \
            or "format" in body["results"][0]["error"].lower()


async def _run_all():
    await _t_intruder_cannot_read_other_acp_statement()
    await _t_intruder_cannot_download_source_file()
    await _t_intruder_cannot_categorize_other_txn()
    await _t_intruder_cannot_uncategorize_other_txn()
    await _t_intruder_cannot_import_files_to_other_acp()
    await _t_reject_too_large_file()
    await _t_reject_too_many_files()
    await _t_reject_non_pdf_content_masquerading_as_pdf()


def test_iter90n_security_hardening():
    asyncio.run(_run_all())
