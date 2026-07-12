"""PDF Registre des traitements RGPD (art. 30).

Genere un document PDF listant les activites de traitement des donnees
personnelles de la plateforme NextGe Copro, prêt à envoyer à l'APD en cas
de contrôle.

Contenu :
- En-tete responsable de traitement (societe, BCE, contact)
- Table des activites de traitement (finalite, base legale, categories,
  destinataires, transferts, duree de conservation)
- Table des sous-traitants (nom, prestation, siege, garanties)
- Table des mesures de securite techniques et organisationnelles
- Pied de page date d'edition + signature
"""
from io import BytesIO
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    PageBreak,
)


BRAND = colors.HexColor("#0055FF")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748B")
SLATE_300 = colors.HexColor("#CBD5E1")
SLATE_100 = colors.HexColor("#F1F5F9")
SLATE_50 = colors.HexColor("#F8FAFC")
ACCENT_BG = colors.HexColor("#E0E7FF")
AMBER_BG = colors.HexColor("#FEF3C7")


def _styles():
    ss = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=16, textColor=BRAND, spaceAfter=6, leading=20),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11, textColor=SLATE_900, spaceBefore=10,
                             spaceAfter=4, leading=14),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName="Helvetica-Bold",
                             fontSize=9, textColor=SLATE_700, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=8.5, textColor=SLATE_700, leading=11,
                               alignment=TA_JUSTIFY),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=7.5, textColor=SLATE_500, leading=9.5),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=7.5, textColor=SLATE_900, leading=9.5),
        "cell_head": ParagraphStyle("cell_head", parent=ss["Normal"],
                                    fontName="Helvetica-Bold", fontSize=7.5,
                                    textColor=colors.white, leading=9,
                                    alignment=TA_LEFT),
        "kv_key": ParagraphStyle("kv_key", parent=ss["Normal"],
                                 fontName="Helvetica-Bold", fontSize=8,
                                 textColor=SLATE_700, leading=10),
        "kv_val": ParagraphStyle("kv_val", parent=ss["Normal"],
                                 fontName="Helvetica", fontSize=8.5,
                                 textColor=SLATE_900, leading=10.5),
        "footer": ParagraphStyle("footer", parent=ss["Normal"], fontName="Helvetica",
                                 fontSize=7, textColor=SLATE_500, alignment=TA_CENTER),
    }
    return styles


