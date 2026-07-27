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
- import_wizard.py (>3500 lignes) a decouper
- reports.py (logique PCMN complexe) a simplifier
