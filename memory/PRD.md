# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email)

## Architecture
```
/app/
├── backend/
│   ├── routes/ (import_wizard, invoices, properties, reports, fiscal, etc.)
│   ├── import_wizard/ (pdf_invoices_bundle, pdf_supplier_invoice_list, pdf_utils)
│   ├── scripts/ (reverse_degrande_q4_mut_f, merge_good_experience)
│   ├── fiscal_lock.py (verrou fiscal centralise)
│   └── journal_reversals.py
├── frontend/
│   ├── src/pages/ (InvoicesPage, BankingPage, CoproprietesPage, etc.)
│   └── src/components/ (BundleImportDialog, SupplierSearchSelect, AccountSearchSelect)
```

## Fonctionnalites implementees

### Iteration courante (Juillet 2026)
- **Bundle Import Dialog** : Refonte complete du flux d'import de regroupement PDF
  - Nouveau formulaire complet de creation (formulaire a gauche, apercu PDF a droite)
  - Dropdown fournisseurs existants avec recherche
  - Endpoint preview bloc PDF (GET /api/invoices/bundle-preview-block)
  - Commit partiel (cleanup=false pour creation par bloc)
- **Verrou fiscal ameliore** : Fallback datetime + diagnostics
  - Fallback en comparaison datetime si la comparaison string echoue
  - Message d'erreur enrichi avec la liste des exercices existants
- **Fix expense_categories** : KeyError 'name' sur les comptes PCMN sans nom

### Iterations precedentes
- Corrections journal_entry_id (generate_purchase_entry)
- Warning modal suppression releves bancaires
- Buyer/Seller names dans Balance Tiers UI et PDF
- MUT-F safeguard (pas de doublon si owner_id == old_owner_id)
- Fix doublons fournisseurs dans import_wizard (check DB, pas cache)
- Auxiliary_code search + bouton "Creer les proprietaires manquants"
- Fix import CSV quand periodes fermees
- Cascade deletion MUT-* journal entries
- Auto-unlink bank transactions lors d'un reverse_journal_entry
- Bouton "Auto-lettrage VCS" dans BankingPage

## Backlog prioritise

### P0 (Bloquant)
- ~~Bundle Import PDF : parser AI fallback~~ → Refonte complete du dialog faite
- Scripts cleanup donnees : user verification pending (scripts dans /app/backend/scripts/)

### P1 (Important)
- PCMN Consistency : aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping : logique lot.owner_id + distribution_keys pour mutations historiques

### P2 (Amelioration)
- Export Journaux CSV/PDF avec selecteur de dates

### P3-P5 (Futur)
- P3: Reset bulk factures payees → impayees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)

## Notes techniques
- **Preview DB vide** : les scripts de correction doivent etre executes sur l'environnement production
- **Route ordering** : Les endpoints statiques (bundle-preview-block, bundle-analyze) doivent etre definis AVANT /invoices/{invoice_id} dans FastAPI
