# NextGe Copro - PRD

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN).

## Completed Features

### Fix Bilan + Compte de Resultat
- 499 = Provisions (cl.70) - (Charges (cl.6) - Produits financiers (cl.75))

### Fix Doublonnage Ecritures
- _hard_delete_auto_entries: suppression reelle + $or

### Wizard Import: Extraits de Compte UNIQUEMENT
- commit-journals ne cree PLUS d'ecritures FI

### Refonte Fournisseurs Wizard
- Preview endpoint, isolation par ligne, auto-creation scopee ACP

### Fix Owners importes invisibles (iter90kz)
- commit_lots/commit_owners: appelle assign_owner_accounts immediatement
- import_finalizer.py: mode defensif via lots de l'ACP (fallback)
- _allowed_owner_ids: 4eme source via import_session_id

### Mode Promoteur (iter90kz)
- CoproprieteInput.promoter_owner_id persiste sur le document copropriete
- CoproprietesPage Step 2: UI "Promoteur immobilier?" avec select HTML natif dedup
- create_copropriete: assigne tous lots au promoteur + ownership_history promoteur_initial
- commit_lots: support promoter_owner_id (param explicite OU fallback copropriete)
- ImportWizardPage: banner "Mode promoteur actif"

### Fusion doublons owners (iter90kz - 2026-07-22)
- Utilise scripts/merge_owners.py existant (_apply_merge)
- 17 groupes fusionnes, 47 doublons supprimes (83 -> 36 owners)
- MATEXI: 9+2 doublons -> 1 canonique (55655c92, 120 lots, 8 JE)
- Vendeur: 12->1, Acheteur: 11->1, Buyer: 4->1, TEUWEN: 2->1
- 8 paires homonymes test (Martin, Dubois, Lefevre, etc.) fusionnees
- Verification: 0 doublon restant (case-insensitive)
- owner_ids_to_link dans coproprietes.py L327-339: verifie OK

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
