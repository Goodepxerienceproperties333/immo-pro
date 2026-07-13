"""Iter17 backend tests:
- Expense categories CRUD (1:1 strict on PCMN class 6, scoped by ACP)
- Invoice POST/PUT account_number override via expense_category_id
- Distribution-keys usage endpoint + PUT force=true detach + DELETE 400 when used
- Accounting entries: PUT auto -> manually_edited=true, DELETE policy on auto + manually_edited
- _delete_auto_entries skip manually_edited (regen of source preserves edited entries)
- Regression: invoice without expense_category_id uses raw account_number
- RBAC: owner -> 403 on expense-categories CRUD and /distribution-keys/{id}/usage
"""
import os
import uuid
import requests
import pytest

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE}/api"
ADMIN = {"email": "admin@copro.be", "password": "admin123"}
TAG = f"TEST_iter17_{uuid.uuid4().hex[:6]}"


# ---------------- fixtures ----------------
@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def copro_id(admin_client):
    r = admin_client.get(f"{API}/coproprietes", timeout=20)
    assert r.status_code == 200
    arr = r.json()
    assert arr, "no copropriete"
    return arr[0]["id"]


@pytest.fixture(scope="module")
def pcmn_class6(admin_client, copro_id):
    """Find at least 3 unused PCMN class-6 accounts for this ACP."""
    r = admin_client.get(f"{API}/accounting/pcmn", params={"copropriete_id": copro_id, "class_num": 6}, timeout=20)
    assert r.status_code == 200
    accs = r.json()
    # Filter out accounts already linked to an expense_category
    used = admin_client.get(f"{API}/expense-categories", params={"copropriete_id": copro_id}, timeout=20)
    used_nums = {c.get("account_number") for c in (used.json() if used.status_code == 200 else [])}
    free = [a for a in accs if a.get("number") not in used_nums]
    assert len(free) >= 3, f"need >=3 free class-6 PCMN, found {len(free)}"
    return free


@pytest.fixture(scope="module")
def pcmn_class7(admin_client, copro_id):
    r = admin_client.get(f"{API}/accounting/pcmn", params={"copropriete_id": copro_id, "class_num": 7}, timeout=20)
    assert r.status_code == 200
    arr = r.json()
    if not arr:
        pytest.skip("No class-7 PCMN account in this ACP")
    return arr[0]


@pytest.fixture(scope="module")
def supplier_name(admin_client, copro_id):
    r = admin_client.get(f"{API}/suppliers", params={"copropriete_id": copro_id}, timeout=20)
    assert r.status_code == 200
    arr = r.json()
    assert arr, "no suppliers"
    return arr[0]["name"]


@pytest.fixture(scope="module")
def created_ids():
    return {"categories": [], "invoices": [], "dist_keys": []}


@pytest.fixture(scope="module", autouse=True)
def _cleanup(admin_client, created_ids):
    yield
    for iid in created_ids["invoices"]:
        try:
            admin_client.delete(f"{API}/invoices/{iid}", timeout=15)
        except Exception:
            pass
    for kid in created_ids["dist_keys"]:
        try:
            admin_client.delete(f"{API}/distribution-keys/{kid}", timeout=15)
        except Exception:
            pass
    for cid in created_ids["categories"]:
        try:
            admin_client.delete(f"{API}/expense-categories/{cid}", timeout=15)
        except Exception:
            pass


