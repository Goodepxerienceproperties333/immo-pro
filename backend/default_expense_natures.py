"""Liste des natures de depense par defaut a creer pour toute nouvelle ACP.

Source : PDF "Liste des natures de depense par defaut" fournit par SRL FINLEAD
PROPERTIES (26/06/2026). 23 natures couvrant les cas usuels d'une copropriete
belge avec syndic professionnel : honoraires, assurances, ascenseurs, fluides
(eau, electricite, gaz), incendie, entretien, frais bancaires.

Schema : aligne avec ce que produit le wizard Optipro (commit_natures) :
- code : "0001" a "0053" (codes Optipro / Sogis)
- name + label : libelle complet
- account_number : compte PCMN belge
- vat_code : "A4" (0%), "A2" (6%) ou "A1" (21%)
- default_occupant_pct + default_proprietaire_pct (somme = 100)
- kind : "charge" (classe 6) ou "produit" (classe 7) - pour traitement special
  cote UI (intetets crediteurs 750 = produit, pas une charge)
"""

DEFAULT_EXPENSE_NATURES = [
    # Syndic - frais partages 50/50
    {"code": "0001", "name": "Honoraires Syndic", "account_number": "61300",
     "vat_code": "A4", "default_proprietaire_pct": 50.0, "default_occupant_pct": 50.0, "kind": "charge"},
    {"code": "0002", "name": "Frais d'administration syndic", "account_number": "6160",
     "vat_code": "A4", "default_proprietaire_pct": 50.0, "default_occupant_pct": 50.0, "kind": "charge"},

    # Assurances - 100% proprietaire
    {"code": "0003", "name": "Assurance incendie", "account_number": "6140",
     "vat_code": "A4", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
    {"code": "0004", "name": "Assurance RC Conseil de Copropriete et Commissaire aux comptes",
     "account_number": "6141",
     "vat_code": "A4", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
    {"code": "0005", "name": "Assurance defense en justice", "account_number": "6146",
     "vat_code": "A4", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},

    # Ascenseurs
    {"code": "0009", "name": "Ascenseurs - contrat d'entretien", "account_number": "61011",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0010", "name": "Ascenseurs - controle periodique", "account_number": "61010",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0011", "name": "Ascenseurs - entretien et reparations non compris dans contrat d'entretien",
     "account_number": "61012",
     "vat_code": "A1", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},

    # Fluides (eau, electricite, gaz) - 100% occupant
    {"code": "0012", "name": "Eau parties communes", "account_number": "61201",
     "vat_code": "A2", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0013", "name": "Electricite parties communes", "account_number": "61210",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0014", "name": "Eau - redevance fixe et consommation", "account_number": "61200",
     "vat_code": "A2", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0015", "name": "Gaz", "account_number": "61220",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},

    # Incendie
    {"code": "0020", "name": "Incendie - alerte et detection", "account_number": "61004",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0021", "name": "Incendie - contrats d'entretien", "account_number": "61000",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0022", "name": "Incendie - extincteurs", "account_number": "61001",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0023", "name": "Incendie - prevention", "account_number": "61005",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},

    # Entretien batiment / jardins
    {"code": "0025", "name": "Entretien jardins et environs immediats selon contrat",
     "account_number": "61060",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},
    {"code": "0030", "name": "Nettoyage batiment selon contrat", "account_number": "61050",
     "vat_code": "A1", "default_proprietaire_pct": 0.0, "default_occupant_pct": 100.0, "kind": "charge"},

    # Travaux + salles + frais financiers + honoraires - 100% proprietaire
    {"code": "0045", "name": "Travaux divers", "account_number": "61066",
     "vat_code": "A1", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
    {"code": "0050", "name": "Utilisation salles de reunion", "account_number": "61610",
     "vat_code": "A1", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
    {"code": "0051", "name": "Frais bancaires et charges des dettes", "account_number": "650",
     "vat_code": "A4", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
    {"code": "0052", "name": "Interets crediteurs", "account_number": "750",
     "vat_code": "A4", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "produit"},
    {"code": "0053", "name": "Honoraires experts", "account_number": "61303",
     "vat_code": "A1", "default_proprietaire_pct": 100.0, "default_occupant_pct": 0.0, "kind": "charge"},
]
