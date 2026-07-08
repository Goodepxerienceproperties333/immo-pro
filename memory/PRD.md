# CoproManager PRD

## Architecture
Multi-ACP avec **chinese walls stricts** sur donnees comptables/financieres.
Auth: JWT cookie + middleware global FastAPI.
Roles: `superadmin`, `syndic`, `gestionnaire`, `owner`.

## Security model
1. Auth middleware global sur /api/* (sauf /auth/login, /auth/register, /auth/refresh, /auth/logout).
2. RBAC path-based: admin only sur /api/admin/* + /api/users; owner restreint a /api/owner/*, /api/auth/*, GET /api/coproprietes(+sous-paths), GET /api/documents/.../download.
3. Chinese walls: `copropriete_id` propage automatiquement (frontend interceptor) et filtre cote backend.


### Iter90bx (Feb 2026) - Contre-passation traceable des ecritures comptables (BLOQUANT LEGAL)
### Iter90cd (Feb 2026) - Fix regeneration appels apres mutation (double-comptage prorata)
### Iter90ce (Feb 2026) - Verrou regression : fonds de reserve JAMAIS transferes en mutation
### Iter90cf (Feb 2026) - Fix appels crees APRES mutation + double-ecriture db.mutations

**Ticket utilisateur** : "les OD n'apparaissent plus suite a tes corrections
le total n'est pas bon!! Lors des mutations apres appels tout se passe bien
mais lors des appels apres mutation tout ne fonctionne pas".

**Root cause identifiee (double bug)** :
1. `mutate_lot` ecrivait UNIQUEMENT dans `lot.mutations` array. Jamais dans la
   collection `db.mutations`. Or `_rebind_owner_at_call_date` (fund_calls.py)
   lit dans `db.mutations`. Consequence : les mutations effectuees en prod
   etaient invisibles pour toute la logique iter90aj / iter90cd.
2. Meme si (1) etait resolu, la logique de mutate_lot ne creait les OD MUT-P
   / MUT-F qu'au moment de la mutation. Si les appels etaient generes APRES,
   aucune OD n'etait creee -> le prorata temporis manquait.

**Cas reel (ACP Acacia, mutation Matexi -> DEGRANDE 10/06/2026)** :
- Appel Q2 2026 (01/04, periode 01/04-30/06) genere apres la mutation.
- Distribution 100% Matexi (correct : owner-at-call-date).
- MAIS aucune OD MUT-P pour transferer les 21 jours (10/06-30/06) a DEGRANDE.
- Balance de tiers faussee de ~85.85 EUR.

**Fix (3 volets)** :
1. `properties.py::mutate_lot` : ajoute double-ecriture `db.mutations.insert_one`
   avec sync symetrique dans `cancel_mutation`.
2. `server.py::startup` : sync idempotent `db.mutations` <- `lot.mutations`
   au demarrage. Peuple la collection existante en prod au 1er redeploy.
3. `fund_calls.py` : nouvelle fonction `generate_prorata_mut_ods_for_call(db, call)`
   appelee apres chaque creation d'appel provisions. Detecte les mutations
   dans la periode et cree les OD MUT-P retroactivement (DR acheteur /
   CR vendeur, ref `MUTP-POST-{lot}-{call_short}-{buyer_short}`).
4. `fund_calls.py::delete_fund_call` : contre-passe egalement les OD MUT-P
   retroactives associees au fund_call supprime (via reversal, pas hard delete).
5. Rebind owner-at-call-date etendu aux **provisions** (pas seulement
   reserve/roulement/special) dans POST /api/fund-calls single.

**Script de reparation** :
`backend/scripts/repair_post_mutation_prorata.py --dry-run|--apply`
Detecte et corrige toutes les OD MUT-P manquantes sur la base existante.

**Tests iter90cf** : 5/5 PASS
1. Appel straddling mutation -> OD MUT-P creee correctement.
2. Appel entierement post-mutation -> distribution.owner=acheteur, aucune OD.
3. Appel entierement pre-mutation -> distribution.owner=vendeur, aucune OD.
4. Idempotence : supprimer + recreer l'appel = 1 seule OD active.
5. Multi-mutations dans la periode : plusieurs OD segmentees.

**Regressions verifiees (sans nouvel echec)** :
iter76, iter90ai, iter90aj, iter90cd, iter90ce (17/17 tests PASS).

**Ticket utilisateur** : "les fonds de reserve ne sont JAMAIS transferes dans le
cadre des mutations... Corrige".

**Verification realisee** : investigation du code (`routes/properties.py` L945
+ L966-976 + L1033) et debug scripts. Conclusion : **le systeme respecte deja
strictement cette regle**. Ligne 945 filtre `call_type == "provisions"` avant
le calcul du prorata et des appels futurs, et lignes 966-976 excluent la portion
`reserve_amount` injectee dans les appels provisions legacy.

**Fix** : aucune modification de code necessaire. Creation d'un test de
regression `tests/test_iter90ce_reserve_never_transferred.py` verrouillant 4
scenarios :
1. Appels reserve standalone (pre + post mutation) -> zero prorata / zero OD.
2. Provisions avec reserve_amount injecte -> portion reserve exclue du prorata.
3. Mix provisions + reserve -> seul provisions genere une OD.
4. Solde tier vendeur reste debiteur de 100% de la reserve apres mutation.

Tests : 4/4 PASS.

**Ticket utilisateur** : "le systeme fonctionne correctement lors de la generation
initiale (appels a Matexi) + mutation (OD prorata correcte). Le bug est que si
les appels sont supprimes et relances par la suite, tu ne realises pas les memes
calculs. Il faut appliquer la meme regle de calcul que pour les mutations apres
appels."

**Regle metier belge (spec complete)** :
1. Appels emis AVANT mutation -> integralement impute au vendeur (100%).
2. Appels emis APRES mutation -> integralement impute a l'acquereur (100%).
3. Modifications d'appels en cours d'exercice -> memes proprietaires que
   l'appel initial (owner-at-date).
4. **Aucune ventilation entre vendeur et acquereur au niveau de l'appel lui-meme.**
   Le prorata temporis est realise SEPAREMENT par l'OD "Mutation Prorata"
   (properties.py::_apply_mutation_writes).

**Bug racine identifie** : `_split_lot_entry_by_mutations` (iter90ag) splittait
l'appel prorata temporis entre vendeur/acquereur lors de la generation. Combine
avec l'OD Mutation Prorata (manually_edited=True, non contre-passee), cela
creait un DOUBLE-COMPTAGE lors d'une suppression + regeneration :
- Suppression VE original -> contre-passation (matexi credite 400)
- Regeneration VE -> split matexi 200 / buyer 200 (mauvais)
- OD Mutation Prorata inchangee (credit matexi 200, debit buyer 200)
- Balance finale INCORRECTE : matexi 0 (attendu 200), buyer 400 (attendu 200)

**Fix (line 825 fund_calls.py)** : remplace `_split_lot_entry_by_mutations` par
`_rebind_owner_at_call_date` pour les budget lines. La fonction
`_rebind_owner_at_call_date` (deja utilisee pour reserve/roulement depuis
iter90aj) affecte simplement chaque entree au proprietaire a la date d'emission
de l'appel, SANS split. La fonction `_split_lot_entry_by_mutations` (~110L)
n'est plus appelee (dead code, conservee temporairement pour rollback).

**Comportement post-fix** :
- Appel emis avant mutation -> 100% vendeur ✓
- Appel emis apres mutation -> 100% acquereur ✓
- OD Mutation Prorata inchangee (gere le transfert prorata temporis independamment)
- Plus de double-comptage lors d'une regeneration
- Reserve/roulement inchanges (utilisent deja _rebind depuis iter90aj)

**Tests** (`test_iter90cd_regenerate_after_mutation.py` 3/3) :
1. `test_iter90cd_regenerate_appel_before_mutation_stays_on_seller` : scenario
   Matexi exact - Q4 emis 01/10, mutation 15/11, apres regeneration : Q4 100%
   Matexi, buyer 0. Verifie que le buyer n'apparait JAMAIS dans la distribution
   des appels emis AVANT sa mutation.
2. `test_iter90cd_appel_after_mutation_goes_to_buyer` : appels emis en 2026
   apres mutation 15/11/2025 -> 100% buyer.
3. `test_iter90cd_no_mutation_uses_current_owner` : regression, sans mutation
   le comportement standard s'applique.

**Regression** : `test_iter90ag_prorata_mutation_mid_q1` mis a jour pour refleter
la nouvelle regle (Q1 emis avant mutation -> 100% vendeur au lieu du split V/A).
Iter90aj/ah/ai/cb/cc/bx/af : 22/22 tests critiques passent.

**⚠️ Impact PROD sur donnees existantes** : les fund_calls DEJA generes avant ce
fix (avec l'ancien split) restent inchangees. Le fix s'applique aux nouvelles
generations. Pour reregler l'anomalie sur Acacia : redeployer + regenerer les
appels concernes (contre-passation preserve l'audit iter90bx).


### Iter90cc (Feb 2026) - Preflight check lots orphelins avant emission d'appels

**Contexte** : suite au bug iter90cb, ajout d'une verification pre-emission pour
alerter le syndic quand des lots orphelins sont detectes dans une cle de
distribution utilisee par le budget/fonds. Le fix iter90cb continue de
redistribuer automatiquement les shares (filet de securite), mais le syndic est
averti pour eventuellement corriger la saisie en amont.

**Backend** :
- Nouveau helper `_detect_orphan_lots_for_budget(data)` : analyse toutes les
  cles utilisees (budget.lines + reserve_fund + roulement_fund) et retourne :
  ```
  {
    orphan_count: int,
    orphan_share_percentage: float,       # % max sur toutes les cles affectees
    keys_affected: [{key_id, key_name, orphan_share, total_share, orphan_percentage}],
    lots: [{lot_id, lot_number, share, keys: [key_name...]}]
  }
  ```
- Nouvel endpoint `POST /api/fund-calls/preflight-orphan-check` : leger,
  retourne uniquement le warning (utilisable pour check async avant confirmation).
- Endpoint `POST /api/fund-calls/preview-from-budget` enrichi : la reponse
  inclut desormais le champ `orphan_lots_warning` (auto-affiche dans le wizard).

**Frontend** (`BudgetWizard.js`, step 5 "Recapitulatif") :
- Banner amber (border-2, AlertTriangle icon) affiche automatiquement quand
  `preview.orphan_lots_warning.orphan_count > 0`.
- Message clair : "X lot(s) orphelin(s) detecte(s) (Y.YY% des shares). Leurs
  shares seront redistribuees proportionnellement... Verifiez qu'il ne s'agit
  pas d'une erreur de saisie".
- Section `<details>` cliquable : liste lot par lot avec numero et cles impactees,
  puis synthese par cle (orphan_share / total_share / percentage).
- data-testid="orphan-lots-warning" pour tests E2E.

**Tests** (`test_iter90cc_preflight_orphan_check.py` 3/3) :
- test_preflight_detects_orphan_lots_with_share : detection Matexi 1 lot
  orphelin 3.6% avec cle referencee 3x -> une seule entree keys_affected
- test_preflight_no_orphans_returns_empty_warning : ACP saine -> 0 warning
- test_preview_endpoint_also_includes_warning : preview inclut le warning +
  les 4 calls normaux

**Design** : le check ne BLOQUE PAS la generation (l'utilisateur peut ignorer
l'avertissement). C'est un rappel informatif ; le fix iter90cb reste actif
comme filet de securite.


### Iter90cb (Feb 2026) - Fix distribution appel avec lots orphelins (bug 3.6% Matexi)

**Ticket utilisateur (ACP Acacia)** : Balance de tiers Matexi affichait Reserve
1446 EUR et Roulement 5012.80 EUR alors que le budget prevoyait 1500 EUR et
5200 EUR. Ecart exact de 3.6% sur les deux fonds - meme quand Matexi possedait
100% des lots avec owner.

**Cause racine identifiee** : bug asymetrique dans `fund_calls.py::_distribute_amount`
lignes 620-640 (branche "avec cle de distribution") :
- `total_shares` (denominateur) etait calcule sur TOUS les lots actifs de la cle
- Boucle de distribution skip les lots sans owner_id
- Resultat : `sum(amounts_distribues) = amount * (shares_avec_owner / total_shares) < amount`
- Les shares des lots orphelins etaient perdues (jamais creditees a personne)

**Verification numerique** : 1500 * 0.964 = 1446 EUR et 5200 * 0.964 = 5012.80 EUR
=> ratio identique de 96.4% sur les deux fonds prouve que 3.6% des shares de la
cle "Charges communes" pointaient vers des lots sans owner_id.

**Impact avant fix** :
- Compte 160 (Fonds de reserve) sous-alimente de 54 EUR/an
- Compte 100 (Fonds de roulement) sous-alimente de 187.20 EUR/an
- Sur 10 ans : ecart cumule 540 + 1872 = 2412 EUR pour l'ACP Acacia
- Comportement asymetrique : la branche fallback quotity (ligne 642) etait deja
  correcte car elle filtrait `if lt.get("owner_id")` AVANT le calcul du total.

**Fix (option A validee par user)** :
```python
lots_by_id = {lt["id"]: lt for lt in lots}
owned_kls = [
    kle for kle in key.get("lots", [])
    if not kle.get("excluded")
    and lots_by_id.get(kle.get("lot_id"), {}).get("owner_id")
]
total_shares = sum(kle["share"] for kle in owned_kls)  # <- filtre AVANT total
```
Effet : les shares orphelines sont redistribuees proportionnellement sur les
proprietaires actuels. La somme des amounts distribues == amount du budget.

**Comportement post-fix** :
- Matexi (seul owner) recoit 1500 EUR de reserve (au lieu de 1446)
- Compte 160 accumule bien 1500 EUR/an conformement au budget
- Balance de tiers Matexi affichera 1500.00 EUR au lieu de 1446.00

**IMPORTANT - appels DEJA GENERES en PROD** : ce fix ne modifie PAS les fund_calls
existants en base. L'utilisateur doit soit :
  1. Supprimer + regenerer les appels affectes (les contre-passations preservent
     l'audit trail via iter90bx)
  2. OU attendre le prochain exercice fiscal (nouveaux appels utiliseront le fix)

**Tests** (`test_iter90cb_orphan_lot_shares.py` 3/3) :
- test_iter90cb_orphan_lot_shares_are_redistributed_to_matexi : scenario reel
  Matexi 9 lots + 1 orphelin 3.6% => Matexi recoit 100% (1500 EUR)
- test_iter90cb_no_orphans_regression : sans orphelin, comportement inchange
- test_iter90cb_multi_owners_orphan_lot : 2 owners 50/50 + 1 orphelin 20%
  chacun recoit 500 EUR (redistribution proportionnelle)

**Regression** : 12/12 tests critiques passent (iter90ac, ah, ai, aj, w).


### Iter90bz (Feb 2026) - Agregation des mutations lot dans la situation de compte

**Ticket utilisateur** : "Le rapport affiche des lignes separees pour 'mutations par
lot' et 'appels par lot' pour chaque lot. Je souhaite une seule ligne consolidee
par tiers, totalisant tous les lots de ce proprietaire, avec le detail par appel
agrege sur l'ensemble des lots."

**Cause racine** : chaque mutation lot cree une ecriture OD distincte dans
`journal_entries` avec une `reference` UNIQUE (`MUT-{lot_number}-{suffix}`) et
une `description` UNIQUE (`"Mutation lot 001 - Prorata appel (Q1/4)..."`).
Pour un promoteur avec 30 lots, cela genere 30 lignes visuellement identiques
par trimestre = 120 lignes sur 8 pages PDF (illisible).

L'iter90bv `_group_movements_by_owner` utilisait `reference` dans sa cle de
regroupement, ce qui empechait la fusion.

**Nouvelle fonction** `_normalize_mutation_desc(desc)` :
- Regex `r"^\s*(?:\[[A-Z]{2,3}\]\s*)?(?:Operation\s*:\s*)?Mutation\s+lot\s+\S+\s*-\s*([^:]+?)(?:\s*:.*)?$"`
- Extrait le suffixe STABLE (label independant du lot) : "Mutation lot 001 - Prorata appel (Q1/4): Matexi -> Buyer (206.44)" -> "Mutation lots - Prorata appel (Q1/4)"

**Nouvelle cle de regroupement pour mutations** (dans `_group_movements_by_owner`) :
- `(MUT-AGG, date, account_number, normalized_label, journal_type, third_party_id)`
- Ignore `reference` (unique par lot) et le numero de lot dans la description
- Ne fusionne que les mutations du meme proprietaire, meme date, meme label

**Post-processing** : si N > 1 mutations fusionnees :
- description : `"Mutations (N lots) - Prorata appel (Q1/4)"`
- reference : `"MUT-AGG (N)"`
- Si N == 1 : description originale conservee (numero de lot visible, pas de perte d'info)

**Impact** :
- Endpoint `GET /api/reports/balance-tiers/owners/{id}?group_by_owner=true` (defaut)
  affiche desormais 2 mouvements par trimestre (VE + Mutations agregees) au lieu de
  1 + N. Reduction ~ -75% du volume PDF pour les promoteurs.
- PDF "Situation de compte" utilise toujours `group_by_owner=true` par defaut.
- Mode `group_by_owner=false` (toggle "Detail par lot" UI) conserve les N lignes
  detaillees pour audit.
- **Comptabilite INCHANGEE** : les 30 ecritures OD restent en base pour audit
  trail legal (art. III.86 CDE). Seule la presentation est agregee.

**Tests** :
- `test_iter90bz_group_mutations_by_owner.py` (13/13) : normalisation, aggregation
  Matexi 30 lots, single lot preservation, VE non fusionne, quartiers distincts,
  proprietaires distincts, Fonds de roulement multi-lots.
- `test_iter90bz_e2e_matexi_scenario.py` (2/2) : HTTP endpoint vue groupee (2
  mouvements) vs detail (6 mouvements).
- Regression iter90bv : 6/6 tests toujours passants.

**Bonus** : correction lint pre-existant `E701 Multiple statements on one line`
sur 6 lignes dans `reports.py` (blocs `if start_date/end_date`).


### Iter90by (Feb 2026) - Refactor P5 (frontend + backend, dette technique)

**Frontend** (`BalanceTiersPage.js` : 665 -> 399 lignes, -40%) :
- `components/balance-tiers/FilterBar.js` (77L) : composant reutilisable de
  filtres periode + presets (aujourd'hui/mois/trimestre/annee/N-1/tout).
  Export nomme `FilterBar` + helper `getPreset(name)`.
- `components/balance-tiers/TiersDetailDialog.js` (86L) : dialog situation de
  compte proprietaire/fournisseur avec toggle Vue resumee / Detail par lot
  (iter90bv).
- `components/balance-tiers/LettrerDialog.js` (93L) : dialog de rattachement
  manuel d'un fournisseur orphelin (nom sans fiche) a un fournisseur en base.
- `components/balance-tiers/SupplierMergeDialog.js` (96L) : dialog de fusion
  multi-fournisseurs avec choix radio du fournisseur a conserver.
- Imports allegees : plus de `Dialog*`, `Filter`, `X`, `Input`, `fmtDate` dans
  la page principale. Tous les data-testid preserves pour tests E2E.

**Backend** (`auto_entries.py`) - 3 helpers extraits pour reduire la complexite :
- `_resolve_or_create_supplier_account(db, supplier_name, copro_id)` (37L) :
  isole la logique Chinese-walls-safe de creation auto de fiche fournisseur
  + assignation du compte 44000XXX (jamais 440000 master).
- `_resolve_bank_account(db, txn, copro_id)` (25L) : IBAN -> compte PCMN
  bancaire configure sur l'ACP. Fallback "550000".
- `_resolve_bank_counterpart(db, txn, copro_id)` (43L) : match_type ->
  (counterpart_acc, counterpart_name, third_party_id, invoice_number).
  Supporte owner_payment / invoice / supplier_payment.
- `generate_purchase_entry` : 221 -> 196 lignes (-11%).
- `generate_bank_entry` : 234 -> 191 lignes (-18%).

**Bonus** : correction pre-existante `E741 Ambiguous variable name 'l'` dans
`_balanced` (renomme `l` -> `ln`).

**Tests** : 50/51 tests unit critiques passent (le seul echec est un 429
rate-limit transitoire sur test HTTP). Aucune regression fonctionnelle.


### Iter90bw (Feb 2026) - Backup legal 10 ans enrichi (Art. III.86 CDE)

**Ticket utilisateur** : "P1 Finaliser le backup legal 10 ans (backup_service.py) :
inclure PDF factures + details fournisseurs pour conformite Art. III.86 CDE"

**Nouveaux fichiers ajoutes dans le ZIP archive** (`backup_service.build_acp_archive_zip`) :
- `suppliers.csv` (racine) : id, nom, BCE, TVA, adresse, telephone, email,
  IBAN, BIC, compte tier PCMN, compte par defaut, notes.
- `documents.csv` (racine) : id, categorie (AG, PV, contrat...), titre,
  description, date, uploaded_by, filename, mime_type. Les originaux PDF/
  images sont copies dans `documents_generaux/{categorie}/{titre}.{ext}`.
- Par exercice :
  - `invoice_lines.csv` : lignes multi-natures des factures (compte, cle,
    montant, description) — auparavant perdues dans le ZIP.
  - `fund_calls_distribution.csv` : repartition PAR PROPRIETAIRE ET LOT
    (fund_call_date, name, owner_id, owner_name, lot_id, lot_number,
    vcs_code, share, amount, paid, paid_date).

**Enrichissements CSVs existants** :
- `owners.csv` : ajout BCE/TVA, IBAN, BIC, VCS complets, comptes tiers PCMN
  (compte_provisions, compte_reserve), is_company, notes. Inclut aussi les
  proprietaires HISTORIQUES (via journal_entries + fund_calls, pas seulement
  ceux ayant encore un lot).
- `lots.csv` : ajout adresse complete, type, floor, parent_lot_id, reference
  cadastrale, date + notaire de l'acte notarie.
- `invoices.csv` : ajout description, due_date, BCE/TVA/IBAN/BIC du fournisseur
  DENORMALIZES (preuve legale : si le fournisseur change de BCE/IBAN dans 5
  ans, l'archive conserve la valeur au moment de la facture), vat_amount,
  expense_category_id, distribution_key_id, amount_paid, is_private_fee.
- `fund_calls.csv` : ajout call_type, reserve_amount, roulement_amount,
  distribution_key_id, budget_id.
- `bank_transactions.csv` : ajout value_date, counterparty_iban, description,
  currency, matched_to, matched_invoice_number, statement_number, reference.

**README.txt** : mis a jour pour lister tous les nouveaux fichiers +
mentions legales (10 ans art. III.86 CDE, PCMN, mode utilisation Excel).

**Format version** : `1.1` -> `1.2` (dans metadata.json).

**Tests** (`test_iter90bw_legal_backup_10y.py` - 10/10) : verifie chaque
fichier requis, presence de tous les champs BCE/IBAN/tier_accounts,
denormalisation supplier dans invoices, distribution par proprietaire,
match bancaire, format_version, README mentions legales.



**Ticket utilisateur** : "les contrepassations ne sont pas visibles, les suppressions
d'ecritures n'arrivent jamais en comptabilite, il faut toujours garder l'ecriture
supprimee dans la comptabilite, il faut donc pour chaque suppression effectuer une
ecriture inverse permettant un tracing comptable."

**Regle metier belge (PCMN + art. III.86 CDE)** : une ecriture comptable NE PEUT
JAMAIS etre supprimee. Toute "suppression" doit generer une ecriture INVERSE
(Dr<->Cr swap) qui neutralise mathematiquement l'originale tout en gardant la
trace audit legale.

**Nouveau helper** (`journal_reversals.py`) :
- `reverse_journal_entry(db, orig, reason)` : cree une contre-passation avec
  Dr<->Cr swappes ligne par ligne. Marque l'originale `reversed=True` +
  `reversed_by_entry_id`. La contre-passation porte `is_reversal=True` +
  `reverses_entry_id`.
- Idempotent : ne fait rien si deja `reversed` ou `is_reversal`.
- Date de contre-passation : `date_originale` si exercice ouvert, sinon
  `today` (respect verrou fiscal, jamais de saisie dans un FY cloture).
- `reverse_auto_entries(source_type, source_id, reason)` : cascade toutes les
  ecritures d'une source (facture, appel, extrait).

**Refactor complet des points de suppression** :
- `auto_entries._delete_auto_entries` : nom historique conserve mais redirige
  desormais vers `reverse_auto_entries`. Les 15+ call sites (routes/invoices.py,
  routes/banking.py, routes/fund_calls.py, routes/fiscal.py) beneficient
  automatiquement du nouveau comportement.
- `DELETE /api/accounting/entries/{id}` : cree une contre-passation, retourne
  `{original_id, reversal_id, reversal_reference}`. Refuse 400 si deja
  `is_reversal` ou deja `reversed`. Refuse toujours les ecritures auto sans
  edit manuel (suggere de supprimer la source).
- `GET /api/accounting/entries` : parametre `include_reversals` par defaut
  `TRUE` (etait `false`). Les journaux comptables affichent maintenant TOUTES
  les ecritures, y compris les paires reversed/is_reversal (audit trail).
  Les balances/bilan/grand livre continuent d'exclure via `_exclude_reversals`
  (elles s'annulent mathematiquement).

**Frontend** (`pages/JournalsPage.js`) :
- Toggle "Inclure les contre-passations" par defaut coche.
- `handleDelete` remplace `window.confirm` par un prompt "Motif (optionnel)".
  Le message clarifie que l'originale est preservee et une inverse est creee.
- Badges deja implementes : "Contre-passation" (amber) sur `is_reversal`,
  "Extournee" (rouge, ligne barree) sur `reversed`.

**Tests** :
- `test_iter90bx_traceable_reversals.py` (7/7) :
  1. Cree une inverse + marque originale immuable
  2. Idempotence (2eme call = no-op)
  3. Ne peut pas contre-passer une contre-passation
  4. `_delete_auto_entries` genere des contre-passations
  5. Endpoint DELETE HTTP : 200 + contre-passation, 2eme call = 400
  6. GET /entries retourne les contre-passations par defaut
  7. Date de contre-passation = today si FY cloture

**Regression** :
- `test_iter90af_delete_budget_cascade` mis a jour : verifie que les JE sont
  preservees (`reversed=True`) et 2 contre-passations existent apres cascade
  (au lieu de count == 0).
- `test_iter85e_private_fee_multi_allocations` mis a jour : le
  `find_one({source_id, journal_type})` filtre desormais sur
  `reversed:{$ne:True}, is_reversal:{$ne:True}` pour trouver l'ecriture
  ACTIVE apres regeneration.


### Iter90bv (Feb 2026) - Regroupement multi-lots dans situation de compte

**Ticket user** : "Le detail par lot dans la Balance de tiers est trop complique
a comprendre pour les proprietaires. Il faut sommer les montants de tous les
lots d'un meme proprietaire."

**Cause racine** : quand un proprietaire possede N lots dans une ACP, un meme
appel de fonds VE genere N lignes debit sur son compte tier (une par lot).
Dans la vue "Situation de compte" et le PDF, cela produit N lignes visuellement
identiques (meme date, meme description "Appel de provisions - Q1 2026"), ce
qui est confus pour un lecteur non-comptable.

**Fix backend** (`routes/reports.py`) :
- Nouveau helper `_group_movements_by_owner(movements)` : regroupe les lignes
  par cle `(reference|entry_id, date, account_number, description, journal_type)`
  et somme debit/credit. Preserve l'ordre chronologique.
- `GET /api/reports/balance-tiers/owners/{owner_id}` : nouveau parametre
  `group_by_owner=true` (defaut) => vue resumee. `false` => detail par lot (audit).
- `_build_situation_compte_pdf` : ajoute `group_by_owner=true` par defaut
  (le PDF envoye aux proprietaires utilise la vue resumee).
- `GET /api/owner/situation/{copropriete_id}` : agrege desormais les montants
  par (fund_call.id, invoice.id) au lieu de generer 1 mouvement par lot.

**Frontend** (`pages/BalanceTiersPage.js`) :
- Toggle "Vue resumee" / "Detail par lot" dans le dialog de detail proprietaire
  (defaut = Vue resumee). Passe `group_by_owner` a l'endpoint.
- Le toggle n'apparait que pour les proprietaires (pas les fournisseurs).

**Tests** :
- `test_iter90bv_group_movements_by_owner.py` (6/6) : helper unit (merge,
  fund calls distincts, comptes distincts, paiements FI, fallback entry_id).
- `test_iter90bv_e2e_situation_compte_grouped.py` (2/2) : endpoint HTTP
  (3 lots + 1 VE => 1 mouvement en vue groupee, 3 mouvements en detail),
  PDF genere sans erreur en mode groupe.

**Regle metier** : le PDF envoye au proprietaire est toujours en vue resumee ;
le detail par lot reste accessible aux syndics pour audit via le toggle UI.


### Iter90be (Feb 2026) - Detection stricte doublons fournisseurs avec particules juridiques

- **Bug** : "Finlead" et "SRL Finlead" (meme entite) coexistaient en base (44000002
  et 44000004 dans le bilan PASSIF). Cause : `_norm_name` NE FILTRAIT PAS
  les particules juridiques (sa, sprl, srl, sarl, sas, scrl, asbl, ...).
  Ratio SequenceMatcher = 0.78 < seuil 0.80 => aucun blocage.
- **Fix** : ajout de `_LEGAL_PARTICLES` (constantes fr/nl/en). `_norm_name`
  filtre les particules AVANT tri alphabetique + gestion ponctuation ("S.A."
  -> "sa" -> filtre). Fallback si nom uniquement particule (garde-fou).
- **Effet immediat** : "Finlead" ↔ "SRL Finlead" ↔ "Finlead SRL" ↔ "AXA S.A."
  ↔ "AXA SA" => tous EXACT match via `find_duplicate_supplier`.
- **Doublons existants** : l'user peut fusionner via `/admin/duplicates` (UI
  deja implementee - iter79). Les groupes seront maintenant correctement
  detectes grace au nouveau normalizer.
- Tests : `test_iter90be_supplier_legal_particles.py` (E2E API) +
  `test_iter78_suppliers_duplicate.py::test_normalize_helpers` mis a jour.

### Iter90ba (Feb 2026) - Simplification date picker Bilan

- **Probleme UX** : sur l'onglet Bilan (ReportsPage), 2 date pickers (Du/Au)
  etaient affiches, alors que le bilan est un arrete a date (utilise
  UNIQUEMENT `date_to` cote backend). L'user devait cliquer 4+ fois pour
  saisir 2 dates identiques.
- **Solution** :
  1. Auto-remplissage : quand l'user selectionne un exercice fiscal,
     `dateFrom` = fy.start_date et `dateTo` = fy.end_date sont pre-remplis
     automatiquement (utile aussi pour Balance / Resultat).
  2. Onglet Bilan : DateFilters remplace par un layout unique en ligne
     (Exercice fiscal -> Arrete au -> Vue -> Charger le bilan -> PDF Bilan)
     avec **un seul** input date "Arrete au".
  3. Badge d'info sous les champs : "Exercice YYYY : du DD/MM/YYYY au
     DD/MM/YYYY" pour clarifier la periode. Warning "arrete intermediaire"
     si l'user modifie manuellement la date d'arrete.

### Iter90az (Feb 2026) - Auto-apprentissage nature de depense par fournisseur

- **Nouvel endpoint** : `GET /api/invoices/supplier-suggestion?supplier=X&copropriete_id=Y`
  - Retourne la nature de depense la plus utilisee historiquement pour ce
    fournisseur au sein de l'ACP (Chinese walls STRICT).
  - Comptage : factures mode 1-nature (+1) et factures mode multi-lignes
    (+1 par ligne). Nature majoritaire renvoyee avec `account_number` et
    `distribution_key_id` associes.
- **UI Invoices form** : quand le user selectionne / saisit un fournisseur,
  le formulaire interroge l'endpoint et **pre-remplit automatiquement** la
  nature de depense (+ compte PCMN + cle de repartition).
  - **Non-destructif** : si l'utilisateur a deja choisi une nature manuellement,
    la suggestion est ignoree. Idem sur la 1ere ligne du mode multi-lignes.
  - Toast informatif : "Nature apprise : X (N factures de Y)".
- **Champ Commentaire par ligne (renommage UX)** : le libelle "Description"
  des lignes multiples a ete renomme "Commentaire" avec un placeholder qui
  affiche un extrait de la description generale. Comportement backend inchange :
  vide -> heritage de la description generale ; rempli -> remplace uniquement
  pour cette ligne dans la liste des depenses (`expense_rows.py`).
- Tests : `tests/test_iter90az_supplier_learning.py` (6 scenarios : sans
  historique, majorite simple, egalite, case-insensitive, chinese walls, vide).


### Iter90as/at (Feb 2026) - Deploiement K8s robuste + Hardening securite P0

**Iter90as - Fix deploiement K8s** :
- Startup event : chaque `create_index` + `seed_admin` + `seed_pcmn` + write
  `/app/memory/test_credentials.md` enveloppes dans un try/except individuel.
- Cause racine du timeout de readiness probe en prod : ecriture de
  `/app/memory/test_credentials.md` sur un filesystem read-only ou volume
  ephemere en Kubernetes -> exception -> pod ne devient jamais ready.
- Corrections idempotentes : preview + prod n'ont plus d'impact sur le startup.

**Iter90at - Hardening securite P0 (5 protections)** :

1. **Rate limiting** (slowapi) :
   - Global : 100 req/min/IP par defaut (anti-DDoS applicatif).
   - `/api/auth/login` : 10/min/IP.
   - `/api/auth/register`, `/api/auth/forgot-password`, `/api/auth/reset-password`
     : 5/min/IP.
   - Detecte l'IP via `get_remote_address` (compatible reverse-proxy).

2. **Security headers** (middleware `@app.middleware("http")` place APRES
   auth_middleware pour etre outermost, wrap les 401/403 early returns) :
   - `X-Frame-Options: SAMEORIGIN` (anti-clickjacking)
   - `X-Content-Type-Options: nosniff` (anti-MIME sniffing)
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (HSTS 1 an)
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
   - `Content-Security-Policy: default-src 'self'; ...` (CSP basique)

3. **CORS restreint** : `_build_cors_origins()` deja env-driven, plus de fallback `*`.

4. **Cookies auth durcis** :
   - Helper `_set_auth_cookie()` unifie (login, register, refresh, oauth).
   - `COOKIE_SECURE` env-driven (false preview localhost, **true prod HTTPS**).
   - `COOKIE_SAMESITE=lax` par defaut (CSRF-safe + compat redirects).
   - Toutes les occurrences `set_cookie(secure=False, ...)` remplacees.

5. **Body size limit** : `BodySizeLimitMiddleware` renvoie 413 si
   Content-Length > `MAX_BODY_MB` (defaut 20 MB) - refuse tout upload DoS
   avant meme la lecture du body par Starlette.

**Nouvelles env vars** (backend/.env) :
- `COOKIE_SECURE=false` (mettre `true` en production HTTPS)
- `COOKIE_SAMESITE=lax`
- `MAX_BODY_MB=20`

**Dependance ajoutee** : `slowapi==0.1.10` (+ `limits`, `wrapt`, `Deprecated`).

**Tests** (`test_iter90at_security_hardening.py` - 4/4) :
- 6 security headers presents sur `/api/health`.
- Payload 25 MB -> HTTP 413.
- 12 login rapides -> HTTP 429 declenche.
- Cookie access_token contient HttpOnly + SameSite + Path=/.

**⚠️ Action prod** : lors du redeploiement, ajouter `COOKIE_SECURE=true` dans
le Deployment Panel Emergent (Secrets) pour que les cookies auth soient
transmissibles uniquement en HTTPS.


### Iter90ar (Feb 2026) - Sante comptable : rendu lisible + adaptation situation

**Ticket utilisateur** :
- "rendre cette vue plus lisible et hestetique c'est tres moche et complique"
- "il considere toujours qu'il n'y a qu'un proprietaire alors que tous les
  proprietaires sont en retard etant donne que je n'ai pas comptabilise les
  extraits de compte. Le tableau de bords doit s'adapter a la situation comptable"

**Bug 1 - Rendu moche (iter90ak avait un mismatch de noms de categories)** :
- Backend emet `owners_late` et `invoices_overdue`
- Frontend attendait `owners_overdue` et `invoices_over_60d`
- Consequence : chute sur le fallback `JSON.stringify(it)` -> affichage JSON brut.

**Fix 1** (`DashboardPage.js`) : refonte complete du rendu :
- Categories correctement mappees (owners_late, invoices_overdue, duplicates,
  orphans, unbalanced, + nouveau owners_pending).
- Cartes visuelles avec icone emoji + couleur par categorie (rouge/orange).
- Tables HTML propres au lieu de listes JSON.
- Header categorie avec libelle et badge.
- Compteur "... et N autre(s) non affiche(s)" style separe.

**Bug 2 - Tableau de bord ne reflete pas la situation comptable** :
- Regle existante : owner apparait "en retard" SEULEMENT si solde tier > 0.01.
- Cas de figure : appels de fonds emis, VE non encore generees ou extraits non
  lettres -> solde tier = 0 -> proprietaire absent du dashboard.

**Fix 2** (`health_audit.py`) : ajout d'une seconde piste `owners_pending` :
- Owner avec appel non paye ET solde tier <= 0.01 (0 ou credit).
- Anomalie de severite "medium" (vs "high" pour les retards confirmes).
- Score impact reduit (-1 pt/owner max -10, vs -4/owner max -20).
- Champ `stats.owners_pending` ajoute a la reponse.

**Frontend** : nouvelle categorie `owners_pending` avec icone hourglass, couleur
orange, message explicatif : "solde tier n'est pas debiteur, cause probable :
VE non generee OU extrait bancaire pas encore lettre".

**Note pedagogique** : chaque section d'anomalie affiche maintenant un texte
italique en dessous expliquant le critere metier pour aider le syndic a
comprendre la donnee (transparence + pedagogie).


### Iter90aq (Feb 2026) - Template appris par fournisseur (skip IA)

**Idee** : monitorer les corrections manuelles pour construire un "template
appris" par fournisseur -> a la Nieme facture du meme emetteur, l'IA n'est
plus necessaire, extraction quasi 100% fiable en <100ms.

**Backend** :

- Nouveau fichier `routes/invoice_templates.py` :
  - Collection Mongo `invoice_templates` clef par (supplier_id, copropriete_id).
  - `learn_template()` : trouve chaque valeur user dans le texte brut via ses
    variantes (dates DD/MM/YYYY belge, montants europeens/anglais avec point
    ou virgule decimale, espaces milliers) et memorise les 6 derniers mots
    avant la valeur comme `anchor`.
  - `apply_template()` : cherche l'anchor dans le nouveau texte, regex par
    type de champ dans les 120 chars suivants -> extrait valeur normalisee.
  - `try_apply_supplier_template()` : point d'entree pour invoice_ai.py.
  - Endpoint HTTP `POST /api/invoice-templates/learn` +
    `GET /api/invoice-templates/{supplier_id}`.

- `routes/invoice_ai.py` modifie :
  - Extrait raw_text du PDF AVANT l'appel IA.
  - Detecte le supplier via BCE/VAT dans le texte (regex `BE 0xxx.xxx.xxx`)
    + lookup DB.
  - Si supplier trouve + template existe : applique.
  - Si `number` + `date` + `total_amount` tous trouves par template ->
    `_extraction_source="template"`, aucun appel Claude.
  - Sinon : appel IA + injection des champs du template en priorite (le
    template a une confiance plus haute quand il matche).
  - Reponse enrichie avec `raw_text` + `supplier_id_guess` pour le learn cote UI.

**Frontend** (`InvoicesPage.js`) :

- Nouveau state : `lastAiRawText`, `lastAiSupplierIdGuess`, `lastAiValues`,
  `lastAiExtractionSource`.
- Apres extract, ces states sont peuples.
- Toast contextualise :
  - "⚡ Extraction template : donnees reprises du profil fournisseur (rapide, sans IA)"
  - vs "Donnees extraites par IA - verifiez avant enregistrement."
- Apres save reussi d'une NOUVELLE facture (pas edit) : POST /learn avec
  `{supplier_id, raw_text, user_values: {number, date, due_date, total_amount, vat_amount}}`.
- Best-effort : erreur learn silencieuse (console.warn).

**Formats de nombre supportes** :
- `1234.56` / `1234,56` / `1234` (arrondi)
- `1 234,56` (europeen espace milliers)
- `1,234.56` (anglais)
- `1.234,56` (belge/europeen classique) - critique, ajoute au fix

**Tests** (`test_iter90aq_invoice_template_learning.py` - 9/9) :
- Extract pattern date DD/MM/YYYY belge -> anchor "date facture" capture.
- Extract pattern montant "1.234,56" avec point milliers.
- Valeur absente du texte -> None.
- Apply template : anchor "Date facture" trouve nouvelle date sur nouveau texte.
- Apply template : anchor "Total TTC" trouve nouveau montant.
- Apply template : anchor absent -> champ omis (pas d'erreur).
- Endpoint /learn : POST 200, template cree.
- Endpoint /learn : appels multiples -> sample_count incremente (3).
- Endpoint /learn : sans supplier_id -> 400.

**Cycle vertueux** : plus le syndic importe des factures d'un meme fournisseur
(ex. syndic professionnel avec 3 factures/mois de la meme SPRL d'entretien),
plus le template devient precis. Apres 3-4 factures corrigees, l'IA n'est
quasi plus appelee pour ce fournisseur.


### Iter90ap (Feb 2026) - Fiabilite reconnaissance IA dates factures

**Ticket utilisateur** : "la reconnaissance des documents n'est pas toujours
correcte attention aux dates" (regression apres bascule iter90an vers Haiku 4.5).

**Diagnostic** : Haiku 4.5 est rapide mais moins fiable sur les dates belges
DD/MM/YYYY ambigues avec le format US MM/DD/YYYY. Une facture 05/06/2026 (5 juin)
pouvait etre interpretee comme 6 mai.

**Corrections** (`routes/invoice_ai.py`) :

1. **Haiku 4.5 -> Sonnet 4.6** (recommande par playbook) : meilleur ratio
   vitesse/precision, plus robuste sur les cas ambigus. Le path vision garde
   Sonnet 4.5 (qualite OCR critique).

2. **System prompt enrichi** avec section "CRITICAL - BELGIAN DATE FORMAT RULES" :
   - DD/MM/YYYY inconditionnel (jamais US MM/DD/YYYY)
   - Mois FR : janvier..decembre + NL : januari..december
   - Distinguer explicitement `date` (issue) vs `due_date` (echeance)
     avec labels typiques FR/NL/EN
   - Sortie ISO YYYY-MM-DD obligatoire
   - `""` si absent, NE PAS inventer

3. **Validation post-extraction** (nouveau `_validate_dates()` dans l'endpoint) :
   - Annee hors [2020, 2035] -> reset a "" + entree dans `_date_warning`
   - Format non parseable ISO -> reset a "" + entree dans `_date_warning`
   - `due_date < date` -> entree "IA a probablement inverse jour/mois"
   - Warnings agreges dans `result["_date_warning"]` (string separee par " ; ")

4. **Frontend** (`InvoicesPage.js`) :
   - Toast d'alerte 10s : "Dates a verifier : ..."
   - Warning ajoute au hint textuel affiche dans le formulaire

**Tests** (`test_iter90ap_invoice_date_validation.py` - 7/7) :
- Marqueurs de validation presents dans le code source.
- Prompt contient toutes les regles de date belge.
- Annee < 2020 ou > 2035 -> flag + reset.
- Format non-ISO ("15/06/2026") -> flag + reset.
- `due_date < date` -> flag "inverse jour/mois".
- Dates valides et coherentes -> pas de warning.
- Dates absentes -> pas de warning.

**Trade-off** : Sonnet 4.6 est ~2x plus lent que Haiku (mais 2x plus rapide
que Sonnet 4.5). Le gain de precision sur les dates justifie largement le cout
en latence supplementaire (evite les corrections manuelles apres coup).


### Iter90ao (Feb 2026) - Anti-doublon facture enrichi (supplier + numero + montant)

**Demande utilisateur** : "il faut qu'il y ait une verification anti doublon
de facture check sur le nr de facture et le fournisseur et le montant".

**Etat avant** : `_check_invoice_duplicate` bloquait UNIQUEMENT sur
(fournisseur + numero + ACP). Le montant n'etait pas verifie.

**Fix** (`routes/invoices.py::_check_invoice_duplicate`) : deux regles cumulatives
sont maintenant executees dans la meme passe async sur les factures existantes :

- **Regle 1 (inchangee)** : meme (fournisseur normalise, numero normalise, ACP)
  -> HTTPException 409, message "numero identique".
- **Regle 2 (nouvelle)** : meme (fournisseur normalise, montant a 0.01 EUR pres,
  ACP) ET date +/- 3 jours -> HTTPException 409, message "montant + fournisseur
  + date proche". Attrape le cas OCR qui mal-lit un chiffre du numero de
  facture ou une saisie manuelle divergente.

**Choix fenetre 3 jours (inclusive)** :
- Trop court (0 jour) : rate les cas ou la date de facture differe de 1-2 jours.
- Trop long (>7 jours) : faux positifs sur les recurrences mensuelles/hebdo
  avec montant identique.
- 3 jours est le meilleur compromis pour les factures reelles.

**Message d'erreur** : affiche systematiquement supplier + numero + montant
+ date de la facture existante et propose des pistes de resolution.

**Tests** (`test_iter90ao_invoice_duplicate_amount.py` - 6/6) :
- Rule 1 : meme supplier+numero -> 409.
- Rule 2 : meme supplier+montant a J+2 (numeros differents) -> 409.
- Rule 2 : meme supplier+montant a J+5 -> autorise (fenetre depassee).
- Fournisseur different -> autorise.
- Montant different -> autorise.
- Borne 3 jours inclusive : J+3 -> 409, J+4 -> autorise.


### Iter90an (Feb 2026) - Acceleration reconnaissance IA factures

**Demande utilisateur** : "accelerer la reconnaissance IA des factures".

**Diagnostic** : le goulot d'etranglement etait l'appel Claude Sonnet 4.5,
lourd meme pour de l'extraction JSON structuree simple sur des factures 1-2 pages.

**Optimisations** (`routes/invoice_ai.py`) :

1. **Path texte : Claude Sonnet 4.5 -> Haiku 4.5**
   (`claude-haiku-4-5-20251001`) : 3-4x plus rapide sur du JSON structure,
   qualite equivalente pour cette tache.
2. **Path vision (PDF scannes) : garde Sonnet 4.5** : qualite OCR critique,
   pas de compromis. Basculement automatique via `use_vision = not text`.
3. **max_chars PDF text : 8000 -> 4000** : suffisant pour facture belge 1-2 pages,
   ~50% moins de tokens input.
4. **Liste PCMN classe 6 : 60 -> 40 comptes** : moins de tokens system prompt.
5. **Cache in-memory PCMN par ACP** (TTL 5 min via `_PCMN_CACHE` + `_get_pcmn_cached`) :
   evite le double fetch DB (class 6 + full list). Renvoie `(class6_list,
   valid_accs_set)` en une fois.
6. **Parallelisation post-processing** : PCMN existence check + supplier match
   lances via `asyncio.gather`.
7. **valid_accs en memoire (set O(1))** : plus de round-trip Mongo pour valider
   les comptes suggeres sur les lignes AI.

**Gain estime** : facture texte simple 3s -> 0.8s (**~4x plus rapide**). Facture
scannee : gain marginal ~10% (Sonnet reste requis pour qualite OCR).

**Tests** (`test_iter90an_invoice_ai_speedup.py` - 4/4) :
- `test_pcmn_cache_hit_avoids_refetch` : cache hit < 20ms, TTL expiration OK.
- `test_model_selection_by_use_vision` : Haiku pour texte, Sonnet pour vision.
- `test_pcmn_list_limit_40` : plafond 40 comptes classe 6.
- `test_max_chars_pdf_text` : default = 4000.


### Iter90am (Feb 2026) - Sidebar syndic redesigne avec accents colores

**Demande** : sidebar plus design, plus dynamique, plus grand avec des couleurs,
visuellement attractif.

**Changements** :
- **Ajout d'un champ `accent`** sur chaque section dans `Layout.js` :
  - Gestion -> blue
  - Comptabilite -> violet
  - Finance -> emerald
  - Rapports -> amber
  - Plateforme (superadmin) -> amber
  - Compte -> slate
- **`sidebar-link` enrichi dans `App.css`** :
  - Icone dans wrapper `w-8 h-8` (au lieu de nue), prend la couleur d'accent au hover et active
  - Icone plus grande : 16px -> 18px, strokeWidth 1.5 -> 2
  - Label plus grand : 13px -> 13.5px, font-medium
  - Padding plus genereux : `py-2` -> `py-2.5`
  - Rounded plus prononce : `rounded-md` -> `rounded-lg`
  - Transition sur `transform` et `padding-left` (slide de 2px au hover)
  - Etat actif : barre laterale gauche coloree 4px + gradient horizontal de fond
    + icone dans cercle avec shadow
- **Section headers** enrichis : petite pastille lumineuse (glow effect) a la
  couleur d'accent + label toujours en `tracking-[0.2em]` uppercase.

**Fichiers** :
- `frontend/src/components/Layout.js` (rendu nav + array sections)
- `frontend/src/App.css` (variants `.sidebar-link-{color}` + `.sidebar-accent-dot-{color}`)

**Impact visuel** : chaque section a maintenant une identite chromatique claire,
l'utilisateur voit du premier coup d'oeil ou il est (barre laterale + icone
coloree), l'ensemble reste sobre grace au fond dark slate.


### Iter90al (Feb 2026) - Actions rapides adaptatives (dashboard syndic)

**Demande** : rendre la carte "Actions rapides" du tableau de bord syndic
plus intuitive, les tuiles doivent s'adapter automatiquement selon l'usage
du gestionnaire.

**Design** : composant reutilisable `AdaptiveQuickActions.js` sans dependance
supplementaire (framer-motion pas installe, CSS transitions natives).

- **Traking usage** : localStorage cle `qa-stats:u:{userId}-c:{acpId}` (segmentation
  utilisateur + ACP -> chaque syndic voit son propre ordre par ACP).
- **Scoring** : `score = count * (1 + recency_boost)` avec `recency_boost = max(0, 14-days)/14`
  (favorise les actions recentes, decay lineaire 2 semaines).
- **UX visuelle** :
  - Top 1 : anneau dore + etoile pleine dans le coin (badge "Favori")
  - Autres tuiles utilisees : compteur discret opacite 40% dans le coin
  - Hover : scale 1.03 + translate-y-[-0.5] + shadow-md
  - Fade-in staggered a l'entree
- **Catalogue** : 10 actions courantes du workflow syndic (Facture, Ecriture,
  Extrait, Appel fonds, Balance tiers, Mutation lot, Proprietaires, Fournisseurs,
  Rapports, Doublons)
- **Progressive disclosure** : 6 tuiles visibles par defaut + toggle "Voir toutes
  les actions ({N} de plus)" pour reveler les 4 autres
- **Grille responsive** : `grid-cols-2 sm:grid-cols-3`

**Test manuel** : reorder valide en injectant `qa-stats` dans localStorage, la
tuile en #1 apparait avec anneau dore + etoile, les compteurs sont visibles sur
les autres, expand/reduce fonctionne.


### Iter90aj (Feb 2026) - Appel de fonds retroactif : owner = proprietaire a la DATE de l'appel

**Ticket user (PROD Acacia)** : "le fonds de reserve est bien assigne a Mme
Teuwen alors que l'appartement etait encore a Matexi au 01.10.2025, date de
la mutation le 17/11/2025 !!"

**Bug racine** : `_distribute_amount` utilisait `lot.owner_id` (proprietaire
courant post-mutation = Teuwen). Quand le budget 2026 etait vote APRES la
mutation avec des appels dates 01/10/2025 (retroactifs), la VE reserve/
roulement/provisions etait debitee au current owner (Teuwen) au lieu du
proprietaire en place a la date de l'appel (Matexi).

Ce bug est distinct d'iter90ai (qui excluait la reserve du decompte de mutation
OD, mais ne corrigeait pas l'affectation initiale de la VE lors de la creation
de l'appel).

**Backend** (`routes/fund_calls.py`) :
- Nouveau helper `_resolve_owner_at_date(lot_id, target_date, fallback)` :
  marche dans `mutations_by_lot` pour trouver le proprietaire a la date cible
  (from_owner du premier segment, switche vers to_owner a chaque
  `mutation.sale_date <= target`).
- Nouveau helper `_rebind_owner_at_call_date(entries, call_date)` : reprend
  les entries de `_distribute_amount` et re-affecte `owner_id`/`owner_name`/
  `vcs_code` au proprietaire correct. PAS de proratisation (regle metier :
  reserve/roulement = injection one-shot).
- Applique aux 3 chemins :
  1. `reserve_dist` / `roul_dist` inline dans `_generate_from_budget` (appel #1)
  2. `dist` dans `_generate_independent_series` (fonds avec frequency propre) --
     c'est le path utilise par ACP Acacia (VE reserve/roulement Annuel 1/1)
  3. `distribution` dans `POST /api/fund-calls` pour call_type in
     (reserve, roulement, special) -- appels standalone

**Tests** (`test_iter90aj_reserve_owner_at_call_date.py` - 4/4) :
- `test_standalone_reserve_before_mutation_uses_seller` : reserve 01/10/2025
  (pre-mutation 17/11/2025) -> owner_id = Matexi (seller).
- `test_standalone_reserve_after_mutation_uses_buyer` : reserve 15/12/2025
  (post-mutation) -> owner_id = Teuwen (buyer, current owner).
- `test_standalone_roulement_before_mutation_uses_seller` : roulement pre-mutation
  -> Matexi.
- `test_no_mutation_uses_current_owner_regression` : sans mutation, comportement
  inchange -> Teuwen (current owner).

**Regression cumulative** : 13/13 tests iter90 mutation-related passent
(iter90aj + iter90ai + iter90ah + iter90ag).

**Action prod ACP Acacia** : apres redeploiement, les appels de fonds
existants avec mauvaise attribution doivent etre supprimes puis regeneres
(via bouton "Regenerer" du budget) pour beneficier du fix. Les VE journal
entries sont automatiquement recreees a partir de la nouvelle distribution.


### Iter90ai (Feb 2026) - Fonds de reserve exclu du decompte de mutation

**Regle metier validee** :
- A) Fonds de reserve vote avant la mutation -> 100% a charge vendeur, jamais transferable a l'acheteur.
- B) Fonds de reserve : JAMAIS de transfert V/A (ni via OD MUT-P prorata, ni via MUT-F futurs).
- C) Fonds de roulement : transfert unique a date de mutation, jamais proratise (deja conforme).
- D) Seules les provisions pour charges sont proratisees.

**Bug initial** : quand `call_type='provisions'` contient `reserve_amount > 0`
(cas legacy : injection reserve dans un appel provisions), la part reserve etait
incluse dans les OD MUT-P (prorata) et MUT-F (futurs).

**Backend** (`routes/properties.py::_compute_mutation_breakdown`) :
- Apres calcul de `amount_lot` pour un call `provisions`, si `c_reserve > 0` et
  `c_total > 0` : `amount_lot = amount_lot * ((c_total - c_reserve) / c_total)`.
- Applique avant le calcul du prorata (OD MUT-P) et avant la reference dans
  `future_calls` (OD MUT-F).
- Appels standalone `call_type='reserve'` deja filtres en amont (L893).

**Tests** (`test_iter90ai_mutation_exclude_reserve.py` - 3/3) :
- `test_pure_provisions_unchanged` : reserve_amount=0 -> prorata=1013.33 (regression, comportement inchange).
- `test_provisions_with_reserve_excluded` : reserve_amount=200/1200 -> quote-part ajustee a 1000 -> prorata acheteur = 844.44 (au lieu de 1013.33).
- `test_standalone_reserve_ignored` : call_type='reserve' seul -> aucun prorata ni appel futur.

**Convention prorata** : `days_after = (period_end - sale_dt).days + 1` -> la date
de vente est comptee cote acheteur (buyer inclut sale_dt).


## Implemented
### Iter90ah (Feb 2026) - Appels de fonds standalone (reserve / roulement / special)

**Ticket user** : "Il faut pouvoir creer que des appels de fonds de reserve,
fonds de roulement ou des fonds specifique dans la parties des appels. Ces
appels ne sont pas bases sur un budget mais sur une somme donc il faut juste
la calculer sur la cle choisie."

**Etat existant** : le formulaire "Nouvel appel de fonds" exposait deja les
4 types (provisions, reserve, roulement, special) via `call_type`, et le
backend POST /api/fund-calls distribuait sur la cle. Mais :

1. **Bug comptable** : pour `call_type='reserve'` standalone (sans reserve_amount
   injecte via budget), l'ecriture VE creditait 700000 (provisions) au lieu
   de 160 (Fonds reserve). Idem pour roulement (creditait 700000 au lieu de 100).
2. **UI incoherente** : dropdown "Cle de repartition" utilisait "Par tantiemes
   (defaut)" hardcode au lieu d'aligner avec iter90aa.
3. Le mapping `account_map` de `generate_journal_entries` (endpoint manuel)
   n'avait pas d'entree pour `roulement`.

**Backend** :
- `auto_entries.py::generate_sale_entry` : detecte call_type='reserve' /
  'roulement' sans reserve_amount/roulement_amount injecte -> redirige la
  totalite du montant sur la bonne categorie. Le prorata par owner utilise
  alors accs["reserve"] (40010XXX) ou accs["provisions"] (40000XXX) pour
  le debit, credit 160 ou 100 respectivement.
- `routes/fund_calls.py::generate_journal_entries` : ajout du mapping
  `"roulement": ("400000", "100")`.

**Frontend** (`pages/FundCallsPage.js`) :
- `useMemo defaultKeyId` (is_default sinon premiere cle).
- `openCreate` initialise `distribution_key_id` avec `defaultKeyId`.
- Dropdown cle : retire "Par tantiemes (defaut)" hardcode, affiche
  uniquement les cles creees avec suffixe " (defaut)" sur is_default.
  Placeholder "Aucune cle - creez-en une" si vide.

**Tests** (`test_iter90ah_standalone_reserve_roulement.py` - 3/3) :
- Reserve standalone 10000 EUR sur cle 40/60 -> debits tier reserve
  4001001/4001002 (4000+6000), credit unique 160 = 10000. Assertion
  explicite "aucun credit sur 700000".
- Roulement standalone 5000 EUR -> debits tier provisions, credit unique 100.
- Provisions standalone 1000 EUR -> regression : credit 700000 comme avant.

**Regression complete** : 131/131 tests passent.

### Iter90ag (Feb 2026) - Prorata mutation temporis pour provisions

**Ticket user** : "Il faut appliquer les appels sur base du prorata pour les
provision pour charges donc meme si un budget est realise apres une mutation,
le calcul se fait sur le bon proprietaire (vendeur et acheteur) sur base
de la date de la mutation."

**Regle metier validee** :
- Si un lot subit une mutation V -> A pendant la periode couverte par un
  appel de provision, l'amount est splitte prorata TEMPORIS jour-a-jour :
  * V paye pour la periode `[period_start, mutation_date - 1]`
  * A paye pour la periode `[mutation_date, period_end]`
- Exemple canonique validee : mutation 15-03-2026 sur Q1 (01-01 -> 31-03,
  90 jours), montant lot 900 EUR -> V=73/90*900=730.00 EUR, A=17/90*900=170.00 EUR.
- S'applique UNIQUEMENT aux provisions (call_type='provisions', budget_lines).
- NE s'applique PAS a la reserve/roulement (injections one-shot).

**Backend** (`routes/fund_calls.py::_generate_from_budget`) :
- Pre-fetch de toutes les `mutations` de l'ACP en 1 query, group by lot_id
  trie par sale_date ASC.
- Nouvelle fonction `_split_lot_entry_by_mutations(entry, period_start,
  period_end)` : construction de segments (owner_id, days) avec cursor
  incremental. Dernier segment absorbe le drift d'arrondi.
- Applique `_split_lot_entry_by_mutations` sur les line_dist des
  budget_lines (provisions). PAS applique sur reserve_dist / roul_dist.
- Refactor de `_merge_into_lot_agg` : cle d'agregation passe de `lot_id`
  a `(lot_id, owner_id)` pour permettre plusieurs owners par lot dans
  une meme periode.
- Distribution serialise avec `prorata_days` + `prorata_total_days` sur
  les entrees issues d'une mutation (trace metier pour PDF appel de fonds).

**Tests** (`test_iter90ag_prorata_mutation_provisions.py` - 3/3) :
- test_prorata_mutation_mid_q1 : validation exacte 730/170 EUR sur Q1
  + Q2-Q4 = 100% acheteur.
- test_mutation_before_period_no_prorata : mutation antebellum -> V absent
  de toute la distribution, A prend 100%.
- test_no_mutation_single_entry : sans mutation, distribution reste
  identique a l'ancien comportement.

**Regression complete** : 128/128 tests passent (iter76 -> iter90ag).

### Iter90af (Feb 2026) - DELETE budget en cascade : fund_calls + journal_entries + balances

**Ticket user (PROD Acacia)** : "Une fois que des appels sont supprimes les
balances de tiers doivent s'adapter a cette realite. Concretement une fois
que le budget est remis en brouillon, les appels lies a ce budget sont
supprimes et le bilan et balance de tiers s'ajuste. Dans ce cas tu ne
met pas les balances de tiers a jour."

**Bug racine** : `DELETE /api/fiscal/budgets/{id}` supprimait seulement le
document `budget`. Les `fund_calls` (FK `budget_id`) et leurs ecritures
auto-generees (`journal_entries` source_type='fund_call') restaient en base
-> balances de tiers faussees (21987 EUR fantomes sur ACP Acacia).

**Backend** (`routes/fiscal.py::delete_budget`) :
- Signature : `delete_budget(budget_id, force=False)`.
- Fetch `fund_calls.budget_id=budget_id` -> detecte les appels payes ;
  refuse si non-force. Message clair listant les appels payes.
- `force=True` + payes : delettre `bank_transactions.matched_fund_call_id`
  puis proceder.
- Pour chaque fund_call lie : `_delete_auto_entries(db, "fund_call", fc.id)`
  supprime les VE (delete_many, hard delete, aucune contrepassation).
- `db.fund_calls.delete_many({"budget_id": budget_id})` puis
  `db.budgets.delete_one(...)`.
- Retour : `deleted_fund_calls`, `unlettred_transactions`.
- Balance des tiers (`balance_tiers_owners`) etant calcul dynamique
  depuis journal_entries, elle se re-ajuste automatiquement.

**Frontend** (`pages/FiscalYearPage.js`) :
- Nouveau state `deleteConfirm` + fonctions `prepareDeleteBudget` et
  `confirmDeleteBudget`.
- `prepareDeleteBudget` : GET `/fund-calls?budget_id=X` -> compte les
  appels, somme le total, extrait le nombre de proprietaires impactes.
  Populate le state pour affichage.
- Dialog de confirmation `budget-delete-dialog` : nom du budget, nombre
  d'appels + montant + proprietaires impactes + warning rouge sur la
  suppression des ecritures. Ou message "aucun appel de fonds lie" si vide.
- Boutons Annuler + Confirmer (destructive).
- Bouton poubelle sur les budgets draft utilise le nouveau flow.

**Tests** (`test_iter90af_delete_budget_cascade.py` - 5/5) :
- test_delete_cascade_clears_calls_and_entries : cascade complete verifiee
  au niveau MongoDB.
- test_delete_paid_without_force_rejected : 400 clair avec message.
- test_delete_paid_with_force_unlettres_and_deletes : force delettre les
  bank_transactions puis supprime tout.
- test_delete_idempotence_returns_404 : re-suppression = 404.
- test_delete_without_calls_succeeds_trivially : deleted_fund_calls=0.

**Total tests : 69/69** (64 existants + 5 nouveaux iter90af).

### Iter90ae (Feb 2026) - Prevision de fusion avant execution (safety net)

**Ticket user** : "Voulez-vous que j'ajoute une prevision avant fusion ? Vu
le volume detecte (35+84 groupes), une confirmation renforcee eviterait
tout risque de fusion accidentelle sur les gros clusters comme AXA
Belgium x13." -> OUI.

**Backend** :
- `routes/duplicates.py::preview_owner_merge` (POST /api/admin/duplicates/
  owners/merge/preview) : compte lots (simple/multi), tx bancaires
  matchees, mutations (vendeur/acheteur), lignes de journal, details
  d'appel de fonds, champs qui seront enrichis. AUCUNE modification en base.
- `routes/suppliers.py::preview_supplier_merge` (POST /api/suppliers/merge/
  preview) : compte factures + tx bancaires matchees. AUCUNE modification.
- Meme validation que le merge reel : 400 si keep_id in remove_ids, 404 si
  IDs inconnus, 403 si chinese wall enfreint.

**Frontend** (`pages/AdminDuplicatesPage.js`) :
- Le bouton "Fusionner" declenche desormais POST /merge/preview d'abord.
- Nouveau Dialog `merge-preview-dialog` avec sections codees par couleur :
  * Vert = fiche a CONSERVER (nom + email/BCE/id)
  * Rouge = N fiches SUPPRIMEES DEFINITIVEMENT (liste tronquee scroll)
  * Bleu = X REFERENCES MIGREES avec breakdown par type
    (factures, lots, tx, mutations, ecritures comptables, etc.)
  * Orange = champs enrichis automatiquement (source + valeur)
- 2 boutons : "Annuler" (outline) + "Confirmer la fusion" (rouge).
- Etat 'previewLoading' propage sur le bouton "Analyse..." pour retour visuel.

**Tests** (`test_iter90ae_merge_preview.py` - 3/3) :
- test_owner_merge_preview_counts_correctly : owners avec lots + tx +
  mutations + journal + fund_call -> compteurs corrects, DB inchangee.
- test_supplier_merge_preview_counts_correctly : 5 factures + 2 tx ->
  counts corrects, iban+email enriches, DB inchangee.
- test_preview_rejects_keep_id_in_remove_ids : 400 avec message clair.

**Regression complete** : 64/64 tests passent (iter76 + iter83 x7 +
iter85 + iter90ab + iter90ac + iter90ae).

**Verification manuelle** : Screenshot dialog affiche 12 AXA Belgium a
supprimer, 3 references migrees (1 facture + 2 tx), 4 champs enrichis
(vat_number, iban, bic, email).

### Iter90ad (Feb 2026) - Menage doublons proprietaires + fournisseurs cross-ACP

**Ticket user** : "il faut aussi permettre d'avoir acces a la base de donnee
des proprietaires et fournisseurs de l'ensemble des ACP sans filtre. je
vois 2 fois le meme proprietaire et j'aimerais faire le menage idem pour
les fournisseurs."

**User choix (via ask_human)** :
- Doublons owner : nom similaire + email OU VCS identique
- Doublons supplier : N BCE uniquement
- Fusion : merge complet (migration references + suppression source)
- Page dediee /admin/duplicates avec preview

**Frontend** :
- `pages/OwnersPage.js` : correction du bug du toggle "Afficher tous". Avant
  requerait `selectedCopro==='all' && showAll` -> ne marchait jamais avec
  ACP specifique choisie. Maintenant `showAll` seul suffit, envoie
  `syndic_wide=true` pour bypasser le filtre backend. Badge dynamique
  "(ACP active)" ou "(toutes ACPs)". Bouton "Detecter les doublons"
  (data-testid="owners-detect-duplicates-btn") -> /admin/duplicates?tab=owners.
- `pages/SuppliersPage.js` : label "Vue globale : tous les fournisseurs de
  vos ACPs" + bouton "Detecter les doublons".
- `pages/AdminDuplicatesPage.js` : lit `?tab=` de l'URL au mount pour deep
  link direct sur owners ou suppliers.
- `lib/api.js` : ajout de `/admin/duplicates` a `GLOBAL_PATH_PREFIXES` pour
  couper l'auto-injection copropriete_id (essentiel pour scan cross-ACP).

**Backend** : deja implemente (routes/duplicates.py + routes/suppliers.py::
merge_suppliers). RAS pour cette iteration.

**Regression fix concomitante** : les 4 tests iter83 (acacia_teuwen,
linked_lots, pdf_3_lots_via_key, prorata_lot_share) qui echouaient depuis
iter90ab (mutation exige cle par defaut) sont mis a jour pour creer la
fixture. Test acacia_teuwen : assertion sum_debit ajustee pour inclure
future_calls (comportement iter84+).

**Tests** :
- E2E : `test_iter90ad_duplicates.py` (6/6) - detection global scope, merge
  owners avec migration lots, merge suppliers, guard keep_id != remove_ids.
- Regression complete mutation : 28/28 tests passent
  (iter76 + iter83 x7 + iter85 + iter90ab + iter90ac).

**Feedback visuel utilisateur** : 35 groupes doublons fournisseurs
detectes (AXA Belgium x13 !), 84 groupes doublons proprietaires (Vendeur
x30 dus a import). 437 proprietaires et 198 fournisseurs dans le scope
superadmin.

### Iter90ac (Feb 2026) - Exclusion explicite de lots sur les cles de repartition

**Ticket user** : "Ajouter le concept d'exclusion sur les lots d'une cle de
repartition [...] certains lots ne participent pas a certaines categories
de charges (ex. lot commercial au rez exclu des charges d'ascenseur).
L'exclusion doit etre explicite, pas deduite d'un share=0."

**Backend** :
- `routes/invoices.py::DistKeyLot` : nouveau champ `excluded: bool = False`
  (backward compat).
- `routes/properties.py::_compute_mutation_breakdown` : `key_total_quotity`
  ignore les lots `excluded=True` ; si le lot mute est exclu ->
  `lot_share_in_key=0`, `roulement_quota=0`, `lot_excluded_from_key=True`
  (pas d'erreur HTTP 400). Nouveau champ retourne : `lot_excluded_from_key`.
- `routes/fund_calls.py` : 3 endroits patches (POST fund_call classique,
  fund_call auto-generated, compute per-lot amounts) - filtrent
  `excluded=True`.
- `routes/invoices.py` : 2 endroits patches (distribution auto invoice
  + direct dispatch) - `active_kls = [l for l in key["lots"] if not
  l.get("excluded")]`.
- `pdf_decompte.py::dk_index` : construction ignore les lots exclus.
- `scripts/recompute_invoice_distribution_lines.py` : `active_lots`
  exclut les `excluded=True`.

**Frontend** (`pages/InvoicesPage.js`) :
- Init form clef : `excluded: false` par defaut a la creation ; preserve
  la valeur a l'edition.
- Colonne "Exclu" (checkbox) dans le form d'edition des cles avec
  `data-testid="key-lot-excluded-{i}"`.
- Ligne excluee : `bg-slate-100 opacity-50`, Input share disable, pct = "—".
- Total en pied de table : "Total (hors exclus)" - ignore les exclus.
- Badge coherence + hasZero : ne se declenchent QUE sur les lots non exclus.
- Liste des cles : "3 lots (+1 exclu)" au lieu de "4 lots".
- Boutons "Repartir egalement", "Reprendre tantiemes", "Normaliser /1000"
  ne modifient PAS les lots exclus.

**Tests** :
- Unit : `test_iter90ac_lot_exclusion.py` (2/2) - scenario lot exclu +
  backward compat cle legacy sans champ excluded.
- E2E HTTP : `test_iter90ac_e2e_exclusion_http.py` (5/5) - POST
  distribution-keys, mutate-preview, invoices, fund-calls.
- Total : 21/21 tests passent (16 regressions iter76/83/85/90ab + 5 e2e).

### Iter90ab (Feb 2026) - Mutation : fonds de roulement calcule sur la cle par defaut

**Regle metier appliquee** :
- **Provisions** -> cle par ligne budgetaire (via `_compute_lot_amount_in_call`)
  INCHANGE.
- **Fonds de roulement / reserve** -> cle de repartition GENERALE (celle
  marquee `is_default=true`), avec possibilite de derogation future via
  distribution_key_id override.
- `lot.quotity` n'est plus la source de verite pour la mutation.

**Backend** (`routes/properties.py::_compute_mutation_breakdown`) :
- Lookup de la cle `is_default=true` sur la copropriete. Levee d'erreur 400
  claire si absente ("Marquez une cle comme 'par defaut' dans Factures >
  Cles de repartition").
- Verification que le lot est dans la cle. Sinon 400 avec message identifiant
  le lot et la cle.
- `roulement_quota = fonds_roul_total * (lot_share_in_key / key_total_quotity)`.
- Retour : `lot_share_in_key`, `key_total_quotity`, `default_key_name`
  (au lieu de `lot_quotity`, `total_quotity` supprimes).

**Frontend** (`pages/LotsPage.js`) :
- Preview simple : "Part du lot dans la cle par defaut ({default_key_name})"
  au lieu de "Quotites lot / total ACP".
- Preview groupee (cascade parent+enfants) : colonne "Part / cle" au lieu
  de "Quotite".

**Tests** :
- `/app/backend/tests/test_iter90ab_mutation_roulement_default_key.py` (3/3) :
  * `test_roulement_uses_default_key_not_quotity` : lot quotity 1000 mais
    share=500/1500 dans la cle -> 3000 * 500/1500 = 1000 EUR (et non 2000
    qui serait le calcul quotity). Verifie aussi que les anciens champs
    `lot_quotity`/`total_quotity` ne sont plus exposes.
  * `test_roulement_400_when_no_default_key` : absence de is_default = 400.
  * `test_roulement_400_when_lot_absent_from_default_key` : lot pas dans
    la cle = 400 avec identification du lot.

**Regression** : Tests iter76/iter83/iter85 (mutation prorata + grouped
cancel + PDF decompte) mis a jour pour creer une cle par defaut dans leur
fixture. 11/11 verts. Total 14/14 tests mutation passent.

**Evolution future prevue** : parametre optionnel `distribution_key_id` sur
l'endpoint de mutation pour derogation ponctuelle (a implementer si
demande par l'utilisateur).

### Iter90aa (Feb 2026) - Dropdowns cles de repartition : filtrage + pre-selection is_default

**Ticket user** : "Ne reprendre dans le drop down du budget et des factures
que les cles de repartitions crees et mettre la cle selectionne par defaut
en tant que par defaut dans le champ."

**Frontend** :
- `FiscalYearPage.js` : useMemo `defaultKeyId` (is_default || premiere cle) ;
  `addBudgetLine` pre-remplit avec defaultKeyId ; dropdown des lignes de
  budget retire l'option hardcodee "Tantiemes (defaut)" ; suffixe " (defaut)"
  affiche sur la cle is_default.
- `InvoicesPage.js` : useMemo `defaultKeyId` ; openCreateInvoice initialise
  invForm.distribution_key_id avec defaultKeyId ; dropdown header
  (inv-dist-key) et dropdown lignes multi-natures (invoice-line-key-*)
  retirent les options "Aucune" et "—" hardcodees ; extraction IA multi-
  lignes utilise defaultKeyId.
- `BudgetWizard.js` : useMemo `defaultKeyId` + useEffect qui auto-remplit
  reserveKeyId & roulKeyId (idempotent : ne remplace pas la saisie user) ;
  dropdowns reserve/roulement retirent "Tantiemes (defaut)" hardcode ;
  suffixe " (defaut)" affiche.

**Comportement pour copro sans cle** : placeholder "Aucune cle - creez-en
une" (option desactivee) au lieu d'un fallback trompeur.

**Test coverage** : Testing agent - 15/15 UI checks passed (Iter40).
Setup fixture : copro 9e1dbd5a, "Charges communes" set as is_default.

### Iter90z (Feb 2026) - OCR Tesseract fallback pour PDFs bancaires scannes

**Ticket user (P3)** : Certains extraits de compte bancaires sont fournis en
PDF scanne (image seule, sans couche texte). Avant, l'import remontait
`PDF sans texte extractible (probablement scanne). L'OCR n'est pas encore
supporte`. Il faut basculer automatiquement sur un OCR pour permettre
l'extraction.

**Backend** (`bank_import.py`) :
- Nouvelle fonction `_extract_pdf_text_ocr(file_path)` : rasterise chaque
  page via PyMuPDF (fitz) @ 300 DPI, applique Tesseract avec langues
  `fra+eng` et PSM 6 (bloc uniforme, adapte aux tableaux bancaires).
- `_extract_pdf_text` : si pdfplumber + pypdf retournent < 40 caracteres,
  bascule automatiquement sur l'OCR. Sinon comportement inchange.
- `parse_with_llm` : detecte le mode OCR et propage :
  - `extraction_method="llm_text_ocr"`
  - Warning explicite : "PDF scanne detecte : extraction via OCR Tesseract
    (fra+eng). Verifiez chaque transaction, la reconnaissance de caracteres
    peut introduire des erreurs sur montants ou dates."

**Dependances** :
- pip : `pytesseract==0.3.13`, `PyMuPDF==1.24.14`
- apt (Dockerfile) : `tesseract-ocr`, `tesseract-ocr-fra`, `tesseract-ocr-eng`
- Dockerfile mis a jour pour installer les paquets apt avant le pip install.

**Tests** :
- Unit : `/app/backend/tests/test_iter90z_ocr_bank_pdf_fallback.py` (3/3)
  * test_ocr_extracts_scanned_pdf
  * test_text_pdf_does_not_use_ocr (verifie qu'un PDF natif ne declenche PAS
    l'OCR via mock)
  * test_ocr_direct_returns_text_from_scan
- E2E HTTP : `/app/backend/tests/test_iter90z_ocr_e2e_http.py` (2/2) - PIL
  image-only PDF uploade via `/api/banking/statements/import-files` ->
  extraction_method="llm_text_ocr" + 2 transactions extraites correctement
  par Claude Sonnet 4.5 a partir du texte OCR.

**Non-regression** : path natif (reportlab PDF avec couche texte) ->
`extraction_method="llm_text"` inchange, pas d'appel a Tesseract (mock
verifie).

### Iter90y (Feb 2026) - Bouton "+ Ajouter un lot" au bas du tableau des lots

**Ticket user** : "Lors de la creation des lots manuelles, ajouter le bouton
aussi a cet endroit pour eviter de devoir toujours remonter au dessus pour
ajouter un lot manuel..."

**Frontend** (`pages/LotsPage.js`) :
- Ajout d'un bouton "+ Ajouter un lot" plein largeur, discret (bordure
  superieure en pointille, texte gris hover bleu), affiche sous le tableau
  des lots (uniquement si la liste filtree n'est pas vide).
- data-testid : `add-lot-footer-btn`.
- Ouvre le meme dialog de creation via `openCreate()`.
- Lint OK, verifie visuellement en preview (Cascade ACP-202606-053).

### Iter90x' (Feb 2026) - Relettrage direct depuis le journal financier

**Ticket user** : Bouton "Relettrer directement" dans le meme dialog de delettrage,
qui ouvre en un clic la liste des factures du meme fournisseur pour choisir la
bonne, sans passer par la page Banque. Reduction du flow de 3 clics + navigation
a 1 clic + selection.

**Backend** (`routes/banking.py`) :
- `GET /api/banking/unlettrage-candidates/{txn_id}` : retourne les factures
  candidates a un re-lettrage pour la transaction, avec :
  - transaction : amount_abs, date, description
  - current_supplier : fournisseur de la facture actuellement lettree
  - candidates : liste triee par score (meme fournisseur > montant exact > date recente),
    chaque entree porte `exact_match`, `same_supplier`, `remaining` (solde du),
    `amount_paid`, `status`
- `POST /api/banking/relettrage/{txn_id}` avec body `{new_invoice_id}` :
  operation atomique qui :
  1. Delettre l'ancienne facture (recalcul status : unpaid ou partially_paid)
  2. Lettre la nouvelle facture avec nouveau lettrage_code
  3. Regenere l'ecriture FI vers le compte fournisseur correct
  4. Verifie que la nouvelle facture est dans la meme ACP (chinese walls)

**Frontend** (`components/UnlettrageDialog.js` - nouveau) :
- Dialog modal 3xl remplace l'ancien `window.confirm`.
- Recap : transaction + facture actuellement lettree (badge vert emeraude).
- Section "Delettrer seulement" : carte orange avec bouton unlettrage direct.
- Section "Relettrer directement" :
  - Recherche live (N° / fournisseur)
  - Toggle "Meme fournisseur uniquement" / "Tous fournisseurs"
  - Tableau des candidates avec colonnes : N° facture (badge "Match exact"
    ou "Meme fournisseur"), Fournisseur, Date, Solde du (+ montant paye si
    partially_paid)
  - Bouton "Relettrer" par ligne (vert emeraude si match exact, bleu sinon)
  - Confirmation avant execution
- Integration dans `pages/JournalsPage.js` : le bouton "Delettrer" du journal FI
  ouvre desormais ce dialog au lieu d'un confirm brut.

**Tests** (`test_iter90x_unlettrage_journal_fi.py`) :
- 4 tests PASS (2 nouveaux) :
  3. `test_relettrage_atomic_flow` : POST relettrage delettre l'ancienne facture
     (status=unpaid) et lettre la nouvelle (status=paid), atomique.
  4. `test_unlettrage_candidates_endpoint` : GET candidates retourne les factures
     triees par score, 1ere = meme supplier + montant exact.
- 28/28 tests pass en regression complete.


## Implemented
### Iter90x (Feb 2026) - Delettrage des factures via le journal financier

**Ticket user** : "Copropriete Acacia a des factures deja lettrees et j'aimerais
les delettrer afin de me permettre d'affecter le bon mouvement financier a
cette facture" — solution pragmatique et economique.

**Constat** : Les endpoints backend existent deja (`POST /banking/unlettrage/{txn_id}`
et `POST /banking/unlettrage-by-invoice/{invoice_id}`), mais aucune exposition
UI dans le Journal Financier. L'utilisateur devait aller dans BankingPage
pour delettrer, ce qui n'est pas naturel quand on regarde une ecriture FI.

**Backend** (`routes/accounting.py`) :
- Enrichissement de `GET /api/accounting/entries?journal_type=FI` : pour chaque
  ecriture FI avec `source_type=bank_txn`, ajoute `bank_txn_matched`,
  `bank_txn_match_type` et `linked_invoice` (id, invoice_number,
  supplier_name, amount_ttc) si la txn est lettree a une facture.
- Zero nouvel endpoint : reutilise l'existant `POST /banking/unlettrage/{txn_id}`
  qui gere deja : reset matched/matched_to, invoice status -> unpaid,
  regeneration de l'ecriture FI en compte d'attente 499000, recalcul du statut
  pour les lettrages N->1.

**Frontend** (`pages/JournalsPage.js`) :
- Badge vert emeraude "Facture <numero> - <fournisseur>" affiche a cote de la
  description dans le journal FI, pour chaque ecriture liee a une facture
  (tooltip avec le montant TVAC).
- Nouveau bouton "Delettrer" (icone Unlink, orange) dans la colonne Actions,
  visible uniquement sur les ecritures FI dont la transaction bancaire est
  lettree (a une facture, un proprietaire, un fournisseur, etc.).
- Confirmation dialog contextuelle qui rappelle a quelle facture la
  transaction est actuellement lettree.
- Sur clic : call `POST /banking/unlettrage/{source_id}`, toast de confirmation,
  reload de la liste. La facture redevient "en attente de paiement" et la
  transaction est disponible pour un nouveau lettrage.

**Verification live** :
- ACP Gaura : 324 ecritures FI dont 53 lettrees a des factures + 84 lettrees
  au total (owner/supplier/invoice). Toutes s'affichent avec le badge et le
  bouton delettrer.

**Tests** :
- `test_iter90x_unlettrage_journal_fi.py` - 2 tests PASS :
  1. GET /accounting/entries?journal_type=FI expose bien `linked_invoice`
  2. Flow complet unlettrage : facture repasse en `unpaid`, transaction
     redevient `matched=False`
- 26/26 tests pass en regression (iter73 bilan, iter90s-w compliance/PCMN/drift).


## Implemented
### Iter90w (Feb 2026) - Correction derive d'arrondi dans les appels de fonds

**Ticket user** : Balance de tiers affiche 25700.04 EUR au lieu de 25700.00 EUR
(0,04 EUR de trop, soit 1 centime par appel trimestriel).

**Cause identifiee** : Le calcul `round(total * quotity/total_quotity, 2)` par
lot ne garantit pas que `sum(lots) == total_amount` (drift naturel de +/- 0,01
EUR par appel selon les quotites). Sur 4 appels trimestriels + 1 fonds de
roulement, le drift peut atteindre 0,04 EUR.

**Fix backend** (`routes/fund_calls.py`) :
- Nouveau helper `_snap_distribution_to_total(distribution, target)` qui
  applique la methode des plus grands restes : les lots avec les shares les
  plus eleves recoivent 1 centime supplementaire (ou perdent 1 centime) pour
  que `sum(distribution.amount) == target` exactement.
- Applique dans 3 endroits :
  1. `create_fund_call` (appels manuels)
  2. `_generate_from_budget` -> pour chaque call trimestriel + reserve + roulement
  3. `_rebuild_distribution_from_lines` (regeneration depuis les budgets)
- Nouvel endpoint `POST /api/fund-calls/fix-rounding-drift?copropriete_id=X` :
  corrige les appels EXISTANTS (non payes) dans une ACP. Regenere aussi les
  journal entries auto-generees (VE) via `generate_sale_entry` pour synchroniser
  la balance de tiers.

**Fix frontend** (`pages/FundCallsPage.js`) :
- Nouveau bouton "Corriger arrondis" (icone RefreshCcw, style emerald) qui
  appelle l'endpoint fix-rounding-drift avec confirmation dialog.
- Toast avec detail des appels corriges et de leur drift respectif (en centimes).

**Tests** :
- `test_iter90w_fund_call_no_drift.py` - 3 tests PASS :
  1. Generation depuis budget (7 lots x 4 trimestres) : chaque appel a
     sum(distribution) == total_amount exactement.
  2. Appel manuel avec 7 lots : distribution parfaite, JE equilibree.
  3. Endpoint fix-rounding-drift : corrige 2 appels drifteds injectes en base.
- 24/24 tests pass en regression complete (iter73 bilan, iter90s-v).


## Implemented
### Iter90v (Feb 2026) - Comptes tiers PCMN + garde-fou suppression + n° comptes au bilan

**Ticket user** :
1. A la creation d'un proprietaire, 2 comptes comptables (fonds de roulement + fonds
   de reserve) doivent toujours etre assignes automatiquement, avec les codes PCMN
   belges officiels (4101XXXX / 4100XXXX).
2. Un proprietaire ne peut pas etre supprime tant qu'une ecriture comptable le
   reference (securite comptable, anti-orphelins).
3. Le bilan doit afficher le n° de compte (commencant par 410) pour chaque
   proprietaire ET la somme des soldes (roulement + reserve).

**Backend** :
- `tier_accounts.py` :
  - Nouveaux prefixes PCMN standard (arrete royal 12/07/2012 modifie 2018) :
    - `PROVISIONS_PREFIX = "4101"` (fonds de roulement appele)
    - `RESERVE_PREFIX = "4100"` (fonds de reserve appele)
  - Anciens prefixes conserves pour les 230 proprietaires existants
    (`40000XXX / 40010XXX`).
  - Nouveaux comptes generes avec largeur 4 (`41010001` / `41000001`, max 9999
    par ACP).
  - Comptes maitres `4100` et `4101` crees par ACP avec libelles conformes.
  - Comptes auxiliaires libelles :
    "Acompte de fonds de roulement appele - Nom" / "Acompte de fonds de reserve appele - Nom".
  - Helpers `is_provisions_account` / `is_reserve_account` acceptent les 2
    formats (longueur >= 8 pour eviter les faux positifs sur les comptes maitres
    ou les imports Optipro courts).
- `routes/properties.py :: DELETE /api/owners/{owner_id}` :
  Refuse 409 si le proprietaire est reference par :
  - Ecritures dans les journaux (journal_entries.lines.third_party_id)
  - Lots encore assignes
  - Appels de fonds (fund_calls.details.owner_id)
  - Factures (invoices.third_party_id)
  Message d'erreur detaille listant le nombre d'items bloquants et suggerant
  la fusion via l'outil Doublons.
- `routes/duplicates.py :: POST /api/duplicates/owners/merge` :
  Ajout de la reassignation ANTI-ORPHELIN des `journal_entries.lines[].third_party_id`
  et `fund_calls.details.owner_id` vers le proprietaire conserve avant la
  suppression des doublons.
- `routes/reports.py :: bilan` :
  - Chaque ligne agregee proprietaire expose le n° de compte "primaire"
    (fonds de roulement 4101XXXX ou legacy 40000XXX) via le champ
    `display_account`, remonte dans `account_number` du bucket
    `V_creances_coproprietaires` / `VI_dettes_coproprietaires`.
  - La somme (debit-credit) sur les 2 comptes tiers du proprietaire etait
    deja calculee via le merge par owner_id (`merged` dict) - pas de
    changement metier.
  - Classification etendue aux prefixes `410` / `411` (PCMN belge officiel)
    en plus de `400 / 401 / 416` (legacy).
- `routes/fiscal.py` : le check hardcode `startswith("40000")` remplace par
  `is_provisions_account(acc)` pour supporter les 2 formats.
- `pcmn_data.py` : ajout des comptes maitres `4100` et `4101` dans PCMN_COMPAT.

**Tests** :
- `test_iter90v_owner_pcmn_accounts.py` — 4 tests PASS :
  1. Creation owner : `tier_accounts.provisions` commence par `4101` et
     `tier_accounts.reserve` par `4100`, comptes maitres crees avec le libelle
     PCMN officiel.
  2. Delete refuse 409 si une ecriture comptable existe (message contenant
     "1 ecriture").
  3. Delete refuse 409 si un lot est encore assigne.
  4. Bilan : le proprietaire apparait avec le n° de compte 4101... et le
     montant est la somme des soldes provisions + reserve (test : 100 + 50 = 150).
- 21/21 tests PASS incluant regression iter73 (bilan apres repartition), iter90s (legal), iter90t (admin legal), iter90u (RGPD register).

**Verification manuelle sur donnees reelles (ACP Gaura)** :
V.A Coproprietaires debiteurs affiche 10 lignes avec n° comptes visibles :
`41011988, 41001996, 4101987, 4101989, 4101984, ...` (mix de tous formats).


## Implemented
### Iter90u (Feb 2026) - Registre des traitements RGPD (art. 30) + PDF

**Ticket user** : Fiche registre RGPD art. 30 - liste des traitements, sous-traitants,
base legale, duree de conservation. PDF pret a envoyer a l'APD en cas de controle.

**Backend** (`routes/legal.py` + `pdf_rgpd_register.py`) :
- Collection `legal_rgpd_register` (document unique `_id="default"`).
- 3 endpoints (superadmin only) :
  - `GET /api/legal/admin/rgpd-register` — donnees editables (renvoie defauts si jamais sauvegarde)
  - `PUT /api/legal/admin/rgpd-register` — persiste controller / processings / subprocessors / security_measures + audit log
  - `GET /api/legal/admin/rgpd-register/pdf` — genere le PDF (reportlab, A4, 2+ pages) + audit log download
- PDF (`pdf_rgpd_register.py`) genere via reportlab avec :
  - En-tete responsable de traitement (societe, BCE, TVA, DPO, ...)
  - Table 7 colonnes activites de traitement (finalite, base legale, categories, subjects, destinataires, retention)
  - Table sous-traitants (Emergent LLM, Microsoft Graph, hebergeur)
  - Mesures techniques et organisationnelles (bcrypt, TLS, RBAC, chinese walls, audit, backup, verrous fiscaux)
  - Droits des personnes (art. 15-22)
  - Encart autorite de controle APD (Bruxelles)
  - Zone signature responsable + cachet
  - Footer avec date de generation + pagination
- Contenu par defaut precomplete : 6 activites de traitement, 3 sous-traitants, 10 mesures de securite (base solide pour un SaaS de gestion de copropriete).

**Frontend** (`pages/AdminRgpdRegisterPage.js`) :
- Route `/admin/rgpd-register` (superadmin only).
- 4 sections editables :
  1. Responsable de traitement (grille 9 champs)
  2. Activites de traitement (liste dynamique, ajouter/supprimer, 7 champs par entree)
  3. Sous-traitants (liste dynamique, 4 champs par entree)
  4. Mesures techniques et organisationnelles (liste texte simple ajouter/supprimer)
- Sticky bottom bar avec "Sauvegarder" + "Telecharger PDF" + date derniere sauvegarde.
- Sidebar admin : nouveau lien "Registre RGPD" (icone FileArchive).

**Tests** :
- `test_iter90u_rgpd_register.py` — 4 tests PASS :
  1. ACL : owner obtient 403 sur GET/PUT/PDF
  2. GET renvoie defauts avec >= 3 traitements + 2 sous-traitants + 3 mesures
  3. PUT persiste + audit log (action `legal.rgpd_register_update`)
  4. PDF valide : Content-Type application/pdf, magic bytes %PDF-, > 3 Ko, texte "Registre des traitements" + "article 30" + "APD" retrouvable via pypdf, audit log `legal.rgpd_register_pdf_download`

Total legal pytest suite (iter90s + 90t + 90u) : **16 tests PASS**.
Total avec regression (iter89 owner portal + iter90n security + iter90r support) : **25 tests PASS**.


## Implemented
### Iter90t (Feb 2026) - Admin UI edition des documents legaux

**Ticket user** : P3 - Ecran admin pour modifier CGU/Privacy sans passer par
mongo shell (bump automatique de version force reacceptation).

**Backend** (`routes/legal.py`) :
- `GET /api/legal/admin/documents` — liste avec contenu (superadmin only, 403 sinon)
- `PUT /api/legal/admin/documents/{slug}` — edit contenu + titre. Param
  `bump_version=true` incrementе la version → force re-acceptance obligatoire
  quand slug in ('cgu', 'privacy'). Rejette contenu vide ou > 200 000 chars.
- `GET /api/legal/admin/documents/{slug}/history` — historique versions dans
  collection `legal_document_history` (snapshot avant modification).
- Audit trail complet dans `audit_log` (action: `legal.admin_edit_document`).

**Frontend** :
- `pages/AdminLegalDocsPage.js` route `/admin/legal` (superadmin only).
- Tabs pour les 5 documents avec badge version courante.
- Split-view editeur (titre + textarea markdown monospace) / preview live
  (react-markdown + remark-gfm).
- 2 boutons :
  - "Sauvegarder (sans bump)" : PATCH sans changer version
  - "Publier nouvelle version (vN+1)" avec dialog de confirmation qui
    alerte de l'impact sur la re-acceptation pour cgu/privacy.
- Bouton "Historique" affiche panel avec liste des modifications
  (version_before → version_after, badge bump, email editeur, date).
- Confirmation "Modifications non enregistrees" au changement d'onglet.
- Sidebar admin : lien "Documents legaux" (icone FileCheck).

**Tests** :
- `test_iter90t_legal_admin_edit.py` — 5 tests PASS :
  1. Owner obtient 403 sur endpoints admin
  2. Superadmin liste les 5 docs avec contenu
  3. Edit sans bump preserve la version
  4. Edit CGU avec bump : version+1, my-acceptance renvoie needs_accept=true
  5. Rejet contenu vide et > 200 000 chars

Total pytest iter90s+90t : 12/12 PASS.


## Implemented
### Iter90s (Feb 2026) - Systeme legalement blinde (CGU, RGPD, cookies)

**Ticket user** : Systeme legalement blinde couvrant CGU/Confidentialite/
Mentions/Cookies/Disclaimer + acceptation utilisateur + RGPD (export + delete).

**Backend** (`routes/legal.py` - complet, ~500 lignes) :
- Collection `legal_documents` (auto-seed au demarrage via `_ensure_defaults`)
  avec 5 slugs : `cgu`, `privacy`, `mentions`, `cookies`, `disclaimer`.
  Chaque doc a une version incrementale (bump manuel force re-acceptation).
- Endpoints :
  - `GET /api/legal/documents` — liste publique (sans auth)
  - `GET /api/legal/documents/{slug}` — contenu markdown public
  - `GET /api/legal/current-versions` — versions courantes CGU + Privacy
  - `GET /api/legal/my-acceptance` — statut acceptation du user + needs_accept
  - `POST /api/legal/accept` — enregistre acceptation (persiste ip/ua/date + audit log)
  - `POST /api/legal/rgpd/export` — JSON attachment: profile (sans password_hash),
    audit_logs, support_conversations, linked_owner_record. Log l'export dans audit.
  - `POST /api/legal/rgpd/delete-account` — exige phrase EXACTE 'SUPPRIMER MON COMPTE',
    protection dernier superadmin, marque `deletion_requested_at` + `deletion_purge_at_ts`
    (30 jours).
  - `POST /api/legal/rgpd/cancel-deletion` — annule la demande dans le delai.
- Middleware :
  - `AUTH_EXEMPT_PREFIXES = ('/api/legal/documents',)` pour lecture publique
  - `role=owner` autorise sur `/api/legal/*` (modal + RGPD self-service)
- Placeholders `[SOCIETE]`, `[NUMERO_BCE]`, `[NUMERO_TVA]`, `[ADRESSE_COMPLETE]`,
  `[FORME_JURIDIQUE]`, `[NOM_REPRESENTANT]`, `[TELEPHONE]`, `[DATE_MISE_EN_LIGNE]`
  laisses tels quels pour completion avocat. Email contact pre-rempli
  `welcome@goodexperienceproperties.be` partout.

**Frontend** :
- `pages/LegalDocPage.js` : rendu markdown (react-markdown + remark-gfm)
  avec nav pills entre les 5 slugs. Route `/legal/:slug` + `/legal` redirige
  vers `/legal/cgu`. Accessible sans authentification.
- `components/LegalAcceptanceModal.js` : modal bloquant apres login si
  `needs_accept=true`. 2 checkboxes CGU + Privacy, submit disabled tant qu'aucune
  cochee, liens externes vers `/legal/{slug}` en nouveau tab.
- `components/CookieBanner.js` : banner bas-droit, cookies techniques
  uniquement, un bouton "J'ai compris" qui pose `localStorage
  copromgr_cookie_ack_v1`. Lien "En savoir plus" vers `/legal/cookies`.
- `components/RgpdSection.js` : integre dans `/profile`. Export JSON (blob
  download), Supprimer avec dialog + confirm phrase exacte, section "pending"
  si `deletion_requested_at` present avec bouton "Annuler ma demande".
- Footers legaux : `login-link-{slug}` sur `/login`, `layout-legal-footer` dans
  les 2 modes Layout (avec/sans ACP), `owner-portal-legal-footer` sur `/portal`,
  `profile-link-{slug}` (5 badges) sur `/profile`.
- `App.js` : CookieBanner et LegalAcceptanceModal montes au niveau global.
- `api.js` : `/legal` ajoute a `GLOBAL_PATH_PREFIXES` (pas de scope ACP).

**Tests** :
- `test_iter90s_legal_compliance.py` — 7 sous-flows PASS :
  1. Docs publics (5 slugs listes + contenus lisibles sans auth)
  2. Endpoints prives 401 sans token
  3. Cycle accept complet (needs_accept true -> false, version 999 -> 400 obsoletes)
  4. Export RGPD (Content-Disposition attachment, profile sans password_hash, audit_logs)
  5. Delete-account exige phrase exacte 'SUPPRIMER MON COMPTE'
  6. Protection dernier superadmin (403 'dernier')
  7. Owner delete+cancel cycle 200
- Testing agent E2E : 100% BE + FE (iteration_38.json). Regression iter89+90n+90r : 9/9 PASS.
- `_ensure_defaults` idempotent (n'ecrase pas les docs deja en base pour permettre
  edition future via UI admin).

**Dependances** : `react-markdown`, `remark-gfm`, `@tailwindcss/typography` (prose).


## Implemented
### Iter90r (Feb 2026) - Support chatbot IA + escalade email

**Ticket user** : Bouton "?" dans l'interface qui ouvre un chatbot pouvant
repondre aux questions classiques d'un syndic sur les fonctionnalites.
Si la reponse depasse ses capacites, un email est envoye au support
(welcome@goudexperienceproperties.be).

**Backend** :
- `/app/backend/routes/support.py` — 6 endpoints :
  - `GET /api/support/conversations` — liste des convs de l'user
  - `POST /api/support/conversations` — nouvelle conv
  - `GET /api/support/conversations/{id}/messages` — historique
  - `POST /api/support/conversations/{id}/chat` — envoi message + reponse IA
  - `POST /api/support/conversations/{id}/escalate` — envoi manuel au support
  - `DELETE /api/support/conversations/{id}` — supprime conv + messages
- IA : Claude Sonnet 4.5 via `emergentintegrations.LlmChat` + system prompt
  detaille sur les fonctionnalites CoproManager (10 modules documentes).
- Escalade automatique : l'IA marque `[[NEEDS_ESCALATION]]` en fin de reponse
  quand elle detecte un bug/diagnostic technique/demande hors scope. Email
  envoye en background via MS Graph, `replyTo` = email du syndic (le support
  peut repondre directement).
- Modeles MongoDB : `support_conversations`, `support_messages`
- Chinese wall : chaque user ne voit QUE ses propres conversations (403 sinon)
- Var d'env : `SUPPORT_EMAIL=Welcome@goudexperienceproperties.be`
- `graph_email.send_html_email` etendu avec parametre `reply_to`

**Frontend** (`/app/frontend/src/components/SupportChatBubble.js`) :
- Bouton `HelpCircle` (`data-testid="support-open-btn"`) dans les 3 headers
  du Layout (admin plateforme / superadmin sans copro / syndic normal)
- Panneau lateral droit (`fixed right-0 h-full w-520px z-50`) avec :
  - Liste des conversations passees (preview + date + badge escalade)
  - Bouton "Nouvelle question"
  - Fenetre de chat active avec bulles utilisateur/assistant
  - Bouton "Envoyer cette conversation au support" (escalade manuelle)
  - Toast auto quand IA declenche l'escalade automatique
- Historique persistant, suppression conv, retour a la liste, close panel

**Tests** : `test_iter90r_support_chatbot.py` — 6 sous-tests (creation, list,
chinese wall cross-user, delete cascade, message empty/too-long, escalation
flag). Tous PASS.

**Verdict test manuel** : IA Claude Sonnet 4.5 repond correctement aux
questions fonctionnelles (import PDF, workflow appels de fonds, PCMN...) et
escalade automatiquement les diagnostics techniques.



## Implemented
### Iter90n (Feb 2026) - Security hardening + Sticky header extrait

**Ticket security audit** :
- SEC-001 [HIGH] : Cross-building bypass sur endpoints banking by-id/by-body
- SEC-002 [MEDIUM] : Upload PDF/CSV sans limites -> DoS LLM
- SEC-003 [LOW] : Password-reset token logge quand MS Graph absent

**Ticket UX** : Le header du panneau extrait (titre + boutons Comptabiliser
/ Repasser brouillon + bandeau de solde) reste flottant en haut du panneau
quand l'utilisateur scrolle la liste des transactions.

**Fixes securite** :
- Nouveau helper `_ensure_copro_access(request, copro_id)` dans banking.py
  qui verifie que le role est superadmin/admin OU que `copro_id` est
  dans `user_copropriete_ids`. Applique sur :
  - `GET /statements/{id}` (SEC-001)
  - `GET /statements/{id}/source-file` (SEC-001)
  - `POST /transactions/{id}/categorize` (SEC-001)
  - `DELETE /transactions/{id}/categorize` (SEC-001)
  - `POST /statements/import-files` (SEC-001)
- Bornes anti-abus sur `import-files` (SEC-002) :
  - Max 20 fichiers par upload (retourne 413)
  - Max 10 MB par fichier
  - Max 100 MB total agrege par upload
  - Content-type sniffing : PDF verifie via magic bytes (%PDF-), CSV/TXT
    via ratio de bytes imprimables
- SEC-003 : `server.py:596` — supprime le raw_token du log warning
  (garde uniquement l'evenement + email destinataire).

**Fix UX sticky** :
- `BankingPage.js` CardHeader : `sticky top-2 z-20 bg-white/95
  backdrop-blur-sm border-b shadow-sm rounded-t-lg` avec `data-testid
  "stmt-sticky-header"`. Le titre, les boutons Comptabiliser/Repasser
  brouillon, le bandeau equilibre restent visibles pendant le scroll.

**Tests** : `test_iter90n_security_hardening.py` - 8 scenarios (5 sur
chinese wall + 3 sur bornes upload). Non-regression : **15/15 iter90
PASS en suite**.



## Implemented
### Iter90m (Feb 2026) - Categorisation d'extraits avec compte 58 Virements internes

**Ticket user** : Permettre dans la catégorisation d'extraits de mentionner
le compte 58 (Virements internes, PCMN belge, classe 5) pour les transferts
entre compte à vue et compte épargne. Ces mouvements ne sont ni des
charges ni des produits — ils utilisent 58 comme compte de passage qui
revient à zéro une fois les 2 transactions du transfert saisies.

**Backend** :
- `/app/backend/routes/expense_categories.py` : autorise classe 5 dans la
  création/édition d'une nature MAIS uniquement pour les comptes `58*`.
  Nouveau `kind='transfer'` auto-derivé pour la classe 5. Erreurs
  explicites si compte classe 5 non-58.
- `/app/backend/routes/banking.py` catégorisation : accepte classe
  5 (58*), 6, 7. Rejette classe 5 non-58 avec message clair.
- `/app/backend/expense_rows.py::_is_charge_account` : garde-fou defensif
  `if num.startswith("58"): return False` — le compte 58 n'apparait JAMAIS
  dans la liste des dépenses (invariant critique).
- L'écriture FI générée pour un débit "virement interne" :
  `Dr 58 + Cr 550/551/552` — quand la transaction contrepartie arrive
  sur l'autre extrait, le compte 58 s'équilibre naturellement à zéro.

**Frontend** :
- `BankingPage.js` dropdown Nature : nouveau badge "• virement" (indigo)
  pour les natures 58*, tri distinct des charges/produits.
- `ExpenseCategoriesPage.js` : charge aussi les comptes classe 5 mais
  filtre côté client pour n'exposer QUE les `58*`.
- Label mis a jour : "Compte PCMN (classe 6, 7 ou 58 Virements internes)".

**PCMN par défaut** : le compte "58" (Virements internes) est déjà dans
le template `/app/backend/pcmn_data.py:116`.

**Tests** : `test_iter90m_internal_transfers_58.py` — 5 scenarios
(création OK sur 58*, rejet classe 5 non-58, catégorisation FI correcte
Dr 58 / Cr 55x, invariant 58 hors expenses, équilibrage symétrique).
Non-régression : 14/14 iter90 PASS en suite.



## Implemented
### Iter90l (Feb 2026) - Import PDF/CSV d'extraits bancaires -> creation auto en brouillon

**Ticket user** : Dans les extraits de compte, permettre d'uploader un ou
plusieurs PDF/CSV d'extraits bancaires belges. L'application extrait
automatiquement les transactions et cree les extraits en brouillon que
le syndic n'a plus qu'a valider. Choix user : IA + regex CSV smart +
creation directe brouillon + multi-fichiers + persistance GridFS des
originaux (audit).

**Backend** :
- Nouveau module `/app/backend/bank_import.py` :
  - `parse_csv_smart(content, filename)` : detection auto separateur
    (`;` / `,` / `\t` / `|` via `csv.Sniffer`), header identification par
    alias multi-banques (FR + NL), colonnes reconnues :
    date/montant OU debit+credit/communication/contrepartie/iban. Support
    des montants `1.234,56`, `1,234.56`, `-25,00`, `25,00-`, `(100,00)`.
  - `parse_with_llm(file_path, mime_type)` : appel `LlmChat` avec
    `FileContentWithMimeType` (Gemini 2.5 Flash) et prompt JSON strict.
  - `extract_bank_statement(...)` : dispatcher (PDF -> IA, CSV -> smart
    puis fallback IA si non reconnu).
- Endpoint `POST /api/banking/statements/import-files` :
  - multipart, `files: List[UploadFile]`, `copropriete_id: Form`
  - Persiste chaque fichier original en GridFS bucket
    `bank_statement_sources` (audit) puis appelle le dispatcher.
  - Cree `bank_statement` en `status='draft'` + `source_extraction_method`
    (`csv_smart` / `csv_llm_fallback` / `llm_vision`) + `source_file_id`.
  - Cree les `bank_transactions` non lettrees.
  - Retourne un rapport par fichier avec `status`, `transactions_count`,
    `warnings`.
- Endpoint `GET /api/banking/statements/{id}/source-file` : telecharge le
  PDF/CSV original.

**Frontend** (`BankingPage.js`) :
- Bouton "Importer PDF/CSV" (purple, testid `import-files-btn`) a cote de
  Import CODA. Disabled tant qu'aucune ACP selectionnee, tooltip explicite.
- Input file multiple accept `.pdf,.csv`.
- Toast "X extrait(s) importe(s) en brouillon — N transactions au total".
- Statements draft affichent badge "Brouillon" (amber), "PDF IA" (purple),
  "CSV" (blue).
- Lien "Voir fichier source" par statement importe.

**Tests** : `test_iter90l_import_bank_statements_pdf_csv.py` — 8 sous-tests
(unites parse_amount/parse_date/csv_smart-BNPP/csv_smart-Belfius/csv_unrecognized
+ E2E single/multi/GridFS-download/copro-manquant/fichier-vide). Tous PASS.
Regression iter90 : 13/13 PASS en 15.87s.

**Integration LLM** : Emergent LLM Key + Gemini 2.5 Flash via
`emergentintegrations.llm.chat.LlmChat`. Cle deja en `.env`
(`EMERGENT_LLM_KEY`).



## Implemented
### Iter90k (Feb 2026) - Categoriser une transaction bancaire par nature de depense/revenu

**Ticket user** : Dans les extraits de compte, permettre d'attribuer une
nature (`expense_category`) a une transaction bancaire non lettree —
typiquement les frais bancaires (compte 650000), commissions, intérêts
créditeurs (compte 750000), charges financières... Support des splits
multi-natures (une transaction = plusieurs lignes). Les montants doivent
apparaître dans la "Liste des dépenses" (positifs pour classe 6, négatifs
pour classe 7).

**User choices** : UI dans la ligne d'extrait • classes 6+7 • splits ON •
revenus dans liste dépenses ON (flux) • clé de répartition obligatoire.

**Backend** :
- `POST /api/banking/transactions/{txn_id}/categorize` — body :
  `{"splits":[{"expense_category_id","distribution_key_id","amount","description"}]}`
  Génère une écriture journal **FI** multi-lignes (banque + N contreparties
  6xxx/7xxx). Valide : classe PCMN in (6,7), somme==|txn.amount| (0.01), clé
  obligatoire. Retourne `journal_entry_id`.
- `DELETE /api/banking/transactions/{txn_id}/categorize` — supprime le FI,
  remet la txn en état non-lettré, regénère un FI d'attente 499000 si
  l'extrait est `posted`.
- `expense_rows.py::_is_charge_account` — accepte désormais TOUS les
  comptes classe 6 et 7 sauf `70*` (Provisions/appels de fonds, garde-fou
  anti-double-comptage), `44*`/`40*`/`41*`/`42*` (tiers), et `643*` (frais
  privatifs).
- `auto_entries.py::generate_bank_entry` — nouveau branch pour
  `match_type="expense_category"` avec écriture FI multi-lignes, source_type
  =`bank_txn` (traçabilité).

**Frontend** (`BankingPage.js`) :
- Icône Tag (purple) sur chaque txn non-lettrée → ouvre le dialog
  `data-testid="categorize-dialog"`.
- Dialog : entête récap txn (montant signé + contrepartie) + N splits
  (nature + clé + montant + description), bouton "Ajouter un split", badge
  "Équilibré" / "Écart X" en direct, tri des natures produits en tête pour
  les crédits.
- Bouton "Catégoriser" disabled tant que somme != txn.amount ou champ
  manquant.
- Ligne catégorisée affiche badge "Nature" ou "Nature (N)" (purple) +
  bouton "Retirer la nature".

**Tests** :
- `test_iter90k_categorize_bank_transaction.py` — 7 scenarios (débit
  charge, crédit produit négatif, multi-splits, erreurs somme/DK, uncateg
  supprime FI, provisions 70* hors expenses).
- `test_iter90k_extra_validation.py` — 2 scenarios ajoutés par testing
  agent (nature inconnue, compte non 6/7 rejeté).
- **9/9 sous-tests PASS**. Non-régression iter90 : 6/6 PASS en suite.
- Testing agent : Backend 100% success rate. Frontend structure DOM
  validée (tous les data-testids en place).



## Implemented
### Iter90j (Feb 2026) - Fix flakiness "Event loop is closed" sur la suite tests

**Ticket** : `test_iter90cd_owner_scope_duplicates_pdfs.py` (et d'autres tests
basés sur `asyncio.run`) échouaient en suite complète avec
`RuntimeError: Event loop is closed`, alors qu'ils passaient en isolation.

**Cause racine** : Le client Motor (`db`) est créé à l'import de `server.py`
et se lie au PREMIER event loop sur lequel il opère. Chaque `asyncio.run()`
crée puis ferme son propre loop → après le premier test, Motor reste
référencé à un loop fermé et tous les tests `asyncio.run`-based suivants
crashent dans le bridge PyMongo.

**Fix** : Nouveau `/app/backend/tests/conftest.py`
- Crée un event loop UNIQUE persistant à scope session.
- Monkey-patch `asyncio.run(coro)` → dispatch sur ce loop persistent
  via `loop.run_until_complete(coro)`.
- Tous les tests utilisent automatiquement le même loop → Motor reste lié
  à ce loop toute la session → plus aucun "Event loop is closed".
- Zéro modification dans les 133 occurrences `asyncio.run` des tests.

**Validation** :
- `test_iter90*.py` (5 fichiers) : 5/5 PASS en suite.
- Mix iter76+77+83+85+87+89+90 (33 tests asyncio) : 33/33 PASS.
- 3 runs successifs confirment la stabilité.
- Suite complète : 436 PASS (vs 433 avant le fix).
- Les 136 failed/106 errors restants sont des tests d'intégration HTTP
  (BASE_URL manquante, exercices fiscaux non seedés) — antérieurs et hors
  périmètre P1.



## Implemented
### Iter90i (Feb 2026) - Exclusion des frais privatifs du total "Dépenses"

**Ticket user** : Sur l'ACP Acacia (production), 9 factures
`is_private_fee=true` totalisant 800,02 EUR (compte 643) étaient incluses
dans le total "Dépenses de l'exercice" (12 128,74 EUR au lieu de 11 328,72 EUR).
Le sur-comptage venait de l'absence de filtre `is_private_fee` dans la
requête Mongo de `compute_expense_rows`.

**Backend** (`/app/backend/expense_rows.py`) :
1. **Requête invoices** : ajout du filtre `"is_private_fee": {"$ne": True}`
   pour exclure dès le départ les factures privatives. Elles sont
   refacturées via OD au compte 643 mais ne sont PAS des charges communes.
2. **`_is_charge_account`** : ajout d'une protection défensive — tout compte
   commençant par `643*` est exclu, même si marqué `class_num=6` dans le PCMN.
   Cela bloque le double comptage via la pass FI/OD (les OD-PRIV de
   refacturation posent un débit sur 643).

**Invariants vérifiés** :
- `sum(TVAC PDF)` == `totals.total UI` == charges communes uniquement
- Widgets "Par Nature" / "Par Clé" / "Par Banque" = même total
- Cas Acacia simulé : 5 privatives 160€ + 1 commune 500€ → total = 500€

**Tests** : `test_iter90i_private_fees_excluded_from_expenses.py` → 3 flows :
exclusion facture privative, exclusion ligne OD-643, scénario Acacia-like.
Test invariant `test_iter90e_liste_depenses_pdf_invariant.py` toujours vert.

**Endpoint diagnostic** (déjà existant, iter90h) :
`GET /api/fiscal/expenses-diff?copropriete_id=&date_from=&date_to=`
détecte les comptes 4xxx mal classés en classe 6 et les factures
privatives sur compte ≠ 643.


## Implemented
### Iter90g (Feb 2026) - Suppression complete de l'acces proprietaire (DELETE)

**Demande user** : Pouvoir supprimer (pas juste suspendre) l'acces d'un
proprietaire a la plateforme. Cas d'usage : vente definitive, erreur
d'invitation a purger pour recommencer a zero.

**Backend** :
- Nouveau `DELETE /api/owners/{owner_id}/access` :
  * Supprime le user account (role=owner uniquement)
  * Detache `owner.user_id`
  * Invalide les tokens reset actifs
  * Log audit avec action='delete' (target_email preserve pour tracabilite)
- Idempotent : 200 OK si aucun acces a supprimer.
- Securite : si l'email est partage avec un compte syndic/admin/gestionnaire,
  le compte est PRESERVE (seule la fiche owner est detachee).
- Session existante du proprio : son JWT devient 401 immediatement
  (user not found in DB).

**Frontend** :
- `OwnerAccessSection` : nouveau bouton "Supprimer l'acces" (rouge, icone
  Trash2), visible des qu'un compte est lie (status != 'none').
- Confirmation native window.confirm() avec explication claire des
  consequences (suppression + invalidation sessions + historique conserve).
- L'historique d'audit affiche la nouvelle action 'delete' avec son
  badge color-code rouge gras.

**Tests** : `test_iter90g_delete_owner_access.py` -> 5 sous-flows PASS :
suppression full, idempotence, preservation non-owner, invalidation
reset tokens, invalidation session existante.

**E2E preview** : selimabed@protonmail.com supprime, audit log mis a jour,
2e DELETE retourne "Aucun acces a supprimer".


### Iter90f (Feb 2026) - Vue UI Depenses + PDF unifies sur source unique

**Ticket user** : Aligner la vue UI "Depenses de l'exercice" sur la meme
source que le PDF + widgets coherents avec Total filtre + bucket "Autres"
+ footer "Total" + label "Sans cle" et by_nature au lieu de by_account.

**Backend** :
- `routes/fiscal.py::list_expenses` refactore : 350 lignes de logique
  dupliquee supprimees, remplacees par un appel a
  `expense_rows.compute_expense_rows()`. Single source of truth garantie
  pour la UI et le PDF.
- `totals.by_nature` ajoute en plus de `by_account` (N3) et `by_key` (N1).
  Cle = `expense_category_name` (ou "Sans nature").
- Invariant verifie e2e :
  sum(by_nature) == sum(by_account) == sum(by_key) == total = 33828.43.
- `distribution_key_name` retourne "Sans cle" au lieu de "—" pour les
  rows sans cle de repartition.

**Frontend** :
- `ExpensesPage.js` widgets "Par Nature" et "Par Cle" :
  * Top 3 + bucket "Autres" (somme du reste) + footer "Total".
  * Le widget Par Nature lit `data.totals.by_nature` (N2) au lieu de
    `by_account` (N3).
  * Le label "—" remplace par "Sans cle" dans la hierarchie (par defaut
    via `r.distribution_key_name || 'Sans cle'`).
  * data-testid : `widget-by-nature`, `widget-by-key`,
    `widget-nature-total`, `widget-key-total`, etc.

**Validation e2e (ACP Gaura, exercice 2025)** :
- Total filtre = 33.828,43 EUR (120 depenses)
- Widget Nature : Top 3 + Autres 13168,12 = TOTAL 33828,43
- Widget Cle : "Charges communes" 32608,06 + "Sans cle" 1220,37 = TOTAL 33828,43
- PDF "Liste des depenses" TVAC = 33.828,43
- Tous matchent au centime.


### Iter90e (Feb 2026) - PDF "Liste des depenses" aligne sur la vue UI + colonnes HTVA/TVA/TVAC

**Ticket user** : Aligner le PDF "Liste des depenses" sur la vue UI des
depenses et inclure la TVA correctement. Invariant P0 :
  sum(TVAC du PDF, sans filtre) == totals.total de /api/fiscal/expenses

**Probleme** : la route `reports.py::liste_depenses_pdf` lisait directement
`db.invoices` -> ratait les ecritures OD/FI classe 6 (frais bancaires,
fin d'exercice) ET ne decomposait pas HTVA/TVA/TVAC -> divergence
recurrente avec la vue UI et Optipro.

**Backend** :
- Nouveau `/app/backend/expense_rows.py` : helper `compute_expense_rows()`
  reproduisant exactement la logique de `fiscal.py::list_expenses` (factures
  expandees en N lignes + ecritures OD/FI classe 6, filtrage par
  `$or` au niveau document + ligne pour les filtres
  account_number/distribution_key_id/expense_category_id, TVA distribuee
  pro-rata sur les lignes en multi-ligne). Retourne aussi
  `total_htva` et `total_vat` dans les totaux.
- `routes/reports.py::liste_depenses_pdf` : reecrite pour appeler le helper
  + accepte le parametre `expense_category_id`.
- `pdf_liste_depenses.py::build_liste_depenses_pdf` : nouvelles colonnes
  HTVA + TVA + TVAC ; sous-totaux et grand total decomposes en 3 montants ;
  `proprietaire_amount` / `occupant_amount` lus depuis les rows.

**Tests** : `test_iter90e_liste_depenses_pdf_invariant.py` -> 4 sous-flows
PASS : invariant TVAC=fiscal/expenses, filtre dist_key sur une ligne d'une
multi-ligne, filtre account, colonnes HTVA/TVA/TVAC presentes dans le PDF.

**Validation e2e (ACP Gaura, exercice 2025)** :
- /api/fiscal/expenses total = **33828,43 EUR**
- PDF "Liste des depenses" totaux : HTVA 30.470,32 / TVA 3.358,11 /
  **TVAC 33.828,43** / Part prop. 30.572,18 / Part occ. 23.391,90
- Match exact -> invariant respecte.


### Iter90c+d (Feb 2026) - Owner scope ACP-aware + frais privatifs + exports PDF

**Demande user 1** : Voir les proprietaires sans lot dans l'ACP courante
(sinon doublons crees par accident lors de la mutation/allocation privative).
**Demande user 2** : Lors d'affectation de frais privatif, imputer sur le
compte principal du proprio existant, pas creer un nouveau proprio.
**Demande user 3** : Exporter PDF de TOUS les journaux comptables + PDF de
la liste exhaustive des factures.

**Backend** :
- `list_owners` et `_allowed_owner_ids` : UNION lots + `copropriete_ids[]`
  -> un proprio rattache mais sans lot est visible dans le scope.
- `_owner_in_scope` etend la verification (chinese-wall preserve).
- Nouveau `POST /api/owners/{id}/attach-to-copro` : rattachement idempotent
  via `assign_owner_accounts` ; verifie le scope syndic.
- `_norm_name` (matching doublons) : supprime '&', tirets, accents avant
  tri alphabetique des mots -> matche "X & Y" avec "X Y".
- 2 nouveaux PDF endpoints :
  * `GET /api/reports/journals/pdf` -> Tous les journaux (AC/OD/BQ/VE)
    avec controle PCMN (sum debits = sum credits).
  * `GET /api/reports/invoices-list/pdf` -> Liste exhaustive factures
    (HTVA / TVA / TVAC / statut), trie par date.
- Nouveau module `pdf_journals_and_invoices.py` (paysage A4, branding bleu).

**Frontend** :
- `OwnerPicker` (LotsPage) : prop `coproproId`, payload de creation inclut
  `copropriete_id`, et `attach-to-copro` est appele apres select/create.
  Toast propre des 4xx/5xx (plus d'erreurs silencieuses).
- `InvoicesPage` : owners charges en `syndic_wide=true` -> le combobox
  d'allocation des frais privatifs montre TOUS les proprios du syndic,
  empechant la creation accidentelle de doublons.
- `ExpensesPage` : 2 nouveaux boutons (`invoices-list-pdf-btn`,
  `journals-pdf-btn`) a cote de `Liste des depenses (PDF)`.

**Tests** : `test_iter90cd_owner_scope_duplicates_pdfs.py` -> 4 sous-flows
passent : visibilite owner sans lot, detection doublon avec '&',
idempotence attach-to-copro, PDFs valides (header %PDF-, > 2KB).


### Iter90b (Feb 2026) - Journal d'audit des operations d'acces proprietaire

**Demande user** : Tracer qui active/suspend/reactive l'acces d'un proprietaire,
pour quel proprio, et quand (RGPD + litige).

**Backend** :
- Nouvelle collection `owner_access_audit` (indexes : owner_id, (owner_id, created_at desc))
- Helper `_log_audit(...)` dans `routes/owner_access.py` (best-effort,
  capture : action, owner_id/name/email, target_user_id/email,
  actor_user_id/email/name/role, ip, user_agent, details, created_at)
- Loggage automatique dans les 4 endpoints : `grant`, `resend`, `revoke`, `reactivate`
- Nouvel endpoint `GET /api/owners/{owner_id}/access-audit?limit=50`
  (scoped chinese-wall, tri DESC par date)

**Frontend** :
- `OwnerAccessSection` : section dépliable « Historique des actions d&apos;acces »
  qui charge l'historique a la demande, affiche les badges color-coded
  (grant=vert, revoke=rouge, etc.), l'acteur, la cible, l'IP, et les
  details (compte existant lie / email non envoye).

**Validation e2e** : revoke + reactivate via curl loguent bien 2 entrees
avec actor=admin@copro.be, target_user_email=selimabed@protonmail.com,
ip captured, tri DESC OK.


### Iter90 (Feb 2026) - Gestion manuelle de l'acces propriete + reset password

**Demande user** : Le syndic doit pouvoir activer/desactiver l'acces du
proprietaire a la plateforme depuis sa fiche. Le proprietaire reçoit
une invitation par email, definit son propre mot de passe, et peut
demander un reset par "mot de passe oublie" si besoin. Si le syndic
n'active pas l'acces -> aucun acces.

**Backend** :
- Nouveau `/app/backend/routes/owner_access.py` avec :
  - `GET /api/owners/{id}/access-status` -> none / pending / active / suspended
  - `POST /api/owners/{id}/grant-access` -> cree user `role=owner`
    avec `must_change_password=True` + envoi invitation MSGRAPH ;
    si user existant -> linkage (1a : compte unique multi-ACP).
  - `POST /api/owners/{id}/resend-invitation` (uniquement si pending)
  - `POST /api/owners/{id}/revoke-access` -> `is_suspended=True`
  - `POST /api/owners/{id}/reactivate-access`
- `server.py` :
  - `POST /api/auth/forgot-password` (public, enumeration-safe,
    rate-limit 5/h par IP, token sha256 stocke, TTL 1h via index Mongo)
  - `POST /api/auth/reset-password` (single-use, expire 1h,
    invalide les autres tokens du user)
  - Middleware bloque les users `is_suspended=True` sur tous les
    endpoints + check explicite au login (403)
- `graph_email.py` : `build_password_reset_email(...)` ajoute
  un template HTML conforme branding.

**Frontend** :
- Nouvelles pages publiques : `/forgot-password`, `/reset-password?token=...`
- LoginPage : lien "Mot de passe oublie ?"
- OwnersPage : nouveau composant `OwnerAccessSection` integre dans le
  dialog d'edition d'un proprio (4 etats, boutons contextuels).

**Tests** :
- `tests/test_iter90_owner_access_and_password_reset.py` : 17 sous-flows
  orchestres dans un test pytest (acces lifecycle complet,
  suspended user blocked at login + by middleware, enumeration safety,
  rate limit, token expire/consume, mot de passe oublie multi-ACP).
- E2E curl validation OK (envoi MSGRAPH reel a selimabed@protonmail.com).


## Implemented
### Iter89c (Feb 2026) - Validation page Doublons potentiels (P0)

**Etat** : feature complete deja en place (frontend + backend + tests).
- Backend `/app/backend/routes/duplicates.py` : 3 endpoints GET (suppliers,
  owners, users) + 1 POST /owners/merge + delegation a /api/suppliers/merge
  pour la fusion fournisseurs. Chinese wall respecte.
- Frontend `/app/frontend/src/pages/AdminDuplicatesPage.js` : tabs
  Fournisseurs / Proprietaires / Utilisateurs, filtre ACP, cartes
  cliquables avec selection visuelle, badges "match: IBAN/TVA/nom/email/
  adresse/BCE/telephone", bouton "Fusionner (N a absorber)" desactive
  tant qu'aucune fiche "a conserver" n'est selectionnee.
- Route enregistree dans `App.js` (`/admin/duplicates`) + carte d'acces
  dans `AdminDashboardPage.js`.

**Validation P0 (Feb 2026)** :
- Pytest doublons (iter78, 79, 81, 84, 88d) : **25/25 passed**.
- Smoke test UI live : 35 groupes detectes sur 173 fournisseurs scannes,
  badges et boutons fonctionnels en mode superadmin (scope Plateforme).


## Implemented
### Iter89b (Feb 2026) - Mutation : trouver les proprios syndic-wide + lier auto a l'ACP

**Demande user** (2 screenshots) :
1. "Lors de mutation il faut pouvoir retrouver les proprietaires lies a l'ACP"
   (le picker affichait "Aucun proprietaire trouve" alors que ABED existait
   dans une autre ACP du syndic)
2. "En cas de doublons de proprietaire une fois qu'il est selectionne il faut
   qu'il soit sauve en tant que proprietaire dans l'ACP"

**Bug** : Chinese Wall iter85k filtrait /owners?copropriete_id=X strict ->
proprios sans lot dans cette ACP -> INVISIBLES au picker. Mais le backend
detectait quand meme le doublon a la creation -> impasse UI ("Aucun trouve"
+ "Doublon detecte").

**Fix backend** :
- `GET /owners?syndic_wide=true` : nouveau parametre qui IGNORE le scope ACP
  et retourne tous les owners accessibles au syndic via ses ACPs (RBAC
  respecte). Pour superadmin : tous les owners de la DB.
- `assign_owner_accounts(db, owner, copro_id)` (`tier_accounts.py`) :
  ajoute idempotemment `copro_id` a `owner.copropriete_ids[]` ($addToSet).
  Cela couvre TOUS les cas ou un proprio "entre" dans une ACP :
    * Mutation de lot (POST /lots/{id}/mutate appelle assign_owner_accounts)
    * Creation d'une facture impliquant le proprio
    * Toute operation qui necessite un compte tiers
  -> Apres mutation, le nouvel acquereur EST officiellement dans l'ACP
  (visible dans /owners?copropriete_id=X scope normal).

**Fix frontend** :
- `LotsPage.load()` : charge les owners avec `params: { syndic_wide: true }`
  -> Le picker mutation voit TOUS les proprios accessibles, plus uniquement
  ceux deja lies a l'ACP courante.
- `OwnerPicker.handleCreateOwner()` : si le backend renvoie 409 (doublon
  detecte), extrait l'id du proprio existant depuis le detail, refetch via
  syndic_wide, et le selectionne directement avec un toast clair
  "Proprietaire existant selectionne".
  -> Plus aucune incoherence "Aucun trouve" + "Doublon" simultanee.

**Tests** (`tests/test_iter89b_owner_picker_syndic_wide_and_link.py` - 4/4 PASS) :
1. syndic_wide=true sur ACP B retourne aussi ABED (qui est dans ACP A)
2. Scope normal (sans syndic_wide) continue a filtrer par ACP (regression)
3. assign_owner_accounts ajoute idempotemment l'ACP a copropriete_ids
4. Superadmin syndic_wide=true voit TOUS les owners

**Regression complete iter85-iter89b** : **67/67 PASS**.

**Fichiers** :
- `/app/backend/routes/properties.py` (param `syndic_wide` sur GET /owners)
- `/app/backend/tier_accounts.py` ($addToSet copropriete_ids dans assign_owner_accounts)
- `/app/frontend/src/pages/LotsPage.js` (load() syndic_wide + handleCreateOwner gere 409)
- `/app/backend/tests/test_iter89b_owner_picker_syndic_wide_and_link.py` (NEW - 4 tests)

### Iter89 (Feb 2026) - Portail proprietaire self-service : modif coords + locataires + notif syndic

**Demande user** : "Creer une interface permettant aux proprietaires d'avoir
acces a leur compte [...]. Le proprietaire doit pouvoir modifier ses coordonnees
et ajouter ses locataires, en cas de modification le syndic est averti par email."

**Existant (avant iter89)** : portail read-only complet (dashboard, ACPs, lots,
appels, charges, documents, decompte annuel PDF, VCS copy). Rien a refaire.

**iter89 ajoute** :

**Backend `routes/owner_portal.py`** :
- `PUT /api/owner/me` : update self-service des coords proprio. Champs
  whitelistes (`first_name`, `last_name`, `address`, `postal_code`, `city`,
  `country`, `email`, `email2`, `phone`, `phone2`). Les champs sensibles
  (`vcs_code`, `auxiliary_code`, `tier_accounts`) sont LOCKED. Recompute
  automatique du `name` quand last/first change.
- `GET /api/owner/tenants` : liste les locataires des lots du proprio
  uniquement (scope strict, retourne aussi `lots[]` pour le selecteur UI)
- `POST/PUT/DELETE /api/owner/tenants` : CRUD self-service. Le `lot_id`
  passe doit appartenir au proprio (verif sur `lots.owner_id` OR
  `lots.owner_ids`), sinon 403.

**Helper `owner_self_notify.py`** :
- Fonction `notify_syndic_of_owner_change(db, owner, change_type, summary_lines, ...)`
- 2 phases :
  1. PERSISTE TOUJOURS la notification dans `db.owner_notifications`
     (le syndic la verra dans l'UI meme si l'email echoue)
  2. Best-effort send via Microsoft Graph (`graph_email.send_html_email`)
     si configure (AZURE_TENANT_ID + GRAPH_SENDER_UPN dans .env)
- Recipients : tous les users avec role syndic/gestionnaire/superadmin
  ayant acces a au moins une des ACPs du proprio
- Email HTML stylise avec le diff exact des modifications

**Frontend `OwnerPortalPage.js`** :
- 2 nouveaux onglets : **"Mon profil"** (formulaire editable complet)
  et **"Mes locataires"** (table + dialog create/edit + delete)
- Toast "Coordonnees mises a jour - votre syndic a ete averti par email"
  apres save profile
- Toast "Locataire ajoute/modifie/supprime - syndic averti" apres CRUD
- Selecteur de lot dans le dialog tenant (limite a ses propres lots)
- Bouton "Ajouter" disabled si le proprio n'a aucun lot
- Dialog avec dates bail (input type=date) + loyer (input number step=0.01)

**Tests** (`tests/test_iter89_owner_portal_self_service.py` - 7/7 PASS) :
1. PUT /me : update + notification persisted
2. PUT /me : pas de modif -> pas de notification
3. PUT /me : champs whitelistes + recompute du name
4. GET /tenants : scope strict (uniquement lots du proprio)
5. POST /tenants : sur son lot OK + notification creee
6. POST /tenants : sur lot d'un autre proprio -> 403
7. PUT + DELETE /tenants/{id} : update + delete + 2 notifications creees

**Regression complete iter85-iter89** : tous tests pertinents PASS.

**Smoke UI** : verifie via login owner (sophie.martin@example.be) -> portail
charge correctement avec les 6 onglets + formulaire profil pre-rempli + onglet
locataires fonctionnel.

**Securite** :
- RBAC middleware deja permettait `/api/owner/*` pour role `owner` (toutes methodes)
- Verification ownership via `_resolve_owner` (par email) + checks lot
  (`owner_id` OR `owner_ids`)
- Aucune fuite cross-owner possible

**Fichiers** :
- `/app/backend/routes/owner_portal.py` (+200 lignes : PUT /me + CRUD tenants)
- `/app/backend/owner_self_notify.py` (NEW - helper notification)
- `/app/frontend/src/pages/OwnerPortalPage.js` (+250 lignes : 2 tabs + dialog)
- `/app/backend/tests/test_iter89_owner_portal_self_service.py` (NEW - 7 tests)

### Iter88e (Feb 2026) - CODA parser : precision decimale corrigee (millimes au lieu de centimes)

**Bug carry-over depuis iter85** : `coda_parser.py::parse_amount` divisait par
100 (centimes) au lieu de 1000 (millimes - 3 decimales fixes).

**Reference** : Febelfin "Standard CODA v2.6" - field "Bedrag/Amount" :
    Format = N15(3) -> 15 chars, 3 decimales fixes
    Exemple : 1234.56 EUR -> "000000001234560"
    Exemple : 310.00 EUR  -> "000000000310000"

**Impact du bug** : tous les montants imports CODA etaient multiplies par 10.
Un debit de 310.00 EUR etait enregistre comme 3100.00 EUR. Erreur catastrophique
sur les soldes et les rapprochements bancaires.

**Fix** : `parse_amount` divise maintenant par 1000.0 (1 ligne de code).

**Side-effect** : les fichiers CODA deja imports AVANT iter88e sont a re-importer
(ou corriger manuellement). Sur la preview : 0 statement CODA en base -> rien
a corriger. **En prod** : verifier via :
```python
db.bank_statements.count_documents({"source": "CODA"})
```
S'il y a des statements, refaire l'import du fichier .cod original (en
supprimant d'abord les transactions polluees).

**Tests** (`tests/test_iter88e_coda_amount_precision.py` - 10/10 PASS) :
1. parse_amount basique : "000000000310000" -> 310.00 EUR (cas user du handoff)
2. Sign credit (0) -> positif
3. Sign debit (1) -> negatif
4. Precision 3 decimales : 0.123 EUR
5. Gros montant : 12345678.901
6. Zero : 0.00
7. Edge cases (vide, None, non-numerique) -> 0.0
8. Cas reel facture syndic 315.72 EUR
9. Cas reel provision 1500.00 EUR
10. End-to-end : fichier CODA minimal -> mouvement 310.00 debit + soldes
    coherents (5000 -> 4690)

**Regression complete iter85-iter88e** : **62/62 PASS**.

**Fichiers** :
- `/app/backend/coda_parser.py` (1 ligne : `/ 100.0` -> `/ 1000.0` + doc CODA 2.6)
- `/app/backend/tests/test_iter88e_coda_amount_precision.py` (NEW - 10 tests)

### Iter88d (Feb 2026) - Doublon proprietaire selectionnable

**Demande user** (screenshot) : "En cas de doublons de proprietaire detecte,
permettre de le selectionner"

**Avant** : le warning "Doublon detecte" affichait juste un message texte sans
moyen d'action. L'utilisateur etait force soit d'annuler et de chercher
manuellement le proprio existant, soit de creer un doublon en DB.

**Backend** (`routes/properties.py::check_duplicate_owner`) :
- Ajout de `owner_id` + details complets (email/phone/vcs/first_name/last_name/
  copropriete_ids) dans chaque entree de `duplicates[]`
- Dedup via `seen_ids` : un meme proprio qui match sur email ET phone n'est
  retourne qu'une seule fois
- Conserve le scoping RGPD (syndic = ses ACPs seulement)

**Frontend** (`OwnersPage.js`) :
- Nouvelle UI : carte detaillee par doublon avec :
  * Nom + prenom du proprio existant
  * Email/GSM/VCS en font-mono pour scannabilite
  * Liste des champs qui ont match (italic)
  * **Bouton "Utiliser ce proprietaire"** (orange) qui :
    1. GET /owners/{id} pour recuperer la fiche complete
    2. Ferme le dialog de creation
    3. Bascule en mode edition sur le doublon (openEdit)
    4. Toast warning si le doublon est dans une autre ACP
- Dedup cote frontend (au cas ou) : groupe par `owner_id` et combine les matches
- Helper text en bas : "Cliquez sur << Utiliser ce proprietaire >> ou
  poursuivez la creation en cliquant sur << Creer >>"
- data-testid : `duplicate-warning`, `use-duplicate-{owner_id}`

**Tests** (`tests/test_iter88d_duplicate_owner_selection.py` - 4/4 PASS) :
1. Match par email -> owner_id + tous les details retournes
2. Match par phone -> idem
3. Dedup : meme owner match email + phone -> 1 seule entree (seen_ids backend)
4. Pas de match -> liste vide

**Regression complete iter85-iter88d** : **42/42 PASS**.

**Smoke UI** : verifie via screenshot - dialog "Nouveau proprietaire" avec
champ email rempli affiche la carte de doublon avec tous les details + bouton
"Utiliser ce proprietaire" fonctionnel.

**Fichiers** :
- `/app/backend/routes/properties.py` (endpoint enrichi)
- `/app/frontend/src/pages/OwnersPage.js` (carte cliquable + handler)
- `/app/backend/tests/test_iter88d_duplicate_owner_selection.py` (NEW - 4 tests)

### Iter88c (Feb 2026) - Montants non tronques / non casses sur 2 lignes dans TOUS les tableaux

**Demande user** (avec screenshot) : "donner assez de largeur pour que les
montants ne soient pas tronqués ou passés à la ligne dans les tableaux ceci
doit s'appliquer partout"

Le screenshot montrait "1144.29 EUR" casse en "1144.29" / "EUR" sur 2 lignes
dans le tableau Factures.

**Fix global via CSS** (`frontend/src/App.css`) :
```css
table td.text-right,
table th.text-right,
.data-table td.text-right,
.data-table th.text-right { white-space: nowrap; }
table td.font-mono, .data-table td.font-mono { white-space: nowrap; }
.amount-nowrap { white-space: nowrap; }
```

**Effet** : 
- Toutes les cellules `text-right` (montants, totaux) restent sur 1 ligne
- Toutes les cellules `font-mono` (codes, refs, dates) idem
- Couvre TOUS les tableaux de l'app sans modifier chaque fichier :
  InvoicesPage, ExpensesPage, BankingPage, ReportsPage, FundCallsPage,
  BalanceTiersPage, BudgetsPage, AccountingPage, LotsPage, etc.

**Renforcement explicite** sur la colonne Montant des factures (defense en
profondeur) : `min-w-[110px]` sur le header + `whitespace-nowrap` sur le td.

**Tests** : verifie via screenshot que la colonne Montant a maintenant assez
d'espace. Lint clean.

### Iter88b (Feb 2026) - Chinese Wall complet : migration localStorage -> useAuth sur les 3 dernieres pages

**Demande user** : "Remplacer les derniers `localStorage.getItem('selectedCopro')` par
`useAuth().selectedCopro` dans BankingPage, ReportsPage, FundCallsPage"

**Pourquoi** : la migration vers `AuthContext` (iter85k) etait incomplete.
Ces 3 pages utilisaient encore le pattern legacy localStorage qui :
- Ne reagissait pas au changement d'ACP (necessitait F5)
- Permettait des fuites entre ACPs si la cle localStorage etait obsolete

**Changements** :

1. **FundCallsPage** :
   - Import `useAuth`, destructuring `const { selectedCopro } = useAuth()`
   - 3 occurrences de `localStorage.getItem` remplacees (deleteAllCalls,
     regenerateEntries, repairEmptyDistributions)
   - useEffect : ajout de `selectedCopro` aux dependencies pour reload auto

2. **BankingPage** : deja utilisait `useAuth().selectedCopro` mais avait
   2 fallbacks legacy `|| localStorage.getItem('copropriete_id') || ''` :
   - handleCodaImport : suppression du fallback
   - CodaImportDialog prop : suppression du fallback

3. **ReportsPage** :
   - Import `useAuth`, destructuring
   - Variable `copro` : remplacement du `typeof window... localStorage` par
     `selectedCopro || ''`
   - useEffect : ajout `[selectedCopro]` aux dependencies + reset des donnees
     (`setBalance/setBilan/setResultat/setDecomptes` a null) au changement d'ACP
     pour eviter d'afficher le bilan d'une autre copro

**Effet user-visible** :
- Changement d'ACP -> les 3 pages se rafraichissent instantanement (sans F5)
- Les rapports d'une ACP A ne restent JAMAIS visibles apres bascule sur ACP B
- Plus aucun risque de fuite via une cle localStorage obsolete

**Tests** : pas de nouveau test (verifie par lint + smoke screenshot
FundCallsPage qui affiche bien les appels de l'ACP courante).

**Lint** : OK sur les 3 fichiers.

**Fichiers** :
- `/app/frontend/src/pages/FundCallsPage.js`
- `/app/frontend/src/pages/BankingPage.js`
- `/app/frontend/src/pages/ReportsPage.js`

### Iter88 (Feb 2026) - Cles de repartition : numero + cle par defaut selectionnable

**Demande user** : "Ajouter un numero aux cles de repartition et permettre de
definir manuellement quelle cle est la cle par defaut"

**Backend** (`routes/invoices.py`) :
- `DistKeyInput` enrichi : `code` (str optionnel) + `is_default` (bool)
- Validation unicite du code dans l'ACP (scope strict : meme code OK dans 2 ACPs)
- Mutex `is_default` : 1 seule cle par defaut par ACP (les autres repassent
  automatiquement a False quand on en marque une nouvelle)
- 2 endpoints dedies :
  * `POST /distribution-keys/{id}/set-default` : bascule rapide sans rouvrir
    le dialog d'edition (1 click depuis la liste)
  * `POST /distribution-keys/{id}/unset-default` : retire le flag
- `GET /distribution-keys` : tri par `(code, name)` (les sans-code en fin)

**Frontend** (`InvoicesPage.js`) :
- Dialog d'edition de cle :
  * Nouveau champ "N° (optionnel)" placeholder "ex: 001"
  * Checkbox "Definir comme cle par defaut pour cette ACP" (avec icone Star
    + bg amber + helper text)
- Liste des cles :
  * Nouvelle colonne "N°" en font-mono (a gauche)
  * Nouvelle colonne "Defaut" (centree) avec bouton Star :
    - Cle par defaut -> Star pleine + "Par defaut" + clic = unset
    - Autres cles -> Star vide + "Definir" + clic = set
  * Row teintee `bg-amber-50/40` pour la cle par defaut (visibilite immediate)
  * Icone Star plein a cote du nom de la cle par defaut
- Handler `toggleDefaultKey(k, makeDefault)` : 1 click pour set/unset, sans dialog
- `data-testid` : `key-code`, `key-is-default`, `key-default-on-{id}`,
  `key-default-off-{id}`, `key-code-{id}`

**Tests** (`tests/test_iter88_dist_keys_code_and_default.py` - 7/7 PASS) :
1. Create avec code + is_default=True
2. Mutex 1 seule cle default par ACP (la 2e set ramene la 1ere a False)
3. Code unique par ACP -> 409 si doublon
4. Meme code OK dans 2 ACPs differentes (scope strict)
5. Endpoint /set-default et /unset-default
6. Tri GET par code asc puis name asc
7. PUT update : champs name/desc/lots conserves + code/is_default appliques

**Regression complete iter82-iter88** : tous tests pertinents PASS.

**Fichiers** :
- `/app/backend/routes/invoices.py` (modele + endpoints + helpers)
- `/app/frontend/src/pages/InvoicesPage.js` (form + table + handler toggle)
- `/app/backend/tests/test_iter88_dist_keys_code_and_default.py` (NEW - 7 tests)

### Iter87 (Feb 2026) - Migration storage filesystem -> MongoDB GridFS (PERSISTANT)

**Probleme critique resolu** : "Les PDFs uploades sont TOUJOURS perdus apres un deploiement"
-> Le filesystem `/app/uploads/` etait EPHEMERE (efface a chaque redeploy).
A partir d'iter87, **tout est stocke en MongoDB GridFS** (persistant + backup).

**Architecture** :
- `gridfs_storage.py` : helper unique pour tous les buckets
  - Methodes : `upload`, `download`, `delete`, `stat`, `exists`, `stream_download`
  - Buckets :
    * `invoice_attachments` : PJ factures fournisseurs
    * `invoice_bundles`     : PDF d'import multi-pages (TTL 24h)
    * `journal_attachments` : PJ ecritures comptables (OD)
    * `documents`           : documents legaux (PV AG, contrats, etc.)
  - Fix motor : `stream.close()` SYNC (pas await) car `GridOut` wrapper
- Chaque attachment porte maintenant un champ `gridfs_id` (nouveau) ET conserve
  `stored_path` pour audit/migration.
- Endpoints download : prefer GridFS si `gridfs_id` present, sinon fallback disque
  (retrocompat pour PJ creees avant la migration mais non-migrees).

**Migration des 1.7 GB existants** (`scripts/migrate_uploads_to_gridfs.py`) :
- Parcourt `invoices.attachments[]`, `journal_entries.attachments[]`, `documents`
- Upload chaque fichier disque vers son bucket GridFS, ecrit `gridfs_id`
- Idempotent : skip si `gridfs_id` deja present
- Mode `--dry-run` pour preview
- **Resultat preview** : 40 PJ factures migrees = 697.82 MB (14 documents
  "missing" car deja perdus lors d'un precedent redeploy)
- **TODO PROD** : lancer `python /app/backend/scripts/migrate_uploads_to_gridfs.py`
  via terminal Emergent (l'agent n'a pas acces production)

**Index TTL (24h) pour les sessions bundle PDF** :
- Au startup du backend, creation auto de l'index TTL sur
  `invoice_bundle_sessions.expires_at` (BSON ISODate)
- Mongo supprime automatiquement les sessions expirees + leur PDF GridFS
- Termine donc le risque "import inacheve = PDF orphelin sur disque pour toujours"

**Refactor `invoice_ai.py`** : suppression complete de l'ecriture disque.
L'extraction IA utilise `NamedTemporaryFile(delete=False)` puis `os.unlink()` -
zero trace sur disque ni GridFS (le user reuploadera le PDF s'il veut l'attacher).

**Refactor `coproprietes.py` (suppression ACP)** : la cascade delete supprime
maintenant aussi les fichiers GridFS de tous les buckets (invoice, journal,
documents) en plus du disque legacy.

**Tests** (`tests/test_iter87_gridfs_migration.py` - 6/6 PASS) :
1. Upload + download d'une PJ facture via GridFS (gridfs_id present, stored_path absent)
2. Delete d'une facture -> cascade GridFS (les 2 PJ disparaissent du bucket)
3. Upload + download + delete d'une PJ ecriture comptable (OD)
4. Upload + download + delete d'un document
5. Retrocompat : une PJ legacy (stored_path seul) reste lisible via fallback disque
6. Bundle session : PDF en GridFS + metadata `expires_at` BSON pour TTL

**Regression complete iter82-iter87** : **56/56 PASS** (mutations, decomptes,
chinese wall, frais privatifs, homonymes, scope ACP, GridFS).

**Fichiers** :
- `/app/backend/gridfs_storage.py` (fix sync close + bucket helpers)
- `/app/backend/routes/invoices.py` (GridFS upload/download/delete + bundle GridFS+TTL)
- `/app/backend/routes/accounting.py` (GridFS pour journal_attachments)
- `/app/backend/routes/documents.py` (GridFS pour documents + tempfile IA)
- `/app/backend/routes/invoice_ai.py` (tempfile-only, zero persistance disque)
- `/app/backend/routes/coproprietes.py` (cascade delete GridFS)
- `/app/backend/server.py` (TTL index auto au startup)
- `/app/backend/scripts/migrate_uploads_to_gridfs.py` (NEW)
- `/app/backend/tests/test_iter87_gridfs_migration.py` (NEW - 6 tests)

### Iter86 (Feb 2026) - Frais privatifs visibles dans liste des depenses + tri par colonne factures

**Demandes user** :
1. "Dans les factures ajouter des filtres par colonne permettant de classer par ordre chronologique ou alphabetique les differentes colonnes"
2. "Toutes les depenses doivent etre prises en compte dans la liste des depenses meme les frais privatifs comme ici" (PDF Optipro "Liste des depenses 01/10/2025-30/06/2026" fourni)

**Bug 1 - Frais privatifs invisibles dans la liste des depenses** :
- **Root cause** : `routes/fiscal.py::list_expenses` cherchait `je.get("source_invoice_id")` (champ inexistant). Pour une facture privative :
  - La facture etait listee avec compte 643 a +amount
  - L'OD d'imputation (Dr owner / Cr 643) etait AUSSI listee a -amount (sur 643)
  - Total compte 643 = 0 -> le frais privatif etait neutralise dans le total
- **Fix** :
  - Filtre corrige : `if je.get("source_type") == "invoice" and je.get("source_id") in invoice_ids_done: continue`
  - Enrichissement des rows facture privative : nouveaux champs `is_private_fee`, `private_fee_allocations[]` (avec owner_name pre-resolu), `private_fee_owners_display` (CSV des noms)
  - Resolution des owners en bulk via `owners_by_id` (1 seul find Mongo)
- **Frontend** (`ExpensesPage.js`) :
  - Mode flat : ligne entiere teintee `bg-purple-50/30`, description + badge violet "Privatif" + nom(s) du/des proprietaire(s)
  - Mode hierarchique : meme badge, plus compact
  - `data-testid` : `expense-private-{id}`

**Feature 2 - Tri cliquable par colonne sur les factures** (`InvoicesPage.js`) :
- Nouveau state `invSort = { key, dir }`, defaut `{ key: 'date', dir: 'desc' }`
- 7 colonnes triables : Ref. interne / N fournisseur / Date / Fournisseur / Description / Montant / Statut
- Icones lucide ArrowUp / ArrowDown / ArrowUpDown affichees a cote du label
- Click sur entete : 1er click ASC, 2eme click DESC
- Tri stable cote client, numerique pour Montant, lexicographique ISO pour Date
- `data-testid` : `inv-sort-{key}` pour chaque entete cliquable

**Tests** (`tests/test_iter86_private_fees_in_expenses_list.py` - 3/3 PASS) :
1. Facture privative single-owner 155 EUR (cas notaire MATEXI/SRL Finlead reel) -> 1 row +155, total +155, owner expose
2. Facture privative multi-allocs 600/400 -> 1 row +1000, 2 owners exposes, names dans display
3. Facture normale 500 EUR -> non doublee, total +500 (regression)

**Regression complete** : 38/38 PASS sur stack iter85+iter86 (frais privatifs, mutations, decomptes, scope ACP, homonymes).

**Fichiers** :
- `/app/backend/routes/fiscal.py` (fix source_invoice_id -> source_id + enrichissement)
- `/app/backend/tests/test_iter86_private_fees_in_expenses_list.py` (NEW - 3 tests)
- `/app/frontend/src/pages/ExpensesPage.js` (badge "Privatif" + display owners)
- `/app/frontend/src/pages/InvoicesPage.js` (tri par colonne cliquable)

### Iter85k (Feb 2026) - Scope owners par ACP + event copropriete-changed + sentinelle 'all'

**Probleme** : OwnersPage maintenait son propre state `selectedCopro` initialise depuis
localStorage, et ecoutait un event `copropriete-changed` jamais emis par AuthContext.
Resultat : la liste restait sur l'ancienne ACP au changement, ou partait avec
selectedCopro vide -> tous les owners (toutes ACPs) affiches.

**1. AuthContext** (`contexts/AuthContext.js`) :
- `setSelectedCopro(id)` dispatche desormais `window.dispatchEvent(new CustomEvent('copropriete-changed', { detail: { copropriete_id: id } }))`
- Permet aux pages avec listener legacy de se rafraichir (filet de securite).

**2. OwnersPage** (`pages/OwnersPage.js`) :
- **Option propre adoptee** : lecture directe de `selectedCopro` depuis
  `useAuth()` (plus de useState local + listener).
- `load()` n'appelle PAS `/owners` tant que `selectedCopro` est vide.
- Etat visuel **"Selectionnez une ACP"** (icone AlertTriangle + texte explicatif
  sur le chinese wall) affiche quand aucune ACP n'est selectionnee.
- Quand `selectedCopro` defini : `GET /owners?copropriete_id=<id>` (backend scope
  via jointure sur lots).

**3. api.js** (`lib/api.js`) :
- `/owners` **retire** de `GLOBAL_PATH_PREFIXES` -> auto-injection du
  `copropriete_id` et du header `X-Copropriete-Id` par l'intercepteur.
- Filet de securite : meme si une page oublie de passer le param, l'intercepteur
  l'ajoute automatiquement.

**4. Backend** (`routes/properties.py::list_owners`) :
- Nouvelle sentinelle `copropriete_id == 'all'` traitee comme `None` :
  retourne TOUS les owners (vue plateforme). Utilisee par CoproprietesPage
  pour creer une nouvelle ACP et lier des owners existants (eviter doublons).
- Le header `X-Copropriete-Id` est deja utilise comme fallback du query param.

**5. CoproprietesPage** (`pages/CoproprietesPage.js`) :
- Tous les `api.get('/owners', ...)` (6 occurrences : load, refreshOwnersIfStale,
  PDF import, autres) passent maintenant explicitement `copropriete_id: 'all'`
  pour conserver l'acces cross-ACP necessaire a la creation d'ACP.

**Tests** (`tests/test_iter85k_owners_scope_acp.py` - 4/4 PASS) :
1. `copro_id="all"` -> tous les owners (cross-ACP + orphans pour superadmin)
2. `copro_id=cid1` -> scope strict (o1, o3 dans cid1 ; o2 exclu)
3. Header `X-Copropriete-Id` utilise quand query vide
4. `include_unassigned=true` ajoute les orphans (pour CoproprietesPage)

**Regression complete** : 62/62 PASS sur stack iter82-85.

**Fichiers** :
- `/app/frontend/src/contexts/AuthContext.js` (event dispatch)
- `/app/frontend/src/pages/OwnersPage.js` (useAuth + etat vide + scope)
- `/app/frontend/src/lib/api.js` (retrait /owners de GLOBAL_PATH_PREFIXES)
- `/app/backend/routes/properties.py` (sentinelle 'all')
- `/app/frontend/src/pages/CoproprietesPage.js` (explicite 'all' x6)
- `/app/backend/tests/test_iter85k_owners_scope_acp.py` (NEW)

### Iter85i-j (Feb 2026) - Allocations privatifs : recherche + arrondi en centimes + chinese wall

**Bugs critiques resolus en cascade** :

**1. Bug arrondi flottant (iter85j) - "OK equilibre" trompeur** :
   - Cas : total facture 90.02 EUR / somme allocations 90.01 EUR -> badge VERT "OK equilibre" alors qu'il y a un ecart de 0.01 EUR.
   - Cause : `Math.abs(0.02 - 0.01) = 0.00999...` < 0.01 en flottant JavaScript.
   - Fix : comparaison en **CENTIMES (entiers)** partout (frontend + backend).
     - `sumCents = allocs.reduce((s,a) => s + Math.round(amount * 100), 0)`
     - `balanced = (totalCents === sumCents)`
   - Bouton **"Equilibrer"** ajoute pour repartir le cent manquant sur la derniere ligne en 1 click.
   - Validation backend POST + PUT aussi en centimes, message d'erreur expose l'ecart precis.

**2. Recherche dans le dropdown owner (iter85i)** :
   - Cas : ACP Acacia a 60+ proprietaires, le `Select` shadcn etait inutilisable (pas de recherche, scroll geant).
   - Fix : nouveau composant `OwnerComboboxAlloc` (Popover + Command shadcn) :
     - Champ recherche libre : filtre par nom + prenom + VCS code + email
     - Display "Rechercher un proprietaire (nom, VCS, email)..."
     - Affiche le proprietaire choisi avec son VCS en mono
     - Exclut les owners deja selectionnes dans les autres lignes
     - data-testid : `alloc-owner-combo-{idx}`, `alloc-owner-combo-{idx}-input`,
       `alloc-owner-combo-{idx}-option-{owner_id}`

**3. Bug chinese wall (iter85h fix) - propriétaires d'autres ACPs visibles** :
   - Cause : la cle localStorage correcte est `'selectedCopro'` (cf. `lib/api.js`)
     mais mon code iter85e utilisait `'copropriete_id'` -> toujours null -> backend
     sans scope -> tous les owners retournes.
   - Fix : pattern `localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || ''`
     applique dans :
     - `pages/InvoicesPage.js` : load owners (chinese wall), aiExtractFromPdf,
       createSupplierWithHomonymCheck, 2 inline URLs attachments
     - `pages/SuppliersPage.js` : handleSave (check-duplicate)
     - `pages/ReportsPage.js`
     - `components/BundleImportDialog.js`
   - Frontend envoie maintenant aussi le header `X-Copropriete-Id` explicitement
     pour `/owners` dans le contexte facture.

**Tests** (etendus dans `test_iter85e_private_fee_multi_allocations.py`) :
- `test_iter85j_validation_one_cent_off_blocks` : 45.00 + 45.01 != 90.02 detecte
- `test_iter85j_validation_exactly_balanced_passes` : 45.01 + 44.99 = 90.00 passe

**Regression complete** : 58/58 PASS sur stack iter82-85.

**Fichiers** :
- `/app/backend/routes/invoices.py` (validation en centimes POST + PUT)
- `/app/frontend/src/pages/InvoicesPage.js` (combobox + centimes + bouton Equilibrer + localStorage fix)
- `/app/frontend/src/pages/SuppliersPage.js` (localStorage fix)
- `/app/frontend/src/pages/ReportsPage.js` (localStorage fix)
- `/app/frontend/src/components/BundleImportDialog.js` (localStorage fix)
- `/app/backend/tests/test_iter85e_private_fee_multi_allocations.py` (+2 tests)

### Iter85h (Feb 2026) - Detection homonymes etendue au dialog facture

**Demande user** : "Etendre la detection au champ Fournisseur du dialog
facture (creation auto via texte libre depuis InvoicesPage) - actuellement
seule la page Fournisseurs beneficie de la confirmation."

**Frontend** (`pages/InvoicesPage.js`) :
- Nouvelle fonction helper `createSupplierWithHomonymCheck(payload)` :
  - Promise-based, gere le flux complet : pre-check -> dialog si similaires -> POST avec force
  - Pre-appel `/suppliers/check-duplicate` avant tout POST
  - Si `exact` : toast d'erreur + pre-remplit le champ Fournisseur avec le sup existant
  - Si `similar` : ouvre dialog modal `<Dialog data-testid="invoice-supplier-homonyms-dialog">`
  - Si aucun : POST direct (force=false)
- 2 chemins de creation utilisent maintenant ce helper :
  1. Bouton "Creer la fiche" du bandeau `suggestCreateSupplier` (suggestion IA / extraction PDF)
  2. `SupplierSearchSelect.onCreateSupplier` (autocomplete dialog facture)
- Dialog "Homonymes potentiels detectes" identique a celui de SuppliersPage :
  - Liste les sup similaires avec score %, TVA, ville, IBAN
  - Bouton "Utiliser celui-ci" : remplit le champ `invForm.supplier` avec le sup existant + ferme dialog
  - Bouton "Creer quand meme" (amber) : POST avec `force_create_despite_similar=true`
  - Bouton "Annuler"
- data-testid : `invoice-supplier-homonyms-dialog`, `inv-similar-supplier-{i}`,
  `inv-use-existing-supplier-{i}`, `inv-similar-cancel-btn`, `inv-similar-force-create-btn`

**Effet user-visible** :
- Lors de la saisie d'une facture, si l'utilisateur essaie de creer un nouveau
  fournisseur (via autocomplete ou suggestion IA post-extraction PDF), le systeme
  detecte les homonymes proches AVANT la creation et propose :
  - Soit reutiliser un fournisseur existant (1 click "Utiliser celui-ci")
  - Soit confirmer la creation malgre la similitude (1 click "Creer quand meme")
- En cas de doublon strict (meme nom/TVA/IBAN), le champ Fournisseur de la
  facture est auto-rempli avec le sup existant (pas de creation, ni de toast bloquant).

**Pas de changement backend** : reutilise l'endpoint `/suppliers/check-duplicate`
et le flag `force_create_despite_similar` introduits dans iter85g.

**Regression** : 17/17 PASS sur tests backend touches (iter84/85e/85g).

**Fichier modifie** :
- `/app/frontend/src/pages/InvoicesPage.js` (helper + dialog + 2 callsites)

### Iter85g (Feb 2026) - Detection homonymes fournisseurs avec confirmation

**Demande user** : "il faut eviter les doublons de fournisseur verifier les
noms ou les possibles homonyme et demander confirmation avant la creation."

**Etat avant** : detection STRICTE (BCE/TVA/IBAN/nom normalise) existait deja
mais aucune detection des coquilles / homonymes proches (ex. "ELEC PLUS SRL"
vs "ELEC PLUSE SRL").

**Backend** (`routes/suppliers.py`) :
- Nouvelle fonction `find_similar_suppliers(name, copro_id, threshold=0.80)`
  utilisant `difflib.SequenceMatcher` sur les noms normalises (mots tries).
  Exclut les matches strictement identiques (geres par `find_duplicate_supplier`)
  pour eviter le double-traitement. Scope ACP strict.
- Nouveau endpoint `POST /api/suppliers/check-duplicate` qui retourne
  `{exact: {...} | null, similar: [{supplier, score}]}`. Appele par le
  frontend AVANT le POST de creation.
- `create_supplier` bloque desormais aussi sur les similaires sauf si
  `force_create_despite_similar=true` (HTTP 409 avec liste des homonymes).
- `SupplierInput.force_create_despite_similar: bool = False` ajoute. Le flag
  est exclu de `model_dump()` lors de la persistance (jamais stocke en base).

**Frontend** (`pages/SuppliersPage.js`) :
- `handleSave` (creation) : pre-appelle `/suppliers/check-duplicate` avant
  POST. Si `exact` -> toast d'erreur. Si `similar` non vide -> ouvre un
  Dialog de confirmation.
- Nouveau Dialog "Homonymes potentiels detectes" :
  - Liste les fournisseurs similaires avec leur score (%), TVA, ville, IBAN
  - Bouton "Utiliser celui-ci" sur chaque ligne -> ouvre l'edit du sup existant
  - Bouton "Creer quand meme" (amber) -> POST avec force_create_despite_similar=true
  - Bouton "Annuler"
- data-testid : `similar-suppliers-dialog`, `similar-supplier-{i}`,
  `use-existing-supplier-{i}`, `similar-cancel-btn`, `similar-force-create-btn`

**Tests** (`tests/test_iter85g_supplier_homonyms.py` - 7/7 PASS) :
1. check-duplicate detecte exact match
2. check-duplicate detecte coquille (score >= 0.80)
3. check-duplicate ne match pas noms tres differents
4. POST sans force -> 409 sur similaire
5. POST avec force=true cree quand meme + flag non stocke
6. Scope ACP : sup d'une autre ACP n'apparait pas
7. find_similar exclut les matches stricts (deduplication)

**Regression complete** : 56/56 PASS sur stack iter82-85.

**Fichiers** :
- `/app/backend/routes/suppliers.py` (find_similar_suppliers + check-duplicate + force flag)
- `/app/frontend/src/pages/SuppliersPage.js` (pre-check + dialog confirmation)
- `/app/backend/tests/test_iter85g_supplier_homonyms.py` (NEW)

### Iter85f (Feb 2026) - Descriptions de lignes de factures propagees aux decomptes

**Demande user** : "permettre d'ajouter un commentaire sur les lignes de
factures qui seront visibles dans la description de la liste de depenses
et decomptes"

**Etat avant fix** :
- Saisie : OK (champ `description` par ligne deja present dans InvoicesPage,
  data-testid `invoice-line-desc-{idx}`)
- Liste des depenses (`fiscal.py::list_expenses`) : OK depuis iter83
  (priorise `li.description or inv.description`)
- Decomptes : **BUG** - utilisait toujours `inv.description` (globale)

**Fix** :
- `pdf_decompte.py` : quand on stocke `acc_bucket["invoices"]`, on cherche les
  lignes de la facture qui touchent le compte courant. Si une/plusieurs lignes
  ont une description, on les concatene avec " - ". Sinon fallback sur
  `inv.description` globale.
- `routes/reports.py::decompte_annuel` (endpoint JSON) : agregation similaire,
  toutes les descriptions de lignes de la facture concatenees.

**Effet user-visible** :
- Decompte PDF (sous "1. Detail de vos charges") : affiche les libelles precis
  des lignes (ex. "Honoraires reunion AG du 12/03 - Nettoyage hall - operation
  ponctuelle") au lieu d'une description generique de facture
- Decompte JSON (utilise par le frontend pour preview) : pareil
- Liste des depenses (deja OK) : 1 ligne par item avec sa description

**Tests** (`tests/test_iter85f_line_descriptions_in_decomptes.py` - 3/3 PASS) :
1. `decompte_annuel_uses_line_descriptions` : 2 lignes avec descriptions
   distinctes -> les 2 libelles apparaissent dans charges[].description
2. `decompte_annuel_single_line_legacy` : facture sans `lines[]` conserve
   description globale (retrocompat)
3. `pdf_decompte_includes_line_description` : libelle ligne present dans le
   PDF (extrait via pypdf)

**Regression complete** : 49/49 PASS sur stack iter82-85.

**Fichiers** :
- `/app/backend/pdf_decompte.py` (acc_bucket invoices append - desc enrichie)
- `/app/backend/routes/reports.py` (decompte_annuel - desc enrichie)
- `/app/backend/tests/test_iter85f_line_descriptions_in_decomptes.py` (NEW)

### Iter85e (Feb 2026) - Frais privatifs multi-allocations + chinese wall owners

**Demande user** :
  - Repartir une facture frais privatif sur PLUSIEURS proprietaires
  - Mode : montants fixes en EUR par owner (somme = total facture)
  - Dropdown owners filtre par ACP courante (chinese wall strict)
  - Comportement : 100% proprietaire (pas d'occupant)
  - Affichage : quote-part par owner dans sa situation de compte

**Backend** (`routes/invoices.py`) :
- Nouveau modele `PrivateFeeAllocation(owner_id, amount)`
- `InvoiceInput.private_fee_allocations: Optional[List[PrivateFeeAllocation]]`
- Validation POST + PUT : somme == total_amount (tolerance 0.01), owners existent
- Rétrocompat : `private_fee_owner_id` legacy converti en 1-element allocation
- Persistance du champ `private_fee_allocations` sur la facture

**Backend** (`auto_entries.py::generate_purchase_entry`) :
- Refonte du bloc frais privatif :
  - AC : Dr 643 (total) / Cr 44000XXX fournisseur (total) - inchangee
  - OD : N lignes (1 DR 4100XXX owner + 1 CR 643) PAR allocation
  - Resolution des comptes owner via `assign_owner_accounts` + `get_owner_accounts`
- Equilibre par construction : `total_debit = total_credit = somme allocations`

**Frontend** (`pages/InvoicesPage.js`) :
- Load owners : ajout du parametre `?copropriete_id=X` (chinese wall)
- Dialog frais privatif : remplace single owner picker par tableau dynamique
  d'allocations [{owner_id, amount}] :
  - Bouton "+ Ajouter un proprietaire"
  - Select scope ACP (chinese wall) avec filtrage des owners deja choisis
  - Input montant EUR par ligne
  - Live total : "Somme allocations X.XX / Total facture Y.YY" (vert si OK, rouge sinon)
  - Bouton Trash pour supprimer une ligne
- Validation cote front avant submit : somme = total, pas de doublon, montants > 0
- data-testid : `private-fee-allocations`, `alloc-owner-select-{idx}`,
  `alloc-amount-{idx}`, `alloc-remove-{idx}`, `add-private-fee-allocation`,
  `alloc-balance-status`

**Tests** (`tests/test_iter85e_private_fee_multi_allocations.py` - 5/5 PASS) :
1. Create avec 2 allocations 600/400 -> AC 2 lignes + OD 4 lignes equilibrees
2. Validation somme mismatch (700 vs 1000) -> 400
3. Retrocompat private_fee_owner_id legacy -> 1 allocation derivee + OD 2 lignes
4. Update single -> multi : OD regeneree avec 4 lignes (montants corrects)
5. Chinese wall : `GET /api/owners?copropriete_id=X` exclut owners d'autres ACPs

**Regression complete** : 46/46 PASS sur stack iter82-85.

**Fichiers modifies** :
- `/app/backend/routes/invoices.py` (PrivateFeeAllocation + validation + persistance POST/PUT)
- `/app/backend/auto_entries.py` (refonte bloc frais privatif multi-owners)
- `/app/frontend/src/pages/InvoicesPage.js` (UI tableau d'allocations + chinese wall)
- `/app/backend/tests/test_iter85e_private_fee_multi_allocations.py` (NEW)

### Iter85d (Feb 2026) - Reprise comptable : ODs de mutation pre-periode exclues

**Bug user** : "Une mutation n'est PAS une reprise comptable tu as completement
foire la". Les ODs de mutation (datees AVANT le start_date du rapport) etaient
agregees dans la ligne "Reprise comptable" synthetique, ce qui masquait
totalement le transfert vendeur -> acheteur du fonds de roulement et du prorata.

**Fix** (`routes/reports.py`) - 2 endpoints touches :
- `situation_compte_owner` (lignes ~1323+) :
  - Helper `_is_mutation_entry` : detecte `source_type='lot_mutation'` OU `reference` commence par `MUT-`
  - Pendant le scan des `pre_entries` (date < start_date) : si l'entry est une
    mutation, ses lignes sont collectees dans `pre_mutation_movements` (datees
    a leur jour d'origine) au lieu d'etre agregees dans `an_debit/an_credit`
  - Apres l'insertion de la ligne "Reprise comptable", on ajoute les lignes
    de mutation pre-periode comme mouvements distincts avec :
    * `date` = date d'origine (ex. 2025-12-15)
    * `source_type` = "lot_mutation"
    * `is_pre_period_mutation` = true (pour styling frontend potentiel)
    * description complete avec [OD] + libelle
- `situation_compte_supplier` (lignes ~1916+) : meme fix par coherence

**Resultat user-visible** :
- Vendeur : situation de compte FY 2026 montre les CREDITS de mutation datees
  2025-12-15 (au lieu d'etre noyes dans une ligne Reprise opaque)
- Acheteur : situation de compte FY 2026 montre les DEBITS de mutation datees
  2025-12-15 distinctement
- Les A-Nouveau (cloture exercice anterieur) restent dans la Reprise (regle
  inchangee : ils sont une vraie ouverture comptable, pas une mutation)

**Tests** (`tests/test_iter85d_reprise_excludes_mutations.py` - 3/3 PASS) :
- `mutation_pre_period_not_in_reprise` : 2 ODs mutation au 2025-12-15 sur FY 2026
  -> 0 ligne Reprise, 2 lignes mutation distinctes datees 2025-12-15
  (CR 1300 vendeur, DR 1300 acheteur)
- `an_entry_remains_in_reprise_alongside_mutation` : AN 500 + mutation 800
  pre-periode -> Reprise = 500 + 1 ligne mutation distincte = 800
- `mutation_in_period_still_in_movements` : mutation IN-period reste dans
  mouvements normaux (pas marquee `is_pre_period_mutation`)

**Regression complete** : 41/41 PASS sur stack iter82-85.

**Fichiers** :
- `/app/backend/routes/reports.py` (2 sections Reprise modifiees)
- `/app/backend/tests/test_iter85d_reprise_excludes_mutations.py` (NEW)

### Iter85c (Feb 2026) - PDF futurs : montants reels + wrap + sous-totaux trimestre + cascade lots

**Demandes user** :
  1. BUG : montants des appels futurs a 0,00 EUR dans le PDF (lecture cle "lot_amount" inexistante)
  2. BUG : colonne "Appel" deborde sur "Date appel" sur libelles longs
  3. Sous-totaux par trimestre dans le bloc 3 du PDF
  4. Cascade par lot (parent/enfants) dans FundCallsPage

**Fixes PDF** (`pdf_mutation_decompte.py`) :
- Lecture montant : `f.get("amount", f.get("lot_amount", f.get("owner_amount", 0)))` (la cle reelle stockee est "amount", cf. `_compute_mutation_breakdown`)
- Wrap automatique : toutes les cellules du bloc 3 sont enveloppees dans des `Paragraph(text, style)` (style cell_left/cell_right) pour activer le retour a la ligne
- colWidths ajustees : `[56*mm, 24*mm, 58*mm, 40*mm]` (plus de marge sur la colonne Appel)
- Sous-totaux par trimestre :
  - Detection T1/T2/T3/T4 via regex "trimestriel X/4" OU mois de period_start
  - Insertion d'une ligne fond SLATE_100 "Sous-total TX ... montant" apres chaque groupe
  - Sous-total general "Sous-total appels futurs" conserve en fin (fond BLUE_BG)

**Fixes backend** (`routes/fund_calls.py`) :
- `create_fund_call` (distribution manuelle) : ajout du champ `parent_lot_id` issu de `lot.get("parent_lot_id", "")` dans chaque entry de distribution
- `_distribute_amount` refactor : retourne maintenant une LISTE d'entrees par LOT (avec `lot_id`, `lot_number`, `parent_lot_id`, `owner_id`, `owner_name`, `vcs_code`, `amount`, `share`) au lieu d'un dict agrege par owner
- Boucle d'agregation dans `generate_from_budget` : utilise `lot_agg` (indexe par lot_id) pour preserver le detail par lot. La distribution finale a 1 entree par LOT (et non par owner) avec `parent_lot_id`
- Idem pour `_generate_independent_series` (reserve/roulement avec frequency propre)

**Fixes frontend** (`pages/FundCallsPage.js`) :
- Helper `sortLotsCascade` : tri parents d'abord (alphanumerique), enfants ensuite avec `_depth=1`
- Affichage tableau : `pl-14` pour les enfants (vs `pl-8` parents), icone `└─` et badge "(secondaire)"
- Retrait de la condition `if (!hasAnyLot && g.lots.length === 1) return null;` -> le detail est toujours visible
- Comportement retrocompatible : si parent_lot_id absent (anciens fund_calls), tous les lots sont consideres parents (pas de cascade)

**Tests** (`tests/test_iter85b_cascade_and_pdf.py` - 4/4 PASSED) :
- `test_iter85b_create_fund_call_parent_lot_id` : verifie parent_lot_id dans la distribution
- `test_iter85b_pdf_uses_amount_key` : "500,00" apparait 3+ fois (texte extrait via pypdf)
- `test_iter85b_pdf_subtotals_per_trimester` : "Sous-total T2/T3/T4" + "Sous-total appels futurs" presents
- `test_iter85b_pdf_long_appel_wraps` : libelle long encode et lisible

**Regression complete** : 38/38 PASS sur stack iter82-85.

**Fichiers** :
- `/app/backend/pdf_mutation_decompte.py` (bloc 3 refait)
- `/app/backend/routes/fund_calls.py` (_distribute_amount par lot + parent_lot_id partout)
- `/app/frontend/src/pages/FundCallsPage.js` (cascade UI)
- `/app/backend/tests/test_iter85b_cascade_and_pdf.py` (NEW - 4 tests)

### Iter85b (Feb 2026) - Endpoint PDF "Decompte de mutation" expose

**Demande user** : "Endpoint GET /api/lots/{lot_id}/mutations/{mutation_id}/
decompte.pdf + bouton frontend (le builder PDF pdf_mutation_decompte.py est
deja complet, manque juste l'exposition route)."

**Implementation backend** (`routes/properties.py`) :
- Nouveau endpoint `GET /api/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf`
- Accepte `mutation_id = "last"` ou un UUID exact
- Recupere lot + mutation_record + copropriete + vendeur + acheteur
- Reconstruit le breakdown depuis le mutation_record persiste (roulement_quota,
  current_period_prorata, future_calls, etc.)
- Appelle `pdf_mutation_decompte.build_mutation_decompte_pdf` et retourne
  StreamingResponse avec `Content-Disposition: attachment`
- Filename pattern : `decompte_mutation_lot_{number}_{YYYYMMDD}.pdf`
- 404 si lot ou mutation introuvables

**Frontend** (`pages/LotsPage.js`) :
- Bouton bleu "PDF" (icone FileDown) ajoute a CHAQUE mutation dans l'historique
  (la derniere mutation a "PDF" + "Annuler", les anciennes ont juste "PDF")
- Telechargement via blob + lien <a download> (pattern identique a Decomptes locataires)
- data-testid : `download-mutation-pdf-{mutation_id}`

**Tests** (4 tests PASSED) :
- `test_iter85_pdf_decompte_valid_pdf` : 200 + magic bytes %PDF + >2000 bytes
- `test_iter85_pdf_decompte_mutation_id_last` : "last" resoud derniere mutation
- `test_iter85_pdf_decompte_invalid_lot_404`
- `test_iter85_pdf_decompte_invalid_mutation_404`

**Fichiers** :
- `/app/backend/routes/properties.py` (nouvel endpoint)
- `/app/backend/tests/test_iter85_pdf_decompte_mutation.py` (NEW)
- `/app/frontend/src/pages/LotsPage.js` (bouton PDF + import FileDown)

### Iter85 (Feb 2026) - Mutation : OD futures aux dates des appels (refonte)

**Demande user** : "La balance de tier doit lister chaque appel de provision a
sa date d'appel (01.01, 01.04, 01.07, 01.10), peu importe la date de mutation.
Le VE des appels futurs reste au nom du proprietaire original (vendeur) dans la
distribution. La mutation cree une ecriture OD (DR acheteur / CR vendeur) a la
date de chaque appel futur, pour le montant de la quote-part du lot mute."

**Refonte de `_apply_to_lot` dans `routes/properties.py`** :

1. **Nouveau bloc "3) Appels futurs"** (apres prorata) :
   - Itere sur `bd["future_calls"]` (groupes par date)
   - Cree 1 ecriture OD par DATE d'appel futur (DR `new_acc` / CR `old_acc`)
   - `source_subtype = "future_call"`
   - Quote-part du lot = `f["amount"]` (sortie de `_compute_lot_amount_in_call`)
   - Push de tous les IDs dans `journal_entry_ids`
   - Ajout au breakdown `entries_created`

2. **Neutralisation de `_regenerate_future_calls_after_mutation`** :
   - L'appel a la fonction est remplace par `mut_rec["regenerated_calls"] = {"fixed": 0, "info": "future calls now booked via OD per call date (iter85)"}`
   - La distribution des appels FUTURS reste INTACTE (owner_id = vendeur)
   - Pas de double comptage : la quote-part est portee par les OD futures

**Effet sur les rapports** :
- `situation_compte` du vendeur : 1 ligne credit par appel futur a la bonne date
- `situation_compte` de l'acheteur : 1 ligne debit par appel futur a la bonne date
- Balance de tier reflete correctement le transfert a chaque date d'appel
- Les ODs "MUT-XXX-F" (suffix F pour future) sont supprimees par `cancel_mutation`
  (qui itere sur `journal_entry_ids`)

**Tests** :
- `tests/test_iter84_mutation_split_dates.py::test_iter85_quarterly_mutation_5_ods`
  (mutation 15/02/2026, 4 appels trimestriels -> 5 OD : FR + prorata Q1 + 3 futures Q2/Q3/Q4)
- `tests/test_iter84_post_mutation_regen.py` reecrit (4 tests) :
  - distribution_not_modified : owner_id reste vendeur dans la distribution
  - one_od_per_future_call_date : 1 OD par date d'appel futur avec DR/CR corrects
  - regenerate_is_neutralized : `regenerated_calls.fixed == 0`
  - cancel_deletes_future_ods : annulation supprime aussi les OD futures
- `tests/test_iter82_mutation_split.py` mis a jour : attendu 5 entries (FR + prorata + 3 futures)
- 30/30 tests passent (iter82-iter85 stack mutation).

**Fichiers** :
- `/app/backend/routes/properties.py` (bloc "3) Appels futurs" + neutralisation regen)
- `/app/backend/tests/test_iter84_mutation_split_dates.py` (+1 test trimestriel)
- `/app/backend/tests/test_iter84_post_mutation_regen.py` (reecrit pour nouvelle logique)
- `/app/backend/tests/test_iter82_mutation_split.py` (mise a jour assertions)

### Iter84 (Feb 2026) - Mutation lot decomposee + Apercu facture + Lignes multiples + Animation IA + CORS deployment fix

**Demande user** : (1) "lors d'une mutation de lot, separer explicitement le
transfert du fonds de roulement et les proratas des appels de provisions
futurs a prevoir selon la periodicite du budget" (2) "permettre d'avoir un
apercu de la facture lors de la redaction d'une facture, genre un oeil qui
permet de voir la facture" (3) deployment readiness check

**Implementation** :

1. **Decompte de mutation en 3 sections** (`routes/properties.py`) :
   - Refactor : nouvelle fonction `_compute_mutation_breakdown(lot, old_owner_id, sale_dt)`
     partagee entre `mutate_lot` et `mutate_lot_preview` (DRY).
   - Helper `_resolve_call_period(call, fy_by_id)` extrait pour calculer la
     periode couverte d'un appel (period_start/period_end stockes > deduction
     X/N + fy > fallback 90j).
   - 3 blocs distincts dans le payload :
     * **Fonds de roulement** : quote-part sur `lot_quotity / total_quotity`,
       JAMAIS au prorata temporel (capital permanent).
     * **Prorata appel en cours** (`current_period_prorata`) : portion APRES
       sale_date des appels chevauchant sale_dt, transferee vendeur->acquereur.
     * **Appels futurs** (`future_calls`, `future_calls_total`) : liste
       informationnelle des appels dont period_start > sale_dt (l'acquereur
       les paiera normalement apres reaffectation du lot). NON inclus dans l'OD.
   - Detection automatique de la frequence (`budget_frequency`,
     `budget_frequency_label`) depuis le pattern X/N des noms d'appels.
   - L'ecriture OD ne contient QUE `roulement_quota + current_period_prorata`
     (les futurs sont info-only).
   - Alias retrocompatibles : `prorata_provisions`, `prorata_details` (anciens
     noms preservés pour ne pas casser les historiques de mutations passees).

2. **Frontend mutation (`LotsPage.js`)** :
   - 3 blocs visuels distincts dans le dialogue :
     * Bloc 1 (vert) : "Transfert du fonds de roulement" avec quote-part.
     * Bloc 2 (bleu) : "Appels de provisions a prevoir" avec sous-bloc 2a
       (prorata appel courant) + 2b (table des appels futurs).
     * Bloc Synthese : recap de l'ecriture OD (roulement + prorata courant).
   - Historique des mutations : affiche aussi les appels futurs et
     budget_frequency_label si disponibles.

3. **Apercu facture pendant rédaction** (`pages/InvoicesPage.js`) :
   - Icone Eye (lucide-react) ajoutee a cote du bandeau "PDF a attacher"
     (PDF en attente d'attachement). Clic genere une URL blob locale et
     ouvre le viewer plein ecran (iframe).
   - Nouveau bloc "Pieces jointes (N)" en mode edition, avec une icone Eye
     par fichier qui ouvre le viewer (URL inline du backend).
   - Cleanup memoire : `URL.revokeObjectURL` appele a la fermeture du viewer
     pour eviter les fuites sur les blob URLs.
   - Bouton Telecharger du viewer adapte : `download=filename` pour les blobs,
     URL avec `disposition=attachment` pour les pieces jointes serveur.

4. **CORS deployment fix** (`server.py`) :
   - Bug : code lisait FRONTEND_URL mais ignorait CORS_ORIGINS env var.
   - Fix : `_build_cors_origins()` lit CORS_ORIGINS (comma-separated, ignore "*"),
     toujours fusionne FRONTEND_URL pour preview+prod. `allow_credentials=True`
     conserve pour l'auth cookie JWT.
   - Login + cookies verifies : 200 OK avec set-cookie access_token+refresh_token.

5. **Lignes multiples sur facture** (`routes/invoices.py` + `auto_entries.py` + `pages/InvoicesPage.js`) :
   - **Backend** :
     * Nouveau model `InvoiceLineInput` (account_number, expense_category_id,
       distribution_key_id, amount, description).
     * `InvoiceInput.lines: Optional[List[InvoiceLineInput]]` (None ou [] = mode legacy 1-ligne).
     * Helper `_resolve_invoice_lines()` valide somme == total_amount (0.01 tolerance),
       resolve account_number depuis expense_category, agrege distribution_lines
       par lot a travers toutes les cles utilisees.
     * `auto_entries.generate_purchase_entry` : si invoice.lines existe, genere
       UNE ecriture AC avec N debits (1 par ligne) + 1 credit fournisseur.
     * Refus de combinaison lines + is_private_fee (400).
   - **Frontend** :
     * Bouton "+ Splitter en plusieurs natures de depense" sous le bloc
       Nature/PCMN/Cle (mode 1-ligne par defaut).
     * Bloc bleu "LIGNES MULTIPLES (N)" avec table dynamique :
       Nature select / Compte PCMN / Cle / Description / Montant / Trash par ligne.
     * Validation visuelle : Somme vs Total, OK vert ou ecart rouge en temps reel.
     * "Revenir au mode 1 nature" pour retour rapide.
     * Bloc Nature/PCMN/Cle du dessus grise automatiquement en mode multi.
     * Validation cote front : refus si somme != total ou ligne sans compte avant POST.

6. **Animation de chargement IA** (`pages/InvoicesPage.js` + `index.css`) :
   - Bouton "Importer facture PDF (IA)" affiche `Loader2` avec `animate-spin` pendant extraction.
   - Banniere "Analyse de la facture par IA" en haut du dialogue avec :
     * Spinner Loader2 + Sparkles pulsing
     * Texte avec points animes (.) defilants
     * Progress bar indeterminee glissant horizontalement
   - Animations CSS dans `index.css` : `ai-progress-slide` + `ai-dot-bounce`.

7. **Extraction IA des lignes detaillees** (`routes/invoice_ai.py` + `pages/InvoicesPage.js`) :
   - **Backend** : prompt Claude enrichi avec champ `lines: []` retournant
     {description, amount, suggested_pcmn_account} par ligne detail/poste.
     Validation post-AI : verifie chaque suggested_pcmn_account existe en base,
     blank-le sinon. Verifie somme == total_amount (0.01 tolerance), reset
     les lignes en cas de mismatch.
   - **Frontend** : si l'IA retourne 2+ lignes, pre-remplit automatiquement
     `invForm.lines` en mode multi-lignes (le bloc "LIGNES MULTIPLES" s'active
     tout seul, le mode 1-ligne se grise). Bandeau aiHint affiche
     "N lignes detectees - mode multi-lignes pre-rempli".
   - **Test live** : PDF Finlead V-260701 (135 KB) extrait correctement
     2 lignes : Honoraires syndic 934.29 + Frais admin 135.00 = 1069.29 EUR.
     Le fournisseur Finlead srl est aussi reconnu via BCE (BE0728990830).

**Tests** :
- `tests/test_iter82_mutation_split.py` : 3/3 (mutation 3 sections).
- `tests/test_iter83_invoice_multi_lines.py` : 3/3 (create multi-line, total
  mismatch rejected, legacy single-line unchanged).
- Tous tests d'integration regression : 23/23 pass.

**Smoke test screenshots** :
- Dialogue facture avec PDF charge -> Eye + X visibles a droite du bandeau.
- Clic Eye -> viewer plein ecran ouvre avec iframe du PDF + bouton Telecharger.
- Mode multi-lignes -> bloc bleu "LIGNES MULTIPLES (1)" + bouton Ajouter ligne
  + indicateur somme vs total.

**Deployment check** : FAIL -> WARN. Seuls les warnings de query optimization
restent (preexistants, non-bloquants). CORS pass.



**Demande user** : (1) "il n'est pas autorise de creer de doublons aussi bien
proprietaires que fournisseurs, en cas de doublons il faut garder l'original
et l'utiliser, le check se fait sur nom et prenom, adresse email, nr de
telephone, NR BCE, adresse" (2) "supprimer tous les tests user en production"
(3) "Voulez-vous appliquer le meme verrouillage anti-fermeture aux autres
dialogs critiques (Bundle PDF, Fusion fournisseurs, Mutation lot) ? oui"

**Implementation** :

1. **Anti-doublon proprietaires** (`routes/properties.py::find_duplicate_owner`) :
   - 5 criteres testes (l'un suffit pour bloquer) :
     - Nom + prenom (mots tries alphabetiquement -> tolere ordre inverse)
     - Email (champ email ou email2, casse insensitive)
     - Telephone normalise (alphanumerique uppercase, tolere espaces/tirets/parentheses)
     - BCE normalise (alphanumerique uppercase)
     - Adresse complete normalisee (rue + code postal + ville, alphanumerique
       + minuscules, ponctuation ignoree)
   - Ajout du champ `bce_number` au model `OwnerInput` pour les proprietaires
     personnes morales (societes proprietaires de lots).
   - POST /api/owners : 409 si doublon detecte
   - PUT /api/owners/{id} : 409 si doublon (exclude_id pour permettre l'edition
     du proprietaire sans declenchement contre lui-meme)
   - import_wizard commit_owners : skip silencieux des doublons avec compteur
     `skipped_duplicates`. Cache local pour perf import en lot.
   - Scope ACP (chinese wall) : 2 ACPs peuvent avoir le meme owner.

2. **Cleanup test users** (`scripts/cleanup_test_users.py`) :
   - Detection des emails de test via patterns regex :
     `test_[a-f0-9]+@`, `test_iter\d+`, `@example\.com$`, `itr\d+`, `pytest_`
   - Whitelist defensive : admin@copro.be, gerald@gep.be,
     welcome@goodexperienceproperties.be, evrard.gerald@outlook.be
   - Cleanup associes : `user_sessions`, `password_reset_tokens`,
     `owners` (par email).
   - Idempotent. Mode `--dry-run` supporte.
   - **PREVIEW** : 4 test users supprimes (test_2b38e4fb, test_be9c662c,
     test_iter9_xxx, test_iter16_owner_xxx).
   - **PROD** : script disponible, a executer apres redeploiement via terminal
     Emergent (l'agent n'a pas acces production).

3. **Verrouillage dialogs critiques** :
   - `BundleImportDialog.js` (import factures groupees)
   - `LotsPage.js mutation-dialog` (vente / changement de proprietaire)
   - `BalanceTiersPage.js merge-suppliers-dialog` (fusion fournisseurs)
   - 3 props ajoutees a `<DialogContent>` :
     - `onPointerDownOutside={(e) => e.preventDefault()}`
     - `onInteractOutside={(e) => e.preventDefault()}`
     - `onEscapeKeyDown={(e) => e.preventDefault()}`
   - Fermeture possible UNIQUEMENT via le bouton X / Annuler / validation finale.

**Tests** : `test_iter81_owners_duplicate_and_cleanup.py` (7 tests, PASSED).
**Suite complete iter73-81** : 33/33 tests verts.

### Iter80 (Feb 2026) - Lettrage 1 transaction -> N factures

**Demande user** : "je dois pouvoir selectionner 2 factures permettant d'arriver
au montant du paiement, corrige ca une checkbox est une bonne approche".

**Implementation** :
- Backend : nouveau endpoint `POST /api/banking/lettrage-multi-invoices` qui
  accepte `transaction_id` + `invoice_ids[]`. Marque chaque facture comme paid
  avec un `lettrage_code` commun. La txn passe en `match_type='multi_invoice'`
  avec `matched_to_ids[]` (champ liste) + `matched_to` (singulier, retro-compat).
  Sur-paiement accepte (excedent reflete sur compte tiers).
- Frontend `BankingPage.js` : checkbox sur chaque carte de facture (uniquement
  pour les "A PAYER") dans le dialog "Lettrage de la transaction". Toolbar bleue
  qui apparait avec running total + indicateur SOLDE EXACT / partiel / sur-paiement.
  Bouton "Lettrer ces N factures" pour valider en lot.
- **Tests** : `test_iter80_lettrage_multi_invoices.py` (2 tests, PASSED).

### Iter79 (Feb 2026) - Fusion de fournisseurs (centralisation)

**Demande user** : "fusionne les comptes fournisseurs pour centraliser les
informations" (cas Finlead srl vs SRL Finlead - meme entreprise, libelles
inverses).

**Implementation** :
1. **Normalisation tolerante a l'ordre des mots** (`routes/suppliers.py::_norm_name`) :
   les mots sont tries alphabetiquement avant comparaison. Detecte desormais
   "Finlead srl" == "SRL Finlead" comme doublon.
2. **Endpoint `POST /api/suppliers/merge`** :
   - Input : `{keep_id, remove_ids: List[str]}`
   - Reassocie `invoices.supplier_id` + `bank_transactions.matched_to`
     (match_type='supplier_payment')
   - Enrichit le supplier conserve depuis les absorbes (premier non-vide gagne) :
     bce_number, vat_number, iban, bic, email, phone, address, postal_code,
     city, country, notes.
   - Supprime les remove_ids.
3. **UI** (`BalanceTiersPage.js`) :
   - Bouton "Fusionner des fournisseurs" sur la card "Total a payer".
   - Mode merge : checkbox par ligne fournisseur (uniquement ceux avec supplier_id).
   - Dialog : radio pour choisir le supplier "a conserver" (defaut = celui avec
     TVA, sinon le 1er). Aper├ºu des soldes. Confirme.

**Tests** : `test_iter79_suppliers_merge.py` (3 tests, PASSED).

### Iter78 (Feb 2026) - Check anti-doublon fournisseurs

**Demande user** : "pas de doublon de fournisseur autorise, check fait sur le
nr BCE, nom et compte bancaire".

**Implementation** :
- Helper `find_duplicate_supplier(db, *, name, bce_number, vat_number, iban,
  copro_id, exclude_id=None)` dans `routes/suppliers.py`. Recherche sur 3
  criteres (l'un suffit) : BCE/TVA normalises, nom normalise (tri alphabetique),
  IBAN normalise.
- Scope ACP : 2 ACPs peuvent avoir le meme fournisseur (chinese wall preserve).
- POST /api/suppliers : 409 si doublon detecte avec libelle "doublon detecte :
  un fournisseur avec le meme {nom|BCE|TVA|IBAN} existe deja".
- PUT /api/suppliers/{id} : meme check avec exclude_id (on peut editer sans
  declencher un doublon contre soi-meme).
- import_wizard (CSV + PDF) : skip silencieux des doublons (skipped_duplicates
  retourne dans la reponse, pas une erreur bloquante).

**Tests** : `test_iter78_suppliers_duplicate.py` (5 tests, PASSED).

### Iter77 (Feb 2026) - Lettrage en lot bancaire (N transactions -> 1 facture)

**Demande user** : "possibilite de lettrer plusieurs transactions dans les
extraits de compte" - choix : N txns bancaires vers 1 facture (paiements partiels).

**Implementation** :

1. **Backend** (`/app/backend/routes/banking.py`) :
   - Nouveau model `LettrageBatchInput(transaction_ids: List[str], match_to_id, match_type)`.
   - Nouveau endpoint `POST /api/banking/lettrage-batch` :
     - Valide : toutes les txns existent, aucune lettree a une AUTRE facture,
       toutes dans la meme ACP que la facture, somme <= total TVAC.
     - Marque les N txns avec `matched=True, match_type='invoice', matched_to=<id>,
       lettrage_code=<UUID8>` (code commun pour tracer le groupe).
     - Facture : `paid` si somme == TVAC (a 0.01 EUR pres), sinon `partially_paid`
       avec `amount_paid` + `paid_by_transaction_ids[]`.
   - `/unlettrage/{txn_id}` enrichi : recalcule le statut de la facture en fonction
     des txns restantes (paid -> partially_paid -> unpaid suivant somme).
   - Refuse les sur-paiements (total_txn > inv_amount + 0.01).

2. **Frontend** (`/app/frontend/src/pages/BankingPage.js`) :
   - Checkbox "select all" dans le header de la table + checkbox par ligne (uniquement
     pour les txns non lettrees).
   - Toolbar bleue qui apparait des qu'une txn est selectionnee : affiche
     "N transaction(s) selectionnee(s) - Total: X EUR" + bouton "Lettrer la
     selection vers une facture".
   - Dialog batch-lettrage : recherche par numero/fournisseur/description avec
     coloration :
       - Vert + badge "SOLDE EXACT" si selectedTotal == facture
       - Rouge + badge "SUR-PAIEMENT" si selectedTotal > facture (bouton disabled)
       - Ambre + "PARTIEL (X EUR restant)" sinon

**Tests de regression** : `test_iter77_lettrage_batch.py` (4 tests, PASSED) :
- 3x100 EUR -> facture 300 EUR -> paid + lettrage_code unique partage
- 2x100 EUR -> facture 300 EUR -> partially_paid + amount_paid=200, remaining=100
- 3x200 EUR -> facture 300 EUR -> HTTP 400 (sur-paiement refuse)
- Apres lettrage 3x100 (paid), unlettrage d'1 txn -> statut recalcule en partially_paid

### Iter76 (Feb 2026) - Prorata mutation sur appels de provisions

**Bug** : la mutation utilisait `[call.date, call.due_date]` (fenetre d'emission
~30j) au lieu de la periode COUVERTE (~3 mois pour trimestriel). Le prorata ne
s'appliquait que si la vente tombait DANS la fenetre d'emission, soit <10% des
cas reels.

**Fix** :
- Generation des appels : ajout de `period_start` / `period_end` (periode reelle
  couverte) lors de la creation depuis le budget. Pour Q1 fy 2026 :
  `[2026-01-01, 2026-03-31]` (vs `[2026-01-01, 2026-01-31]` precedemment).
- Mutate_lot : utilise `period_start/period_end` si presents, sinon deduit depuis
  "X/N" + fiscal_year (fallback intelligent), sinon `+90j` en dernier recours.
- Script migration `migrate_fund_calls_periods.py` : 13 fund_calls existants
  mis a jour. Idempotent.

**Tests** : `test_iter76_mutation_prorata.py` (6 tests, PASSED) - validations
unitaires de `_compute_period` + E2E test mutation Q1 vente au 15/02/2026 :
- Periode = 90j (01/01 - 31/03), jours restants = 45j
- Prorata = 600 EUR * (45/90) = 300.00 EUR (transfert vendeur -> acheteur)

### Iter75 (Feb 2026) - Budget PDF : cles speciales + exercice fiscal non-calendar

**Bugs corriges** :
1. **Crash IndexError** sur budgets a exercice fiscal non-calendrier
   (01/10/2025 - 30/09/2026) : 4 year-tokens dans le header + clusters fragments
   de milliers ("18" + "800,00") creaient >3 colonnes, depassant les keys
   ("realise_n1", "budget_n", "en_cours").
2. **Cles speciales non reconnues** : section "0002 - Cle Speciales ascenseurs"
   etait creee comme cle generique (is_special=False).

**Fix** (`/app/backend/import_wizard/pdf_utils.py` + `routes/import_wizard.py`) :
- Cluster merge intelligent : les clusters "fragment" (entiers 1-3 chiffres sans
  virgule) sont mergees avec leur voisin de droite (gap < 35px).
- Defensive : truncate `amt_col_centers` a `len(keys)` pour eviter l'IndexError.
- Detection regex "Cle Speciale" / "speciale" sur le libelle de section ->
  `section.is_special=True`. Auto-creation de la distribution_key avec ce flag.
- UI BudgetPreview : badge violet "SPECIALE" sur les sections speciales.

**Tests** : `test_iter75_budget_special_keys.py` (1 test, PASSED).

### Iter74 (Feb 2026) - Natures de depense par defaut auto-seedees

**Demande user** : "ces natures de depenses sont a creer pour toutes nouvelles ACP,
ce sont des natures par defaut" (PDF "LISTE DES NATURES DE DEPENSE PAR DEFAUT"
fourni par SRL FINLEAD PROPERTIES contenant 23 entrees).

**Implementation** :

1. **Liste centrale** (`/app/backend/default_expense_natures.py`) : 23 natures
   couvrant tous les cas usuels d'une copro belge avec syndic professionnel :
   - Honoraires syndic + frais admin (50/50)
   - Assurances (incendie, RC, defense en justice) - 100% proprietaire
   - Ascenseurs (contrat, controle, reparations)
   - Fluides : eau, electricite, gaz - 100% occupant
   - Incendie (alerte, contrats, extincteurs, prevention)
   - Entretien jardins + nettoyage batiment
   - Travaux, salles, frais bancaires, honoraires experts
   - 1 produit : Interets crediteurs (compte 750, kind="produit")
   - Codes TVA belges : A1 (21%), A2 (6%), A4 (0%/exonere)

2. **Auto-seed a la creation d'ACP** (`/app/backend/routes/coproprietes.py`) :
   - `_seed_default_expense_natures(copro_id)` appele dans `create_copropriete`
     apres `_seed_pcmn_for_acp`. Skip silencieux si une nature existe deja
     pour le meme `account_number` (respect 1:1).
   - Verification E2E : nouvelle ACP -> 23 natures auto-creees (22 charges + 1 produit).

3. **Migration des ACPs existantes** (`/app/backend/scripts/seed_default_expense_natures_existing_acps.py`) :
   - Idempotent (`--dry-run` supporte). Skip les comptes deja attribues a une autre nature.
   - Resultat : 101 natures inserees sur 7 ACPs existantes (60 skip car deja en place).

4. **Support classe 7 (Produits) dans expense_categories** (`/app/backend/routes/expense_categories.py`) :
   - Validation `class_num` elargie : accepte classe 6 (Charges) ET classe 7 (Produits).
   - Champ `kind` auto-derive depuis le PCMN si non fourni : 7 -> "produit", 6 -> "charge".
   - Nouveaux champs `ExpenseCategoryInput` : `code`, `vat_code`, `kind`.

5. **UI NaturesPage** (`/app/frontend/src/pages/ExpenseCategoriesPage.js`) :
   - Charge desormais les comptes des classes 6 ET 7 (deux appels paralleles fusionnes).
   - Nouvelles colonnes table : `Code`, `TVA`, `Type` (badge "Charge" gris / "Produit" emeraude).
   - Dialog edition enrichi avec champs `code` et `vat_code` au-dessus du nom.

**Tests de regression** :
- `/app/backend/tests/test_iter74_default_expense_natures_seed.py` (2 tests, PASSED)

**Fichiers** :
- `/app/backend/default_expense_natures.py` (NEW - 23 natures)
- `/app/backend/routes/coproprietes.py` (helper + invocation)
- `/app/backend/routes/expense_categories.py` (support classe 7 + nouveaux champs)
- `/app/backend/scripts/seed_default_expense_natures_existing_acps.py` (NEW - migration)
- `/app/frontend/src/pages/ExpenseCategoriesPage.js` (table + dialog enrichis)

### Iter73 (Feb 2026) - Bilan equilibre apres repartition + Rappels PDF (periode + no overflow)

**Bugs corriges** :

1. **P0 - Bilan "Apres repartition" desequilibre** (`/app/backend/routes/reports.py`)
   - Symptome : sur ACP Gaura, `view_mode=after_distribution` retournait
     `ecart=23029.82` (Actif != Passif).
   - Root cause : la distribution des comptes 49X (regul.) sur les proprietaires
     inversait les cotes :
       - Regul ACTIF (D-C > 0) etait ajoute en CREDIT owner (-> PASSIF)
       - Regul PASSIF (D-C < 0) etait ajoute en DEBIT owner (-> ACTIF)
     Resultat : chaque montant creait un ecart de 2*amount au lieu de zero.
   - Fix : la NATURE du compte 49X est preservee sur le compte proprietaire.
     C'est juste un changement de rubrique de presentation, pas une re-affectation
     comptable.
       - 49X ACTIF  -> owner DEBIT  (reste cote ACTIF, rubrique V.A)
       - 49X PASSIF -> owner CREDIT (reste cote PASSIF, rubrique VI.A)
   - Ajout : ajustement d'arrondi sur le dernier owner pour neutraliser les
     ecarts cumules dus a `round(amount * ratio, 2)`.
   - Result E2E : Gaura `view_mode=after_distribution` -> actif=93821.58,
     passif=93821.58, equilibre=True, ecart=0.0.

2. **P1 - Rappels PDF : superposition de texte + date de periode manquante**
   (`/app/backend/routes/exports.py::generate_reminder_letter`)
   - Symptome : la cellule "Appel" debordait sur les colonnes voisines quand
     le libelle etait long (ex. "Trimestriel 1/4 - Exercice 2026"). Pas de
     date de periode pour identifier l'appel concerne.
   - Fix : chaque cellule de la Table ReportLab est wrappee dans un `Paragraph`
     pour permettre le word-wrap. Nouvelle colonne "Periode" entre "Appel" et
     "Echeance" affichant la date d'appel. Dates au format DD/MM/YYYY (vs
     YYYY-MM-DD ISO precedent). Items tries par date de periode pour
     chronologie lisible.
   - Result E2E : PDF de rappel pour ACP Test/Trimestriels 1-4 2026 confirme :
     pas de chevauchement de texte, colonne Periode bien presente avec dates
     01/01/2026, 01/04/2026, 01/07/2026, 01/10/2026.

**Tests de regression** :
- `/app/backend/tests/test_iter73_bilan_apres_repartition_49x.py` (1 test)
- `/app/backend/tests/test_iter73_reminders_pdf_layout.py` (1 test)

**Fichiers modifies** :
- `/app/backend/routes/reports.py` (lignes 435-505 : repartition 49X)
- `/app/backend/routes/exports.py` (lignes 345-460 : generate_reminder_letter)

### Iter72quatuordecies (Feb 2026) - Bundle PDF Invoices + Viewer + Cleanup Gaura

**Tasks completed in this iteration**:

1. **Cleanup Gaura ACP (script `gaura_remediation_execute.py`)**:
   - Phase A: deleted 3 test owners (ALEXIS Jean-Pierre, GRAMME GILLES, Wauthier-Catinus) + cascade (88 JE FI, 6 PCMN accounts)
   - Phase A: merged 5 remaining CM/Optipro duplicate owners (BERNARD, Heremans, Ferdinande, Leyder, Dubuisson) - 28 JE lines migrated
   - Phase B: merged 9 SPRL Marougrav records (2024/525, 13.75-13.76 EUR each) into 1 record (123.78 EUR)
   - Phase B: merged 4 SRL Finlead pairs (each had 2 records: principal 1269.06/1142.16 + admin fee 165) into 1 record (1434.06 or 1307.16)
   - Result: 75 -> **63 invoices** matching official Optipro "Factures fournisseurs" PDF list

2. **Bundle PDF Invoice Import**:
   - Backend `/app/backend/import_wizard/pdf_invoices_bundle.py`: Smart segmentation using per-page signatures (BCE/TVA, invoice number, date) to split concatenated invoices. Detection robustness: 14 -> 33 invoice blocks on Gaura "Regroupement.pdf" (87 pages). Match cascade: exact n° -> supplier+amount -> supplier+date -> amount-only fallback. 21 strong auto-matches (>=85% confidence).
   - Parser fixes:
     - Belgian thousand separator "." vs space: allow space but skip qty x price tax-breakdown table rows
     - Date sanity: reject month > 12 or year < 2000 or year > 2035
     - Amount sanity: reject > 100,000 EUR (avoid BCE/TVA numbers as amounts)
     - Invoice number match amount cross-check: downgrade confidence to 0.55 if amount differs > 5%
   - Backend `/api/invoices/bundle-analyze` + `/api/invoices/bundle-commit`: 2-step flow (analyze + review + commit)
   - Frontend `/app/frontend/src/components/BundleImportDialog.js`: Full UI with table review, per-block actions (attach/create/skip), auto-mode based on confidence. `[deja PJ]` warning when target invoice already has attachment (safety against duplicates)
   - Bundle commit: extracts pages from session PDF (saved in `/app/uploads/invoice_attachments/_bundles/<session_id>/`), creates 1 attachment per block with `source=bundle`

3. **PDF List Parser (compare/diagnostic tool)**:
   - `/app/backend/import_wizard/pdf_supplier_invoice_list.py` parses the official Optipro "Factures fournisseurs" tabular PDF (63 invoices in 5 pages with HT/TVAC allocations). Used for DB reconciliation diagnostics.

4. **Attachment Viewer (PDF/Image)**:
   - Backend: `/api/invoices/{id}/attachments/{att_id}/download?disposition=inline` returns `Content-Disposition: inline` for browser native viewer rendering
   - Default `disposition=attachment` (legacy) forces download
   - Frontend (`InvoicesPage.js`): Attachment filename is now a clickable link (blue, hover underline). Click opens a wide modal (`max-w-6xl h-[92vh]`) with iframe pointing to inline URL. Download button still available alongside.
   - Verified via curl: HTTP 200, Content-Type application/pdf, Content-Disposition inline, %PDF-1.5 magic bytes

**Files changed**:
- `/app/backend/routes/invoices.py`: added `bundle-analyze`, `bundle-commit` endpoints, inline disposition support
- `/app/backend/import_wizard/pdf_invoices_bundle.py`: smart segmentation + parser fixes
- `/app/backend/import_wizard/pdf_supplier_invoice_list.py`: new (official list parser)
- `/app/backend/scripts/merge_cm_optipro_owners.py`: enhanced with stale account cleanup
- `/app/backend/scripts/gaura_full_remediation.py`: dry-run script
- `/app/backend/scripts/gaura_remediation_execute.py`: execute script for Phase A + B
- `/app/frontend/src/components/BundleImportDialog.js`: new
- `/app/frontend/src/pages/InvoicesPage.js`: bundle button + viewer modal

**Result E2E**:
- ✅ Backend curl tests pass for bundle-analyze and bundle-commit
- ✅ Backend curl test for inline disposition header
- ✅ Frontend UI demonstrated via screenshot tool (bundle dialog opens correctly)
- ⚠️ E2E viewer test in test environment blocked by FY 2026 default filter (data is in FY 2025) - viewer works manually when user selects FY 2025


### Iter72undecies (Feb 2026) - Wizard OD year-end : pre-flight format-aware

**Bug rapporte** (video) : a l'etape 9/9 OD year-end avec le PDF Journal OD charge, le clic "Terminer le wizard" affichait `15 entrees sans contrepartie. Definissez le compte de contrepartie pour chaque ligne avant validation.` empechant la fin du wizard.

**Root cause** : la pre-flight `handleCommit` du frontend appliquait la regle de l'ancien format (`expense_list`) a TOUS les formats. Pour les entrees `od_journal` (lignes deja explicites, pas de champ `counterpart_account`), le filtre `!e.counterpart_account` retournait toujours `true` -> blocage 100% des entrees.

**Fix** (`/app/frontend/src/pages/ImportWizardPage.js::handleCommit`) :
- Detection du format via `odEntriesParsed.format === 'od_journal'`
- Format `od_journal` : verifie qu'au moins UNE entree est cochee (`included`)
- Format `expense_list` : conserve la verification de contrepartie

**Result E2E** :
- ✅ Lint frontend OK
- ✅ Format `od_journal` : le bouton "Terminer le wizard" valide direct (pas de blocage)
- ✅ Format `expense_list` : comportement legacy preserve

### Iter72decies (Feb 2026) - Wizard : navigation arriere sans consequence

**Bug rapporte** : "lors de la creation dans le wizard il doit etre possible de retourner en arriere sans consequence". L'utilisateur revenait a l'etape 3/9 (Exercice fiscal), modifiait quelques champs, cliquait "Valider et continuer" et obtenait `400 L'exercice '2025' existe deja dans cette ACP`.

**Root cause** : chaque endpoint `commit-XXX` du wizard creait des records DB. Retour en arriere + re-validation = doublon -> rejet backend. La conception ne supportait pas un workflow back-and-forth.

**Fix backend** (`commit-fiscal-year`) :
- Idempotence ciblee par session : si un fiscal year avec le meme nom existe deja pour cette ACP et qu'il a ete cree par CETTE session (`import_session_id`), on update les dates/status et on retourne `{id, name, idempotent: true}` au lieu de lever 400.
- Une vraie collision avec une AUTRE session leve toujours 400 (protege contre l'ecrasement involontaire).

**Fix frontend** (`ImportWizardPage.js` footer) :
- Detection auto : `stepAlreadyDone = !!(session.steps[step.key].count > 0 || inserted > 0 || fiscal_year_id)`
- Si l'etape est deja validee ET aucun nouveau fichier upload :
  - Affiche un bouton **vert** "Etape deja validee - Continuer ->" qui passe a l'etape suivante SANS commit
  - L'utilisateur peut cliquer "Annuler ce fichier" pour repartir et re-valider fresh
- Sinon, comportement normal (Valider et continuer)
- Le bouton "Etape precedente" reinit aussi l'etat `odEntriesParsed` (oubli precedent qui fuyait entre etapes)

**Result E2E (verifie via API)** :
- ✅ 1er commit fiscal-year : `{id: "1ecfe6cd", name: "2025"}`
- ✅ 2eme commit (back+forward) : `{id: "1ecfe6cd", name: "2025", idempotent: true}` SAME ID
- ✅ Le bouton frontend devient vert "Etape deja validee" quand on revient sur une etape complete
- ✅ Lint frontend OK

**Fichiers** :
- `/app/backend/routes/import_wizard.py::commit_fiscal_year` (idempotence par session)
- `/app/frontend/src/pages/ImportWizardPage.js` (logique conditionnelle du bouton primary)

### Iter72nonies (Feb 2026) - Support du PDF "Journal OD" Optipro (format optimal) + correctif crash UI

**Probleme** : a l'upload du PDF "Journal comptable OD" (le bon document Optipro), la page crashait avec
`TypeError: Cannot read properties of undefined (reading 'length')` au niveau de `OdEntriesPreview` parce
que les entrees du format `od_journal` n'ont pas les champs `libelle/amount/suggested_counterpart`.

**Decouverte** : le PDF "Journal comptable OD" est BIEN MEILLEUR que la "Liste des depenses" car :
- Chaque ecriture est deja **equilibree avec ses lignes explicites** (compte, debit, credit)
- Les **codes auxiliaires** (C1996, F0145) sont directement dans le PDF
- Aucune saisie manuelle de contrepartie requise
- L'utilisateur n'a qu'a inclure/exclure les ecritures de cloture annuelle

**Implementation** :

1. **Backend - Auto-detection des 2 formats** (`pdf_utils.py::parse_od_entries_pdf`) :
   - Detecte "JOURNAL COMPTABLE : OPERATIONS DIVERSES" dans le premier texte
   - Si oui -> `_parse_od_journal_pdf` (35 entrees, lignes explicites)
   - Sinon -> `_parse_od_expense_list_pdf` (33 entrees, suggested_counterpart)
   - Retour unifie avec `format: "od_journal" | "expense_list"`

2. **Backend - Parser Journal OD** :
   - Regex header : `DD/MM/YYYY - NNNNNN - description TOTAL_D TOTAL_C`
   - Regex line : `ACCOUNT - LIBELLE [| AUX_INFO] OD DD/MM/YYYY DD/MM/YYYY DEBIT CREDIT`
   - Split aux_info pour separer `account_name` et `auxiliary_info`
   - **Exclusion auto** des "Cloture - SOMETHING" et "Solde des comptes" (vrais closings)
   - **NON-exclusion** des "Sinistre X - cloture" (fausses-positives evitees via prefix check)
   - Validation balance par entree

3. **Backend - Endpoint `commit-od-entries` dual-mode** :
   - Detecte le format via presence de `lines` dans la 1re entree
   - Format A (expense_list) : controle counterpart obligatoire, JE 2 lignes auto-construite
   - Format B (journal_od) : utilise les lignes du PDF telles quelles, controle equilibre
   - Auxiliary lookup : C1996 -> owner_id canonique, F0145 -> supplier_id canonique via `auxiliary_code`
   - Idempotence via `optipro_reference` (Journal OD) ou `source_y/page` (expense list)

4. **Frontend - Composant `OdEntriesPreview` refactor** :
   - Dispatch sur `odData.format` :
     - `od_journal` -> `OdJournalPreview` : tableau avec checkbox include/exclude, lignes detaillees indentees
     - `expense_list` -> `OdExpenseListPreview` : ancien tableau avec dropdown contrepartie
   - Empeche le crash en utilisant le bon composant pour chaque format

**Result E2E (verifie sur Gaura via API)** :
- ✅ PDF "Journal OD" parse : 35 entrees, format auto-detecte
- ✅ 33 incluses par defaut + 2 exclues (000466, 000467 cloture)
- ✅ Sinistre closures NON-exclues (000187, 000428 correctement gardes)
- ✅ Codes auxiliaires C1996/F0145 lies aux owner/supplier_id canoniques
- ✅ Commit Journal OD : 33 inserted, 2 skipped (closing), 0 errors
- ✅ **API expense list = 33,828.43 EUR = PDF Optipro 33,828.43 EUR (diff +0.00)**
- ✅ Idempotence parfaite via `optipro_reference`
- ✅ Backwards-compat : "Liste des depenses" toujours supporte (4 tests legacy PASSED)

**Regression tests** : `/app/backend/tests/test_iter72_journal_od_wizard.py` (5 tests, PASSED)

**Fichiers** :
- `/app/backend/import_wizard/pdf_utils.py` (auto-detect + parser journal_od)
- `/app/backend/routes/import_wizard.py` (commit dual-mode)
- `/app/frontend/src/pages/ImportWizardPage.js` (OdEntriesPreview dispatcher, Fragment import)
- `/app/backend/tests/test_iter72_journal_od_wizard.py` (5 tests)

### Iter72octies (Feb 2026) - Step "OD year-end" dans le wizard d'import

**Demande utilisateur** : "Ajouter un step OD year-end dans le wizard d'import (pour eviter de devoir scripter chaque ACP)"

**Choix utilisateur** :
- Format : upload du PDF "Liste des depenses" Optipro, parse uniquement les lignes avec N° piece = "-"
- Contrepartie : auto-detection par mot-cle + correction manuelle ligne par ligne (mix)
- Inconnue : bloquer la validation tant que toutes les contreparties ne sont pas definies

**Implementation backend** :
- `/app/backend/import_wizard/pdf_utils.py` :
  - `parse_od_entries_pdf(raw)` : parse les lignes OD (Ref. interne = "-"), skip compte 650 (FI), extrait date/libelle/account/amount/proprietaire_pct/occupant_pct par colonnes x-position
  - `_suggest_od_counterpart(libelle, charge_acc, amount)` : auto-detection par mot-cle (FAR -> 444, "charges a reporter" -> 490, "Nettoyage de bilan/AGS" -> 417, "SIN INONDATION" -> 494001, "Sinistre pompe/garage" -> 499603, "Imputation copro" -> 410, fallback 4990) avec normalization d'accents pour matcher "à reporter"
  - Special : compte 643 (Frais privatifs) -> 410 forcement
  - Retourne `confidence: high/medium/low/none`
- `/app/backend/routes/import_wizard.py` :
  - `POST /api/import-wizard/sessions/{id}/sniff-pdf` accepte `kind=od_entries`
  - Nouveau endpoint `POST /api/import-wizard/sessions/{id}/commit-od-entries` :
    - Bloque si une entry a `counterpart_account` vide (HTTPException 400 avec liste des lignes incompletes)
    - Auto-cree les comptes PCMN manquants
    - Cree une JE balancee par entree (DEBIT charge / CREDIT counterpart si amount > 0 ; inverse si < 0)
    - Idempotence : signature `(date, libelle, charge_acc, amount, source_y, source_page)` -> re-import du meme PDF skip 33/33

**Implementation frontend** (`/app/frontend/src/pages/ImportWizardPage.js`) :
- Nouveau step `od_entries` (icon ClipboardList) en position 11/11, optional
- `OdEntriesPreview` component : tableau avec colonnes Date/Libelle/Compte/Montant/Contrepartie (dropdown)/Conf (badge OK/~/?/!)/X
- Pre-fill des contreparties high-confidence ; les low/none arrivent vides (fond rouge)
- Header summary : total positif/negatif/net, count, periode
- Bouton commit bloque cote front si `missing > 0` (avec toast.error explicite)
- Liste de comptes PCMN belge standard pre-remplie + support compte custom (fallback 4990)

**Result E2E (verifie via API sur Gaura)** :
- 33 OD entries detectees du PDF Gaura : 31 high confidence + 2 low (sinistres complexes)
- Commit sans contrepartie -> HTTP 400 bloquant
- Commit complet : 33 inserted, 0 skipped, 0 errors
- **API expense total : 33,828.43 EUR** = PDF total **33,828.43 EUR** (diff +0.00)
- Re-import du meme PDF : 0 inserted, 33 skipped (idempotence parfaite)

**Regression tests** : `/app/backend/tests/test_iter72_od_entries_wizard.py` (4 tests, PASSED)
- Filtrage 650 OK
- Auto-detection accent-insensitive (à reporter)
- 643 -> 410 force
- Periode et entries count corrects

**Fichiers** :
- `/app/backend/import_wizard/pdf_utils.py` (parser + helper auto-detect)
- `/app/backend/routes/import_wizard.py` (sniff + commit endpoints)
- `/app/frontend/src/pages/ImportWizardPage.js` (step + preview component)
- `/app/backend/tests/test_iter72_od_entries_wizard.py` (regression)

### Iter72septies (Feb 2026) - Liste des depenses : dedup triplication + import OD year-end

**Bugs rapportes** : "Corrige la liste depenses alors elle n'est pas bonne"
- ACP Gaura : total API 33658.05 EUR vs PDF Optipro 33828.43 EUR (diff -170 EUR)
- Compte 650 (Frais bancaires) montrait 894.96 EUR au lieu de 298.32 EUR (x3)

**Root cause 1 - Triplication FI/OD/bank** :
L'utilisateur avait clique 3 fois sur "commit-journals" pendant l'import. Chaque
clic re-creait toutes les ecritures FI + bank_transactions + bank_statements :
- 618 FI JE (au lieu de 206 unique)
- 618 bank_transactions (au lieu de 197 unique)
- 39 bank_statements (au lieu de 13 unique)

**Root cause 2 - Entrees OD manquantes** :
Le wizard ne gere que AC (achats) et FI (banque). Les ajustements de fin
d'annee Optipro (charges a reporter, FAR, AGS write-offs, sinistres) ne sont
PAS importes. Pour Gaura, 10 entrees OD totalisant +595.97 EUR (net) etaient
absentes.

**Fix 1** (`/app/backend/scripts/dedup_journal_entries.py` - permanent) :
- Signature de dedup : `(date, reference, journal_type, sorted lines amounts)`
- Garde le plus ancien par `created_at`, supprime les copies
- Couvre AC/FI/OD + bank_transactions + bank_statements (signatures distinctes)
- **Result Gaura** : 412 JE supprimees + 417 bank_txns + 26 bank_statements

**Fix 2** (`/app/backend/scripts/add_gaura_year_end_od.py` - one-shot Gaura) :
Cree 10 ecritures OD year-end avec contrepartie correcte :
1. Annulation charges a reporter (Ascenseurs) +4141.40
2. Charge a reporter ascenseurs 2026 -4234.26
3. FAR ENGIE 10-12/2025 +512.00
4. Nettoyage de bilan (AGS point 8) +7342.97
5. Sinistre pompe de relevage - cloture -3183.65
6. Rupture devidoir - remboursement partiel -5813.63
7. Sinistre inondation pompe de relevage - cloture -562.67
8. Sinistre rupture canalisation (2022) +4018.14
9. Regularisation SIN 202200724 INONDATION -1298.25
10. Imputation coproprietaire frais privatifs (643->410) -155.03

**Result E2E (verifie par API)** :
- ✅ **API total : 33,828.43 EUR** = PDF total **33,828.43 EUR** (diff +0.00)
- ✅ **22/22 comptes match exactement** (61011, 61066, 61214, 66, 650, 643, etc.)
- ✅ 109 lignes de depenses dans la vue (vs 147 avant dedup)
- ✅ Cle "Charges communes" : 43/43 lots matched, total 10000.00

**Note** : Les OD year-end utilisent des contreparties par defaut (499603,
494001, 4990 Provisions sinistres, 410). L'utilisateur peut les re-categoriser
dans la page Comptabilite si son syndic utilise d'autres comptes.

**Fichiers** :
- `/app/backend/scripts/dedup_journal_entries.py` (etendu avec bank dedup)
- `/app/backend/scripts/add_gaura_year_end_od.py` (nouveau)


### Iter72sexies (Feb 2026) - Distribution keys schema unifie + lot matching + invoice distribution_lines recompute

**Bugs rapportes** :
1. "La creation de la cle de repartition ne s'est pas faite" : sur ACP Gaura,
   le dialog "Modifier la cle" affichait Total quotite = 0.00 alors que la cle
   "Charges communes" existait avec 63 factures liees.
2. "La liste de depenses ne correspond pas" : repartition par proprietaire
   non calculee sur les factures importees via le wizard.

**Root cause** :
Le wizard `commit-distribution-keys` ecrivait les cles avec un schema different
de celui attendu par l'API publique :
- Wizard : `lines: [{lot_id, lot_label_raw, lot_code_raw, owner_label_raw, quotity}]` + `type: 'tantiemes'`
- API publique : `lots: [{lot_id, lot_number, share}]` + `key_type: 'quotity'`

En plus, le matching des lots echouait :
- `lot_code_raw` = "-" (juste un tiret du PDF Optipro)
- `lot_label_raw` = "B0-1 - APPARTEMENT" -> ne matche pas le `lot.number = "B0-1"`

Resultat :
- Frontend dialog : `keyForm.lots` vide -> Total quotite = 0.00
- POST /api/invoices : `key.lots = []` -> `distribution_lines = []` -> pas de repartition par proprietaire
- API GET /api/fiscal/expenses : totals OK mais champ `total_amount` par invoice manquait de detail proprietaire

**Fix backend** (`/app/backend/routes/import_wizard.py::commit_distribution_keys`) :
1. Ajout helper `_extract_lot_number()` qui strip " - SUFFIX" :
   `"B0-1 - APPARTEMENT"` -> `"b0-1"`, `"Cave 1 - CAVE"` -> `"cave 1"`.
2. Tentative de matching en cascade : `lot_code` extrait, `lot_label` extrait,
   `lot_code` brut, `lot_label` brut. Skip si valeur `"-"` ou vide.
3. Ecrit DOUBLE schema (`lots` ET `lines` en alias) pour compat ascendante.
4. Stocke `key_type = 'quotity'` (vs `type = 'tantiemes'` en plus).

**Scripts permanents `/app/backend/scripts/`** :
- `migrate_distribution_keys_schema.py` : migre toutes les cles existantes
  (`lines` -> `lots`) avec rematching des lot_ids via numero extrait.
- `recompute_invoice_distribution_lines.py` : re-calcule
  `invoices.distribution_lines` apres migration des cles (regroupe les
  factures sans distribution par owner_id+amount).

**Result E2E (verifie par API)** :
- Cle "Charges communes" Gaura : 43/43 lots matched, total 10000.00 ✓
- 90 factures reparties (63 Gaura + 27 autres ACPs) avec distribution_lines
  computees (lot_id + lot_number + owner_name + share + amount)
- API `/api/distribution-keys?copropriete_id=Gaura` retourne lots = 43 matched
- Frontend "Modifier la cle" affichera Total quotite = 10000.00 (non plus 0.00)

**Regression tests** : `/app/backend/tests/test_iter72_distribution_keys_schema.py` (2 tests, PASSED)

**Fichiers** :
- `/app/backend/routes/import_wizard.py` (commit_distribution_keys reecrit)
- `/app/backend/scripts/migrate_distribution_keys_schema.py` (nouveau)
- `/app/backend/scripts/recompute_invoice_distribution_lines.py` (nouveau)
- `/app/backend/tests/test_iter72_distribution_keys_schema.py` (nouveau)

### Iter72quinquies (Feb 2026) - PDF Bilan parser : reconnaissance des comptes PCMN 2 chiffres

**Bug rapporte** : a l'import d'un Bilan comptable Optipro au 31/12/2024, l'etape 8/8 (OD d'ouverture) affichait un desequilibre de 25.46 EUR :
- Total Actif : 23 981.64
- Total Passif : 23 956.18 (manque 25.46)
- Bilan correct dans Optipro.

**Root cause** : le regex d'ancres du parser `parse_balance_pdf` filtrait les
comptes a 3-7 chiffres (`^\d{3,7}$`). Le compte PCMN `14 - Résultat exercice`
(2 chiffres) etait donc ignore. Les bilans belges PCMN incluent regulierement
des comptes de classe 1 a 2 chiffres : `10 Capital`, `13 Reserves`,
`14 Résultat reporte`, `15 Subsides en capital`, `16 Provisions`.

**Fix** (`/app/backend/import_wizard/pdf_utils.py::parse_balance_pdf`) :
- Regex d'ancres : `^\d{3,7}$` -> `^\d{2,7}$`
- Anti-faux-positif : un compte a 2 chiffres ne peut JAMAIS etre un
  sous-compte (`is_sub`). Ca evite que des fragments d'amounts comme `10`
  (issus de `10 262,39`) soient pris pour des ancres.

**Result E2E (verifie par API)** :
- Bilan 31/12/2024 (FINLEAD PROPERTIES) : Total Actif 23 981.64 = Total Passif 23 981.64 (equilibre)
- 10 entrees Actif + 23 entrees Passif (dont `14 - Résultat exercice : 25,46`)
- Regression test : `/app/backend/tests/test_iter72_bilan_2digit.py` (2 tests)

**Fichiers** :
- `/app/backend/import_wizard/pdf_utils.py` (regex + filtre is_sub)
- `/app/backend/tests/test_iter72_bilan_2digit.py` (nouveau)

### Iter72quater (Feb 2026) - Restauration des refs owners orphelines apres dedup trop aggressif + Restoration ACP TER

**Bug rapporte (escalation)** : meme apres le fix de l'ACP "import", l'utilisateur
exporte la Balance des tiers de l'ACP **TER** en Excel et constate :
- 60 proprietaires liste avec colonnes vides (Total appele, Total paye)
- Les 4 proprietaires Optipro (LENOTRE-MANSART, RUBENS-RENOIR, VELASQUEZ-GOYA,
  RAPHAEL-MICHEL ANGE) sont TOUJOURS ABSENTS de TER.

**Root cause (decouverte critique)** : le script `dedup_owners.py` (iter72ter)
avait supprime les copies ACP-specifiques de chaque owner. Le PDF Optipro avait
ete importe 3 fois (dans les ACPs "import", "BIS" et "TER"), creant 3 instances
de chaque owner (UUID different mais meme aux_code). Le dedup en a garde UN seul
(celui de "import") et a supprime les 2 autres -> ACPs BIS et TER se sont
retrouvees avec des `lots.owner_id` et `journal_entries.lines.third_party_id`
pointant vers des owner_id supprimes (orphelins).

**Symptome** : 12 orphan tpids dans les JE de TER (4 owners + 4 suppliers pour
le AN d'ouverture) + 4 orphan lots (les 4 appartements Optipro). Le endpoint
`list_owners` ne resoud les owners que via `lots.distinct("owner_id")` -> avec
des owner_ids invalides, seuls les 60 vrais proprietaires de TER apparaissaient,
les 4 Optipro etaient invisibles.

**Fix data (script permanent `restore_orphan_owner_refs.py`)** :
1. Pour chaque ACP, scan des `lots` et `journal_entries.lines` pour detecter les
   `owner_id` / `third_party_id` orphelins (ID inexistant en DB).
2. Mapping orphelin -> canonical via :
   - **Lots** : `lot.number` (ex. "Lots Le Nôtre-Mansart" -> aux C0960 canonical)
   - **JE lines** : `account_number` (ex. `4100960` -> owner C0960, `4400471` -> supplier F0471)
3. Update des references en place :
   - `lots.owner_id` + `owner_ids` -> canonical id
   - `JE.lines.third_party_id` + `third_party_type` -> canonical id
4. Pour chaque ACP, ensure `owner.copropriete_ids` contient l'ACP +
   `tier_accounts[ACP].provisions` est configure (derive de l'aux_code :
   `C0960` -> `4100960`).

**Result E2E (apres restore)** :
- **ACP "import"** : 4 owners (Optipro), Total deb 3682.05 / cred 5940.52 EUR ✅
- **ACP "TER"** : 64 owners (60 existants + 4 Optipro relinkees), Total deb 3682.05 /
  cred 5940.52 EUR + suppliers 9 unique, Total a payer 10843.07 EUR ✅
- **ACP "BIS"** : 0 owners (vide, AN sans tpid -> normal) ✅
- 4 lots relinkees + 8 JE lines relinkees + 69 copropriete_ids additions + 13 tier_accounts configures

**Lecons apprises (regle critique pour futurs dedups)** :
- **NE JAMAIS dedup-er les owners par aux_code GLOBAL** sans verifier que les
  copies ne sont pas ACP-specifiques (chaque ACP doit avoir SA copie d'un owner
  Optipro si l'import a ete fait dans chaque ACP separement).
- Alternative correcte : dedup PAR ACP (grouper par `auxiliary_code` ET
  `tier_accounts[ACP_id]` existant) pour ne jamais effacer une copie utilisee
  par une autre ACP.
- Le `restore_orphan_owner_refs.py` est utilisable a la demande si une telle
  situation se reproduit.

**Fichiers de reference** :
- `/app/backend/scripts/fix_owners_import_acp.py`
- `/app/backend/scripts/dedup_suppliers.py`
- `/app/backend/scripts/restore_orphan_owner_refs.py` (nouveau - critique)


### Iter72ter (Feb 2026) - Budget parser v2 (multi-pages) + Reprise comptable + Lettrage auto + Banking names

#### Budget PDF parser - Refonte multi-pages + alignement colonnes par cluster
**Bug** : nouveau PDF Budget Optipro (format 3-colonnes "Désignation / Réalisé N-1 / Budget N")
ne parse pas correctement :
- Sections 0009, 0010, 0011 (page 2) IGNOREES car le filtre `top > 195` excluait
  toute la page 2 (header de page 2 a un Y different de page 1).
- Montants Budget 2026 lus comme 0 EUR : les colonnes etaient detectees depuis
  les en-tetes annee ("2025", "2026") qui sont decales ~75px a GAUCHE des montants
  reels (effet Optipro : annee centree, montants right-aligned).
- Montants split par pdfplumber : "30 051,00" -> 2 mots "30" + "051,00" landing
  dans des colonnes differentes apres boundary cut.

**Fix `import_wizard/pdf_utils.py::parse_budget_pdf`** :
1. **Header dynamique par page** : detecte le Y de la ligne "20XX" sur CHAQUE
   page (ex : top=180 page 1, top=46 page 2) et utilise ce Y comme cutoff au
   lieu d'un seuil hardcode 195.
2. **Cluster des colonnes depuis les amounts** : collecte tous les right-edges
   des mots numeriques (>400px), cluster 1D (gap < 25px), keep clusters >= 3 points.
   Plus robuste que les en-tetes year-words.
3. **Amount groups** : avant le matching column, regroupe les mots numeriques
   contigus (gap < 15px) sur la meme Y en "amount group" -> "30" + "051,00"
   devient une seule entite "30051.00" matchee a UNE colonne par son right-edge.
4. **Current_section persistant entre pages** : un detail line sur page N+1 sans
   anchor section precedente est rattache a la section ouverte en fin de page N
   (fix des lines 61056-61300 de la section 0008 sur page 2).

**Test E2E** : PDF "Budget du 01_01_2026 au 31_12_2026 (1).pdf" :
- AVANT fix : 41 231 EUR (manque 769)
- APRES fix : **42 000.00 EUR exactement** (12 sections detectees, ecart 0.00)
- Sections : 0001 (30051), 0006 (0), 0007 (9000), 0008 (2054), 0009 (0), 0010 (0),
  0011 (-605), 0012 (250), 0014 (0), 0015 (1250), 0017 (0), 0018 (0).

**Note** : le commit-budget utilise deja `budget_n` (Budget 2026 uniquement),
PAS `realise_n1` (Realise 2025 sert juste a la previsualisation).

#### Reprise comptable dans Situation de compte (owners + suppliers)
**Demande user** : la situation de compte d'un proprietaire ou fournisseur DOIT
inclure une ligne "Reprise comptable" au sommet, datee du 1er jour de l'exercice
visualise, agregeant le solde de cloture des exercices precedents (incl. OD
d'ouverture / Bilan).

**Backend** `routes/reports.py::situation_compte_owner` + `_supplier` :
- Combine 2 sources dans une seule ligne "Reprise comptable au [start_date]" :
  (a) toutes les ecritures avec date < start_date (cumul exercices clos),
  (b) toutes les ecritures journal_type='AN' dans la periode visualisee.
- Les lignes AN-in-period sont alors EXCLUES de la liste mouvements normale
  (pas de doublon avec la ligne reprise).
- Tag `is_reprise: True` + `journal_type: 'AN'` pour styling frontend.
- Description : "Reprise comptable au YYYY-MM-DD".

**Test E2E** : FY 2027 (start_date=2027-01-01) avec AN dans FY 2026 (1_400 EUR
crediteur) :
- AVANT fix : Total Debit=0 / Credit=0 / Solde=0 EUR (le AN etait < start_date)
- APRES fix : ligne "Reprise comptable au 2027-01-01" affichee, Total Debit=2599.29
  EUR pour LENOTRE-MANSART, ligne distincte du reste des mouvements.

#### Import journaux bancaires - Nom fournisseur + auto-lettrage
**Bug** : les bank_transactions importees affichaient "Fournisseurs" generique
(label du compte 4400) au lieu du vrai nom (SRL ACE Garden, Engie...), rendant
les extraits illisibles et empechant tout lettrage.

**Fix `csv_utils.py::parse_journals_csv`** :
- Recupere `Auxiliaire` (F0XXX) et `Identite` (nom fournisseur) depuis le CSV
  Optipro (champ `Identite` = nom reel, `Auxiliaire` = code F0606).
- Output transactions : ajoute `counterparty_name` (nom) et `counterparty_aux`
  (code) en plus du libelle technique.

**Fix `routes/import_wizard.py::commit_journals`** :
- bank_transactions stocke maintenant le VRAI nom du fournisseur (counterparty_name)
  + auxiliary_code + supplier_id resolu via auxiliary_code.
- Journal entries FI : description enrichie avec nom du fournisseur, ET la ligne
  credit/debit du fournisseur a `third_party_id` + `third_party_type='supplier'`
  pour matching balance-tiers.

**Auto-lettrage post-import** : pour chaque transaction "out" (paiement) avec
supplier_id resolu, cherche une facture du meme fournisseur avec montant identique
(tolerance 0.01 EUR), la plus ancienne d'abord. Si trouvee :
- bank_transaction.matched=True, matched_id=invoice.id, match_source='auto_import_journals'
- invoice.status='paid', paid_at=transaction.date

**Test** : sur 39 txns Optipro, X transactions auto-lettrees (resultat dependant
de la presence de factures correspondantes en DB).




### Iter72 (Feb 2026) - Phase I (OD d'ouverture) + Cles details + Banking visibility + Balance tiers AN + Lettrage manuel orphelins

#### Lettrage manuel des fournisseurs orphelins (Balance de Tiers)
Bug observe par le user : apres l'import de factures, certains fournisseurs
apparaissent comme "Orphelin" dans la balance de tiers (factures presentes
mais pas d'ecriture comptable AC sur leur compte tier 4400XXX).
Cause : pendant le commit-invoices, soit le supplier_aux_code etait vide,
soit le tier_account du fournisseur n'etait pas encore configure, soit la
session a ete partiellement rollbackee.

**Nouveau endpoint** `POST /api/reports/balance-tiers/lettrer-supplier` :
- Body : `{copropriete_id, orphan_name, supplier_id}`
- Resout / cree le compte tier 4400XXX pour le fournisseur (via auxiliary_code
  F0XXX -> 4400XXX, ou auto-incremente 44000XXX si pas d'aux).
- Cree le PCMN account associe si manquant.
- Met a jour `supplier.tier_accounts[copro_id].main = tier`.
- Pour chaque facture avec `supplier == orphan_name` ET pas d'ecriture AC :
  - Cree une ecriture AC double-entree (debit charge 6xxx / credit tier 4400xxx)
    avec `third_party_id = supplier_id`.
  - Backfill `invoice.supplier_id` si necessaire.
- Returns : `{tier_account, invoices_matched, invoices_relinked, journal_entries_created}`.

**Frontend** `BalanceTiersPage.js` :
- Bouton "Lettrer" (icone Link2 ambre) sur chaque ligne orpheline du tab
  Fournisseurs (au lieu du bouton Eye qui n'est dispo que pour les fournisseurs
  connus).
- Dialog modale avec :
  - Titre + sous-titre indiquant le nom orphelin + nb factures.
  - Input recherche autofocus (filtre par nom / aux_code / BCE / VAT).
  - Liste scrollable des fournisseurs en base (max 60), affichant nom, BCE,
    badge aux_code, icone Link2.
  - Banner amber d'explication de l'effet du lettrage.
  - Clic sur un fournisseur -> POST commit -> toast feedback -> reload table.

**Test E2E** : sur ACP "import" avec 7 orphelins, click "Lettrer" sur
"Dardenne Pierre" -> selection F0606 -> 2 factures relinkees + 2 ecritures AC
creees (compte tier 44000606) -> orphan disparu, balance 1142.00 EUR affiche
en "A payer".


### Iter72bis - Original

#### Phase I - OD d'ouverture / Bilan comptable (Optipro -> AN)
**Backend** : `backend/routes/import_wizard.py::commit_opening_balance` retravaille
- Parser PDF `parse_balance_pdf` (deja en place) extrait actif/passif avec
  is_subaccount=True pour les sous-comptes (4100960, 4400025, etc.)
- **Filtre leaf-accounts** : exclut les comptes parents (410, 440) quand ils ont
  des sous-comptes -> evite le double comptage (parent + enfants).
  Ex : Actif {410=3682.05, 4100960=2599.29, 4100962=1082.76, 550472=8173.79} ->
  leaves = [4100960, 4100962, 550472] = 11855.84.
- **Mapping owners/suppliers** via auxiliary_code Optipro :
  - Compte 4100960 -> owner avec auxiliary_code "C0960"
  - Compte 4400025 -> supplier avec auxiliary_code "F0025"
  - Compte 4001XXXX (reserve) -> owner via meme regle
  - Si match : `third_party_id` + `third_party_type` ajoutes sur la ligne, ET
    `owner.tier_accounts[copro_id].provisions` (ou reserve) mis a jour avec le
    code Optipro -> la balance-tiers reconnait automatiquement les soldes.
- Ecriture journal_entries de type **AN** datee au 1er jour de l'exercice
  fiscal (ou 01/01 de l'annee+1 du period_end_date).
- Fiscal lock check sur la date de l'ecriture.

**Frontend** : `OpeningBalancePreview` (`ImportWizardPage.js`) :
- Totaux Actif/Passif calcules sur leaves uniquement (synchro avec backend).
- Banner d'info : "Les comptes parents qui regroupent des sous-comptes sont
  automatiquement exclus du commit pour eviter le double comptage."
- Tableau editable 2 colonnes Actif/Passif avec affichage hierarchique (parents
  en gras, sous-comptes indented).
- Toast post-commit indique le nombre d'owners + suppliers lies.

**Test E2E** : PDF "Bilan comptable au 31/12/2025.pdf" -> 9 lignes leaves
inserees (4 actif, 5 passif, dont 3 + 6 sub-accounts), Debit=Credit=11855.84,
4 owners + 4 suppliers automatiquement lies.

#### Balance de Tiers - Reprise comptable des soldes d'ouverture
Le bilan d'ouverture (AN) est maintenant visible dans la Balance de Tiers :
- Owners : LENOTRE-MANSART 2599.29 debiteur, RAPHAEL-MICHEL ANGE -1194.36
  crediteur, RUBENS-RENOIR 1082.76 debiteur, VELASQUEZ-GOYA -4746.16 crediteur.
- Suppliers : 4 fournisseurs avec leurs comptes 440xxxx (Engie 360, SUEZ
  1656.79, SRL ACE Garden 1515.53, Vidange 2383).
- Total debiteurs : 3682.05 EUR, Total crediteurs : 5940.52 EUR.
- Total a payer aux fournisseurs : 14828.57 EUR (incluant AN + factures import).

#### Cles de repartition - Parser refonte (lignes detail)
**Bug** : le parser `parse_distribution_keys_pdf` ne lisait pas les lignes
detail (lot/quotite). Le PDF Optipro a une structure 2-lignes :
  - Row N   = resume cle : ['0001 - Charges communes', '-', '4', '168,00']
  - Row N+1 = details : ['Lot1\nLot2\n...', 'C0960\nC0961\n...', '-...', '39\n40...']
**Fix** : parsing en 2 passes - on detecte la row resume (avec code 0XXX) et
on ATTACHE les lignes de la row suivante (sans code) au `current_key`.
- Resultat sur PDF reference : 4 lignes detail extraites (LENOTRE-MANSART,
  RAPHAEL-MICHEL ANGE, RUBENS-RENOIR, VELASQUEZ-GOYA) avec quotities 39, 40,
  49, 40 -> total 168 ✓.

**Frontend** : `KeysPreview` (`ImportWizardPage.js`) :
- Colonne "Coproprietaire" ajoutee (affiche owner_label de Optipro, ex.
  "C0960 - LENOTRE-MANSART").
- Banner amber si aucune ligne extraite ("Verifiez le PDF source").

**Commit** : `commit-distribution-keys` met a jour les cles existantes avec 0
lignes au lieu de les ignorer (cas frequent : une cle 0001 avait ete creee
vide par un import precedent, le nouvel import enrichit avec les lignes).

#### Banking - Visibilite des imports journaux
**Bug** : apres l'import du CSV "journaux", les transactions etaient stockees
dans `bank_statement_lines` mais l'interface `/banking` ne les voyait pas (elle
lit `bank_statements` + `bank_transactions`).
**Fix** `commit_journals` (`routes/import_wizard.py`) :
- Cree des **bank_statements** parents groupes par (bank_pcmn, year-month) -
  ex: pour 39 txns on a 4 statements (IMP-2026-01, IMP-2026-03, IMP-2026-04,
  IMP-2026-05).
- Pour chaque txn : insertion dans `bank_transactions` avec statement_id +
  signed_amount (IN=positif, OUT=negatif), counterparty_name, communication.
- Closing_balance auto-calcule = sum des mouvements de l'extrait.
- Conserve bank_statement_lines pour audit/tracabilite.
- Rollback : ces collections etaient deja dans la liste -> propre.

**Test E2E** : CSV `journaux_20260621644.csv` (39 txns) -> 4 bank_statements
visibles dans /banking + 39 journal_entries FI + 39 bank_statement_lines.

#### Dropdown propriétaires - Bug d'affichage post-import
**Bug** : dans l'Assistant de creation ACP (Step 2 - Lots & proprietaires), le
dropdown d'affectation lot -> proprietaire ne montrait QUE les owners ayant un
lot dans l'ACP courante (chinese-wall via `db.lots.distinct`). Les owners
juste importes via PdfImportDialog (sans lot encore lie) etaient invisibles.

**Fix 1 - Backend `properties.py::list_owners`** : nouveau param
`include_unassigned=true` qui ajoute les owners orphelins (copropriete_id="")
au resultat. Test : sans param = 0 owners pour ACP "import" ; avec param =
845 owners visibles (les imports orphelins du PDF Optipro).

**Fix 2 - Frontend `CoproprietesPage.js`** :
- Tous les `api.get('/owners')` envoient maintenant `?include_unassigned=true`
  pendant le dialog d'edition/creation d'ACP.
- Nouveau state `ownerFocusLot` : la dropdown s'ouvre AU FOCUS de l'input
  (sans saisie obligatoire) et affiche jusqu'a 50 owners disponibles avec leur
  auxiliary_code (C0XXX) et VCS code.
- Refetch automatique des owners au focus pour avoir la liste fraiche.
- Placeholder : "Cliquez pour voir la liste, ou tapez nom / email / VCS..."

#### Difference Optipro vs CoproManager (montants depenses)
**Analyse demandee** : User comparait le "Total filtre depenses" CoproManager
(20026.18 EUR / 34) avec l'Optipro "Liste des depenses" (20323.29 EUR).
Difference = 297.11 EUR.

**Decompose** :
- 305.00 + 0.15 + 2.50 + 2.50 + 2.50 + 9.68 - 25.22 = 297.11 EUR
- Lignes Optipro sur comptes 650 (Frais bancaires) + 750 (Produits financiers)

**Conclusion** : difference attendue et CORRECTE :
- Optipro liste tout (frais bancaires + factures) dans la meme "Liste des depenses"
- CoproManager separe par journal comptable :
  - AC (achats fournisseurs) : 34 factures = 20026.18 EUR (matches le dashboard)
  - FI (financier - frais bancaires) : ~297.11 EUR (dans /banking et grand livre)
- Conforme au PCMN belge : ne pas melanger compte 6X (charges) et 65X (frais financiers).

#### Files de reference
- `/app/backend/import_wizard/pdf_utils.py` (parser keys refonte 2-passes)
- `/app/backend/routes/import_wizard.py` (commit_opening_balance + journals + keys)
- `/app/backend/routes/properties.py` (list_owners + include_unassigned)
- `/app/frontend/src/pages/ImportWizardPage.js` (OpeningBalancePreview + KeysPreview)
- `/app/frontend/src/pages/CoproprietesPage.js` (dropdown owners au focus)





### Iter71 (Feb 2026) - Wizard Optipro Phases G + H : Factures + Journaux financiers

#### Phase G - Factures (CSV Optipro)
**Nouveau parser** `parse_invoices_csv` dans `backend/import_wizard/csv_utils.py` :
- Encodage Latin-1 detecte automatiquement (CSVs Optipro/Sogis).
- Separateur `;` detecte via sniff.
- Headers normalises (lowercase + strip accents) : `Copropriete code`, `Date facture`,
  `Date echeance`, `Reference interne/externe`, `Libelle`, `Ne pas payer`,
  `Fournisseur`, `Compte`, `Cle`, `Nature`, `Code TVA`, `Part occupant/proprietaire`,
  `Montant HT/TVAC`.
- Decompose `Fournisseur` ("F0471 - SRL ACE Garden") en code + nom via `split_optipro_code`.
- Calcul TVA = TVAC - HT.
- Normalisation date DD/MM/YYYY -> YYYY-MM-DD via `parse_date`.

**Nouvel endpoint** `POST /api/import-wizard/sessions/{id}/commit-invoices` :
- Construit 3 lookups scoped a l'ACP : `sup_by_aux`, `keys_by_code`, `cats_by_code` + `cats_by_account`.
- Auto-rattachement de chaque facture :
  - Supplier via `auxiliary_code` F0XXX (exact match)
  - Distribution_key via code 0XXX
  - Expense_category via code Nature 0XXX OU fallback sur account_number (61060)
- Repartition occupant/proprietaire normalisee (defaut 100/0 si non specifiee).
- Generation auto de la `internal_reference` "FA-YYYY-NNNN" par ACP/annee.
- Fiscal lock (`ensure_period_open`) verifie la date de chaque facture.
- Tag `import_session_id` pour rollback.

**Test E2E** : 22 fournisseurs PDF -> 34 factures importees -> **34/34 auto-rattachees**
au fournisseur via F0XXX (100% match).

#### Phase H - Journaux financiers / Extraits bancaires (CSV Optipro)
**Nouveau parser** `parse_journals_csv` dans `csv_utils.py` :
- Lit les 78 lignes en double-entry (debit + credit pour chaque transaction).
- Regroupement par `Num. doc` -> 39 transactions consolidees.
- Detection automatique du compte bancaire (PCMN 55x / 57x / 416x / 417x).
- Direction `in/out/neutral` selon debit/credit du compte banque.
- Compteparty account extrait des autres lignes.

**Nouvel endpoint** `POST /api/import-wizard/sessions/{id}/commit-journals` :
- Lookup automatique des `bank_accounts` de l'ACP via `account_number` ou `pcmn_account`.
- Insere dans `bank_statement_lines` avec :
  `bank_account_id, bank_pcmn_code, counterparty_account, num_doc, code_journal,
   date_value, date_compta, libelle, amount, direction, status='imported'`.
- Status "imported" -> pret pour rapprochement bancaire ulterieur.
- Fiscal lock par ligne, rollback support via import_session_id.

#### Frontend (`ImportWizardPage.js`)
- **STEPS passe de 5 a 7** : Fournisseurs, Natures, Exercice fiscal, Budget,
  Cles, **Factures** (icon FileText), **Journaux** (icon Landmark).
- Les 2 nouvelles etapes ont `kind: 'csv_invoices'` / `'csv_journals'` -
  upload direct sans mapping (format Optipro detecte).
- Nouveau **`InvoicesPreview`** : table editable avec colonnes Date / N° ext /
  Fournisseur (avec badge F0XXX vert) / Cpte / Cle / Nat / Libelle / HT / TVAC / TVA.
  Banner haut : compteur + totaux HT/TVAC.
- Nouveau **`JournalsPreview`** : table editable Date / Doc / J. / Banque / Cpte ctr /
  Libelle / Montant / Sens (vert +/rouge -/gris ~). Banner avec total IN/OUT + solde net.

#### Cleanup rollback
`bank_statement_lines` ajoute a la liste des collections nettoyees lors du
rollback de session.

#### Tests E2E
```
SUPPLIERS PDF -> 22 suppliers inserted
INVOICES CSV  -> 34 invoices inserted, 34/34 matched_supplier (auto-affectation via F0XXX)
JOURNALS CSV  -> 39 transactions inserted (78 lignes -> 39 transactions consolidees)
ROLLBACK      -> {suppliers: 22, invoices: 34, bank_statement_lines: 39} supprimes
```

#### Phase I - OD d'ouverture
**Pas encore implementee**. Le user n'a pas fourni de fichier d'exemple pour
cette phase. A planifier dans un prochain iter en attendant les specs.



### Iter70 (Feb 2026) - Refonte parser PDF Budget (3 colonnes correctes)

#### Bug rapporte
Le parser `parse_budget_pdf` etait text-based (regex sur full_text) et echouait sur
le PDF Optipro "Budget" : il detectait une seule section "0008" au lieu de 10,
melait les amounts dans les libelles ("Travaux divers 36,30 38,00"), et affichait
des montants 0 ou aberrants (ex: 145,2 pour Ordures menageres au lieu de 2 820,00).

#### Refonte anchor-based (`backend/import_wizard/pdf_utils.py`)
Memes principes que les autres parsers (owners/lots/suppliers) :
- Anchor sections : `^0\d{3}$` (4 chiffres commencant par 0) avec `x0 < 50`.
- Anchor details : `^\d{3,5}$` (3 a 5 chiffres NE commencant PAS par 0) avec `x0 >= 50`.
- Bandes Y entre anchors consecutifs pour capturer les libelles multi-lignes
  (ex. "Repartition frais banane (Le Notre-Mansart / Raphael-Michel-Ange /
  Velasquez - goya)" sur 3 lignes).
- 3 colonnes de montants detectees automatiquement par leurs centres x
  (Realise N-1, Budget N, En cours).
- Bornes amount non-chevauchantes (midpoints entre centres consecutifs).
- Detection du mot "Totaux" pour stopper la bande avant la ligne footer
  "Totaux generaux : 42 000,00" (qui polluait la derniere section).
- Selection de la ligne d'amounts CLOSEST de l'anchor (au lieu du max) pour
  eviter qu'une ligne footer plus loin vole les montants.

#### Donnees retournees enrichies
Chaque section et chaque ligne porte maintenant les 3 montants :
- `realise_n1` (Realise N-1, info pour comparaison)
- `budget_n` (Budget N, **valeur importee comme budget previsionnel**)
- `en_cours` (En cours, info pour suivi)
- `amount` (legacy : synchro avec `budget_n` pour back-compat avec
  `/commit-budget` endpoint inchange)

#### Resultats sur PDF de reference (`Budget du 01_01_2026 au 31_12_2026.pdf`)
- **10 sections** detectees (0001 / 0006 / 0007 / 0008 / 0011 / 0012 / 0014 /
  0015 / 0017 / 0018) au lieu de 1.
- **25 lignes de detail** parsees avec amounts repartis sur les 3 colonnes.
- **Total Budget N = 42 000.00 EUR** correspond exactement au "Totaux
  generaux" du PDF.
- Libelles multi-lignes captures (ex. section 0008 nom complet avec "goya)").

#### Frontend - `BudgetPreview` (`ImportWizardPage.js`)
- Affichage en **3 colonnes** : Realise N-1 (gris, indicatif) / **Budget N**
  (fond bleu, surligne, editable - **importe**) / En cours (gris, indicatif).
- Header section : montre les 3 sous-totaux ([0001] Charges communes -
  N-1: 25 507,50 / **N: 30 051,00** / En cours: 15 909,91).
- Banner haut : Budget N total + Realise N-1 et En cours en reference, avec
  rappel "Seule la colonne Budget N est importee".
- Edition inline des 3 montants + synchronisation `amount`/`budget_n`.

#### Validation
- Standalone `POST /api/import-wizard/parse-pdf?kind=budget` : 10 sections,
  total 42 000.
- Session-bound `POST /sessions/{id}/sniff-pdf?kind=budget` : meme resultat.
- Rollback session OK (chinese wall preservee).



### Iter69 (Feb 2026) - Import PDF Fournisseurs + Wizard Optipro simplifie

#### Demande user
1. Le wizard de reprise Optipro/Sogis affichait encore "Proprietaires" et
   "Lots" en etapes 1 et 3 alors que ces 2 imports sont desormais faits
   dans l'Assistant de creation ACP (iter68). Etapes redondantes.
2. L'etape "Fournisseurs" du wizard etait limitee au CSV. Il faut supporter
   le PDF aussi (export Optipro "Liste des fournisseurs.pdf").

#### Suppression des etapes redondantes (`frontend/src/pages/ImportWizardPage.js`)
- STEPS passe de 7 a **5** :
  Fournisseurs -> Natures depense -> Exercice fiscal -> Budget -> Cles de repartition.
- Owners + Lots retires (commentaire explicatif laisse dans le code).
- Cleanup des imports `Users`, `Home` (lucide) et de `TARGET_FIELDS.owners` / `.lots`.

#### Nouveau parser `parse_suppliers_pdf` (`backend/import_wizard/pdf_utils.py`)
Strategie identique a owners/lots :
- Anchor `^F\d{4}$` avec `x0 < 80`.
- Colonnes : `aux, name, default, coord, address`.
- Header multi-rows "PAR DEFAUT" merge correctement.
- Coord parsing : email + phone separes (regex).
- Address parsing : `address_pc_re` extrait `<rue> <CP 4digits> <ville> , <pays>`.
- `is_default` detecte la valeur "Oui" (vs "Non").
- Test : 22 fournisseurs extraits du PDF de reference, avec F0001 SRL Finlead
  correctement marque `is_default=True`.

#### Endpoint backend
- `POST /api/import-wizard/parse-pdf?kind=suppliers` (standalone, ACP non requise).
- `POST /api/import-wizard/sessions/{id}/sniff-pdf?kind=suppliers` (session-bound).
- **NOUVEAU** `POST /api/import-wizard/sessions/{id}/commit-suppliers-pdf` :
  prend `{suppliers: [...]}` deja structures (pas de mapping requis).
  Insere directement dans `db.suppliers` avec `import_session_id` (rollback OK).
- Stocke `auxiliary_code` + `is_default` (preserve la reference legacy Optipro).

#### Frontend - `kind: 'csv_or_pdf'` (nouvelle modalite)
L'etape Fournisseurs supporte les 2 formats :
- **2 boutons** [data-testid=`upload-csv-btn` / `upload-pdf-btn`] affiches
  cote a cote dans la zone upload.
- `uploadMode` state tracke le mode choisi pour le step en cours.
- Si CSV : flow classique avec mapping de colonnes.
- Si PDF : nouveau composant `SuppliersPdfPreview` affichant un tableau
  editable (code aux., nom, email, telephone, adresse, CP, ville, defaut),
  puis commit via `/commit-suppliers-pdf`.

#### Tests E2E (playwright)
- Login -> /import-wizard -> Etape 1/5 Fournisseurs avec 2 boutons CSV/PDF.
- Click "Choisir PDF" + upload `Liste des fournisseurs.pdf` ->
  22 lignes detectees, table editable affichee.
- F0001 SRL Finlead : checkbox `Defaut` coche (correctement detecte).
- Commit -> toast "22 fournisseur(s) importes" + step "Fournisseurs"
  marque `22 importes` dans le stepper + passage auto a "Etape 2/5 :
  Natures depense".
- Rollback session -> 22 fournisseurs supprimes (chinese wall OK).



### Iter68 (Feb 2026) - Import PDF Owners + Lots dans l'Assistant ACP + Auto-affectation

#### Probleme P0
Le parser PDF `parse_owners_pdf` (iter precedent) extrayait 0 ligne sur le PDF
Optipro "Liste des coproprietaires.pdf" en raison des coordonnees x/y mal calculees.
De plus, le CSV Lots avait des limites (le user demande un import PDF aussi).

#### Refonte des parsers PDF (`import_wizard/pdf_utils.py`)
Strategie anchor-based robuste utilisant 4 helpers reutilisables :
- `_normalize_header(text)` : minuscules + NFD strip-accents.
- `_detect_column_boundaries(header_words, label_map)` : detecte la ligne header
  ACTUELLE (multi-rows merge si gap < 8px), exclut les headers parasites
  (HAULOTTE, "Lots" dans le body), retourne les bornes x + `header_y_bottom`.
- `_refine_columns_from_data(cols, ...)` : cluster les `x0` des 8 premieres
  rows-anchors, merge les clusters dont gap < 25px (= meme colonne), puis
  associe chaque cluster a une colonne header (margin gauche elargie a 30px
  pour capturer les prefixes type "C0XXX - " dans la colonne PROPRIETAIRE).
- `_group_into_rows_by_anchor(words, anchor_pred, header_y_max)` : bandes
  Y delimitees par MIDPOINT entre anchors consecutifs -> capture le texte
  qui wrap AU-DESSUS ET EN-DESSOUS de l'anchor (ex. "M. et Mme MOUCHET-"
  sur la ligne au-dessus, "GERMAIN Gaston et Nicole" en-dessous).

Cle technique : matching des mots par `x0` (donnees gauche-alignees Optipro)
au lieu du centre (sinon les mots longs comme "Bernadette" debordent dans la
colonne suivante).

#### `parse_owners_pdf` corrige
- Cols detectees : `aux, name, ident, coord, lots, qts, vcs`.
- Anchor = `^C\d{4}$` (codes auxiliaires Optipro).
- Decompose `name` en `civility / last_name / first_name` (regex civility
  enrichie : `M. et/ou Mme`, `Mme et M.`, `Mr et Mme`...).
- Tokens uppercase + hyphenated (MOUCHET-GERMAIN) traites comme last_name.
- Test : 64 proprietaires extraits sur le PDF de reference (2 pages).

#### `parse_lots_pdf` (nouveau)
- Cols detectees : `code, reference, nature, batiment, qts, owner, address`.
- Anchor = `^\d{4}$` avec `x0 < 80` (codes lot Optipro).
- Header multi-ligne "NATURE DU BIEN" merge correctement (rows < 8px gap).
- Parse owner field "C0946 - M. LEGROS Jean" -> owner_auxiliary_code +
  owner_name separes.
- Test : 72 lots extraits dont 4 appartements avec quotities (39/40/49/40).

#### Endpoint backend
- `POST /api/import-wizard/parse-pdf` (stateless, ACP non requise) :
  ajout du parametre `kind=lots` (en plus de `owners/natures/budget/keys`).

#### Model `OwnerInput` enrichi (`routes/properties.py`)
Nouveaux champs accepts a la creation :
- `civility` (M. / Mme / M. et Mme ...)
- `auxiliary_code` (C0XXX Optipro - cle de matching avec les lots)
- `identifier` (reference legacy Optipro)
- `vcs_code` + `vcs_digits` (si fournis, on REUTILISE le VCS Optipro au lieu
  d'en generer un nouveau - preserve la continuite des paiements).
- `iban`.

#### Frontend - Nouveau composant `PdfImportDialog.js`
Composant generique reutilisable pour les imports PDF :
- Zone upload + preview en tableau scrollable avec checkbox de selection
  ligne par ligne.
- Toggle "Tout selectionner / deselectionner".
- Badge de comptage `X / Y selectionnees`.
- Validation : reset ou import (callback `onImport(selectedRows)`).
- Avertissement amber : "Verifiez les donnees parsees avant import."

#### Frontend - `CoproprietesPage.js` (Assistant de creation ACP - Step 2)
4 boutons d'import :
- **Proprietaires (CSV)** [data-testid=`import-owners-csv-btn`]
- **Proprietaires (PDF)** [data-testid=`import-owners-pdf-btn`] NOUVEAU
- **Lots (CSV)** [data-testid=`import-lots-csv-btn`]
- **Lots (PDF)** [data-testid=`import-lots-pdf-btn`] NOUVEAU
- **Ajouter manuellement** [data-testid=`add-lot-btn`]

#### Auto-affectation lot <-> proprietaire (cle de la demande user)
- **Au moment de l'import lots** : match via `owner_auxiliary_code` (C0XXX)
  exact, puis fallback nom (substring case-insensitive). Refetch owners
  juste avant matching pour avoir la liste a jour.
- **Au moment de l'import owners (PDF ou CSV)** : RETROACTIF - re-scan
  `form.lots` apres setOwners, et auto-affecte les lots orphelins via leur
  `_imported_owner_aux` ou `_imported_owner_name` sauvegardes.
- Resultat : auto-affectation totale quel que soit l'ordre (Owners->Lots
  ou Lots->Owners).

#### UI feedback
- **Summary banner** [data-testid=`lots-summary`] : "X lot(s) au total
  - Y auto-affectes - Z orphelins (proprietaire manquant)".
- **Badge vert** pour chaque proprietaire affecte (avec X de retrait).
- **Badge ambre "Non rattache: C0XXX <nom>"** [data-testid=`lot-{i}-orphan`]
  pour les lots orphelins (proprietaire pas encore importe).
- **Bouton "Reessayer l'auto-affectation"** [data-testid=`lots-retry-match-btn`]
  visible quand orphelins > 0 : refetch owners + re-scan.
- **Toast d'import** mentionne le nombre auto-affecte : "72 lot(s) importes -
  72 auto-affectes a leurs proprietaires".

#### Tests E2E (playwright)
- Login superadmin -> /coproprietes -> "Nouvelle ACP" -> Step 1 (nom) ->
  Step 2 -> "Proprietaires (PDF)" -> upload `Liste des coproprietaires.pdf`
  -> 64 lignes detectees -> Import -> 64 owners crees en DB.
- "Lots (PDF)" -> upload `Liste des lots.pdf` -> 72 lignes detectees ->
  Import -> 72 lots dans le state local, **72/72 auto-affectes via C0XXX**.
- Summary banner affiche "72 lot(s) au total · 72 auto-affectes" en vert.
- Badges verts visibles sur chaque ligne lot avec le nom du proprietaire
  (ex: "M. LEGROS Jean", "Mme Grignard Liliane").

#### Files de reference
- `/app/backend/import_wizard/pdf_utils.py` (refonte parsers)
- `/app/backend/routes/import_wizard.py` (endpoint +kind=lots)
- `/app/backend/routes/properties.py` (OwnerInput enrichi)
- `/app/frontend/src/components/PdfImportDialog.js` (NEW)
- `/app/frontend/src/pages/CoproprietesPage.js` (boutons PDF + auto-affectation)



### Iter65 (Feb 2026) - Wizard d'import Optipro / Sogis (Phase 1)

Module CRITIQUE pour la migration depuis les anciens logiciels syndic Optipro/Sogis. Decoupage en **4 phases**. Cette iteration livre la **Phase 1**.

#### Analyse des fichiers Optipro fournis
- **Factures CSV** : UTF-8, separateur `;`, dates JJ/MM/AAAA, virgule decimale. Colonnes : Copropriete code, nom, Date, Fournisseur (F0471 - SRL ACE Garden), Compte (61060 - libelle), Cle (0001 - libelle), Nature, Code TVA, Part occupant/proprietaire, Montant HT/TVAC.
- **Journaux CSV** : UTF-8, separateur `,`, dates JJ/MM/AAAA, point decimal. Format dual-entry (1 facture = 2 lignes : debit/credit).
- **Bilan PDF** : comptes 410 (copro), 440 (fournisseurs), 550xxx (banque), 100 (fonds roulement), 494 (regularisation). Total ACTIF / PASSIF.
- **Budget PDF** : sections par cle de repartition + comptes 61xxx avec montants budgetes.
- **Natures PDF** : Code (0001-0042), Libelle, Compte PCMN, TVA, Part occupant/proprietaire en %.
- **Cles PDF** : code + nom, type tantiemes, repartition par lot avec total quotites.

#### Backend `import_wizard/` (Phase 1)
- `csv_utils.py` : sniffing (UTF-8/cp1252/latin-1), detection separateur, normalisation headers (suppression accents/replacement chars), parse_french_number, parse_date, split_optipro_code (`F0471 - SRL ACE Garden` -> `("F0471", "SRL ACE Garden")`).
- `pdf_utils.py` : extraction pdfplumber avec parser `parse_natures_pdf` qui gere les cellules multilignes (split par `\n` + zip des colonnes + heuristique de merge des wraps).
- `routes/import_wizard.py` : endpoints
  - `POST /api/import-wizard/sessions` : creer session pour ACP
  - `GET /api/import-wizard/sessions/active?copropriete_id=` : recuperer session active
  - `POST /api/import-wizard/sessions/{id}/sniff-csv` : preview CSV (headers + 20 lignes + meta)
  - `POST /api/import-wizard/sessions/{id}/sniff-pdf?kind=natures` : extraction PDF
  - `POST /api/import-wizard/sessions/{id}/commit-owners` : insert tagged `import_session_id`
  - idem pour `commit-suppliers`, `commit-lots`, `commit-natures`
  - `POST /api/import-wizard/sessions/{id}/finish` : verrouille la session
  - `DELETE /api/import-wizard/sessions/{id}` : **rollback complet** (supprime tous docs taggees)
- Chinese wall actif : seul un user avec l'ACP dans son `copropriete_ids` (ou superadmin) peut creer/manipuler une session.

#### Frontend `ImportWizardPage.js`
- Stepper visuel 4 etapes : Proprietaires (A) -> Fournisseurs (C) -> Lots (D) -> Natures (K).
- Upload + sniff + preview tableau (20 premieres lignes) avec encoding/separateur affiches.
- **Mapping manuel** des colonnes : pour chaque champ cible (last_name, address, etc.), select sur les headers detectes. Champs obligatoires marques `*`.
- Pour les natures (PDF) : tableau editable inline (code, libelle, compte, TVA, % occ/prop, suppression de lignes).
- Bouton `Annuler l'import` permanent (delete session = rollback complet).
- Apres `finish`, redirection vers le dashboard de l'ACP.

#### Integration creation d'ACP
- Apres `POST /coproprietes` reussi, modal de confirmation : "S'agit-il d'une REPRISE depuis Optipro / Sogis ?"
- Si Oui : redirection automatique vers `/import-wizard?copropriete_id=<new_id>`.
- Si Non : flow normal.

#### Tests E2E (curl)
- Creation session pour ACP Test (Finlead) : OK (id genere).
- Sniff PDF natures : 27 natures detectees correctement (code + libelle + compte + TVA + parts).
- Commit-natures : 27 docs inseres dans `expense_categories` avec `import_session_id`.
- DELETE session : 27 docs supprimes, base intacte (4 natures pre-existantes preservees).
- Screenshot E2E Playwright : wizard charge correctement avec stepper + zone upload.



### Iter63 (Feb 2026) - Integration Microsoft Graph pour invitations par email

#### Configuration MSGRAPH (Azure AD App Registration)
- 4 nouvelles variables dans `backend/.env` : `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `GRAPH_SENDER_UPN`.
- Expediteur : `welcome@goodexperienceproperties.be` (boite mail Microsoft 365 valide et licenciee).
- Permission requise (deja consentie) : **Mail.Send (Application)** sur Microsoft Graph.
- Flow OAuth2 : `client_credentials` (app-only, sans delegation utilisateur).

#### Backend `graph_email.py`
- MSAL `ConfidentialClientApplication` avec cache de jeton automatique (depuis 1.23).
- `send_html_email(recipients, subject, html_body)` : POST `/v1.0/users/{sender}/sendMail` via httpx async (timeout 15s).
- `build_invitation_email(...)` : template HTML branded CoproManager (gradient blue + cards + lien CTA).
- `is_configured()` helper pour les checks de disponibilite.

#### Auto-envoi a la creation
- `POST /api/admin/users` (creation syndic par superadmin) : envoi auto si `must_change_password=true`.
- `POST /api/team/members` (creation gestionnaire par syndic) : idem, avec inclusion du role_template_name dans le label.
- Lien d'invitation : `${FRONTEND_URL}/login?invite=<email>`.
- Envoi via `asyncio.create_task(...)` non bloquant (la creation user repond meme si email lent).

#### Endpoints supplementaires
- `POST /api/admin/users/{id}/resend-invitation` (superadmin) : renvoi pour un syndic.
- `POST /api/team/members/{id}/resend-invitation` (syndic) : renvoi pour un gestionnaire.
- `GET /api/admin/email-config` : etat de la config sans envoi.
- `POST /api/admin/email-config/test` : envoi d'un email test au superadmin connecte.

#### Frontend
- `LoginPage.js` : detection du parametre URL `?invite=<email>` dans un `useEffect`, pre-remplissage du champ email + bascule directe en mode `first-set` + banner "Bienvenue, veuillez definir votre mot de passe".
- `AdminUsersPage.js` : bouton `Mail` (Lucide) visible UNIQUEMENT pour les syndics avec `must_change_password=true` -> appelle `POST /admin/users/{id}/resend-invitation`.
- `TeamMembersPage.js` : meme bouton dans la ligne du gestionnaire si `must_change_password=true`.

#### Tests E2E
- `GET /api/admin/email-config` -> `configured: true, sender_upn: welcome@goodexperienceproperties.be`.
- `POST /api/admin/email-config/test` -> `{"status":"ok","sent_to":"gerald@gep.be"}` -> email recu.
- `POST /api/admin/users` avec email test -> `invitation_sent: true` -> email d'invitation effectivement recu sur la boite test.
- Lien d'invitation `/login?invite=test@example.com` -> page LoginPage en mode first-set avec email pre-rempli (verifie playwright).

#### Piege rencontre
- L'utilisateur a fourni 3 valeurs dans cet ordre : "102519da..." / "5b245644..." / secret.
- Premier essai : tenant=102519da -> Azure renvoie `AADSTS90002 Tenant not found`.
- Verification via `curl https://login.microsoftonline.com/<guid>/v2.0/.well-known/openid-configuration` -> identification que le bon Tenant ID est **5b245644-0340-4928-b1a9-d83441344aeb**. Inversion appliquee, ca fonctionne.



### Iter60 (Feb 2026) - Chinese wall syndic + Verrous fiscaux complets + Roles simplifies

#### A. Chinese wall STRICT entre syndics
Bug observe : Benjamin (nouveau syndic) voyait l'ACP "Test" de Finlead. Pire encore : en forcant `?copropriete_id=X`, n'importe quel syndic pouvait lire tout le contenu d'une ACP qu'il ne gere pas (lots, factures, owners, banking, accounting, dashboard, reports...).

Fixes :
1. **Middleware global** (`server.py`) : si la requete contient `?copropriete_id=X` ou header `X-Copropriete-Id: X`, et que l'utilisateur n'est pas superadmin/admin, et que X n'est pas dans `user.copropriete_ids` -> 403 immediat sur **TOUS** les endpoints (path-agnostic).
2. **`list_coproprietes`** : superadmin/admin voient tout, les autres (syndic/gestionnaire/owner) ne voient que leurs ACPs (`{"id": {"$in": user.copropriete_ids}}`). Si liste vide : retour [].
3. **`dashboard/stats`** : `is_admin_role` (qui inclut syndic) -> `is_superadmin_only`. Sans param, un syndic voit `coproprietes_count = nb de SES ACPs` au lieu de count global.

#### B. Auto-rattachement ACP au syndic createur
Bug : quand un syndic creait une ACP, elle n'etait PAS ajoutee a son `user.copropriete_ids`. Resultat : il ne voyait pas sa propre ACP apres creation.
Fix `routes/coproprietes.py::create_copropriete` :
- Apres insert, si role != superadmin : `db.users.update_one({"_id": ObjectId(user["_id"])}, {"$addToSet": {"copropriete_ids": doc["id"]}})`
- Pieg : `get_current_user` convertit `_id` en str -> il faut reconvertir en ObjectId pour matcher.
- Import `from bson import ObjectId` ajoute.
- Script ad-hoc execute pour rattacher retroactivement les ACPs orphelines aux syndics createurs (via `created_by` field).

#### C. Roles simplifies : superadmin ne gere QUE les comptes syndic principaux
Demande utilisateur : "Le superadmin ne gere que le compte principal. Le reste (ACPs + equipe) est du cote syndic."

Backend `routes/admin.py` :
- `POST /api/admin/users` : refuse 400 si `role != "syndic"`. Force `copropriete_ids = []`, ignore `role_template_id`/`permissions` (le syndic configure son equipe lui-meme).
- `PUT /api/admin/users/{id}` : refuse de changer `role`, `copropriete_ids`, `role_template_id`, `permissions` depuis cette interface. Permet uniquement name, password reset, must_change_password.

Frontend `AdminUsersPage.js` (refonte complete) :
- Titre : "Comptes syndic" (au lieu de "Gestion des utilisateurs").
- Formulaire : email + nom + mot de passe UNIQUEMENT. Plus de selecteur de role. Plus de checkboxes ACPs.
- Banner d'explication "Le syndic gerera lui-meme ses ACPs et son equipe".
- Filtre client : n'affiche que `syndic` + `superadmin` (les gestionnaires sont geres par leur syndic via /team).

Frontend `DashboardPage.js` :
- Bouton "Generer ACP de demo" : `isAdmin` -> `isSuperadmin` (les syndics ne le voient plus).
Backend `routes/demo_seed.py` : `is_admin_role` -> `is_superadmin_only` (403 si syndic essaie).

#### D. Verrous fiscaux complets (P0 finalise)
Demande : "Toute saisie/modification sur exercice ferme refusee."

Application de `fiscal_lock.ensure_period_open` :
- **`routes/invoices.py`** :
  - POST /invoices : verrouille selon `data.date`
  - PUT /invoices/{id} : verrouille selon `existing.date` ET `data.date` (deux periodes)
  - DELETE /invoices/{id} : verrouille selon `inv.date`
- **`routes/fund_calls.py`** :
  - POST / : verrouille selon `data.date`
  - DELETE /{id} : verrouille selon `fc.date`
- **`routes/accounting.py`** :
  - DELETE /entries/{id} : appelle `ensure_entry_modifiable` (verifie aussi `is_reversal`/`reversed`)
  - POST/PUT etaient deja verrouilles (iter precedent)

Message d'erreur clair en francais :
> "L'exercice 'Exercice 2026' (01/01/2026 au 31/12/2026) est cloture. Impossible de saisir/modifier une facture sur une periode verrouillee. Pour modifier, rouvrez d'abord l'exercice via Comptabilite > Exercices fiscaux > Reouvrir (les ecritures de cloture seront automatiquement contre-passees)."

#### E. Bug fix compte Benjamin
Le compte syndic Benjamin etait enregistre sous `bejamin@gep.be` (typo : un 'n' manquant). Correction directe en DB : email normalise a `benjamin@gep.be`, password reset a `Capibara`, copropriete_ids vide pour respect du chinese wall.

#### Tests E2E
- Login Benjamin (Capibara) -> 200 ✅
- Benjamin GET /coproprietes -> ne voit que SES ACPs (1) ✅
- Benjamin GET /api/lots?copropriete_id=<ACP_de_Finlead> -> 403 chinese wall ✅ (10 endpoints testes : lots, invoices, owners, banking, accounting, fund-calls, dashboard, reports, expense-categories)
- Benjamin /dashboard/stats sans param -> tout a 0 (pas de fuite) ✅
- Gerald (superadmin) /coproprietes -> voit tout ✅
- Benjamin cree une ACP -> auto-rattachement OK ✅ (user.copropriete_ids incremente)
- POST /api/invoices date 2026-06-01 (cloture) -> 400 verrou fiscal ✅
- POST /api/invoices date 2027-03-15 (ouvert) -> 200 ✅
- DELETE /api/invoices/<id-2026> -> 400 verrou fiscal ✅
- POST /api/fund-calls date 2026-06-01 (cloture) -> 400 verrou fiscal ✅
- Benjamin POST /api/admin/demo/seed -> 403 (reserve superadmin) ✅



### Iter59 (Feb 2026) - Fix bug : Superadmin peut creer un gestionnaire via /team

#### Bug rapporte
Connecte en tant que superadmin (gerald@gep.be), l'utilisateur ouvre `/team`, clique sur "Ajouter un gestionnaire", remplit le formulaire et obtient l'erreur 400 :
> "Le superadmin doit creer les gestionnaires via le syndic concerne. Connectez-vous en tant que ce syndic ou utilisez /api/admin/users."

Cas d'usage reel : Gerald est superadmin de la plateforme **ET** syndic de sa propre agence (Good Experience Properties). Il a besoin de gerer ses gestionnaires comme n'importe quel syndic.

#### Fix `/app/backend/routes/team.py`
- `_get_syndic()` retourne desormais `(user, effective_syndic_id, is_superadmin_global)`.
- Pour un superadmin : `effective_syndic_id = son propre user.id` (il agit comme syndic de sa propre agence) + flag `is_superadmin_global=True` (permet d'attribuer n'importe quelle ACP et de voir toutes les equipes via `?scope_param=all`).
- `_validate_acps_belong_to_syndic(syndic_id, copro_ids, is_super)` : si superadmin, verifie juste que les ACPs existent ; si syndic sans `copropriete_ids` (all-access), autorise tout ; sinon scope strict.
- POST/PUT/DELETE `/api/team/members` : retiree la garde "Le superadmin doit creer...". Le superadmin peut maintenant creer, editer et supprimer ses propres gestionnaires.

#### Verification
- curl POST `/api/team/members` en superadmin -> 200 + gestionnaire cree avec `parent_syndic_id = superadmin.id`
- E2E playwright : login gerald -> /team -> "Ajouter un gestionnaire" -> "Marie Dupont" cree, toast "Gestionnaire cree" + ligne visible dans le tableau



### Iter58 (Feb 2026) - Refonte PDF Decompte selon le modele FINLEAD (Lot -> Cle -> Compte) + Section dediee locataire

#### Demande utilisateur
"Décompte annuel doit etre completement restructure pour matcher l'exemple FINLEAD : groupement par Cle de repartition, puis Nature/Compte, avec separation claire Occupant vs Proprietaire. Les charges locataire ne doivent PAS etre cachees ; elles doivent etre listees mais correctement totalisees a part du proprietaire."

Reference utilisateur : `Décompte copropriétaire 2025.10 - 2026.09.pdf` (FINLEAD).

#### Refonte du PDF (`pdf_decompte.py`)

**Section 1 : Detail des charges** - structure entierement repensee
- Hierarchie : **Lot → Cle de repartition → Compte (nature)** (au lieu de la precedente Cle → Compte → Facture).
- Tableau unique a **5 colonnes** (modele Finlead) :
  - `Designation` | `Quotites` | `Montant a repartir` | `Part proprietaire` | `Part occupant`
- Lignes :
  - **En-tete Lot** (background slate, fusionne sur 5 colonnes) : "Lot: A-002 [description] (Prorata: 365 / 365 jours)"
  - **En-tete Cle** (background bleu clair, en gras) : "[code] - [name] (total_quotities)" + ses totaux (montant a repartir, part prop, part occ pour ce proprietaire)
  - **Lignes de detail Compte** indentees (slate_500) : "[account_number] - [account_name]" avec les chiffres
  - **Sous-total par Lot** (background slate_100) : "Total Lot X"
  - **Totaux generaux** (background noir) en fin de tableau
- Couleurs colonnes : **Part proprietaire** = bleu #1E40AF, **Part occupant** = ambre #92400E.
- Fix critique : les distribution_keys utilisent le champ **`share`** (pas `quotity`) — lecture corrigee dans `dk_index` builder.

**Section 2 : Recapitulatif des charges locataire** (NOUVELLE section dediee — demande utilisateur 3b)
- Affichee UNIQUEMENT si `total_occupant_share > 0.001`.
- Tableau a 3 colonnes : `Nature de la depense` | `Compte` | `Montant a refacturer au locataire`.
- Header + footer ambres (#92400E), lignes alternees clair/ambre tres clair.
- TOTAL en gras "TOTAL A REFACTURER AU LOCATAIRE".
- Note legale en bas : "Repartition occupant/proprietaire definie conformement aux usages locatifs belges (RD du 12/07/2024 relatif aux charges locatives). A confronter avec les stipulations particulieres du bail."

**Renumerotation des sections :**
- Section 1 = Detail des charges
- Section 2 = Recapitulatif charges locataire (nouveau)
- Section 3 = Vos appels de fonds (ex-Section 2)
- Section 4 = Vos paiements (ex-Section 3)
- Section 5 = Modalites de paiement

#### Tests
- Fichier `/app/backend/tests/test_iter33_decompte_finlead.py` : 7/7 PASS
- Couverture :
  - test_pdf_grouping_lot_key_account : hierarchie Lot → Cle → Compte
  - test_pdf_quotites_displayed_correctly : "1200.00 / 2950.00" (au lieu de "0.00 / 0.00")
  - test_pdf_recap_locataire_section_present_when_occupant : section visible si occupant_pct > 0
  - test_pdf_recap_locataire_hidden_when_no_occupant : section masquee sinon
  - test_pdf_columns_show_part_proprietaire_and_occupant : 5 colonnes Finlead
  - test_pdf_section_numbering : numerotation 1-2-3-4
  - test_pdf_multiple_distribution_keys : plusieurs cles dans un meme lot

#### Bouton "Supprimer TOUS les appels" (Bulk Delete) — VERIFIE deja implemente
- Endpoint `POST /api/fund-calls/delete-all?copropriete_id=X` deja operationnel (iter49).
- Bouton UI `[data-testid="delete-all-calls-btn"]` deja en place dans `FundCallsPage.js` (L154-161).
- Affiche uniquement quand `calls.length > 0`. Confirmation via window.confirm + message detaille.



### Iter57 (Feb 2026) - Reouverture extourne les regularisations + Vue Journaux avec contre-passations + Decompte enrichi

#### 1. Reouverture d'exercice = CONTRE-PASSATION
Demande utilisateur : "lorsqu'on reouvre un exercice il faut extourner les regularisations de cloture. Toutes les suppressions sont des contre-passations, rien n'est definitivement supprime."

Avant : `POST /fiscal/years/{id}/reopen` ne faisait que repasser le statut a 'open'. Les ecritures de cloture restaient figees -> les soldes etaient incorrects (double-passe).

Apres : pour chaque OD de regularisation (`is_regularization=True`) ET ecriture AN, le systeme cree une **contre-passation** :
- Reference : `EXT-{orig_reference}` (ex: `EXT-OD-REG-PROV`)
- Lines : Dr/Cr inverses ligne par ligne
- Flag `is_reversal=True` + `reverses_entry_id` pointant vers l'originale
- L'originale est marquee `reversed=True` + `reversed_at` + `reversed_by_entry_id`
- Filtre `$or [reversed, is_reversal]` exclut les deja-traitees pour eviter les doubles passes
- Test : close 2026 -> 3 entries, reopen -> 3 contre-passations, status='open'

#### 2. Vue journaux avec checkbox "Inclure les contre-passations"
- Backend : `GET /api/accounting/entries?include_reversals=true|false` (defaut false = vue active uniquement, filtre `reversed=True` ET `is_reversal=True`).
- Frontend JournalsPage : checkbox `include-reversals-toggle` a cote des tabs. Affichage conditionnel :
  - Badge `Contre-passation` (ambre) sur les entries `is_reversal=True`
  - Badge `Extournee` (rouge) sur les entries `reversed=True`
  - Ligne extournee : `bg-red-50/30 line-through opacity-70`
  - Boutons edit/delete caches sur les entries reversed ou de reversal (principe d'immutabilite audit).

#### 3. PDF Decompte annuel enrichi
Demande : "il faut un detail des depenses par cle - Nature - compte permettant aux proprietaires de lire un decompte clair... le detail des frais pris en charge par les occupants et le proprietaire avec une vue claire et lisible".

Changements `pdf_decompte.py` :
- **Compte PCMN explicite** devant le nom de nature : `[614000] Nettoyage`
- **2 nouvelles colonnes** par ligne facture : Occupant (ambre #92400E) + Proprio (bleu #1E40AF)
- **Sous-totaux par nature** : 3 colonnes Votre part / Occupant / Proprio
- **Recap visuel global** "Repartition de vos charges" si total_occupant_share > 0 :
  - Carte ambre = Part occupant (refacturable au locataire) + EUR + %
  - Carte bleue = Part proprietaire (definitive)
- Calcul : `owner_occ = owner_amt * occupant_pct / 100`, `owner_prop = owner_amt - owner_occ`

#### Tests
- iter_32 : 4/4 backend pytest PASS + 100% frontend.
- Fichier permanent : `/app/backend/tests/test_iter32_reopen_reversals.py`.

#### Architecture / Backlog principle
Le principe "rien n'est definitivement supprime, tout est contre-passation" est partiellement implemente :
- ✅ Reouverture d'exercice : contre-passation
- ⚠️ Autres deletes (fund_calls, invoices, OD manuelles, bank txns) : DELETE encore present
- Backlog : etendre le pattern a toutes les deletions comptables (refacto important - prevu pour iter futur).

### Iter56 (Feb 2026) - Auto-match par NOM + Solde d'ouverture auto-rempli

#### Auto-lettrage : 4 niveaux de fallback
Probleme : `_try_auto_lettrage_vcs` ne tentait QUE la VCS. Pour les transactions sans VCS (manuelles, anciens formats), aucun match -> compte d'attente 499.
Fix : nouvelle cascade dans cet ordre :
1. **VCS** (regex `(\d{3})[\s/]*(\d{4})[\s/]*(\d{5})` -> 12 chiffres VCS belge)
2. **Nom owner exact** (counterparty_name match case-insensitive contre owner.name)
3. **Nom owner partiel** (last_name OU permutations First/Last)
4. **Nom supplier exact** (uniquement pour les paiements sortants debit)
5. **Numero de facture** dans counterparty_name OU communication (cherche dans les factures impayees de l'ACP, lettre + marque la facture comme paid)

Validation : `counterparty_name="De Smet Catherine"` + communication vide -> auto-matched a l'owner De Smet via le niveau 2.

#### Solde d'ouverture auto-rempli (nouvel endpoint)
Demande : "Lors de la creation d'un extrait le solde precedent du compte bancaire doit apparaitre dans 'solde d'ouverture'"

Backend :
- Nouvel endpoint `GET /api/banking/statements/previous-closing?account_number=IBAN&copropriete_id=X` retourne `{balance, source, previous_statement_*}`.
- `source` peut etre : `posted` (figeable), `draft_computed` (calcule depuis les mouvements du draft precedent), ou `none` (premier extrait).
- `POST /api/banking/statements` : si `opening_balance` est 0 (ou non fourni) ET un IBAN est specifie, auto-rempli depuis le dernier extrait. Stocke `opening_balance_source` pour audit.

Frontend (BankingPage) :
- Quand l'utilisateur selectionne un IBAN dans le dialog "Nouvel extrait", appel API `/previous-closing` -> solde d'ouverture pre-rempli automatiquement.
- Affichage de la **source** sous le selecteur : vert = posted ("Solde repris de l'extrait #X du JJ/MM"), ambre = draft (warning), bleu = aucun extrait precedent.
- L'utilisateur peut surcharger manuellement.

Verification :
- IBAN Test : extrait precedent posted -> auto-fill 25237.70 EUR
- IBAN Demo : extrait precedent draft -> calcul live 200 EUR + warning
- IBAN inconnu : 0 EUR, source `none`

### Iter55 (Feb 2026) - Numero de facture dans le libelle lors du lettrage

#### Demande
Lors du lettrage d'une transaction bancaire a une facture (match_type='invoice'), le libelle de l'ecriture FI generee doit explicitement mentionner le **numero de facture** (pour traçabilite, lecture rapide dans les journaux et grand livre, et exports comptables).

#### Avant le fix
- Description : `"<nom contrepartie> - <communication>"` (souvent vide ou peu informatif)
- Ligne tier : pas de line_description

#### Apres le fix
- Description ecriture : `"Paiement facture 2026/007 - Pierre Dardenne"`
- Ligne tier (compte 44000XXX fournisseur) : `line_description = "Paiement facture 2026/007 - Pierre Dardenne"` -> apparait directement dans la balance des tiers et la situation de compte.
- Champ `invoice_number` stocke sur le journal_entry pour les reporting futurs.

#### Implementation
- `auto_entries.generate_bank_entry` : extraction `invoice_number` de l'invoice lors du match_type=='invoice'. Construction d'une `description` et d'une `line_description` enrichies.
- Fallback si supplier non trouve en base : on garde le nom de la facture comme contrepart_name (avant : la ligne etait abandonnee).

#### Verification
Test ACP Demo : facture #2026/007 (Pierre Dardenne, 265 EUR) lettree -> description = "Paiement facture 2026/007 - Pierre Dardenne" verifie en BDD.

### Iter54 (Feb 2026) - Contrepartie EXPLICITE prime sur l'auto-VCS

#### Probleme reel observe
Sur le terrain, un utilisateur peut saisir une transaction avec un **mauvais VCS** (typo, copier-coller errone), tout en ayant **explicitement selectionne le bon proprietaire** via le widget CounterpartySearchSelect. L'ancienne logique ignorait cette selection explicite et reposait uniquement sur le VCS de la communication. Resultat constate sur l'ACP Test : "Peeters Luc" saisi avec VCS de Lefevre -> l'auto-VCS lettrait sur Lefevre (ex-prop) -> Peeters restait debiteur a 5200 EUR + Lefevre apparaissait artificiellement comme crediteur.

#### Fix
1. **Modele etendu** `TransactionInput` + `InlineLineInput` : nouveaux champs `counterparty_id` + `counterparty_type` ('owner'|'supplier').
2. **Nouveau helper backend** `_try_explicit_match_then_vcs(txn)` :
   - PRIORITE 1 : si `counterparty_id` + `counterparty_type` fournis (selection explicite UI) -> lettrage direct, `matched=True`, ecriture FI generee contre cet ID.
   - PRIORITE 2 (fallback) : auto-VCS classique (regex 12 chiffres VCS belge).
3. Remplacement de `_try_auto_lettrage_vcs` par `_try_explicit_match_then_vcs` dans tous les flux : `POST /transactions`, `PUT /transactions/{id}`, `add-lines`, `post_statement`.
4. **PUT /transactions/{id}** : si l'utilisateur (re)selectionne explicitement une contrepartie -> reset le matched existant et FORCE le re-match avec le nouveau ID.
5. **Frontend BankingPage** : `CounterpartySearchSelect.onSelect` propage maintenant `item.id` + `type` dans le state (inline lines + edit form). Stockes en BDD et envoyes au backend.

#### Verification
ACP Test apres re-saisie correcte des transactions :
- Balance des tiers : 5 proprietaires, chacun a 3200 EUR (4200 debit - 1000 paye)
- Total debiteurs : 21000 EUR (5x4200)
- Total crediteurs : 0 EUR (plus aucun ex-prop avec faux credit)
- Tous les FI generes pointent sur les bons comptes 40000XXX

### Iter53 (Feb 2026) - Repartition occupant/proprietaire sur factures + OD (preparation decompte locataire)

#### Objectif
Preparer la generation future du **decompte locataire annuel** : chaque charge (facture ou OD) peut porter une repartition explicite entre la part **occupant** (locataire) et la part **proprietaire**. Conserve les % a la source pour calculer plus tard la quote-part refacturable au locataire.

#### Modeles enrichis
- `ExpenseCategory` : nouveaux champs `default_occupant_pct` + `default_proprietaire_pct` (somme = 100, valide cote backend en POST/PUT).
- `Invoice` : nouveaux champs `occupant_pct`, `proprietaire_pct`, `occupant_amount`, `proprietaire_amount`. Si `occupant_pct` non fourni : herite automatiquement de la nature de depense (`expense_category.default_occupant_pct`), sinon 0%.
- `JournalEntryLine` (OD) : nouveaux champs `occupant_pct` + `proprietaire_pct` (Optional). Helper `_enrich_lines_with_occupant_pct` qui :
  - Pour les lignes de classe 6/7 (charges) : auto-herite du default de la nature de depense liee au compte (0%/100% si aucune mapping).
  - Pour les lignes non-charge (banques, tiers, fonds propres) : laisse None (pas de repartition applicable).
  - Si l'un des 2 est fourni : auto-complete l'autre. Si les 2 sont fournis et somme != 100 : 400.

#### UI (3 pages mises a jour)
- **ExpenseCategoriesPage** : nouveau bloc "Repartition par defaut" avec 2 champs % (data-testid `cat-occupant-pct` / `cat-proprietaire-pct`). Auto-complete : `setOccupant` met a jour proprietaire = 100-v et inversement.
- **InvoicesPage** : nouvelle section ambree "Repartition occupant/proprietaire" (data-testid `invoice-occupant-section`). 2 champs auto-complete + affichage temps reel des **montants calcules** (Part occupant / Part proprietaire). Pre-remplissage automatique lors de la selection d'une nature de depense.
- **JournalsPage** : 2 nouvelles colonnes %Occ / %Prop dans le tableau d'edition des lignes OD. Affichees UNIQUEMENT pour les lignes de classe 6/7 (charges) - les autres lignes (banque, tiers) affichent un tiret. Pre-rempli automatiquement quand on choisit un compte rattache a une nature de depense.

#### Defaults / heritage
1. Nature de depense (defaut configurable au niveau de la categorie - ex. "Chauffage commun" = 100% occupant, "Toiture" = 100% proprio).
2. Si pas de defaut sur la categorie : 0% occupant / 100% proprietaire (charge a 100% pour le proprio).
3. Surchargeable au cas par cas dans chaque facture / OD.

#### Tests
- iter_31 : 13/13 backend pytest PASS + 100% frontend valide.
- Fichier de regression permanent : `/app/backend/tests/test_iter31_occupant_proprietaire.py`.

### Iter52 (Feb 2026) - Auto-VCS robuste + Balance des tiers en temps reel

#### Auto-lettrage VCS plus robuste (auto-comptabilisation des paiements)
Probleme : la fonction `_try_auto_lettrage_vcs` faisait `comm.replace('+','').replace('/','').replace(' ','')` puis matchait l'ENTIERE chaine au `vcs_digits`. Pour une communication realiste `'+++100/7407/40231+++ - Votre paiement au 19/06/2026'`, le clean donnait `100740740231-Votrepaiementau19062026` qui ne matchait jamais `'100740740231'`.
Fix : extraction VCS via regex `(\d{3})[\s/]*(\d{4})[\s/]*(\d{5})` pour isoler les 12 chiffres VCS belges peu importe le suffixe. Fallback : prendre les 12 premiers digits.
Validation test ACP : 5/5 transactions auto-lettrees au post_statement sans intervention manuelle (De Smet, Dubois, Janssens, Lefevre, Martin Sophie).

#### Auto-lettrage execute aussi a la comptabilisation
`post_statement` lance d'abord l'auto-VCS sur toutes les txns non matched, PUIS genere les ecritures FI. Resultat : les transactions avec VCS valides sont directement comptabilisees au bon compte tier (40000XXX) sans avoir a lettrer manuellement.

#### Modification de transaction = mise a jour automatique de la balance des tiers
Probleme : si l'utilisateur modifiait une transaction (montant, date, communication) d'un extrait deja comptabilise, l'ecriture FI restait inchangee -> balance des tiers et bilan obsoletes.
Fix : nouveau helper `_refresh_fi_if_posted(txn_id)` qui regenere l'ecriture FI (via `generate_bank_entry` qui supprime d'abord l'ancienne). Appele depuis :
- `POST /api/banking/transactions` (creation)
- `PUT /api/banking/transactions/{id}` (modification)
- `POST /api/banking/unlettrage/{id}` (deja en place)
Validation : modif amount 1000 -> 1500 sur txn De Smet -> balance prov passe de 3200 a 2700 EUR automatiquement.

### Iter51 (Feb 2026) - Bilan dynamique : compte bancaire + paiements visibles via compte d'attente 499000

#### Probleme
Quand l'utilisateur "Comptabilise" un extrait bancaire avec des transactions NON lettrees (ex: 5x1000 EUR avec communications VCS non reconnues), AUCUNE ecriture FI n'etait creee. Resultat : le compte bancaire et les 5000 EUR encaisses n'apparaissaient PAS dans le bilan.
Cause racine : `generate_bank_entry` (auto_entries.py L359-360) faisait un early-return si `txn.matched == False`. Et `post_statement` ne generait aucune ecriture - il se contentait de passer le statut a "posted".

#### Fix
- `auto_entries.generate_bank_entry` : suppression de l'early-return sur transactions non matched. Si aucun counterpart resolu, fallback sur le compte d'attente PCMN `499000 "Encaissements / Decaissements non identifies"` (auto-cree dans le pcmn de l'ACP si manquant).
- `routes/banking.post_statement` : apres validation de l'equilibre, boucle sur les transactions et appelle `generate_bank_entry` pour chacune (matched ou pas). Retourne `fi_entries_created` + `fi_errors`.
- `routes/banking.unpost_statement` : supprime TOUTES les ecritures FI auto-generees liees aux transactions de l'extrait avant de repasser draft.
- `routes/banking.unlettrage/{txn_id}` : si l'extrait est `posted`, regenere l'ecriture FI en mode "compte d'attente 499000" apres le delettrage (sinon le compte bancaire disparait du bilan).

#### Verification
Test ACP (5x1000 EUR comptabilises) :
- Bilan AVANT : 0 EUR sur compte bancaire (bug)
- Bilan APRES : ACTIF Banque 55143100 = 5000 EUR + PASSIF Compte d'attente 499000 = 5000 EUR
- Cycle lettrage manuel txn -> owner : FI passe de Dr Banque / Cr 499000 a Dr Banque / Cr 40000005 (proprietaire) automatiquement.
- Cycle delettrage : FI revient automatiquement a Dr Banque / Cr 499000.
- Equilibre Actif=Passif preserve a chaque etape.

### Iter50 (Feb 2026) - Lockdown Gestion utilisateurs + Self-profile + Budget revoke cascade + Edit ACP

#### Verrouillage de la gestion utilisateurs au superadmin SEUL
- Le role `syndic` (qui a "acces total" via `is_admin_role()`) pouvait creer/modifier/supprimer d'autres utilisateurs. Risque : un syndic pouvait creer un autre superadmin.
- Nouveau helper `is_superadmin_only(role) -> bool` (server.py L178) : retourne True uniquement pour `superadmin`/`admin`.
- Tous les endpoints `/api/admin/users` (GET, POST, PUT, DELETE) appellent desormais `_get_superadmin_only`. Test : un user `owner` recoit 403 "Seul un super administrateur peut gerer les acces a la plateforme".
- AuthContext frontend : ajout du flag `isSuperadmin` (= superadmin uniquement).
- Layout sidebar : le lien "Utilisateurs" (data-testid='nav-admin-users') est conditionne par `isSuperadmin`. Cache pour les syndics.
- AdminUsersPage : banner "Acces reserve au super administrateur" pour les non-superadmin avec lien vers `/profile`.

#### Nouvelle page "Mon profil" (/profile)
- Endpoint `PUT /api/auth/me` : tout utilisateur authentifie peut modifier SON nom + mot de passe.
- Validations : new_password >= 6 chars, current_password requis pour changement, verify_password, re-fetch du hash depuis DB (puisque `get_current_user` le pop).
- Page `ProfilePage.js` avec 3 cartes : Identite/role read-only (badge couleur selon role), Nom modifiable, Mot de passe avec confirmation + auto-logout post-change.
- Lien sidebar "Mon profil" visible pour TOUS les utilisateurs authentifies (data-testid='nav-profile').

#### Devalidation budget = contrepassation cascade
- `POST /api/fiscal/budgets/{id}/revoke` ne se contentait plus de remettre le statut en `draft`.
- Desormais : supprime TOUS les fund_calls lies (provisions, reserve, roulement, special) ET leurs ecritures comptables VE auto-generees (via `_delete_auto_entries`).
- Protection : si au moins UN appel a recu un paiement, retourne 400 avec message explicite. Le UI propose `force=true` pour passer outre.
- `force=true` : delettre les bank_transactions liees (matched_fund_call_id) puis cascade complete.
- UI `FiscalYearPage.revokeBudget` : dialog confirm enrichi (mentionne provisions/reserve/roulement) + retry automatique avec force si 400 sur paiements.
- Retour API : `{deleted_fund_calls, preserved_paid_calls, unlettred_transactions, message}`.

#### Edition d'une ACP plus visible
- Le bouton "Modifier" dans CoproprietesPage etait un `ghost` icon-only difficile a reperer.
- Refonte : `outline` bleu (#0055FF) avec icone `Pencil` + texte "Modifier" + data-testid `edit-copro-{id}`. Le dialog s'ouvre pre-rempli (name, BCE, address, bank_accounts, defaults provisions). Endpoint PUT /api/coproprietes/{id} (existant) preserve `pcmn_number` des comptes bancaires.

#### Tests
- iter_30 : 17/17 backend pytest PASS + 100% frontend PASS.
- Couverture : RBAC complet (401/403/200), validation mot de passe (5 cas), revoke cascade sur ACP temporaire, edit ACP avec preservation pcmn_number, sidebar et banners.

### Iter49 (Feb 2026) - Libelle PAR LIGNE dans la situation de compte + Bypass syndic + Bulk delete + Regenerate

#### Fix critique : libelle par ligne (provisions/reserve/roulement)
- Bug : dans la situation de compte d'un proprietaire, lorsqu'un appel de fonds avait un mode mixte (provisions + reserve + roulement dans la meme ecriture VE), TOUTES les lignes affichaient le meme libelle "Appel de provisions" car la description etait au niveau de l'ECRITURE. Resultat : impossible de distinguer visuellement les contributions par type.
- Fix `auto_entries.generate_sale_entry` : chaque ligne d'une ecriture VE recoit desormais un champ `line_description` differencie :
  - Provisions => "Appel de provisions - <nom>"
  - Reserve => "Appel fonds de reserve - <nom>"
  - Roulement => "Appel fonds de roulement - <nom>"
- Fix `routes/reports.py::balance_tiers_owner_detail` (UI) et `situation_compte_pdf` : `ln.get('line_description') or e.get('description','')` -> chaque mouvement affiche le bon libelle.
- Fix `pdf_situation_compte._humanize_label` : evite la redondance "Appel de fonds : Appel de provisions" en detectant si la description commence deja par "Appel ".

#### Bypass syndic dans Dashboard
- Bug "Sante comptable indisponible : Acces refuse a cette copropriete" sur le dashboard pour les utilisateurs `syndic`.
- Cause : `GET /api/dashboard/health-audit` et `/stats` n'autorisaient le bypass que pour `superadmin`/`admin`, alors que la fonction helper `is_admin_role()` (server.py L176) inclut bien `syndic` ("Syndic et superadmin: acces total").
- Fix : utilisation de `is_admin_role(role)` au lieu de la liste hardcodee. Les syndics avec `copropriete_ids: []` peuvent maintenant acceder a toutes les ACPs.

#### Nouveau endpoint : POST /api/fund-calls/regenerate-entries
- `POST /api/fund-calls/regenerate-entries?copropriete_id=X` regenere les ecritures comptables VE auto-generees de tous les appels de fonds d'une ACP avec le bon libelle differencie par type.
- Chinese walls : 400 si copropriete_id absent ou "all".
- Preserve `manually_edited=true` (jamais touchees).
- UI : bouton bleu "Regenerer ecritures" dans `FundCallsPage` [data-testid='regenerate-entries-btn'].

#### Bouton "Supprimer TOUS les appels"
- `POST /api/fund-calls/delete-all?copropriete_id=X` (deja existant, confirme operationnel).
- UI : bouton rouge "Supprimer tous les appels" [data-testid='delete-all-calls-btn'] avec confirmation.

#### Verification
- Demo ACP : 6/6 ecritures regenerees. Situation de compte De Smet Catherine affiche correctement :
  - "Appel de provisions - Trimestriel 1/4..." (40000007)
  - "Appel fonds de reserve - Fonds de reserve - Annuel 1/1..." (40010007)
  - "Appel fonds de roulement - Fonds de roulement - Annuel 1/1..." (40000007)
- PDF Situation : libelles propres sans double-prefixe.
- Testing agent iteration_29 : 10/10 backend PASS + 100% frontend PASS.



### Iter48 (Feb 2026) - Description claire des appels (PDF + ecritures)

#### Fix : description differenciee selon call_type
- Avant : toute ecriture d'appel avait description "Appel: <nom>" - sans difference visible entre provisions/reserve/roulement/special. Le PDF Situation affichait donc "Appel de fonds : Trimestriel 1/4" meme pour un appel de **Fonds de reserve** ou **Fonds de roulement** (confusion).
- Apres : `generate_fund_call_entries` genere desormais la description suivant le type :
  - provisions → `"Appel de provisions - <nom>"`
  - reserve → `"Appel fonds de reserve - <nom>"`
  - roulement → `"Appel fonds de roulement - <nom>"`
  - special → `"Appel special - <nom>"`
- Le label apparait correctement dans :
  - PDF Situation de compte (`_humanize_label` preserve la description)
  - PDF Decompte annuel (label par type deja en place)
  - Balance des tiers (mouvements de l'owner affiches avec description complete)



### Iter47 (Feb 2026) - Appels de fonds clarifies

#### UI clarification des types d'appels
Nouveau systeme visuel pour distinguer les 4 types d'appels :
- **Provisions** (bleu, icone Banknote) : avances trimestrielles/annuelles sur charges
- **Fonds de reserve** (violet, ShieldCheck) : gros travaux a venir (toiture, ascenseur)
- **Fonds de roulement** (vert emeraude, Wallet) : tresorerie permanente
- **Appel special** (orange, AlertTriangle) : depense exceptionnelle hors budget

#### Changements
- Cards d'appels : badge colore + border gauche teintee + montant en evidence
- Dialog "Nouvel appel" : Select avec icones + tooltip pedagogique sous chaque option
- Panneau detail : badge type + bandeau pedagogique colore expliquant l'usage
- Ajout de "Fonds de roulement" dans le Select (manquant precedemment)
- Description claire de chaque type pour eviter la confusion entre reserve / roulement



### Iter46 (Feb 2026) - Carte Sante toujours visible + retry

#### UI Dashboard : carte sante robuste
- Avant : `{health && (...)}` masquait totalement la carte si l'API echouait ou pendant le chargement.
- Apres : carte TOUJOURS rendue quand une ACP est selectionnee, avec 3 etats :
  - **Loading** : spinner + "Calcul de la sante comptable..."
  - **Erreur** : message rouge + bouton "Reessayer" (`data-testid="health-retry"`)
  - **Succes** : score, tiles, anomalies
- Logs explicites dans la console pour debug en cas d'erreur reseau.
- `loadHealth()` factorise en `useCallback` + dependance `[selectedCopro]` pour reload automatique au changement d'ACP.



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
- **iter84: 25/25 (Mutation - Split OD par date : 4/4 / Detection+fusion doublons admin chinese-wall : 5/5 / CODA preview+mapping UI : 3/3 / regressions iter79+82+83 : 13/13)**
  - `_compute_mutation_breakdown` enrichi avec `call_date` dans current_period_details
  - `_apply_to_lot` ecrit 1 OD `fonds_roulement` datee `sale_date` + N OD `prorata` datees call_date d'origine (agregees par date)
  - `mutation_record.journal_entry_ids` (liste) et `entries_created` (breakdown kind/date/amount)
  - Cancellation supprime toutes les ecritures via `journal_entry_ids` (compat legacy single id preservee)
  - Routes `/api/admin/duplicates/{suppliers,owners,users}` (GET) + `/api/admin/duplicates/owners/merge` (POST) avec chinese wall syndic-scope
  - UI `AdminDuplicatesPage.js` 3 tabs (Fournisseurs/Proprietaires/Utilisateurs) + ACP picker + selection KEEP + bouton Fusionner cards 3-col
  - Composant `SupplierSearchSelect` autocomplete dans facture (liste filtree des fournisseurs deja utilises, deduplique, creation fiche en ligne avec verif BCE doublon)
  - Frontend LotsPage : colonne "Date appel" ajoutee dans preview prorata + note explicative
  - Routes `/api/banking/coda/preview` (parse + suggestions match VCS/IBAN/nom + hash SHA256) et `/api/banking/coda/import-confirmed` (persiste avec overrides utilisateur + auto-lettrage)
  - Composant `CodaImportDialog.js` mapping UI : header balances + stats + filtres + table mouvements avec override Type+Entite par ligne + checkbox include + verif equilibre delta
  - Detection doublon CODA : SHA256 par ACP, bandeau d'alerte si re-import, blocage idempotence backend (409)

## Backlog P1
- Gestion AG (ordre du jour, votes, PV, convocations)
- Notifications email (Resend) lors des nouveaux appels de fonds
- Generateur PDF "Decompte de mutation" (document notarial reprenant les 3 blocs FR + prorata + futurs)

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

---

## iter90au — Module Communication (Feb 2026)

### Feature livree
Interface complete pour l'envoi d'emails aux proprietaires avec PDF attaches.

**Backend (`/api/communication/*`)**
- `GET /mailboxes` : liste des boites autorisees du cabinet (heritage `parent_syndic_id`)
- `POST/DELETE /mailboxes` : gestion (syndic/admin uniquement)
- `GET/PUT /signature` : signature HTML per-user
- `GET /owners-balances?copropriete_id=X` : proprietaires + solde pour selection
- `POST /send/situation` : PDF situation compte + envoi Graph (dry-run si `MAIL_ENABLED != 'true'`)
- `POST /send/decompte` : PDF decompte annuel + envoi
- `POST /send/mutation` : PDF decompte mutation + envoi (emails saisis manuellement)
- `POST /send/generic` : email libre + PJ PDF (multipart)

**Helpers module-level extraits** (reutilisables backend)
- `reports.py::_build_situation_compte_pdf(db, owner_id, copropriete_id, ...)`
- `reports.py::_build_decompte_annuel_pdf(db, owner_id, copropriete_id, fiscal_year_id, preview=True)`
- `reports.py::_compute_balance_tiers_for_ui(db, copropriete_id)`
- `properties.py::_build_mutation_decompte_context(db, lot_id, mutation_id)`

**Frontend** : `/app/frontend/src/pages/CommunicationPage.js`
- Tabs "Envois" / "Email libre" / "Boites & signature"
- Multi-select proprietaires avec badges de balance colores (rouge/vert)
- 4 filtres (Tous, Debiteurs, Crediteurs, Avec email)
- Composer email libre avec upload PDF
- MailboxesSection (cabinet-scoped, syndic-only pour edition)
- SignatureSection (per-user, HTML + apercu)

**Tests** : `/app/backend/tests/test_iter90au_communication.py` (6/6 pass) +
`test_iter90au_extra_communication.py` (testing agent, 5/5 pass) = **11/11 verts**.

**Correctifs P0 dans le meme iter**
- Fix build backend casse par refactor precedent : `_end_helper_pdf` dangling function retirée,
  `_build_situation_compte` remonte au niveau module dans `reports.py`.
- Fix E741 (variable `l` ambiguë) et F401 (`timedelta` inutilise) dans `reports.py`.

### Chinese Wall
- Chaque cabinet (`user.role=syndic`) gere sa liste `authorized_mailboxes`.
- Les gestionnaires enfants (`parent_syndic_id`) heritent en lecture seule.
- Chaque user a sa signature perso (`signature_html`). Modifier la sienne ne change pas celle des autres.
- Les envois cross-cabinet sont bloques (403).


---

## iter90aw + iter90ax + iter90ay (Feb 2026)

### iter90aw — Modeles emails reutilisables
- 4 templates par defaut (relance amiable J+15, mise en demeure J+45, accuse reglement, envoi decompte)
- Variables dynamiques : `{owner_name}`, `{abs_balance}`, `{copropriete_name}`, `{vcs_code}`, `{iban}`, `{today}`, etc.
- CRUD complet + preview + override des defaults
- Page `/email-templates` + dropdown template dans les dialogues d'envoi de `/communication`
- Endpoints `/api/email-templates/*` (list/create/update/delete/preview/variables)

### iter90ax — Systeme de backup ACP
- Backup quotidien automatique 00h00 Europe/Brussels via APScheduler
- ZIP par ACP contenant : copropriete + owners + 13 collections scopees (lots, journal_entries, invoices, ...)
- Stockage GridFS `acp_backups` avec index `backups_index`
- Retention : 30 quotidiens + 12 mensuels + tous les manuels par ACP
- Page admin `/admin/backups` : liste + stats + trigger manuel + download + restore (dry-run)
- Endpoint syndic `/coproprietes/{id}/archive-download` : ZIP structure par annee fiscale (CSV + PDF optionnels)
- Bouton "Telecharger archive" dans `/coproprietes` (icone Download, tous statuts)

### iter90ay — Anti-doublon soft contournable
- Regle 1 (meme numero fournisseur) : blocage DUR meme avec force=true
- Regle 2 (montant+fournisseur+date proches) : blocage SOFT contournable via ?force=true
- Message backend prefixe `[SOFT_DUPLICATE]` pour differencier
- Frontend `saveInvoice` intercepte le 409, propose "Enregistrer quand meme ?", retry avec force=true
- Tests : 3/3 nouveaux + 6 existants iter90ao restent verts

### Preconfiguration MS Graph
- Finlead Properties (welcome@goodexperienceproperties.be) + gerald@gep.be
- Tenant `5b245644-0340-4928-b1a9-d83441344aeb`, Client `f41c7b44-f2c2-4e1e-94cf-5d71fdda63bb`
- Client secret chiffre AES/Fernet en base (EMAIL_CONFIG_KEY)
- Token Azure valide, boite `welcome@...` marquee `default=true` pour les 2 users

