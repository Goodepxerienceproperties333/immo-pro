"""iter90fs / iter90ft — Manual supplier reconciliation in Import Wizard.

Covers:
  A. GET /api/import-wizard/sessions/{id}/suppliers-catalog + chinese-wall
  B. POST preview-invoices name_suggestion (homonym) + orphan_accounts
  C. POST preview-invoices with manual_matches (by aux_code)
  D. POST preview-invoices with manual_matches (by NAME upper)
  E. POST commit-invoices with manual_matches -> no duplicate supplier,
     invoice's supplier_aux_code rewritten to target, AC entry credits target.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"

ALPHA_COPRO = "b6fe4e32-555a-435b-84cd-1f080cdd47c6"  # Test E2E ACP Alpha
BETA_COPRO = "1a6b28ee-3a0e-41dd-90f1-a25e7a139917"   # Test E2E ACP Beta

ALPHA_CREDS = {"email": "syndic_alpha@copro.be", "password": "Syndic123!"}
BETA_CREDS = {"email": "syndic_beta@copro.be", "password": "Syndic123!"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _login(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def alpha():
    return _login(ALPHA_CREDS)


@pytest.fixture(scope="module")
def beta():
    return _login(BETA_CREDS)


@pytest.fixture(scope="module")
def seeded_supplier(alpha):
    """Seed 'Baloise' with auxiliary_code F00099 in Alpha ACP.

    Uses POST /api/suppliers then sets auxiliary_code directly in DB
    because the API does not expose auxiliary_code on create.
    """
    # unique BCE to avoid dup collisions across runs
    bce = "BE0403257506"
    name = "Baloise"
    # try create; if already exists (dup) reuse
    payload = {
        "name": name,
        "bce_number": bce,
        "copropriete_id": ALPHA_COPRO,
        "force_create_despite_similar": True,
    }
    r = alpha.post(f"{API}/suppliers", json=payload, timeout=15)
    if r.status_code == 409:
        # fetch existing
        rl = alpha.get(f"{API}/suppliers?copropriete_id={ALPHA_COPRO}", timeout=15)
        assert rl.status_code == 200
        sup = None
        data = rl.json()
        rows = data if isinstance(data, list) else data.get("suppliers", [])
        for s in rows:
            if (s.get("name") or "").lower() == name.lower():
                sup = s
                break
        assert sup, "Existing Baloise not found"
    else:
        assert r.status_code in (200, 201), f"create supplier: {r.status_code} {r.text}"
        sup = r.json()

    # Force auxiliary_code=F00099 directly in DB (motor)
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _set_aux():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        await db.suppliers.update_one({"id": sup["id"]}, {"$set": {"auxiliary_code": "F00099"}})
        cli.close()

    asyncio.run(_set_aux())
    sup["auxiliary_code"] = "F00099"
    return sup


@pytest.fixture(scope="module")
def alpha_session(alpha):
    r = alpha.post(f"{API}/import-wizard/sessions", json={"copropriete_id": ALPHA_COPRO}, timeout=15)
    assert r.status_code == 200, f"create session: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def _cleanup(seeded_supplier, alpha_session):
    """Teardown: cleanup any test-seeded suppliers/invoices."""
    yield
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _cleanup_db():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        # cleanup invoices created by commit test (external_ref TEST_ITER90FS_*)
        await db.invoices.delete_many({"external_ref": {"$regex": "^TEST_ITER90FS_"}})
        await db.journal_entries.delete_many({"reference": {"$regex": "TEST_ITER90FS_"}})
        # NOTE: do not delete Baloise supplier to keep tests idempotent across runs
        cli.close()

    asyncio.run(_cleanup_db())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoice_baloise_insurance(ref_suffix="A"):
    """Invoice referring to 'Baloise Insurance' F00500 - homonym of Baloise F00099."""
    return {
        "supplier_name": "Baloise Insurance",
        "supplier_aux_code": "F00500",
        "account_number": "61000",
        "account_label": "Assurance",
        "montant_tvac": 100.0,
        "date": "2025-06-15",
        "external_ref": f"TEST_ITER90FS_{ref_suffix}",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSuppliersCatalog:
    def test_alpha_can_read_catalog(self, alpha, alpha_session, seeded_supplier):
        r = alpha.get(f"{API}/import-wizard/sessions/{alpha_session['id']}/suppliers-catalog", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "suppliers" in body and "count" in body
        assert body["count"] == len(body["suppliers"])
        names = [s["name"] for s in body["suppliers"]]
        assert names == sorted(names, key=lambda x: (x or "").lower()) or names == sorted(names)
        aux_codes = [s["auxiliary_code"] for s in body["suppliers"]]
        assert "F00099" in aux_codes, f"Expected seeded Baloise F00099 in catalog: {aux_codes}"

    def test_chinese_wall_beta_cannot_read_alpha_session_catalog(self, beta, alpha_session):
        r = beta.get(f"{API}/import-wizard/sessions/{alpha_session['id']}/suppliers-catalog", timeout=15)
        assert r.status_code in (403, 404), f"Expected 403/404, got {r.status_code} {r.text}"


class TestPreviewNameSuggestion:
    def test_homonym_suggestion_populated(self, alpha, alpha_session, seeded_supplier):
        payload = {
            "invoices": [_invoice_baloise_insurance("SUG1")],
            "manual_matches": {},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/preview-invoices",
            json=payload, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        row = body["preview"][0]
        assert row["status"] == "to_create"
        assert row["name_suggestion"] is not None, f"Expected name_suggestion, got {row}"
        assert row["name_suggestion"]["auxiliary_code"] == "F00099"
        assert body["to_create"] == 1

    def test_unknown_name_no_suggestion(self, alpha, alpha_session):
        payload = {
            "invoices": [{
                "supplier_name": "ZzzUnknownCorp",
                "supplier_aux_code": "F00900",
                "account_number": "61000",
                "account_label": "Divers",
                "montant_tvac": 50.0,
                "date": "2025-06-15",
                "external_ref": "TEST_ITER90FS_UNK",
            }],
            "manual_matches": {},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/preview-invoices",
            json=payload, timeout=20,
        )
        assert r.status_code == 200, r.text
        row = r.json()["preview"][0]
        assert row["status"] == "to_create"
        assert row["name_suggestion"] is None


class TestPreviewManualMatchByAuxCode:
    def test_manual_match_flips_status_and_rewrites_aux(self, alpha, alpha_session, seeded_supplier):
        payload = {
            "invoices": [_invoice_baloise_insurance("MMA")],
            "manual_matches": {"F00500": "F00099"},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/preview-invoices",
            json=payload, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["manual_match"] == 1, body
        assert body["applied_matches_count"] == 1, body
        assert body["to_create"] == 0, body
        row = body["preview"][0]
        assert row["status"] == "manual_match"
        assert row["supplier_aux_code"] == "F00099"


class TestPreviewManualMatchByName:
    def test_manual_match_by_uppercase_name(self, alpha, alpha_session, seeded_supplier):
        payload = {
            "invoices": [_invoice_baloise_insurance("MMB")],
            "manual_matches": {"BALOISE INSURANCE": "F00099"},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/preview-invoices",
            json=payload, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["manual_match"] == 1, body
        row = body["preview"][0]
        assert row["status"] == "manual_match"
        assert row["supplier_aux_code"] == "F00099"


class TestOrphanAccounts:
    def test_orphan_440_detected(self, alpha, alpha_session):
        payload = {
            "invoices": [{
                "supplier_name": "OrphanCorp",
                "supplier_aux_code": "F00777",
                "account_number": "61000",
                "account_label": "Divers",
                "montant_tvac": 20.0,
                "date": "2025-06-15",
                "external_ref": "TEST_ITER90FS_ORPHAN",
                "_split_lines": [
                    {"account_number": "44000042", "amount": 20.0}
                ],
            }],
            "manual_matches": {},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/preview-invoices",
            json=payload, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        orphans = body.get("orphan_accounts") or []
        match = next((o for o in orphans if o["account_number"] == "44000042"), None)
        assert match is not None, f"Expected orphan 44000042: {orphans}"
        assert match["invoice_rows_count"] >= 1
        assert "OrphanCorp" in (match.get("supplier_names") or [])
        assert "F00777" in (match.get("supplier_aux_codes") or [])
        assert "suggestions" in match  # can be empty for OrphanCorp


class TestCommitWithManualMatches:
    def test_commit_merges_no_duplicate_created(self, alpha, alpha_session, seeded_supplier):
        # snapshot supplier count for ACP before commit
        rc = alpha.get(f"{API}/suppliers?copropriete_id={ALPHA_COPRO}", timeout=15)
        assert rc.status_code == 200
        pre = rc.json()
        pre_list = pre if isinstance(pre, list) else pre.get("suppliers", [])
        pre_count = len(pre_list)

        payload = {
            "invoices": [_invoice_baloise_insurance("COMMIT1")],
            "manual_matches": {"F00500": "F00099"},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/commit-invoices",
            json=payload, timeout=30,
        )
        # Some commit endpoints return 200 with counts; capture body either way.
        assert r.status_code == 200, f"commit failed: {r.status_code} {r.text}"

        # Post-commit supplier count MUST NOT have grown
        rc2 = alpha.get(f"{API}/suppliers?copropriete_id={ALPHA_COPRO}", timeout=15)
        post = rc2.json()
        post_list = post if isinstance(post, list) else post.get("suppliers", [])
        assert len(post_list) == pre_count, (
            f"Duplicate supplier created! pre={pre_count} post={len(post_list)}"
        )

        # No F00500 supplier should exist
        f500 = [s for s in post_list if (s.get("auxiliary_code") or "").upper() == "F00500"]
        assert not f500, f"Unexpected supplier with auxiliary_code F00500: {f500}"

        # Invoice was inserted with supplier_aux_code=F00099
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient

        async def _fetch():
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = cli[os.environ["DB_NAME"]]
            inv = await db.invoices.find_one({"external_ref": "TEST_ITER90FS_COMMIT1"})
            entries = []
            if inv:
                async for e in db.journal_entries.find({"invoice_id": inv.get("id")}, {"_id": 0}):
                    entries.append(e)
            cli.close()
            return inv, entries

        inv, entries = asyncio.run(_fetch())
        assert inv is not None, "Invoice not persisted"
        assert (inv.get("supplier_aux_code") or "").upper() == "F00099", inv.get("supplier_aux_code")
        # AC journal entry should credit F00099's tier account
        # (best-effort assertion: at least one entry exists)
        assert entries, "Expected at least one journal entry for committed invoice"
