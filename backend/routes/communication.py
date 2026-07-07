"""Iter90au : Interface de communication avec les proprietaires.

Fonctionnalites :
1. Envoi de situations de compte (balance de tiers) par email + PDF.
2. Envoi de decomptes annuels par email + PDF.
3. Envoi de decomptes de mutation par email + PDF.
4. Communication libre (email texte + piece jointe PDF optionnelle).
5. Gestion des boites mail autorisees au niveau du cabinet syndic
   (chaque gestionnaire choisit sa boite d'envoi dans la liste du syndic).
6. Signature HTML par gestionnaire (enregistree sur le profil user).

Modele de donnees :
- `user.authorized_mailboxes` (uniquement sur les users role=syndic) :
  array de {address, display_name, active}. Le syndic gere sa liste ;
  les gestionnaires du meme cabinet (parent_syndic_id) heritent.
- `user.signature_html` (tous roles) : signature HTML libre du gestionnaire.

Endpoints :
- GET  /api/communication/mailboxes         : liste boites autorisees (heritage syndic)
- POST /api/communication/mailboxes         : ajouter (syndic uniquement)
- DELETE /api/communication/mailboxes/{addr}: retirer (syndic uniquement)
- GET  /api/communication/signature         : signature du user courant
- PUT  /api/communication/signature         : maj signature du user courant
- GET  /api/communication/owners-balances   : proprietaires + balance (pour UI selection)
- POST /api/communication/send/situation    : envoi situation de compte
- POST /api/communication/send/decompte     : envoi decompte annuel
- POST /api/communication/send/mutation     : envoi decompte mutation
- POST /api/communication/send/generic      : envoi libre + pj PDF
"""
from __future__ import annotations

import base64
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


