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

JWT_ALGORITHM = "HS256"
ROLES = ["superadmin", "syndic", "owner"]

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
    return role in ("superadmin", "admin")

def can_manage(role: str) -> bool:
    return role in ("superadmin", "admin", "syndic")

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
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
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
    return await get_current_user(request)

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
async def dashboard_stats(request: Request):
    await get_current_user(request)
    owners_count = await db.owners.count_documents({})
    lots_count = await db.lots.count_documents({})
    tenants_count = await db.tenants.count_documents({})
    invoices_count = await db.invoices.count_documents({})
    unpaid = await db.invoices.count_documents({"status": "unpaid"})
    pipeline = [{"$match": {"status": {"$in": ["paid", "unpaid"]}}}, {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}]
    agg = await db.invoices.aggregate(pipeline).to_list(1)
    total_charges = agg[0]["total"] if agg else 0
    recent_entries = await db.journal_entries.find({}, {"_id": 0}).sort("created_at", -1).to_list(5)
    return {
        "owners_count": owners_count,
        "lots_count": lots_count,
        "tenants_count": tenants_count,
        "invoices_count": invoices_count,
        "unpaid_invoices": unpaid,
        "total_charges": round(total_charges, 2),
        "recent_entries": recent_entries
    }

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
    from pcmn_data import PCMN_ACCOUNTS
    count = await db.pcmn_accounts.count_documents({})
    if count == 0:
        await db.pcmn_accounts.insert_many(PCMN_ACCOUNTS)
        logger.info(f"Seeded {len(PCMN_ACCOUNTS)} PCMN accounts")

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

app.include_router(create_properties_router(db))
app.include_router(create_accounting_router(db))
app.include_router(create_invoices_router(db))
app.include_router(create_meters_router(db))
app.include_router(create_banking_router(db))
app.include_router(create_documents_router(db))
app.include_router(create_admin_router(db))
app.include_router(create_coproprietes_router(db))
app.include_router(create_suppliers_router(db))
app.include_router(create_fiscal_router(db))
app.include_router(create_reports_router(db))
app.include_router(create_fund_calls_router(db))
