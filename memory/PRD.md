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
- Ventilation multi-comptes dans les JE d'achat

### Wizard Import: Extraits de Compte UNIQUEMENT
- commit-journals ne cree PLUS d'ecritures FI
- Cree uniquement bank_statements + bank_transactions
- Les ecritures FI sont generees a la comptabilisation par le syndic

### Refonte Fournisseurs Wizard
- Preview endpoint, isolation par ligne, auto-creation scopee ACP

### Fix Owners importes invisibles (iter90kz - 2026-07-22)
- commit_lots (import_wizard.py): appelle assign_owner_accounts pour chaque owner unique apres insertion des lots -> cree comptes tiers 4100/4101 + ajoute ACP a copropriete_ids
- create_copropriete (coproprietes.py): meme logique pour les lots crees inline
- import_finalizer.py: mode defensif — decouvre les owners via les lots de l'ACP (fallback)

### Fix OwnerPicker mutation trop restrictif (iter90kz - 2026-07-22)
- _allowed_owner_ids (properties.py): ajout 4eme source via import_session_id -> import_sessions.copropriete_id pour decouvrir les owners importes sans lots
- Meme logique ajoutee pour le path copropriete_id specifique (owner_ids_imported)
- TEUWEN Gael visible et selectionnable dans le dialog de mutation

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