# ---------------- Expense Categories CRUD ----------------
class TestExpenseCategoriesCRUD:
    def test_create_and_list(self, admin_client, copro_id, pcmn_class6, created_ids):
        acc = pcmn_class6[0]
        payload = {
            "name": f"{TAG}_cat1",
            "account_number": acc["number"],
            "description": "ascenseur",
            "copropriete_id": copro_id,
        }
        r = admin_client.post(f"{API}/expense-categories", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == payload["name"]
        assert data["account_number"] == acc["number"]
        assert "id" in data
        created_ids["categories"].append(data["id"])

        # GET list -> account_name + invoice_count/total enriched
        lr = admin_client.get(f"{API}/expense-categories", params={"copropriete_id": copro_id}, timeout=15)
        assert lr.status_code == 200
        items = lr.json()
        mine = next((c for c in items if c["id"] == data["id"]), None)
        assert mine, "created category not in list"
        assert mine.get("account_name") == acc["name"]
        assert mine.get("invoice_count") == 0
        assert mine.get("invoice_total") == 0

    def test_multiple_categories_allowed_same_account(self, admin_client, copro_id, pcmn_class6, created_ids):
        """iter90ex : plusieurs natures peuvent partager le meme compte
        PCMN (ex: 'RC copro' et 'Assurance RC CoC & Comm.aux comptes'
        tous deux sur 6141)."""
        acc = pcmn_class6[0]
        # cat1 already created on this account in previous test
        r = admin_client.post(f"{API}/expense-categories", json={
            "name": f"{TAG}_second_on_same_account", "account_number": acc["number"],
            "copropriete_id": copro_id,
        }, timeout=15)
        assert r.status_code == 200, f"iter90ex : creation multi-natures/compte doit passer, got {r.status_code}: {r.text}"
        created_ids["categories"].append(r.json()["id"])

    def test_must_be_class6(self, admin_client, copro_id, pcmn_class7):
        r = admin_client.post(f"{API}/expense-categories", json={
            "name": f"{TAG}_class7", "account_number": pcmn_class7["number"], "copropriete_id": copro_id,
        }, timeout=15)
        assert r.status_code == 400, f"expected 400 for non-class-6, got {r.status_code}: {r.text}"

    def test_update_reassign_and_invalid_class(self, admin_client, copro_id, pcmn_class6, pcmn_class7, created_ids):
        # Create a second cat on pcmn[1]
        acc2 = pcmn_class6[1]
        r = admin_client.post(f"{API}/expense-categories", json={
            "name": f"{TAG}_cat2", "account_number": acc2["number"], "copropriete_id": copro_id,
        }, timeout=15)
        assert r.status_code == 200
        cat2 = r.json()
        created_ids["categories"].append(cat2["id"])
        # iter90ex : PUT cat2 onto cat1's account -> 200 (plus de 1:1)
        cat1_acc = pcmn_class6[0]["number"]
        pu = admin_client.put(f"{API}/expense-categories/{cat2['id']}", json={
            "name": cat2["name"], "account_number": cat1_acc, "copropriete_id": copro_id,
        }, timeout=15)
        assert pu.status_code == 200, f"iter90ex : reaffectation autorisee, got {pu.status_code}: {pu.text}"
        # PUT to class 7 -> 400 (regle metier inchangee)
        pu2 = admin_client.put(f"{API}/expense-categories/{cat2['id']}", json={
            "name": cat2["name"], "account_number": pcmn_class7["number"], "copropriete_id": copro_id,
        }, timeout=15)
        assert pu2.status_code == 400, f"expected 400 invalid class, got {pu2.status_code}: {pu2.text}"

    def test_delete_blocked_if_used(self, admin_client, copro_id, pcmn_class6, supplier_name, created_ids):
        # Create cat3 on pcmn[2], create invoice referencing it, then DELETE cat3 -> 400
        acc3 = pcmn_class6[2]
        r = admin_client.post(f"{API}/expense-categories", json={
            "name": f"{TAG}_cat3", "account_number": acc3["number"], "copropriete_id": copro_id,
        }, timeout=15)
        assert r.status_code == 200
        cat3 = r.json()
        created_ids["categories"].append(cat3["id"])

        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_cat3",
            "date": "2026-01-10",
            "supplier": supplier_name,
            "description": "test cat link",
            "total_amount": 100.0,
            "expense_category_id": cat3["id"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200, ir.text
        inv = ir.json()
        created_ids["invoices"].append(inv["id"])
        assert inv["account_number"] == acc3["number"], "expense_category_id should derive account_number"
        assert inv["expense_category_id"] == cat3["id"]

        dr = admin_client.delete(f"{API}/expense-categories/{cat3['id']}", timeout=15)
        assert dr.status_code == 400, f"expected 400 (used), got {dr.status_code}: {dr.text}"


# ---------------- Invoice account_number derivation ----------------
class TestInvoiceCategoryDerivation:
    def test_create_with_category_overrides_account(self, admin_client, copro_id, pcmn_class6, supplier_name, created_ids):
        # cat on pcmn[0]; provide a DIFFERENT account_number in payload -> category wins
        cat = admin_client.get(f"{API}/expense-categories", params={"copropriete_id": copro_id}, timeout=10).json()
        cat1 = next(c for c in cat if c["name"] == f"{TAG}_cat1")
        wrong_acc = pcmn_class6[1]["number"]
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_override",
            "date": "2026-01-11",
            "supplier": supplier_name,
            "description": "override test",
            "total_amount": 50.0,
            "account_number": wrong_acc,
            "expense_category_id": cat1["id"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200, ir.text
        inv = ir.json()
        created_ids["invoices"].append(inv["id"])
        assert inv["account_number"] == cat1["account_number"], "category must override account_number"

    def test_update_category_changes_account(self, admin_client, copro_id, pcmn_class6, created_ids):
        # take the previous invoice, update with cat2 -> account changes to cat2.account
        inv_id = created_ids["invoices"][-1]
        cats = admin_client.get(f"{API}/expense-categories", params={"copropriete_id": copro_id}, timeout=10).json()
        cat2 = next(c for c in cats if c["name"] == f"{TAG}_cat2")
        ur = admin_client.put(f"{API}/invoices/{inv_id}", json={
            "number": f"{TAG}_INV_override",
            "date": "2026-01-11",
            "supplier": "x",
            "description": "switched",
            "total_amount": 50.0,
            "expense_category_id": cat2["id"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ur.status_code == 200, ur.text
        assert ur.json()["account_number"] == cat2["account_number"]
        # GET re-confirm persistence
        gr = admin_client.get(f"{API}/invoices/{inv_id}", timeout=10)
        assert gr.json()["account_number"] == cat2["account_number"]
        assert gr.json()["expense_category_id"] == cat2["id"]

    def test_regression_invoice_without_category(self, admin_client, copro_id, pcmn_class6, supplier_name, created_ids):
        """Backward compat: posting account_number without category still works."""
        raw_acc = pcmn_class6[0]["number"]
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_raw",
            "date": "2026-01-12",
            "supplier": supplier_name,
            "description": "no cat",
            "total_amount": 25.0,
            "account_number": raw_acc,
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200, ir.text
        inv = ir.json()
        created_ids["invoices"].append(inv["id"])
        assert inv["account_number"] == raw_acc
        assert inv.get("expense_category_id", "") == ""


# ---------------- Distribution keys usage + force ----------------
class TestDistributionKeysUsage:
    @pytest.fixture(scope="class")
    def lots(self, admin_client, copro_id):
        r = admin_client.get(f"{API}/lots", params={"copropriete_id": copro_id}, timeout=15)
        assert r.status_code == 200
        arr = r.json()
        assert len(arr) >= 2
        return arr[:2]

    def test_usage_endpoint_shape(self, admin_client, copro_id, lots, supplier_name, created_ids):
        # Create dedicated key + invoice referencing it
        kr = admin_client.post(f"{API}/distribution-keys", json={
            "name": f"{TAG}_key1",
            "key_type": "custom",
            "lots": [{"lot_id": lots[0]["id"], "lot_number": lots[0].get("number", "L1"), "share": 1},
                     {"lot_id": lots[1]["id"], "lot_number": lots[1].get("number", "L2"), "share": 1}],
            "copropriete_id": copro_id,
        }, timeout=15)
        assert kr.status_code == 200
        key = kr.json()
        created_ids["dist_keys"].append(key["id"])

        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_K1",
            "date": "2026-01-13",
            "supplier": supplier_name,
            "description": "key usage",
            "total_amount": 200.0,
            "account_number": "600000",
            "distribution_key_id": key["id"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200, ir.text
        created_ids["invoices"].append(ir.json()["id"])

        ur = admin_client.get(f"{API}/distribution-keys/{key['id']}/usage", timeout=15)
        assert ur.status_code == 200
        data = ur.json()
        assert "invoices" in data and "budgets" in data and "fund_calls" in data and "total" in data
        assert data["total"] >= 1
        assert any(i.get("number") == f"{TAG}_INV_K1" for i in data["invoices"])

    def test_put_conflict_without_force(self, admin_client, copro_id, lots, created_ids):
        key_id = created_ids["dist_keys"][-1]
        pu = admin_client.put(f"{API}/distribution-keys/{key_id}", json={
            "name": f"{TAG}_key1_renamed",
            "key_type": "custom",
            "lots": [{"lot_id": lots[0]["id"], "lot_number": lots[0].get("number", "L1"), "share": 2}],
            "copropriete_id": copro_id,
        }, timeout=15)
        assert pu.status_code == 409, f"expected 409, got {pu.status_code}: {pu.text}"

    def test_put_force_detaches(self, admin_client, copro_id, lots, created_ids):
        key_id = created_ids["dist_keys"][-1]
        pu = admin_client.put(f"{API}/distribution-keys/{key_id}?force=true", json={
            "name": f"{TAG}_key1_forced",
            "key_type": "custom",
            "lots": [{"lot_id": lots[0]["id"], "lot_number": lots[0].get("number", "L1"), "share": 3}],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert pu.status_code == 200, pu.text
        data = pu.json()
        assert data.get("updated") is True
        assert data.get("detached_invoices", 0) >= 1
        # Verify invoice's distribution_key_id cleared
        invs = admin_client.get(f"{API}/invoices", params={"copropriete_id": copro_id}, timeout=15).json()
        our = next((i for i in invs if i.get("number") == f"{TAG}_INV_K1"), None)
        assert our is not None
        assert our.get("distribution_key_id", "") in ("", None)
        assert our.get("distribution_lines") in ([], None)

    def test_delete_blocked_if_used(self, admin_client, copro_id, lots, supplier_name, created_ids):
        # Create new key + invoice, then DELETE -> 400
        kr = admin_client.post(f"{API}/distribution-keys", json={
            "name": f"{TAG}_key2",
            "key_type": "custom",
            "lots": [{"lot_id": lots[0]["id"], "lot_number": lots[0].get("number", "L1"), "share": 1}],
            "copropriete_id": copro_id,
        }, timeout=15)
        assert kr.status_code == 200
        k2 = kr.json()
        created_ids["dist_keys"].append(k2["id"])
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_K2",
            "date": "2026-01-14",
            "supplier": supplier_name,
            "description": "block delete",
            "total_amount": 100.0,
            "account_number": "600000",
            "distribution_key_id": k2["id"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200
        created_ids["invoices"].append(ir.json()["id"])
        dr = admin_client.delete(f"{API}/distribution-keys/{k2['id']}", timeout=15)
        assert dr.status_code == 400, f"expected 400, got {dr.status_code}: {dr.text}"


# ---------------- Accounting entries: manual edit policy ----------------
class TestAutoEntryManualEdit:
    @pytest.fixture(scope="class")
    def auto_entry(self, admin_client, copro_id, supplier_name, created_ids, pcmn_class6):
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_AUTO",
            "date": "2026-01-15",
            "supplier": supplier_name,
            "description": "auto edit",
            "total_amount": 77.0,
            "account_number": pcmn_class6[0]["number"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200
        inv = ir.json()
        created_ids["invoices"].append(inv["id"])
        # Fetch AC entry
        entries = admin_client.get(f"{API}/accounting/entries", params={"copropriete_id": copro_id, "journal_type": "AC"}, timeout=15).json()
        e = next((x for x in entries if x.get("source_id") == inv["id"]), None)
        assert e, "no AC auto-entry for invoice"
        return {"invoice_id": inv["id"], "entry": e}

    def test_put_auto_marks_manually_edited(self, admin_client, auto_entry):
        e = auto_entry["entry"]
        # Update with same lines (still balanced) but change description
        upd = {
            "journal_type": e["journal_type"],
            "date": e["date"],
            "reference": e.get("reference", ""),
            "description": "EDITED-" + (e.get("description") or ""),
            "lines": e["lines"],
            "copropriete_id": e.get("copropriete_id", ""),
        }
        r = admin_client.put(f"{API}/accounting/entries/{e['id']}", json=upd, timeout=15)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out.get("manually_edited") is True
        assert out.get("manually_edited_at")
        assert out.get("auto_generated") is True  # preserved

    def test_delete_auto_without_edit_blocked(self, admin_client, copro_id, supplier_name, pcmn_class6, created_ids):
        # Create new invoice -> new auto entry NOT edited, expect 400 on delete
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_NODEL",
            "date": "2026-01-15",
            "supplier": supplier_name,
            "description": "no del",
            "total_amount": 11.0,
            "account_number": pcmn_class6[0]["number"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200
        created_ids["invoices"].append(ir.json()["id"])
        entries = admin_client.get(f"{API}/accounting/entries", params={"copropriete_id": copro_id, "journal_type": "AC"}, timeout=15).json()
        e = next((x for x in entries if x.get("source_id") == ir.json()["id"]), None)
        assert e
        dr = admin_client.delete(f"{API}/accounting/entries/{e['id']}", timeout=15)
        assert dr.status_code == 400, f"expected 400 auto-not-edited, got {dr.status_code}: {dr.text}"

    def test_delete_auto_after_edit_allowed(self, admin_client, auto_entry):
        # Previously edited; deletion should succeed
        eid = auto_entry["entry"]["id"]
        dr = admin_client.delete(f"{API}/accounting/entries/{eid}", timeout=15)
        assert dr.status_code in (200, 204), f"expected 200/204, got {dr.status_code}: {dr.text}"

    def test_update_source_skips_manually_edited(self, admin_client, copro_id, supplier_name, pcmn_class6, created_ids):
        """When the source invoice is updated, _delete_auto_entries must NOT erase entries flagged manually_edited."""
        ir = admin_client.post(f"{API}/invoices", json={
            "number": f"{TAG}_INV_SKIP",
            "date": "2026-01-16",
            "supplier": supplier_name,
            "description": "skip test",
            "total_amount": 30.0,
            "account_number": pcmn_class6[0]["number"],
            "copropriete_id": copro_id,
        }, timeout=20)
        assert ir.status_code == 200
        inv = ir.json()
        created_ids["invoices"].append(inv["id"])
        # Fetch its AC entry and mark manually_edited via PUT (same balanced lines)
        entries = admin_client.get(f"{API}/accounting/entries", params={"copropriete_id": copro_id, "journal_type": "AC"}, timeout=15).json()
        e = next((x for x in entries if x.get("source_id") == inv["id"]), None)
        assert e
        admin_client.put(f"{API}/accounting/entries/{e['id']}", json={
            "journal_type": e["journal_type"], "date": e["date"], "reference": e.get("reference", ""),
            "description": "kept-" + (e.get("description") or ""), "lines": e["lines"],
            "copropriete_id": e.get("copropriete_id", "")
        }, timeout=15)
        # Now PUT invoice (triggers _delete_auto_entries + regenerate)
        ur = admin_client.put(f"{API}/invoices/{inv['id']}", json={
            "number": inv["number"], "date": inv["date"], "supplier": inv["supplier"],
            "description": "AFTER-UPDATE", "total_amount": 30.0,
            "account_number": pcmn_class6[0]["number"], "copropriete_id": copro_id,
        }, timeout=20)
        assert ur.status_code == 200
        # The edited entry should STILL exist; a new auto entry will also be inserted
        entries2 = admin_client.get(f"{API}/accounting/entries", params={"copropriete_id": copro_id, "journal_type": "AC"}, timeout=15).json()
        kept = [x for x in entries2 if x["id"] == e["id"]]
        assert len(kept) == 1, "manually_edited entry was overwritten by source update"
        assert kept[0].get("manually_edited") is True


# ---------------- RBAC: owner forbidden ----------------
class TestRBACOwner:
    @pytest.fixture(scope="class")
    def owner_client(self):
        email = f"TEST_iter17_owner_{uuid.uuid4().hex[:6]}@example.com"
        s = requests.Session()
        r = s.post(f"{API}/auth/register", json={
            "email": email, "password": "ownerpass123", "name": "owner17",
        }, timeout=20)
        if r.status_code not in (200, 201):
            pytest.skip(f"cannot register owner: {r.status_code} {r.text}")
        # Login (register may or may not auto-login)
        s2 = requests.Session()
        lr = s2.post(f"{API}/auth/login", json={"email": email, "password": "ownerpass123"}, timeout=20)
        assert lr.status_code == 200, lr.text
        return s2

    def test_owner_cannot_list_categories(self, owner_client):
        r = owner_client.get(f"{API}/expense-categories", timeout=15)
        assert r.status_code == 403, f"expected 403, got {r.status_code}"

    def test_owner_cannot_create_category(self, owner_client):
        r = owner_client.post(f"{API}/expense-categories", json={
            "name": "x", "account_number": "611100", "copropriete_id": ""
        }, timeout=15)
        assert r.status_code == 403, f"expected 403, got {r.status_code}"

    def test_owner_cannot_get_dist_key_usage(self, owner_client, admin_client, copro_id):
        # Pick any key id
        keys = admin_client.get(f"{API}/distribution-keys", params={"copropriete_id": copro_id}, timeout=15).json()
        if not keys:
            pytest.skip("no dist key to test")
        r = owner_client.get(f"{API}/distribution-keys/{keys[0]['id']}/usage", timeout=15)
        assert r.status_code == 403, f"expected 403, got {r.status_code}"
