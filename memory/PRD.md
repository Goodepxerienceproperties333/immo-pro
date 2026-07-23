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
- Fonction utilitaire _date_lte() ajoutee : ajoute T23:59:59 aux dates YYYY-MM-DD
- Appliquee a TOUTES les requetes $lte dans reports.py (~30 occurrences)
- Corrige l'ecart de 12 EUR sur le Boni (3 x 4 EUR frais bancaires Oct/Nov/Dec exclus)
- Impacte: Bilan, Resultat d'exercice, Grand livre, Balance comptes, Balance tiers,
  Decompte annuel, Liste depenses, Journaux PDF, Situation proprietaire

#### Fix Balance de Tiers - Proprietaires manquants (DONE)
- 3 sources de proprietaires ajoutees: lots.owner_ids, tier_accounts, mutations

#### Endpoint diagnostic debug-owner-balance (DONE)
- GET /api/reports/debug-owner-balance

#### Script fix_matexi_balance.py (DONE)

### Sessions precedentes
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal
- Amelioration lettrage bancaire, Isolation ACP, Chatbot, Layout, Spinner

## Backlog prioritise

### P0 - Scripts production (user verification pending)
- cleanup_pcmn.py, fix_matexi_balance.py
- debug-owner-balance endpoint a tester

### P1 - Important
- PCMN Consistency: aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF
- P3: Reset bulk factures payees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)
