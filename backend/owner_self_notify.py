"""iter89 - Notification syndic when an owner self-services from the portal.

Triggered by :
  - PUT /api/owner/me (owner updates his coordinates)
  - POST/PUT/DELETE /api/owner/tenants (owner CRUDs his tenants)

Strategy :
  1. Always persist a notification record in `db.owner_notifications`
     (so the syndic sees it in the UI even if email fails).
  2. Best-effort send via Microsoft Graph email if configured. We do NOT
     block the user response if email fails (logged + silent).

iter93cu (fev 2026) - Refonte du template email pour un rendu professionnel :
  - En-tete avec logo/badge + nom de la copropriete
  - Tableau structure des modifications (Champ / Avant / Apres) au lieu de
    la liste ` field : 'old' -> 'new' ` peu lisible.
  - Pied de page avec branding NextGe Copro et mention automatique.
"""
import logging
import uuid
import html as _html
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("owner_self_notify")


BRAND_COLOR = "#022D52"
BRAND_ACCENT = "#1D4ED8"
BG_SOFT = "#F5F7FB"
BORDER_SOFT = "#E2E8F0"
TEXT_MUTED = "#64748B"


def _esc(v) -> str:
    """Escape user-supplied content for safe HTML embedding."""
    if v is None:
        return ""
    return _html.escape(str(v))


def _fmt_date_fr(iso_dt: str) -> str:
    """Format ISO datetime as `DD/MM/YYYY a HH:MM` (Belgian style)."""
    try:
        dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y a %H:%M")
    except Exception:
        return iso_dt or ""


def _render_changes_table(changes: List[dict]) -> str:
    """Render structured changes as an HTML table.
    changes: list of {label, before, after} dicts.
    """
    if not changes:
        return ""
    rows = []
    for c in changes:
        label = _esc(c.get("label", ""))
        before = _esc(c.get("before", "") or "-")
        after = _esc(c.get("after", "") or "-")
        rows.append(
            f'''<tr>
                <td style="padding:10px 14px;border-bottom:1px solid {BORDER_SOFT};font-size:13px;color:#0f172a;font-weight:600;width:32%;">{label}</td>
                <td style="padding:10px 14px;border-bottom:1px solid {BORDER_SOFT};font-size:13px;color:{TEXT_MUTED};text-decoration:line-through;">{before}</td>
                <td style="padding:10px 14px;border-bottom:1px solid {BORDER_SOFT};font-size:13px;color:#0f172a;font-weight:500;">{after}</td>
            </tr>'''
        )
    return f'''
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;background:#ffffff;border:1px solid {BORDER_SOFT};border-radius:8px;overflow:hidden;margin:16px 0;">
        <thead>
            <tr style="background:{BG_SOFT};">
                <th style="padding:10px 14px;text-align:left;font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Champ</th>
                <th style="padding:10px 14px;text-align:left;font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Ancienne valeur</th>
                <th style="padding:10px 14px;text-align:left;font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Nouvelle valeur</th>
            </tr>
        </thead>
        <tbody>
            {"".join(rows)}
        </tbody>
    </table>'''


def _render_summary_list(summary_lines: List[str]) -> str:
    """Fallback rendering when no structured changes are provided
    (ex: creation/suppression de locataire)."""
    if not summary_lines:
        return ""
    items = "".join(
        f'<tr><td style="padding:8px 14px;border-bottom:1px solid {BORDER_SOFT};font-size:13px;color:#0f172a;">{_esc(l)}</td></tr>'
        for l in summary_lines
    )
    return f'''
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;background:#ffffff;border:1px solid {BORDER_SOFT};border-radius:8px;overflow:hidden;margin:16px 0;">
        <tbody>{items}</tbody>
    </table>'''


