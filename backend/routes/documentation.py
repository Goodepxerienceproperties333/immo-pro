"""Documentation & Commercial PDF (iter90br).

Genere un PDF commercial complet listant l'integralite des
fonctionnalites de CoproManager, utilisable comme argument commercial
(brochure client, appel d'offres, kit de presentation).

Endpoint : GET /api/documentation/features-pdf
Reponse : application/pdf en telechargement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)

BLUE = colors.HexColor("#2563EB")
BLUE_DARK = colors.HexColor("#1D4ED8")
SLATE_900 = colors.HexColor("#0F172A")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748B")
SLATE_200 = colors.HexColor("#E2E8F0")
SLATE_50 = colors.HexColor("#F8FAFC")
GREEN = colors.HexColor("#10B981")
AMBER = colors.HexColor("#F59E0B")
VIOLET = colors.HexColor("#8B5CF6")

FEATURES = [
    {
        "section": "Structure de la copropriete",
        "icon": "•",
        "color": BLUE,
        "features": [
            ("Gestion multi-ACP", "Cloisonnement strict des donnees entre coproprietes (Chinese Walls). Chaque ACP est isolee : plan comptable, exercices fiscaux, factures, extraits bancaires. Cohabitation possible d'un portefeuille illimite de coproprietes."),
            ("Fiche ACP complete", "Enregistrement des donnees legales (nom, adresse, BCE, RGPD DPO), coordonnees bancaires multiples (compte a vue, compte epargne, compte reserve), quotites par lot, calcul automatique des tantiemes."),
            ("Lots et quotites", "Enregistrement de chaque lot (numero, surface, quotites, type), historique des mutations, calcul automatique des cles de repartition selon quotites."),
            ("Proprietaires et locataires", "Fiches completes avec coordonnees, comptes IBAN, historique des mutations. Traitement RGPD conforme (droit d'acces, effacement, rectification)."),
            ("Fournisseurs partages", "Base de fournisseurs par syndic avec detection automatique des doublons (numero BCE + normalisation des raisons sociales, filtre des particules juridiques SA/SPRL/SRL/...)."),
        ],
    },
    {
        "section": "Comptabilite belge (PCMN)",
        "icon": "•",
        "color": VIOLET,
        "features": [
            ("Plan comptable minimum normalise", "Support complet du PCMN belge (arrete royal 21/10/2018). Auto-generation du plan par ACP a la creation. Gestion des comptes actifs/inactifs, comptes de tiers proprietaires/fournisseurs (410xxx, 440xxx)."),
            ("Exercices fiscaux", "Ouverture/cloture par exercice, verrous fiscaux (impossible d'ecrire dans un exercice clos), report automatique de l'A-nouveau, calcul du resultat en imputation aux proprietaires (compte 499)."),
            ("Journaux comptables", "Journal des Achats (AC), des Ventes (VE), Financier (FI), Operations Diverses (OD), A-Nouveaux (AN). Ecritures en double partie stricte, contre-passation en un clic pour devalider un extrait."),
            ("Grand livre", "Consultation par compte, filtre par exercice et periode, calcul des soldes, export PDF/Excel."),
            ("Bilan et compte de resultat", "Bilan actif/passif avant et apres repartition (compte 499). Compte de resultat par nature. Balance tiers proprietaires. Certificat fiscal annuel."),
        ],
    },
    {
        "section": "Facturation et depenses",
        "icon": "•",
        "color": VIOLET,
        "features": [
            ("Import PDF/CSV intelligent", "Extraction IA (Claude Sonnet 4.5) des factures scannees. Reconnaissance automatique du fournisseur, montant, TVA, echeance, numero. Batch import multi-fichiers avec parallelisation."),
            ("Factures multi-lignes", "Une facture, plusieurs natures de depense reparties avec cles de repartition differentes par ligne. Commentaire par ligne visible dans la liste des depenses."),
            ("Auto-apprentissage fournisseur", "Le systeme apprend la nature de depense la plus utilisee par fournisseur et la pre-remplit automatiquement lors de la saisie."),
            ("Frais privatifs (a charge d'un seul proprietaire)", "Support des allocations privatives multi-proprietaires (repartition d'une facture entre plusieurs beneficiaires)."),
            ("Anti-doublons strict", "Blocage automatique si meme numero + fournisseur + ACP. Message clair guidant l'utilisateur."),
            ("Ventilation Occupant / Proprietaire", "Chaque nature de depense peut avoir un ratio de repartition Occupant vs Proprietaire (ex: 70% locataire, 30% proprietaire)."),
            ("Templates fournisseur", "Memorisation automatique des reglages recurrents par fournisseur (nature, compte, cle)."),
        ],
    },
    {
        "section": "Finance et banque",
        "icon": "•",
        "color": GREEN,
        "features": [
            ("Import CODA (Isabel)", "Import natif du format CODA belge (extrait de compte standardise). Extraction automatique des transactions, comptes contreparties, communications."),
            ("Import PDF bancaire IA", "Support des PDF de tous les principaux etablissements belges (BNP, ING, KBC, Belfius, Fintro, Beobank). Extraction IA robuste avec OCR de fallback."),
            ("Lettrage automatique", "Rapprochement automatique des transactions avec les factures et appels de fonds (matching sur communication structuree, montant, IBAN)."),
            ("Splits multi-natures", "Une seule transaction bancaire peut etre repartie sur plusieurs natures de depense (utile pour les prelevements groupes)."),
            ("Virements internes (compte 58)", "Support des transferts entre compte a vue et compte epargne (pas de repartition proprietaire, ecriture 58/55)."),
            ("Comptes bancaires multiples", "Une ACP peut avoir plusieurs comptes bancaires (vue, epargne, reserve, travaux). Badge de couleur distinctif par compte."),
            ("Ecritures financieres automatiques", "Chaque transaction categorisee genere automatiquement les ecritures FI en double partie."),
        ],
    },
    {
        "section": "Appels de fonds et decomptes",
        "icon": "•",
        "color": AMBER,
        "features": [
            ("Appels de fonds trimestriels", "Generation en masse selon le budget approuve. Personnalisation par proprietaire (quotites, montants specifiques, communications structurees)."),
            ("Budget wizard", "Assistant de creation de budget previsionnel avec ventilation par nature, projection sur l'exercice, comparaison budget/realise."),
            ("Decompte de mutation PDF", "Generation automatique lors d'un changement de proprietaire. Format legal belge (article 3.87 CCiv). PDF clair sans jargon comptable."),
            ("Balance tiers proprietaires", "Vue complete des soldes de chaque proprietaire, avec detail des factures, appels et paiements. Export PDF individuel."),
            ("Rappels de paiement automatiques", "Job APScheduler quotidien detectant les debiteurs. Envoi automatique de relances via templates emails personnalisables. Escalade progressive (1er rappel > 30j, 2eme > 60j, mise en demeure > 90j)."),
        ],
    },
    {
        "section": "Communication et emailing",
        "icon": "•",
        "color": GREEN,
        "features": [
            ("Microsoft Graph natif", "Integration MS Graph par syndic. Envoi depuis la boite mail professionnelle du syndic (welcome@societe.be), delivrabilite optimale, historique dans Outlook. Credentials Azure AD chiffres AES."),
            ("Templates d'email", "CRUD complet des modeles (situation debitrice, appel de fonds, avis AG, decompte annuel). Editeur riche. Variables dynamiques (nom, montant, echeance)."),
            ("Envoi de situations", "Envoi en 1 clic de la situation debitrice du proprietaire selectionne. Template selectionnable."),
            ("Documents en piece jointe", "Attachement automatique du decompte PDF, situation, statut de compte."),
            ("Multi-mailbox", "Support de plusieurs adresses d'expedition par syndic (welcome@, contact@, comptabilite@)."),
        ],
    },
    {
        "section": "Portail proprietaire self-service",
        "icon": "•",
        "color": BLUE,
        "features": [
            ("Login separe", "Authentification independante pour les proprietaires (email + mot de passe). Interface simplifiee sans jargon comptable."),
            ("Consultation situation", "Vue de leurs appels de fonds, paiements, solde. Historique complet."),
            ("Documents en telechargement", "Acces aux PV d'AG, budgets approuves, decomptes annuels, statuts de compte, en 1 clic."),
            ("Paiement en ligne (roadmap)", "Support Stripe/Bancontact/Payconiq prevu pour reglement direct depuis le portail."),
            ("Compteurs (roadmap)", "Saisie autonome des releves de compteurs (eau, chauffage, electricite) avec historisation et repartition selon consommation."),
        ],
    },
    {
        "section": "Import de donnees et migration",
        "icon": "•",
        "color": VIOLET,
        "features": [
            ("Import Optipro / Sage", "Wizard d'import guidee des donnees Optipro (concurrent belge). Mapping automatique des lots, proprietaires, factures, balances."),
            ("Import Excel/CSV", "Import universel de listes (lots, proprietaires, fournisseurs, factures) avec preview + validation en batch."),
            ("Detection de doublons a l'import", "Detection automatique des doublons de fournisseurs et proprietaires (BCE, IBAN, nom normalise)."),
            ("Bundle IA (roadmap)", "Analyse d'un dossier ZIP contenant plusieurs documents heterogenes (factures, extraits, PV) et attribution automatique."),
        ],
    },
    {
        "section": "Rapports et exports",
        "icon": "•",
        "color": AMBER,
        "features": [
            ("Bilan comptable PDF", "Format 100% conforme aux normes belges. Vue avant/apres repartition. Signature superadmin optionnelle."),
            ("Compte de resultat PDF", "Par nature (charges/produits), avec sous-totaux par section."),
            ("Balance tiers PDF", "Detail par proprietaire, individuel ou groupe."),
            ("Grand livre par compte", "Export PDF ou Excel."),
            ("Certificat fiscal annuel", "Document pret pour la declaration ISOC/IPP du proprietaire (roadmap P2)."),
            ("Decomptes de mutation", "PDF automatique en cas de vente d'un lot, format legal 3.87 CCiv."),
            ("Journal des ecritures", "Export complet en PDF, tri par journal, filtres par date."),
        ],
    },
    {
        "section": "Securite et conformite",
        "icon": "•",
        "color": VIOLET,
        "features": [
            ("Cloisonnement Chinese Wall", "Impossible pour un syndic gerant plusieurs ACPs de voir/modifier les donnees d'une autre ACP. Verification a chaque requete via header X-Copropriete-Id + verification serveur."),
            ("RBAC roles", "Superadmin, Admin, Syndic, Gestionnaire, Owner. Chaque role a des permissions strictes verifiees en middleware."),
            ("Rate limiting slowapi", "Protection anti-DDOS et anti-brute-force sur les endpoints sensibles (login, password reset, PDF generation)."),
            ("Cookies HttpOnly + Secure", "Tokens JWT stockes en cookies HTTP-only avec attributs Secure et SameSite=Lax."),
            ("Chiffrement AES Fernet", "Les credentials MS Graph (tenant, client, secret) sont chiffres au repos en base de donnees."),
            ("Sanitization XSS", "Tous les contenus HTML rendus (templates emails, previews) sont sanitizes par DOMPurify avec whitelist stricte."),
            ("Legal blindage", "CGU + Politique de confidentialite + Mentions legales + Politique cookies + Disclaimer accessibles publiquement. Acceptation obligatoire au login avec traabilite (versions + date)."),
            ("RGPD", "Registre des traitements, journal d'audit complet, droit a l'effacement (soft-delete 30j puis purge)."),
            ("Backups journaliers", "APScheduler declenche a 00h00 Brussels time, dump JSON complet par ACP, archive ZIP telechargeable par le syndic. Purge automatique apres 90 jours."),
        ],
    },
    {
        "section": "Administration et exploitation",
        "icon": "•",
        "color": AMBER,
        "features": [
            ("Journal d'audit", "Trace de toutes les actions administratives (creation user, deletion ACP, modification permissions). Consultation superadmin."),
            ("Outils de deblocage superadmin", "Reinitialisation mot de passe, deverrouillage compte apres brute-force, purge d'un user, migration de donnees."),
            ("Historique de connexion", "Toutes les connexions loggees (email, IP, user-agent, statut). Detection des tentatives suspectes."),
            ("Gestion des utilisateurs", "CRUD complet des utilisateurs, invitation par email, changement de role, gestion des acces par ACP."),
            ("Configuration par syndic", "Chaque syndic configure independamment sa boite mail, ses signatures, ses templates emails, ses branches actives."),
            ("Notes de version automatiques", "Popup au login informant des dernieres mises a jour. L'utilisateur clique OK pour continuer. Categorisation feature/fix/security/breaking."),
        ],
    },
    {
        "section": "Intelligence artificielle",
        "icon": "•",
        "color": VIOLET,
        "features": [
            ("Claude Sonnet 4.5 (Emergent Universal Key)", "Extraction structuree de PDF factures/extraits, generation d'ecritures, suggestions de rapprochement. Aucune donnee client n'est envoyee a des tiers non-controles."),
            ("OCR de fallback", "Tesseract active automatiquement si le PDF n'a pas de texte extractible (scans a l'ancienne)."),
            ("Detection d'anomalies", "Sante comptable analysee en temps reel : factures > 60j impayees, doublons, orphelins, desequilibres, proprietaires en retard."),
            ("Actions rapides adaptatives", "Le tableau de bord affiche les 6 actions les plus pertinentes selon l'usage du user (apprentissage par tracking)."),
        ],
    },
]

TECH_SPECS = [
    ("Backend", "FastAPI (Python 3.11) - Async/await, Motor MongoDB, Pydantic v2, JWT + slowapi rate limiting"),
    ("Frontend", "React 19 + Tailwind CSS + Shadcn UI (Radix) - Design system moderne, responsive desktop/mobile"),
    ("Base de donnees", "MongoDB 6.x avec GridFS pour les fichiers (factures, extraits, PV) - Backups journaliers"),
    ("Comptabilite", "PCMN belge (arrete royal 21/10/2018) - Double partie stricte - Exercices verrouilles"),
    ("IA integree", "Claude Sonnet 4.5 (via Emergent Universal Key) - Extraction PDF, OCR Tesseract fallback"),
    ("Email", "Microsoft Graph API par syndic (Azure AD) - Chiffrement AES des credentials"),
    ("Hebergement", "Kubernetes cloud europeen - HTTPS strict - Backup off-site journalier"),
    ("Legal", "CGU / Confidentialite / Mentions legales / Cookies - Conforme RGPD"),
]


def _build_pdf() -> bytes:
    """Construit le PDF commercial complet."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=1.5 * cm, bottomMargin=2 * cm,
        title="CoproManager - Fonctionnalites",
        author="CoproManager",
    )
    styles = getSampleStyleSheet()
    story: list = []

    # Styles custom
    h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"],
        fontName="Helvetica-Bold", fontSize=28, textColor=BLUE_DARK,
        alignment=TA_LEFT, spaceAfter=8, leading=32,
    )
    h1_center = ParagraphStyle("h1c", parent=h1, alignment=TA_CENTER)
    subtitle = ParagraphStyle(
        "sub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=13, textColor=SLATE_500,
        alignment=TA_CENTER, spaceAfter=24, leading=18,
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=16, textColor=BLUE_DARK,
        spaceBefore=18, spaceAfter=8, leading=20,
    )
    feat_title = ParagraphStyle(
        "ft", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=11, textColor=SLATE_900,
        spaceAfter=2, leading=14,
    )
    feat_desc = ParagraphStyle(
        "fd", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9.5, textColor=SLATE_700,
        alignment=TA_JUSTIFY, spaceAfter=8, leading=13.5,
    )
    body = ParagraphStyle(
        "body", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10.5, textColor=SLATE_700,
        alignment=TA_JUSTIFY, leading=15, spaceAfter=10,
    )

    # ==================== PAGE DE COUVERTURE ====================
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("CoproManager", h1_center))
    story.append(Paragraph(
        "La plateforme complete de gestion de copropriete<br/>"
        "conforme au droit belge",
        subtitle,
    ))
    story.append(Spacer(1, 2 * cm))
    # Bloc bleu resume
    intro_table = Table([[
        Paragraph(
            "<b>Une solution moderne, securisee et intelligente</b><br/><br/>"
            "CoproManager est la premiere plateforme de gestion de "
            "copropriete belge nativement pensee pour les syndics "
            "professionnels : comptabilite PCMN stricte, imports "
            "bancaires IA, portail proprietaire self-service, "
            "communication Microsoft Graph, verrouillage fiscal et "
            "backups journaliers.",
            ParagraphStyle("intro", fontName="Helvetica", fontSize=11,
                           textColor=colors.white, leading=16,
                           alignment=TA_JUSTIFY),
        )
    ]], colWidths=[17 * cm])
    intro_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
        ("TOPPADDING", (0, 0), (-1, -1), 20),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 20),
        ("ROUNDEDCORNERS", (0, 0), (-1, -1), [8, 8, 8, 8]),
    ]))
    story.append(intro_table)
    story.append(Spacer(1, 3 * cm))
    # Chiffres cles (facteur "wow")
    kpi_data = [[
        Paragraph("<b>10+</b><br/>Modules", ParagraphStyle(
            "kpi", fontName="Helvetica", fontSize=14,
            textColor=BLUE_DARK, alignment=TA_CENTER, leading=20)),
        Paragraph("<b>PCMN 2018</b><br/>Conforme", ParagraphStyle(
            "kpi", fontName="Helvetica", fontSize=14,
            textColor=BLUE_DARK, alignment=TA_CENTER, leading=20)),
        Paragraph("<b>RGPD</b><br/>Blindage complet", ParagraphStyle(
            "kpi", fontName="Helvetica", fontSize=14,
            textColor=BLUE_DARK, alignment=TA_CENTER, leading=20)),
        Paragraph("<b>IA native</b><br/>Claude Sonnet", ParagraphStyle(
            "kpi", fontName="Helvetica", fontSize=14,
            textColor=BLUE_DARK, alignment=TA_CENTER, leading=20)),
    ]]
    kpi_table = Table(kpi_data, colWidths=[4.25 * cm] * 4)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 1, SLATE_200),
        ("LINEBEFORE", (1, 0), (-1, -1), 1, SLATE_200),
        ("TOPPADDING", (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(
        f"Document genere le {datetime.now(timezone.utc).strftime('%d/%m/%Y')}",
        ParagraphStyle("footer", fontName="Helvetica-Oblique",
                       fontSize=9, textColor=SLATE_500,
                       alignment=TA_CENTER),
    ))
    story.append(PageBreak())

    # ==================== EXECUTIVE SUMMARY ====================
    story.append(Paragraph("Pourquoi CoproManager ?", h1))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Le marche belge de la gestion de copropriete est domine par des "
        "outils vieillissants (Optipro, Sage, Excel). Aucun ne combine :",
        body,
    ))
    story.append(Paragraph(
        "<b>1. Une comptabilite PCMN native et stricte</b> — pas de "
        "bricolage, pas de compatibilite Excel douteuse. Ecritures en "
        "double partie, verrouillage fiscal reel, bilan avant/apres "
        "repartition conforme.",
        body,
    ))
    story.append(Paragraph(
        "<b>2. L'automatisation par l'IA</b> — extraction PDF factures "
        "en 1 clic (~10 secondes), rapprochement bancaire automatique, "
        "apprentissage des habitudes fournisseur.",
        body,
    ))
    story.append(Paragraph(
        "<b>3. Un portail proprietaire moderne</b> — vos coproprietaires "
        "peuvent consulter leur situation, telecharger leurs decomptes "
        "et payer en ligne, sans intervention du syndic.",
        body,
    ))
    story.append(Paragraph(
        "<b>4. La securite et la conformite juridique belge</b> — RGPD "
        "blindage complet, CGU pretes a l'emploi, journal d'audit, "
        "backups journaliers, chiffrement AES des credentials.",
        body,
    ))
    story.append(Paragraph(
        "<b>5. La communication professionnelle</b> — envoi d'emails "
        "depuis votre propre boite Outlook (Microsoft Graph) : vos "
        "proprietaires voient vos vraies coordonnees, votre logo, votre "
        "signature. Fini les mails perdus dans les SPAM.",
        body,
    ))
    story.append(PageBreak())

    # ==================== FONCTIONNALITES DETAILLEES ====================
    story.append(Paragraph("Toutes les fonctionnalites", h1))
    story.append(Spacer(1, 12))

    for group in FEATURES:
        # Titre de section avec bande coloree
        section_table = Table([[
            Paragraph(
                f"<font color='white'><b>{group['section']}</b></font>",
                ParagraphStyle("st", fontName="Helvetica-Bold",
                               fontSize=13, textColor=colors.white,
                               leading=16),
            )
        ]], colWidths=[17 * cm])
        section_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), group["color"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([section_table, Spacer(1, 8)]))

        for title, desc in group["features"]:
            item = [
                Paragraph(f"▸ {title}", feat_title),
                Paragraph(desc, feat_desc),
            ]
            story.append(KeepTogether(item))
        story.append(Spacer(1, 10))

    # ==================== SPECS TECHNIQUES ====================
    story.append(PageBreak())
    story.append(Paragraph("Specifications techniques", h1))
    story.append(Spacer(1, 12))

    spec_data = [
        [
            Paragraph(f"<b>{key}</b>", ParagraphStyle(
                "sk", fontName="Helvetica-Bold", fontSize=10,
                textColor=SLATE_900)),
            Paragraph(val, ParagraphStyle(
                "sv", fontName="Helvetica", fontSize=9.5,
                textColor=SLATE_700, leading=13)),
        ]
        for key, val in TECH_SPECS
    ]
    spec_table = Table(spec_data, colWidths=[4 * cm, 13 * cm])
    spec_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_200),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, SLATE_200),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(spec_table)

    # ==================== CONCLUSION COMMERCIALE ====================
    story.append(PageBreak())
    story.append(Paragraph("Prochaines etapes", h1))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<b>Envie de tester CoproManager sur vos coproprietes ?</b>",
        body,
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Nous vous proposons :", body,
    ))
    story.append(Paragraph(
        "&#8226; <b>Une demo personnalisee</b> sur vos donnees reelles (1h)",
        body,
    ))
    story.append(Paragraph(
        "&#8226; <b>Une periode d'essai gratuite</b> avec import de vos "
        "donnees Optipro/Sage",
        body,
    ))
    story.append(Paragraph(
        "&#8226; <b>Un accompagnement de migration</b> assure (mapping, "
        "formation, hotline)",
        body,
    ))
    story.append(Spacer(1, 20))

    contact_table = Table([[
        Paragraph(
            "<b>Contact commercial</b><br/><br/>"
            "welcome@goodexperienceproperties.be<br/>"
            "https://immo-pcmn.emergent.host",
            ParagraphStyle("contact", fontName="Helvetica",
                           fontSize=11, textColor=colors.white,
                           leading=18, alignment=TA_CENTER),
        )
    ]], colWidths=[17 * cm])
    contact_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLUE_DARK),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
        ("TOPPADDING", (0, 0), (-1, -1), 20),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 20),
    ]))
    story.append(contact_table)

    doc.build(story)
    return buf.getvalue()


def create_documentation_router(db):
    router = APIRouter(prefix="/api/documentation", tags=["documentation"])

    @router.get("/features-pdf")
    async def features_pdf():
        """Genere et retourne le PDF commercial complet."""
        pdf_bytes = _build_pdf()
        filename = f"CoproManager-Fonctionnalites-{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-cache",
            },
        )

    return router
