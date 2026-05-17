"""Iter12: Bilan/Resultat rubriques, Excel exports, Reminders, Invoice AI extract, Attachments on entries/invoices."""
import os
import io
import pytest
import requests
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _load_backend_url():
    url = os.environ.get('REACT_APP_BACKEND_URL')
    if url:
        return url.rstrip('/')
    envp = Path('/app/frontend/.env')
    if envp.exists():
        for line in envp.read_text().splitlines():
            if line.startswith('REACT_APP_BACKEND_URL='):
                return line.split('=', 1)[1].strip().rstrip('/')
    raise RuntimeError("REACT_APP_BACKEND_URL not set")


BASE_URL = _load_backend_url()
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# -------- Fixtures --------
@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Login admin failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def owner_session(admin_session):
    """Create an owner-role user and return its session, or skip."""
    email = "TEST_iter12_owner@example.com"
    pwd = "ownerpass123"
    # Ensure user exists
    payload = {"email": email, "name": "Test Iter12 Owner", "password": pwd, "role": "owner"}
    r = admin_session.post(f"{BASE_URL}/api/admin/users", json=payload)
    user_id = None
    if r.status_code in (200, 201):
        user_id = r.json().get("id")
    elif r.status_code == 400 and "existe" in (r.text or "").lower():
        # already exists - fetch id
        u = admin_session.get(f"{BASE_URL}/api/admin/users")
        if u.status_code == 200:
            for x in u.json():
                if x.get("email") == email:
                    user_id = x.get("id")
                    break
    else:
        pytest.skip(f"Cannot create owner user: {r.status_code} {r.text}")

    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    lr = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pwd})
    if lr.status_code != 200:
        pytest.skip(f"Owner login failed: {lr.text}")
    yield s
    # Cleanup
    try:
        if user_id:
            admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}")
    except Exception:
        pass


@pytest.fixture(scope="module")
def first_copro_id(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/coproprietes")
    assert r.status_code == 200
    coprs = r.json()
    if not coprs:
        pytest.skip("No coproprietes available")
    return coprs[0]["id"]


def _make_pdf_bytes():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(100, 800, "TEST PDF iter12")
    c.drawString(100, 770, "Facture N° 2024-001")
    c.drawString(100, 740, "Total TTC: 250.00 EUR")
    c.save()
    buf.seek(0)
    return buf.getvalue()


def _make_png_bytes():
    # tiny 1x1 PNG
    return bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4"
        "890000000A49444154789C6300010000000500010D0A2DB40000000049454E44"
        "AE426082"
    )


