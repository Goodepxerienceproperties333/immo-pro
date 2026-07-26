"""iter90gs — Note de credit fournisseur : cote debit correct.

Regle comptable belge (PCMN) :
  Facture fournisseur : DEBIT 6xxx (charge) / CREDIT 44xxxx (dette)
  Note de credit     : DEBIT 44xxxx (dette reduite) / CREDIT 6xxx (charge annulee)

Bug utilisateur (16/07/2026) :
  Situation de compte Engie affichait la NC au CREDIT au lieu du DEBIT.
  Cause : le fallback historique iter90fe (_line_matches) filtrait par
  `credit > 0` ce qui excluait la ligne compte tier au debit et remontait
  a la place la ligne de charges 61000 au credit -> double bug d'inversion.

Fix iter90gs : le fallback ne prend QUE les lignes 44XXXX (compte tier
fournisseur PCMN), quel que soit le sens debit/credit.

Ce test cree un mini-scenario minimaliste dans une ACP dediee, injecte
une facture + sa NC, et verifie que la situation de compte fournisseur
retourne bien la NC au DEBIT (correct) sans dupliquer la ligne de charges.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200
    return s


@pytest.fixture(scope="module")
def db():
    """Direct DB handle for injecting test data - E2E via API would require
    a full ACP + FY + supplier + PCMN setup which is heavy and covered
    elsewhere. Here we just check the read endpoint's line filtering logic.
    """
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from pymongo import MongoClient
    c = MongoClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _insert_test_supplier_and_entries(db, copro_id: str):
    """Insere un fournisseur + 1 facture normale + 1 NC. Retourne
    (supplier_id, facture_id, nc_id) pour cleanup."""
    supplier_id = f"iter90gs-sup-{uuid.uuid4()}"
    tier_acc = "44099999"  # compte tier de test - hors de la plage reelle
    charge_acc = "61099999"  # compte de charge de test
    db.suppliers.insert_one({
        "id": supplier_id,
        "name": "Test iter90gs SPRL",
        "copropriete_id": copro_id,
        "tier_accounts": {copro_id: {"main": tier_acc}},
    })
    # Facture normale : DEBIT charge / CREDIT tier
    fact_id = f"iter90gs-fact-{uuid.uuid4()}"
    fact_je = f"iter90gs-je-fact-{uuid.uuid4()}"
    db.invoices.insert_one({
        "id": fact_id, "supplier_id": supplier_id, "supplier": "Test iter90gs SPRL",
        "total_amount": 100.0, "is_credit_note": False,
        "copropriete_id": copro_id, "date": "2026-01-15",
        "internal_reference": "TEST-FA-001",
    })
    db.journal_entries.insert_one({
        "id": fact_je, "journal_type": "AC", "date": "2026-01-15",
        "reference": "TEST-FA-001", "description": "Facture test",
        "source_invoice_id": fact_id, "copropriete_id": copro_id,
        "lines": [
            {"account_number": charge_acc, "account_name": "Charge test", "debit": 100.0, "credit": 0.0},
            {"account_number": tier_acc, "account_name": "Test iter90gs SPRL",
             "third_party_id": supplier_id, "third_party_type": "supplier",
             "debit": 0.0, "credit": 100.0},
        ],
        "total_debit": 100.0, "total_credit": 100.0, "is_credit_note": False,
    })
    # Note de credit : DEBIT tier / CREDIT charge (regle PCMN belge)
    nc_id = f"iter90gs-nc-{uuid.uuid4()}"
    nc_je = f"iter90gs-je-nc-{uuid.uuid4()}"
    db.invoices.insert_one({
        "id": nc_id, "supplier_id": supplier_id, "supplier": "Test iter90gs SPRL",
        "total_amount": -30.0, "is_credit_note": True,
        "copropriete_id": copro_id, "date": "2026-01-20",
        "internal_reference": "TEST-NC-001",
    })
    db.journal_entries.insert_one({
        "id": nc_je, "journal_type": "AC", "date": "2026-01-20",
        "reference": "TEST-NC-001", "description": "NC test",
        "source_invoice_id": nc_id, "copropriete_id": copro_id,
        "lines": [
            {"account_number": charge_acc, "account_name": "Charge test", "debit": 0.0, "credit": 30.0},
            {"account_number": tier_acc, "account_name": "Test iter90gs SPRL",
             "third_party_id": supplier_id, "third_party_type": "supplier",
             "debit": 30.0, "credit": 0.0},
        ],
        "total_debit": 30.0, "total_credit": 30.0, "is_credit_note": True,
    })
    return supplier_id, [fact_id, nc_id], [fact_je, nc_je]


def _cleanup(db, supplier_id, invoice_ids, je_ids):
    db.suppliers.delete_one({"id": supplier_id})
    db.invoices.delete_many({"id": {"$in": invoice_ids}})
    db.journal_entries.delete_many({"id": {"$in": je_ids}})


def test_iter90gs_nc_shown_at_debit_not_credit(admin_session, db):
    """La NC doit apparaitre au DEBIT dans la situation de compte fournisseur.
    Aucun mouvement sur le compte de charge ne doit remonter (bug pre-fix)."""
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"  # ACP Maria - existante
    supplier_id, inv_ids, je_ids = _insert_test_supplier_and_entries(db, copro_id)
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/reports/balance-tiers/suppliers/{supplier_id}",
            params={"copropriete_id": copro_id},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        movements = data["movements"]
        # 2 mouvements attendus (facture + NC), 0 sur compte charge
        charge_moves = [m for m in movements if m["account_number"] == "61099999"]
        assert len(charge_moves) == 0, (
            f"Aucune ligne du compte de charge 61099999 ne doit apparaitre "
            f"dans la situation fournisseur. Trouve : {charge_moves}"
        )
        # La ligne de la NC doit etre au DEBIT (regle PCMN)
        nc_moves = [m for m in movements if m["reference"] == "TEST-NC-001"]
        assert len(nc_moves) == 1, f"1 seule ligne NC attendue, trouve : {nc_moves}"
        nc = nc_moves[0]
        assert nc["debit"] == 30.0, f"NC devrait etre au DEBIT 30.00, got debit={nc['debit']}"
        assert nc["credit"] == 0.0, f"NC devrait avoir credit=0, got {nc['credit']}"
        # La facture doit rester au CREDIT
        fact_moves = [m for m in movements if m["reference"] == "TEST-FA-001"]
        assert len(fact_moves) == 1
        fact = fact_moves[0]
        assert fact["credit"] == 100.0
        assert fact["debit"] == 0.0
        # Solde : 100 (dette facture) - 30 (NC reduit dette) = 70 crediteur
        assert data["total_debit"] == 30.0
        assert data["total_credit"] == 100.0
        assert data["balance"] == 70.0
        assert data["status"] == "crediteur"
    finally:
        _cleanup(db, supplier_id, inv_ids, je_ids)


def test_iter90gs_fallback_orphan_invoice_still_picks_tier_line(admin_session, db):
    """Verifie que le fallback historique (source_invoice_id sans tpid ni tier_acc match)
    prend bien la ligne 44XXXX (pas la ligne 6XXXX charge). Regression test.
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"
    # Fournisseur SANS tier_accounts declares dans cette copro -> force le fallback
    supplier_id = f"iter90gs-orphan-{uuid.uuid4()}"
    db.suppliers.insert_one({
        "id": supplier_id, "name": "Orphan Legacy SA",
        "copropriete_id": copro_id, "tier_accounts": {},
    })
    inv_id = f"iter90gs-orphan-inv-{uuid.uuid4()}"
    je_id = f"iter90gs-orphan-je-{uuid.uuid4()}"
    db.invoices.insert_one({
        "id": inv_id, "supplier_id": supplier_id, "supplier": "Orphan Legacy SA",
        "total_amount": 50.0, "copropriete_id": copro_id, "date": "2026-02-01",
        "internal_reference": "TEST-ORPH-001",
    })
    # Ecriture SANS third_party_id sur les lignes (legacy)
    db.journal_entries.insert_one({
        "id": je_id, "journal_type": "AC", "date": "2026-02-01",
        "reference": "TEST-ORPH-001", "description": "Legacy",
        "source_invoice_id": inv_id, "copropriete_id": copro_id,
        "lines": [
            {"account_number": "61088888", "debit": 50.0, "credit": 0.0},
            {"account_number": "44088888", "debit": 0.0, "credit": 50.0},
        ],
        "total_debit": 50.0, "total_credit": 50.0,
    })
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/reports/balance-tiers/suppliers/{supplier_id}",
            params={"copropriete_id": copro_id},
            timeout=30,
        )
        assert r.status_code == 200
        movements = r.json()["movements"]
        # Une seule ligne : la 44088888 (jamais la 61088888)
        assert len(movements) == 1, f"1 seul mouvement attendu (44088888), trouve {len(movements)}: {movements}"
        assert movements[0]["account_number"] == "44088888"
        assert movements[0]["credit"] == 50.0
    finally:
        db.suppliers.delete_one({"id": supplier_id})
        db.invoices.delete_one({"id": inv_id})
        db.journal_entries.delete_one({"id": je_id})