def _build_html(
    owner_name: str,
    owner_email: str,
    change_type: str,
    summary_lines: List[str],
    copropriete_name: str = "",
    changes: Optional[List[dict]] = None,
) -> str:
    """Build a professional HTML notification email.

    Uses `changes` (structured) when available for a clean table.
    Falls back to `summary_lines` (raw strings) otherwise.
    """
    now_str = _fmt_date_fr(datetime.now(timezone.utc).isoformat())
    body_block = (
        _render_changes_table(changes) if changes else _render_summary_list(summary_lines)
    )
    copro_row = ""
    if copropriete_name:
        copro_row = f'''
        <tr>
            <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;width:130px;">Copropriete</td>
            <td style="padding:6px 0;color:#0f172a;font-size:13px;font-weight:600;">{_esc(copropriete_name)}</td>
        </tr>'''

    email_row = ""
    if owner_email:
        email_row = f'''
        <tr>
            <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;width:130px;">Email proprietaire</td>
            <td style="padding:6px 0;color:#0f172a;font-size:13px;">
                <a href="mailto:{_esc(owner_email)}" style="color:{BRAND_ACCENT};text-decoration:none;">{_esc(owner_email)}</a>
            </td>
        </tr>'''

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Notification proprietaire</title>
</head>
<body style="margin:0;padding:0;background:{BG_SOFT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;color:#0f172a;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{BG_SOFT};padding:32px 12px;">
        <tr>
            <td align="center">
                <table role="presentation" cellpadding="0" cellspacing="0" width="600" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(15,23,42,0.06);">
                    <!-- Header -->
                    <tr>
                        <td style="background:{BRAND_COLOR};padding:22px 28px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="color:#ffffff;font-family:'Chivo',Arial,sans-serif;font-size:11px;letter-spacing:2px;text-transform:uppercase;opacity:0.85;">
                                        NextGe Copro - Espace Proprietaire
                                    </td>
                                </tr>
                                <tr>
                                    <td style="color:#ffffff;font-family:'Chivo',Arial,sans-serif;font-size:20px;font-weight:700;padding-top:6px;">
                                        Modification depuis l'espace proprietaire
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding:26px 28px 12px 28px;">
                            <p style="margin:0 0 14px 0;font-size:14px;line-height:1.55;color:#0f172a;">
                                Le proprietaire <strong>{_esc(owner_name)}</strong> vient de
                                <strong>{_esc(change_type)}</strong> via son espace proprietaire.
                            </p>

                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-top:1px solid {BORDER_SOFT};border-bottom:1px solid {BORDER_SOFT};margin:8px 0;padding:12px 0;">
                                {copro_row}
                                {email_row}
                                <tr>
                                    <td style="padding:6px 0;color:{TEXT_MUTED};font-size:12px;width:130px;">Date de la modification</td>
                                    <td style="padding:6px 0;color:#0f172a;font-size:13px;">{now_str}</td>
                                </tr>
                            </table>

                            <div style="margin-top:12px;font-size:12px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">
                                Details de la modification
                            </div>
                            {body_block}

                            <p style="margin:16px 0 0 0;font-size:12px;color:{TEXT_MUTED};line-height:1.55;">
                                Aucune action n'est requise. Ces modifications ont deja ete appliquees dans le systeme.
                                Vous pouvez toutefois verifier la fiche du proprietaire depuis l'application syndic.
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background:{BG_SOFT};padding:16px 28px;border-top:1px solid {BORDER_SOFT};">
                            <p style="margin:0;font-size:11px;color:{TEXT_MUTED};line-height:1.5;">
                                Notification automatique envoyee par <strong style="color:{BRAND_COLOR};">NextGe Copro</strong> -
                                Ne pas repondre a ce courriel.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


async def _find_syndic_recipients(db, copropriete_ids: List[str]) -> List[str]:
    """Find email addresses of users with role syndic/gestionnaire who have
    access to at least one of these ACPs. Returns deduped list."""
    if not copropriete_ids:
        copropriete_ids = []
    q = {"role": {"$in": ["syndic", "gestionnaire", "superadmin"]}}
    if copropriete_ids:
        # Match users whose copropriete_ids overlap OR superadmins (no ACP filter for them)
        q = {
            "$or": [
                {"role": "superadmin"},
                {"role": {"$in": ["syndic", "gestionnaire"]},
                 "copropriete_ids": {"$in": copropriete_ids}},
            ]
        }
    users = await db.users.find(q, {"_id": 0, "email": 1, "role": 1}).to_list(50)
    emails = list({u["email"] for u in users if u.get("email")})
    return emails


async def notify_syndic_of_owner_change(
    db,
    owner: dict,
    change_type: str,
    summary_lines: List[str],
    copropriete_ids: Optional[List[str]] = None,
    copropriete_name: str = "",
    changes: Optional[List[dict]] = None,
) -> dict:
    """Persist + (best-effort) email notification. Returns {persisted, emailed, recipients}.

    `changes` (optional) : liste structuree [{label, before, after}] utilisee
    pour le rendu HTML pro. `summary_lines` (obligatoire) reste conserve dans
    la collection Mongo pour compat avec l'UI syndic et les tests iter89.
    """
    owner_id = owner.get("id", "")
    owner_name = owner.get("name", "") or f"{owner.get('last_name','')} {owner.get('first_name','')}".strip()
    copropriete_ids = copropriete_ids or []
    # 1. Persist record (always)
    notif = {
        "id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "owner_name": owner_name,
        "owner_email": owner.get("email", ""),
        "copropriete_ids": copropriete_ids,
        "copropriete_name": copropriete_name,
        "change_type": change_type,
        "summary_lines": summary_lines,
        "changes": changes or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read": False,
        "emailed": False,
    }
    await db.owner_notifications.insert_one(notif)
    # 2. Try email via Graph if configured
    emailed = False
    recipients: List[str] = []
    try:
        from graph_email import is_configured, send_html_email
        if is_configured():
            recipients = await _find_syndic_recipients(db, copropriete_ids)
            if recipients:
                html = _build_html(
                    owner_name=owner_name,
                    owner_email=owner.get("email", ""),
                    change_type=change_type,
                    summary_lines=summary_lines,
                    copropriete_name=copropriete_name,
                    changes=changes,
                )
                copro_prefix = f"[{copropriete_name}] " if copropriete_name else ""
                subject = f"{copro_prefix}Modification proprietaire - {owner_name} - {change_type}"
                await send_html_email(recipients, subject, html)
                emailed = True
                await db.owner_notifications.update_one(
                    {"id": notif["id"]},
                    {"$set": {"emailed": True, "recipients": recipients}}
                )
    except Exception as exc:
        # Best-effort : email failure is silent, the notification is persisted
        logger.warning(
            "Owner change email notif failed (still persisted in DB) : %s", exc
        )
    return {"persisted": True, "emailed": emailed, "recipients": recipients,
            "notification_id": notif["id"]}
