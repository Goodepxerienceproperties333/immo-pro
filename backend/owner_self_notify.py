"""iter89 - Notification syndic when an owner self-services from the portal.

Triggered by :
  - PUT /api/owner/me (owner updates his coordinates)
  - POST/PUT/DELETE /api/owner/tenants (owner CRUDs his tenants)

Strategy :
  1. Always persist a notification record in `db.owner_notifications`
     (so the syndic sees it in the UI even if email fails).
  2. Best-effort send via Microsoft Graph email if configured. We do NOT
     block the user response if email fails (logged + silent).
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("owner_self_notify")


def _build_html(owner_name: str, change_type: str, summary_lines: List[str],
                copropriete_name: str = "") -> str:
    """Build a simple HTML notification email."""
    lines_html = "\n".join(f"<li>{line}</li>" for line in summary_lines)
    copro_block = (
        f'<p style="color:#666;font-size:13px;margin-top:4px;">Copropriete : <strong>{copropriete_name}</strong></p>'
        if copropriete_name else ""
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
        <h2 style="color:#0055FF;font-family:'Chivo',Arial,sans-serif;margin-bottom:6px;">
            Modification depuis l'espace proprietaire
        </h2>
        {copro_block}
        <p>Le proprietaire <strong>{owner_name}</strong> vient de
        <strong>{change_type}</strong> via son espace proprietaire.</p>
        <ul style="background:#f7f9fc;padding:12px 24px;border-left:3px solid #0055FF;border-radius:4px;">
            {lines_html}
        </ul>
        <p style="font-size:12px;color:#888;margin-top:24px;">
            Notification automatique - NextGe Copro (iter89)
        </p>
    </body></html>
    """


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
) -> dict:
    """Persist + (best-effort) email notification. Returns {persisted, emailed, recipients}."""
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
                html = _build_html(owner_name, change_type, summary_lines, copropriete_name)
                subject = f"[NextGe Copro] {owner_name} - {change_type}"
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
