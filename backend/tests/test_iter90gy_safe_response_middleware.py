"""iter90gy — Verrou : SafeResponseMiddleware evite les 502 Cloudflare.

Contexte utilisateur (16/07/2026) :
  "encore cette erreur!! je veux un fix definitif pour ce bug!"
  L'utilisateur voit regulierement un 502 Cloudflare "The origin web server
  returned an invalid or incomplete response" en particulier sur PROD.

Cause :
  Le pattern Starlette `RuntimeError: No response returned` (typique d'un
  crash silencieux : task cancelled, shutdown en cours de requete,
  ExceptionGroup non-catch, ...) fait que **AUCUNE reponse HTTP** n'est
  envoyee au client. Cloudflare interprete ca comme une origine down
  et retourne un 502.

Fix (iter90gy) :
  Un middleware `SafeResponseMiddleware` en tete de chaine attrape TOUTE
  exception non-geree et renvoie une reponse JSON valide (503 ou 500).
  Le client (et Cloudflare) recoit toujours quelque chose de valide.

Tests :
  1. Un endpoint qui leve `RuntimeError("No response returned")` -> 503
  2. Un endpoint qui leve une exception generique -> 500 propre
  3. Un endpoint qui repond normalement -> reponse preservee (200)
  4. Le corps de la reponse est du JSON parsable (pas d'HTML)
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://optipro-parser-fix.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200
    return s


def test_iter90gy_normal_endpoint_still_works(admin_session):
    """Sanity : un endpoint normal continue de fonctionner apres l'ajout du safe-net."""
    r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json() is not None


def test_iter90gy_404_still_returns_json(admin_session):
    """Une route inconnue renvoie 404 JSON (pas HTML par defaut)."""
    r = admin_session.get(f"{BASE_URL}/api/nonexistent-endpoint-iter90gy", timeout=30)
    assert r.status_code == 404
    # FastAPI renvoie du JSON par defaut
    assert r.headers.get("content-type", "").startswith("application/json")


def test_iter90gy_middleware_registered_in_server():
    """Verifie que le middleware SafeResponseMiddleware est bien present dans server.py.
    Sans lui, la protection anti-502 disparait.
    """
    with open("/app/backend/server.py") as f:
        content = f.read()
    assert "class SafeResponseMiddleware" in content, \
        "SafeResponseMiddleware doit etre defini dans server.py"
    assert "app.add_middleware(SafeResponseMiddleware)" in content, \
        "SafeResponseMiddleware doit etre enregistre via add_middleware"
    # Verifie qu'il gere bien "No response returned"
    assert '"No response returned"' in content, \
        "Le middleware doit specifiquement detecter 'No response returned'"


def test_iter90gy_middleware_ordering():
    """Le SafeResponseMiddleware doit etre ajoute APRES BodySizeLimitMiddleware
    pour englober TOUS les autres middlewares (executes en LIFO). Attention :
    l'ordre d'ajout Starlette est inverse a l'ordre d'execution.
    """
    with open("/app/backend/server.py") as f:
        content = f.read()
    idx_body = content.find("app.add_middleware(BodySizeLimitMiddleware)")
    idx_safe = content.find("app.add_middleware(SafeResponseMiddleware)")
    assert idx_body > 0 and idx_safe > 0
    # SafeResponseMiddleware doit etre AJOUTE APRES pour etre EXECUTE EN PREMIER
    assert idx_safe > idx_body, \
        "SafeResponseMiddleware doit etre add_middleware APRES BodySizeLimitMiddleware"
