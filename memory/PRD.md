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

## Corrections appliquees (Juillet 2026 - Session actuelle)
### Corrections comptables
- _resolve_bank_account: match direct pcmn_number == account_number (extraits sans IBAN)
- Import CSV journaux: skip remap 55x/58 (CSV Optipro authoritatif)
- Import factures: champ `lines` pour generate_purchase_entry (fix structurel)
- set_private_fee_allocations: appel generate_purchase_entry (fix orphelines JE)
- Grand Livre: exclusion reversals (deja en place)

### Balance de Tiers - Mutations
- line_description sur chaque ligne de mutation OD:
  - Ligne vendeur: "Mutation lot X - {label}: vente a {ACHETEUR}"
  - Ligne acheteur: "Mutation lot X - {label}: achat de {VENDEUR}"
- regenerate_orphan_mutation_od: meme format line_description
- PDF _humanize_label: skip prefixe "Operation:" pour mutations, limite 80->120 chars
- situation_compte_owner: line_description prioritaire (deja en place)

### UX
- Modal confirmation suppression extrait bancaire (preview impacts: lettrages, factures, FI)
- DELETE /api/banking/statements/{id}/delete-preview endpoint

### Corrections precedentes (sessions anterieures)
- Layout responsive fix 1920x1080
- Tabs restructures (FiscalYearPage, DocumentsPage)
- Balance Tiers: fusion colonnes Solde Reserve + Provision -> Solde Net a Regler (UI+PDF)
- Bilan equilibre: compte_499 = result_exercise
- Fournisseurs dupliques fusionnes (algorithme sous-ensemble)
- Orphelins LAHAYE nettoyes + _cancel_single patche

## Backlog
- P1: PCMN Consistency - aligner convention 8 chiffres entre bank_accounts config, extraits et PCMN
- P1: TEUWEN lot mapping - fix logique owner_id + distribution_keys pour decomptes mutations historiques
- P2: Export Journaux CSV/PDF avec selecteur de dates
- P3: Outil admin reset bulk factures payees -> impayees
- P4: Certificat fiscal annuel
- P5: Emails de relance automatiques (APScheduler quotidien)

## Architecture
/app/
├── backend/
│   ├── auto_entries.py (resolve bank account, generate_purchase_entry, generate_bank_entry)
│   ├── import_finalizer.py (finalize_je_doc, canonicalize accounts)
│   ├── pdf_situation_compte.py (_humanize_label - skip Operation prefix pour mutations)
│   ├── routes/
│   │   ├── reports.py (Bilan, Grand Livre, Balance Tiers, Decompte, Situation Compte)
│   │   ├── banking.py (Extraits, transactions, lettrage, delete-preview)
│   │   ├── invoices.py (CRUD factures, allocations frais privatifs + regenerate JE)
│   │   ├── import_wizard.py (Import CSV/CODA, _build_gpe_lines, skip remap 55x/58)
│   │   ├── properties.py (_build_entry avec line_description, mutations)
│   │   └── suppliers.py (Fournisseurs, matching sous-ensemble)
├── frontend/
│   ├── src/pages/
│   │   ├── BankingPage.js (Extraits, modal suppression)
│   │   ├── BalanceTiersPage.js (Solde Net a Regler)
│   ├── src/components/balance-tiers/
│   │   ├── TiersDetailDialog.js (Affiche description mutations avec line_description)
