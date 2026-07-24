from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, UploadFile, File
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import bcrypt
import jwt
import uuid
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from pymongo import ReturnDocument

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="NextGe Copro")

# iter90at : Rate limiting global (anti-DDoS + anti-brute-force).
# - Global : 100 req/min/IP par defaut (protege contre le scraping / DDoS applicatif).
# - Endpoints sensibles (login, register, password reset) : 5 req/min/IP.
#   Chaque route sensible utilise @limiter.limit("5/minute") dans son handler.
# Utilise l'adresse IP source (X-Forwarded-For gere par slowapi via
# get_remote_address si le reverse-proxy K8s forwarde correctement l'header).
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# iter90at : Security headers - HSTS, X-Frame-Options, X-Content-Type-Options,
# Referrer-Policy, Permissions-Policy, CSP basique.
# NOTE : sera enregistre en `@app.middleware("http")` APRES auth_middleware
# pour devenir outermost (afin de wrapper les 401 early returns de l'auth).
_SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https:; "
        "frame-ancestors 'self';"
    ),
}


# iter90at : Body size limit - refuse tout upload > 20 MB des le middleware
# (avant lecture par Starlette / uvicorn). Protege contre les uploads DoS.
_MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_MB", "20")) * 1024 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > _MAX_BODY_BYTES:
                    return StarletteResponse(
                        content='{"detail":"Requete trop volumineuse (max %d MB)"}' % (_MAX_BODY_BYTES // (1024*1024)),
                        status_code=413,
                        media_type="application/json",
                    )
            except (TypeError, ValueError):
                pass
        return await call_next(request)


class SafeResponseMiddleware(BaseHTTPMiddleware):
    """iter90gy : filet de securite anti-Cloudflare 502.

    Sans ce middleware, quand un endpoint crash silencieusement (asyncio
    cancel, task group exception, shutdown en cours de requete, exception
    non-catchee dans un middleware amont), Starlette leve `RuntimeError:
    No response returned.` et **aucune reponse HTTP n'est envoyee au
    client**. Cloudflare interprete ca comme une origine down et retourne
    un 502 "The origin web server returned an invalid or incomplete
    response".

    On englobe donc `call_next` dans un try/except large : toute exception
    non-attrapee en aval produit un 503 JSON propre (au lieu de rien).
    L'exception reste loguee pour debug, mais le client recoit toujours
    une reponse valide.

    iter90gz : logging structure vers `backend_errors.log` (contexte:
    method, path, user_id, status_code, duration_ms).
    """
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except RuntimeError as e:
            # "No response returned" -> l'endpoint a plante sans repondre
            if "No response returned" in str(e):
                _log_error_context(request, 503, "No response returned")
                logging.getLogger(__name__).error(
                    "iter90gy safe-net: endpoint %s %s a plante sans reponse",
                    request.method, request.url.path,
                )
                return StarletteResponse(
                    content='{"detail":"Service temporairement indisponible, veuillez reessayer."}',
                    status_code=503,
                    media_type="application/json",
                )
            raise
        except Exception as _exc:
            # Toute autre exception non-attrapee (asyncio.CancelledError,
            # ExceptionGroup, etc.) - loguer + retourner 500 propre plutot
            # que laisser Cloudflare voir un stream tronque.
            _log_error_context(request, 500, repr(_exc))
            logging.getLogger(__name__).exception(
                "iter90gy safe-net: exception non-geree sur %s %s",
                request.method, request.url.path,
            )
            return StarletteResponse(
                content='{"detail":"Erreur interne du serveur"}',
                status_code=500,
                media_type="application/json",
            )


# iter90gz : Watchdog Uvicorn - kill toute requete depassant REQUEST_TIMEOUT_SEC
# (default 60s) avec un 504 propre. Empeche Cloudflare de timeout a 100s
# (ce qui coupe la connexion sans reponse). Certains paths legitimes (import
# CSV/PDF, migrations, PDFs de masse) peuvent depasser ce seuil : ils sont
# exemptes via REQUEST_TIMEOUT_SKIP_PREFIXES.
_REQUEST_TIMEOUT_SEC = float(os.environ.get("REQUEST_TIMEOUT_SEC", "60"))
_REQUEST_TIMEOUT_SKIP_PREFIXES = (
    "/api/import-wizard/",       # imports Optipro/CODA lourds
    "/api/admin/gridfs-migration",  # migration one-time
    "/api/admin/duplicates-audit",  # audit global cross-ACP (peut etre long)
    "/api/backups/",             # backup / restore
    "/api/communication/send/",  # envoi email groupe (peut etre long)
    "/api/reports/decompte",     # PDF decomptes de masse
)


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _REQUEST_TIMEOUT_SKIP_PREFIXES):
            return await call_next(request)
        import asyncio as _asyncio
        try:
            return await _asyncio.wait_for(call_next(request), timeout=_REQUEST_TIMEOUT_SEC)
        except _asyncio.TimeoutError:
            _log_error_context(request, 504, f"timeout after {_REQUEST_TIMEOUT_SEC}s")
            logging.getLogger(__name__).warning(
                "iter90gz timeout: %s %s a depasse %ss",
                request.method, path, _REQUEST_TIMEOUT_SEC,
            )
            return StarletteResponse(
                content=('{"detail":"La requete a depasse le delai imparti (%ss). '
                         'Reessayez ou reduisez la periode/le volume."}' % int(_REQUEST_TIMEOUT_SEC)),
                status_code=504,
                media_type="application/json",
            )


# iter90gz : Logger dedie aux erreurs 5xx (fichier rotatif 5MB x 3).
def _setup_error_file_logger():
    from logging.handlers import RotatingFileHandler
    logger_5xx = logging.getLogger("backend_errors")
    if logger_5xx.handlers:
        return logger_5xx
    logger_5xx.setLevel(logging.INFO)
    logger_5xx.propagate = False
    log_dir = Path(os.environ.get("ERROR_LOG_DIR", "/var/log/supervisor"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "backend_errors.log"
        handler = RotatingFileHandler(str(log_path), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        ))
        logger_5xx.addHandler(handler)
    except Exception as _e:
        # Fallback : si le repertoire n'est pas accessible, on log en stderr
        # (visible dans /var/log/supervisor/backend.err.log).
        print(f"[startup] backend_errors.log handler skipped: {_e}")
    return logger_5xx


_error_logger = _setup_error_file_logger()


def _log_error_context(request, status_code, error_detail):
    """iter90gz : log structure d'une erreur 5xx avec contexte utilisateur."""
    try:
        user_id = getattr(request.state, "user_id", None)
        user_email = getattr(request.state, "user_email", None)
        _error_logger.error(
            "%s %s | status=%s | user_id=%s | email=%s | error=%s",
            request.method, request.url.path,
            status_code, user_id or "-", user_email or "-",
            (error_detail or "")[:500],
        )
    except Exception:
        pass


app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestTimeoutMiddleware)
# iter90gy : le SafeResponseMiddleware DOIT etre ajoute EN DERNIER (donc
# execute EN PREMIER dans la chaine) pour attraper toutes les exceptions
# des autres middlewares en aval (auth, security, cors, body_size...).
app.add_middleware(SafeResponseMiddleware)

