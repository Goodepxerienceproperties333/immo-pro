"""Routes for managing platform access of owners (iter90).

Workflow:
- A syndic clicks "Activer l'acces" on an owner's profile.
  -> POST /api/owners/{owner_id}/grant-access
  -> If a user already exists with the owner's email: link it (owner.user_id) and
     enable it (clear is_suspended). User keeps current password if any, else
     a fresh invitation is sent.
  -> If no user exists: create one with role='owner', must_change_password=True,
     unguessable placeholder password, and send invitation email via Microsoft Graph.
- "Renvoyer invitation" : POST /api/owners/{id}/resend-invitation
- "Desactiver" : POST /api/owners/{id}/revoke-access -> sets user.is_suspended=True
- "Reactiver"  : POST /api/owners/{id}/reactivate-access -> clears is_suspended

Chinese wall: the syndic must have at least one ACP in common with the owner
(via lots.owner_id / lots.owner_ids). Superadmin bypasses.
"""
from fastapi import APIRouter, HTTPException, Request
from bson import ObjectId
from datetime import datetime, timezone
import os
import uuid
import asyncio
import logging

logger = logging.getLogger(__name__)


def create_owner_access_router(db):
    router = APIRouter(prefix="/api/owners")

    async def _get_admin_scope(request: Request):
        """Returns (is_super, allowed_copro_ids). Only syndic/superadmin can manage access."""
        from server import get_current_user, is_admin_role, is_superadmin_only
        user = await get_current_user(request)
        if not is_admin_role(user.get("role", "")):
            raise HTTPException(403, "Acces reserve a l'administration")
        return user, is_superadmin_only(user.get("role", "")), user.get("copropriete_ids", []) or []

    async def _log_audit(
        *, action: str, actor: dict, owner: dict, user_doc: dict | None = None,
        details: dict | None = None, request: Request | None = None,
    ):
        """Insert an entry in owner_access_audit. Best-effort (logs but never raises)."""
        try:
            ip = ""
            ua = ""
            if request is not None:
                ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                      or (request.client.host if request.client else "")
                      or "")
                ua = (request.headers.get("user-agent") or "")[:300]
            await db.owner_access_audit.insert_one({
                "id": str(uuid.uuid4()),
                "action": action,  # 'grant' | 'resend' | 'revoke' | 'reactivate'
                "owner_id": owner.get("id"),
                "owner_name": owner.get("name") or "",
                "owner_email": (owner.get("email") or "").lower(),
                "target_user_id": (str(user_doc["_id"]) if user_doc and user_doc.get("_id") else None),
                "target_user_email": (user_doc or {}).get("email", "").lower() if user_doc else None,
                "actor_user_id": str(actor.get("_id") or actor.get("id") or ""),
                "actor_email": actor.get("email", ""),
                "actor_name": actor.get("name", ""),
                "actor_role": actor.get("role", ""),
                "ip": ip,
                "user_agent": ua,
                "details": details or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.warning(f"owner_access_audit insert failed: {e}")

    async def _owner_in_scope(owner_id: str, allowed_copros) -> bool:
        c1 = await db.lots.count_documents({
            "owner_id": owner_id,
            "copropriete_id": {"$in": allowed_copros}
        })
        if c1 > 0:
            return True
        c2 = await db.lots.count_documents({
            "owner_ids": owner_id,
            "copropriete_id": {"$in": allowed_copros}
        })
        return c2 > 0

    async def _load_owner_or_404(owner_id: str, is_super: bool, allowed_copros):
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            raise HTTPException(404, "Proprietaire non trouve")
        return owner

    def _serialize_status(owner: dict, user: dict | None) -> dict:
        if not user:
            return {
                "has_access": False,
                "status": "none",
                "user_id": None,
                "user_email": None,
                "must_change_password": False,
                "is_suspended": False,
                "last_login_at": None,
            }
        suspended = bool(user.get("is_suspended"))
        must = bool(user.get("must_change_password"))
        if suspended:
            status = "suspended"
        elif must:
            status = "pending"  # invited, awaiting first-set-password
        else:
            status = "active"
        return {
            "has_access": True,
            "status": status,
            "user_id": str(user["_id"]),
            "user_email": user.get("email", ""),
            "must_change_password": must,
            "is_suspended": suspended,
            "last_login_at": user.get("last_login_at"),
        }

    async def _find_linked_user(owner: dict) -> dict | None:
        """Find the user account linked to this owner.
        Priority: owner.user_id (link by id) > user.email == owner.email."""
        uid = owner.get("user_id")
        if uid:
            try:
                u = await db.users.find_one({"_id": ObjectId(uid)})
                if u:
                    return u
            except Exception:
                pass
        email = (owner.get("email") or "").lower().strip()
        if email:
            u = await db.users.find_one({"email": email})
            return u
        return None

    def _build_setup_url(email: str) -> str:
        frontend_url = os.environ.get("FRONTEND_URL", "")
        return f"{frontend_url}/login?invite={email}"

    async def _send_invitation_email(email: str, recipient_name: str, inviter: dict | None):
        """Sends the invitation email in background (non-blocking, logs failures)."""
        try:
            from graph_email import is_configured, send_html_email, build_invitation_email
            if not is_configured():
                logger.warning(f"MSGRAPH not configured; invitation link for {email}: {_build_setup_url(email)}")
                return False
            subject, html = build_invitation_email(
                recipient_name=recipient_name or "Proprietaire",
                role_label="Proprietaire",
                setup_url=_build_setup_url(email),
                inviter_name=(inviter or {}).get("name"),
                inviter_email=(inviter or {}).get("email"),
            )
            asyncio.create_task(send_html_email([email], subject, html))
            return True
        except Exception as e:
            logger.warning(f"Owner invitation email failed for {email}: {e}")
            return False

    # ---------- GET status ----------
    @router.get("/{owner_id}/access-status")
    async def get_access_status(owner_id: str, request: Request):
        _, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        user = await _find_linked_user(owner)
        return _serialize_status(owner, user)

    # ---------- POST grant-access ----------
    @router.post("/{owner_id}/grant-access")
    async def grant_access(owner_id: str, request: Request):
        inviter, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        email = (owner.get("email") or "").lower().strip()
        if not email or "@" not in email:
            raise HTTPException(400, "Le proprietaire doit avoir une adresse email valide pour recevoir son invitation.")
        from server import hash_password
        name = owner.get("name") or f"{owner.get('first_name','')} {owner.get('last_name','')}".strip() or "Proprietaire"
        existing = await db.users.find_one({"email": email})
        invitation_sent = False
        if existing:
            # User already exists -> link + re-enable. (1a : single account multi-ACP)
            update = {}
            if existing.get("role") != "owner":
                # Do NOT downgrade an existing syndic/admin/gestionnaire to owner.
                # Keep their role but still link the owner card to the same user.
                pass
            if existing.get("is_suspended"):
                update["is_suspended"] = False
                update["reactivated_at"] = datetime.now(timezone.utc).isoformat()
            if update:
                await db.users.update_one({"_id": existing["_id"]}, {"$set": update})
            # Link owner -> user
            if owner.get("user_id") != str(existing["_id"]):
                await db.owners.update_one(
                    {"id": owner_id},
                    {"$set": {"user_id": str(existing["_id"]),
                              "access_granted_at": datetime.now(timezone.utc).isoformat(),
                              "access_granted_by": str(inviter.get("_id", ""))}}
                )
            # Resend invitation only if the user has not yet set a password.
            if existing.get("must_change_password"):
                invitation_sent = await _send_invitation_email(email, name, inviter)
            refreshed = await db.users.find_one({"_id": existing["_id"]})
            owner_after = await db.owners.find_one({"id": owner_id}, {"_id": 0})
            await _log_audit(
                action="grant", actor=inviter, owner=owner_after or owner,
                user_doc=refreshed, request=request,
                details={"linked_existing_user": True, "invitation_sent": invitation_sent},
            )
            return {
                "message": "Acces active (compte existant lie)",
                "linked_existing_user": True,
                "invitation_sent": invitation_sent,
                "status": _serialize_status(owner_after, refreshed),
            }
        # No user yet -> create one with must_change_password
        placeholder = uuid.uuid4().hex + uuid.uuid4().hex
        doc = {
            "email": email,
            "password_hash": hash_password(placeholder),
            "name": name,
            "role": "owner",
            "copropriete_ids": owner.get("copropriete_ids", []) or [],
            "must_change_password": True,
            "is_suspended": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": str(inviter.get("_id", "")),
        }
        result = await db.users.insert_one(doc)
        user_id_str = str(result.inserted_id)
        await db.owners.update_one(
            {"id": owner_id},
            {"$set": {"user_id": user_id_str,
                      "access_granted_at": datetime.now(timezone.utc).isoformat(),
                      "access_granted_by": str(inviter.get("_id", ""))}}
        )
        invitation_sent = await _send_invitation_email(email, name, inviter)
        new_user = await db.users.find_one({"_id": result.inserted_id})
        owner_after = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        await _log_audit(
            action="grant", actor=inviter, owner=owner_after or owner,
            user_doc=new_user, request=request,
            details={"linked_existing_user": False, "invitation_sent": invitation_sent},
        )
        return {
            "message": "Acces active. Invitation envoyee.",
            "linked_existing_user": False,
            "invitation_sent": invitation_sent,
            "status": _serialize_status(owner_after, new_user),
        }

    # ---------- POST resend-invitation ----------
    @router.post("/{owner_id}/resend-invitation")
    async def resend_invitation(owner_id: str, request: Request):
        inviter, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        user = await _find_linked_user(owner)
        if not user:
            raise HTTPException(404, "Aucun compte lie. Activez d'abord l'acces.")
        if not user.get("must_change_password"):
            raise HTTPException(
                400,
                "Ce compte a deja defini un mot de passe. "
                "Le proprietaire peut utiliser 'Mot de passe oublie' depuis la page de connexion."
            )
        email = user.get("email")
        name = user.get("name") or owner.get("name") or "Proprietaire"
        sent = await _send_invitation_email(email, name, inviter)
        await _log_audit(
            action="resend", actor=inviter, owner=owner, user_doc=user,
            request=request, details={"invitation_sent": sent},
        )
        return {"message": "Invitation renvoyee" if sent else "Invitation enregistree (envoi differe)", "invitation_sent": sent}

    # ---------- POST revoke-access ----------
    @router.post("/{owner_id}/revoke-access")
    async def revoke_access(owner_id: str, request: Request):
        actor, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        user = await _find_linked_user(owner)
        if not user:
            raise HTTPException(404, "Aucun compte lie a ce proprietaire.")
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"is_suspended": True,
                      "suspended_at": datetime.now(timezone.utc).isoformat()}}
        )
        # Invalidate any active reset tokens (defense in depth)
        await db.password_reset_tokens.update_many(
            {"user_id": str(user["_id"]), "consumed_at": None},
            {"$set": {"consumed_at": datetime.now(timezone.utc).isoformat()}}
        )
        refreshed = await db.users.find_one({"_id": user["_id"]})
        await _log_audit(
            action="revoke", actor=actor, owner=owner, user_doc=refreshed, request=request,
        )
        return {
            "message": "Acces suspendu",
            "status": _serialize_status(owner, refreshed),
        }

    # ---------- POST reactivate-access ----------
    @router.post("/{owner_id}/reactivate-access")
    async def reactivate_access(owner_id: str, request: Request):
        actor, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        user = await _find_linked_user(owner)
        if not user:
            raise HTTPException(404, "Aucun compte lie a ce proprietaire.")
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"is_suspended": False,
                      "reactivated_at": datetime.now(timezone.utc).isoformat()},
             "$unset": {"suspended_at": ""}}
        )
        refreshed = await db.users.find_one({"_id": user["_id"]})
        await _log_audit(
            action="reactivate", actor=actor, owner=owner, user_doc=refreshed, request=request,
        )
        return {
            "message": "Acces reactive",
            "status": _serialize_status(owner, refreshed),
        }

    # ---------- GET access-audit (history) ----------
    @router.get("/{owner_id}/access-audit")
    async def get_access_audit(owner_id: str, request: Request, limit: int = 50):
        """Returns the access-events history (grant/resend/revoke/reactivate)
        for a specific owner. Scoped by chinese wall : the requester must
        have access to this owner. Most recent first."""
        _, is_super, allowed_copros = await _get_admin_scope(request)
        await _load_owner_or_404(owner_id, is_super, allowed_copros)
        limit = max(1, min(int(limit or 50), 200))
        entries = await db.owner_access_audit.find(
            {"owner_id": owner_id},
            {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)
        return {"entries": entries, "count": len(entries)}

    return router
