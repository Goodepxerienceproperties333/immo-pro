# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur données comptables/financières.
Auth: JWT cookie + middleware global FastAPI.
Roles: `superadmin`, `syndic`, `gestionnaire`, `owner`.

## Security layers (Iter9 + Iter10)
1. **Auth middleware**: toutes routes `/api/*` exigent un token valide (cookie ou Bearer), sauf `/auth/login`, `/auth/register`, `/auth/refresh`, `/auth/logout`.
2. **RBAC path-based**:
   - `/api/admin/*` + `/api/users` → admin (superadmin/syndic) seul, toutes méthodes
   - Toutes autres `/api/*` POST/PUT/PATCH/DELETE → manager+ (superadmin/syndic/gestionnaire)
   - GET libres pour tout user authentifié
   - `/api/auth/me` exempt (self-service)
3. **Chinese walls**: `copropriete_id` propagé automatiquement (frontend interceptor) et filtré côté backend.

## Implemented (Feb 2026)

### Core
- Auth JWT cookie avec middleware global de validation
- RBAC path-based (admin / manager / read-only)
- Dashboard global sans sidebar, sidebar ACP contextuelle
- Multi-coproprietes archivables, refs auto ACP-YYYYMM-NNN

### Comptabilité (PCMN par ACP)
- PCMN scope par ACP (95 comptes belges seedes a la creation)
- 2 comptes actifs par defaut: 614000 Honoraires syndic + 615000 Frais de gestion
- Toggle active/inactive par compte
- Banques auto-generent PCMN 550xxx (epargne) / 551xxx (vue)
- Exercices fiscaux avec cloture + a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AC/AN), Grand Livre, Balance, Bilan, Resultat

### Banque
- CODA import, saisie inline, edition + suppression transactions
- VCS mod-97, auto-lettrage
- Lookup contrepartie (proprietaires globaux + factures locales)

### Facturation
- Cles de repartition par ACP avec quotites
- Factures avec ventilation par cle de repartition
- Appels de fonds avec distribution automatique
- Balance de tiers debiteurs/crediteurs

### Documents
- Upload PDF/images avec stockage disque
- **Auto-classification IA via Claude Sonnet 4.5**
- 10 categories par defaut seedees a chaque ACP
- Download/delete

### UX
- Wizard ACP 3 etapes (Identite -> Lots+proprietaires -> Options)
- Autocomplete proprietaires par nom/email/VCS dans lots et wizard
- Bouton "Generer ACP demo" (admin only, idempotent)

### Deploiement
- Scaleway docker-compose + nginx + webhook GitHub
- GDPR compliant (EU hosting Paris)

## Test coverage
- iter7: 16/16 chinese walls + 139/140 regression
- iter8: 18/18 features + 157/158 regression
- iter9: 43/43 auth middleware + regression
- iter10: 28/28 RBAC + 59/59 regression

## Backlog P0
- Portail proprietaire restreint (vue par ACP: balances, documents, appels)

## Backlog P1
- Decomptes annuels PDF reportlab complets
- Bilan/Compte de Resultats logique PCMN belge stricte
- Export Excel rapports + balance de tiers
- Rappels paiement automatises
- Gestion AG (ordre du jour, votes, PV, convocations)

## Backlog P2
- Auto-extraction IA factures fournisseurs (Claude) -> pre-remplir formulaire
- Bordereaux SEPA pain.001
- Module gros entretien fonds de reserve

## Files of reference
- `/app/backend/server.py`: auth_middleware + RBAC (line 48-145)
- `/app/backend/routes/admin.py`: /api/admin/users CRUD (admin only)
- `/app/backend/routes/coproprietes.py`: wizard + PCMN seed + cascade delete
- `/app/backend/routes/accounting.py`: PCMN CRUD scope par ACP
- `/app/backend/routes/documents.py`: upload + Claude classification
- `/app/backend/routes/demo_seed.py`: bouton demo idempotent
- `/app/backend/routes/reports.py`: tous reports scopes par ACP
- `/app/frontend/src/pages/CoproprietesPage.js`: wizard 3 etapes
- `/app/frontend/src/pages/LotsPage.js`: autocomplete proprietaires
- `/app/frontend/src/lib/api.js`: chinese-wall interceptor (exclut routes globales)
