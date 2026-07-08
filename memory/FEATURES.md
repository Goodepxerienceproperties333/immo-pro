# CoproManager — Documentation des fonctionnalites

> Version : 1.9.0 (Iteration 90bq — Feb 2026)
> Ce document est la version texte de la brochure commerciale PDF
> generee dynamiquement via `GET /api/documentation/features-pdf`.

## Table des matieres

1. [Structure de la copropriete](#1-structure-de-la-copropriete)
2. [Comptabilite belge (PCMN)](#2-comptabilite-belge-pcmn)
3. [Facturation et depenses](#3-facturation-et-depenses)
4. [Finance et banque](#4-finance-et-banque)
5. [Appels de fonds et decomptes](#5-appels-de-fonds-et-decomptes)
6. [Communication et emailing](#6-communication-et-emailing)
7. [Portail proprietaire](#7-portail-proprietaire)
8. [Import de donnees](#8-import-de-donnees)
9. [Rapports et exports](#9-rapports-et-exports)
10. [Securite et conformite](#10-securite-et-conformite)
11. [Administration](#11-administration)
12. [Intelligence artificielle](#12-intelligence-artificielle)

---

## 1. Structure de la copropriete

- **Gestion multi-ACP** : cloisonnement strict (Chinese Walls)
- **Fiche ACP** : donnees legales, IBAN multiples, quotites
- **Lots et quotites** : mutations, cles de repartition
- **Proprietaires/locataires** : RGPD conforme
- **Fournisseurs** : detection doublons (BCE + noms normalises + particules juridiques)

## 2. Comptabilite belge (PCMN)

- **Plan comptable minimum normalise** (arrete royal 21/10/2018)
- **Exercices fiscaux** : verrous, A-nouveau, imputation 499
- **Journaux** : AC, VE, FI, OD, AN. Ecritures double partie stricte.
- **Grand livre** : consultation, export PDF/Excel
- **Bilan** avant/apres repartition, compte de resultat, balance tiers

## 3. Facturation et depenses

- **Import PDF/CSV IA** (Claude Sonnet 4.5) — batch parallelise (5 concurrent)
- **Factures multi-lignes** avec commentaire par ligne
- **Auto-apprentissage fournisseur** : nature de depense pre-remplie
- **Frais privatifs** : allocations multi-proprietaires
- **Anti-doublons strict** : meme numero + fournisseur bloque
- **Ventilation Occupant/Proprietaire** par nature
- **Templates fournisseur** memorises

## 4. Finance et banque

- **Import CODA (Isabel)** natif
- **Import PDF bancaire IA** (BNP, ING, KBC, Belfius, Fintro, Beobank)
- **Lettrage automatique** (communication structuree + IBAN)
- **Splits multi-natures** par transaction
- **Virements internes** (compte 58)
- **Comptes bancaires multiples** avec badge de couleur distinctif
- **Ecritures FI automatiques** en double partie

## 5. Appels de fonds et decomptes

- **Appels de fonds** trimestriels avec personnalisation
- **Budget wizard** : previsionnel avec projection
- **Decompte de mutation PDF** (art. 3.87 CCiv)
- **Balance tiers proprietaires** avec export
- **Rappels automatiques** debiteurs (job APScheduler)

## 6. Communication et emailing

- **Microsoft Graph** par syndic (Azure AD + AES chiffre)
- **Templates d'email** CRUD + variables dynamiques
- **Envoi de situations** en 1 clic
- **Documents en piece jointe** automatique
- **Multi-mailbox** par syndic

## 7. Portail proprietaire

- **Login separe**
- **Consultation situation** + historique
- **Documents en telechargement**
- **Paiement en ligne** (roadmap Stripe/Bancontact)
- **Compteurs** (roadmap eau/chauffage/electricite)

## 8. Import de donnees

- **Import Optipro/Sage** wizard guide
- **Import Excel/CSV** universel
- **Detection doublons a l'import**
- **Bundle IA** (roadmap : ZIP heterogene)

## 9. Rapports et exports

- **Bilan comptable PDF**
- **Compte de resultat PDF**
- **Balance tiers PDF**
- **Grand livre** par compte
- **Certificat fiscal annuel** (roadmap)
- **Decomptes de mutation**
- **Journal des ecritures**

## 10. Securite et conformite

- **Cloisonnement Chinese Wall** avec X-Copropriete-Id
- **RBAC** : superadmin, admin, syndic, gestionnaire, owner
- **Rate limiting** slowapi
- **Cookies HttpOnly + Secure**
- **Chiffrement AES** MS Graph credentials
- **Sanitization XSS** DOMPurify (15/15 tests)
- **Legal blindage** : CGU + Confidentialite + Mentions + Cookies + Disclaimer
- **RGPD** : registre, audit, effacement 30j
- **Backups journaliers** 00h00 Brussels

## 11. Administration

- **Journal d'audit** superadmin
- **Outils de deblocage** superadmin
- **Historique connexions**
- **Gestion utilisateurs**
- **Configuration par syndic**
- **Notes de version** avec popup automatique au login

## 12. Intelligence artificielle

- **Claude Sonnet 4.5** (Emergent Universal Key)
- **OCR Tesseract** de fallback
- **Detection anomalies** sante comptable
- **Actions rapides adaptatives**

---

## Stack technique

| Couche | Technologie |
|---|---|
| Backend | FastAPI 0.115 (Python 3.11), Motor MongoDB, Pydantic v2 |
| Frontend | React 19, Tailwind CSS, Shadcn UI, Radix |
| Base de donnees | MongoDB 6.x + GridFS |
| Comptabilite | PCMN belge |
| IA | Claude Sonnet 4.5 via Emergent Universal Key |
| Email | Microsoft Graph API |
| Hebergement | Kubernetes cloud europeen |

---

## Genereration du PDF

- **UI** : `Plateforme > Notes de version > PDF commercial` (superadmin)
- **API** : `GET /api/documentation/features-pdf` (authent requis)
- **Format** : A4 avec page de couverture, sommaire par section colore,
  page technique, page contact commercial
