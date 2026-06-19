"""Generateur PDF du decompte annuel proprietaire - VERSION SIMPLIFIEE
Optimise pour LECTURE par des proprietaires non-comptables :
- Pas de jargon technique (Debit/Credit -> "Votre quote-part" / "Vous avez deja paye")
- Dates au format jj/mm/aaaa
- Libelles parlants, mise en page aeree
- Carte recap visible avec solde colore (rouge=a payer / vert=en votre faveur)
- Detail des charges groupees par nature de depense (lisible)
- Instructions de paiement claires (IBAN + communication structuree)
"""
from io import BytesIO
from datetime import datetime
from collections import defaultdict
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
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


def _addr_block(name, parts):
    lines = [name] + [p for p in parts if p]
    return "<br/>".join(lines)


def build_decompte_pdf(
    owner: dict,
    copropriete: dict,
    fiscal_year: dict,
    owner_lots: list,
    all_lots: list,
    invoices: list,
    distribution_keys: list,
    fund_calls: list,
    payments: list,
    expense_accounts_map: dict = None,
) -> bytes:
    """Genere le PDF Decompte annuel pour un proprietaire."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=f"Decompte annuel - {owner.get('name','')} - {fiscal_year.get('name','')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=20, leading=24, textColor=BRAND,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["BodyText"],
        fontSize=10, leading=13, textColor=SLATE_500, spaceAfter=4,
    )
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=15, textColor=SLATE_900,
                        spaceAfter=4, fontName="Helvetica-Bold")
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8.5, leading=11, textColor=SLATE_500)
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9.5, leading=12.5)
    right = ParagraphStyle("right", parent=body, alignment=2)

    elems = []

    # ---- HEADER : ACP block ----
    acp_lines = _addr_block(
        f"<b>{copropriete.get('name','')}</b>",
        [
            copropriete.get("address", ""),
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}".strip(),
            f"BCE : {copropriete.get('bce','')}" if copropriete.get('bce') else "",
        ],
    )
    period_str = (
        f"Exercice <b>{fiscal_year.get('name','')}</b><br/>"
        f"Periode du <b>{_fmt_date(fiscal_year.get('start_date',''))}</b> "
        f"au <b>{_fmt_date(fiscal_year.get('end_date',''))}</b><br/>"
        f"Edite le <b>{datetime.now().strftime('%d/%m/%Y')}</b>"
    )
    header_tbl = Table(
        [[Paragraph(acp_lines, small), Paragraph(period_str, small)]],
        colWidths=[90 * mm, 90 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(header_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- TITLE ----
    elems.append(Paragraph("Decompte annuel de vos charges", title_style))
    elems.append(Paragraph(
        "Recapitulatif des charges qui vous concernent et de vos versements pendant l'exercice.",
        sub_style,
    ))
    elems.append(Spacer(1, 4 * mm))

    # ---- DESTINATAIRE BLOCK ----
    owner_addr = _addr_block(
        f"<b>{owner.get('name','')}</b>",
        [
            owner.get("address", ""),
            f"{owner.get('postal_code','')} {owner.get('city','')}".strip(),
            owner.get("country", ""),
        ],
    )
    owner_lot_ids = {l["id"] for l in owner_lots}
    owner_quotity = sum(l.get("quotity", 0) for l in owner_lots)
    total_quotity = sum(l.get("quotity", 0) for l in all_lots) or 1
    share_pct = owner_quotity / total_quotity * 100
    lots_str = "<br/>".join(
        f"Lot <b>{l.get('number','')}</b> - {l.get('description','')} ({l.get('quotity',0):.0f} tantiemes)"
        for l in owner_lots
    ) or "(aucun lot)"

    info_lines = [
        f"<b>Vos lots :</b><br/>{lots_str}",
        "&nbsp;",
        f"<b>Votre quote-part globale :</b> "
        f"<font color='{BRAND.hexval()}'><b>{share_pct:.2f}%</b></font> "
        f"<font size='8' color='#64748B'>({owner_quotity:.0f}/{total_quotity:.0f} tantiemes)</font>",
        "&nbsp;",
        f"<b>Reference communication :</b> "
        f"<font color='{BRAND.hexval()}'><b>{owner.get('vcs_code','')}</b></font>",
    ]
    dest_tbl = Table(
        [[Paragraph(owner_addr, body), Paragraph("<br/>".join(info_lines), small)]],
        colWidths=[90 * mm, 90 * mm],
    )
    dest_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    elems.append(dest_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- COMPUTE TOTALS ----
    # Group invoices by distribution_key -> account -> [(inv, owner_amount)]
    grouped = defaultdict(lambda: defaultdict(list))
    total_owner_charges = 0.0
    for inv in invoices:
        owner_amt = 0.0
        for dl in inv.get("distribution_lines", []) or []:
            if dl.get("lot_id") in owner_lot_ids:
                owner_amt += float(dl.get("amount", 0) or 0)
        if owner_amt <= 0.001:
            continue
        key_id = inv.get("distribution_key_id", "") or "_none"
        acc = inv.get("account_number", "") or "_other"
        grouped[key_id][acc].append((inv, owner_amt))
        total_owner_charges += owner_amt

    # Owner share of fund calls + payments
    total_called = 0.0
    fund_calls_owner = []
    for fc in fund_calls:
        share = next((d for d in fc.get("distribution", []) if d.get("owner_id") == owner["id"]), None)
        if not share:
            continue
        amt = float(share.get("amount", 0) or 0)
        total_called += amt
        fund_calls_owner.append({
            "date": fc.get("date", ""),
            "name": fc.get("name", ""),
            "call_type": fc.get("call_type", "provisions"),
            "due_date": fc.get("due_date", ""),
            "amount": amt,
            "paid": share.get("paid", False),
            "paid_date": share.get("paid_date", ""),
        })

    total_payments = sum(abs(float(p.get("amount", 0) or 0)) for p in payments)
    # Solde = Charges - Paiements (positif = doit payer)
    balance = round(total_owner_charges - total_payments, 2)

    # ---- SUMMARY CARD ----
    sold_color = RED if balance > 0.01 else (GREEN if balance < -0.01 else SLATE_500)
    sold_label = "RESTE A REGLER" if balance > 0.01 else (
        "EN VOTRE FAVEUR" if balance < -0.01 else "EQUILIBRE"
    )
    bg_summary = RED_BG if balance > 0.01 else (GREEN_BG if balance < -0.01 else SLATE_50)

    summary_rows = [
        [Paragraph("<b>Vos charges sur la periode</b>", body),
         Paragraph(_fmt_eur(total_owner_charges), right)],
        [Paragraph("<b>Total appele par le syndic</b>", body),
         Paragraph(_fmt_eur(total_called), right)],
        [Paragraph("<b>Vos versements pendant la periode</b>", body),
         Paragraph(
             f"<font color='{GREEN.hexval()}'>{_fmt_eur(total_payments)}</font>", right)],
        [Paragraph(f"<b><font size='12'>{sold_label}</font></b>", body),
         Paragraph(
             f"<b><font size='16' color='{sold_color.hexval()}'>{_fmt_eur(abs(balance))}</font></b>",
             right)],
    ]
    summary_tbl = Table(summary_rows, colWidths=[110 * mm, 70 * mm])
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_summary),
        ("BOX", (0, 0), (-1, -1), 1.2, sold_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEABOVE", (0, -1), (-1, -1), 1, sold_color),
        ("TOPPADDING", (0, -1), (-1, -1), 11),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 11),
    ]))
    elems.append(summary_tbl)
    elems.append(Spacer(1, 8 * mm))

    # ---- 1. DETAIL DES CHARGES ----
    elems.append(Paragraph("1. Detail de vos charges", h2))
    elems.append(Paragraph(
        "Voici les depenses de la copropriete et la part qui vous est repartie selon les cles applicables.",
        sub_style,
    ))
    elems.append(Spacer(1, 2 * mm))

    dk_by_id = {dk["id"]: dk for dk in distribution_keys}
    acc_names = expense_accounts_map or {}

    if not grouped:
        elems.append(Paragraph(
            "<i>Aucune charge ne vous concerne sur cette periode.</i>", body))
    else:
        for key_id, by_acc in grouped.items():
            dk_name = dk_by_id.get(key_id, {}).get("name", "Tantiemes")
            key_subtotal = 0.0
            # Sub-header per cle
            elems.append(Spacer(1, 2 * mm))
            elems.append(Paragraph(
                f"<b>Cle de repartition : {dk_name}</b>",
                ParagraphStyle("dkname", parent=body, textColor=BRAND, fontSize=10),
            ))

            for acc, items in by_acc.items():
                nature_label = acc_names.get(acc, "") or (acc if acc != "_other" else "Autres charges")
                rows = [["Date", "Fournisseur", "Description", "Total facture", "Votre part"]]
                # Style pour libelles wrappables
                cell_style = ParagraphStyle(
                    "cell", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
                )
                subtotal = 0.0
                for inv, owner_amt in items:
                    rows.append([
                        _fmt_date(inv.get("date", "")),
                        Paragraph(inv.get("supplier", "") or "", cell_style),
                        Paragraph(inv.get("description", "") or "", cell_style),
                        _fmt_eur(inv.get("total_amount", 0)),
                        _fmt_eur(owner_amt),
                    ])
                    subtotal += owner_amt
                rows.append(["", "", "", "Sous-total", _fmt_eur(subtotal)])

                # Section title
                elems.append(Spacer(1, 1 * mm))
                elems.append(Paragraph(
                    f"<b>{nature_label}</b>",
                    ParagraphStyle("nat", parent=body, fontSize=9.5, textColor=SLATE_900,
                                   leftIndent=4),
                ))
                tbl = Table(rows, colWidths=[22 * mm, 40 * mm, 50 * mm, 30 * mm, 28 * mm])
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), SLATE_100),
                    ("TEXTCOLOR", (0, 0), (-1, 0), SLATE_900),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (3, 0), (4, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2),
                     [colors.white, SLATE_50]),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, SLATE_300),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.5, SLATE_300),
                    ("BACKGROUND", (3, -1), (4, -1), SLATE_100),
                    ("FONTNAME", (3, -1), (4, -1), "Helvetica-Bold"),
                    ("TEXTCOLOR", (4, -1), (4, -1), BRAND),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]))
                elems.append(tbl)
                key_subtotal += subtotal

            # Subtotal per cle
            sub_tbl = Table(
                [[f"Total cle '{dk_name}'", _fmt_eur(key_subtotal)]],
                colWidths=[142 * mm, 28 * mm],
            )
            sub_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SLATE_100),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (1, 0), (1, 0), BRAND),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            elems.append(Spacer(1, 1 * mm))
            elems.append(sub_tbl)
            elems.append(Spacer(1, 3 * mm))

        # Grand total
        grand_tbl = Table(
            [["TOTAL DE VOS CHARGES", _fmt_eur(total_owner_charges)]],
            colWidths=[142 * mm, 28 * mm],
        )
        grand_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SLATE_900),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        elems.append(grand_tbl)

    elems.append(Spacer(1, 8 * mm))

    # ---- 2. APPELS DE FONDS ----
    elems.append(Paragraph("2. Vos appels de fonds", h2))
    elems.append(Paragraph(
        "Les appels que le syndic vous a adresses pendant la periode.",
        sub_style,
    ))
    elems.append(Spacer(1, 2 * mm))

    if not fund_calls_owner:
        elems.append(Paragraph(
            "<i>Aucun appel de fonds sur cette periode.</i>", body))
    else:
        type_label = {
            "provisions": "Provisions",
            "reserve": "Fonds de reserve",
            "roulement": "Fonds de roulement",
            "special": "Appel special",
        }
        fc_rows = [["Date", "Libelle", "Type", "Echeance", "Montant", "Statut"]]
        fc_cell = ParagraphStyle(
            "fc", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
        )
        for fc in fund_calls_owner:
            fc_rows.append([
                _fmt_date(fc["date"]),
                Paragraph(fc["name"] or "", fc_cell),
                type_label.get(fc["call_type"], fc["call_type"]),
                _fmt_date(fc["due_date"]),
                _fmt_eur(fc["amount"]),
                ("Paye " + _fmt_date(fc["paid_date"])) if fc["paid"] else "A payer",
            ])
        fc_rows.append(["", "", "", "TOTAL APPELE", _fmt_eur(total_called), ""])

        fc_tbl = Table(fc_rows, colWidths=[20 * mm, 50 * mm, 25 * mm, 22 * mm, 28 * mm, 25 * mm])
        fc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("ALIGN", (5, 0), (5, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (3, -1), (4, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (4, -1), (4, -1), BRAND),
            ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(fc_tbl)

    elems.append(Spacer(1, 8 * mm))

    # ---- 3. PAIEMENTS ----
    if payments:
        elems.append(Paragraph("3. Vos paiements", h2))
        elems.append(Paragraph(
            "Les versements recus par la copropriete et associes a votre compte.",
            sub_style,
        ))
        elems.append(Spacer(1, 2 * mm))
        pay_rows = [["Date", "Communication", "Montant"]]
        pay_cell = ParagraphStyle(
            "pay", parent=body, fontSize=8.5, leading=10.5, wordWrap="CJK",
        )
        for p in payments:
            amt = abs(float(p.get("amount", 0) or 0))
            pay_rows.append([
                _fmt_date(p.get("date", "")),
                Paragraph(p.get("communication", "") or p.get("counterparty_name", "") or "", pay_cell),
                _fmt_eur(amt),
            ])
        pay_rows.append(["", "TOTAL VERSE", _fmt_eur(total_payments)])
        pay_tbl = Table(pay_rows, colWidths=[22 * mm, 122 * mm, 26 * mm])
        pay_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2),
             [colors.white, SLATE_50]),
            ("BACKGROUND", (0, -1), (-1, -1), SLATE_100),
            ("FONTNAME", (1, -1), (2, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (2, -1), (2, -1), GREEN),
            ("LINEABOVE", (0, -1), (-1, -1), 1, GREEN),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(pay_tbl)
        elems.append(Spacer(1, 8 * mm))

    # ---- 4. MODALITES DE PAIEMENT ----
    iban = ""
    bic = ""
    for ba in copropriete.get("bank_accounts", []) or []:
        if ba.get("iban"):
            iban = ba["iban"]
            bic = ba.get("bic", "")
            break

    if balance > 0.01:
        rib_lines = [
            "<font size='11'><b>Comment regler ce solde ?</b></font>",
            "&nbsp;",
            f"Veuillez verser <b>{_fmt_eur(balance)}</b> sur le compte :",
            f"<b>IBAN :</b> <font name='Courier'>{iban or '—'}</font>",
            f"<b>BIC :</b> <font name='Courier'>{bic or '—'}</font>",
            f"<b>Beneficiaire :</b> {copropriete.get('name','')}",
            "&nbsp;",
            "<b>Communication structuree (obligatoire) :</b>",
            f"<font color='{BRAND.hexval()}' size='13'><b>{owner.get('vcs_code','')}</b></font>",
            "&nbsp;",
            "<font size='8' color='#64748B'><i>Indiquez imperativement cette communication "
            "structuree afin que votre paiement soit reconnu automatiquement et impute "
            "sur votre compte.</i></font>",
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
    elif balance < -0.01:
        msg = Paragraph(
            f"<font size='11'><b>Solde en votre faveur : {_fmt_eur(abs(balance))}</b></font><br/>"
            "&nbsp;<br/>"
            "Ce solde sera deduit de votre prochain appel de fonds. "
            "Pour obtenir le remboursement par virement, transmettez votre IBAN au syndic.",
            body,
        )
        msg_tbl = Table([[msg]], colWidths=[180 * mm])
        msg_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
            ("BOX", (0, 0), (-1, -1), 1, GREEN),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        elems.append(msg_tbl)
        elems.append(Spacer(1, 6 * mm))

    # ---- FOOTER ----
    elems.append(Spacer(1, 4 * mm))
    elems.append(Paragraph(
        f"<font size='7.5' color='#94A3B8'>"
        f"<i>Decompte etabli conformement a la loi belge sur la copropriete "
        f"(art. 3.86 et suivants du Code civil). Toute contestation doit etre formulee "
        f"par ecrit au syndic dans les 30 jours suivant reception.</i>"
        f"<br/>Document edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')} - "
        f"{owner.get('name','')} - {copropriete.get('name','')}.</font>",
        small,
    ))

    doc.build(elems)
    buf.seek(0)
    return buf.read()
