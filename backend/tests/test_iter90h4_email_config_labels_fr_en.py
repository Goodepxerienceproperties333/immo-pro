"""iter90h4 — Labels bilingues FR/EN pour la config email admin.

Contexte utilisateur (Feb 2026) :
  "Pourrais-tu aussi mettre la denomination des differents champs demandes
   le nom en francais donc la traduction anglaise et francaise de maniere
   a ne pas se tromper au niveau des champs a mon avis il y a une inversion"

Verifications :
- Il n'y a AUCUNE inversion backend : `graph_tenant_id` va bien dans l'URL
  Azure `login.microsoftonline.com/{tid}/...`, `graph_client_id` dans le
  payload OAuth (`client_id`).
- Le doute venait des labels UI qui utilisaient "Azure Tenant ID" au lieu
  du nom officiel Azure "Directory (tenant) ID".

Fix iter90h4 :
- Labels bilingues FR (principal) + EN Azure officiel (secondaire) pour
  les 3 champs Graph + les 5 champs SMTP.
- Bandeau d'aide "Azure Portal > App registrations > ..." pour chaque
  provider.
- Placeholder + note explicative sous chaque champ (chemin Azure exact).
"""


def test_iter90h4_graph_labels_bilingues():
    """Les 3 champs MSGraph exposent le nom FR + le nom Azure officiel EN."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    # Tenant ID
    assert "Identifiant du repertoire" in content
    assert "Directory (tenant) ID" in content
    # Client ID
    assert "Identifiant de l&apos;application" in content
    assert "Application (client) ID" in content
    # Client Secret Value (attention : Value, pas Secret ID)
    assert "Cle secrete client" in content
    assert "Client Secret (Value)" in content
    # Attention Value != Secret ID
    assert "Value" in content and "Secret ID" in content


def test_iter90h4_smtp_labels_bilingues():
    """Les champs SMTP exposent le nom FR + le nom EN."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert "SMTP Host" in content and "Serveur SMTP" in content
    assert "SMTP Username" in content and "Nom d&apos;utilisateur" in content
    assert "SMTP Password" in content
    assert "STARTTLS" in content


def test_iter90h4_provider_select_uses_full_labels():
    """Le Select 'Fournisseur' explique quel provider fait quoi."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert "Microsoft Graph (Azure AD + Office 365)" in content
    assert "SMTP (serveur email standard)" in content
    assert "Aucun / Fallback" in content


def test_iter90h4_help_banner_present():
    """Un bandeau bleu 'Azure Portal > ...' guide l'utilisateur pour trouver les IDs."""
    with open("/app/frontend/src/pages/AdminSyndicConfigPage.js") as f:
        content = f.read()
    assert "portal.azure.com" in content
    assert "App registrations" in content
    assert "Certificates" in content and "secrets" in content


def test_iter90h4_no_backend_inversion_graph_tenant_vs_client():
    """Regression check : le backend utilise bien graph_tenant_id dans l'URL
    login.microsoftonline.com et graph_client_id dans le payload OAuth.
    Une inversion serait un bug critique."""
    with open("/app/backend/routes/syndic_config.py") as f:
        content = f.read()
    # Le tenant_id va dans l'URL (tid), pas dans les credentials
    assert 'login.microsoftonline.com/{tid}' in content
    # Le client_id + client_secret vont dans le payload data
    assert '"client_id": cid' in content
    assert '"client_secret": cs' in content
    # Verifie que tid vient bien de graph_tenant_id, pas de graph_client_id
    idx = content.find('tid = effective_cfg')
    assert idx > 0
    line = content[idx:idx+80]
    assert "graph_tenant_id" in line, f"tid doit venir de graph_tenant_id, ligne={line!r}"
    idx = content.find('cid = effective_cfg')
    line = content[idx:idx+80]
    assert "graph_client_id" in line, f"cid doit venir de graph_client_id, ligne={line!r}"
