# CoproManager PRD

## Implemented (May 2026) - 110/110 tests
- Auth JWT (superadmin/syndic/owner), multi-coproprietes archivables
- Proprietaires: nom, prenom, adresse complete (rue, CP, ville, pays), email 1+2, GSM 1+2, VCS auto-genere
- Lots avec multi-proprietaires (owner_ids array)
- Locataires, Fournisseurs (TVA, IBAN, BIC)
- PCMN complet 95 comptes belges, Exercices avec cloture/a-nouveau, Budgets + comparaison
- Journaux (OD/AV/AP/AN), Grand Livre, Balance des comptes, Bilan, Compte de resultats
- Facturation avec cles de repartition, Appels de fonds avec suivi paiements
- Balance de tiers proprietaires (debiteur/crediteur) et fournisseurs (a payer)
- Interface bancaire: saisie en ligne multiple, edition transactions existantes, lookup contrepartie (noms+VCS+fournisseurs), auto-lettrage VCS, import CODA, lettrage proprietaires/factures/fournisseurs
- Decomptes annuels PDF, Documents par categories
- Codes VCS belges (communication structuree mod-97) permanents par proprietaire

## Backlog P0: Auth middleware toutes routes, Portail proprietaire restreint
## Backlog P1: Rappels paiement, Export Excel, Gestion AG
