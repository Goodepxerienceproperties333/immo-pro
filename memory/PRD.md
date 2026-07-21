# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features (session 2026-07-21)

### Fix Bilan + Compte de Resultat (doublonnage charges)
- Filtre dedup AC via invoice.journal_entry_id dans bilan ET resultat
- Exclusion reversed/is_reversal/EXT-*/OD-REG-* dans le Resultat
- 499 = 1170.16 Boni (Passif), Charges = 5333.51, Equilibre = True

### Fix Doublonnage Ecritures sur Modification Facture
- _hard_delete_auto_entries: suppression REELLE (pas contre-passation) des anciennes AC
- Garantit qu'il n'y a JAMAIS plus d'1 ecriture AC par facture, meme apres N modifications

### Fix Import CSV Multi-Comptes
- Ventilation multi-comptes: le JE a maintenant N lignes de debit distinctes (1 par compte)
- Ex: 61300:558.99 + 6160:195.00 au lieu de 61300:753.99 lump sum

### Refonte Import Wizard Optipro
- Endpoint preview-invoices: tableau de controle (Fournisseur|TVA|Compte) AVANT commit
- Isolation stricte par ligne: chaque ligne resolve son fournisseur independamment
- Auto-creation fournisseurs manquants (scoped ACP, Chinese Wall strict)
- UI: tableau de controle dans le wizard

## Pending Issues
- P1: TEUWEN Owner mapping & distribution lines logic
- P2: 10EUR difference Optipro vs Liste depenses (BLOCKED)

## Upcoming Tasks
- P1: UI Modal for Statement Deletion warning
- P2: UI "Merge Owners" admin page
- P3: Export Journals CSV/PDF
- P5: Certificat fiscal annuel
- P6: Automated debt collection emails

## Refactoring Needed
- reports.py (>3800 lines), import_wizard.py (>3200 lines), banking.py (>3000 lines)
