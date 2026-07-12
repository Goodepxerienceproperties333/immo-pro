"""Generateur PDF 'Liste des journaux comptables' (PCMN belge).

Structure :
  - Header : copropriete + periode + 'fait le'
  - Pour chaque type de journal (AP, AV, OD, BQ...) :
      * Titre du journal + total debit / total credit
      * Tableau des ecritures (date, reference, libelle, compte/debit/credit par ligne)
      * Sous-total du journal
  - Total general (somme debits = somme credits = controle PCMN)
"""
import io
from datetime import datetime
from collections import defaultdict

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER


BRAND_BLUE = colors.HexColor("#0055FF")
LIGHT_GREY = colors.HexColor("#F1F5F9")
MID_GREY = colors.HexColor("#E2E8F0")
DARK_GREY = colors.HexColor("#475569")
ACCENT = colors.HexColor("#DDE4EE")


JOURNAL_LABELS = {
    "AP": "Journal des achats",
    "AV": "Journal des ventes",
    "OD": "Operations diverses",
    "BQ": "Journal de banque",
    "BANK": "Journal de banque",
    "CA": "Caisse",
    "FB": "Fonds de roulement",
}


def _eur_be(n: float) -> str:
    s = f"{n:,.2f}"
    parts = s.split(".")
    int_part = parts[0].replace(",", ".")
    return f"{int_part},{parts[1]}"


def _fmt_date(iso: str) -> str:
    if not iso or len(iso) < 10:
        return iso or ""
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def _add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(DARK_GREY)
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[1] / 2, 10 * mm, f"{page_num}")
    canvas.restoreState()


