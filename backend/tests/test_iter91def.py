"""iter91d/e/f backend tests.

- iter91d: _build_expenses_list_pdf (fusion synthese portrait + detail paysage).
  Test indirect via /api/reports/expenses/pdf or direct import call.
- iter91e: POST /api/admin/expense-categories/dedupe dry_run + execution idempotence.
- iter91f: POST /api/import-wizard/sessions/{id}/preview-opening-balance-orphans.
"""
import os
import io
import pytest
import requests
import uuid

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = BASE_URL + "/api"

# Fixtures fournies par le sujet
GAURA_COPRO_ID = "5725c50e-6115-4622-8917-cfed2bd0b96b"
GAURA_FY_ID = "defb50cf-4e58-49dd-9e28-9afc2ee0cef1"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    # Cookie-based auth or bearer token both supported
    tok = r.json().get("token") or r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def auth(session):
    # Legacy alias; returns the session for backward code readability
    return session


# ---------- iter91e : dedupe expense-categories ----------

def test_iter91e_dedupe_dry_run(auth):
    """Dry run doit retourner un rapport JSON avec duplicate_groups et malformed."""
    r = auth.post(f"{API}/admin/expense-categories/dedupe",

                      json={"copropriete_id": GAURA_COPRO_ID, "dry_run": True,
                            "strategy": "name_normalized", "cleanup_malformed": True},
                      timeout=60)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert data["copropriete_id"] == GAURA_COPRO_ID
    assert data["dry_run"] is True
    assert isinstance(data["duplicate_groups"], list)
    assert isinstance(data["malformed"], list)
    assert isinstance(data["total_categories"], int)
    print(f"[iter91e] Dry run: {len(data['duplicate_groups'])} groupes doublons, "
          f"{len(data['malformed'])} malformes sur {data['total_categories']} categories.")


def test_iter91e_dedupe_missing_copro(auth):
    r = auth.post(f"{API}/admin/expense-categories/dedupe",

                      json={"dry_run": True},
                      timeout=30)
    assert r.status_code in (400, 422)


def test_iter91e_dedupe_execute_no_merges(auth):
    """Mode execution sans merges doit renvoyer 400."""
    r = auth.post(f"{API}/admin/expense-categories/dedupe",

                      json={"copropriete_id": GAURA_COPRO_ID, "dry_run": False,
                            "merges": []},
                      timeout=30)
    assert r.status_code in (400, 422)


def test_iter91e_dedupe_unauthorized():
    r = requests.post(f"{API}/admin/expense-categories/dedupe",
                      json={"copropriete_id": GAURA_COPRO_ID, "dry_run": True},
                      timeout=30)
    assert r.status_code in (401, 403)


# ---------- iter91f : preview opening balance orphans ----------

@pytest.fixture(scope="module")
def import_session(auth):
    """Creer une session d'import wizard pour Gaura et retourner l'id."""
    r = auth.post(f"{API}/import-wizard/sessions",

                      json={"copropriete_id": GAURA_COPRO_ID, "source": "test_iter91f"},
                      timeout=30)
    if r.status_code == 404:
        pytest.skip("import-wizard sessions endpoint not accessible")
    assert r.status_code in (200, 201), f"{r.status_code} {r.text[:300]}"
    body = r.json()
    return body.get("id") or body.get("session_id")


def test_iter91f_preview_detects_orphan_and_ignores_mapped(auth, import_session):
    """Envoie 2 comptes 410*: un existant (mappe) et un totalement nouveau."""
    if not import_session:
        pytest.skip("no session")
    payload = {
        "actif": [
            {"account": "41010999", "label": "Nouveau Test Orphan iter91f",
             "amount": 1234.56}
        ],
        "passif": [
            {"account": "40000000", "label": "Fournisseurs (compte non-owner)",
             "amount": 1234.56}
        ],
        "period_end_date": "2025-12-31",
    }
    r = auth.post(
        f"{API}/import-wizard/sessions/{import_session}/preview-opening-balance-orphans",
        json=payload, timeout=60)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()
    assert body["copropriete_id"] == GAURA_COPRO_ID
    assert isinstance(body["orphan_owner_accounts"], list)
    accs = [o["account_number"] for o in body["orphan_owner_accounts"]]
    # 41010999 doit apparaitre (compte 410 non mappe)
    assert "41010999" in accs, f"41010999 pas detecte, orphans={accs}"
    # 40000000 ne doit PAS apparaitre (pas un compte 410*/4001*)
    assert "40000000" not in accs
    # suggested_aux_code = C + 4 derniers chars
    orphan = next(o for o in body["orphan_owner_accounts"] if o["account_number"] == "41010999")
    assert orphan["suggested_aux_code"] == "C0999"
    assert orphan["side"] == "actif"
    assert orphan["amount"] == 1234.56


def test_iter91f_preview_empty_when_no_owner_accounts(auth, import_session):
    if not import_session:
        pytest.skip("no session")
    payload = {
        "actif": [{"account": "55000000", "label": "Banque", "amount": 100.0}],
        "passif": [{"account": "10000000", "label": "Capital", "amount": 100.0}],
        "period_end_date": "2025-12-31",
    }
    r = auth.post(
        f"{API}/import-wizard/sessions/{import_session}/preview-opening-balance-orphans",
        json=payload, timeout=30)
    assert r.status_code == 200
    assert r.json()["count"] == 0


# ---------- iter91d : _build_expenses_list_pdf ----------

def test_iter91d_expenses_list_pdf_merges_synthese_and_detail():
    """Appel direct du helper. Verifie qu'un PDF non vide (>1KB) est renvoye
    et qu'il commence par '%PDF-'. Verifie aussi le nombre de pages >=2."""
    import sys, asyncio
    sys.path.insert(0, "/app/backend")
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.reports import _build_expenses_list_pdf

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    assert mongo_url and db_name, "MONGO_URL / DB_NAME manquants"

    async def _run():
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        try:
            return await _build_expenses_list_pdf(db, GAURA_COPRO_ID, GAURA_FY_ID)
        finally:
            client.close()

    pdf = asyncio.run(_run())
    assert isinstance(pdf, (bytes, bytearray)), "should return bytes"
    if not pdf:
        pytest.skip("no expense rows on Gaura FY 2026 - PDF empty by design")
    assert pdf[:5] == b"%PDF-", f"invalid PDF header: {pdf[:10]!r}"
    assert len(pdf) > 1024, f"PDF too small ({len(pdf)} bytes)"
    # Compte les pages via pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(bytes(pdf)))
        n_pages = len(reader.pages)
        print(f"[iter91d] PDF {len(pdf)} bytes, {n_pages} pages")
        # Doit contenir synthese + detail => au moins 2 pages
        assert n_pages >= 2, f"expected >=2 pages (synthese + detail), got {n_pages}"
    except ImportError:
        pass


def test_iter91d_via_communication_flow_reachable(auth):
    """Verifie que /api/communication/send/decompte est protege / accessible.
    On envoie une requete invalide juste pour verifier le routage (400/422 ok)."""
    r = auth.post(f"{API}/communication/send/decompte",

                      json={"copropriete_id": GAURA_COPRO_ID,
                            "fiscal_year_id": GAURA_FY_ID,
                            "include_expenses_list": True,
                            "recipient_owner_ids": []},
                      timeout=30)
    # 200 (envoi vide accepte) ou 400/422 (validation) OK; PAS 500
    assert r.status_code != 500, f"500 error: {r.text[:300]}"
