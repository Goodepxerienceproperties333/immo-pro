# NextGe Copro — PRD

## Problem Statement
Application de gestion de copropriété (droit belge, PCMN) avec RBAC strict,
cloisonnement Chinese Wall, imports CODA/Optipro, verrous fiscaux,
multi-syndic, portail propriétaire, communication email/postal, superadmin
billing.

## Société éditrice
- **Good Experience Properties SRL** — SRL de droit belge
- Siège : Rue René Sacré 33, 1367 Ramillies (Belgique)
- BCE : BE 1028.571.469 — IPI : 517595
- Gérant : Gérald Evrard
- Contact : welcome@goodexperienceproperties.be

## Environments
- Preview (dev)
- Production : https://immo-pcmn.emergent.host

## Recent changes (Feb 2026)
- **2026-02-06 (iter94h) SPRINT 1 OpenBanking** : integration Enable Banking
  (PSD2 Belgique) - UI de connexion bancaire. Nouvelle route backend
  `/api/banking/openbanking/*` (aspsps, authorize/start, callback,
  sessions, sessions/{id}/accounts). Nouveau composant frontend
  `OpenBankingConnectDialog` + bouton vert "Connecter banque" sur la page
  Banking. Auth via JWT RS256 signe (kid=APP_ID, iss=enablebanking.com,
  aud=api.enablebanking.com, cle privee RSA 4096 stockee dans
  /app/backend/secrets/enablebanking_private.pem, chmod 600).
  Callback exempte de l'auth cookie (state one-shot CSRF-safe).
  Collections MongoDB : openbanking_states (short-lived 15min) et
  openbanking_sessions (90 jours consent PSD2).
  Testé : JWT signe OK, /aspsps?country=BE retourne 2 banques sandbox
  (BBVA, Mock ASPSP), UI modal fonctionnelle.
  **Coût prod** : ~0,45€ HT / compte / mois (Enable Banking) apres KYB.
  **Sprints suivants** : Sprint 2 (sync auto transactions), Sprint 3
  (mapping PCMN + suggestions lettrage), Sprint 4 (renouvellement 90j),
  Sprint 5 (alertes + monitoring).
