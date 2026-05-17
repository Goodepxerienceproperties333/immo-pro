# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur donnees comptables/financieres.
Auth: JWT cookie + middleware global FastAPI.
Roles: `superadmin`, `syndic`, `gestionnaire`, `owner`.

## Security model
1. Auth middleware global sur /api/* (sauf /auth/login, /auth/register, /auth/refresh, /auth/logout).
2. RBAC path-based: admin only sur /api/admin/* + /api/users; owner restreint a /api/owner/*, /api/auth/*, GET /api/coproprietes(+sous-paths), GET /api/documents/.../download.
3. Chinese walls: `copropriete_id` propage automatiquement (frontend interceptor) et filtre cote backend.

## Implemented

### Iter13 (Feb 2026) - Workflow Budget -> Appels de fonds
- **Budget enrichi** (`fiscal.py`)
  - Champs `status` (draft/approved), `approved_at`, `approved_by`
  - `distribution_key_id` par ligne (chaque nature de depense a sa propre cle)
  - Edition/suppression bloquees si approved (revoke d'abord obligatoire)
  - POST /api/fiscal/budgets/{id}/approve + /revoke
- **Recapitulatif N-1** (`GET /api/fiscal/previous-year-expenses?fiscal_year_id=`)
  - Agregation invoices.total_amount + journal_entries class-6 net (hors entries liees a fund_call)
  - Groupement par (compte PCMN, distribution_key)
  - Bouton "Pre-remplir depuis N-1" dans le dialog budget
- **Assistant d'appels de fonds 4 etapes** (`fund_calls.py` + `BudgetWizard.js`)
  - Etape 1 - Frequence: 1/2/3/4/6/12 appels (annuel a mensuel)
  - Etape 2 - Calendrier: date 1er appel + due_offset_days, calendrier auto des N dates
  - Etape 3 - Fonds de reserve oui/non + montant + cle dediee (applique au call 1 uniquement)
  - Etape 4 - Recap: tableau N appels, montant, reserve, nb proprietaires + details proprietaire du 1er appel
- **Endpoints** :
  - POST /api/fund-calls/preview-from-budget (sans persister) : 400 si budget draft
  - POST /api/fund-calls/generate-from-budget (persiste N fund_calls avec status pending)
- **Multi-clés distribution** : chaque ligne du budget distribue son quart selon SA cle, sommation par proprietaire
- Declenchement auto du wizard a l'approbation du budget

### Iter12 (Feb 2026) - finalisation P1/P2
- **Bilan & Compte de Resultats PCMN belge strict** (`reports.py` lignes 130-401)
  - Rubriques I-VIII actif / I-VII passif officielles, calcul automatique du resultat de l'exercice injecte dans III bis
  - Compte de resultat groupe par rubriques 60-67 / 70-76 avec label de resultat (Benefice/Perte/Equilibre)
- **Exports Excel** (`routes/exports.py`)
  - GET /api/exports/balance-tiers/owners.xlsx
  - GET /api/exports/bilan.xlsx
  - GET /api/exports/grand-livre.xlsx
- **Rappels paiement automatises** (`routes/exports.py` create_reminders_router + `pages/RemindersPage.js`)
  - GET /api/reminders/late-payments?grace_days=N -> tri par severite (critique +90j, urgent +30j, rappel2 +7j, rappel1)
  - GET /api/reminders/owner/{owner_id}/letter -> PDF lettre de rappel professionnelle avec VCS
  - Page UI dediee avec cartes statistiques + table des retards
- **Auto-extraction IA factures fournisseurs** (`routes/invoice_ai.py`)
  - POST /api/invoices-ai/extract: upload PDF -> pypdf extract + Claude Sonnet 4.5 -> JSON {supplier, number, date, montants, vat, suggested PCMN}
  - Frontend: bouton "Importer facture PDF (IA)" dans InvoicesPage qui pre-remplit le formulaire
  - PDF source attache automatiquement a la facture creee
- **Pieces jointes aux ecritures comptables et factures** (NOUVEAU user demande Feb 2026)
  - Journal entries (OD/AV/AP): POST/GET/DELETE /api/accounting/entries/{id}/attachments[/{att_id}]
  - Invoices: POST/GET/DELETE /api/invoices/{id}/attachments[/{att_id}]
  - Formats: PDF, PNG, JPG
  - Storage: /app/uploads/journal_attachments + /app/uploads/invoice_attachments
  - Cleanup auto a la suppression de l'ecriture/facture
  - UI: icone Paperclip sur chaque ligne + dialog d'upload/download/suppression

### Iter11 - Portail proprietaire (DONE)
- Login owner -> redirection auto vers `/portal`
- 6 endpoints `/api/owner/*` (resolution auto par email)
- Page OwnerPortalPage: identite + VCS copiable, 4 stats, alertes appels en attente, onglets coproprietes/appels/charges/documents
- Bouton "Telecharger mon decompte annuel (PDF)"

### Comptabilite (PCMN par ACP)
- PCMN scope par ACP (95 comptes belges seedes a la creation)
- 2 comptes actifs par defaut: 614000 Honoraires syndic + 615000 Frais de gestion
- Toggle active/inactive par compte
- Banques auto-generent PCMN 550xxx (epargne) / 551xxx (vue)
- Exercices fiscaux avec cloture + a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AC/AN) + ATTACHMENTS, Grand Livre, Balance, Bilan, Resultat

### Banque
- CODA import, saisie inline, edition + suppression transactions
- VCS mod-97, auto-lettrage
- Lookup contrepartie

### Facturation
- Cles de repartition par ACP avec quotites
- Factures + ATTACHMENTS, avec ventilation
- Appels de fonds avec distribution
- Balance de tiers

### Documents
- Upload PDF/images stockage disque
- Auto-classification IA via Claude Sonnet 4.5
- 10 categories par defaut seedees a chaque ACP

### UX
- Wizard ACP 3 etapes
- Autocomplete proprietaires par nom/email/VCS
- Bouton "Generer ACP demo" (admin, idempotent)

### Deploiement
- Scaleway docker-compose + nginx + webhook GitHub
- GDPR compliant (EU hosting Paris)

## Test coverage
- iter7: 16/16 chinese walls + 139/140 regression
- iter8: 18/18 features + 157/158 regression
- iter9: 43/43 auth middleware
- iter10: 28/28 RBAC + 59/59 regression
- iter11: 30/30 owner portal + 84/87 regression
- **iter12: 20/20 new features (Bilan/Resultat rubriques, Excel exports, reminders, AI invoice, attachments, RBAC) + 29/30 regression**
- **iter13: 15/15 Budget workflow (N-1 expenses, approve/revoke, edit-lock, preview/generate-from-budget, reserve on call#1, multi-key distribution, RBAC, frequency)**

## Backlog P1
- Gestion AG (ordre du jour, votes, PV, convocations)
- Notifications email (Resend) lors des nouveaux appels de fonds

## Backlog P2
- Bordereaux SEPA pain.001
- Module gros entretien fonds de reserve
- Optimisation perf reminders/exports (eviter O(n^2) pour gros ACP)
- Limit file size attachments (25 MB max) + sanitize filename

## Files of reference
- `/app/backend/server.py`: auth + RBAC middleware + router registration (lignes 380-410)
- `/app/backend/routes/exports.py`: Excel exports + reminders + lettre PDF
- `/app/backend/routes/invoice_ai.py`: extraction IA factures fournisseurs
- `/app/backend/routes/reports.py`: bilan + resultat rubriques PCMN
- `/app/backend/routes/accounting.py`: journal entries + ATTACHMENTS
- `/app/backend/routes/invoices.py`: factures + ATTACHMENTS
- `/app/backend/routes/owner_portal.py`: 6 endpoints owner
- `/app/backend/pdf_decompte.py`: PDF decompte annuel
- `/app/frontend/src/pages/RemindersPage.js`: page rappels
- `/app/frontend/src/pages/ReportsPage.js`: rubriques + boutons Excel export
- `/app/frontend/src/pages/InvoicesPage.js`: bouton IA + paperclip + attachments dialog
- `/app/frontend/src/pages/JournalsPage.js`: paperclip + attachments dialog
- `/app/frontend/src/pages/BalanceTiersPage.js`: bouton Excel
- `/app/frontend/src/pages/OwnerPortalPage.js`: portail proprietaire
- `/app/frontend/src/App.js` + `/app/frontend/src/components/Layout.js`: routes + sidebar
