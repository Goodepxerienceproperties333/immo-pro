# NextGe Copro - PRD (Product Requirements Document)

## Probleme
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Stack
- **Backend**: FastAPI, Async MongoDB (Motor), Python
- **Frontend**: React, Tailwind CSS, Shadcn UI
- **DB**: MongoDB
- **Integrations**: Emergent LLM Key (Claude Sonnet pour extraction IA), Microsoft Graph (Email), SMTP (One2Net)

## Architecture Multi-Syndic (syndic_id)
- Chaque document metier porte un champ `syndic_id` identifiant le cabinet syndic proprietaire
- `syndic_scope.py` : helpers centraux (resolve_syndic_id, syndic_query, inject_syndic)
- Middleware `server.py` : extrait le syndic_id du token JWT et le place dans `request.state.syndic_id`
- Chinese Wall : double protection (middleware copropriete_id check + route-level syndic_query filter)
- Convention : superadmin voit tout (syndic_id=None), syndic voit ses donnees, gestionnaire herite du parent_syndic_id

## Fonctionnalites implementees

### Session courante (Fevrier 2026)

#### P1 - Export Journaux CSV / PDF avec selecteur de dates (DONE - 7/7 backend + E2E iter 76)
- Backend: /api/exports/journals.csv et /api/exports/journals.pdf (routes/exports.py)
- Filtres: copropriete_id, journal_type (OD/AC/VE/FI/AN/AP), date_from, date_to, include_reversals
- Chinese Wall: syndic_id + copropriete_id enforced (middleware 403 + _build_journal_query)
- CSV: UTF-8+BOM, delimiteur ';', decimales FR (virgule), ligne TOTAL
- PDF: A4 paysage, ReportLab, en-tete (ACP, journal, periode, edition), totaux
- Frontend: 2 boutons "Export CSV" / "Export PDF" dans JournalsPage (data-testid: export-journals-csv-btn, export-journals-pdf-btn)

#### P0 - Chinese Wall attach-to-copro (DONE - Tests 5/5 iter 75)
- Fix verifie sur /api/owners/{id}/attach-to-copro (properties.py L953-978)
- Owner doit deja appartenir au meme syndic (via copropriete_ids OR lots OR owner.syndic_id)
- Sinon 403 "Acces refuse a ce proprietaire (chinese wall)"
- Cas legitime same-syndic reste 200 (idempotent)
- Superadmin conserve son bypass
- Test file: /app/backend/tests/test_attach_owner_chinese_wall.py

#### P0 - Securite Multi-Syndic syndic_id (DONE - Tests 11/11)
- Infrastructure: syndic_scope.py, middleware server.py (lines 356-363)
- Verrouille: coproprietes.py, properties.py, suppliers.py, invoices.py, accounting.py, banking.py, fund_calls.py, import_wizard.py, reports.py, documents.py, fiscal.py, expense_categories.py
- Fix: create_owner (request: Request), create_transaction (request: Request), create_invoice (request: Request)
- Fix: find_duplicate_owner accepts request=None param
- DB nettoyee: 96 test users + 183 coproprietes orphelines supprimees
- Tests: iteration_74 - 11/11 backend tests PASS

#### Gestion des Collaborateurs dans Mon Bureau (DONE)
- Section "Collaborateurs" dans MonBureauPage avec composant TeamSection.js
- Deux modes de creation : directe (nom + email + mdp temporaire) OU invitation par email
- Selection du profil (role_template) via dropdown
- Selection des ACPs accessibles par checkbox (avec "Toutes les coproprietes")
- CRUD complet : ajouter, modifier, supprimer, renvoyer invitation
- Badges : profil, statut "En attente", ACPs assignees
- Tests: 26/26 backend + 100% frontend (iteration 73)

#### P0 - Fix Purge Syndic - Suppression owners complete (DONE)
- 5 sources de collecte owner_ids + suppression users role=owner + owner_access_audit
- Tests: 18/18 (iter 72) + 26/26 (iter 71)

#### P1 - PCMN Consistency - Normalisation 8 chiffres (DONE)
- pcmn_utils.py + endpoint migration normalize-bank-pcmn
- Tests: 26/26 (iter 71)

#### P0 - Fix Email SMTP "Boite non autorisee" (DONE)
- smtp_username automatiquement ajoute aux boites autorisees quand provider=smtp
- communication.py supporte maintenant l'envoi SMTP complet

#### Purge DB Preview (DONE)
- Collections videes sauf users et pcmn_accounts

### Sessions precedentes
- Auto-lettrage, Balance de Tiers fix, Categorisation simplifiee, Filtre date $lte fix
- Nettoyage PCMN, Bundle Import Dialog, Verrou fiscal, Chatbot, Layout, Spinner

## Backlog prioritise

### P1 - Important
- TEUWEN lot mapping: logique lot.owner_id + distribution_keys (RECURRENT)

### P2-P5 - Futur
- P2: Outil admin reset bulk factures payees -> impayees
- P3: Certificat fiscal annuel
- P4: Emails relance automatiques (APScheduler)
- P5: (option UX) Remplacer les inputs date natifs de JournalsPage par un shadcn DatePicker FR

### Session courante (Fevrier 2026)

#### iter90gz - Bilan equilibre avec OD de financement par reserve (DONE - 4/4 tests)
- Bug : le bilan etait desequilibre de 3545.30 EUR apres OD Sneyers (financement par fonds de reserve)
- RCA : `compute_bilan_data` excluait les lignes classe 6 des OD (iter90g3b) -> reserve (Passif) baissait sans compensation par 499 (Passif)
- Fix : integrer TOUTES les lignes classe 6 dans total_charges (AC + OD + VE), independamment du journal_type
- Impact : reserve -X EUR (Passif) <-> 499 boni +X EUR (Passif) -> equilibre Actif = Passif preserve
- Fichier : /app/backend/routes/reports.py L1029-1050
- Tests : /app/backend/tests/test_iter90gz_bilan_od_included_in_charges.py (4/4 PASS)
- Ancien test iter90g3b (logique inverse obsolete) supprime

#### iter90g0 - Owner Portal Charges : OD (Op. Diverses) integrees (DONE - 6/6 backend + FE iter 85)
- Endpoint /api/owner/invoices : merge factures + OD (class-6) avec sign inversion
- Fallback DK par defaut de l'ACP si aucune DK sur la ligne/entree OD
- Reversed/is_reversal OD ignorees
- UI OwnerPortalPage : badge 'OD' + statut 'Ecriture diverse' + row bg-indigo
- Verifie : Boxus Maria = 7 factures 1630.72 + 1 OD Senyers -1091.95 = 538.77 EUR
- Test file : /app/backend/tests/test_iter90g0_charges_od.py

#### iter90fz - Owner Portal Charges : projection dynamique via DK (DONE - iter 84)
#### iter90fx - Situation email dialog : Du = 1er jour FY (DONE - iter 83)
#### iter90fw - Bank movements : match IBAN + PCMN prefix (DONE - iter 83)
#### iter90fv - SMTP One2Net (DONE - 9/9 + envoi reel iter 82)

#### FEATURE - Systeme de tickets support (bug escalation) (DONE - 19/19 backend + 100% frontend iter 77)
- Chatbot avec onglets Assistant IA / Mes tickets + mode picker (operationnel vs bug)
- Formulaire BugReportForm (titre, description, etapes, attendu/observe, pieces jointes max 5x10MB tout type, email)
- Backend /api/tickets (create, list, detail, events, status, comments, assign, attachments, delete, statuses)
- Numerotation atomique TICK-YYYY-XXXX via db.counters
- Stockage pieces jointes GridFS bucket 'ticket_attachments'
- Statuts : Ouvert -> Affecte -> En cours -> Testing -> Deploiement -> Cloture (+ Refuse)
- Chinese Wall : syndic voit ses tickets (via syndic_id ou requester_user_id), superadmin voit tout
- Emails DRY-RUN (MAIL_ENABLED=false) : notification support a la creation, notification demandeur a chaque changement de statut
- Page superadmin /admin/tickets + sidebar 'Support & Tickets'
- Test file: /app/backend/tests/test_iter90fs_tickets.py

### iter91 (27 juillet 2026)

