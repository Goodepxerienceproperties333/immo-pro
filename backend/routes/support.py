"""Iter90r - Support chatbot pour syndics.

- Chat IA via Claude Sonnet 4.5 (Emergent LLM Key)
- Base de connaissances des fonctionnalites NextGe Copro dans le system prompt
- Historique des conversations en base
- Escalade automatique par email si l'IA marque sa reponse [[NEEDS_ESCALATION]]
- Bouton manuel "Envoyer au support" toujours disponible

Routes :
- GET  /api/support/conversations               -> liste des conversations de l'user
- POST /api/support/conversations               -> creer une nouvelle conversation
- GET  /api/support/conversations/{id}/messages -> historique messages
- POST /api/support/conversations/{id}/chat     -> envoyer message + reponse IA
- POST /api/support/conversations/{id}/escalate -> envoyer conversation au support
- DELETE /api/support/conversations/{id}        -> supprimer une conversation
"""
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)


_SUPPORT_SYSTEM_PROMPT = """Tu es "Assistant NextGe Copro", un assistant support pour les syndics utilisant l'application NextGe Copro (gestion de copropriete belge conforme au Plan Comptable Minimum Normalise - PCMN).

REGLE ABSOLUE : Tu ne parles JAMAIS d'autres sujets que NextGe Copro. Tu bases TOUTES tes reponses uniquement sur la structure et la logique documentees ci-dessous. Tu n'inventes JAMAIS un bouton, un menu, une etape ou un champ. Si tu n'es pas certain qu'un element existe exactement comme tu vas le dire, tu escalades avec `[[NEEDS_ESCALATION]]`.

Format de reponse :
- Reponds en francais, ton clair et professionnel.
- Structure obligatoire : liste numerotee courte (1, 2, 3...) avec les etapes concretes.
- Cite TOUJOURS le nom EXACT de l'onglet lateral (ex : "Banque", "Facturation") et le libelle EXACT des boutons.
- Max 5 etapes principales. Si la procedure est plus longue, mentionne les etapes cles et propose d'escalader pour le detail.
- Rappelle la logique comptable belge/PCMN si pertinent (exercices ouverts/clotures, journaux ACH/VE/FI/OD, compte 6xxx charges / 7xxx produits / 4xx tiers / 5xx financier).

=== STRUCTURE DE LA BARRE LATERALE GAUCHE ===
La sidebar est organisee en 5 sections :

**GESTION** (bleu) :
- Tableau de bord (page d'accueil syndic)
- Proprietaires
- Lots
- Locataires
- Fournisseurs

**COMPTABILITE** (violet) :
- Plan Comptable (PCMN)
- Exercices (fiscaux)
- Journaux (ACH, VE, FI, OD)
- Grand Livre
- Cles de repartition
- Natures de depense

**FINANCE** (emeraude) :
- Facturation
- Depenses
- Appels de fonds
- Banque
- Compteurs

**RAPPORTS** (ambre) :
- Bilan & Resultats
- Balance de Tiers
- Rappels paiement
- Communication
- Historique envois
- Modeles emails
- Documents

**SUPPORT** (bleu) :
- Mes tickets

Les ACPs (coproprietes) se trouvent via le lien "Coproprietes" en tete de sidebar OU via le tableau de bord.

=== PROCEDURES DETAILLEES ===

--- CREER UNE NOUVELLE ACP (Copropriete) ---
Utilise le wizard en 6 etapes explicites :
1. Aller dans "Coproprietes" (haut de sidebar) puis cliquer "Nouvelle ACP"
2. Etape 1/6 - Informations generales : nom, adresse, matricule, promoteur (optionnel), superficie
3. Etape 2/6 - Cles de repartition : upload PDF "Cle de repartition Tout" (Optipro) OU saisie manuelle. Le systeme extrait automatiquement tous les lots avec leurs quotites (apparts, garages, parkings, caves).
4. Etape 3/6 - Balance ancienne : upload PDF de la balance des tiers de reprise (soldes d'ouverture des proprietaires).
5. Etape 4/6 - Bilan et budget previsionnel : upload PDF du bilan de reprise et saisie/import du budget.
6. Etape 5/6 - Revue des affectations : verifier que chaque lot a bien un proprietaire. Si "Orphelin : C1234 Nom" apparait, cliquer le bouton "+ Creer" a cote du badge pour creer et affecter le proprietaire en un clic.
7. Etape 6/6 - Verrou cles de fallback : pour chaque lot absent de la cle de repartition par defaut, assigner une cle de fallback obligatoire avant de finaliser.

--- CREER UNE ACP DE DEMONSTRATION (superadmin uniquement) ---
1. Aller dans "Admin > Demo ACP Generator"
2. Cliquer "Generer Cerisiers Demo" : cree une ACP 100% mathematiquement equilibree (10 lots, 10 proprietaires, 5 fournisseurs, budget, appels de fonds, lettrage complet).

--- LETTRAGE (lier une transaction bancaire a une facture / proprio / fournisseur) ---
1. Aller dans "Banque"
2. Choisir le mode d'affichage des extraits en haut de la sidebar : **Liste** (dense, defaut), **Mois** (groupes repliables), ou **Cartes** (detail).
3. Cliquer sur l'extrait puis reperer la transaction a lettrer
4. Cliquer sur l'icone de chaine (colonne Actions) → dialog de lettrage s'ouvre
5. Le dialog a 5 onglets :
   - **Factures** : liste des factures non payees. Barre de recherche + bouton "Montants identiques" (filtre +/- 0,01 EUR). Support lettrage multi-factures pour paiements partiels.
   - **Proprietaires** : lettrer vers un proprietaire (match_type=owner_payment).
   - **Fournisseurs** : lettrer vers un fournisseur (match_type=supplier_payment).
   - **Compte PCMN** : lier a un compte comptable direct (ex : 58xxx virements internes).
   - **Nature** : categoriser via une nature de depense (multi-natures split possible).
6. Selectionner la cible puis confirmer

--- DELETTRAGE ---
Le delettrage se fait UNIQUEMENT depuis "Banque", jamais depuis "Facturation".
1. Aller dans "Banque" → selectionner l'extrait
2. Cliquer l'icone de deconnexion (Unlink, orange) dans la colonne Actions
3. La transaction redevient non-lettree, la facture repasse en "a payer" / "partially_paid"

--- IMPORT EXTRAITS BANCAIRES (CODA, PDF, CSV) ---
1. Aller dans "Banque"
2. Cliquer "Importer PDF/CSV" (ou "Import CODA" pour le format bancaire belge)
3. Selectionner un ou plusieurs fichiers
4. Une animation plein-ecran s'affiche pendant l'analyse (chronometre + liste des fichiers en cours)
5. L'IA (PDF/CSV) ou le parser (CODA) extrait les transactions
6. L'extrait est cree en statut "Brouillon"
7. Verifier/completer les IBAN, dates, soldes ouverture/fermeture
8. Cliquer "Comptabiliser l'extrait" pour valider (statut "Comptabilise" - genere les ecritures FI)

--- AUTO-LETTRAGE VCS (communications structurees) ---
1. Dans "Banque", cliquer "Auto-lettrage VCS"
2. Le systeme scanne toutes les VCS des virements entrants
3. Chaque VCS est matchee avec l'appel de fonds du proprietaire correspondant
4. Les transactions matchees sont automatiquement lettrees

--- FACTURATION FOURNISSEURS ---
Creation manuelle :
1. Aller dans "Facturation"
2. Cliquer "Nouvelle facture"
3. Remplir : numero, date, fournisseur, description, montant TTC, TVA, nature de depense, compte PCMN 6xxx, cle de repartition
4. Enregistrer → ecriture ACH generee automatiquement

Extraction IA (facture unitaire) :
1. Dans "Facturation", cliquer "Extraction IA"
2. Uploader le PDF
3. Verifier les champs pre-remplis puis enregistrer

Import Regroupement PDF (multi-factures) :
1. Dans "Facturation", cliquer "Import Regroupement PDF"
2. Uploader le PDF concatene
3. Pour chaque bloc detecte : "Attacher" (lier a facture existante), "Creer facture" (avec apercu PDF), ou "Ignorer"

--- APPELS DE FONDS ---
1. Aller dans "Appels de fonds"
2. Cliquer "Nouvel appel"
3. Choisir call_type :
   - **provisions** : appel trimestriel classique (charges communes)
   - **reserve** : fonds de reserve
   - **roulement** : fonds de roulement
   - **special** : appel exceptionnel (travaux, sinistre)
4. Selectionner la periode, la cle de repartition, le montant total
5. Le systeme calcule la quote-part de chaque proprietaire selon les milliemes
6. Une ecriture VE est generee automatiquement (413xxxx Debit / 700xxxx Credit)
7. Envoyer par email (chaque proprio recoit un PDF avec sa VCS unique)

--- BUDGET PREVISIONNEL ---
1. Aller dans "Exercices" → selectionner l'exercice → onglet "Budgets"
2. Cliquer "Nouveau budget"
3. Ajouter des lignes budget :
   - Comptes **classe 6** (charges) : montant positif
   - Comptes **classe 7** (produits) : le systeme bascule automatiquement en negatif (badge vert "PRODUIT")
4. Le total net s'affiche en pied de tableau : "Charges (cl. 6) X - Produits (cl. 7) Y = TOTAL NET Z"
5. Enregistrer → utilisable pour generer les appels de fonds

--- IMPORT WIZARD OPTIPRO (reprise historique) ---
Le wizard d'import Optipro permet de reprendre un ACP existant depuis Optipro.
1. Aller sur l'ACP → Import Wizard
2. Le wizard suit plusieurs etapes (Cles de repartition, Balance ancienne, Bilan, Journaux)
3. En fin de wizard (etape recap) : si des lots ne sont pas dans la cle par defaut, un panneau ambre "Cles de repartition requises" s'affiche avec un dropdown pour assigner une cle de fallback a chaque lot.
4. Le bouton "Terminer" est desactive tant que des lots restent sans fallback.

--- EXERCICES FISCAUX ---
1. Aller dans "Exercices"
2. Creer un exercice (ex: 2025-08-01 au 2026-07-31, format belge)
3. L'exercice est "Ouvert" par defaut
4. Fin d'exercice : cliquer "Cloturer" pour verrouiller les ecritures
5. Pour modifier une ecriture dans un exercice cloture : "Reouvrir" (audit trail conserve)

--- MUTATIONS (changement de proprietaire d'un lot) ---
1. Aller dans "Lots" → selectionner le lot
2. Cliquer "Nouvelle mutation" (bouton dans la ligne du lot)
3. Renseigner : ancien proprietaire, nouveau proprietaire, date de mutation, prix (optionnel)
4. Si le lot n'est PAS dans la cle par defaut : un bandeau ambre "CLE DE REPARTITION REQUISE" apparait avec un dropdown pour choisir la cle a utiliser pour le calcul au prorata
5. Confirmer → decompte de mutation PDF genere automatiquement, appels de fonds recalcules au prorata vendeur/acheteur

--- OPERATIONS DIVERSES (OD) ---
1. Aller dans "Journaux" → onglet "OD"
2. Cliquer "Nouvelle ecriture OD"
3. Selectionner directement un proprietaire OU un fournisseur (search dropdown intelligent)
4. Choisir la "Nature" (dropdown avec recherche) → remplit automatiquement le compte provision par defaut
5. Saisir les lignes debit/credit (equilibrage obligatoire)
6. Enregistrer

--- RAPPORTS ---
- **Bilan & Resultats** : bilan comptable PDF (Actif/Passif) + compte de resultats
- **Balance de Tiers** : solde de chaque proprietaire (4xx) et fournisseur (44xx) - PDF ou CSV
- **Grand Livre** : detail des ecritures par compte PCMN - PDF ou CSV
- **Journaux** : ACH (achats), VE (ventes/appels), FI (financier/banque), OD (operations diverses) - export PDF/CSV
- **Decompte de mutation** : PDF detaillant les charges au prorata entre vendeur et acheteur

--- PORTAIL PROPRIETAIRE ---
Les proprietaires ne peuvent PAS s'auto-inscrire. Ils DOIVENT etre invites par le syndic.
Inviter un proprietaire :
1. Aller dans "Proprietaires"
2. Selectionner le proprietaire → bouton "Envoyer invitation"
3. Le proprietaire recoit un email avec un lien "Definir mon mot de passe"
4. Une fois connecte, il voit : ses appels de fonds, son solde, ses documents

--- ROLES ET PERMISSIONS ---
- **superadmin** : acces total (multi-syndic, admin)
- **syndic** : gere une ou plusieurs coproprietes de son organisation
- **admin_syndic** : gere l'organisation syndic (utilisateurs, config)
- **accountant** : lecture + comptabilisation (pas de modification structurelle)
- **owner** / **occupant** : portail proprietaire uniquement (invitation obligatoire)

=== LOGIQUE COMPTABLE BELGE (PCMN) ===
- **Classe 4** : tiers (400 clients, 440 fournisseurs, 411 provisions proprietaires, 413 appels non regles)
- **Classe 5** : financier (55x comptes bancaires)
- **Classe 6** : charges (601 combustibles, 611 travaux, 615 assurances...)
- **Classe 7** : produits (700 acomptes fonds de reserve, 742 loyers, 750 interets crediteurs)
- **Journal ACH** : factures fournisseurs → Debit 6xxx (charge) / Credit 440xxx (fournisseur)
- **Journal VE** : appels de fonds → Debit 413xxx (proprio) / Credit 700xxx (fonds appele)
- **Journal FI** : mouvements bancaires (extraits) → Debit/Credit 55xxx
- **Journal OD** : ecritures manuelles (ecritures d'ouverture/cloture, mutations, regularisations)

=== DIAGNOSTIC & TROUBLESHOOTING COMPTABLE ===

Quand un syndic te demande "pourquoi mon bilan n'est pas equilibre" ou similaire, tu suis un raisonnement structure. Un CONTEXTE DIAGNOSTIC peut te etre injecte automatiquement en debut de message avec les metriques reelles de son ACP. Utilise-le pour donner une reponse chiffree et personnalisee.

**Causes classiques d'un BILAN NON EQUILIBRE (Actif != Passif)** :
1. **Ecritures deseq** : au moins une ecriture dans `journal_entries` a total_debit != total_credit. La regle PCMN est stricte : chaque ecriture doit avoir Debit = Credit a 0,01 EUR pres. -> Le syndic doit aller dans "Journaux", filtrer les 4 journaux, chercher les ecritures orange/rouge marquees "Deseq".
2. **Solde d'ouverture manquant** : reprise d'un ACP existant sans balance ancienne saisie. Le compte 100 (Capital) ou 693 (Report a nouveau) est vide. -> Aller dans "Journaux > OD" et saisir l'ecriture d'ouverture.
3. **Solde d'ouverture desequilibre** : l'ecriture OD de reprise a un total Debit != total Credit. -> Editer l'ecriture, ajouter une ligne d'ajustement sur le compte 693.
4. **Comptes 4xx non-lettres** : soldes proprietaires ou fournisseurs sans contre-partie. -> Verifier "Balance de Tiers".
5. **Exercice ouvert precedent** : le report a nouveau n'a pas ete genere entre l'ancien exercice et le nouveau. -> Aller dans "Exercices", cliquer "Cloturer" l'ancien exercice.

**Causes classiques d'un COMPTE DE RESULTATS ANORMAL** :
1. Produits classe 7 saisis en positif dans un budget mais oublies en negatif -> Verifier les budgets, chaque ligne classe 7 doit etre negative.
2. Charges 6xxx sans nature de depense assignee -> Rapport "Grand Livre" filtre par 6xx sans distribution key.

**Causes classiques d'un EXTRAIT BANCAIRE NON EQUILIBRE** :
1. Solde ouverture + somme mouvements != solde fermeture -> Corriger le solde saisi manuellement ou reimporter le CODA/PDF.
2. Transactions manquantes dans un PDF importe par IA -> L'IA peut avoir manque une ligne. Comparer avec le PDF source (bouton "Voir fichier source").

**Causes classiques d'une BALANCE DE TIERS ANORMALE** :
1. Proprietaire avec solde > 0 (debiteur) sans appel de fonds correspondant -> Manque une ecriture VE.
2. Fournisseur avec solde > 0 sans facture -> Manque une ecriture ACH.
3. Appels de fonds lettres 2 fois -> Verifier les doublons dans "Journaux > VE".
4. **Frais privatif impaye** : une facture marquee `is_private_fee=true` est imputee directement au proprietaire via le compte 643 (charges recuperables) au lieu de la cle de repartition standard. Si le proprietaire ne l'a pas paye, son solde 4xx est alourdi sans qu'aucun appel de fonds ne le facture. -> Aller dans "Facturation" et filtrer par frais privatif ; verifier que le proprietaire a bien recu un decompte ou un appel special pour ces montants.

**Frais privatifs (comptabilite spécifique)** :
Un frais privatif est une facture fournisseur imputee directement a un ou plusieurs proprietaires (sans passer par la cle de repartition), typiquement pour des travaux dans un lot specifique, une consommation individuelle, ou une prestation nominative.
- Champ `is_private_fee=true` sur la facture
- Compte comptable : **643** (charges recuperables) au lieu d'un 6xxx classique
- Repartition : via `private_fee_allocations` (multi-proprios) ou `private_fee_owner_id` (legacy)
- Ecriture ACH generee : Debit 643 (charge recuperable) / Credit 440xxx (fournisseur)
- Recuperation : le montant doit ensuite etre facture au(x) proprietaire(s) via un appel special OU un OD de refacturation (Debit 4xxx proprio / Credit 643)
Si un frais privatif reste NON refacture au proprietaire, il gonfle artificiellement les charges globales et le proprietaire n'a rien a payer. Le compte 643 doit toujours revenir a zero en fin d'exercice.

**Methodologie de diagnostic** :
Quand un syndic te demande "pourquoi X ne fonctionne pas", tu :
1. Explique brievement le concept (1 phrase)
2. Liste les 3-4 causes les plus probables dans l'ordre de frequence
3. Pour chaque cause : donne le chemin exact (onglet + bouton) pour verifier
4. Si un CONTEXTE DIAGNOSTIC t'est fourni avec des chiffres, cite-les explicitement
5. Termine par une invitation : "Si le probleme persiste apres ces verifications, je peux escalader a l'equipe support"


Tous les montants dans l'application utilisent le format belge : espace insecable comme separateur de milliers, virgule comme separateur decimal. Exemple : "10 800,50 EUR".

=== REGLES DE REPONSE STRICTES ===
1. **Base-toi UNIQUEMENT sur les procedures ci-dessus.** N'invente rien.
2. **Cite les noms exacts** des onglets (ex : "Facturation", pas "Factures") et des boutons.
3. **Etapes numerotees courtes** (max 5 principales).
4. **Si tu ne sais pas** : "Je ne suis pas certain de la procedure exacte pour cela sur NextGe Copro. Je transmets a l'equipe support." puis `[[NEEDS_ESCALATION]]`.
5. **Escalade obligatoire** avec `[[NEEDS_ESCALATION]]` pour : bugs, erreurs techniques, questions de facturation/contrat/remboursement, demandes de modification produit.
6. **Hors-sujet** : "Je ne peux repondre qu'aux questions sur NextGe Copro."
7. **N'evoque JAMAIS un bouton "Delier" sur Facturation** ou "Supprimer" sur un exercice cloture : ces boutons n'existent pas.
"""


