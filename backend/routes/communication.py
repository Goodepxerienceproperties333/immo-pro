"""Iter90au : Interface de communication avec les proprietaires.

Fonctionnalites :
1. Envoi de situations de compte (balance de tiers) par email + PDF.
2. Envoi de decomptes annuels par email + PDF.
3. Envoi de decomptes de mutation par email + PDF.
4. Communication libre (email texte + piece jointe PDF optionnelle).
5. Gestion des boites mail autorisees au niveau du cabinet syndic
   (chaque gestionnaire choisit sa boite d'envoi dans la liste du syndic).
6. Signature HTML par gestionnaire (enregistree sur le profil user).

Iter90db : Historisation des envois dans `db.sent_communications` pour
exposition dans le portail proprietaire (tab Communications).

Modele de donnees :
- `user.authorized_mailboxes` (uniquement sur les users role=syndic) :
  array de {address, display_name, active}. Le syndic gere sa liste ;
  les gestionnaires du meme cabinet (parent_syndic_id) heritent.
- `user.signature_html` (tous roles) : signature HTML libre du gestionnaire.
- `db.sent_communications` (iter90db) :
  {id, from_mailbox, to[], cc[], subject, body_html, body_preview,
   has_attachment, attachment_filename, kind, copropriete_id, owner_ids[],
   sent_at, sent_by_user_id, dry_run, status}
"""
from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _extract_preview(html: str, max_len: int = 240) -> str:
    """Iter90db : extrait un apercu texte sans HTML pour la vue portail."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _err_reason(exc: Exception, max_len: int = 200) -> str:
    """Iter90dc : extrait un message d'erreur lisible pour l'utilisateur.

    - HTTPException : renvoie `detail` (message metier)
    - Autre : str(exc) tronque a max_len.
    """
    try:
        from fastapi import HTTPException as _HE
        if isinstance(exc, _HE):
            det = exc.detail
            if isinstance(det, (dict, list)):
                det = str(det)
            return (det or "").strip()[:max_len]
    except Exception:
        pass
    return str(exc)[:max_len]


# ---------- helpers scope cabinet ----------

async def _resolve_syndic_scope(db, request: Request) -> tuple[str, str]:
    """Retourne (user_id, syndic_scope_id) pour l'user courant.

    - Si role=syndic : scope = son propre id.
    - Si role=gestionnaire : scope = parent_syndic_id.
    - Si role=admin/superadmin : scope = son propre id (agence propre).
    - Sinon : 403.
    """
    from bson import ObjectId
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "Non authentifie")
    try:
        u = await db.users.find_one({"_id": ObjectId(uid)}, {"_id": 0, "role": 1, "parent_syndic_id": 1})
    except Exception:
        u = None
    if not u:
        raise HTTPException(401, "Utilisateur introuvable")
    role = u.get("role")
    if role in ("syndic", "admin", "superadmin"):
        return uid, uid
    if role == "gestionnaire":
        parent = u.get("parent_syndic_id")
        if not parent:
            raise HTTPException(403, "Compte gestionnaire orphelin (pas de syndic parent)")
        return uid, parent
    raise HTTPException(403, "Role non autorise pour la communication")


async def _get_scope_user(db, scope_id: str) -> dict:
    """Retourne le user document du scope (le syndic-cabinet)."""
    from bson import ObjectId
    try:
        u = await db.users.find_one({"_id": ObjectId(scope_id)}, {"_id": 0})
    except Exception:
        u = None
    if not u:
        raise HTTPException(404, "Scope utilisateur introuvable")
    return u


async def _get_current_user(db, request: Request) -> dict:
    from bson import ObjectId
    uid = request.state.user_id
    u = await db.users.find_one({"_id": ObjectId(uid)}, {"_id": 0})
    if not u:
        raise HTTPException(401, "Utilisateur introuvable")
    return u


# ---------- Models ----------

class MailboxAdd(BaseModel):
    address: str = Field(..., min_length=5)
    display_name: str = ""


class SignatureUpdate(BaseModel):
    signature_html: str = ""


class SendGeneric(BaseModel):
    from_mailbox: str  # une des authorized_mailboxes
    to: List[str]
    subject: str
    body_html: str
    include_signature: bool = True


class SendSituation(BaseModel):
    from_mailbox: str
    copropriete_id: str
    owner_ids: List[str]
    subject: str = ""
    body_html: str = ""
    include_signature: bool = True
    start_date: str = ""
    end_date: str = ""
    template_id: str = ""  # iter90aw : substitue subject/body_html si renseigne


class SendDecompteAnnuel(BaseModel):
    from_mailbox: str
    copropriete_id: str
    fiscal_year_id: str
    owner_ids: List[str]
    subject: str = ""
    body_html: str = ""
    include_signature: bool = True
    template_id: str = ""
    # iter90i8 : PJ additionnelles a joindre (au choix du syndic).
    # - include_expenses_list : joint la liste des depenses de l'exercice (PDF)
    # - extra_document_ids : ids de documents/documents a joindre
    #   (typiquement PJ relevés compteur de l'exercice)
    include_expenses_list: bool = False
    extra_document_ids: List[str] = []


class SendDecompteMutation(BaseModel):
    from_mailbox: str
    lot_id: str
    mutation_id: str
    to_emails: List[str]  # vendeur + acheteur (a saisir manuellement)
    subject: str = ""
    body_html: str = ""
    include_signature: bool = True
    template_id: str = ""


class PreviewSituation(BaseModel):
    from_mailbox: str
    copropriete_id: str
    owner_id: str  # un seul owner a la fois pour la previsualisation
    subject: str = ""
    body_html: str = ""
    include_signature: bool = True
    start_date: str = ""
    end_date: str = ""
    template_id: str = ""


class PreviewDecompte(BaseModel):
    from_mailbox: str
    copropriete_id: str
    fiscal_year_id: str
    owner_id: str
    subject: str = ""
    body_html: str = ""
    include_signature: bool = True
    template_id: str = ""


# ---------- Router ----------

def create_communication_router(db):
    router = APIRouter(prefix="/api/communication", tags=["communication"])

    # ============== Mailboxes ==============

    @router.get("/mailboxes")
    async def list_mailboxes(request: Request):
        """Liste les boites mail autorisees au niveau du cabinet (syndic parent).

        La boite marquee `default: True` est renvoyee en premier (utilisee comme
        expediteur par defaut dans l'UI). Si aucune boite n'est configuree, on
        propose l'email du syndic lui-meme en fallback.
        """
        _, scope_id = await _resolve_syndic_scope(db, request)
        scope_user = await _get_scope_user(db, scope_id)
        boxes = [b for b in (scope_user.get("authorized_mailboxes") or []) if b.get("active", True)]
        # Fallback : si aucune boite configuree, proposer l'email du syndic lui-meme
        if not boxes and scope_user.get("email"):
            boxes = [{
                "address": scope_user["email"],
                "display_name": scope_user.get("name", ""),
                "active": True,
                "default": True,
            }]
        # Sort : default en premier, puis alphabetique
        boxes.sort(key=lambda b: (not b.get("default", False), b.get("address", "").lower()))
        return {"mailboxes": boxes}

    @router.post("/mailboxes")
    async def add_mailbox(payload: MailboxAdd, request: Request):
        """Ajoute une boite mail autorisee (syndic/admin uniquement)."""
        from bson import ObjectId
        uid, scope_id = await _resolve_syndic_scope(db, request)
        cur = await _get_current_user(db, request)
        if cur.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Seul le syndic peut gerer les boites mail du cabinet")
        addr = payload.address.strip().lower()
        if "@" not in addr:
            raise HTTPException(400, "Adresse email invalide")
        scope_user = await _get_scope_user(db, scope_id)
        boxes = scope_user.get("authorized_mailboxes") or []
        # dedupe
        if any(b.get("address", "").lower() == addr for b in boxes):
            raise HTTPException(400, "Cette adresse est deja dans la liste")
        boxes.append({
            "address": addr,
            "display_name": payload.display_name.strip(),
            "active": True,
        })
        await db.users.update_one({"_id": ObjectId(scope_id)}, {"$set": {"authorized_mailboxes": boxes}})
        return {"success": True, "mailboxes": boxes}

    @router.delete("/mailboxes")
    async def remove_mailbox(address: str, request: Request):
        from bson import ObjectId
        _uid, scope_id = await _resolve_syndic_scope(db, request)
        cur = await _get_current_user(db, request)
        if cur.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Seul le syndic peut gerer les boites mail")
        scope_user = await _get_scope_user(db, scope_id)
        boxes = scope_user.get("authorized_mailboxes") or []
        addr_low = (address or "").strip().lower()
        new_boxes = [b for b in boxes if b.get("address", "").lower() != addr_low]
        if len(new_boxes) == len(boxes):
            raise HTTPException(404, "Adresse introuvable")
        await db.users.update_one({"_id": ObjectId(scope_id)}, {"$set": {"authorized_mailboxes": new_boxes}})
        return {"success": True, "mailboxes": new_boxes}

    # ============== Signature ==============

    @router.get("/signature")
    async def get_signature(request: Request):
        cur = await _get_current_user(db, request)
        return {
            "signature_html": cur.get("signature_html", ""),
            "name": cur.get("name", ""),
            "phone": cur.get("phone", ""),
            "email": cur.get("email", ""),
        }

    @router.put("/signature")
    async def update_signature(payload: SignatureUpdate, request: Request):
        from bson import ObjectId
        uid = request.state.user_id
        await db.users.update_one(
            {"_id": ObjectId(uid)},
            {"$set": {"signature_html": payload.signature_html}},
        )
        return {"success": True}

    # ============== Owners avec balance (pour UI selection) ==============

    @router.get("/owners-balances")
    async def owners_balances(copropriete_id: str, request: Request):
        """Retourne la balance des tiers formatee pour l'UI de selection.
        Reutilise la logique de /reports/balance-tiers/owners."""
        # Delegue le calcul au router reports pour ne pas dupliquer la logique
        from routes.reports import _compute_balance_tiers_for_ui
        data = await _compute_balance_tiers_for_ui(db, copropriete_id)
        return data

    # ============== Envoi generique ==============

    async def _send_email(from_mailbox: str, to: List[str], subject: str,
                         html_body: str, attachment_pdf: Optional[bytes] = None,
                         attachment_filename: str = "",
                         *,
                         kind: str = "generic",
                         copropriete_id: str = "",
                         owner_ids: Optional[List[str]] = None,
                         request: Optional[Request] = None,
                         use_bcc: bool = False,
                         extra_attachments: Optional[List[dict]] = None) -> dict:
        """Wrapper Graph : envoie a plusieurs destinataires. Si attachment_pdf est
        fourni, l'ajoute en PJ (base64 dans le message Graph).

        iter90i8 : extra_attachments = [{filename, bytes, mime_type}] pour
        joindre plusieurs PJ additionnelles (ex: liste depenses + PJ compteurs
        lors de l'envoi du decompte annuel).

        Iter90db : persiste chaque envoi dans `db.sent_communications` (dry_run inclus)
        pour affichage dans le portail proprietaire (tab Communications).
        iter90fx : `use_bcc=True` place les destinataires en CCI (bccRecipients)
        au lieu de TO, pour conformite GDPR sur les communications groupees.
        L'expediteur (`from_mailbox`) est alors mis en `toRecipient` unique -
        Microsoft Graph exige au moins un `toRecipient` non-vide.
        """
        dry_run = False
        status = "sent"
        try:
            from graph_email import _MAIL_ENABLED, _TENANT_ID, _CLIENT_ID, _CLIENT_SECRET
            import httpx
        except Exception as e:
            raise HTTPException(500, f"Module email indisponible : {e}")

        # iter90h7 : privilegier la config par-syndic (stockee en DB)
        # sur les env vars globales. Supporte Graph ET SMTP.
        graph_tid = _TENANT_ID
        graph_cid = _CLIENT_ID
        graph_cs = _CLIENT_SECRET
        graph_source = "env"
        use_per_syndic = False
        use_smtp = False
        smtp_cfg = {}
        if request is not None:
            try:
                from routes.syndic_config import get_effective_email_config, _resolve_syndic_user_id
                syndic_uid = await _resolve_syndic_user_id(db, request)
                effective = await get_effective_email_config(db, syndic_uid)
                if effective and effective.get("provider") == "smtp":
                    use_smtp = True
                    smtp_cfg = effective
                    use_per_syndic = True
                elif effective and effective.get("provider") == "graph":
                    eff_tid = effective.get("graph_tenant_id")
                    eff_cid = effective.get("graph_client_id")
                    eff_cs = effective.get("graph_client_secret")
                    if eff_tid and eff_cid and eff_cs:
                        graph_tid, graph_cid, graph_cs = eff_tid, eff_cid, eff_cs
                        graph_source = "db_per_syndic"
                        use_per_syndic = True
            except Exception as _e:
                # Pas grave - on retombe sur env vars
                logger.info("Config par-syndic indisponible, fallback env : %s", _e)

        # Dry-run mode : compte les envois mais n'appelle pas Graph/SMTP.
        # Si on a une config par-syndic valide, on N'APPLIQUE PAS le dry-run
        # global (l'utilisateur a explicitement configure son compte).
        if not _MAIL_ENABLED and not use_per_syndic:
            logger.info("[DRY-RUN] Email suppressed (no per-syndic + MAIL_ENABLED=false). From=%s To=%s Subject=%s",
                        from_mailbox, to, subject)
            dry_run = True
        elif use_smtp:
            # ---- Envoi SMTP (One2Net, etc.) ----
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.base import MIMEBase
            from email import encoders

            host = smtp_cfg.get("smtp_host", "")
            port = int(smtp_cfg.get("smtp_port", 587))
            user = smtp_cfg.get("smtp_username", "")
            pwd = smtp_cfg.get("smtp_password", "")
            use_tls = smtp_cfg.get("smtp_use_tls", True)

            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = from_mailbox
            if use_bcc:
                msg["To"] = from_mailbox
                msg["Bcc"] = ", ".join(to)
            else:
                msg["To"] = ", ".join(to)
            html_part = MIMEText(html_body, "html")
            msg.attach(html_part)
            if attachment_pdf:
                part = MIMEBase("application", "pdf")
                part.set_payload(attachment_pdf)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment",
                                filename=attachment_filename or "document.pdf")
                msg.attach(part)
            # iter90i8 : PJ additionnelles
            for extra in (extra_attachments or []):
                ex_bytes = extra.get("bytes")
                ex_name = extra.get("filename") or "extra.pdf"
                ex_mime = extra.get("mime_type") or "application/pdf"
                if not ex_bytes:
                    continue
                main_type, _, sub_type = ex_mime.partition("/")
                part = MIMEBase(main_type or "application", sub_type or "octet-stream")
                part.set_payload(ex_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=ex_name)
                msg.attach(part)
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                        s.login(user, pwd)
                        s.send_message(msg)
                else:
                    with smtplib.SMTP(host, port, timeout=15) as s:
                        if use_tls:
                            s.starttls()
                        s.login(user, pwd)
                        s.send_message(msg)
                logger.info("SMTP email sent from %s to %s (subject: %s)", from_mailbox, to, subject)
            except Exception as smtp_err:
                logger.error("SMTP send failed: %s", smtp_err)
                status = "failed"
                await _persist_sent_communication(
                    from_mailbox, to, subject, html_body, kind, copropriete_id,
                    owner_ids, request, "failed",
                )
                raise HTTPException(500, f"Envoi SMTP echoue : {smtp_err}") from smtp_err
        elif not (graph_tid and graph_cid and graph_cs):
            raise HTTPException(500,
                "Microsoft Graph non configure : ni credentials globaux "
                "(AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET) ni config par-syndic "
                "en DB. Configurez /admin/syndic-config ou /mon-bureau.")
        else:
            # Token OAuth2 client credentials
            async with httpx.AsyncClient(timeout=30) as client:
                tok = await client.post(
                    f"https://login.microsoftonline.com/{graph_tid}/oauth2/v2.0/token",
                    data={
                        "client_id": graph_cid,
                        "client_secret": graph_cs,
                        "scope": "https://graph.microsoft.com/.default",
                        "grant_type": "client_credentials",
                    },
                )
                if tok.status_code >= 400:
                    logger.error("Graph OAuth failed (source=%s) : %s %s", graph_source, tok.status_code, tok.text)
                    err_body = tok.text[:200] if tok.text else ""
                    raise HTTPException(500,
                        f"Auth Graph echouee (source={graph_source}) : HTTP {tok.status_code} - {err_body}")
                token = tok.json()["access_token"]

                message = {
                    "message": {
                        "subject": subject,
                        "body": {"contentType": "HTML", "content": html_body},
                    },
                    "saveToSentItems": True,
                }
                if use_bcc:
                    # iter90fx : GDPR - destinataires en CCI. Graph exige au
                    # moins un `toRecipients` -> on met l'expediteur lui-meme
                    # (il recoit une copie visible, utile pour tracabilite).
                    message["message"]["toRecipients"] = [{"emailAddress": {"address": from_mailbox}}]
                    message["message"]["bccRecipients"] = [{"emailAddress": {"address": a}} for a in to]
                else:
                    message["message"]["toRecipients"] = [{"emailAddress": {"address": a}} for a in to]
                if attachment_pdf:
                    message["message"]["attachments"] = [{
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": attachment_filename or "document.pdf",
                        "contentType": "application/pdf",
                        "contentBytes": base64.b64encode(attachment_pdf).decode("ascii"),
                    }]
                # iter90i8 : PJ additionnelles (mode Graph)
                for extra in (extra_attachments or []):
                    ex_bytes = extra.get("bytes")
                    ex_name = extra.get("filename") or "extra.pdf"
                    ex_mime = extra.get("mime_type") or "application/pdf"
                    if not ex_bytes:
                        continue
                    if "attachments" not in message["message"]:
                        message["message"]["attachments"] = []
                    message["message"]["attachments"].append({
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": ex_name,
                        "contentType": ex_mime,
                        "contentBytes": base64.b64encode(ex_bytes).decode("ascii"),
                    })

                r = await client.post(
                    f"https://graph.microsoft.com/v1.0/users/{from_mailbox}/sendMail",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=message,
                )
                if r.status_code >= 400:
                    logger.error("Graph sendMail failed : %s %s", r.status_code, r.text)
                    status = "failed"
                    # Extraire un message d'erreur Graph lisible (iter90dc)
                    err_detail = f"HTTP {r.status_code}"
                    try:
                        j = r.json()
                        gerr = (j or {}).get("error", {})
                        gcode = gerr.get("code", "")
                        gmsg = gerr.get("message", "")[:200]
                        # iter90h8 : messages plus explicites pour les erreurs
                        # Mail.Send frequentes qui bloquent les envois Communication
                        low = (gcode + " " + gmsg).lower()
                        if "accessdenied" in low or "authorization" in low:
                            err_detail = (
                                f"Permission Mail.Send manquante. "
                                f"Dans Azure Portal > App registrations > votre app > API permissions, "
                                f"ajoutez 'Mail.Send' (Application, pas Delegated) puis cliquez 'Grant admin consent'. "
                                f"(Detail Microsoft : {gcode} - {gmsg})"
                            )
                        elif "mailboxnotenabled" in low or "requestedmailboxnotfound" in low:
                            err_detail = (
                                f"La boite '{from_mailbox}' n'a pas de licence Exchange Online active. "
                                f"Verifiez dans Microsoft 365 Admin Center que la boite est activee. "
                                f"(Detail Microsoft : {gcode} - {gmsg})"
                            )
                        elif "throttled" in low or "toomanyrequests" in low:
                            err_detail = (
                                f"Limite d'envoi Microsoft depassee, reessayez plus tard. "
                                f"(Detail : {gcode} - {gmsg})"
                            )
                        elif gcode or gmsg:
                            err_detail = f"Graph {r.status_code} {gcode} : {gmsg}"
                    except Exception:
                        pass
                    # Log l'echec en DB puis lever l'exception
                    try:
                        await _persist_sent_communication(
                            from_mailbox=from_mailbox, to=to, subject=subject,
                            html_body=html_body, attachment_filename=attachment_filename,
                            has_attachment=bool(attachment_pdf), kind=kind,
                            copropriete_id=copropriete_id, owner_ids=owner_ids or [],
                            dry_run=False, status="failed",
                            error_msg=err_detail, request=request,
                            attachment_pdf=None,  # iter90hs : n'archive pas les PJ des envois failed
                        )
                    except Exception as pe:
                        logger.warning("Persist failed comm log failed : %s", pe)
                    raise HTTPException(502, f"Envoi email echoue : {err_detail}")

        # Iter90db : persiste succes / dry-run
        try:
            await _persist_sent_communication(
                from_mailbox=from_mailbox, to=to, subject=subject,
                html_body=html_body, attachment_filename=attachment_filename,
                has_attachment=bool(attachment_pdf), kind=kind,
                copropriete_id=copropriete_id, owner_ids=owner_ids or [],
                dry_run=dry_run, status=status,
                error_msg="", request=request,
                attachment_pdf=attachment_pdf,  # iter90hs : archive la PJ pour consultation proprio
            )
        except Exception as pe:
            logger.warning("Persist sent_communications failed : %s", pe)
        return {"sent": len(to), "dry_run": dry_run}

    async def _persist_sent_communication(
        *,
        from_mailbox: str, to: List[str], subject: str,
        html_body: str, attachment_filename: str, has_attachment: bool,
        kind: str, copropriete_id: str, owner_ids: List[str],
        dry_run: bool, status: str, error_msg: str,
        request: Optional[Request],
        attachment_pdf: Optional[bytes] = None,
    ) -> None:
        """Iter90db : insertion dans db.sent_communications (sync a chaque envoi
        via _send_email). Tolerance : ne remonte jamais d'erreur au caller.

        iter90hs : si `attachment_pdf` est fourni, stocke la PJ en GridFS
        (bucket `documents`) et cree une entree dans `db.documents` pour chaque
        proprietaire cible - permet au proprio de retrouver le document dans
        l'onglet Documents et de le consulter depuis l'onglet Communications.
        """
        sent_by = ""
        if request is not None:
            sent_by = getattr(request.state, "user_id", "") or ""
        comm_id = str(uuid.uuid4())

        # iter90hs : persist PJ en GridFS (une seule fois) + docs par proprio
        attachment_gridfs_id = ""
        attachment_size = 0
        if attachment_pdf and has_attachment and status != "failed":
            try:
                from gridfs_storage import get_documents_storage
                storage = get_documents_storage(db)
                attachment_gridfs_id = await storage.upload(
                    filename=attachment_filename or f"comm-{comm_id}.pdf",
                    contents=attachment_pdf,
                    metadata={
                        "communication_id": comm_id,
                        "copropriete_id": copropriete_id or "",
                        "kind": kind or "generic",
                        "mime_type": "application/pdf",
                        "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                attachment_size = len(attachment_pdf)
                # iter90hs / iter90i0 : categorie UNIFIEE "Communication".
                # (Auparavant : sous-categories par kind. Le user demande une
                # categorie unique pour toutes les communications syndic.)
                cat_name = "Communication"
                # Cherche la category existante par nom pour l'ACP, sinon la cree
                category_id = ""
                if copropriete_id:
                    existing_cat = await db.document_categories.find_one({
                        "copropriete_id": copropriete_id, "name": cat_name,
                    })
                    if existing_cat:
                        category_id = existing_cat["id"]
                    else:
                        category_id = str(uuid.uuid4())
                        await db.document_categories.insert_one({
                            "id": category_id,
                            "name": cat_name,
                            "description": f"Documents envoyes par le syndic ({cat_name})",
                            "copropriete_id": copropriete_id,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                # Cree UN document par proprio destinataire (owner_id_visible
                # sert au chinese wall dans /api/owner/documents).
                now_iso = datetime.now(timezone.utc).isoformat()
                docs_to_insert = []
                for oid in (owner_ids or []):
                    docs_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "title": subject or attachment_filename or "Communication",
                        "description": f"Communication du syndic - {subject or 'sans sujet'}",
                        "category_id": category_id,
                        "filename": attachment_filename or f"comm-{comm_id}.pdf",
                        "gridfs_id": attachment_gridfs_id,
                        "mime_type": "application/pdf",
                        "size_bytes": attachment_size,
                        "copropriete_id": copropriete_id or "",
                        "owner_id": oid,  # chinese wall proprio
                        "source": "communication",
                        "communication_id": comm_id,
                        "kind": kind or "generic",
                        "created_at": now_iso,
                    })
                if docs_to_insert:
                    await db.documents.insert_many(docs_to_insert)
            except Exception as e:
                logger.warning("iter90hs GridFS/documents persist failed : %s", e)
                attachment_gridfs_id = ""
                attachment_size = 0

        doc = {
            "id": comm_id,
            "from_mailbox": from_mailbox,
            "to": to,
            "subject": subject or "(sans sujet)",
            "body_html": (html_body or "")[:100000],  # cap 100k chars
            "body_preview": _extract_preview(html_body),
            "has_attachment": bool(has_attachment),
            "attachment_filename": attachment_filename or "",
            "attachment_gridfs_id": attachment_gridfs_id,
            "attachment_size": attachment_size,
            "kind": kind or "generic",
            "copropriete_id": copropriete_id or "",
            "owner_ids": owner_ids or [],
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "sent_by_user_id": sent_by,
            "dry_run": bool(dry_run),
            "status": status or "sent",
            "error_msg": error_msg or "",
        }
        await db.sent_communications.insert_one(doc)

    async def _ensure_mailbox_allowed(request: Request, mailbox: str):
        _, scope_id = await _resolve_syndic_scope(db, request)
        scope_user = await _get_scope_user(db, scope_id)
        boxes = scope_user.get("authorized_mailboxes") or []
        addr_low = (mailbox or "").strip().lower()
        # Fallback : email du syndic implicite
        if not boxes and scope_user.get("email", "").lower() == addr_low:
            return
        allowed = {b.get("address", "").lower() for b in boxes if b.get("active", True)}
        # SMTP : le smtp_username est l'identite authentifiee sur le serveur
        # -> toujours autorise comme expediteur
        try:
            from routes.syndic_config import get_effective_email_config
            eff = await get_effective_email_config(db, scope_id)
            smtp_user = (eff.get("smtp_username") or "").strip().lower()
            if smtp_user and eff.get("provider") == "smtp":
                allowed.add(smtp_user)
        except Exception:
            pass
        if addr_low not in allowed:
            raise HTTPException(403, f"Boite '{mailbox}' non autorisee pour ce cabinet")

    async def _build_html_with_signature(request: Request, body_html: str, include_signature: bool) -> str:
        if not include_signature:
            return body_html
        cur = await _get_current_user(db, request)
        sig = cur.get("signature_html", "").strip()
        if not sig:
            sig = (
                f"<br><br>--<br>"
                f"<b>{cur.get('name', '')}</b><br>"
                f"{cur.get('email', '')}"
                + (f"<br>Tel : {cur.get('phone', '')}" if cur.get("phone") else "")
            )
        return f"{body_html}<br><br>{sig}"

    # iter90h8 : endpoint pour visualiser l'historique des envois email
    # avec leur statut Microsoft. Utile pour diagnostiquer les envois
    # silencieusement rejetes (202 Accepted mais mail non delivre).
    @router.get("/sent-log")
    async def get_sent_log(
        request: Request,
        limit: int = 100,
        copropriete_id: Optional[str] = None,
        only_failed: bool = False,
    ):
        """Retourne l'historique des envois email (kind, from, to, status,
        error_msg, dry_run) pour le syndic connecte.

        iter90h8 : critique pour diagnostiquer les mails "acceptes par Graph
        mais jamais delivres" (permission Mail.Send manquante, boite sans
        licence Exchange, Application Access Policy restrictive).
        """
        _, scope_id = await _resolve_syndic_scope(db, request)
        query = {"syndic_user_id": scope_id}
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        if only_failed:
            query["status"] = "failed"
        rows = await db.sent_communications.find(query).sort("sent_at", -1).limit(min(limit, 500)).to_list(500)
        for r in rows:
            r["_id"] = str(r.get("_id", ""))
            # Retirer html_body volumineux du payload (envois de masse)
            r.pop("html_body", None)
        # Statistiques rapides
        total = len(rows)
        counts = {"sent": 0, "failed": 0, "dry_run": 0}
        for r in rows:
            if r.get("dry_run"):
                counts["dry_run"] += 1
            elif r.get("status") == "sent":
                counts["sent"] += 1
            elif r.get("status") == "failed":
                counts["failed"] += 1
        return {"rows": rows, "count": total, "counts": counts}

    @router.post("/send/generic")
    async def send_generic(
        request: Request,
        from_mailbox: str = Form(...),
        to_json: str = Form(...),  # JSON list
        subject: str = Form(...),
        body_html: str = Form(...),
        include_signature: str = Form("true"),
        use_bcc: str = Form("false"),  # iter90fx : envoi en CCI (GDPR)
        attachment: Optional[UploadFile] = File(None),
    ):
        import json
        to = json.loads(to_json)
        if not isinstance(to, list) or not to:
            raise HTTPException(400, "Liste destinataires vide")
        await _ensure_mailbox_allowed(request, from_mailbox)
        html = await _build_html_with_signature(request, body_html, include_signature.lower() == "true")
        pdf_bytes = None
        pdf_name = ""
        if attachment:
            fname = attachment.filename or ""
            if not fname.lower().endswith(".pdf"):
                raise HTTPException(400, "Piece jointe doit etre un PDF")
            pdf_bytes = await attachment.read()
            if len(pdf_bytes) > 15 * 1024 * 1024:
                raise HTTPException(413, "Piece jointe > 15 MB")
            pdf_name = fname
        bcc_flag = use_bcc.lower() == "true"
        result = await _send_email(from_mailbox, to, subject, html, pdf_bytes, pdf_name,
                                   kind="generic", request=request, use_bcc=bcc_flag)
        return {"success": True, **result}

    # ============== ADDRESS BOOK (iter90fx) ==============
    @router.get("/address-book")
    async def address_book(request: Request, copropriete_id: Optional[str] = None):
        """Retourne les listes proprietaires + locataires de l'ACP courante
        pour alimenter les pickers de l'email libre. Chinese wall STRICT :
        seuls les owners/tenants de l'ACP demandee sont retournes.

        iter90fx : indispensable pour permettre l'envoi groupe en CCI aux
        proprietaires ET locataires d'une meme ACP (GDPR : les adresses ne
        doivent PAS etre visibles entre destinataires).
        """
        from routes.reports import _require_copro
        cid = _require_copro(copropriete_id, request)
        # Owners : ceux qui ont un lot dans cette ACP OU un tier_account
        # configure (couvre aussi les anciens proprietaires ayant encore
        # un solde). L'utilisateur peut filtrer visuellement cote frontend.
        lots = await db.lots.find(
            {"copropriete_id": cid}, {"_id": 0, "owner_id": 1, "owner_ids": 1},
        ).to_list(2000)
        owner_ids = set()
        for lt in lots:
            if lt.get("owner_id"):
                owner_ids.add(lt["owner_id"])
            for oid in (lt.get("owner_ids") or []):
                if oid:
                    owner_ids.add(oid)
        owners = await db.owners.find(
            {"$or": [
                {"id": {"$in": list(owner_ids)}},
                {f"tier_accounts.{cid}": {"$exists": True}},
            ]},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "vcs_code": 1},
        ).sort("name", 1).to_list(2000) if (owner_ids or True) else []
        owners_out = [
            {
                "id": o["id"], "name": o.get("name", ""),
                "email": (o.get("email") or "").strip(),
                "vcs_code": o.get("vcs_code", ""),
                "kind": "owner",
            }
            for o in owners if (o.get("email") or "").strip()
        ]
        tenants = await db.tenants.find(
            {"copropriete_id": cid},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "lot_number": 1, "phone": 1},
        ).sort("name", 1).to_list(2000)
        tenants_out = [
            {
                "id": t["id"], "name": t.get("name", ""),
                "email": (t.get("email") or "").strip(),
                "lot_number": t.get("lot_number", ""),
                "kind": "tenant",
            }
            for t in tenants if (t.get("email") or "").strip()
        ]
        return {"owners": owners_out, "tenants": tenants_out}

    @router.post("/send/situation")
    async def send_situation(payload: SendSituation, request: Request):
        """Envoie la situation de compte (PDF genere) a chaque proprietaire selectionne."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        if not payload.owner_ids:
            raise HTTPException(400, "Aucun proprietaire selectionne")

        # Import lazy pour eviter la circularite de routes
        from routes.reports import _build_situation_compte_pdf, _compute_balance_tiers_for_ui
        from routes.email_templates import get_template_by_id, render_template, render_body_html, ensure_html_paragraphs, build_owner_email_context

        # iter90aw : charge le template si demande (une seule fois)
        tpl = None
        if payload.template_id:
            _, syndic_uid = await _resolve_syndic_scope(db, request)
            tpl = await get_template_by_id(db, syndic_uid, payload.template_id)
            if not tpl:
                raise HTTPException(404, f"Template '{payload.template_id}' non trouve")

        # Precharge les balances pour substituer {balance} dans les templates
        balances_map = {}
        if tpl:
            bal_data = await _compute_balance_tiers_for_ui(db, payload.copropriete_id)
            balances_map = {b["owner_id"]: b["balance"] for b in bal_data.get("owners", [])}

        from bson import ObjectId as _oid
        current_user = await db.users.find_one({"_id": _oid(request.state.user_id)})

        sent = 0
        failed: List[dict] = []
        subject_default = "Situation de votre compte - Copropriete"
        body_default = "Bonjour,<br><br>Veuillez trouver en piece jointe la situation actuelle de votre compte.<br><br>Cordialement,"

        for oid in payload.owner_ids:
            owner = await db.owners.find_one({"id": oid}, {"_id": 0, "email": 1, "name": 1})
            if not owner or not owner.get("email"):
                failed.append({"owner_id": oid, "reason": "email manquant"})
                continue
            try:
                # Compute subject + body per-owner (template rendering ou fallback)
                if tpl:
                    ctx = await build_owner_email_context(db, oid, payload.copropriete_id, current_user)
                    # iter93ac : format unifie plateforme pour les emails
                    from utils.format import fmt_eur as _fmt_eur
                    _b = balances_map.get(oid, 0.0)
                    ctx["balance"] = _fmt_eur(_b, with_suffix=False)
                    ctx["abs_balance"] = _fmt_eur(abs(_b), with_suffix=False)
                    ctx["balance_status"] = "debiteur" if _b > 0 else ("crediteur" if _b < 0 else "solde")
                    subj = render_template(tpl.get("subject", "") or subject_default, ctx)
                    body_rendered = render_body_html(tpl.get("body_html", "") or body_default, ctx)
                else:
                    subj = payload.subject.strip() or subject_default
                    body_rendered = ensure_html_paragraphs(payload.body_html.strip() or body_default)
                html = await _build_html_with_signature(request, body_rendered, payload.include_signature)
                # iter90fv fix : `_build_situation_compte_pdf` retourne un
                # tuple (pdf_bytes, filename). L'ancien code passait la tuple
                # directement a `_send_email(attachment_pdf=...)` -> echec
                # base64.b64encode masque par le try/except (les envois
                # remontaient "0 email envoye" cote UI, deroutant l'utilisateur).
                pdf_bytes, _ = await _build_situation_compte_pdf(
                    db, oid, payload.copropriete_id,
                    payload.start_date or None, payload.end_date or None,
                )
                filename = f"situation_compte_{owner['name'].replace(' ', '_')}.pdf"
                await _send_email(payload.from_mailbox, [owner["email"]], subj, html,
                                  attachment_pdf=pdf_bytes, attachment_filename=filename,
                                  kind="situation", copropriete_id=payload.copropriete_id,
                                  owner_ids=[oid], request=request)
                sent += 1
            except Exception as e:
                logger.warning("Send situation failed for %s : %s", oid, e)
                failed.append({"owner_id": oid, "reason": _err_reason(e)})
        return {"success": True, "sent": sent, "failed": failed}

    @router.post("/send/decompte")
    async def send_decompte_annuel(payload: SendDecompteAnnuel, request: Request):
        """Envoie le decompte annuel (PDF genere) a chaque proprietaire selectionne."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        if not payload.owner_ids:
            raise HTTPException(400, "Aucun proprietaire selectionne")

        from routes.reports import _build_decompte_annuel_pdf
        from routes.email_templates import get_template_by_id, render_template, render_body_html, ensure_html_paragraphs, build_owner_email_context
        from bson import ObjectId as _oid

        # iter90aw : template optionnel
        tpl = None
        if payload.template_id:
            _, syndic_uid = await _resolve_syndic_scope(db, request)
            tpl = await get_template_by_id(db, syndic_uid, payload.template_id)
            if not tpl:
                raise HTTPException(404, f"Template '{payload.template_id}' non trouve")
        current_user = await db.users.find_one({"_id": _oid(request.state.user_id)})

        # iter90i8 : precharge les PJ additionnelles selectionnees par le syndic
        # (partagees a tous les proprios cibles - meme PJ pour tous).
        common_extra_attachments: List[dict] = []
        if payload.extra_document_ids:
            from gridfs_storage import get_documents_storage
            storage = get_documents_storage(db)
            docs = await db.documents.find(
                {"id": {"$in": payload.extra_document_ids},
                 "copropriete_id": payload.copropriete_id,
                 "gridfs_id": {"$exists": True, "$ne": ""}},
                {"_id": 0, "id": 1, "filename": 1, "gridfs_id": 1, "mime_type": 1}
            ).to_list(len(payload.extra_document_ids))
            for d in docs:
                data = await storage.download(d["gridfs_id"])
                if not data:
                    continue
                common_extra_attachments.append({
                    "bytes": data,
                    "filename": d.get("filename") or "document.pdf",
                    "mime_type": d.get("mime_type") or "application/pdf",
                })
        # iter90i8 / iter91d : liste des depenses de l'exercice (PDF genere on-the-fly)
        # `_build_expenses_list_pdf` fusionne synthese portrait + detail paysage.
        expenses_pdf_bytes = None
        if payload.include_expenses_list:
            try:
                from routes.reports import _build_expenses_list_pdf
                expenses_pdf_bytes = await _build_expenses_list_pdf(
                    db, payload.copropriete_id, payload.fiscal_year_id,
                )
                if expenses_pdf_bytes:
                    common_extra_attachments.append({
                        "bytes": expenses_pdf_bytes,
                        "filename": f"liste_depenses_{payload.fiscal_year_id}.pdf",
                        "mime_type": "application/pdf",
                    })
            except Exception as e:
                logger.warning("iter91d : erreur generation liste depenses : %s", e)

        sent = 0
        failed: List[dict] = []
        subj_default = "Decompte annuel de charges - Copropriete"
        body_default = (
            "Bonjour,<br><br>Veuillez trouver en piece jointe votre decompte annuel de charges.<br><br>"
            "N'hesitez pas a nous contacter en cas de question.<br><br>Cordialement,"
        )

        for oid in payload.owner_ids:
            owner = await db.owners.find_one({"id": oid}, {"_id": 0, "email": 1, "name": 1})
            if not owner or not owner.get("email"):
                failed.append({"owner_id": oid, "reason": "email manquant"})
                continue
            try:
                if tpl:
                    ctx = await build_owner_email_context(db, oid, payload.copropriete_id, current_user)
                    subj = render_template(tpl.get("subject", "") or subj_default, ctx)
                    body_rendered = render_body_html(tpl.get("body_html", "") or body_default, ctx)
                else:
                    subj = payload.subject.strip() or subj_default
                    body_rendered = ensure_html_paragraphs(payload.body_html.strip() or body_default)
                html = await _build_html_with_signature(request, body_rendered, payload.include_signature)
                pdf_bytes, _ = await _build_decompte_annuel_pdf(
                    db, oid, payload.copropriete_id, payload.fiscal_year_id,
                )
                filename = f"decompte_annuel_{owner['name'].replace(' ', '_')}.pdf"
                await _send_email(payload.from_mailbox, [owner["email"]], subj, html,
                                  attachment_pdf=pdf_bytes, attachment_filename=filename,
                                  kind="decompte", copropriete_id=payload.copropriete_id,
                                  owner_ids=[oid], request=request,
                                  extra_attachments=common_extra_attachments)
                sent += 1
            except Exception as e:
                logger.warning("Send decompte failed for %s : %s", oid, e)
                failed.append({"owner_id": oid, "reason": _err_reason(e)})
        return {"success": True, "sent": sent, "failed": failed}

    @router.post("/send/mutation")
    async def send_decompte_mutation(payload: SendDecompteMutation, request: Request):
        """Envoie le decompte de mutation (PDF genere via properties router) aux
        emails saisis manuellement (vendeur + acheteur)."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        if not payload.to_emails:
            raise HTTPException(400, "Aucun destinataire fourni")

        from pdf_mutation_decompte import build_mutation_decompte_pdf

        # Fetch mutation + lot pour construire le PDF
        lot = await db.lots.find_one({"id": payload.lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot introuvable")
        mutation = await db.mutations.find_one(
            {"id": payload.mutation_id, "lot_id": payload.lot_id}, {"_id": 0}
        )
        if not mutation:
            raise HTTPException(404, "Mutation introuvable pour ce lot")

        # Reutilise la logique du download endpoint (properties.py L1514+)
        # On importe la fonction wrapper qui builds le PDF - on la reproduit inline ici
        # pour eviter de dupliquer la logique de download.
        from routes.properties import _build_mutation_decompte_context
        try:
            context = await _build_mutation_decompte_context(db, payload.lot_id, payload.mutation_id)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Impossible de generer le PDF mutation : {e}")

        pdf_bytes = build_mutation_decompte_pdf(**context)
        sale_date = (mutation.get("sale_date") or "").replace("-", "")
        lot_num = lot.get("number", "")
        filename = f"decompte_mutation_lot_{lot_num}_{sale_date}.pdf"

        subj = payload.subject.strip() or f"Decompte de mutation - Lot {lot_num}"
        body = payload.body_html.strip() or (
            "Bonjour,<br><br>Veuillez trouver en piece jointe le decompte de mutation.<br><br>"
            "Cordialement,"
        )
        html = await _build_html_with_signature(request, body, payload.include_signature)

        result = await _send_email(payload.from_mailbox, payload.to_emails, subj, html,
                                   attachment_pdf=pdf_bytes, attachment_filename=filename,
                                   kind="mutation",
                                   copropriete_id=lot.get("copropriete_id", ""),
                                   owner_ids=[mutation.get("from_owner_id"), mutation.get("to_owner_id")],
                                   request=request)
        return {"success": True, **result}

    # ============== PREVIEW (iter90fv) ==============
    # Rendu du mail (subject + body HTML + signature + PDF PJ en base64)
    # SANS envoi effectif. Utilise par le dialog "Aperçu avant envoi" cote
    # frontend pour permettre au syndic de valider chaque destinataire avant
    # de cliquer sur "Envoyer" pour de bon. Retourne un JSON leger que le
    # frontend peut afficher dans un iframe (data:URL pour le PDF, HTML
    # inline pour le mail).

    async def _preview_common(request, from_mailbox, template_id, subject_in,
                              body_in, include_signature, subject_default,
                              body_default, owner_id, copropriete_id,
                              balances_map=None):
        """Rend le subject + body HTML final (avec substitution template +
        signature) pour UN proprietaire donne. Reutilise `render_template`
        exactement comme les endpoints send/situation et send/decompte, pour
        garantir que l'apercu est FIDELE a ce qui sera envoye."""
        from routes.email_templates import (
            get_template_by_id, render_template, render_body_html,
            ensure_html_paragraphs, build_owner_email_context,
        )
        from bson import ObjectId as _oid

        tpl = None
        if template_id:
            _, syndic_uid = await _resolve_syndic_scope(db, request)
            tpl = await get_template_by_id(db, syndic_uid, template_id)
            if not tpl:
                raise HTTPException(404, f"Template '{template_id}' non trouve")
        current_user = await db.users.find_one({"_id": _oid(request.state.user_id)})
        if tpl:
            ctx = await build_owner_email_context(db, owner_id, copropriete_id, current_user)
            if balances_map is not None:
                bal = balances_map.get(owner_id, 0.0)
                # iter93ac : format unifie plateforme pour les emails
                from utils.format import fmt_eur as _fmt_eur
                ctx["balance"] = _fmt_eur(bal, with_suffix=False)
                ctx["abs_balance"] = _fmt_eur(abs(bal), with_suffix=False)
                ctx["balance_status"] = (
                    "debiteur" if bal > 0 else ("crediteur" if bal < 0 else "solde")
                )
            subj = render_template(tpl.get("subject", "") or subject_default, ctx)
            body_rendered = render_body_html(tpl.get("body_html", "") or body_default, ctx)
        else:
            subj = (subject_in or "").strip() or subject_default
            body_rendered = ensure_html_paragraphs((body_in or "").strip() or body_default)
        html = await _build_html_with_signature(request, body_rendered, include_signature)
        return subj, html

    @router.post("/preview/situation")
    async def preview_situation(payload: PreviewSituation, request: Request):
        """Rend l'apercu du mail + PDF Situation de compte pour UN
        proprietaire, sans envoi. iter90fv."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        from routes.reports import _build_situation_compte_pdf, _compute_balance_tiers_for_ui

        owner = await db.owners.find_one(
            {"id": payload.owner_id},
            {"_id": 0, "id": 1, "email": 1, "name": 1},
        )
        if not owner:
            raise HTTPException(404, "Proprietaire introuvable")

        balances_map = {}
        if payload.template_id:
            bal_data = await _compute_balance_tiers_for_ui(db, payload.copropriete_id)
            balances_map = {b["owner_id"]: b["balance"] for b in bal_data.get("owners", [])}

        subj, html = await _preview_common(
            request, payload.from_mailbox, payload.template_id,
            payload.subject, payload.body_html, payload.include_signature,
            "Situation de votre compte - Copropriete",
            "Bonjour,<br><br>Veuillez trouver en piece jointe la situation actuelle de votre compte.<br><br>Cordialement,",
            payload.owner_id, payload.copropriete_id, balances_map,
        )
        # iter90fv fix : `_build_situation_compte_pdf` retourne un TUPLE
        # (pdf_bytes, filename). Le code initial de preview essayait de
        # base64-encoder le tuple directement -> 500 "expected bytes-like".
        pdf_bytes, filename = await _build_situation_compte_pdf(
            db, payload.owner_id, payload.copropriete_id,
            payload.start_date or None, payload.end_date or None,
        )
        return {
            "owner_id": payload.owner_id,
            "owner_name": owner.get("name", ""),
            "owner_email": owner.get("email", ""),
            "from_mailbox": payload.from_mailbox,
            "subject": subj,
            "body_html": html,
            "attachment_filename": filename,
            "attachment_pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        }

    @router.post("/preview/decompte")
    async def preview_decompte(payload: PreviewDecompte, request: Request):
        """Rend l'apercu du mail + PDF Decompte annuel pour UN proprietaire,
        sans envoi. iter90fv."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        from routes.reports import _build_decompte_annuel_pdf

        owner = await db.owners.find_one(
            {"id": payload.owner_id},
            {"_id": 0, "id": 1, "email": 1, "name": 1},
        )
        if not owner:
            raise HTTPException(404, "Proprietaire introuvable")

        subj, html = await _preview_common(
            request, payload.from_mailbox, payload.template_id,
            payload.subject, payload.body_html, payload.include_signature,
            "Decompte annuel de charges - Copropriete",
            "Bonjour,<br><br>Veuillez trouver en piece jointe votre decompte annuel de charges.<br><br>"
            "N'hesitez pas a nous contacter en cas de question.<br><br>Cordialement,",
            payload.owner_id, payload.copropriete_id,
        )
        # iter90fv fix : `_build_decompte_annuel_pdf` retourne aussi un tuple
        # (pdf_bytes, filename). Cf note dans preview_situation.
        pdf_bytes, filename = await _build_decompte_annuel_pdf(
            db, payload.owner_id, payload.copropriete_id, payload.fiscal_year_id,
        )
        return {
            "owner_id": payload.owner_id,
            "owner_name": owner.get("name", ""),
            "owner_email": owner.get("email", ""),
            "from_mailbox": payload.from_mailbox,
            "subject": subj,
            "body_html": html,
            "attachment_filename": filename,
            "attachment_pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        }

    # iter90i8 : Liste des documents joignables au decompte annuel
    # (PJ de releves compteur de l'exercice + docs manuels partages).
    @router.get("/attachable-documents")
    async def list_attachable_documents(
        request: Request,
        copropriete_id: str,
        fiscal_year_id: str,
    ):
        fy = await db.fiscal_years.find_one(
            {"id": fiscal_year_id, "copropriete_id": copropriete_id},
            {"_id": 0, "start_date": 1, "end_date": 1, "name": 1},
        )
        if not fy:
            raise HTTPException(404, "Exercice introuvable")
        # PJ compteurs de l'exercice (deduplique par batch)
        readings = await db.meter_readings.find({
            "copropriete_id": copropriete_id,
            "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]},
            "attachment_gridfs_id": {"$exists": True, "$ne": ""},
        }, {"_id": 0, "id": 1, "meter_type": 1, "date": 1, "batch_id": 1,
            "attachment_gridfs_id": 1, "attachment_filename": 1,
            "attachment_size": 1}).to_list(1000)
        meter_attachments = []
        seen_batches = set()
        # Retrouve un document.id pour chaque batch via matching sur gridfs_id
        gridfs_ids = [r["attachment_gridfs_id"] for r in readings]
        docs = await db.documents.find(
            {"copropriete_id": copropriete_id, "source": "meter_reading",
             "gridfs_id": {"$in": gridfs_ids}},
            {"_id": 0, "id": 1, "gridfs_id": 1, "batch_id": 1, "title": 1}
        ).to_list(2000)
        # Un doc.id representatif par batch (le premier trouve)
        doc_by_gridfs = {}
        for d in docs:
            if d["gridfs_id"] not in doc_by_gridfs:
                doc_by_gridfs[d["gridfs_id"]] = d
        for r in readings:
            key = r.get("batch_id") or r["id"]
            if key in seen_batches:
                continue
            seen_batches.add(key)
            d = doc_by_gridfs.get(r["attachment_gridfs_id"])
            if not d:
                continue
            meter_attachments.append({
                "id": d["id"],
                "kind": "meter_reading",
                "meter_type": r.get("meter_type", ""),
                "date": r.get("date", ""),
                "filename": r.get("attachment_filename", ""),
                "size": r.get("attachment_size", 0),
                "title": d.get("title") or r.get("attachment_filename", ""),
            })
        return {
            "fiscal_year": fy,
            "meter_attachments": meter_attachments,
        }

    return router
