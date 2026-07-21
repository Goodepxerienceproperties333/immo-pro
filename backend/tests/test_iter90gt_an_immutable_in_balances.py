"""iter90gt — Journal AN (A-Nouveau) immuable dans les balances tiers.

Regle utilisateur : "le journal A-Nouveau n'est pas pris en compte dans les
balances. c'est immuable". L'AN represente le solde d'ouverture d'exercice
et DOIT :
  1. Toujours apparaitre dans la situation de compte du fournisseur, avec
     sa vraie date d'exercice et sa reference AN-YYYY-XXX (pas agrege en
     "REPRISE" opaque quand il est DANS la periode courante).
  2. Toujours etre inclus dans le solde cumule de la balance-tiers/suppliers
     (endpoint agrege) meme si un filtre `start_date` posterieur est applique.
  3. Etre immuable (les tests s'assurent qu'il n'y a pas de re-ecriture ou
     de double-comptabilisation via `seen` dedup).

Bug pre-iter90gt : les AN dans la periode etaient agreges dans une ligne
"Reprise comptable" sans date -> l'utilisateur ne voyait plus l'origine du
solde (facture d'ouverture, provision, etc.) et pensait a tort que "l'AN
n'etait pas pris en compte".
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://copro-belge-app.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200
    return s


@pytest.fixture(scope="module")
def db():
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    from pymongo import MongoClient
    c = MongoClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _insert_supplier_with_an(db, copro_id: str, an_amount: float = 500.0):
    """Cree un fournisseur + 1 ecriture AN d'ouverture au CREDIT du compte tier."""
    sid = f"iter90gt-sup-{uuid.uuid4()}"
    tier_acc = "44088777"
    charge_acc = "61088777"
    db.suppliers.insert_one({
        "id": sid,
        "name": f"Fournisseur AN Test {uuid.uuid4().hex[:6]}",
        "copropriete_id": copro_id,
        "tier_accounts": {copro_id: {"main": tier_acc}},
    })
    # AN d'ouverture : DEBIT 550000 (banque fictive) / CREDIT tier fournisseur
    an_je = f"iter90gt-an-{uuid.uuid4()}"
    db.journal_entries.insert_one({
        "id": an_je,
        "journal_type": "AN",
        "date": "2026-01-01",  # 1er jour d'exercice
        "reference": "AN-TEST-001",
        "description": "OD d'ouverture test iter90gt",
        "copropriete_id": copro_id,
        "lines": [
            {"account_number": "550000", "debit": an_amount, "credit": 0.0, "is_opening_balance": True},
            {"account_number": tier_acc, "third_party_id": sid, "third_party_type": "supplier",
             "debit": 0.0, "credit": an_amount, "is_opening_balance": True},
        ],
        "total_debit": an_amount, "total_credit": an_amount,
    })
    return sid, an_je, tier_acc


def _cleanup(db, sid, je_id):
    db.suppliers.delete_one({"id": sid})
    db.journal_entries.delete_one({"id": je_id})


