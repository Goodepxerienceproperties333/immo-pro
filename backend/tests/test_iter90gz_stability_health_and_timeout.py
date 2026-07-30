"""iter90gz — Stabilite proactive : health checks, timeout middleware, error logging.

Contexte utilisateur (Feb 2026) :
  "je veux toujours maintenir la stabilite donc trouve des solutions en vue
  de permettre cela"

Fix (iter90gz) :
  1. Endpoint /api/health (liveness) + /api/health/ready (readiness) publics.
     Utilises par K8s / Cloudflare / UptimeRobot.
  2. RequestTimeoutMiddleware : tue toute requete > 60s avec un 504 propre
     avant que Cloudflare ne timeout a 100s. Certains paths (imports, PDFs
     de masse) sont exemptes.
  3. Logging structure des 5xx vers /var/log/supervisor/backend_errors.log
     avec contexte (method, path, user_id, status, duration).
  4. Frontend ErrorBoundary global : rattrape les crashs React qui
     produiraient un ecran blanc.
"""
import os
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://optipro-parser-fix.preview.emergentagent.com",
).rstrip("/")


def test_iter90gz_liveness_endpoint_public_and_fast():
    """/api/health : accessible sans auth, retourne 200 avec un JSON minimal."""
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert data.get("status") == "ok"
    assert data.get("service") == "nextge-copro"
    assert "timestamp" in data


def test_iter90gz_liveness_alias_endpoint():
    """/api/health/live : meme comportement que /api/health (alias explicite)."""
    r = requests.get(f"{BASE_URL}/api/health/live", timeout=10)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_iter90gz_readiness_endpoint_pings_mongo():
    """/api/health/ready : verifie MongoDB, expose la latence."""
    r = requests.get(f"{BASE_URL}/api/health/ready", timeout=10)
    assert r.status_code in (200, 503)
    data = r.json()
    assert "mongodb" in data
    mongo = data["mongodb"]
    assert "ok" in mongo
    assert "latency_ms" in mongo
    # En preview la BD locale doit repondre en < 500 ms
    if r.status_code == 200:
        assert mongo["ok"] is True
        assert mongo["latency_ms"] < 500


def test_iter90gz_health_does_not_require_auth():
    """Les probes doivent etre publics (K8s/Cloudflare n'ont pas de cookie)."""
    for path in ("/api/health", "/api/health/live", "/api/health/ready"):
        r = requests.get(f"{BASE_URL}{path}", timeout=10)
        assert r.status_code in (200, 503), f"{path} refuse l'acces public: {r.status_code}"


def test_iter90gz_request_timeout_middleware_registered():
    """Le middleware RequestTimeoutMiddleware doit etre enregistre dans server.py."""
    with open("/app/backend/server.py") as f:
        content = f.read()
    assert "class RequestTimeoutMiddleware" in content
    assert "app.add_middleware(RequestTimeoutMiddleware)" in content
    assert "REQUEST_TIMEOUT_SEC" in content
    # SkipPrefixes pour les imports lourds
    assert "_REQUEST_TIMEOUT_SKIP_PREFIXES" in content
    assert "/api/import-wizard/" in content


def test_iter90gz_error_logger_configured():
    """Verifie que le RotatingFileHandler est configure dans server.py."""
    with open("/app/backend/server.py") as f:
        content = f.read()
    assert "RotatingFileHandler" in content
    assert "backend_errors" in content
    assert "_log_error_context" in content
    # Le logger doit etre appele dans SafeResponseMiddleware
    assert content.count("_log_error_context") >= 3


def test_iter90gz_error_boundary_frontend_component_exists():
    """Le composant ErrorBoundary doit etre defini et wrap AppRoutes dans App.js."""
    with open("/app/frontend/src/components/ErrorBoundary.js") as f:
        eb = f.read()
    assert "class ErrorBoundary" in eb
    assert "getDerivedStateFromError" in eb
    assert "componentDidCatch" in eb
    assert 'data-testid="global-error-boundary"' in eb
    assert "Recharger la page" in eb

    with open("/app/frontend/src/App.js") as f:
        app = f.read()
    assert 'import ErrorBoundary from "@/components/ErrorBoundary"' in app
    assert "<ErrorBoundary>" in app
    assert "</ErrorBoundary>" in app


def test_iter90gz_health_ready_returns_json_content_type():
    """Cloudflare / monitoring : content-type doit etre application/json."""
    r = requests.get(f"{BASE_URL}/api/health/ready", timeout=10)
    ct = r.headers.get("content-type", "").lower()
    assert ct.startswith("application/json"), ct


def test_iter90gz_expenses_page_guards_against_missing_copro():
    """ExpensesPage.load() doit avoir une garde `if (!selectedCopro)` avant de
    faire l'appel a /fiscal/expenses, sinon le backend retourne 400 et le
    front declenche un 'Uncaught runtime error' (overlay dev react-scripts).
    """
    with open("/app/frontend/src/pages/ExpensesPage.js") as f:
        content = f.read()
    # Doit contenir la garde anti-400
    assert "!selectedCopro" in content, "ExpensesPage.load doit garder selectedCopro"
    assert "selectedCopro === 'all'" in content, "ExpensesPage.load doit exclure 'all'"
    # Doit avoir un catch (pas seulement finally)
    load_snippet = content.split("const load = useCallback")[1].split("useEffect(() => { load(); }")[0]
    assert "catch" in load_snippet, "ExpensesPage.load doit avoir un catch (swallow -> toast)"


def test_iter90gz_global_unhandled_rejection_handler_wired():
    """App.js doit avoir un handler `window.addEventListener('unhandledrejection',...)`
    qui suppress l'overlay dev pour les erreurs Axios non-catch."""
    with open("/app/frontend/src/App.js") as f:
        content = f.read()
    assert "unhandledrejection" in content, "Handler unhandledrejection manquant"
    assert "event.preventDefault()" in content, "Le handler doit preventDefault sur Axios"
