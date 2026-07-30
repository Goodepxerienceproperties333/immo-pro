"""Test credentials centralisees - iter93bk.

Ce module lit les credentials de test depuis l'environnement (`.env.test`
ou variables d'env) avec des defaults documentes pour le dev local.

Usage :
    from tests.test_credentials import ADMIN_EMAIL, ADMIN_PASSWORD, get_test_user

Chaque credential a un default equivalent au seed local pour eviter de
casser les CI existantes qui ne definissent pas ces variables. En
production le `.env.test` n'est pas deploye.

Note secu : les valeurs par default correspondent au seed de demo (mot
de passe simple, comptes admin@copro.be etc). Ces credentials ne
donnent acces qu'a la DB de dev locale, pas de fuite si le repo est
public (l'app deployee utilise d'autres seeds).
"""
import os

# --- Superadmin (seed via demo_seed.py) ---
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")

# --- Syndics de test (fixtures multi-syndic Chinese Wall) ---
SYNDIC_ALPHA_EMAIL = os.environ.get("TEST_SYNDIC_ALPHA_EMAIL", "syndic_alpha@copro.be")
SYNDIC_ALPHA_PASSWORD = os.environ.get("TEST_SYNDIC_ALPHA_PASSWORD", "Syndic123!")
SYNDIC_BETA_EMAIL = os.environ.get("TEST_SYNDIC_BETA_EMAIL", "syndic_beta@copro.be")
SYNDIC_BETA_PASSWORD = os.environ.get("TEST_SYNDIC_BETA_PASSWORD", "Syndic123!")

# --- Owner de test (portail proprietaire) ---
OWNER_EMAIL = os.environ.get("TEST_OWNER_EMAIL", "owner@copro.be")
OWNER_PASSWORD = os.environ.get("TEST_OWNER_PASSWORD", "owner123")

# --- Generic test user (autres tests) ---
GENERIC_TEST_PASSWORD = os.environ.get("TEST_GENERIC_PASSWORD", "testpass123")


def get_login_payload(role: str = "admin") -> dict:
    """Retourne un payload {email, password} pour un role donne."""
    mapping = {
        "admin": (ADMIN_EMAIL, ADMIN_PASSWORD),
        "superadmin": (ADMIN_EMAIL, ADMIN_PASSWORD),
        "syndic_alpha": (SYNDIC_ALPHA_EMAIL, SYNDIC_ALPHA_PASSWORD),
        "syndic_beta": (SYNDIC_BETA_EMAIL, SYNDIC_BETA_PASSWORD),
        "owner": (OWNER_EMAIL, OWNER_PASSWORD),
    }
    if role not in mapping:
        raise ValueError(f"Unknown test role: {role}. Valid: {list(mapping.keys())}")
    email, password = mapping[role]
    return {"email": email, "password": password}
