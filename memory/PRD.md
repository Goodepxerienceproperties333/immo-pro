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
