"""PDF Situation de compte (releve detaille proprietaire).
Genere un PDF professionnel envoyable par email ou postal :
- Entete syndic + ACP
- Identite + adresse proprietaire + VCS
- Periode
- Mouvements detailles (date, libelle, ref, debit, credit, solde progressif)
- Solde final + IBAN de paiement
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


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " EUR"


def _addr_block(name, parts):
    """Build a multi-line address block as one Paragraph."""
    lines = [name] + [p for p in parts if p]
    return "<br/>".join(lines)


def build_situation_compte_pdf(
    *,
    syndic_info: dict,
    copropriete: dict,
    owner: dict,
    movements: list,
    period_start: str = "",
    period_end: str = "",
    opening_balance: float = 0.0,
    iban: str = "",
    bic: str = "",
) -> bytes:
    """Returns PDF bytes.

    movements: list of dicts {date, description, reference, account_number,
                              account_name, debit, credit, journal_type}
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Situation de compte - {owner.get('name','')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=16, leading=20, textColor=colors.HexColor("#0055FF"),
        spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, leading=14, spaceAfter=2)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    right = ParagraphStyle("right", parent=body, alignment=2)
    bold_right = ParagraphStyle("bright", parent=right, fontName="Helvetica-Bold")

    elems = []

    # ---- HEADER : syndic / acp ----
    syndic_lines = _addr_block(
        syndic_info.get("name", "Syndic"),
        [
            syndic_info.get("address", ""),
            f"{syndic_info.get('postal_code','')} {syndic_info.get('city','')}".strip(),
            syndic_info.get("country", ""),
            syndic_info.get("email", ""),
            syndic_info.get("phone", ""),
            f"BCE/IPI : {syndic_info.get('bce','')}" if syndic_info.get('bce') else "",
        ],
    )
    acp_lines = _addr_block(
        copropriete.get("name", ""),
        [
            copropriete.get("address", ""),
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}".strip(),
            f"BCE : {copropriete.get('bce','')}" if copropriete.get("bce") else "",
        ],
    )
    header_tbl = Table(
        [[Paragraph(syndic_lines, small), Paragraph(acp_lines, small)]],
        colWidths=[90 * mm, 90 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 6 * mm))

    # ---- TITLE ----
    elems.append(Paragraph("Situation de compte", title_style))
    period_str = ""
    if period_start and period_end:
        period_str = f"Periode du {period_start} au {period_end}"
    elif period_end:
        period_str = f"Arretee au {period_end}"
    if period_str:
        elems.append(Paragraph(period_str, body))
    elems.append(Spacer(1, 4 * mm))

    # ---- DEST: proprietaire ----
    owner_addr = _addr_block(
        owner.get("name", ""),
        [
            owner.get("address", ""),
            f"{owner.get('postal_code','')} {owner.get('city','')}".strip(),
            owner.get("country", ""),
            owner.get("email", ""),
        ],
    )
    info_lines = [
        f"<b>Reference VCS :</b> <font color='#0055FF'>{owner.get('vcs_code','')}</font>",
        f"<b>Compte propre :</b> {owner.get('account_provisions','-')}  /  Reserve : {owner.get('account_reserve','-')}",
        f"<b>Edite le :</b> {datetime.now().strftime('%d/%m/%Y')}",
    ]
    info_block = Paragraph("<br/>".join(info_lines), small)
    dest_tbl = Table(
        [[Paragraph(owner_addr, small), info_block]],
        colWidths=[90 * mm, 90 * mm],
    )
    dest_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(dest_tbl)
    elems.append(Spacer(1, 6 * mm))

    # ---- MOUVEMENTS TABLE ----
    elems.append(Paragraph("Detail des mouvements", h2))
    head = ["Date", "Journal", "Reference", "Libelle", "Cpt", "Debit", "Credit", "Solde"]
    rows = [head]

    running = float(opening_balance or 0.0)
    if abs(running) > 0.001 or period_start:
        rows.append([
            period_start or "", "", "", "Solde a nouveau", "",
            _fmt_eur(running) if running > 0 else "",
            _fmt_eur(-running) if running < 0 else "",
            _fmt_eur(running),
        ])

    total_debit = 0.0
    total_credit = 0.0
    for m in movements:
        d = float(m.get("debit", 0) or 0)
        c = float(m.get("credit", 0) or 0)
        running += d - c
        total_debit += d
        total_credit += c
        rows.append([
            m.get("date", ""),
            m.get("journal_type", ""),
            (m.get("reference", "") or "")[:18],
            (m.get("description", "") or "")[:42],
            m.get("account_number", ""),
            _fmt_eur(d) if d > 0 else "",
            _fmt_eur(c) if c > 0 else "",
            _fmt_eur(running),
        ])

    # Totals row
    rows.append([
        "", "", "", "TOTAUX", "",
        _fmt_eur(total_debit), _fmt_eur(total_credit), _fmt_eur(running),
    ])

    col_widths = [18 * mm, 14 * mm, 22 * mm, 52 * mm, 14 * mm, 22 * mm, 22 * mm, 26 * mm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0055FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (5, 0), (7, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0055FF")),
        ("LINEBELOW", (0, -2), (-1, -2), 0.5, colors.HexColor("#94A3B8")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FAFBFC")]),
    ]))
    elems.append(tbl)
    elems.append(Spacer(1, 6 * mm))

    # ---- SOLDE FINAL + RIB ----
    sold_color = colors.HexColor("#DC2626") if running > 0.01 else (colors.HexColor("#15803D") if running < -0.01 else colors.HexColor("#475569"))
    sold_label = "Solde a payer" if running > 0.01 else ("Solde en votre faveur" if running < -0.01 else "Solde nul")
    sold_value = abs(running)
    sold_tbl = Table(
        [[
            Paragraph(f"<b>{sold_label}</b>", body),
            Paragraph(f"<b>{_fmt_eur(sold_value)}</b>", ParagraphStyle("sv", parent=body, alignment=2, textColor=sold_color, fontSize=14)),
        ]],
        colWidths=[120 * mm, 60 * mm],
    )
    sold_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFBEB") if running > 0.01 else colors.HexColor("#F0FDF4") if running < -0.01 else colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, sold_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(sold_tbl)
    elems.append(Spacer(1, 6 * mm))

    # ---- INSTRUCTIONS PAIEMENT ----
    if running > 0.01:
        rib_lines = [
            "<b>Coordonnees de paiement</b>",
            f"IBAN : <b>{iban or '—'}</b>",
            f"BIC : {bic or '—'}",
            f"Beneficiaire : {syndic_info.get('name', copropriete.get('name',''))}",
            f"Communication structuree : <font color='#0055FF'><b>{owner.get('vcs_code','')}</b></font>",
            "<font size='7' color='#64748B'><i>Veuillez utiliser obligatoirement la communication structuree ci-dessus pour permettre l'imputation automatique du paiement.</i></font>",
        ]
        rib = Paragraph("<br/>".join(rib_lines), body)
        rib_tbl = Table([[rib]], colWidths=[180 * mm])
        rib_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elems.append(rib_tbl)
        elems.append(Spacer(1, 6 * mm))
    elif running < -0.01:
        elems.append(Paragraph(
            "<i>Votre compte presente un solde crediteur. Pour obtenir le remboursement, "
            "merci de transmettre votre IBAN au syndic.</i>",
            small,
        ))
        elems.append(Spacer(1, 4 * mm))

    elems.append(Paragraph(
        f"<font size='7' color='#94A3B8'>Document edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')} - "
        f"Situation de compte du proprietaire {owner.get('name','')} - ACP {copropriete.get('name','')}.</font>",
        small,
    ))

    doc.build(elems)
    return buf.getvalue()
