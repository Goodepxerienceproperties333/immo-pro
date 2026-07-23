# NextGe Copro - PRD

## Enonce du probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack technique
- Backend: FastAPI, Async MongoDB (Motor), APScheduler
- Frontend: React, Tailwind CSS, Shadcn UI
- Auth: Cookie httpOnly
- 3rd Party: Emergent LLM Key (Claude Sonnet text), Microsoft Graph (Email)

## Fonctionnalites implementees
- Authentification (superadmin, syndic, owner)
- Gestion des coproprietes, lots, proprietaires, fournisseurs
- Import CODA / Optipro (CSV/PDF) via wizard multi-etapes
- Comptabilite PCMN stricte (journaux AC, FI, OD, AN)
- Balance de Tiers, Bilan, Grand Livre, Decompte de mutation
- Lettrage bancaire (simple, multi-factures, batch)
- Categorisation bancaire par nature de depense
- Verrous fiscaux, periodes ouvertes/fermees
- PDF: Balance Tiers, Decompte mutation, Situation de compte
- Portail proprietaire
- Modal confirmation suppression extrait bancaire avec apercu impacts
- Mutation: line_description personnalisee (vendeur voit acheteur, acheteur voit vendeur)
- Safeguard MUT-F : verifie distribution avant creation (evite doublons)
- Script reversal Degrande Q4 MUT-F (3 ecritures)

## Corrections appliquees (Juillet 2026)
### Corrections comptables
- _resolve_bank_account: match direct pcmn_number == account_number (extraits sans IBAN)
- Import CSV journaux: skip remap 55x/58 (CSV Optipro authoritatif)
- Import factures: champ `lines` pour generate_purchase_entry (fix structurel)
- set_private_fee_allocations: appel generate_purchase_entry (fix orphelines JE)
- Grand Livre: exclusion reversals (deja en place)

### Balance de Tiers - Mutations
- line_description sur chaque ligne de mutation OD (vendeur/acheteur)
- PDF _humanize_label: skip prefixe "Operation:" pour mutations, limite 80->120 chars

### Safeguard MUT-F
- Bloc creation MUT-F (properties.py ~L2548) : avant creation, verifie si
  la distribution du fund_call a deja owner_id == new_owner_id pour le lot.
  Si oui, skip pour eviter les doublons.
- Script reversal: /app/backend/scripts/reverse_degrande_q4_mut_f.py
  Contre-passe 3 MUT-F Degrande Q4 via reverse_journal_entry (audit trail).

### UX
- Modal confirmation suppression extrait bancaire (preview impacts)

## Backlog
- P1: PCMN Consistency - aligner convention 8 chiffres
- P1: TEUWEN lot mapping - fix logique owner_id + distribution_keys
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails de relance automatiques (APScheduler quotidien)

## Architecture
/app/
├── backend/
│   ├── auto_entries.py
│   ├── journal_reversals.py (reverse_journal_entry, reverse_auto_entries)
│   ├── pdf_situation_compte.py
│   ├── scripts/
│   │   └── reverse_degrande_q4_mut_f.py
│   ├── routes/
│   │   ├── reports.py
│   │   ├── banking.py (delete-preview endpoint)
│   │   ├── invoices.py (generate_purchase_entry on allocation update)
│   │   ├── import_wizard.py (skip remap 55x/58, _build_gpe_lines)
│   │   ├── properties.py (MUT-F safeguard, line_description)
│   │   └── suppliers.py
├── frontend/
│   ├── src/pages/
│   │   ├── BankingPage.js (modal suppression)
│   │   ├── BalanceTiersPage.js
│   ├── src/components/balance-tiers/
│   │   ├── TiersDetailDialog.js
