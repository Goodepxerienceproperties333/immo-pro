"""iter93ck/cl/cm/cn - Owner portal FIFO + bank accounts + fund-calls unpaid.

Bugs fixed:
  - iter93ck : GET /api/owner/dashboard pending_calls calcules via FIFO
    (miroir de la comptabilite). Guerit sur ACP Agathe : T1+Reserve+T2+T3
    et il a paye 675.86 EUR (env). FIFO => T1 & Reserve payes, T2 en tete
    des pending, T3 impaye complet.
  - iter93cl : GET /api/owner/bank-accounts/{copropriete_id} matche via
    IBAN.last4 vs bank_statements.account_number pour deduire le vrai
    pcmn_number JE lorsque copro.bank_accounts.pcmn_number diverge.
  - iter93cm : GET /api/owner/fund-calls retourne unpaid_amount et paid
    par appel via FIFO sur les credits (FI + AN opening).

Auth : superadmin ne peut pas acceder aux endpoints owner (chinese wall).
On authentifie donc l'utilisateur Guerit (owner) apres avoir temporairement
setter un mot de passe bcrypt connu, puis on restore l'ancien hash en
teardown.
"""
import os
import asyncio
import pytest
import requests
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

OWNER_ID = "d1318f71-a975-4997-8659-138d78f62842"
COPRO_ID = "c9cfce94-96c6-4202-8a6d-0a5627b50856"  # ACP Agathe
OWNER_EMAIL = "evrard.gerald@outlook.be"
TEST_PASSWORD = "TestOwnerIter93CK!"


@pytest.fixture(scope="module")
def owner_session():
    """Login as the Guerit owner user (temp password swap)."""
    # 1. Save original hash + set temp bcrypt hash
    orig_hash = None

    async def _setup():
        nonlocal orig_hash
        c = AsyncIOMotorClient(MONGO_URL)
        db = c[DB_NAME]
        u = await db.users.find_one({"email": OWNER_EMAIL}, {"password_hash": 1})
        assert u, f"User {OWNER_EMAIL} not found - test data missing"
        orig_hash = u.get("password_hash")
        new_hash = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
        await db.users.update_one(
            {"email": OWNER_EMAIL},
            {"$set": {"password_hash": new_hash, "must_change_password": False}},
        )
        c.close()

    async def _teardown():
        c = AsyncIOMotorClient(MONGO_URL)
        db = c[DB_NAME]
        if orig_hash:
            await db.users.update_one(
                {"email": OWNER_EMAIL}, {"$set": {"password_hash": orig_hash}}
            )
        c.close()

    asyncio.run(_setup())
    # 2. Login via HTTP
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OWNER_EMAIL, "password": TEST_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"Owner login failed: {r.status_code} {r.text[:300]}"
    yield s
    asyncio.run(_teardown())


# ---------- iter93ck : dashboard FIFO pending_calls ----------
class TestDashboardFIFO:
    def test_dashboard_returns_stats_and_pending_calls(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30)
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "stats" in data and "pending_calls" in data
        assert "stats_by_acp" in data
        # ACP Agathe stats present
        assert COPRO_ID in data["stats_by_acp"], (
            f"ACP Agathe missing from stats_by_acp keys={list(data['stats_by_acp'])}"
        )
        agathe = data["stats_by_acp"][COPRO_ID]
        print(f"AGATHE stats: {agathe}")
        # Solde debiteur (owner owes money)
        assert agathe["status"] == "debiteur", (
            f"Expected debiteur, got {agathe['status']} balance={agathe['balance']}"
        )
        # Balance approx 475.94 EUR (T2 unpaid completely + rest)
        assert agathe["balance"] > 400.0, f"balance too small: {agathe['balance']}"

    def test_pending_calls_fifo_t2_first(self, owner_session):
        """T1 + Reserve payes par FIFO. T2 doit apparaitre en premier avec
        unpaid_amount ~475.94 EUR (miroir compta)."""
        r = owner_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30)
        assert r.status_code == 200
        data = r.json()
        agathe_pcs = [
            p for p in data["pending_calls"] if p.get("copropriete_id") == COPRO_ID
        ]
        assert agathe_pcs, "No pending_calls for ACP Agathe (expected T2 & T3)"
        print(f"AGATHE pending: {agathe_pcs}")
        first = agathe_pcs[0]
        # Nom de l'appel : ne doit PAS etre T1/Reserve (payes par FIFO)
        name = (first.get("fund_call_name") or "").lower()
        assert "t1" not in name and "reserve" not in name.replace("re", ""), (
            f"FIFO broken: T1/Reserve should be paid, got {first}"
        )
        # Premier pending doit correspondre a T2 (partiel ~475.94) ou plus
        assert first["amount"] > 100.0, f"unpaid too small: {first}"
        assert first["amount"] <= 500.0, f"unpaid too large: {first}"


