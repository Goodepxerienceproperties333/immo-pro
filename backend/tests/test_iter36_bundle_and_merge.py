"""Iter36 - P0 tests:
   1) merge_cm_optipro_owners.py result verification (no duplicates in Gaura)
   2) POST /api/invoices/bundle-analyze (PDF parsing + matching)
   3) POST /api/invoices/bundle-commit (attach/create/skip modes + validations)
"""
import os
import pytest
import requests
from pathlib import Path

def _load_backend_url():
    url = os.environ.get("REACT_APP_BACKEND_URL", "")
    if not url:
        try:
            with open("/app/frontend/.env") as f:
                for ln in f:
                    if ln.startswith("REACT_APP_BACKEND_URL="):
                        url = ln.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    return url.rstrip("/")


BASE_URL = _load_backend_url()
GAURA_ID = "b5f14232-34f5-4805-9b9e-ebd32ad8baa5"
BUNDLE_PDF = "/tmp/regroupement.pdf"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    # Login (cookie-based JWT)
    r = sess.post(f"{BASE_URL}/api/auth/login",
                  json={"email": "admin@copro.be", "password": "admin123"},
                  timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return sess


# ---------- Part 1: Merge owners verification ----------
class TestMergeOwners:
    def test_no_40000_owner_accounts_in_gaura(self, s):
        r = s.get(f"{BASE_URL}/api/owners?copropriete_id={GAURA_ID}", timeout=15)
        assert r.status_code == 200, r.text
        owners = r.json()
        assert isinstance(owners, list)
        dup = [o for o in owners if str(o.get("tier_account_code") or "").startswith("40000")]
        assert len(dup) == 0, f"Doublons restants 40000XXX: {[o.get('name') for o in dup]}"

    def test_no_40000_or_40010_pcmn_accounts_in_gaura(self, s):
        r = s.get(f"{BASE_URL}/api/accounting/pcmn?copropriete_id={GAURA_ID}", timeout=15)
        assert r.status_code == 200, r.text
        accs = r.json()
        bad = [a for a in accs if str(a.get("number") or a.get("code") or "").startswith(("40000", "40010"))]
        assert len(bad) == 0, f"Comptes PCMN restants: {[a.get('number') or a.get('code') for a in bad]}"

    def test_balance_tiers_one_line_per_owner(self, s):
        r = s.get(f"{BASE_URL}/api/reports/balance-tiers/owners?copropriete_id={GAURA_ID}", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        rows = data.get("rows") or data.get("lines") or data.get("owners") or (data if isinstance(data, list) else [])
        owner_keys = []
        for row in rows:
            key = row.get("owner_id") or row.get("id") or row.get("name") or row.get("tier_account_code")
            if key:
                owner_keys.append(key)
        dups = {k for k in owner_keys if owner_keys.count(k) > 1}
        assert len(dups) == 0, f"Balance Tiers: doublons {dups}"


# ---------- Part 2: bundle-analyze ----------
class TestBundleAnalyze:
    def test_missing_copropriete_id(self, s):
        with open(BUNDLE_PDF, "rb") as f:
            r = s.post(f"{BASE_URL}/api/invoices/bundle-analyze",
                       files={"file": ("regroupement.pdf", f, "application/pdf")},
                       timeout=120)
        # Without ACP header/form, should reject
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text[:200]}"

    def test_invalid_pdf(self, s):
        r = s.post(f"{BASE_URL}/api/invoices/bundle-analyze",
                   files={"file": ("bad.pdf", b"not a pdf at all just text",
                                   "application/pdf")},
                   data={"copropriete_id": GAURA_ID},
                   timeout=30)
        assert r.status_code in (400, 500), f"Expected 400/500, got {r.status_code}"

    def test_valid_bundle_analyze(self, s, tmp_path_factory):
        assert Path(BUNDLE_PDF).exists(), f"Bundle PDF missing: {BUNDLE_PDF}"
        with open(BUNDLE_PDF, "rb") as f:
            r = s.post(f"{BASE_URL}/api/invoices/bundle-analyze",
                       files={"file": ("regroupement.pdf", f, "application/pdf")},
                       data={"copropriete_id": GAURA_ID},
                       timeout=180)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        data = r.json()
        assert "session_id" in data and data["session_id"]
        assert data.get("total_pages") == 87, f"Got total_pages={data.get('total_pages')}"
        assert data.get("invoice_count") == 14, f"Got invoice_count={data.get('invoice_count')}"
        blocks = data.get("blocks") or []
        assert len(blocks) == 14
        for blk in blocks:
            assert "block_id" in blk
            assert isinstance(blk.get("page_range"), list) and len(blk["page_range"]) > 0
            assert "supplier_hint" in blk
            assert "date_iso" in blk
            assert "invoice_number" in blk
            assert "total_amount" in blk
            # suggested_match is optional
        # Stash session for next class via module-level
        pytest.bundle_session = data
        pytest.bundle_blocks = blocks


# ---------- Part 3: bundle-commit ----------
class TestBundleCommit:
    def test_session_not_found(self, s):
        r = s.post(f"{BASE_URL}/api/invoices/bundle-commit",
                   json={"session_id": "00000000-0000-0000-0000-000000000000",
                         "assignments": [{"block_id": "blk-0", "page_range": [1], "mode": "skip"}]},
                   timeout=15)
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text[:200]}"

    def test_commit_attach_create_skip_and_errors(self, s):
        sess_data = getattr(pytest, "bundle_session", None)
        assert sess_data, "Bundle session missing - analyze test must run first"
        blocks = pytest.bundle_blocks
        # get an existing invoice in Gaura to attach to
        rinv = s.get(f"{BASE_URL}/api/invoices?copropriete_id={GAURA_ID}", timeout=15)
        assert rinv.status_code == 200
        invoices = rinv.json()
        assert len(invoices) > 0, "No invoices in Gaura"
        target_inv_id = invoices[0]["id"]

        # Build 5 assignments to test all branches
        # blk[0] -> attach (valid)
        # blk[1] -> attach (missing invoice_id -> error)
        # blk[2] -> create (missing fields -> error)
        # blk[3] -> create (full data -> success)
        # blk[4] -> skip
        b0, b1, b2, b3, b4 = blocks[:5]
        assignments = [
            {"block_id": b0["block_id"], "page_range": b0["page_range"], "mode": "attach",
             "invoice_id": target_inv_id},
            {"block_id": b1["block_id"], "page_range": b1["page_range"], "mode": "attach"},
            {"block_id": b2["block_id"], "page_range": b2["page_range"], "mode": "create",
             "invoice_data": {"number": "", "date": "", "supplier": "", "total_amount": 100}},
            {"block_id": b3["block_id"], "page_range": b3["page_range"], "mode": "create",
             "invoice_data": {
                 "number": f"TEST-BUNDLE-{b3['block_id']}",
                 "date": "2025-01-15",
                 "supplier": "TEST_Bundle_Supplier",
                 "description": "TEST bundle creation",
                 "total_amount": 123.45,
                 "vat_amount": 21.00,
                 "copropriete_id": GAURA_ID,
                 "status": "unpaid",
             }},
            {"block_id": b4["block_id"], "page_range": b4["page_range"], "mode": "skip"},
        ]

        r = s.post(f"{BASE_URL}/api/invoices/bundle-commit",
                   json={"session_id": sess_data["session_id"], "assignments": assignments},
                   timeout=120)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
        data = r.json()
        assert data["skipped"] == 1
        assert data["attached"] >= 2, f"Expected at least 2 attached, got {data}"  # b0 attach + b3 create_and_attach
        assert data["created"] == 1, f"Expected 1 created, got {data['created']}"
        errors = data.get("errors") or []
        # b1 (attach no invoice_id) + b2 (create incomplete data)
        err_blocks = {e.get("block_id") for e in errors}
        assert b1["block_id"] in err_blocks, f"Expected error for b1; errors={errors}"
        assert b2["block_id"] in err_blocks, f"Expected error for b2; errors={errors}"

        # Verify created invoice has attachment with source='bundle'
        results = data.get("results") or []
        created_res = [x for x in results if x.get("status") == "created_and_attached"]
        assert len(created_res) == 1
        new_inv_id = created_res[0]["invoice_id"]
        ginv = s.get(f"{BASE_URL}/api/invoices/{new_inv_id}", timeout=15)
        assert ginv.status_code == 200
        inv = ginv.json()
        atts = inv.get("attachments") or []
        bundle_atts = [a for a in atts if a.get("source") == "bundle"]
        assert len(bundle_atts) >= 1, f"No bundle attachment on created invoice: {atts}"

        # Cleanup: delete the created test invoice (and its attachment file)
        s.delete(f"{BASE_URL}/api/invoices/{new_inv_id}", timeout=15)
        # Cleanup the attach made on existing invoice
        attach_res = [x for x in results if x.get("status") == "attached"]
        for ar in attach_res:
            s.delete(f"{BASE_URL}/api/invoices/{ar['invoice_id']}/attachments/{ar['attachment_id']}", timeout=15)
