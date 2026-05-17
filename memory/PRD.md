# CoproManager PRD

## Implemented (Feb 2026)
- Auth JWT (superadmin/syndic/owner), multi-coproprietes archivables
- Sidebar conditionnelle: cachee sur le dashboard global, visible uniquement avec ACP selectionnee
- Proprietaires: nom, prenom, adresse complete, email 1+2, GSM 1+2, VCS auto-genere (mod-97)
- Lots avec multi-proprietaires (owner_ids array)
- Creation de lots a la volee depuis le formulaire de creation d'une ACP (POST /api/coproprietes accepte un tableau `lots`)
- Locataires, Fournisseurs (TVA, IBAN, BIC)
- PCMN complet 95 comptes belges, Exercices avec cloture/a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AN), Grand Livre, Balance des comptes, Bilan, Compte de resultats
- Facturation avec cles de repartition, Appels de fonds avec suivi paiements
- Balance de tiers proprietaires (debiteur/crediteur) et fournisseurs (a payer)
- Interface bancaire: saisie en ligne, edition transactions, lookup contrepartie (noms+VCS+fournisseurs), auto-lettrage VCS, import CODA
- Decomptes annuels PDF, Documents par categories
- Deploiement Scaleway (docker-compose, nginx, webhook auto-deploy)

## Backlog P0
- Portail proprietaire restreint (vue par ACP: balances, documents, appels de fonds, sans admin)
- Auth middleware sur toutes les routes (verification systematique)

## Backlog P1
- Decomptes annuels PDF complets via reportlab (clés de repartition + cloture exercice)
- Bilan & Compte de Resultats: completer endpoints reports.py (logique PCMN belge stricte)
- Export Excel rapports + balance de tiers
- Rappels paiement automatises
- Gestion AG (assemblees generales): ordre du jour, votes, PV
