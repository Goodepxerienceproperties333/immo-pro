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

### Iter19 (Feb 2026) - Frais privatifs + Wizard Fonds de roulement
- **Frais privatifs sur facture** (`routes/invoices.py` + `auto_entries.py` + `pages/InvoicesPage.js`):
  - Nouveau champ `is_private_fee` + `private_fee_owner_id` sur InvoiceInput
  - Quand active : `account_number` force a 643, `distribution_key_id` ignore, `distribution_lines=[]`
  - UI : checkbox ambree "Frais privatif" + champ de recherche proprietaire (filtre par nom/email/VCS)
  - **Ecriture AC auto-generee 4 lignes** (modele Finlead) :
    - Dr 643 (Frais privatifs) montant / Cr 44000XXX (Fournisseur) montant
    - Dr 40000XXX (Proprietaire) montant / Cr 643 (Imputation) montant
    - Resultat : compte 643 net = 0, fournisseur credite, owner tiers debite
  - Validations : 400 si owner manquant, 404 si owner invalide
- **Wizard Budget - Etape 4 "Fonds de roulement"** (`components/BudgetWizard.js` + `routes/fund_calls.py` + `auto_entries.py`):
  - Nouvelle etape inseree entre "Fonds de reserve" et "Recapitulatif" (5 etapes au total)
  - Choix Mode : **Creation initiale** ou **Augmentation**
  - Champs : libelle, montant total, cle de repartition (Tantiemes par defaut)
  - Le montant est ajoute UNIQUEMENT au 1er appel (comme la reserve)
  - **Ecriture VE auto-generee** : prorate par proprietaire avec Dr 40000XXX + Cr 100 (Fonds de roulement general, classe 1)
  - Recap a 5 cartes (Nbre appels, Budget, Reserve, **Roulement**, Total) + colonne "Dont roulement" dans le tableau d'appels

### Iter18 (Feb 2026) - PCMN belge officiel (327 + compat) + CRUD custom + PDF Liste des depenses
- **PCMN renouvele**: `pcmn_data.py` contient maintenant les **327 comptes officiels** fournis par l'utilisateur (CSV Finlead) + **10 comptes de compatibilite** (`PCMN_COMPAT_ACCOUNTS`) pour preserver les automatismes existants (tier_accounts 40000XXX/40010XXX/44000XXX, banques 550xxx/551xxx, fallback auto-entries 614000/615000/700000/701000). Export `PCMN_ALL_ACCOUNTS = 337` comptes au total.
- **Hierarchie complete** Classes 1 a 7 (Bilan + Resultat) avec parents auto-detectes par prefixe.
- **Seed par defaut**: nouvelle ACP -> 337 comptes (614000 & 615000 actifs, le reste inactif). `demo_seed.py` et `coproprietes.py` ont ete migres.
- **CRUD PCMN renforce** (`routes/accounting.py`):
  - POST `/api/accounting/pcmn` : creation custom (is_custom=true), auto-derivation class_num (1er chiffre) + type (1-5 balance, 6-7 result), active=true par defaut, 400 si numero non-numerique ou doublon.
  - PUT `/api/accounting/pcmn/{number}` : modification partielle via `PCMNUpdateInput` (name/class_num/parent/type/active).
  - PATCH `/api/accounting/pcmn/{number}/toggle-active` (inchange).
  - DELETE `/api/accounting/pcmn/{number}` : 3 niveaux de protection - (1) is_tier_account => refus, (2) is_custom=false sans `force=true` => 400, (3) compte utilise dans journal_entries / invoices / expense_categories => 409 avec compte exact.
- **Migration superadmin idempotente**: POST `/api/admin/migrate/pcmn-import?copropriete_id=opt` ajoute uniquement les comptes manquants sans toucher l'existant. Retourne stats {added, existing} par ACP.
- **Page Plan Comptable enrichie** (`pages/AccountingPage.js`):
  - Filtres : recherche, classes 1-7 (tabs), actifs uniquement, customs uniquement
  - Toggle actif inline (Switch shadcn)
  - Badges statut : "Tiers auto" (violet), "Custom" (ambre), "Officiel" (gris)
  - Boutons Edit + Delete (delete grise si tier auto)
  - Bouton "Importer PCMN complet" pour superadmin
  - Dialog creation/edition avec auto-derivation class+type a la saisie du numero
