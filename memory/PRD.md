# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur données comptables/financières.
Auth: JWT cookie + middleware global FastAPI.
Roles: `superadmin`, `syndic`, `gestionnaire`, `owner`.

## Security model (Iter9 + Iter10 + Iter11)
1. **Auth middleware**: toutes routes `/api/*` exigent un token valide (cookie ou Bearer), sauf `/auth/login`, `/auth/register`, `/auth/refresh`, `/auth/logout`.
2. **RBAC path-based**:
   - `/api/admin/*` + `/api/users` → admin (superadmin/syndic) seul, toutes méthodes
   - Pour `role=owner`: whitelist stricte — accès uniquement à `/api/owner/*`, `/api/auth/*`, `GET /api/coproprietes` (+ sous-paths), `GET /api/documents/{id}/download`. Tout autre 403.
   - Pour autres rôles: GET libres pour authenticated users, POST/PUT/PATCH/DELETE sur `/api/*` → manager+ (superadmin/syndic/gestionnaire).
   - `/api/auth/me` exempt RBAC (self-service)
3. **Chinese walls**: `copropriete_id` propagé automatiquement (frontend interceptor) et filtré côté backend.

## Implemented (Feb 2026)

### Core
- Auth JWT cookie avec middleware global de validation
- RBAC path-based (admin / manager / owner read-only)
- Dashboard global sans sidebar, sidebar ACP contextuelle
- Multi-coproprietes archivables, refs auto ACP-YYYYMM-NNN

### Portail proprietaire (Iter11)
- Login owner -> redirection auto vers `/portal`
- 6 endpoints `/api/owner/*` qui résolvent automatiquement l'owner connecté (email matching)
- Page OwnerPortalPage avec :
  - Carte identité (nom, contact, **VCS copiable**)
  - 4 stats (coproprietes, lots, total appelé, solde avec status)
  - Alerte appels en attente avec VCS copiable par ligne
  - Onglet **Mes coproprietes** : cartes ACP avec lots détaillés
  - Onglet **Appels de fonds** : tableau avec statut payé/à payer + VCS
  - Onglet **Charges** : factures avec quote-part calculée par ventilation
  - Onglet **Documents** : par catégorie, téléchargement
- Sélecteur d'ACP multi-ACP
- 100% read-only enforced par RBAC backend

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
- Lookup contrepartie

### Facturation
- Cles de repartition par ACP avec quotites
- Factures avec ventilation
- Appels de fonds avec distribution
- Balance de tiers

### Documents
- Upload PDF/images stockage disque
- **Auto-classification IA via Claude Sonnet 4.5**
- 10 categories par defaut seedees a chaque ACP
- Download/delete

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
- iter11: 30/30 owner portal + 84/87 regression (3 iter10 obsoletes by design)

## Backlog P1
- Decomptes annuels PDF reportlab complets
- Bilan/Compte de Resultats logique PCMN belge stricte
- Export Excel rapports + balance de tiers
- Rappels paiement automatises
- Gestion AG (ordre du jour, votes, PV, convocations)

## Backlog P2
- Auto-extraction IA factures fournisseurs (Claude)
- Bordereaux SEPA pain.001
- Module gros entretien fonds de reserve
- Portail proprietaire: ajouter PDF decompte annuel téléchargeable
- Notifications email (Resend) lors des nouveaux appels de fonds

## Files of reference
- `/app/backend/server.py`: auth + RBAC middleware (line 48-160)
- `/app/backend/routes/owner_portal.py`: 6 endpoints owner
- `/app/backend/routes/admin.py`: /api/admin/users CRUD
- `/app/backend/routes/coproprietes.py`: wizard + PCMN seed + cascade delete
- `/app/backend/routes/documents.py`: upload + Claude classification
- `/app/backend/routes/demo_seed.py`: bouton demo idempotent
- `/app/backend/routes/reports.py`: tous reports scopes par ACP
- `/app/frontend/src/pages/OwnerPortalPage.js`: portail propriétaire
- `/app/frontend/src/pages/CoproprietesPage.js`: wizard 3 etapes
- `/app/frontend/src/App.js`: redirection auto /portal pour role=owner
- `/app/frontend/src/contexts/AuthContext.js`: isOwner flag
- `/app/frontend/src/lib/api.js`: chinese-wall interceptor (exclut globales)
