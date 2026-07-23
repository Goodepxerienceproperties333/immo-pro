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
- PDF: Balance Tiers, Decompte mutation
- Portail proprietaire
- Modal confirmation suppression extrait bancaire avec apercu impacts

## Corrections appliquees (Juillet 2026)
- Layout responsive fix 1920x1080
- Tabs restructures (FiscalYearPage, DocumentsPage)
- Balance Tiers: fusion colonnes Solde Reserve + Provision -> Solde Net a Regler (UI+PDF)
- Bilan equilibre: compte_499 = result_exercise
- Fournisseurs dupliques fusionnes (algorithme sous-ensemble)
- Orphelins LAHAYE nettoyes + _cancel_single patche
- _resolve_bank_account: match direct pcmn_number (Correction 1)
- Grand Livre: exclusion reversals (Correction 4 - deja en place)
- Import CSV journaux: skip remap 55x/58 (CSV authoritatif, Correction 2)
- Import factures: champ `lines` pour generate_purchase_entry (fix structurel)
- set_private_fee_allocations: appel generate_purchase_entry (fix orphelines)
- Modal confirmation suppression extrait bancaire (Backend preview + Frontend dialog)

## Backlog
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails de relance automatiques (APScheduler quotidien)

## Problemes connus non resolus
- TEUWEN lot mapping logic (combine owner_id + distribution_keys pour mutations historiques)
- PCMN Consistency: aligner convention 8 chiffres entre bank_accounts config, extraits et PCMN

## Architecture
/app/
├── backend/
│   ├── auto_entries.py (resolve bank account, generate_purchase_entry, generate_bank_entry)
│   ├── import_finalizer.py (finalize_je_doc, canonicalize accounts)
│   ├── routes/
│   │   ├── reports.py (Bilan, Grand Livre, Balance Tiers, Decompte)
│   │   ├── banking.py (Extraits, transactions, lettrage, categorisation, delete-preview)
│   │   ├── invoices.py (CRUD factures, allocations frais privatifs)
│   │   ├── import_wizard.py (Import CSV/CODA multi-etapes)
│   │   ├── properties.py (Coproprietes, lots, mutations)
│   │   └── suppliers.py (Fournisseurs, matching sous-ensemble)
├── frontend/
│   ├── src/pages/
│   │   ├── BankingPage.js (Extraits, transactions, modal suppression)
│   │   ├── BalanceTiersPage.js (Solde Net a Regler)
│   │   ├── FiscalYearPage.js (Tabs restructures)
