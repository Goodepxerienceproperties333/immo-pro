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
- **P1 (récurrent, oublié 8×)** : TEUWEN legacy lot mapping dans
  `reports.py` et `pdf_decompte.py` — aligner `lot.owner_id` avec
  `distribution_keys`.
- P2 : Outil admin bulk-reset factures payées legacy → impayé.
- P3 : Certificat fiscal annuel.
- P4 : Emails automatiques de relance (APScheduler quotidien).
- P5 : Refactoring `auto_entries.py` (complexité cyclomatique élevée).

## Architecture
- Backend : FastAPI, MongoDB (Motor), Pydantic, RBAC via decorators
- Frontend : React, Tailwind, Shadcn UI
- Legal docs : DB-versioned (`legal_documents` collection, history
  dans `legal_document_history`).
