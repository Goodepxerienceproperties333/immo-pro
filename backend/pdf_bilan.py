"""PDF Bilan comptable par exercice - structure type 'Bilan apres repartition'.

Format inspire des bilans de syndic belge :
- En-tete avec immeuble + syndic + date d'edition
- 2 colonnes : ACTIF (gauche) | PASSIF (droite)
- Detail par compte avec libelle + montant
- Totaux en pied de chaque colonne (equilibre)
"""
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    PageBreak, KeepInFrame, Flowable,
)


class _ScaledFlowable(Flowable):
    """iter93by : Wrap n'importe quel flowable et le scale down si necessaire
    pour tenir dans (max_width, max_height). Utilise pour GARANTIR que le
    bilan tienne sur 1 page landscape - contrairement a KeepInFrame qui
    echoue souvent sur les tables imbriquees, ce wrapper applique un
    scale() canvas natif qui fonctionne toujours."""

    def __init__(self, content, max_width, max_height):
        Flowable.__init__(self)
        self.content = content
        self.max_width = max_width
        self.max_height = max_height
        self._scale = 1.0
        self._nat_w = 0
        self._nat_h = 0

    def wrap(self, availWidth, availHeight):
        # Mesure la taille naturelle du contenu (SANS contrainte availHeight
        # car on veut scale, pas paginer).
        self._nat_w, self._nat_h = self.content.wrap(self.max_width, 100000)
        # Calcule le scale pour tenir dans max_w x max_h ET dans availHeight
        # (jamais > 1). Cette derniere condition force le scale-down si le
        # flowable arrive apres un title/header et n'a pas la pleine page.
        sx = min(1.0, self.max_width / self._nat_w) if self._nat_w > 0 else 1.0
        sy_max = min(1.0, self.max_height / self._nat_h) if self._nat_h > 0 else 1.0
        # iter93by fix : contrainte supplementaire sur availHeight (marge 5%
        # de securite pour eviter les arrondis reportlab qui forcent le
        # page-break a la moindre depassement d'1pt).
        avail_h_safe = max(1.0, availHeight * 0.95)
        sy_avail = min(1.0, avail_h_safe / self._nat_h) if self._nat_h > 0 else 1.0
        self._scale = min(sx, sy_max, sy_avail)
        rendered_w = self._nat_w * self._scale
        rendered_h = self._nat_h * self._scale
        return (rendered_w, rendered_h)

    def draw(self):
        c = self.canv
        c.saveState()
        if self._scale != 1.0:
            c.scale(self._scale, self._scale)
        # drawOn utilise les coords non-scalees ; le canvas.scale les compensera
        self.content.drawOn(c, 0, 0)
        c.restoreState()


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
HEADER_BG = colors.HexColor("#1E40AF")
RUBRIQUE_BG = colors.HexColor("#E0E7FF")


def _fmt_eur(v):
    try:
        v = float(v)
    except Exception:
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v:,.2f}".replace(",", " ").replace(".", ",")


def _fmt_date(s):
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return s