- **PDF "Liste des depenses"** (`pdf_liste_depenses.py` + GET `/api/reports/depenses/pdf`):
  - Format landscape A4, colonnes : Date valeur, Libelle, Fournisseur, Ref. interne, Montant, Part proprietaire, Part occupant
  - Hierarchie : Cle de repartition (titre bleu) -> Nature (gras gris) -> Compte PCMN (italique) -> lignes
  - Sous-totaux par nature + par cle + ligne "Totaux generaux immeuble" finale
  - En-tete avec ACP + periode + "Fait le" + IPI/BCE
  - Bouton "Liste des depenses (PDF)" dans `ExpensesPage.js`
- **PDF Decompte aligne**: colonnes renommees pour matcher la nomenclature Syndic ("Date valeur", "Ref. interne", "Montant TVAC", "Votre quote-part")

### Iter17 (Feb 2026) - Natures de depense + edition cles + edit auto entries
- **Module "Nature de depense"** (`expense_categories.py` + `ExpenseCategoriesPage.js`)
  - Relation 1:1 stricte avec un compte PCMN classe 6 (validation 409 + 400)
  - Lookup par nom du compte via Popover + Command shadcn
  - Affiche invoice_count + invoice_total
  - CRUD complet, DELETE refusé si factures liées
- **Facture liee a une nature**: champ `expense_category_id` sur InvoiceInput. POST/PUT auto-derivent `account_number` depuis la categorie selectionnee. Page Invoices: select nature -> auto-fill compte PCMN
- **Edition cle de repartition avec warning**:
  - GET /api/distribution-keys/{id}/usage retourne invoices/budgets/fund_calls liés
  - PUT sans force=true -> 409 si invoices liées; PUT?force=true -> detach invoices + update
  - Dialog d'edition avec banner ambre warning + liste des factures liées + auto-confirmation force
