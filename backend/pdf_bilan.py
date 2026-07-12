"""PDF Bilan comptable par exercice - structure type 'Bilan apres repartition'.

Format inspire des bilans de syndic belge :
- En-tete avec immeuble + syndic + date d'edition
- 2 colonnes : ACTIF (gauche) | PASSIF (droite)
- Detail par compte avec libelle + montant
- Totaux en pied de chaque colonne (equilibre)
"""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    PageBreak,
)


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
HEADER_BG = colors.HexColor("#1E40AF")
RUBRIQUE_BG = colors.HexColor("#E0E7FF")


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",")


def _fmt_date(s):
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def _column_table(rubriques, side_label, total):
    """Construit le tableau ACTIF ou PASSIF detaille - layout aere."""
    # Filtre les rubriques vides (total = 0) pour alleger l'affichage
    rubriques = [r for r in rubriques if r.get("total", 0) > 0.005]
    rows = []
    # Header de la colonne
    rows.append([
        Paragraph(f"<b>{side_label}</b>", _hdr_style()),
        Paragraph("<b>EUR</b>", _hdr_right()),
    ])
    for r in rubriques:
        # Ligne rubrique
        rows.append([
            Paragraph(f"<b>{r['label']}</b>", _rub_style()),
            Paragraph(f"<b>{_fmt_eur(r['total'])}</b>", _rub_right()),
        ])
        # Detail des comptes
        for a in r.get("accounts", []):
            num = a.get("account_number", "") or ""
            name = a.get("account_name", "") or ""
            amt = a.get("amount", 0)
            # Si pas de numero (compte agrege owner), afficher juste le nom
            if num:
                label_html = (
                    f"<font color='#94A3B8' size='7' name='Courier'>{num}</font> "
                    f"<font size='8.5'>{name}</font>"
                )
            else:
                label_html = f"<font size='8.5'>{name}</font>"
            rows.append([
                Paragraph(label_html, _acc_style()),
                Paragraph(_fmt_eur(amt), _acc_right()),
            ])
    # TOTAL
    rows.append([
        Paragraph(f"<b>TOTAL {side_label.upper()}</b>", _tot_style()),
        Paragraph(f"<b>{_fmt_eur(total)}</b>", _tot_right()),
    ])
    tbl = Table(rows, colWidths=[58 * mm, 32 * mm])
    style = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        # Total
        ("BACKGROUND", (0, -1), (-1, -1), HEADER_BG),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("LINEABOVE", (0, -1), (-1, -1), 1, SLATE_900),
        ("TOPPADDING", (0, -1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
        # Grille legere
        ("BOX", (0, 0), (-1, -1), 0.4, SLATE_300),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, SLATE_100),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 1), (-1, -2), 1),
        ("BOTTOMPADDING", (0, 1), (-1, -2), 1),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]
    # Rubrique rows (label gras) - identifier les indexes de rubriques
    idx = 1
    for r in rubriques:
        style.append(("BACKGROUND", (0, idx), (-1, idx), RUBRIQUE_BG))
        style.append(("TOPPADDING", (0, idx), (-1, idx), 3))
        style.append(("BOTTOMPADDING", (0, idx), (-1, idx), 3))
        idx += 1 + len(r.get("accounts", []))
    tbl.setStyle(TableStyle(style))
    return tbl


def _hdr_style():
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("hdr", parent=s, fontSize=10, textColor=colors.white,
                          alignment=0, leading=12)


def _hdr_right():
    return ParagraphStyle("hdrR", parent=_hdr_style(), alignment=2)


def _rub_style():
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("rub", parent=s, fontSize=9.5, textColor=SLATE_900,
                          fontName="Helvetica-Bold", leading=12)


def _rub_right():
    return ParagraphStyle("rubR", parent=_rub_style(), alignment=2)


def _acc_style():
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("acc", parent=s, fontSize=8.5, textColor=SLATE_700,
                          leading=11)


def _acc_right():
    return ParagraphStyle("accR", parent=_acc_style(), alignment=2,
                          fontName="Helvetica")


def _tot_style():
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("tot", parent=s, fontSize=10, textColor=colors.white,
                          fontName="Helvetica-Bold", leading=13)


def _tot_right():
    return ParagraphStyle("totR", parent=_tot_style(), alignment=2)