def _column_table(rubriques, side_label, total, full_width: bool = False,
                  compact: bool = False, table_width: float | None = None):
    """Construit le tableau ACTIF ou PASSIF detaille - layout aere.

    iter93by : `compact=True` reduit fontSize/paddings pour bilans denses.
    iter93bz : `table_width` permet une largeur custom (utilise pour centrer
    en landscape avec 200mm au lieu de la moitie de USABLE_W).
    """
    # Filtre les rubriques vides (total = 0) pour alleger l'affichage
    rubriques = [r for r in rubriques if r.get("total", 0) > 0.005]
    # Styles - taille normale (lisible) par defaut. Compact reserve aux
    # bilans tres denses (>40 lignes en portrait, non utilise en landscape).
    if compact == "ultra":
        acc_fs = 6
        hdr_fs = 7.5
        rub_fs = 7
        tot_fs = 7.5
        pad = 0
        hdr_pad = 1.5
    elif compact:
        acc_fs = 7.5
        hdr_fs = 9
        rub_fs = 8.5
        tot_fs = 9
        pad = 0.5
        hdr_pad = 3
    else:
        acc_fs = 9
        hdr_fs = 11
        rub_fs = 10
        tot_fs = 11
        pad = 2
        hdr_pad = 6
    rows = []
    # Header de la colonne
    rows.append([
        Paragraph(f"<b>{side_label}</b>", _hdr_style(hdr_fs)),
        Paragraph("<b>EUR</b>", _hdr_right(hdr_fs)),
    ])
    for r in rubriques:
        # Ligne rubrique
        rows.append([
            Paragraph(f"<b>{r['label']}</b>", _rub_style(rub_fs)),
            Paragraph(f"<b>{_fmt_eur(r['total'])}</b>", _rub_right(rub_fs)),
        ])
        # Detail des comptes
        for a in r.get("accounts", []):
            num = a.get("account_number", "") or ""
            name = a.get("account_name", "") or ""
            amt = a.get("amount", 0)
            # Si pas de numero (compte agrege owner), afficher juste le nom
            if num:
                label_html = (
                    f"<font color='#94A3B8' size='{acc_fs - 1.5:.1f}' name='Courier'>{num}</font> "
                    f"<font size='{acc_fs}'>{name}</font>"
                )
            else:
                label_html = f"<font size='{acc_fs}'>{name}</font>"
            rows.append([
                Paragraph(label_html, _acc_style(acc_fs)),
                Paragraph(_fmt_eur(amt), _acc_right(acc_fs)),
            ])
    # TOTAL
    rows.append([
        Paragraph(f"<b>TOTAL {side_label.upper()}</b>", _tot_style(tot_fs)),
        Paragraph(f"<b>{_fmt_eur(total)}</b>", _tot_right(tot_fs)),
    ])
    # iter93bz : largeur adaptee. table_width prime, sinon fallback historique
    if table_width is not None:
        # Ratio 65/35 : label + numero de compte plus large que le montant
        col_widths = [table_width * 0.65, table_width * 0.35]
    elif full_width:
        col_widths = [(87 * 2) * mm, (45 * 2) * mm]
    else:
        col_widths = [87 * mm, 45 * mm]
    tbl = Table(
        rows,
        colWidths=col_widths,
        repeatRows=1,
    )
    style = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, 0), hdr_pad),
        ("BOTTOMPADDING", (0, 0), (-1, 0), hdr_pad),
        # Total
        ("BACKGROUND", (0, -1), (-1, -1), HEADER_BG),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("LINEABOVE", (0, -1), (-1, -1), 1, SLATE_900),
        ("TOPPADDING", (0, -1), (-1, -1), hdr_pad),
        ("BOTTOMPADDING", (0, -1), (-1, -1), hdr_pad),
        # Grille legere
        ("BOX", (0, 0), (-1, -1), 0.4, SLATE_300),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, SLATE_100),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 1), (-1, -2), pad),
        ("BOTTOMPADDING", (0, 1), (-1, -2), pad),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]
    # Rubrique rows (label gras) - identifier les indexes de rubriques
    idx = 1
    for r in rubriques:
        style.append(("BACKGROUND", (0, idx), (-1, idx), RUBRIQUE_BG))
        style.append(("TOPPADDING", (0, idx), (-1, idx), pad + 1))
        style.append(("BOTTOMPADDING", (0, idx), (-1, idx), pad + 1))
        idx += 1 + len(r.get("accounts", []))
    tbl.setStyle(TableStyle(style))
    return tbl


def _hdr_style(fs: float = 10):
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("hdr", parent=s, fontSize=fs, textColor=colors.white,
                          alignment=0, leading=fs + 2)


def _hdr_right(fs: float = 10):
    return ParagraphStyle("hdrR", parent=_hdr_style(fs), alignment=2)


def _rub_style(fs: float = 9.5):
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("rub", parent=s, fontSize=fs, textColor=SLATE_900,
                          fontName="Helvetica-Bold", leading=fs + 2.5)


def _rub_right(fs: float = 9.5):
    return ParagraphStyle("rubR", parent=_rub_style(fs), alignment=2)


