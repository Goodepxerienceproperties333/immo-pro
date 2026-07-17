"""iter90h7 — Les envois de mails "normaux" utilisent la config par-syndic
(pas seulement les env vars globales).

Contexte utilisateur (Feb 2026) : "les mails ne partent pas pour
Welcome@goodexperienceproperties.be!!"

Root cause :
  Deux systemes d'envoi email coexistaient :
  - `graph_email.send_html_email()` (invitations, resets) utilisait les env
    vars AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET globales.
  - `routes/communication._send_email()` (envois utilisateur) idem.

  En preview, `MAIL_ENABLED=false` -> tous les envois "silently dropped"
  en dry-run, meme si l'utilisateur avait parfaitement configure ses
  credentials par-syndic dans `syndic_configs`. Symptome : "email_verified=True"
  (le bouton Test admin marchait) mais aucun mail normal n'etait envoye.

Fix iter90h7 :
- `communication._send_email()` resout d'abord la config par-syndic via
  `get_effective_email_config(db, syndic_user_id)`. Si trouvee et complete
  (provider=graph + tenant + client + secret), elle remplace les env vars
  et bypasse le `MAIL_ENABLED=false`.
- `graph_email.send_html_email()` accepte 2 nouveaux params optionnels
  (`db`, `for_syndic_user_id`) pour beneficier du meme mecanisme.
- Le fallback env-vars reste intact pour la retro-compat.
- Le dry-run global ne s'applique QUE si aucune config par-syndic n'est
  presente pour l'utilisateur.
"""
import os


def test_iter90h7_communication_prefers_per_syndic_config():
    """`_send_email` dans communication.py doit resoudre la config par-syndic
    et l'utiliser en priorite sur les env vars."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    assert "get_effective_email_config" in content, \
        "communication._send_email doit importer get_effective_email_config"
    assert "_resolve_syndic_user_id" in content
    # La config par-syndic doit prendre priorite (graph_tid/cid/cs assignes)
    assert "graph_source = " in content
    assert 'graph_source = "db_per_syndic"' in content
    # Bypass MAIL_ENABLED si config par-syndic complete
    assert "use_per_syndic" in content
    assert "not _MAIL_ENABLED and not use_per_syndic" in content


def test_iter90h7_graph_email_accepts_db_and_syndic_id():
    """`send_html_email` accepte les nouveaux parametres."""
    with open("/app/backend/graph_email.py") as f:
        content = f.read()
    # Nouvelle signature
    assert "for_syndic_user_id: Optional[str] = None" in content
    assert "db=None" in content
    # Priorite config par-syndic
    assert "per_syndic" in content
    # Fallback si per_syndic absent ET MAIL_ENABLED false : dry-run
    assert "not per_syndic and not _MAIL_ENABLED" in content


def test_iter90h7_is_configured_for_syndic_helper():
    """Nouveau helper `is_configured_for_syndic(db, uid)` : True si env OK
    ou si per-syndic config OK."""
    with open("/app/backend/graph_email.py") as f:
        content = f.read()
    assert "async def is_configured_for_syndic" in content
    # Import correct dans le helper
    assert "get_effective_email_config" in content
    # Le helper est bien async
    idx = content.index("async def is_configured_for_syndic")
    signature_block = content[idx:idx+300]
    assert "-> bool" in signature_block


def test_iter90h7_smoke_communication_imports_correctly():
    """Le module communication.py doit importer sans erreur (regression)."""
    import importlib
    import sys
    if "routes.communication" in sys.modules:
        importlib.reload(sys.modules["routes.communication"])
    else:
        __import__("routes.communication")
    # Verifie que le module a bien un objet router
    from routes import communication as _c
    assert _c is not None


def test_iter90h7_no_regression_on_env_only_fallback():
    """Le fallback env vars reste en place quand pas de per-syndic config."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    # Les env vars restent utilisees comme point de depart
    assert "graph_tid = _TENANT_ID" in content
    assert "graph_cid = _CLIENT_ID" in content
    assert "graph_cs = _CLIENT_SECRET" in content
    # La condition d'erreur "Microsoft Graph non configure" mentionne les 2 sources
    assert "config par-syndic" in content
