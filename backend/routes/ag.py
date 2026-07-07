"""iter90ax : Module Convocation d'Assemblee Generale (Copropriete belge).

Base legale : Code civil belge, Livre 3, Titre 4, Chapitre III (Copropriete
force) - Sect. 3.87 (Convocation) et 3.88 (Majorites).

Regles cles :
- AG ordinaire : au moins une fois par an, dans la periode fixee au reglement
  (fenetre de 15 jours). Art. 3.87 §1.
- Convocation envoyee au moins 15 jours avant. Art. 3.87 §2 al.2.
- Ordre du jour : fixe par le syndic, chaque coproprietaire peut demander
  ajout de points au moins 3 semaines avant l'AG. Art. 3.87 §3.
- Aucune decision valable sur un point non a l'ordre du jour. Art. 3.87 §4.
- Quorum : au moins 50% des quotites presentes/representees OU > 50% des voix
  ET > 50% des quotites. Sinon nouvelle AG dans 15-30j sans quorum requis. §5.
- Procurations : max 3 par mandataire (sauf conjoint/descendants). §6.
- Majorites Art. 3.88 :
    * simple : majorite absolue des voix presentes/representees
    * 2/3 : travaux, appels de fonds exceptionnels, contrats > 3 ans
    * 4/5 : modification statuts, actes de disposition
    * unanimite : modification quotites, destination de l'immeuble

Types de decision codes :
    "info" : information, non soumis au vote
    "simple" : majorite absolue
    "2_3" : 2/3
    "4_5" : 4/5
    "unanimite" : unanimite

Cycle de vie d'une AG :
    draft -> ready -> convocation_sent -> held -> archived
                          |
                          v (si echec quorum)
                     second_call_scheduled -> convocation_sent -> held
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from routes.syndic_config import _resolve_syndic_user_id

logger = logging.getLogger("ag")


# ---- Constantes legales ----
LEGAL_MIN_DAYS_CONVOCATION = 15  # Art. 3.87 §2
LEGAL_MIN_DAYS_AGENDA_REQUEST = 21  # 3 semaines avant. Art. 3.87 §3
DECISION_TYPES = {
    "info": {"label": "Information", "majority": None, "legal_basis": ""},
    "simple": {"label": "Majorite simple (>50%)", "majority": 0.5, "legal_basis": "Art. 3.88 §1"},
    "2_3": {"label": "Majorite 2/3", "majority": 2/3, "legal_basis": "Art. 3.88 §2"},
    "4_5": {"label": "Majorite 4/5", "majority": 4/5, "legal_basis": "Art. 3.88 §3"},
    "unanimite": {"label": "Unanimite", "majority": 1.0, "legal_basis": "Art. 3.88 §4"},
}


# ---- Pydantic models ----
class AgendaItem(BaseModel):
    id: Optional[str] = None
    order: int = 0
    title: str = Field(..., min_length=2, max_length=250)
    description: str = ""
    decision_type: str = Field(default="simple")
    proposed_by: str = "syndic"  # 'syndic' ou owner_id
    legal_notes: str = ""

    @field_validator("decision_type")
    @classmethod
    def _check_decision_type(cls, v):
        if v not in DECISION_TYPES:
            raise ValueError(f"decision_type invalide (attendu : {list(DECISION_TYPES.keys())})")
        return v


class MeetingCreate(BaseModel):
    copropriete_id: str
    type: str = Field(default="ordinaire")  # 'ordinaire' | 'extraordinaire' | 'second_call'
    scheduled_date: str  # ISO YYYY-MM-DD
    scheduled_time: str = "18:30"
    location: str = Field(..., min_length=3)
    agenda_items: List[AgendaItem] = []
    quorum_required: bool = True  # false pour 2e convocation
    parent_meeting_id: Optional[str] = None  # si 2e convocation
    notes: str = ""


class MeetingUpdate(BaseModel):
    type: Optional[str] = None
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


def _validate_convocation_deadline(scheduled_date: str) -> dict:
    """Verifie que la convocation peut encore etre envoyee dans les delais legaux."""
    try:
        sd = datetime.strptime(scheduled_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Date invalide (attendu YYYY-MM-DD)")
    today = date.today()
    days_to_ag = (sd - today).days
    ok = days_to_ag >= LEGAL_MIN_DAYS_CONVOCATION
    return {
        "scheduled_date": scheduled_date,
        "today": today.isoformat(),
        "days_until_ag": days_to_ag,
        "legal_min_days": LEGAL_MIN_DAYS_CONVOCATION,
        "on_time": ok,
        "warning": None if ok else (
            f"Delai legal non respecte : {days_to_ag}j < {LEGAL_MIN_DAYS_CONVOCATION}j minimum "
            "(art. 3.87 §2 CC). L'AG risque d'etre annulable pour vice de procedure."
        ),
    }


def create_ag_router(db):
    router = APIRouter(prefix="/api/ag", tags=["ag"])

    # ============ Helpers ============

    async def _require_manager(request: Request) -> tuple[str, dict]:
        """Retourne (user_id, user_doc) pour les roles autorises."""
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user:
            raise HTTPException(401, "Utilisateur introuvable")
        if user.get("role") not in ("syndic", "gestionnaire", "admin", "superadmin"):
            raise HTTPException(403, "Reserve aux syndics/gestionnaires")
        return uid, user

    async def _load_meeting(mid: str) -> dict:
        m = await db.ag_meetings.find_one({"id": mid}, {"_id": 0})
        if not m:
            raise HTTPException(404, "AG introuvable")
        return m

    # ============ Constantes / referentiels ============

    @router.get("/decision-types")
    async def list_decision_types(request: Request):
        """Liste les types de decision et leurs bases legales."""
        await _require_manager(request)
        return {"decision_types": DECISION_TYPES,
                "legal": {"min_days_convocation": LEGAL_MIN_DAYS_CONVOCATION,
                          "min_days_agenda_request": LEGAL_MIN_DAYS_AGENDA_REQUEST}}

    # ============ CRUD Meetings ============

    @router.get("/meetings")
    async def list_meetings(request: Request, copropriete_id: Optional[str] = None,
                            status: Optional[str] = None):
        """Liste les AG. Filtre par copropriete_id et status."""
        await _require_manager(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        if status:
            q["status"] = status
        meetings = await db.ag_meetings.find(q, {"_id": 0}).sort([("scheduled_date", -1)]).to_list(1000)
        return {"meetings": meetings}

    @router.get("/meetings/{mid}")
    async def get_meeting(mid: str, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        # Enrichit : copropriete
        m["copropriete"] = await db.coproprietes.find_one(
            {"id": m.get("copropriete_id", "")}, {"_id": 0}) or {}
        return m

    @router.post("/meetings")
    async def create_meeting(payload: MeetingCreate, request: Request):
        uid, _ = await _require_manager(request)
        copro = await db.coproprietes.find_one({"id": payload.copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete introuvable")
        deadline_check = _validate_convocation_deadline(payload.scheduled_date)

        # Auto-generation IDs sur les agenda items
        items = []
        for i, it in enumerate(payload.agenda_items):
            d = it.model_dump()
            d["id"] = d.get("id") or str(uuid.uuid4())
            d["order"] = d.get("order") or (i + 1)
            items.append(d)

        doc = {
            "id": str(uuid.uuid4()),
            "copropriete_id": payload.copropriete_id,
            "type": payload.type,
            "scheduled_date": payload.scheduled_date,
            "scheduled_time": payload.scheduled_time,
            "location": payload.location,
            "agenda_items": items,
            "quorum_required": payload.quorum_required,
            "parent_meeting_id": payload.parent_meeting_id,
            "notes": payload.notes,
            "status": "draft",
            "convocation_sent_at": None,
            "convocation_sent_by": None,
            "convocation_sent_to_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": uid,
            "legal_check": deadline_check,
        }
        await db.ag_meetings.insert_one(dict(doc))
        return doc

    @router.put("/meetings/{mid}")
    async def update_meeting(mid: str, payload: MeetingUpdate, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") in ("held", "archived"):
            raise HTTPException(400, f"AG en status '{m['status']}' - modification impossible")
        update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if not update:
            raise HTTPException(400, "Aucune modification")
        if "scheduled_date" in update:
            update["legal_check"] = _validate_convocation_deadline(update["scheduled_date"])
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.ag_meetings.update_one({"id": mid}, {"$set": update})
        return await _load_meeting(mid)

    @router.delete("/meetings/{mid}")
    async def delete_meeting(mid: str, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") in ("convocation_sent", "held", "archived"):
            raise HTTPException(400, "AG deja convoquee ou tenue - suppression interdite")
        await db.ag_meetings.delete_one({"id": mid})
        return {"success": True}

    # ============ Agenda items CRUD ============

    @router.post("/meetings/{mid}/agenda-items")
    async def add_agenda_item(mid: str, payload: AgendaItem, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") in ("held", "archived"):
            raise HTTPException(400, "AG terminee - ordre du jour fige")
        d = payload.model_dump()
        d["id"] = d.get("id") or str(uuid.uuid4())
        items = m.get("agenda_items") or []
        d["order"] = d.get("order") or (len(items) + 1)
        items.append(d)
        await db.ag_meetings.update_one({"id": mid}, {"$set": {"agenda_items": items,
                                                                "updated_at": datetime.now(timezone.utc).isoformat()}})
        return d

    @router.put("/meetings/{mid}/agenda-items/{iid}")
    async def update_agenda_item(mid: str, iid: str, payload: AgendaItem, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") in ("held", "archived"):
            raise HTTPException(400, "AG terminee")
        items = m.get("agenda_items") or []
        idx = next((i for i, it in enumerate(items) if it.get("id") == iid), -1)
        if idx < 0:
            raise HTTPException(404, "Point de l'ordre du jour introuvable")
        d = payload.model_dump()
        d["id"] = iid
        items[idx] = d
        await db.ag_meetings.update_one({"id": mid}, {"$set": {"agenda_items": items}})
        return d

    @router.delete("/meetings/{mid}/agenda-items/{iid}")
    async def delete_agenda_item(mid: str, iid: str, request: Request):
        await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") in ("held", "archived"):
            raise HTTPException(400, "AG terminee")
        items = [it for it in (m.get("agenda_items") or []) if it.get("id") != iid]
        await db.ag_meetings.update_one({"id": mid}, {"$set": {"agenda_items": items}})
        return {"success": True}

    @router.post("/meetings/{mid}/reorder-agenda")
    async def reorder_agenda(mid: str, request: Request, order: List[str] = None):
        """Reordonne l'agenda selon la liste d'IDs fournie."""
        await _require_manager(request)
        m = await _load_meeting(mid)
        if not order:
            raise HTTPException(400, "Liste d'IDs requise")
        items = m.get("agenda_items") or []
        by_id = {it.get("id"): it for it in items}
        new_items = []
        for i, iid in enumerate(order):
            if iid in by_id:
                by_id[iid]["order"] = i + 1
                new_items.append(by_id[iid])
        # Ajoute les items non listes a la fin
        for it in items:
            if it.get("id") not in order:
                it["order"] = len(new_items) + 1
                new_items.append(it)
        await db.ag_meetings.update_one({"id": mid}, {"$set": {"agenda_items": new_items}})
        return {"success": True, "agenda_items": new_items}

    # ============ Convocation PDF + envoi ============

    @router.get("/meetings/{mid}/convocation/pdf")
    async def convocation_pdf(mid: str, request: Request):
        """Retourne le PDF de convocation pour telechargement."""
        from fastapi.responses import Response as FastAPIResponse
        await _require_manager(request)
        m = await _load_meeting(mid)
        copro = await db.coproprietes.find_one({"id": m.get("copropriete_id", "")}, {"_id": 0}) or {}
        # On genere une convocation "modele" (adresse destinataire generique).
        # L'envoi personnalise se fait via l'endpoint /send.
        pdf_bytes = await _build_convocation_pdf_bytes(db, m, copro, owner=None)
        filename = f"convocation-ag-{m.get('scheduled_date','')}-{copro.get('reference','')}.pdf"
        return FastAPIResponse(
            content=pdf_bytes, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.post("/meetings/{mid}/convocation/send")
    async def send_convocation(mid: str, request: Request,
                                from_mailbox: str = "", template_id: str = ""):
        """Envoie la convocation par email a tous les proprietaires de l'ACP,
        avec le PDF de convocation personnalise (adresse destinataire fenetre C6).

        Marque l'AG comme `convocation_sent` avec date/heure.
        """
        uid, user = await _require_manager(request)
        m = await _load_meeting(mid)
        if m.get("status") == "held":
            raise HTTPException(400, "AG deja tenue")
        # Verifie delai legal
        legal = _validate_convocation_deadline(m.get("scheduled_date", ""))
        if not legal["on_time"]:
            logger.warning("Convocation AG %s hors delai legal : %sj (min %sj)",
                           mid, legal["days_until_ag"], LEGAL_MIN_DAYS_CONVOCATION)

        copro = await db.coproprietes.find_one({"id": m.get("copropriete_id", "")}, {"_id": 0}) or {}
        # Recupere tous les proprietaires actifs de la copro (via lots)
        lots = await db.lots.find({"copropriete_id": m["copropriete_id"]}, {"_id": 0}).to_list(10000)
        owner_ids = list({lt.get("owner_id") for lt in lots if lt.get("owner_id")})
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(1000) if owner_ids else []

        # Envoi via le module communication
        from routes.communication import _resolve_syndic_scope, _send_email, _build_html_with_signature

        _, scope_id = await _resolve_syndic_scope(db, request)
        scope_user = await db.users.find_one({"_id": ObjectId(scope_id)})
        boxes = scope_user.get("authorized_mailboxes") or []
        boxes_addrs = {b.get("address", "").lower() for b in boxes if b.get("active", True)}
        if not from_mailbox:
            default_box = next((b for b in boxes if b.get("default")), None) or (boxes[0] if boxes else None)
            from_mailbox = (default_box or {}).get("address", "") or scope_user.get("email", "")
        if from_mailbox.lower() not in boxes_addrs and from_mailbox.lower() != (scope_user.get("email", "") or "").lower():
            raise HTTPException(403, f"Boite expediteur '{from_mailbox}' non autorisee")

        sent = 0
        failed = []
        subject = f"Convocation Assemblee Generale {m.get('type','ordinaire')} du {m.get('scheduled_date','')}"
        body_default = (
            f"<p>Bonjour,</p>"
            f"<p>Vous trouverez en piece jointe la convocation officielle a l'Assemblee "
            f"Generale <b>{m.get('type','ordinaire')}</b> de la copropriete "
            f"<b>{copro.get('name','')}</b>, qui se tiendra le "
            f"<b>{m.get('scheduled_date','')}</b> a <b>{m.get('scheduled_time','')}</b> "
            f"a <b>{m.get('location','')}</b>.</p>"
            f"<p>Conformement a l'article 3.87 du Code civil, cette convocation vous est "
            f"adressee au moins 15 jours avant la reunion. L'ordre du jour figure dans le "
            f"document ci-joint.</p>"
            f"<p>Cordialement,</p>"
        )
        html_body = await _build_html_with_signature(request, body_default, include_signature=True)

        for owner in owners:
            if not owner.get("email"):
                failed.append({"owner_id": owner.get("id"), "reason": "email manquant"})
                continue
            try:
                pdf_bytes = await _build_convocation_pdf_bytes(db, m, copro, owner=owner)
                filename = f"convocation-ag-{m.get('scheduled_date','')}-{owner.get('name','').replace(' ','_')}.pdf"
                await _send_email(from_mailbox, [owner["email"]], subject, html_body,
                                  attachment_pdf=pdf_bytes, attachment_filename=filename)
                sent += 1
            except Exception as e:
                logger.warning("Convocation send failed for %s : %s", owner.get("id"), e)
                failed.append({"owner_id": owner.get("id"), "reason": str(e)[:100]})

        now = datetime.now(timezone.utc).isoformat()
        await db.ag_meetings.update_one({"id": mid}, {"$set": {
            "status": "convocation_sent",
            "convocation_sent_at": now,
            "convocation_sent_by": uid,
            "convocation_sent_to_count": sent,
            "convocation_sent_from_mailbox": from_mailbox,
            "convocation_failed": failed,
        }})
        return {"success": True, "sent": sent, "failed": failed, "legal_check": legal}

    return router


# ============ PDF Convocation builder (module-level) ============

async def _build_convocation_pdf_bytes(db, meeting: dict, copropriete: dict, owner: Optional[dict] = None) -> bytes:
    """Genere le PDF de convocation. Utilise le pdf_layout iter90av pour
    l'entete cabinet + adresse destinataire fenetre C6."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from pdf_layout import (
        build_header_with_logo, build_recipient_address_flowable,
        make_footer_callback, resolve_syndic_pdf_context,
    )

    ctx = await resolve_syndic_pdf_context(db, copropriete)
    use_new = bool(ctx.get("syndic_config"))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=28 * mm if use_new else 18 * mm,
        title=f"Convocation AG {meeting.get('type','ordinaire')} - {copropriete.get('name','')}",
    )
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=11)
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=13)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=14, textColor=colors.HexColor("#0F172A"), spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, textColor=colors.HexColor("#1E40AF"), spaceAfter=4)
    legal_style = ParagraphStyle("legal", parent=styles["Normal"], fontSize=8, leading=10,
                                  textColor=colors.HexColor("#64748B"), fontName="Helvetica-Oblique")

    elems = []
    # Header + destinataire C6
    if use_new:
        elems.append(build_header_with_logo(ctx.get("logo_bytes"), ctx.get("syndic_config") or {}, small))
        elems.append(Spacer(1, 4 * mm))
        if owner:
            elems.append(build_recipient_address_flowable(owner, small))
        else:
            # Modele generique : montrer un placeholder
            elems.append(build_recipient_address_flowable(
                {"name": "[Proprietaire]", "address": "[Adresse]", "postal_code": "[CP]", "city": "[Ville]"},
                small,
            ))
        elems.append(Spacer(1, 8 * mm))
    else:
        elems.append(Paragraph(f"<b>{copropriete.get('name','')}</b>", h1))
        elems.append(Spacer(1, 4 * mm))

    # Titre
    type_label = {"ordinaire": "ORDINAIRE", "extraordinaire": "EXTRAORDINAIRE",
                  "second_call": "ORDINAIRE (2e convocation)"}.get(meeting.get("type", "ordinaire"), "ORDINAIRE")
    elems.append(Paragraph(f"CONVOCATION - ASSEMBLEE GENERALE {type_label}", h1))
    elems.append(Paragraph(f"Copropriete : <b>{copropriete.get('name','')}</b>", normal))
    if copropriete.get("address"):
        elems.append(Paragraph(f"Adresse : {copropriete.get('address','')}, "
                                f"{copropriete.get('postal_code','')} {copropriete.get('city','')}", small))
    elems.append(Spacer(1, 6 * mm))

    # Bloc date/lieu/heure
    info_tbl = Table([
        ["Date", meeting.get("scheduled_date", "")],
        ["Heure", meeting.get("scheduled_time", "")],
        ["Lieu", meeting.get("location", "")],
    ], colWidths=[35 * mm, 145 * mm])
    info_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    elems.append(info_tbl)
    elems.append(Spacer(1, 6 * mm))

    # Mention legale delai
    today = date.today()
    try:
        sd = datetime.strptime(meeting.get("scheduled_date", ""), "%Y-%m-%d").date()
        days = (sd - today).days
    except Exception:
        days = -1
    if days >= 0:
        elems.append(Paragraph(
            f"Convocation adressee le {today.strftime('%d/%m/%Y')} - "
            f"soit {days} jour(s) avant l'AG. "
            f"<i>(Art. 3.87 §2 CC - minimum 15 jours)</i>",
            small,
        ))
        elems.append(Spacer(1, 4 * mm))

    # ORDRE DU JOUR
    elems.append(Paragraph("Ordre du jour", h2))
    items = sorted(meeting.get("agenda_items", []) or [], key=lambda x: x.get("order", 0))
    if not items:
        elems.append(Paragraph("<i>Aucun point a l'ordre du jour.</i>", small))
    else:
        rows = [["#", "Point", "Type de vote", "Base legale"]]
        for it in items:
            dt = DECISION_TYPES.get(it.get("decision_type", "simple"), {})
            rows.append([
                str(it.get("order", "")),
                Paragraph(f"<b>{it.get('title','')}</b>" +
                          (f"<br/><font size='8'>{it.get('description','')}</font>" if it.get("description") else ""),
                          small),
                dt.get("label", ""),
                dt.get("legal_basis", ""),
            ])
        agenda_tbl = Table(rows, colWidths=[10 * mm, 100 * mm, 40 * mm, 30 * mm], repeatRows=1)
        agenda_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E40AF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#94A3B8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ]))
        elems.append(agenda_tbl)
    elems.append(Spacer(1, 6 * mm))

    # Rappel legal : procurations + quorum
    elems.append(Paragraph("Rappels legaux", h2))
    quorum_text = (
        "Le quorum est atteint si au moins la moitie des quotites sont "
        "presentes ou representees, OU si plus de la moitie des coproprietaires sont "
        "presents/representes et detiennent au moins la moitie des quotites "
        "(<i>art. 3.87 §5 CC</i>)."
    )
    if not meeting.get("quorum_required", True):
        quorum_text = (
            "<b>2e convocation :</b> aucun quorum n'est requis pour cette assemblee "
            "(<i>art. 3.87 §5 al.3 CC</i>). Les decisions sont prises a la majorite "
            "requise, quel que soit le nombre de coproprietaires presents/representes."
        )
    elems.append(Paragraph(quorum_text, small))
    elems.append(Spacer(1, 2 * mm))
    elems.append(Paragraph(
        "Chaque coproprietaire peut se faire representer par un mandataire porteur "
        "de procuration ecrite. Un meme mandataire ne peut detenir plus de "
        "<b>trois procurations</b> (sauf conjoint ou descendants). "
        "<i>(Art. 3.87 §6 CC)</i>",
        small,
    ))
    elems.append(Spacer(1, 2 * mm))
    elems.append(Paragraph(
        "Toute demande d'ajout d'un point a l'ordre du jour doit etre adressee au "
        "syndic au moins <b>trois semaines avant</b> l'AG. Aucune decision valable "
        "ne peut etre prise sur un point non inscrit a l'ordre du jour. "
        "<i>(Art. 3.87 §3 et §4 CC)</i>",
        small,
    ))
    elems.append(Spacer(1, 6 * mm))

    # Signature syndic
    elems.append(Paragraph("Fait le " + date.today().strftime("%d/%m/%Y") + ",", small))
    elems.append(Paragraph("Le syndic,", small))
    elems.append(Spacer(1, 8 * mm))
    if ctx.get("syndic_config"):
        elems.append(Paragraph(f"<b>{ctx['syndic_config'].get('legal_name','')}</b>", normal))

    # Footer
    footer = make_footer_callback(ctx.get("legal_mentions", "")) if use_new else None
    if footer:
        doc.build(elems, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(elems)
    return buf.getvalue()
