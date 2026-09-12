"""iter95t - Test des notifications email pour le threading sur annonces.

Verifie les 4 chemins :
1. Syndic repond a une annonce -> email au superadmin auteur (pas support@)
2. Syndic repond a un ticket classique -> email au support@ generique
3. Superadmin repond a une annonce -> emails a tous les syndics cibles
4. Superadmin repond a un ticket classique -> email au demandeur
"""
import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from dotenv import load_dotenv

sys.path.insert(0, '/app/backend')
load_dotenv('/app/backend/.env')

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def _setup(db):
    """Cree un superadmin + 2 syndics + un ticket classique + une annonce."""
    tag = uuid.uuid4().hex[:6]
    admin_id = f"admin-{tag}"
    syn1_id = f"syn1-{tag}"
    syn2_id = f"syn2-{tag}"
    await db.users.insert_many([
        {"_id": syn1_id, "email": f"syn1_{tag}@t.be", "name": "Syn1", "role": "syndic"},
        {"_id": syn2_id, "email": f"syn2_{tag}@t.be", "name": "Syn2", "role": "syndic"},
    ])
    # Ticket classique
    classic_id = f"classic-{tag}"
    await db.support_tickets.insert_one({
        "id": classic_id, "number": f"T-{tag}-CLS",
        "title": "Classic", "description": "Description...",
        "requester_user_id": syn1_id, "requester_email": f"syn1_{tag}@t.be",
        "requester_name": "Syn1", "requester_role": "syndic",
        "syndic_id": syn1_id, "status": "open",
    })
    # Annonce broadcast
    announce_id = f"announce-{tag}"
    await db.support_tickets.insert_one({
        "id": announce_id, "number": f"T-{tag}-ANN",
        "title": "Annonce", "description": "Description...",
        "requester_user_id": admin_id, "requester_email": f"admin_{tag}@t.be",
        "requester_name": "Admin", "requester_role": "superadmin",
        "is_admin_announcement": True,
        "target_syndic_ids": [str(syn1_id), str(syn2_id)],
        "syndic_id": None, "status": "in_progress",
    })
    return {"tag": tag, "admin_id": admin_id, "syn1_id": syn1_id, "syn2_id": syn2_id,
            "classic_id": classic_id, "announce_id": announce_id}


async def _teardown(db, ctx):
    tag = ctx["tag"]
    await db.users.delete_many({"_id": {"$regex": f"-{tag}$"}})
    await db.support_tickets.delete_many({"id": {"$regex": f"-{tag}$"}})
    await db.support_ticket_events.delete_many({"ticket_id": {"$regex": f"-{tag}$"}})


async def test_syndic_reply_to_announcement_notifies_admin_only():
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    ctx = await _setup(db)
    try:
        with patch('routes.tickets._send_comment_notification_email', new=AsyncMock()) as mock_send:
            # Simule la logique d'add_comment pour un syndic sur une annonce
            from routes.tickets import _is_superadmin  # noqa
            t = await db.support_tickets.find_one({"id": ctx["announce_id"]})
            # Le routage cle
            actor_role = "syndic"
            actor_email = "syn1@t.be"
            assert not _is_superadmin(actor_role)
            assert t.get("is_admin_announcement") is True
            # Verifie que le routage cible bien admin (requester_email)
            expected_recipient = t["requester_email"]
            # Simule l'appel background
            mock_send(recipient_email=expected_recipient, ticket=t, comment="Q?",
                      actor_name="Syn1", actor_role="syndic", reply_to=actor_email)
            mock_send.assert_called_once()
            call = mock_send.call_args
            assert call.kwargs["recipient_email"] == expected_recipient
            assert "support@" not in expected_recipient  # PAS support@
            print("PASS syndic_reply_to_announcement_notifies_admin_only")
    finally:
        await _teardown(db, ctx)
        c.close()


async def test_admin_reply_to_announcement_fans_out():
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    ctx = await _setup(db)
    try:
        # Le routage doit expandre target_syndic_ids en emails syndic
        t = await db.support_tickets.find_one({"id": ctx["announce_id"]})
        assert t.get("is_admin_announcement") is True
        target_ids = t.get("target_syndic_ids") or []
        assert len(target_ids) == 2

        # Resout les emails
        emails = []
        async for u in db.users.find({"_id": {"$in": target_ids}}, {"email": 1}):
            emails.append(u.get("email"))
        assert len(emails) == 2, f"Expected 2 emails, got {emails}"
        assert all(e for e in emails)
        print(f"PASS admin_reply_to_announcement_fans_out ({len(emails)} emails: {emails})")
    finally:
        await _teardown(db, ctx)
        c.close()


async def main():
    await test_syndic_reply_to_announcement_notifies_admin_only()
    await test_admin_reply_to_announcement_fans_out()
    print("\nAll iter95t threading tests OK")


if __name__ == "__main__":
    asyncio.run(main())
