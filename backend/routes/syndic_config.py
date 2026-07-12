"""iter90av : Configuration syndic (identite cabinet + config email).

Objectifs :
1. Permettre a chaque syndic de configurer son identite (nom, adresse, mentions
   legales) et son logo -> reutilise dans les PDF et les emails.
2. Configurer un provider email (MS Graph ou SMTP) avec credentials chiffres au
   repos (Fernet, cle EMAIL_CONFIG_KEY).
3. Bouton "test email" pour valider la config avant activation.
4. Superadmin peut gerer les config de tous les syndics.
5. Fallback : si un syndic n'a pas de config, on utilise les env vars globaux
   AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET.

Chinese wall :
- Un syndic voit/edite uniquement sa propre config (`user_id = current`).
- Les gestionnaires enfants (parent_syndic_id) LISENT la config du parent
  (pour envoyer depuis les boites du cabinet) mais ne peuvent pas la modifier.
- Superadmin voit/edite tous les configs via /api/admin/syndic-config/*.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, EmailStr, Field

from crypto_utils import decrypt_secret, encrypt_secret, is_encrypted, redact
from gridfs_storage import GridFSStorage

logger = logging.getLogger("syndic_config")


class SyndicConfigUpdate(BaseModel):
    legal_name: Optional[str] = None
    display_name: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    bce: Optional[str] = None
    tva: Optional[str] = None
    ipi_number: Optional[str] = None  # agrement syndic professionnel
    legal_mentions: Optional[str] = Field(
        default=None,
        description="Texte pied de page PDF (RGPD, agrement, TVA, iban...)",
    )


class EmailConfigUpdate(BaseModel):
    provider: str = Field(..., description="'graph' | 'smtp' | 'none'")
    graph_tenant_id: Optional[str] = None
    graph_client_id: Optional[str] = None
    graph_client_secret: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = True


class TestEmailPayload(BaseModel):
    from_mailbox: str
    to: EmailStr


def _redact_config(cfg: dict) -> dict:
    """Retourne une copie safe-for-UI (secrets masques)."""
    if not cfg:
        return {}
    safe = dict(cfg)
    safe.pop("_id", None)
    for k in ("graph_client_secret", "smtp_password"):
        if safe.get(k):
            safe[k] = redact(safe[k])
    return safe


async def _resolve_syndic_user_id(db, request: Request) -> str:
    """Retourne l'ObjectId string du syndic-owner de la config :
    - si user.role == 'syndic'/'admin'/'superadmin' -> user_id lui-meme
    - si user.role == 'gestionnaire' -> parent_syndic_id
    """
    uid = request.state.user_id
    user = await db.users.find_one({"_id": ObjectId(uid)})
    if not user:
        raise HTTPException(401, "Utilisateur inconnu")
    role = user.get("role", "")
    if role in ("syndic", "admin", "superadmin"):
        return str(user["_id"])
    parent = user.get("parent_syndic_id")
    if parent:
        return str(parent)
    raise HTTPException(403, "Cet utilisateur n'appartient a aucun cabinet syndic")


async def _get_config_for(db, syndic_user_id: str) -> dict:
    doc = await db.syndic_configs.find_one({"syndic_user_id": syndic_user_id})
    return doc or {}


def _has_effective_email_config(cfg: dict) -> bool:
    """Retourne True si le syndic a une config email complete (non-fallback)."""
    prov = cfg.get("email_provider", "none")
    if prov == "graph":
        return bool(cfg.get("graph_tenant_id") and cfg.get("graph_client_id") and cfg.get("graph_client_secret"))
    if prov == "smtp":
        return bool(cfg.get("smtp_host") and cfg.get("smtp_port") and cfg.get("smtp_username") and cfg.get("smtp_password"))
    return False


async def get_effective_email_config(db, syndic_user_id: str) -> dict:
    """Retourne la config email a utiliser pour ce syndic. Fallback env vars si
    aucune config perso. Les secrets sont dechiffres. Utilise par communication.py.
    """
    cfg = await _get_config_for(db, syndic_user_id)
    if _has_effective_email_config(cfg):
        return {
            "source": "syndic",
            "provider": cfg["email_provider"],
            "graph_tenant_id": cfg.get("graph_tenant_id", ""),
            "graph_client_id": cfg.get("graph_client_id", ""),
            "graph_client_secret": decrypt_secret(cfg.get("graph_client_secret", "")),
            "smtp_host": cfg.get("smtp_host", ""),
            "smtp_port": cfg.get("smtp_port", 0),
            "smtp_username": cfg.get("smtp_username", ""),
            "smtp_password": decrypt_secret(cfg.get("smtp_password", "")),
            "smtp_use_tls": cfg.get("smtp_use_tls", True),
        }
    # Fallback env vars (Graph global)
    return {
        "source": "env_fallback",
        "provider": "graph" if os.environ.get("AZURE_TENANT_ID") else "none",
        "graph_tenant_id": os.environ.get("AZURE_TENANT_ID", ""),
        "graph_client_id": os.environ.get("AZURE_CLIENT_ID", ""),
        "graph_client_secret": os.environ.get("AZURE_CLIENT_SECRET", ""),
        "smtp_host": "",
        "smtp_port": 0,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": True,
    }


async def _send_test_email_impl(effective_cfg: dict, from_mailbox: str, to: str) -> dict:
    """Envoie un email test avec la config effective, sans dry-run. Utilise pour
    valider la config avant activation."""
    import base64
    import httpx

    subject = "[Test] Configuration email NextGe Copro"
    body_html = (
        f"<p>Bonjour,</p>"
        f"<p>Cet email confirme que la configuration email de votre cabinet est operationnelle "
        f"(provider : <b>{effective_cfg.get('provider')}</b>, source : <b>{effective_cfg.get('source')}</b>).</p>"
        f"<p>Envoye a {datetime.now(timezone.utc).isoformat()} depuis <b>{from_mailbox}</b>.</p>"
        f"<p>-- NextGe Copro</p>"
    )

    prov = effective_cfg.get("provider")
    if prov == "graph":
        tid = effective_cfg["graph_tenant_id"]
        cid = effective_cfg["graph_client_id"]
        cs = effective_cfg["graph_client_secret"]
        if not (tid and cid and cs):
            raise HTTPException(400, "Config Graph incomplete (tenant/client/secret)")
        async with httpx.AsyncClient(timeout=30) as client:
            tok = await client.post(
                f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
                data={"client_id": cid, "client_secret": cs,
                      "scope": "https://graph.microsoft.com/.default",
                      "grant_type": "client_credentials"},
            )
            if tok.status_code >= 400:
                raise HTTPException(400, f"Auth Graph echouee : {tok.status_code} {tok.text[:200]}")
            token = tok.json()["access_token"]
            r = await client.post(
                f"https://graph.microsoft.com/v1.0/users/{from_mailbox}/sendMail",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "message": {
                        "subject": subject,
                        "body": {"contentType": "HTML", "content": body_html},
                        "toRecipients": [{"emailAddress": {"address": to}}],
                    },
                    "saveToSentItems": False,
                },
            )
            if r.status_code >= 400:
                raise HTTPException(400, f"Envoi Graph echoue : {r.status_code} {r.text[:200]}")
        return {"success": True, "provider": "graph"}

    if prov == "smtp":
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        host = effective_cfg["smtp_host"]
        port = effective_cfg["smtp_port"]
        user = effective_cfg["smtp_username"]
        pwd = effective_cfg["smtp_password"]
        use_tls = effective_cfg.get("smtp_use_tls", True)
        if not (host and port and user and pwd):
            raise HTTPException(400, "Config SMTP incomplete")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_mailbox
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html"))
        try:
            if int(port) == 465:
                with smtplib.SMTP_SSL(host, int(port), timeout=15) as s:
                    s.login(user, pwd)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, int(port), timeout=15) as s:
                    if use_tls:
                        s.starttls()
                    s.login(user, pwd)
                    s.send_message(msg)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Envoi SMTP echoue : {e}") from e
        return {"success": True, "provider": "smtp"}

    raise HTTPException(400, "Aucun provider email configure")


def create_syndic_config_router(db):
    router = APIRouter(prefix="/api")
    logos_storage = GridFSStorage(db, bucket_name="syndic_logos")

    async def _require_syndic(request: Request) -> dict:
        uid = request.state.user_id
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user:
            raise HTTPException(401, "Utilisateur inconnu")
        if user.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Reserve aux syndics et administrateurs")
        return user

    async def _require_superadmin(request: Request) -> dict:
        uid = request.state.user_id
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user or user.get("role") not in ("admin", "superadmin"):
            raise HTTPException(403, "Reserve au superadmin")
        return user

    # ============ Self-service (syndic) ============
    @router.get("/syndic-config/me")
    async def get_my_config(request: Request):
        """Retourne la config du cabinet du user courant (syndic ou herite via parent)."""
        syndic_uid = await _resolve_syndic_user_id(db, request)
        cfg = await _get_config_for(db, syndic_uid)
        cfg_safe = _redact_config(cfg)
        cfg_safe["syndic_user_id"] = syndic_uid
        cfg_safe["onboarding_completed"] = bool(cfg.get("onboarding_completed"))
        cfg_safe["has_logo"] = bool(cfg.get("logo_gridfs_id"))
        cfg_safe["email_configured"] = _has_effective_email_config(cfg)
        return cfg_safe

    @router.put("/syndic-config/me")
    async def update_my_config(payload: SyndicConfigUpdate, request: Request):
        await _require_syndic(request)
        syndic_uid = await _resolve_syndic_user_id(db, request)
        update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if not update:
            raise HTTPException(400, "Aucune donnee a mettre a jour")
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_uid},
            {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
            upsert=True,
        )
        cfg = await _get_config_for(db, syndic_uid)
        return _redact_config(cfg)

    @router.put("/syndic-config/me/email")
    async def update_email_config(payload: EmailConfigUpdate, request: Request):
        await _require_syndic(request)
        syndic_uid = await _resolve_syndic_user_id(db, request)
        data = payload.model_dump(exclude_unset=True)
        # Chiffre les secrets
        if "graph_client_secret" in data and data["graph_client_secret"]:
            data["graph_client_secret"] = encrypt_secret(data["graph_client_secret"])
        if "smtp_password" in data and data["smtp_password"]:
            data["smtp_password"] = encrypt_secret(data["smtp_password"])
        data["email_provider"] = data.pop("provider", "none")
        data["email_verified"] = False  # Reset verification apres modif
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_uid},
            {"$set": data, "$setOnInsert": {"created_at": data["updated_at"]}},
            upsert=True,
        )
        cfg = await _get_config_for(db, syndic_uid)
        return _redact_config(cfg)

    @router.post("/syndic-config/me/complete-onboarding")
    async def complete_onboarding(request: Request):
        await _require_syndic(request)
        syndic_uid = await _resolve_syndic_user_id(db, request)
        cfg = await _get_config_for(db, syndic_uid)
        # Verifier que le minimum est rempli
        missing = []
        for k, label in [("legal_name", "Nom legal"), ("address", "Adresse"), ("city", "Ville"), ("email", "Email")]:
            if not cfg.get(k):
                missing.append(label)
        if missing:
            raise HTTPException(400, f"Champs manquants pour cloturer l'onboarding : {', '.join(missing)}")
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_uid},
            {"$set": {"onboarding_completed": True,
                      "onboarding_completed_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"success": True}

    @router.post("/syndic-config/me/logo")
    async def upload_logo(request: Request, file: UploadFile = File(...)):
        await _require_syndic(request)
        syndic_uid = await _resolve_syndic_user_id(db, request)
        content = await file.read()
        if not content:
            raise HTTPException(400, "Fichier vide")
        if len(content) > 3 * 1024 * 1024:
            raise HTTPException(400, "Logo trop volumineux (max 3 Mo)")
        mime = (file.content_type or "").lower()
        if not any(mime.startswith(x) for x in ("image/png", "image/jpeg", "image/jpg", "image/svg")):
            raise HTTPException(400, f"Format non supporte : {mime}. Utilisez PNG/JPEG.")
        # Supprime l'ancien logo si present
        cfg = await _get_config_for(db, syndic_uid)
        old_id = cfg.get("logo_gridfs_id")
        if old_id:
            await logos_storage.delete(old_id)
        file_id = await logos_storage.upload(
            filename=file.filename or "logo.png",
            contents=content,
            metadata={"syndic_user_id": syndic_uid, "mime": mime},
        )
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_uid},
            {"$set": {"logo_gridfs_id": file_id, "logo_mime": mime,
                      "updated_at": datetime.now(timezone.utc).isoformat()},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return {"success": True, "logo_gridfs_id": file_id}

    @router.get("/syndic-config/{syndic_user_id}/logo")
    async def get_logo(syndic_user_id: str, request: Request):
        """Endpoint public au sein du system d'auth : n'importe quel user
        authentifie peut recuperer un logo (utilise dans les PDF des ACP gerees
        par ce syndic)."""
        cfg = await _get_config_for(db, syndic_user_id)
        file_id = cfg.get("logo_gridfs_id")
        if not file_id:
            raise HTTPException(404, "Aucun logo configure")
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            logos_storage.stream_download(file_id),
            media_type=cfg.get("logo_mime", "image/png"),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post("/syndic-config/me/test-email")
    async def test_email(payload: TestEmailPayload, request: Request):
        """Envoie un email test avec la config actuelle (bypasses MAIL_ENABLED).
        Permet de valider la config avant de l'activer."""
        await _require_syndic(request)
        syndic_uid = await _resolve_syndic_user_id(db, request)
        # Verifie que la boite d'envoi est autorisee
        user = await db.users.find_one({"_id": ObjectId(syndic_uid)})
        boxes = user.get("authorized_mailboxes") or []
        addr_low = payload.from_mailbox.strip().lower()
        allowed = {b.get("address", "").lower() for b in boxes if b.get("active", True)}
        if user.get("email", "").lower() != addr_low and addr_low not in allowed:
            raise HTTPException(403, f"Boite '{payload.from_mailbox}' non autorisee")
        effective = await get_effective_email_config(db, syndic_uid)
        try:
            result = await _send_test_email_impl(effective, payload.from_mailbox, payload.to)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Erreur test email : {e}") from e
        # Marque comme verifie
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_uid},
            {"$set": {"email_verified": True,
                      "email_verified_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"success": True, **result}

    # ============ Superadmin (edite n'importe quel syndic) ============
    @router.get("/admin/syndic-config/list")
    async def admin_list_configs(request: Request):
        await _require_superadmin(request)
        syndics = await db.users.find({"role": "syndic"}, {"_id": 1, "email": 1, "name": 1}).to_list(1000)
        result = []
        for s in syndics:
            uid = str(s["_id"])
            cfg = await _get_config_for(db, uid)
            result.append({
                "syndic_user_id": uid,
                "user_email": s.get("email", ""),
                "user_name": s.get("name", ""),
                "legal_name": cfg.get("legal_name", ""),
                "display_name": cfg.get("display_name", ""),
                "city": cfg.get("city", ""),
                "email_configured": _has_effective_email_config(cfg),
                "email_verified": bool(cfg.get("email_verified")),
                "email_provider": cfg.get("email_provider", "none"),
                "onboarding_completed": bool(cfg.get("onboarding_completed")),
                "has_logo": bool(cfg.get("logo_gridfs_id")),
                "mailboxes_count": len(s.get("authorized_mailboxes") or []),
            })
        return {"syndics": result}

    @router.get("/admin/syndic-config/{syndic_user_id}")
    async def admin_get_config(syndic_user_id: str, request: Request):
        await _require_superadmin(request)
        cfg = await _get_config_for(db, syndic_user_id)
        return _redact_config(cfg)

    @router.put("/admin/syndic-config/{syndic_user_id}")
    async def admin_update_config(syndic_user_id: str, payload: SyndicConfigUpdate, request: Request):
        await _require_superadmin(request)
        update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if not update:
            raise HTTPException(400, "Aucune donnee a mettre a jour")
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        update["updated_by_admin"] = True
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_user_id},
            {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
            upsert=True,
        )
        cfg = await _get_config_for(db, syndic_user_id)
        return _redact_config(cfg)

    @router.put("/admin/syndic-config/{syndic_user_id}/email")
    async def admin_update_email(syndic_user_id: str, payload: EmailConfigUpdate, request: Request):
        await _require_superadmin(request)
        data = payload.model_dump(exclude_unset=True)
        if data.get("graph_client_secret"):
            data["graph_client_secret"] = encrypt_secret(data["graph_client_secret"])
        if data.get("smtp_password"):
            data["smtp_password"] = encrypt_secret(data["smtp_password"])
        data["email_provider"] = data.pop("provider", "none")
        data["email_verified"] = False
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["updated_by_admin"] = True
        await db.syndic_configs.update_one(
            {"syndic_user_id": syndic_user_id},
            {"$set": data, "$setOnInsert": {"created_at": data["updated_at"]}},
            upsert=True,
        )
        cfg = await _get_config_for(db, syndic_user_id)
        return _redact_config(cfg)

    return router
