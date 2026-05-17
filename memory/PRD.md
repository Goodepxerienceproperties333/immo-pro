# CoproManager PRD

## Implemented (Feb 2026)
- Auth JWT cookie (superadmin/syndic/owner), multi-coproprietes archivables
- Sidebar conditionnelle: cachee sur dashboard global, visible uniquement avec ACP selectionnee
- Proprietaires globaux: nom, prenom, adresse, email 1+2, GSM 1+2, VCS auto mod-97
- Lots avec multi-proprietaires (owner_ids array)
- Creation de lots a la volee depuis la creation d'ACP
- Locataires, Fournisseurs (TVA, IBAN, BIC)
- PCMN complet 95 comptes belges, Exercices avec cloture/a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AN), Grand Livre, Balance des comptes, Bilan, Compte de resultats
- Facturation avec cles de repartition, Appels de fonds avec suivi paiements
- Balance de tiers proprietaires/fournisseurs
- Banque: saisie en ligne, edition, lookup contrepartie (VCS+nom+fournisseurs), auto-lettrage VCS, import CODA
- Decomptes annuels PDF, Documents par categories
- Deploiement Scaleway (docker-compose, nginx, webhook auto-deploy)

## Chinese Walls (Feb 2026)
Cloisonnement strict des donnees comptables/financieres entre ACP:
- Frontend axios interceptor injecte automatiquement copropriete_id dans body POST/PUT/PATCH et query GET
- Header X-Copropriete-Id sur toutes requetes
- Tous les Input models acceptent copropriete_id
- Tous les POST stockent copropriete_id
- Tous les GET listes/rapports filtrent par copropriete_id (helper _apply_copro dans reports.py)
- Collections scopees: lots, tenants, distribution_keys, invoices, journal_entries, bank_statements, bank_transactions, fund_calls, meters, documents, document_categories, fiscal_years, budgets
- Collections globales: users, owners, suppliers, pcmn_accounts (intentionnel)
- Cross-queries internes (ex: fund_calls -> lots) sont aussi scopees
- Tests: 16/16 chinese-wall + 139/140 regression OK

## Backlog P0
- Portail proprietaire restreint (vue par ACP: balances, documents, appels de fonds, sans admin)
- Auth middleware sur toutes les routes (verification systematique)

## Backlog P1
- Decomptes annuels PDF complets via reportlab (cle de repartition + cloture exercice)
- Bilan & Compte de Resultats: completer endpoints reports.py (logique PCMN belge stricte)
- Export Excel rapports + balance de tiers
- Rappels paiement automatises
- Gestion AG (assemblees generales): ordre du jour, votes, PV

## Notes techniques
- Auth cookie: POST /api/auth/login depose un cookie, withCredentials=true dans axios
- Owners restent globaux (anti-doublon entre ACPs) mais filtres en aval par lots/factures de l'ACP courante dans les rapports
- Auto-lettrage VCS: amelioration possible = scoper owner lookup par appartenance via lots->ACP
