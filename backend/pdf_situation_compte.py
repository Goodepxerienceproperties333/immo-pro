"""PDF Situation de compte (releve detaille proprietaire) - VERSION SIMPLIFIEE
Optimise pour LECTURE par des proprietaires non-comptables :
- Pas de jargon technique (Debit/Credit -> "Montant a payer" / "Paiement recu")
- Dates au format jj/mm/aaaa
- Libelles parlants (preprocesses pour clarifier les "AF-..." etc.)
- Mise en page aeree avec icones et couleurs
"""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",") + " EUR"


def _fmt_date(s):
    """Convert YYYY-MM-DD -> jj/mm/aaaa. Returns input if not parseable."""
    if not s:
        return ""
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.strftime("%d/%m/%Y")
    except Exception:
        return s


def _humanize_label(description, reference, journal_type):
    """Transform technical labels into something readable for non-accountants."""
    desc = (description or "").strip()
    ref = (reference or "").strip()
    # Strip technical [VE], [AC]... prefix
    if desc.startswith("[") and "]" in desc:
        desc = desc.split("]", 1)[1].strip()
    # Strip "Appel: " prefix
    if desc.lower().startswith("appel:"):
        desc = desc[6:].strip()
    # Reference variants
    label_prefix = ""
    if journal_type == "VE":
        label_prefix = "Appel de fonds : "
    elif journal_type == "AC":
        label_prefix = "Facture : "
    elif journal_type == "FI" or journal_type == "BANK":
        label_prefix = "Paiement recu : "
    elif journal_type == "OD":
        label_prefix = "Operation : "
    elif journal_type == "AN":
        label_prefix = "Solde reporte : "
    # Si la description commence deja par "Appel " (provisions/reserve/roulement/...)
    # on ne re-prefixe pas pour eviter "Appel de fonds : Appel de provisions - ..."
    if label_prefix == "Appel de fonds : " and desc.lower().startswith("appel "):
        full = desc
    elif label_prefix and not desc.lower().startswith(label_prefix.lower().rstrip(": ").lower()):
        full = f"{label_prefix}{desc}" if desc else label_prefix.rstrip(": ")
    else:
        full = desc or label_prefix.rstrip(": ")
    return full[:80]


