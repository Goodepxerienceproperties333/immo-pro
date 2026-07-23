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
│   ├── routes/ (import_wizard, invoices, properties, reports, fiscal, accounting, expense_categories)
│   ├── import_wizard/ (pdf_invoices_bundle, pdf_supplier_invoice_list, pdf_utils)
│   ├── scripts/ (cleanup_pcmn, cleanup_expense_categories, audit_class6_entries, reverse_degrande_q4_mut_f, merge_good_experience)
│   ├── fiscal_lock.py (verrou fiscal centralise avec fallback datetime)
│   └── journal_reversals.py
├── frontend/
│   ├── src/pages/ (InvoicesPage, BankingPage, CoproprietesPage, etc.)
│   └── src/components/ (BundleImportDialog, SupplierSearchSelect, AccountSearchSelect)
```

## Fonctionnalites implementees

### Iteration courante (Juillet 2026 - Session 2)
- **Nettoyage PCMN et natures de depenses**
  - Garde-fou anti-doublons sur creation PCMN comptes 6/7xxx (noms similaires → 409)
  - Garde-fou anti-doublons sur creation natures de depenses (noms similaires → 409)
  - Script `cleanup_pcmn.py` : fusion doublons classe 6 + correction 6140/6141
  - Script `cleanup_expense_categories.py` : fusion natures doublons
  - Script `audit_class6_entries.py` : detection ecritures en doublon sur comptes differents
- **Bundle Import Dialog** : Refonte complete du flux d'import de regroupement PDF
  - Nouveau formulaire complet de creation (formulaire a gauche, apercu PDF a droite)
  - Dropdown fournisseurs existants avec recherche
  - Endpoint preview bloc PDF (GET /api/invoices/bundle-preview-block)
  - Commit partiel (cleanup=false pour creation par bloc)
- **Verrou fiscal ameliore** : Fallback datetime + diagnostics enrichis
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
- Scripts cleanup donnees : user verification pending (scripts dans /app/backend/scripts/)
  - `cleanup_pcmn.py` (dry-run d'abord, puis --apply)
  - `cleanup_expense_categories.py` (dry-run d'abord, puis --apply)
  - `audit_class6_entries.py` (audit puis --delete)
  - `reverse_degrande_q4_mut_f.py`
  - `merge_good_experience.py`

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
- **Route ordering** : Les endpoints statiques doivent etre definis AVANT /invoices/{invoice_id}
- **Gardes-fous** : Normalisation unicode (accents), substring match, >70% word overlap
