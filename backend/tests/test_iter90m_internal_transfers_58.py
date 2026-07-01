"""Iter90m — Permettre la catégorisation d'une transaction bancaire sur le
compte 58* (Virements internes, PCMN belge, classe 5).

Contexte : Un syndic effectue régulièrement des virements entre le compte
à vue (551*) et le compte épargne (551* ou 552*) de la copropriété. Ces
mouvements ne sont ni des charges ni des produits — ils utilisent le
compte 58 (Virements internes) comme compte de passage, qui doit revenir
à zéro une fois les 2 transactions du transfert saisies.

Tests :
- T1 : Créer une expense_category sur compte 58 (classe 5) → OK
- T2 : Créer une expense_category sur compte 590000 (classe 5 non-58) → 400
- T3 : Catégoriser une transaction bancaire (virement sortant) avec la
       nature "Virement interne" → écriture FI créée
- T4 : Le compte 58 N'APPARAIT PAS dans /api/fiscal/expenses (invariant
       critique : ni charge ni produit, jamais dans la liste des dépenses)
- T5 : Symétrie : le virement entrant sur l'autre extrait crédite 58 → les
       2 mouvements équilibrent le compte 58 à zéro
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90m-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-iter90m-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-iter90m-"}})
    await db.journal_entries.delete_many({"source_type": "bank_txn",
                                          "source_id": {"$regex": "^bt-iter90m-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90m-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90m-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90m-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-iter90m-"}})


async def _login(c):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed_acp():
    cid = f"c-iter90m-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP Iter90m",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    for num, name, cls in [
        ("551000", "Compte a vue", 5),
        ("552000", "Compte epargne", 5),
        ("580000", "Virements internes", 5),
        ("590000", "Autre compte cl5", 5),  # classe 5 non-58 -> doit etre refuse
        ("611000", "Entretien", 6),
    ]:
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls, "active": True,
        })
    dk = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": dk, "copropriete_id": cid, "code": "100",
        "name": "Charges communes", "lots": [],
    })
    return cid, dk


async def _t_create_category_on_58_ok():
    await _wipe()
    cid, _ = await _seed_acp()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post("/api/expense-categories", json={
            "name": "Virement interne (vue -> epargne)",
            "account_number": "580000",
            "copropriete_id": cid,
            "default_occupant_pct": 0,
            "default_proprietaire_pct": 100,
        })
        assert r.status_code == 200, r.text
        cat = r.json()
        assert cat["kind"] == "transfer"
        assert cat["account_number"] == "580000"


async def _t_create_category_on_class5_not58_rejected():
    await _wipe()
    cid, _ = await _seed_acp()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post("/api/expense-categories", json={
            "name": "Compte cl5 autre",
            "account_number": "590000",
            "copropriete_id": cid,
            "default_occupant_pct": 0,
            "default_proprietaire_pct": 100,
        })
        assert r.status_code == 400
        assert "58" in r.text or "Virements internes" in r.text


async def _t_categorize_transaction_on_58():
    """Transaction sortante 1500 EUR virement vers epargne : catégoriser
    avec la nature 'Virement interne' -> FI genere Dr 58 / Cr 550."""
    await _wipe()
    cid, dk = await _seed_acp()
    # 1 expense_category "Virement interne" sur compte 580000
    cat_id = str(uuid.uuid4())
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid,
        "name": "Virement interne", "account_number": "580000",
        "kind": "transfer",
    })
    # 1 statement + 1 transaction (débit 1500)
    sid = f"bs-iter90m-{uuid.uuid4().hex[:8]}"
    await db.bank_statements.insert_one({
        "id": sid, "number": "S-M", "date": "2026-02-10",
        "account_number": "551000", "opening_balance": 5000.0,
        "closing_balance": 3500.0, "status": "draft",
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tid = f"bt-iter90m-{uuid.uuid4().hex[:8]}"
    await db.bank_transactions.insert_one({
        "id": tid, "statement_id": sid, "date": "2026-02-08",
        "amount": -1500.0, "account_number": "551000",
        "transaction_type": "debit",
        "counterparty_name": "ACP Compte epargne",
        "communication": "Transfert vers fonds de reserve",
        "matched": False, "match_type": "", "matched_to": "",
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat_id,
                        "distribution_key_id": dk,
                        "amount": 1500.0,
                        "description": "Vers fonds de reserve"}]
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["journal_entry_id"]
        # Verifier l'ecriture FI
        je = await db.journal_entries.find_one(
            {"id": body["journal_entry_id"]}, {"_id": 0}
        )
        assert je["journal_type"] == "FI"
        accs = [line["account_number"] for line in je["lines"]]
        assert "580000" in accs
        assert any(a.startswith("55") for a in accs)
        # 58 en debit (money out), 55x en credit
        line_58 = next(l for l in je["lines"] if l["account_number"] == "580000")
        line_bk = next(l for l in je["lines"] if l["account_number"].startswith("55"))
        assert line_58["debit"] == 1500.0 and line_58["credit"] == 0.0
        assert line_bk["credit"] == 1500.0 and line_bk["debit"] == 0.0


async def _t_account_58_hors_expenses():
    """INVARIANT CRITIQUE : le compte 58 NE DOIT JAMAIS apparaitre dans
    /api/fiscal/expenses meme si une ecriture FI le porte."""
    await _wipe()
    cid, dk = await _seed_acp()
    # Ecriture FI directe portant 580000 en debit
    await db.journal_entries.insert_one({
        "id": "je-iter90m-transfer", "journal_type": "FI", "date": "2026-02-08",
        "reference": "TEST-VIR-INT", "description": "Vir vers epargne",
        "lines": [
            {"account_number": "580000", "account_name": "Vir int",
             "debit": 1500.0, "credit": 0.0, "distribution_key_id": dk},
            {"account_number": "551000", "account_name": "Cpt vue",
             "debit": 0.0, "credit": 1500.0},
        ],
        "total_debit": 1500.0, "total_credit": 1500.0,
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Aussi une vraie charge 611000 pour comparaison
    await db.invoices.insert_one({
        "id": "inv-iter90m-charge", "number": "F-M", "date": "2026-02-10",
        "supplier": "Entretien", "description": "Nettoyage",
        "total_amount": 250.0, "vat_amount": 43.39,
        "account_number": "611000", "distribution_key_id": dk,
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
    )
    accs = [r["account_number"] for r in rows]
    assert "580000" not in accs, \
        f"580000 ne doit PAS etre dans expenses, got: {accs}"
    assert not any(a.startswith("58") for a in accs)
    # La charge 611000 est bien la
    assert "611000" in accs
    # Total = 250 (charge uniquement, virement exclu)
    assert abs(totals["total"] - 250.0) < 0.01, \
        f"Total attendu 250.00, got {totals['total']}"


async def _t_symmetric_transfer_equilibrates_58():
    """Simulation complete : 2 transactions (sortante compte vue + entrante
    compte epargne) qui équilibrent le compte 58 à zéro."""
    await _wipe()
    cid, dk = await _seed_acp()
    # Category
    cat = str(uuid.uuid4())
    await db.expense_categories.insert_one({
        "id": cat, "copropriete_id": cid,
        "name": "Virement interne", "account_number": "580000", "kind": "transfer",
    })
    # 2 statements (1 par compte)
    for sid, acc, opening, closing in [
        ("bs-iter90m-vue", "551000", 5000.0, 3500.0),
        ("bs-iter90m-epa", "552000", 10000.0, 11500.0),
    ]:
        await db.bank_statements.insert_one({
            "id": sid, "number": f"S-{acc}", "date": "2026-02-10",
            "account_number": acc,
            "opening_balance": opening, "closing_balance": closing,
            "status": "draft", "copropriete_id": cid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    # 2 transactions symetriques
    tid_out = "bt-iter90m-out"
    tid_in = "bt-iter90m-in"
    await db.bank_transactions.insert_many([
        {
            "id": tid_out, "statement_id": "bs-iter90m-vue", "date": "2026-02-08",
            "amount": -1500.0, "account_number": "551000",
            "transaction_type": "debit",
            "counterparty_name": "Vers epargne", "communication": "Transfert",
            "matched": False, "match_type": "", "matched_to": "",
            "copropriete_id": cid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "id": tid_in, "statement_id": "bs-iter90m-epa", "date": "2026-02-08",
            "amount": +1500.0, "account_number": "552000",
            "transaction_type": "credit",
            "counterparty_name": "Depuis vue", "communication": "Transfert",
            "matched": False, "match_type": "", "matched_to": "",
            "copropriete_id": cid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    ])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        for tid in (tid_out, tid_in):
            r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
                "splits": [{"expense_category_id": cat,
                            "distribution_key_id": dk, "amount": 1500.0}]
            })
            assert r.status_code == 200, r.text
    # Verifier que 58 s'equilibre (somme debits - credits = 0)
    entries = await db.journal_entries.find(
        {"copropriete_id": cid, "lines.account_number": "580000"},
        {"_id": 0, "lines": 1},
    ).to_list(100)
    debit_58 = sum(l["debit"] for e in entries for l in e["lines"]
                   if l["account_number"] == "580000")
    credit_58 = sum(l["credit"] for e in entries for l in e["lines"]
                    if l["account_number"] == "580000")
    assert abs(debit_58 - credit_58) < 0.01, \
        f"Compte 58 non equilibre: dr={debit_58}, cr={credit_58}"


async def _run_all():
    await _t_create_category_on_58_ok()
    await _t_create_category_on_class5_not58_rejected()
    await _t_categorize_transaction_on_58()
    await _t_account_58_hors_expenses()
    await _t_symmetric_transfer_equilibrates_58()


def test_iter90m_internal_transfers_58():
    asyncio.run(_run_all())