class NewConversationInput(BaseModel):
    title: Optional[str] = ""


class ChatMessageInput(BaseModel):
    message: str
    # iter93ai : contexte optionnel de l'ACP courante pour permettre au chatbot
    # d'analyser les donnees reelles (bilan equilibre, ecritures deseq, etc.)
    copropriete_id: Optional[str] = None


class EscalateInput(BaseModel):
    reason: Optional[str] = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# iter93ai : mots-cles declenchant la collecte du contexte diagnostic
_DIAGNOSTIC_KEYWORDS = [
    "bilan", "equilibre", "equilibre", "deseq", "desequilibre",
    "balance", "resultat", "solde", "ecart", "difference",
    "pourquoi", "erreur", "probleme", "anormal",
    "ne fonctionne", "manquant", "manque", "diagnostic",
    "extrait", "cloture", "cloturer", "report",
    # iter93aj : mots-cles frais privatifs et analyse comptable generale
    "privatif", "privative", "643", "refacture", "refacturation",
    "analyse", "analyser", "verifier", "controler", "audit",
    "comptabilite", "compta",
]


async def _compute_diagnostic_snapshot(db, copropriete_id: str) -> str:
    """iter93ai : calcule un snapshot des metriques comptables cles pour une ACP,
    formate en texte injectable dans le prompt LLM.

    Metriques collectees :
    - Nombre d'ecritures deseq (total_debit != total_credit)
    - Nombre d'exercices ouverts / clotures
    - Nombre d'appels de fonds
    - Nombre d'extraits bancaires en brouillon vs comptabilises
    - Nombre de factures non payees
    - Somme des soldes tiers (proprietaires 411 + fournisseurs 440)
    """
    if not copropriete_id:
        return ""
    try:
        # Ecritures deseq
        pipeline = [
            {"$match": {"copropriete_id": copropriete_id}},
            {"$project": {
                "total_debit": {"$ifNull": ["$total_debit", 0]},
                "total_credit": {"$ifNull": ["$total_credit", 0]},
                "diff": {"$abs": {"$subtract": [
                    {"$ifNull": ["$total_debit", 0]},
                    {"$ifNull": ["$total_credit", 0]},
                ]}},
            }},
            {"$match": {"diff": {"$gt": 0.01}}},
            {"$count": "n"},
        ]
        deseq_cur = db.journal_entries.aggregate(pipeline)
        deseq_rs = await deseq_cur.to_list(1)
        deseq_count = (deseq_rs[0].get("n") if deseq_rs else 0) or 0

        # Exercices
        fy_open = await db.fiscal_years.count_documents(
            {"copropriete_id": copropriete_id, "status": {"$ne": "closed"}}
        )
        fy_closed = await db.fiscal_years.count_documents(
            {"copropriete_id": copropriete_id, "status": "closed"}
        )

        # Appels de fonds
        fc_count = await db.fund_calls.count_documents({"copropriete_id": copropriete_id})

        # Extraits bancaires
        stmt_draft = await db.bank_statements.count_documents(
            {"copropriete_id": copropriete_id, "status": {"$ne": "posted"}}
        )
        stmt_posted = await db.bank_statements.count_documents(
            {"copropriete_id": copropriete_id, "status": "posted"}
        )

        # Factures non payees
        inv_unpaid = await db.invoices.count_documents(
            {"copropriete_id": copropriete_id,
             "status": {"$in": ["unpaid", "partially_paid", None, ""]}}
        )

        # iter93aj : frais privatifs (factures imputees directement a des proprios via 643)
        priv_total = await db.invoices.count_documents(
            {"copropriete_id": copropriete_id, "is_private_fee": True}
        )
        priv_unpaid = await db.invoices.count_documents(
            {"copropriete_id": copropriete_id,
             "is_private_fee": True,
             "status": {"$in": ["unpaid", "partially_paid", None, ""]}}
        )
        # Somme des montants privatifs impayes
        priv_amount_pipeline = [
            {"$match": {
                "copropriete_id": copropriete_id,
                "is_private_fee": True,
                "status": {"$in": ["unpaid", "partially_paid", None, ""]},
            }},
            {"$group": {"_id": None, "s": {"$sum": {"$ifNull": ["$total_amount", 0]}}}},
        ]
        priv_amt_rs = await db.invoices.aggregate(priv_amount_pipeline).to_list(1)
        priv_unpaid_amount = float((priv_amt_rs[0].get("s") if priv_amt_rs else 0) or 0)

        # Nom de l'ACP pour contextualiser
        acp = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0, "name": 1})
        acp_name = (acp or {}).get("name", copropriete_id[:8])

        # iter93aj : format espace millier + virgule decimale pour montants
        from utils.format import fmt_eur as _fmt_eur
        lines = [
            f"=== CONTEXTE DIAGNOSTIC DE L'ACP \u00ab {acp_name} \u00bb ===",
            f"- Ecritures desequilibrees (Debit != Credit) : {deseq_count}",
            f"- Exercices fiscaux : {fy_open} ouvert(s), {fy_closed} cloture(s)",
            f"- Appels de fonds crees : {fc_count}",
            f"- Extraits bancaires : {stmt_draft} en brouillon, {stmt_posted} comptabilises",
            f"- Factures non integralement payees : {inv_unpaid}",
            f"- Frais privatifs (factures imputees a des proprios via cpt 643) : "
            f"{priv_total} au total, dont {priv_unpaid} impaye(s) "
            f"pour {_fmt_eur(priv_unpaid_amount)}",
            "Utilise ces chiffres reels pour personnaliser ta reponse.",
            "===",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.exception("diagnostic snapshot failed: %s", e)
        return ""


async def _get_user(request: Request):
    user_id = getattr(request.state, "user_id", "")
    if not user_id:
        raise HTTPException(401, "Authentification requise")
    return user_id


async def _load_conversation(db, conv_id: str, user_id: str) -> dict:
    conv = await db.support_conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation introuvable")
    if conv.get("user_id") != user_id:
        raise HTTPException(403, "Acces refuse a cette conversation")
    return conv


async def _send_support_escalation_email(
    *, support_email: str, requester_email: str, requester_name: str,
    conv_title: str, messages: List[dict], reason: str = "",
) -> None:
    """Envoie l'historique complet au service support avec Reply-To = requester."""
    from graph_email import send_html_email

    def _fmt_msg(m):
        role = m.get("role", "user")
        bg = "#EEF6FF" if role == "user" else "#F4F4F5"
        label = "Syndic" if role == "user" else "Assistant IA"
        content = (m.get("content") or "").replace("\n", "<br/>")
        return (
            f'<div style="background:{bg};padding:10px 12px;border-radius:8px;margin:6px 0;">'
            f'<div style="font-size:11px;color:#666;margin-bottom:4px;">{label}</div>'
            f'<div style="font-size:13px;color:#111;">{content}</div>'
            '</div>'
        )

    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <h2 style="color:#0055FF;margin-bottom:4px;">Nouvelle demande support NextGe Copro</h2>
  <p style="color:#555;margin-top:0;font-size:13px;">
    Un syndic a besoin d'assistance humaine. L'IA n'a pas pu resoudre la question.
  </p>
  <div style="background:#FAFAFA;border:1px solid #EEE;border-radius:8px;padding:12px;margin:12px 0;">
    <b>Demandeur :</b> {requester_name} &lt;{requester_email}&gt;<br/>
    <b>Conversation :</b> {conv_title or 'Sans titre'}<br/>
    <b>Date :</b> {_now()}<br/>
    {'<b>Raison :</b> ' + reason + '<br/>' if reason else ''}
  </div>
  <h3 style="color:#333;margin-top:20px;">Historique complet</h3>
  {''.join(_fmt_msg(m) for m in messages)}
  <hr style="margin:24px 0;border:none;border-top:1px solid #EEE;"/>
  <p style="color:#888;font-size:11px;">
    Repondez directement a cet email : votre reponse partira automatiquement a {requester_email}.
  </p>
</div>
""".strip()

    await send_html_email(
        recipients=[support_email],
        subject=f"[NextGe Copro Support] {conv_title or 'Nouvelle demande'} — {requester_name}",
        html_body=body,
        reply_to=requester_email,
    )


def create_support_router(db):
    router = APIRouter(prefix="/api/support", tags=["support"])

    @router.get("/conversations")
    async def list_conversations(request: Request):
        user_id = await _get_user(request)
        convs = await db.support_conversations.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("updated_at", -1).to_list(200)
        return convs

    @router.post("/conversations")
    async def create_conversation(data: NewConversationInput, request: Request):
        user_id = await _get_user(request)
        conv = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": getattr(request.state, "user_email", ""),
            "user_name": getattr(request.state, "user_name", ""),
            "title": (data.title or "Nouvelle question").strip()[:120],
            "messages_count": 0,
            "escalated": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.support_conversations.insert_one(conv)
        conv.pop("_id", None)
        return conv

    @router.get("/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, request: Request):
        user_id = await _get_user(request)
        await _load_conversation(db, conv_id, user_id)
        msgs = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        return msgs

    @router.post("/conversations/{conv_id}/chat")
    async def chat(conv_id: str, data: ChatMessageInput, request: Request,
                   background: BackgroundTasks):
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        user_id = await _get_user(request)
        conv = await _load_conversation(db, conv_id, user_id)
        user_msg = (data.message or "").strip()
        if not user_msg:
            raise HTTPException(400, "Message vide")
        if len(user_msg) > 2000:
            raise HTTPException(400, "Message trop long (max 2000 caracteres)")

        # Store user message
        um = {
            "id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "role": "user",
            "content": user_msg,
            "created_at": _now(),
        }
        await db.support_messages.insert_one(um)

        # Fetch conversation history (last 20 messages) for context
        history = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(50)

        api_key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not api_key:
            raise HTTPException(500, "EMERGENT_LLM_KEY absent - IA indisponible")

        chat_obj = LlmChat(
            api_key=api_key,
            session_id=f"support-{conv_id}",
            system_message=_SUPPORT_SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")

        # Build the prompt with prior context (simple : concatenation of past turns)
        past_context = ""
        for m in history[:-1]:  # exclude the just-inserted user msg
            role_label = "Syndic" if m["role"] == "user" else "Assistant"
            past_context += f"\n{role_label} : {m['content']}\n"

        # iter93ai : si la question est de type "diagnostic" ET qu'un copropriete_id
        # est fourni, on injecte les metriques reelles de l'ACP.
        diag_context = ""
        msg_lower = user_msg.lower()
        if data.copropriete_id and any(kw in msg_lower for kw in _DIAGNOSTIC_KEYWORDS):
            diag_context = await _compute_diagnostic_snapshot(db, data.copropriete_id)

        full_prompt = user_msg
        if past_context.strip() or diag_context:
            parts = []
            if diag_context:
                parts.append(diag_context)
            if past_context.strip():
                parts.append("Historique de conversation :" + past_context)
            parts.append(f"Nouvelle question du syndic : {user_msg}")
            full_prompt = "\n\n".join(parts)
        try:
            ai_resp = await chat_obj.send_message(UserMessage(text=full_prompt))
        except Exception as e:
            logger.exception("Support LLM call failed")
            raise HTTPException(502, f"IA indisponible : {str(e)[:150]}")

        ai_text = (ai_resp or "").strip()
        needs_escalation = "[[NEEDS_ESCALATION]]" in ai_text
        # Clean the marker from the visible answer
        clean_text = ai_text.replace("[[NEEDS_ESCALATION]]", "").strip()

        am = {
            "id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "role": "assistant",
            "content": clean_text,
            "needs_escalation": needs_escalation,
            "created_at": _now(),
        }
        await db.support_messages.insert_one(am)
        am.pop("_id", None)

        # Update conversation meta
        title_update = conv.get("title") or ""
        if conv.get("messages_count", 0) == 0 or title_update == "Nouvelle question":
            title_update = user_msg[:80]
        await db.support_conversations.update_one(
            {"id": conv_id},
            {"$set": {
                "title": title_update,
                "updated_at": _now(),
                "last_message_preview": clean_text[:200],
            }, "$inc": {"messages_count": 2}}
        )

        # Auto-escalation if needed and not already escalated
        auto_escalated = False
        if needs_escalation and not conv.get("escalated"):
            support_email = os.environ.get("SUPPORT_EMAIL", "").strip()
            if support_email:
                # Refresh full history including new answer
                full_history = await db.support_messages.find(
                    {"conversation_id": conv_id}, {"_id": 0},
                ).sort("created_at", 1).to_list(500)
                requester_email = conv.get("user_email") or getattr(request.state, "user_email", "") or ""
                requester_name = conv.get("user_name") or getattr(request.state, "user_name", "") or "Syndic"
                background.add_task(
                    _send_support_escalation_email,
                    support_email=support_email,
                    requester_email=requester_email,
                    requester_name=requester_name,
                    conv_title=title_update,
                    messages=full_history,
                    reason="Escalade automatique (IA n'a pas pu resoudre)",
                )
                await db.support_conversations.update_one(
                    {"id": conv_id},
                    {"$set": {"escalated": True,
                              "escalated_at": _now(),
                              "escalated_kind": "auto"}}
                )
                auto_escalated = True

        return {
            "assistant_message": am,
            "needs_escalation": needs_escalation,
            "auto_escalated": auto_escalated,
        }

    @router.post("/conversations/{conv_id}/escalate")
    async def escalate(conv_id: str, data: EscalateInput, request: Request,
                       background: BackgroundTasks):
        user_id = await _get_user(request)
        conv = await _load_conversation(db, conv_id, user_id)
        support_email = os.environ.get("SUPPORT_EMAIL", "").strip()
        if not support_email:
            raise HTTPException(500, "SUPPORT_EMAIL non configure - contactez l'administrateur")
        history = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        if not history:
            raise HTTPException(400, "Aucun message dans la conversation")
        requester_email = conv.get("user_email") or getattr(request.state, "user_email", "") or ""
        requester_name = conv.get("user_name") or getattr(request.state, "user_name", "") or "Syndic"
        background.add_task(
            _send_support_escalation_email,
            support_email=support_email,
            requester_email=requester_email,
            requester_name=requester_name,
            conv_title=conv.get("title", ""),
            messages=history,
            reason=(data.reason or "Escalade manuelle par le syndic"),
        )
        await db.support_conversations.update_one(
            {"id": conv_id},
            {"$set": {"escalated": True, "escalated_at": _now(),
                      "escalated_kind": "manual", "updated_at": _now()}}
        )
        return {"message": "Votre demande a ete transmise au support. Vous recevrez une reponse par email."}

    @router.delete("/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, request: Request):
        user_id = await _get_user(request)
        await _load_conversation(db, conv_id, user_id)
        await db.support_messages.delete_many({"conversation_id": conv_id})
        await db.support_conversations.delete_one({"id": conv_id})
        return {"message": "Conversation supprimee"}

    return router
