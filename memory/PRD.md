# NextGe Copro - PRD

## Enonce du probleme
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Stack technique
- Backend: FastAPI, Async MongoDB (Motor), APScheduler
- Frontend: React, Tailwind CSS, Shadcn UI
- Auth: Cookie httpOnly
- 3rd Party: Emergent LLM Key (Claude Sonnet text), Microsoft Graph (Email)

## Corrections session actuelle (Juillet 2026)
### Comptabilite
- _resolve_bank_account: match direct pcmn_number (extraits sans IBAN)
- Import CSV journaux: skip remap 55x/58 (CSV Optipro authoritatif)
- Import factures: champ `lines` pour generate_purchase_entry
- set_private_fee_allocations: appel generate_purchase_entry

### Mutations
- line_description personnalisee (vendeur voit acheteur, acheteur voit vendeur)
- PDF _humanize_label: skip prefixe "Operation:" pour mutations, limite 120 chars
- Safeguard MUT-F: verifie distribution avant creation
- Script reversal Degrande Q4 MUT-F

### Fournisseurs
- Fix doublon: import_wizard fallback find_duplicate_supplier sur DB
- Script fusion: merge_good_experience.py

### Proprietaires / Lots (NEW)
- Recherche dropdown: ajout auxiliary_code dans le filtre
- Bouton "Creer les proprietaires manquants": auto-cree les owners depuis
  les donnees "Non rattache" des lots (auxiliary_code + name) via POST /owners
  avec reuse_on_duplicate=true
- Fix root cause: les owners non importes n'apparaissaient pas dans le dropdown
  meme si leur nom etait parse dans les donnees du lot

### UX
- Modal confirmation suppression extrait bancaire (preview impacts)

## Backlog
- P1: PCMN Consistency - aligner convention 8 chiffres
- P1: TEUWEN lot mapping
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques

## Scripts
- /app/backend/scripts/reverse_degrande_q4_mut_f.py
- /app/backend/scripts/merge_good_experience.py (--apply)
