"""PDF Budget previsionnel - synthese A4 portrait.

Structure du document :
- En-tete : nom ACP + reference + statut budget + FY
- Bloc metadonnees : nom du budget + total previsionnel + engagement AG
  (fonds de reserve, fonds de roulement) + date approbation + approuve par
- Tableau des lignes budgetaires : Compte / Nature / Cle / Montant HTVA
- Total general
- (iter90cj) Section separee "Engagement AG - Fonds permanents" si des
  montants reserve/roulement sont fixes.
- Footer : date generation + reference ACP
"""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
BLUE_BG = colors.HexColor("#EFF6FF")
BLUE = colors.HexColor("#2563EB")
PURPLE = colors.HexColor("#7C3AED")
PURPLE_BG = colors.HexColor("#F5F3FF")
EMERALD = colors.HexColor("#059669")
EMERALD_BG = colors.HexColor("#ECFDF5")
AMBER = colors.HexColor("#D97706")
AMBER_BG = colors.HexColor("#FEF3C7")


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",") + " EUR"


def _fmt_date(s):
    if not s:
        return ""
    try:
        if "T" in s:
            s = s.split("T")[0]
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def build_budget_pdf(
    *,
    copropriete: dict,
    budget: dict,
    fiscal_year: dict,
    pcmn_map: dict,
    keys_map: dict,
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """iter90dj : `syndic_pdf_ctx` (optionnel) ajoute logo cabinet + pied de
    page legal avec numeros de page sur toutes les pages."""
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx)  # iter90hm : nouveau layout TOUJOURS actif si contexte fourni
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=28 * mm if use_new_layout else 14 * mm,
        title=f"Budget - {budget.get('name', '')}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=18, leading=22, textColor=BRAND,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=10, leading=13, textColor=SLATE_500, spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontSize=12, leading=15, textColor=SLATE_900,
        spaceAfter=6, fontName="Helvetica-Bold",
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        fontSize=9.5, leading=12, textColor=SLATE_900,
    )
    body_small = ParagraphStyle(
        "small", parent=styles["BodyText"],
        fontSize=8.5, leading=11, textColor=SLATE_500,
    )

    story = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        story.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            body_small,
        ))
        story.append(Spacer(1, 4 * mm))

    # ---- En-tete ----
    story.append(Paragraph(
        f"Budget previsionnel &mdash; {copropriete.get('name', '')}",
        title,
    ))
    ref = copropriete.get("reference", "") or copropriete.get("code", "")
    fy_name = fiscal_year.get("name", "") if fiscal_year else ""
    fy_period = ""
    if fiscal_year:
        fy_period = f"{_fmt_date(fiscal_year.get('start_date',''))} au {_fmt_date(fiscal_year.get('end_date',''))}"
    story.append(Paragraph(
        f"Reference ACP : <b>{ref or 'N/A'}</b> &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Exercice : <b>{fy_name}</b> ({fy_period})",
        sub,
    ))

    # ---- Meta budget ----
    status = budget.get("status", "draft")
    status_label = {"draft": "Brouillon", "approved": "Vote"}.get(status, status)
    status_bg = AMBER_BG if status == "draft" else EMERALD_BG
    status_fg = AMBER if status == "draft" else EMERALD
    approved_at = _fmt_date(budget.get("approved_at", ""))
    approved_by = budget.get("approved_by", "")
    total_amount = float(
        budget.get("total_amount") or budget.get("total") or 0
    )

    meta_data = [
        [
            Paragraph("<b>Nom du budget</b>", body_small),
            Paragraph("<b>Statut</b>", body_small),
            Paragraph("<b>Total previsionnel</b>", body_small),
            Paragraph("<b>Date de vote</b>", body_small),
        ],
        [
            Paragraph(budget.get("name", ""), body),
            Paragraph(f"<font color='#0F172A'><b>{status_label}</b></font>", body),
            Paragraph(f"<b>{_fmt_eur(total_amount)}</b>", body),
            Paragraph(approved_at or "&mdash;", body),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[55 * mm, 30 * mm, 45 * mm, 40 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_50),
        ("BACKGROUND", (1, 1), (1, 1), status_bg),
        ("TEXTCOLOR", (1, 1), (1, 1), status_fg),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, SLATE_300),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)
    if approved_by:
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            f"Approuve par : <b>{approved_by}</b>", body_small,
        ))

    story.append(Spacer(1, 10))

    # ---- Tableau des lignes budgetaires ----
    story.append(Paragraph("Detail des postes budgetaires", h2))

    header = [
        Paragraph("<b>Compte</b>", body_small),
        Paragraph("<b>Nature de depense</b>", body_small),
        Paragraph("<b>Cle de repartition</b>", body_small),
        Paragraph("<b>Montant HTVA</b>", body_small),
    ]
    lines = budget.get("lines") or []
    rows = [header]
    total = 0.0
    for ln in lines:
        acct = ln.get("account_number", "") or ""
        acct_name = ln.get("account_name") or pcmn_map.get(acct, "") or ""
        amt = float(ln.get("amount", 0) or 0)
        total += amt
        key_id = ln.get("distribution_key_id", "") or ""
        key_name = keys_map.get(key_id, "Cle par defaut" if not key_id else key_id)
        rows.append([
            Paragraph(f"<b>{acct}</b>", body),
            Paragraph(acct_name, body),
            Paragraph(key_name, body_small),
            Paragraph(f"<para align='right'>{_fmt_eur(amt)}</para>", body),
        ])
    rows.append([
        "", "",
        Paragraph("<b>TOTAL POSTES</b>", body),
        Paragraph(f"<para align='right'><b>{_fmt_eur(total)}</b></para>", body),
    ])

    tbl = Table(rows, colWidths=[22 * mm, 78 * mm, 45 * mm, 35 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
        ("TEXTCOLOR", (0, 0), (-1, 0), SLATE_900),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("INNERGRID", (0, 0), (-1, -2), 0.25, SLATE_300),
        ("LINEABOVE", (0, -1), (-1, -1), 1, SLATE_900),
        ("BACKGROUND", (0, -1), (-1, -1), SLATE_50),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)

    # ---- Section "Engagement AG - Fonds permanents" ----
    reserve_amt = float(budget.get("reserve_fund_amount", 0) or 0)
    roulement_amt = float(budget.get("roulement_fund_amount", 0) or 0)
    if reserve_amt > 0 or roulement_amt > 0:
        story.append(Spacer(1, 12))
        story.append(Paragraph(
            "Engagement AG &mdash; Fonds permanents (capital classe 1)", h2,
        ))
        story.append(Paragraph(
            "Montants engages par l'Assemblee Generale. Ces fonds alimentent "
            "directement le compte capital de la copropriete et sont "
            "transferables lors des mutations (art. 3.86 CDE).",
            body_small,
        ))
        story.append(Spacer(1, 4))

        eng_rows = [[
            Paragraph("<b>Nature</b>", body_small),
            Paragraph("<b>Cle de repartition</b>", body_small),
            Paragraph("<b>Montant</b>", body_small),
        ]]
        if reserve_amt > 0:
            key_id = budget.get("reserve_fund_key_id", "") or ""
            key_name = keys_map.get(key_id, "Cle par defaut" if not key_id else key_id)
            eng_rows.append([
                Paragraph("<font color='#7C3AED'><b>Fonds de reserve</b></font><br/>"
                          "<font size='8' color='#64748B'>Gros travaux futurs (compte 160)</font>", body),
                Paragraph(key_name, body_small),
                Paragraph(f"<para align='right'><b>{_fmt_eur(reserve_amt)}</b></para>", body),
            ])
        if roulement_amt > 0:
            key_id = budget.get("roulement_fund_key_id", "") or ""
            key_name = keys_map.get(key_id, "Cle par defaut" if not key_id else key_id)
            eng_rows.append([
                Paragraph("<font color='#059669'><b>Fonds de roulement</b></font><br/>"
                          "<font size='8' color='#64748B'>Tresorerie permanente (compte 100)</font>", body),
                Paragraph(key_name, body_small),
                Paragraph(f"<para align='right'><b>{_fmt_eur(roulement_amt)}</b></para>", body),
            ])
        eng_rows.append([
            "",
            Paragraph("<b>TOTAL ENGAGE</b>", body),
            Paragraph(f"<para align='right'><b>{_fmt_eur(reserve_amt + roulement_amt)}</b></para>", body),
        ])
        eng = Table(eng_rows, colWidths=[85 * mm, 55 * mm, 40 * mm], repeatRows=1)
        eng.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
            ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
            ("INNERGRID", (0, 0), (-1, -2), 0.25, SLATE_300),
            ("LINEABOVE", (0, -1), (-1, -1), 1, SLATE_900),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_50),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(eng)

    # ---- Grand total ----
    grand_total = total + reserve_amt + roulement_amt
    if reserve_amt > 0 or roulement_amt > 0:
        story.append(Spacer(1, 8))
        gt = Table([[
            Paragraph("<b>TOTAL GENERAL BUDGET (postes + engagements permanents)</b>", body),
            Paragraph(f"<para align='right'><b>{_fmt_eur(grand_total)}</b></para>", body),
        ]], colWidths=[140 * mm, 40 * mm])
        gt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
            ("BOX", (0, 0), (-1, 0), 1, BLUE),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, 0), 6),
            ("RIGHTPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ]))
        story.append(gt)

    # ---- Footer ----
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"<font size='7.5' color='#94A3B8'>Document genere le "
        f"{datetime.now().strftime('%d/%m/%Y a %H:%M')} par NextGe Copro. "
        f"Reference : Budget {budget.get('id', '')[:8]} &mdash; "
        f"ACP {ref or 'N/A'}.</font>",
        body_small,
    ))

    if footer_cb:
        doc.build(story, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(story)
    buf.seek(0)
    return buf.read()
