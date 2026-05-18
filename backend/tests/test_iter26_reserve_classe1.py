"""Iter26 - Fonds de reserve credit doit utiliser compte CLASSE 1 (160) au lieu de
CLASSE 7 (701000). Raison comptable belge PCMN : reserve et roulement sont des
augmentations de PASSIF, pas des produits.

Tests:
 (1) Migration endpoint POST /api/coproprietes/{id}/migrate-reserve-to-classe1
     - admin/syndic only (owner -> 403)
     - idempotent (2eme appel : lines_changed=0)
     - retourne {entries_scanned, entries_updated, lines_changed}
 (2) Apres migration, AUCUNE VE auto-generated source_type=fund_call ne contient 701000.
     Les reserves credit 160, les roulements credit 100.
 (3) POST /api/fund-calls (avec reserve_amount via generate-from-budget) genere une VE
     auto qui CREDITE 160 (pas 701000) et la roulement_amount credite 100.
 (4) POST /api/fund-calls/{id}/generate-entries (legacy) avec call_type=reserve
     genere ligne credit 160 (pas 701000).
 (5) GET /api/reports/bilan : 160 et 100 presents au PASSIF (credit -> passif).
 (6) Regression : VE provisions credit toujours 700000 (non-regression).
"""
import os
import uuid
import pytest
import requests

# Load REACT_APP_BACKEND_URL from environment or frontend/.env
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

ACP_ID = "6748ca1a-216d-4002-8417-799287238736"


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "X-Copropriete-Id": ACP_ID})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def owner_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "X-Copropriete-Id": ACP_ID})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "evrard.gerald@outlook.be", "password": "owner123"})
    if r.status_code != 200:
        pytest.skip(f"Owner login failed: {r.status_code}")
    return s


@pytest.fixture(scope="module")
def approved_budget(admin):
    r = admin.get(f"{BASE_URL}/api/fiscal/budgets?copropriete_id={ACP_ID}")
    assert r.status_code == 200, r.text
    items = r.json()
    approved = [b for b in items if b.get("status") == "approved"]
    if not approved:
        pytest.skip("No approved budget found on ACP_DEMO")
    return approved[0]


