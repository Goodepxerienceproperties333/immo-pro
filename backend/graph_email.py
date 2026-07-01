"""Microsoft Graph integration for sending invitation emails.

Uses OAuth2 client_credentials flow (app-only). Requires Mail.Send (Application)
permission consented at the Azure AD tenant level.

The configured sender (GRAPH_SENDER_UPN) must be a valid Microsoft 365 user
with an active Exchange Online mailbox.
"""

import os
import logging
from typing import Iterable, List, Optional
import httpx
import msal

logger = logging.getLogger("graph_email")

_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
_SENDER_UPN = os.environ.get("GRAPH_SENDER_UPN", "")
_GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
_AUTHORITY = f"https://login.microsoftonline.com/{_TENANT_ID}" if _TENANT_ID else ""
_SCOPE = "https://graph.microsoft.com/.default"

# Lazy-init MSAL client (avoid network at module import time)
_msal_app: Optional[msal.ConfidentialClientApplication] = None


def _get_msal_app() -> msal.ConfidentialClientApplication:
    global _msal_app
    if _msal_app is None:
        if not (_TENANT_ID and _CLIENT_ID and _CLIENT_SECRET):
            raise RuntimeError(
                "MSGRAPH credentials missing (AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET in .env)"
            )
        _msal_app = msal.ConfidentialClientApplication(
            client_id=_CLIENT_ID,
            client_credential=_CLIENT_SECRET,
            authority=_AUTHORITY,
        )
    return _msal_app


def is_configured() -> bool:
    return bool(_TENANT_ID and _CLIENT_ID and _CLIENT_SECRET and _SENDER_UPN)


def _acquire_token() -> str:
    app = _get_msal_app()
    result = app.acquire_token_for_client(scopes=[_SCOPE])
    if "access_token" not in result:
        err = result.get("error")
        desc = result.get("error_description")
        raise RuntimeError(f"MSGRAPH token error: {err} - {desc}")
    return result["access_token"]


async def send_html_email(
    recipients: Iterable[str],
    subject: str,
    html_body: str,
    sender_upn: Optional[str] = None,
    save_to_sent: bool = True,
    reply_to: Optional[str] = None,
) -> None:
    """Send an HTML email via Microsoft Graph (sendMail).

    Raises RuntimeError on failure. Use within a background task to avoid blocking.
    iter90r : `reply_to` sets the Reply-To header so support can respond
    directly to the requester (e.g. syndic asking a support question).
    """
    sender = sender_upn or _SENDER_UPN
    if not sender:
        raise RuntimeError("GRAPH_SENDER_UPN not configured")
    token = _acquire_token()
    to_list: List[dict] = [
        {"emailAddress": {"address": str(r)}}
        for r in recipients if r
    ]
    if not to_list:
        return
    url = f"{_GRAPH_ENDPOINT}/users/{sender}/sendMail"
    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": to_list,
    }
    if reply_to:
        message["replyTo"] = [{"emailAddress": {"address": str(reply_to)}}]
    payload = {"message": message, "saveToSentItems": save_to_sent}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 202):
        body = resp.text[:500]
        logger.error("Graph sendMail failed [%s]: %s", resp.status_code, body)
        raise RuntimeError(f"Graph sendMail failed: HTTP {resp.status_code} - {body}")
    logger.info("Invitation email sent to %s (subject: %s)", [t["emailAddress"]["address"] for t in to_list], subject)


