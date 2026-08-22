"""iter94h : Integration Enable Banking (PSD2 Belgique).

Sprint 1 : UI de connexion bancaire + OAuth flow.
- List Belgian ASPSPs (banques)
- POST /authorize/start -> URL redirection banque
- GET /callback -> echange code contre session_id
- GET /sessions/{id}/accounts -> liste des comptes bancaires

Docs : https://enablebanking.com/docs/api/reference/
Points cles :
- JWT RS256 pour auth : iss=enablebanking.com, kid=APP_ID, aud=api.enablebanking.com
- Session accounts renouveler tous les 90j (consent PSD2)
- Cle privee RSA jamais exposee au frontend
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import secrets as _secrets

import httpx
import jwt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel


class StartAuthRequest(BaseModel):
    aspsp_name: str
    aspsp_country: str = "BE"
    copropriete_id: str
    psu_type: str = "personal"  # ou "business"


def create_openbanking_router(db):
    router = APIRouter(prefix="/api/banking/openbanking")

    # ---------- Config ----------
    APP_ID = os.environ.get("ENABLE_APP_ID", "")
    KEY_PATH = os.environ.get("ENABLE_PRIVATE_KEY_PATH", "")
    API_URL = os.environ.get(
        "ENABLE_API_URL", "https://api.enablebanking.com"
    ).rstrip("/")
    # URL de callback publique (preview / prod) - construite dynamiquement
    # depuis le referer ou le Host header.
    _private_key: Optional[bytes] = None
    if KEY_PATH and Path(KEY_PATH).exists():
        _private_key = Path(KEY_PATH).read_bytes()

    def _configured() -> bool:
        return bool(APP_ID and _private_key)

    def _sign_jwt() -> str:
        """Genere un JWT RS256 court (5min) pour Enable Banking."""
        if not _configured():
            raise HTTPException(
                503, "Enable Banking non configure "
                     "(ENABLE_APP_ID / ENABLE_PRIVATE_KEY_PATH manquants)"
            )
        now = int(datetime.now(timezone.utc).timestamp())
        return jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": now,
                "exp": now + 300,
            },
            _private_key,
            algorithm="RS256",
            headers={"typ": "JWT", "kid": APP_ID},
        )

    async def _eb(method: str, path: str, **kwargs) -> dict:
        """Appel HTTP a Enable Banking avec JWT signe."""
        headers = {
            "Authorization": f"Bearer {_sign_jwt()}",
            "Accept": "application/json",
        }
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(base_url=API_URL, timeout=30.0) as c:
            r = await c.request(method, path, headers=headers, **kwargs)
        if r.is_error:
            # Ne surtout pas leak le JWT ou le code
            raise HTTPException(
                r.status_code,
                f"Enable Banking API {r.status_code} : {r.text[:400]}",
            )
        return r.json() if r.content else {}

    async def _require_syndic_scope(request: Request, copropriete_id: str):
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        u = await db.users.find_one({"_id": ObjectId(uid)})
        if not u:
            raise HTTPException(401, "Utilisateur introuvable")
        role = u.get("role")
        if role in ("admin", "superadmin"):
            return u
        allowed = set(u.get("copropriete_ids") or [])
        if role == "gestionnaire" and u.get("parent_syndic_id"):
            parent = await db.users.find_one(
                {"_id": ObjectId(u["parent_syndic_id"])}
            )
            if parent:
                allowed |= set(parent.get("copropriete_ids") or [])
        if copropriete_id not in allowed:
            raise HTTPException(
                403, "Cette ACP ne fait pas partie de votre portefeuille"
            )
        return u

    def _callback_url(request: Request) -> str:
        """Construit l'URL de callback publique (preview ou prod)."""
        # Force le scheme https (proxy Emergent termine SSL en front)
        host = request.headers.get("host", "").split(",")[0].strip()
        return f"https://{host}/api/banking/openbanking/callback"

    def _frontend_url(request: Request) -> str:
        host = request.headers.get("host", "").split(",")[0].strip()
        return f"https://{host}"

    # ============ Endpoints ============

    @router.get("/status")
    async def status():
        """Diagnostic : verifie config Enable Banking."""
        return {
            "configured": _configured(),
            "app_id_present": bool(APP_ID),
            "private_key_present": bool(_private_key),
            "api_url": API_URL,
        }

    @router.get("/aspsps")
    async def list_aspsps(request: Request, country: str = "BE"):
        """Liste des banques disponibles pour un pays donne (AIS = Account Info)."""
        # Auth : n'importe quel user authentifie (juste la liste des banques)
        if not getattr(request.state, "user_id", None):
            raise HTTPException(401, "Non authentifie")
        data = await _eb(
            "GET", "/aspsps",
            params={"country": country.upper()},
        )
        # Filtrer sur les ASPSPs qui supportent AIS (Account Information Service)
        raw = data.get("aspsps", data) if isinstance(data, dict) else data
        filtered = []
        for a in raw or []:
            services = a.get("psu_types") or []  # psu_types indique retail/business
            filtered.append({
                "name": a.get("name"),
                "country": a.get("country"),
                "logo": a.get("logo"),
                "psu_types": services,
                "maximum_consent_validity": a.get("maximum_consent_validity"),
                "beta": a.get("beta", False),
            })
        return {"aspsps": filtered, "count": len(filtered)}

    @router.post("/authorize/start")
    async def start_auth(body: StartAuthRequest, request: Request):
        """Demarre le flow OAuth : cree une session Enable Banking et
        retourne l'URL de redirection vers la banque."""
        user = await _require_syndic_scope(request, body.copropriete_id)
        state = _secrets.token_urlsafe(32)
        valid_until = (
            datetime.now(timezone.utc) + timedelta(days=90)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        payload = {
            "access": {"valid_until": valid_until},
            "aspsp": {
                "name": body.aspsp_name,
                "country": body.aspsp_country,
            },
            "state": state,
            "redirect_url": _callback_url(request),
            "psu_type": body.psu_type,
        }
        # Persiste le state avec le contexte user + ACP pour verifier au callback
        await db.openbanking_states.insert_one({
            "state": state,
            "user_id": str(user["_id"]),
            "copropriete_id": body.copropriete_id,
            "aspsp_name": body.aspsp_name,
            "aspsp_country": body.aspsp_country,
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
            "used": False,
        })
        result = await _eb("POST", "/auth", json=payload)
        return {"url": result.get("url"), "state": state}

    @router.get("/callback")
    async def callback(
        request: Request,
        code: Optional[str] = Query(None),
        state: Optional[str] = Query(None),
        error: Optional[str] = Query(None),
    ):
        """Retour de la banque apres autorisation utilisateur."""
        front = _frontend_url(request)
        if error:
            return RedirectResponse(
                f"{front}/banking?openbanking_error={error}"
            )
        if not code or not state:
            return RedirectResponse(
                f"{front}/banking?openbanking_error=missing_params"
            )
        # Verifie state (protection CSRF + one-shot)
        saved = await db.openbanking_states.find_one_and_update(
            {"state": state, "used": False},
            {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}},
        )
        if not saved:
            return RedirectResponse(
                f"{front}/banking?openbanking_error=invalid_state"
            )
        if saved.get("expires_at") and saved["expires_at"] < datetime.now(
            timezone.utc,
        ):
            return RedirectResponse(
                f"{front}/banking?openbanking_error=expired_state"
            )
        # Echange code -> session_id
        try:
            session = await _eb("POST", "/sessions", json={"code": code})
        except HTTPException as e:
            return RedirectResponse(
                f"{front}/banking?openbanking_error=session_exchange&msg={e.detail[:60]}"
            )
        session_id = session.get("session_id")
        if not session_id:
            return RedirectResponse(
                f"{front}/banking?openbanking_error=no_session_id"
            )
        # Persiste la session pour l'ACP
        await db.openbanking_sessions.insert_one({
            "session_id": session_id,
            "user_id": saved["user_id"],
            "copropriete_id": saved["copropriete_id"],
            "aspsp_name": saved["aspsp_name"],
            "aspsp_country": saved["aspsp_country"],
            "accounts": session.get("accounts", []),
            "access": session.get("access", {}),
            "created_at": datetime.now(timezone.utc),
        })
        # Redirection frontend avec message succes
        return RedirectResponse(
            f"{front}/banking?openbanking_success=1&session_id={session_id}"
        )

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        copropriete_id: str = Query(...),
    ):
        """Liste les sessions bancaires ouvertes pour une ACP."""
        await _require_syndic_scope(request, copropriete_id)
        sessions = await db.openbanking_sessions.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0},
        ).sort("created_at", -1).to_list(50)
        # Serialize datetimes
        for s in sessions:
            if isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].isoformat()
        return {"sessions": sessions, "count": len(sessions)}

    @router.get("/sessions/{session_id}/accounts")
    async def session_accounts(session_id: str, request: Request):
        """Retourne la liste des comptes lies a une session."""
        record = await db.openbanking_sessions.find_one({"session_id": session_id})
        if not record:
            raise HTTPException(404, "Session inconnue")
        await _require_syndic_scope(request, record["copropriete_id"])
        # Actualiser depuis Enable Banking
        try:
            fresh = await _eb("GET", f"/sessions/{session_id}")
            accounts = fresh.get("accounts", [])
            await db.openbanking_sessions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "accounts": accounts,
                    "last_refresh_at": datetime.now(timezone.utc),
                }},
            )
            return {"session_id": session_id, "accounts": accounts}
        except HTTPException as e:
            # Session expiree cote Enable Banking -> reauth requise
            if e.status_code in (401, 403, 410):
                await db.openbanking_sessions.update_one(
                    {"session_id": session_id},
                    {"$set": {"status": "reauthorization_required"}},
                )
            raise

    return router