def _acc_style(fs: float = 8.5):
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("acc", parent=s, fontSize=fs, textColor=SLATE_700,
                          leading=fs + 2.5)


def _acc_right(fs: float = 8.5):
    return ParagraphStyle("accR", parent=_acc_style(fs), alignment=2,
                          fontName="Helvetica")


def _tot_style(fs: float = 10):
    s = getSampleStyleSheet()["BodyText"]
    return ParagraphStyle("tot", parent=s, fontSize=fs, textColor=colors.white,
                          fontName="Helvetica-Bold", leading=fs + 3)


def _tot_right(fs: float = 10):
    return ParagraphStyle("totR", parent=_tot_style(fs), alignment=2)


def build_bilan_pdf(
    *,
    copropriete: dict,
    syndic: dict = None,
    fiscal_year: dict = None,
    bilan_data: dict,
    date_to: str = "",
    syndic_pdf_ctx: dict = None,
    view_mode: str = "before_distribution",
) -> bytes:
    """Genere le PDF Bilan (avant ou apres repartition selon `view_mode`).

    iter90dj : si `syndic_pdf_ctx` est fourni, ajoute logo cabinet en tete
    (1re page uniquement) + pied de page avec mentions legales + numero de
    page sur toutes les pages.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx)  # iter90hm : nouveau layout TOUJOURS actif si contexte fourni
    footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", "")) if use_new_layout else None

    buf = BytesIO()
    # iter93by : format PAYSAGE + SimpleDocTemplate. Le side_by_side sera
    # eventuellement wrap dans _ScaledFlowable pour garantir 1-page.
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=25 * mm if use_new_layout else 12 * mm,
        title=f"Bilan - {copropriete.get('name','')}",
    )
    PAGE_W, PAGE_H = landscape(A4)
    USABLE_W = PAGE_W - 20 * mm
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"],
        fontSize=14, leading=18, textColor=colors.white,
        alignment=1, fontName="Helvetica-Bold",
    )
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=8.5, leading=11, textColor=SLATE_500)
    elems = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        elems.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            copropriete,
            small,
        ))
        elems.append(Spacer(1, 4 * mm))

    # ---- BANDEAU TITRE COLORE ----
    end_str = _fmt_date(date_to or (fiscal_year or {}).get("end_date", ""))
    fy_name = (fiscal_year or {}).get("name", "")
    # BUG FIX (iter90fq) : le titre etait hardcode "APRES REPARTITION" meme
    # quand les donnees etaient calculees en mode "avant repartition" (compte
    # 499 non reparti visible). Le titre doit refleter le VRAI mode utilise
    # pour le calcul, sinon l'utilisateur ne peut pas detecter l'incoherence.
    title_text = (
        "BILAN COMPTABLE APRES REPARTITION" if view_mode == "after_distribution"
        else "BILAN COMPTABLE AVANT REPARTITION"
    )
    if end_str:
        title_text += f" AU {end_str}"
    title_tbl = Table(
        [[Paragraph(title_text, title_style)],
         [Paragraph(f"<font color='#E0E7FF' size='9'>FAIT LE {datetime.now().strftime('%d/%m/%Y')}"
                    + (f" - Exercice {fy_name}" if fy_name else "")
                    + "</font>", title_style)]],
        colWidths=[USABLE_W],
    )
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HEADER_BG),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elems.append(title_tbl)
    elems.append(Spacer(1, 4 * mm))

    # ---- HEADER INFO ACP + SYNDIC ----
    # iter90dp : quand le header logo affiche deja l'ACP, on omet le bloc
    # ACP ci-dessous pour eviter la duplication.
    if not use_new_layout:
        acp_block = (
            f"<b>IMMEUBLE : {copropriete.get('name','')}</b><br/>"
            f"{copropriete.get('address','')}<br/>"
            f"{copropriete.get('postal_code','')} {copropriete.get('city','')}<br/>"
            + (f"BCE : {copropriete.get('bce','')}<br/>" if copropriete.get('bce') else "")
        )
        syndic_block = ""
        if syndic:
            syndic_block = (
                f"<b>{syndic.get('name','')}</b><br/>"
                f"{syndic.get('address','')}<br/>"
                f"{syndic.get('postal_code','')} {syndic.get('city','')}<br/>"
                + (f"Tel. {syndic.get('phone','')}<br/>" if syndic.get('phone') else "")
                + (f"{syndic.get('email','')}<br/>" if syndic.get('email') else "")
                + (f"<font size='7' color='#64748B'>Num. IPI : {syndic.get('ipi','')}</font>"
                   if syndic.get('ipi') else "")
            )
        header_tbl = Table(
            [[Paragraph(acp_block, small), Paragraph(syndic_block, small)]],
            colWidths=[USABLE_W / 2, USABLE_W / 2],
        )
        header_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
            ("BOX", (0, 0), (-1, -1), 0.5, SLATE_300),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elems.append(header_tbl)
        elems.append(Spacer(1, 6 * mm))

    # ---- TABLES ACTIF + PASSIF ----
    # iter93bz : USER FEEDBACK - "document illisible, autoriser plusieurs
    # pages, centrer les tableaux, agrandir les caracteres". Retour a une
    # typographie NORMALE (non-compacte), stacked ACTIF puis PASSIF, chaque
    # tableau centre en landscape, avec splitByRow pour la pagination naturelle
    # sur les bilans denses (> 30 lignes).
    actif_rubs = bilan_data.get("actif", [])
    passif_rubs = bilan_data.get("passif", [])
    # Largeur des tableaux : 200mm (centre dans 277mm utile -> 38.5mm marges G/D)
    TBL_W = 200 * mm
    actif_tbl = _column_table(
        actif_rubs, "ACTIF", bilan_data.get("total_actif", 0),
        full_width=False, compact=False, table_width=TBL_W,
    )
    passif_tbl = _column_table(
        passif_rubs, "PASSIF", bilan_data.get("total_passif", 0),
        full_width=False, compact=False, table_width=TBL_W,
    )
    actif_tbl.splitByRow = 1
    passif_tbl.splitByRow = 1
    actif_tbl.hAlign = "CENTER"
    passif_tbl.hAlign = "CENTER"
    elems.append(actif_tbl)
    elems.append(Spacer(1, 6 * mm))
    elems.append(passif_tbl)

    # ---- EQUILIBRE ----
    elems.append(Spacer(1, 5 * mm))
    eq = bilan_data.get("equilibre", False)
    ecart = bilan_data.get("ecart", 0)
    if eq:
        eq_msg = (
            f"<font color='#16A34A' size='10'><b>BILAN EQUILIBRE</b></font> - "
            f"Total Actif = Total Passif = <b>{_fmt_eur(bilan_data.get('total_actif',0))} EUR</b>"
        )
    else:
        eq_msg = (
            f"<font color='#DC2626' size='10'><b>BILAN DESEQUILIBRE</b></font> - "
            f"Ecart = <b>{_fmt_eur(ecart)} EUR</b> - "
            "Verifier les ecritures du grand livre."
        )
    eq_tbl = Table([[Paragraph(eq_msg, small)]], colWidths=[TBL_W])
    eq_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1),
         colors.HexColor("#F0FDF4") if eq else colors.HexColor("#FEF3F2")),
        ("BOX", (0, 0), (-1, -1), 0.6,
         colors.HexColor("#16A34A") if eq else colors.HexColor("#DC2626")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    eq_tbl.hAlign = "CENTER"
    elems.append(eq_tbl)

    # ---- FOOTER ----
    elems.append(Spacer(1, 4 * mm))
    elems.append(Paragraph(
        f"<font size='7' color='#94A3B8'><i>Bilan etabli au {end_str or 'date du jour'} - "
        f"Document interne genere par NextGe Copro le {datetime.now().strftime('%d/%m/%Y a %H:%M')}.</i></font>",
        small,
    ))

    if footer_cb:
        doc.build(elems, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(elems)
    return buf.getvalue()