DEFAULT_PROCESSINGS = [
    {
        "name": "Gestion des utilisateurs de la plateforme",
        "purpose": "Authentification, gestion des acces, RBAC (Syndic, Gestionnaire, Proprietaire, Superadmin)",
        "legal_basis": "Execution du contrat (art. 6.1.b RGPD)",
        "data_categories": "Nom, email, mot de passe hache (bcrypt), role, telephone (optionnel), historique de connexion, adresse IP",
        "subjects": "Utilisateurs (syndics, gestionnaires, proprietaires)",
        "recipients": "Superadmin (Editeur), sous-traitants (hebergeur, service d'authentification)",
        "transfers": "Aucun transfert hors UE (donnees hebergees en UE)",
        "retention": "Duree du contrat + 30 jours (delai de suppression RGPD) puis anonymisation",
    },
    {
        "name": "Gestion comptable des ACP (copropriete)",
        "purpose": "Tenue de la comptabilite belge des copropriétés (PCMN), journaux, factures, appels de fonds, decomptes de mutation, bilans",
        "legal_basis": "Obligation legale (art. 6.1.c RGPD - loi belge sur la copropriete), execution du contrat",
        "data_categories": "Coordonnees proprietaires (nom, adresse, email, IBAN), quotes-parts, montants dus, mouvements bancaires, factures fournisseurs, historique paiements",
        "subjects": "Copropriétaires, locataires, fournisseurs",
        "recipients": "Syndic de l'ACP, gestionnaire assigne, proprietaire concerne, expert-comptable eventuel, administration fiscale (obligation legale)",
        "transfers": "Aucun transfert hors UE",
        "retention": "7 ans apres cloture de l'exercice (art. III.86 CDE - obligations legales comptables)",
    },
    {
        "name": "Portail proprietaire (self-service)",
        "purpose": "Consultation par le proprietaire de ses appels de fonds, factures, decomptes, documents",
        "legal_basis": "Execution du contrat (art. 6.1.b RGPD)",
        "data_categories": "Identifiants de connexion, quotes-parts, montants dus/payes, PDF des documents",
        "subjects": "Proprietaires connectes",
        "recipients": "Proprietaire lui-meme",
        "transfers": "Aucun",
        "retention": "Duree du mandat + 30 jours",
    },
    {
        "name": "Import de releves bancaires (CODA, PDF, CSV)",
        "purpose": "Categorisation automatique des transactions bancaires via IA (Claude Sonnet 4.5) pour reconciliation",
        "legal_basis": "Execution du contrat + interet legitime (art. 6.1.b et 6.1.f RGPD)",
        "data_categories": "Libelles de transactions, IBAN, montants, contrepartie, dates",
        "subjects": "Titulaires de compte, contreparties bancaires",
        "recipients": "Syndic, gestionnaire, sous-traitant IA (traitement texte uniquement)",
        "transfers": "Traitement IA par Emergent Labs (Universal LLM Key) - sous-traitant declare",
        "retention": "7 ans (comptabilite) ou suppression a la demande",
    },
    {
        "name": "Assistance et support",
        "purpose": "Assistance utilisateur via chatbot IA + escalade email vers support",
        "legal_basis": "Execution du contrat (art. 6.1.b RGPD)",
        "data_categories": "Contenu des messages, email de l'utilisateur, historique de conversation",
        "subjects": "Utilisateurs demandeurs",
        "recipients": "Superadmin (support), sous-traitant IA, prestataire email (Microsoft Graph)",
        "transfers": "Microsoft Graph (Microsoft Ireland, UE) - clauses contractuelles types",
        "retention": "Conversations : 12 mois, puis anonymisation",
    },
    {
        "name": "Journal d'audit et securite",
        "purpose": "Tracabilite des actions sensibles (edition documents legaux, exports RGPD, suppressions), detection incidents",
        "legal_basis": "Interet legitime + obligation legale (art. 6.1.c et 6.1.f RGPD)",
        "data_categories": "User_id, email, action, timestamp, IP, user-agent, details JSON",
        "subjects": "Utilisateurs de la plateforme",
        "recipients": "Superadmin uniquement",
        "transfers": "Aucun",
        "retention": "3 ans (durée preuve) puis anonymisation",
    },
]

DEFAULT_SUBPROCESSORS = [
    {
        "name": "Emergent Labs (Universal LLM Key)",
        "service": "Fourniture d'API LLM (Anthropic Claude, OpenAI, Google Gemini) via une cle unifiee — traitement de texte uniquement (import bancaire, chatbot support)",
        "location": "Etats-Unis / UE (variable selon fournisseur sous-jacent)",
        "guarantees": "Clauses contractuelles types (CCT), pas de conservation permanente des prompts (mode API sans training)",
    },
    {
        "name": "Microsoft (Microsoft Graph API)",
        "service": "Envoi d'emails de support (welcome@goodexperienceproperties.be) via l'API Graph Microsoft 365",
        "location": "Microsoft Ireland (UE)",
        "guarantees": "Clauses contractuelles types + accord de sous-traitance Microsoft DPA",
    },
    {
        "name": "[HEBERGEUR]",
        "service": "Hebergement de l'application (serveurs Kubernetes) + base MongoDB",
        "location": "[EMPLACEMENT_HEBERGEUR - UE de preference]",
        "guarantees": "[À COMPLÉTER - contrat de sous-traitance signé, ISO 27001 le cas échéant]",
    },
]

