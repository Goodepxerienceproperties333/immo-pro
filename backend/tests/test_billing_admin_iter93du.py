"""iter93du - Tests backend Facturation Admin - passage forfait annuel FIXE.

Regles :
  - Tranches [1-50 @ 500€/an], [51-200 @ 1200€/an], [201+ @ 2500€/an]
  - Le montant du bareme = le forfait de la tranche dans laquelle tombe lot_count.
  - REGRESSION legacy : si un tier a `price_per_lot` (ancien schema) sans
    `annual_fee`, retro-compat = price_per_lot * lots.
"""
import os
import sys
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

API = f"{BASE_URL}/api"

sys.path.insert(0, "/app/backend")
from routes.billing_admin import _compute_tiered_amount  # noqa: E402

# iter93du : nouveau schema — forfait annuel FIXE par tranche
DEFAULT_TIERS = [
    {"min_lots": 1, "max_lots": 50, "annual_fee": 500.0},
    {"min_lots": 51, "max_lots": 200, "annual_fee": 1200.0},
    {"min_lots": 201, "max_lots": None, "annual_fee": 2500.0},
]

LEGACY_TIERS = [
    {"min_lots": 1, "max_lots": 50, "price_per_lot": 5.0},
    {"min_lots": 51, "max_lots": 200, "price_per_lot": 4.0},
    {"min_lots": 201, "max_lots": None, "price_per_lot": 3.0},
]


# ---------- Unit tests: _compute_tiered_amount (forfait fixe) ----------
@pytest.mark.parametrize("lots,expected", [
    (0, 0.0),
    (1, 500.0),
    (30, 500.0),      # tranche 1
    (50, 500.0),      # limite haute tranche 1
    (51, 1200.0),     # debut tranche 2
    (88, 1200.0),     # tranche 2 (cas metier)
    (200, 1200.0),    # limite haute tranche 2
    (201, 2500.0),    # debut tranche 3
    (250, 2500.0),    # tranche 3 (cas metier)
    (10000, 2500.0),  # tres grand nombre - tranche 3 ouverte
])
def test_compute_tiered_amount_flat_fee(lots, expected):
    assert _compute_tiered_amount(lots, DEFAULT_TIERS) == expected


def test_compute_tiered_empty_tiers():
    assert _compute_tiered_amount(100, []) == 0.0


def test_compute_tiered_zero_lots():
    assert _compute_tiered_amount(0, DEFAULT_TIERS) == 0.0


# REGRESSION : retro-compat legacy price_per_lot (approximation temporaire)
@pytest.mark.parametrize("lots,expected", [
    (10, 50.0),   # 10 * 5 = 50 (tranche 1 legacy)
    (30, 150.0),  # 30 * 5 = 150
    (88, 352.0),  # 88 * 4 = 352 (tranche 2 legacy)
    (250, 750.0),  # 250 * 3 = 750 (tranche 3 legacy)
])
def test_compute_tiered_legacy_price_per_lot(lots, expected):
    """Si un tier a price_per_lot sans annual_fee, calcul retro-compat."""
    assert _compute_tiered_amount(lots, LEGACY_TIERS) == expected


def test_compute_tiered_mixed_schema_prefers_annual_fee():
    """Si annual_fee ET price_per_lot presents, annual_fee gagne."""
    mixed = [{"min_lots": 1, "max_lots": None, "annual_fee": 999.0, "price_per_lot": 10.0}]
    assert _compute_tiered_amount(50, mixed) == 999.0