def build_invitation_email(
    *,
    recipient_name: str,
    role_label: str,
    setup_url: str,
    inviter_name: Optional[str] = None,
    inviter_email: Optional[str] = None,
) -> tuple[str, str]:
    """Build the (subject, html_body) tuple for the standard invitation email."""
    subject = "Invitation a rejoindre CoproManager"
    inviter_line = ""
    if inviter_name:
        inviter_line = (
            f"<p style=\"color:#475569;font-size:14px;margin:8px 0 0\">"
            f"Invite par : <strong>{inviter_name}</strong>"
            + (f" &lt;{inviter_email}&gt;" if inviter_email else "")
            + "</p>"
        )
    html = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#F5F7FA;margin:0;padding:0">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="padding:32px 0">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" border="0" width="600" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.06)">
        <tr><td style="background:linear-gradient(135deg,#0055FF 0%,#0040CC 100%);padding:32px 32px 28px">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-0.02em">CoproManager</h1>
          <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:14px">Gestion de copropriete &mdash; Droit belge</p>
        </td></tr>
        <tr><td style="padding:32px">
          <h2 style="color:#0F172A;margin:0 0 16px;font-size:20px">Bonjour {recipient_name},</h2>
          <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 16px">
            Vous avez ete invite a rejoindre la plateforme <strong>CoproManager</strong> en tant que <strong>{role_label}</strong>.
          </p>
          {inviter_line}
          <p style="color:#334155;font-size:15px;line-height:1.6;margin:20px 0 24px">
            Pour activer votre compte, cliquez sur le bouton ci-dessous et definissez votre mot de passe :
          </p>
          <table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 24px">
            <tr><td style="background:#0055FF;border-radius:8px">
              <a href="{setup_url}" style="display:inline-block;padding:14px 28px;color:#fff;text-decoration:none;font-weight:600;font-size:15px">Activer mon compte</a>
            </td></tr>
          </table>
          <p style="color:#64748B;font-size:13px;line-height:1.5;margin:24px 0 0;padding-top:20px;border-top:1px solid #E2E8F0">
            Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br>
            <a href="{setup_url}" style="color:#0055FF;word-break:break-all;font-size:12px">{setup_url}</a>
          </p>
          <p style="color:#94A3B8;font-size:12px;line-height:1.5;margin:24px 0 0">
            Ce lien est valable 7 jours. Si vous n'attendiez pas cette invitation, vous pouvez l'ignorer.
          </p>
        </td></tr>
        <tr><td style="background:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0">
          <p style="color:#94A3B8;font-size:11px;margin:0;text-align:center">
            CoproManager &middot; Conforme PCMN belge &middot; Chinese wall RGPD strict
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return subject, html


def build_password_reset_email(
    *,
    recipient_name: str,
    reset_url: str,
    expires_minutes: int = 60,
) -> tuple[str, str]:
    """Build (subject, html_body) for the password reset email."""
    subject = "Reinitialisation de votre mot de passe CoproManager"
    html = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#F5F7FA;margin:0;padding:0">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="padding:32px 0">
    <tr><td align="center">
      <table cellpadding="0" cellspacing="0" border="0" width="600" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.06)">
        <tr><td style="background:linear-gradient(135deg,#0055FF 0%,#0040CC 100%);padding:32px 32px 28px">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-0.02em">CoproManager</h1>
          <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:14px">Reinitialisation de mot de passe</p>
        </td></tr>
        <tr><td style="padding:32px">
          <h2 style="color:#0F172A;margin:0 0 16px;font-size:20px">Bonjour {recipient_name},</h2>
          <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 16px">
            Vous avez demande la reinitialisation de votre mot de passe. Cliquez sur le bouton ci-dessous
            pour definir un nouveau mot de passe :
          </p>
          <table cellpadding="0" cellspacing="0" border="0" style="margin:24px auto">
            <tr><td style="background:#0055FF;border-radius:8px">
              <a href="{reset_url}" style="display:inline-block;padding:14px 28px;color:#fff;text-decoration:none;font-weight:600;font-size:15px">Reinitialiser mon mot de passe</a>
            </td></tr>
          </table>
          <p style="color:#64748B;font-size:13px;line-height:1.5;margin:24px 0 0;padding-top:20px;border-top:1px solid #E2E8F0">
            Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br>
            <a href="{reset_url}" style="color:#0055FF;word-break:break-all;font-size:12px">{reset_url}</a>
          </p>
          <p style="color:#94A3B8;font-size:12px;line-height:1.5;margin:24px 0 0">
            Ce lien est valable {expires_minutes} minutes et ne peut etre utilise qu'une seule fois.
            Si vous n'avez pas demande cette reinitialisation, ignorez simplement cet email
            &mdash; votre mot de passe actuel reste inchange.
          </p>
        </td></tr>
        <tr><td style="background:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0">
          <p style="color:#94A3B8;font-size:11px;margin:0;text-align:center">
            CoproManager &middot; Conforme PCMN belge &middot; Chinese wall RGPD strict
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return subject, html