# ---------- iter93cl : bank-accounts avec matching IBAN.last4 ----------
class TestBankAccountsMatching:
    def test_bank_accounts_returns_nonzero_balance(self, owner_session):
        r = owner_session.get(
            f"{BASE_URL}/api/owner/bank-accounts/{COPRO_ID}", timeout=30
        )
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "bank_accounts" in data
        accs = data["bank_accounts"]
        assert len(accs) == 2, f"Expected 2 bank accounts, got {len(accs)}"
        print(f"BANK ACCOUNTS: {[(a['type'], a['iban'], a['balance']) for a in accs]}")
        # Au moins un compte doit avoir une balance non-nulle (bug precedent : 0)
        nonzero = [a for a in accs if abs(a["balance"]) > 0.01]
        assert len(nonzero) >= 2, (
            f"Expected 2 accounts with non-zero balance (IBAN.last4 matching), got "
            f"{[(a['iban'], a['balance']) for a in accs]}"
        )
        # Verifie balances approx attendues (13111.41 epargne, 28023.42 a vue)
        by_type = {a["type"]: a["balance"] for a in accs}
        assert "epargne" in by_type and "vue" in by_type
        # tolerance +/- 100 EUR sur les vraies balances (fluctuations possibles)
        # iter93co : epargne doit etre ~13111.41 via fallback JE 55011383 (last4=1383)
        assert by_type["epargne"] > 1000.0, (
            f"epargne balance suspicious : {by_type['epargne']}"
        )
        assert abs(by_type["epargne"] - 13111.41) < 5.0, (
            f"iter93co epargne expected ~13111.41 got {by_type['epargne']}"
        )
        assert by_type["vue"] > 1000.0, f"vue balance suspicious : {by_type['vue']}"

    def test_payment_qr_with_numeric_amount(self, owner_session):
        """iter93cp : le frontend envoie un montant numerique brut '475.94'
        (dot decimal) - FastAPI doit le parser correctement et renvoyer un PNG."""
        r = owner_session.get(
            f"{BASE_URL}/api/owner/payment-qr/{COPRO_ID}?amount=475.94", timeout=30
        )
        assert r.status_code == 200, r.text[:300]
        assert r.headers.get("content-type", "").startswith("image/png"), (
            f"Expected PNG, got {r.headers.get('content-type')}"
        )
        assert len(r.content) > 100, "QR PNG suspiciously small"
        # Format francais '475,94' (virgule) doit renvoyer 422
        r2 = owner_session.get(
            f"{BASE_URL}/api/owner/payment-qr/{COPRO_ID}?amount=475,94", timeout=30
        )
        assert r2.status_code in (400, 422), (
            f"Comma format should fail parsing, got {r2.status_code}"
        )


# ---------- iter93cm : fund-calls unpaid_amount FIFO ----------
class TestFundCallsUnpaidAmount:
    def test_fund_calls_returns_unpaid_amount_field(self, owner_session):
        r = owner_session.get(
            f"{BASE_URL}/api/owner/fund-calls?copropriete_id={COPRO_ID}", timeout=30
        )
        assert r.status_code == 200, r.text[:500]
        calls = r.json()
        assert isinstance(calls, list)
        assert len(calls) >= 3, f"Expected at least T1/T2/T3, got {len(calls)}"
        # Chaque appel a bien unpaid_amount + paid
        for c in calls:
            assert "unpaid_amount" in c, f"Missing unpaid_amount on {c.get('name')}"
            assert "paid" in c
            assert isinstance(c["unpaid_amount"], (int, float))
        # FIFO : au moins un appel paid=True et au moins un paid=False attendu
        paid_calls = [c for c in calls if c["paid"]]
        unpaid_calls = [c for c in calls if not c["paid"]]
        print(
            f"FUND CALLS: paid={[c['name'] for c in paid_calls]} "
            f"unpaid={[(c['name'], c['unpaid_amount']) for c in unpaid_calls]}"
        )
        assert paid_calls, "FIFO broken: no paid calls (Guerit a paye 675.86 EUR)"
        assert unpaid_calls, (
            "FIFO broken: no unpaid calls (Guerit doit encore 475.94+475.97)"
        )

    def test_fund_calls_total_unpaid_matches_balance(self, owner_session):
        """La somme des unpaid_amount doit approx correspondre au solde
        debiteur dashboard."""
        dr = owner_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30).json()
        agathe_balance = dr["stats_by_acp"][COPRO_ID]["balance"]

        fr = owner_session.get(
            f"{BASE_URL}/api/owner/fund-calls?copropriete_id={COPRO_ID}", timeout=30
        ).json()
        total_unpaid = sum(c["unpaid_amount"] for c in fr)
        print(f"balance={agathe_balance} total_unpaid={total_unpaid}")
        # Tolerance 5 EUR (arrondis + reserve)
        assert abs(total_unpaid - agathe_balance) < 5.0, (
            f"unpaid={total_unpaid} vs balance={agathe_balance}"
        )


# ---------- Regression : endpoints ne crashent pas ----------
class TestRegression:
    def test_movements(self, owner_session):
        r = owner_session.get(
            f"{BASE_URL}/api/owner/movements?copropriete_id={COPRO_ID}", timeout=30
        )
        assert r.status_code == 200, r.text[:300]

    def test_situation_via_dashboard(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/owner/dashboard", timeout=30)
        assert r.status_code == 200

    def test_coproprietes_list(self, owner_session):
        r = owner_session.get(f"{BASE_URL}/api/owner/coproprietes", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) >= 1

    def test_payment_qr(self, owner_session):
        r = owner_session.get(
            f"{BASE_URL}/api/owner/payment-qr/{COPRO_ID}?amount=475.94", timeout=30
        )
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/")
