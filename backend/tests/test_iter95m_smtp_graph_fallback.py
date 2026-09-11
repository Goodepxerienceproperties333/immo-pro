"""iter95m - Test du fallback SMTP -> Microsoft Graph pour l'envoi d'emails.

Contexte : le SMTP One2Net (mail01.one2net.net) rejette l'authentification
(535 auth failed) pour info@nextgecopro.be. Sans fallback, toutes les
invitations syndic/gestionnaire echouaient silencieusement (le code utilisait
`asyncio.create_task` fire-and-forget). Le fix (iter95m) :

1. `graph_email.send_html_email` : si SMTP echoue et Graph est configure,
   bascule automatiquement sur Graph.
2. `routes/admin.create_user` et `routes/team.add_member` : envoi synchrone
   (`await`) avec remontee de `invitation_sent` + `invitation_error` dans la
   reponse HTTP.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

from dotenv import load_dotenv

sys.path.insert(0, '/app/backend')
load_dotenv('/app/backend/.env')


def test_smtp_ok_no_fallback_needed():
    """Cas nominal : SMTP OK -> Graph pas appele."""
    import graph_email
    with patch.object(graph_email, '_send_via_smtp', new=AsyncMock(return_value=None)) as smtp_mock, \
         patch.object(graph_email, '_acquire_token') as graph_token:
        asyncio.run(graph_email.send_html_email(
            ['x@example.com'], 'S', '<p>b</p>',
        ))
        assert smtp_mock.await_count == 1, "SMTP doit etre appele en priorite"
        assert graph_token.call_count == 0, "Graph ne doit PAS etre appele si SMTP OK"


def test_smtp_fail_falls_back_to_graph():
    """SMTP KO + Graph configure -> bascule automatique vers Graph."""
    import httpx
    import graph_email
    smtp_err = RuntimeError("SMTP send failed: (535, '5.7.8 Error: auth failed')")

    async def _fake_smtp(**_kw):
        raise smtp_err

    class _FakeResp:
        status_code = 202
        text = ''

    with patch.object(graph_email, '_send_via_smtp', new=AsyncMock(side_effect=smtp_err)) as smtp_mock, \
         patch.object(graph_email, '_acquire_token', return_value='FAKE_TOKEN') as graph_token, \
         patch.object(httpx, 'AsyncClient') as httpx_client:
        # AsyncClient context manager mock
        cm = AsyncMock()
        cm.post = AsyncMock(return_value=_FakeResp())
        httpx_client.return_value.__aenter__.return_value = cm

        asyncio.run(graph_email.send_html_email(
            ['x@example.com'], 'S', '<p>b</p>',
        ))
        assert smtp_mock.await_count == 1, "SMTP tente en 1er"
        assert graph_token.call_count == 1, "Fallback : token Graph acquis"
        assert cm.post.await_count == 1, "Fallback : POST vers Graph sendMail"


def test_smtp_fail_no_graph_reraises():
    """SMTP KO + Graph pas configure -> exception remontee (pas de silence)."""
    import graph_email
    smtp_err = RuntimeError("SMTP send failed: bogus")
    # Vide les credentials Graph
    with patch.object(graph_email, '_send_via_smtp', new=AsyncMock(side_effect=smtp_err)), \
         patch.object(graph_email, '_TENANT_ID', ''), \
         patch.object(graph_email, '_CLIENT_ID', ''), \
         patch.object(graph_email, '_CLIENT_SECRET', ''), \
         patch.object(graph_email, '_SENDER_UPN', ''):
        try:
            asyncio.run(graph_email.send_html_email(['x@example.com'], 'S', '<p>b</p>'))
            raise AssertionError("Devrait avoir raise (pas de fallback)")
        except RuntimeError as e:
            assert 'SMTP' in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                raise
    print("All iter95m fallback tests OK")
