"""Iteration 9 backend tests: P0 Auth middleware systematique.

The middleware protects ALL /api/* routes EXCEPT:
- POST /api/auth/login, /api/auth/register, /api/auth/refresh, /api/auth/logout
- OPTIONS preflight requests (CORS)

It checks a valid 'access_token' cookie OR an 'Authorization: Bearer <token>' header.
Returns 401 with proper detail messages for missing / invalid / expired tokens.
"""
import os
import time
import jwt
import pytest
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def jwt_secret():
    """Read JWT secret from backend .env (same one server uses)."""
    secret = None
    env_path = "/app/backend/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("JWT_SECRET="):
                    secret = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not secret:
        pytest.skip("JWT_SECRET not found in backend .env")
    return secret


@pytest.fixture(scope="session")
def auth_session():
    """Logged-in session with valid access_token cookie."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text}")
    assert "access_token" in s.cookies
    return s


@pytest.fixture(scope="session")
def access_token(auth_session):
    return auth_session.cookies.get("access_token")


@pytest.fixture(scope="session")
def admin_user_id(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200, r.text
    body = r.json()
    # /me returns Mongo "_id" (inconsistency vs /login which returns "id")
    uid = body.get("id") or body.get("_id")
    assert uid, f"No user id in /me response: {body}"
    return uid


# ---------- Tests: protected routes return 401 without auth ----------
PROTECTED_GET = [
    "/api/auth/me",
    "/api/owners",
    "/api/coproprietes",
    "/api/accounting/pcmn",
    "/api/reports/bilan",
    "/api/reports/grand-livre",
    "/api/lots",
    "/api/invoices",
    "/api/banking/statements",
    "/api/banking/transactions",
    "/api/documents",
    "/api/documents/categories",
    "/api/fund-calls",
    "/api/fiscal/years",
    "/api/distribution-keys",
    "/api/suppliers",
]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_get_without_cookie_returns_401(path):
    r = requests.get(f"{BASE_URL}{path}")
    assert r.status_code == 401, f"{path} should be protected but got {r.status_code} {r.text[:200]}"
    body = r.json()
    assert "detail" in body
    assert body["detail"] == "Not authenticated", f"{path} unexpected detail: {body}"


def test_destructive_admin_seed_without_cookie_returns_401():
    """CRITICAL: destructive endpoint must NEVER be exposed without auth."""
    r = requests.post(f"{BASE_URL}/api/admin/demo/seed", json={})
    assert r.status_code == 401, f"demo/seed exposed without auth! got {r.status_code}: {r.text[:200]}"


def test_post_coproprietes_without_cookie_returns_401():
    r = requests.post(f"{BASE_URL}/api/coproprietes", json={"name": "Hack"})
    assert r.status_code == 401, r.text[:200]


def test_put_owner_without_cookie_returns_401():
    r = requests.put(f"{BASE_URL}/api/owners/507f1f77bcf86cd799439011", json={"name": "x"})
    assert r.status_code == 401, r.text[:200]


def test_delete_lot_without_cookie_returns_401():
    r = requests.delete(f"{BASE_URL}/api/lots/507f1f77bcf86cd799439011")
    assert r.status_code == 401, r.text[:200]


# ---------- Tests: token validity ----------
def test_expired_token_returns_401_token_expired(jwt_secret, admin_user_id):
    expired_payload = {
        "sub": admin_user_id,
        "email": ADMIN_EMAIL,
        "type": "access",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
    }
    expired_token = jwt.encode(expired_payload, jwt_secret, algorithm="HS256")
    r = requests.get(f"{BASE_URL}/api/owners",
                     cookies={"access_token": expired_token})
    assert r.status_code == 401
    assert r.json().get("detail") == "Token expired", r.json()


def test_invalid_random_token_returns_401_invalid_token():
    r = requests.get(f"{BASE_URL}/api/owners",
                     cookies={"access_token": "not.a.valid.jwt.token.at.all"})
    assert r.status_code == 401
    assert r.json().get("detail") == "Invalid token", r.json()


def test_garbage_token_returns_401_invalid_token():
    r = requests.get(f"{BASE_URL}/api/owners",
                     cookies={"access_token": "garbagestring"})
    assert r.status_code == 401
    assert r.json().get("detail") == "Invalid token", r.json()


def test_refresh_token_used_as_access_returns_401_invalid_type(jwt_secret, admin_user_id):
    """A refresh-type token must NOT be accepted as access token."""
    refresh_payload = {
        "sub": admin_user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
        "type": "refresh",
    }
    refresh_tok = jwt.encode(refresh_payload, jwt_secret, algorithm="HS256")
    r = requests.get(f"{BASE_URL}/api/owners",
                     cookies={"access_token": refresh_tok})
    assert r.status_code == 401
    assert r.json().get("detail") == "Invalid token type", r.json()


def test_token_signed_with_wrong_secret_returns_401(admin_user_id):
    bad_payload = {
        "sub": admin_user_id,
        "email": ADMIN_EMAIL,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    bad_tok = jwt.encode(bad_payload, "wrong-secret-for-test", algorithm="HS256")
    r = requests.get(f"{BASE_URL}/api/owners",
                     cookies={"access_token": bad_tok})
    assert r.status_code == 401
    assert r.json().get("detail") == "Invalid token", r.json()


# ---------- Tests: exempt paths work without cookie ----------
def test_login_without_cookie_works():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    assert "access_token" in r.cookies


def test_login_with_bad_credentials_returns_401_not_middleware_401():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert r.status_code == 401
    # Must be the LOGIN error, not the middleware error
    assert r.json().get("detail") == "Identifiants invalides", r.json()


def test_register_without_cookie_reaches_handler():
    """Register must be reachable without auth. Duplicate email → 400, never 401."""
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": ADMIN_EMAIL, "password": "x", "name": "x"})
    assert r.status_code != 401, "register should not be blocked by auth middleware"
    # Existing admin → 400
    assert r.status_code == 400, r.text


def test_register_new_user_without_cookie_works():
    ts = int(time.time())
    email = f"test_iter9_{ts}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": "Test1234!", "name": "Test Iter9"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("email") == email


def test_logout_without_cookie_works():
    r = requests.post(f"{BASE_URL}/api/auth/logout")
    assert r.status_code == 200, r.text


def test_refresh_without_cookie_returns_401_no_refresh_token():
    """refresh is exempt from middleware but the handler itself requires refresh_token cookie."""
    r = requests.post(f"{BASE_URL}/api/auth/refresh")
    assert r.status_code == 401
    # This is the handler's own 401, NOT the middleware's "Not authenticated"
    assert r.json().get("detail") == "No refresh token", r.json()


# ---------- Tests: valid auth allows access ----------
def test_valid_cookie_allows_owners(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/owners")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_valid_cookie_allows_coproprietes(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/coproprietes")
    assert r.status_code == 200, r.text


def test_valid_cookie_allows_pcmn(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/accounting/pcmn")
    # 200 (list) or 400 (no ACP selected) - must NOT be 401
    assert r.status_code != 401, r.text


def test_valid_cookie_allows_documents_categories(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/documents/categories")
    assert r.status_code != 401, r.text


def test_valid_cookie_allows_auth_me(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body.get("email") == ADMIN_EMAIL


# ---------- Tests: Bearer token alternative ----------
def test_bearer_token_in_header_works(access_token):
    """Bearer header should be accepted as alternative to cookie."""
    r = requests.get(f"{BASE_URL}/api/owners",
                     headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200, r.text


def test_bearer_token_invalid_returns_401():
    r = requests.get(f"{BASE_URL}/api/owners",
                     headers={"Authorization": "Bearer bogus.token.here"})
    assert r.status_code == 401
    assert r.json().get("detail") == "Invalid token"


def test_bearer_token_allows_auth_me(access_token):
    r = requests.get(f"{BASE_URL}/api/auth/me",
                     headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    assert r.json().get("email") == ADMIN_EMAIL


# ---------- Tests: OPTIONS preflight passes ----------
def test_options_preflight_on_protected_route_passes():
    """OPTIONS must bypass auth (CORS preflight)."""
    r = requests.options(
        f"{BASE_URL}/api/owners",
        headers={
            "Origin": os.environ.get("FRONTEND_URL", "http://localhost:3000"),
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code in (200, 204), f"OPTIONS preflight failed: {r.status_code} {r.text[:200]}"


def test_options_preflight_on_login_passes():
    r = requests.options(
        f"{BASE_URL}/api/auth/login",
        headers={
            "Origin": os.environ.get("FRONTEND_URL", "http://localhost:3000"),
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code in (200, 204), r.text[:200]


# ---------- Regression: a couple of write operations still work with valid auth ----------
def test_regression_create_and_delete_acp_with_auth(auth_session):
    ts = int(time.time())
    r = auth_session.post(f"{BASE_URL}/api/coproprietes", json={
        "name": f"TEST_iter9_{ts}",
        "address": "Rue test",
        "postal_code": "1000",
        "city": "Bruxelles",
        "bank_accounts": [
            {"iban": "BE99000000000000", "bic": "TESTBEBB",
             "account_type": "vue", "is_default": True, "label": "Test"}
        ],
    })
    assert r.status_code == 200, r.text
    acp_id = r.json()["id"]
    # cleanup
    rd = auth_session.delete(f"{BASE_URL}/api/coproprietes/{acp_id}")
    assert rd.status_code in (200, 204), rd.text


def test_non_api_path_is_not_blocked():
    """Middleware must only protect /api/* paths. Docs/openapi should still be reachable."""
    r = requests.get(f"{BASE_URL}/docs")
    # Could be 200 (docs) or 404 if disabled - must NOT be 401
    assert r.status_code != 401, "non-/api path should not be auth-protected"
