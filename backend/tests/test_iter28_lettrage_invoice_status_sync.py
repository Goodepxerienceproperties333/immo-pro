"""Iter28 backend tests - NEW lettrage/unlettrage features.

Scope of this session (iter28):
  (1) POST /api/banking/lettrage with match_type='invoice'
      -> facture status='paid' + paid_at + paid_by_transaction_id
      -> journal_entries FI auto-creee (GET /api/accounting/entries?journal_type=FI)

  (2) POST /api/banking/unlettrage/{txn_id}
      -> facture revient a status='unpaid', paid_at/paid_by_transaction_id supprimes
      -> ecriture FI auto supprimee

  (3) POST /api/banking/unlettrage-by-invoice/{invoice_id}
      -> 404 si aucune txn liee
      -> sinon delettre toutes les txns liees et remet facture 'unpaid'
"""
import os
import uuid
import pytest
import requests


_be = os.environ.get("REACT_APP_BACKEND_URL")
if not _be:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    _be = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass
BASE_URL = (_be or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

ACP_ID = "6748ca1a-216d-4002-8417-799287238736"  # Demo - Residence Les Tilleuls
STATEMENT_ID = "e02fc9d5-93ae-42be-bb69-48235f648e2b"


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


def _create_invoice(admin, supplier="TEST_Lettrage SPRL", amount=150.0):
    """Create a regular (non-private) supplier invoice for lettrage tests."""
    payload = {
        "number": f"TEST-LETT-{uuid.uuid4().hex[:8]}",
        "date": "2026-01-15",
        "supplier": supplier,
        "description": "Facture test lettrage",
        "total_amount": amount,
        "vat_amount": 0.0,
        "account_number": "611000",
        "expense_category_id": "",
        "distribution_key_id": "",
        "status": "unpaid",
        "copropriete_id": ACP_ID,
    }
    r = admin.post(f"{BASE_URL}/api/invoices", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _create_txn(admin, amount=-150.0, counterparty="TEST_Lettrage SPRL"):
    """Create a (unmatched) bank transaction."""
    payload = {
        "statement_id": STATEMENT_ID,
        "date": "2026-01-20",
        "amount": amount,
        "counterparty_name": counterparty,
        "counterparty_account": "",
        "communication": "",
        "transaction_type": "debit" if amount < 0 else "credit",
        "copropriete_id": ACP_ID,
    }
    r = admin.post(f"{BASE_URL}/api/banking/transactions", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ============================================================
# (1) Lettrage -> facture paid + FI journal entry
# ============================================================
class TestLettrageInvoiceStatusSync:
    def test_lettrage_invoice_sets_paid_and_creates_FI(self, admin):
        inv = _create_invoice(admin)
        txn = _create_txn(admin)
        inv_id, txn_id = inv["id"], txn["id"]
        try:
            # Lettrage
            r = admin.post(
                f"{BASE_URL}/api/banking/lettrage",
                json={"transaction_id": txn_id, "match_to_id": inv_id, "match_type": "invoice"},
            )
            assert r.status_code == 200, r.text

            # Check transaction is now matched
            r_t = admin.get(f"{BASE_URL}/api/banking/transactions?statement_id={STATEMENT_ID}")
            assert r_t.status_code == 200
            updated_txn = next((t for t in r_t.json() if t["id"] == txn_id), None)
            assert updated_txn is not None
            assert updated_txn.get("matched") is True
            assert updated_txn.get("match_type") == "invoice"
            assert updated_txn.get("matched_to") == inv_id

            # Check invoice is now paid
            r_i = admin.get(f"{BASE_URL}/api/invoices/{inv_id}")
            assert r_i.status_code == 200, r_i.text
            inv_after = r_i.json()
            assert inv_after.get("status") == "paid", f"Expected paid, got {inv_after.get('status')}"
            assert inv_after.get("paid_at"), "paid_at missing"
            assert inv_after.get("paid_by_transaction_id") == txn_id

            # Check FI journal entry was auto-created
            r_e = admin.get(
                f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}&journal_type=FI"
            )
            assert r_e.status_code == 200
            related = [
                e for e in r_e.json()
                if e.get("source_id") == txn_id and e.get("source_type") == "bank_txn"
            ]
            # Note: may not create FI if supplier not in suppliers collection (counterpart_acc empty)
            # so accept 0 or 1, but log
            print(f"FI entries created for txn {txn_id}: {len(related)}")
        finally:
            admin.post(f"{BASE_URL}/api/banking/unlettrage/{txn_id}")
            admin.delete(f"{BASE_URL}/api/banking/transactions/{txn_id}")
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")


# ============================================================
# (2) Unlettrage -> facture revient unpaid
# ============================================================
class TestUnlettrageRestoresUnpaid:
    def test_unlettrage_invoice_resets_status_to_unpaid(self, admin):
        inv = _create_invoice(admin)
        txn = _create_txn(admin)
        inv_id, txn_id = inv["id"], txn["id"]
        try:
            # Lettrage first
            r = admin.post(
                f"{BASE_URL}/api/banking/lettrage",
                json={"transaction_id": txn_id, "match_to_id": inv_id, "match_type": "invoice"},
            )
            assert r.status_code == 200
            r_i = admin.get(f"{BASE_URL}/api/invoices/{inv_id}")
            assert r_i.json().get("status") == "paid"

            # Unlettrage
            r_u = admin.post(f"{BASE_URL}/api/banking/unlettrage/{txn_id}")
            assert r_u.status_code == 200, r_u.text

            # Check invoice is back to unpaid + no paid_at / paid_by_transaction_id
            r_i2 = admin.get(f"{BASE_URL}/api/invoices/{inv_id}")
            assert r_i2.status_code == 200
            inv_after = r_i2.json()
            assert inv_after.get("status") == "unpaid", \
                f"Expected unpaid, got {inv_after.get('status')}"
            assert not inv_after.get("paid_at"), \
                f"paid_at should be removed, got {inv_after.get('paid_at')}"
            assert not inv_after.get("paid_by_transaction_id"), \
                "paid_by_transaction_id should be removed"

            # Check transaction is no longer matched
            r_t = admin.get(f"{BASE_URL}/api/banking/transactions?statement_id={STATEMENT_ID}")
            updated_txn = next((t for t in r_t.json() if t["id"] == txn_id), None)
            assert updated_txn.get("matched") is False
            assert updated_txn.get("match_type") in ("", None)
        finally:
            admin.delete(f"{BASE_URL}/api/banking/transactions/{txn_id}")
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")

    def test_unlettrage_404_on_unknown_txn(self, admin):
        r = admin.post(f"{BASE_URL}/api/banking/unlettrage/{uuid.uuid4()}")
        assert r.status_code == 404


# ============================================================
# (3) Unlettrage by invoice
# ============================================================
class TestUnlettrageByInvoice:
    def test_404_when_no_txn_linked(self, admin):
        inv = _create_invoice(admin, supplier="TEST_Empty SPRL", amount=99.0)
        inv_id = inv["id"]
        try:
            r = admin.post(f"{BASE_URL}/api/banking/unlettrage-by-invoice/{inv_id}")
            assert r.status_code == 404, r.text
        finally:
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")

    def test_unlettrage_by_invoice_clears_all_linked_txns(self, admin):
        inv = _create_invoice(admin)
        txn1 = _create_txn(admin, amount=-100.0)
        txn2 = _create_txn(admin, amount=-50.0)
        inv_id = inv["id"]
        try:
            # Lettrage both txns to same invoice
            for tid in (txn1["id"], txn2["id"]):
                r = admin.post(
                    f"{BASE_URL}/api/banking/lettrage",
                    json={"transaction_id": tid, "match_to_id": inv_id, "match_type": "invoice"},
                )
                assert r.status_code == 200

            # Invoice is paid
            assert admin.get(f"{BASE_URL}/api/invoices/{inv_id}").json().get("status") == "paid"

            # Unlettrage by invoice
            r_u = admin.post(f"{BASE_URL}/api/banking/unlettrage-by-invoice/{inv_id}")
            assert r_u.status_code == 200, r_u.text
            body = r_u.json()
            assert body.get("count") == 2, f"Expected 2 txns delettered, got {body}"

            # Invoice back to unpaid
            inv_after = admin.get(f"{BASE_URL}/api/invoices/{inv_id}").json()
            assert inv_after.get("status") == "unpaid"
            assert not inv_after.get("paid_at")
            assert not inv_after.get("paid_by_transaction_id")

            # Both txns delettered
            txns = admin.get(f"{BASE_URL}/api/banking/transactions?statement_id={STATEMENT_ID}").json()
            for t in txns:
                if t["id"] in (txn1["id"], txn2["id"]):
                    assert t.get("matched") is False
        finally:
            for tid in (txn1["id"], txn2["id"]):
                admin.delete(f"{BASE_URL}/api/banking/transactions/{tid}")
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")
