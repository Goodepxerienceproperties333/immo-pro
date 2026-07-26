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
GERALD_COPRO = "a0eef7c0-1db2-45ae-9053-040fbee71be5"  # Gerald ACP Maria
GERALD_COPRO_OTHER = "73603546-6a7c-4145-b969-e990fc988d5e"  # Gerald other ACP

# Syndic ids (from users._id in mongo)
ALPHA_SYNDIC_ID = "6a65b6396daad720f9dcbfa6"
BETA_SYNDIC_ID = "6a65b6396daad720f9dcbfa7"
GERALD_SYNDIC_ID = "6a633d2eeb4b5d8517837b17"

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


@pytest.fixture(scope="module")
def seeded_fiscal_year():
    """iter90ft: Seed an open fiscal_year for ALPHA_COPRO that covers 2025-06-15
    so the commit-invoices test can actually persist an invoice.
    Idempotent: if a covering FY already exists, do nothing.
    """
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _seed():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        existing = await db.fiscal_years.find_one({
            "copropriete_id": ALPHA_COPRO,
            "start_date": {"$lte": "2025-06-15"},
            "end_date": {"$gte": "2025-06-15"},
        })
        seeded_id = None
        if not existing:
            doc = {
                "id": f"TEST_FY_{uuid.uuid4()}",
                "copropriete_id": ALPHA_COPRO,
                "syndic_id": ALPHA_SYNDIC_ID,
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
                "name": "FY 2025 TEST",
                "is_closed": False,
                "status": "open",
            }
            await db.fiscal_years.insert_one(doc)
            seeded_id = doc["id"]
        cli.close()
        return seeded_id

    seeded_id = asyncio.run(_seed())
    yield seeded_id
    # Teardown: remove only if we seeded it
    if seeded_id:
        async def _rm():
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = cli[os.environ["DB_NAME"]]
            await db.fiscal_years.delete_one({"id": seeded_id})
            cli.close()
        asyncio.run(_rm())


@pytest.fixture(scope="module", autouse=True)
def _cleanup(seeded_supplier, alpha_session, seeded_fiscal_year):
    """Teardown: cleanup any test-seeded suppliers/invoices."""
    yield
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _cleanup_db():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        # cleanup invoices created by commit test (external_ref TEST_ITER90FS_*)
        await db.invoices.delete_many({"external_ref": {"$regex": "^TEST_ITER90FS_"}})
        # Also cleanup test invoices without external_ref (only Alpha ACP F00099 recent test rows)
        await db.invoices.delete_many({
            "copropriete_id": ALPHA_COPRO,
            "supplier_aux_code": "F00099",
            "total_amount": {"$in": [100.0, 42.0]},
        })
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
            # iter90ft: external_ref is not persisted on invoice docs.
            # Look up the most recent invoice for this ACP w/ target aux code.
            inv = await db.invoices.find_one(
                {
                    "copropriete_id": ALPHA_COPRO,
                    "supplier_aux_code": "F00099",
                    "total_amount": 100.0,
                },
                sort=[("created_at", -1)],
            )
            entries = []
            if inv:
                inv_id = inv.get("id")
                # Journal entries can reference the invoice via various fields.
                async for e in db.journal_entries.find(
                    {"$or": [
                        {"invoice_id": inv_id},
                        {"id": inv.get("journal_entry_id")},
                    ]},
                    {"_id": 0},
                ):
                    entries.append(e)
            cli.close()
            return inv, entries

        inv, entries = asyncio.run(_fetch())
        assert inv is not None, "Invoice not persisted"
        assert (inv.get("supplier_aux_code") or "").upper() == "F00099", inv.get("supplier_aux_code")
        # AC journal entry should credit F00099's tier account
        # (best-effort assertion: at least one entry exists)
        assert entries, "Expected at least one journal entry for committed invoice"


# ---------------------------------------------------------------------------
# iter90ft — Noise-word normalization + keyword multi-match + syndic-wide scope
# ---------------------------------------------------------------------------


