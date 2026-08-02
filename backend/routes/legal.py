"""Iter90s - Compliance legale (CGU, Privacy, Mentions, Cookies, RGPD).

Contenu editable via base de donnees (une seule ligne par doc). Chaque
document a une VERSION incrementale. Quand la version change, l'user doit
re-accepter (interstitiel a la connexion).

Endpoints :
- GET  /api/legal/documents           -> liste des documents publics
- GET  /api/legal/documents/{slug}    -> contenu d'un doc (CGU, privacy, ...)
- GET  /api/legal/current-versions    -> versions courantes des CGU / privacy
- POST /api/legal/accept              -> user accepte les CGU (marque acceptation)
- GET  /api/legal/my-acceptance       -> statut acceptation CGU du user courant
- POST /api/legal/rgpd/export         -> export RGPD (art. 20 portabilite)
- POST /api/legal/rgpd/delete-account -> demande de suppression (art. 17)
"""
import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _oid(user_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(user_id)
    except Exception:
        return None


# Contenu par defaut (V1) - stocke en base au premier demarrage.
# Placeholders "[A COMPLETER]" a remplacer par l'entite juridique.

_DEFAULT_DOCS = {
    "cgu": {
        "title": "Conditions Générales d'Utilisation",
        "version": 1,
        "content": """
# Conditions Générales d'Utilisation

**Version 1 — En vigueur au [DATE_MISE_EN_LIGNE]**

## 1. Objet
Les présentes Conditions Générales d'Utilisation (« CGU ») régissent l'accès et l'utilisation de la plateforme SaaS **NextGe Copro** (ci-après « la Plateforme »), éditée par [SOCIETE] (ci-après « l'Éditeur »).

## 2. Identification de l'Éditeur
- Raison sociale : **[SOCIETE]**
- Forme juridique : **[FORME_JURIDIQUE]**
- Siège social : **[ADRESSE_COMPLETE]**
- N° BCE : **[NUMERO_BCE]**
- N° TVA : **[NUMERO_TVA]**
- Représentant légal : **[NOM_REPRESENTANT], [FONCTION]**
- Email contact : **welcome@goodexperienceproperties.be**

## 3. Acceptation des CGU
L'utilisation de la Plateforme implique l'acceptation pleine et entière des présentes CGU. L'Utilisateur atteste être majeur, avoir la capacité juridique de contracter et agir soit en son nom personnel, soit au nom d'une entité qu'il représente valablement.

## 4. Description du service
La Plateforme est un logiciel de gestion comptable et administrative destiné aux syndics de copropriétés belges, incluant notamment : gestion des lots et copropriétaires, comptabilité (PCMN), appels de fonds, extraits bancaires, rapports fiscaux, portail propriétaire.

## 5. Nature de l'outil — Limitation de responsabilité
**NextGe Copro est un outil d'aide à la gestion.** La Plateforme :
- ne se substitue pas à un expert-comptable, un réviseur d'entreprises ou un conseil juridique ;
- ne garantit pas la conformité fiscale ou comptable des écritures générées, laquelle relève de la seule responsabilité du syndic ;
- n'est pas responsable des décisions prises par l'Utilisateur sur base des données affichées.

L'Utilisateur reconnaît être seul responsable de l'exactitude des données qu'il saisit ou importe. Il lui appartient de vérifier chaque écriture comptable, chaque appel de fonds et chaque décompte avant diffusion à des tiers ou à l'administration fiscale.

## 6. Obligations de l'Utilisateur
L'Utilisateur s'engage à :
- fournir des informations exactes et complètes ;
- respecter la confidentialité de ses identifiants ;
- ne pas tenter d'accéder à des données appartenant à d'autres utilisateurs (cloisonnement / chinese wall) ;
- n'utiliser la Plateforme qu'à des fins licites et dans le cadre de son activité professionnelle de syndic ;
- respecter la législation en vigueur, notamment le RGPD, dans le traitement des données de ses copropriétaires.

## 7. Propriété intellectuelle
Le code source, les visuels, la charte graphique et l'ensemble des éléments constitutifs de la Plateforme sont la propriété exclusive de l'Éditeur. Toute reproduction, modification, revente ou décompilation sans autorisation écrite est strictement interdite.

Les **données saisies** par l'Utilisateur (comptabilité, propriétaires, factures) restent la propriété exclusive de l'Utilisateur.

## 8. Abonnement, prix, résiliation
Les conditions financières sont précisées dans le devis ou contrat séparé. En cas de non-paiement, l'accès peut être suspendu après mise en demeure restée sans effet 15 jours.

L'Utilisateur peut résilier son abonnement à tout moment via son profil ; ses données sont alors conservées 30 jours puis supprimées (sauf obligations légales de conservation, cf. § 12).

## 9. Disponibilité — Maintenance
L'Éditeur s'engage à des efforts raisonnables pour maintenir la Plateforme disponible 24/7, sans obligation de résultat. Des interruptions pour maintenance peuvent survenir, préavis raisonnable dans la mesure du possible.

## 10. Force majeure
L'Éditeur est exonéré de toute responsabilité en cas de force majeure telle que définie par la jurisprudence belge (grève, panne réseau généralisée, cyberattaque massive, décision d'autorité publique, etc.).

## 11. Modifications des CGU
L'Éditeur se réserve le droit de modifier les CGU. Toute modification substantielle sera notifiée à l'Utilisateur au minimum 30 jours avant application, avec possibilité de résiliation sans frais avant la date d'entrée en vigueur.

## 12. Données personnelles
Le traitement des données personnelles est régi par la **Politique de Confidentialité** consultable séparément, qui fait partie intégrante des présentes CGU. Les données comptables sont conservées **7 ans** conformément à l'article III.86 du Code de droit économique belge (obligation de conservation des documents comptables).

## 13. Loi applicable — Juridiction
Les présentes CGU sont régies par le **droit belge**. Tout litige relatif à leur interprétation ou exécution sera de la compétence exclusive des **tribunaux de Bruxelles (arrondissement judiciaire francophone)**, sauf disposition impérative contraire.

## 14. Divisibilité
Si l'une des clauses des présentes CGU était déclarée nulle ou inapplicable, les autres clauses conserveront leur pleine force et effet.

## 15. Contact
Pour toute question : **welcome@goodexperienceproperties.be**
""".strip(),
    },
    "privacy": {
        "title": "Politique de Confidentialité (RGPD)",
        "version": 1,
        "content": """
# Politique de Confidentialité

**Version 1 — En vigueur au [DATE_MISE_EN_LIGNE]**

Conforme au Règlement Général sur la Protection des Données (RGPD — UE 2016/679) et à la loi belge du 30 juillet 2018 relative à la protection des personnes physiques à l'égard des traitements de données à caractère personnel.

## 1. Responsable du traitement
- **[SOCIETE]**, [FORME_JURIDIQUE]
- Siège : [ADRESSE_COMPLETE]
- N° BCE : [NUMERO_BCE]
- Contact : welcome@goodexperienceproperties.be
- DPO / Délégué à la protection : **welcome@goodexperienceproperties.be** (à préciser si un DPO externe est désigné)

## 2. Données collectées

### 2.1 Utilisateurs (syndics, gestionnaires)
- Identité : nom, prénom, email, téléphone (optionnel)
- Authentification : email + mot de passe hashé (bcrypt)
- Données de connexion : IP, user-agent, dates de connexion
- Contenu généré : conversations avec l'assistant IA support

### 2.2 Copropriétaires (traités par le syndic via la Plateforme)
- Identité : nom, prénom, adresse postale, email, téléphone
- Financier : quotités, appels de fonds, paiements, décomptes de mutation
- Fiscal : VCS (Virement Communication Structurée)

## 3. Base légale du traitement
- **Exécution du contrat** (art. 6.1.b RGPD) — pour la fourniture du service SaaS
- **Obligation légale** (art. 6.1.c RGPD) — conservation des documents comptables (7 ans)
- **Intérêt légitime** (art. 6.1.f RGPD) — sécurité, prévention de la fraude, amélioration du service

## 4. Finalités
- Fournir les fonctionnalités de gestion de copropriété
- Émettre appels de fonds, décomptes, rapports fiscaux
- Communiquer par email avec les copropriétaires (via Microsoft Graph)
- Assurer le support utilisateur (assistant IA Claude, éventuellement escalade humaine)
- Sécuriser l'accès (audit trail, détection d'intrusion)

## 5. Sous-traitants
La Plateforme recourt aux sous-traitants suivants, tenus au respect du RGPD :

| Sous-traitant | Finalité | Localisation |
|---------------|----------|--------------|
| **Emergent Labs** | Hébergement Cloud, infrastructure applicative | UE / conforme RGPD |
| **Microsoft Corporation** (Graph / Azure) | Envoi d'emails transactionnels | UE (Irlande) |
| **Anthropic PBC** (Claude via Emergent) | Assistant IA support conversationnel | USA (Standard Contractual Clauses) |
| **MongoDB Atlas** (le cas échéant) | Stockage base de données | UE |

Un **contrat de sous-traitance (DPA)** conforme à l'article 28 RGPD est en place avec chacun.

## 6. Durée de conservation
- **Données comptables** : 7 ans (obligation légale — art. III.86 CDE)
- **Données de connexion / logs** : 12 mois
- **Compte utilisateur** : durée de l'abonnement + 30 jours en cas de résiliation, puis suppression sauf obligations légales
- **Conversations support IA** : 24 mois puis suppression automatique

## 7. Vos droits (RGPD)
Vous disposez des droits suivants sur vos données :
- **Accès** (art. 15) — obtenir une copie des données vous concernant
- **Rectification** (art. 16) — corriger une donnée inexacte
- **Effacement** (art. 17) — droit à l'oubli, sous réserve des obligations légales
- **Portabilité** (art. 20) — export au format machine (JSON) — disponible directement dans votre profil
- **Opposition** (art. 21) — pour les traitements fondés sur l'intérêt légitime
- **Limitation** (art. 18) — restreindre temporairement le traitement
- **Réclamation** auprès de l'**Autorité de Protection des Données belge** (APD, www.autoriteprotectiondonnees.be)

Pour exercer ces droits : **welcome@goodexperienceproperties.be** ou via votre profil (bouton « Exporter mes données » / « Supprimer mon compte »).

## 8. Sécurité
- Chiffrement HTTPS (TLS 1.2+) pour tous les transferts
- Mots de passe hashés (bcrypt)
- Cloisonnement strict entre copropriétés (« chinese wall »)
- Journalisation des accès sensibles (audit trail)
- Sauvegardes régulières
- Contrôle d'accès par rôle (RBAC : superadmin, admin, syndic, comptable, propriétaire)

## 9. Cookies
Voir la **Politique Cookies** dédiée. La Plateforme utilise uniquement des cookies techniques indispensables (authentification, session) — aucun cookie de tracking ou publicitaire.

## 10. Transferts hors UE
Les données peuvent être traitées par Anthropic (USA) pour le fonctionnement de l'assistant IA. Ce transfert est encadré par les **Clauses Contractuelles Types (CCT)** adoptées par la Commission européenne (Décision 2021/914).

## 11. Modifications
Toute modification substantielle sera notifiée par email et interstitiel dans la Plateforme au moins 30 jours avant application.

## 12. Contact
Toute question : **welcome@goodexperienceproperties.be**
Réclamation autorité : APD Belgique — Rue de la Presse 35 — 1000 Bruxelles — contact@apd-gba.be
""".strip(),
    },
    "mentions": {
        "title": "Mentions Légales",
        "version": 1,
        "content": """
# Mentions Légales

## Éditeur du site
- **Raison sociale** : [SOCIETE]
- **Forme juridique** : [FORME_JURIDIQUE]
- **Siège social** : [ADRESSE_COMPLETE]
- **N° d'entreprise (BCE)** : [NUMERO_BCE]
- **N° TVA** : [NUMERO_TVA]
- **Représentant légal** : [NOM_REPRESENTANT], [FONCTION]
- **Email** : welcome@goodexperienceproperties.be
- **Téléphone** : [TELEPHONE]

## Directeur de la publication
[NOM_REPRESENTANT]

## Hébergement
Application hébergée par **Emergent Labs** (infrastructure Cloud).

## Propriété intellectuelle
Le contenu, la structure, la charte graphique et le code source de NextGe Copro sont protégés par les lois belges et internationales relatives à la propriété intellectuelle. Toute reproduction totale ou partielle sans autorisation écrite préalable est interdite.

## Responsabilité
Les informations diffusées sont fournies « en l'état ». L'Éditeur s'efforce d'assurer leur exactitude mais ne garantit pas leur exhaustivité ni leur mise à jour permanente. L'utilisation de la Plateforme se fait sous la seule responsabilité de l'Utilisateur.

## Contact
Pour toute réclamation ou question : welcome@goodexperienceproperties.be
""".strip(),
    },
    "cookies": {
        "title": "Politique de Cookies",
        "version": 1,
        "content": """
# Politique de Cookies

## 1. Qu'est-ce qu'un cookie ?
Un cookie est un petit fichier texte déposé sur votre appareil lors de la visite d'un site web, permettant de stocker des informations relatives à votre navigation.

## 2. Cookies utilisés par NextGe Copro

NextGe Copro n'utilise **QUE des cookies strictement nécessaires** au fonctionnement du service. Aucun cookie de tracking, de publicité ou d'analyse tiers n'est utilisé.

| Cookie | Finalité | Durée | Type |
|--------|----------|-------|------|
| `auth_token` | Session d'authentification | 7 jours (renouvelé à chaque connexion) | HttpOnly, Secure, SameSite=Lax |
| `_preferences` (le cas échéant) | Sauvegarde préférences UI (langue, thème) | 12 mois | Localstorage |

Ces cookies sont **indispensables** au fonctionnement de la Plateforme et **exemptés de consentement** au sens de l'article 129 de la loi belge du 13 juin 2005 (transposition ePrivacy).

## 3. Gestion des cookies
Vous pouvez à tout moment désactiver les cookies dans les paramètres de votre navigateur. **Cependant, la désactivation empêche l'accès à la Plateforme** (impossibilité de vous authentifier).

## 4. Contact
Pour toute question : welcome@goodexperienceproperties.be
""".strip(),
    },
    "disclaimer": {
        "title": "Disclaimer Comptable",
        "version": 1,
        "content": """
# Disclaimer — Nature de l'outil comptable

**NextGe Copro est un outil d'aide à la gestion administrative et comptable des copropriétés.**

## 1. Non-substitution
La Plateforme ne se substitue en aucun cas à :
- un **expert-comptable** (Institut IEC / ITAA belge)
- un **réviseur d'entreprises**
- un **conseil juridique** ou fiscal spécialisé
- un **notaire** pour les actes de mutation

## 2. Responsabilité de l'utilisateur
L'Utilisateur (syndic ou gestionnaire) reste seul responsable :
- de l'**exactitude** des données saisies ou importées ;
- de la **conformité fiscale** des écritures et déclarations ;
- de la **vérification** de chaque décompte, appel de fonds et rapport avant diffusion à des tiers, à l'administration fiscale ou à l'assemblée générale ;
- de la **conservation** des pièces justificatives originales (art. III.86 CDE).

## 3. Limitation de responsabilité
La responsabilité de l'Éditeur, en cas de dommage prouvé lié à l'utilisation de la Plateforme, ne pourra en aucun cas excéder **le montant des abonnements payés au cours des 12 mois précédant le fait générateur du dommage**, hors dommage direct exclusivement matériel.

Sont exclus : dommages indirects, perte de bénéfice, perte de chance, préjudice d'image, pertes de données non causées par une faute lourde de l'Éditeur.

## 4. Recommandation
Nous recommandons une **revue périodique par un expert-comptable** de vos états comptables et fiscaux produits par la Plateforme.
""".strip(),
    },
    "data-act": {
        "title": "Registre Data Act & Formats d'export",
        "version": 1,
        "content": """
# Registre Data Act & Formats d'export

**Version 1 — En vigueur au [DATE_MISE_EN_LIGNE]**

Le présent registre complète les Conditions Générales d'Utilisation et satisfait aux obligations de **portabilité des données** prévues par le Règlement (UE) 2023/2854 relatif à des règles harmonisées portant sur l'équité de l'accès aux données et de leur utilisation (« Data Act »), en particulier son **article 25** sur les clauses contractuelles applicables aux services de traitement de données.

> **Attention** : ce document est **complémentaire** au contrat. Les catégories de données exportables, les délais de restitution et les conditions de changement de fournisseur (« switching ») restent inscrites dans les CGU/Contrat.

---

## 1. Données exportables par le Client

Les données suivantes, générées ou saisies par le Client dans la Plateforme, peuvent être exportées à tout moment sur demande :

- **Copropriétés (ACPs)** : identification, adresse, statut, statuts (RCS/BCE), quotités.
- **Lots** : numéros, descriptions, quotités, mutations, historique d'attribution.
- **Propriétaires** : coordonnées, communications structurées VCS, tiers comptables associés, préférences de communication.
- **Écritures comptables** : journaux (ACH, VCS, OD, AN), grand-livre PCMN, extourne, exercices comptables clos et ouverts.
- **Bilans & Comptes de résultat** : bilans après répartition, comptes de résultat annuels, décomptes de charges par lot.
- **Appels de fonds & Paiements** : appels trimestriels, réserves, roulements, extraits CODA importés, transactions bancaires matchées.
- **Factures & Justificatifs** : factures fournisseurs, pièces jointes (PDF, images), OD frais privatifs.
- **AG & PV** : convocations, ordre du jour, votes, procès-verbaux.
- **Documents** : PDF, images, courriers scannés stockés dans le module Documents.
- **Communications émises** : historique des envois email, corps, destinataires, statut de livraison.
- **Fichiers d'origine** : PDF de la liste des propriétaires importée, exports Optipro/CODA sources.

---

## 2. Formats disponibles

| Type de données | Formats disponibles |
|-----------------|---------------------|
| Comptabilité (journaux, écritures, grand-livre) | **CSV** (UTF-8, séparateur `;`), **XLSX** |
| Rapports (bilan, compte de résultat, décomptes) | **PDF** (mise en page officielle), **XLSX** |
| Balance de tiers, appels de fonds | **CSV**, **XLSX**, **PDF** |
| Fiches propriétaires, lots, ACPs | **CSV**, **XLSX**, **JSON** |
| Documents & justificatifs | **Fichier original** (PDF, JPEG, PNG…) dans une archive ZIP |
| Extraits CODA importés | **Fichier CODA original** (`.cod`) + **CSV** du parse |
| Base de données brute | **Dump JSON** structuré (une clé par collection) sur demande explicite |

---

## 3. Procédure de demande d'export

**Auto-service** (immédiat) :
- Les exports CSV/XLSX/PDF sont accessibles directement depuis l'interface :
  - Rapports > Bilan & Résultats : bouton *Exporter*
  - Balance de Tiers : bouton *Exporter CSV / PDF*
  - Communication > Historique envois : bouton *Exporter CSV*
  - Facturation admin : bouton *Exporter CSV*

**Sur demande** (dump complet ou fichiers d'origine en masse) :
1. Adresser une demande écrite à **[EMAIL_SUPPORT]** avec en objet `[DATA-ACT] Demande d'export`.
2. Préciser : ACPs concernées, période, format(s) souhaité(s).
3. Le Client est authentifié par un utilisateur ayant le rôle *syndic* ou *admin* du compte.
4. Accusé de réception sous **48 h ouvrées**.
5. Livraison via lien sécurisé chiffré, valable 7 jours.

---

## 4. Délais de restitution et de suppression

| Événement | Délai |
|-----------|-------|
| Export auto-service | Immédiat |
| Export standard sur demande (dump JSON, archive PDF) | **≤ 5 jours ouvrés** |
| Export complexe (multi-ACP, historique > 5 ans) | **≤ 30 jours ouvrés** |
| Restitution finale à la résiliation du contrat | **≤ 30 jours** après demande écrite |
| Suppression irréversible des données après export final | **90 jours** après restitution (délai légal de rétention comptable applicable ensuite) |
| Purge des sauvegardes chiffrées | **12 mois maximum** |

---

## 5. Limitations techniques

- Volume maximal d'un export unique en auto-service : **500 Mo**. Au-delà, l'export est découpé et fourni en plusieurs fichiers.
- Les PDF signés numériquement (justificatifs) sont exportés **tels quels**, sans re-signature.
- Les exports XLSX suivent la spécification Office Open XML — compatibles Excel 2016+, LibreOffice Calc 7+, Google Sheets.
- Les fichiers CSV utilisent l'encodage **UTF-8 avec BOM** pour compatibilité Excel Windows.
- Les fichiers CODA sont livrés **binaires**, sans conversion, format bancaire Febelfin.

---

## 6. Données INTERNES non exportables

Les éléments suivants **ne sont pas** couverts par le droit à la portabilité (art. 4 Data Act & jurisprudence associée) :

- **Code source** de la Plateforme (propriété exclusive de l'Éditeur).
- **Journaux techniques de sécurité** internes (WAF, IDS, corrélation SIEM).
- **Secrets techniques** : clés API, JWT signing keys, mots de passe hachés, données chiffrées à la volée (chiffrement au repos).
- **Configurations propriétaires** : règles métier, algorithmes de réconciliation, modèles LLM entraînés.
- **Journaux d'audit inter-clients** : les entrées d'audit ne concernent que le compte du Client demandeur.
- **Statistiques agrégées** anonymisées utilisées pour l'amélioration du service.

---

## 7. Localisation et juridiction de l'infrastructure

- **Hébergement principal** : Union européenne (Belgique / Pays-Bas).
- **Base de données** : MongoDB — instance managée hébergée en **UE**.
- **Sauvegardes** : chiffrées AES-256, répliquées sur région UE distincte.
- **Prestataires sous-traitants** figurant à l'Annexe DPA du contrat.
- **Juridiction applicable** : Droit belge et Règlement européen (RGPD, Data Act, NIS 2, DORA le cas échéant).
- **Tribunal compétent** : tribunal de l'entreprise de **[VILLE]** (Belgique).

---

## 8. Mesures contre les demandes illicites d'accès gouvernemental de pays tiers

Conformément à l'article 32 du Data Act, l'Éditeur s'engage à :

1. **Ne pas transférer** les données du Client vers des juridictions non couvertes par une décision d'adéquation de la Commission européenne ou des clauses contractuelles types (SCCs) approuvées.
2. **Refuser toute demande** émise par une autorité publique d'un pays tiers qui contreviendrait au droit de l'Union — sauf ordonnance judiciaire européenne ou traité d'entraide en vigueur (MLAT).
3. **Notifier le Client** dans les **72 heures** de toute demande officielle reçue, sauf interdiction légale expresse ; dans ce cas, notification dès la levée de l'interdiction.
4. **Contester juridiquement** toute demande manifestement disproportionnée ou contraire aux droits fondamentaux du Client, aux frais de l'Éditeur.
5. **Publier un rapport de transparence annuel** listant le nombre et la nature des demandes reçues et la manière dont elles ont été traitées.

---

## 9. Changement de fournisseur (« switching »)

Conformément aux articles 23 à 31 du Data Act :

- Le Client peut **changer de fournisseur** de services de traitement de données à tout moment moyennant un préavis fixé au **contrat**.
- Le processus de switching est **accompagné techniquement** par le support (préparation du dump, mapping des formats, transferts sécurisés).
- **Aucun frais de sortie** n'est facturé au-delà des coûts opérationnels raisonnables et transparents détaillés au contrat.
- L'Éditeur maintient la **portabilité fonctionnelle équivalente** : formats ouverts (CSV, JSON, PDF/A) permettant la reprise chez tout fournisseur concurrent.

---

## 10. Contact

Toute demande relative au présent registre doit être adressée à :

**[SOCIETE]** — Délégué à la Protection des Données
Courriel : **[EMAIL_DPO]**
Adresse postale : **[ADRESSE_POSTALE]**

Un accusé de réception est envoyé sous **72 heures ouvrées**.
""".strip(),
    },
}


class AcceptTermsInput(BaseModel):
    cgu_version: int
    privacy_version: int


class DeleteAccountInput(BaseModel):
    confirm: str  # doit valoir "SUPPRIMER MON COMPTE"


class UpdateDocumentInput(BaseModel):
    title: Optional[str] = None
    content: str
    bump_version: bool = False  # If True, increment version and force user re-acceptance


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_defaults(db):
    """Insere les docs par defaut s'ils n'existent pas encore."""
    for slug, doc in _DEFAULT_DOCS.items():
        existing = await db.legal_documents.find_one({"slug": slug})
        if not existing:
            await db.legal_documents.insert_one({
                "id": str(uuid.uuid4()),
                "slug": slug,
                "title": doc["title"],
                "version": doc["version"],
                "content": doc["content"],
                "created_at": _now(),
                "updated_at": _now(),
            })


def create_legal_router(db):
    router = APIRouter(prefix="/api/legal", tags=["legal"])

    @router.get("/documents")
    async def list_documents():
        await _ensure_defaults(db)
        docs = await db.legal_documents.find(
            {}, {"_id": 0, "slug": 1, "title": 1, "version": 1, "updated_at": 1},
        ).to_list(50)
        return docs

    @router.get("/documents/{slug}")
    async def get_document(slug: str):
        await _ensure_defaults(db)
        doc = await db.legal_documents.find_one({"slug": slug}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Document introuvable")
        return doc

    @router.get("/current-versions")
    async def current_versions():
        """Renvoie les versions actuelles des documents dont l'acceptation est requise."""
        await _ensure_defaults(db)
        cgu = await db.legal_documents.find_one({"slug": "cgu"}, {"_id": 0, "version": 1})
        priv = await db.legal_documents.find_one({"slug": "privacy"}, {"_id": 0, "version": 1})
        return {
            "cgu_version": (cgu or {}).get("version", 1),
            "privacy_version": (priv or {}).get("version", 1),
        }

    @router.get("/my-acceptance")
    async def my_acceptance(request: Request):
        user_id = getattr(request.state, "user_id", "")
        oid = _oid(user_id) if user_id else None
        if not oid:
            raise HTTPException(401, "Authentification requise")
        user = await db.users.find_one({"_id": oid}, {"_id": 0, "legal_accepted": 1})
        legal = (user or {}).get("legal_accepted", {}) or {}
        current = await current_versions()
        needs_accept = (
            legal.get("cgu_version") != current["cgu_version"]
            or legal.get("privacy_version") != current["privacy_version"]
        )
        return {"legal_accepted": legal, "needs_accept": needs_accept,
                "current_versions": current}

    @router.post("/accept")
    async def accept(data: AcceptTermsInput, request: Request):
        user_id = getattr(request.state, "user_id", "")
        oid = _oid(user_id) if user_id else None
        if not oid:
            raise HTTPException(401, "Authentification requise")
        current = await current_versions()
        if data.cgu_version != current["cgu_version"] or data.privacy_version != current["privacy_version"]:
            raise HTTPException(400,
                f"Versions obsoletes. Actuelles : CGU v{current['cgu_version']}, Privacy v{current['privacy_version']}")
        accepted_at = _now()
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")[:300]
        await db.users.update_one(
            {"_id": oid},
            {"$set": {"legal_accepted": {
                "cgu_version": data.cgu_version,
                "privacy_version": data.privacy_version,
                "accepted_at": accepted_at,
                "ip": ip, "user_agent": ua,
            }}}
        )
        # Log audit trail
        user_email = getattr(request.state, "user_email", "")
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": user_email,
            "action": "legal.accept",
            "details": {"cgu_version": data.cgu_version,
                        "privacy_version": data.privacy_version},
            "ip": ip, "user_agent": ua,
            "timestamp": accepted_at,
        })
        return {"message": "Conditions acceptees", "accepted_at": accepted_at}

    @router.post("/rgpd/export")
    async def rgpd_export(request: Request):
        """RGPD art. 20 - portabilite. Renvoie un JSON complet des donnees
        du user courant."""
        user_id = getattr(request.state, "user_id", "")
        oid = _oid(user_id) if user_id else None
        if not oid:
            raise HTTPException(401, "Authentification requise")
        user = await db.users.find_one({"_id": oid}, {"password_hash": 0})
        if not user:
            raise HTTPException(404, "Utilisateur introuvable")
        user["_id"] = str(user["_id"])
        # Collecter les donnees liees a cet user (support conversations,
        # audit logs, owners linked, etc.)
        support_convs = await db.support_conversations.find(
            {"user_id": user_id}, {"_id": 0},
        ).to_list(1000)
        support_msgs = []
        if support_convs:
            conv_ids = [c.get("id") for c in support_convs if c.get("id")]
            if conv_ids:
                support_msgs = await db.support_messages.find(
                    {"conversation_id": {"$in": conv_ids}},
                    {"_id": 0},
                ).to_list(10000)
        audit_logs = await db.audit_log.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("timestamp", -1).to_list(5000)
        # Owner-side data (si user est aussi proprietaire)
        owner = None
        if user.get("email"):
            owner = await db.owners.find_one({"email": user["email"]}, {"_id": 0})

        export = {
            "generated_at": _now(),
            "generated_by_user_id": user_id,
            "profile": user,
            "support_conversations": support_convs,
            "support_messages": support_msgs,
            "linked_owner_record": owner,
            "audit_logs": audit_logs,
            "note": "Fichier exporte au titre du droit a la portabilite (art. 20 RGPD)",
        }
        # Log this export
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": user.get("email", ""),
            "action": "legal.rgpd_export",
            "details": {},
            "timestamp": _now(),
        })
        return JSONResponse(
            content=json.loads(json.dumps(export, default=str)),
            headers={
                "Content-Disposition": f'attachment; filename="copromanager-export-{user_id[:8]}-{_now()[:10]}.json"'
            },
        )

    @router.post("/rgpd/delete-account")
    async def rgpd_delete_account(data: DeleteAccountInput, request: Request,
                                  background: BackgroundTasks):
        """RGPD art. 17 - droit a l'oubli. Marque le compte pour suppression
        differree (30 jours). Certaines donnees sont conservees pour obligations
        legales (7 ans pour donnees comptables belges - art. III.86 CDE).
        """
        user_id = getattr(request.state, "user_id", "")
        oid = _oid(user_id) if user_id else None
        if not oid:
            raise HTTPException(401, "Authentification requise")
        if data.confirm != "SUPPRIMER MON COMPTE":
            raise HTTPException(400,
                'Pour confirmer, envoyez le champ "confirm" avec la valeur exacte "SUPPRIMER MON COMPTE".')
        user = await db.users.find_one({"_id": oid})
        if not user:
            raise HTTPException(404, "Utilisateur introuvable")
        # Refuser la suppression pour le superadmin sans en avoir un autre
        if user.get("role") in ("superadmin", "admin"):
            others = await db.users.count_documents({
                "role": {"$in": ["superadmin", "admin"]},
                "_id": {"$ne": oid},
            })
            if others == 0:
                raise HTTPException(403,
                    "Impossible de supprimer le dernier compte super administrateur. Contactez le support.")
        purge_at = datetime.now(timezone.utc).timestamp() + 30 * 24 * 3600
        await db.users.update_one(
            {"_id": oid},
            {"$set": {
                "deletion_requested_at": _now(),
                "deletion_purge_at_ts": purge_at,
            }}
        )
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": user.get("email", ""),
            "action": "legal.rgpd_delete_request",
            "details": {"purge_at": purge_at},
            "timestamp": _now(),
        })
        return {
            "message": "Demande de suppression enregistree",
            "purge_in_days": 30,
            "note": ("Votre demande de suppression est enregistree. La suppression "
                     "definitive interviendra sous 30 jours. Vous pouvez encore "
                     "annuler pendant cette periode via le support. Les donnees "
                     "comptables liees au syndic (factures, journaux) sont "
                     "conservees 7 ans conformement a l'article III.86 CDE."),
        }

    @router.post("/rgpd/cancel-deletion")
    async def cancel_deletion(request: Request):
        """Annule une demande de suppression enregistree (dans les 30 jours)."""
        user_id = getattr(request.state, "user_id", "")
        oid = _oid(user_id) if user_id else None
        if not oid:
            raise HTTPException(401, "Authentification requise")
        result = await db.users.update_one(
            {"_id": oid, "deletion_requested_at": {"$exists": True}},
            {"$unset": {"deletion_requested_at": "", "deletion_purge_at_ts": ""}},
        )
        if result.modified_count == 0:
            return {"message": "Aucune demande de suppression a annuler"}
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": getattr(request.state, "user_email", ""),
            "action": "legal.rgpd_delete_cancel",
            "details": {},
            "timestamp": _now(),
        })
        return {"message": "Demande de suppression annulee"}

    # === Admin endpoints (superadmin only) ===================================

    async def _require_superadmin(request: Request):
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403,
                "Seul un super administrateur peut gerer les documents legaux")
        return user

    @router.get("/admin/documents")
    async def admin_list_documents(request: Request):
        """Liste complete des documents legaux (avec contenu) pour edition."""
        await _require_superadmin(request)
        await _ensure_defaults(db)
        docs = await db.legal_documents.find({}, {"_id": 0}).to_list(50)
        docs.sort(key=lambda d: ["cgu", "privacy", "mentions", "cookies", "disclaimer"].index(d.get("slug", "cgu")) if d.get("slug") in ["cgu", "privacy", "mentions", "cookies", "disclaimer"] else 999)
        return docs

    @router.put("/admin/documents/{slug}")
    async def admin_update_document(slug: str, data: UpdateDocumentInput, request: Request):
        """Met a jour le contenu d'un document. Si bump_version=True, incremente
        la version et force les utilisateurs a re-accepter les CGU/Privacy."""
        admin_user = await _require_superadmin(request)
        await _ensure_defaults(db)
        existing = await db.legal_documents.find_one({"slug": slug}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Document introuvable")

        content = (data.content or "").strip()
        if not content:
            raise HTTPException(400, "Le contenu ne peut pas etre vide")
        if len(content) > 200_000:
            raise HTTPException(400, "Contenu trop volumineux (max 200 000 caracteres)")

        old_version = int(existing.get("version", 1))
        new_version = old_version + 1 if data.bump_version else old_version
        update_set = {
            "content": content,
            "updated_at": _now(),
            "version": new_version,
        }
        if data.title:
            update_set["title"] = data.title[:200]
        # Audit trail preserve la version precedente
        await db.legal_document_history.insert_one({
            "id": str(uuid.uuid4()),
            "slug": slug,
            "version_before": old_version,
            "version_after": new_version,
            "bumped": data.bump_version,
            "title_before": existing.get("title", ""),
            "content_before": existing.get("content", ""),
            "edited_by_user_id": str(admin_user.get("_id") or admin_user.get("id", "")),
            "edited_by_email": admin_user.get("email", ""),
            "edited_at": _now(),
        })
        await db.legal_documents.update_one({"slug": slug}, {"$set": update_set})

        # Log dans l'audit trail general
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": str(admin_user.get("_id") or admin_user.get("id", "")),
            "user_email": admin_user.get("email", ""),
            "action": "legal.admin_edit_document",
            "details": {
                "slug": slug,
                "version_before": old_version,
                "version_after": new_version,
                "bumped": data.bump_version,
            },
            "timestamp": _now(),
        })

        return {
            "message": ("Document mis a jour et nouvelle version publiee. Les utilisateurs devront re-accepter."
                       if data.bump_version and slug in ("cgu", "privacy")
                       else "Document mis a jour"),
            "slug": slug,
            "version": new_version,
            "bumped": data.bump_version,
        }

    @router.get("/admin/documents/{slug}/history")
    async def admin_document_history(slug: str, request: Request):
        """Historique des modifications d'un document (versions precedentes)."""
        await _require_superadmin(request)
        history = await db.legal_document_history.find(
            {"slug": slug}, {"_id": 0, "content_before": 0},
        ).sort("edited_at", -1).to_list(100)
        return history

    # === RGPD Register (art. 30) ===============================================

    @router.get("/admin/rgpd-register")
    async def get_rgpd_register(request: Request):
        """Renvoie les donnees editables du registre des traitements RGPD."""
        await _require_superadmin(request)
        doc = await db.legal_rgpd_register.find_one({"_id": "default"}, {"_id": 0})
        if not doc:
            # Return defaults so the UI can pre-fill
            from pdf_rgpd_register import (
                DEFAULT_PROCESSINGS, DEFAULT_SUBPROCESSORS, DEFAULT_SECURITY_MEASURES,
            )
            doc = {
                "controller": {
                    "societe": "[SOCIETE]",
                    "forme_juridique": "[FORME_JURIDIQUE]",
                    "adresse": "[ADRESSE_COMPLETE]",
                    "bce": "[NUMERO_BCE]",
                    "tva": "[NUMERO_TVA]",
                    "representant": "[NOM_REPRESENTANT]",
                    "email": "welcome@goodexperienceproperties.be",
                    "telephone": "[TELEPHONE]",
                    "dpo_email": "welcome@goodexperienceproperties.be",
                },
                "processings": DEFAULT_PROCESSINGS,
                "subprocessors": DEFAULT_SUBPROCESSORS,
                "security_measures": DEFAULT_SECURITY_MEASURES,
                "updated_at": None,
            }
        return doc

    class RgpdRegisterInput(BaseModel):
        controller: dict
        processings: list
        subprocessors: list
        security_measures: list

    @router.put("/admin/rgpd-register")
    async def update_rgpd_register(data: RgpdRegisterInput, request: Request):
        """Met a jour les donnees editables du registre RGPD."""
        admin_user = await _require_superadmin(request)
        payload = {
            "controller": data.controller or {},
            "processings": data.processings or [],
            "subprocessors": data.subprocessors or [],
            "security_measures": data.security_measures or [],
            "updated_at": _now(),
            "updated_by_email": admin_user.get("email", ""),
        }
        await db.legal_rgpd_register.update_one(
            {"_id": "default"}, {"$set": payload}, upsert=True,
        )
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": str(admin_user.get("_id") or admin_user.get("id", "")),
            "user_email": admin_user.get("email", ""),
            "action": "legal.rgpd_register_update",
            "details": {"processings_count": len(payload["processings"]),
                        "subprocessors_count": len(payload["subprocessors"])},
            "timestamp": _now(),
        })
        return {"message": "Registre mis a jour", "updated_at": payload["updated_at"]}

    @router.get("/admin/rgpd-register/pdf")
    async def download_rgpd_register_pdf(request: Request):
        """Genere le PDF du registre des traitements (art. 30 RGPD) pret pour l'APD."""
        admin_user = await _require_superadmin(request)
        from fastapi.responses import Response
        from pdf_rgpd_register import build_rgpd_register_pdf
        from pdf_layout import fetch_logo_bytes

        register = await db.legal_rgpd_register.find_one({"_id": "default"}, {"_id": 0})
        if not register:
            # Use defaults
            register = await get_rgpd_register(request)

        # iter90dj : si l'admin a configure un logo/mentions legales, les
        # utiliser dans le PDF (pied de page + entete).
        syndic_pdf_ctx = None
        try:
            uid = str(admin_user.get("_id") or admin_user.get("id", ""))
            cfg = await db.syndic_configs.find_one({"syndic_user_id": uid})
            if cfg:
                logo_bytes = await fetch_logo_bytes(db, uid)
                syndic_pdf_ctx = {
                    "syndic_user_id": uid,
                    "syndic_config": cfg,
                    "logo_bytes": logo_bytes,
                    "legal_mentions": cfg.get("legal_mentions", ""),
                }
        except Exception:  # noqa: BLE001
            syndic_pdf_ctx = None

        pdf_bytes = build_rgpd_register_pdf(register, syndic_pdf_ctx=syndic_pdf_ctx)

        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": str(admin_user.get("_id") or admin_user.get("id", "")),
            "user_email": admin_user.get("email", ""),
            "action": "legal.rgpd_register_pdf_download",
            "details": {"size_bytes": len(pdf_bytes)},
            "timestamp": _now(),
        })

        filename = f"registre-rgpd-copromanager-{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    return router