# CORS - cookie-based auth needs explicit origins (allow_credentials=True is
# incompatible with allow_origins=["*"]). We honor CORS_ORIGINS if it lists
# specific domains; otherwise fall back to FRONTEND_URL.
def _build_cors_origins():
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    fe = os.environ.get("FRONTEND_URL", "").strip()
    explicit = []
    if raw and raw != "*":
        explicit = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    # Always include FRONTEND_URL so preview/prod stay reachable
    if fe and fe not in explicit:
        explicit.append(fe)
    return explicit or [fe] if fe else ["http://localhost:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Auth middleware: ALL /api/* routes require a valid access_token cookie or Bearer header,
# EXCEPT login, register, refresh, logout (auth flow itself), and OPTIONS preflight.
AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/first-set-password",
    "/api/auth/check-must-change-password",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    # iter90gz : health checks publics pour monitoring K8s/Cloudflare
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
}

# Prefix-based exemption for public legal document reads (unauthenticated users
# must be able to read CGU/Privacy/Cookies/Mentions before login).
AUTH_EXEMPT_PREFIXES = (
    "/api/legal/documents",
)

# RBAC: paths that require admin role (superadmin/syndic) for any write/destructive action.
ADMIN_ONLY_PATHS = (
    "/api/users",         # user management
    "/api/admin/",        # admin tools (demo seed, etc.)
)

# Paths exempted from RBAC role check beyond authentication (e.g. self-service endpoints
# that any authenticated user can read).
RBAC_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
}

# Write methods that require at least 'manager' role (superadmin/syndic/gestionnaire).
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    # Skip CORS preflight
    if method == "OPTIONS":
        return await call_next(request)
    # Only protect /api routes
    if not path.startswith("/api"):
        return await call_next(request)
    # Exempt list (auth flow)
    if path in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    # Public prefixes (e.g. legal documents readable without login)
    if method == "GET" and path.startswith(AUTH_EXEMPT_PREFIXES):
        return await call_next(request)

    # Verify access token (cookie or Authorization Bearer)
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Invalid token type"})
        request.state.user_id = payload.get("sub")
        request.state.user_email = payload.get("email")
    except jwt.ExpiredSignatureError:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Token expired"})
    except jwt.InvalidTokenError:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})

    # ---- RBAC: load user role and enforce role-based access ----
    # Fetch role from DB (cached in request.state so handlers can reuse)
    try:
        user_doc = await db.users.find_one(
            {"_id": ObjectId(request.state.user_id)},
            {"_id": 0, "role": 1, "copropriete_ids": 1, "email": 1, "is_suspended": 1, "parent_syndic_id": 1}
        )
    except Exception:
        user_doc = None
    if not user_doc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "User not found"})
    # ---- ACCESS REVOCATION : block all API calls for suspended users ----
    if user_doc.get("is_suspended"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=403,
            content={"detail": "Votre acces a la plateforme a ete suspendu. Contactez votre syndic."}
        )
    role = user_doc.get("role", "owner")
    request.state.user_role = role
    request.state.user_copropriete_ids = user_doc.get("copropriete_ids", [])

    # ---- SYNDIC_ID : isolation multi-syndic ----
    from syndic_scope import resolve_syndic_id
    _user_for_scope = {
        "_id": request.state.user_id,
        "role": role,
        "parent_syndic_id": user_doc.get("parent_syndic_id"),
    }
    request.state.syndic_id = resolve_syndic_id(_user_for_scope)

    # ---- CHINESE WALL GLOBAL : isolation stricte entre syndics ----
    # Si la requete porte un copropriete_id (param OU header X-Copropriete-Id),
    # verifier que l'utilisateur (non-superadmin) a bien acces a cette ACP.
    # Le superadmin/admin voient tout (gestion plateforme).
    # Iter90dd : les endpoints /api/owner/* font leur propre chinese wall base
    # sur les LOTS du proprietaire (via _resolve_owner). Le champ
    # user.copropriete_ids sur les owners peut etre incomplet/obsolete, donc
    # on skip ce check ici pour ne pas doublement contrarier.
    if role not in ("superadmin", "admin") and path not in RBAC_EXEMPT_PATHS \
       and not (role == "owner" and path.startswith("/api/owner/")):
        try:
            copro_q = request.query_params.get("copropriete_id")
        except Exception:
            copro_q = None
        copro_h = request.headers.get("X-Copropriete-Id")
        requested_copro = copro_q or copro_h
        # "all" est une convention superadmin -> ignorer pour non-superadmin
        if requested_copro and requested_copro not in ("all", "", "None"):
            user_copros = request.state.user_copropriete_ids or []
            if requested_copro not in user_copros:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Acces refuse a cette copropriete (chinese wall : vous ne gerez pas cette ACP)"}
                )

    # Skip RBAC for self-service endpoints (any authenticated user)
    if path not in RBAC_EXEMPT_PATHS:
        # Admin-only path families: any verb requires admin role (superadmin/syndic)
        if any(path == p.rstrip("/") or path.startswith(p) for p in ADMIN_ONLY_PATHS):
            if not is_admin_role(role):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Reserve aux syndics / superadmins"})
        # Owners (proprietaires) are limited to /api/owner/* + /api/auth/* + their own
        # GET on /api/coproprietes (so they can see ACP names referenced in their portal).
        # Anything else returns 403 (no leaking other owners' info, no admin tools).
        elif role == "owner":
            allowed_owner_prefixes = ("/api/owner/", "/api/auth/", "/api/legal/")
            allowed_owner_exact_get = {"/api/coproprietes"}
            is_allowed = False
            if path.startswith(allowed_owner_prefixes):
                is_allowed = True
            elif method == "GET" and path in allowed_owner_exact_get:
                is_allowed = True
            # Allow GET on a specific copropriete (to display name)
            elif method == "GET" and path.startswith("/api/coproprietes/"):
                is_allowed = True
            # Allow GET on download of documents (file fetch) - we still check upstream that doc is in owner's ACPs
            elif method == "GET" and path.startswith("/api/documents/") and path.endswith("/download"):
                is_allowed = True
            if not is_allowed:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Acces restreint au portail proprietaire (/portal)"})
        # Write operations for non-owner authenticated roles require manager+
        elif method in WRITE_METHODS:
            if not can_manage(role):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Acces en lecture seule"})

    return await call_next(request)


# iter90at : Security headers middleware - PLACE APRES auth_middleware
# donc s'execute en OUTERMOST (wrap tous les 401/403 early returns).
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        # setdefault : ne pas ecraser si un endpoint a deja mis une valeur specifique
        if k not in response.headers:
            response.headers[k] = v
    return response


