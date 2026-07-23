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

#### Fix Balance de Tiers - Proprietaires manquants (DONE)
- Corrige `balance_tiers_owners` et `_compute_balance_tiers_for_ui` dans reports.py
- Ajout de 3 sources de proprietaires manquantes :
  1. `lots.owner_ids` (tableau pluriel, co-proprietaires)
  2. `owners.tier_accounts.{copropriete_id}` (proprietaires avec comptes tiers configures sans lots, ex: Matexi)
  3. `mutations` (anciens proprietaires via from_owner_id/to_owner_id)
- Corrige aussi `current_owner_ids` pour inclure owner_ids pluriel

#### Nettoyage PCMN & Natures de depenses
- Garde-fou anti-doublons creation PCMN comptes 6/7xxx
- Scripts cleanup: cleanup_pcmn.py, cleanup_expense_categories.py, audit_class6_entries.py

#### Amelioration interface lettrage bancaire
- Bouton "Montants identiques", tri, badge "= MONTANT"

#### Bundle Import Dialog
- Refonte complete du flux d'import de regroupement PDF

#### Verrou fiscal ameliore
- Fallback datetime + diagnostics enrichis

### Sessions precedentes
- Corrections journal_entry_id, Warning modal suppression releves
- Buyer/Seller names Balance Tiers, MUT-F safeguard
- Fix doublons fournisseurs, bouton "Creer proprietaires manquants"
- Cascade deletion MUT-*, Auto-unlink bank txns
- Isolation ACP, Fix chatbot, Layout grille, Spinner import

## Backlog prioritise

### P0 - Scripts cleanup (user verification pending)
- cleanup_pcmn.py, cleanup_expense_categories.py, audit_class6_entries.py

### P1 - Important
- PCMN Consistency : aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping : logique lot.owner_id + distribution_keys

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF
- P3: Reset bulk factures payees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)
