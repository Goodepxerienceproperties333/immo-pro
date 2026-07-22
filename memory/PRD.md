# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features

### Fix Bilan + Compte de Resultat
- Filtre dedup AC via invoice.journal_entry_id + exclusion reversed/EXT-*
- 499 = Provisions (cl.70) - (Charges (cl.6) - Produits financiers (cl.75))

### Fix Doublonnage Ecritures
- _hard_delete_auto_entries: suppression reelle + $or pour les 2 schemas de marquage
- Import wizard: ajout source_type/source_id/auto_generated sur JE

### Wizard Import: Extraits de Compte UNIQUEMENT
- commit-journals ne cree PLUS d'ecritures FI
- Cree uniquement bank_statements + bank_transactions

### Refonte Fournisseurs Wizard
- Preview endpoint, isolation par ligne, auto-creation scopee ACP

### Fix Owners importes invisibles (iter90kz - 2026-07-22)
- commit_lots: appelle assign_owner_accounts pour chaque owner unique
- create_copropriete: meme logique pour les lots crees inline
- import_finalizer.py: mode defensif via lots de l'ACP (fallback)

### Validation anticipee owners (iter90kz - 2026-07-22)
- commit_owners: appelle assign_owner_accounts IMMEDIATEMENT a l'etape 1
- _allowed_owner_ids: 4eme source via import_session_id
- Backfill TEUWEN + 10 orphelins Acacia

### Lien ACP complet + Mode Promoteur (iter90kz - 2026-07-22)
- Backend CoproprieteInput: ajout owner_ids_to_link + promoter_owner_id
- create_copropriete: rattache TOUS les owners transmis (pas seulement ceux des lots)
- Mode promoteur: si active, tous les lots sont affectes au promoteur avec start_date=fy_start
  et ownership_history type=promoteur_initial
- Frontend Step 3: UI "S'agit-il d'un promoteur?" avec selecteur owner
- handleSave: envoie owner_ids_to_link=owners.map(id) + promoter_owner_id
- Backfill: 10 orphelins Acacia rattaches (ABED, ALBRECQ, DEGRANDE, DEWINTER, FROMENT,
  HACKEN, JAQUEMIN, LAHAYE, Marexi, MATTOT) - tous avec comptes tiers 4100/4101

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
