# NextGe Copro - PRD

## Enonce du probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack technique
- Backend: FastAPI, Async MongoDB (Motor), APScheduler
- Frontend: React, Tailwind CSS, Shadcn UI
- Auth: Cookie httpOnly
- 3rd Party: Emergent LLM Key (Claude Sonnet text), Microsoft Graph (Email)

## Corrections appliquees (Juillet 2026)
### Corrections comptables
- _resolve_bank_account: match direct pcmn_number (extraits sans IBAN)
- Import CSV journaux: skip remap 55x/58 (CSV Optipro authoritatif)
- Import factures: champ `lines` pour generate_purchase_entry (fix structurel)
- set_private_fee_allocations: appel generate_purchase_entry (fix orphelines JE)

### Mutations
- line_description personnalisee (vendeur voit acheteur, acheteur voit vendeur)
- PDF _humanize_label: skip prefixe "Operation:" pour mutations, limite 120 chars
- Safeguard MUT-F: verifie distribution avant creation (evite doublons)
- Script reversal Degrande Q4 MUT-F

### Fix doublon fournisseur
- Root cause: import_wizard commit-invoices cherchait fournisseurs uniquement dans
  le cache session (sup_by_aux), pas dans la DB. Ajout fallback find_duplicate_supplier
  sur la DB avant auto-creation.
- Script fusion: /app/backend/scripts/merge_good_experience.py

### UX
- Modal confirmation suppression extrait bancaire (preview impacts)

## Backlog
- P1: PCMN Consistency - aligner convention 8 chiffres
- P1: TEUWEN lot mapping - fix logique owner_id + distribution_keys
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails de relance automatiques (APScheduler quotidien)

## Scripts utilitaires
- /app/backend/scripts/reverse_degrande_q4_mut_f.py : contre-passe 3 MUT-F
- /app/backend/scripts/merge_good_experience.py : fusionne doublons Good Experience
  Usage: python3 scripts/merge_good_experience.py (dry run)
         python3 scripts/merge_good_experience.py --apply (execution)
