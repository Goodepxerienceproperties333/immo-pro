# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email)

## Fonctionnalites implementees

### Session courante (Juillet 2026)

#### P0 - Fix Purge Syndic - Suppression owners complete (DONE)
- Collecte owner_ids via 4 methodes: lots.owner_id, lots.owner_ids, owners.copropriete_ids (array), owners.copropriete_id (singulier)
- Suppression en union de tous les owners trouves
- Collections additionnelles purgees: owner_bank_accounts, tier_accounts, deleted_entries, import_sessions, documents, document_categories
- Tests: 26/26 passes (iteration 71)

#### P1 - PCMN Consistency - Normalisation 8 chiffres (DONE)
- Nouvelle lib pcmn_utils.py: normalize_bank_pcmn() et pcmn_bank_match()
- Normalisation 6 chiffres (551618) -> 8 chiffres (55161800) partout
- Endpoint migration: POST /api/admin/migrate/normalize-bank-pcmn (idempotent)
- 3 cibles normalisees: coproprietes.bank_accounts, pcmn_accounts.number, journal_entries.lines
- _resolve_bank_account dans auto_entries.py utilise pcmn_bank_match pour matching fuzzy
- _generate_pcmn_number genere toujours 8 chiffres
- Tests: 26/26 passes (iteration 71)

#### P0 - Workflow suggestion/validation auto-lettrage (DONE)
- Backend: _try_auto_lettrage_vcs refactore pour SUGGESTIONS
- 3 nouveaux endpoints: validate-suggestion, validate-all-suggestions, reject-suggestion
- Frontend: Badges amber, boutons Valider/Rejeter, barre batch

#### Simplification dialog categorisation bancaire (DONE)
- Supprime le dropdown "Nature de depense"
- Champ PCMN AccountSearchSelect comme champ principal unique

#### Fix filtre date $lte dans TOUS les rapports (DONE)
- Fonction utilitaire _date_lte() : ajoute T23:59:59 aux dates YYYY-MM-DD
- Appliquee a toutes les requetes $lte dans reports.py

#### Fix Balance de Tiers - Proprietaires manquants (DONE)
- 3 sources de proprietaires ajoutees: lots.owner_ids, tier_accounts, mutations

#### Endpoints diagnostic (DONE)
- GET /api/reports/debug-owner-balance
- GET /api/reports/debug-bilan-exclusions

### Sessions precedentes
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal
- Amelioration lettrage bancaire, Isolation ACP, Chatbot, Layout, Spinner

## Backlog prioritise

### P0 - Scripts production (user verification pending)
- cleanup_pcmn.py, fix_matexi_balance.py
- debug-owner-balance et debug-bilan-exclusions endpoints disponibles

### P0 - Ecart 12 EUR Boni (investigation en cours)
- Audit complet effectue: donnees coherentes dans notre systeme
- Ecart confirme vs Optipro (20456.26 vs 20444.26 en charges)
- Aucun doublon, aucune exclusion incorrecte detectee
- Hypothese: difference de traitement comptable entre systemes

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
