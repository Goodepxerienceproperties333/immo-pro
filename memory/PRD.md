# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email), SMTP (One2Net)

## Architecture Multi-Tenant
- **Isolation**: `syndic_id` sur toutes les collections operationnelles
- **Middleware**: `request.state.syndic_id` calcule dans server.py
- **Helpers**: `syndic_scope.py` (resolve_syndic_id, syndic_query, inject_syndic)
- **Deduplication**: Owners et Suppliers scopes au niveau syndic (canonical_email, canonical_phone, BCE)

## Fonctionnalites implementees

### Session courante (Juillet 2026 - Fork actuel)

#### Bug Fix: Import Wizard commit-invoices crash (DONE - 24/07/2026)
- Cause: `_ensure_pcmn_accounts` appelait `inject_syndic(_pcmn, request)` sans avoir `request` dans ses parametres (artefact du refactoring sed syndic_id)
- Fix: ajout `request=None` comme parametre + mise a jour des 4 appelants (commit_invoices, commit_journals, commit_opening_balance, commit_od_entries)
- Verification AST: aucune autre fonction helper avec `request` manquant

#### Gestion Collaborateurs dans Mon Bureau (DONE - 24/07/2026)
- Section "Mon equipe — Collaborateurs" integree dans la page Mon Bureau (/mon-bureau)
- Composant TeamSection.js reutilisant /api/team/members (GET/POST/PUT/DELETE)
- Creation collaborateur avec nom, email, mot de passe, profil/role template, ACPs assignees, permissions
- Table collapsible avec statut (Actif/En attente), actions (editer, supprimer, renvoyer invitation)
- Bug fix: team.py user.get('id') -> user.get('_id') pour parent_syndic_id
- Tests: 100% (10/10 backend + frontend complet, iteration_74)

#### Step E - UX Deduplication Fournisseurs BCE (DONE - 24/07/2026)
- SuppliersPage.js: check BCE debounced (300ms) via POST /api/suppliers/check-duplicate
- Carte inline jaune d'alerte si doublon BCE detecte avec bouton "Utiliser ce fournisseur"

#### Step F - Cascade Deletion Coproprietes (DONE - 24/07/2026)
- coproprietes.py delete_copropriete: $pull copro_id de owners.copropriete_ids
- $unset tier_accounts.{copro_id} des owners
- Suppression fournisseurs ACP-scoped (copropriete_id == copro_id)
- Marquage orphelins (copropriete_ids vide -> is_orphan: true)

#### Bug Fixes Critiques (DONE - 24/07/2026)
- properties.py create_owner: ajout parametre `request: Request` manquant
- properties.py find_duplicate_owner: remplacement syndic_query(request) par syndic_id_filter param
- properties.py mutate_lot: ajout parametre `request: Request` manquant
- suppliers.py: imports syndic_query manquants dans plusieurs fonctions
- import_wizard.py _ensure_pcmn_accounts: ajout request param (4 appelants)

### Session precedente (Juillet 2026)

#### P0 - Fix Purge Syndic (DONE)
- 5 sources de collecte owner_ids, suppression users role=owner, owner_access_audit, collections additionnelles

#### P1 - PCMN Consistency 8 chiffres (DONE)
- pcmn_utils.py, migration endpoint, normalisation 6->8 digits

#### P0 - Fix Email SMTP (DONE)
- smtp_username ajoute dynamiquement aux boites autorisees

#### Securite Multi-Syndic syndic_id (DONE - Massive refactoring)
- Infrastructure syndic_scope.py + middleware server.py
- Verrouillage de TOUTES les routes operationnelles
- Purge DB preview pour appliquer le lock

### Sessions precedentes historiques
- Auto-lettrage, Balance de Tiers fix, Categorisation simplifiee, Filtre date $lte fix
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal
- Amelioration lettrage bancaire, Isolation ACP, Chatbot, Layout, Spinner

## Backlog prioritise

### P1 - Important
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys dans reports.py et pdf_decompte.py (RECURRENT)
- Import Wizard: audit complet des inject_syndic dans toutes les fonctions commit_* (risque residuel du refactoring sed)

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)

## Refactoring
- import_wizard.py (>3400 lignes) a decouper
- reports.py (logique PCMN complexe) a simplifier
