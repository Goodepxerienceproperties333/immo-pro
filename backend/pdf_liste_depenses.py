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
    invoices: list,            # iter90e : already-expanded rows from compute_expense_rows()
                               # Each row has: total_amount (TVAC), vat_amount (TVA),
                               # account_number, distribution_key_id, expense_category_name,
                               # supplier, description, number, date, occupant_amount,
                               # proprietaire_amount.
    distribution_keys: list,
    pcmn_map: dict,            # {account_number: account_name}
    expense_categories: list,  # natures de depense
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Render le PDF 'Liste des depenses' en bytes. iter90e : aligned with
    /api/fiscal/expenses (HTVA + TVA + TVAC + parts proprio/occupant).

    iter90dj : `syndic_pdf_ctx` (optionnel) ajoute logo cabinet + pied de
    page legal avec numeros de page sur toutes les pages.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=14 * mm,
        bottomMargin=28 * mm if use_new_layout else 14 * mm,
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

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elements.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            syndic_pdf_ctx.get("syndic_config") or {},
            small,
        ))
        elements.append(Spacer(1, 3 * mm))

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
    # iter90e : colonnes HTVA + TVA + TVAC + Part proprio + Part occupant
    col_widths = [18 * mm, 60 * mm, 36 * mm, 20 * mm,
                  22 * mm, 18 * mm, 22 * mm, 22 * mm, 22 * mm]
    headers = ["Date valeur", "Libelle", "Fournisseur", "Ref. interne",
               "HTVA", "TVA", "TVAC", "Part prop.", "Part occ."]

    grand_total = 0.0
    grand_htva = 0.0
    grand_vat = 0.0
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
        key_htva = 0.0
        key_vat = 0.0
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
            acc_htva = 0.0
            acc_vat = 0.0
            acc_propr = 0.0
            acc_occ = 0.0
            for inv in sorted(items, key=lambda x: x.get("date", "")):
                tvac = float(inv.get("total_amount", 0) or 0)
                tva = float(inv.get("vat_amount", 0) or 0)
                htva = round(tvac - tva, 2)
                # iter90e : la vue UI fournit deja occupant_amount / proprietaire_amount,
                # qu'on doit privilegier sur l'ancien `part_occupant` (deprecated).
                if inv.get("occupant_amount") is not None or inv.get("proprietaire_amount") is not None:
                    part_occ = float(inv.get("occupant_amount", 0) or 0)
                    part_propr = float(inv.get("proprietaire_amount", 0) or 0)
                else:
                    part_occ = float(inv.get("part_occupant", 0) or 0)
                    part_propr = tvac - part_occ
                rows.append([
                    _fmt_date(inv.get("date", "")),
                    (inv.get("description", "") or "")[:45],
                    (inv.get("supplier", "") or "")[:20],
                    (inv.get("number", "") or "")[:12],
                    _eur_be(htva),
                    _eur_be(tva),
                    _eur_be(tvac),
                    _eur_be(part_propr),
                    _eur_be(part_occ),
                ])
                acc_total += tvac
                acc_htva += htva
                acc_vat += tva
                acc_propr += part_propr
                acc_occ += part_occ

            # Sous-total nature
            rows.append([
                "", "", "", Paragraph("<b>Sous-total nature</b>", small),
                Paragraph(f"<b>{_eur_be(acc_htva)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_vat)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_total)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_propr)}</b>", small),
                Paragraph(f"<b>{_eur_be(acc_occ)}</b>", small),
            ])

            tbl = Table(rows, colWidths=col_widths, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), MID_GREY),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (4, 0), (8, -1), "RIGHT"),
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
            key_htva += acc_htva
            key_vat += acc_vat
            key_propr += acc_propr
            key_occ += acc_occ

        # Sous-total cle
        key_tbl = Table(
            [[
                "", "", "", Paragraph(f"<b>Sous-total cle {dk_name}</b>", body),
                Paragraph(f"<b>{_eur_be(key_htva)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_vat)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_total)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_propr)}</b>", body),
                Paragraph(f"<b>{_eur_be(key_occ)}</b>", body),
            ]],
            colWidths=col_widths,
        )
        key_tbl.setStyle(TableStyle([
            ("BACKGROUND", (3, 0), (-1, 0), colors.HexColor("#DDE4EE")),
            ("ALIGN", (4, 0), (8, 0), "RIGHT"),
            ("LINEABOVE", (0, 0), (-1, 0), 1.0, BRAND_BLUE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(key_tbl)
        elements.append(Spacer(1, 4 * mm))

        grand_total += key_total
        grand_htva += key_htva
        grand_vat += key_vat
        grand_propr += key_propr
        grand_occ += key_occ

    # ---- TOTAUX GENERAUX ----
    if grouped:
        gt = Table(
            [[
                "", "", "", Paragraph("<b>Totaux generaux immeuble :</b>", body),
                Paragraph(f"<b>{_eur_be(grand_htva)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_vat)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_total)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_propr)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_occ)}</b>", body),
            ]],
            colWidths=col_widths,
        )
        gt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DARK_GREY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (4, 0), (8, 0), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(gt)
    else:
        elements.append(Paragraph(
            "<i>Aucune depense enregistree sur la periode.</i>", body
        ))

    doc.build(elements, onFirstPage=footer_cb or _add_page_number, onLaterPages=footer_cb or _add_page_number)
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