DEFAULT_SECURITY_MEASURES = [
    "Authentification : mots de passe hachés (bcrypt cost 12), JWT signes, session avec cookie HttpOnly + Secure + SameSite",
    "Chiffrement : HTTPS/TLS 1.2+ sur toutes les communications (proxy Kubernetes)",
    "RBAC (Role-Based Access Control) : chaque endpoint verifie le role (superadmin/syndic/gestionnaire/proprietaire)",
    "Chinese walls : isolation stricte entre ACP (copropriete_id filtre au niveau backend, verifie par testing agent)",
    "Journal d'audit : toutes les actions sensibles tracees (qui, quand, quoi, IP)",
    "Sauvegardes : base MongoDB sauvegardee quotidiennement",
    "Verrous fiscaux : ecritures comptables gelees apres cloture d'exercice (impossible a modifier sans double authentification)",
    "Tests de securite : suite pytest avec verification cross-tenant + tests d'intrusion role owner/syndic",
    "Suppression differree : les demandes de suppression RGPD sont mises en attente 30 jours pour laisser le temps d'annuler",
    "Conservation legale : donnees comptables conservees 7 ans conformement a l'article III.86 CDE",
]


def _cell(text_or_para, styles, style_name="cell"):
    if hasattr(text_or_para, "wrap"):
        return text_or_para
    return Paragraph(str(text_or_para), styles[style_name])


def _key_value_table(pairs, styles, col_widths=None):
    """Petit tableau clef -> valeur pour l'en-tete."""
    if col_widths is None:
        col_widths = [45 * mm, 130 * mm]
    rows = []
    for k, v in pairs:
        rows.append([
            Paragraph(f"<b>{k}</b>", styles["kv_key"]),
            Paragraph(str(v) if v else "—", styles["kv_val"]),
        ])
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, SLATE_100),
    ]))
    return t


def _processing_table(processings, styles):
    """Grande table des activites de traitement."""
    headers = [
        Paragraph("Activite", styles["cell_head"]),
        Paragraph("Finalite", styles["cell_head"]),
        Paragraph("Base legale", styles["cell_head"]),
        Paragraph("Categories de donnees", styles["cell_head"]),
        Paragraph("Personnes concernees", styles["cell_head"]),
        Paragraph("Destinataires", styles["cell_head"]),
        Paragraph("Duree conservation", styles["cell_head"]),
    ]
    rows = [headers]
    for p in processings:
        rows.append([
            Paragraph(f"<b>{p['name']}</b>", styles["cell"]),
            Paragraph(p['purpose'], styles["cell"]),
            Paragraph(p['legal_basis'], styles["cell"]),
            Paragraph(p['data_categories'], styles["cell"]),
            Paragraph(p['subjects'], styles["cell"]),
            Paragraph(p['recipients'], styles["cell"]),
            Paragraph(p['retention'], styles["cell"]),
        ])
    col_widths = [25*mm, 32*mm, 26*mm, 32*mm, 22*mm, 25*mm, 22*mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, SLATE_300),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SLATE_50]),
    ]))
    return t


def _subprocessor_table(subprocs, styles):
    headers = [
        Paragraph("Sous-traitant", styles["cell_head"]),
        Paragraph("Prestation", styles["cell_head"]),
        Paragraph("Localisation", styles["cell_head"]),
        Paragraph("Garanties", styles["cell_head"]),
    ]
    rows = [headers]
    for sp in subprocs:
        rows.append([
            Paragraph(f"<b>{sp['name']}</b>", styles["cell"]),
            Paragraph(sp['service'], styles["cell"]),
            Paragraph(sp['location'], styles["cell"]),
            Paragraph(sp['guarantees'], styles["cell"]),
        ])
    col_widths = [45*mm, 65*mm, 35*mm, 40*mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, SLATE_300),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SLATE_50]),
    ]))
    return t


def _security_list(measures, styles):
    rows = []
    for i, m in enumerate(measures, 1):
        rows.append([
            Paragraph(f"<b>{i}.</b>", styles["kv_key"]),
            Paragraph(m, styles["cell"]),
        ])
    t = Table(rows, colWidths=[8*mm, 175*mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, SLATE_100),
    ]))
    return t


def _footer_maker(doc_title):
    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(SLATE_500)
        canvas.drawString(15 * mm, 10 * mm,
                          f"{doc_title} — Genere le "
                          f"{datetime.now(timezone.utc).strftime('%d/%m/%Y a %H:%M UTC')}")
        canvas.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return _draw


