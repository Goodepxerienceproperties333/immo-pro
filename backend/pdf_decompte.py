"""Générateur PDF du décompte annuel propriétaire (PCMN belge).

Structure:
  - En-tête ACP (nom, BCE, adresse) + période (exercice)
  - Identité propriétaire + lots + VCS + quote-part globale
  - Tableau 1: Charges par clé de répartition (avec sous-totaux par clé)
  - Tableau 2: Appels de fonds (avec statut payé/dû)
  - Récap final: Total charges - Total appels = Solde
"""
import io
from datetime import datetime
from collections import defaultdict

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER


BRAND_BLUE = colors.HexColor("#0055FF")
LIGHT_GREY = colors.HexColor("#F1F5F9")
MID_GREY = colors.HexColor("#E2E8F0")
DARK_GREY = colors.HexColor("#475569")
RED = colors.HexColor("#DC2626")
GREEN = colors.HexColor("#16A34A")


def _eur(n: float) -> str:
    return f"{n:,.2f} EUR".replace(",", " ").replace(".", ",").replace(" ", ".").replace(",", " ", 1)
    # Belgian-style "1.234,56 EUR"


def _eur_be(n: float) -> str:
    """Format Belgian: 1.234,56 EUR (thousands dot, decimal comma)."""
    s = f"{n:,.2f}"  # "1,234.56"
    parts = s.split(".")
    int_part = parts[0].replace(",", ".")  # 1.234
    return f"{int_part},{parts[1]} EUR"