def build_journals_pdf(
    copropriete: dict,
    date_from: str,
    date_to: str,
    entries: list,            # list of journal_entries (already filtered & in period)
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Render le PDF 'Liste des journaux'.

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
        title=f"Journaux {date_from} au {date_to}",
    )

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], fontSize=15, leading=18,
                              textColor=DARK_GREY, alignment=TA_CENTER, fontName="Helvetica-Bold")
    h_period = ParagraphStyle("h_period", parent=styles["Normal"], fontSize=10, leading=12,
                               alignment=TA_CENTER, textColor=DARK_GREY)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7, leading=9, textColor=DARK_GREY)
    j_title = ParagraphStyle("j_title", parent=body, fontSize=11, leading=13,
                              textColor=BRAND_BLUE, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4)

    elements = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elements.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            small,
        ))
        elements.append(Spacer(1, 3 * mm))

    # ---- HEADER ----
    df = _fmt_date(date_from)
    dt = _fmt_date(date_to)
    # iter90dp : quand le header logo a deja affiche name/adresse/BCE/Ref,
    # on omet la colonne de gauche pour eviter la duplication.
    left_col_para = (
        Paragraph("", small)
        if use_new_layout
        else Paragraph(
            f"<b>{copropriete.get('name','')}</b><br/>"
            f"{copropriete.get('address','')}<br/>"
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
            f"BCE: {copropriete.get('bce','-')} - Ref: {copropriete.get('reference','')}",
            small,
        )
    )
    header = [[
        left_col_para,
        Paragraph("<b>JOURNAUX COMPTABLES</b>", h_title),
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
    elements.append(Spacer(1, 4 * mm))

    # ---- GROUP BY JOURNAL TYPE ----
    by_journal = defaultdict(list)
    for e in entries:
        by_journal[(e.get("journal_type") or "OD").upper()].append(e)

    grand_debit = 0.0
    grand_credit = 0.0

    # Sort journals : AP, AV, OD, BQ, others
    journal_order = ["AP", "AV", "OD", "BQ", "BANK", "CA", "FB"]
    sorted_keys = sorted(by_journal.keys(), key=lambda j: (journal_order.index(j) if j in journal_order else 99, j))

    col_widths = [22 * mm, 28 * mm, 60 * mm, 28 * mm, 60 * mm, 28 * mm, 28 * mm]
    headers = ["Date", "Reference", "Libelle", "Compte", "Nom compte", "Debit", "Credit"]

    for j_key in sorted_keys:
        j_label = JOURNAL_LABELS.get(j_key, f"Journal {j_key}")
        j_entries = sorted(by_journal[j_key], key=lambda x: (x.get("date", ""), x.get("reference", "")))
        # Compute totals
        j_debit = 0.0
        j_credit = 0.0
        for e in j_entries:
            for ln in (e.get("lines") or []):
                j_debit += float(ln.get("debit") or 0)
                j_credit += float(ln.get("credit") or 0)
        elements.append(Paragraph(
            f"{j_label} ({j_key}) &mdash; {len(j_entries)} ecriture{'s' if len(j_entries) > 1 else ''} &mdash; "
            f"Debit : {_eur_be(j_debit)} | Credit : {_eur_be(j_credit)}",
            j_title,
        ))

        # iter90dp : wrap Libelle et Nom compte dans des Paragraph pour eviter
        # que les textes longs debordent sur les colonnes voisines (superposition).
        from reportlab.lib.styles import ParagraphStyle as _PS
        cell_style = _PS("cell", fontName="Helvetica", fontSize=7, leading=8)
        # Build rows : 1 row per LINE, with the entry header repeated only on the first line of each entry.
        rows = [[Paragraph(f"<b>{h}</b>", small) for h in headers]]
        for e in j_entries:
            lines = e.get("lines") or []
            first = True
            for ln in lines:
                if first:
                    date_cell = _fmt_date(e.get("date", ""))
                    ref_cell = e.get("reference", "") or ""
                    desc_cell = (e.get("description", "") or "")
                else:
                    date_cell = ""
                    ref_cell = ""
                    desc_cell = ""
                debit = float(ln.get("debit") or 0)
                credit = float(ln.get("credit") or 0)
                rows.append([
                    date_cell,
                    ref_cell,
                    Paragraph(desc_cell, cell_style),
                    ln.get("account_number", "") or "",
                    Paragraph((ln.get("account_name", "") or ""), cell_style),
                    _eur_be(debit) if debit else "",
                    _eur_be(credit) if credit else "",
                ])
                first = False
            # Separator after entry
            rows.append(["", "", "", "", "", "", ""])

        # Journal subtotal
        rows.append([
            "", "", "", "", Paragraph("<b>Sous-total journal</b>", small),
            Paragraph(f"<b>{_eur_be(j_debit)}</b>", small),
            Paragraph(f"<b>{_eur_be(j_credit)}</b>", small),
        ])

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), MID_GREY),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("ALIGN", (5, 0), (6, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, MID_GREY),
            ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(tbl)
        elements.append(Spacer(1, 3 * mm))

        grand_debit += j_debit
        grand_credit += j_credit

    # ---- TOTAL GENERAL ----
    if by_journal:
        diff = round(grand_debit - grand_credit, 2)
        equilibre = abs(diff) < 0.01
        gt = Table(
            [[
                "", "", "", "", Paragraph("<b>Total general (controle PCMN)</b>", body),
                Paragraph(f"<b>{_eur_be(grand_debit)}</b>", body),
                Paragraph(f"<b>{_eur_be(grand_credit)}</b>", body),
            ]],
            colWidths=col_widths,
        )
        gt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DARK_GREY if equilibre else colors.HexColor("#B91C1C")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (5, 0), (6, 0), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(gt)
        if not equilibre:
            elements.append(Paragraph(
                f"<b style='color:#B91C1C'>ATTENTION : journaux desequilibres "
                f"(ecart {_eur_be(diff)} EUR). Verifier les ecritures.</b>",
                small,
            ))
    else:
        elements.append(Paragraph(
            "<i>Aucune ecriture comptable sur la periode.</i>", body,
        ))

    doc.build(elements, onFirstPage=footer_cb or _add_page_number, onLaterPages=footer_cb or _add_page_number)
    buf.seek(0)
    return buf.read()


def build_invoices_list_pdf(
    copropriete: dict,
    date_from: str,
    date_to: str,
    invoices: list,
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Render le PDF 'Liste exhaustive des factures' (toutes natures, tous statuts).

    Colonnes : Date | N piece | Fournisseur | Libelle | Compte | HTVA | TVA | TVAC | Statut
    Trie par date ASC. Totaux HTVA / TVA / TVAC en bas.

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
        leftMargin=10 * mm, rightMargin=10 * mm,
        title=f"Liste des factures {date_from} au {date_to}",
    )

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], fontSize=15, leading=18,
                              textColor=DARK_GREY, alignment=TA_CENTER, fontName="Helvetica-Bold")
    h_period = ParagraphStyle("h_period", parent=styles["Normal"], fontSize=10, leading=12,
                               alignment=TA_CENTER, textColor=DARK_GREY)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8, leading=10)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7, leading=9, textColor=DARK_GREY)

    elements = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elements.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            small,
        ))
        elements.append(Spacer(1, 3 * mm))

    # ---- HEADER ----
    df = _fmt_date(date_from)
    dt = _fmt_date(date_to)
    # iter90dp : evite duplication ACP quand header logo actif
    left_col_para = (
        Paragraph("", small)
        if use_new_layout
        else Paragraph(
            f"<b>{copropriete.get('name','')}</b><br/>"
            f"{copropriete.get('address','')}<br/>"
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
            f"BCE: {copropriete.get('bce','-')} - Ref: {copropriete.get('reference','')}",
            small,
        )
    )
    header = [[
        left_col_para,
        Paragraph("<b>LISTE EXHAUSTIVE DES FACTURES</b>", h_title),
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
    elements.append(Paragraph(f"DU {df} AU {dt} &mdash; {len(invoices)} facture(s)", h_period))
    elements.append(Spacer(1, 4 * mm))

    # ---- TABLE ----
    headers = ["Date", "N piece", "Fournisseur", "Libelle", "Compte",
               "HTVA", "TVA", "TVAC", "Statut"]
    col_widths = [20 * mm, 22 * mm, 45 * mm, 50 * mm, 18 * mm,
                  22 * mm, 18 * mm, 22 * mm, 22 * mm]

    rows = [[Paragraph(f"<b>{h}</b>", small) for h in headers]]
    total_htva = 0.0
    total_tva = 0.0
    total_tvac = 0.0
    # iter90dp : wrap Fournisseur + Libelle dans Paragraph pour eviter debordement
    from reportlab.lib.styles import ParagraphStyle as _PS
    inv_cell = _PS("inv_cell", fontName="Helvetica", fontSize=7, leading=8)

    for inv in sorted(invoices, key=lambda x: (x.get("date", ""), x.get("number", ""))):
        tvac = float(inv.get("total_amount") or 0)
        tva = float(inv.get("vat_amount") or 0)
        htva = round(tvac - tva, 2)
        status = (inv.get("status") or "").lower()
        status_label = {
            "paid": "Payee",
            "unpaid": "A payer",
            "partial": "Partiel",
            "cancelled": "Annulee",
        }.get(status, status or "-")
        rows.append([
            _fmt_date(inv.get("date", "")),
            (inv.get("number", "") or "")[:14],
            Paragraph(inv.get("supplier", "") or "", inv_cell),
            Paragraph(inv.get("description", "") or "", inv_cell),
            inv.get("account_number", "") or "",
            _eur_be(htva),
            _eur_be(tva),
            _eur_be(tvac),
            status_label,
        ])
        total_htva += htva
        total_tva += tva
        total_tvac += tvac

    # Total row
    rows.append([
        "", "", "", "", Paragraph("<b>TOTAL</b>", body),
        Paragraph(f"<b>{_eur_be(total_htva)}</b>", body),
        Paragraph(f"<b>{_eur_be(total_tva)}</b>", body),
        Paragraph(f"<b>{_eur_be(total_tvac)}</b>", body),
        "",
    ])

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), MID_GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (5, 0), (7, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, MID_GREY),
        ("BACKGROUND", (0, -1), (-1, -1), DARK_GREY),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(tbl)

    if not invoices:
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph("<i>Aucune facture sur la periode.</i>", body))

    doc.build(elements, onFirstPage=footer_cb or _add_page_number, onLaterPages=footer_cb or _add_page_number)
    buf.seek(0)
    return buf.read()
