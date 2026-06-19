from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, UploadFile, File
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from pymongo import ReturnDocument

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="CoproManager")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get('FRONTEND_URL', 'http://localhost:3000')],
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
}

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
            {"_id": 0, "role": 1, "copropriete_ids": 1, "email": 1}
        )
    except Exception:
        user_doc = None
    if not user_doc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "User not found"})
    role = user_doc.get("role", "owner")
    request.state.user_role = role
    request.state.user_copropriete_ids = user_doc.get("copropriete_ids", [])

    # ---- CHINESE WALL GLOBAL : isolation stricte entre syndics ----
    # Si la requete porte un copropriete_id (param OU header X-Copropriete-Id),
    # verifier que l'utilisateur (non-superadmin) a bien acces a cette ACP.
    # Le superadmin/admin voient tout (gestion plateforme).
    if role not in ("superadmin", "admin") and path not in RBAC_EXEMPT_PATHS:
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
            allowed_owner_prefixes = ("/api/owner/", "/api/auth/")
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

def user_response(user_doc):
    """Build a safe user response dict from a MongoDB user document."""
    return {
        "id": str(user_doc["_id"]) if isinstance(user_doc.get("_id"), ObjectId) else user_doc.get("_id", user_doc.get("id", "")),
        "email": user_doc["email"],
        "name": user_doc["name"],
        "role": user_doc["role"],
        "copropriete_ids": user_doc.get("copropriete_ids", []),
    }

@auth_router.post("/login")
async def login(data: LoginInput, response: Response):
    email = data.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    # Block login if the user must define their password first
    if user.get("must_change_password"):
        raise HTTPException(
            status_code=403,
            detail={"code": "PASSWORD_SETUP_REQUIRED",
                    "message": "Vous devez definir votre mot de passe lors de la premiere connexion."}
        )
    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="lax", max_age=7200, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=False, samesite="lax", max_age=604800, path="/")
    return user_response(user)


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
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="lax", max_age=7200, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=False, samesite="lax", max_age=604800, path="/")
    return user_response(user)

@auth_router.post("/register")
async def register(data: RegisterInput, response: Response):
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
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="lax", max_age=7200, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=False, samesite="lax", max_age=604800, path="/")
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
        response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="lax", max_age=7200, path="/")
        return {"message": "Token refreshed"}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

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
    return await compute_health_audit(db, copropriete_id, days_threshold=days_threshold)

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
    await db.users.create_index("email", unique=True)
    await db.owners.create_index("vcs_code", sparse=True)
    await seed_admin()
    await seed_pcmn()
    os.makedirs("/app/memory", exist_ok=True)
    with open("/app/memory/test_credentials.md", "w") as f:
        f.write("# Test Credentials\n\n")
        f.write(f"## Super Admin\n- Email: {os.environ.get('ADMIN_EMAIL', 'admin@copro.be')}\n- Password: {os.environ.get('ADMIN_PASSWORD', 'admin123')}\n- Role: superadmin\n\n")
        f.write("## Roles: superadmin, syndic, owner\n\n")
        f.write("## Auth Endpoints\n- POST /api/auth/login\n- POST /api/auth/register\n- GET /api/auth/me\n- POST /api/auth/logout\n")

@app.on_event("shutdown")
async def shutdown():
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
from routes.expense_categories import create_expense_categories_router
from routes.team import create_team_router

app.include_router(create_properties_router(db))
app.include_router(create_accounting_router(db))
app.include_router(create_invoices_router(db))
app.include_router(create_meters_router(db))
app.include_router(create_banking_router(db))
app.include_router(create_documents_router(db))
app.include_router(create_admin_router(db))
app.include_router(create_team_router(db))
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
app.include_router(create_expense_categories_router(db))