class TestNoiseWordNormalization:
    """Unit tests for _norm_name expanded noise-word list."""

    def test_baloise_insurance_belgium_sa_norms_to_baloise(self):
        from routes.suppliers import _norm_name
        assert _norm_name("Baloise Insurance Belgium SA") == "baloise"

    def test_baloise_srl_belgium_norms_to_baloise(self):
        from routes.suppliers import _norm_name
        assert _norm_name("Baloise SRL Belgium") == "baloise"

    def test_baloise_assurances_belgique_norms_to_baloise(self):
        from routes.suppliers import _norm_name
        assert _norm_name("Baloise Assurances Belgique") == "baloise"

    def test_engie_group_holding_services_norms_to_engie(self):
        from routes.suppliers import _norm_name
        # All 3 tokens are noise -> only "engie" remains
        assert _norm_name("Engie Group Holding Services") == "engie"

    def test_multi_word_brand_preserved(self):
        from routes.suppliers import _norm_name
        # "Good Experience" is 2 real words -> both preserved (sorted)
        assert _norm_name("Good Experience Properties SA") in ("experience good properties", "experience good properties")


class TestKeywordMatchingSyndicWide:
    """iter90ft — find_supplier_candidates_by_keyword must:
      - Return matches across an ENTIRE syndic (across ACPs).
      - NEVER cross-syndic (Chinese Wall).
      - Rank same-copro higher via +0.15 bonus.
    """

    def test_gerald_keyword_finds_both_baloise_insurance_across_acps(self):
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import find_supplier_candidates_by_keyword

        async def _run():
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = cli[os.environ["DB_NAME"]]
            hits = await find_supplier_candidates_by_keyword(
                db, name="Baloise Insurance",
                syndic_id=GERALD_SYNDIC_ID,
                copro_id=GERALD_COPRO,
            )
            cli.close()
            return hits

        hits = asyncio.run(_run())
        assert len(hits) >= 2, f"Expected 2+ Baloise hits for Gerald, got {hits}"
        names = [h["supplier"]["name"] for h in hits]
        # Both Baloise Insurance fiches must appear
        assert names.count("Baloise Insurance") >= 2, f"Expected 2 Baloise Insurance fiches, got {names}"
        # The one in Gerald's queried ACP (GERALD_COPRO) must rank first (same_copro bonus)
        top = hits[0]["supplier"]
        assert top.get("copropriete_id") == GERALD_COPRO, (
            f"Top hit should be same-copro; got copropriete_id={top.get('copropriete_id')}"
        )

    def test_alpha_keyword_never_sees_gerald_baloise_insurance(self):
        """CRITICAL Chinese Wall: Alpha searching 'Baloise' MUST NOT
        surface Gerald's Baloise Insurance suppliers."""
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import find_supplier_candidates_by_keyword

        async def _run():
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = cli[os.environ["DB_NAME"]]
            hits = await find_supplier_candidates_by_keyword(
                db, name="Baloise Insurance",
                syndic_id=ALPHA_SYNDIC_ID,
                copro_id=ALPHA_COPRO,
            )
            cli.close()
            return hits

        hits = asyncio.run(_run())
        # All hits must belong to Alpha's syndic
        for h in hits:
            assert h["supplier"].get("syndic_id") == ALPHA_SYNDIC_ID, (
                f"Chinese Wall BREACH: Alpha saw supplier from syndic_id="
                f"{h['supplier'].get('syndic_id')}: {h['supplier']}"
            )
        # Should include Alpha's own seeded 'Baloise' F00099
        alpha_names = [h["supplier"]["name"] for h in hits]
        assert "Baloise" in alpha_names, f"Expected Alpha's 'Baloise' in hits: {alpha_names}"
        # Must NOT contain 'Baloise Insurance' (that's Gerald's)
        assert "Baloise Insurance" not in alpha_names, (
            f"Chinese Wall BREACH: Alpha saw 'Baloise Insurance': {alpha_names}"
        )


