# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur donnees comptables/financieres.
Auth: JWT cookie + middleware global FastAPI.
Roles: `superadmin`, `syndic`, `gestionnaire`, `owner`.

## Security model
1. Auth middleware global sur /api/* (sauf /auth/login, /auth/register, /auth/refresh, /auth/logout).
2. RBAC path-based: admin only sur /api/admin/* + /api/users; owner restreint a /api/owner/*, /api/auth/*, GET /api/coproprietes(+sous-paths), GET /api/documents/.../download.
3. Chinese walls: `copropriete_id` propage automatiquement (frontend interceptor) et filtre cote backend.

## Implemented

### Iter45 (Feb 2026) - Cloture exercice : OD permanentes + Fix bilan boni/mali

#### Bouton "Cloturer l'exercice" avec OD permanentes
- Endpoint `POST /api/fiscal/years/{id}/close` etendu :
  - **OD-1 "Annulation provisions"** : Dr 7400 (somme appels) / Cr 4000XX par owner selon quotites
  - **OD-2 "Imputation charges"** : Dr 4000XX par owner / Cr 6XXX (sommes charges)
  - Apres ces 2 OD : classes 6/7 a zero, resultat reparti sur comptes 4000XX
  - Generation AN (a-nouveau) au 01/01/N+1 sur les nouveaux soldes
  - Flag `is_regularization: True` sur les OD pour les distinguer
- UI FiscalYearPage : confirmation explicite des 4 etapes (OD-1, OD-2, AN, verrouillage)

#### Fix critique : Bilan boni/mali correct
- Bug : apres cloture, le bilan "avant repartition" affichait un MALI alors que le budget
  excedait largement les charges (boni reel attendu).
- Cause : les OD de regularisation/extourne (refs `EXT-`, `OD-REG-`) etaient incluses
  dans le calcul du bilan "avant repartition", inversant le solde 7-6.
- Fix : exclusion des ecritures `is_regularization=True` OU `reference startsWith
  "OD-REG-"|"EXT-"` du bilan en mode `before_distribution` (= vue brute avant cloture).
  En mode `after_distribution`, ces ecritures SONT incluses (puisqu'elles realisent
  la repartition).
- Verifie : compte 499 affiche maintenant correctement **21 063,75 EUR CREDITEUR**
  au passif (boni a repartir). Bilan equilibre 31 267,70 EUR.



### Iter44 (Feb 2026) - Repartition boni/mali + Audit Sante dashboard

#### Formule de repartition documentee
- Mode "apres repartition" : `delta[i] = result_exercise * (quotite[i] / total_quotites)`
- Bilan equilibre garanti dans les 2 modes (test : 11 862 → 10 374,62 EUR apres repartition).
- TODO ulterieur : Option A stricte (`appels_recus[i] - charges_imputees[i]`) necessite
  que les appels de fonds soient inscrits sur les comptes 4000XX en double-entree
  permanente. A coupler avec un bouton "Cloturer l'exercice" qui materialise les OD.

#### Audit sante comptable - endpoint + UI dashboard
- **Endpoint** : `GET /api/dashboard/health-audit?copropriete_id=...&days_threshold=60`
  Retourne `{score, health_label, stats, anomalies}` :
  - factures impayees > 60j
  - doublons potentiels (meme fournisseur + montant + dates < 7j)
  - comptes tier orphelins (400/440 sans owner/supplier link)
  - ecritures non equilibrees
  - proprietaires en retard de paiement
- **UI Dashboard** : Carte "Sante comptable" avec score colore (vert/bleu/orange/rouge),
  5 mini-tiles (Fact>60j, Doublons, Orphelins, Desequilibres, Owners retard),
  details d'anomalies dans un `<details>` pliable.
  Verifie : ACP Test affiche Score 75/100 "Bon" avec 1 facture en retard + 5 owners
  en retard.



### Iter43 (Feb 2026) - AN au 01/01/N+1 + Audit Sante + nettoyage libelles

#### Fix : AN datees au 01/01/N+1 (etat post-paiement)
- Avant : AN au 31/12/N + soldes calcules sur la PERIODE (excluait paiements posterieurs)
- Apres : AN au **01/01/N+1** (lendemain de la cloture, date d'ouverture exercice suivant)
  + soldes calcules en CUMUL jusqu'a end_date (= reflete tous les paiements effectues).
  Les AN precedentes sont exclues pour eviter le double comptage.

#### Nouveau : module audit comptable (`/app/backend/health_audit.py`)
Detection automatique des anomalies + score de sante /100 :
- Factures impayees > X jours (60j par defaut)
- Doublons potentiels (meme fournisseur + meme montant + dates < 7j)
- Comptes tier orphelins (soldes 400/440 sans owner/supplier link)
- Ecritures non equilibrees (debit != credit)
- Coproprietaires en retard de paiement
Score = 100 - penalites par anomalie. Labels : Excellent/Bon/Moyen/Critique.

#### Nettoyage libelles "Fourn. -" dans le bilan
- Bilan : `_clean_account_name()` supprime le prefixe "Fourn. - " des comptes 440xxx
  (contexte rubrique "VI.B Fournisseurs" suffit a identifier).
- Generation auto : `tier_accounts.py` et `auto_entries.py` ne mettent plus de prefixe
  pour les NOUVEAUX comptes (juste le nom du fournisseur).



### Iter42 (Feb 2026) - Fix critique : exclusion ecritures AN du bilan

#### Bug : ecritures "A nouveau" (AN) doublaient les soldes
- Symptomes utilisateur :
  - Facture Finlead PAYEE apparaissait au bilan (30 EUR fantome)
  - "Fournisseurs divers" (44000004) sans aucune facture apparaissait avec 2000 EUR
- Cause : les ecritures AN (cloture d'exercice) sont datees au 31/12/N alors qu'elles
  representent l'OUVERTURE du 01/01/N+1. Elles dupliquent donc les soldes du bilan
  (deja calcules a partir des ecritures originales).
- Fix : dans `GET /api/reports/bilan`, exclusion `journal_type != 'AN'` dans les 2
  requetes (balances 1-5 + resultat 6/7). Le bilan utilise uniquement les ecritures
  ORIGINALES (factures, paiements, OD).
- Verifie : Finlead disparu, 44000004 disparu, bilan reste equilibre (29 237,70 EUR).



### Iter41 (Feb 2026) - Bilan allege (UI + PDF)

#### UI ReportsPage > Bilan
- Rubriques vides (total=0) **masquees** automatiquement.
- Badge equilibre/desequilibre en haut (vert/rouge).
- Paddings reduits (py-1 / py-1.5), fonts plus sobres (text-[11px] tracking-wide
  pour les rubriques, text-sm pour les comptes, font-mono text-[11px] text-slate-400
  pour les numeros de compte).
- Headers ACTIF/PASSIF plus discrets (py-2, text-sm), bordures 2px sur totals.
- Bouton Excel deplace en haut a droite, plus petit (variant=ghost).

#### PDF Bilan allege (`pdf_bilan.py`)
- Filtrage des rubriques vides cote PDF aussi (gain : moins de pages, meilleure lisibilite).
- Compte agrege owner : libelle sans numero (juste le nom).
- Couleurs grises plus claires (#94A3B8 pour les numeros au lieu de #64748B).
- Borders/grille allegees : 0.4 box + 0.2 lignes legeres.
- Paddings reduits (1pt sur les lignes detail, 3pt sur les rubriques).
- Header/Total : 5pt padding (au lieu de 6pt).
- Resultat : PDF plus compact (~4KB stable).



### Iter40 (Feb 2026) - Bilan equilibre garanti

#### Fix critique : Bilan toujours equilibre quel que soit le filtre
- Avant : `result_exercise` calcule sur la PERIODE [fy.start_date → date_to]
  alors que les balances classes 1-5 sont **cumulees** jusqu'a date_to.
  -> Desequilibre si charges/produits anterieurs a fy.start_date impactent le bilan.
- Apres : `result_exercise` = TOUTES les ecritures 6/7 jusqu'a date_to (sans start_date).
  Mathematiquement equivalent au solde net des classes 1-5 (par double-entree).
  -> Bilan TOUJOURS equilibre, meme avec filtre exercice fiscal.
- Verifie sur 3 scenarios : sans filtre, avec exercice, mode "apres repartition" -
  Ecart = 0.00 EUR systematiquement.



### Iter39 (Feb 2026) - Bilan : fusion proprietaires + Migration 550000 -> 55143100

#### Fusion comptes proprietaires dans le bilan
- Avant : ligne separee pour chaque compte 400 (provisions) et 401 (reserve) avec libelle technique
  "Prov. charges - Dubois" + "Fonds reserve - Dubois" (illisible).
- Apres : **1 seule ligne par proprietaire** avec son nom + solde total agrege
  (provisions + reserve). Le numero de compte est masque pour les lignes aggregees.
- Implementation : map owner_id -> {owner_name, debit_total, credit_total} via
  `owners.tier_accounts[copropriete_id].{provisions,reserve}`, puis injection dans
  `balances` avec `is_owner_aggregated=True`.

#### Bug fix : compte banque fallback 550000 -> PCMN reel
- `generate_bank_entry` (auto_entries.py) recuperait l'IBAN sur `txn.account_number`
  mais celui-ci etait vide sur de nombreuses txn (l'IBAN est sur le STATEMENT parent).
  Resultat : fallback `550000` au lieu du compte PCMN configure (ex 55143100).
- Fix : si `txn.account_number` vide, charge le statement via `txn.statement_id` et lit
  `stmt.account_number || stmt.iban`.
- **Nouveau endpoint admin** : `POST /api/banking/migrate-fallback-bank-account/{copropriete_id}`
  qui remappe en lot les lignes legacy `550000` -> compte PCMN configure (compte par defaut
  ou 1er disponible). Permet de nettoyer les ecritures historiques en 1 clic.



### Iter38 (Feb 2026) - Bilan avant/apres repartition + Layout PDF + Split 400/440

#### Bilan PCMN : compte de regularisation 499 (boni/mali)
- Endpoint `GET /api/reports/bilan` accepte desormais `view_mode` :
  - **`before_distribution`** (defaut) : resultat de l'exercice place sur compte **499** :
    - Boni (benefice) → CREDITEUR au Passif "VII. Comptes de regularisation (boni)"
    - Mali (perte) → DEBITEUR a l'Actif "VIII. Comptes de regularisation (mali)"
  - **`after_distribution`** : le 499 est reparti sur les comptes 4000XX provisions
    des proprietaires au prorata des quotites de lots. Le 499 est neutralise (= 0).
- Bilan toujours equilibre par construction (Actif = Passif = sum classe 1-5 + resultat).

#### Split 400 (coproprietaires) / 440 (fournisseurs)
- Avant : melange dans "V. Creances" (actif) et "VI. Dettes a un an" (passif)
- Apres :
  - Actif : V.A Coproprietaires debiteurs (400), V.B Fournisseurs acomptes (440 D), V.C Autres
  - Passif : VI.A Coproprietaires crediteurs (400), VI.B Fournisseurs (440), VI.C Autres dettes
- Lisibilite syndic : voir d'un coup d'oeil qui doit / qui est du.

#### Fix layout PDF Bilan
- `colWidths` inner table reduits 88/35mm → 58/32mm pour eviter le debordement
  du texte sur la colonne montant (superposition observee).
- Largeurs cohrentes avec `side_by_side` table (93/93mm).

#### Fix Frontend "impossible de recharger le bilan"
- Bug `useState(() => api.get(...))` au lieu de `useEffect(() => ..., [])` → exercices
  jamais charges, selecteur vide.
- Ajout du toggle UI "Avant repartition / Apres repartition" (data-testid='bilan-view-mode')
  qui envoie le `view_mode` au backend.



### Iter37 (Feb 2026) - UX + PDF non-comptable + Lettrage manuel + Dates EU + Solde cumulatif tier

#### PDFs (lisibilite non-comptable)
- **PDF Decompte annuel** entierement refondu (`/app/backend/pdf_decompte.py`) :
  carte recap (charges/appels/paiements/solde colore), detail charges par cle + par nature,
  paragraphs wrap (anti-superposition), bloc paiement avec IBAN+VCS, no jargon comptable.
- **PDF Situation de compte** : label "Total facture pendant la periode" -> "Total des appels"
  + Paragraph wrap dans la colonne "Operation" pour eviter le debordement long-libelle.
- **NOUVEAU PDF Balance des Tiers** (`/app/backend/pdf_balance_tiers.py`) : synthese
  proprietaires + fournisseurs en A4 paysage, totaux debiteurs/crediteurs en cards,
  endpoint `GET /api/reports/balance-tiers/pdf?copropriete_id=...` (400 si absent).
- **NOUVEAU PDF Bilan par exercice** (`/app/backend/pdf_bilan.py`) : structure
  "Bilan apres repartition" (ACTIF/PASSIF cote a cote, rubriques colorees, equilibre OK/KO),
  endpoint `GET /api/reports/bilan/pdf?copropriete_id=...&fiscal_year_id=...&date_to=...`.
  Selecteur d'exercice + bouton "PDF Bilan" dans `ReportsPage > Bilan`.

#### Comptabilite
- **Frais privatifs** : separation de l'ecriture en 2 (au lieu d'une seule doublee) :
  1. AC `Dr 643 / Cr 44000XXX fournisseur` (facture)
  2. OD `Dr 40000XXX proprietaire / Cr 643` (refacturation)
  Net 643 = 0, fournisseur credite, proprietaire debite, source_id partage pour cleanup auto.
- **Verrou exercice cloture** : `GET /api/reports/decompte/pdf/{owner_id}` ET
  `GET /api/owner/decompte/pdf` retournent 400 si `fiscal_year.status != "closed"`.
  Message clair en francais + bandeau warning dans `ReportsPage > Decomptes`.

#### Bug fix critique : Solde du compte tier CUMULATIF
- Avant : balance fournisseurs/proprietaires calculee uniquement sur la periode filtree
  (ex: Clean & Co montrait `-1000.00` au lieu du vrai solde `-237.70` car une facture
  d'aout 2023 etait exclue par le filtre 2026).
- Apres : `GET /api/reports/balance-tiers/suppliers` et `/owners` calculent maintenant :
  - `total_invoiced` / `total_paid` = mouvements sur la PERIODE
  - `balance` / `account_debit` / `account_credit` = solde CUMULATIF du compte tier
    (toutes ecritures jusqu'a `end_date`).
  Nouvelles colonnes "D. compte" et "C. compte" affichees dans BalanceTiersPage Fournisseurs.

#### UX banking
- **`CounterpartySearchSelect`** : nouveau composant autocomplete proprietaires +
  fournisseurs (dropdown au focus, filtre client instantane, sections coloriees bleu/orange).
  Utilise dans ajout ET edition des lignes d'extrait.
- **Libelle auto encaissement** : selection proprietaire genere automatiquement
  `"VCS - Votre paiement au JJ/MM/AAAA"` dans la communication si elle est vide.
- **Colonnes contrepartie wrappees** (break-word) - plus de troncature.
- **Lettrage manuel par facture** : onglet "Factures" du dialog lettrage refondu :
  - Affiche TOUTES les factures (pas seulement impayees), pre-filtre par nom contrepartie
  - Border ROUGE + badge "A PAYER" pour non lettrees / VERT + "PAYE" pour lettrees
  - Bouton "Lettrer" (bleu) pour rouges / "Delettrer" (orange) pour vertes
  - **`POST /api/banking/unlettrage-by-invoice/{invoice_id}`** : nouvel endpoint qui
    delettre toutes les txns liees a une facture + remet la facture en `unpaid`.
- **Synchronisation status facture** : `POST /api/banking/lettrage` (match_type=invoice)
  passe la facture en `paid` + `paid_at` + `paid_by_transaction_id`. Le delettrage
  inverse correctement.

#### UX budget
- Dialog "Modifier le budget" refait : largeur adaptive `w-[95vw] max-w-5xl`,
  grille `grid-cols-12` lisible (6 compte / 3 cle / 2 montant / 1 action),
  integration `AccountSearchSelect` (recherche par numero OU libelle).

#### Dates au format europeen JJ/MM/AAAA partout
- **Utilitaire `/app/frontend/src/lib/dateFmt.js`** (`fmtDate`, `fmtDateTime`).
- **14 fichiers** modifies par script `/tmp/apply_date_fmt.py` :
  BalanceTiersPage, FiscalYearPage, DashboardPage, ExpensesPage, LotsPage,
  InvoicesPage, BankingPage, FundCallsPage, GrandLivrePage, MetersPage,
  ReportsPage, RemindersPage, JournalsPage, BudgetWizard.
- Inputs `type="date"` preserves (value brute YYYY-MM-DD).



### Iter36 (Feb 2026) - Nettoyage complet base + Verrouillage strict chinese walls (regle non-modifiable)

#### Etape 1 - Nettoyage des donnees de test
- **2 ACPs de test** supprimees (TEST_Iter7_ACP_A_1779082500 + TEST_Iter7_ACP_B_1779082500) avec **cascade complete** sur 12 collections (invoices, journal_entries, fund_calls, bank_transactions, bank_statements, lots, budgets, distribution_keys, fiscal_years, pcmn_accounts, expense_categories, tenants).
- Donnees de test residuelles dans ACP Demo nettoyees :
  - Facture TEST-PRIV-001 supprimee
  - journal_entries avec ref TEST_PAYER_*, TEST_ITER26_LEGACY, et toute ref contenant "TEST" supprimees
  - bank_statement TEST_ITER22_* supprime
  - Fournisseur SRL Test + factures associees supprimes
- **9 comptes utilisateurs de test** supprimes (`test_iter1[67]_owner_*`)
- Audit final : coproprietes 11, users 4 (admin + 3 reels).

#### Etape 2 - Verrouillage strict chinese walls
- **Nouvelle fonction `_require_copro(copropriete_id, request)`** dans `reports.py` : resout copropriete_id depuis param OU header X-Copropriete-Id, raise 400 si absent. Centralise la regle pour TOUS les endpoints.
- **Endpoints verrouilles** (copropriete_id obligatoire, 400 sinon) :
  - `GET /api/reports/grand-livre`
  - `GET /api/reports/trial-balance`
  - `GET /api/reports/bilan`
  - `GET /api/reports/resultat`
  - `GET /api/reports/decompte` (+ verification fiscal_year_id match ACP)
  - `GET /api/reports/balance-tiers/owners/{id}` (+ filtre periode optionnel)
  - `GET /api/reports/balance-tiers/suppliers` (+ filtre periode optionnel)
  - `GET /api/fiscal/expenses`
- Verification cross-ACP : si `fiscal_year_id` fourni mais son `copropriete_id` ne matche pas la requete -> 400.
- **Teste** : 7/7 endpoints scope-less retournent 400 + 4/4 avec scope retournent 200.

### Iter35 (Feb 2026) - Chinese walls verrouillage strict TOUS endpoints critiques + Reference interne facture + Filtres invoices

#### Verrouillage chinese walls strict (regle non-modifiable)
- **`GET /api/banking/statements`** : sans `copropriete_id` (param ou header X-Copropriete-Id) -> **liste vide** au lieu de retourner tous les ACPs.
- **`POST /api/banking/statements`** : `copropriete_id` **obligatoire** + verification que l'IBAN appartient bien aux `bank_accounts` de l'ACP (refuse sinon).
- **`GET /api/banking/transactions`** : si pas de `statement_id`, exige `copropriete_id` (sinon liste vide). Si statement_id fourni, verification que le statement appartient bien a l'ACP de la requete.
- **`GET /api/invoices`** : `copropriete_id` obligatoire (liste vide sinon).
- **`GET /api/accounting/entries`** : `copropriete_id` obligatoire (liste vide sinon).
- **`POST /api/accounting/entries`** : `copropriete_id` obligatoire ET tous les comptes utilises (`lines.account_number`) doivent exister dans le PCMN de cette ACP. Refus 400 sinon ("Chaque ACP dispose de son propre plan comptable - aucun melange autorise").
- **`GET /api/reports/decompte/pdf/{owner_id}`** : `copropriete_id` obligatoire (avant : fallback sur le premier lot du proprietaire, ce qui pouvait selectionner une AUTRE ACP). Si `fiscal_year_id` est fourni, son `copropriete_id` DOIT matcher.
- **`GET /api/owners`** scope-able : `?copropriete_id=X` -> filtre via `lots.owner_id` ET `lots.owner_ids[]`.

#### Migration historique
- **3 journal_entries orphelines** (sans copropriete_id) supprimees.
- **1 bank_transaction + 1 bank_statement** orphelins supprimes.
- **3 ecritures avec compte master 440000** remappees vers un compte 44000XXX dedie a leur ACP (avec creation auto du compte pcmn si absent) + creation auto des fiches fournisseur manquantes pour eviter le master a l'avenir.
- **`auto_entries.generate_purchase_entry`** : ne fait plus jamais fallback sur 440000. Si pas de supplier, cree un compte 44000XXX "Fournisseur divers" dedie a l'ACP.

#### Reference interne facture (FA-AAAA-NNNN)
- **Auto-generee** a la creation : prefixe FA + annee de la date + sequence 4 chiffres, **scopee par ACP** (chaque ACP a sa propre numerotation). Migration appliquee sur 7 factures existantes.
- Affichee en premiere colonne du tableau (font monospace bleu) et dans le dialog d'edition (read-only). Le champ "Numero" devient "N facture fournisseur" pour eviter la confusion.

#### Filtres factures
- Bandeau filtres complet : **dates (du/au), fournisseur (Select avec liste deduplique des fournisseurs effectivement presents), reference/description (input texte), statut (Select)**. Reset disponible. Filtre applique cote client sur la liste deja scopee ACP.
- Le backend `GET /api/invoices` accepte aussi `?supplier=&reference=&start_date=&end_date=&min_amount=&max_amount=` pour les exports/scripts.

#### Filtres balance tiers (recap iter34)
- `?start_date=&end_date=` sur `GET /api/reports/balance-tiers/owners` + FilterBar dans BalanceTiersPage avec 7 presets.

### Iter34 (Feb 2026) - Filtres période + recherche sur Balance Tiers + scope ACP OwnersPage + nature de depense inline

#### Backend
- **`GET /api/reports/balance-tiers/owners`** accepte desormais `?start_date=&end_date=` (ISO YYYY-MM-DD) et filtre les `journal_entries` ET `bank_transactions` sur cette periode -> soldes calcules sur la fenetre demandee uniquement.
- **`GET /api/owners`** : si `?copropriete_id=X` (ou header `X-Copropriete-Id`), retourne uniquement les proprietaires ayant au moins un lot dans X (cherche dans `lots.owner_id` ET `lots.owner_ids[]`). Sans param : tous les owners (cas global).

#### Frontend - BalanceTiersPage
- **FilterBar reutilisable** : dates Du/Au + recherche texte (nom/VCS/BCE) + 7 presets rapides (Auj, Ce mois, Mois -1, Trim, Annee, N-1, Tout) + bouton Reset. Memorise dans `localStorage.balance-tiers-filters`.
- Banner ambre indiquant que les soldes sont calcules sur la periode.
- Filtre texte applique cote client sur la liste owners ET suppliers.

#### Frontend - OwnersPage
- Charge `/owners?copropriete_id={selectedCopro}` -> n'affiche que les proprietaires de l'ACP active.
- Toggle "Afficher tous les proprietaires (toutes ACPs)" pour le mode global.
- Reagit aux changements d'ACP via `window.addEventListener('storage')` et evenement custom `copropriete-changed`.
- Badge count "(ACP active)" si scope ACP applique.

#### Frontend - InvoicesPage : nature de depense inline
- Option `+ Creer une nature de depense...` dans le `Select` -> ouvre un mini-dialog (nom + compte PCMN classe 6 via AccountSearchSelect + description) qui POST `/api/expense-categories` puis selectionne automatiquement la nouvelle nature et pre-remplit le compte de la ligne.
- Plus besoin de quitter la facture pour creer une nature manquante.

### Iter33 (Feb 2026) - Bug fix critique : frais privatif laissait le proprietaire a solde 0
- **Bug rapporte** : apres affectation d'un frais privatif (facture Finlead avec `is_private_fee=true`), le proprietaire apparaissait debiteur ET creditee du meme montant -> solde 0 au lieu de debiteur du montant des frais.
- **Cause** : dans `auto_entries.py` (ligne 100-104), la **4eme ligne** de l'ecriture AC (Cr 643 - contrepartie de l'imputation) recevait `third_party_id = owner_id`. La balance tiers et le PDF situation incluaient donc cette ligne dans les mouvements du proprietaire, annulant son debit.
- **Modele Finlead correct** (4 lignes) :
  1. Dr 643 (charge privative) / Cr 44000XXX (fournisseur) - `tpid=supplier`
  2. Dr 40000XXX (proprietaire) / Cr 643 (imputation) - **`tpid=owner` SEULEMENT sur le Dr 40000XXX**, pas sur le Cr 643
- **Fix** : retire `third_party_id=owner_id` de la 4eme ligne (Cr 643 imputation), garde seulement le `third_party_name` informatif.
- **Migration historique** : 2 ecritures auto-generees retroactivement corrigees (lignes 643 Cr ont leur tpid passe a `None`).
- **Verifie** : Dubois Jean sur ACP Test passe de balance 0 a **balance 30.00 EUR debiteur** apres migration. Le PDF n'affichera plus que la ligne 40000003 Dr 30€ (et solde 30€).

### Iter32 (Feb 2026) - Cles de repartition : total + controle de coherence
- **Liste des cles** (`InvoicesPage.js`) : ajout de 2 nouvelles colonnes
  - **"Total quotites"** : somme live des quote-parts (e.g. 1000.00)
  - **"Coherence"** : badge code couleur :
    - `OK` (vert) si total = 1 / 100 / 1000 / 10000 (rounds typiques copro belge)
    - `Custom` (bleu) si total > 0 mais non-rond (ex : releves d'eau)
    - `Lots a 0` (ambre) si au moins un lot a une quote-part nulle
- **Dialog edit** : zone repartition entierement repensee
  - **3 boutons d'aide** : "Reprendre tantiemes lots" (lit `lots.quotity` par numero), "Repartir egalement (=1000)" (1000/n par lot), "Normaliser /1000" (rescale au total 1000)
  - **Tableau** : colonne supplementaire "% du total" calculee live, surlignage ambre des lots a 0
  - **Pied de tableau** : ligne TOTAL en bold (toujours visible, sticky header)
  - **Badge de coherence** sous le tableau : meme logique que la liste
- **Fix bug clé de répartition** : `lot_number` désormais lu sur `l.number` (et non `l.lot_number`) pour POST `/api/distribution-keys`. Toutes les anciennes clés s'affichent correctement, les nouvelles se créent en HTTP 200.

### Iter31 (Feb 2026) - Bug fix création de clé de répartition (lot_number)
- `lots.X.lot_number: Field required` -> remplacement de `l.lot_number` par `l.number` dans `openCreateKey` (InvoicesPage ligne 204). Le dialog affiche maintenant "Lot A-101" au lieu de "Lot" anonyme.

### Iter30 (Feb 2026) - Annulation mutation + PDF Situation de compte + Robustesse erreurs Pydantic

#### Annulation de mutation
- **`DELETE /api/lots/{lot_id}/mutate/{mutation_id}`** (`routes/properties.py`) : annule la **derniere** mutation d'un lot, restaure `lot.owner_id`, supprime l'OD comptable liee, retire l'entree d'historique. Refuse si la mutation visee n'est pas la plus recente.
- **Accepte `mutation_id="last"`** comme raccourci pour les anciennes mutations creees avant l'ajout du champ `id` (retrocompat).
- **UI** : bouton rouge **"Annuler"** uniquement sur la mutation la plus recente dans l'historique du dialog Mutation (`LotsPage.js`), avec confirmation. Mention "Seule la mutation la plus recente peut etre annulee."
- Le champ "Prix de vente" a ete retire du formulaire et de l'affichage historique sur demande utilisateur.

#### PDF Situation de compte
- **Nouveau module `/app/backend/pdf_situation_compte.py`** : genere un PDF A4 professionnel envoyable par email/postal :
  - En-tete syndic + ACP en 2 colonnes encadrees
  - Bloc destinataire (nom, adresse, VCS, comptes provisions/reserve)
  - Tableau des mouvements (date, journal, ref, libelle, cpt, debit, credit, solde progressif)
  - Ligne TOTAUX + solde final code couleur (rouge debiteur / vert crediteur)
  - Bloc "Coordonnees de paiement" avec IBAN, BIC, communication structuree (VCS) si solde debiteur
- **`GET /api/reports/situation-compte/{owner_id}/pdf`** : parametres `copropriete_id` (required), `start_date` / `end_date` (optionnels) pour la periode. Inclut un solde a nouveau si une date de debut est donnee. Retourne `application/pdf` en attachment (`situation-{owner_name}-{date}.pdf`).
- **UI BalanceTiersPage** : bouton FileText `[data-testid="pdf-situation-{owner_id}"]` a cote de l'oeil "Detail" pour chaque proprietaire -> ouvre le PDF dans un nouvel onglet.

#### Robustesse erreurs Pydantic
- **Bug rapporte** : `Uncaught runtime error: Objects are not valid as a React child (found: object with keys {type, loc, msg, input, url})` lors de la creation d'une cle de repartition avec donnees incompletes.
- **Cause** : FastAPI renvoie 422 avec `detail` = ARRAY d'objets Pydantic. Le code `toast.error(err.response.data.detail || 'Erreur')` (present dans **>20 fichiers**) passe l'array a sonner qui essaie de le render -> crash React.
- **Fix dans `lib/api.js`** : nouvel intercepteur de reponse axios qui **normalise systematiquement** `error.response.data.detail` en string formattee. Si array Pydantic -> `"name: Field required | due_date: Must be ISO format"`. Si objet -> `JSON.stringify`. Si string -> inchange.
- Avantage : zero modification des >20 pages qui utilisent ce pattern, le fix est global.

### Iter29 (Feb 2026) - Bug fix mutation : solde crediteur du vendeur enfin visible
- **Bug rapporte** : apres mutation d'un lot, le vendeur disparaissait de la Balance Tiers proprietaires bien que son compte 40000XXX soit crediteur du transfert (1567.69 EUR dans le test Demo).
- **Cause** : `routes/reports.py::balance_tiers_owners` filtrait les owners par `db.lots` -> un proprietaire sans lot dans l'ACP n'apparaissait plus, alors qu'il garde un solde residuel apres la vente.
- **Fix** :
  - Etend la liste des owners scannes avec **tous les third_party_id** trouves dans `journal_entries` de l'ACP (via `db.journal_entries.distinct("lines.third_party_id", ...)`)
  - Ajoute un flag `is_former_owner` (true = ne possede plus aucun lot dans l'ACP)
  - Filtre les ex-proprietaires a solde nul + 0 mouvement pour eviter le bruit
  - UI BalanceTiersPage : badge ambre **"Ex-prop."** a cote du nom
- Verifie : apres mutation Martin -> Dubois, Martin apparait avec balance **-195.64 EUR (crediteur)** (provisions 702.76 Dr - 1567.69 Cr = -864.93 + reserve 669.29 inchangee). Badge Ex-prop. affiche. Dubois cumule bien sa propre balance + 1567.69 debit.

### Iter28 (Feb 2026) - Mutation lot (vente) + Owner picker inline create
- **Lots : selecteur proprietaire avec creation inline** (`LotsPage.js`) : nouveau composant `OwnerPicker` reutilisable. Recherche par nom/email/VCS, ajout en tags, bouton "Creer ce proprietaire" qui ouvre un mini-form (nom/prenom/email/tel) quand aucun resultat -> `POST /api/owners` + selection automatique. Utilise sur creation/edition de lot ET sur mutation.
- **Endpoint mutation** `POST /api/lots/{lot_id}/mutate` + `POST /api/lots/{lot_id}/mutate-preview` (`routes/properties.py`) :
  - Input: `{new_owner_id, sale_date (ISO YYYY-MM-DD), sale_price?, note?}`
  - **Quote-part fonds de roulement** = solde compte 100 (passif ACP) x (lot.quotity / total quotity ACP). Calcule via `db.journal_entries.aggregate` pivot sur compte 100.
  - **Prorata provisions** : pour chaque fund_call de type "provisions" deja emis dont la periode `[date, due_date]` chevauche `sale_date`, calcule `montant_vendeur x (jours_apres_vente / total_jours)`. Le vendeur est credite du prorata, l'acquereur debite. Pas d'impact sur les appels futurs (entierement a l'acquereur via l'attribution du lot).
  - **Generation OD** (`MUT-{lot_number}`, manually_edited=true) : Dr compte_acquereur / Cr compte_vendeur du `total_transfer = roulement_quota + prorata_provisions`.
  - **Mise a jour** `lot.owner_id` + push dans `lot.mutations[]` : `{date, old_owner_id, new_owner_id, roulement_quota, prorata_provisions, total_transfer, sale_price, note, journal_entry_id, prorata_details, created_at}`
  - **Fonds de reserve PAS impacte** (conforme exigence metier).
- **UI Mutation** (`MutationDialog` dans LotsPage) : bouton icone ArrowRightLeft sur chaque lot (desactive si pas d'owner), dialog avec :
  - Acquereur (OwnerPicker + create inline)
  - Date vente + prix (info) + note
  - Apercu live des calculs (auto-refresh sur changement) avec detail prorata expandable
  - Historique des mutations precedentes
- Verifie sur ACP Demo : mutation lot A001 (Martin -> Dubois, 15/04/2026) -> Roulement 1505.91 EUR + Prorata 61.78 EUR (sur appel Trimestriel 2/4, 12j/26j sur Martin 133.86 EUR) = **Total 1567.69 EUR**. OD MUT-A001 generee, equilibree.

### Iter27 (Feb 2026) - Fix critique chinese walls : Dashboard scope ACP
- **Bug** : `GET /api/dashboard/stats` agregait owners/lots/factures/encaissements/recent_entries **toutes ACPs confondues**. Apres creation d'une nouvelle ACP, le tableau de bord affichait encore les factures/proprietaires/ecritures des autres ACPs -> fuite massive cross-tenant.
- **Fix `server.py` `dashboard_stats`** :
  - Accepte `?copropriete_id=...` ET lit aussi le header `X-Copropriete-Id` (envoye par l'interceptor frontend)
  - Filtre OBLIGATOIRE `copropriete_id` sur invoices/lots/tenants/journal_entries
  - Owners = uniquement les owners qui ont au moins un lot dans cette ACP (via `db.lots.distinct('owner_id', {copropriete_id: ...})`)
  - **403** si l'utilisateur n'est pas membre de l'ACP (admin/superadmin bypass)
  - Sans ACP scope: counters a 0 (pas de leak) + `coproprietes_count` borne aux ACPs accessibles a l'utilisateur
  - Ajoute `coproprietes_count` (visible utilisateur) et `copropriete_id` dans la reponse
- **Verifie** : ACP Demo Tilleuls -> 8 owners/8 lots/3 invoices/594.60 EUR. Nouvelle ACP "Test" -> 0/5/0/0 + 0 ecriture (au lieu des 5 fuyantes auparavant).

### Iter26 (Feb 2026) - Fix comptable critique : Fonds de reserve en classe 1 (160), pas classe 7 (701000)
- **Probleme** : Les appels de fonds de reserve creditaient le compte **701000** (classe 7 - produits), ce qui traitait l'augmentation de reserve comme un revenu. **FAUX** en compta belge copropriete : un fonds de reserve est une augmentation de PASSIF (classe 1), pas un produit. Idem fonds de roulement (deja correct sur 100).
- **Fix `auto_entries.generate_sale_entry`** (lignes 240-252) :
  - Provisions classiques : Dr 40000XXX / Cr 700000 (classe 7) - inchange
  - Fonds de reserve : Dr 40010XXX / **Cr 160** (Fonds de reserve, classe 1) - etait 701000
  - Fonds de roulement : Dr 40000XXX / **Cr 100** (Fonds de roulement, classe 1) - inchange
- **Fix `routes/fund_calls.py` ligne 199** : endpoint legacy `generate-entries` avec `call_type=reserve` -> account_map = ('401000', '160').
- **Endpoint de migration `POST /api/coproprietes/{id}/migrate-reserve-to-classe1`** (admin/syndic) : remappe les lignes auto-generated 701000 -> 160 dans les VE source_type=fund_call non manually_edited. Idempotent. Owner -> 403.
- **Bilan corrige** (`routes/reports.py`) : classe 16 (Fonds de reserve copropriete) range en section **II. Reserves** du passif, plus en VI. Dettes court. Demo Tilleuls : Capital 14999.99 (100), **Reserves 5234.08 (160)**, dettes court 187.86.
- Migration ACP Demo : 4 entries VE auto reserve remappees (4 lignes changees). Nouveaux fund_calls genres deja en 160.
- Resultat : **9/9 PASS** tests iter26 + 29/31 regression iter25/iter20/iter16 (2 echecs pre-existants).

### Iter25 (Feb 2026) - Finalisation Bilan PCMN bancaire + regenerate-bank-entries
- **Endpoint `POST /api/coproprietes/{id}/regenerate-bank-entries`** (`routes/coproprietes.py`) : regenere les ecritures FI auto pour TOUTES les transactions matched d'une ACP. Utile apres correction d'un IBAN / pcmn_number dans `copro.bank_accounts`. Reserve admin/syndic/gestionnaire (owner -> 403). Reponse `{status, copropriete, transactions_processed, regenerated, errors}`.
- **Migration historique** : script bash ad-hoc pour assigner un `account_number` (IBAN) par defaut aux bank_transactions historiques qui avaient `account_number=''`, puis regeneration des FI -> les comptes 55103400 (compte courant) et 55076900 / 551079 (epargne) apparaissent enfin correctement au Bilan a l'actif (avec leurs soldes reels), au lieu de tomber sur le 550000 generique.
- **Verification end-to-end** : pipeline complet teste -> POST `/api/banking/transactions` (IBAN herite de stmt.account_number) -> POST `/api/banking/lettrage` -> `generate_bank_entry` resout IBAN -> pcmn via `copro.bank_accounts` -> FI entry sur PCMN reel -> GET `/api/reports/bilan` expose le PCMN reel.
- Resultat : **5/5 PASS** nouveaux tests + 44/46 regression iter21-iter24 (2 echecs = drift Alex BENOIT, pre-existing).

### Iter24 (Feb 2026) - 3 bugs banking : edition statement + signe debit/credit + IBAN add-lines
- **Bug fix #1 - PUT /api/banking/statements/{id}** : nouvel endpoint pour modifier un extrait apres creation. Bouton "Modifier" frontend disponible sur les extraits draft (corrige typos solde initial/final).
- **Bug fix #2 - Signe debit/credit** : POST + PUT /api/banking/transactions normalisent maintenant le `amount` selon `transaction_type` (debit -> amount negatif, credit -> amount positif). Avant : un debit s'affichait en +montant dans la table car le code frontend testait `amount >= 0` sans regarder le type. Migration auto effectuee sur les 8 txns existants (Alex BENOIT 300 -> -300).
- **Bug fix #3 - add-lines IBAN** : `POST /api/banking/statements/{id}/add-lines` heritait `account_number=''` au lieu de l'IBAN du statement parent. Resultat : les ecritures FI auto-generees avaient un IBAN vide et tombaient sur le compte 550000 generique. Fix : utilise maintenant `stmt.account_number`.
- **Frontend** : bouton "Modifier" (Pencil) cote `Comptabiliser` sur la card du statement selectionne en draft. Dialog reutilise le state existant, distingue creation/edition via `editingStmtId`.
- Resultat : **11/11 PASS** + 33/35 regression (les 2 echecs = data drift Alex BENOIT 614.60 au lieu de hardcoded 314.60, pas un bug).

### Iter23 (Feb 2026) - Banking statements : dropdown PCMN + auto-fill + workflow draft/posted
- **Bug fix critique** (`BankingPage.js`) : `localStorage.getItem('copropriete_id')` -> `useAuth().selectedCopro` (la cle reelle). Resultat : les `bank_accounts` de l'ACP sont enfin charges -> dropdown plus jamais vide.
- **Dropdown extraits enrichi** : chaque compte bancaire affiche IBAN + label + badge **PCMN** (ex `PCMN 55103400`) + badge `defaut`. Permet de savoir exactement vers quel compte comptable l'extrait sera impute.
- **Auto-fill solde ouverture** : a la selection du compte, le `opening_balance` est rempli automatiquement avec le `closing_balance` du dernier extrait du **meme IBAN**. Banner d'info montre "Dernier solde (date) : XXX,XX" ou "Aucun extrait precedent".
- **Workflow draft / posted** :
  - POST `/api/banking/statements` cree avec `status='draft'`
  - GET `/api/banking/statements/{id}` retourne `computed_closing` (= opening + sum(credit) - sum(debit)), `balance_diff` (= computed - closing), `is_balanced` (boolean, tolerance 0.01)
  - POST `/api/banking/statements/{id}/post` : refuse 400 si non equilibre avec message clair `"Solde ouverture (1000.00) + mouvements (100.00) = 1100.00, mais solde fermeture saisi = 1500.00. Difference : -400.00"`. Succes -> status='posted' + posted_at.
  - POST `/api/banking/statements/{id}/unpost` : repasse en draft pour correction
  - PUT `/api/banking/statements/{id}` : modifier opening/closing/dates
- **UI bandeau equilibre** : carte couleur (vert si OK, ambre si desequilibre) qui montre en temps reel `opening + mvts = computed vs. closing saisi` avec la diff. Bouton "Comptabiliser" desactive tant que la balance n'est pas equilibree.
- Resultat : **17/17 tests PASS** + 40/40 regression iter22.

### Iter22 (Feb 2026) - Synchronisation suppression + ecritures orphelines + balance suppliers depuis grand livre
- **Bug fix DELETE statement** (`routes/banking.py`) : `DELETE /api/banking/statements/{id}` supprime maintenant aussi les auto-entries FI des transactions du statement (via `_delete_auto_entries`). Avant : les ecritures FI orphelines polluaient le bilan apres suppression d'un extrait.
- **Endpoint nettoyage** (`routes/coproprietes.py`) : `POST /api/coproprietes/{id}/cleanup-orphan-entries` detecte et supprime les ecritures auto-generees dont la source n'existe plus (invoice/fund_call/bank_txn). Idempotent. Retourne stats detaillees par type.
- **Bouton frontend** (`CoproprietesPage.js`) : icone baguette magique bleue (Wand2) a cote du bouton reset, declenche le cleanup avec confirmation.
- **Balance tiers fournisseurs refactor** (`routes/reports.py`) : 
  - GET `/api/reports/balance-tiers/suppliers` et `/{id}` calculent maintenant depuis `journal_entries` (44000XXX) au lieu de fund_calls/bank_txns matchees
  - Resultat : les OD manuelles sur compte fournisseur sont prises en compte, balance synchronisee avec le grand livre
  - Affichage des fournisseurs orphelins (nom dans facture mais pas de fiche) preserve
  - Exemple validation : Alex BENOIT 314.60 crediteur sans paiement, balance retombe a 214.60 apres OD Dr 44000012 100.00

### Iter21 (Feb 2026) - Bug fix : resolution IBAN -> compte PCMN bancaire dans FI auto-entry
- **Probleme** : `generate_bank_entry()` utilisait directement `txn['account_number']` (qui est un IBAN ex `BE68539007547034`) comme numero de compte PCMN dans l'ecriture FI. Resultat : les ecritures bancaires utilisaient un IBAN au lieu d'un vrai compte PCMN classe 5, ce qui faussait le bilan (le compte 550 generique apparaissait au passif au lieu des comptes specifiques 55103400 / 55076900).
- **Fix** (`auto_entries.py`) : avant de generer l'ecriture, on resout l'IBAN -> `pcmn_number` en consultant `copro.bank_accounts`. Si l'IBAN est inconnu, fallback sur 550000. Le `account_name` utilise aussi le `label` du bank_account (ex "Compte courant").
- Resultat : 63/63 tests PASS (8 nouveaux + 55 regression). Le bilan affiche maintenant correctement les comptes bancaires specifiques avec leur solde reel a l'actif (classe 5).

### Iter20 (Feb 2026) - Bugfix balance tiers + Wizard 3 fonds independants + Recherche compte + BCE fournisseur
- **P0 Bug balance tiers refactor** (`routes/reports.py`):
  - Balance owners maintenant calculee depuis `journal_entries` (au lieu de fund_calls). Les **OD manuelles** sur les comptes tiers 40000XXX/40010XXX apparaissent enfin (bug Dubois corrige)
  - Separation explicite **provisions_balance** (40000) vs **reserve_balance** (40010) avec champs detailles (debit/credit) + total agrege
  - Champ `unmatched_paid` pour les paiements bancaires reconnus par VCS mais non encore lettres
  - UI BalanceTiersPage : nouvelles colonnes "Solde Prov." et "Solde Reserve"
  - Endpoint detail `/balance-tiers/owners/{id}` aggrege depuis journal_entries avec `journal_type` par ligne (visible OD/AC/VE/FI/A-Nouveau)
- **P1 Wizard 3 fonds independants** (`routes/fund_calls.py` + `BudgetWizard.js`):
  - `ReserveFund` et `RoulementFund` acceptent `frequency` (0=injection legacy, 1/2/3/4/6/12=serie propre) + `start_date` + `due_offset_days` propres
  - Si `frequency > 0` : helper `_generate_independent_series` cree N appels separes avec `call_type='reserve'` ou `'roulement'`
  - UI : selecteurs Frequence (Unique/Annuel/Semestriel/Quadrimestriel/Trimestriel/Bi-mensuel/Mensuel) + date debut + echeance dans chaque etape
  - Mode `0` preserve = compatibilite tests anterieurs (injection sur appel #1 provisions)
- **P2 Composant AccountSearchSelect** (`components/AccountSearchSelect.js`):
  - Selecteur de compte avec recherche par numero ET par nom (max 50 resultats)
  - Filtre par classe(s) PCMN configurable
  - Bouton clear, navigation clavier, dropdown avec autofocus
  - Integre dans InvoicesPage (compte PCMN classe 6) et JournalsPage (toutes classes pour OD)
- **P3 Extraction BCE fournisseur** (`routes/invoice_ai.py` + `routes/suppliers.py`):
  - Schema AI etendu : extraction du `bce_number` en plus du `vat_number`
  - Matching priorise BCE > VAT (normalises sur 10 chiffres) puis fallback nom fuzzy
  - Champs retour API : `bce_normalized`, `supplier_match_method` (bce|name|null), `supplier_suggest_create`
  - Si pas de match + nom present : banner ambree dans le dialog facture "Creer la fiche fournisseur" avec bouton one-click qui POST /api/suppliers
  - `SupplierInput` accepte `bce_number`, recherche fournisseurs cherche aussi par BCE

### Iter19 (Feb 2026) - Frais privatifs + Wizard Fonds de roulement
- **Frais privatifs sur facture** (`routes/invoices.py` + `auto_entries.py` + `pages/InvoicesPage.js`):
  - Nouveau champ `is_private_fee` + `private_fee_owner_id` sur InvoiceInput
  - Quand active : `account_number` force a 643, `distribution_key_id` ignore, `distribution_lines=[]`
  - UI : checkbox ambree "Frais privatif" + champ de recherche proprietaire (filtre par nom/email/VCS)
  - **Ecriture AC auto-generee 4 lignes** (modele Finlead) :
    - Dr 643 (Frais privatifs) montant / Cr 44000XXX (Fournisseur) montant
    - Dr 40000XXX (Proprietaire) montant / Cr 643 (Imputation) montant
    - Resultat : compte 643 net = 0, fournisseur credite, owner tiers debite
  - Validations : 400 si owner manquant, 404 si owner invalide
- **Wizard Budget - Etape 4 "Fonds de roulement"** (`components/BudgetWizard.js` + `routes/fund_calls.py` + `auto_entries.py`):
  - Nouvelle etape inseree entre "Fonds de reserve" et "Recapitulatif" (5 etapes au total)
  - Choix Mode : **Creation initiale** ou **Augmentation**
  - Champs : libelle, montant total, cle de repartition (Tantiemes par defaut)
  - Le montant est ajoute UNIQUEMENT au 1er appel (comme la reserve)
  - **Ecriture VE auto-generee** : prorate par proprietaire avec Dr 40000XXX + Cr 100 (Fonds de roulement general, classe 1)
  - Recap a 5 cartes (Nbre appels, Budget, Reserve, **Roulement**, Total) + colonne "Dont roulement" dans le tableau d'appels

### Iter18 (Feb 2026) - PCMN belge officiel (327 + compat) + CRUD custom + PDF Liste des depenses
- **PCMN renouvele**: `pcmn_data.py` contient maintenant les **327 comptes officiels** fournis par l'utilisateur (CSV Finlead) + **10 comptes de compatibilite** (`PCMN_COMPAT_ACCOUNTS`) pour preserver les automatismes existants (tier_accounts 40000XXX/40010XXX/44000XXX, banques 550xxx/551xxx, fallback auto-entries 614000/615000/700000/701000). Export `PCMN_ALL_ACCOUNTS = 337` comptes au total.
- **Hierarchie complete** Classes 1 a 7 (Bilan + Resultat) avec parents auto-detectes par prefixe.
- **Seed par defaut**: nouvelle ACP -> 337 comptes (614000 & 615000 actifs, le reste inactif). `demo_seed.py` et `coproprietes.py` ont ete migres.
- **CRUD PCMN renforce** (`routes/accounting.py`):
  - POST `/api/accounting/pcmn` : creation custom (is_custom=true), auto-derivation class_num (1er chiffre) + type (1-5 balance, 6-7 result), active=true par defaut, 400 si numero non-numerique ou doublon.
  - PUT `/api/accounting/pcmn/{number}` : modification partielle via `PCMNUpdateInput` (name/class_num/parent/type/active).
  - PATCH `/api/accounting/pcmn/{number}/toggle-active` (inchange).
  - DELETE `/api/accounting/pcmn/{number}` : 3 niveaux de protection - (1) is_tier_account => refus, (2) is_custom=false sans `force=true` => 400, (3) compte utilise dans journal_entries / invoices / expense_categories => 409 avec compte exact.
- **Migration superadmin idempotente**: POST `/api/admin/migrate/pcmn-import?copropriete_id=opt` ajoute uniquement les comptes manquants sans toucher l'existant. Retourne stats {added, existing} par ACP.
- **Page Plan Comptable enrichie** (`pages/AccountingPage.js`):
  - Filtres : recherche, classes 1-7 (tabs), actifs uniquement, customs uniquement
  - Toggle actif inline (Switch shadcn)
  - Badges statut : "Tiers auto" (violet), "Custom" (ambre), "Officiel" (gris)
  - Boutons Edit + Delete (delete grise si tier auto)
  - Bouton "Importer PCMN complet" pour superadmin
  - Dialog creation/edition avec auto-derivation class+type a la saisie du numero
- **PDF "Liste des depenses"** (`pdf_liste_depenses.py` + GET `/api/reports/depenses/pdf`):
  - Format landscape A4, colonnes : Date valeur, Libelle, Fournisseur, Ref. interne, Montant, Part proprietaire, Part occupant
  - Hierarchie : Cle de repartition (titre bleu) -> Nature (gras gris) -> Compte PCMN (italique) -> lignes
  - Sous-totaux par nature + par cle + ligne "Totaux generaux immeuble" finale
  - En-tete avec ACP + periode + "Fait le" + IPI/BCE
  - Bouton "Liste des depenses (PDF)" dans `ExpensesPage.js`
- **PDF Decompte aligne**: colonnes renommees pour matcher la nomenclature Syndic ("Date valeur", "Ref. interne", "Montant TVAC", "Votre quote-part")

### Iter17 (Feb 2026) - Natures de depense + edition cles + edit auto entries
- **Module "Nature de depense"** (`expense_categories.py` + `ExpenseCategoriesPage.js`)
  - Relation 1:1 stricte avec un compte PCMN classe 6 (validation 409 + 400)
  - Lookup par nom du compte via Popover + Command shadcn
  - Affiche invoice_count + invoice_total
  - CRUD complet, DELETE refusé si factures liées
- **Facture liee a une nature**: champ `expense_category_id` sur InvoiceInput. POST/PUT auto-derivent `account_number` depuis la categorie selectionnee. Page Invoices: select nature -> auto-fill compte PCMN
- **Edition cle de repartition avec warning**:
  - GET /api/distribution-keys/{id}/usage retourne invoices/budgets/fund_calls liés
  - PUT sans force=true -> 409 si invoices liées; PUT?force=true -> detach invoices + update
  - Dialog d'edition avec banner ambre warning + liste des factures liées + auto-confirmation force
- **Edition des ecritures auto AC/VE/FI**:
  - PUT autorisé même sur auto_generated; marque `manually_edited=true` + `manually_edited_at`
  - DELETE autorisé uniquement si `manually_edited=true` (sinon 400)
  - `_delete_auto_entries` skip les entries `manually_edited` (préservation lors d'un update de source)
  - Badges UI: "Auto" bleu si pas modifié, "Modifie" orange si edité
- **PDF decompte 3 niveaux**: Cle de repartition -> Nature de depense (libellé) -> Compte PCMN (numéro). Sous-totaux par cle + par nature.
- **Bouton modifier** sur ExpensesPage: deep-link `/invoices?edit={id}` ouvre directement le dialog d'edition

### Iter16 (Feb 2026) - Auto-écritures + Clôture + Page Dépenses
- **Module `auto_entries.py`** : 3 helpers
  - `generate_purchase_entry` -> AC journal (Dr 6xxxxx + Cr 44000XXX) sur POST/PUT facture
  - `generate_sale_entry` -> VE journal (Dr 40000XXX par owner + Cr 700000, Dr 40010XXX + Cr 701000 pour réserve) sur POST fund_call et generate-from-budget
  - `generate_bank_entry` -> FI journal (Dr/Cr 550xxx + 40000XXX/44000XXX) sur lettrage manuel + auto-VCS
- **Cleanup automatique** des auto entries quand la source est supprimée (invoice, fund_call, bank_txn). DELETE manuel des auto entries -> 400.
- **Journaux séparés**: tabs Operations Diverses / Achats / Ventes / Financier / A-Nouveau dans JournalsPage avec badge "Auto" sur les écritures générées.
- **Page Dépenses** (`ExpensesPage.js` + GET /api/fiscal/expenses) : filtres exercice, nature (compte 6xx), clé de répartition, compte bancaire, dates. Agrégations Top 3 par nature/clé/banque + total + count. Indicateur PJ (paperclip).
- **Clôture annuelle - Régularisation** (`/api/fiscal/years/{id}/regularize`)
  - Mode `dry_run=true` : preview sans persister
  - Calcule budget vs frais réels vs provisions appelées
  - Extourne automatiquement les provisions (Dr 700000 / Cr 40000XXX)
  - Affecte les frais réels par clé de répartition (Dr 40000XXX / Cr 700000)
  - Fonds de réserve (40010XXX/701000) PAS extourné (reste au bilan)
  - DELETE pour rollback la régularisation
  - Dialog `RegularizationDialog` 2 étapes: preview détaillé + confirm
  - Bouton Calculator orange sur les exercices ouverts

### Iter15 (Feb 2026) - Comptes tiers automatiques + bug balance
- **Module `tier_accounts.py`** : helper d'assignation automatique de comptes PCMN par tiers
  - Propriétaire = 2 comptes par ACP: `40000XXX` (provisions charges) + `40010XXX` (fonds réserve)
  - Fournisseur = 1 compte par ACP: `44000XXX`
  - Numérotation: 3 chiffres séquentiels, scopée par ACP, gap-free
  - Idempotent: tier_accounts persistées dans owner.tier_accounts[copro_id] et supplier.tier_accounts[copro_id]
- **Auto-assignation** sur POST/PUT owners + POST/PUT suppliers avec `copropriete_id`
- **Migration de masse** : POST /api/admin/migrate/tier-accounts (superadmin) — backfill tous les owners + suppliers existants (8 owners x 2 + 5 suppliers = 21 comptes créés en demo)
- **Bug fix balance tiers fournisseur** : matching case-insensitive entre invoice.supplier et supplier.name, inclusion des "orphelins" (fournisseurs présents dans factures mais sans fiche Supplier) avec badge ambre
- **Affichage** : colonne « Comptes (40000 / 40010) » sur balance propriétaires, colonne « Compte » sur balance fournisseurs
- **Pièce jointe à la création de facture** : bouton « Joindre la facture PDF / image » directement dans le dialog Nouvelle facture (en plus du flow IA)

### Iter14 (Feb 2026) - Affichage et regeneration
- **FundCallsPage enrichi** : affiche `lines` (detail par nature avec compte+libelle+cle+montant), `reserve_amount` (badge violet "dont reserve X EUR"), badge "Issu du budget X" (bleu) pour les appels generes par wizard
- **POST /api/fund-calls/regenerate-from-budget** : supprime les appels futurs UNPAID lies au budget puis regenere selon nouveau planning. Les appels avec >=1 paiement recu sont PRESERVES (history protection). Retourne deleted_count + preserved_count.
- **Bouton "Regenerer non-echus"** sur les cartes budget approuve (FiscalYearPage) -> ouvre le wizard en mode regenerate
- Workflow complet : approve -> wizard -> revoke -> modifier -> re-approve -> "Regenerer non-echus" -> ajustement intelligent sans casser l'historique

### Iter13 (Feb 2026) - Workflow Budget -> Appels de fonds
- **Budget enrichi** (`fiscal.py`)
  - Champs `status` (draft/approved), `approved_at`, `approved_by`
  - `distribution_key_id` par ligne (chaque nature de depense a sa propre cle)
  - Edition/suppression bloquees si approved (revoke d'abord obligatoire)
  - POST /api/fiscal/budgets/{id}/approve + /revoke
- **Recapitulatif N-1** (`GET /api/fiscal/previous-year-expenses?fiscal_year_id=`)
  - Agregation invoices.total_amount + journal_entries class-6 net (hors entries liees a fund_call)
  - Groupement par (compte PCMN, distribution_key)
  - Bouton "Pre-remplir depuis N-1" dans le dialog budget
- **Assistant d'appels de fonds 4 etapes** (`fund_calls.py` + `BudgetWizard.js`)
  - Etape 1 - Frequence: 1/2/3/4/6/12 appels (annuel a mensuel)
  - Etape 2 - Calendrier: date 1er appel + due_offset_days, calendrier auto des N dates
  - Etape 3 - Fonds de reserve oui/non + montant + cle dediee (applique au call 1 uniquement)
  - Etape 4 - Recap: tableau N appels, montant, reserve, nb proprietaires + details proprietaire du 1er appel
- **Endpoints** :
  - POST /api/fund-calls/preview-from-budget (sans persister) : 400 si budget draft
  - POST /api/fund-calls/generate-from-budget (persiste N fund_calls avec status pending)
- **Multi-clés distribution** : chaque ligne du budget distribue son quart selon SA cle, sommation par proprietaire
- Declenchement auto du wizard a l'approbation du budget

### Iter12 (Feb 2026) - finalisation P1/P2
- **Bilan & Compte de Resultats PCMN belge strict** (`reports.py` lignes 130-401)
  - Rubriques I-VIII actif / I-VII passif officielles, calcul automatique du resultat de l'exercice injecte dans III bis
  - Compte de resultat groupe par rubriques 60-67 / 70-76 avec label de resultat (Benefice/Perte/Equilibre)
- **Exports Excel** (`routes/exports.py`)
  - GET /api/exports/balance-tiers/owners.xlsx
  - GET /api/exports/bilan.xlsx
  - GET /api/exports/grand-livre.xlsx
- **Rappels paiement automatises** (`routes/exports.py` create_reminders_router + `pages/RemindersPage.js`)
  - GET /api/reminders/late-payments?grace_days=N -> tri par severite (critique +90j, urgent +30j, rappel2 +7j, rappel1)
  - GET /api/reminders/owner/{owner_id}/letter -> PDF lettre de rappel professionnelle avec VCS
  - Page UI dediee avec cartes statistiques + table des retards
- **Auto-extraction IA factures fournisseurs** (`routes/invoice_ai.py`)
  - POST /api/invoices-ai/extract: upload PDF -> pypdf extract + Claude Sonnet 4.5 -> JSON {supplier, number, date, montants, vat, suggested PCMN}
  - Frontend: bouton "Importer facture PDF (IA)" dans InvoicesPage qui pre-remplit le formulaire
  - PDF source attache automatiquement a la facture creee
- **Pieces jointes aux ecritures comptables et factures** (NOUVEAU user demande Feb 2026)
  - Journal entries (OD/AV/AP): POST/GET/DELETE /api/accounting/entries/{id}/attachments[/{att_id}]
  - Invoices: POST/GET/DELETE /api/invoices/{id}/attachments[/{att_id}]
  - Formats: PDF, PNG, JPG
  - Storage: /app/uploads/journal_attachments + /app/uploads/invoice_attachments
  - Cleanup auto a la suppression de l'ecriture/facture
  - UI: icone Paperclip sur chaque ligne + dialog d'upload/download/suppression

### Iter11 - Portail proprietaire (DONE)
- Login owner -> redirection auto vers `/portal`
- 6 endpoints `/api/owner/*` (resolution auto par email)
- Page OwnerPortalPage: identite + VCS copiable, 4 stats, alertes appels en attente, onglets coproprietes/appels/charges/documents
- Bouton "Telecharger mon decompte annuel (PDF)"

### Comptabilite (PCMN par ACP)
- PCMN scope par ACP (95 comptes belges seedes a la creation)
- 2 comptes actifs par defaut: 614000 Honoraires syndic + 615000 Frais de gestion
- Toggle active/inactive par compte
- Banques auto-generent PCMN 550xxx (epargne) / 551xxx (vue)
- Exercices fiscaux avec cloture + a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AC/AN) + ATTACHMENTS, Grand Livre, Balance, Bilan, Resultat

### Banque
- CODA import, saisie inline, edition + suppression transactions
- VCS mod-97, auto-lettrage
- Lookup contrepartie

### Facturation
- Cles de repartition par ACP avec quotites
- Factures + ATTACHMENTS, avec ventilation
- Appels de fonds avec distribution
- Balance de tiers

### Documents
- Upload PDF/images stockage disque
- Auto-classification IA via Claude Sonnet 4.5
- 10 categories par defaut seedees a chaque ACP

### UX
- Wizard ACP 3 etapes
- Autocomplete proprietaires par nom/email/VCS
- Bouton "Generer ACP demo" (admin, idempotent)

### Deploiement
- Scaleway docker-compose + nginx + webhook GitHub
- GDPR compliant (EU hosting Paris)

## Test coverage
- iter7: 16/16 chinese walls + 139/140 regression
- iter8: 18/18 features + 157/158 regression
- iter9: 43/43 auth middleware
- iter10: 28/28 RBAC + 59/59 regression
- iter11: 30/30 owner portal + 84/87 regression
- **iter12: 20/20 new features (Bilan/Resultat rubriques, Excel exports, reminders, AI invoice, attachments, RBAC) + 29/30 regression**
- **iter13: 15/15 Budget workflow (N-1 expenses, approve/revoke, edit-lock, preview/generate-from-budget, reserve on call#1, multi-key distribution, RBAC, frequency)**
- **iter14: 8/8 regenerate-from-budget (history protection, scope filter, idempotence, RBAC) + 15/15 iter13 regression**
- **iter15: 16/16 tier accounts auto (assignment, migration idempotence, case-insensitive supplier matching, orphan handling, RBAC) + 8/8 iter14 regression**
- **iter16: 16/16 auto-entries AC/VE/FI + cleanup + manual delete protection + expenses endpoint + regularize dry-run/persist/delete + reserve-not-extourned + RBAC + 24/24 iter14+iter15 regression**
- **iter17: 19/19 expense categories 1:1 + invoice account derivation + dist-key usage/force-detach + auto-entry edit policy + PDF 3 niveaux + 16/16 iter16 regression**
- **iter18: 17/17 nouveau PCMN belge officiel 327 + 10 compat = 337/ACP, CRUD custom (is_custom flag, protection deletion 3-niveaux, auto-derivation class/type), migration pcmn-import idempotente, PDF Liste des depenses (Cle->Nature->Compte hierarchique, sous-totaux + totaux generaux) + 16/16 regression iter16 auto-entries**
- **iter19: 12/12 frais privatifs (AC 4 lignes 643/44000/40000/643 + UI search owner + validation 400/404) + wizard Fonds de roulement (etape 4 Create/Increase + VE credit 100 prorate par owner) + 33/33 regression iter16+iter18**
- **iter20: 10/10 (P0 balance tiers from journal_entries - OD Dubois visible + reserve_balance separe / P1 wizard 3 series independantes provisions+reserve+roulement avec frequency/start/due par fonds / P2 AccountSearchSelect composant reutilisable / P3 BCE fournisseur extraction + matching priorise + suggest creation fiche) + 45/45 regression iter16+18+19 = 55/55**
- **iter25: 5/5 nouveaux (Bilan ACP Demo expose PCMN 55103400 + 551079 reels / regenerate-bank-entries shape correcte + admin-only / cleanup-orphan-entries idempotent / signe debit/credit verifie / DELETE statement cascade FI auto) + 44/46 regression iter21-iter24 (2 echecs = drift Alex BENOIT pre-existing, hors scope)**
- **iter26: 9/9 nouveaux (Fonds reserve credit 160 classe 1 / Fonds roulement credit 100 classe 1 / migrate-reserve-to-classe1 idempotent + RBAC / legacy generate-entries call_type=reserve credit 160 / Bilan ACP Demo 160 en II.Reserves + 100 en I.Capital / aucune 701000 auto-generee restante) + 29/31 regression iter25/iter20/iter16**

## Backlog P1
- Gestion AG (ordre du jour, votes, PV, convocations)
- Notifications email (Resend) lors des nouveaux appels de fonds

## Backlog P2
- Bordereaux SEPA pain.001
- Module gros entretien fonds de reserve
- Optimisation perf reminders/exports (eviter O(n^2) pour gros ACP)
- Limit file size attachments (25 MB max) + sanitize filename

## Files of reference
- `/app/backend/server.py`: auth + RBAC middleware + router registration (lignes 380-410)
- `/app/backend/routes/exports.py`: Excel exports + reminders + lettre PDF
- `/app/backend/routes/invoice_ai.py`: extraction IA factures fournisseurs
- `/app/backend/routes/reports.py`: bilan + resultat rubriques PCMN
- `/app/backend/routes/accounting.py`: journal entries + ATTACHMENTS
- `/app/backend/routes/invoices.py`: factures + ATTACHMENTS
- `/app/backend/routes/owner_portal.py`: 6 endpoints owner
- `/app/backend/pdf_decompte.py`: PDF decompte annuel
- `/app/frontend/src/pages/RemindersPage.js`: page rappels
- `/app/frontend/src/pages/ReportsPage.js`: rubriques + boutons Excel export
- `/app/frontend/src/pages/InvoicesPage.js`: bouton IA + paperclip + attachments dialog
- `/app/frontend/src/pages/JournalsPage.js`: paperclip + attachments dialog
- `/app/frontend/src/pages/BalanceTiersPage.js`: bouton Excel
- `/app/frontend/src/pages/OwnerPortalPage.js`: portail proprietaire
- `/app/frontend/src/App.js` + `/app/frontend/src/components/Layout.js`: routes + sidebar
