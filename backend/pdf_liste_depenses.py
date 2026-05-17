"""Generateur PDF 'Liste des depenses' (PCMN belge - format Syndic).

Structure (modele utilisateur Finlead) :
  - En-tete societe + ACP + periode + "fait le"
  - Tableau avec colonnes :
      Date valeur | Libelle | Fournisseur | Ref. interne | Montant | Part proprietaire | Part occupant
  - Groupement hierarchique :
      Cle de repartition
        -> Nature de depense (compte PCMN en italique)
            -> Lignes de detail
            -> Sous-total nature
        -> Sous-total cle
  - Ligne finale : Totaux generaux immeuble

Note : "Part occupant" reflete les charges locatives recuperables si modelisees,
sinon 0,00. "Part proprietaire" = montant total moins part occupant.
"""
import io
from datetime import datetime
from collections import defaultdict

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER


BRAND_BLUE = colors.HexColor("#0055FF")
LIGHT_GREY = colors.HexColor("#F1F5F9")
MID_GREY = colors.HexColor("#E2E8F0")
DARK_GREY = colors.HexColor("#475569")


def _eur_be(n: float) -> str:
    """Format belge : 1.234,56."""
    s = f"{n:,.2f}"
    parts = s.split(".")
    int_part = parts[0].replace(",", ".")
    return f"{int_part},{parts[1]}"


def _add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(DARK_GREY)
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[1] / 2, 10 * mm, f"{page_num}")
    canvas.restoreState()