JWT_ALGORITHM = "HS256"
ROLES = ["superadmin", "syndic", "gestionnaire", "owner"]

def get_jwt_secret():
    return os.environ["JWT_SECRET"]

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "exp": datetime.now(timezone.utc) + timedelta(hours=2), "type": "access"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "refresh"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def is_admin_role(role: str) -> bool:
    """Syndic et superadmin: acces total aux donnees + gestion utilisateurs PROPRES."""
    return role in ("superadmin", "admin", "syndic")

def is_superadmin_only(role: str) -> bool:
    """SEUL le superadmin (plateforme) peut gerer les ACCES (creer/modifier/supprimer d'autres utilisateurs)."""
    return role in ("superadmin", "admin")

def can_manage(role: str) -> bool:
    """Syndic, gestionnaire: peuvent gerer les donnees des coproprietes."""
    return role in ("superadmin", "admin", "syndic", "gestionnaire")

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["_id"] = str(user["_id"])
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def generate_vcs(db_ref) -> str:
    """Generate a unique Belgian structured communication +++XXX/XXXX/XXXCC+++"""
    counter = await db_ref.counters.find_one_and_update(
        {"_id": "vcs_counter"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    seq = counter["seq"]
    base = str(seq).zfill(10)
    mod = int(base) % 97
    if mod == 0:
        mod = 97
    check = str(mod).zfill(2)
    full = base + check
    return f"+++{full[0:3]}/{full[3:7]}/{full[7:12]}+++"

# Auth Models
class LoginInput(BaseModel):
    email: str
    password: str

class RegisterInput(BaseModel):
    email: str
    password: str
    name: str

class MeUpdateInput(BaseModel):
    name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

# Auth Router
auth_router = APIRouter(prefix="/api/auth")

# iter90at : Configuration cookies auth durcie.
# En prod HTTPS : COOKIE_SECURE=true (le navigateur n'envoie le cookie que via HTTPS).
# En preview/local (http://localhost) : COOKIE_SECURE=false pour compat dev.
# SameSite=Lax protege contre les CSRF cross-site tout en laissant les redirects
# normaux fonctionner.
_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
_COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").lower()


def _set_auth_cookie(response: Response, key: str, value: str, max_age: int):
    """Helper unifie pour poser un cookie auth avec les bons flags de securite."""
    response.set_cookie(
        key=key, value=value,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        max_age=max_age,
        path="/",
    )


def user_response(user_doc):
    """Build a safe user response dict from a MongoDB user document."""
    return {
        "id": str(user_doc["_id"]) if isinstance(user_doc.get("_id"), ObjectId) else user_doc.get("_id", user_doc.get("id", "")),
        "email": user_doc["email"],
        "name": user_doc["name"],
        "role": user_doc["role"],
        "copropriete_ids": user_doc.get("copropriete_ids", []),
        "onboarding_completed": bool(user_doc.get("onboarding_completed", False)),
        "deletion_requested_at": user_doc.get("deletion_requested_at"),
        "deletion_purge_at_ts": user_doc.get("deletion_purge_at_ts"),
    }

@auth_router.post("/onboarding-complete")
async def mark_onboarding_complete(request: Request):
    """Marque le tutoriel de premiere connexion comme termine pour l'utilisateur courant."""
    user = await get_current_user(request)
    await db.users.update_one(
        {"_id": ObjectId(user["_id"])},
        {"$set": {"onboarding_completed": True,
                  "onboarding_completed_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"status": "ok"}

@auth_router.post("/login")
@limiter.limit("10/minute")
async def login(data: LoginInput, request: Request, response: Response):
    email = data.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user:
        await _record_login_attempt(email, None, request, success=False, reason="user_not_found")
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    # Block login for suspended accounts (access revoked by syndic / superadmin)
    if user.get("is_suspended"):
        await _record_login_attempt(email, user, request, success=False, reason="suspended")
        raise HTTPException(
            status_code=403,
            detail="Votre acces a la plateforme a ete suspendu. Contactez votre syndic."
        )
    # Block login if the user must define their password first
    if user.get("must_change_password"):
        raise HTTPException(
            status_code=403,
            detail={"code": "PASSWORD_SETUP_REQUIRED",
                    "message": "Vous devez definir votre mot de passe lors de la premiere connexion."}
        )
    if not verify_password(data.password, user["password_hash"]):
        await _record_login_attempt(email, user, request, success=False, reason="bad_password")
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    _set_auth_cookie(response, "access_token", access_token, 7200)
    _set_auth_cookie(response, "refresh_token", refresh_token, 604800)
    # Trace the successful login (history visible by superadmin)
    await _record_login_attempt(email, user, request, success=True)
    # Update last_login_at on the user document
    try:
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"last_login_at": datetime.now(timezone.utc).isoformat()}}
        )
    except Exception:
        pass
    return user_response(user)


async def _record_login_attempt(email: str, user: Optional[dict], request: Request, *, success: bool, reason: Optional[str] = None):
    """Inserts a row in db.login_history for audit purposes.
    Captures IP (X-Forwarded-For or client.host) + User-Agent (truncated).
    Failures are also tracked (security audit)."""
    try:
        ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "")
            or ""
        )
        ua = (request.headers.get("user-agent") or "")[:300]
        doc = {
            "id": str(uuid.uuid4()),
            "email": email,
            "user_id": str(user["_id"]) if user else None,
            "role": user.get("role") if user else None,
            "user_name": user.get("name") if user else None,
            "parent_syndic_id": user.get("parent_syndic_id") if user else None,
            "ip": ip,
            "user_agent": ua,
            "success": bool(success),
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.login_history.insert_one(doc)
    except Exception as e:
        import logging
        logging.warning(f"login_history insert failed: {e}")


class FirstSetPasswordInput(BaseModel):
    email: str
    new_password: str


@auth_router.post("/check-must-change-password")
async def check_must_change_password(data: dict):
    """Public endpoint: tells whether the given email must set its password
    on first connection. Used by the frontend to show the proper UI."""
    email = (data.get("email") or "").lower().strip()
    if not email:
        return {"must_change_password": False, "exists": False}
    user = await db.users.find_one({"email": email}, {"_id": 0, "must_change_password": 1})
    if not user:
        return {"must_change_password": False, "exists": False}
    return {"must_change_password": bool(user.get("must_change_password", False)),
            "exists": True}


@auth_router.post("/first-set-password")
async def first_set_password(data: FirstSetPasswordInput, response: Response):
    """Public endpoint: lets a user with must_change_password=True
    define their password for the first time and authenticates them."""
    email = data.email.lower().strip()
    if not data.new_password or len(data.new_password) < 6:
        raise HTTPException(400, "Le mot de passe doit contenir au moins 6 caracteres")
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(404, "Utilisateur non trouve")
    if user.get("is_suspended"):
        raise HTTPException(403, "Votre acces a la plateforme a ete suspendu. Contactez votre syndic.")
    if not user.get("must_change_password"):
        raise HTTPException(400, "Ce compte a deja un mot de passe defini. Utilisez la connexion classique.")
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": hash_password(data.new_password),
                  "must_change_password": False,
                  "password_set_at": datetime.now(timezone.utc).isoformat()}}
    )
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    _set_auth_cookie(response, "access_token", access_token, 7200)
    _set_auth_cookie(response, "refresh_token", refresh_token, 604800)
    return user_response(user)

