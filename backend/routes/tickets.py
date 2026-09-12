"""Iter90fs - Systeme de tickets support / remontee de bug.

Workflow :
1. Syndic ouvre le chatbot -> choisit "Remontee de bug"
2. Remplit un formulaire (titre, description, etapes, comportement attendu/observe,
   pieces jointes, email demandeur pre-rempli).
3. Le systeme cree un ticket TICK-YYYY-XXXX, stocke les pieces jointes en GridFS
   (bucket `ticket_attachments`) et envoie un email au support.
4. Le syndic peut suivre son ticket (statut + commentaires).
5. Le superadmin peut :
   - Voir tous les tickets
   - Assigner un ticket a lui-meme (ou un autre membre)
   - Changer le statut (Ouvert -> Affecte -> En cours -> Testing -> Deploiement -> Cloture)
   - Ajouter des commentaires
   - A chaque changement de statut, un email est envoye au demandeur.

Isolation :
- Syndic voit UNIQUEMENT ses propres tickets (via `syndic_id`).
- Superadmin voit tout.
- Un ticket porte le `syndic_id` du demandeur pour permettre le filtre chinese-wall.
"""
import os
import io
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import (
    APIRouter, HTTPException, Request, BackgroundTasks,
    UploadFile, File, Form,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gridfs_storage import GridFSStorage

logger = logging.getLogger(__name__)

STATUSES = ["open", "assigned", "in_progress", "testing", "deployment", "closed", "rejected"]

STATUS_LABELS = {
    "open": "Ouvert",
    "assigned": "Affecte",
    "in_progress": "En cours de developpement",
    "testing": "Testing",
    "deployment": "Deploiement",
    "closed": "Cloture",
    "rejected": "Refuse / Duplique",
}

MAX_FILES = 5
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIMES_PREFIX = None  # aucun filtre : "tout type" par choix utilisateur


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ticket_storage(db) -> GridFSStorage:
    return GridFSStorage(db, bucket_name="ticket_attachments")


async def _next_ticket_number(db) -> str:
    year = datetime.now(timezone.utc).year
    key = f"tickets-{year}"
    r = await db.counters.find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = r["seq"] if r else 1
    return f"TICK-{year}-{seq:04d}"


async def _load_user_context(db, user_id: str) -> dict:
    """Retourne {email, name, role} pour l'user_id donne, ou defauts vides."""
    from bson import ObjectId
    try:
        doc = await db.users.find_one(
            {"_id": ObjectId(user_id)},
            {"_id": 0, "email": 1, "name": 1, "role": 1, "parent_syndic_id": 1},
        )
    except Exception:
        doc = None
    if not doc:
        return {"email": "", "name": "Utilisateur", "role": "syndic", "parent_syndic_id": None}
    return {
        "email": doc.get("email", "") or "",
        "name": doc.get("name", "") or doc.get("email", "") or "Utilisateur",
        "role": doc.get("role", "syndic") or "syndic",
        "parent_syndic_id": doc.get("parent_syndic_id"),
    }


def _mask_ticket(t: dict) -> dict:
    """Nettoie un doc ticket pour la reponse API (pas de _id)."""
    t = dict(t)
    t.pop("_id", None)
    t["status_label"] = STATUS_LABELS.get(t.get("status", ""), t.get("status", ""))
    return t


async def _send_ticket_created_email(*, support_email: str, ticket: dict) -> None:
    from graph_email import send_html_email
    number = ticket["number"]
    subject = f"[NextGe Copro Support] Nouveau ticket {number} — {ticket['title']}"
    atts = ticket.get("attachments") or []
    att_lines = "".join(
        f"<li>{a.get('filename','?')} ({a.get('size',0)} octets)</li>" for a in atts
    ) or "<li><i>Aucune piece jointe</i></li>"
    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <h2 style="color:#022D52;margin-bottom:4px;">Nouveau ticket support</h2>
  <p style="color:#555;font-size:13px;">Un syndic vient de remonter un bug via le chatbot NextGe Copro.</p>
  <div style="background:#FAFAFA;border:1px solid #EEE;border-radius:8px;padding:12px;margin:12px 0;">
    <b>Ticket :</b> {number}<br/>
    <b>Titre :</b> {ticket.get('title','')}<br/>
    <b>Demandeur :</b> {ticket.get('requester_name','')} &lt;{ticket.get('requester_email','')}&gt;<br/>
    <b>Date :</b> {ticket.get('created_at','')}<br/>
  </div>
  <h3 style="color:#333;">Description</h3>
  <div style="white-space:pre-wrap;font-size:13px;">{ticket.get('description','')}</div>
  <h3 style="color:#333;margin-top:16px;">Etapes pour reproduire</h3>
  <div style="white-space:pre-wrap;font-size:13px;">{ticket.get('steps_to_reproduce','') or '<i>Non fourni</i>'}</div>
  <h3 style="color:#333;margin-top:16px;">Comportement attendu</h3>
  <div style="white-space:pre-wrap;font-size:13px;">{ticket.get('expected_behavior','') or '<i>Non fourni</i>'}</div>
  <h3 style="color:#333;margin-top:16px;">Comportement observe</h3>
  <div style="white-space:pre-wrap;font-size:13px;">{ticket.get('observed_behavior','') or '<i>Non fourni</i>'}</div>
  <h3 style="color:#333;margin-top:16px;">Pieces jointes</h3>
  <ul style="font-size:13px;">{att_lines}</ul>
  <hr style="margin:24px 0;border:none;border-top:1px solid #EEE;"/>
  <p style="color:#888;font-size:11px;">
    Repondez directement a cet email : votre reponse partira automatiquement au demandeur.
  </p>
</div>
""".strip()
    await send_html_email(
        recipients=[support_email],
        subject=subject,
        html_body=body,
        reply_to=ticket.get("requester_email") or None,
    )


async def _send_admin_announcement_email(*, recipients: list, ticket: dict,
                                          admin_name: str, target_scope: str,
                                          target_count: int) -> None:
    """iter95s : email d'annonce du superadmin aux syndics."""
    if not recipients:
        return
    from graph_email import send_html_email
    number = ticket["number"]
    scope_label = "tous les syndics" if target_scope == "all" else f"{target_count} syndic(s) cible(s)"
    subject = f"[NextGe Copro] Annonce support {number} — {ticket['title']}"
    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <div style="background:#022D52;color:white;padding:12px 16px;border-radius:8px 8px 0 0;">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:0.85;">Communication support NextGe Copro</div>
    <h2 style="margin:4px 0 0 0;">Annonce support</h2>
  </div>
  <div style="border:1px solid #E5E7EB;border-top:none;padding:16px;border-radius:0 0 8px 8px;">
    <p style="color:#374151;font-size:13px;line-height:1.5;">
      Bonjour,<br/>
      <b>{admin_name}</b> a ouvert un ticket de suivi qui vous concerne
      (destinataire : <i>{scope_label}</i>).
    </p>
    <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:8px;padding:12px;margin:12px 0;">
      <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;">Ticket #{number}</div>
      <h3 style="margin:4px 0 8px 0;color:#111827;">{ticket.get('title','')}</h3>
      <div style="white-space:pre-wrap;font-size:13px;color:#374151;">{ticket.get('description','')}</div>
    </div>
    <p style="color:#374151;font-size:13px;">
      Vous pouvez suivre l'avancement (statut, commentaires) directement dans votre
      espace <b>Support</b> de l'application. Toute mise a jour vous sera notifiee
      automatiquement par email.
    </p>
    <hr style="margin:20px 0;border:none;border-top:1px solid #E5E7EB;"/>
    <p style="color:#9CA3AF;font-size:11px;">
      Vous recevez cet email car un ticket support ouvert par l'equipe NextGe Copro
      vous concerne. Pour toute question, repondez a ce mail.
    </p>
  </div>
</div>
""".strip()
    await send_html_email(
        recipients=recipients,
        subject=subject,
        html_body=body,
    )


async def _send_status_changed_email(
    *, requester_email: str, ticket: dict, old_status: str, new_status: str,
    comment: str, actor_name: str,
) -> None:
    from graph_email import send_html_email
    if not requester_email:
        return
    number = ticket["number"]
    subject = f"[NextGe Copro Support] Ticket {number} — {STATUS_LABELS.get(new_status, new_status)}"
    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <h2 style="color:#022D52;margin-bottom:4px;">Mise a jour de votre ticket</h2>
  <div style="background:#FAFAFA;border:1px solid #EEE;border-radius:8px;padding:12px;margin:12px 0;">
    <b>Ticket :</b> {number}<br/>
    <b>Titre :</b> {ticket.get('title','')}<br/>
    <b>Ancien statut :</b> {STATUS_LABELS.get(old_status, old_status)}<br/>
    <b>Nouveau statut :</b> <span style="color:#022D52;font-weight:bold;">{STATUS_LABELS.get(new_status, new_status)}</span><br/>
    <b>Mise a jour par :</b> {actor_name}<br/>
  </div>
  {f'<h3 style="color:#333;">Commentaire</h3><div style="white-space:pre-wrap;font-size:13px;background:#F1F5F9;padding:10px;border-radius:6px;">{comment}</div>' if comment else ''}
  <p style="color:#555;font-size:12px;margin-top:16px;">
    Vous pouvez suivre l'evolution de votre ticket dans NextGe Copro (menu Support).
  </p>
</div>
""".strip()
    await send_html_email(
        recipients=[requester_email],
        subject=subject,
        html_body=body,
    )


async def _send_comment_notification_email(
    *, recipient_email: str, ticket: dict, comment: str, actor_name: str,
    actor_role: str, reply_to: Optional[str] = None,
) -> None:
    """iter90h1 : notification email quand un commentaire est ajoute a un ticket.
    - Si commentaire du syndic -> notifie support@ (avec reply-to = demandeur)
    - Si commentaire du support -> notifie le demandeur
    """
    from graph_email import send_html_email
    if not recipient_email:
        return
    number = ticket["number"]
    subject = f"[NextGe Copro Support] Nouveau commentaire ticket {number}"
    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <h2 style="color:#022D52;margin-bottom:4px;">Nouveau commentaire</h2>
  <div style="background:#FAFAFA;border:1px solid #EEE;border-radius:8px;padding:12px;margin:12px 0;">
    <b>Ticket :</b> {number}<br/>
    <b>Titre :</b> {ticket.get('title','')}<br/>
    <b>Statut :</b> {STATUS_LABELS.get(ticket.get('status',''), ticket.get('status',''))}<br/>
    <b>De :</b> {actor_name} ({actor_role})<br/>
  </div>
  <h3 style="color:#333;">Commentaire</h3>
  <div style="white-space:pre-wrap;font-size:13px;background:#F1F5F9;padding:10px;border-radius:6px;">{comment}</div>
  <p style="color:#555;font-size:12px;margin-top:16px;">
    Repondez a ce ticket depuis NextGe Copro (menu Support).
  </p>
</div>
""".strip()
    await send_html_email(
        recipients=[recipient_email],
        subject=subject,
        html_body=body,
        reply_to=reply_to,
    )


def _is_superadmin(role: str) -> bool:
    return role in ("superadmin", "admin")


async def _load_ticket_scoped(db, ticket_id: str, request: Request) -> dict:
    """Charge un ticket et applique l'isolation (syndic = ses propres,
    superadmin = tous)."""
    t = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Ticket introuvable")
    role = getattr(request.state, "user_role", "") or ""
    if _is_superadmin(role):
        return t
    sid = getattr(request.state, "syndic_id", None)
    user_id = getattr(request.state, "user_id", "")
    # Le demandeur peut toujours voir son ticket meme si le syndic_id a change
    if t.get("requester_user_id") == user_id:
        return t
    if sid and t.get("syndic_id") == sid:
        return t
    # iter95s : annonces superadmin visibles par les syndics cibles ou tous
    targets = t.get("target_syndic_ids") or []
    if t.get("is_admin_announcement") and (
        "*" in targets or (sid and sid in targets)
    ):
        return t
    raise HTTPException(403, "Acces refuse a ce ticket (chinese wall)")


class StatusChangeInput(BaseModel):
    new_status: str
    comment: Optional[str] = ""


class CommentInput(BaseModel):
    comment: str


class AssignInput(BaseModel):
    assigned_to_user_id: Optional[str] = None  # None -> unassign


def create_tickets_router(db):
    router = APIRouter(prefix="/api/tickets", tags=["support-tickets"])

    @router.get("/statuses")
    async def list_statuses(request: Request):
        """Liste des statuts disponibles + labels FR."""
        return [{"key": k, "label": STATUS_LABELS[k]} for k in STATUSES]

    # iter95s : annonces superadmin -> syndics
    class _AdminAnnounceInput(BaseModel):
        title: str
        description: str
        target: str = "all"  # "all" ou "specific"
        target_syndic_ids: List[str] = []  # utilise si target=="specific"
        notify_email: bool = True

    @router.post("/admin/announce")
    async def admin_announce(request: Request, data: _AdminAnnounceInput, background: BackgroundTasks):
        """iter95s : le superadmin cree un ticket-annonce visible par un ou
        plusieurs syndics (`target="all"` OU `target="specific"` +
        `target_syndic_ids`). Un email est envoye a chaque syndic cible.
        """
        role = getattr(request.state, "user_role", "") or ""
        if not _is_superadmin(role):
            raise HTTPException(403, "Superadmin uniquement")
        title = (data.title or "").strip()
        description = (data.description or "").strip()
        if len(title) < 5:
            raise HTTPException(400, "Titre trop court (min 5 caracteres)")
        if len(description) < 10:
            raise HTTPException(400, "Description trop courte (min 10 caracteres)")
        if data.target not in ("all", "specific"):
            raise HTTPException(400, "target doit etre 'all' ou 'specific'")

        # Resolution des syndics cibles
        if data.target == "all":
            # Les users ont un _id ObjectId - on cast en string cote sortie
            targets = []
            async for u in db.users.find(
                {"role": "syndic", "must_change_password": {"$ne": True}},
                {"_id": 1, "email": 1, "name": 1},
            ):
                targets.append({
                    "id": str(u["_id"]),
                    "email": u.get("email"),
                    "name": u.get("name"),
                })
            target_ids = ["*"]  # marque broadcast
        else:
            if not data.target_syndic_ids:
                raise HTTPException(400, "target_syndic_ids requis pour target='specific'")
            # Les syndics n'ont souvent qu'un `_id` (ObjectId) - on cherche
            # d'abord par ObjectId, puis par `id` string en fallback.
            from bson import ObjectId
            oids = []
            for sid in data.target_syndic_ids:
                try: oids.append(ObjectId(sid))
                except Exception: pass
            targets = []
            if oids:
                async for u in db.users.find(
                    {"_id": {"$in": oids}, "role": "syndic"},
                    {"email": 1, "name": 1},
                ):
                    targets.append({
                        "id": str(u["_id"]),
                        "email": u.get("email"),
                        "name": u.get("name"),
                    })
            if not targets:
                async for u in db.users.find(
                    {"id": {"$in": data.target_syndic_ids}, "role": "syndic"},
                    {"_id": 0, "id": 1, "email": 1, "name": 1},
                ):
                    targets.append(u)
            target_ids = data.target_syndic_ids

        # Contexte superadmin (createur)
        user_id = getattr(request.state, "user_id", "") or ""
        admin_ctx = await _load_user_context(db, user_id)

        number = await _next_ticket_number(db)
        ticket_id = str(uuid.uuid4())
        now = _now()
        ticket = {
            "id": ticket_id,
            "number": number,
            "title": title[:200],
            "description": description[:10000],
            "steps_to_reproduce": "",
            "expected_behavior": "",
            "observed_behavior": "",
            "requester_user_id": user_id,
            "requester_email": admin_ctx.get("email", ""),
            "requester_name": admin_ctx.get("name", "Super Administrateur"),
            "requester_role": "superadmin",
            "syndic_id": None,  # annonce = pas de syndic proprietaire
            "copropriete_id": None,
            "attachments": [],
            "status": "open",  # annonce ouverte a la lecture / discussion
            "assigned_to_user_id": user_id,
            "assigned_to_name": admin_ctx.get("name", "Super Administrateur"),
            "linked_conversation_id": None,
            "is_admin_announcement": True,
            "target_syndic_ids": target_ids,  # ["*"] ou liste
            "target_syndic_count": len(targets),
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        await db.support_tickets.insert_one(ticket)
        await db.support_ticket_events.insert_one({
            "id": str(uuid.uuid4()), "ticket_id": ticket_id,
            "event_type": "admin_announced",
            "actor_user_id": user_id, "actor_name": admin_ctx.get("name", ""),
            "actor_role": "superadmin",
            "old_status": None, "new_status": "open",
            "comment": f"Annonce diffusee a {len(targets)} syndic(s)"
                       + (" (tous)" if data.target == "all" else ""),
            "created_at": now,
        })

        # Email de notification aux syndics cibles (best-effort)
        if data.notify_email and targets:
            recipients = [t["email"] for t in targets if t.get("email")]
            background.add_task(
                _send_admin_announcement_email,
                recipients=recipients,
                ticket=ticket,
                admin_name=admin_ctx.get("name", "Super Administrateur"),
                target_scope=data.target,
                target_count=len(recipients),
            )

        return {
            **_mask_ticket(ticket),
            "notified_recipients": len([t for t in targets if t.get("email")]),
            "total_targets": len(targets),
        }

    @router.post("")
    async def create_ticket(
        request: Request,
        background: BackgroundTasks,
        title: str = Form(...),
        description: str = Form(...),
        steps_to_reproduce: Optional[str] = Form(""),
        expected_behavior: Optional[str] = Form(""),
        observed_behavior: Optional[str] = Form(""),
        requester_email: Optional[str] = Form(""),
        copropriete_id: Optional[str] = Form(""),
        linked_conversation_id: Optional[str] = Form(""),
        files: List[UploadFile] = File(default=[]),
    ):
        user_id = getattr(request.state, "user_id", "")
        if not user_id:
            raise HTTPException(401, "Authentification requise")
        title = (title or "").strip()
        description = (description or "").strip()
        if not title or len(title) < 5:
            raise HTTPException(400, "Titre trop court (min 5 caracteres)")
        if not description or len(description) < 10:
            raise HTTPException(400, "Description trop courte (min 10 caracteres)")

        # Filtrage / validation des fichiers
        files = files or []
        if len(files) > MAX_FILES:
            raise HTTPException(400, f"Maximum {MAX_FILES} pieces jointes autorisees")

        # Contexte user
        ctx = await _load_user_context(db, user_id)
        req_email = (requester_email or ctx["email"] or "").strip()
        if not req_email:
            raise HTTPException(400, "Email demandeur manquant")

        # syndic_id (Chinese Wall)
        sid = getattr(request.state, "syndic_id", None)

        # Upload pieces jointes vers GridFS
        storage = _get_ticket_storage(db)
        attachments = []
        for f in files:
            content = await f.read()
            size = len(content)
            if size == 0:
                continue
            if size > MAX_FILE_SIZE:
                raise HTTPException(
                    400,
                    f"Piece jointe '{f.filename}' trop grosse ({size} octets, max {MAX_FILE_SIZE})",
                )
            file_id = await storage.upload(
                filename=f.filename or "attachment",
                contents=content,
                metadata={
                    "content_type": f.content_type or "application/octet-stream",
                    "uploaded_by": user_id,
                    "uploaded_at": _now(),
                },
            )
            attachments.append({
                "file_id": file_id,
                "filename": f.filename or "attachment",
                "size": size,
                "content_type": f.content_type or "application/octet-stream",
            })

        number = await _next_ticket_number(db)
        ticket_id = str(uuid.uuid4())
        now = _now()
        ticket = {
            "id": ticket_id,
            "number": number,
            "title": title[:200],
            "description": description[:10000],
            "steps_to_reproduce": (steps_to_reproduce or "")[:5000],
            "expected_behavior": (expected_behavior or "")[:5000],
            "observed_behavior": (observed_behavior or "")[:5000],
            "requester_user_id": user_id,
            "requester_email": req_email,
            "requester_name": ctx["name"],
            "requester_role": ctx["role"],
            "syndic_id": sid,
            "copropriete_id": (copropriete_id or None) or None,
            "attachments": attachments,
            "status": "open",
            "assigned_to_user_id": None,
            "assigned_to_name": None,
            "linked_conversation_id": (linked_conversation_id or "") or None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        await db.support_tickets.insert_one(ticket)

        # Event "created"
        await db.support_ticket_events.insert_one({
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "event_type": "created",
            "actor_user_id": user_id,
            "actor_name": ctx["name"],
            "actor_role": ctx["role"],
            "old_status": None,
            "new_status": "open",
            "comment": "",
            "created_at": now,
        })

        # Email au support (background)
        support_email = os.environ.get("TICKETS_SUPPORT_EMAIL", "").strip() \
            or os.environ.get("SUPPORT_EMAIL", "").strip() \
            or "support@nextgecopro.be"
        background.add_task(_send_ticket_created_email, support_email=support_email, ticket=ticket)

        return _mask_ticket(ticket)

    @router.get("")
    async def list_tickets(
        request: Request,
        status: Optional[str] = None,
        syndic_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
    ):
        role = getattr(request.state, "user_role", "") or ""
        q: dict = {}
        if _is_superadmin(role):
            if syndic_id:
                q["syndic_id"] = syndic_id
        else:
            # Isolation : soit ticket ouvert par cet user, soit meme syndic_id,
            # soit annonce superadmin ciblant ce syndic (iter95s).
            sid = getattr(request.state, "syndic_id", None)
            user_id = getattr(request.state, "user_id", "")
            or_conds = [{"requester_user_id": user_id}]
            if sid:
                or_conds.append({"syndic_id": sid})
                or_conds.append({"target_syndic_ids": sid})       # annonce ciblee
            or_conds.append({"target_syndic_ids": "*"})           # broadcast
            q["$or"] = or_conds
        if status:
            if status not in STATUSES:
                raise HTTPException(400, f"Statut inconnu : {status}")
            q["status"] = status
        if search:
            import re as _re
            rgx = {"$regex": _re.escape(search), "$options": "i"}
            search_or = [{"title": rgx}, {"description": rgx}, {"number": rgx}, {"requester_name": rgx}]
            existing_or = q.pop("$or", None)
            if existing_or:
                q["$and"] = [{"$or": existing_or}, {"$or": search_or}]
            else:
                q["$or"] = search_or
        tickets = await db.support_tickets.find(q, {"_id": 0}).sort("created_at", -1).to_list(min(limit, 500))
        return [_mask_ticket(t) for t in tickets]

    @router.get("/{ticket_id}")
    async def get_ticket(ticket_id: str, request: Request):
        t = await _load_ticket_scoped(db, ticket_id, request)
        return _mask_ticket(t)

    @router.get("/{ticket_id}/events")
    async def get_events(ticket_id: str, request: Request):
        await _load_ticket_scoped(db, ticket_id, request)
        events = await db.support_ticket_events.find(
            {"ticket_id": ticket_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        return events

    @router.post("/{ticket_id}/status")
    async def change_status(
        ticket_id: str, data: StatusChangeInput,
        request: Request, background: BackgroundTasks,
    ):
        role = getattr(request.state, "user_role", "") or ""
        if not _is_superadmin(role):
            raise HTTPException(403, "Reserve au superadmin")
        t = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        if not t:
            raise HTTPException(404, "Ticket introuvable")
        new_status = (data.new_status or "").strip().lower()
        if new_status not in STATUSES:
            raise HTTPException(400, f"Statut invalide : {new_status}. Valeurs : {STATUSES}")
        old_status = t.get("status", "open")
        if new_status == old_status:
            return _mask_ticket(t)
        user_id = getattr(request.state, "user_id", "")
        ctx = await _load_user_context(db, user_id)
        now = _now()
        update = {"status": new_status, "updated_at": now}
        if new_status == "closed":
            update["closed_at"] = now
        elif old_status == "closed":
            update["closed_at"] = None
        await db.support_tickets.update_one({"id": ticket_id}, {"$set": update})

        # Event
        await db.support_ticket_events.insert_one({
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "event_type": "status_changed",
            "actor_user_id": user_id,
            "actor_name": ctx["name"],
            "actor_role": ctx["role"],
            "old_status": old_status,
            "new_status": new_status,
            "comment": (data.comment or "")[:5000],
            "created_at": now,
        })

        # Email au demandeur
        background.add_task(
            _send_status_changed_email,
            requester_email=t.get("requester_email", ""),
            ticket={**t, **update},
            old_status=old_status,
            new_status=new_status,
            comment=(data.comment or ""),
            actor_name=ctx["name"],
        )

        refreshed = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        return _mask_ticket(refreshed)

    @router.post("/{ticket_id}/comments")
    async def add_comment(ticket_id: str, data: CommentInput, request: Request, background: BackgroundTasks):
        t = await _load_ticket_scoped(db, ticket_id, request)
        comment = (data.comment or "").strip()
        if not comment:
            raise HTTPException(400, "Commentaire vide")
        user_id = getattr(request.state, "user_id", "")
        ctx = await _load_user_context(db, user_id)
        now = _now()
        event = {
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "event_type": "comment",
            "actor_user_id": user_id,
            "actor_name": ctx["name"],
            "actor_role": ctx["role"],
            "old_status": None,
            "new_status": None,
            "comment": comment[:5000],
            "created_at": now,
        }
        await db.support_ticket_events.insert_one(event)
        await db.support_tickets.update_one(
            {"id": ticket_id}, {"$set": {"updated_at": now}}
        )
        event.pop("_id", None)

        # iter90h1 : notification email du commentaire
        # - Superadmin/admin commente -> notifier le demandeur
        # - Syndic/owner commente -> notifier support@
        support_email = os.environ.get("TICKETS_SUPPORT_EMAIL", "").strip() \
            or os.environ.get("SUPPORT_EMAIL", "").strip() \
            or "support@nextgecopro.be"
        if _is_superadmin(ctx["role"]):
            # Superadmin -> demandeur (ou aux syndics cibles si c'est une annonce)
            targets = []
            if t.get("is_admin_announcement"):
                # iter95t : sur une annonce, une reponse superadmin notifie
                # les syndics cibles du thread pour qu'ils voient la reponse.
                target_ids = t.get("target_syndic_ids") or []
                if "*" in target_ids:
                    async for u in db.users.find(
                        {"role": "syndic", "must_change_password": {"$ne": True}},
                        {"email": 1},
                    ):
                        if u.get("email"):
                            targets.append(u["email"])
                else:
                    from bson import ObjectId
                    oids = []
                    for sid in target_ids:
                        try: oids.append(ObjectId(sid))
                        except Exception: pass
                    if oids:
                        async for u in db.users.find({"_id": {"$in": oids}}, {"email": 1}):
                            if u.get("email"):
                                targets.append(u["email"])
            else:
                if t.get("requester_email"):
                    targets = [t.get("requester_email")]
            for rec in targets:
                background.add_task(
                    _send_comment_notification_email,
                    recipient_email=rec,
                    ticket=t,
                    comment=comment,
                    actor_name=ctx["name"],
                    actor_role="Support",
                )
        else:
            # iter95t : syndic/owner qui repond a une annonce -> notifie
            # directement le superadmin auteur de l'annonce (au lieu de
            # support@ generique) pour un thread efficace.
            if t.get("is_admin_announcement") and t.get("requester_email"):
                background.add_task(
                    _send_comment_notification_email,
                    recipient_email=t["requester_email"],
                    ticket=t,
                    comment=comment,
                    actor_name=ctx["name"],
                    actor_role=ctx["role"],
                    reply_to=ctx["email"] or None,
                )
            else:
                # Syndic/owner -> support (avec reply-to = demandeur)
                background.add_task(
                    _send_comment_notification_email,
                    recipient_email=support_email,
                    ticket=t,
                    comment=comment,
                    actor_name=ctx["name"],
                    actor_role=ctx["role"],
                    reply_to=ctx["email"] or t.get("requester_email", "") or None,
                )
        return event

    @router.post("/{ticket_id}/resend-support-email")
    async def resend_support_creation_email(ticket_id: str, request: Request, background: BackgroundTasks):
        """iter90h1 : renvoie l'email de creation vers support@nextgecopro.be.
        Utile quand la notif initiale a echoue (ex: MAIL_ENABLED=false au moment
        de la creation). Reserve aux superadmins."""
        role = getattr(request.state, "user_role", "") or ""
        if not _is_superadmin(role):
            raise HTTPException(403, "Reserve au superadmin")
        t = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        if not t:
            raise HTTPException(404, "Ticket introuvable")
        support_email = os.environ.get("TICKETS_SUPPORT_EMAIL", "").strip() \
            or os.environ.get("SUPPORT_EMAIL", "").strip() \
            or "support@nextgecopro.be"
        background.add_task(_send_ticket_created_email, support_email=support_email, ticket=t)
        return {"ok": True, "sent_to": support_email, "ticket_number": t.get("number", "")}

    @router.post("/{ticket_id}/assign")
    async def assign_ticket(ticket_id: str, data: AssignInput, request: Request):
        role = getattr(request.state, "user_role", "") or ""
        if not _is_superadmin(role):
            raise HTTPException(403, "Reserve au superadmin")
        t = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        if not t:
            raise HTTPException(404, "Ticket introuvable")
        assigned_user_id = (data.assigned_to_user_id or "").strip() or None
        assigned_name = None
        if assigned_user_id:
            actx = await _load_user_context(db, assigned_user_id)
            assigned_name = actx["name"]
        now = _now()
        update = {
            "assigned_to_user_id": assigned_user_id,
            "assigned_to_name": assigned_name,
            "updated_at": now,
        }
        # Assigner passe automatiquement en statut "assigned" si etait "open"
        if assigned_user_id and t.get("status") == "open":
            update["status"] = "assigned"
        await db.support_tickets.update_one({"id": ticket_id}, {"$set": update})
        user_id = getattr(request.state, "user_id", "")
        actor_ctx = await _load_user_context(db, user_id)
        await db.support_ticket_events.insert_one({
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "event_type": "assigned",
            "actor_user_id": user_id,
            "actor_name": actor_ctx["name"],
            "actor_role": actor_ctx["role"],
            "old_status": t.get("status"),
            "new_status": update.get("status", t.get("status")),
            "comment": f"Assigne a {assigned_name}" if assigned_name else "Desassigne",
            "created_at": now,
        })
        refreshed = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        return _mask_ticket(refreshed)

    @router.get("/{ticket_id}/attachments/{file_id}")
    async def download_attachment(ticket_id: str, file_id: str, request: Request):
        t = await _load_ticket_scoped(db, ticket_id, request)
        # Verifie que le file_id appartient bien a ce ticket
        match = next(
            (a for a in (t.get("attachments") or []) if a.get("file_id") == file_id),
            None,
        )
        if not match:
            raise HTTPException(404, "Piece jointe introuvable")
        storage = _get_ticket_storage(db)
        try:
            data = await storage.download(file_id)
        except Exception as e:
            logger.exception("Echec download ticket attachment: %s", e)
            raise HTTPException(404, "Fichier introuvable en stockage")
        return StreamingResponse(
            io.BytesIO(data),
            media_type=match.get("content_type") or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{match.get("filename","attachment")}"'},
        )

    @router.delete("/{ticket_id}")
    async def delete_ticket(ticket_id: str, request: Request):
        role = getattr(request.state, "user_role", "") or ""
        if not _is_superadmin(role):
            raise HTTPException(403, "Reserve au superadmin")
        t = await db.support_tickets.find_one({"id": ticket_id}, {"_id": 0})
        if not t:
            raise HTTPException(404, "Ticket introuvable")
        storage = _get_ticket_storage(db)
        for a in (t.get("attachments") or []):
            fid = a.get("file_id")
            if fid:
                await storage.delete(fid)
        await db.support_ticket_events.delete_many({"ticket_id": ticket_id})
        await db.support_tickets.delete_one({"id": ticket_id})
        return {"message": "Ticket supprime"}

    return router
