"""iter90aw : Modeles d'emails reutilisables par cabinet syndic.

Objectifs :
- Chaque cabinet cree/edite des templates (nom, sujet, corps HTML) reutilisables
  dans les envois (Communication > Envois).
- Support de variables dynamiques {owner_name}, {balance}, {vcs_code},
  {copropriete_name}, {syndic_name}, {today}, {due_amount}, {days_late}.
- Templates de base pre-charges (relance amiable, rappel avant mise en demeure,
  accuse reception, confirmation reglement).
- Chinese wall : chaque cabinet syndic voit ses propres templates ; les
  gestionnaires enfants heritent des templates du parent (lecture + utilisation).
- Superadmin ne les gere PAS (ce sont des donnees metier propres au cabinet).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from routes.syndic_config import _resolve_syndic_user_id

logger = logging.getLogger("email_templates")


# ---- Variables supportees dans les templates ----
SUPPORTED_VARIABLES = {
    "owner_name": "Nom du proprietaire",
    "owner_email": "Email du proprietaire",
    "balance": "Solde en EUR (positif = debiteur)",
    "abs_balance": "Solde en valeur absolue",
    "balance_status": "'debiteur' | 'crediteur' | 'solde'",
    "vcs_code": "Communication structuree (+++XXX/XXXX/XXXXX+++)",
    "copropriete_name": "Nom de la copropriete",
    "copropriete_address": "Adresse de la copropriete",
    "syndic_name": "Nom du cabinet syndic",
    "manager_name": "Prenom Nom du gestionnaire connecte",
    "manager_email": "Email du gestionnaire connecte",
    "manager_phone": "GSM du gestionnaire (ou telephone cabinet syndic)",
    "today": "Date d'envoi (JJ/MM/AAAA)",
    "iban": "IBAN du compte de la copropriete",
    "bic": "BIC du compte",
    # iter93cx : reperes temporels comptables
    "fiscal_year_start": "Date de debut de l'exercice comptable en cours (JJ/MM/AAAA)",
    "fiscal_year_end": "Date de fin de l'exercice comptable en cours (JJ/MM/AAAA)",
    "fiscal_year_label": "Libelle de l'exercice (ex : Exercice 2026-2027)",
    "last_bank_statement_date": "Date du dernier extrait comptabilise (JJ/MM/AAAA)",
}


# ---- Templates par defaut charges au 1er acces ----
DEFAULT_TEMPLATES = [
    {
        "id": "default_relance_amiable",
        "name": "Relance amiable J+15",
        "category": "recouvrement",
        "subject": "Rappel amiable : votre solde du {today}",
        "body_html": (
            "<p>Bonjour {owner_name},</p>"
            "<p>Sauf erreur de notre part, il reste un solde de "
            "<b>{abs_balance} EUR</b> sur votre compte proprietaire "
            "de la copropriete <b>{copropriete_name}</b>.</p>"
            "<p>Merci de proceder au reglement en communiquant obligatoirement "
            "la reference structuree :<br><b>{vcs_code}</b><br>"
            "sur notre compte IBAN <b>{iban}</b>.</p>"
            "<p>Vous trouverez en piece jointe le detail de votre situation "
            "de compte a ce jour.</p>"
            "<p>Cordialement,</p>"
        ),
        "is_default": True,
    },
    {
        "id": "default_mise_en_demeure",
        "name": "Avant mise en demeure J+45",
        "category": "recouvrement",
        "subject": "Rappel avant mise en demeure - Copropriete {copropriete_name}",
        "body_html": (
            "<p>Bonjour {owner_name},</p>"
            "<p>Malgre nos precedents rappels, votre solde debiteur de "
            "<b>{abs_balance} EUR</b> reste impaye a ce jour.</p>"
            "<p>Sauf reglement dans les 8 jours calendrier a compter de la "
            "reception de cet email, nous serons contraints d'engager une "
            "procedure de recouvrement judiciaire, avec frais et interets a "
            "votre charge (art. 3.86-3.87 CC).</p>"
            "<p>Reglement a effectuer sur <b>{iban}</b> avec la communication "
            "<b>{vcs_code}</b>.</p>"
            "<p>Cordialement,</p>"
        ),
        "is_default": True,
    },
    {
        "id": "default_accuse_reglement",
        "name": "Accuse reception reglement",
        "category": "confirmation",
        "subject": "Bien recu : votre reglement pour {copropriete_name}",
        "body_html": (
            "<p>Bonjour {owner_name},</p>"
            "<p>Nous accusons bonne reception de votre reglement. "
            "Votre solde actuel est de <b>{abs_balance} EUR</b> ({balance_status}).</p>"
            "<p>Vous trouverez ci-joint le detail actualise de votre compte.</p>"
            "<p>Cordialement,</p>"
        ),
        "is_default": True,
    },
    {
        "id": "default_decompte_envoi",
        "name": "Envoi decompte annuel",
        "category": "information",
        "subject": "Votre decompte annuel de charges - {copropriete_name}",
        "body_html": (
            "<p>Bonjour {owner_name},</p>"
            "<p>Vous trouverez en piece jointe votre <b>decompte annuel de charges</b> "
            "pour la copropriete <b>{copropriete_name}</b>.</p>"
            "<p>Ce decompte est etabli conformement aux articles 3.87 et 3.90 du "
            "Code civil belge. Toute reclamation peut etre adressee par courrier "
            "recommande dans les 30 jours de la reception.</p>"
            "<p>N'hesitez pas a nous contacter pour toute question.</p>"
            "<p>Cordialement,</p>"
        ),
        "is_default": True,
    },
    {
        # iter93cy : template professionnel pour la situation de compte
        # trimestrielle / annuelle. Utilise les variables fiscal_year_*,
        # last_bank_statement_date et manager_phone.
        "id": "default_situation_compte",
        "name": "Situation de compte",
        "category": "information",
        "subject": "{copropriete_name} - Votre situation de compte au {today}",
        "body_html": (
            "<p>Madame, Monsieur <b>{owner_name}</b>,</p>"
            "<p>Vous trouverez en piece jointe votre <b>situation de compte</b> "
            "pour la copropriete <b>{copropriete_name}</b>, arretee au {today}.</p>"
            "<p>Cette situation reprend les elements suivants :</p>"
            "<ul>"
            "<li>Votre solde reporte au debut de l'exercice comptable (le <b>{fiscal_year_start}</b>).</li>"
            "<li>Vos appels de provisions pour charges trimestrielles.</li>"
            "<li>Votre eventuel appel de fonds de reserve decide lors de la derniere assemblee generale.</li>"
            "<li>Votre eventuel ajustement de fonds de roulement decide lors de la derniere assemblee generale.</li>"
            "<li>Vos paiements recus jusqu'au <b>{last_bank_statement_date}</b> "
            "(les paiements realises apres cette date seront comptabilises lors de la prochaine importation bancaire).</li>"
            "</ul>"
            "<p><b>Votre solde a ce jour : {abs_balance} EUR ({balance_status}).</b></p>"
            "<p>Si votre compte presente un solde debiteur, nous vous invitons a proceder au reglement "
            "au profit de la copropriete au moyen des elements suivants :</p>"
            "<ul>"
            "<li>Beneficiaire : <b>{copropriete_name}</b></li>"
            "<li>IBAN : <b>{iban}</b>{bic}</li>"
            "<li>Communication structuree : <b>{vcs_code}</b></li>"
            "</ul>"
            "<p>Nous vous remercions d'effectuer le versement dans les meilleurs delais, "
            "et au plus tard le 25 du mois en cours.</p>"
            "<p>En cas de solde crediteur, aucune action n'est requise de votre part. "
            "Les remboursements eventuels sont effectues uniquement sur demande, "
            "a l'issue de l'assemblee generale annuelle.</p>"
            "<p>Pour toute question relative a ce document, notre service de gestion "
            "reste a votre disposition :</p>"
            "<ul>"
            "<li>Gestionnaire : <b>{manager_name}</b></li>"
            "<li>Email : <a href=\"mailto:{manager_email}\">{manager_email}</a></li>"
            "<li>Telephone : <b>{manager_phone}</b></li>"
            "</ul>"
            "<p>Nous vous remercions pour votre confiance.</p>"
            "<p>Bien cordialement,<br>"
            "<b>{manager_name}</b><br>"
            "{syndic_name}</p>"
        ),
        "is_default": True,
    },
]


class TemplatePayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    category: str = Field(default="general", max_length=50)
    subject: str = Field(..., min_length=1, max_length=250)
    body_html: str = Field(..., min_length=1)
    order: Optional[int] = 0


def render_template(text: str, context: dict) -> str:
    """Substitue les variables {name} dans le texte. Les cles absentes sont
    remplacees par une chaine vide (silencieux, safe)."""
    if not text:
        return ""
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""
    try:
        return text.format_map(_SafeDict(**context))
    except Exception:
        # Si un accolade orpheline crash la substitution, on tolere
        return text


# iter93cy : detection HTML block-level pour auto-formatage.
# Si un template body_html est saisi en texte brut (aucune balise <p>, <br>,
# <div>, <ul>...), la reception via Outlook/Gmail affiche un pave illisible.
# On auto-enveloppe alors les lignes en paragraphes.
import re as _re
_HTML_BLOCK_RE = _re.compile(
    r"<\s*(p|div|br|ul|ol|li|table|tr|td|h[1-6]|blockquote|pre|hr)\b",
    _re.IGNORECASE,
)


def ensure_html_paragraphs(html_or_text: str) -> str:
    """Auto-format : si aucune balise block-level detectee, on convertit
    les sauts de ligne doubles en paragraphes et simples en <br>.
    Preserve les templates HTML existants sans modification.
    """
    if not html_or_text:
        return ""
    if _HTML_BLOCK_RE.search(html_or_text):
        return html_or_text  # Deja formate HTML -> ne pas toucher
    # Normalise les sauts de ligne, decoupe en paragraphes
    normalized = html_or_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    # Segments separes par ligne(s) vide(s)
    paragraphs = [p.strip() for p in _re.split(r"\n\s*\n", normalized) if p.strip()]
    if not paragraphs:
        # Une seule ligne : traite comme un paragraphe
        paragraphs = [normalized] if normalized else []
    html_parts = []
    for p in paragraphs:
        # Sauts de ligne simples -> <br>
        p_with_br = p.replace("\n", "<br>")
        html_parts.append(f"<p>{p_with_br}</p>")
    return "\n".join(html_parts)


def render_body_html(text: str, context: dict) -> str:
    """Rend un template body_html : substitution + auto-format paragraphes.
    A utiliser partout ou l'on genere le corps final d'un email."""
    rendered = render_template(text, context)
    return ensure_html_paragraphs(rendered)


async def get_template_by_id(db, syndic_user_id: str, template_id: str) -> Optional[dict]:
    """Retourne le template par ID (DB ou default)."""
    # 1. Custom template
    doc = await db.email_templates.find_one(
        {"syndic_user_id": syndic_user_id, "id": template_id},
        {"_id": 0},
    )
    if doc:
        return doc
    # 2. Default template
    for t in DEFAULT_TEMPLATES:
        if t["id"] == template_id:
            return dict(t)
    return None


def create_email_templates_router(db):
    router = APIRouter(prefix="/api")

    async def _require_syndic_scope(request: Request) -> str:
        """Retourne le syndic_user_id du cabinet du user courant."""
        return await _resolve_syndic_user_id(db, request)

    @router.get("/email-templates/variables")
    async def list_variables(request: Request):
        """Documentation des variables disponibles dans les templates."""
        await _require_syndic_scope(request)  # auth check
        return {"variables": SUPPORTED_VARIABLES}

    @router.get("/email-templates")
    async def list_templates(request: Request):
        """Liste les templates du cabinet + les defauts non ecrases."""
        syndic_uid = await _require_syndic_scope(request)
        customs = await db.email_templates.find(
            {"syndic_user_id": syndic_uid}, {"_id": 0}
        ).sort([("category", 1), ("order", 1), ("name", 1)]).to_list(1000)
        # On ajoute les defauts qui n'ont pas ete "shadowed" par un custom
        custom_default_ids = {t["id"] for t in customs if t.get("is_default")}
        result = list(customs)
        for t in DEFAULT_TEMPLATES:
            if t["id"] not in custom_default_ids:
                result.append({**t, "syndic_user_id": syndic_uid, "readonly": True})
        # Sort final
        result.sort(key=lambda x: (x.get("category", ""), x.get("name", "")))
        return {"templates": result, "variables": SUPPORTED_VARIABLES}

    @router.post("/email-templates")
    async def create_template(payload: TemplatePayload, request: Request):
        """Cree un nouveau template. Reserve aux syndics (pas gestionnaires)."""
        uid = request.state.user_id
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user or user.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Reserve aux syndics")
        syndic_uid = await _require_syndic_scope(request)
        doc = {
            "id": str(uuid.uuid4()),
            "syndic_user_id": syndic_uid,
            "is_default": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": uid,
            **payload.model_dump(),
        }
        await db.email_templates.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.put("/email-templates/{template_id}")
    async def update_template(template_id: str, payload: TemplatePayload, request: Request):
        """Met a jour un template custom. Pour un default, on cree un override
        (copy in DB avec meme id)."""
        uid = request.state.user_id
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user or user.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Reserve aux syndics")
        syndic_uid = await _require_syndic_scope(request)

        # Verifie si c'est un template custom existant
        existing = await db.email_templates.find_one(
            {"syndic_user_id": syndic_uid, "id": template_id}
        )
        if existing:
            await db.email_templates.update_one(
                {"_id": existing["_id"]},
                {"$set": {**payload.model_dump(),
                          "updated_at": datetime.now(timezone.utc).isoformat(),
                          "updated_by": uid}},
            )
            doc = await db.email_templates.find_one({"_id": existing["_id"]}, {"_id": 0})
            return doc

        # Si c'est un default (pas encore override) -> cree l'override
        is_default = any(t["id"] == template_id for t in DEFAULT_TEMPLATES)
        if is_default:
            doc = {
                "id": template_id,
                "syndic_user_id": syndic_uid,
                "is_default": True,  # marque comme override d'un default
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": uid,
                **payload.model_dump(),
            }
            await db.email_templates.insert_one(doc)
            doc.pop("_id", None)
            return doc

        raise HTTPException(404, "Template non trouve")

    @router.delete("/email-templates/{template_id}")
    async def delete_template(template_id: str, request: Request):
        """Supprime un template custom. Pour un default : reset (supprime
        l'override et le default reapparait)."""
        uid = request.state.user_id
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user or user.get("role") not in ("syndic", "admin", "superadmin"):
            raise HTTPException(403, "Reserve aux syndics")
        syndic_uid = await _require_syndic_scope(request)
        result = await db.email_templates.delete_one(
            {"syndic_user_id": syndic_uid, "id": template_id}
        )
        if result.deleted_count == 0:
            # Si c'est un default : deja "supprimable" (ne s'affiche pas si on considere qu'un default retire est masque)
            is_default = any(t["id"] == template_id for t in DEFAULT_TEMPLATES)
            if is_default:
                return {"success": True, "note": "Default templates cannot be permanently deleted"}
            raise HTTPException(404, "Template non trouve")
        return {"success": True}

    @router.post("/email-templates/{template_id}/preview")
    async def preview_template(template_id: str, request: Request):
        """Rend le template avec des variables de demonstration."""
        syndic_uid = await _require_syndic_scope(request)
        tpl = await get_template_by_id(db, syndic_uid, template_id)
        if not tpl:
            raise HTTPException(404, "Template non trouve")
        demo_ctx = {
            "owner_name": "Marie DEVOS",
            "owner_email": "marie.devos@example.be",
            "balance": "247,50 €",
            "abs_balance": "247,50 €",
            "balance_status": "debiteur",
            "vcs_code": "+++123/4567/89012+++",
            "copropriete_name": "Residence Les Peupliers",
            "copropriete_address": "Rue Test 12, 1000 Bruxelles",
            "syndic_name": "Cabinet Syndic Demo",
            "manager_name": "Jean DUPONT",
            "manager_email": "jean.dupont@cabinet.be",
            "manager_phone": "+32 475 12 34 56",
            "today": datetime.now().strftime("%d/%m/%Y"),
            "iban": "BE68 5390 0754 7034",
            "bic": "BBRUBEBB",
            # iter93cx : valeurs demo pour les reperes temporels
            "fiscal_year_start": "01/04/2026",
            "fiscal_year_end": "31/03/2027",
            "fiscal_year_label": "Exercice 2026-2027",
            "last_bank_statement_date": datetime.now().strftime("%d/%m/%Y"),
        }
        return {
            "subject": render_template(tpl.get("subject", ""), demo_ctx),
            "body_html": render_body_html(tpl.get("body_html", ""), demo_ctx),
            "variables_used": [k for k in demo_ctx if "{" + k + "}" in (tpl.get("subject", "") + tpl.get("body_html", ""))],
        }

    return router


# ---- Helper pour resolve le contexte lors d'un envoi ----
async def build_owner_email_context(db, owner_id: str, copropriete_id: str, current_user: dict = None) -> dict:
    """Construit le dict de substitution pour render_template lors d'un envoi
    concret. Utilise par communication.py."""
    owner = await db.owners.find_one({"id": owner_id}, {"_id": 0}) or {}
    copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0}) or {}
    iban = ""
    bic = ""
    for ba in (copro.get("bank_accounts") or []):
        if ba.get("iban"):
            iban = ba["iban"]
            bic = ba.get("bic", "")
            break
    # Solde : simple approx a partir de tier_accounts
    balance = 0.0  # sera injecte par le caller si connu
    # iter93ac : format espace millier + virgule decimale pour emails
    from utils.format import fmt_eur as _fmt_eur

    # iter93cx : exercice comptable en cours (statut 'open') de l'ACP.
    # Fallback : le plus recent par date de debut.
    def _fmt_date_fr(iso_or_date: str) -> str:
        if not iso_or_date:
            return ""
        s = str(iso_or_date)[:10]  # YYYY-MM-DD
        try:
            y, m, d = s.split("-")
            return f"{d}/{m}/{y}"
        except Exception:
            return s

    fy_start = fy_end = fy_label = ""
    if copropriete_id:
        fy = await db.fiscal_years.find_one(
            {"copropriete_id": copropriete_id, "status": "open"},
            {"_id": 0, "name": 1, "start_date": 1, "end_date": 1},
            sort=[("start_date", -1)],
        )
        if not fy:
            fy = await db.fiscal_years.find_one(
                {"copropriete_id": copropriete_id},
                {"_id": 0, "name": 1, "start_date": 1, "end_date": 1},
                sort=[("start_date", -1)],
            )
        if fy:
            fy_start = _fmt_date_fr(fy.get("start_date", ""))
            fy_end = _fmt_date_fr(fy.get("end_date", ""))
            fy_label = fy.get("name", "") or ""

    # iter93cx : dernier extrait bancaire comptabilise (statut 'posted').
    # Fallback : dernier extrait tous statuts confondus.
    last_stmt_date = ""
    if copropriete_id:
        last_stmt = await db.bank_statements.find_one(
            {"copropriete_id": copropriete_id, "status": "posted"},
            {"_id": 0, "date": 1},
            sort=[("date", -1), ("created_at", -1)],
        )
        if not last_stmt:
            last_stmt = await db.bank_statements.find_one(
                {"copropriete_id": copropriete_id},
                {"_id": 0, "date": 1},
                sort=[("date", -1), ("created_at", -1)],
            )
        if last_stmt:
            last_stmt_date = _fmt_date_fr(last_stmt.get("date", ""))

    # iter93cx : manager_phone -> priorite user.phone, fallback syndic_configs.phone
    manager_phone = ""
    if current_user and current_user.get("phone"):
        manager_phone = str(current_user.get("phone", "")).strip()
    if not manager_phone:
        try:
            # Retrouver la config du syndic parent du user connecte
            syndic_uid = None
            if current_user:
                syndic_uid = current_user.get("parent_syndic_id") or (
                    str(current_user.get("_id", "")) if current_user.get("role") == "syndic" else None
                )
            if syndic_uid:
                sc = await db.syndic_configs.find_one(
                    {"syndic_user_id": syndic_uid}, {"_id": 0, "phone": 1}
                )
                if sc and sc.get("phone"):
                    manager_phone = str(sc["phone"]).strip()
        except Exception:
            pass

    return {
        "owner_name": owner.get("name", ""),
        "owner_email": owner.get("email", ""),
        "balance": _fmt_eur(balance, with_suffix=False) + " €",
        "abs_balance": _fmt_eur(abs(balance), with_suffix=False) + " €",
        "balance_status": "debiteur" if balance > 0 else ("crediteur" if balance < 0 else "solde"),
        "vcs_code": owner.get("vcs_code", ""),
        "copropriete_name": copro.get("name", ""),
        "copropriete_address": copro.get("address", ""),
        "syndic_name": copro.get("syndic_name", ""),
        "manager_name": (current_user or {}).get("name", ""),
        "manager_email": (current_user or {}).get("email", ""),
        "manager_phone": manager_phone,
        "today": datetime.now().strftime("%d/%m/%Y"),
        "iban": iban,
        "bic": bic,
        # iter93cx : reperes temporels comptables
        "fiscal_year_start": fy_start,
        "fiscal_year_end": fy_end,
        "fiscal_year_label": fy_label,
        "last_bank_statement_date": last_stmt_date,
    }
