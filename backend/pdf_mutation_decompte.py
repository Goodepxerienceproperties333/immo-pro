"""Generateur PDF Decompte de mutation (notarial).

Reprend les 3 blocs comptables generes par `_compute_mutation_breakdown` :
  1. Transfert du fonds de roulement (quote-part du lot)
  2. Prorata des appels de provisions du trimestre courant
  3. Prorata des appels futurs de la periode comptable

Format pensa pour transmission au notaire et joint a l'acte de vente.
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


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
RED = colors.HexColor("#DC2626")
GREEN = colors.HexColor("#16A34A")
AMBER_BG = colors.HexColor("#FEF3C7")
BLUE_BG = colors.HexColor("#DBEAFE")


def _fmt_eur(v: float) -> str:
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",") + " EUR"


def _fmt_date(s: str) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def build_mutation_decompte_pdf(
    *, copropriete: dict, lot: dict,
    seller: dict, buyer: dict,
    mutation: dict, breakdown: dict,
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Genere le PDF Decompte de mutation.

    Args:
        copropriete: dict copropriete (name, reference, address...)
        lot: dict lot (number, quotity, description...)
        seller: dict owner vendeur (name, address, vcs...)
        buyer: dict owner acheteur (idem)
        mutation: dict mutation_record (date, sale_price, total_transfer, etc.)
        breakdown: dict resultat de `_compute_mutation_breakdown` (roulement_quota,
                   current_period_details, future_calls, ...)
        syndic_pdf_ctx: iter90av - contexte cabinet (logo + mentions legales)
    """
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    from pdf_layout import (
        build_header_with_logo, build_recipient_address_flowable,
        make_footer_callback,
    )
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=28 * mm if use_new_layout else 18 * mm,
        title=f"Decompte de mutation - Lot {lot.get('number','')}",
    )
    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], fontName="Helvetica-Bold",
                              fontSize=18, textColor=BRAND, alignment=0, spaceAfter=4)
    h_sub = ParagraphStyle("h_sub", parent=styles["Normal"], fontName="Helvetica",
                            fontSize=10, textColor=SLATE_500, spaceAfter=8)
    h_section = ParagraphStyle("h_section", parent=styles["Heading2"], fontName="Helvetica-Bold",
                                fontSize=12, textColor=SLATE_900, spaceBefore=14, spaceAfter=6)
    h_block = ParagraphStyle("h_block", parent=styles["Normal"], fontName="Helvetica-Bold",
                              fontSize=11, textColor=BRAND, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica",
                           fontSize=9, textColor=SLATE_700, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=SLATE_500)
    rule = ParagraphStyle("rule", parent=body, fontSize=8, textColor=SLATE_500,
                           backColor=SLATE_50, borderPadding=4, leading=11)
    legal = ParagraphStyle("legal", parent=small, fontSize=7,
                            backColor=AMBER_BG, borderPadding=6, leading=10)

    elements = []

    # ----- HEADER : logo + adresse destinataire (nouveau layout iter90av) -----
    # Destinataire mutation = acquereur (buyer) pour envoi postal
    if use_new_layout:
        cabinet_info = syndic_pdf_ctx.get("syndic_config") or {}
        elements.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"), cabinet_info, small,
        ))
        elements.append(Spacer(1, 4 * mm))
        elements.append(build_recipient_address_flowable(buyer, small))
        elements.append(Spacer(1, 6 * mm))

    # ----- HEADER -----
    sale_date = mutation.get("date", "")
    elements.append(Paragraph("Decompte de mutation", h_title))
    elements.append(Paragraph(
        f"<b>{copropriete.get('name','')}</b> "
        + (f" - Reference {copropriete.get('reference','')}" if copropriete.get('reference') else "")
        + (f" - {copropriete.get('address','')}" if copropriete.get('address') else ""),
        h_sub,
    ))
    elements.append(Paragraph(
        f"Date de vente : <b>{_fmt_date(sale_date)}</b>"
        + (f" - Prix : <b>{_fmt_eur(mutation.get('sale_price', 0))}</b>" if mutation.get("sale_price") else ""),
        h_sub,
    ))

    # ----- PARTIES (vendeur / acheteur / lot) -----
    parties_data = [
        [
            Paragraph("<b>VENDEUR</b>", small),
            Paragraph("<b>ACHETEUR</b>", small),
        ],
        [
            Paragraph(
                f"<b>{seller.get('name','')}</b><br/>"
                + (f"{seller.get('address','')}<br/>" if seller.get('address') else "")
                + (f"{seller.get('postal_code','')} {seller.get('city','')}<br/>" if seller.get('city') else "")
                + (f"<font size=7>VCS : {seller.get('vcs_code','')}</font>" if seller.get('vcs_code') else ""),
                body,
            ),
            Paragraph(
                f"<b>{buyer.get('name','')}</b><br/>"
                + (f"{buyer.get('address','')}<br/>" if buyer.get('address') else "")
                + (f"{buyer.get('postal_code','')} {buyer.get('city','')}<br/>" if buyer.get('city') else "")
                + (f"<font size=7>VCS : {buyer.get('vcs_code','')}</font>" if buyer.get('vcs_code') else ""),
                body,
            ),
        ],
    ]
    tbl = Table(parties_data, colWidths=[88 * mm, 88 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, SLATE_300),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(tbl)

    elements.append(Spacer(1, 6))
    lot_box = [
        [
            Paragraph(f"<b>LOT VENDU</b>", small),
            Paragraph(f"<b>QUOTITES</b>", small),
            Paragraph(f"<b>DESCRIPTION</b>", small),
        ],
        [
            Paragraph(f"Lot {lot.get('number','')}", body),
            Paragraph(f"{lot.get('quotity', 0)}", body),
            Paragraph(f"{lot.get('description','') or '-'}", body),
        ],
    ]
    tbl_lot = Table(lot_box, colWidths=[40 * mm, 30 * mm, 106 * mm])
    tbl_lot.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, SLATE_300),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(tbl_lot)

    # ----- RECAP NUMERIQUE -----
    total_transfer = float(breakdown.get("total_transfer", 0) or 0)
    fr_quota = float(breakdown.get("roulement_quota", 0) or 0)
    current_prorata = float(breakdown.get("current_period_prorata", 0) or 0)
    futures_total = float(breakdown.get("future_calls_total", 0) or 0)

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Recapitulatif du decompte", h_section))
    recap_data = [
        ["Poste", "Montant", "Sens"],
        ["1. Transfert fonds de roulement", _fmt_eur(fr_quota), "Acheteur > Vendeur"],
        ["2. Prorata appels en cours", _fmt_eur(current_prorata), "Acheteur > Vendeur"],
        ["3. Appels futurs (periode comptable)", _fmt_eur(futures_total), "Acheteur > Vendeur"],
        ["TOTAL DU A L'ACHETEUR PAR LE VENDEUR",
         _fmt_eur(fr_quota + current_prorata + futures_total),
         ""],
    ]
    recap_tbl = Table(recap_data, colWidths=[110 * mm, 40 * mm, 36 * mm])
    recap_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, -1), (-1, -1), BLUE_BG),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, SLATE_300),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(recap_tbl)

    # ----- BLOC 1 : FONDS DE ROULEMENT -----
    elements.append(Paragraph("1. Transfert du fonds de roulement", h_section))
    elements.append(Paragraph(
        "<i>Conformement a la legislation belge sur la copropriete (art. 3.86 Code civil), "
        "le fonds de roulement suit le lot lors de la mutation. La quote-part du vendeur "
        "est restituee par l'acheteur a la date de la vente.</i>",
        rule,
    ))
    elements.append(Spacer(1, 4))
    fr_data = [
        ["Date ecriture", "Compte", "Libelle", "Quote-part lot", "Montant"],
        [
            _fmt_date(sale_date),
            "100",
            f"Fonds de roulement - lot {lot.get('number','')}",
            f"{lot.get('quotity', 0)}",
            _fmt_eur(fr_quota),
        ],
    ]
    fr_tbl = Table(fr_data, colWidths=[28 * mm, 18 * mm, 70 * mm, 32 * mm, 30 * mm])
    fr_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, SLATE_300),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(fr_tbl)

    # ----- BLOC 2 : PRORATA APPELS EN COURS -----
    elements.append(Paragraph("2. Prorata des appels de provisions en cours", h_section))
    elements.append(Paragraph(
        "<i>Le vendeur conserve a sa charge les provisions de la periode "
        "[date d'appel, date de vente]. L'acheteur prend en charge le solde "
        "[date de vente, derniere date de l'appel] au prorata des jours.</i>",
        rule,
    ))
    elements.append(Spacer(1, 4))
    details = breakdown.get("current_period_details") or []
    if not details:
        elements.append(Paragraph(
            "<i>Aucun appel de provisions n'est en cours a la date de vente "
            f"({_fmt_date(sale_date)}).</i>",
            small,
        ))
    else:
        bloc2_data = [["Appel", "Date appel", "Periode", "Mt vendeur", "J apres / total", "Prorata"]]
        for d in details:
            bloc2_data.append([
                d.get("fund_call_name", ""),
                _fmt_date(d.get("call_date", "")),
                f"{_fmt_date(d.get('period_start',''))} -> {_fmt_date(d.get('period_end',''))}",
                _fmt_eur(d.get("owner_amount", 0)),
                f"{d.get('days_after','')} / {d.get('total_days','')}",
                _fmt_eur(d.get("prorata", 0)),
            ])
        bloc2_data.append([
            "", "", "",
            "", "Sous-total",
            _fmt_eur(current_prorata),
        ])
        bloc2_tbl = Table(bloc2_data, colWidths=[34 * mm, 20 * mm, 44 * mm, 22 * mm, 26 * mm, 32 * mm])
        bloc2_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.4, SLATE_300),
            ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (4, 1), (4, -1), "CENTER"),
            ("BACKGROUND", (0, -1), (-1, -1), BLUE_BG),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(bloc2_tbl)

    # ----- BLOC 3 : APPELS FUTURS -----
    elements.append(Paragraph(
        f"3. Appels futurs ({breakdown.get('budget_frequency_label','')})",
        h_section,
    ))
    elements.append(Paragraph(
        "<i>Pour chaque appel de provisions a echoir apres la date de vente et "
        "jusqu'a la cloture de la periode comptable, l'integralite de la quote-part "
        "du lot est transferee du vendeur a l'acheteur. Les appels relatifs aux "
        "fonds permanents (reserve, augmentation roulement) deja appeles avant la "
        "vente restent a charge exclusive du vendeur, sans nouvel appel pour ce lot.</i>",
        rule,
    ))
    elements.append(Spacer(1, 4))
    futures = breakdown.get("future_calls") or []
    if not futures:
        elements.append(Paragraph(
            "<i>Aucun appel futur de provisions n'est enregistre pour la "
            "periode comptable restante.</i>",
            small,
        ))
    else:
        # iter85b fix : key = "amount" (et fallbacks). iter85b enhance : Paragraph
        # wrap + sous-totaux par trimestre.
        import re as _re

        def _trimester_label(fc_name: str, period_start: str) -> str:
            """Deduit T1/T2/T3/T4 depuis 'Trimestriel X/4 ...' ou period_start."""
            m = _re.search(r"trimestriel\s*(\d+)\s*/\s*4", (fc_name or "").lower())
            if m:
                return f"T{m.group(1)}"
            try:
                mon = datetime.strptime((period_start or "")[:10], "%Y-%m-%d").month
                return f"T{((mon - 1) // 3) + 1}"
            except Exception:
                return "Autres"

        bloc3_data = [["Appel", "Date appel", "Periode", "Quote-part lot"]]
        # Style cellule pour wrap automatique
        cell_left = ParagraphStyle("cell_left", parent=small, alignment=0, leading=10)
        cell_right = ParagraphStyle("cell_right", parent=small, alignment=2, leading=10)
        cell_bold = ParagraphStyle("cell_bold", parent=small, fontName="Helvetica-Bold",
                                     alignment=0, leading=10)
        cell_bold_right = ParagraphStyle("cell_bold_right", parent=small, fontName="Helvetica-Bold",
                                           alignment=2, leading=10)

        # Pre-tri : grouper par trimestre tout en preservant l'ordre chronologique
        grouped: dict = {}
        order: list = []
        for f in futures:
            tri = _trimester_label(f.get("fund_call_name", ""), f.get("period_start", ""))
            if tri not in grouped:
                grouped[tri] = []
                order.append(tri)
            grouped[tri].append(f)

        subtotal_row_indexes: list = []  # pour styling apres construction
        for tri in order:
            items = grouped[tri]
            sub_total = 0.0
            for f in items:
                amt = float(
                    f.get("amount", f.get("lot_amount", f.get("owner_amount", 0))) or 0
                )
                sub_total += amt
                bloc3_data.append([
                    Paragraph(f.get("fund_call_name", ""), cell_left),
                    Paragraph(_fmt_date(f.get("date", "")), cell_left),
                    Paragraph(
                        f"{_fmt_date(f.get('period_start',''))} -> {_fmt_date(f.get('period_end',''))}",
                        cell_left,
                    ),
                    Paragraph(_fmt_eur(amt), cell_right),
                ])
            # Sous-total trimestre (fond SLATE_100)
            bloc3_data.append([
                Paragraph(f"Sous-total {tri}", cell_bold),
                Paragraph("", cell_left),
                Paragraph("", cell_left),
                Paragraph(_fmt_eur(sub_total), cell_bold_right),
            ])
            subtotal_row_indexes.append(len(bloc3_data) - 1)

        # Ligne finale : sous-total general
        bloc3_data.append([
            Paragraph("", cell_left),
            Paragraph("", cell_left),
            Paragraph("Sous-total appels futurs", cell_bold),
            Paragraph(_fmt_eur(futures_total), cell_bold_right),
        ])

        bloc3_tbl = Table(bloc3_data, colWidths=[56 * mm, 24 * mm, 58 * mm, 40 * mm])
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.4, SLATE_300),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, -1), (-1, -1), BLUE_BG),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        # Styling des lignes sous-total trimestre
        for rid in subtotal_row_indexes:
            style_cmds.append(("BACKGROUND", (0, rid), (-1, rid), SLATE_100))
        bloc3_tbl.setStyle(TableStyle(style_cmds))
        elements.append(bloc3_tbl)

    # ----- MENTIONS LEGALES + SIGNATURES -----
    elements.append(Spacer(1, 12))
    elements.append(KeepTogether([
        Paragraph(
            "<b>Mentions legales</b><br/>"
            "Le present decompte est etabli conformement aux dispositions du Code "
            "civil belge relatives a la copropriete (art. 3.85 et suivants) et au "
            "plan comptable normalise des associations de coproprietaires (PCMN). "
            "Il sera joint a l'acte authentique de vente.",
            legal,
        ),
        Spacer(1, 14),
        Table(
            [
                [
                    Paragraph("<b>Pour le syndic</b><br/><br/><br/>"
                              "_______________________________<br/>"
                              "<font size=7>Nom, signature et cachet</font>",
                              small),
                    Paragraph("<b>Le vendeur</b><br/><br/><br/>"
                              "_______________________________<br/>"
                              f"<font size=7>{seller.get('name','')}</font>",
                              small),
                    Paragraph("<b>L'acheteur</b><br/><br/><br/>"
                              "_______________________________<br/>"
                              f"<font size=7>{buyer.get('name','')}</font>",
                              small),
                ],
            ],
            colWidths=[60 * mm, 58 * mm, 58 * mm],
        ),
    ]))

    if use_new_layout:
        footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", ""))
        doc.build(elements, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elements)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
