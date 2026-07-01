"""Iter90l — Import PDF/CSV d'extraits de compte -> creation auto de
bank_statements + bank_transactions en status='draft'.

Portee des tests :
- U1  parse_csv_smart : format BNP Fortis (headers FR)
- U2  parse_csv_smart : format Belfius (headers FR/NL, sep ';')
- U3  parse_csv_smart : format debit/credit separes
- U4  parse_csv_smart : montants avec , decimale et parentheses negatifs
- U5  parse_csv_smart : CSV non reconnu -> extraction_method='csv_unrecognized'
- E1  POST /api/banking/statements/import-files avec 1 CSV -> statement draft + N txns
- E2  Multi-fichiers : 2 CSV -> 2 statements draft
- E3  Le fichier original est persiste en GridFS + telechargeable via
      GET /statements/{id}/source-file
- E4  copropriete_id obligatoire (400 sinon)
- E5  Fichier vide -> resultat 'error'
"""
import os
import sys
import io
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90l-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402
from bank_import import parse_csv_smart, _parse_amount, _parse_date  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"copropriete_id": {"$regex": "^c-iter90l-"}})
    await db.bank_statements.delete_many({"copropriete_id": {"$regex": "^c-iter90l-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90l-"}})
    await db["bank_statement_sources.files"].delete_many(
        {"metadata.copropriete_id": {"$regex": "^c-iter90l-"}}
    )


async def _login(c):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed_acp():
    cid = f"c-iter90l-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP Iter90l",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return cid


# ======== Unitaires du parser ========


def test_parse_amount_variants():
    assert _parse_amount("1.234,56") == 1234.56
    assert _parse_amount("1,234.56") == 1234.56
    assert _parse_amount("-25,00") == -25.0
    assert _parse_amount("25,00-") == -25.0
    assert _parse_amount("(100,00)") == -100.0
    assert _parse_amount("42") == 42.0
    assert _parse_amount("") is None
    assert _parse_amount(None) is None


def test_parse_date_variants():
    assert _parse_date("15/02/2026") == "2026-02-15"
    assert _parse_date("2026-02-15") == "2026-02-15"
    assert _parse_date("15.02.2026") == "2026-02-15"
    assert _parse_date("20260215") == "2026-02-15"
    assert _parse_date("") is None
    assert _parse_date("junk") is None


def test_parse_csv_bnpp_fortis_like():
    csv_text = (
        "Date;Montant;Communication;Contrepartie\n"
        "15/02/2026;-125,00;Facture #F2026-001;Fournisseur Elec SPRL\n"
        "16/02/2026;+2500,00;Appel de fonds Q1;Cop. Rue Test\n"
        "17/02/2026;-45,50;Frais tenue de compte;\n"
    )
    result = parse_csv_smart(csv_text.encode("utf-8"), "fortis.csv")
    assert result["extraction_method"] == "csv_smart"
    assert len(result["transactions"]) == 3
    t0, t1, t2 = result["transactions"]
    assert t0["date"] == "2026-02-15"
    assert t0["amount"] == -125.0
    assert t0["transaction_type"] == "debit"
    assert "Fournisseur Elec" in t0["counterparty_name"]
    assert t1["amount"] == 2500.0
    assert t1["transaction_type"] == "credit"
    assert t2["counterparty_name"] == ""


def test_parse_csv_belfius_like_debit_credit_split():
    csv_text = (
        "Date de valeur;Debit;Credit;Communication;Tegenpartij\n"
        "10/02/2026;;150,50;Interet 4eme trim;\n"
        "12/02/2026;35,00;;Cotisation carte;Belfius\n"
    )
    result = parse_csv_smart(csv_text.encode("utf-8"), "belfius.csv")
    assert result["extraction_method"] == "csv_smart", result
    assert len(result["transactions"]) == 2
    assert result["transactions"][0]["amount"] == 150.5
    assert result["transactions"][1]["amount"] == -35.0


def test_parse_csv_unrecognized():
    csv_text = "colA;colB;colC\n1;2;3\n"
    result = parse_csv_smart(csv_text.encode("utf-8"), "junk.csv")
    assert result["extraction_method"] == "csv_unrecognized"
    assert result["transactions"] == []


# ======== E2E backend (endpoint) ========


async def _t_e2e_import_single_csv():
    await _wipe()
    cid = await _seed_acp()
    csv_text = (
        "Date;Montant;Communication;Contrepartie\n"
        "15/02/2026;-125,00;Facture Elec;SPRL Elec\n"
        "16/02/2026;2500,00;Appel Q1;\n"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        files = [("files", ("test.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_statements"] == 1
        assert body["total_transactions"] == 2
        result = body["results"][0]
        assert result["status"] == "ok"
        assert result["extraction_method"] == "csv_smart"
        assert result["transactions_count"] == 2
        stmt = await db.bank_statements.find_one(
            {"id": result["statement_id"]}, {"_id": 0}
        )
        assert stmt["status"] == "draft"
        assert stmt["source"] == "CSV"
        assert stmt["source_file_id"]
        txns = await db.bank_transactions.find(
            {"statement_id": result["statement_id"]}, {"_id": 0},
        ).to_list(length=100)
        assert len(txns) == 2
        assert all(t["matched"] is False for t in txns)


async def _t_e2e_multi_files():
    await _wipe()
    cid = await _seed_acp()
    csv1 = "Date;Montant;Communication\n15/02/2026;-50,00;X\n"
    csv2 = "Date;Montant;Communication\n16/02/2026;100,00;Y\n17/02/2026;-25,00;Z\n"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        files = [
            ("files", ("a.csv", io.BytesIO(csv1.encode("utf-8")), "text/csv")),
            ("files", ("b.csv", io.BytesIO(csv2.encode("utf-8")), "text/csv")),
        ]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_statements"] == 2
        assert body["total_transactions"] == 3
        assert all(res["status"] == "ok" for res in body["results"])


async def _t_e2e_source_file_download():
    await _wipe()
    cid = await _seed_acp()
    csv_text = "Date;Montant;Communication\n15/02/2026;-25,00;Frais\n"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        files = [("files", ("original.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid})
        stmt_id = r.json()["results"][0]["statement_id"]
        # Telecharger le fichier source
        r = await c.get(f"/api/banking/statements/{stmt_id}/source-file")
        assert r.status_code == 200
        assert r.content == csv_text.encode("utf-8"), \
            "Le fichier original doit etre servi tel quel"
        assert "attachment" in r.headers.get("content-disposition", "").lower()


async def _t_e2e_missing_copro():
    await _wipe()
    csv_text = "Date;Montant\n15/02/2026;-10,00\n"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        files = [("files", ("x.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": ""})
        assert r.status_code == 400


async def _t_e2e_empty_file():
    await _wipe()
    cid = await _seed_acp()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        files = [("files", ("empty.csv", io.BytesIO(b""), "text/csv"))]
        r = await c.post("/api/banking/statements/import-files",
                         files=files, data={"copropriete_id": cid})
        assert r.status_code == 200
        body = r.json()
        assert body["total_statements"] == 0
        assert body["results"][0]["status"] == "error"


async def _run_all():
    await _t_e2e_import_single_csv()
    await _t_e2e_multi_files()
    await _t_e2e_source_file_download()
    await _t_e2e_missing_copro()
    await _t_e2e_empty_file()


def test_iter90l_import_pdf_csv_e2e():
    asyncio.run(_run_all())