def build_decompte_pdf(
    owner: dict,
    copropriete: dict,
    fiscal_year: dict,
    owner_lots: list,
    all_lots: list,
    invoices: list,
    distribution_keys: list,
    fund_calls: list,
    payments: list,  # bank_transactions matched to owner
    expense_accounts_map: dict = None,  # {account_number: nature_name}
) -> bytes:
    """Render full PDF and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
        title=f"Decompte {owner.get('name','')} - {fiscal_year.get('name','')}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=18, leading=22,
                        textColor=DARK_GREY, alignment=TA_LEFT, fontName="Helvetica-Bold")
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, leading=14,
                        textColor=BRAND_BLUE, spaceBefore=8, spaceAfter=4, fontName="Helvetica-Bold")
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10,
                           textColor=DARK_GREY)
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)

    elements = []

    # ---- HEADER ----
    period_from = fiscal_year.get("start_date", "")
    period_to = fiscal_year.get("end_date", "")
    header_data = [
        [
            Paragraph(f"<b>{copropriete.get('name','')}</b>", body),
            Paragraph("<b>DECOMPTE ANNUEL DE CHARGES</b>", h1),
        ],
        [
            Paragraph(
                f"{copropriete.get('address','')}<br/>"
                f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
                f"BCE: {copropriete.get('bce','-')}<br/>"
                f"Ref: {copropriete.get('reference','')}",
                small,
            ),
            Paragraph(
                f"<b>Exercice:</b> {fiscal_year.get('name','')}<br/>"
                f"<b>Periode:</b> {period_from} au {period_to}<br/>"
                f"<b>Edite le:</b> {datetime.now().strftime('%d/%m/%Y')}",
                small,
            ),
        ],
    ]
    header_tbl = Table(header_data, colWidths=[90 * mm, 90 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, 1), 1.5, BRAND_BLUE),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 6 * mm))

    # ---- OWNER IDENTITY ----
    owner_lot_ids = {l["id"] for l in owner_lots}
    owner_quotity = sum(l.get("quotity", 0) for l in owner_lots)
    total_quotity = sum(l.get("quotity", 0) for l in all_lots) or 1
    share_pct = owner_quotity / total_quotity * 100

    lots_str = "<br/>".join(
        f"Lot <b>{l['number']}</b> - {l.get('description','')} ({l.get('quotity',0)} tantiemes)"
        for l in owner_lots
    ) or "(aucun lot)"

    owner_data = [
        [
            Paragraph("<b>PROPRIETAIRE</b>", small),
            Paragraph("<b>VOS LOTS</b>", small),
            Paragraph("<b>QUOTE-PART</b>", small),
        ],
        [
            Paragraph(
                f"<b>{owner.get('name','')}</b><br/>"
                f"{owner.get('address','')}<br/>"
                f"{owner.get('postal_code','')} {owner.get('city','')}<br/>"
                f"{owner.get('email','')}<br/>"
                f"<font color='#0055FF'><b>VCS:</b> {owner.get('vcs_code','-')}</font>",
                body,
            ),
            Paragraph(lots_str, body),
            Paragraph(
                f"<font size=18 color='#0055FF'><b>{share_pct:.2f}%</b></font><br/>"
                f"<font size=8 color='#475569'>{owner_quotity:.0f} / {total_quotity:.0f}<br/>tantiemes</font>",
                body,
            ),
        ],
    ]
    owner_tbl = Table(owner_data, colWidths=[70 * mm, 70 * mm, 40 * mm])
    owner_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, MID_GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, MID_GREY),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(owner_tbl)
    elements.append(Spacer(1, 8 * mm))

    # ---- CHARGES BY DISTRIBUTION KEY ----
    elements.append(Paragraph("1. Charges reparties par cle de repartition et nature de depense", h2))

    dk_by_id = {dk["id"]: dk for dk in distribution_keys}
    # Group: distribution_key_id -> account_number -> [(invoice, owner_amt)]
    grouped = defaultdict(lambda: defaultdict(list))
    for inv in invoices:
        dlines = inv.get("distribution_lines", [])
        owner_amt = 0.0
        if dlines:
            for dl in dlines:
                if dl.get("lot_id") in owner_lot_ids:
                    owner_amt += dl.get("amount", 0)
        if owner_amt <= 0:
            continue
        key_id = inv.get("distribution_key_id", "") or "_none"
        acc = inv.get("account_number", "") or "_other"
        grouped[key_id][acc].append((inv, owner_amt))

    # Build account_name lookup (from PCMN + categories if provided)
    acc_names = expense_accounts_map or {}

    total_owner_charges = 0.0
    if not grouped:
        elements.append(Paragraph("<i>Aucune charge n'affecte vos lots sur cette periode.</i>", body))
    else:
        for key_id, by_acc in grouped.items():
            dk_name = dk_by_id.get(key_id, {}).get("name", "Sans cle de repartition")
            # Niveau 1 : Cle
            elements.append(Paragraph(f"<b>Cle : {dk_name}</b>", body))
            key_subtotal = 0.0
            for acc, items in by_acc.items():
                acc_label = acc if acc != "_other" else "Autres"
                nature_label = acc_names.get(acc, "")
                # Niveau 2 : Nature de depense / compte PCMN
                header = f"<b>Nature :</b> {nature_label} <font color='#94a3b8'>(compte {acc_label})</font>" if nature_label else f"<b>Compte :</b> {acc_label}"
                elements.append(Paragraph(header, ParagraphStyle(name="nature", parent=body, leftIndent=10, fontSize=8, textColor=DARK_GREY)))
                rows = [["Date valeur", "Fournisseur", "Libelle", "Ref. interne", "Montant TVAC", "Votre quote-part"]]
                subtotal = 0.0
                for inv, owner_amt in items:
                    rows.append([
                        inv.get("date", ""),
                        inv.get("supplier", "")[:30],
                        inv.get("description", "")[:40],
                        inv.get("number", ""),
                        _eur_be(inv.get("total_amount", 0)),
                        _eur_be(owner_amt),
                    ])
                    subtotal += owner_amt
                rows.append(["", "", "", "", "Sous-total nature", _eur_be(subtotal)])
                tbl = Table(rows, colWidths=[20 * mm, 33 * mm, 47 * mm, 22 * mm, 27 * mm, 27 * mm])
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (4, 0), (5, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.3, MID_GREY),
                    ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
                    ("FONTNAME", (4, -1), (5, -1), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                elements.append(tbl)
                elements.append(Spacer(1, 2 * mm))
                key_subtotal += subtotal
            # Sous-total par cle (niveau 1)
            key_total_tbl = Table(
                [["", "", "", "", f"Sous-total cle '{dk_name}'", _eur_be(key_subtotal)]],
                colWidths=[20 * mm, 33 * mm, 47 * mm, 22 * mm, 27 * mm, 27 * mm],
            )
            key_total_tbl.setStyle(TableStyle([
                ("BACKGROUND", (4, 0), (5, 0), MID_GREY),
                ("FONTNAME", (4, 0), (5, 0), "Helvetica-Bold"),
                ("FONTSIZE", (4, 0), (5, 0), 8),
                ("ALIGN", (4, 0), (5, 0), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(key_total_tbl)
            elements.append(Spacer(1, 4 * mm))
            total_owner_charges += key_subtotal

        # Grand total charges
        grand_total = Table(
            [["", "", "", "", "TOTAL CHARGES", _eur_be(total_owner_charges)]],
            colWidths=[22 * mm, 35 * mm, 50 * mm, 22 * mm, 25 * mm, 25 * mm],
        )
        grand_total.setStyle(TableStyle([
            ("BACKGROUND", (4, 0), (5, 0), DARK_GREY),
            ("TEXTCOLOR", (4, 0), (5, 0), colors.white),
            ("FONTNAME", (4, 0), (5, 0), "Helvetica-Bold"),
            ("FONTSIZE", (4, 0), (5, 0), 9),
            ("ALIGN", (4, 0), (5, 0), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(grand_total)

    elements.append(Spacer(1, 8 * mm))

    # ---- FUND CALLS ----
    elements.append(Paragraph("2. Appels de fonds (provisions)", h2))
    fc_rows = [["Date", "Appel", "Type", "Echeance", "Montant", "Statut"]]
    total_called = 0.0
    total_paid_calls = 0.0
    for fc in fund_calls:
        share = next((d for d in fc.get("distribution", []) if d.get("owner_id") == owner["id"]), None)
        if not share:
            continue
        amt = share.get("amount", 0)
        paid = share.get("paid", False)
        fc_rows.append([
            fc.get("date", ""),
            fc.get("name", ""),
            fc.get("call_type", "provisions"),
            fc.get("due_date", ""),
            _eur_be(amt),
            "PAYE" if paid else "DU",
        ])
        total_called += amt
        if paid:
            total_paid_calls += amt

    if len(fc_rows) > 1:
        fc_rows.append(["", "", "", "TOTAL APPELE", _eur_be(total_called), ""])
        fc_tbl = Table(fc_rows, colWidths=[22 * mm, 50 * mm, 25 * mm, 25 * mm, 30 * mm, 27 * mm])
        fc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("ALIGN", (5, 0), (5, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.3, MID_GREY),
            ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
            ("FONTNAME", (3, -1), (4, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(fc_tbl)
    else:
        elements.append(Paragraph("<i>Aucun appel de fonds sur cette periode.</i>", body))

    elements.append(Spacer(1, 6 * mm))

    # ---- PAYMENTS ----
    if payments:
        elements.append(Paragraph("3. Paiements recus", h2))
        pay_rows = [["Date", "Communication", "Reference", "Montant"]]
        total_payments = 0.0
        for p in payments:
            amt = abs(p.get("amount", 0))
            pay_rows.append([
                p.get("date", ""),
                p.get("communication", "") or p.get("counterparty_name", ""),
                p.get("id", "")[:8],
                _eur_be(amt),
            ])
            total_payments += amt
        pay_rows.append(["", "", "TOTAL PAYE", _eur_be(total_payments)])
        pay_tbl = Table(pay_rows, colWidths=[22 * mm, 70 * mm, 50 * mm, 37 * mm])
        pay_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.3, MID_GREY),
            ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
            ("FONTNAME", (2, -1), (3, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(pay_tbl)
        elements.append(Spacer(1, 6 * mm))
    else:
        total_payments = 0.0

    # ---- FINAL SUMMARY ----
    elements.append(Paragraph("4. Recapitulatif et solde", h2))
    balance = round(total_owner_charges - total_payments, 2)
    balance_color = RED if balance > 0.01 else (GREEN if balance < -0.01 else DARK_GREY)
    balance_label = "A VERSER" if balance > 0.01 else ("A REMBOURSER" if balance < -0.01 else "EQUILIBRE")

    summary_data = [
        ["Total charges affectees a vos lots", _eur_be(total_owner_charges)],
        ["Total paiements recus", _eur_be(total_payments)],
        ["", ""],  # spacer row
        [Paragraph(f"<b>SOLDE ({balance_label})</b>", body),
         Paragraph(f"<font color='{balance_color.hexval()}' size=14><b>{_eur_be(abs(balance))}</b></font>", body)],
    ]
    sum_tbl = Table(summary_data, colWidths=[120 * mm, 60 * mm])
    sum_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, MID_GREY),
        ("LINEABOVE", (0, 3), (-1, 3), 1.5, DARK_GREY),
        ("BACKGROUND", (0, 3), (-1, 3), LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(sum_tbl)

    if balance > 0.01 and owner.get("vcs_code"):
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph(
            f"<b>Versement par virement bancaire</b><br/>"
            f"Communication structuree obligatoire: <font color='#0055FF'><b>{owner['vcs_code']}</b></font>",
            small,
        ))

    # ---- FOOTER ----
    elements.append(Spacer(1, 12 * mm))
    elements.append(Paragraph(
        f"<i>Decompte etabli conformement a la loi belge sur la copropriete (art. 577-3 et suivants du Code civil). "
        f"Toute contestation doit etre formulee par ecrit au syndic dans les 30 jours suivant reception.</i>",
        ParagraphStyle("footer", parent=small, alignment=TA_CENTER, fontSize=7, textColor=DARK_GREY),
    ))

    doc.build(elements)
    buf.seek(0)
    return buf.read()