@auth_router.post("/register")
@limiter.limit("5/minute")
async def register(data: RegisterInput, request: Request, response: Response):
    email = data.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Cet email existe deja")
    doc = {
        "email": email,
        "password_hash": hash_password(data.password),
        "name": data.name,
        "role": "owner",
        "copropriete_ids": [],
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = await db.users.insert_one(doc)
    user_id = str(result.inserted_id)
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    _set_auth_cookie(response, "access_token", access_token, 7200)
    _set_auth_cookie(response, "refresh_token", refresh_token, 604800)
    return {"id": user_id, "email": email, "name": data.name, "role": "owner", "copropriete_ids": []}

@auth_router.get("/me")
async def get_me(request: Request):
    user = await get_current_user(request)
    return user_response(user)

@auth_router.put("/me")
async def update_me(data: MeUpdateInput, request: Request):
    """Self-service profile update : tout utilisateur peut modifier SON propre profil
    (nom + mot de passe). Ne touche PAS au role ni aux copropriete_ids."""
    user = await get_current_user(request)
    update = {}
    if data.name and data.name.strip():
        update["name"] = data.name.strip()
    # Password change requires current_password validation
    if data.new_password:
        if len(data.new_password) < 6:
            raise HTTPException(400, "Le nouveau mot de passe doit contenir au moins 6 caracteres")
        if not data.current_password:
            raise HTTPException(400, "Mot de passe actuel requis pour changer le mot de passe")
        current_hash = user.get("password_hash") or ""
        if not current_hash:
            # Re-fetch from DB (user dict from get_current_user has password_hash popped)
            full = await db.users.find_one({"_id": ObjectId(user["_id"])})
            current_hash = (full or {}).get("password_hash", "")
        if not verify_password(data.current_password, current_hash):
            raise HTTPException(400, "Mot de passe actuel incorrect")
        update["password_hash"] = hash_password(data.new_password)
        update["must_change_password"] = False
        update["password_set_at"] = datetime.now(timezone.utc).isoformat()
    if not update:
        raise HTTPException(400, "Aucun champ a modifier")
    await db.users.update_one({"_id": ObjectId(user["_id"])}, {"$set": update})
    updated = await db.users.find_one({"_id": ObjectId(user["_id"])})
    return user_response(updated)

@auth_router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Deconnecte"}

@auth_router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access_token = create_access_token(str(user["_id"]), user["email"])
        _set_auth_cookie(response, "access_token", access_token, 7200)
        return {"message": "Token refreshed"}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ---------- PASSWORD RESET (forgot password) ----------
# Public endpoints: enumeration-safe (always return 200 OK with a generic message
# whether the email exists or not). Tokens are single-use, 1h TTL, sha256-hashed
# at rest. Rate-limited by IP via login_attempts collection.

class ForgotPasswordInput(BaseModel):
    email: str


class ResetPasswordInput(BaseModel):
    token: str
    new_password: str


def _hash_reset_token(raw_token: str) -> str:
    import hashlib
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def _check_forgot_rate_limit(ip: str) -> bool:
    """Returns True if the request is allowed (under 5 per hour for this IP)."""
    if not ip:
        return True
    from datetime import datetime, timezone, timedelta
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    count = await db.password_reset_attempts.count_documents({
        "ip": ip,
        "created_at": {"$gte": one_hour_ago},
    })
    return count < 5


@auth_router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(data: ForgotPasswordInput, request: Request):
    """Always returns 200 OK with the same message regardless of whether the
    email exists, to prevent user enumeration attacks. Sends a reset link
    via Microsoft Graph if the email matches a real, non-suspended user."""
    email = (data.email or "").lower().strip()
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "")
          or "")
    generic = {"message": "Si cette adresse correspond a un compte, un email de reinitialisation a ete envoye."}
    if not email or "@" not in email:
        return generic
    # Rate limit (5 requests / IP / hour) to prevent email spam abuse
    allowed = await _check_forgot_rate_limit(ip)
    await db.password_reset_attempts.insert_one({
        "ip": ip, "email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    if not allowed:
        # Still return generic to avoid revealing rate-limit existence
        return generic
    user = await db.users.find_one({"email": email})
    if not user or user.get("is_suspended"):
        # Do NOT reveal: just return generic (enumeration-safe)
        return generic
    # Generate token (raw -> sent in email, sha256 -> stored)
    import secrets
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await db.password_reset_tokens.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": str(user["_id"]),
        "token_hash": token_hash,
        "expires_at": expires_at,  # BSON datetime so TTL index can act
        "consumed_at": None,
        "created_ip": ip,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Build reset link + send email asynchronously
    try:
        from graph_email import is_configured, send_html_email, build_password_reset_email
        import asyncio
        if is_configured():
            frontend_url = os.environ.get("FRONTEND_URL", "")
            reset_url = f"{frontend_url}/reset-password?token={raw_token}"
            subject, html = build_password_reset_email(
                recipient_name=user.get("name", "Utilisateur"),
                reset_url=reset_url,
                expires_minutes=60,
            )
            asyncio.create_task(send_html_email([email], subject, html))
        else:
            logger.warning(f"MSGRAPH not configured; password-reset requested for {email} but email cannot be sent")
    except Exception as e:
        logger.warning(f"Reset email send failed for {email}: {e}")
    return generic


@auth_router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(data: ResetPasswordInput, request: Request, response: Response):
    """Validates the reset token, updates the password, marks token consumed,
    logs the user in directly."""
    if not data.token or len(data.token) < 16:
        raise HTTPException(400, "Lien invalide")
    if not data.new_password or len(data.new_password) < 6:
        raise HTTPException(400, "Le mot de passe doit contenir au moins 6 caracteres")
    token_hash = _hash_reset_token(data.token)
    now = datetime.now(timezone.utc)
    token_doc = await db.password_reset_tokens.find_one({"token_hash": token_hash})
    if not token_doc:
        raise HTTPException(400, "Lien invalide ou expire")
    if token_doc.get("consumed_at"):
        raise HTTPException(400, "Ce lien a deja ete utilise")
    expires_at = token_doc.get("expires_at")
    # expires_at may be a BSON datetime (preferred) or ISO string (legacy)
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            expires_at = None
    if expires_at and expires_at.tzinfo is None:
        # MongoDB returns naive datetimes; treat them as UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at < now:
        raise HTTPException(400, "Lien expire. Demandez un nouveau lien.")
    user = await db.users.find_one({"_id": ObjectId(token_doc["user_id"])})
    if not user:
        raise HTTPException(400, "Lien invalide")
    if user.get("is_suspended"):
        raise HTTPException(403, "Votre acces a la plateforme a ete suspendu. Contactez votre syndic.")
    # Update password and clear any "must_change_password" flag
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "password_hash": hash_password(data.new_password),
            "must_change_password": False,
            "password_set_at": now.isoformat(),
        }}
    )
    # Mark token consumed
    await db.password_reset_tokens.update_one(
        {"_id": token_doc["_id"]},
        {"$set": {"consumed_at": now.isoformat()}}
    )
    # Invalidate any other unconsumed tokens for this user (defense in depth)
    await db.password_reset_tokens.update_many(
        {"user_id": str(user["_id"]), "consumed_at": None, "_id": {"$ne": token_doc["_id"]}},
        {"$set": {"consumed_at": now.isoformat()}}
    )
    # Log the user in directly (same as first-set-password flow)
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, user["email"])
    refresh_token = create_refresh_token(user_id)
    _set_auth_cookie(response, "access_token", access_token, 7200)
    _set_auth_cookie(response, "refresh_token", refresh_token, 604800)
    return user_response(user)


