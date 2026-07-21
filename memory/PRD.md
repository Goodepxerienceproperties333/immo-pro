# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features (this session - 2026-07-21)
- **Fix Compte 499 Bilan**: Ajout filtre dedup AC orphelines (ecritures non liees a une facture via journal_entry_id). Empeche le sur-comptage des charges et fournisseurs.
- **Fix Compte de Resultat**: Applique les memes filtres que le bilan (exclusion reversed, is_reversal, EXT-*, OD-REG-*, AN cloture, dedup FI, dedup AC orphelines).
- **Nettoyage DB**: Suppression de 2 ecritures AC orphelines (FA-260081, FA-V-260654) creees manuellement en doublon de l'import Optipro.
- **Resultats verifies**: Bilan 499=1170.16 Boni (Passif), Charges=5333.51, Produits=6503.67, Equilibre=True, Finlead=639.69.

## Pending Issues
- P1: TEUWEN Owner mapping & distribution lines logic
- P2: 10EUR difference Optipro import vs Liste des depenses (BLOCKED)

## Upcoming Tasks
- P1: UI Modal for Statement Deletion warning
- P2: UI "Merge Owners" admin page
- P3: Export Journals CSV/PDF with date selector
- P4: Admin bulk reset paid invoices to unpaid
- P5: Certificat fiscal annuel
- P6: Automated debt collection emails

## Refactoring Needed
- reports.py (>3800 lines) - split into submodules (bilan, resultat, expenses)
- banking.py (>3000 lines) - split into modules
- Extract shared AC dedup helper between bilan and resultat
