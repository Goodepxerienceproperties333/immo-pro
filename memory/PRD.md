# CoproManager - PRD

## Problem Statement
Programme comptable complet pour la gestion de copropriete en Belgique, integrant: PCMN, journaux comptables, exercices, budgets, appels de fonds, fournisseurs, facturation avec cles de repartition, compteurs, interface bancaire CODA, lettrage, decomptes annuels PDF, bilan, compte de resultats, grand livre, gestion multi-coproprietes, roles superadmin/syndic/owner, codes VCS.

## Architecture
- Backend: FastAPI + MongoDB (Motor async) - 12 route modules
- Frontend: React 19 + Tailwind + Shadcn/UI - 18 pages
- Auth: JWT httpOnly cookies, bcrypt, 3 roles (superadmin/syndic/owner)
- PDF: reportlab

## Implemented (May 2026)
- [x] Auth JWT avec roles superadmin/syndic/owner
- [x] Multi-coproprietes avec selecteur
- [x] PCMN complet (95 comptes belges)
- [x] Exercices comptables avec cloture et a-nouveau
- [x] Budgets previsionnels avec comparaison budget/reel
- [x] Journaux comptables (OD, AV, AP, AN) avec validation equilibre
- [x] Grand Livre avec solde progressif
- [x] Balance des comptes
- [x] Bilan (actif/passif)
- [x] Compte de resultats (charges/produits)
- [x] Fournisseurs (TVA, IBAN, BIC)
- [x] Facturation avec cles de repartition
- [x] Appels de fonds avec suivi paiements et generation ecritures
- [x] Codes VCS auto-generes (communication structuree belge mod-97)
- [x] Import CODA (parser fichiers bancaires belges)
- [x] Lettrage transactions/factures/proprietaires
- [x] Compteurs (eau, chauffage, electricite) avec releves
- [x] Decomptes annuels par proprietaire avec PDF
- [x] Documents par categories
- [x] Gestion utilisateurs avec droits
- [x] 68/68 tests backend passent

## Backlog
### P0
- Ajouter auth middleware a tous les routes (seuls coproprietes et admin sont proteges)
- Tests CODA avec fichiers reels

### P1
- Portail proprietaire (vue restreinte)
- Rappels de paiement automatiques
- Historique transferts de propriete

### P2
- Gestion AG (assemblees generales)
- Multi-exercice dans les rapports
- Export CSV/Excel des rapports