# Dashboard
@app.get("/api/dashboard/stats")
async def dashboard_stats(request: Request, copropriete_id: Optional[str] = None):
    """Stats du dashboard, OBLIGATOIREMENT scopees a une ACP pour respecter
    les chinese walls. Sans copropriete_id (ex: dashboard d'accueil
    multi-ACP), renvoie uniquement le nombre global de coproprietes
    accessibles et zero pour le reste (pas de fuite cross-ACP)."""
    user = await get_current_user(request)

    # Resolve scope from header X-Copropriete-Id if not provided
    if not copropriete_id:
        copropriete_id = request.headers.get("X-Copropriete-Id") or None

    # Verify user has access to this ACP (only platform superadmin bypasses chinese walls)
    role = user.get("role", "")
    user_copro_ids = user.get("copropriete_ids", []) or []
    is_super = is_superadmin_only(role)
    if copropriete_id and not is_super and copropriete_id not in user_copro_ids:
        raise HTTPException(403, "Acces refuse a cette copropriete")

    # Build query strictly scoped to the ACP
    if copropriete_id:
        q = {"copropriete_id": copropriete_id}
        # Owners scope: an owner appears in this ACP if at least one of his lots is in this ACP
        lot_owner_ids = await db.lots.distinct("owner_id", {"copropriete_id": copropriete_id})
        owners_count = await db.owners.count_documents({"id": {"$in": lot_owner_ids}})
        lots_count = await db.lots.count_documents(q)
        tenants_count = await db.tenants.count_documents(q)
        invoices_count = await db.invoices.count_documents(q)
        unpaid = await db.invoices.count_documents({**q, "status": "unpaid"})
        # iter90i4 : total_charges DOIT correspondre exactement au total de la
        # page "Liste des depenses" (`compute_expense_rows`). Avant : simple
        # agregation `sum(total_amount) WHERE status IN [paid, unpaid]` qui
        # excluait les notes de credit, les factures non lettrees et surtout
        # ne comptabilisait pas les ecritures FI/OD directes sur classe 6.
        # Aligner les deux vues evite les ecarts perturbants pour le syndic.
        try:
            from expense_rows import compute_expense_rows
            _rows, _totals = await compute_expense_rows(db, copropriete_id)
            total_charges = float(_totals.get("total", 0) or 0)
        except Exception:
            # Fallback defensif : garde l'ancienne agregation si le calcul
            # riche echoue (evite un dashboard casse).
            pipeline = [
                {"$match": {**q, "status": {"$in": ["paid", "unpaid"]}}},
                {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}},
            ]
            agg = await db.invoices.aggregate(pipeline).to_list(1)
            total_charges = agg[0]["total"] if agg else 0
        recent_entries = await db.journal_entries.find(
            q, {"_id": 0}
        ).sort("created_at", -1).to_list(5)
    else:
        # No ACP scoped : only safe global counters (no leakage)
        owners_count = 0
        lots_count = 0
        tenants_count = 0
        invoices_count = 0
        unpaid = 0
        total_charges = 0
        recent_entries = []

    # Coproprietes count visible to the user (multi-ACP landing)
    if is_super:
        copro_count = await db.coproprietes.count_documents({})
    else:
        copro_count = await db.coproprietes.count_documents({"id": {"$in": user_copro_ids}})

    return {
        "copropriete_id": copropriete_id or "",
        "coproprietes_count": copro_count,
        "owners_count": owners_count,
        "lots_count": lots_count,
        "tenants_count": tenants_count,
        "invoices_count": invoices_count,
        "unpaid_invoices": unpaid,
        "total_charges": round(total_charges, 2),
        "recent_entries": recent_entries
    }

@app.get("/api/health")
@app.get("/api/health/live")
async def health_liveness():
    """iter90gz : liveness probe - retourne 200 tant que le process repond.
    Utilise par K8s / Cloudflare / UptimeRobot pour verifier que l'app tourne.
    N'exige PAS d'auth. Aucun acces DB (rapide, sans dependance externe)."""
    return {
        "status": "ok",
        "service": "nextge-copro",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/health/ready")
async def health_readiness():
    """iter90gz : readiness probe - verifie que l'app peut servir du trafic.
    Ping MongoDB (max 2s), mesure la latence, expose le statut des caches.
    - 200 si MongoDB repond en < 2s.
    - 503 sinon (K8s / Cloudflare enleveront l'instance du load balancer).
    N'exige PAS d'auth."""
    import asyncio as _asyncio
    import time as _time
    checks = {"service": "nextge-copro", "timestamp": datetime.now(timezone.utc).isoformat()}
    # 1. Ping MongoDB avec timeout dur
    t0 = _time.perf_counter()
    mongo_ok = False
    mongo_error = None
    try:
        await _asyncio.wait_for(db.command("ping"), timeout=2.0)
        mongo_ok = True
    except _asyncio.TimeoutError:
        mongo_error = "timeout (>2s)"
    except Exception as _e:
        mongo_error = str(_e)[:100]
    mongo_ms = round((_time.perf_counter() - t0) * 1000, 1)
    checks["mongodb"] = {"ok": mongo_ok, "latency_ms": mongo_ms, "error": mongo_error}

    # 2. Statut caches in-memory (bilan + duplicates-audit) - non-fatal, indicatif
    try:
        from routes.reports import _bilan_cache_stats  # type: ignore
        checks["bilan_cache"] = _bilan_cache_stats()
    except Exception:
        checks["bilan_cache"] = {"available": False}
    try:
        from routes.admin import _dup_audit_cache_stats  # type: ignore
        checks["duplicates_audit_cache"] = _dup_audit_cache_stats()
    except Exception:
        checks["duplicates_audit_cache"] = {"available": False}

    status_code = 200 if mongo_ok else 503
    checks["status"] = "ok" if mongo_ok else "degraded"
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=checks)