- **Edition des ecritures auto AC/VE/FI**:
  - PUT autorisé même sur auto_generated; marque `manually_edited=true` + `manually_edited_at`
  - DELETE autorisé uniquement si `manually_edited=true` (sinon 400)
  - `_delete_auto_entries` skip les entries `manually_edited` (préservation lors d'un update de source)
  - Badges UI: "Auto" bleu si pas modifié, "Modifie" orange si edité
- **PDF decompte 3 niveaux**: Cle de repartition -> Nature de depense (libellé) -> Compte PCMN (numéro). Sous-totaux par cle + par nature.
- **Bouton modifier** sur ExpensesPage: deep-link `/invoices?edit={id}` ouvre directement le dialog d'edition

### Iter16 (Feb 2026) - Auto-écritures + Clôture + Page Dépenses
- **Module `auto_entries.py`** : 3 helpers
  - `generate_purchase_entry` -> AC journal (Dr 6xxxxx + Cr 44000XXX) sur POST/PUT facture
  - `generate_sale_entry` -> VE journal (Dr 40000XXX par owner + Cr 700000, Dr 40010XXX + Cr 701000 pour réserve) sur POST fund_call et generate-from-budget
  - `generate_bank_entry` -> FI journal (Dr/Cr 550xxx + 40000XXX/44000XXX) sur lettrage manuel + auto-VCS
- **Cleanup automatique** des auto entries quand la source est supprimée (invoice, fund_call, bank_txn). DELETE manuel des auto entries -> 400.
- **Journaux séparés**: tabs Operations Diverses / Achats / Ventes / Financier / A-Nouveau dans JournalsPage avec badge "Auto" sur les écritures générées.
- **Page Dépenses** (`ExpensesPage.js` + GET /api/fiscal/expenses) : filtres exercice, nature (compte 6xx), clé de répartition, compte bancaire, dates. Agrégations Top 3 par nature/clé/banque + total + count. Indicateur PJ (paperclip).
- **Clôture annuelle - Régularisation** (`/api/fiscal/years/{id}/regularize`)
  - Mode `dry_run=true` : preview sans persister
  - Calcule budget vs frais réels vs provisions appelées
  - Extourne automatiquement les provisions (Dr 700000 / Cr 40000XXX)
  - Affecte les frais réels par clé de répartition (Dr 40000XXX / Cr 700000)
  - Fonds de réserve (40010XXX/701000) PAS extourné (reste au bilan)
  - DELETE pour rollback la régularisation
  - Dialog `RegularizationDialog` 2 étapes: preview détaillé + confirm
  - Bouton Calculator orange sur les exercices ouverts

### Iter15 (Feb 2026) - Comptes tiers automatiques + bug balance
- **Module `tier_accounts.py`** : helper d'assignation automatique de comptes PCMN par tiers
  - Propriétaire = 2 comptes par ACP: `40000XXX` (provisions charges) + `40010XXX` (fonds réserve)
  - Fournisseur = 1 compte par ACP: `44000XXX`
  - Numérotation: 3 chiffres séquentiels, scopée par ACP, gap-free
  - Idempotent: tier_accounts persistées dans owner.tier_accounts[copro_id] et supplier.tier_accounts[copro_id]
- **Auto-assignation** sur POST/PUT owners + POST/PUT suppliers avec `copropriete_id`
- **Migration de masse** : POST /api/admin/migrate/tier-accounts (superadmin) — backfill tous les owners + suppliers existants (8 owners x 2 + 5 suppliers = 21 comptes créés en demo)
- **Bug fix balance tiers fournisseur** : matching case-insensitive entre invoice.supplier et supplier.name, inclusion des "orphelins" (fournisseurs présents dans factures mais sans fiche Supplier) avec badge ambre
- **Affichage** : colonne « Comptes (40000 / 40010) » sur balance propriétaires, colonne « Compte » sur balance fournisseurs
- **Pièce jointe à la création de facture** : bouton « Joindre la facture PDF / image » directement dans le dialog Nouvelle facture (en plus du flow IA)

### Iter14 (Feb 2026) - Affichage et regeneration
- **FundCallsPage enrichi** : affiche `lines` (detail par nature avec compte+libelle+cle+montant), `reserve_amount` (badge violet "dont reserve X EUR"), badge "Issu du budget X" (bleu) pour les appels generes par wizard
- **POST /api/fund-calls/regenerate-from-budget** : supprime les appels futurs UNPAID lies au budget puis regenere selon nouveau planning. Les appels avec >=1 paiement recu sont PRESERVES (history protection). Retourne deleted_count + preserved_count.
- **Bouton "Regenerer non-echus"** sur les cartes budget approuve (FiscalYearPage) -> ouvre le wizard en mode regenerate
- Workflow complet : approve -> wizard -> revoke -> modifier -> re-approve -> "Regenerer non-echus" -> ajustement intelligent sans casser l'historique

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
- **iter14: 8/8 regenerate-from-budget (history protection, scope filter, idempotence, RBAC) + 15/15 iter13 regression**
- **iter15: 16/16 tier accounts auto (assignment, migration idempotence, case-insensitive supplier matching, orphan handling, RBAC) + 8/8 iter14 regression**
- **iter16: 16/16 auto-entries AC/VE/FI + cleanup + manual delete protection + expenses endpoint + regularize dry-run/persist/delete + reserve-not-extourned + RBAC + 24/24 iter14+iter15 regression**
- **iter17: 19/19 expense categories 1:1 + invoice account derivation + dist-key usage/force-detach + auto-entry edit policy + PDF 3 niveaux + 16/16 iter16 regression**
- **iter18: 17/17 nouveau PCMN belge officiel 327 + 10 compat = 337/ACP, CRUD custom (is_custom flag, protection deletion 3-niveaux, auto-derivation class/type), migration pcmn-import idempotente, PDF Liste des depenses (Cle->Nature->Compte hierarchique, sous-totaux + totaux generaux) + 16/16 regression iter16 auto-entries**
- **iter19: 12/12 frais privatifs (AC 4 lignes 643/44000/40000/643 + UI search owner + validation 400/404) + wizard Fonds de roulement (etape 4 Create/Increase + VE credit 100 prorate par owner) + 33/33 regression iter16+iter18**

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