#### iter91a - Homonymes VCS Banking (DONE - 13/13 pytest)
- Bug : quand 2 proprietaires partagent exactement le meme last_name (ex. Dupont Paul et Dupont Marie), une transaction bancaire etait auto-matchee aleatoirement.
- Fix : `_disambiguate_owner_candidates` dans `banking.py` applique en cascade : (1) VCS unique, (2) discriminant `first_name` present dans `cp_name` (\b), (3) discriminant montant = solde debiteur ouvert unique, (4) sinon `ambiguous_owner_candidates` stockes sur la txn pour rapprochement manuel.
- Frontend BankingPage : banner orange "Homonymes (N) - lettrage manuel" (data-testid=homonym-warning-<txn_id>).
- Test : /app/backend/tests/test_iter91a_homonym_disambiguation.py (6/6) + regression iter90ib (7/7).

#### iter91b - Refonte OD JournalsPage (DONE - E2E validated)
- Reordre colonnes : Nature de depense | Compte | Libelle | Cle de repartition | Debit | Credit | %Occ | %Prop.
- Selection d'une Nature de depense auto-remplit et VERROUILLE le compte comptable (bg gris, non editable), + %Occ/%Prop + Cle par defaut de la categorie.
- Backend JournalEntryLine accepte `expense_category_id: Optional[str]` (audit trail + rapport OD par nature).

