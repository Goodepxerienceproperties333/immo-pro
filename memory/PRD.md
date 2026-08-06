# NextGe Copro — PRD

## Problem Statement
Application de gestion de copropriété (droit belge, PCMN) avec RBAC strict,
cloisonnement Chinese Wall, imports CODA/Optipro, verrous fiscaux,
multi-syndic, portail propriétaire, communication email/postal, superadmin
billing.

## Société éditrice
- **Good Experience Properties SRL** — SRL de droit belge
- Siège : Rue René Sacré 33, 1367 Ramillies (Belgique)
- BCE : BE 1028.571.469 — IPI : 517595
- Gérant : Gérald Evrard
- Contact : welcome@goodexperienceproperties.be

## Environments
- Preview (dev)
- Production : https://immo-pcmn.emergent.host

## Recent changes (Feb 2026)
- **2026-02-06 (iter94c)** : Backend crash après import extrait bancaire.
  `POST /api/banking/statements/import-files` déclenche une extraction IA
  (Claude Sonnet 4.5) qui peut dépasser 60s sur des PDFs volumineux. Le
  `RequestTimeoutMiddleware` (60s) l'annulait -> `RuntimeError: No response
  returned` -> chaîne de middleware corrompue -> requêtes suivantes
  (login, auth/me) bloquées -> preview affiche écran blanc infini.
  **Fix** (`/app/backend/server.py`) : ajout de `/api/banking/statements/import-files`,
  `/api/banking/import-`, `/api/invoices/import`, `/api/invoices/analyze` à
  `_REQUEST_TIMEOUT_SKIP_PREFIXES`. Backend restart -> login=500ms,
  auth/me=133ms, banking/statements=213ms (vs 60s+ timeout avant).
- **2026-02-06 (iter94b)** : Parser Bilan PDF - fix phantom sub-accounts.
  Sur les montants belges avec espace en séparateur de milliers ("131 472,36"),
  le fragment "131" était mal identifié comme un code de sous-compte (regex
  `\d{2,10}`) créant une ligne fantôme dont le libellé = compte parent et
  montant = fragment suivant. Reproduit sur `Bilan comptable au 30_09_2025.pdf`
  (SA Finlead Properties) : écart 101,82 EUR entre Actif (228 796,36) et
  Passif (228 694,54) = différence des 2 fantômes (472,26 côté Actif, 370,44
  côté Passif). Correctif : rejeter les ancres dont x0 tombe dans la colonne
  montant (>= amount_xmin de la stratégie active). Fix appliqué sur **les
  deux parsers** : `pdf_utils.parse_balance_pdf` (legacy) et
  `optipro_parser._parse_page` (moteur wizard iter93bu). 15 tests parser
  passent. Résultat : Actif = Passif = 228 324,10 (bilan équilibré).
  ⚠️ Utilisateur doit **re-uploader le PDF** dans le wizard pour ré-appliquer
  le parseur - l'ancien parse cache est stocke en DB session.
- **2026-02-06 (iter94a)** : Chinese Wall STRICT (RGPD/P0). Frontend
  `InvoicesPage` (allocation frais privatifs) et `LotsPage` (dropdown
  assignation lot->owner) ne chargent plus les proprios via `syndic_wide=true`.
  Ils utilisent strictement `copropriete_id` = ACP courante. Toggle
  "Afficher tous les proprietaires du syndic" supprime (fuite RGPD).
  Backend `/api/owners?copropriete_id=X` deja OK (middleware bloque 403 sur
  ACPs hors scope + filtre `_allowed_owner_ids`). Cross-syndic isolation
  intacte via `syndic_id` sur owners + `syndic_query()`.
- **2026-02-02** : Politique de Confidentialité (RGPD) mise à jour avec
  données société réelles. `_DEFAULT_DOCS["privacy"]` v2, DB bumpée v1→v2.
  Section 1 (Responsable du traitement) et Section 12 (Contact) mises à jour.
- 2026-02 (précédent) : CGU v4, Mentions Légales avec données société
  (via admin UI).
- 2026-02 : Data Act Registry page ajoutée.
- 2026-02 : Superadmin Billing (annual_fee, pricing_mode).
- 2026-02 : Owner communication preferences (email/postal/registered + AG).
- 2026-02 : Communication module (variable insertion, HTML formatting,
  dynamic {balance}, postal PDF).
- 2026-02 : Phantom owner lockdown (Callens) via strict lots/mutations check.
- 2026-02 : RBAC `require_permission` sur routes Invoice.

## Backlog
- **P1 (récurrent, oublié 9×)** : TEUWEN legacy lot mapping dans
  `reports.py` et `pdf_decompte.py` — aligner `lot.owner_id` avec
  `distribution_keys`.
- P2 : UI Frontend Audit Bancaire Superadmin (`/api/admin/bank-audit/scan`).
- P2 : Outil admin bulk-recalcul factures legacy (fmtEUR NaN cleanup).
- P2 : Outil admin bulk-reset factures payées legacy → impayé.
- P3 : Certificat fiscal annuel.
- P4 : Emails automatiques de relance (APScheduler quotidien).
- P5 : Refactoring `auto_entries.py` (complexité cyclomatique élevée).

## Architecture
- Backend : FastAPI, MongoDB (Motor), Pydantic, RBAC via decorators
- Frontend : React, Tailwind, Shadcn UI
- Legal docs : DB-versioned (`legal_documents` collection, history
  dans `legal_document_history`).
