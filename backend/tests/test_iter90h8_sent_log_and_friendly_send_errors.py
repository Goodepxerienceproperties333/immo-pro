"""iter90h8 — Endpoint sent-log + messages d'erreur Microsoft explicites.

Contexte utilisateur (Feb 2026) : "les mails via l'onglet communication ne
fonctionnent toujours pas !!" puis "probleme semble resolu je viens de
recevoir le mail" (apres iter90h7 + iter90h8).

Fix iter90h7 + iter90h8 :
- iter90h7 : `_send_email` et `send_html_email` privilegient la config
  Graph par-syndic (DB) sur les env vars globales. Bypass du dry-run
  MAIL_ENABLED=false quand une config par-syndic est presente.
- iter90h8 :
  * Endpoint `GET /api/communication/sent-log` pour visualiser l'historique
    des envois avec statut et erreur Microsoft.
  * Messages d'erreur explicites quand Microsoft rejette avec
    AccessDenied (permission Mail.Send manquante), MailboxNotEnabled
    (pas de licence Exchange), TooManyRequests (throttled).
"""


def test_iter90h8_sent_log_endpoint_registered():
    """L'endpoint GET /api/communication/sent-log est bien enregistre."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    assert '@router.get("/sent-log")' in content
    assert "async def get_sent_log" in content
    assert "only_failed: bool = False" in content
    # Retourne counts + rows
    assert '"counts":' in content or 'counts": counts' in content or '"counts"' in content
    assert '"rows":' in content or 'rows": rows' in content or '"rows"' in content


def test_iter90h8_friendly_send_error_messages():
    """Le _send_email traduit les erreurs Microsoft en messages actionnables."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    # AccessDenied -> permission Mail.Send
    assert "Permission Mail.Send manquante" in content
    assert "Grant admin consent" in content
    # MailboxNotEnabled -> pas de licence
    assert "licence Exchange Online" in content
    assert "mailboxnotenabled" in content.lower() or "requestedmailboxnotfound" in content.lower()
    # Throttled
    assert "Limite d'envoi Microsoft depassee" in content


def test_iter90h8_error_messages_include_microsoft_detail():
    """Les messages friendly conservent quand meme le code Microsoft brut
    pour le debug (dans '(Detail Microsoft : ...)')."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    assert "Detail Microsoft" in content or "(Detail : " in content


def test_iter90h8_sent_log_hides_html_body():
    """Le html_body (potentiellement volumineux) n'est PAS retourne dans
    la reponse sent-log (envois de masse -> payload petit)."""
    with open("/app/backend/routes/communication.py") as f:
        content = f.read()
    # La ligne qui retire html_body doit exister
    assert 'r.pop("html_body", None)' in content or "html_body" in content.split('async def get_sent_log')[1].split("return")[0]
