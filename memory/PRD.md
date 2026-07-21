# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features (session 2026-07-21)

### Fix Bilan + Compte de Resultat (doublonnage charges)
- Suppression ecritures AC orphelines (FA-260081, FA-V-260654)
- Filtre dedup AC via invoice.journal_entry_id dans bilan ET resultat
- Exclusion reversed/is_reversal/EXT-*/OD-REG-* dans le Resultat
- 499 = 1170.16 Boni (Passif), Charges = 5333.51, Equilibre = True

### Refonte Import Wizard Optipro
- Endpoint preview-invoices : tableau de controle (Fournisseur|TVA|Compte) AVANT commit
- Isolation stricte par ligne : chaque ligne resolve son fournisseur independamment
- Auto-creation fournisseurs manquants (scoped ACP, Chinese Wall strict)
- Matching : aux_code > nom normalise > auto-create
- Purge fournisseurs Auto 3 Maria effectuee
- UI : tableau de controle affiche dans le wizard avec bouton Annuler

## Pending Issues
- P1: TEUWEN Owner mapping & distribution lines logic
- P2: 10EUR difference Optipro vs Liste depenses (BLOCKED)

## Upcoming Tasks
- P1: UI Modal for Statement Deletion warning
- P2: UI "Merge Owners" admin page
- P3: Export Journals CSV/PDF
- P4: Admin bulk reset paid invoices to unpaid
- P5: Certificat fiscal annuel
- P6: Automated debt collection emails

## Refactoring Needed
- reports.py (>3800 lines) - split bilan/resultat/expenses
- import_wizard.py (>3200 lines) - split preview/commit/matching
- banking.py (>3000 lines)