@app.get("/api/dashboard/health-audit")
async def dashboard_health_audit(request: Request, copropriete_id: Optional[str] = None,
                                  days_threshold: int = 60):
    """Audit sante comptable de l'ACP. Detection des anomalies (factures impayees,
    doublons, comptes orphelins, ecritures desequilibrees, owners en retard) avec
    score sur 100."""
    from health_audit import compute_health_audit
    user = await get_current_user(request)
    if not copropriete_id:
        copropriete_id = request.headers.get("X-Copropriete-Id") or None
    if not copropriete_id or copropriete_id == "all":
        raise HTTPException(400, "copropriete_id requis - chinese walls strict")
    role = user.get("role", "")
    if role not in ("superadmin", "admin", "syndic") and copropriete_id not in (user.get("copropriete_ids") or []):
        raise HTTPException(403, "Acces refuse a cette copropriete")
    # P0 fix (iter90fo) : garde-fou anti-timeout. Meme optimise, on ne veut
    # JAMAIS qu'un audit lourd puisse geler l'app (mode single-worker) ni
    # provoquer un timeout Cloudflare 502 (qui coupe la connexion brutalement
    # et affecte TOUS les clients pendant qu'il tourne). Si le calcul depasse
    # 20s, on retourne une erreur degradee propre plutot que de laisser
    # la requete pendre jusqu'au timeout du reverse-proxy.
    import asyncio
    try:
        return await asyncio.wait_for(
            compute_health_audit(db, copropriete_id, days_threshold=days_threshold),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(503, "Audit sante comptable temporairement indisponible (volume de donnees trop important) - reessayez dans quelques instants")

# Admin seed
async def seed_admin():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@copro.be")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Super Administrateur",
            "role": "superadmin",
            "copropriete_ids": [],
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        logger.info(f"Superadmin created: {admin_email}")
    else:
        updates = {}
        if existing.get("role") not in ("superadmin",):
            updates["role"] = "superadmin"
        if "copropriete_ids" not in existing:
            updates["copropriete_ids"] = []
        if not verify_password(admin_password, existing["password_hash"]):
            updates["password_hash"] = hash_password(admin_password)
        if updates:
            await db.users.update_one({"email": admin_email}, {"$set": updates})

async def seed_pcmn():
    """Legacy: PCMN was global. Now per-ACP, seeded on ACP creation. No-op kept for safety."""
    return

@app.on_event("startup")
async def startup():
    # iter90as : chaque create_index dans son propre try pour eviter qu'un
    # echec (index existant avec definition differente, timeout Atlas, etc.)
    # crash tout le startup event et bloque la readiness probe K8s.
    try:
        await db.users.create_index("email", unique=True)
    except Exception as _e:
        print(f"[startup] users.email index skipped: {_e}")
    try:
        await db.owners.create_index("vcs_code", sparse=True)
    except Exception as _e:
        print(f"[startup] owners.vcs_code index skipped: {_e}")
    # P0 fix (iter90fo) : index critiques manquants sur copropriete_id.
    # Sans ces index, chaque requete filtree par copropriete_id (invoices,
    # journal_entries, suppliers) fait un FULL COLLECTION SCAN sur TOUTE
    # la base multi-tenant (tous les clients), ce qui degrade et finit par
    # crasher/timeout le dashboard (health-audit) quand la base grossit.
    try:
        await db.journal_entries.create_index([("copropriete_id", 1), ("journal_type", 1)])
        await db.invoices.create_index([("copropriete_id", 1), ("status", 1)])
        await db.suppliers.create_index("copropriete_id")
        await db.owners.create_index("copropriete_ids")
        await db.fund_calls.create_index("copropriete_id")
    except Exception as _e:
        print(f"[startup] copropriete_id indexes skipped: {_e}")
    # iter90gk : UNICITE STRICTE des comptes PCMN par ACP (couple copro+number).
    # Sans cet index, le meme numero de compte pouvait etre insere plusieurs
    # fois pour la meme ACP par des code paths differents (wizard AN, module
    # banking, assign_supplier_account, etc.) -> doublons dans le Bilan +
    # divergence avec la Balance des Tiers. Regle metier utilisateur : "il
    # est interdit de dupliquer les comptes, c'est bloquant en comptabilite".
    try:
        await db.pcmn_accounts.create_index(
            [("copropriete_id", 1), ("number", 1)], unique=True, name="uq_pcmn_copro_number"
        )
    except Exception as _e:
        print(f"[startup] pcmn_accounts unique index skipped: {_e}")
    # iter90ih -> iter90ii : UNICITE STRICTE des natures de depenses par
    # (ACP, NOM). Regle metier utilisateur revisee :
    #   * Interdit : 2 natures avec le meme NOM dans une meme ACP
    #   * Autorise : 2 natures avec le meme COMPTE COMPTABLE (ex: "Assurance
    #     incendie parties communes" + "Assurance incendie parties privees"
    #     peuvent toutes deux pointer sur 6140).
    # NOTE : `account_number` n'est donc PAS unique. Seul `name` l'est.
    # Idempotent : drop de l'ancien index (uq_expcat_copro_account) s'il
    # existe encore, puis creation du nouveau.
    try:
        # Drop l'ancien index base sur account_number (iter90ih initial)
        try:
            await db.expense_categories.drop_index("uq_expcat_copro_account")
        except Exception:
            pass
        await db.expense_categories.create_index(
            [("copropriete_id", 1), ("name", 1)],
            unique=True,
            name="uq_expcat_copro_name",
            partialFilterExpression={"name": {"$type": "string", "$gt": ""}},
        )
    except Exception as _e:
        print(f"[startup] expense_categories unique index skipped: {_e}")

    # iter90ii : UNICITE des FOURNISSEURS par (ACP, nom). Un meme nom de
    # fournisseur ne peut apparaitre qu'une seule fois dans une ACP.
    # NB : `bce_number` reste unique globalement (index deja en place).
    try:
        await db.suppliers.create_index(
            [("copropriete_id", 1), ("name", 1)],
            unique=True,
            name="uq_supplier_copro_name",
            partialFilterExpression={"name": {"$type": "string", "$gt": ""}},
        )
    except Exception as _e:
        print(f"[startup] suppliers unique name index skipped: {_e}")

    # iter90is (Chinese Wall strict) : UNICITE TVA **par ACP** (plus globale).
    # Precedemment (iter90im) : la TVA etait cle unique globale. Ceci est
    # incompatible avec le chinese wall qui autorise 2 fiches distinctes
    # pour la meme entreprise dans 2 ACPs. Passage per-ACP.
    try:
        # Drop l'ancien index global (best-effort).
        try:
            await db.suppliers.drop_index("uq_supplier_vat_global")
        except Exception:
            pass
        await db.suppliers.create_index(
            [("copropriete_id", 1), ("vat_number", 1)],
            unique=True,
            name="uq_supplier_copro_vat",
            partialFilterExpression={"vat_number": {"$type": "string", "$gt": ""}},
        )
    except Exception as _e:
        print(f"[startup] suppliers unique vat index skipped: {_e}")

    # iter90ii : UNICITE des PROPRIETAIRES par (ACP, auxiliary_code). Un
    # meme code auxiliaire ne peut apparaitre qu'une seule fois dans une
    # ACP. Comme `copropriete_ids` est un array, MongoDB cree un multikey
    # index unique : chaque valeur du array combinee avec auxiliary_code
    # doit etre unique. En clair : si 2 owners partagent le meme aux_code
    # ET ont au moins une ACP en commun, l'insertion est refusee.
    try:
        await db.owners.create_index(
            [("copropriete_ids", 1), ("auxiliary_code", 1)],
            unique=True,
            name="uq_owner_copro_aux",
            partialFilterExpression={"auxiliary_code": {"$type": "string", "$gt": ""}},
        )
    except Exception as _e:
        print(f"[startup] owners unique aux index skipped: {_e}")
    # iter90is (Chinese Wall strict) : UNICITE BCE **par ACP** (plus globale).
    # Chaque ACP a sa propre fiche pour chaque BCE ; le partage cross-ACP
    # est supprime. Deux fiches "Engie" (BE0403201185) peuvent coexister
    # dans 2 ACPs distinctes, mais pas 2 fois dans la meme ACP.
    # `partialFilterExpression` ignore les fiches sans BCE (bce_number="").
    try:
        # Drop l'ancien index global (best-effort).
        try:
            await db.suppliers.drop_index("uq_supplier_bce")
        except Exception:
            pass
        await db.suppliers.create_index(
            [("copropriete_id", 1), ("bce_number", 1)],
            unique=True, name="uq_supplier_copro_bce",
            partialFilterExpression={"bce_number": {"$type": "string", "$gt": ""}},
        )
    except Exception as _e:
        print(f"[startup] suppliers.uq_supplier_copro_bce index skipped: {_e}")
    # iter87 : TTL index on invoice_bundle_sessions for auto-cleanup of bundle
    # PDF sessions after 24h (uses `expires_at` ISODate field set on creation).
    try:
        await db.invoice_bundle_sessions.create_index("expires_at", expireAfterSeconds=0)
    except Exception:
        pass
    # iter90 : TTL index on password_reset_tokens (auto-delete expired rows).
    # expires_at MUST be a BSON datetime for the TTL monitor to pick it up.
    try:
        await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
        await db.password_reset_tokens.create_index("token_hash", unique=True)
        await db.password_reset_attempts.create_index("created_at")
        # iter90b : audit journal for owner access operations
        await db.owner_access_audit.create_index("owner_id")
        await db.owner_access_audit.create_index([("owner_id", 1), ("created_at", -1)])
    except Exception:
        pass
    try:
        await seed_admin()
    except Exception as _e:
        print(f"[startup] seed_admin failed: {_e}")
    try:
        await seed_pcmn()
    except Exception as _e:
        print(f"[startup] seed_pcmn failed: {_e}")

    # iter90cf : SYNC db.mutations depuis lot.mutations (idempotent).
    # Historiquement, mutate_lot ne peuplait que lot.mutations, laissant la
    # collection db.mutations vide. Les fonctions _rebind_owner_at_call_date
    # et _resolve_owner_at_date (fund_calls.py) lisaient dans db.mutations,
    # donc les mutations restaient invisibles lors de la generation d'appels
    # post-mutation. Ce sync corrige les donnees existantes en production.
    try:
        synced = 0
        async for lot_doc in db.lots.find(
            {"mutations": {"$exists": True, "$ne": []}}, {"_id": 0, "id": 1, "copropriete_id": 1, "mutations": 1}
        ):
            for mr in (lot_doc.get("mutations") or []):
                if not mr.get("id") or not mr.get("date"):
                    continue
                doc = {
                    "id": mr["id"],
                    "copropriete_id": lot_doc.get("copropriete_id", ""),
                    "lot_id": lot_doc["id"],
                    "from_owner_id": mr.get("old_owner_id", ""),
                    "to_owner_id": mr.get("new_owner_id", ""),
                    "sale_date": mr.get("date", ""),
                    "roulement_quota": mr.get("roulement_quota", 0.0),
                    "current_period_prorata": mr.get("current_period_prorata",
                                                     mr.get("prorata_provisions", 0.0)),
                    "total_transfer": mr.get("total_transfer", 0.0),
                    "journal_entry_ids": mr.get("journal_entry_ids") or (
                        [mr.get("journal_entry_id")] if mr.get("journal_entry_id") else []
                    ),
                    "created_at": mr.get("created_at", ""),
                }
                await db.mutations.update_one({"id": mr["id"]}, {"$set": doc}, upsert=True)
                synced += 1
        if synced:
            print(f"[startup][iter90cf] db.mutations synced from lot.mutations: {synced} entries")
    except Exception as _e:
        print(f"[startup][iter90cf] mutation sync skipped: {_e}")

    # iter90g2 : seed la boite mail welcome@goodexperienceproperties.be pour
    # le syndic gerald@gep.be. Idempotent : n'ajoute que si absent. Corrige
    # le bug "Graph 404 ErrorInvalidUser" ou l'envoi echouait car le fallback
    # utilisait l'email de compte (`gerald@gep.be`) qui n'est pas une mailbox
    # Microsoft Graph valide dans le tenant.
    try:
        gep_user = await db.users.find_one(
            {"email": "gerald@gep.be"}, {"_id": 1, "authorized_mailboxes": 1}
        )
        if gep_user:
            boxes = list(gep_user.get("authorized_mailboxes") or [])
            wanted_addr = "welcome@goodexperienceproperties.be"
            has_wanted = any(
                (b.get("address") or "").lower() == wanted_addr for b in boxes
            )
            if not has_wanted:
                # Si aucune boite n'a `default=True` -> nouvelle boite = default
                # Sinon respect de l'existant : la nouvelle est ajoutee non-default
                has_default = any(b.get("default", False) for b in boxes)
                boxes.append({
                    "address": wanted_addr,
                    "display_name": "GEP - Good Experience Properties",
                    "active": True,
                    "default": not has_default,
                })
                await db.users.update_one(
                    {"_id": gep_user["_id"]},
                    {"$set": {"authorized_mailboxes": boxes}},
                )
                print(f"[startup][iter90g2] mailbox seeded for gerald@gep.be: {wanted_addr}")
    except Exception as _e:
        print(f"[startup][iter90g2] mailbox seed skipped: {_e}")

    # iter90i7 : Self-heal automatique des doublons AP+VE sur les appels de
    # fonds. Bug historique : la route legacy /fund-calls/{id}/generate-entries
    # creait un JE AP en plus du VE deja auto-genere -> double-comptage du
    # fonds de reserve/roulement (bilan surevalue de 2x l'appel).
    # Ce heal est IDEMPOTENT : cherche les paires (AP.fund_call_id == VE.source_id)
    # et supprime l'AP. Peut etre desactive par IT7_HEAL_DISABLED=1.
    try:
        if os.environ.get("IT7_HEAL_DISABLED") != "1":
            removed_i7 = 0
            async for ap in db.journal_entries.find(
                {"journal_type": "AP", "fund_call_id": {"$exists": True}},
                {"_id": 0, "id": 1, "fund_call_id": 1, "reference": 1,
                 "copropriete_id": 1},
            ):
                fcid = ap.get("fund_call_id")
                if not fcid:
                    continue
                ve = await db.journal_entries.find_one(
                    {"source_type": "fund_call", "source_id": fcid,
                     "journal_type": "VE",
                     "reversed": {"$ne": True},
                     "is_reversal": {"$ne": True}},
                    {"_id": 0, "id": 1},
                )
                if ve:
                    r = await db.journal_entries.delete_one({"id": ap["id"]})
                    if r.deleted_count:
                        removed_i7 += 1
            if removed_i7:
                print(f"[startup][iter90i7] AP/VE doublons cleaned: {removed_i7}")
    except Exception as _e:
        print(f"[startup][iter90i7] duplicate AP/VE heal skipped: {_e}")

    # iter90if : renommage des champs lies aux imports externes historiques
    # (Optipro/Sogis) pour retirer les noms de logiciels tiers des donnees.
    try:
        from migrations.iter90if_rename_external_import_fields import run_migration_iter90if
        _if_report = await run_migration_iter90if(db)
        if not _if_report.get("skipped"):
            print(f"[startup][iter90if] external import fields renamed: {_if_report}")
    except Exception as _e:
        print(f"[startup][iter90if] rename skipped: {_e}")

    # iter90as : ecriture test_credentials.md en dev/preview UNIQUEMENT.
    # En production K8s, /app/memory peut ne pas etre writable (filesystem
    # hardened, volume ephemere) -> le crash faisait timeout le readiness probe.
    try:
        os.makedirs("/app/memory", exist_ok=True)
        with open("/app/memory/test_credentials.md", "w") as f:
            f.write("# Test Credentials\n\n")
            f.write(f"## Super Admin\n- Email: {os.environ.get('ADMIN_EMAIL', 'admin@copro.be')}\n- Password: {os.environ.get('ADMIN_PASSWORD', 'admin123')}\n- Role: superadmin\n\n")
            f.write("## Roles: superadmin, syndic, owner\n\n")
            f.write("## Auth Endpoints\n- POST /api/auth/login\n- POST /api/auth/register\n- GET /api/auth/me\n- POST /api/auth/logout\n")
    except Exception as _e:
        print(f"[startup] memory/test_credentials.md write skipped: {_e}")

    # iter90ax : Scheduler backup quotidien 00h00 Europe/Brussels
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from backup_service import create_backup_all_acps

        # Skip scheduler dans les tests (evite les jobs parallels non-desires)
        if not os.environ.get("BACKUP_SCHEDULER_DISABLED"):
            global _backup_scheduler
            _backup_scheduler = AsyncIOScheduler(timezone="Europe/Brussels")

            async def _backup_job():
                try:
                    summary = await create_backup_all_acps(db, source="scheduler")
                    print(f"[backup] daily done: {summary['success']}/{summary['total']}")
                except Exception as e:  # noqa: BLE001
                    print(f"[backup] daily FAILED: {e}")

            _backup_scheduler.add_job(
                _backup_job,
                trigger=CronTrigger(hour=0, minute=0, timezone="Europe/Brussels"),
                id="acp_daily_backup",
                replace_existing=True,
            )
            _backup_scheduler.start()
            print("[startup] backup scheduler started (00:00 Europe/Brussels)")
    except Exception as _e:
        print(f"[startup] backup scheduler skipped: {_e}")

_backup_scheduler = None

@app.on_event("shutdown")
async def shutdown():
    global _backup_scheduler
    if _backup_scheduler:
        try:
            _backup_scheduler.shutdown(wait=False)
        except Exception:
            pass
    client.close()

# Include routers
app.include_router(auth_router)

from routes.properties import create_properties_router
from routes.accounting import create_accounting_router
from routes.invoices import create_invoices_router
from routes.meters import create_meters_router
from routes.banking import create_banking_router
from routes.documents import create_documents_router
from routes.admin import create_admin_router
from routes.coproprietes import create_coproprietes_router
from routes.suppliers import create_suppliers_router
from routes.fiscal import create_fiscal_router
from routes.reports import create_reports_router
from routes.fund_calls import create_fund_calls_router
from routes.demo_seed import create_demo_router
from routes.owner_portal import create_owner_portal_router
from routes.exports import create_exports_router, create_reminders_router
from routes.invoice_ai import create_invoice_ai_router
from routes.invoice_templates import create_invoice_templates_router, try_apply_supplier_template
from routes.expense_categories import create_expense_categories_router
from routes.team import create_team_router
from routes.import_wizard import create_import_wizard_router
from routes.duplicates import create_duplicates_router
from routes.owner_access import create_owner_access_router
from routes.support import create_support_router
from routes.legal import create_legal_router
from routes.communication import create_communication_router
from routes.syndic_config import create_syndic_config_router
from routes.email_templates import create_email_templates_router
from routes.backups import create_backups_router
from routes.release_notes import create_release_notes_router
from routes.documentation import create_documentation_router

app.include_router(create_properties_router(db))
app.include_router(create_accounting_router(db))
app.include_router(create_invoices_router(db))
app.include_router(create_meters_router(db))
app.include_router(create_banking_router(db))
app.include_router(create_documents_router(db))
app.include_router(create_admin_router(db))
app.include_router(create_team_router(db))
app.include_router(create_import_wizard_router(db))
app.include_router(create_coproprietes_router(db))
app.include_router(create_suppliers_router(db))
app.include_router(create_fiscal_router(db))
app.include_router(create_reports_router(db))
app.include_router(create_fund_calls_router(db))
app.include_router(create_demo_router(db))
app.include_router(create_owner_portal_router(db))
app.include_router(create_exports_router(db))
app.include_router(create_reminders_router(db))
app.include_router(create_invoice_ai_router(db))
app.include_router(create_invoice_templates_router(db))
app.include_router(create_expense_categories_router(db))
app.include_router(create_duplicates_router(db))
app.include_router(create_owner_access_router(db))
app.include_router(create_support_router(db))
app.include_router(create_legal_router(db))
app.include_router(create_communication_router(db))
app.include_router(create_syndic_config_router(db))
app.include_router(create_email_templates_router(db))
app.include_router(create_backups_router(db))
app.include_router(create_release_notes_router(db))
app.include_router(create_documentation_router(db))
