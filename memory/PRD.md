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
- Backend: `_try_auto_lettrage_vcs` refactore pour stocker des SUGGESTIONS au lieu de lettrer directement
- 3 nouveaux endpoints: validate-suggestion, validate-all-suggestions, reject-suggestion
- Frontend: Badges amber, boutons Valider/Rejeter, barre batch

#### Simplification dialog categorisation bancaire (DONE)
- Supprime le dropdown "Nature de depense" du dialog de categorisation
- Le champ "Compte PCMN" (AccountSearchSelect) est maintenant le champ principal et unique
- L'utilisateur tape directement le numero de compte (ex: 61 pour charges)
- Garde: Cle de repartition, Montant, Description
- Backend deja compatible (chemin account_number direct genere ecriture FI)
- Teste avec 21 EUR: ecriture AC correctement generee

#### Fix Balance de Tiers - Proprietaires manquants (DONE)
- Corrige balance_tiers_owners et _compute_balance_tiers_for_ui
- 3 sources de proprietaires ajoutees: lots.owner_ids, tier_accounts, mutations

#### Endpoint diagnostic debug-owner-balance (DONE)
- GET /api/reports/debug-owner-balance?copropriete_id=X&search=matexi&account=41010986
- Diagnostic complet: tier_accounts, third_party_id, ecritures AN, solde recalcule

#### Script fix_matexi_balance.py (DONE)
- Diagnostic + correction automatique du proprietaire Matexi dans l'ACP Gaura
- Corrige tier_accounts et third_party_id sur les ecritures 41010986

#### Nettoyage PCMN & Natures de depenses (sessions precedentes)
- Garde-fou anti-doublons, scripts cleanup

### Sessions precedentes
- Amelioration interface lettrage bancaire (montants identiques, tri, badge)
- Bundle Import Dialog refonte
- Verrou fiscal ameliore
- Corrections journal_entry_id, Warning modal, Balance Tiers, MUT-F
- Fix doublons fournisseurs, Cascade deletion, Auto-unlink bank txns
- Isolation ACP, Fix chatbot, Layout grille, Spinner import

## Backlog prioritise

### P0 - Scripts cleanup (user verification pending en production)
- cleanup_pcmn.py, cleanup_expense_categories.py, audit_class6_entries.py
- fix_matexi_balance.py (a executer en production)

### P1 - Important
- PCMN Consistency: aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF
- P3: Reset bulk factures payees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)
