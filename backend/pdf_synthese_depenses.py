"""Generateur PDF 'Synthese des depenses' - format portrait, groupe par
nature de depense (iter91d).

Complement du `build_liste_depenses_pdf` (paysage detaille).
Utilise dans l'envoi automatique du decompte annuel (email) pour joindre
un recapitulatif synthetique en plus du detail.

Structure :
  - En-tete cabinet + ACP + periode
  - Tableau agrege : Nature de depense | Compte | Nb lignes | TVAC total
  - Sous-total par cle de repartition (si groupement demande)
  - Total general immeuble
"""
import io
from collections import defaultdict

from reportlab.lib.pagesizes import A4
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
    # iter93ac : format unifie plateforme (espace millier + virgule decimale)
    try:
        f = float(n)
    except (TypeError, ValueError):
        return ""
    sign = "-" if f < 0 else ""
    f = abs(f)
    s = f"{sign}{f:,.2f}".replace(",", "\u202f").replace(".", ",")
    return s


def build_synthese_depenses_pdf(
    copropriete: dict,
    date_from: str,
    date_to: str,
    rows: list,               # sortie de compute_expense_rows()
    distribution_keys: list,
    pcmn_map: dict,
    syndic_pdf_ctx: dict = None,
) -> bytes:
    """Rend un PDF 'Synthese des depenses' (portrait A4) en bytes.

    Agrege les lignes de `rows` par (cle de repartition -> nature -> compte)
    et affiche uniquement les totaux (pas le detail des factures). Utile
    pour joindre un resume synthetique au decompte annuel envoye par email.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback

    use_new_layout = bool(syndic_pdf_ctx)
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=14 * mm, bottomMargin=18 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10, textColor=DARK_GREY)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=15, textColor=BRAND_BLUE, alignment=TA_LEFT)
    sub_style = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, leading=11, textColor=DARK_GREY)
    key_style = ParagraphStyle("key", parent=styles["Normal"], fontSize=10, leading=12, textColor=BRAND_BLUE, spaceBefore=6, spaceAfter=2, fontName="Helvetica-Bold")
    total_style = ParagraphStyle("total", parent=styles["Normal"], fontSize=10, leading=12, alignment=TA_RIGHT, fontName="Helvetica-Bold")

    story = []

    # ---- Header ----
    if use_new_layout:
        header = build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            {
                "name": copropriete.get("name", ""),
                "address": copropriete.get("address", "") or "",
                "period": f"Du {date_from} au {date_to}",
            },
            small,
        )
        story.append(header)
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Synthese des depenses", title))
    story.append(Paragraph(
        f"Copropriete <b>{copropriete.get('name', '')}</b> - Periode du {date_from} au {date_to}",
        sub_style,
    ))
    story.append(Spacer(1, 4 * mm))

    # ---- Aggregation Cle -> Nature -> Compte ----
    # Chaque row a : distribution_key_id, expense_category_name, account_number,
    # description, total_amount (TVAC), vat_amount, proprietaire_amount, occupant_amount.
    dk_map = {k["id"]: k for k in distribution_keys}

    # Structure: {dk_id: {nature_key: {"name","account","count","htva","tva","tvac","prop","occ"}}}
    agg = defaultdict(lambda: defaultdict(lambda: {
        "name": "", "account": "", "count": 0,
        "htva": 0.0, "tva": 0.0, "tvac": 0.0, "prop": 0.0, "occ": 0.0,
    }))
    for r in rows:
        dk_id = r.get("distribution_key_id") or ""
        nature = r.get("expense_category_name") or "(non categorise)"
        acc = r.get("account_number") or ""
        key = f"{nature}|{acc}"
        cell = agg[dk_id][key]
        cell["name"] = nature
        cell["account"] = acc
        cell["count"] += 1
        tvac = float(r.get("total_amount") or 0)
        tva = float(r.get("vat_amount") or 0)
        cell["tvac"] += tvac
        cell["tva"] += tva
        cell["htva"] += (tvac - tva)
        cell["prop"] += float(r.get("proprietaire_amount") or 0)
        cell["occ"] += float(r.get("occupant_amount") or 0)

    grand_htva = 0.0
    grand_tva = 0.0
    grand_tvac = 0.0
    grand_prop = 0.0
    grand_occ = 0.0

    for dk_id, natures_map in sorted(agg.items(), key=lambda kv: (dk_map.get(kv[0], {}).get("name") or "ZZZ")):
        dk = dk_map.get(dk_id, {})
        dk_name = dk.get("name") or "(sans cle de repartition)"
        dk_code = dk.get("code") or ""
        title_line = f"[{dk_code}] {dk_name}" if dk_code else dk_name
        story.append(Paragraph(title_line, key_style))

        # Header
        data = [["Nature", "Compte", "Nb", "HTVA", "TVA", "TVAC"]]
        sub_htva = sub_tva = sub_tvac = 0.0
        for _key, cell in sorted(natures_map.items()):
            data.append([
                cell["name"], cell["account"],
                str(cell["count"]),
                _eur_be(cell["htva"]),
                _eur_be(cell["tva"]),
                _eur_be(cell["tvac"]),
            ])
            sub_htva += cell["htva"]
            sub_tva += cell["tva"]
            sub_tvac += cell["tvac"]
            grand_htva += cell["htva"]
            grand_tva += cell["tva"]
            grand_tvac += cell["tvac"]
            grand_prop += cell["prop"]
            grand_occ += cell["occ"]

        data.append([
            "Sous-total", "", "",
            _eur_be(sub_htva), _eur_be(sub_tva), _eur_be(sub_tvac),
        ])
        t = Table(data, colWidths=[70 * mm, 25 * mm, 12 * mm, 25 * mm, 22 * mm, 26 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, MID_GREY),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 3 * mm))

    # ---- Totaux generaux ----
    story.append(Spacer(1, 4 * mm))
    tot_data = [
        ["", "HTVA", "TVA", "TVAC", "Part proprietaire", "Part occupant"],
        [
            "Total immeuble",
            _eur_be(grand_htva),
            _eur_be(grand_tva),
            _eur_be(grand_tvac),
            _eur_be(grand_prop),
            _eur_be(grand_occ),
        ],
    ]
    tot = Table(tot_data, colWidths=[42 * mm, 25 * mm, 22 * mm, 26 * mm, 32 * mm, 32 * mm])
    tot.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK_GREY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#FEF3C7")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, DARK_GREY),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tot)

    if use_new_layout and footer_cb:
        doc.build(story, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(story)
    return buf.getvalue()
