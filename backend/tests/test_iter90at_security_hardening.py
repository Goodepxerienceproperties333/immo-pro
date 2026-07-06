"""
Iter90at : Hardening securite P0 - 5 protections.

1. Rate limiting sur /auth/login (10/min), /auth/register + /auth/forgot-password
   + /auth/reset-password (5/min).
2. Security headers : X-Frame-Options, X-Content-Type-Options,
   Strict-Transport-Security, Referrer-Policy, Permissions-Policy, CSP.
3. CORS whitelist stricte (verifie que le code build la liste correctement).
4. Cookies auth durcis : httpOnly + samesite=lax + secure gere via env COOKIE_SECURE.
5. Body size limit : refus HTTP 413 si Content-Length > MAX_BODY_MB (defaut 20 MB).
"""
import asyncio
import os
import sys
import httpx
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
LOCAL_URL = "http://localhost:8001"


async def _run_security_headers_present():
    """Verifie que les 6 headers de securite sont poses sur toutes les reponses."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{LOCAL_URL}/api/health")
        # Headers doivent etre presents meme sur une 401 (auth middleware)
        h = {k.lower(): v for k, v in r.headers.items()}
        assert h.get("x-frame-options") == "SAMEORIGIN"
        assert h.get("x-content-type-options") == "nosniff"
        assert "max-age" in (h.get("strict-transport-security") or "")
        assert h.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in (h.get("permissions-policy") or "")
        assert "default-src 'self'" in (h.get("content-security-policy") or "")


async def _run_body_size_limit():
    """Payload > 20 MB (defaut) doit retourner 413 avant meme le handler."""
    async with httpx.AsyncClient(timeout=15) as c:
        big_payload = '{"x":"' + "A" * (25 * 1024 * 1024) + '"}'
        r = await c.post(
            f"{LOCAL_URL}/api/auth/login",
            content=big_payload,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413, f"Expected 413, got {r.status_code}"


async def _run_rate_limit_login():
    """Login rate-limited a 10/min/IP. La 11e requete doit renvoyer 429.
    Utilise X-Forwarded-For fictif pour eviter d'etre bloque par le cache
    des autres tests."""
    async with httpx.AsyncClient(timeout=10) as c:
        headers = {
            "Content-Type": "application/json",
            "X-Forwarded-For": "9.9.9.99",  # IP fictive unique pour ce test
        }
        codes = []
        for _ in range(12):
            r = await c.post(f"{LOCAL_URL}/api/auth/login",
                             json={"email": "nope@nope.nope", "password": "x"},
                             headers=headers)
            codes.append(r.status_code)
        # On doit avoir au moins un 429 dans les 12 dernieres requetes
        assert 429 in codes, f"Rate limit non declenche apres 12 tentatives : {codes}"


async def _run_cookies_have_security_flags():
    """Login reussi -> cookies doivent avoir HttpOnly + SameSite."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{LOCAL_URL}/api/auth/login",
            json={"email": "admin@copro.be", "password": "admin123"},
            headers={"X-Forwarded-For": "8.8.8.88"},  # IP fictive unique
        )
        assert r.status_code == 200, r.text
        set_cookies = r.headers.get_list("set-cookie")
        access_cookie = next((c for c in set_cookies if c.startswith("access_token=")), None)
        assert access_cookie is not None
        # Flags obligatoires
        assert "HttpOnly" in access_cookie
        assert "SameSite" in access_cookie
        assert "Path=/" in access_cookie


def test_security_headers_present():
    asyncio.run(_run_security_headers_present())


def test_body_size_limit_413():
    asyncio.run(_run_body_size_limit())


def test_rate_limit_login_429():
    asyncio.run(_run_rate_limit_login())


def test_cookies_have_security_flags():
    asyncio.run(_run_cookies_have_security_flags())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