# -------- Bilan / Resultat rubriques --------
class TestReportsRubriques:
    def test_bilan_rubriques_structure(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reports/bilan")
        assert r.status_code == 200, r.text
        data = r.json()
        # Must have rubrique-based structure
        for key in ("actif", "passif", "total_actif", "total_passif", "equilibre", "ecart"):
            assert key in data, f"Missing key {key} in bilan: {list(data.keys())}"
        assert isinstance(data["actif"], list)
        assert isinstance(data["passif"], list)
        # Each rubrique should have label/total/accounts
        for rub in data["actif"] + data["passif"]:
            assert "label" in rub
            assert "total" in rub
            assert "accounts" in rub
            assert isinstance(rub["accounts"], list)
        assert isinstance(data["equilibre"], bool)

    def test_resultat_rubriques_structure(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reports/resultat")
        assert r.status_code == 200, r.text
        data = r.json()
        for key in ("charges", "produits", "total_charges", "total_produits", "resultat", "resultat_label"):
            assert key in data, f"Missing key {key} in resultat"
        assert isinstance(data["charges"], list)
        assert isinstance(data["produits"], list)
        for rub in data["charges"] + data["produits"]:
            assert "label" in rub and "total" in rub and "accounts" in rub


# -------- Excel exports --------
class TestExcelExports:
    def test_export_balance_tiers_owners(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/exports/balance-tiers/owners.xlsx")
        assert r.status_code == 200, r.text
        assert XLSX_MIME in r.headers.get("content-type", "")
        assert len(r.content) > 100
        # XLSX is a zip
        assert r.content[:2] == b"PK"

    def test_export_bilan_xlsx(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/exports/bilan.xlsx")
        assert r.status_code == 200, r.text
        assert XLSX_MIME in r.headers.get("content-type", "")
        assert r.content[:2] == b"PK"

    def test_export_grand_livre_xlsx(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/exports/grand-livre.xlsx")
        assert r.status_code == 200, r.text
        assert XLSX_MIME in r.headers.get("content-type", "")
        assert r.content[:2] == b"PK"


# -------- Reminders / Late payments --------
class TestReminders:
    def test_late_payments_structure(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reminders/late-payments?grace_days=0")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "late_payments" in data and "summary" in data
        s = data["summary"]
        for k in ("total_count", "total_amount", "by_severity"):
            assert k in s
        for sev in ("critique", "urgent", "rappel2", "rappel1"):
            assert sev in s["by_severity"]
        # Order desc days_late
        if len(data["late_payments"]) >= 2:
            for a, b in zip(data["late_payments"], data["late_payments"][1:]):
                assert a["days_late"] >= b["days_late"]

    def test_late_payments_count_positive(self, admin_session):
        """Per request, DB should contain 8 late calls."""
        r = admin_session.get(f"{BASE_URL}/api/reminders/late-payments?grace_days=0")
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["total_count"] >= 1, "Expected at least 1 late payment in seeded DB"

    def test_reminder_letter_pdf(self, admin_session):
        # Find a late owner+copro pair
        r = admin_session.get(f"{BASE_URL}/api/reminders/late-payments?grace_days=0")
        assert r.status_code == 200
        items = r.json().get("late_payments") or []
        if not items:
            pytest.skip("No late payments to build letter")
        owner_id = items[0]["owner_id"]
        copro_id = items[0]["copropriete_id"]
        r2 = admin_session.get(
            f"{BASE_URL}/api/reminders/owner/{owner_id}/letter",
            params={"copropriete_id": copro_id},
        )
        assert r2.status_code == 200, r2.text
        assert r2.headers.get("content-type", "").startswith("application/pdf")
        assert r2.content[:4] == b"%PDF"


# -------- Invoice AI extract --------
class TestInvoiceAIExtract:
    def test_invoice_ai_extract_pdf(self, admin_session):
        pdf_bytes = _make_pdf_bytes()
        # Don't use the session's JSON header for multipart
        sess = requests.Session()
        sess.cookies.update(admin_session.cookies)
        files = {"file": ("test.pdf", pdf_bytes, "application/pdf")}
        r = sess.post(f"{BASE_URL}/api/invoices-ai/extract", files=files)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "extracted" in data
        assert "filename" in data
        assert "stored_temp_path" in data
        # supplier_match field present (can be None)
        assert "supplier_match" in data


# -------- Attachments on journal entries --------
class TestEntryAttachments:
    def test_entry_attachment_lifecycle(self, admin_session, first_copro_id):
        # Create a manual journal entry (OD)
        payload = {
            "date": "2024-01-15",
            "journal_type": "OD",
            "reference": "TEST-ITER12-OD",
            "description": "Test OD iter12",
            "copropriete_id": first_copro_id,
            "lines": [
                {"account_number": "6110000", "account_name": "Test", "debit": 100.0, "credit": 0.0},
                {"account_number": "4400000", "account_name": "Test", "debit": 0.0, "credit": 100.0},
            ],
        }
        r = admin_session.post(f"{BASE_URL}/api/accounting/entries", json=payload)
        assert r.status_code in (200, 201), r.text
        entry = r.json()
        entry_id = entry["id"]

        try:
            # Upload PDF attachment
            sess = requests.Session()
            sess.cookies.update(admin_session.cookies)
            files = {"file": ("piece.pdf", _make_pdf_bytes(), "application/pdf")}
            u = sess.post(f"{BASE_URL}/api/accounting/entries/{entry_id}/attachments", files=files)
            assert u.status_code == 200, u.text
            att = u.json()
            assert att["id"]
            att_id = att["id"]
            assert att["filename"] == "piece.pdf"

            # Verify it appears in entry.attachments[]
            g = admin_session.get(f"{BASE_URL}/api/accounting/entries")
            assert g.status_code == 200
            found = next((e for e in g.json() if e["id"] == entry_id), None)
            assert found is not None
            assert any(a["id"] == att_id for a in (found.get("attachments") or []))

            # Download
            d = sess.get(f"{BASE_URL}/api/accounting/entries/{entry_id}/attachments/{att_id}/download")
            assert d.status_code == 200
            assert d.content[:4] == b"%PDF"

            # Wrong format -> 400
            files_bad = {"file": ("malware.exe", b"MZbad", "application/octet-stream")}
            bad = sess.post(f"{BASE_URL}/api/accounting/entries/{entry_id}/attachments", files=files_bad)
            assert bad.status_code == 400

            # PNG also accepted
            files_png = {"file": ("img.png", _make_png_bytes(), "image/png")}
            pr = sess.post(f"{BASE_URL}/api/accounting/entries/{entry_id}/attachments", files=files_png)
            assert pr.status_code == 200, pr.text

            # Delete first attachment
            de = sess.delete(f"{BASE_URL}/api/accounting/entries/{entry_id}/attachments/{att_id}")
            assert de.status_code == 200
        finally:
            # Delete entry (should also cleanup remaining attachments)
            admin_session.delete(f"{BASE_URL}/api/accounting/entries/{entry_id}")


# -------- Attachments on invoices --------
class TestInvoiceAttachments:
    def test_invoice_attachment_lifecycle(self, admin_session, first_copro_id):
        # Need a supplier
        sups = admin_session.get(f"{BASE_URL}/api/suppliers")
        assert sups.status_code == 200
        suppliers = sups.json()
        if not suppliers:
            pytest.skip("No suppliers in DB to create invoice")
        sup_id = suppliers[0]["id"]

        sup_name = suppliers[0].get("name", "Supplier")

        payload = {
            "supplier_id": sup_id,
            "supplier": sup_name,
            "copropriete_id": first_copro_id,
            "number": "TEST-ITER12-INV",
            "invoice_number": "TEST-ITER12-INV",
            "date": "2024-01-15",
            "invoice_date": "2024-01-15",
            "due_date": "2024-02-15",
            "amount_ht": 100.0,
            "vat_amount": 21.0,
            "amount_ttc": 121.0,
            "total_amount": 121.0,
            "description": "Test invoice iter12",
        }
        r = admin_session.post(f"{BASE_URL}/api/invoices", json=payload)
        assert r.status_code in (200, 201), r.text
        inv = r.json()
        inv_id = inv["id"]

        try:
            sess = requests.Session()
            sess.cookies.update(admin_session.cookies)
            files = {"file": ("invoice.pdf", _make_pdf_bytes(), "application/pdf")}
            u = sess.post(f"{BASE_URL}/api/invoices/{inv_id}/attachments", files=files)
            assert u.status_code == 200, u.text
            att = u.json()
            att_id = att["id"]

            # Verify in invoice.attachments
            g = admin_session.get(f"{BASE_URL}/api/invoices")
            assert g.status_code == 200
            found = next((i for i in g.json() if i["id"] == inv_id), None)
            assert found is not None
            assert any(a["id"] == att_id for a in (found.get("attachments") or []))

            # Download
            d = sess.get(f"{BASE_URL}/api/invoices/{inv_id}/attachments/{att_id}/download")
            assert d.status_code == 200
            assert d.content[:4] == b"%PDF"

            # Bad format
            bad = sess.post(f"{BASE_URL}/api/invoices/{inv_id}/attachments",
                            files={"file": ("note.txt", b"hello", "text/plain")})
            assert bad.status_code == 400

            # Delete attachment
            de = sess.delete(f"{BASE_URL}/api/invoices/{inv_id}/attachments/{att_id}")
            assert de.status_code == 200
        finally:
            admin_session.delete(f"{BASE_URL}/api/invoices/{inv_id}")


# -------- RBAC for owner --------
class TestOwnerRBAC:
    def test_owner_forbidden_reminders(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/reminders/late-payments")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text[:200]}"

    def test_owner_forbidden_exports(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/exports/balance-tiers/owners.xlsx")
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"

    def test_owner_forbidden_invoice_ai(self, owner_session):
        files = {"file": ("test.pdf", _make_pdf_bytes(), "application/pdf")}
        sess = requests.Session()
        sess.cookies.update(owner_session.cookies)
        r = sess.post(f"{BASE_URL}/api/invoices-ai/extract", files=files)
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"


# -------- Regression: existing endpoints --------
class TestRegression:
    def test_login(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": "admin@copro.be", "password": "admin123"})
        assert r.status_code == 200

    def test_coproprietes(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/coproprietes")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_owners(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/owners")
        assert r.status_code == 200

    def test_invoices(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/invoices")
        assert r.status_code == 200

    def test_accounting_entries(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/accounting/entries")
        assert r.status_code == 200

    def test_balance(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reports/balance")
        assert r.status_code == 200
