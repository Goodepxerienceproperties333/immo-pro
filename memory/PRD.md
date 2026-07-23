# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email)

## Fonctionnalites implementees

### Session courante (Juillet 2026)

#### P0 - Workflow suggestion/validation auto-lettrage (DONE)
- Backend: `_try_auto_lettrage_vcs` refactore pour stocker des SUGGESTIONS (suggested_match_to, suggested_match_type, suggested_match_label) au lieu de lettrer directement
- Endpoint POST /api/banking/transactions/{txn_id}/validate-suggestion : valide une suggestion individuelle
- Endpoint POST /api/banking/statements/{stmt_id}/validate-all-suggestions : validation batch
- Endpoint DELETE /api/banking/transactions/{txn_id}/suggestion : rejet d'une suggestion
- Endpoint POST /api/banking/auto-lettrage-vcs : refactore pour retourner le nombre de suggestions generees
- Frontend: Badge amber "Proprio?", "Fourn.?", "Fact.?" pour les suggestions
- Frontend: Boutons Valider (check vert) / Rejeter (X rouge) par transaction
- Frontend: Barre "Valider toutes les suggestions" au niveau de l'extrait
- Frontend: Nom suggere affiche sous la contrepartie (label amber)
- Bouton renomme "Suggestions VCS" (anciennement "Auto-lettrage VCS")

#### Nettoyage PCMN & Natures de depenses
- Garde-fou anti-doublons creation PCMN comptes 6/7xxx (noms similaires -> 409)
- Garde-fou anti-doublons creation natures de depenses (noms similaires -> 409)
- Script `cleanup_pcmn.py` : fusion doublons classe 6 + correction 6140/6141
- Script `cleanup_expense_categories.py` : fusion natures doublons
- Script `audit_class6_entries.py` : detection ecritures en doublon sur comptes differents

#### Amelioration interface lettrage bancaire
- Bouton "Montants identiques" : filtre les factures dont le montant est egal a la transaction (tolerance +/-0.01 EUR)
- Tri par defaut : factures avec montant identique affichees en premier
- Badge "= MONTANT" sur les factures correspondantes (bordure amber)
- Reset du filtre a la fermeture du dialog

#### Bundle Import Dialog
- Refonte complete du flux d'import de regroupement PDF
- Formulaire complet de creation (formulaire gauche, apercu PDF droite)
- Dropdown fournisseurs existants avec recherche
- Endpoint preview bloc PDF (GET /api/invoices/bundle-preview-block)
- Commit partiel (cleanup=false pour creation par bloc)

#### Verrou fiscal ameliore
- Fallback datetime + diagnostics enrichis listant les exercices existants

### Sessions precedentes
- Corrections journal_entry_id, Warning modal suppression releves
- Buyer/Seller names Balance Tiers, MUT-F safeguard
- Fix doublons fournisseurs, bouton "Creer proprietaires manquants"
- Cascade deletion MUT-*, Auto-unlink bank txns
- Isolation ACP dans le lettrage (lot_owners_only=true)
- Fix chatbot support hallucinations
- Layout grille categorisation bancaire
- Spinner chargement import extraits

## Backlog prioritise

### P0 - Scripts cleanup (user verification pending)
- `cleanup_pcmn.py`, `cleanup_expense_categories.py`, `audit_class6_entries.py`
- `reverse_degrande_q4_mut_f.py`, `merge_good_experience.py`

### P1 - Important
- PCMN Consistency : aligner convention 8 chiffres comptes bancaires
- TEUWEN lot mapping : logique lot.owner_id + distribution_keys

### P2-P5 - Futur
- P2: Export Journaux CSV/PDF
- P3: Reset bulk factures payees
- P4: Certificat fiscal annuel
- P5: Emails relance automatiques (APScheduler)
