# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email), SMTP (One2Net)

## Architecture Multi-Syndic (syndic_id)
- Chaque document metier porte un champ `syndic_id` identifiant le cabinet syndic proprietaire
- `syndic_scope.py` : helpers centraux (resolve_syndic_id, syndic_query, inject_syndic)
- Middleware `server.py` : extrait le syndic_id du token JWT et le place dans `request.state.syndic_id`
- Chinese Wall : double protection (middleware copropriete_id check + route-level syndic_query filter)
- Convention : superadmin voit tout (syndic_id=None), syndic voit ses donnees, gestionnaire herite du parent_syndic_id

## Fonctionnalites implementees

### Session courante (Fevrier 2026)

#### P0 - Chinese Wall attach-to-copro (DONE - Tests 5/5 iter 75)
- Fix verifie sur /api/owners/{id}/attach-to-copro (properties.py L953-978)
- Owner doit deja appartenir au meme syndic (via copropriete_ids OR lots OR owner.syndic_id)
- Sinon 403 "Acces refuse a ce proprietaire (chinese wall)"
- Cas legitime same-syndic reste 200 (idempotent)
- Superadmin conserve son bypass
- Test file: /app/backend/tests/test_attach_owner_chinese_wall.py

#### P0 - Securite Multi-Syndic syndic_id (DONE - Tests 11/11)
- Infrastructure: syndic_scope.py, middleware server.py (lines 356-363)
- Verrouille: coproprietes.py, properties.py, suppliers.py, invoices.py, accounting.py, banking.py, fund_calls.py, import_wizard.py, reports.py, documents.py, fiscal.py, expense_categories.py
- Fix: create_owner (request: Request), create_transaction (request: Request), create_invoice (request: Request)
- Fix: find_duplicate_owner accepts request=None param
- DB nettoyee: 96 test users + 183 coproprietes orphelines supprimees
- Tests: iteration_74 - 11/11 backend tests PASS

#### Gestion des Collaborateurs dans Mon Bureau (DONE)
- Section "Collaborateurs" dans MonBureauPage avec composant TeamSection.js
- Deux modes de creation : directe (nom + email + mdp temporaire) OU invitation par email
- Selection du profil (role_template) via dropdown
- Selection des ACPs accessibles par checkbox (avec "Toutes les coproprietes")
- CRUD complet : ajouter, modifier, supprimer, renvoyer invitation
- Badges : profil, statut "En attente", ACPs assignees
- Tests: 26/26 backend + 100% frontend (iteration 73)

#### P0 - Fix Purge Syndic - Suppression owners complete (DONE)
- 5 sources de collecte owner_ids + suppression users role=owner + owner_access_audit
- Tests: 18/18 (iter 72) + 26/26 (iter 71)

#### P1 - PCMN Consistency - Normalisation 8 chiffres (DONE)
- pcmn_utils.py + endpoint migration normalize-bank-pcmn
- Tests: 26/26 (iter 71)

#### P0 - Fix Email SMTP "Boite non autorisee" (DONE)
- smtp_username automatiquement ajoute aux boites autorisees quand provider=smtp
- communication.py supporte maintenant l'envoi SMTP complet

#### Purge DB Preview (DONE)
- Collections videes sauf users et pcmn_accounts

### Sessions precedentes
- Auto-lettrage, Balance de Tiers fix, Categorisation simplifiee, Filtre date $lte fix
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal, Chatbot, Layout, Spinner

## Backlog prioritise

### P1 - Important
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys (RECURRENT)

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)

## Refactoring
- import_wizard.py (>3500 lignes) a decouper
- reports.py (logique PCMN complexe) a simplifier