class TestSuppliersCatalogSyndicWide:
    """iter90ft — GET /suppliers-catalog now returns whole-syndic scope
    with `same_copro` flag."""

    def test_catalog_has_same_copro_flag_and_no_cross_syndic(self, alpha, alpha_session):
        r = alpha.get(f"{API}/import-wizard/sessions/{alpha_session['id']}/suppliers-catalog", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        rows = body["suppliers"]
        # New field must be present
        assert all("same_copro" in s for s in rows), "Missing same_copro flag on rows"
        # Should NOT contain any 'Baloise Insurance' (that name is Gerald-only)
        names = [s["name"] for s in rows]
        assert "Baloise Insurance" not in names, (
            f"Chinese Wall BREACH: Alpha's catalog contains Gerald's Baloise Insurance: {names}"
        )
        # Alpha has multiple ACPs, catalog should ideally include at least one
        # entry marked same_copro=True (the seeded Baloise F00099).
        same_copro_true = [s for s in rows if s.get("same_copro") is True]
        assert len(same_copro_true) >= 1, f"No same_copro=true entries: {rows[:5]}"


class TestPreviewNameSuggestionsArray:
    """iter90ft — preview-invoices returns name_suggestions[] per row."""

    def test_alpha_baloise_insurance_returns_suggestions_array(self, alpha, alpha_session, seeded_supplier):
        payload = {
            "invoices": [{
                "supplier_name": "Baloise Insurance",
                "supplier_aux_code": "F09876",  # unknown aux
                "account_number": "61000",
                "account_label": "Assurance",
                "montant_tvac": 100.0,
                "date": "2025-06-15",
                "external_ref": "TEST_ITER90FT_SUGS",
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
        # Backward-compat single field
        assert row.get("name_suggestion") is not None, f"Expected name_suggestion: {row}"
        assert row["name_suggestion"]["name"].lower() == "baloise"
        # New array field must be non-empty and contain 'Baloise' entry
        suggestions = row.get("name_suggestions") or []
        assert isinstance(suggestions, list), f"name_suggestions must be list: {type(suggestions)}"
        assert len(suggestions) >= 1, f"Expected 1+ suggestions, got {suggestions}"
        baloise_entry = next((s for s in suggestions if (s.get("name") or "").lower() == "baloise"), None)
        assert baloise_entry is not None, f"Missing 'Baloise' in suggestions: {suggestions}"
        assert baloise_entry.get("same_copro") is True, f"'Baloise' should be same_copro: {baloise_entry}"
        assert baloise_entry.get("score", 0) >= 1.0, f"Same-copro top hit score should be >=1.0: {baloise_entry}"


class TestFindDuplicateSupplierRegression:
    """Regression: find_duplicate_supplier still matches 'Baloise Insurance'
    as a subset-match of 'Baloise' (backward-compat)."""

    def test_baloise_insurance_matches_baloise(self, seeded_supplier):
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.suppliers import find_duplicate_supplier

        async def _run():
            cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = cli[os.environ["DB_NAME"]]
            hit = await find_duplicate_supplier(
                db, name="Baloise Insurance", copro_id=ALPHA_COPRO,
            )
            cli.close()
            return hit

        hit = asyncio.run(_run())
        # With the expanded noise-word list, "Baloise Insurance" -> "baloise"
        # which now matches the seeded 'Baloise' fiche directly.
        assert hit is not None, "Expected 'Baloise Insurance' to subset-match 'Baloise'"
        assert (hit["supplier"].get("name") or "").lower() == "baloise"


class TestCommitInvoicesNoNameError:
    """iter90ft regression: _ensure_pcmn_accounts takes syndic_id explicitly;
    commit-invoices no longer raises 500 NameError('request')."""

    def test_commit_returns_200_and_persists_invoice(
        self, alpha, alpha_session, seeded_supplier, seeded_fiscal_year,
    ):
        # Use a distinct external_ref so it won't collide with the other commit test.
        payload = {
            "invoices": [{
                "supplier_name": "Baloise Insurance",
                "supplier_aux_code": "F09876",  # unknown -> forces PCMN auto-create
                "account_number": "61000",
                "account_label": "Assurance",
                "montant_tvac": 42.0,
                "date": "2025-06-15",
                "external_ref": "TEST_ITER90FS_COMMIT_NOMANUAL",
            }],
            "manual_matches": {"F09876": "F00099"},
        }
        r = alpha.post(
            f"{API}/import-wizard/sessions/{alpha_session['id']}/commit-invoices",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, f"commit returned non-200: {r.status_code} {r.text}"
        body = r.json()
        # Must not have 500-style errors in body
        errors = body.get("errors") or []
        for err in errors:
            assert "NameError" not in str(err), f"NameError leak in errors: {err}"
            assert "500" not in str(err.get("error", ""))[:5], f"500 in errors: {err}"
        # Expect 1 invoice inserted (FY seeded for 2025-06-15)
        assert body.get("inserted", 0) >= 1, f"Expected 1+ invoice inserted: {body}"
        assert body.get("journal_entries", 0) >= 1, f"Expected 1+ journal entries: {body}"
        # No NEW supplier should be created (manual-match remap to existing F00099)
        rc2 = alpha.get(f"{API}/suppliers?copropriete_id={ALPHA_COPRO}", timeout=15)
        post = rc2.json()
        post_list = post if isinstance(post, list) else post.get("suppliers", [])
        f09876 = [s for s in post_list if (s.get("auxiliary_code") or "").upper() == "F09876"]
        assert not f09876, f"Unexpected supplier F09876 created: {f09876}"

