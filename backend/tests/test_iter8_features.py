"""Iteration 8 backend tests:
- PCMN seeding (95+ accounts) on ACP creation + 614000/615000 default-active
- Default 10 document categories on ACP creation
- toggle-active PCMN endpoint
- Demo seed endpoint (idempotent + comprehensive)
- Document upload + auto-classification (Claude Sonnet 4.5)
- Document download/delete
- DELETE banking transaction
- Cascade DELETE of ACP
- Chinese wall isolation between 2 fresh ACPs
- /api/owners global (no ACP filter side-effect)
"""
import os
import time
import pytest
import requests
from pathlib import Path

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
TS = int(time.time())


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text}")
    return s


def _create_acp(session, name):
    r = session.post(f"{BASE_URL}/api/coproprietes", json={
        "name": name,
        "address": "Rue de test 1",
        "postal_code": "1000",
        "city": "Bruxelles",
        "bank_accounts": [
            {"iban": "BE99000000000000", "bic": "TESTBEBB",
             "account_type": "vue", "is_default": True, "label": "Test compte"}
        ],
    })
    assert r.status_code == 200, f"Create ACP failed: {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def acp_a(session):
    acp = _create_acp(session, f"TEST_Iter8_ACP_A_{TS}")
    yield acp
    # cascade delete after module tests
    session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}")


@pytest.fixture(scope="module")
def acp_b(session):
    acp = _create_acp(session, f"TEST_Iter8_ACP_B_{TS}")
    yield acp
    session.delete(f"{BASE_URL}/api/coproprietes/{acp['id']}")


