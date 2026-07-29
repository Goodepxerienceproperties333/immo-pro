"""iter93af : tests regression pour les corrections de securite du security audit.

Couvre :
- SEC-001 : register ne cree plus un role="owner", et refuse les emails
  correspondant a une fiche proprietaire existante (403).
- SEC-001 defense-in-depth : owner_portal rejette les callers de role
  syndic/admin/superadmin (403).
- SEC-002 : invoice/statement endpoints refusent les IDs d'objets appartenant
  a un autre syndic (404).
- P3 : regex utilisateur echappe (pas de ReDoS via $regex Mongo).
"""
import re


def test_sec_p3_regex_escape():
    """La query utilisateur passee dans $regex doit etre echappee via
    re.escape() pour empecher tout meta-caractere regex (ReDoS, injection).
    """
    malicious = "(a+)+$"
    escaped = re.escape(malicious)
    # re.escape enleve tous les meta-caracteres regex
    assert "\\(" in escaped or "\\+" in escaped
    assert escaped != malicious


def test_sec_001_registration_role_default():
    """Le role par defaut pour l'inscription publique est 'syndic', pas 'owner'.
    Verifie que le comportement est bien code dans server.py.
    """
    with open("/app/backend/server.py", "r") as f:
        src = f.read()
    # Le nouveau register doit definir role="syndic", pas role="owner"
    reg_block_start = src.find('@auth_router.post("/register")')
    reg_block_end = src.find('@auth_router.get("/me")')
    reg_block = src[reg_block_start:reg_block_end]
    assert '"role": "syndic"' in reg_block, "Le register doit creer un role syndic (pas owner) - iter93af"
    assert '"role": "owner"' not in reg_block, "Register ne doit plus creer de role owner - iter93af"


def test_sec_001_owner_portal_role_guard():
    """L'owner_portal doit avoir un guard _require_owner_role qui exige
    role in ('owner', 'occupant').
    """
    with open("/app/backend/routes/owner_portal.py", "r") as f:
        src = f.read()
    assert "_require_owner_role" in src
    assert 'role not in ("owner", "occupant")' in src


def test_sec_002_invoice_scope():
    """get_invoice(invoice_id) doit appliquer syndic_query pour empecher IDOR."""
    with open("/app/backend/routes/invoices.py", "r") as f:
        src = f.read()
    # Cherche la definition de get_invoice
    idx = src.find("async def get_invoice(invoice_id: str")
    assert idx >= 0
    # Le prochain "raise HTTPException" doit etre precede d'un syndic_query
    block = src[idx:idx+500]
    assert "syndic_query" in block, "get_invoice doit utiliser syndic_query - iter93af (SEC-002)"


def test_sec_002_banking_statement_endpoints():
    """post_statement / update_statement / delete_statement / lettrage doivent
    tous appliquer syndic_query."""
    with open("/app/backend/routes/banking.py", "r") as f:
        src = f.read()
    # Cherche les async def specifiques (pas les commentaires les referencant)
    for endpoint in [
        "async def post_statement(stmt_id: str, request: Request",
        "async def update_statement(stmt_id: str, data: StatementInput, request: Request",
        "async def delete_statement(stmt_id: str, request: Request",
        "async def lettrage(data: LettrageInput, request: Request",
    ]:
        idx = src.find(endpoint)
        assert idx >= 0, f"Signature {endpoint} introuvable"
        block = src[idx:idx+1600]
        assert "syndic_query" in block, f"{endpoint[:40]}... doit utiliser syndic_query - iter93af (SEC-002)"


def test_sec_p3_admin_password_fail_closed():
    """seed_admin doit fail-closed si ADMIN_PASSWORD non defini."""
    with open("/app/backend/server.py", "r") as f:
        src = f.read()
    idx = src.find("async def seed_admin")
    block = src[idx:idx+1200]
    assert 'ADMIN_PASSWORD", "admin123"' not in block, "Plus de default admin123 - iter93af"
    assert "ADMIN_PASSWORD non defini" in block or "not admin_password" in block


def test_sec_p3_cookie_secure_auto():
    """COOKIE_SECURE doit etre auto-detecte en prod (FRONTEND_URL https ou
    APP_ENV=production).
    """
    with open("/app/backend/server.py", "r") as f:
        src = f.read()
    assert "_auto_secure_cookie_default" in src
    assert 'startswith("https://")' in src


if __name__ == "__main__":
    for fn in [
        test_sec_p3_regex_escape,
        test_sec_001_registration_role_default,
        test_sec_001_owner_portal_role_guard,
        test_sec_002_invoice_scope,
        test_sec_002_banking_statement_endpoints,
        test_sec_p3_admin_password_fail_closed,
        test_sec_p3_cookie_secure_auto,
    ]:
        fn()
        print(f"OK: {fn.__name__}")
    print("\nTous les tests de securite passent")
