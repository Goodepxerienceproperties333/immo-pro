"""iter93dt - Tests backend Facturation Admin (superadmin -> syndics)."""
import os
import sys
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback to frontend .env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

API = f"{BASE_URL}/api"

sys.path.insert(0, "/app/backend")
from routes.billing_admin import _compute_tiered_amount  # noqa: E402

DEFAULT_TIERS = [
    {"min_lots": 1, "max_lots": 50, "price_per_lot": 5.0},
    {"min_lots": 51, "max_lots": 200, "price_per_lot": 4.0},
    {"min_lots": 201, "max_lots": None, "price_per_lot": 3.0},
]


# ---------- Unit tests: compute_tiered_amount ----------
@pytest.mark.parametrize("lots,expected", [
    (0, 0.0),
    (30, 150.0),
    (50, 250.0),
    (88, 402.0),  # 50*5 + 38*4
    (200, 850.0),  # 50*5 + 150*4
    (250, 1000.0),  # 50*5 + 150*4 + 50*3
])
def test_compute_tiered_amount(lots, expected):
    assert _compute_tiered_amount(lots, DEFAULT_TIERS) == expected


def test_compute_tiered_empty_tiers():
    assert _compute_tiered_amount(100, []) == 0.0


# ---------- HTTP fixtures ----------
@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="session")
def syndic_session():
    """Create a fresh temp syndic user via /auth/register (role=syndic)."""
    s = requests.Session()
    email = f"TEST_syndic_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "TestSyndic123!"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd, "name": "TEST Syndic iter93dt",
    })
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    # register may auto-login (cookie set) — ensure by login explicitly
    s2 = requests.Session()
    r = s2.post(f"{API}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"syndic login: {r.status_code} {r.text}"
    return s2, email


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
        r = s.put(f"{API}/admin/billing/config", json={"tiers": [], "vat_rate": 21, "default_frequency": "annual", "admin_info": {}})
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
    def test_get_config_default(self, admin_session):
        # Ensure no config in DB (best-effort: not deleting to avoid impacting UI).
        r = admin_session.get(f"{API}/admin/billing/config")
        assert r.status_code == 200
        data = r.json()
        assert "tiers" in data and isinstance(data["tiers"], list)
        assert "vat_rate" in data
        assert "default_frequency" in data
        assert "admin_info" in data
        # If no DB record OR default returned, we expect at least 3 tiers with values 5/4/3
        # (this is the seeded default in code path).
        # Since PUT may have been done previously, do not enforce exact values here,
        # only structure.
        assert data["default_frequency"] in ("monthly", "quarterly", "annual")

    def test_put_config_persist(self, admin_session):
        payload = {
            "tiers": [
                {"min_lots": 1, "max_lots": 50, "price_per_lot": 5.0},
                {"min_lots": 51, "max_lots": 200, "price_per_lot": 4.0},
                {"min_lots": 201, "max_lots": None, "price_per_lot": 3.0},
            ],
            "vat_rate": 21.0,
            "default_frequency": "annual",
            "admin_info": {"name": "TEST Cabinet", "iban": "BE00 0000 0000 0000", "vat_number": "BE0000000000"},
        }
        r = admin_session.put(f"{API}/admin/billing/config", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("config", {}).get("vat_rate") == 21.0

        # verify persistence
        r2 = admin_session.get(f"{API}/admin/billing/config")
        assert r2.status_code == 200
        cfg = r2.json()
        assert cfg["vat_rate"] == 21.0
        assert cfg["default_frequency"] == "annual"
        assert len(cfg["tiers"]) == 3
        assert cfg["admin_info"].get("name") == "TEST Cabinet"


# ---------- Syndics list & pricing ----------
class TestSyndicsListing:
    def test_list_syndics_structure(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 200
        data = r.json()
        assert "rows" in data and "totals" in data and "config" in data
        assert isinstance(data["rows"], list)
        for k in ("annual_ht", "period_ht", "period_ttc", "lot_count"):
            assert k in data["totals"]
        # If any rows exist, check required keys
        if data["rows"]:
            row = data["rows"][0]
            for k in ("syndic_user_id", "syndic_name", "syndic_email", "acp_count",
                     "lot_count", "frequency", "pricing_mode", "annual_amount_ht",
                     "period_amount_ht", "period_amount_tva", "period_amount_ttc",
                     "negotiated_flat_fee", "notes"):
                assert k in row, f"missing key {k} in row"

    def test_pricing_calculation_consistency(self, admin_session):
        """For each row in pricing_mode=tranches, annual = _compute_tiered_amount(lot_count, cfg.tiers)."""
        r = admin_session.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 200
        data = r.json()
        tiers = data["config"]["tiers"]
        for row in data["rows"]:
            if row["pricing_mode"] == "tranches":
                expected = _compute_tiered_amount(row["lot_count"], tiers)
                assert abs(row["annual_amount_ht"] - expected) < 0.01, (
                    f"syndic {row['syndic_name']}: lots={row['lot_count']}"
                    f" expected {expected}, got {row['annual_amount_ht']}"
                )


# ---------- PUT /syndics/{id} : negocie + frequency ----------
class TestSyndicBillingUpdate:
    @pytest.fixture(scope="class")
    def target_syndic_id(self, admin_session):
        r = admin_session.get(f"{API}/admin/billing/syndics")
        assert r.status_code == 200
        rows = r.json()["rows"]
        # pick any syndic (does not need lots)
        if not rows:
            pytest.skip("No syndic in DB")
        return rows[0]["syndic_user_id"]

    def test_set_negotiated_flat_fee(self, admin_session, target_syndic_id):
        r = admin_session.put(
            f"{API}/admin/billing/syndics/{target_syndic_id}",
            json={"negotiated_flat_fee": 999.99, "frequency": "annual"},
        )
        assert r.status_code == 200, r.text
        # verify effect via GET syndics
        rr = admin_session.get(f"{API}/admin/billing/syndics")
        row = next(r for r in rr.json()["rows"] if r["syndic_user_id"] == target_syndic_id)
        assert row["pricing_mode"] == "negocie"
        assert abs(row["annual_amount_ht"] - 999.99) < 0.01
        assert abs(row["period_amount_ht"] - 999.99) < 0.01  # annual

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
        # Repository accepts negotiated_flat_fee=null? The pydantic model uses Optional and
        # exclude_none=True on update -> null will be dropped. We must use $unset semantics.
        # Endpoint contract per request: "PUT ... with negotiated_flat_fee=null retire le negocie".
        r = admin_session.put(
            f"{API}/admin/billing/syndics/{target_syndic_id}",
            json={"negotiated_flat_fee": None, "frequency": "annual"},
        )
        assert r.status_code == 200
        rr = admin_session.get(f"{API}/admin/billing/syndics")
        row = next(r for r in rr.json()["rows"] if r["syndic_user_id"] == target_syndic_id)
        assert row["pricing_mode"] == "tranches", (
            f"Expected pricing_mode='tranches' after clearing negotiated_flat_fee=null, "
            f"got {row['pricing_mode']} (negotiated_flat_fee={row['negotiated_flat_fee']}). "
            "Backend likely uses exclude_none=True and does NOT unset the field."
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

    def test_pdf_unknown_syndic_404(self, admin_session):
        # Bad ObjectId format triggers except -> 404
        r = admin_session.get(f"{API}/admin/billing/invoice/deadbeef/pdf")
        assert r.status_code == 404
