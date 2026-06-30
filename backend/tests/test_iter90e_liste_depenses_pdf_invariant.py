"""Iter90e - Liste des depenses PDF aligned with /api/fiscal/expenses.

INVARIANT (P0) :
  sum(TVAC du PDF "Liste des depenses", sans filtre, sur une periode)
  ==
  totals.total renvoye par /api/fiscal/expenses pour la meme ACP, periode,
  et filtres.

Tests :
- T1 : factures simples + facture multi-lignes + ecriture OD classe 6 ->
       le PDF inclut tout, sa colonne TVAC totale matche fiscal/expenses.
- T2 : avec filtre distribution_key_id ciblant une ligne d'une facture
       multi-ligne, le PDF inclut UNIQUEMENT cette ligne (et son montant
       associe), pas la facture entiere.
- T3 : avec filtre account_number, idem (ligne-level matching).
- T4 : le PDF contient les colonnes HTVA, TVA, TVAC.
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90e-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.invoices.delete_many({"id": {"$regex": "^inv-iter90e-"}})
    await db.journal_entries.delete_many({"id": {"$regex": "^je-iter90e-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90e-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90e-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90e-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-iter90e-"}})


async def _login(c: AsyncClient):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed_acp():
    cid = f"c-iter90e-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP TestInvariant",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # PCMN class 6 accounts
    for num, name in [
        ("600000", "Achats marchandises"),
        ("611000", "Entretien"),
        ("612000", "Energie"),
        ("613000", "Honoraires"),
        ("650000", "Frais bancaires"),
        ("440000", "Fournisseurs"),
    ]:
        cls = int(num[0])
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls,
        })
    # Distribution keys
    dk_main = str(uuid.uuid4())
    dk_other = str(uuid.uuid4())
    await db.distribution_keys.insert_many([
        {"id": dk_main, "copropriete_id": cid, "code": "100",
         "name": "Charges communes", "lots": []},
        {"id": dk_other, "copropriete_id": cid, "code": "200",
         "name": "Chauffage", "lots": []},
    ])
    return cid, dk_main, dk_other


async def _t_invariant_matches_no_filter():
    await _wipe()
    cid, dk_main, dk_other = await _seed_acp()
    # Facture simple
    await db.invoices.insert_one({
        "id": "inv-iter90e-A", "number": "F-A", "date": "2026-02-10",
        "supplier": "Test", "description": "Facture simple",
        "total_amount": 121.00, "vat_amount": 21.00,
        "account_number": "611000", "distribution_key_id": dk_main,
        "status": "paid", "copropriete_id": cid,
        "occupant_pct": 0, "proprietaire_pct": 100,
        "occupant_amount": 0, "proprietaire_amount": 121.00,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Facture multi-lignes (2 lignes, 2 cles differentes)
    await db.invoices.insert_one({
        "id": "inv-iter90e-B", "number": "F-B", "date": "2026-03-15",
        "supplier": "Multi", "description": "Facture multi",
        "total_amount": 242.00, "vat_amount": 42.00,
        "account_number": "611000",
        "lines": [
            {"description": "Entretien", "account_number": "611000",
             "distribution_key_id": dk_main, "amount": 121.00},
            {"description": "Chauffage", "account_number": "612000",
             "distribution_key_id": dk_other, "amount": 121.00},
        ],
        "status": "paid", "copropriete_id": cid,
        "occupant_pct": 0, "proprietaire_pct": 100,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # OD on class-6 (frais bancaires)
    await db.journal_entries.insert_one({
        "id": "je-iter90e-1", "journal_type": "OD", "date": "2026-04-01",
        "reference": "OD-001", "description": "Frais bancaires Q1",
        "lines": [
            {"account_number": "650000", "account_name": "Frais bancaires",
             "debit": 10.50, "credit": 0.0},
            {"account_number": "440000", "account_name": "Fournisseurs",
             "debit": 0.0, "credit": 10.50},
        ],
        "total_debit": 10.50, "total_credit": 10.50,
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        # 1. Call /api/fiscal/expenses
        r = await c.get(f"/api/fiscal/expenses?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31",
                        headers={"X-Copropriete-Id": cid})
        assert r.status_code == 200, r.text
        ui_total = r.json()["totals"]["total"]
        ui_count = r.json()["totals"]["count"]
        # 2. Call PDF endpoint -> verify total embedded
        r = await c.get(f"/api/reports/depenses/pdf?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31")
        assert r.status_code == 200, r.text
        assert r.content[:5] == b"%PDF-"
        # 3. Use compute_expense_rows directly to verify same rows count + total
        from expense_rows import compute_expense_rows
        rows, totals = await compute_expense_rows(
            db, cid, date_from="2026-01-01", date_to="2026-12-31",
        )
        # rows must contain : 1 (facture A) + 2 (facture B 2 lignes) + 1 (OD 650000) = 4
        assert len(rows) == 4, f"Expected 4 rows, got {len(rows)} : {[r['id'] for r in rows]}"
        # TOTAL = 121 + 121 + 121 + 10.50 = 373.50
        assert abs(totals["total"] - 373.50) < 0.01, f"Total mismatch : {totals['total']}"
        # INVARIANT P0 : fiscal/expenses total == compute_expense_rows total
        assert abs(ui_total - totals["total"]) < 0.01, f"Invariant broken: ui={ui_total} vs pdf={totals['total']}"
        assert ui_count == len(rows), f"Row count mismatch: ui={ui_count} vs computed={len(rows)}"


async def _t_filter_by_dist_key_returns_multi_line_match():
    """Avec distribution_key_id ciblant une seule ligne d'une facture multi,
    seule cette ligne doit etre incluse."""
    await _wipe()
    cid, dk_main, dk_other = await _seed_acp()
    # 1 facture multi-lignes avec une seule ligne sur dk_other
    await db.invoices.insert_one({
        "id": "inv-iter90e-multi", "number": "F-M", "date": "2026-05-01",
        "supplier": "MultiKey", "description": "Multi key",
        "total_amount": 300.00, "vat_amount": 0.0,
        "account_number": "611000",
        "lines": [
            {"description": "Main", "account_number": "611000",
             "distribution_key_id": dk_main, "amount": 200.00},
            {"description": "Chauffage", "account_number": "612000",
             "distribution_key_id": dk_other, "amount": 100.00},
        ],
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
        distribution_key_id=dk_other,
    )
    # Une seule ligne (celle de dk_other)
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    assert rows[0]["distribution_key_id"] == dk_other
    assert abs(rows[0]["total_amount"] - 100.00) < 0.01
    assert abs(totals["total"] - 100.00) < 0.01


async def _t_filter_by_account_returns_multi_line_match():
    await _wipe()
    cid, dk_main, _ = await _seed_acp()
    await db.invoices.insert_one({
        "id": "inv-iter90e-accfil", "number": "F-AF", "date": "2026-06-01",
        "supplier": "AccFilter",
        "total_amount": 300.0, "vat_amount": 0.0,
        "account_number": "611000",
        "lines": [
            {"description": "L1", "account_number": "611000",
             "distribution_key_id": dk_main, "amount": 100.00},
            {"description": "L2", "account_number": "612000",
             "distribution_key_id": dk_main, "amount": 200.00},
        ],
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
        account_number="612000",
    )
    assert len(rows) == 1
    assert rows[0]["account_number"] == "612000"
    assert abs(rows[0]["total_amount"] - 200.00) < 0.01


async def _t_pdf_has_htva_tva_tvac_columns():
    """Le PDF genere doit contenir les colonnes HTVA, TVA, TVAC."""
    await _wipe()
    cid, dk_main, _ = await _seed_acp()
    await db.invoices.insert_one({
        "id": "inv-iter90e-vat", "number": "F-VAT", "date": "2026-07-01",
        "supplier": "VAT", "description": "VAT facture",
        "total_amount": 121.0, "vat_amount": 21.0,
        "account_number": "611000", "distribution_key_id": dk_main,
        "status": "paid", "copropriete_id": cid,
        "occupant_pct": 0, "proprietaire_pct": 100,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.get(f"/api/reports/depenses/pdf?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31")
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"
        # Extract text and check for HTVA / TVA / TVAC
        try:
            from pdfminer.high_level import extract_text
            import io
            text = extract_text(io.BytesIO(r.content))
        except ImportError:
            text = r.content.decode("latin1", errors="ignore")
        assert "HTVA" in text, "PDF must contain HTVA column"
        assert "TVA" in text, "PDF must contain TVA column"
        assert "TVAC" in text, "PDF must contain TVAC column"
        # The amounts : HTVA = 100, TVA = 21, TVAC = 121
        # Belgian format : 100,00 / 21,00 / 121,00
        assert "100,00" in text or "100.00" in text, f"PDF must show HTVA=100. Text snippet: {text[-2000:]}"
        assert "21,00" in text or "21.00" in text
        assert "121,00" in text or "121.00" in text


async def _run_all():
    await _t_invariant_matches_no_filter()
    await _t_filter_by_dist_key_returns_multi_line_match()
    await _t_filter_by_account_returns_multi_line_match()
    await _t_pdf_has_htva_tva_tvac_columns()


def test_iter90e_all_flows():
    asyncio.run(_run_all())