def test_iter90gt_an_visible_in_situation_de_compte_with_date_and_ref(admin_session, db):
    """iter90gu : la ligne REPRISE (Journal A-Nouveau) apparait en TETE de la
    situation de compte avec la date d'ouverture d'exercice, la reference
    AN-YYYY-XXX, et le montant issu du journal AN. Le running_balance part
    de cette reprise (base de calcul des mouvements suivants).
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"
    sid, an_je, tier_acc = _insert_supplier_with_an(db, copro_id, an_amount=500.0)
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/reports/balance-tiers/suppliers/{sid}",
            params={"copropriete_id": copro_id},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        movements = data["movements"]
        # Recherche la ligne REPRISE (is_reprise=True)
        reprise_lines = [m for m in movements if m.get("is_reprise")]
        assert len(reprise_lines) == 1, f"Une seule ligne REPRISE attendue, trouve {len(reprise_lines)} : {movements}"
        reprise = reprise_lines[0]
        # La date est celle de l'AN (immuable), pas start_date
        assert reprise["date"] == "2026-01-01", f"Date REPRISE doit etre l'AN 01/01/2026, got {reprise['date']}"
        # La reference expose l'AN reelle (traçabilite)
        assert "AN-TEST-001" in reprise["reference"], f"Ref REPRISE doit contenir 'AN-TEST-001', got {reprise['reference']}"
        assert reprise["credit"] == 500.0
        assert reprise["debit"] == 0.0
        assert reprise["journal_type"] == "AN"
        # Description mentionne "Journal A-Nouveau" (rappel metier / immuabilite)
        assert "A-Nouveau" in reprise["description"]
        # Running balance PART de la reprise (500€ apres cette 1ere ligne)
        assert reprise["running_balance"] == 500.0
        # Solde global inclut l'AN
        assert data["total_credit"] == 500.0
        assert data["balance"] == 500.0
        assert data["status"] == "crediteur"
    finally:
        _cleanup(db, sid, an_je)


def test_iter90gt_reprise_line_is_first_row_even_when_other_movements_share_date(admin_session, db):
    """iter90gu : la ligne REPRISE doit apparaitre AVANT tout autre mouvement
    partageant la meme date (AN au 01/01/YYYY, souvent = date d'une facture
    d'ouverture ou d'un OD). Le tri secondaire respecte `is_reprise` en tete.
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"
    sid, an_je, tier_acc = _insert_supplier_with_an(db, copro_id, an_amount=100.0)
    # Ajoute une facture AC au meme jour que l'AN (01/01/2026)
    inv_id = f"iter90gu-inv-{uuid.uuid4()}"
    ac_je = f"iter90gu-ac-{uuid.uuid4()}"
    db.invoices.insert_one({
        "id": inv_id, "supplier_id": sid, "supplier": "Test",
        "total_amount": 200.0, "copropriete_id": copro_id, "date": "2026-01-01",
        "internal_reference": "TEST-AC-001",
    })
    db.journal_entries.insert_one({
        "id": ac_je, "journal_type": "AC", "date": "2026-01-01",
        "reference": "TEST-AC-001", "description": "Facture jour AN",
        "source_invoice_id": inv_id, "copropriete_id": copro_id,
        "lines": [
            {"account_number": "61099998", "debit": 200.0, "credit": 0.0},
            {"account_number": tier_acc, "third_party_id": sid,
             "debit": 0.0, "credit": 200.0},
        ],
        "total_debit": 200.0, "total_credit": 200.0,
    })
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/reports/balance-tiers/suppliers/{sid}",
            params={"copropriete_id": copro_id},
            timeout=30,
        )
        assert r.status_code == 200
        movements = r.json()["movements"]
        # La 1ere ligne DOIT etre la REPRISE
        assert movements[0].get("is_reprise") is True, f"1ere ligne devrait etre REPRISE, got {movements[0]}"
        assert movements[0]["credit"] == 100.0
        assert movements[0]["running_balance"] == 100.0
        # 2eme : la facture (running = 100 + 200 = 300)
        assert not movements[1].get("is_reprise")
        assert movements[1]["credit"] == 200.0
        assert movements[1]["running_balance"] == 300.0
    finally:
        _cleanup(db, sid, an_je)
        db.invoices.delete_one({"id": inv_id})
        db.journal_entries.delete_one({"id": ac_je})


def test_iter90gt_an_immutable_in_cumulative_balance_with_start_date_filter(admin_session, db):
    """Meme avec un filtre `start_date` posterieur, l'AN doit rester dans le
    solde CUMULE de la balance-tiers/suppliers (endpoint agrege).

    L'AN est date du 01/01/2026 ; on filtre start_date=2026-06-01 -> l'AN
    n'apparait plus dans la periode filtree, MAIS le solde cumulatif doit
    quand meme l'inclure (regle "immuable").
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"
    sid, an_je, _ = _insert_supplier_with_an(db, copro_id, an_amount=777.77)
    try:
        r = admin_session.get(
            f"{BASE_URL}/api/reports/balance-tiers/suppliers",
            params={"copropriete_id": copro_id, "start_date": "2026-06-01"},
            timeout=30,
        )
        assert r.status_code == 200
        suppliers = r.json()["suppliers"]
        our = [s for s in suppliers if s["supplier_id"] == sid]
        assert len(our) == 1, f"Fournisseur test manquant dans balance : {[s['supplier_name'] for s in suppliers][:10]}"
        s = our[0]
        # Le solde CUMULE inclut l'AN meme si start_date=2026-06-01 (l'AN est du 01/01/2026)
        assert s["balance"] == 777.77, f"Solde cumule doit inclure l'AN meme filtre : got {s['balance']}"
        # Mais dans la periode filtree (Facture/Paye), rien
        assert s["total_invoiced"] == 0.0
        assert s["total_paid"] == 0.0
    finally:
        _cleanup(db, sid, an_je)
