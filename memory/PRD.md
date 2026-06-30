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