# ============================================================
# (1) Migration RBAC + idempotency
# ============================================================
class TestMigrationEndpoint:
    def test_owner_forbidden(self, owner_client):
        r = owner_client.post(
            f"{BASE_URL}/api/coproprietes/{ACP_ID}/migrate-reserve-to-classe1")
        assert r.status_code == 403, f"Expected 403, got {r.status_code} {r.text}"

    def test_admin_returns_shape(self, admin):
        r = admin.post(
            f"{BASE_URL}/api/coproprietes/{ACP_ID}/migrate-reserve-to-classe1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "entries_scanned" in body
        assert "entries_updated" in body
        assert "lines_changed" in body
        assert isinstance(body["entries_scanned"], int)
        assert isinstance(body["entries_updated"], int)
        assert isinstance(body["lines_changed"], int)
        assert body.get("status") == "ok"

    def test_idempotent_second_call(self, admin):
        # First call - may or may not change anything (already migrated)
        admin.post(f"{BASE_URL}/api/coproprietes/{ACP_ID}/migrate-reserve-to-classe1")
        # Second call must be a no-op
        r2 = admin.post(
            f"{BASE_URL}/api/coproprietes/{ACP_ID}/migrate-reserve-to-classe1")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["entries_updated"] == 0, f"Not idempotent : {body}"
        assert body["lines_changed"] == 0, f"Not idempotent : {body}"

    def test_404_when_copro_unknown(self, admin):
        bogus = str(uuid.uuid4())
        r = admin.post(
            f"{BASE_URL}/api/coproprietes/{bogus}/migrate-reserve-to-classe1")
        assert r.status_code == 404


# ============================================================
# (2) Post-migration : no 701000 in any auto VE fund_call entry
# ============================================================
class TestNoLegacyReserveAccount:
    def test_no_701000_in_auto_ve_fund_call(self, admin):
        r = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
        assert r.status_code == 200, r.text
        entries = r.json()
        offenders = []
        seen_160 = False
        seen_100 = False
        seen_700000 = False
        for e in entries:
            if not (e.get("source_type") == "fund_call"
                    and e.get("journal_type") == "VE"
                    and e.get("auto_generated")
                    and not e.get("manually_edited")):
                continue
            for ln in e.get("lines", []):
                acc = ln.get("account_number")
                if acc == "701000" and (ln.get("credit") or 0) > 0:
                    offenders.append({"entry": e.get("reference"), "acc": acc})
                if acc == "160":
                    seen_160 = True
                if acc == "100":
                    seen_100 = True
                if acc == "700000" and (ln.get("credit") or 0) > 0:
                    seen_700000 = True
        assert offenders == [], f"701000 credit still present in auto VE: {offenders}"
        # On ACP demo we have at least one reserve call and one roulement call
        assert seen_160, "Aucune VE auto avec compte 160 trouvee (reserve)"
        assert seen_100, "Aucune VE auto avec compte 100 trouvee (roulement)"
        assert seen_700000, "VE provisions ne credite plus 700000 (regression)"


# ============================================================
# (3) New VE auto-entries via /generate-from-budget credit 160 / 100
# ============================================================
class TestGenerateFromBudgetCredits:
    def test_reserve_call_credits_160(self, admin, approved_budget):
        """Use /preview-from-budget so we don't pollute the DB, but
        we need persistence to inspect generated VE. We use generate-from-budget
        with a far-future start_date so the calls don't clash with existing ones,
        then we delete them after the test."""
        tag = f"TEST_ITER26_{uuid.uuid4().hex[:6]}"
        payload = {
            "budget_id": approved_budget["id"],
            "frequency": 1,
            "start_date": "2099-01-01",
            "due_offset_days": 30,
            "reserve_fund": {
                "enabled": True, "amount": 600.0,
                "label": f"{tag}-RES", "frequency": 2,
                "start_date": "2099-01-01", "due_offset_days": 30,
            },
            "roulement_fund": {
                "enabled": True, "amount": 400.0,
                "label": f"{tag}-ROUL", "mode": "create",
                "frequency": 0,  # injected on call #1
            },
            "copropriete_id": ACP_ID,
        }
        r = admin.post(f"{BASE_URL}/api/fund-calls/generate-from-budget", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        created_ids = body.get("created_ids", [])
        assert created_ids, body
        try:
            # Fetch journal entries and look for new ones tied to these fund calls.
            j = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
            assert j.status_code == 200
            ent = j.json()
            relevant = [e for e in ent if e.get("source_id") in created_ids
                        and e.get("journal_type") == "VE"
                        and e.get("auto_generated")]
            assert relevant, f"Aucune VE auto generee pour created_ids={created_ids}"

            # Verifie qu'aucune VE n'a 701000 cote credit
            bad = []
            cr_160_total = 0.0
            cr_100_total = 0.0
            cr_700000_total = 0.0
            for e in relevant:
                for ln in e.get("lines", []):
                    if ln.get("account_number") == "701000" and (ln.get("credit") or 0) > 0:
                        bad.append({"entry": e["id"], "ln": ln})
                    if ln.get("account_number") == "160":
                        cr_160_total += float(ln.get("credit") or 0)
                    if ln.get("account_number") == "100":
                        cr_100_total += float(ln.get("credit") or 0)
                    if ln.get("account_number") == "700000":
                        cr_700000_total += float(ln.get("credit") or 0)
            assert bad == [], f"701000 detecte dans nouvelles VE: {bad}"
            assert cr_160_total > 0, "Aucun credit 160 sur les nouvelles VE de reserve"
            assert cr_100_total > 0, "Aucun credit 100 sur la nouvelle VE de roulement"
            # provisions (700000) doit aussi exister (call principal du budget)
            assert cr_700000_total > 0, "VE provisions absente (regression 700000)"
        finally:
            # Cleanup: delete created fund-calls (cascade auto entries)
            for cid in created_ids:
                try:
                    admin.delete(f"{BASE_URL}/api/fund-calls/{cid}")
                except Exception:
                    pass


# ============================================================
# (4) Legacy POST /fund-calls/{id}/generate-entries with call_type=reserve
# ============================================================
class TestLegacyGenerateEntries:
    def test_legacy_endpoint_credits_160_for_reserve(self, admin):
        # Create a minimal fund_call with call_type=reserve.
        payload = {
            "name": f"TEST_ITER26_LEGACY_{uuid.uuid4().hex[:6]}",
            "date": "2099-02-01",
            "total_amount": 1234.0,
            "call_type": "reserve",
            "copropriete_id": ACP_ID,
        }
        r = admin.post(f"{BASE_URL}/api/fund-calls", json=payload)
        assert r.status_code in (200, 201), r.text
        call_id = r.json()["id"]
        try:
            g = admin.post(f"{BASE_URL}/api/fund-calls/{call_id}/generate-entries")
            assert g.status_code == 200, g.text
            entry_id = g.json().get("entry_id")
            assert entry_id

            # Fetch journal entries to verify line credit 160
            j = admin.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={ACP_ID}")
            assert j.status_code == 200
            entries = [e for e in j.json() if e.get("id") == entry_id]
            assert entries, f"Entry {entry_id} not found"
            entry = entries[0]
            credit_accs = [ln.get("account_number") for ln in entry["lines"]
                           if (ln.get("credit") or 0) > 0]
            assert "160" in credit_accs, f"legacy reserve doit crediter 160, got {credit_accs}"
            assert "701000" not in credit_accs, \
                f"legacy reserve credite encore 701000: {credit_accs}"
        finally:
            try:
                admin.delete(f"{BASE_URL}/api/fund-calls/{call_id}")
            except Exception:
                pass


# ============================================================
# (5) Bilan exposes 160 + 100 at PASSIF
# ============================================================
class TestBilanPassif:
    def test_160_and_100_present_in_passif(self, admin):
        r = admin.get(f"{BASE_URL}/api/reports/bilan?copropriete_id={ACP_ID}")
        assert r.status_code == 200, r.text
        bilan = r.json()
        passif_accs = {}
        for section in bilan.get("passif", []):
            for a in section.get("accounts", []):
                passif_accs[a.get("account_number")] = a.get("amount", 0)
        assert "160" in passif_accs, f"160 absent du PASSIF: keys={list(passif_accs)}"
        assert "100" in passif_accs, f"100 absent du PASSIF: keys={list(passif_accs)}"
        # Solde doit etre > 0 (au moins une migration / un roulement a eu lieu)
        assert passif_accs["160"] != 0, f"Solde 160 = 0 (attendu non nul)"
        assert passif_accs["100"] != 0, f"Solde 100 = 0 (attendu non nul)"

    def test_no_160_or_100_in_actif(self, admin):
        # 160 et 100 sont des passifs : ils ne doivent pas apparaitre cote actif
        r = admin.get(f"{BASE_URL}/api/reports/bilan?copropriete_id={ACP_ID}")
        assert r.status_code == 200
        actif_accs = []
        for section in r.json().get("actif", []):
            for a in section.get("accounts", []):
                actif_accs.append(a.get("account_number"))
        assert "160" not in actif_accs, "160 ne doit pas etre a l'actif"
        # NB: 100 est un fonds passif - meme regle
        assert "100" not in actif_accs, "100 ne doit pas etre a l'actif"
