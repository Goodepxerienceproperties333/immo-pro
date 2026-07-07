"""iter90av : Helpers de mise en page PDF partages entre les generateurs
(situation compte, decompte annuel, mutation).

Objectifs :
1. Logo cabinet syndic en haut-a-gauche (max 40mm de large).
2. Bloc adresse destinataire positionne pour rentrer dans la fenetre droite
   d'une enveloppe C5/6 pliage triple (standard belge/EU).
3. Pied de page avec mentions legales du cabinet (agrement IPI, TVA, RGPD...).

Positionnement enveloppe :
- Format A4 = 210 x 297 mm
- Enveloppe C5/6 (110 x 220 mm) pliage triple = 3 sections de 99 mm de haut
- Fenetre DROITE standard : x = 110-198 mm depuis gauche, y = 47-92 mm depuis haut
  (donc dans la section haute qui apparait plie en 3)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle


# ---- Coordonnees fenetre C6 droite (mm depuis coin haut-gauche A4) ----
C6_WINDOW_LEFT = 110  # colonne de gauche du bloc adresse
C6_WINDOW_TOP = 45    # y du haut du bloc (~ 45mm depuis le haut A4)
C6_WINDOW_WIDTH = 85  # largeur du bloc
C6_WINDOW_HEIGHT = 35 # hauteur du bloc


def _load_logo_flowable(db, syndic_user_id: str, max_width_mm: float = 40, max_height_mm: float = 22) -> Optional[Image]:
    """Charge le logo du syndic depuis GridFS et retourne un Flowable Image.
    Retourne None si aucun logo configure.

    NOTE : appelle depuis async n'est PAS possible ici (reportlab veut du sync).
    Utiliser la version pre-fetch : passer directement les `logo_bytes`.
    """
    return None  # implementation via fetch_logo_bytes() ci-dessous


def load_logo_image(logo_bytes: Optional[bytes], max_width_mm: float = 40, max_height_mm: float = 22) -> Optional[Image]:
    """Construit un Flowable Image a partir des bytes du logo. None si vide."""
    if not logo_bytes:
        return None
    from io import BytesIO
    from reportlab.lib.utils import ImageReader
    try:
        reader = ImageReader(BytesIO(logo_bytes))
        w, h = reader.getSize()
        # Redimensionne en preservant le ratio, contrainte par max_w et max_h
        ratio = w / h if h else 1.0
        max_w_pt = max_width_mm * mm
        max_h_pt = max_height_mm * mm
        target_w = min(max_w_pt, max_h_pt * ratio)
        target_h = target_w / ratio if ratio else max_h_pt
        if target_h > max_h_pt:
            target_h = max_h_pt
            target_w = target_h * ratio
        img = Image(BytesIO(logo_bytes), width=target_w, height=target_h)
        return img
    except Exception:
        return None


async def fetch_logo_bytes(db, syndic_user_id: str) -> Optional[bytes]:
    """Recupere les bytes du logo depuis GridFS pour ce syndic. None si absent."""
    if not syndic_user_id:
        return None
    cfg = await db.syndic_configs.find_one({"syndic_user_id": syndic_user_id})
    if not cfg or not cfg.get("logo_gridfs_id"):
        return None
    try:
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="syndic_logos")
        return await storage.download(cfg["logo_gridfs_id"])
    except Exception:
        return None


async def resolve_syndic_pdf_context(db, copropriete: dict) -> dict:
    """Retourne le contexte PDF pour l'ACP donnee :
    {syndic_user_id, syndic_config, logo_bytes, legal_mentions}.

    Priorite pour retrouver le syndic proprietaire de l'ACP :
    1. copropriete.syndic_user_id (nouveau champ explicite)
    2. copropriete.created_by -> user.role=syndic ou parent_syndic_id du createur
    3. Recherche un syndic dont copropriete_ids contient l'id de l'ACP
    """
    from bson import ObjectId
    syndic_user_id = (copropriete or {}).get("syndic_user_id", "") or ""
    if not syndic_user_id:
        created_by = (copropriete or {}).get("created_by", "")
        if created_by:
            try:
                creator = await db.users.find_one({"_id": ObjectId(str(created_by))})
                if creator:
                    if creator.get("role") == "syndic":
                        syndic_user_id = str(creator["_id"])
                    elif creator.get("parent_syndic_id"):
                        syndic_user_id = str(creator["parent_syndic_id"])
            except Exception:
                pass
    if not syndic_user_id and copropriete and copropriete.get("id"):
        # Fallback : chercher un syndic qui a cette ACP dans copropriete_ids
        try:
            u = await db.users.find_one(
                {"role": "syndic", "copropriete_ids": copropriete["id"]},
                {"_id": 1},
            )
            if u:
                syndic_user_id = str(u["_id"])
        except Exception:
            pass

    syndic_config = {}
    logo_bytes = None
    if syndic_user_id:
        cfg = await db.syndic_configs.find_one({"syndic_user_id": syndic_user_id})
        if cfg:
            syndic_config = cfg
            logo_bytes = await fetch_logo_bytes(db, syndic_user_id)
    return {
        "syndic_user_id": syndic_user_id,
        "syndic_config": syndic_config,
        "logo_bytes": logo_bytes,
        "legal_mentions": syndic_config.get("legal_mentions", "") if syndic_config else "",
    }


def build_recipient_address_flowable(recipient: dict, small_style) -> Table:
    """Construit un flowable Table positionne pour la fenetre C6 droite.

    IMPORTANT : ce bloc doit etre le premier a etre place APRES le logo header,
    et sa position verticale controlee via un Spacer pour tomber a y=45mm.

    `recipient` = {name, address, postal_code, city, country, email?, vcs_code?}
    """
    lines = [f"<b>{recipient.get('name', '')}</b>"]
    for candidate in [
        recipient.get("address", ""),
        f"{recipient.get('postal_code', '')} {recipient.get('city', '')}".strip(),
        recipient.get("country", ""),
    ]:
        if candidate and candidate.strip():
            lines.append(candidate)
    addr_html = "<br/>".join(lines)

    # Placement horizontal : un Table 2 colonnes avec la gauche vide (espace
    # pour fenetre) et la droite avec l'adresse. La gauche fait 105mm,
    # la droite 75mm - total 180mm (A4 utile).
    p = Paragraph(addr_html, small_style)
    tbl = Table(
        [["", p]],
        colWidths=[105 * mm, 75 * mm],
    )
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (1, 0), (1, 0), 4),
        ("BOTTOMPADDING", (1, 0), (1, 0), 4),
    ]))
    return tbl


def build_header_with_logo(logo_bytes: Optional[bytes], cabinet_info: dict, small_style) -> Table:
    """Construit le header PDF avec logo a gauche + infos cabinet.

    `cabinet_info` = syndic_config dict (legal_name, address, city, phone, email, bce, ipi_number).
    """
    logo_flow = load_logo_image(logo_bytes)
    # Bloc texte cabinet a droite du logo
    name = cabinet_info.get("legal_name") or cabinet_info.get("display_name") or "Syndic"
    lines = [f"<b>{name}</b>"]
    addr = cabinet_info.get("address", "")
    if addr:
        lines.append(addr)
    ville_line = f"{cabinet_info.get('postal_code','')} {cabinet_info.get('city','')}".strip()
    if ville_line:
        lines.append(ville_line)
    contact = " · ".join(x for x in [cabinet_info.get("email", ""), cabinet_info.get("phone", "")] if x)
    if contact:
        lines.append(contact)
    ipi = cabinet_info.get("ipi_number", "")
    if ipi:
        lines.append(f"Agrement IPI : {ipi}")

    cabinet_para = Paragraph("<br/>".join(lines), small_style)
    left_cell = logo_flow if logo_flow else Paragraph("", small_style)

    tbl = Table(
        [[left_cell, cabinet_para]],
        colWidths=[50 * mm, 130 * mm],
        rowHeights=[24 * mm],
    )
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def draw_legal_footer(canvas_obj, doc, legal_mentions: str):
    """Callback onPage pour dessiner le pied de page avec mentions legales.
    Utilise via `SimpleDocTemplate(onFirstPage=..., onLaterPages=...)`."""
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(colors.HexColor("#94A3B8"))
    # Mentions legales sur plusieurs lignes
    if legal_mentions:
        # Wrap manuel : split par ligne si necessaire
        lines = legal_mentions.split("\n")
        y = 10 * mm
        for line in reversed(lines[:3]):  # max 3 lignes
            canvas_obj.drawCentredString(105 * mm, y, line.strip()[:140])
            y += 3 * mm
    # Numero de page a droite
    canvas_obj.drawRightString(200 * mm, 8 * mm, f"Page {doc.page}")
    # Trait separateur
    canvas_obj.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas_obj.setLineWidth(0.4)
    canvas_obj.line(15 * mm, 22 * mm, 195 * mm, 22 * mm)
    canvas_obj.restoreState()


def make_footer_callback(legal_mentions: str):
    """Retourne un callable pour `onFirstPage`/`onLaterPages`. Permet capture
    du texte legal via closure."""
    def _cb(canvas_obj, doc):
        draw_legal_footer(canvas_obj, doc, legal_mentions or "")
    return _cb


def spacer_to_c6_position(current_y_pt: float) -> Spacer:
    """Retourne un Spacer permettant de positionner le NEXT flowable pour que
    son haut soit a y=45mm depuis le haut de la page A4 (position fenetre C6).

    A4 = 297mm haut. topMargin par defaut 15mm.
    On veut que apres header (24mm) + spacer, le prochain flow demarre a y_top_pt.

    Return None si on ne peut pas calculer precisement -> le caller utilise
    un Spacer fixe.
    """
    return Spacer(1, 4 * mm)