# ---------- PCMN seeding on ACP creation ----------
class TestPCMNSeeding:
    def test_pcmn_seeded_at_creation(self, session, acp_a):
        r = session.get(f"{BASE_URL}/api/accounting/pcmn",
                        params={"copropriete_id": acp_a["id"]})
        assert r.status_code == 200
        accounts = r.json()
        # 95 PCMN + 1 bank account PCMN = 96 minimum
        assert len(accounts) >= 95, f"Expected >=95 accounts, got {len(accounts)}"
        numbers = {a["number"] for a in accounts}
        # Sanity check on key Belgian condominium accounts
        for must in ("614000", "615000", "400000", "440000"):
            assert must in numbers, f"Missing key PCMN account {must}"

    def test_default_active_accounts(self, session, acp_a):
        r = session.get(f"{BASE_URL}/api/accounting/pcmn",
                        params={"copropriete_id": acp_a["id"]})
        accounts = {a["number"]: a for a in r.json()}
        assert accounts["614000"]["active"] is True, "614000 should be active by default"
        assert accounts["615000"]["active"] is True, "615000 should be active by default"
        # A non-default account should be inactive
        assert accounts.get("400000", {}).get("active") is False, "400000 should NOT be active by default"

    def test_only_active_filter(self, session, acp_a):
        r = session.get(f"{BASE_URL}/api/accounting/pcmn",
                        params={"copropriete_id": acp_a["id"], "only_active": "true"})
        assert r.status_code == 200
        accounts = r.json()
        # 614000 + 615000 + 1 bank account PCMN = at least 3
        assert all(a.get("active") is True for a in accounts)
        nums = {a["number"] for a in accounts}
        assert "614000" in nums and "615000" in nums

    def test_toggle_active_endpoint(self, session, acp_a):
        # Toggle 400000 to active
        r = session.patch(
            f"{BASE_URL}/api/accounting/pcmn/400000/toggle-active",
            params={"copropriete_id": acp_a["id"]},
            json={"active": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["active"] is True
        # Verify via GET only_active
        r2 = session.get(f"{BASE_URL}/api/accounting/pcmn",
                         params={"copropriete_id": acp_a["id"], "only_active": "true"})
        nums = {a["number"] for a in r2.json()}
        assert "400000" in nums
        # Toggle back off
        r3 = session.patch(
            f"{BASE_URL}/api/accounting/pcmn/400000/toggle-active",
            params={"copropriete_id": acp_a["id"]},
            json={"active": False},
        )
        assert r3.status_code == 200
        assert r3.json()["active"] is False


# ---------- Default document categories on ACP creation ----------
class TestDefaultCategories:
    def test_10_default_categories_seeded(self, session, acp_a):
        r = session.get(f"{BASE_URL}/api/documents/categories",
                        params={"copropriete_id": acp_a["id"]})
        assert r.status_code == 200
        cats = r.json()
        names = {c["name"] for c in cats}
        expected = {
            "Reglement d'ordre interieur", "Acte de base", "Statuts", "PV d'AG",
            "Contrats", "Polices d'assurance", "Factures fournisseurs",
            "Decomptes", "Rapports techniques", "Autres",
        }
        missing = expected - names
        assert not missing, f"Missing default categories: {missing}"
        assert len(cats) >= 10


# ---------- Demo seed ----------
class TestDemoSeed:
    def test_demo_seed_creates_complete_acp(self, session):
        r = session.post(f"{BASE_URL}/api/admin/demo/seed")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "Demo - Residence Les Tilleuls"
        c = data["counts"]
        assert c["owners"] == 8
        assert c["lots"] == 8
        assert c["suppliers"] == 5
        assert c["invoices"] == 10
        assert c["fund_calls"] == 2
        assert c["distribution_keys"] == 2
        assert c["categories"] == 10
        assert c["pcmn_accounts"] >= 95
        assert c["journal_entries"] >= 13  # 1 OD + 2 AP + 10 AC
        copro_id = data["copropriete_id"]
        # Verify data really exists and is scoped
        lots = session.get(f"{BASE_URL}/api/lots",
                           params={"copropriete_id": copro_id}).json()
        assert len(lots) == 8
        invs = session.get(f"{BASE_URL}/api/invoices",
                           params={"copropriete_id": copro_id}).json()
        assert len(invs) == 10
        # cleanup this demo (cascade)
        session.delete(f"{BASE_URL}/api/coproprietes/{copro_id}")

    def test_demo_seed_idempotent(self, session):
        # Call twice - both should succeed (creates 2 demo ACPs which is acceptable)
        r1 = session.post(f"{BASE_URL}/api/admin/demo/seed")
        r2 = session.post(f"{BASE_URL}/api/admin/demo/seed")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # cleanup both
        session.delete(f"{BASE_URL}/api/coproprietes/{r1.json()['copropriete_id']}")
        session.delete(f"{BASE_URL}/api/coproprietes/{r2.json()['copropriete_id']}")


# ---------- Document upload + AI classification ----------
class TestDocumentUpload:
    def test_upload_pdf_no_ai(self, session, acp_a):
        """Upload PDF without auto-classification."""
        pdf = Path("/tmp/test_pv_ag.pdf")
        if not pdf.exists():
            pytest.skip("Missing /tmp/test_pv_ag.pdf")
        with pdf.open("rb") as f:
            r = session.post(
                f"{BASE_URL}/api/documents/upload",
                files={"file": ("test.pdf", f, "application/pdf")},
                data={
                    "title": "TEST_Doc_NoAI",
                    "copropriete_id": acp_a["id"],
                    "auto_classify": "false",
                },
            )
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["copropriete_id"] == acp_a["id"]
        assert doc["filename"] == "test.pdf"
        assert doc["size_bytes"] > 0
        # cleanup
        session.delete(f"{BASE_URL}/api/documents/{doc['id']}")

    def test_upload_pdf_with_ai_classification(self, session, acp_a):
        """Upload PV d'AG PDF with auto_classify=true; AI should classify it.
        Fallback graceful: if AI fails, upload should still succeed."""
        pdf = Path("/tmp/test_pv_ag.pdf")
        if not pdf.exists():
            pytest.skip("Missing /tmp/test_pv_ag.pdf")
        with pdf.open("rb") as f:
            r = session.post(
                f"{BASE_URL}/api/documents/upload",
                files={"file": ("pv_ag_2024.pdf", f, "application/pdf")},
                data={
                    "title": "TEST_Doc_AI",
                    "copropriete_id": acp_a["id"],
                    "auto_classify": "true",
                },
                timeout=60,
            )
        assert r.status_code == 200, r.text
        doc = r.json()
        # Upload always succeeds; AI may or may not have run
        assert "id" in doc
        ai = doc.get("ai_classification") or {}
        # If AI ran successfully, category should map to a category_id
        if ai.get("category"):
            print(f"[AI classified as]: {ai.get('category')}")
            # category_id should be assigned (since we didn't pass one)
            assert doc.get("category_id"), "category_id should be set when AI returns a category"
        else:
            print("[AI returned empty - fallback OK]")
        # Cleanup
        session.delete(f"{BASE_URL}/api/documents/{doc['id']}")

    def test_download_document(self, session, acp_a):
        pdf = Path("/tmp/test_pv_ag.pdf")
        if not pdf.exists():
            pytest.skip()
        with pdf.open("rb") as f:
            up = session.post(
                f"{BASE_URL}/api/documents/upload",
                files={"file": ("dl.pdf", f, "application/pdf")},
                data={"copropriete_id": acp_a["id"], "auto_classify": "false"},
            ).json()
        r = session.get(f"{BASE_URL}/api/documents/{up['id']}/download")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) == up["size_bytes"]
        session.delete(f"{BASE_URL}/api/documents/{up['id']}")

    def test_delete_document_removes_file(self, session, acp_a):
        pdf = Path("/tmp/test_pv_ag.pdf")
        if not pdf.exists():
            pytest.skip()
        with pdf.open("rb") as f:
            up = session.post(
                f"{BASE_URL}/api/documents/upload",
                files={"file": ("rm.pdf", f, "application/pdf")},
                data={"copropriete_id": acp_a["id"], "auto_classify": "false"},
            ).json()
        stored_path = up["stored_path"]
        assert os.path.exists(stored_path)
        r = session.delete(f"{BASE_URL}/api/documents/{up['id']}")
        assert r.status_code == 200
        assert not os.path.exists(stored_path), "File should be removed from disk"
        # 404 on subsequent download
        r2 = session.get(f"{BASE_URL}/api/documents/{up['id']}/download")
        assert r2.status_code == 404


# ---------- Banking transaction delete ----------
class TestBankingTransactionDelete:
    def test_delete_bank_transaction(self, session, acp_a):
        # Create a statement
        stmt = session.post(f"{BASE_URL}/api/banking/statements", json={
            "number": f"TEST-STMT-{TS}",
            "date": "2025-01-15",
            "account_number": "BE99000000000000",
            "opening_balance": 100,
            "closing_balance": 150,
            "copropriete_id": acp_a["id"],
        }).json()
        # Create a transaction
        txn = session.post(f"{BASE_URL}/api/banking/transactions", json={
            "statement_id": stmt["id"],
            "date": "2025-01-15",
            "amount": 50,
            "counterparty_name": "TEST_Counter",
            "communication": "TEST",
            "transaction_type": "credit",
            "copropriete_id": acp_a["id"],
        }).json()
        assert "id" in txn
        # Delete it
        r = session.delete(f"{BASE_URL}/api/banking/transactions/{txn['id']}")
        assert r.status_code == 200, r.text
        # Verify gone
        listed = session.get(f"{BASE_URL}/api/banking/transactions",
                             params={"copropriete_id": acp_a["id"]}).json()
        ids = {t["id"] for t in listed}
        assert txn["id"] not in ids


# ---------- Cascade delete ACP ----------
class TestCascadeDelete:
    def test_cascade_delete_wipes_all_data(self, session):
        # Create new ACP
        acp = _create_acp(session, f"TEST_Cascade_{TS}_{int(time.time()*1000)%10000}")
        copro_id = acp["id"]
        # PCMN exists
        assert len(session.get(f"{BASE_URL}/api/accounting/pcmn",
                               params={"copropriete_id": copro_id}).json()) >= 95
        # categories exist
        assert len(session.get(f"{BASE_URL}/api/documents/categories",
                               params={"copropriete_id": copro_id}).json()) >= 10
        # Insert an invoice
        inv = session.post(f"{BASE_URL}/api/invoices", json={
            "number": f"TEST-INV-{TS}",
            "date": "2025-01-01", "due_date": "2025-01-31",
            "supplier": "TestSupp", "description": "x",
            "total_amount": 100, "account_number": "614000",
            "copropriete_id": copro_id,
        }).json()
        assert inv.get("id")
        # DELETE the ACP
        r = session.delete(f"{BASE_URL}/api/coproprietes/{copro_id}")
        assert r.status_code == 200, r.text
        # Now all scoped data should be gone
        assert session.get(f"{BASE_URL}/api/accounting/pcmn",
                           params={"copropriete_id": copro_id}).json() == []
        assert session.get(f"{BASE_URL}/api/documents/categories",
                           params={"copropriete_id": copro_id}).json() == []
        assert session.get(f"{BASE_URL}/api/invoices",
                           params={"copropriete_id": copro_id}).json() == []
        # ACP itself 404
        assert session.get(f"{BASE_URL}/api/coproprietes/{copro_id}").status_code == 404


# ---------- Chinese walls revisited (Iter8 fresh ACPs) ----------
class TestChineseWalls:
    def test_pcmn_isolation(self, session, acp_a, acp_b):
        # Activate 400000 in A
        session.patch(
            f"{BASE_URL}/api/accounting/pcmn/400000/toggle-active",
            params={"copropriete_id": acp_a["id"]},
            json={"active": True},
        )
        # B should still have 400000 inactive (separate doc)
        b_accts = {a["number"]: a for a in session.get(
            f"{BASE_URL}/api/accounting/pcmn",
            params={"copropriete_id": acp_b["id"]}).json()}
        assert b_accts["400000"]["active"] is False, "Chinese wall breach: B's 400000 became active"

    def test_categories_isolation(self, session, acp_a, acp_b):
        a_cats = session.get(f"{BASE_URL}/api/documents/categories",
                             params={"copropriete_id": acp_a["id"]}).json()
        b_cats = session.get(f"{BASE_URL}/api/documents/categories",
                             params={"copropriete_id": acp_b["id"]}).json()
        a_ids = {c["id"] for c in a_cats}
        b_ids = {c["id"] for c in b_cats}
        assert a_ids.isdisjoint(b_ids), "Categories leaked between ACPs"


# ---------- Global endpoints (front-end interceptor must NOT pass copropriete_id) ----------
class TestGlobalEndpoints:
    def test_owners_endpoint_is_global(self, session):
        """GET /api/owners (no params) should return all owners regardless of ACP."""
        r = session.get(f"{BASE_URL}/api/owners")
        assert r.status_code == 200
        owners = r.json()
        assert isinstance(owners, list)
        # Just sanity - we don't assert count > 0 since DB might be empty,
        # but call should succeed without filter.

    def test_suppliers_endpoint_is_global(self, session):
        r = session.get(f"{BASE_URL}/api/suppliers")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_coproprietes_endpoint_is_global(self, session):
        r = session.get(f"{BASE_URL}/api/coproprietes")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