def build_bilan_pdf(
    *,
    copropriete: dict,
    syndic: dict = None,
    fiscal_year: dict = None,
    bilan_data: dict,
    date_to: str = "",
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Genere le PDF Bilan apres repartition.

    iter90dj : si `syndic_pdf_ctx` est fourni, ajoute logo cabinet en tete
    (1re page uniquement) + pied de page avec mentions legales + numero de
    page sur toutes les pages.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=28 * mm if use_new_layout else 14 * mm,
        title=f"Bilan - {copropriete.get('name','')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=14, leading=18, textColor=colors.white,
        alignment=1, fontName="Helvetica-Bold",
    )
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8.5, leading=11, textColor=SLATE_500)
    elems = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elems.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            small,
        ))
        elems.append(Spacer(1, 4 * mm))

    # ---- BANDEAU TITRE COLORE ----
    end_str = _fmt_date(date_to or (fiscal_year or {}).get("end_date", ""))
    fy_name = (fiscal_year or {}).get("name", "")
    title_text = "BILAN COMPTABLE APRES REPARTITION"
    if end_str:
        title_text += f" AU {end_str}"
    title_tbl = Table(
        [[Paragraph(title_text, title_style)],
         [Paragraph(f"<font color='#E0E7FF' size='9'>FAIT LE {datetime.now().strftime('%d/%m/%Y')}"
                    + (f" - Exercice {fy_name}" if fy_name else "")
                    + "</font>", title_style)]],
        colWidths=[186 * mm],
    )
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HEADER_BG),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elems.append(title_tbl)
    elems.append(Spacer(1, 4 * mm))

    # ---- HEADER INFO ACP + SYNDIC ----
    acp_block = (
        f"<b>IMMEUBLE : {copropriete.get('name','')}</b><br/>"
        f"{copropriete.get('address','')}<br/>"
        f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
        + (f"BCE : {copropriete.get('bce','')}<br/>" if copropriete.get('bce') else "")
    )
    syndic_block = ""
    if syndic:
        syndic_block = (
            f"<b>{syndic.get('name','')}</b><br/>"
            f"{syndic.get('address','')}<br/>"
            f"{syndic.get('postal_code','')} {syndic.get('city','')}<br/>"
            + (f"Tel. {syndic.get('phone','')}<br/>" if syndic.get('phone') else "")
            + (f"{syndic.get('email','')}<br/>" if syndic.get('email') else "")
            + (f"<font size='7' color='#64748B'>Num. IPI : {syndic.get('ipi','')}</font>"
               if syndic.get('ipi') else "")
        )
    header_tbl = Table(
        [[Paragraph(acp_block, small), Paragraph(syndic_block, small)]],
        colWidths=[93 * mm, 93 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 6 * mm))

    # ---- TABLES ACTIF + PASSIF (cote a cote) ----
    actif_tbl = _column_table(
        bilan_data.get("actif", []),
        "ACTIF",
        bilan_data.get("total_actif", 0),
    )
    passif_tbl = _column_table(
        bilan_data.get("passif", []),
        "PASSIF",
        bilan_data.get("total_passif", 0),
    )
    side_by_side = Table(
        [[actif_tbl, passif_tbl]],
        colWidths=[93 * mm, 93 * mm],
    )
    side_by_side.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(side_by_side)

    # ---- EQUILIBRE ----
    elems.append(Spacer(1, 4 * mm))
    eq = bilan_data.get("equilibre", False)
    ecart = bilan_data.get("ecart", 0)
    if eq:
        msg = (
            f"<font color='#16A34A' size='9'><b>BILAN EQUILIBRE</b></font> - "
            f"Total Actif = Total Passif = <b>{_fmt_eur(bilan_data.get('total_actif',0))} EUR</b>"
        )
    else:
        msg = (
            f"<font color='#DC2626' size='9'><b>BILAN DESEQUILIBRE</b></font> - "
            f"Ecart = <b>{_fmt_eur(ecart)} EUR</b> - "
            "Verifier les ecritures du grand livre."
        )
    eq_tbl = Table([[Paragraph(msg, small)]], colWidths=[186 * mm])
    eq_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1),
         colors.HexColor("#F0FDF4") if eq else colors.HexColor("#FEF3F2")),
        ("BOX", (0, 0), (-1, -1), 0.6,
         colors.HexColor("#16A34A") if eq else colors.HexColor("#DC2626")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(eq_tbl)

    # ---- FOOTER ----
    elems.append(Spacer(1, 6 * mm))
    elems.append(Paragraph(
        f"<font size='7' color='#94A3B8'><i>Bilan etabli au {end_str or 'date du jour'} - "
        f"Document interne genere par CoproManager le {datetime.now().strftime('%d/%m/%Y a %H:%M')}.</i></font>",
        small,
    ))

    if footer_cb:
        doc.build(elems, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elems)
    return buf.getvalue()
