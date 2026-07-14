"""PDF Balance des Tiers - VERSION SIMPLIFIEE pour syndic / CE.

Synthese :
- 2 sections : Proprietaires, Fournisseurs
- Pour chaque tier : nom, code VCS/BCE, soldes Debit/Credit, balance
- Cards de totaux en tete (debiteurs / crediteurs)
- Mise en page compacte sur A4 paysage si beaucoup de lignes
"""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
RED = colors.HexColor("#DC2626")
GREEN = colors.HexColor("#16A34A")
RED_BG = colors.HexColor("#FEF3F2")
GREEN_BG = colors.HexColor("#F0FDF4")


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
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def build_balance_tiers_detailed_pdf(
    *,
    copropriete: dict,
    owners_detail: list = None,   # [{owner, movements, balance, status}, ...] ou None pour omettre la section
    suppliers_detail: list = None,  # [{supplier, movements, balance, status}, ...] ou None pour omettre la section
    period_start: str = "",
    period_end: str = "",
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """iter90fm : PDF "Balance des Tiers - vue detaillee" : pour chaque
    proprietaire/fournisseur, affiche le solde ET le detail des mouvements
    (grand livre) sur la periode, contrairement a `build_balance_tiers_pdf`
    (vue simplifiee : uniquement les totaux, 1 ligne par tiers).

    Document interne syndic (audit-friendly), a distinguer de la
    "Situation de compte" (lettre postale individuelle par proprietaire,
    `pdf_situation_compte.py`) : ici, TOUS les tiers d'une categorie sont
    regroupes dans UN SEUL document compact.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=28 * mm if use_new_layout else 14 * mm,
        title=f"Balance des Tiers (detaillee) - {copropriete.get('name','')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=18, leading=22, textColor=BRAND,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=9.5, leading=12, textColor=SLATE_500, spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=15, textColor=SLATE_900,
                        spaceAfter=4, fontName="Helvetica-Bold")
    h3 = ParagraphStyle("h3", parent=styles["Heading3"],
                        fontSize=10, leading=13, textColor=colors.white,
                        fontName="Helvetica-Bold")
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9, leading=11.5)
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8, leading=10, textColor=SLATE_500)
    op_style = ParagraphStyle("op", parent=body, fontSize=8, leading=10, wordWrap="CJK")

    elems = []

    if use_new_layout:
        elems.append(build_header_with_logo(syndic_pdf_ctx.get("logo_bytes"), copropriete, small))
        elems.append(Spacer(1, 4 * mm))

    period_str = ""
    if period_start and period_end:
        period_str = f"Periode du {_fmt_date(period_start)} au {_fmt_date(period_end)}"
    elif period_end:
        period_str = f"Arretee au {_fmt_date(period_end)}"
    else:
        period_str = "Situation a date"
    header_left = "" if use_new_layout else (
        f"<b>{copropriete.get('name','')}</b><br/>"
        f"{copropriete.get('address','')} - {copropriete.get('postal_code','')} {copropriete.get('city','')}"
    )
    header_tbl = Table(
        [[Paragraph(header_left, small),
          Paragraph(f"{period_str}<br/>Edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}", small)]],
        colWidths=[150 * mm, 120 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 6 * mm))
    elems.append(Paragraph("Balance des Tiers - Vue detaillee", title_style))
    elems.append(Paragraph(
        "Solde et detail des mouvements (grand livre) par tiers.", sub_style,
    ))
    elems.append(Spacer(1, 5 * mm))

    def _tier_block(name: str, subtitle: str, movements: list, balance: float, status: str):
        color = RED if status == "debiteur" else (GREEN if status == "crediteur" else SLATE_500)
        head_tbl = Table(
            [[Paragraph(f"<b>{name}</b>" + (f"  <font size='7' color='#CBD5E1'>{subtitle}</font>" if subtitle else ""), h3),
              Paragraph(f"<b>{_fmt_eur(balance)}</b>", ParagraphStyle("r3", parent=h3, alignment=2))]],
            colWidths=[220 * mm, 47 * mm],
        )
        head_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        block = [head_tbl]
        if not movements:
            block.append(Paragraph("<i>Aucun mouvement sur la periode.</i>", small))
        else:
            rows = [["Date", "Operation", "Debit", "Credit", "Solde"]]
            running = 0.0
            for m in movements:
                d = float(m.get("debit", 0) or 0)
                c = float(m.get("credit", 0) or 0)
                running += d - c
                rows.append([
                    _fmt_date(m.get("date", "")),
                    Paragraph((m.get("description", "") or "")[:100], op_style),
                    _fmt_eur(d) if d > 0 else "",
                    _fmt_eur(c) if c > 0 else "",
                    _fmt_eur(running),
                ])
            tbl = Table(rows, colWidths=[22 * mm, 165 * mm, 26 * mm, 26 * mm, 28 * mm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 0), (4, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, SLATE_300),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]))
            block.append(tbl)
        block.append(Spacer(1, 5 * mm))
        return block

    if owners_detail is not None:
        elems.append(Paragraph("1. Proprietaires", h2))
        if not owners_detail:
            elems.append(Paragraph("<i>Aucun proprietaire.</i>", body))
        for od in owners_detail:
            owner = od.get("owner", {}) or {}
            elems.extend(_tier_block(
                owner.get("name", "") or "—",
                owner.get("vcs_code", ""),
                od.get("movements", []),
                float(od.get("balance", 0) or 0),
                od.get("status", "solde"),
            ))
        elems.append(Spacer(1, 4 * mm))

    if suppliers_detail is not None:
        elems.append(Paragraph("2. Fournisseurs", h2))
        if not suppliers_detail:
            elems.append(Paragraph("<i>Aucun fournisseur.</i>", body))
        for sd in suppliers_detail:
            supplier = sd.get("supplier", {}) or {}
            elems.extend(_tier_block(
                supplier.get("name", "") or "—",
                supplier.get("vat_number", "") or supplier.get("bce_number", ""),
                sd.get("movements", []),
                float(sd.get("balance", 0) or 0),
                sd.get("status", "solde"),
            ))

    elems.append(Spacer(1, 4 * mm))
    elems.append(Paragraph(
        f"<font size='7' color='#94A3B8'><i>Document interne genere automatiquement - "
        f"Balance des tiers detaillee de la copropriete {copropriete.get('name','')}. "
        f"Edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}.</i></font>",
        small,
    ))

    if footer_cb:
        doc.build(elems, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elems)
    return buf.getvalue()


def build_balance_tiers_pdf(
    *,
    copropriete: dict,
    owners_data: dict,   # {owners, total_debiteurs, total_crediteurs}
    suppliers_data: dict,  # {suppliers, total_debiteurs, total_crediteurs}
    period_start: str = "",
    period_end: str = "",
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Genere le PDF synthese balance des tiers.

    iter90dj : si `syndic_pdf_ctx` est fourni (via
    `pdf_layout.resolve_syndic_pdf_context(db, copropriete)`), ajoute :
    - le logo cabinet + coordonnees en tete de la 1re page ;
    - un pied de page avec mentions legales + numero de page sur toutes les pages.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=28 * mm if use_new_layout else 14 * mm,
        title=f"Balance des Tiers - {copropriete.get('name','')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=18, leading=22, textColor=BRAND,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=9.5, leading=12, textColor=SLATE_500, spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=15, textColor=SLATE_900,
                        spaceAfter=4, fontName="Helvetica-Bold")
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9, leading=11.5)
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8, leading=10, textColor=SLATE_500)

    elems = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elems.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            small,
        ))
        elems.append(Spacer(1, 4 * mm))

    # ---- HEADER ----
    period_str = ""
    if period_start and period_end:
        period_str = f"Periode du {_fmt_date(period_start)} au {_fmt_date(period_end)}"
    elif period_end:
        period_str = f"Arretee au {_fmt_date(period_end)}"
    else:
        period_str = "Situation a date"
    # iter90dp : si le header logo affiche deja les infos ACP (name/adresse/BCE),
    # on omet le bloc de gauche pour eviter la duplication.
    if use_new_layout:
        info_text = (
            f"{period_str}<br/>"
            f"Edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}"
        )
        header_tbl = Table(
            [[Paragraph("", small), Paragraph(info_text, small)]],
            colWidths=[150 * mm, 120 * mm],
        )
    else:
        header_text = (
            f"<b>{copropriete.get('name','')}</b><br/>"
            f"{copropriete.get('address','')} - "
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
            f"BCE : {copropriete.get('bce','-')}"
        )
        info_text = (
            f"{period_str}<br/>"
            f"Edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}"
        )
        header_tbl = Table(
            [[Paragraph(header_text, small), Paragraph(info_text, small)]],
            colWidths=[150 * mm, 120 * mm],
        )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 6 * mm))

    elems.append(Paragraph("Balance des Tiers", title_style))
    elems.append(Paragraph(
        "Synthese des soldes par proprietaire et fournisseur.",
        sub_style,
    ))
    elems.append(Spacer(1, 4 * mm))

    # ---- TOTALS CARDS ----
    owners_deb = float(owners_data.get("total_debiteurs", 0) or 0)
    owners_cred = float(owners_data.get("total_crediteurs", 0) or 0)
    suppliers_deb = float(suppliers_data.get("total_debiteurs", 0) or 0)
    suppliers_cred = float(suppliers_data.get("total_crediteurs", 0) or 0)

    cards = [
        [
            Paragraph(
                f"<font size='9' color='#DC2626'><b>PROPRIETAIRES DEBITEURS</b></font><br/>"
                f"<font size='15' color='#DC2626'><b>{_fmt_eur(owners_deb)}</b></font><br/>"
                f"<font size='8' color='#64748B'>doivent a la copropriete</font>",
                body,
            ),
            Paragraph(
                f"<font size='9' color='#16A34A'><b>PROPRIETAIRES CREDITEURS</b></font><br/>"
                f"<font size='15' color='#16A34A'><b>{_fmt_eur(owners_cred)}</b></font><br/>"
                f"<font size='8' color='#64748B'>la copropriete doit rembourser</font>",
                body,
            ),
            Paragraph(
                f"<font size='9' color='#0055FF'><b>FOURNISSEURS A PAYER</b></font><br/>"
                f"<font size='15' color='#0055FF'><b>{_fmt_eur(suppliers_cred)}</b></font><br/>"
                f"<font size='8' color='#64748B'>factures dues</font>",
                body,
            ),
            Paragraph(
                f"<font size='9' color='#64748B'><b>FOURNISSEURS ACOMPTES</b></font><br/>"
                f"<font size='15' color='#64748B'><b>{_fmt_eur(suppliers_deb)}</b></font><br/>"
                f"<font size='8' color='#64748B'>avances/avoirs en notre faveur</font>",
                body,
            ),
        ]
    ]
    cards_tbl = Table(cards, colWidths=[67 * mm, 67 * mm, 67 * mm, 67 * mm])
    cards_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), RED_BG),
        ("BACKGROUND", (1, 0), (1, 0), GREEN_BG),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#EFF6FF")),
        ("BACKGROUND", (3, 0), (3, 0), SLATE_50),
        ("BOX", (0, 0), (0, 0), 0.8, RED),
        ("BOX", (1, 0), (1, 0), 0.8, GREEN),
        ("BOX", (2, 0), (2, 0), 0.8, BRAND),
        ("BOX", (3, 0), (3, 0), 0.8, SLATE_300),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    elems.append(cards_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- SECTION 1 : PROPRIETAIRES ----
    elems.append(Paragraph("1. Proprietaires", h2))
    owners_list = owners_data.get("owners", []) or []
    if not owners_list:
        elems.append(Paragraph("<i>Aucun proprietaire.</i>", body))
    else:
        rows = [[
            "Proprietaire", "VCS", "Compte prov.", "Compte res.",
            "Appele", "Paye", "Solde prov.", "Solde res.", "Total solde", "Statut",
        ]]
        cell_style = ParagraphStyle(
            "cell", parent=body, fontSize=8, leading=10, wordWrap="CJK",
        )
        for o in owners_list:
            balance = float(o.get("balance", 0) or 0)
            statut = "Debiteur" if balance > 0.01 else (
                "Crediteur" if balance < -0.01 else "Solde")
            statut_color = RED if balance > 0.01 else (
                GREEN if balance < -0.01 else SLATE_500)
            rows.append([
                Paragraph(o.get("owner_name", "") or "", cell_style),
                o.get("vcs_code", "") or "",
                o.get("account_provisions", "") or "",
                o.get("account_reserve", "") or "",
                _fmt_eur(o.get("total_called", 0)),
                _fmt_eur(o.get("total_paid", 0)),
                _fmt_eur(o.get("provisions_balance", 0)),
                _fmt_eur(o.get("reserve_balance", 0)),
                Paragraph(
                    f"<font color='{statut_color.hexval()}'><b>{_fmt_eur(balance)}</b></font>",
                    body),
                Paragraph(
                    f"<font color='{statut_color.hexval()}'>{statut}</font>",
                    body),
            ])
        # TOTAL row
        rows.append([
            "TOTAUX", "", "", "",
            _fmt_eur(sum(float(o.get("total_called", 0) or 0) for o in owners_list)),
            _fmt_eur(sum(float(o.get("total_paid", 0) or 0) for o in owners_list)),
            _fmt_eur(sum(float(o.get("provisions_balance", 0) or 0) for o in owners_list)),
            _fmt_eur(sum(float(o.get("reserve_balance", 0) or 0) for o in owners_list)),
            _fmt_eur(sum(float(o.get("balance", 0) or 0) for o in owners_list)),
            "",
        ])
        col_widths = [50 * mm, 22 * mm, 22 * mm, 22 * mm, 26 * mm, 26 * mm,
                      26 * mm, 26 * mm, 28 * mm, 22 * mm]
        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (4, 0), (8, -1), "RIGHT"),
            ("ALIGN", (1, 0), (3, -1), "CENTER"),
            ("ALIGN", (9, 0), (9, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elems.append(tbl)

    elems.append(Spacer(1, 8 * mm))

    # ---- SECTION 2 : FOURNISSEURS ----
    elems.append(Paragraph("2. Fournisseurs", h2))
    suppliers_list = suppliers_data.get("suppliers", []) or []
    if not suppliers_list:
        elems.append(Paragraph("<i>Aucun fournisseur.</i>", body))
    else:
        rows = [[
            "Fournisseur", "TVA / BCE", "Compte tier",
            "Facture", "Paye", "Solde", "Statut",
        ]]
        for s in suppliers_list:
            balance = float(s.get("balance", 0) or 0)
            # Pour fournisseurs : balance positif = on doit payer (credit > debit dans 440)
            statut = "A payer" if balance > 0.01 else (
                "Acompte" if balance < -0.01 else "Solde")
            statut_color = BRAND if balance > 0.01 else (
                SLATE_500 if balance < -0.01 else GREEN)
            rows.append([
                (s.get("supplier_name", "") or "")[:50],
                s.get("vat_number", "") or s.get("bce_number", "") or "",
                s.get("tier_account", "") or s.get("account_number", "") or "",
                _fmt_eur(s.get("total_invoiced", 0)),
                _fmt_eur(s.get("total_paid", 0)),
                Paragraph(
                    f"<font color='{statut_color.hexval()}'><b>{_fmt_eur(balance)}</b></font>",
                    body),
                Paragraph(
                    f"<font color='{statut_color.hexval()}'>{statut}</font>",
                    body),
            ])
        # TOTAL row
        rows.append([
            "TOTAUX", "", "",
            _fmt_eur(sum(float(s.get("total_invoiced", 0) or 0) for s in suppliers_list)),
            _fmt_eur(sum(float(s.get("total_paid", 0) or 0) for s in suppliers_list)),
            _fmt_eur(sum(float(s.get("balance", 0) or 0) for s in suppliers_list)),
            "",
        ])
        col_widths = [70 * mm, 30 * mm, 28 * mm, 35 * mm, 35 * mm, 35 * mm, 35 * mm]
        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("ALIGN", (3, 0), (5, -1), "RIGHT"),
            ("ALIGN", (1, 0), (2, -1), "CENTER"),
            ("ALIGN", (6, 0), (6, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elems.append(tbl)

    # ---- FOOTER ----
    elems.append(Spacer(1, 6 * mm))
    elems.append(Paragraph(
        f"<font size='7' color='#94A3B8'><i>Document interne genere automatiquement - "
        f"Balance des tiers de la copropriete {copropriete.get('name','')}. "
        f"Edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}.</i></font>",
        small,
    ))

    if footer_cb:
        doc.build(elems, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elems)
    return buf.getvalue()
