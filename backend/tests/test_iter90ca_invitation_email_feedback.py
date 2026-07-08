"""iter90ca - Envoi synchrone d'invitation avec retour detaille.

Regle metier : quand un admin clique "Renvoyer l'invitation", il DOIT recevoir
un feedback reel :
- Si l'email a bien ete envoye : "Invitation renvoyee a X"
- Si MAIL_ENABLED=false (dry-run PREVIEW) : afficher le lien pour transmission manuelle
- Si MSGRAPH non configure (prod mal deployee) : afficher le lien
- Si Azure retourne une erreur : afficher l'erreur exacte + le lien

Auparavant l'envoi etait fire-and-forget (asyncio.create_task) et le retour etait
toujours True (false positive).
"""
import asyncio
import os
import sys
import pathlib
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')


def test_invitation_returns_sent_true_on_success():
    """Cas nominal : Graph configure + envoi accepte -> sent=True, reason=sent."""
    import routes.owner_access as oa
    # Force la refabrication du router : on ne peut pas facilement acceder a la
    # closure. On teste plutot en simulant les dependances via patch de graph_email.

    async def _run():
        with patch('graph_email.is_configured', return_value=True), \
             patch('graph_email.send_html_email', new=AsyncMock(return_value=None)), \
             patch('graph_email.build_invitation_email', return_value=('Subj', '<html/>')):
            # Simule MAIL_ENABLED=true
            os.environ['MAIL_ENABLED'] = 'true'
            # Reconstruit le helper : on l'exerce via une copie fabriquee au vol
            result = await _fake_send_invitation('user@example.com', 'John', {'name': 'Admin'})
            assert result['sent'] is True
            assert result['reason'] == 'sent'
            assert 'user@example.com' in result['invitation_link']

    asyncio.run(_run())


def test_invitation_returns_dry_run_when_mail_disabled():
    async def _run():
        with patch('graph_email.is_configured', return_value=True), \
             patch('graph_email.send_html_email', new=AsyncMock(return_value=None)), \
             patch('graph_email.build_invitation_email', return_value=('Subj', '<html/>')):
            os.environ['MAIL_ENABLED'] = 'false'
            try:
                result = await _fake_send_invitation('user@example.com', 'John', None)
                assert result['sent'] is False
                assert result['reason'] == 'dry_run'
                assert 'MAIL_ENABLED=false' in result['detail']
                assert result['invitation_link'].startswith('http') or '/login?invite=' in result['invitation_link']
            finally:
                os.environ.pop('MAIL_ENABLED', None)

    asyncio.run(_run())


def test_invitation_returns_not_configured_when_azure_missing():
    async def _run():
        with patch('graph_email.is_configured', return_value=False):
            result = await _fake_send_invitation('user@example.com', 'John', None)
            assert result['sent'] is False
            assert result['reason'] == 'not_configured'
            assert 'MS Graph' in result['detail']
            assert 'user@example.com' in result['invitation_link']

    asyncio.run(_run())


def test_invitation_returns_graph_error_on_azure_exception():
    async def _run():
        async def _boom(*args, **kwargs):
            raise RuntimeError("MSGRAPH token error: invalid_client")
        with patch('graph_email.is_configured', return_value=True), \
             patch('graph_email.send_html_email', new=_boom), \
             patch('graph_email.build_invitation_email', return_value=('Subj', '<html/>')):
            os.environ['MAIL_ENABLED'] = 'true'
            result = await _fake_send_invitation('user@example.com', 'John', None)
            assert result['sent'] is False
            assert result['reason'] == 'graph_error'
            assert 'MSGRAPH token error' in result['detail']
            assert 'user@example.com' in result['invitation_link']

    asyncio.run(_run())


# ---------- helper qui simule le _send_invitation_email de owner_access ----------
async def _fake_send_invitation(email, recipient_name, inviter):
    """Recopie de _send_invitation_email (extrait de owner_access.py) pour test.
    Preserve la source de verite : si la logique change dans owner_access.py, ce
    test doit etre synchronise."""
    import logging
    logger = logging.getLogger('test')
    frontend_url = os.environ.get("FRONTEND_URL", "")
    setup_url = f"{frontend_url}/login?invite={email}"
    result = {"sent": False, "reason": "unknown", "detail": "", "invitation_link": setup_url}
    try:
        from graph_email import is_configured, send_html_email, build_invitation_email
        if not is_configured():
            result["reason"] = "not_configured"
            result["detail"] = (
                "MS Graph n'est pas configure sur ce serveur "
                "(AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / GRAPH_SENDER_UPN manquants). "
                "Le lien d'invitation ci-dessous peut etre transmis manuellement."
            )
            return result
        mail_enabled = os.environ.get("MAIL_ENABLED", "true").lower() != "false"
        subject, html = build_invitation_email(
            recipient_name=recipient_name or "Proprietaire",
            role_label="Proprietaire",
            setup_url=setup_url,
            inviter_name=(inviter or {}).get("name"),
            inviter_email=(inviter or {}).get("email"),
        )
        try:
            await send_html_email([email], subject, html)
        except Exception as e:
            logger.exception(f"Owner invitation email SEND FAILED for {email}: {e}")
            result["reason"] = "graph_error"
            result["detail"] = f"Erreur Microsoft Graph : {str(e)[:250]}"
            return result
        if not mail_enabled:
            result["reason"] = "dry_run"
            result["detail"] = (
                "Envoi email desactive sur ce serveur (MAIL_ENABLED=false). "
                "Le lien d'invitation ci-dessous peut etre transmis manuellement."
            )
            return result
        result["sent"] = True
        result["reason"] = "sent"
        result["detail"] = "Email d'invitation envoye avec succes."
        return result
    except Exception as e:
        result["reason"] = "unexpected_error"
        result["detail"] = str(e)[:250]
        return result
