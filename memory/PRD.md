# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features

### Fix Bilan + Compte de Resultat
- Filtre dedup AC via invoice.journal_entry_id + exclusion reversed/EXT-*
- 499 = Provisions (cl.70) - (Charges (cl.6) - Produits financiers (cl.75))

### Fix Doublonnage Ecritures
- _hard_delete_auto_entries: suppression reelle + $or pour les 2 schemas de marquage

### Wizard Import: Extraits de Compte UNIQUEMENT
- commit-journals ne cree PLUS d'ecritures FI

### Refonte Fournisseurs Wizard
- Preview endpoint, isolation par ligne, auto-creation scopee ACP

### Fix Owners importes invisibles (iter90kz)
- commit_lots/commit_owners: appelle assign_owner_accounts immediatement
- import_finalizer.py: mode defensif via lots de l'ACP (fallback)
- _allowed_owner_ids: 4eme source via import_session_id

### Mode Promoteur (iter90kz - 2026-07-22)
- CoproprieteInput.promoter_owner_id persiste sur le document copropriete
- CoproprietesPage Step 2: UI "Promoteur immobilier?" avec select HTML natif dedup
- create_copropriete: assigne tous les lots au promoteur + ownership_history type=promoteur_initial
- commit_lots (import_wizard.py): support promoter_owner_id (param explicite OU fallback copropriete)
- ImportWizardPage: banner "Mode promoteur actif" si configure sur l'ACP
- Backfill: 10 orphelins Acacia rattaches + TEUWEN avec comptes tiers

## Pending Issues
- P1: TEUWEN Owner mapping dans PDF/Reports (reports.py, pdf_decompte.py)

## Upcoming Tasks
- P1: UI Modal suppression releve bancaire
- P2: Export Journals CSV/PDF
- P3: Admin bulk reset invoices
- P4: Certificat fiscal annuel
- P5: Emails relance auto (APScheduler)

## Refactoring
- reports.py, import_wizard.py, banking.py (>3000 lignes chacun)
