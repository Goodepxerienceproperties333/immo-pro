"""Iter90k — Catégoriser une transaction bancaire par nature de dépense/revenu.

Feature : dans les extraits de compte, permettre d'assigner une (ou N via
splits) natures de dépense/revenu à une transaction bancaire non lettrée
(frais bancaires, intérêts créditeurs, commissions…). L'écriture FI
générée automatiquement fait apparaître ces montants dans la "Liste des
dépenses" (charges positives, produits négatifs).

Tests :
- T1  Une transaction débit (frais bancaires) → 1 split classe 6 → FI généré,
      apparaît dans /api/fiscal/expenses en positif.
- T2  Une transaction crédit (intérêts créditeurs) → 1 split classe 7 (75x)
      → FI généré, apparaît dans /api/fiscal/expenses en négatif (produit).
- T3  Multi-splits sur une transaction 100EUR = 80 frais + 20 commission →
      2 lignes dans le FI + 2 rows dans compute_expense_rows.
- T4  Erreur si somme splits != montant transaction.
- T5  Erreur si distribution_key manquante.
- T6  Uncategorize supprime le FI et remet la transaction en état non lettré.
- T7  Un compte 70* (provisions) est rejeté à la catégorisation ou reste hors
      /api/fiscal/expenses (garde-fou anti-double-comptage avec les appels
      de fonds).
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90k-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.bank_transactions.delete_many({"id": {"$regex": "^bt-iter90k-"}})
    await db.bank_statements.delete_many({"id": {"$regex": "^bs-iter90k-"}})
    await db.journal_entries.delete_many({"source_type": "bank_txn",
                                          "source_id": {"$regex": "^bt-iter90k-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90k-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90k-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90k-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-iter90k-"}})


async def _login(c: AsyncClient):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed_acp():
    cid = f"c-iter90k-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP Iter90k",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # PCMN : quelques comptes classe 6 et 7 + banque + provisions
    accs = [
        ("550000", "Banque", 5),
        ("650000", "Charges financieres", 6),
        ("651000", "Commissions bancaires", 6),
        ("750000", "Interets crediteurs", 7),
        ("700000", "Provisions appelees", 7),
    ]
    for num, name, cls in accs:
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


async def _create_category(cid: str, name: str, account: str):
    cat_id = str(uuid.uuid4())
    pcmn = await db.pcmn_accounts.find_one(
        {"number": account, "copropriete_id": cid}, {"_id": 0}
    )
    await db.expense_categories.insert_one({
        "id": cat_id, "copropriete_id": cid,
        "name": name, "account_number": account,
        "kind": "produit" if pcmn and pcmn.get("class_num") == 7 else "charge",
    })
    return cat_id


async def _create_statement_and_txn(cid: str, amount: float, txn_id: str | None = None):
    """Cree un statement + une transaction non lettrée. Amount signé
    (positif = credit / negatif = debit)."""
    sid = f"bs-iter90k-{uuid.uuid4().hex[:8]}"
    await db.bank_statements.insert_one({
        "id": sid, "number": f"S-{sid[:6]}", "date": "2026-02-01",
        "account_number": "550000", "opening_balance": 0, "closing_balance": amount,
        "status": "draft", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tid = txn_id or f"bt-iter90k-{uuid.uuid4().hex[:8]}"
    await db.bank_transactions.insert_one({
        "id": tid, "statement_id": sid, "date": "2026-02-15",
        "amount": amount, "account_number": "550000",
        "transaction_type": "credit" if amount > 0 else "debit",
        "counterparty_name": "Bank", "communication": "",
        "matched": False, "match_type": "", "matched_to": "",
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return sid, tid


# === T1 : débit -> charge classe 6 => apparait en +montant dans expenses ===
async def _t_debit_to_charge_appears_positive():
    await _wipe()
    cid, dk = await _seed_acp()
    cat_frais = await _create_category(cid, "Frais bancaires", "650000")
    _, tid = await _create_statement_and_txn(cid, amount=-25.00)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat_frais,
                        "distribution_key_id": dk,
                        "amount": 25.00,
                        "description": "Frais tenue de compte Q1"}]
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["journal_entry_id"], "Un journal entry doit etre cree"
        # Verifier /api/fiscal/expenses
        r = await c.get(
            f"/api/fiscal/expenses?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31",
            headers={"X-Copropriete-Id": cid},
        )
        assert r.status_code == 200, r.text
        exp = r.json()
        assert exp["totals"]["count"] == 1, f"1 ligne attendue, got {exp['totals']['count']}"
        assert abs(exp["totals"]["total"] - 25.00) < 0.01, f"Total 25.00 attendu, got {exp['totals']['total']}"
        row = exp["expenses"][0]
        assert row["account_number"] == "650000"
        assert row["distribution_key_id"] == dk
        assert row["journal_type"] == "FI"
        assert row["source"] == "journal"


# === T2 : credit -> produit classe 7 (75x) => apparait en -montant ===
async def _t_credit_to_produit_appears_negative():
    await _wipe()
    cid, dk = await _seed_acp()
    cat_int = await _create_category(cid, "Interets crediteurs", "750000")
    _, tid = await _create_statement_and_txn(cid, amount=+13.42)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat_int,
                        "distribution_key_id": dk,
                        "amount": 13.42}]
        })
        assert r.status_code == 200, r.text
        r = await c.get(
            f"/api/fiscal/expenses?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31",
            headers={"X-Copropriete-Id": cid},
        )
        exp = r.json()
        assert exp["totals"]["count"] == 1
        # Produit : montant NEGATIF (debit=0, credit=13.42, amount = -13.42)
        assert abs(exp["totals"]["total"] - (-13.42)) < 0.01, f"Total -13.42 attendu, got {exp['totals']['total']}"
        row = exp["expenses"][0]
        assert row["account_number"] == "750000"
        assert row["total_amount"] < 0


# === T3 : multi-splits ===
async def _t_multi_splits():
    await _wipe()
    cid, dk = await _seed_acp()
    cat_frais = await _create_category(cid, "Frais bancaires", "650000")
    cat_comm = await _create_category(cid, "Commissions", "651000")
    _, tid = await _create_statement_and_txn(cid, amount=-100.00)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [
                {"expense_category_id": cat_frais, "distribution_key_id": dk, "amount": 80.0},
                {"expense_category_id": cat_comm, "distribution_key_id": dk, "amount": 20.0},
            ]
        })
        assert r.status_code == 200, r.text
        r = await c.get(
            f"/api/fiscal/expenses?copropriete_id={cid}&date_from=2026-01-01&date_to=2026-12-31",
            headers={"X-Copropriete-Id": cid},
        )
        exp = r.json()
        assert exp["totals"]["count"] == 2, f"2 rows attendues, got {exp['totals']['count']}"
        assert abs(exp["totals"]["total"] - 100.00) < 0.01
        accs = sorted(r["account_number"] for r in exp["expenses"])
        assert accs == ["650000", "651000"]


# === T4 : erreur si somme splits != montant ===
async def _t_error_sum_mismatch():
    await _wipe()
    cid, dk = await _seed_acp()
    cat = await _create_category(cid, "Frais", "650000")
    _, tid = await _create_statement_and_txn(cid, amount=-100.00)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat, "distribution_key_id": dk,
                        "amount": 50.0}]  # 50 != 100
        })
        assert r.status_code == 400
        assert "somme" in r.text.lower() or "sum" in r.text.lower()


# === T5 : erreur si distribution_key manquante ===
async def _t_error_no_dk():
    await _wipe()
    cid, _ = await _seed_acp()
    cat = await _create_category(cid, "Frais", "650000")
    _, tid = await _create_statement_and_txn(cid, amount=-25.00)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat, "distribution_key_id": "",
                        "amount": 25.0}]
        })
        assert r.status_code == 400
        assert "cle" in r.text.lower() or "repartition" in r.text.lower()


# === T6 : uncategorize supprime le FI ===
async def _t_uncategorize_removes_je():
    await _wipe()
    cid, dk = await _seed_acp()
    cat = await _create_category(cid, "Frais", "650000")
    _, tid = await _create_statement_and_txn(cid, amount=-25.00)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.post(f"/api/banking/transactions/{tid}/categorize", json={
            "splits": [{"expense_category_id": cat, "distribution_key_id": dk, "amount": 25.0}]
        })
        assert r.status_code == 200
        # 1 JE existe
        count_before = await db.journal_entries.count_documents(
            {"source_type": "bank_txn", "source_id": tid}
        )
        assert count_before == 1
        # Uncategorize
        r = await c.delete(f"/api/banking/transactions/{tid}/categorize")
        assert r.status_code == 200
        # JE supprimé
        count_after = await db.journal_entries.count_documents(
            {"source_type": "bank_txn", "source_id": tid}
        )
        assert count_after == 0, f"JE should be removed, got {count_after}"
        # Transaction remise en état non lettré
        txn = await db.bank_transactions.find_one({"id": tid}, {"_id": 0})
        assert txn["matched"] is False
        assert txn.get("match_type") == ""
        assert "category_splits" not in txn or not txn.get("category_splits")


# === T7 : compte 70* provision (appels de fonds) reste hors expenses ===
async def _t_class7_provisions_hors_expenses():
    """Meme si un utilisateur cree une categorie sur 700000 (interdit
    normalement mais possible), le montant ne doit PAS apparaitre dans
    la liste des depenses (garde-fou anti-double-comptage avec VE)."""
    await _wipe()
    cid, dk = await _seed_acp()
    # Ecriture FI factice avec ligne 700000
    await db.journal_entries.insert_one({
        "id": "je-iter90k-prov", "journal_type": "FI", "date": "2026-03-01",
        "reference": "TEST-PROV", "description": "Faux provision",
        "lines": [
            {"account_number": "550000", "account_name": "Banque",
             "debit": 500.0, "credit": 0.0},
            {"account_number": "700000", "account_name": "Provisions",
             "debit": 0.0, "credit": 500.0, "distribution_key_id": dk},
        ],
        "total_debit": 500.0, "total_credit": 500.0,
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
    )
    # 700000 doit etre exclu
    assert all(not r["account_number"].startswith("70") for r in rows), \
        f"Aucun compte 70* ne doit apparaitre : {[(r['account_number'], r['total_amount']) for r in rows]}"


async def _run_all():
    await _t_debit_to_charge_appears_positive()
    await _t_credit_to_produit_appears_negative()
    await _t_multi_splits()
    await _t_error_sum_mismatch()
    await _t_error_no_dk()
    await _t_uncategorize_removes_je()
    await _t_class7_provisions_hors_expenses()


def test_iter90k_categorize_bank_transaction():
    asyncio.run(_run_all())
