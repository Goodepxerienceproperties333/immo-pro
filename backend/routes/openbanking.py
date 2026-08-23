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


def _to_utc_aware(value) -> Optional[datetime]:
    """iter94n : Normalise n'importe quelle valeur date en `datetime` UTC
    tz-aware avant comparaison.

    Gere :
     - `datetime` deja aware -> converti en UTC
     - `datetime` naive -> assume UTC (BSON MongoDB stocke sans tz)
     - `str` ISO-8601 (`2026-11-20T19:57:43Z` ou `+00:00` ou `+02:00`)
     - `str` sans info tz -> assume UTC
     - None / autre -> None

    Utilise systematiquement AVANT toute comparaison < / > / == entre dates
    pour eviter `TypeError: can't compare offset-naive and offset-aware`.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            # Accepte suffixes Z ou +hh:mm ; fromisoformat gere le reste
            cleaned = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def create_openbanking_router(db):
    router = APIRouter(prefix="/api/banking/enablebanking")

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
        """Construit l'URL de callback publique (preview ou prod).
        iter94i : le proxy Emergent renvoie un Host header INTERNE
        (cluster-XX.preview.emergentcf.cloud) different du domaine public
        (preview.emergentagent.com / immo-pcmn.emergent.host). On lit dans
        l'ordre : env ENABLE_CALLBACK_URL > X-Forwarded-Host > Origin > Host.
        Cette URL DOIT etre enregistree dans le Control Panel Enable Banking
        sinon REDIRECT_URI_NOT_ALLOWED (400)."""
        # 1. Env override (le plus fiable en prod)
        env_url = os.environ.get("ENABLE_CALLBACK_URL", "").strip()
        if env_url:
            return env_url.rstrip("/")
        # 2. X-Forwarded-Host (souvent pose par les ingress K8s / CF)
        fwd_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        # 3. Origin (envoye par le frontend, https://<domain>)
        origin = (request.headers.get("origin") or "").strip()
        # 4. Host (interne cluster.local -> a eviter mais fallback ultime)
        host = (request.headers.get("host") or "").split(",")[0].strip()
        # Choisis le premier qui ne ressemble PAS a un host interne cluster.
        candidates = [fwd_host, origin, host]
        for c in candidates:
            if not c:
                continue
            if "cluster-" in c or ".emergentcf.cloud" in c or ".local" in c:
                continue
            # Origin est deja https://host ; sinon on prefixe https://
            base = c if c.startswith("http") else f"https://{c}"
            return base.rstrip("/") + "/api/banking/enablebanking/callback"
        # Dernier recours : reprend Host tel quel
        return f"https://{host}/api/banking/enablebanking/callback"

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

        callback = _callback_url(request)
        # iter94i : log l'URL exacte + longueur pour debugger mismatch
        # avec le Control Panel (REDIRECT_URI_NOT_ALLOWED).
        import logging as _logging
        _log = _logging.getLogger("openbanking")
        _log.warning(
            "EnableBanking POST /auth redirect_url=%r len=%d bytes_hex=%s",
            callback, len(callback),
            callback.encode("utf-8").hex()[:200],
        )

        payload = {
            "access": {"valid_until": valid_until},
            "aspsp": {
                "name": body.aspsp_name,
                "country": body.aspsp_country,
            },
            "state": state,
            "redirect_url": callback,
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
        # iter94n : normalisation UTC tz-aware sur les DEUX cotes avant compare
        exp_saved = _to_utc_aware(saved.get("expires_at"))
        now_utc = datetime.now(timezone.utc)
        if exp_saved is not None and exp_saved < now_utc:
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
        # iter94k/n : store expiration for consent renewal alerts, en
        # normalisant systematiquement en UTC tz-aware (Enable Banking
        # renvoie `2026-11-20T19:57:43Z` -> converti proprement).
        access = session.get("access", {}) or {}
        expires_at_dt = _to_utc_aware(access.get("valid_until"))
        if expires_at_dt is None:
            expires_at_dt = datetime.now(timezone.utc) + timedelta(days=90)

        await db.openbanking_sessions.insert_one({
            "session_id": session_id,
            "user_id": saved["user_id"],
            "copropriete_id": saved["copropriete_id"],
            "aspsp_name": saved["aspsp_name"],
            "aspsp_country": saved["aspsp_country"],
            "accounts": session.get("accounts", []),
            "access": access,
            "expires_at": expires_at_dt.isoformat(),
            "status": "active",
            "renewal_needed": False,
            "created_at": datetime.now(timezone.utc),
        })
        # iter94k : Si c'est un renouvellement, archive l'ancienne session
        if saved.get("renewal_of_session_id"):
            await db.openbanking_sessions.update_one(
                {"session_id": saved["renewal_of_session_id"]},
                {"$set": {
                    "status": "renewed",
                    "replaced_by_session_id": session_id,
                    "renewed_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        # iter94l : declenche une sync initiale IMMEDIATE (fire and forget)
        # pour que le user voie tout de suite ses transactions au retour.
        import asyncio as _asyncio
        try:
            from openbanking_sync import sync_session
            _asyncio.create_task(sync_session(db, session_id, days_back=90))
        except Exception:  # noqa: BLE001
            pass  # non-bloquant
        # Redirection frontend avec message succes
        return RedirectResponse(
            f"{front}/banking?openbanking_success=1&session_id={session_id}"
        )

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        copropriete_id: str = Query(...),
        include_revoked: bool = Query(False),
    ):
        """Liste les sessions bancaires ouvertes pour une ACP.
        Par defaut on cache les sessions revoquees/renouvelees pour
        garder l'UI propre. Passer `include_revoked=true` pour tout voir."""
        await _require_syndic_scope(request, copropriete_id)
        q: dict = {"copropriete_id": copropriete_id}
        if not include_revoked:
            q["status"] = {"$nin": ["revoked", "renewed"]}
        sessions = await db.openbanking_sessions.find(
            q, {"_id": 0},
        ).sort("created_at", -1).to_list(50)
        # Serialize datetimes + iter94k: compute expiration flags
        now = datetime.now(timezone.utc)
        for s in sessions:
            if isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].isoformat()
            # Sprint 4 : days until expiration
            exp = s.get("expires_at")
            if exp:
                try:
                    exp_dt = (
                        datetime.fromisoformat(exp.replace("Z", "+00:00"))
                        if isinstance(exp, str) else exp
                    )
                    delta = (exp_dt - now).total_seconds() / 86400
                    s["days_until_expiration"] = round(delta, 1)
                    s["expired"] = delta < 0
                    s["renewal_needed"] = 0 <= delta <= 15
                except Exception:
                    s["days_until_expiration"] = None
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

    # iter94j : Sprint 2 - synchronisation transactions
    @router.post("/sync-now")
    async def sync_now(
        request: Request,
        copropriete_id: str = Query(...),
        days_back: int = Query(30, ge=1, le=365),
    ):
        """Declenche une sync IMMEDIATE des sessions d'une ACP."""
        await _require_syndic_scope(request, copropriete_id)
        from openbanking_sync import sync_session
        sessions = await db.openbanking_sessions.find(
            {
                "copropriete_id": copropriete_id,
                "status": {"$ne": "reauthorization_required"},
            },
            {"_id": 0, "session_id": 1},
        ).to_list(50)
        if not sessions:
            return {
                "message": "Aucune session active. Connecte d'abord une banque.",
                "sessions": 0,
            }
        results = []
        for s in sessions:
            try:
                r = await sync_session(db, s["session_id"], days_back=days_back)
                results.append(r)
            except Exception as e:  # noqa: BLE001
                results.append({"session_id": s["session_id"],
                                "error": str(e)[:200]})
        # Totaux
        inserted = sum(r.get("inserted", 0) for r in results)
        enriched = sum(r.get("matched_to_existing", 0) for r in results)
        dup = sum(r.get("skipped_duplicate", 0) for r in results)
        return {
            "sessions": len(sessions),
            "inserted": inserted,
            "enriched_from_coda": enriched,
            "skipped_duplicate": dup,
            "details": results,
        }

    # iter94k : Sprint 4 - renouvellement consentement 90j
    class RenewSessionRequest(BaseModel):
        session_id: str

    @router.post("/sessions/renew")
    async def renew_session(body: RenewSessionRequest, request: Request):
        """Cree une nouvelle authorization pour renouveler le consentement
        d'une session existante. Retourne l'URL vers la banque, comme
        authorize/start. La session actuelle sera archivee au retour du callback."""
        record = await db.openbanking_sessions.find_one(
            {"session_id": body.session_id},
        )
        if not record:
            raise HTTPException(404, "Session inconnue")
        await _require_syndic_scope(request, record["copropriete_id"])
        state = _secrets.token_urlsafe(32)
        valid_until = (
            datetime.now(timezone.utc) + timedelta(days=90)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        callback = _callback_url(request)
        payload = {
            "access": {"valid_until": valid_until},
            "aspsp": {
                "name": record["aspsp_name"],
                "country": record["aspsp_country"],
            },
            "state": state,
            "redirect_url": callback,
            "psu_type": "personal",
        }
        await db.openbanking_states.insert_one({
            "state": state,
            "user_id": str(record["user_id"]),
            "copropriete_id": record["copropriete_id"],
            "aspsp_name": record["aspsp_name"],
            "aspsp_country": record["aspsp_country"],
            "renewal_of_session_id": body.session_id,
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
            "used": False,
        })
        result = await _eb("POST", "/auth", json=payload)
        return {"url": result.get("url"), "state": state}

    # iter94m : revocation d'une session (unlink bank account)
    @router.delete("/sessions/{session_id}")
    async def revoke_session(
        session_id: str, request: Request,
        delete_transactions: bool = Query(
            False,
            description="Si true, supprime aussi les bank_transactions "
                        "importees depuis cette session"
        ),
    ):
        """Revoque le consentement PSD2 : DELETE cote Enable Banking +
        marque la session 'revoked' en base. Les transactions deja
        importees sont conservees par defaut (audit trail)."""
        record = await db.openbanking_sessions.find_one(
            {"session_id": session_id},
        )
        if not record:
            raise HTTPException(404, "Session inconnue")
        await _require_syndic_scope(request, record["copropriete_id"])

        # 1. Revoke cote Enable Banking (best effort, on continue si erreur)
        eb_error = None
        try:
            await _eb("DELETE", f"/sessions/{session_id}")
        except HTTPException as e:
            # 404 = deja revoquee cote EB, on ignore. Autres erreurs = warning.
            if e.status_code != 404:
                eb_error = f"EB {e.status_code}: {str(e.detail)[:120]}"

        # 2. Marque la session en base comme revoked
        await db.openbanking_sessions.update_one(
            {"session_id": session_id},
            {"$set": {
                "status": "revoked",
                "revoked_at": datetime.now(timezone.utc).isoformat(),
                "revocation_error": eb_error,
            }},
        )

        # 3. Optionnel : nettoie les transactions liees
        deleted_txn_count = 0
        if delete_transactions:
            r = await db.bank_transactions.delete_many({
                "openbanking_session_id": session_id,
                "source": "openbanking",
            })
            deleted_txn_count = r.deleted_count
            # Marque aussi les extraits virtuels lies comme "revoked"
            # (on ne les supprime pas pour garder l'historique compta)
            await db.bank_statements.update_many(
                {
                    "copropriete_id": record["copropriete_id"],
                    "source": "openbanking",
                    "number": {"$regex": "^OB-"},
                },
                {"$set": {"openbanking_session_revoked": True}},
            )

        return {
            "session_id": session_id,
            "status": "revoked",
            "eb_revoke_ok": eb_error is None,
            "eb_error": eb_error,
            "deleted_transactions": deleted_txn_count,
        }

    return router
