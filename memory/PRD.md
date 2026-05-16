# CoproManager - PRD

## Problem Statement
Programme comptable complet pour gestion copropriete belge: PCMN, exercices, budgets, appels de fonds, balance de tiers proprietaires/fournisseurs, fournisseurs, facturation cles de repartition, compteurs, interface bancaire CODA avec saisie en ligne et lettrage fournisseur, decomptes PDF, bilan, resultats, grand livre, multi-coproprietes archivables, roles superadmin/syndic/owner, codes VCS.

## Architecture
- Backend: FastAPI + MongoDB - 14 route modules, 95 tests
- Frontend: React 19 + Tailwind + Shadcn/UI - 19 pages
- Auth: JWT httpOnly cookies, bcrypt, 3 roles
- PDF: reportlab

## Implemented (May 2026)
- [x] Auth JWT (superadmin/syndic/owner)
- [x] Multi-coproprietes avec archivage
- [x] PCMN complet (95 comptes belges)
- [x] Exercices comptables + cloture + a-nouveau
- [x] Budgets previsionnels + comparaison budget/reel
- [x] Journaux comptables (OD, AV, AP, AN)
- [x] Grand Livre avec solde progressif
- [x] Balance des comptes + Bilan + Compte de resultats
- [x] Balance de tiers proprietaires (debiteur/crediteur)
- [x] Balance de tiers fournisseurs (a payer/trop-paye)
- [x] Situation de compte detaillee par tiers
- [x] Fournisseurs (TVA, IBAN, BIC)
- [x] Facturation avec cles de repartition
- [x] Appels de fonds + suivi paiements + generation ecritures
- [x] Codes VCS auto-generes (mod-97)
- [x] Import CODA + saisie en ligne (ajout lignes a la volee)
- [x] Lettrage transactions/factures/proprietaires/fournisseurs
- [x] VCS lookup automatique dans saisie bancaire
- [x] Compteurs (eau, chauffage, electricite)
- [x] Decomptes annuels PDF par proprietaire
- [x] Documents par categories
- [x] Gestion utilisateurs avec droits
- [x] 95/95 tests passent

## Backlog P0
- Auth middleware sur toutes les routes
- Stocker supplier_id sur factures (au lieu du nom)