- **2026-02-06 (iter94g)** : Dossier comptable enrichi.
    - **`02_Balance/balance_pcmn.pdf`** ajouté : PDF paysage A4 avec table
      Compte / Libelle / Total Débit / Total Crédit / Solde Débit / Solde
      Crédit + ligne TOTAUX (styles Ellevate #022D52). Généré depuis
      `/api/reports/balance` JSON via reportlab.
    - **`08_Decomptes/`** ajouté : un PDF individuel par propriétaire de
      l'ACP (endpoint `/api/reports/decompte/pdf/{owner_id}`). Passe
      `preview=true` si l'exercice n'est pas clôturé (décompte
      prévisionnel). Testé sur Agathe : **37 décomptes générés**
      (14 KB à 28 KB chacun). Nom fichier basé sur `owner.name` sanitized.
- **2026-02-06 (iter94f)** : Dossier comptable complet en ZIP. Nouvel
  endpoint `GET /api/coproprietes/{id}/dossier-comptable.zip` qui agrege
  TOUS les rapports comptables standards via appels HTTP internes
  (ASGITransport) - pas de duplication de code. Structure du ZIP :
    - `01_Bilan/` bilan avant + après répartition (PDF) + bilan.xlsx
    - `02_Balance/` balance tiers proprios (PDF + XLSX)
    - `03_Journaux/` journal_all.csv + PDF/CSV séparés par type
       (AN, OD, VEN, ACH, FIN)
    - `04_Grand_Livre/` grand livre XLSX détaillé par compte
    - `05_Cles_Repartition/` CSV lisible + JSON avec quotités par lot
    - `06_Factures/` liste_factures.pdf + detail_depenses.pdf
    - `README.txt` index + avertissements
  Bouton "Dossier comptable complet (ZIP)" rouge sur la page Reports.
  Fallback dates : si pas d'exercice fiscal ouvert, prend les 12 derniers
  mois. Testé sur ACP Agathe → **22 fichiers, 785 KB, généré en 5.9 s**
  (bilan avant/après, journaux AN/OD complets avec 194 KB CSV, grand
  livre 220 KB, factures + dépenses).
- **2026-02-06 (iter94e)** : Export PDF de synthèse des backups ACP. Nouvel
  endpoint `GET /api/admin/backups/{backup_id}/download-pdf` qui génère un
  PDF paysage A4 : page de garde (nom ACP + stats) + une section par
  collection principale (Propriétaires, Lots, Factures, Écritures, Appels
  de fonds, Extraits bancaires, Transactions, Fournisseurs, Exercices, Clés
  de répartition, Catégories, Mutations). Colonnes clés uniquement, texte
  tronqué à 90 char, max 200 lignes par section (au-delà : renvoi vers
  l'Excel). Bouton "PDF" rose ajouté sur `AdminBackupsPage.js`. Testé :
  backup Maria → 12 pages en 218 ms.
- **2026-02-06 (iter94d)** : Export Excel des backups ACP. Le ZIP de backup
  contenait des `.jsonl` illisibles sans outil. Nouvel endpoint
  `GET /api/admin/backups/{backup_id}/download-xlsx` qui convertit le ZIP
  en fichier `.xlsx` multi-onglets lisibles : un onglet par collection
  (Propriétaires, Lots, Écritures, Factures, Appels de fonds, Transactions,
  Fournisseurs, Exercices, etc.) + onglet `_Manifest` avec les metadata.
  Colonnes triées par pertinence (id, number, date, name, amount en tête),
  header stylé, freeze pane, largeurs auto. Bouton "Excel" ajouté sur la
  page `AdminBackupsPage.js` à côté du bouton ZIP classique. Vérifié sur
  backup Maria (14 onglets, 30KB in → 70KB xlsx out, généré en 268ms).
- **2026-02-06 (iter94c)** : Backend crash après import extrait bancaire.
  `POST /api/banking/statements/import-files` déclenche une extraction IA
  (Claude Sonnet 4.5) qui peut dépasser 60s sur des PDFs volumineux. Le
  `RequestTimeoutMiddleware` (60s) l'annulait -> `RuntimeError: No response
  returned` -> chaîne de middleware corrompue -> requêtes suivantes
  (login, auth/me) bloquées -> preview affiche écran blanc infini.
  **Fix** (`/app/backend/server.py`) : ajout de `/api/banking/statements/import-files`,
  `/api/banking/import-`, `/api/invoices/import`, `/api/invoices/analyze` à
  `_REQUEST_TIMEOUT_SKIP_PREFIXES`. Backend restart -> login=500ms,
  auth/me=133ms, banking/statements=213ms (vs 60s+ timeout avant).
- **2026-02-06 (iter94b)** : Parser Bilan PDF - fix phantom sub-accounts.
  Sur les montants belges avec espace en séparateur de milliers ("131 472,36"),
  le fragment "131" était mal identifié comme un code de sous-compte (regex
  `\d{2,10}`) créant une ligne fantôme dont le libellé = compte parent et
  montant = fragment suivant. Reproduit sur `Bilan comptable au 30_09_2025.pdf`
  (SA Finlead Properties) : écart 101,82 EUR entre Actif (228 796,36) et
  Passif (228 694,54) = différence des 2 fantômes (472,26 côté Actif, 370,44
  côté Passif). Correctif : rejeter les ancres dont x0 tombe dans la colonne
  montant (>= amount_xmin de la stratégie active). Fix appliqué sur **les
  deux parsers** : `pdf_utils.parse_balance_pdf` (legacy) et
  `optipro_parser._parse_page` (moteur wizard iter93bu). 15 tests parser
  passent. Résultat : Actif = Passif = 228 324,10 (bilan équilibré).
  ⚠️ Utilisateur doit **re-uploader le PDF** dans le wizard pour ré-appliquer
  le parseur - l'ancien parse cache est stocke en DB session.
- **2026-02-06 (iter94a)** : Chinese Wall STRICT (RGPD/P0). Frontend
  `InvoicesPage` (allocation frais privatifs) et `LotsPage` (dropdown
  assignation lot->owner) ne chargent plus les proprios via `syndic_wide=true`.
  Ils utilisent strictement `copropriete_id` = ACP courante. Toggle
  "Afficher tous les proprietaires du syndic" supprime (fuite RGPD).
  Backend `/api/owners?copropriete_id=X` deja OK (middleware bloque 403 sur
  ACPs hors scope + filtre `_allowed_owner_ids`). Cross-syndic isolation
  intacte via `syndic_id` sur owners + `syndic_query()`.
- **2026-02-02** : Politique de Confidentialité (RGPD) mise à jour avec
  données société réelles. `_DEFAULT_DOCS["privacy"]` v2, DB bumpée v1→v2.
  Section 1 (Responsable du traitement) et Section 12 (Contact) mises à jour.
- 2026-02 (précédent) : CGU v4, Mentions Légales avec données société
  (via admin UI).
- 2026-02 : Data Act Registry page ajoutée.
- 2026-02 : Superadmin Billing (annual_fee, pricing_mode).
- 2026-02 : Owner communication preferences (email/postal/registered + AG).
- 2026-02 : Communication module (variable insertion, HTML formatting,
  dynamic {balance}, postal PDF).
- 2026-02 : Phantom owner lockdown (Callens) via strict lots/mutations check.
- 2026-02 : RBAC `require_permission` sur routes Invoice.

## Backlog
- **P1 (récurrent, oublié 9×)** : TEUWEN legacy lot mapping dans
  `reports.py` et `pdf_decompte.py` — aligner `lot.owner_id` avec
  `distribution_keys`.
- P2 : UI Frontend Audit Bancaire Superadmin (`/api/admin/bank-audit/scan`).
- P2 : Outil admin bulk-recalcul factures legacy (fmtEUR NaN cleanup).
- P2 : Outil admin bulk-reset factures payées legacy → impayé.
- P3 : Certificat fiscal annuel.
- P4 : Emails automatiques de relance (APScheduler quotidien).
- P5 : Refactoring `auto_entries.py` (complexité cyclomatique élevée).

## Architecture
- Backend : FastAPI, MongoDB (Motor), Pydantic, RBAC via decorators
- Frontend : React, Tailwind, Shadcn UI
- Legal docs : DB-versioned (`legal_documents` collection, history
  dans `legal_document_history`).