def _addr_block(name, parts):
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
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """iter90av : `syndic_pdf_ctx` (optionnel) permet d'utiliser le nouveau
    layout avec logo cabinet, adresse destinataire alignee pour fenetre C6
    droite, et pied de page avec mentions legales."""
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    buf = BytesIO()
    from pdf_layout import (
        build_header_with_logo, build_recipient_address_flowable,
        make_footer_callback,
    )
    footer_cb = None
    if use_new_layout:
        footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", ""))
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=28 * mm if use_new_layout else 18 * mm,
        title=f"Situation de compte - {owner.get('name','')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=20, leading=24, textColor=colors.HexColor("#0055FF"),
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=10, leading=13, textColor=colors.HexColor("#475569"),
        spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=15, textColor=colors.HexColor("#0F172A"),
                        spaceAfter=4, fontName="Helvetica-Bold")
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"))
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9.5, leading=12.5)

    elems = []

    # ---- HEADER : logo + infos ACP (iter90dm) ----
    if use_new_layout:
        elems.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"), copropriete, small,
        ))
        elems.append(Spacer(1, 4 * mm))
        # Bloc adresse destinataire alignee fenetre C6 droite
        elems.append(build_recipient_address_flowable(owner, small))
        elems.append(Spacer(1, 10 * mm))
    else:
        # ---- HEADER : 2 blocks (syndic / acp) - ancien layout ----
        syndic_lines = _addr_block(
            f"<b>{syndic_info.get('name','Syndic')}</b>",
            [
                syndic_info.get("address", ""),
                f"{syndic_info.get('postal_code','')} {syndic_info.get('city','')}".strip(),
                syndic_info.get("email", ""),
                syndic_info.get("phone", ""),
            ],
        )
        acp_lines = _addr_block(
            f"<b>{copropriete.get('name','')}</b>",
            [
                copropriete.get("address", ""),
                f"{copropriete.get('postal_code','')} {copropriete.get('city','')}".strip(),
            ],
        )
        header_tbl = Table(
            [[Paragraph(syndic_lines, small), Paragraph(acp_lines, small)]],
            colWidths=[90 * mm, 90 * mm],
        )
        header_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        elems.append(header_tbl)
        elems.append(Spacer(1, 8 * mm))

    # ---- TITLE BIG ----
    elems.append(Paragraph("Situation de votre compte", title_style))
    period_str = ""
    if period_start and period_end:
        period_str = f"Periode du {_fmt_date(period_start)} au {_fmt_date(period_end)}"
    elif period_end:
        period_str = f"Arretee au {_fmt_date(period_end)}"
    if period_str:
        elems.append(Paragraph(period_str, sub_style))
    elems.append(Spacer(1, 4 * mm))

    # ---- DESTINATAIRE (skip if new layout - deja affiche en haut pour C6) ----
    if not use_new_layout:
        owner_addr_lines = _addr_block(
            f"<b>{owner.get('name','')}</b>",
            [
                owner.get("address", ""),
                f"{owner.get('postal_code','')} {owner.get('city','')}".strip(),
                owner.get("country", ""),
            ],
        )
        info_lines = [
            f"Edite le <b>{datetime.now().strftime('%d/%m/%Y')}</b>",
            f"Reference communication : <font color='#0055FF'><b>{owner.get('vcs_code','')}</b></font>",
        ]
        info_block = Paragraph("<br/>".join(info_lines), small)
        dest_tbl = Table(
            [[Paragraph(owner_addr_lines, small), info_block]],
            colWidths=[90 * mm, 90 * mm],
        )
        dest_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        elems.append(dest_tbl)
        elems.append(Spacer(1, 8 * mm))

    # ---- COMPUTE TOTALS FIRST (for highlighted summary card) ----
    running = float(opening_balance or 0.0)
    total_debit = 0.0
    total_credit = 0.0
    for m in movements:
        d = float(m.get("debit", 0) or 0)
        c = float(m.get("credit", 0) or 0)
        running += d - c
        total_debit += d
        total_credit += c

    # ---- SUMMARY CARD (very visible) ----
    sold_color = colors.HexColor("#DC2626") if running > 0.01 else (colors.HexColor("#16A34A") if running < -0.01 else colors.HexColor("#475569"))
    sold_label = "A REGLER" if running > 0.01 else ("EN VOTRE FAVEUR" if running < -0.01 else "SOLDE NUL")
    sold_value = abs(running)
    bg_summary = colors.HexColor("#FEF3F2") if running > 0.01 else (colors.HexColor("#F0FDF4") if running < -0.01 else colors.HexColor("#F8FAFC"))

    summary_rows = [
        [Paragraph("<b>Total des appels</b>", body),
         Paragraph(_fmt_eur(total_debit), ParagraphStyle("r", parent=body, alignment=2))],
        [Paragraph("<b>Total verse pendant la periode</b>", body),
         Paragraph(_fmt_eur(total_credit), ParagraphStyle("r", parent=body, alignment=2, textColor=colors.HexColor("#16A34A")))],
    ]
    if abs(float(opening_balance or 0)) > 0.01:
        ob = float(opening_balance)
        summary_rows.insert(0, [
            Paragraph(f"<b>Solde reporte au {_fmt_date(period_start)}</b>", body),
            Paragraph(_fmt_eur(ob), ParagraphStyle("r", parent=body, alignment=2)),
        ])
    # Final solde
    summary_rows.append([
        Paragraph(f"<b><font size='12'>{sold_label}</font></b>", body),
        Paragraph(f"<b><font size='16' color='{sold_color.hexval()}'>{_fmt_eur(sold_value)}</font></b>",
                  ParagraphStyle("r", parent=body, alignment=2)),
    ])

    summary_tbl = Table(summary_rows, colWidths=[110 * mm, 70 * mm])
    summary_style = [
        ("BACKGROUND", (0, 0), (-1, -1), bg_summary),
        ("BOX", (0, 0), (-1, -1), 1.2, sold_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        # Final row separator
        ("LINEABOVE", (0, -1), (-1, -1), 1, sold_color),
        ("TOPPADDING", (0, -1), (-1, -1), 11),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 11),
    ]
    summary_tbl.setStyle(TableStyle(summary_style))
    elems.append(summary_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- MOVEMENTS - readable format ----
    elems.append(Paragraph("Detail des operations", h2))
    elems.append(Paragraph(
        "Toutes les operations qui ont impacte votre compte pendant la periode.",
        sub_style,
    ))
    elems.append(Spacer(1, 2 * mm))

    head = ["Date", "Operation", "A payer", "Verse", "Solde"]
    rows = [head]

    # Style pour libelles wrappables dans la colonne Operation
    op_style = ParagraphStyle(
        "op", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
    )

    running2 = float(opening_balance or 0.0)
    if abs(running2) > 0.001 and period_start:
        rows.append([
            _fmt_date(period_start),
            Paragraph("Solde reporte (debut periode)", op_style),
            "", "",
            _fmt_eur(running2),
        ])

    for m in movements:
        d = float(m.get("debit", 0) or 0)
        c = float(m.get("credit", 0) or 0)
        running2 += d - c
        label = _humanize_label(m.get("description", ""), m.get("reference", ""), m.get("journal_type", ""))
        rows.append([
            _fmt_date(m.get("date", "")),
            Paragraph(label, op_style),
            _fmt_eur(d) if d > 0 else "",
            _fmt_eur(c) if c > 0 else "",
            _fmt_eur(running2),
        ])

    # Total row (mini-recap inline)
    rows.append([
        "",
        Paragraph("<b>TOTAL DE LA PERIODE</b>", op_style),
        _fmt_eur(total_debit), _fmt_eur(total_credit), _fmt_eur(running2),
    ])

    col_widths = [22 * mm, 86 * mm, 24 * mm, 24 * mm, 24 * mm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0055FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (2, 0), (-1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        # Body
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("TEXTCOLOR", (3, 1), (3, -2), colors.HexColor("#16A34A")),  # paiements en vert
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FAFBFC")]),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        # Total row
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#0055FF")),
        ("TEXTCOLOR", (3, -1), (3, -1), colors.HexColor("#16A34A")),
        ("TOPPADDING", (0, -1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    elems.append(tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- PAYMENT INSTRUCTIONS ----
    if running > 0.01:
        rib_lines = [
            "<font size='11'><b>Comment regler ce solde ?</b></font>",
            "&nbsp;",
            f"Veuillez verser <b>{_fmt_eur(running)}</b> sur le compte :",
            f"<b>IBAN :</b> <font name='Courier'>{iban or '—'}</font>",
            f"<b>BIC :</b> <font name='Courier'>{bic or '—'}</font>",
            f"<b>Beneficiaire :</b> {copropriete.get('name', syndic_info.get('name',''))}",
            "&nbsp;",
            f"<b>Communication structuree (obligatoire) :</b>",
            f"<font color='#0055FF' size='13'><b>{owner.get('vcs_code','')}</b></font>",
            "&nbsp;",
            "<font size='8' color='#64748B'><i>Indiquez imperativement cette communication structuree afin que votre paiement soit reconnu automatiquement et impute sur votre compte.</i></font>",
        ]
        rib = Paragraph("<br/>".join(rib_lines), body)
        rib_tbl = Table([[rib]], colWidths=[180 * mm])
        rib_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#3B82F6")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        elems.append(KeepTogether(rib_tbl))
        elems.append(Spacer(1, 6 * mm))
    elif running < -0.01:
        msg = Paragraph(
            f"<font size='11'><b>Solde en votre faveur : {_fmt_eur(abs(running))}</b></font><br/>"
            "&nbsp;<br/>"
            "Ce solde sera deduit de votre prochain appel de fonds. "
            "Pour obtenir le remboursement par virement, transmettez votre IBAN au syndic.",
            body,
        )
        msg_tbl = Table([[msg]], colWidths=[180 * mm])
        msg_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#16A34A")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        elems.append(msg_tbl)
        elems.append(Spacer(1, 6 * mm))

    # ---- FOOTER ----
    elems.append(Paragraph(
        f"<font size='7.5' color='#94A3B8'>"
        f"Document edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')} - "
        f"Situation de compte de {owner.get('name','')} - "
        f"Copropriete {copropriete.get('name','')}."
        f"<br/>En cas de question, contactez votre syndic : "
        f"{syndic_info.get('email','')} {syndic_info.get('phone','')}.</font>",
        small,
    ))

    if footer_cb:
        doc.build(elems, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elems)
    return buf.getvalue()
