"""Catalogue des permissions disponibles + role templates predefinis (system).

Les role_templates sont des profils reutilisables que le syndic peut piocher
quand il cree un gestionnaire. Chaque profil contient un set de permissions
predefinies. En mode hybride (option g), le syndic peut ensuite ajuster a la
piece les permissions du gestionnaire cree.
"""

# Catalogue complet des permissions (groupe par domaine fonctionnel).
# Cle = identifiant technique, valeur = label utilisateur.
PERMISSIONS_CATALOG = {
    # Comptabilite
    "accounting.read": "Consulter la comptabilite (PCMN, journaux, grand livre)",
    "accounting.write": "Saisir/modifier les ecritures comptables",
    "fiscal.read": "Consulter les exercices fiscaux et budgets",
    "fiscal.write": "Creer/modifier les exercices et budgets",
    "fiscal.close": "Cloturer/Reouvrir un exercice fiscal",
    # Factures
    "invoices.read": "Consulter les factures fournisseurs",
    "invoices.write": "Saisir/modifier les factures fournisseurs",
    "invoices.delete": "Supprimer des factures",
    # Banque
    "banking.read": "Consulter les extraits et transactions bancaires",
    "banking.write": "Encoder extraits, lettrer transactions",
    "banking.import": "Importer des extraits CODA",
    # Appels de fonds
    "fund_calls.read": "Consulter les appels de fonds",
    "fund_calls.write": "Creer/modifier les appels de fonds",
    "fund_calls.delete": "Supprimer des appels de fonds",
    # Rapports
    "reports.read": "Acceder aux rapports (balance, bilan, resultat)",
    "reports.decompte": "Generer les decomptes annuels",
    # Tiers
    "owners.read": "Consulter les proprietaires",
    "owners.write": "Creer/modifier les proprietaires",
    "suppliers.read": "Consulter les fournisseurs",
    "suppliers.write": "Creer/modifier les fournisseurs",
    "lots.read": "Consulter les lots",
    "lots.write": "Creer/modifier les lots",
    "tenants.read": "Consulter les locataires",
    "tenants.write": "Creer/modifier les locataires",
    # Operations
    "meters.read": "Consulter les compteurs et releves",
    "meters.write": "Saisir/modifier les compteurs et releves",
    "expenses.read": "Consulter les depenses",
    "expenses.write": "Saisir/modifier les categories de depenses",
    "documents.read": "Consulter les documents",
    "documents.write": "Uploader/supprimer des documents",
    "reminders.read": "Consulter les rappels et impayes",
    "reminders.send": "Envoyer des rappels aux proprietaires",
    # Configuration ACP
    "copro.config": "Modifier la configuration de l'ACP (banques, parametres)",
}


# Profils prefefinis (is_system=True, non supprimables par l'admin).
# L'admin peut creer ses propres profils additionnels.
SYSTEM_ROLE_TEMPLATES = [
    {
        "code": "comptable",
        "name": "Comptable",
        "description": "Gestion comptable complete : journaux, factures, banque, "
                       "rapports. Pas d'acces aux modifications structurelles de l'ACP.",
        "permissions": [
            "accounting.read", "accounting.write",
            "fiscal.read", "fiscal.write",
            "invoices.read", "invoices.write", "invoices.delete",
            "banking.read", "banking.write", "banking.import",
            "fund_calls.read", "fund_calls.write",
            "reports.read", "reports.decompte",
            "owners.read", "suppliers.read", "suppliers.write",
            "expenses.read", "expenses.write",
            "documents.read", "documents.write",
        ],
        "is_system": True,
    },
    {
        "code": "assistant",
        "name": "Assistant(e) syndic",
        "description": "Saisie courante : factures, locataires, documents. "
                       "Pas d'acces a la comptabilite avancee ni a la cloture.",
        "permissions": [
            "invoices.read", "invoices.write",
            "banking.read",
            "owners.read", "owners.write", "suppliers.read", "suppliers.write",
            "lots.read", "tenants.read", "tenants.write",
            "documents.read", "documents.write",
            "reminders.read",
            "reports.read",
        ],
        "is_system": True,
    },
    {
        "code": "technique",
        "name": "Gestion technique",
        "description": "Gestion operationnelle : compteurs, lots, fournisseurs, "
                       "documents. Pas d'acces a la comptabilite.",
        "permissions": [
            "lots.read", "lots.write",
            "meters.read", "meters.write",
            "suppliers.read", "suppliers.write",
            "owners.read",
            "documents.read", "documents.write",
            "invoices.read",
        ],
        "is_system": True,
    },
    {
        "code": "consultation",
        "name": "Consultation seule",
        "description": "Lecture seule sur tous les modules. Aucune modification possible. "
                       "Utile pour audits, controles, expertises ponctuelles.",
        "permissions": [
            "accounting.read", "fiscal.read",
            "invoices.read", "banking.read",
            "fund_calls.read", "reports.read",
            "owners.read", "suppliers.read", "lots.read", "tenants.read",
            "meters.read", "expenses.read", "documents.read", "reminders.read",
        ],
        "is_system": True,
    },
]
