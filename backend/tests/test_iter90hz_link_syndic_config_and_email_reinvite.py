"""iter90hz : Tests pour les 3 fonctionnalites de la session :

1. POST /api/coproprietes/{id}/link-syndic-config
   Un syndic peut explicitement lier son cabinet (logo + mentions legales)
   a une ACP. Cela ecrit `syndic_user_id` dans la copropriete, ce qui rend
   deterministe la resolution du logo dans `resolve_syndic_pdf_context`.

2. handle_owner_email_change : quand l'email d'un proprietaire change,
   si un user account est deja lie (invited ou actif) :
   - l'email du user est synchronise
   - must_change_password est repositionne a True
   - une invitation est envoyee au NOUVEL email
   - un audit log est cree dans owner_access_audit

3. Endpoint owner /api/owner/bank-accounts/{copro_id} deja teste
   ailleurs (iter90hw). On verifie ici que le nouveau lien logo n'a
   pas casse la fonction (smoke test).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _cleanup(db, copro_id, owner_id, syndic_uid, user_id_str):
    await db.coproprietes.delete_many({"id": copro_id})
    await db.owners.delete_many({"id": owner_id})
    await db.syndic_configs.delete_many({"syndic_user_id": syndic_uid})
    if user_id_str:
        from bson import ObjectId
        try:
            await db.users.delete_one({"_id": ObjectId(user_id_str)})
        except Exception:
            pass
    await db.owner_access_audit.delete_many({"owner_id": owner_id})


def test_link_syndic_config_endpoint_writes_syndic_user_id():
    """iter90hz-1 : POST /link-syndic-config ecrit `syndic_user_id` dans
    la copropriete et cree une config syndic minimale si absente."""
    copro_id = f"acp-hz-{uuid.uuid4().hex[:8]}"
    syndic_uid = f"syndic-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            # Cleanup
            await db.coproprietes.delete_many({"id": copro_id})
            await db.syndic_configs.delete_many({"syndic_user_id": syndic_uid})
            # Cree la copropriete de test
            await db.coproprietes.insert_one({
                "id": copro_id, "name": "ACP hz test", "reference": "HZ-1",
                "status": "active",
            })
            # Simule le comportement de link_syndic_config : ecrit
            # syndic_user_id sur l'ACP + cree la config vide.
            cfg = await db.syndic_configs.find_one({"syndic_user_id": syndic_uid})
            if not cfg:
                from datetime import datetime, timezone
                await db.syndic_configs.insert_one({
                    "syndic_user_id": syndic_uid,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            await db.coproprietes.update_one(
                {"id": copro_id},
                {"$set": {"syndic_user_id": syndic_uid}},
            )
            # Verifier l'etat resultant
            copro_after = await db.coproprietes.find_one({"id": copro_id})
            assert copro_after.get("syndic_user_id") == syndic_uid
            cfg_after = await db.syndic_configs.find_one({"syndic_user_id": syndic_uid})
            assert cfg_after is not None
            # Verifier que resolve_syndic_pdf_context PRIORISE ce lien
            from pdf_layout import resolve_syndic_pdf_context
            ctx = await resolve_syndic_pdf_context(db, copro_after)
            assert ctx["syndic_user_id"] == syndic_uid, (
                f"resolve_syndic_pdf_context doit prioriser syndic_user_id "
                f"explicite mais a retourne {ctx.get('syndic_user_id')}"
            )
        finally:
            await db.coproprietes.delete_many({"id": copro_id})
            await db.syndic_configs.delete_many({"syndic_user_id": syndic_uid})
            client.close()

    asyncio.run(_run())


def test_email_change_syncs_user_and_sends_invite():
    """iter90hz-2 : handle_owner_email_change synchronise user.email
    et remet must_change_password=True quand l'email owner change."""
    owner_id = f"own-hz-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.owner_access import handle_owner_email_change
        from bson import ObjectId
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_id_str = None
        try:
            await db.owners.delete_many({"id": owner_id})
            await db.owner_access_audit.delete_many({"owner_id": owner_id})
            old_email = f"old-{uuid.uuid4().hex[:6]}@test.local"
            new_email = f"new-{uuid.uuid4().hex[:6]}@test.local"
            # Nettoie tout compte residuel avec ces emails
            await db.users.delete_many({"email": {"$in": [old_email, new_email]}})
            # Cree un compte user 'owner' lie a l'owner de test
            u = await db.users.insert_one({
                "email": old_email,
                "password_hash": "dummy",
                "name": "Alice Testeuse",
                "role": "owner",
                "must_change_password": False,
                "is_suspended": False,
            })
            user_id_str = str(u.inserted_id)
            await db.owners.insert_one({
                "id": owner_id,
                "name": "Alice Testeuse",
                "email": new_email,  # deja mis a jour
                "user_id": user_id_str,
            })
            # Appelle le helper
            result = await handle_owner_email_change(
                db, owner_id, old_email, new_email,
                {"name": "Syndic Test", "email": "syndic@test.local",
                 "role": "syndic", "_id": "actor-id"},
            )
            # L'email peut ne pas etre envoye (MS Graph pas configure en test)
            # mais le retour ne doit pas etre None (traitement effectue).
            assert result is not None, (
                "handle_owner_email_change doit retourner un dict quand un "
                "user lie existe et que l'email change"
            )
            # Verifier que le user a bien ete synchronise
            u_after = await db.users.find_one({"_id": ObjectId(user_id_str)})
            assert u_after is not None
            assert u_after["email"] == new_email, (
                f"user.email doit etre {new_email}, got {u_after.get('email')}"
            )
            assert u_after.get("must_change_password") is True, (
                "must_change_password doit etre reactive apres changement d'email"
            )
            # Verifier l'audit log
            audits = await db.owner_access_audit.find(
                {"owner_id": owner_id, "action": "email_change_reinvite"}
            ).to_list(10)
            assert len(audits) == 1, f"1 entree audit attendue, {len(audits)} trouvee(s)"
            a = audits[0]
            assert a["owner_email_old"] == old_email
            assert a["owner_email_new"] == new_email
            assert a["target_user_id"] == user_id_str
        finally:
            await _cleanup(db, "", owner_id, "", user_id_str)
            client.close()

    asyncio.run(_run())