# ---------- HTTP fixtures ----------
@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="session")
def syndic_session():
    s = requests.Session()
    email = f"TEST_syndic_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "TestSyndic123!"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd, "name": "TEST Syndic iter93du",
    })
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    s2 = requests.Session()
    r = s2.post(f"{API}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"syndic login: {r.status_code} {r.text}"
    return s2, email


@pytest.fixture(scope="session", autouse=True)
def _clear_billing_config():
    """Delete any existing billing_config before test session so /config
    returns the fresh default (annual_fee schema)."""
    try:
        from pymongo import MongoClient
        mongo = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        dbn = os.environ.get("DB_NAME", "test_database")
        MongoClient(mongo)[dbn].billing_config.delete_many({"id": "default"})
    except Exception as e:
        print(f"cleanup skip: {e}")
    yield


# ---------- Auth / 403 isolation ----------
class TestAuthIsolation:
    def test_config_requires_superadmin(self, syndic_session):
        s, _ = syndic_session
        r = s.get(f"{API}/admin/billing/config")
        assert r.status_code == 403

    def test_syndics_requires_superadmin(self, syndic_session):
        s, _ = syndic_session
        r = s.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 403

    def test_put_config_requires_superadmin(self, syndic_session):
        s, _ = syndic_session
        r = s.put(f"{API}/admin/billing/config", json={
            "tiers": [], "vat_rate": 21, "default_frequency": "annual", "admin_info": {}
        })
        assert r.status_code == 403

    def test_put_syndic_billing_requires_superadmin(self, syndic_session):
        s, _ = syndic_session
        r = s.put(f"{API}/admin/billing/syndics/anyid", json={"frequency": "annual"})
        assert r.status_code == 403

    def test_invoice_pdf_requires_superadmin(self, syndic_session):
        s, _ = syndic_session
        r = s.get(f"{API}/admin/billing/invoice/anyid/pdf")
        assert r.status_code == 403

    def test_unauth_403_or_401(self):
        r = requests.get(f"{API}/admin/billing/config")
        assert r.status_code in (401, 403)


# ---------- Config endpoints ----------
class TestBillingConfig:
    def test_get_config_default_new_schema(self, admin_session):
        """SI aucune config en DB, tiers par defaut = annual_fee 500/1200/2500."""
        r = admin_session.get(f"{API}/admin/billing/config")
        assert r.status_code == 200
        data = r.json()
        assert data["default_frequency"] == "annual"
        assert data["vat_rate"] == 21.0
        tiers = data["tiers"]
        assert len(tiers) == 3
        # Check new schema : chaque tier a annual_fee
        assert tiers[0]["min_lots"] == 1
        assert tiers[0]["max_lots"] == 50
        assert tiers[0]["annual_fee"] == 500.0
        assert tiers[1]["min_lots"] == 51
        assert tiers[1]["max_lots"] == 200
        assert tiers[1]["annual_fee"] == 1200.0
        assert tiers[2]["min_lots"] == 201
        assert tiers[2]["max_lots"] is None
        assert tiers[2]["annual_fee"] == 2500.0

    def test_put_config_annual_fee_persist(self, admin_session):
        payload = {
            "tiers": [
                {"min_lots": 1, "max_lots": 50, "annual_fee": 500.0},
                {"min_lots": 51, "max_lots": 200, "annual_fee": 1200.0},
                {"min_lots": 201, "max_lots": None, "annual_fee": 2500.0},
            ],
            "vat_rate": 21.0,
            "default_frequency": "annual",
            "admin_info": {"name": "TEST Cabinet iter93du", "iban": "BE00 0000 0000 0000", "vat_number": "BE0000000000"},
        }
        r = admin_session.put(f"{API}/admin/billing/config", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True

        # verify persistence with annual_fee schema
        r2 = admin_session.get(f"{API}/admin/billing/config")
        assert r2.status_code == 200
        cfg = r2.json()
        assert cfg["vat_rate"] == 21.0
        assert len(cfg["tiers"]) == 3
        for i, expected_fee in enumerate([500.0, 1200.0, 2500.0]):
            assert cfg["tiers"][i]["annual_fee"] == expected_fee
        assert cfg["admin_info"].get("name") == "TEST Cabinet iter93du"


# ---------- Syndics list & pricing ----------
class TestSyndicsListing:
    def test_list_syndics_structure(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data and "totals" in data and "config" in data
        for k in ("annual_ht", "period_ht", "period_ttc", "lot_count"):
            assert k in data["totals"]
        if data["rows"]:
            row = data["rows"][0]
            for k in ("syndic_user_id", "syndic_name", "syndic_email", "acp_count",
                     "lot_count", "frequency", "pricing_mode", "annual_amount_ht",
                     "period_amount_ht", "period_amount_tva", "period_amount_ttc",
                     "negotiated_flat_fee", "notes"):
                assert k in row, f"missing key {k}"

    def test_pricing_flat_fee_no_multiplication(self, admin_session):
        """iter93du : pour pricing_mode=tranches, annual_amount_ht = forfait fixe
        (pas de multiplication par lot_count).
        """
        r = admin_session.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 200
        data = r.json()
        tiers = data["config"]["tiers"]
        for row in data["rows"]:
            if row["pricing_mode"] != "tranches":
                continue
            expected = _compute_tiered_amount(row["lot_count"], tiers)
            assert abs(row["annual_amount_ht"] - expected) < 0.01, (
                f"syndic={row['syndic_name']} lots={row['lot_count']}"
                f" expected={expected} got={row['annual_amount_ht']}"
            )
            # Verifier que l'annual_amount est EGAL a un forfait (pas lots*fee)
            if row["lot_count"] > 0:
                fees_set = {t.get("annual_fee") for t in tiers if t.get("annual_fee") is not None}
                fees_set.add(0.0)
                assert row["annual_amount_ht"] in fees_set, (
                    f"annual_amount_ht={row['annual_amount_ht']} should be a fixed tier fee "
                    f"in {fees_set} (lot_count={row['lot_count']})"
                )

    def test_88_lots_gives_1200(self, admin_session):
        """Cas metier explicite : si un syndic a 88 lots (tranche 2) -> 1200€."""
        r = admin_session.get(f"{API}/admin/billing/syndics")
        data = r.json()
        matches = [row for row in data["rows"]
                   if row["lot_count"] == 88 and row["pricing_mode"] == "tranches"]
        if not matches:
            pytest.skip("Aucun syndic avec exactement 88 lots (mode tranches)")
        for row in matches:
            assert row["annual_amount_ht"] == 1200.0


# ---------- PUT /syndics/{id} : negocie + frequency (regression) ----------
class TestSyndicBillingUpdate:
    @pytest.fixture(scope="class")
    def target_syndic_id(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/syndics")
        rows = r.json()["rows"]
        if not rows:
            pytest.skip("No syndic in DB")
        return rows[0]["syndic_user_id"]

    def test_set_negotiated_flat_fee(self, admin_session, target_syndic_id):
        r = admin_session.put(
            f"{API}/admin/billing/syndics/{target_syndic_id}",
            json={"negotiated_flat_fee": 999.99, "frequency": "annual"},
        )
        assert r.status_code == 200, r.text
        rr = admin_session.get(f"{API}/admin/billing/syndics")
        row = next(r for r in rr.json()["rows"] if r["syndic_user_id"] == target_syndic_id)
        assert row["pricing_mode"] == "negocie"
        assert abs(row["annual_amount_ht"] - 999.99) < 0.01
        assert abs(row["period_amount_ht"] - 999.99) < 0.01

    def test_quarterly_frequency_divides_by_4(self, admin_session, target_syndic_id):
        r = admin_session.put(
            f"{API}/admin/billing/syndics/{target_syndic_id}",
            json={"negotiated_flat_fee": 999.99, "frequency": "quarterly"},
        )
        assert r.status_code == 200
        rr = admin_session.get(f"{API}/admin/billing/syndics")
        row = next(r for r in rr.json()["rows"] if r["syndic_user_id"] == target_syndic_id)
        assert row["frequency"] == "quarterly"
        assert abs(row["period_amount_ht"] - round(999.99 / 4.0, 2)) < 0.01

    def test_clear_negotiated_fallback_to_tiers(self, admin_session, target_syndic_id):
        r = admin_session.put(
            f"{API}/admin/billing/syndics/{target_syndic_id}",
            json={"negotiated_flat_fee": None, "frequency": "annual"},
        )
        assert r.status_code == 200
        rr = admin_session.get(f"{API}/admin/billing/syndics")
        row = next(r for r in rr.json()["rows"] if r["syndic_user_id"] == target_syndic_id)
        assert row["pricing_mode"] == "tranches", (
            f"Expected pricing_mode='tranches' after clearing, got {row['pricing_mode']}"
        )


# ---------- PDF invoice ----------
class TestInvoicePDF:
    def test_generate_pdf(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/syndics")
        rows = r.json()["rows"]
        if not rows:
            pytest.skip("No syndic")
        sid = rows[0]["syndic_user_id"]
        rp = admin_session.get(f"{API}/admin/billing/invoice/{sid}/pdf?period_label=2026-Q3")
        assert rp.status_code == 200
        assert rp.headers.get("content-type", "").startswith("application/pdf")
        assert "attachment" in rp.headers.get("content-disposition", "").lower()
        assert len(rp.content) > 1000
        assert rp.content[:4] == b"%PDF"

    def test_pdf_description_mentions_eur_per_an(self, admin_session):
        """iter93du : la description PDF doit mentionner 'EUR/an' (annuel fixe),
        pas 'EUR/lot' (ancien modele degressif).
        """
        r = admin_session.get(f"{API}/admin/billing/syndics")
        rows = r.json()["rows"]
        # trouver un syndic en pricing_mode=tranches (pour que la desc bareme apparaisse)
        target = next((row for row in rows if row["pricing_mode"] == "tranches"), None)
        if not target:
            pytest.skip("Aucun syndic en mode tranches")
        sid = target["syndic_user_id"]
        rp = admin_session.get(f"{API}/admin/billing/invoice/{sid}/pdf?period_label=2026")
        assert rp.status_code == 200
        import io as _io
        try:
            import pypdf
            reader = pypdf.PdfReader(_io.BytesIO(rp.content))
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception as e:
            pytest.skip(f"pypdf indisponible: {e}")
        assert "EUR/an" in text, f"PDF doit contenir 'EUR/an'. Text extrait:\n{text[:2000]}"
        assert "EUR/lot" not in text, "PDF ne doit PLUS contenir 'EUR/lot' (ancien modele)"

    def test_pdf_unknown_syndic_404(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/invoice/deadbeef/pdf")
        assert r.status_code == 404