#### iter91c - Balance des Tiers : proprietaires orphelins + creation fiche (DONE - E2E validated)
- Bug : Matexi (promoteur avec compte 41010986 en AN, sans fiche owner) etait invisible dans la balance des tiers alors qu'il apparaissait dans le bilan.
- Fix backend `balance_tiers_owners` : elargit la detection d'orphelins de `4100/4000/4001` a `410*` (tous comptes tiers proprietaires classe 41), enrichit `acc_names` depuis les `journal_entries.lines.account_name` et expose `orphan_account_number`.
- Nouvel endpoint POST `/api/reports/balance-tiers/create-owner-from-orphan` : cree une fiche proprietaire complete (nom confirme par le syndic, VCS auto-genere, `tier_accounts[copro].main=<account_number>`, retro-marque les journal_entries lignes avec `third_party_id`, cree l'entree pcmn_accounts si absente).
- Frontend BalanceTiersPage : le texte "Rattacher" est remplace par un bouton "Creer fiche" (data-testid=create-owner-from-orphan-*) qui ouvre un dialogue de confirmation avec nom pre-rempli + solde/mouvements du compte.


## Refactoring

### iter91d/e/f (27 juillet 2026 - suite)

#### iter91d - Liste des depenses PDF (DONE - 8/8 pytest + frontend E2E)
- Nouveau helper `_build_expenses_list_pdf(db, copropriete_id, fiscal_year_id) -> bytes` dans `reports.py`
- Fusionne via pypdf : (1) synthese portrait (`pdf_synthese_depenses.py` - nouveau module) groupee par cle de repartition + nature, avec totaux immeuble ; (2) detail paysage existant `build_liste_depenses_pdf`.
- Retire le try/ImportError silencieux dans `communication.py` (l'attachement decompte annuel fonctionne desormais nativement).

#### iter91e - Nettoyage Natures de depense (DONE)
- Endpoint `POST /api/admin/expense-categories/dedupe` avec mode `dry_run` (rapport JSON) et mode `execution` (avec `merges=[{target_id, source_ids}]`).
- Detection : strategies `name_normalized` (accents/casse ignores, par defaut), `name_account`, `name`.
- Detection libelles malformes : commencent par `<digit>)`, ou 100% numeriques, ou < 3 chars.
- Fusion : reassigne `journal_entries.lines.expense_category_id`, supprime les sources. Idempotente (fusion 2x = no-op safe).
- Frontend `/admin/expense-categories-dedupe` (`AdminExpenseCategoriesDedupePage.js`) : preview + checkboxes pour selection + confirmation window.confirm + re-analyse automatique.
- Lien : card indigo "Nettoyage Natures de depense" dans le dashboard admin.
- Test Gaura : 10 doublons (accents/casse) + 1 malforme "7362)" [61214] detectes.

#### iter91f - Import Wizard : creation proprietaires manquants (DONE)
- Endpoint `POST /api/import-wizard/sessions/{id}/preview-opening-balance-orphans` : detecte les comptes 410XXXX / 4001XXXX presents dans l'AN sans owner rattache. Ignore les comptes deja mappes via tier_accounts et les auxiliary_code existants.
- `CommitOpeningBalanceInput` accepte `owner_confirmations: [{account_number, name, first_name?, last_name?}]`. Chaque confirmation cree une fiche complete AVANT resolution des lignes AN : VCS auto-genere (mod 97), tier_accounts[copro].main=account_number, auxiliary_code = C+4 derniers chars.
- Reponse commit enrichie : `owners_created: [...]`.
- Frontend `ImportWizardPage.js` : preview automatique avant commit sur l'etape `opening_balance`. Si orphelins detectes, un dialogue modal (`orphan-owners-dialog`) s'affiche avec Input editable par ligne. Bouton "Creer les fiches et valider l'OD" -> commit avec confirmations.
- Corrige la racine du probleme Matexi retroactivement corrige par iter91c : desormais, l'import evite de creer des orphelins des le depart.

- import_wizard.py (>3500 lignes) a decouper
- reports.py (logique PCMN complexe) a simplifier

#### iter93a - Tableau de bord Syndic global (Owners + Suppliers) (DONE - 2026-02)
- Backend : deux nouveaux endpoints scopes syndic-wide et Chinese Wall strict :
  - `GET /api/owners/syndic-global` -> pour chaque proprietaire accessible au syndic, enrichit `{acp_ids, acp_names:[{id,name,reference}], acp_count}`. Union depuis `lots.owner_id/owner_ids` + `owners.copropriete_ids[]`.
  - `GET /api/suppliers/syndic-global` -> idem pour fournisseurs. Union depuis `supplier.copropriete_id` + `supplier.tier_accounts[cid]`.
- Frontend `/coproprietes` : refactor en Tabs (`syndic-dashboard-tabs`). 3 onglets :
  - `tab-acps` (comportement legacy conserve : liste + Modifier / Archiver / Vider / Supprimer)
  - `tab-owners` (nouveau composant `SyndicOwnersGlobalTab`) : recherche libre, filtre par ACP, tri Nom<->Nb ACPs, badges cliquables (navigation vers `/lots?copropriete_id=...`), dialog CRUD Owner avec Select ACP obligatoire.
  - `tab-suppliers` (nouveau composant `SyndicSuppliersGlobalTab`) : idem cote fournisseurs (navigation vers `/suppliers?copropriete_id=...`), dialog CRUD avec Select ACP obligatoire pour la creation.
- Tests : 8/8 backend PASS (pytest `test_iter93a_syndic_global.py`), Chinese Wall verifie entre syndic_alpha et syndic_beta.
- Data-testids : `tab-acps|owners|suppliers`, `syndic-owners-global-tab`, `syndic-suppliers-global-tab`, `create-owner-global-btn`, `edit-owner-global-{id}`, `owner-acp-select`, `owner-global-dialog`, `create-supplier-global-btn`, `edit-supplier-global-{id}`, `supplier-acp-select`, `owners-global-search|acp-filter|sort-btn`, etc.

## Roadmap / Prioritized backlog
- P1 : TEUWEN legacy mutations lot mapping (`reports.py` + `pdf_decompte.py`) - combiner `lot.owner_id` avec `distribution_keys` pour les mutations historiques.
- P2 : Admin tool bulk reset legacy paid invoices -> unpaid.
- P3 : Certificat fiscal annuel.
- P4 : Automated debt collection emails (APScheduler quotidien).
- P5 : Refactoring `import_wizard.py` (>3900 lignes) et `reports.py` (>4500 lignes).


---
## 🔒 POINT DE RESTAURATION STABLE - 27/07/2026 17h00 (iter93m)

L'application est declaree **STABLE - Aucun bug connu** a cette date.
Toutes les fonctionnalites principales fonctionnent :
- Wizard de creation d'ACP sequentiel (6 sous-etapes)
- Import PDF/CSV proprietaires & lots avec detection quasi-doublons (SRL/BVBA/...)
- OD avec picker Nature/Proprietaire/Fournisseur unifie recherchable
- Tableau de bord Syndic centralise (ACPs / Owners / Suppliers)
- Chinese Wall strict multi-syndic
- PDFs Bilan / Balance tiers / Decompte / Resultat net / Liste depenses
- ZIP archives comprehensive
- Deduplication comptes / doublons proprios/fournisseurs
- Backup APScheduler quotidien
- Suppression ACP restreinte (Superadmin ou Evrard Gerald)

En cas de regression future, utiliser le rollback Emergent vers ce commit.

## Nouvelle feature (iter93l) - Generateur ACP DEMO
- Endpoint superadmin `POST /api/admin/demo/generate-acp` : cree une ACP "DEMO - Residence Les Cerisiers" avec 10 lots, 10 proprios belges realistes, 5 fournisseurs, 2 cles de repartition (Generale + Ascenseur), budget 2025 (6 lignes ~29.6k EUR), 8 factures reelles avec TVA, 28 transactions bancaires (provisions + reglements). Chinese Wall respecte.
- UI `AdminDashboardPage.js` : bloc violet "Copropriete DEMO pour presentation" avec boutons Generer / Regenerer / Supprimer.
- Regenerer supprime en cascade la precedente DEMO (owners @demo.be + toutes les collections liees).
- ACP rattachee automatiquement au superadmin genereur pour visualisation directe.


## iter93o - DEMO comptablement complete (AN/VE/AC/FI)
- Refonte du generateur pour produire une comptabilite belge integre :
  - 10 lots totalisant **10.000 milliemes** exactement (cle generale)
  - 10 proprietaires avec **comptes tiers 4101xxxx (roulement)** et **4100xxxx (reserve)** par proprio, stockes dans `owner.tier_accounts[copro_id]`
  - Budget annuel **12.000 EUR** (6 natures de depense)
  - **Journal AN** : ouverture avec fonds de reserve 5.000 EUR (`is_opening_balance: True`)
  - **Journal VE** : 4 appels trimestriels de 3.000 EUR (10 lignes proprio + 1 credit 730000 par trimestre)
  - **Journal AC** : 5 factures fournisseurs (ELIA, Kone, NextGeCopro, Securitas, AXA) avec TVA
  - **Journal FI** : 2 paiements bancaires recus (Van Damme + De Coninck Q1) pour tester le lettrage
  - Debits = Credits = **24.995 EUR** parfaitement equilibres
- La Balance des Tiers et le Bilan sont immediatement consultables :
  - Balance tiers : 10 lignes proprios avec comptes prov/reserve reels, total appele/paye/solde correct
  - Bilan : Actif = Passif = 17.945 EUR, `equilibre: True`
- `third_party_id` (et non `tier_id`) utilise dans les lignes de journal pour rattachement proprio/fournisseur.


## iter93v (2026-02-28) - Correction MutationDialog: selecteur cle de repartition
### Bug corrige
- **P0** : Dans `MutationDialog` (LotsPage.js), le selecteur de cle de repartition (obligatoire quand le lot est absent de la cle par defaut) ne s'affichait jamais.
- **Cause racine** : Le frontend appelait `GET /coproprietes/{copropriete_id}/distribution-keys` (endpoint inexistant -> 404), donc `keys` restait vide et la condition `keys.length > 0 && !lotInDefault` etait toujours fausse.
- **Fix** : Utilise l'endpoint reel `GET /distribution-keys?copropriete_id=X` (defini dans `invoices.py:137`).
### Verification
- Test screenshot sur ACP 66dafcef... (Ph. Van der Aa) : le lot A 301 (absent de la cle "3/11 et 8/11") affiche desormais le bandeau ambre "CLE DE REPARTITION REQUISE" avec le dropdown listant "Batiment A (10000.000)".
- Le backend `properties.py` acceptait deja `distribution_key_id` dans `LotMutationInput` et le propage via `_compute_mutation_breakdown(override_key_id=...)`.
- Fichier modifie : `/app/frontend/src/pages/LotsPage.js` (ligne ~403-411, useEffect chargement keys).



## iter93w (2026-02-28) - Wizard Optipro : assignation cle de fallback pour lots hors cle par defaut
### Nouvelle fonctionnalite
- Detection automatique en fin de wizard des lots absents de la cle de repartition par defaut.
- Ecran de recap final affiche un panneau ambre listant chaque lot pending avec un dropdown des cles alternatives contenant ce lot (share > 0 uniquement).
- Boutons "Ouvrir l'ACP" et "Terminer et aller a la Comptabilite" desactives tant qu'il reste des lots sans cle assignee.
- Bouton "Enregistrer les cles selectionnees" pour sauvegarder les assignations partielles.
- Le champ `fallback_distribution_key_id` est stocke sur le document `lots`.
### Backend
- `GET /api/import-wizard/coproprietes/{copropriete_id}/lots-fallback-check` : retourne `{has_default_key, default_key_name, lots_pending: [...]}`.
- `POST /api/import-wizard/coproprietes/{copropriete_id}/assign-fallback-keys` : accepte `{assignments: [{lot_id, distribution_key_id}]}`. Valide que la cle appartient a l'ACP et contient le lot avec share > 0.
- `POST /api/import-wizard/sessions/{session_id}/finish` : refuse (HTTP 400 + code `LOTS_WITHOUT_FALLBACK_KEY`) si des lots pending sans fallback assigne.
### Fichiers modifies
- Backend : `/app/backend/routes/import_wizard.py` (~+170 lignes)
- Frontend : `/app/frontend/src/pages/ImportWizardPage.js` (state fallbackCheck, panneau UI, gating des boutons finish)
### Verification
- Test API : POST assign avec cle invalide -> error "Cle introuvable" ; POST finish avec 30 lots pending -> HTTP 400 avec detail `{code, message, pending_lots}`.
- Test UI screenshot : panneau ambre affiche 30 lots, dropdown pre-rempli si valeur precedente, toast succes apres save, compteur decremente, boutons "Terminer" desactives.


## iter93x (2026-02-28) - Bug fix: appels speciaux invisibles dans l'ecran Appels de Fonds
### Bug corrige
- **P0** : Les appels de fonds crees via `POST /api/fund-calls` (creation manuelle, ex : "Appel special - Travaux coursives") n'apparaissaient pas dans l'ecran `/fund-calls` bien qu'ils genèrent correctement les ecritures VE.
### Cause racine
- L'endpoint `create_fund_call` (fund_calls.py:754) inserait le document sans champ `syndic_id`.
- L'endpoint `list_fund_calls` (fund_calls.py:729) applique `syndic_query(request)` qui filtre par `syndic_id` -> les documents sans ce champ etaient invisibles pour l'utilisateur syndic.
- Les appels auto issus du budget (bulk-from-budget line 2170-2172) ajoutaient correctement `syndic_id`, ce qui masquait le bug pour les creations en masse.
### Fix
- Ajout de `inject_syndic(doc, request)` juste avant `db.fund_calls.insert_one(doc)` dans le POST manuel.
- Backfill script one-shot execute : 3 appels speciaux "Travaux coursives" restaures dans le DB preview.
### Verification
- Test API : GET /fund-calls retourne maintenant 9 appels (au lieu de 6) dont les 3 specials.
- Test UI screenshot : 3 badges "Appel special" (orange) visibles sur l'ecran Appels de Fonds pour ACP Van der Aa.
### Fichier modifie
- `/app/backend/routes/fund_calls.py` (ligne 909-913, injection syndic_id).

## iter93y (2026-02-28) - Budget : support des comptes classe 7 (produits diminuant les charges)
### Nouvelle fonctionnalite
- Selection de comptes de classe 6 (charges) ET de classe 7 (produits) dans les lignes budget.
- Bascule automatique du signe : montant stocke en negatif pour classe 7 (permet a sum(amount) de calculer le net).
- Ventilation visuelle : "Charges (cl. 6) : X - Produits (cl. 7) : Y = TOTAL NET Z EUR".
- Badge vert "PRODUIT" sur les lignes classe 7 pour clarte visuelle.
- Fond legerement teinte sur les lignes classe 7.
### Impact metier
- Le total net du budget est utilise tel quel par le generateur d'appels de fonds (`build_from_budget` fund_calls.py) : la reduction par les produits est propagee automatiquement aux distributions par lot.
- Le PDF Budget reflete correctement le total (deja base sur sum(amount)).
### Fichiers modifies
- `/app/frontend/src/pages/FiscalYearPage.js` : fetch classe 6+7, auto-flip du signe, breakdown UI.
### Verification screenshot
- Ajout ligne "61 - Services et biens divers" (cl. 6) 3000 EUR + ligne "742 - Recettes loyers" (cl. 7) 500 EUR (stocke -500).
- Total net affiche : 2500 EUR = 3000 - 500. Correct.


## iter93z (2026-02-28) - Bug fix: parser cle de repartition ne capturait pas tous les lots
### Bug corrige
- **P0** : Le parser texte-fallback des cles de repartition PDF (`parse_distribution_keys_pdf`) ne capturait que 28/58 lots pour la cle "0015 Clé spéciale 3/11" de l'ACP Van der Aa.
- Lots ignores : tous les garages (A/B G01-G22), parkings exterieurs (A Pex1-7), lot A 301 (sans owner code).
### Cause racine
- Le regex text-fallback utilisait `[A-Z]\s*\d{3,4}` pour le libelle, ce qui exigeait des chiffres directement apres la lettre initiale. Rejette donc :
  - `A G01`, `B G22` (garages : lettre + espace + G + chiffres)
  - `A Pex1`, `A Pex7` (parkings : lettre + espace + mot + chiffres)
- Le regex exigeait aussi un code owner `C\d{3,5}` obligatoire, ce qui excluait les lots sans proprietaire assigne (ex : `A 301 - APPARTEMENT - 218.180000`).
### Fix
- Regex mis a jour dans `/app/backend/import_wizard/pdf_utils.py` (fonction `parse_distribution_keys_pdf`) :
  - Libelle : `[A-Z]\s+[A-Za-z0-9][A-Za-z0-9\-]*` (accepte prefix alphanumerique + tirets pour lots composites 009-010)
  - Groupe owner : rendu **optionnel** via `(?:...)?`
  - Type de lot : capture explicite (APPARTEMENT, GARAGE, PARKING EXT., etc.)
- Backfill one-shot : cle "0015" (ACP Van der Aa) rechargee -> 58/58 lots, total 10000 exact.
- Test regression : `/app/backend/tests/test_iter93z_distribution_key_parser_alphanumeric.py` (4 tests passent).
### Verification
- Parser sur PDF utilisateur : 58 lots detectes (avant : 28).
- Endpoint `lots-fallback-check` retourne desormais 0 lot pending pour cette ACP (avant : 30).


## iter93ab (2026-02-28) - Formatage numerique unifie (espace millier + virgule decimale)
### Nouvelle fonctionnalite
- Formatage des montants EUR uniforme dans **toute la plateforme** : espace insecable comme separateur de milliers, virgule comme separateur decimal (standard belge/francais).
- Exemple : `10800.50` -> **"10 800,50"** (au lieu de "10800.50").
### Fichiers modifies
- **Nouveau** : `/app/frontend/src/lib/format.js` avec `fmtEUR()`, `fmtNumber()`, `fmtQuotity()`, `fmtPct()` (utilisant `Intl.NumberFormat('fr-BE')`).
- **27 fichiers migres** (306 remplacements de `.toFixed(2)` -> `fmtEUR(...)`):
  - 8 composants : BundleImportDialog, ImportSummary, RegularizationDialog, BudgetWizard, UnlettrageDialog, CodaImportDialog, SupplierMergeDialog, TiersDetailDialog
  - 19 pages : ImportWizardPage, OwnerPortalPage, DistributionKeysPage, AdminQualityAuditPage, BalanceTiersPage, FiscalYearPage, DashboardPage, ExpensesPage, LotsPage, ExpenseCategoriesPage, InvoicesPage, BankingPage, AdminUnlockEntryPage, FundCallsPage, GrandLivrePage, ReportsPage, RemindersPage, JournalsPage, TenantsPage
### Verification
- Screenshot FundCallsPage : 17 montants formates "4 750,00", "10 500,00", "49 000,00", "60 000,00" (espace millier + virgule decimale confirmes).
- Aucun montant en format raw dot detecte (ex : "10500.00").
- Lint : 0 erreur de parsing.
### Note
- Le formatage est fait cote frontend uniquement. Les valeurs stockees en DB restent en decimal standard (float).
- Le tab char dans certains noms d'appels ("60 000.00\t") est de la donnee utilisateur, pas du formatage.


## iter93ac (2026-02-28) - Formatage backend unifie (PDF + emails + messages)
### Nouvelle fonctionnalite
- Coherence UI/PDF/emails garantie : tous les montants affiches suivent le format belge/francais francophone (espace insecable millier + virgule decimale).
### Backend
- **Nouveau helper** : `/app/backend/utils/format.py` avec `fmt_eur()`, `fmt_number()`, `fmt_pct()`, `fmt_quotity()` — miroir cote Python de `/app/frontend/src/lib/format.js`.
- **PDF migres** : 3 fichiers utilisaient une variante `_eur_be()` avec point comme separateur (`10.800,50`) -> alignement sur espace insecable (`10 800,50`) :
  - `/app/backend/pdf_synthese_depenses.py`
  - `/app/backend/pdf_liste_depenses.py`
  - `/app/backend/pdf_journals_and_invoices.py`
- **PDF deja OK** : `pdf_decompte.py`, `pdf_bilan.py`, `pdf_budget.py`, `pdf_balance_tiers.py`, `pdf_situation_compte.py`, `pdf_mutation_decompte.py` avaient deja le bon format.
- **Emails / templates** : `routes/email_templates.py`, `routes/communication.py` (build_owner_email_context + placeholder balance) migres vers `fmt_eur()`.
- **Exports PDF** : `routes/exports.py` (PDF journaux + PDF releve situation compte) : montants dans les tables `Debit`, `Credit`, `TOTAL APPELS`, `TOTAL PAIEMENTS`, `Solde restant du` migres.
- **Messages d'erreur** : `routes/accounting.py` (ecriture non equilibree), `routes/banking.py` (5 messages de duplication / equilibre extrait / splits), `routes/fund_calls.py` (descriptions journal mutations + message API distribution regeneree).
### CSV
- **Non modifie** : `routes/exports.py` CSV (lignes 384-393) garde le format `1234,56` sans espace millier (compatibilite Excel EN qui interpreterait "1 234,56" comme texte).
### Tests
- `/app/backend/tests/test_iter93ac_format_helper.py` : 8 tests unitaires (fmt_eur basic/negatif/gros montants/edge cases/sans suffixe, fmt_number, fmt_pct, fmt_quotity) — tous passent.
### Verification
- Import de tous les modules migres : OK, backend redemarre sans erreur.
- Backend endpoint `/api/fund-calls` : 200 OK.


## iter93ad (2026-02-28) - Animation import PDF/CSV extraits bancaires
### Nouvelle fonctionnalite
- Modal full-viewport visible pendant l'import de PDF/CSV d'extraits bancaires (page `/banking`).
- Elements affiches :
  - Icone PDF + badge Sparkles anime (feedback visuel "IA au travail").
  - Chronometre temps ecoule (MM:SS) mis a jour chaque seconde.
  - Barre de progression shimmer animee (CSS keyframes).
  - Liste des fichiers en cours avec nom, taille formatee (Ko/Mo), spinner Loader2 tournant, badge "EN COURS".
  - Ligne de rassurance "Analyse en cours — merci de ne pas fermer cet onglet" avec point vert pulsant.
### Fichier modifie
- `/app/frontend/src/pages/BankingPage.js` :
  - Nouveaux states `importProgress` (files + startedAt) et `importElapsed` (timer).
  - `handleImportFiles` collecte les infos fichiers avant l'upload.
  - `useEffect` timer 1s incremente `importElapsed`.
  - Nouveau modal `import-overlay-modal` (fixed, z-50) remplace l'ancien overlay simple pour PDF/CSV.
  - L'overlay CODA garde son affichage historique (moins riche mais suffisant).
### Verification screenshot
- Screenshot avec network delay 30s : modal visible pendant 4s, chronometre passe de 00:01 -> 00:05, 3 fichiers listes (195.3 Ko, 97.7 Ko, 48.8 Ko) avec spinners actifs et badges "EN COURS".


## iter93ae (2026-02-28) - Banking : 3 modes d'affichage des extraits (Liste / Mois / Cartes)
### Nouvelle fonctionnalite
- Toggle de vue (persist localStorage) en tete de la sidebar extraits :
  - **Liste** (defaut, dense) : une ligne par extrait avec badge compte, numero, date, soldes O/F, badges statut et source. ~3x plus d'extraits visibles par ecran vs cartes.
  - **Mois** (groupe) : sections repliables par mois (AVRIL 2026, MARS 2026...) avec :
    - Triangle ► indiquant l'etat replie/deplie
    - Compteur d'extraits par mois
    - Somme des mouvements du mois (delta cloture-ouverture) en vert (positif) ou rouge
    - Clic sur en-tete pour plier/deplier
  - **Cartes** : ancien affichage riche conserve.
### Fichier modifie
- `/app/frontend/src/pages/BankingPage.js` :
  - States `stmtViewMode` (persist localStorage `banking_stmt_view`), `collapsedMonths`.
  - Helpers `renderStmtRow(s)` (ligne dense) et `renderStmtCard(s)` (carte historique).
  - Regroupement `groupedStatements` par mois (`YYYY-MM`) + labels francais.
### Verification screenshot
- Vue Liste : 133 extraits en lignes denses, hover, selection surlignee.
- Vue Mois : 18 groupes (mois), delta mensuel calcule, expansion/collapse fonctionnel.


## iter93af (2026-02-28) - Security Hardening (SEC-001 + SEC-002 + P3)
### SEC-001 [HIGH] - Auto-inscription publique vulnerable (fix)
- **Avant** : `POST /api/auth/register` creait un user `role="owner"` avec n'importe quel email. `/api/owner/*` linkait par email seul -> vol d'acces au portail d'un co-proprietaire non encore invite.
- **Apres** :
  - Register cree desormais `role="syndic"` (self-service = nouveau syndic, business intent).
  - Register refuse (HTTP 403) tout email correspondant a une fiche `db.owners` existante -> obligatoire d'utiliser le lien d'invitation du syndic.
  - Marquage `self_registered=True` sur les nouveaux comptes.
  - Defense-in-depth : `_require_owner_role` dans `owner_portal.py` verifie `user.role in ("owner", "occupant")` avant tout acces. Un role syndic/admin/superadmin recoit HTTP 403 sur `/api/owner/*`.
  - Frontend LoginPage : mode register libelle "Creer un compte syndic" + sous-titre expliquant que les proprietaires doivent utiliser leur invitation.

### SEC-002 [MEDIUM] - IDOR sur endpoints by-ID (fix)
- **Avant** : 4 endpoints banking + 1 invoices trouvaient les records par `id` sans verifier `syndic_id` -> un manager du syndic A pouvait lire/modifier/supprimer les extraits ou factures du syndic B en connaissant l'UUID.
- **Apres** : `syndic_query(request)` applique sur :
  - `GET /api/invoices/{invoice_id}` (line ~1908)
  - `POST /api/banking/statements/{stmt_id}/post` (line ~773)
  - `PUT /api/banking/statements/{stmt_id}` (line ~1055)
  - `DELETE /api/banking/statements/{stmt_id}` (line ~1113)
  - `POST /api/banking/lettrage` (line ~1610, + verif de l'objet cible facture/paiement)

### P3 - Hardening applique
- **COOKIE_SECURE auto** : detection prod via `FRONTEND_URL=https://...` OU `APP_ENV=production`. Force `Secure` flag sur cookies auth si prod, meme si `.env` ne definit pas explicitement `COOKIE_SECURE`.
- **Regex utilisateur echappe** : `banking.py::/search-owners` et `/lookup` utilisent maintenant `re.escape(q)` pour eliminer meta-caracteres regex (ReDoS neutralise, verifie 0.14s sur `(a+)+$`).
- **Admin seed fail-closed** : `seed_admin` refuse de creer le compte superadmin si `ADMIN_PASSWORD` non defini ou < 8 caracteres. Plus de default `admin123`.
- **test_credentials.md** : n'est plus ecrit en prod (`APP_ENV=production` ou `FRONTEND_URL=https://...`) NI si `ADMIN_PASSWORD` non configure.

### Fichiers modifies
- `/app/backend/server.py` (register, seed_admin, cookie secure auto, test_credentials skip prod)
- `/app/backend/routes/owner_portal.py` (`_require_owner_role` + integration dans `_resolve_owner` et `_resolve_owner_ids`)
- `/app/backend/routes/invoices.py` (get_invoice syndic_query)
- `/app/backend/routes/banking.py` (4 endpoints statements/lettrage + re.escape regex)
- `/app/frontend/src/pages/LoginPage.js` (libelle register)

### Tests
- `/app/backend/tests/test_iter93af_security_hardening.py` : 7 tests unitaires, tous passent.
### Verification curl
- SEC-001 : POST register avec email owner -> **HTTP 403** ("Contactez votre syndic").
- SEC-001 defense : GET /api/owner/dashboard en tant que syndic -> **HTTP 403**.
- SEC-002 : GET /api/invoices/<id_syndic_A> par syndic B -> **HTTP 404**.
- P3 ReDoS : query `(a+)+$` -> **HTTP 200 en 0.14s** (regex escape).
- P3 cookies : `Set-Cookie: ... Secure; HttpOnly; SameSite=None; Partitioned` sur login.


## iter93ag (2026-02-28) - Wizard ACP etape 5 : creation rapide d'un proprietaire orphelin
### Nouvelle fonctionnalite
- Bouton **&laquo; + Creer &raquo;** vert emeraude a cote de chaque badge "Orphelin : C1988Mme LOUETTE & BARBIEAUX Romain" a l'etape 5 (Revue des affectations).
- Ouverture d'un dialogue compact pre-rempli via heuristique :
  - **Nom** et **Prenom** extraits automatiquement (civilite `Mme/M./Mlle` retiree, dernier mot = prenom).
  - **Nom complet** libre (utile pour les cas type &laquo; X &amp; Y &raquo; qui ne se decoupent pas).
  - **Code auxiliaire** importe (C1988) affiche en bandeau ambre + envoye au backend.
  - Champs optionnels : Email, IBAN.
- A la validation :
  - `POST /api/owners?reuse_on_duplicate=true` (reutilise une fiche existante si homonyme detecte).
  - Ajout dans `sessionOwners` pour visibility immediate dans les autocompletes des autres lots.
  - **Auto-affectation au lot** ; efface les champs `_imported_owner_name` / `_imported_owner_aux` pour masquer le badge Orphelin.
  - Toast "Proprietaire cree" ou "Fiche existante reutilisee".
### Fichier modifie
- `/app/frontend/src/pages/CoproprietesPage.js` :
  - State `quickCreateOwner` + handlers `openQuickCreateOwner()` (parse heuristique) et `submitQuickCreateOwner()`.
  - Nouveau dialogue avec 6 champs (Nom, Prenom, Nom complet, Email, IBAN + bandeau code auxiliaire).
  - Bouton "+ Creer" ajoute a droite du badge Orphelin.
### Verification
- Lint : OK.
- Page /coproprietes charge sans erreur.


## iter93ah (2026-02-28) - Bug fix regression SEC-002 : lettrage fournisseur/proprietaire
### Bug corrige
- **P0 (regression iter93af)** : Le lettrage vers un fournisseur ou proprietaire echouait avec `HTTP 404 "Supplier_payment cible non trouve"` / `Owner_payment cible non trouve`.
### Cause racine
- Dans le fix SEC-002 (iter93af), j'avais suppose que `match_to_id` etait un ID de record `supplier_payments` / `owner_payments`. En realite, pour `match_type='supplier_payment'` / `'owner_payment'`, `match_to_id` est l'ID du **supplier** / **owner** directement (le paiement virtuel est materialise par le champ `matched_to` sur la transaction elle-meme, pas par un document dedie).
### Fix
- `routes/banking.py::lettrage` : correction du mapping match_type -> collection :
  - `invoice` -> `db.invoices` (verification syndic_query stricte)
  - `supplier_payment` -> `db.suppliers` (verification existence, visibilite deja gerée par _get_scope_for_filter en amont)
  - `owner_payment` -> `db.owners` (idem)
- Message d'erreur clarifie : "Fournisseur cible non trouve" / "Proprietaire cible non trouve" au lieu du technique "Supplier_payment cible non trouve".
### Verification
- Test lettrage supplier valide : HTTP 200.
- Test lettrage supplier inexistant : HTTP 404 "Fournisseur cible non trouve".
- Unlettrage cleanup : OK.


## iter93ai (2026-02-28) - Chatbot support : analyse comptable + diagnostic base sur donnees reelles
### Nouvelle fonctionnalite
Le chatbot support ("Assistant NextGe Copro") peut desormais **analyser les resultats comptables reels d'une ACP** et diagnostiquer les problemes courants (bilan non equilibre, balance tiers anormale, extraits non comptabilises, etc.).
### Ameliorations du system prompt (support.py)
- **Structure sidebar mise a jour** : 5 sections (GESTION, COMPTABILITE, FINANCE, RAPPORTS, SUPPORT) avec libelles exacts.
- **Procedures exhaustives couvertes** : wizard 6 etapes, lettrage/delettrage, imports (CODA/PDF/CSV), auto-lettrage VCS, appels 4 types, budgets classe 6+7, mutations avec fallback key, OD, exercices, rapports.
- **Section "DIAGNOSTIC & TROUBLESHOOTING COMPTABLE"** : liste des causes classiques et procedures pour :
  - Bilan non equilibre (5 causes chiffrees)
  - Compte de resultats anormal
  - Extrait bancaire non equilibre
  - Balance de tiers anormale
- **Methodologie de diagnostic** : concept + 3-4 causes + chemins de verification + invitation escalade.
- **Regles strictes** : cite noms exacts, base uniquement sur procedures documentees, escalade obligatoire pour bugs/facturation, `[[NEEDS_ESCALATION]]` marker.
### Injection de contexte diagnostique reel
- `ChatMessageInput` accepte desormais `copropriete_id` optionnel.
- Detection auto (via mots-cles : `bilan`, `equilibre`, `deseq`, `balance`, `solde`, `pourquoi`, `probleme`, ...) : si la question porte sur du diagnostic ET qu'un copropriete_id est fourni, on execute `_compute_diagnostic_snapshot()`.
- Le snapshot contient : ecritures deseq, exercices ouverts/clotures, appels de fonds, extraits brouillon vs comptabilises, factures non payees, nom de l'ACP.
- Ces metriques sont injectees en debut de prompt LLM pour permettre des reponses personnalisees chiffrees.
### Fichiers modifies
- `/app/backend/routes/support.py` :
  - System prompt reecrit (structure sidebar exacte + section troubleshooting comptable + regles strictes anti-hallucination).
  - `ChatMessageInput.copropriete_id` optionnel.
  - `_compute_diagnostic_snapshot(db, copropriete_id)` : agregation Mongo async des metriques cles.
  - `_DIAGNOSTIC_KEYWORDS` : mots-cles trigger.
  - Chat endpoint : injection du contexte diagnostic si applicable.
- `/app/frontend/src/components/SupportChatBubble.js` : passe `localStorage.selected_copro` en payload chat.
### Verification
- Test Q "Pourquoi mon bilan n'est pas equilibre ?" -> reponse cite "88 extraits en brouillon", "72 factures non payees", "11 appels de fonds", "aucune ecriture desequilibree", ordre d'intervention priorise.
- Test Q "Solde propriétaire bizarre" -> priorise cause n°1 (88 extraits non comptabilises), propose Auto-lettrage VCS, anticipe cas solde eleve vs negatif.
- Test hallucination (bouton fictif) -> chatbot refuse et escalade.


## iter93aj (2026-02-28) - Chatbot support : analyse des frais privatifs
### Nouvelle fonctionnalite
Le chatbot inclut desormais les **frais privatifs** (`is_private_fee=true`, compte 643 charges recuperables) dans son analyse diagnostique.
### Backend (`support.py`)
- `_compute_diagnostic_snapshot` calcule 3 nouvelles metriques :
  - Total factures privatifs (`is_private_fee=true`)
  - Nombre de privatifs impayes (statut `unpaid` / `partially_paid`)
  - Montant total impaye (agregation `$sum` sur `total_amount`)
- Nouvelle ligne dans le snapshot : "Frais privatifs : N au total, dont M impaye(s) pour X EUR"
- Montant formate via `fmt_eur()` (espace millier + virgule).
### System prompt
- Section troubleshooting "Balance de tiers anormale" enrichie avec cas privatif impaye.
- Nouvelle section "Frais privatifs (comptabilite specifique)" expliquant :
  - Champ `is_private_fee` + compte 643 recuperable
  - Structure `private_fee_allocations` multi-proprios
  - Ecriture ACH generee (Debit 643 / Credit 440xxx)
  - **Regle** : le 643 doit revenir a zero en fin d'exercice via refacturation aux proprios (appel special ou OD Debit 4xxx / Credit 643).
### Trigger keywords elargis
- Ajout de `privatif`, `privative`, `643`, `refacture`, `refacturation`, `analyse`, `verifier`, `controler`, `audit`, `comptabilite`, `compta` aux `_DIAGNOSTIC_KEYWORDS`.
### Verification
- Question "Analyse mes frais privatifs" -> Reponse cite **10 factures pour 1 590,00 EUR**, identifie le probleme (100% impaye), explique impact comptable (gonflement charges), propose 2 solutions concretes (Appel special OU OD Debit 4xxx / Credit 643) avec chemins d'onglets exacts.



## iter93bg - Validation stricte "Frais privatif requires owner" (Feb 2026 - DONE)

### Contexte
Bug utilisateur : "on ne peut pas avoir coche la case 'frais privatif' et sauver la facture sans avoir de proprietaire selectionne". La validation existait deja mais l'UX n'etait pas assez claire (message amber trop faible, bouton Save actif sans blocage visuel).

### Backend (`/app/backend/routes/invoices.py`)
- POST `/api/invoices` : rejet 400 explicite si `is_private_fee=True` sans `private_fee_allocations` (valides) NI `private_fee_owner_id` non-vide.
- PUT `/api/invoices/{id}` : meme regle.
- `private_fee_owner_id` avec whitespace uniquement -> rejete (`.strip()` obligatoire).
- Messages d'erreur explicites : "Frais privatif : au moins un proprietaire doit etre selectionne (champ 'private_fee_allocations' non vide, ou 'private_fee_owner_id' renseigne). Une facture marquee 'is_private_fee=true' ne peut pas etre enregistree sans proprietaire cible."

### Frontend (`/app/frontend/src/pages/InvoicesPage.js`)
- Fonction `saveInvoice` : nouvelle boucle qui valide chaque allocation individuellement AVANT le filter, avec message "proprietaire manquant sur la ligne d'allocation N".
- Bouton "Enregistrer" (`data-testid="inv-save-btn"`) : prop `disabled` inline evalue en live -> desactive quand allocations vides OU allocations avec owner_id vide/whitespace.
- Deux banners rouges/roses persistants :
  - `data-testid="private-fee-no-owner-error"` (aucune allocation)
  - `data-testid="private-fee-missing-owner-error"` (allocation sans owner)
- Ancien banner amber remplace par bordure `border-rose-300` + fond `bg-rose-50` pour signaler l'erreur bloquante.

### Tests
- `/app/backend/tests/test_iter93bg_private_fee_owner_required.py` : 6 tests pytest (POST no-owner, empty list, empty owner_id, whitespace legacy owner, valid allocation, PUT flow) - **6/6 PASSENT**.
- Testing agent `testing_agent_v3_fork` : backend 100%, frontend 100%.

## iter93bh - Wizard : Revue des factures une par une + auto-643 (Feb 2026 - DONE)

### Contexte
Extension demandee par l'utilisateur : "plutot que d'avoir une liste de toutes les factures comptabilisees [...] qu'on puisse passer en revue chaque facture [...] ca permet d'eviter les erreurs et les oublis, et aux syndic de directement correctement affecter en fonction de son expertise". Le compte 643 doit etre pris en compte pendant le wizard.

### Backend (`/app/backend/routes/invoices.py`)
- `GET /api/invoices` accepte un nouveau parametre optionnel `import_session_id`.
  - Restreint aux factures de la session wizard donnee.
  - Tri ASC par date (chronologique) quand present (vs DESC habituel).

### Frontend (`/app/frontend/src/pages/InvoicesPage.js`)
- Support de l'URL `/invoices?review_session=<sid>[&review_index=N]`.
- Charge automatiquement les factures de la session, ouvre le dialog d'edition sur la facture courante.
- Bandeau bleu `data-testid="review-mode-banner"` avec compteur `N/TOTAL`, badge amber "Frais privatif (compte 643) - proprietaire requis" si is_private_fee, et 3 boutons :
  - `review-prev-btn` (facture precedente)
  - `review-skip-btn` (ignorer sans sauvegarder, passe a la suivante)
  - `review-quit-btn` (quitter le mode revue)
- Apres save reussi : navigation automatique vers la facture suivante (toast d'information). Apres la derniere : URL nettoyee + toast "Revue terminee".
- Reutilise INTEGRALEMENT la validation iter93bg (bouton Save disable si allocation privatif vide).

### Frontend (`/app/frontend/src/pages/ImportWizardPage.js`)
- Nouveau state `invoiceReviewCta` populate apres `commit-invoices` reussi.
- Panneau bleu `data-testid="invoice-review-cta"` affiche apres commit :
  - Compteur "N facture(s) importee(s) avec succes"
  - Alerte specifique si `private_fees_detected > 0` (recommande la revue)
  - Bouton `start-invoice-review-btn` -> navigate vers `/invoices?review_session=<sid>`
  - Bouton `skip-invoice-review-btn` -> ferme le CTA, continue le wizard

### Detection 643 (deja en place, verifiee)
- `commit-invoices` marque `is_private_fee=True` automatiquement quand `account_num.startswith("643")` (import_wizard.py ligne 2013).
- Au moment de la revue, la case "Frais privatif" est donc pre-cochee, le compte 643 est verrouille, et la section "Repartition par proprietaire" est deja visible.

### Testing
- `testing_agent_v3_fork` iteration_93.json : backend 100%, frontend 100%, aucun bug identifie.
- Tests manuels e2e verifies (banner 1/33 sur premiere facture, banner 5/33 sur facture 643 avec badge amber, auto-checkbox is_private_fee).


## iter93bi - Wizard : blocage etape invoices + revue orphans 643 (Feb 2026 - DONE)

### Contexte
Utilisateur : "lors de l'import optipro je n'ai pas eu a valider les factures une a 1 alors que c'est ma demande, des frais privatifs etaient present et non reconnu - resultat = pas bon". Le CTA iter93bh n'apparaissait PAS car le wizard auto-avancait d'etape apres commit-invoices.

### Root cause
`ImportWizardPage.js` ligne 635 : `setStepIdx(stepIdx + 1)` s'executait toujours apres commit-invoices reussi. Le CTA rendu conditionnellement sur `step.key === 'invoices'` disparaissait immediatement.

### Fix
1. **Hold-step logic** dans `ImportWizardPage.js` : si `r.data.private_fees_detected > 0` apres commit-invoices, on RESET les buffers de parse (`setSniffResult(null)`, `setInvoicesParsed([])`) mais on ne fait PAS `setStepIdx`. Toast warning "N facture(s) sur compte 643 detectee(s) - assignez les proprietaires avant de continuer".
2. **CTA blocking style** : quand `blocking=true`, le panel devient rouge (`border-rose-500`), le titre "ACTION REQUISE : N frais privatif(s) 643 detecte(s)", bouton principal rouge "Reviser les N frais privatif(s) maintenant". Bouton secondaire "J'ai deja affecte -> reverifier" (`data-testid="check-invoice-review-btn"`) recompte les orphelins.
3. **Filtre `?filter=needs_owner`** : nouveau parametre URL cote `InvoicesPage.js` qui restreint la liste aux `is_private_fee=True AND private_fee_allocations=[]`. Utilise par le wizard quand `blocking=true`. Le bandeau devient rouge (`border-rose-400`) avec texte "Frais privatif 643 : assignez le proprietaire pour chaque facture".

### Testing
- `testing_agent_v3_fork` iteration_94 : backend 100% (4/4), frontend 100%.
- Screenshot manuel valide : sur un orphelin cree temporairement, banner rouge "1/1", dialog auto-ouvert sur facture 0029, Save disable, banner d'erreur "private-fee-no-owner-error" visible.


## iter93bj - Wizard : auto-reprise a la premiere etape non-terminee (Feb 2026 - DONE)

### Contexte
Utilisateur : "tu zappe les etapes apres la validation de la facture donc pas de bilan demande" (recap montrait "OD d'ouverture: -" manquant). Root cause : apres la revue des factures dans /invoices, l'utilisateur revenait au wizard qui repartait a stepIdx=0. Il cliquait "Etape deja validee - Continuer" en autopilote, puis "Passer cette etape" sans realiser qu'il sautait le bilan comptable (opening_balance).

### Fix
1. **Auto-jump (`ImportWizardPage.js` ligne 188+)** : au chargement de la session, on cherche l'index de la premiere etape dont `session.steps[key].count > 0 || inserted > 0 || fiscal_year_id` est faux (= non-terminee), et on `setStepIdx(firstUndoneIdx)`. Un toast INFO annonce "Reprise du wizard a l'etape 'XYZ' (N etape(s) deja validee(s) auparavant)".
2. **Action toast "Retourner au wizard" (`InvoicesPage.js` ligne 780+)** : le toast SUCCESS de fin de revue inclut maintenant un bouton d'action (Sonner action label) qui navigue vers `/import-wizard`. `useNavigate` importe et instancie au haut du composant.
3. **Sortie propre du mode revue** : les params `review_session`, `review_index`, `filter` sont TOUS supprimes de l'URL apres fin de revue.

### Testing
- `testing_agent_v3_fork` iteration_95 : frontend 100% (3/3 scenarios).
- Screenshot manuel : sur ACP Gaura 3 avec 5 etapes deja faites, /import-wizard demarre bien sur "Etape 6/8 : Journaux financiers" avec le toast bleu.


## iter93bk - Code Review : Lots critiques (Feb 2026 - DONE)

### Contexte
L'utilisateur a fourni un rapport de code review avec 10 items (5 Critiques + 5 Importants). Choix confirme : appliquer uniquement les lots CRITIQUES dans cette session, les Importants (refactoring composants monolithiques + auto_entries) sont reportes.

### Lot 1 - Securite : FAUX POSITIFS confirmes
- XSS via `dangerouslySetInnerHTML` (6 endroits) : TOUS deja sanitises via `sanitizeHtml()` (DOMPurify). Le rapport avait flag les usages sans reperer le wrapper.
- localStorage insecure (7 endroits) : TOUS contiennent UNIQUEMENT des donnees non-sensitives (timestamps consent, UI filter state, click counters). Aucun token/password/PII. Auth tokens sont dans les cookies httpOnly.

### Lot 2 - Stabilite backend
- **Imports circulaires** (2 chaines) :
  - `auto_entries.py <-> journal_reversals.py` : deja en late imports (dans les fonctions), pattern recommande par le rapport lui-meme. Rien a faire.
  - `bce_lookup.py <-> bce_opendata.py` : EXTRAIT les helpers partages `_norm`, `_tokens`, `token_similarity`, `_STOPWORDS`, `_MULTISPACE_RE` dans `/app/backend/bce_shared.py`. Les deux modules importent depuis bce_shared. Cycle CASSE.
- **F821 Undefined names (32 warnings audit)** : 30 etaient F841 (variables inutilisees, pas des bugs). Les 2 vrais F821 corriges :
  - `routes/banking.py:3486` : `request` undefined -> ajoute `request: Request` dans la signature de `create_batch_transactions`.
  - `routes/reports.py:2964` : `_ensure_copro_access` (dead code guarded par `if False else None`) -> supprime.
- **Dynamic imports (5 endroits, en fait 3)** : `__import__("re")` dans `reports.py:65` et `expense_categories.py:127,216` convertis en imports statiques `import re` en haut du fichier.

### Lot 3 - Test secrets (28 fichiers cites)
- Nouveau module `/app/backend/tests/test_credentials.py` : centralise `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SYNDIC_ALPHA_*`, `SYNDIC_BETA_*`, `OWNER_*` via `os.environ.get(..., default)`. Fonction `get_login_payload(role)`.
- Fichiers migres : `test_iter74_syndic_isolation.py` (import complet du module), + 20 autres fichiers (patch mecanique de `EMAIL = "admin@copro.be"` -> `EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")`).
- 343 fichiers de tests parsent tous cleanement (AST check).

### Lot 4 - Stale closures (198 warnings)
- Inspection systematique des 4 endroits les plus critiques cites par le rapport (`JournalsPage:62`, `OwnersPage:50`, `SyndicOnboardingWizard:42`, `OwnerPortalPage:115`).
- **VERDICT** : toutes les deps REELLES sont deja listees. Les "missing deps" du rapport sont des refs stables (module `api`, `setState` fns, constantes) que ESLint's `exhaustive-deps` sur-signale par prudence. Aucun stale closure reel detecte.
- Pas de modification code (eviter le bruit d'annotations inutiles).

### Testing
- `testing_agent_v3_fork` iteration_96 : backend 100%, 0 issue.
- Regression `test_iter93bg_private_fee_owner_required.py` : 6/6 OK.
- `ruff check --select F821 .` : 0 issues restantes.
- Login + list coproprietes + list invoices : tous 200.


## iter93bl - Fix retour au wizard apres revue (Feb 2026 - DONE)

### Contexte
Utilisateur (video) : "suite a tes modifications mauvais lien dans le retour au wizard apres affectation de frais privatifs dans les factures". Plusieurs bugs identifies apres analyse video :
1. Bouton "Retourner au wizard" **absent** sur les toasts de fin via **Ignorer** et **Quitter** (uniquement present sur le path Save).
2. Le lien du wizard `/import-wizard` **ne preservait pas** le `copropriete_id`, faisant perdre le contexte ACP au wizard.
3. Le param URL `filter=needs_owner` **restait dans l'URL** apres sortie du mode revue (fuite d'etat).

### Fix
Dans `InvoicesPage.js` :
- **Save-complete** : le toast success contient maintenant `navigate('/import-wizard?copropriete_id=<copro>')` (avec copropriete_id preserve). `sp.delete('filter')` ajoute.
- **review-skip-btn (Ignorer)** : ajout du meme toast avec bouton "Retourner au wizard" + preservation copropriete_id + `sp.delete('filter')`.
- **review-quit-btn (Quitter)** : idem + nouveau toast INFO "Mode revue quitte" avec action de retour.

### Testing
- Screenshot manuel valide : "Retourner au wizard" button visible: True. Apres clic, URL = `/import-wizard?copropriete_id=2ef4fcd6...`, wizard sur "Etape 6/8 : Journaux financiers" avec toast "Reprise du wizard".
- Lint OK, aucune regression.


## iter93bm - Anti-doublon cross-ACP + VCS generation (Feb 2026 - DONE)

### Contexte
Utilisateur : "tu crees encore des doublons de proprietaires c'est innacceptable" (screenshot 2 Blavier Thierry avec meme email/telephone/VCS). Puis : "lors de la creation d'un nouveau proprietaire manuellement, permettre de generer un numero VCS pour les paiements, il doit etre lie au proprietaire ... unique."

### Root cause du doublon
`find_duplicate_owner` (properties.py:433-436) scopait la recherche par `copropriete_ids` du target. Si le proprio existait dans une AUTRE ACP, la query renvoyait vide -> pas de doublon detecte -> creation d'un doublon a chaque nouvelle ACP.

### Fix
1. **Split du scoping** dans `find_duplicate_owner` :
   - `base_query` : cross-ACP dans le scope syndic pour les identifiants uniques (email, telephone, BCE, **VCS ajoute**).
   - `aux_query` : local ACP pour `auxiliary_code` (car aux_code Optipro est propre a chaque ACP).
2. **VCS ajoute** comme identifiant strict (norm_vcs). Detection cross-ACP.
3. **check_duplicate_owner** endpoint accepte maintenant param `vcs_code`.
4. **POST /api/owners/preview-vcs** : nouvel endpoint qui appelle `generate_vcs(db)` et retourne un VCS unique pre-genere pour le formulaire de creation.
5. **UI OwnersPage.js** : champ VCS visible dans le dialog Create/Edit avec bouton **"Generer/Regenerer"** (Sparkles icon). Bouton uniquement en mode Create (VCS fige apres creation). Nouveau champ **"Code auxiliaire (Optipro)"** distinct. Anti-doublon `onBlur` sur le champ VCS.

### Nettoyage donnees
Script MongoDB : merge du doublon Blavier Thierry (cf0d3836 -> ac2b7411). 6 lots reassignes, 7 ACPs consolidees dans `copropriete_ids`, doublon supprime.

### Testing
- `testing_agent_v3_fork` iteration_97 : backend 100% (6/6 pytest), frontend 100%.
- Manuel : preview-vcs retourne VCS uniques (+++000/0000/77701+++, ...78610+++, ...78711+++). POST /owners bloque avec 409 sur email existant. Banner jaune 'Doublon detecte' apparait au blur d'un VCS existant.


## iter93bn - VCS priorise sur email/phone dans dedup owners (Feb 2026 - DONE)

### Contexte
Utilisateur : "cette liste de proprietaires n'est pas reconnue" (PDF Optipro 12 lignes). Root cause : les 12 proprietaires partagent le placeholder email `info@nextgecopro.be`. La dedup basee sur email marquait 11 sur 12 comme doublons du 1er, meme si leur VCS est different (donc personnes differentes).

### Fix
Dans `properties.py:find_duplicate_owner` :
- Si `vcs_code` fourni : check d'abord tous les candidats pour un match VCS exact -> strict dup.
- Si VCS fourni MAIS pas de match -> **SKIP les checks email/phone/BCE** (le VCS est un identifiant unique par personne, il prime sur les canaux de contact).
- Si pas de VCS fourni : logique historique (email/phone/BCE).

### Testing
- `testing_agent_v3_fork` iteration_98 : backend 100% (5/5 pytest).
- E2E idempotent : import du PDF 2x -> 12 crees run1, 12 reused run2, DB=12 owners uniques.
- Multi-ACP consolidation : meme VCS sur ACP A + B -> single owner avec `copropriete_ids=[cid_a, cid_b]`.


## iter93bo - GDPR CRITIQUE : Fuite proprios orphelins dans wizard create ACP (Feb 2026 - DONE)

### Contexte
Utilisateur (screenshot) : "erreur GDPR - Liste de proprietaires orphelins mentionnes directement sans que le document ne soit charge c'est une erreur critique". Dans le wizard "Nouvelle ACP" > etape 2/5 "IMPORTER LA LISTE DES PROPRIETAIRES", **11 proprietaires reels s'affichaient avec nom+code+email AVANT tout upload**. Chinese Wall breach + violation GDPR.

### Root cause
`CoproprietesPage.js` (6 endroits) fetchait `GET /api/owners?unassigned_only=true` en mode CREATE et affichait les orphelins pre-emptivement dans le recap "N proprietaire(s) charge(s)".

### Fix
En mode CREATE :
- **DISPLAY (setOwners)** : uniquement `sessionOwners` (proprios ajoutes lors de cette session d'import).
- **MATCHING (fetch en interne)** : preserve dans `nextOwners` variable locale pour permettre au matching lot-owner par aux_code de fonctionner. Le fetch se fait toujours mais le resultat n'atteint PAS le state React d'affichage.
- Mode EDIT preserve le comportement historique (proprios lies visibles).

### Files touched
- 6 blocs de code dans `/app/frontend/src/pages/CoproprietesPage.js` (useEffect initial, refreshOwnersIfStale, retry-match btn, post-PDF import, post-homonymes confirm, import lots CSV).

### Testing
- `testing_agent_v3_fork` iteration_99 : frontend 100%, cross-check avec 56-68 orphelins en DB - ZERO PII visible dans le HTML avant upload.
- 0 regression sur le matching auto lot-owner (fetch conserve en interne).


## iter93bp - Parser cles de repartition : libelles dotted/hyphen multi-pages (Feb 2026 - DONE)

### Contexte
Utilisateur : "des cles de repartitions peuvent avoir plusieurs pages ce n'est pas normal adoucis le parsing pour que tout soit identifie". PDF Finlead "Cle de repartition" 2 pages avec libelles format `G.3-A.1.1 - APPARTEMENT C1004 ...` et `G34-P21 - PARKING EXT. ...`. Le parser detectait la cle "0001 Charges communes" mais retournait **0 lignes** ("Aucune ligne de detail extraite").

### Root cause
Le regex fallback texte dans `parse_distribution_keys_pdf` (pdf_utils.py ligne 1548) exigeait `[A-Z]\s+[A-Za-z0-9]...` : lettre + espace + alphanumerique. Ne matchait donc PAS :
- `G.3-A.1.1` (dots + tirets, sans espace initial)
- `G34-P21` (chiffres directement apres la lettre)

### Fix
Regex assoupli :
```
^(?P<libelle>[A-Z][A-Za-z0-9.\- ]*?)\s+[-–]\s+
```
Accepte dots, hyphens, whitespaces internes. Le non-greedy `*?` garantit qu'on s'arrete au premier ` - TYPE`.

### Testing
- Nouveau test `tests/test_iter93bp_dist_keys_dotted_labels.py` : PDF Finlead 2 pages -> 102 lignes extraites, total_quotities=100000.0. Validation des formats G.3-* (page 1) et G34-P* (page 2).
- Regression : `test_iter90gi_distribution_keys_total_quotities.py` OK, `test_iter93af_security_hardening.py` OK, `test_iter93bg_private_fee_owner_required.py` 6/6 OK.