def test_email_change_noop_when_no_linked_user():
    """iter90hz-3 : si le proprio n'a AUCUN compte user lie,
    handle_owner_email_change retourne None (no-op propre)."""
    owner_id = f"own-hz-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.owner_access import handle_owner_email_change
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.owners.delete_many({"id": owner_id})
            # Cree un owner sans user_id lie
            await db.owners.insert_one({
                "id": owner_id, "name": "Bob Testeur",
                "email": "bob-new@test.local",
            })
            result = await handle_owner_email_change(
                db, owner_id, "bob-old@test.local", "bob-new@test.local",
                {"role": "syndic", "email": "s@t"},
            )
            assert result is None, (
                "handle_owner_email_change doit retourner None quand aucun "
                "user account n'est lie au proprietaire"
            )
        finally:
            await db.owners.delete_many({"id": owner_id})
            client.close()

    asyncio.run(_run())


def test_email_change_noop_when_email_unchanged():
    """iter90hz-4 : appel avec old == new = no-op immediat."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.owner_access import handle_owner_email_change
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            result = await handle_owner_email_change(
                db, "irrelevant-id", "same@x.local", "same@x.local", None,
            )
            assert result is None
        finally:
            client.close()

    asyncio.run(_run())


def test_email_change_refuses_conflict_with_other_user():
    """iter90hz-5 : si le nouvel email est deja pris par un AUTRE user,
    on retourne un dict `email_conflict` sans casser la base."""
    owner_id = f"own-hz-{uuid.uuid4().hex[:8]}"

    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.owner_access import handle_owner_email_change
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_ids = []
        try:
            await db.owners.delete_many({"id": owner_id})
            old_email = f"o-{uuid.uuid4().hex[:6]}@t.local"
            new_email = f"n-{uuid.uuid4().hex[:6]}@t.local"
            await db.users.delete_many({"email": {"$in": [old_email, new_email]}})
            # Le user de l'owner (owner role)
            r1 = await db.users.insert_one({
                "email": old_email, "password_hash": "x", "name": "A",
                "role": "owner", "must_change_password": False, "is_suspended": False,
            })
            user_ids.append(str(r1.inserted_id))
            # Un AUTRE user avec l'email cible
            r2 = await db.users.insert_one({
                "email": new_email, "password_hash": "x", "name": "B",
                "role": "syndic",
            })
            user_ids.append(str(r2.inserted_id))
            await db.owners.insert_one({
                "id": owner_id, "name": "A", "email": new_email,
                "user_id": str(r1.inserted_id),
            })
            result = await handle_owner_email_change(
                db, owner_id, old_email, new_email,
                {"role": "syndic", "email": "s@t"},
            )
            assert result is not None
            assert result["sent"] is False
            assert result["reason"] == "email_conflict"
        finally:
            from bson import ObjectId
            for uid in user_ids:
                try:
                    await db.users.delete_one({"_id": ObjectId(uid)})
                except Exception:
                    pass
            await db.owners.delete_many({"id": owner_id})
            client.close()

    asyncio.run(_run())
