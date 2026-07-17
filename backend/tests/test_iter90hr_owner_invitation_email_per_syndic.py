"""iter90hr : L'invitation propriétaire utilise maintenant la config MS Graph
par-syndic (db + for_syndic_user_id) au lieu des seules env vars globales.

Avant iter90hr : `owner_access._send_invitation_email` appelait
`send_html_email([email], subject, html)` sans `db` ni `for_syndic_user_id`.
Resultat : en presence de `MAIL_ENABLED=false` (mode dry-run global), les
invitations proprio etaient supprimees meme si le syndic avait une config
Graph valide dans `syndic_configs` -> "Envoi email impossible".

Verification : le code source appelle bien `send_html_email(..., db=db,
for_syndic_user_id=inviter_id_or_none)` et gere `mail_enabled` en tenant
compte de `has_per_syndic`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app/backend")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


OWNER_ACCESS_PATH = "/app/backend/routes/owner_access.py"


def test_send_invitation_email_passes_db_and_syndic_id():
    """`send_html_email` doit etre appele avec db et for_syndic_user_id
    pour permettre l'usage de la config par-syndic."""
    src = _read(OWNER_ACCESS_PATH)
    # Localiser _send_invitation_email
    start = src.index("async def _send_invitation_email")
    end = src.index("# ---------- GET status ----------")
    section = src[start:end]
    # send_html_email doit etre appele avec les kwargs db= et for_syndic_user_id=
    assert "send_html_email(" in section
    assert "db=db" in section
    assert "for_syndic_user_id=" in section


def test_mail_enabled_check_respects_per_syndic_config():
    """`mail_enabled=false` ne doit plus bloquer si has_per_syndic=True."""
    src = _read(OWNER_ACCESS_PATH)
    # Localiser la condition dry-run
    assert "if not mail_enabled and not has_per_syndic:" in src, (
        "La condition dry-run doit tester has_per_syndic pour ne pas bloquer "
        "un envoi lorsque le syndic dispose d'une config Graph en DB."
    )


def test_has_per_syndic_check_uses_is_configured_for_syndic():
    """La detection has_per_syndic doit interroger is_configured_for_syndic
    avec l'id de l'inviter."""
    src = _read(OWNER_ACCESS_PATH)
    assert "is_configured_for_syndic" in src
    assert "inviter_id_precheck" in src, (
        "Doit utiliser un identifiant explicite pour l'inviter afin de "
        "resoudre sa config Graph."
    )


def test_return_value_flags_sent_correctly():
    """Le retour doit exposer invitation_sent + invitation_reason + link."""
    src = _read(OWNER_ACCESS_PATH)
    assert 'result["sent"] = True' in src, "Un envoi reussi doit setter sent=True"
    assert 'result["reason"] = "sent"' in src or "result['reason'] = 'sent'" in src
    assert '"invitation_link"' in src