class SendDecompteMutation(BaseModel):
    from_mailbox: str
    lot_id: str
    mutation_id: str
    to_emails: List[str]  # vendeur + acheteur (a saisir manuellement)
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
                         attachment_filename: str = "") -> dict:
        """Wrapper Graph : envoie a plusieurs destinataires. Si attachment_pdf est
        fourni, l'ajoute en PJ (base64 dans le message Graph)."""
        try:
            from graph_email import _MAIL_ENABLED, _TENANT_ID, _CLIENT_ID, _CLIENT_SECRET
            import httpx
        except Exception as e:
            raise HTTPException(500, f"Module email indisponible : {e}")

        # Dry-run mode : compte les envois mais n'appelle pas Graph
        if not _MAIL_ENABLED:
            logger.info("[DRY-RUN] Email suppressed. From=%s To=%s Subject=%s Attach=%s",
                        from_mailbox, to, subject, bool(attachment_pdf))
            return {"sent": len(to), "dry_run": True}
        if not (_TENANT_ID and _CLIENT_ID and _CLIENT_SECRET):
            raise HTTPException(500, "Microsoft Graph non configure (AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET manquants)")

        # Token OAuth2 client credentials
        async with httpx.AsyncClient(timeout=30) as client:
            tok = await client.post(
                f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/token",
                data={
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            tok.raise_for_status()
            token = tok.json()["access_token"]

            message = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html_body},
                    "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                },
                "saveToSentItems": True,
            }
            if attachment_pdf:
                message["message"]["attachments"] = [{
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": attachment_filename or "document.pdf",
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(attachment_pdf).decode("ascii"),
                }]

            r = await client.post(
                f"https://graph.microsoft.com/v1.0/users/{from_mailbox}/sendMail",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=message,
            )
            if r.status_code >= 400:
                logger.error("Graph sendMail failed : %s %s", r.status_code, r.text)
                raise HTTPException(502, f"Envoi email echoue : {r.status_code}")
        return {"sent": len(to), "dry_run": False}

    async def _ensure_mailbox_allowed(request: Request, mailbox: str):
        _, scope_id = await _resolve_syndic_scope(db, request)
        scope_user = await _get_scope_user(db, scope_id)
        boxes = scope_user.get("authorized_mailboxes") or []
        addr_low = (mailbox or "").strip().lower()
        # Fallback : email du syndic implicite
        if not boxes and scope_user.get("email", "").lower() == addr_low:
            return
        allowed = {b.get("address", "").lower() for b in boxes if b.get("active", True)}
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

    @router.post("/send/generic")
    async def send_generic(
        request: Request,
        from_mailbox: str = Form(...),
        to_json: str = Form(...),  # JSON list
        subject: str = Form(...),
        body_html: str = Form(...),
        include_signature: str = Form("true"),
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
        result = await _send_email(from_mailbox, to, subject, html, pdf_bytes, pdf_name)
        return {"success": True, **result}

    @router.post("/send/situation")
    async def send_situation(payload: SendSituation, request: Request):
        """Envoie la situation de compte (PDF genere) a chaque proprietaire selectionne."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        if not payload.owner_ids:
            raise HTTPException(400, "Aucun proprietaire selectionne")

        # Import lazy pour eviter la circularite de routes
        from routes.reports import _build_situation_compte_pdf, _compute_balance_tiers_for_ui
        from routes.email_templates import get_template_by_id, render_template, build_owner_email_context

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
                    ctx["balance"] = f"{balances_map.get(oid, 0.0):.2f}"
                    ctx["abs_balance"] = f"{abs(balances_map.get(oid, 0.0)):.2f}"
                    ctx["balance_status"] = "debiteur" if balances_map.get(oid, 0) > 0 else ("crediteur" if balances_map.get(oid, 0) < 0 else "solde")
                    subj = render_template(tpl.get("subject", "") or subject_default, ctx)
                    body_rendered = render_template(tpl.get("body_html", "") or body_default, ctx)
                else:
                    subj = payload.subject.strip() or subject_default
                    body_rendered = payload.body_html.strip() or body_default
                html = await _build_html_with_signature(request, body_rendered, payload.include_signature)
                pdf_bytes = await _build_situation_compte_pdf(
                    db, oid, payload.copropriete_id,
                    payload.start_date or None, payload.end_date or None,
                )
                filename = f"situation_compte_{owner['name'].replace(' ', '_')}.pdf"
                await _send_email(payload.from_mailbox, [owner["email"]], subj, html,
                                  attachment_pdf=pdf_bytes, attachment_filename=filename)
                sent += 1
            except Exception as e:
                logger.warning("Send situation failed for %s : %s", oid, e)
                failed.append({"owner_id": oid, "reason": str(e)[:100]})
        return {"success": True, "sent": sent, "failed": failed}

    @router.post("/send/decompte")
    async def send_decompte_annuel(payload: SendDecompteAnnuel, request: Request):
        """Envoie le decompte annuel (PDF genere) a chaque proprietaire selectionne."""
        await _ensure_mailbox_allowed(request, payload.from_mailbox)
        if not payload.owner_ids:
            raise HTTPException(400, "Aucun proprietaire selectionne")

        from routes.reports import _build_decompte_annuel_pdf

        sent = 0
        failed: List[dict] = []
        subj = payload.subject.strip() or "Decompte annuel de charges - Copropriete"
        body = payload.body_html.strip() or (
            "Bonjour,<br><br>Veuillez trouver en piece jointe votre decompte annuel de charges.<br><br>"
            "N'hesitez pas a nous contacter en cas de question.<br><br>Cordialement,"
        )
        html = await _build_html_with_signature(request, body, payload.include_signature)

        for oid in payload.owner_ids:
            owner = await db.owners.find_one({"id": oid}, {"_id": 0, "email": 1, "name": 1})
            if not owner or not owner.get("email"):
                failed.append({"owner_id": oid, "reason": "email manquant"})
                continue
            try:
                pdf_bytes = await _build_decompte_annuel_pdf(
                    db, oid, payload.copropriete_id, payload.fiscal_year_id,
                )
                filename = f"decompte_annuel_{owner['name'].replace(' ', '_')}.pdf"
                await _send_email(payload.from_mailbox, [owner["email"]], subj, html,
                                  attachment_pdf=pdf_bytes, attachment_filename=filename)
                sent += 1
            except Exception as e:
                logger.warning("Send decompte failed for %s : %s", oid, e)
                failed.append({"owner_id": oid, "reason": str(e)[:100]})
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
                                   attachment_pdf=pdf_bytes, attachment_filename=filename)
        return {"success": True, **result}

    return router
