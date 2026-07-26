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
            # Isolation : soit ticket ouvert par cet user, soit meme syndic_id
            sid = getattr(request.state, "syndic_id", None)
            user_id = getattr(request.state, "user_id", "")
            or_conds = [{"requester_user_id": user_id}]
            if sid:
                or_conds.append({"syndic_id": sid})
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
    async def add_comment(ticket_id: str, data: CommentInput, request: Request):
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
        return event

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