def build_liste_depenses_pdf(
    copropriete: dict,
    date_from: str,
    date_to: str,
    invoices: list,            # already filtered & in period
    distribution_keys: list,
    pcmn_map: dict,            # {account_number: account_name}
    expense_categories: list,  # natures de depense
) -> bytes:
    """Render le PDF 'Liste des depenses' en bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
        title=f"Liste des depenses {date_from} au {date_to}",
    )

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], fontSize=15, leading=18,
                              textColor=DARK_GREY, alignment=TA_CENTER, fontName="Helvetica-Bold")
    h_period = ParagraphStyle("h_period", parent=styles["Normal"], fontSize=10, leading=12,
                               alignment=TA_CENTER, textColor=DARK_GREY)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7, leading=9,
                            textColor=DARK_GREY)
    key_lbl = ParagraphStyle("key_lbl", parent=body, fontSize=10, leading=12,
                              textColor=BRAND_BLUE, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=2)
    nat_lbl = ParagraphStyle("nat_lbl", parent=body, fontSize=8, leading=10,
                              textColor=DARK_GREY, fontName="Helvetica-Bold", leftIndent=4, spaceBefore=2)
    cpt_lbl = ParagraphStyle("cpt_lbl", parent=body, fontSize=7, leading=9,
                              textColor=DARK_GREY, fontName="Helvetica-Oblique", leftIndent=10)

    elements = []

    # ---- HEADER ----
    df = _fmt_date(date_from)
    dt = _fmt_date(date_to)
    header = [[
        Paragraph(
            f"<b>{copropriete.get('name','')}</b><br/>"
            f"{copropriete.get('address','')}<br/>"
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
            f"BCE: {copropriete.get('bce','-')} - Ref: {copropriete.get('reference','')}",
            small,
        ),
        Paragraph("<b>LISTE DES DEPENSES</b>", h_title),
        Paragraph(
            f"<b>FAIT LE :</b><br/>{datetime.now().strftime('%d/%m/%Y')}<br/><br/>"
            f"<b>IMMEUBLE :</b><br/>{copropriete.get('reference','')} - {copropriete.get('name','')[:35]}",
            small,
        ),
    ]]
    htbl = Table(header, colWidths=[75 * mm, 130 * mm, 65 * mm])
    htbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, BRAND_BLUE),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ]))
    elements.append(htbl)
    elements.append(Paragraph(f"DU {df} AU {dt}", h_period))
    elements.append(Spacer(1, 5 * mm))

    # ---- BUILD HIERARCHY ----
    dk_by_id = {dk["id"]: dk for dk in distribution_keys}
    # nature_by_acc: {account_number: nature_name}
    nature_by_acc = {}
    nature_id_by_acc = {}
    for c in expense_categories:
        acc = c.get("account_number", "")
        if acc:
            nature_by_acc[acc] = c.get("name", "")
            nature_id_by_acc[acc] = c.get("id", "")

    # Group: key_id -> account_number -> [invoice...]
    grouped = defaultdict(lambda: defaultdict(list))
    for inv in invoices:
        key_id = inv.get("distribution_key_id", "") or "_none"
        acc = inv.get("account_number", "") or "_other"
        grouped[key_id][acc].append(inv)

    # Build table data
    col_widths = [22 * mm, 70 * mm, 45 * mm, 22 * mm, 27 * mm, 27 * mm, 27 * mm]
    headers = ["Date valeur", "Libelle", "Fournisseur", "Ref. interne",
               "Montant", "Part proprietaire", "Part occupant"]

    grand_total = 0.0
    grand_propr = 0.0
    grand_occ = 0.0

    # Helper to push a column-headered sub-table
    def _push_header_row():
        return [Paragraph(f"<b>{h}</b>", small) for h in headers]

    # We'll create ONE big Table per Cle for repeated headers, but to keep
    # subtotal styling clean, we use multiple Tables: 1 per (cle, account).
    # Each cle starts with key label, then for each account: nature label + table.
    for key_id, by_acc in sorted(grouped.items(), key=lambda x: dk_by_id.get(x[0], {}).get("name", "")):
        dk = dk_by_id.get(key_id, {})
        dk_name = dk.get("name", "Sans cle")
        elements.append(Paragraph(f"Cle : {dk.get('code','') or '----'} - {dk_name}", key_lbl))

        key_total = 0.0
        key_propr = 0.0
        key_occ = 0.0

        for acc, items in sorted(by_acc.items()):
            acc_label = acc if acc != "_other" else "(sans compte)"
            acc_name = pcmn_map.get(acc, "")
            nature_name = nature_by_acc.get(acc, "")
            # Nature heading
            if nature_name:
                elements.append(Paragraph(f"Nature : {nature_name}", nat_lbl))
            # Compte (italic) - rappel
            elements.append(Paragraph(f"Compte : {acc_label} - {acc_name}", cpt_lbl))

            # Build rows
            rows = [_push_header_row()]
            acc_total = 0.0
            acc_propr = 0.0
            acc_occ = 0.0
            for inv in sorted(items, key=lambda x: x.get("date", "")):
                amount = float(inv.get("total_amount", 0) or 0)
                # Part proprietaire/occupant : si pas modelise, tout en proprietaire
                part_occ = float(inv.get("part_occupant", 0) or 0)
                part_propr = amount - part_occ
                rows.append([
                    _fmt_date(inv.get("date", "")),
                    (inv.get("description", "") or "")[:50],
                    (inv.get("supplier", "") or "")[:25],
                    inv.get("number", "") or "",
                    _eur_be(amount),
                    _eur_be(part_propr),
                    _eur_be(part_occ),
                ])
                acc_total += amount
                acc_propr += part_propr
                acc_occ += part_occ

            # Sous-total nature
            rows.append([
                "", "", "", Paragraph("<b>Sous-total nature</b>", small),
                Paragraph(f"<b>{_eur_be(acc_total)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_propr)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_occ)}</b>", small),
            ])

            tbl = Table(rows, colWidths=col_widths, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), MID_GREY),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("ALIGN", (4, 0), (6, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, MID_GREY),
                ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]))
            elements.append(tbl)
            elements.append(Spacer(1, 1.5 * mm))

            key_total += acc_total
            key_propr += acc_propr
            key_occ += acc_occ

        # Sous-total cle
        key_tbl = Table(
            [[
                "", "", "", Paragraph(f"<b>Sous-total cle {dk_name}</b>", body),
                Paragraph(f"<b>{_eur_be(key_total)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_propr)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_occ)}</b>", body),
            ]],
            colWidths=col_widths,
        )
        key_tbl.setStyle(TableStyle([
            ("BACKGROUND", (3, 0), (-1, 0), colors.HexColor("#DDE4EE")),
            ("ALIGN", (4, 0), (6, 0), "RIGHT"),
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, BRAND_BLUE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(key_tbl)
        elements.append(Spacer(1, 4 * mm))

        grand_total += key_total
        grand_propr += key_propr
        grand_occ += key_occ

    # ---- TOTAUX GENERAUX ----
    if grouped:
        gt = Table(
            [[
                "", "", "", Paragraph("<b>Totaux generaux immeuble :</b>", body),
                Paragraph(f"<b>{_eur_be(grand_total)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_propr)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_occ)}</b>", body),
            ]],
            colWidths=col_widths,
        )
        gt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DARK_GREY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (4, 0), (6, 0), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(gt)
    else:
        elements.append(Paragraph(
            "<i>Aucune depense enregistree sur la periode.</i>", body
        ))

    doc.build(elements, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
    buf.seek(0)
    return buf.read()


def _fmt_date(iso: str) -> str:
    """Convert YYYY-MM-DD to DD/MM/YYYY (silently passthrough on error)."""
    if not iso or len(iso) < 10:
        return iso or ""
    try:
        return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"
    except Exception:
        return iso
