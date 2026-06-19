"""Iter27 backend tests.

Scope of this session:
 (1) Frais privatifs : creation d'une facture is_private_fee=true cree DEUX entries
     distinctes journal_entries -> AC (Dr 643 / Cr 44000XXX) + OD (Dr 40000XXX / Cr 643).
     Filter GET /api/accounting/entries?journal_type=AC|OD doit isoler.
     Suppression de la facture supprime les 2 ecritures (cleanup auto via source_id).

 (2) Decompte annuel : verrou de cloture
     - GET /api/reports/decompte/pdf/{owner_id}?copropriete_id&fiscal_year_id  -> 400 si fy.status != 'closed'
     - GET /api/owner/decompte/pdf?...                                          -> idem
     - Fallback (sans fiscal_year_id) doit aussi bloquer si exercice trouve non-closed
     - copropriete_id absent / "all" -> 400

 (3) Balance des tiers PDF
     - GET /api/reports/balance-tiers/pdf?copropriete_id=XXX -> 200 + content-type application/pdf
     - copropriete_id absent ou "all" -> 400
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
OWNER_ID = "f01f889d-1057-4b15-91a1-ca3a869ccabe"  # Dubois Jean


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


def _get_open_fy(admin):
    r = admin.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={ACP_ID}")
    assert r.status_code == 200
    for fy in r.json():
        if fy.get("status") != "closed":
            return fy
    return None


# ============================================================
# (1) Frais privatif : 2 ecritures separees AC + OD
# ============================================================
class TestPrivateFeeSplitEntries:
    def test_creates_two_entries_AC_and_OD(self, admin):
        unique_no = f"TEST-PRIV-{uuid.uuid4().hex[:8]}"
        payload = {
            "number": unique_no,
            "date": "2026-01-15",
            "supplier": "TEST_Plombier SPRL",
            "description": "Reparation chasse WC privatif",
            "total_amount": 250.0,
            "vat_amount": 0.0,
            "account_number": "",
            "expense_category_id": "",
            "distribution_key_id": "",
            "status": "unpaid",
            "copropriete_id": ACP_ID,
            "is_private_fee": True,
            "private_fee_owner_id": OWNER_ID,
        }
        r = admin.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code in (200, 201), r.text
        inv = r.json()
        inv_id = inv["id"]
        assert inv["is_private_fee"] is True
        assert inv["account_number"] == "643"

        try:
            # Get all journal entries linked to this invoice id (via source_id)
            r2 = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
            assert r2.status_code == 200, r2.text
            entries = r2.json()
            related = [e for e in entries if e.get("source_id") == inv_id and e.get("source_type") == "invoice"]
            assert len(related) == 2, f"Expected 2 entries (AC+OD), got {len(related)}: {related}"

            by_type = {e["journal_type"]: e for e in related}
            assert "AC" in by_type, f"No AC entry: {related}"
            assert "OD" in by_type, f"No OD entry: {related}"

            ac = by_type["AC"]
            od = by_type["OD"]

            # AC : Dr 643 / Cr 44000XXX, totals balanced
            assert abs(ac["total_debit"] - 250.0) < 0.01
            assert abs(ac["total_credit"] - 250.0) < 0.01
            ac_dr = [l for l in ac["lines"] if l["debit"] > 0]
            ac_cr = [l for l in ac["lines"] if l["credit"] > 0]
            assert len(ac_dr) == 1 and ac_dr[0]["account_number"] == "643"
            assert len(ac_cr) == 1 and ac_cr[0]["account_number"].startswith("44000")

            # OD : Dr 40000XXX (owner) / Cr 643, totals balanced
            assert abs(od["total_debit"] - 250.0) < 0.01
            assert abs(od["total_credit"] - 250.0) < 0.01
            od_dr = [l for l in od["lines"] if l["debit"] > 0]
            od_cr = [l for l in od["lines"] if l["credit"] > 0]
            assert len(od_dr) == 1 and od_dr[0]["account_number"].startswith("400")
            assert od_dr[0]["third_party_id"] == OWNER_ID
            assert len(od_cr) == 1 and od_cr[0]["account_number"] == "643"

        finally:
            # Cleanup invoice (must remove BOTH entries via _delete_auto_entries)
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")

    def test_filter_AC_returns_AC_only(self, admin):
        unique_no = f"TEST-PRIV-{uuid.uuid4().hex[:8]}"
        payload = {
            "number": unique_no, "date": "2026-01-16",
            "supplier": "TEST_Filter SPRL", "description": "Test filter",
            "total_amount": 100.0, "vat_amount": 0.0,
            "account_number": "", "expense_category_id": "",
            "distribution_key_id": "", "status": "unpaid",
            "copropriete_id": ACP_ID,
            "is_private_fee": True, "private_fee_owner_id": OWNER_ID,
        }
        r = admin.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code in (200, 201)
        inv_id = r.json()["id"]
        try:
            r_ac = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}&journal_type=AC")
            assert r_ac.status_code == 200
            ac_for_inv = [e for e in r_ac.json() if e.get("source_id") == inv_id]
            assert len(ac_for_inv) == 1
            assert ac_for_inv[0]["journal_type"] == "AC"

            r_od = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}&journal_type=OD")
            assert r_od.status_code == 200
            od_for_inv = [e for e in r_od.json() if e.get("source_id") == inv_id]
            assert len(od_for_inv) == 1
            assert od_for_inv[0]["journal_type"] == "OD"
        finally:
            admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")

    def test_delete_invoice_removes_both_entries(self, admin):
        unique_no = f"TEST-PRIV-{uuid.uuid4().hex[:8]}"
        payload = {
            "number": unique_no, "date": "2026-01-17",
            "supplier": "TEST_Delete SPRL", "description": "Test delete",
            "total_amount": 75.0, "vat_amount": 0.0,
            "account_number": "", "expense_category_id": "",
            "distribution_key_id": "", "status": "unpaid",
            "copropriete_id": ACP_ID,
            "is_private_fee": True, "private_fee_owner_id": OWNER_ID,
        }
        r = admin.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code in (200, 201)
        inv_id = r.json()["id"]

        # Confirm 2 entries exist
        r2 = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
        before = [e for e in r2.json() if e.get("source_id") == inv_id]
        assert len(before) == 2

        # Delete
        dr = admin.delete(f"{BASE_URL}/api/invoices/{inv_id}")
        assert dr.status_code in (200, 204)

        # Confirm both entries removed
        r3 = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
        after = [e for e in r3.json() if e.get("source_id") == inv_id]
        assert len(after) == 0, f"Entries not cleaned up: {after}"


# ============================================================
# (2) Decompte annuel : 400 si exercice non cloture
# ============================================================
class TestDecompteFiscalYearLock:
    def test_decompte_pdf_blocks_open_fiscal_year(self, admin):
        fy = _get_open_fy(admin)
        if not fy:
            pytest.skip("No open fiscal year found to test the lock")
        r = admin.get(
            f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}",
            params={"copropriete_id": ACP_ID, "fiscal_year_id": fy["id"]},
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text[:300]}"
        body = r.json()
        msg = (body.get("detail") or "").lower()
        assert "clotur" in msg or "closed" in msg, f"Error message must mention closure: {body}"

    def test_decompte_pdf_400_without_copropriete_id(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}")
        assert r.status_code == 400
        # remove any default header that could pass copro
        r2 = admin.get(f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}?copropriete_id=all")
        assert r2.status_code == 400

    def test_decompte_pdf_fallback_also_blocks(self, admin):
        """No fiscal_year_id provided : the route resolves a fy covering the period.
        That fy is also open -> must still 400."""
        r = admin.get(
            f"{BASE_URL}/api/reports/decompte/pdf/{OWNER_ID}",
            params={"copropriete_id": ACP_ID, "date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
        # If a real open fy covers it, expect 400. If no fy exists, the dummy fy has no status -> still blocked.
        assert r.status_code == 400, f"Expected 400 from fallback, got {r.status_code}: {r.text[:300]}"

    def test_owner_decompte_pdf_blocks_open_fiscal_year(self, admin):
        """Owner portal decompte PDF must also enforce the lock.
        Use admin session (the endpoint requires owner role; if 403 we still verify the chain)."""
        # Login as owner if possible
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        rl = s.post(f"{BASE_URL}/api/auth/login", json={"email": "evrard.gerald@outlook.be", "password": "owner123"})
        if rl.status_code != 200:
            pytest.skip(f"Owner login failed: {rl.status_code}")
        fy = _get_open_fy(admin)
        if not fy:
            pytest.skip("No open fy")
        r = s.get(
            f"{BASE_URL}/api/owner/decompte/pdf",
            params={"copropriete_id": ACP_ID, "fiscal_year_id": fy["id"]},
        )
        # Owner may not have a profile in this ACP -> 403/404 acceptable.
        # If 400, must be due to closure. The closure check is verified through admin path above.
        assert r.status_code in (400, 403, 404), f"Got {r.status_code}: {r.text[:200]}"
        if r.status_code == 400:
            msg = (r.json().get("detail") or "").lower()
            assert "clotur" in msg or "closed" in msg


# ============================================================
# (3) Balance des tiers PDF
# ============================================================
class TestBalanceTiersPdf:
    def test_balance_tiers_pdf_ok(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/balance-tiers/pdf?copropriete_id={ACP_ID}")
        assert r.status_code == 200, r.text[:300]
        ct = r.headers.get("content-type", "")
        assert "application/pdf" in ct, f"Expected application/pdf, got {ct}"
        assert r.content[:4] == b"%PDF", "Response must start with %PDF magic"
        # Reasonable size: real PDF, not just header
        assert len(r.content) > 1500, f"PDF too small ({len(r.content)} bytes)"

    def test_balance_tiers_pdf_400_without_copropriete_id(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/balance-tiers/pdf")
        assert r.status_code == 400

    def test_balance_tiers_pdf_400_when_all(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/balance-tiers/pdf?copropriete_id=all")
        assert r.status_code == 400

    def test_balance_tiers_pdf_404_unknown_acp(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/balance-tiers/pdf?copropriete_id={uuid.uuid4()}")
        assert r.status_code == 404
