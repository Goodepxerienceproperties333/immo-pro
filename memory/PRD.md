# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur les données comptables/financières.
Collections globales: `users`, `owners`, `suppliers`.
Collections scopees par ACP: tout le reste incluant le **PCMN par ACP**.

## Implemented (Feb 2026)

### Core
- Auth JWT cookie (superadmin/syndic/owner)
- Dashboard global sans sidebar, sidebar visible uniquement quand ACP selectionnee
- Multi-coproprietes archivables, references auto ACP-YYYYMM-NNN
- Detection doublons proprietaires/fournisseurs

### Comptabilite (PCMN par ACP)
- **PCMN scope par ACP** (95 comptes belges seedes a la creation)
- 2 comptes actifs par defaut: **614000 Honoraires syndic** + **615000 Frais de gestion**
- Toggle active/inactive sur chaque compte
- Banques auto-generent leur compte PCMN 550xxx (epargne) / 551xxx (vue)
- Exercices fiscaux avec cloture + a-nouveau
- Journaux (OD/AV/AP/AC/AN), Grand Livre, Balance, Bilan, Compte de Resultats
- Budgets + comparaison

### Banque
- CODA import, saisie en ligne, edition + suppression transactions
- VCS mod-97, auto-lettrage automatique
- Lookup contrepartie (proprietaires globaux + factures locales)

### Facturation
- Cles de repartition par ACP avec quotites
- Factures avec ventilation par cle de repartition
- Appels de fonds avec distribution automatique
- Balance de tiers debiteurs/crediteurs

### Documents
- **Upload PDF/images** avec stockage disque
- **Auto-classification IA via Claude Sonnet 4.5** (extraction PDF text -> JSON metadata)
- 10 categories par defaut creees a la creation d'une ACP:
  ROI, Acte de base, Statuts, PV d'AG, Contrats, Polices d'assurance,
  Factures fournisseurs, Decomptes, Rapports techniques, Autres
- Download/delete documents

### Wizard ACP (3 etapes)
- Step 1: Identite + adresse + comptes bancaires
- Step 2: Lots avec autocomplete proprietaires par nom/email/VCS
- Step 3: Options + recapitulatif + creation

### Demo
- Bouton "Generer ACP de demo" (Syndic uniquement)
- Cree: 1 ACP, 8 owners, 8 lots, 5 suppliers, 10 invoices, 2 fund calls,
  12 bank transactions, 16 journal entries, 2 distribution keys,
  97 PCMN accounts, 10 doc categories
- Idempotent (skip si deja existant)

### Deploiement
- Scaleway docker-compose + nginx + webhook GitHub
- GDPR compliant (EU hosting Paris)

## Backlog P0
- Portail proprietaire restreint (vue par ACP: balances, documents, appels)
- Auth middleware systematique sur toutes routes

## Backlog P1
- Decomptes annuels PDF reportlab complets (cle repartition + cloture)
- Bilan & Compte de Resultats: logique PCMN belge stricte
- Export Excel rapports + balance de tiers
- Rappels paiement automatises
- Gestion AG (ordre du jour, votes, PV, convocations)
- Bordereaux SEPA pain.001
- Module gros entretien fonds de reserve

## Test coverage
- iter7: 16/16 chinese walls + 139/140 regression
- iter8: 18/18 nouvelles features + 157/158 regression
- Aucun bug critique/mineur

## Files of reference
- `/app/backend/routes/coproprietes.py`: wizard backend + PCMN seed + cascade delete
- `/app/backend/routes/accounting.py`: PCMN CRUD scopé + toggle-active
- `/app/backend/routes/documents.py`: upload + Claude classification + categories par defaut
- `/app/backend/routes/demo_seed.py`: bouton demo idempotent
- `/app/backend/routes/reports.py`: tous reports scopes par ACP
- `/app/frontend/src/pages/CoproprietesPage.js`: wizard 3 etapes
- `/app/frontend/src/pages/LotsPage.js`: autocomplete proprietaires
- `/app/frontend/src/pages/DocumentsPage.js`: upload + Claude AI
- `/app/frontend/src/pages/DashboardPage.js`: bouton seed demo
- `/app/frontend/src/lib/api.js`: chinese-wall interceptor avec liste globale (owners, suppliers, users)
