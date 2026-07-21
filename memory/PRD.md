# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features (session 2026-07-21)

### Fix Bilan + Compte de Resultat
- Filtre dedup AC via invoice.journal_entry_id + exclusion reversed/EXT-*
- 499 = 1170.16 Boni (Passif), Equilibre = True

### Fix Doublonnage Ecritures
- _hard_delete_auto_entries: suppression reelle + $or pour les 2 schemas de marquage
- Import wizard: ajout source_type/source_id/auto_generated sur JE
- Ventilation multi-comptes dans les JE d'achat

### Wizard Import: Extraits de Compte UNIQUEMENT
- commit-journals ne cree PLUS d'ecritures FI
- Cree uniquement bank_statements + bank_transactions
- Les ecritures FI sont generees a la comptabilisation par le syndic
- bank_transactions.auto_je_id = '' (pas de JE liee)

### Refonte Fournisseurs Wizard
- Preview endpoint, isolation par ligne, auto-creation scopee ACP

## Pending Issues
- P1: TEUWEN Owner mapping
- P2: 10EUR diff Optipro vs Depenses (BLOCKED)

## Upcoming Tasks
- P1: UI Modal suppression releve bancaire
- P2: UI Merge Owners
- P3: Export Journals CSV/PDF
- P5: Certificat fiscal annuel
- P6: Emails relance auto

## Refactoring
- reports.py, import_wizard.py, banking.py (>3000 lignes chacun)
