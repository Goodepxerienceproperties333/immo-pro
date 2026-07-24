# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email), SMTP (One2Net)

## Fonctionnalites implementees

### Session courante (Juillet 2026)

#### P0 - Fix Purge Syndic - Suppression owners complete (DONE)
- 5 sources de collecte owner_ids: lots.owner_id, lots.owner_ids, owners.copropriete_ids (array), owners.copropriete_id (singulier), owners.import_session_id
- Suppression des comptes utilisateur role=owner lies aux ACPs (sans syndic_user_id)
- Suppression owner_access_audit pour les owners purges
- Collections additionnelles: owner_bank_accounts, tier_accounts, deleted_entries, import_sessions, documents, document_categories
- Tests: 18/18 iteration 72 + 26/26 iteration 71

#### P1 - PCMN Consistency - Normalisation 8 chiffres (DONE)
- Nouvelle lib pcmn_utils.py: normalize_bank_pcmn() et pcmn_bank_match()
- Normalisation 6 chiffres (551618) -> 8 chiffres (55161800)
- Endpoint migration: POST /api/admin/migrate/normalize-bank-pcmn (idempotent)
- 3 cibles: coproprietes.bank_accounts, pcmn_accounts.number, journal_entries.lines
- _resolve_bank_account utilise pcmn_bank_match pour matching fuzzy
- Tests: 26/26 iteration 71

#### P0 - Fix Email SMTP "Boite non autorisee" (DONE)
- Cause: validation authorized_mailboxes ne reconnaissait pas smtp_username comme expediteur legitime
- Fix: quand provider=smtp, smtp_username est automatiquement ajoute a la liste des boites autorisees
- 3 fichiers corriges: syndic_config.py (test-email self + admin), communication.py (envoi reel)
- communication.py supporte maintenant l'envoi SMTP complet (avec PJ, BCC)

#### Purge DB Preview (DONE)
- Collections videes: coproprietes, owners, suppliers, lots, journal_entries, invoices, bank_statements, bank_transactions, budgets, etc.
- Preserves: users, pcmn_accounts

#### Securite Multi-Syndic syndic_id (EN COURS)
- Infrastructure: syndic_scope.py avec resolve_syndic_id(), syndic_query(), inject_syndic()
- Middleware: request.state.syndic_id calcule automatiquement dans server.py
- Verrouillage coproprietes.py: list, create, update, get filtres par syndic_id
- Verrouillage properties.py: list_owners, get_owner, update_owner, check-duplicate, _fetch_orphans
- Verrouillage suppliers.py: list, create, get, update, delete
- Verrouillage invoices.py: creation avec syndic_id
- Verrouillage accounting.py: creation journal entries avec syndic_id
- RESTANT: banking.py, fund_calls.py, import_wizard.py, reports.py, documents.py, fiscal.py, expense_categories.py

### Sessions precedentes
- Auto-lettrage, Balance de Tiers fix, Categorisation simplifiee, Filtre date $lte fix
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal
- Amelioration lettrage bancaire, Isolation ACP, Chatbot, Layout, Spinner

## Backlog prioritise

### P0 - En cours
- Securite syndic_id: finir les routes restantes (banking, fund_calls, import_wizard, reports, etc.)

### P1 - Important
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys dans reports.py et pdf_decompte.py (RECURRENT)

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)

## Refactoring
- import_wizard.py (>3400 lignes) a decouper
- reports.py (logique PCMN complexe) a simplifier
