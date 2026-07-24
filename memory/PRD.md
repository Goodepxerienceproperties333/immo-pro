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

#### Script fix_matexi_balance.py (DONE)

#### Purge complete dossier syndic (DONE)
- DELETE /api/admin/syndic/{user_id}/purge-data
- Supprime toutes les donnees de toutes les ACPs du syndic
- Confirmation par email a retaper
- Collections purgees: lots, owners, suppliers, invoices, fund_calls,
  mutations, journal_entries, bank_statements, bank_transactions,
  pcmn_accounts, expense_categories, distribution_keys, fiscal_years,
  coproprietes + team users (gestionnaires/owners)
- Frontend: Bouton flamme dans AdminUsersPage + dialog de confirmation
- Tests: 100% (6/6 backend + 5/5 frontend)

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
- PCMN Consistency: aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF
- P3: Reset bulk factures payees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)
