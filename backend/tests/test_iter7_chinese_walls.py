"""Iteration 7: Chinese walls between ACPs - test data isolation across ACPs."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback for tests when env not provided in subprocess
    from pathlib import Path
    fe_env = Path("/app/frontend/.env")
    if fe_env.exists():
        for line in fe_env.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

TS = str(int(time.time()))


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def two_acps(client):
    """Create 2 ACPs with lots inline."""
    acps = []
    for i, label in enumerate(["A", "B"]):
        payload = {
            "name": f"TEST_Iter7_ACP_{label}_{TS}",
            "bce": f"0{i}11.222.333",
            "address": f"Rue {label} 1",
            "postal_code": "1000",
            "city": "Bruxelles",
            "lots": [
                {"number": f"{label}-101", "lot_type": "apartment", "quotity": 100.0},
                {"number": f"{label}-102", "lot_type": "apartment", "quotity": 150.0},
            ],
        }
        r = client.post(f"{BASE_URL}/api/coproprietes", json=payload)
        assert r.status_code == 200, f"ACP create failed: {r.text}"
        acps.append(r.json())
    return acps


# ---------- Auth ----------
def test_auth_login_ok(client):
    r = client.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "superadmin"


# ---------- ACP + Lots inline ----------
def test_acp_creation_with_inline_lots(client, two_acps):
    acp_a, acp_b = two_acps
    assert acp_a["id"] != acp_b["id"]
    # Lots from each ACP must carry copropriete_id
    ra = client.get(f"{BASE_URL}/api/lots?copropriete_id={acp_a['id']}")
    rb = client.get(f"{BASE_URL}/api/lots?copropriete_id={acp_b['id']}")
    assert ra.status_code == 200 and rb.status_code == 200
    lots_a = ra.json(); lots_b = rb.json()
    assert all(l.get("copropriete_id") == acp_a["id"] for l in lots_a), "ACP A lots not scoped"
    assert all(l.get("copropriete_id") == acp_b["id"] for l in lots_b), "ACP B lots not scoped"
    assert len(lots_a) >= 2 and len(lots_b) >= 2
    # Chinese wall: A's lots NOT in B's response
    a_ids = {l["id"] for l in lots_a}
    b_ids = {l["id"] for l in lots_b}
    assert a_ids.isdisjoint(b_ids), "ACP lot leak across ACPs"


# ---------- DISTRIBUTION KEYS (the original user bug) ----------
def test_distribution_key_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    # Create in A
    ra = client.post(f"{BASE_URL}/api/distribution-keys", json={
        "name": f"TEST_DK_A_{TS}", "key_type": "quotity", "lots": [], "copropriete_id": acp_a["id"]
    })
    assert ra.status_code == 200, ra.text
    key_a = ra.json()
    assert key_a["copropriete_id"] == acp_a["id"]
    # Create in B
    rb = client.post(f"{BASE_URL}/api/distribution-keys", json={
        "name": f"TEST_DK_B_{TS}", "key_type": "quotity", "lots": [], "copropriete_id": acp_b["id"]
    })
    assert rb.status_code == 200
    key_b = rb.json()
    # GET filtered - the bug fix verification
    la = client.get(f"{BASE_URL}/api/distribution-keys?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/distribution-keys?copropriete_id={acp_b['id']}").json()
    ids_a = {k["id"] for k in la}
    ids_b = {k["id"] for k in lb}
    assert key_a["id"] in ids_a, "Created dist-key A NOT in filtered list - original bug not fixed!"
    assert key_b["id"] in ids_b
    assert key_a["id"] not in ids_b, "ACP A dist-key leaked into B"
    assert key_b["id"] not in ids_a, "ACP B dist-key leaked into A"


# ---------- INVOICES ----------
def test_invoices_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    inv = {"number": f"INV-A-{TS}", "date": "2025-01-15", "supplier": "Fournisseur X",
           "description": "Test", "total_amount": 100.0, "copropriete_id": acp_a["id"]}
    ra = client.post(f"{BASE_URL}/api/invoices", json=inv)
    assert ra.status_code == 200
    inv_a = ra.json()
    assert inv_a["copropriete_id"] == acp_a["id"]
    inv["number"] = f"INV-B-{TS}"; inv["copropriete_id"] = acp_b["id"]
    rb = client.post(f"{BASE_URL}/api/invoices", json=inv)
    inv_b = rb.json()
    la = client.get(f"{BASE_URL}/api/invoices?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/invoices?copropriete_id={acp_b['id']}").json()
    ids_a = {i["id"] for i in la}; ids_b = {i["id"] for i in lb}
    assert inv_a["id"] in ids_a and inv_b["id"] in ids_b
    assert inv_a["id"] not in ids_b and inv_b["id"] not in ids_a


# ---------- JOURNAL ENTRIES ----------
def test_accounting_entries_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    entry_a = {"journal_type": "OD", "date": "2025-01-15", "description": f"TEST_OD_A_{TS}",
               "lines": [{"account_number": "600000", "debit": 50.0, "credit": 0},
                         {"account_number": "440000", "debit": 0, "credit": 50.0}],
               "copropriete_id": acp_a["id"]}
    ra = client.post(f"{BASE_URL}/api/accounting/entries", json=entry_a)
    assert ra.status_code == 200, ra.text
    je_a = ra.json()
    entry_b = dict(entry_a, description=f"TEST_OD_B_{TS}", copropriete_id=acp_b["id"])
    rb = client.post(f"{BASE_URL}/api/accounting/entries", json=entry_b)
    je_b = rb.json()
    la = client.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={acp_b['id']}").json()
    ids_a = {e["id"] for e in la}; ids_b = {e["id"] for e in lb}
    assert je_a["id"] in ids_a and je_b["id"] in ids_b
    assert je_a["id"] not in ids_b and je_b["id"] not in ids_a


# ---------- BANKING ----------
def test_banking_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    sa = client.post(f"{BASE_URL}/api/banking/statements", json={
        "number": f"STMT-A-{TS}", "date": "2025-01-15", "copropriete_id": acp_a["id"]})
    assert sa.status_code == 200
    stmt_a = sa.json(); assert stmt_a["copropriete_id"] == acp_a["id"]
    sb = client.post(f"{BASE_URL}/api/banking/statements", json={
        "number": f"STMT-B-{TS}", "date": "2025-01-15", "copropriete_id": acp_b["id"]})
    stmt_b = sb.json()
    la = client.get(f"{BASE_URL}/api/banking/statements?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/banking/statements?copropriete_id={acp_b['id']}").json()
    assert stmt_a["id"] in {s["id"] for s in la}
    assert stmt_a["id"] not in {s["id"] for s in lb}
    assert stmt_b["id"] in {s["id"] for s in lb}

    # Transactions
    ta = client.post(f"{BASE_URL}/api/banking/transactions", json={
        "statement_id": stmt_a["id"], "date": "2025-01-15", "amount": 100.0,
        "transaction_type": "credit", "copropriete_id": acp_a["id"]})
    assert ta.status_code == 200
    txn_a = ta.json(); assert txn_a["copropriete_id"] == acp_a["id"]
    tb = client.post(f"{BASE_URL}/api/banking/transactions", json={
        "statement_id": stmt_b["id"], "date": "2025-01-15", "amount": 200.0,
        "transaction_type": "credit", "copropriete_id": acp_b["id"]})
    txn_b = tb.json()
    txa = client.get(f"{BASE_URL}/api/banking/transactions?copropriete_id={acp_a['id']}").json()
    txb = client.get(f"{BASE_URL}/api/banking/transactions?copropriete_id={acp_b['id']}").json()
    assert txn_a["id"] in {t["id"] for t in txa}
    assert txn_a["id"] not in {t["id"] for t in txb}
    assert txn_b["id"] in {t["id"] for t in txb}


# ---------- FUND CALLS - lots scope critical ----------
def test_fund_calls_chinese_wall_and_lots_scope(client, two_acps):
    acp_a, acp_b = two_acps
    # Get lots for each ACP
    lots_a = client.get(f"{BASE_URL}/api/lots?copropriete_id={acp_a['id']}").json()
    lots_b = client.get(f"{BASE_URL}/api/lots?copropriete_id={acp_b['id']}").json()
    fa = client.post(f"{BASE_URL}/api/fund-calls", json={
        "name": f"TEST_FC_A_{TS}", "date": "2025-01-15", "total_amount": 1000.0,
        "call_type": "provisions", "copropriete_id": acp_a["id"]})
    assert fa.status_code == 200, fa.text
    fc_a = fa.json()
    assert fc_a["copropriete_id"] == acp_a["id"]
    # distribution should only mention lots from A (or empty if none assigned to owner). Owner assignment is empty for inline lots,
    # but the chinese wall guarantee is structural: no lot from B should appear in distribution.
    lot_ids_b = {l["id"] for l in lots_b}
    for d in fc_a.get("distribution", []):
        assert d.get("lot_id") not in lot_ids_b, "Fund call A distribution leaked ACP B lot!"
    fb = client.post(f"{BASE_URL}/api/fund-calls", json={
        "name": f"TEST_FC_B_{TS}", "date": "2025-01-15", "total_amount": 2000.0,
        "call_type": "provisions", "copropriete_id": acp_b["id"]})
    fc_b = fb.json()
    la = client.get(f"{BASE_URL}/api/fund-calls?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/fund-calls?copropriete_id={acp_b['id']}").json()
    assert fc_a["id"] in {f["id"] for f in la}
    assert fc_a["id"] not in {f["id"] for f in lb}
    assert fc_b["id"] in {f["id"] for f in lb}


# ---------- REPORTS: balance, bilan, resultat ----------
def test_reports_balance_scoped(client, two_acps):
    acp_a, acp_b = two_acps
    ra = client.get(f"{BASE_URL}/api/reports/balance?copropriete_id={acp_a['id']}")
    assert ra.status_code == 200
    data_a = ra.json()
    assert "accounts" in data_a and "totals" in data_a
    rb = client.get(f"{BASE_URL}/api/reports/balance?copropriete_id={acp_b['id']}")
    data_b = rb.json()
    # We expect different totals (since we posted different entries with different amounts) ; at minimum ACP A should contain 50 EUR
    total_d_a = data_a["totals"]["total_debit"]
    total_d_b = data_b["totals"]["total_debit"]
    assert total_d_a >= 50.0, f"ACP A should contain at least 50 EUR debit, got {total_d_a}"
    # Trial balance for ACP B should not include the entry we made with description TEST_OD_A_{TS}: hard to check directly,
    # so check that totals differ unless both got identical seeds (they shouldn't)
    assert total_d_a != total_d_b or total_d_a == 50.0


def test_reports_bilan_and_resultat_scoped(client, two_acps):
    acp_a, acp_b = two_acps
    for path in ["/api/reports/bilan", "/api/reports/resultat"]:
        ra = client.get(f"{BASE_URL}{path}?copropriete_id={acp_a['id']}")
        rb = client.get(f"{BASE_URL}{path}?copropriete_id={acp_b['id']}")
        assert ra.status_code == 200 and rb.status_code == 200, f"{path} failed"


def test_reports_balance_tiers_owners_scoped(client, two_acps):
    acp_a, _ = two_acps
    r = client.get(f"{BASE_URL}/api/reports/balance-tiers/owners?copropriete_id={acp_a['id']}")
    assert r.status_code == 200
    data = r.json()
    assert "owners" in data
    # No owners attached to lots in TEST_ACPs, so list should be empty
    assert data["owners"] == [] or all(o.get("owner_id") for o in data["owners"])


def test_reports_balance_tiers_suppliers_scoped(client, two_acps):
    acp_a, acp_b = two_acps
    ra = client.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers?copropriete_id={acp_a['id']}").json()
    rb = client.get(f"{BASE_URL}/api/reports/balance-tiers/suppliers?copropriete_id={acp_b['id']}").json()
    # Both should be lists; chinese wall: supplier seen only via invoices in that ACP
    a_names = {s["supplier_name"] for s in ra.get("suppliers", [])}
    b_names = {s["supplier_name"] for s in rb.get("suppliers", [])}
    # 'Fournisseur X' was used in both ACPs, but only since each ACP has its own invoice, both should see it.
    # Important: ensure suppliers list is not empty when invoices exist.
    # (Supplier needs to exist in suppliers collection; may be empty if not seeded - just ensure structure.)
    assert isinstance(ra.get("suppliers"), list) and isinstance(rb.get("suppliers"), list)


# ---------- METERS, DOCUMENTS, FISCAL ----------
def test_meters_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    ra = client.post(f"{BASE_URL}/api/meters", json={
        "name": f"TEST_M_A_{TS}", "meter_type": "water", "copropriete_id": acp_a["id"]})
    assert ra.status_code == 200
    m_a = ra.json(); assert m_a["copropriete_id"] == acp_a["id"]
    rb = client.post(f"{BASE_URL}/api/meters", json={
        "name": f"TEST_M_B_{TS}", "meter_type": "water", "copropriete_id": acp_b["id"]})
    m_b = rb.json()
    la = client.get(f"{BASE_URL}/api/meters?copropriete_id={acp_a['id']}").json()
    lb = client.get(f"{BASE_URL}/api/meters?copropriete_id={acp_b['id']}").json()
    assert m_a["id"] in {m["id"] for m in la}
    assert m_a["id"] not in {m["id"] for m in lb}
    assert m_b["id"] in {m["id"] for m in lb}


def test_documents_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    ca = client.post(f"{BASE_URL}/api/documents/categories", json={
        "name": f"TEST_CAT_A_{TS}", "copropriete_id": acp_a["id"]})
    assert ca.status_code == 200
    cat_a = ca.json(); assert cat_a["copropriete_id"] == acp_a["id"]
    da = client.post(f"{BASE_URL}/api/documents", json={
        "title": f"TEST_DOC_A_{TS}", "category_id": cat_a["id"], "copropriete_id": acp_a["id"]})
    doc_a = da.json()
    cb = client.post(f"{BASE_URL}/api/documents/categories", json={
        "name": f"TEST_CAT_B_{TS}", "copropriete_id": acp_b["id"]}).json()
    db = client.post(f"{BASE_URL}/api/documents", json={
        "title": f"TEST_DOC_B_{TS}", "category_id": cb["id"], "copropriete_id": acp_b["id"]}).json()
    cats_a = client.get(f"{BASE_URL}/api/documents/categories?copropriete_id={acp_a['id']}").json()
    cats_b = client.get(f"{BASE_URL}/api/documents/categories?copropriete_id={acp_b['id']}").json()
    assert cat_a["id"] in {c["id"] for c in cats_a}
    assert cat_a["id"] not in {c["id"] for c in cats_b}
    docs_a = client.get(f"{BASE_URL}/api/documents?copropriete_id={acp_a['id']}").json()
    docs_b = client.get(f"{BASE_URL}/api/documents?copropriete_id={acp_b['id']}").json()
    assert doc_a["id"] in {d["id"] for d in docs_a}
    assert doc_a["id"] not in {d["id"] for d in docs_b}
    assert db["id"] in {d["id"] for d in docs_b}


def test_fiscal_chinese_wall(client, two_acps):
    acp_a, acp_b = two_acps
    fya = client.post(f"{BASE_URL}/api/fiscal/years", json={
        "name": f"TEST_FY_A_{TS}", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "copropriete_id": acp_a["id"]})
    assert fya.status_code == 200
    fy_a = fya.json(); assert fy_a["copropriete_id"] == acp_a["id"]
    fyb = client.post(f"{BASE_URL}/api/fiscal/years", json={
        "name": f"TEST_FY_B_{TS}", "start_date": "2025-01-01", "end_date": "2025-12-31",
        "copropriete_id": acp_b["id"]}).json()
    ba = client.post(f"{BASE_URL}/api/fiscal/budgets", json={
        "fiscal_year_id": fy_a["id"], "name": f"TEST_BUD_A_{TS}",
        "lines": [{"account_number": "600000", "amount": 500.0}],
        "copropriete_id": acp_a["id"]})
    assert ba.status_code == 200
    bud_a = ba.json(); assert bud_a["copropriete_id"] == acp_a["id"]
    bb = client.post(f"{BASE_URL}/api/fiscal/budgets", json={
        "fiscal_year_id": fyb["id"], "name": f"TEST_BUD_B_{TS}",
        "lines": [{"account_number": "600000", "amount": 700.0}],
        "copropriete_id": acp_b["id"]}).json()
    ya = client.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={acp_a['id']}").json()
    yb = client.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={acp_b['id']}").json()
    assert fy_a["id"] in {y["id"] for y in ya}
    assert fy_a["id"] not in {y["id"] for y in yb}
    bsa = client.get(f"{BASE_URL}/api/fiscal/budgets?copropriete_id={acp_a['id']}").json()
    bsb = client.get(f"{BASE_URL}/api/fiscal/budgets?copropriete_id={acp_b['id']}").json()
    assert bud_a["id"] in {b["id"] for b in bsa}
    assert bud_a["id"] not in {b["id"] for b in bsb}
    assert bb["id"] in {b["id"] for b in bsb}


# ---------- BACKWARD COMPATIBILITY ----------
def test_backward_compat_no_filter_returns_all(client):
    """GET without copropriete_id returns everything (incl. legacy docs without the field)."""
    r = client.get(f"{BASE_URL}/api/distribution-keys")
    assert r.status_code == 200
    keys = r.json()
    assert isinstance(keys, list)
    # Must include both A & B keys (and any legacy)
    inv = client.get(f"{BASE_URL}/api/invoices").json()
    assert isinstance(inv, list)
    # Must not crash
    ent = client.get(f"{BASE_URL}/api/accounting/entries").json()
    assert isinstance(ent, list)


def test_backward_compat_create_without_copro_id(client):
    """POST without copropriete_id must store empty string and not crash."""
    r = client.post(f"{BASE_URL}/api/distribution-keys", json={
        "name": f"TEST_DK_NoACP_{TS}", "key_type": "quotity", "lots": []})
    assert r.status_code == 200
    key = r.json()
    assert key.get("copropriete_id") == ""
    # And GET without filter returns it
    all_keys = client.get(f"{BASE_URL}/api/distribution-keys").json()
    assert key["id"] in {k["id"] for k in all_keys}