def build_rgpd_register_pdf(register_data: dict, syndic_pdf_ctx: dict = None) -> bytes:
    """Construit le PDF du registre des traitements.

    register_data doit contenir :
      - controller: dict avec societe, forme_juridique, adresse, bce, tva,
                    representant, email, telephone, dpo_email
      - processings: list[dict] activites de traitement
      - subprocessors: list[dict] sous-traitants
      - security_measures: list[str] mesures techniques et organisationnelles
      - updated_at: iso string

    iter90dj : `syndic_pdf_ctx` (optionnel) ajoute logo cabinet + pied de
    page legal avec numeros de page sur toutes les pages.
    """
    from pdf_layout import build_header_with_logo, make_footer_callback
    use_new_layout = bool(syndic_pdf_ctx and syndic_pdf_ctx.get("syndic_config"))

    styles = _styles()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=28 * mm if use_new_layout else 15 * mm,
        title="Registre des traitements RGPD (art. 30)",
        author="NextGe Copro",
    )
    story = []

    # ---- iter90dj : LOGO CABINET + INFOS (1re page uniquement) ----
    if use_new_layout:
        # iter90dm : le registre RGPD n'est PAS lie a une ACP. On utilise
        # les infos du responsable de traitement (controller) comme header.
        controller = register_data.get("controller") or {}
        acp_like = {
            "name": controller.get("societe", ""),
            "address": controller.get("adresse", ""),
            "bce": controller.get("bce", ""),
        }
        story.append(build_header_with_logo(
            syndic_pdf_ctx.get("logo_bytes"),
            acp_like,
            styles["small"],
        ))
        story.append(Spacer(1, 4 * mm))

    # === Titre ===
    story.append(Paragraph("Registre des traitements de donnees a caractere personnel",
                           styles["h1"]))
    story.append(Paragraph(
        "Etabli conformement a l'article 30 du Reglement (UE) 2016/679 (RGPD)",
        styles["small"]))
    story.append(Spacer(1, 6))

    # Bandeau d'info
    updated_at = register_data.get("updated_at", "")
    if updated_at:
        try:
            up = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            updated_str = up.strftime("%d/%m/%Y")
        except Exception:
            updated_str = updated_at[:10]
    else:
        updated_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    info_row = Table([[
        Paragraph(f"<b>Derniere mise a jour :</b> {updated_str}", styles["small"]),
        Paragraph("<b>Version :</b> Registre unique — SaaS NextGe Copro", styles["small"]),
    ]], colWidths=[95*mm, 90*mm])
    info_row.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(info_row)
    story.append(Spacer(1, 10))

    # === 1. Responsable du traitement ===
    story.append(Paragraph("1. Responsable du traitement", styles["h2"]))
    c = register_data.get("controller", {}) or {}
    ctrl_pairs = [
        ("Denomination sociale", c.get("societe", "[SOCIETE]")),
        ("Forme juridique", c.get("forme_juridique", "[FORME_JURIDIQUE]")),
        ("Siege social", c.get("adresse", "[ADRESSE_COMPLETE]")),
        ("N° BCE (Banque-Carrefour)", c.get("bce", "[NUMERO_BCE]")),
        ("N° TVA", c.get("tva", "[NUMERO_TVA]")),
        ("Representant legal", c.get("representant", "[NOM_REPRESENTANT]")),
        ("Email contact", c.get("email", "welcome@goodexperienceproperties.be")),
        ("Telephone", c.get("telephone", "[TELEPHONE]")),
        ("DPO / Delegue a la protection", c.get("dpo_email", "welcome@goodexperienceproperties.be")),
    ]
    story.append(_key_value_table(ctrl_pairs, styles))
    story.append(Spacer(1, 8))

    # === 2. Activites de traitement ===
    story.append(Paragraph("2. Activites de traitement", styles["h2"]))
    story.append(Paragraph(
        "Liste exhaustive des traitements de donnees a caractere personnel realises par la Plateforme "
        "NextGe Copro. Chaque traitement est detaille : finalite, base legale RGPD, categories de donnees, "
        "personnes concernees, destinataires et duree de conservation.",
        styles["body"]))
    story.append(Spacer(1, 4))
    processings = register_data.get("processings") or DEFAULT_PROCESSINGS
    story.append(_processing_table(processings, styles))
    story.append(Spacer(1, 8))

    # === 3. Sous-traitants ===
    story.append(PageBreak())
    story.append(Paragraph("3. Sous-traitants", styles["h2"]))
    story.append(Paragraph(
        "Liste des sous-traitants au sens de l'article 28 RGPD. Chaque sous-traitant est encadre par un contrat "
        "de sous-traitance (DPA) prevoyant les instructions du responsable, les mesures de securite et le sort des donnees en fin de contrat.",
        styles["body"]))
    story.append(Spacer(1, 4))
    subprocs = register_data.get("subprocessors") or DEFAULT_SUBPROCESSORS
    story.append(_subprocessor_table(subprocs, styles))
    story.append(Spacer(1, 8))

    # === 4. Mesures de securite ===
    story.append(Paragraph("4. Mesures techniques et organisationnelles (art. 32)",
                           styles["h2"]))
    story.append(Paragraph(
        "Ensemble des mesures mises en oeuvre pour garantir un niveau de securite adapte au risque, conformement a "
        "l'article 32 RGPD.",
        styles["body"]))
    story.append(Spacer(1, 4))
    measures = register_data.get("security_measures") or DEFAULT_SECURITY_MEASURES
    story.append(_security_list(measures, styles))
    story.append(Spacer(1, 10))

    # === 5. Droits des personnes ===
    story.append(Paragraph("5. Exercice des droits des personnes concernees", styles["h2"]))
    story.append(Paragraph(
        "Les personnes concernees peuvent exercer les droits suivants prevus aux articles 15 a 22 du RGPD :",
        styles["body"]))
    story.append(Spacer(1, 3))
    droits = [
        "Droit d'acces (art. 15) — obtenir la confirmation que des donnees sont traitees et en recevoir une copie",
        "Droit de rectification (art. 16) — corriger des donnees inexactes ou incompletes",
        "Droit a l'effacement (art. 17) — obtenir la suppression des donnees (sauf obligations legales de conservation)",
        "Droit a la limitation (art. 18) — geler temporairement le traitement",
        "Droit a la portabilite (art. 20) — recuperer les donnees dans un format structure (export JSON dans la plateforme)",
        "Droit d'opposition (art. 21) — s'opposer a un traitement fonde sur l'interet legitime",
        "Droit d'introduire une reclamation aupres de l'APD (Autorite de protection des donnees) belge",
    ]
    droits_rows = [[Paragraph(f"• {d}", styles["cell"])] for d in droits]
    dt = Table(droits_rows, colWidths=[183*mm])
    dt.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(dt)
    story.append(Spacer(1, 6))

    # Autorite de controle
    story.append(Paragraph("6. Autorite de controle competente", styles["h2"]))
    apd = Table([[
        Paragraph("Autorite de protection des donnees (APD - Belgique)<br/>"
                  "Rue de la Presse 35, 1000 Bruxelles<br/>"
                  "contact@apd-gba.be — Tel. +32 (0)2 274 48 00<br/>"
                  "<a href='https://www.autoriteprotectiondonnees.be'>www.autoriteprotectiondonnees.be</a>",
                  styles["body"]),
    ]], colWidths=[183*mm])
    apd.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(apd)
    story.append(Spacer(1, 15))

    # Signature
    story.append(Paragraph(
        "Fait a ______________________, le ___/___/______",
        styles["body"]))
    story.append(Spacer(1, 20))
    sig_table = Table([[
        Paragraph("<b>Signature du responsable de traitement</b><br/>"
                  "(Nom, prenom, qualite)", styles["small"]),
        Paragraph("<b>Cachet de l'entreprise</b>", styles["small"]),
    ]], colWidths=[95*mm, 90*mm])
    sig_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, -1), 0.5, SLATE_500),
        ("TOPPADDING", (0, 0), (-1, -1), 30),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_table)

    if use_new_layout:
        # Utilise le pied de page unifie avec mentions legales + numeros de page
        footer_cb = make_footer_callback(syndic_pdf_ctx.get("legal_mentions", ""))
        doc.build(story, onFirstPage=footer_cb, onLaterPages=footer_cb)
    else:
        doc.build(story,
                  onFirstPage=_footer_maker("Registre RGPD - NextGe Copro"),
                  onLaterPages=_footer_maker("Registre RGPD - NextGe Copro"))
    buf.seek(0)
    return buf.getvalue()
