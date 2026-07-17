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


# iter90hz : Fonctions module-level reutilisables pour la synchronisation
# email owner <-> user account. Appelees depuis owner_portal.py (self-update)
# et properties.py (syndic update) pour renvoyer automatiquement une
# invitation quand l'email du proprietaire change.

def _build_setup_url_module(email: str) -> str:
    """Construit l'URL de set-password / invitation. Duplique intentionnellement
    _build_setup_url pour rester utilisable hors closure du router."""
    frontend_url = os.environ.get("FRONTEND_URL", "")
    return f"{frontend_url}/login?invite={email}"


async def _send_invitation_email_module(
    db, email: str, recipient_name: str, inviter: dict | None,
) -> dict:
    """Version module-level de _send_invitation_email (miroir strict, utilisable
    depuis d'autres routers). Renvoie le meme dict {sent, reason, detail,
    invitation_link}."""
    setup_url = _build_setup_url_module(email)
    result = {"sent": False, "reason": "unknown", "detail": "", "invitation_link": setup_url}
    try:
        from graph_email import is_configured, send_html_email, build_invitation_email
        if not is_configured():
            logger.warning(f"MSGRAPH not configured; invitation link for {email}: {setup_url}")
            result["reason"] = "not_configured"
            result["detail"] = (
                "MS Graph n'est pas configure sur ce serveur. "
                "Le lien d'invitation ci-dessous peut etre transmis manuellement."
            )
            return result
        mail_enabled = os.environ.get("MAIL_ENABLED", "true").lower() != "false"
        inviter_id = str((inviter or {}).get("_id", "")) if inviter else ""
        has_per_syndic = False
        if inviter_id:
            try:
                from graph_email import is_configured_for_syndic
                has_per_syndic = await is_configured_for_syndic(db, inviter_id)
            except Exception:
                has_per_syndic = False
        subject, html = build_invitation_email(
            recipient_name=recipient_name or "Proprietaire",
            role_label="Proprietaire",
            setup_url=setup_url,
            inviter_name=(inviter or {}).get("name"),
            inviter_email=(inviter or {}).get("email"),
        )
        try:
            await send_html_email(
                [email], subject, html,
                db=db,
                for_syndic_user_id=inviter_id or None,
            )
        except Exception as e:
            logger.exception(f"Owner invitation email SEND FAILED for {email}: {e}")
            result["reason"] = "graph_error"
            result["detail"] = f"Erreur Microsoft Graph : {str(e)[:250]}"
            return result
        if not mail_enabled and not has_per_syndic:
            logger.info(f"[DRY-RUN] Invitation suppressed for {email}; link: {setup_url}")
            result["reason"] = "dry_run"
            result["detail"] = (
                "Envoi email desactive sur ce serveur (MAIL_ENABLED=false). "
                "Le lien d'invitation ci-dessous peut etre transmis manuellement."
            )
            return result
        logger.info(f"Owner invitation email sent to {email}")
        result["sent"] = True
        result["reason"] = "sent"
        result["detail"] = "Email d'invitation envoye avec succes."
        return result
    except Exception as e:
        logger.exception(f"Unexpected error preparing invitation for {email}: {e}")
        result["reason"] = "unexpected_error"
        result["detail"] = str(e)[:250]
        return result


async def handle_owner_email_change(
    db,
    owner_id: str,
    old_email: str,
    new_email: str,
    actor: dict | None = None,
) -> dict | None:
    """iter90hz : Quand l'email d'un proprietaire change, si le proprietaire
    possede un compte user lie (invited ou actif), synchroniser :
       1. user.email = new_email   (car l'email est la cle de connexion)
       2. user.must_change_password = True (force re-set du mot de passe
          avec le nouveau email pour prevenir les detournements de compte)
       3. Envoyer une nouvelle invitation au NEW email
       4. Auditer l'evenement dans owner_access_audit

    Retourne un dict compatible avec les autres endpoints (sent, reason,
    detail, invitation_link) OU None si aucun compte lie / pas de vrai
    changement d'email.
    """
    old = (old_email or "").lower().strip()
    new = (new_email or "").lower().strip()
    if not new or old == new or "@" not in new:
        return None
    owner_doc = await db.owners.find_one({"id": owner_id})
    if not owner_doc:
        return None
    # Chercher le user lie : via user_id (source primaire) puis fallback email
    user = None
    uid = owner_doc.get("user_id")
    if uid:
        try:
            user = await db.users.find_one({"_id": ObjectId(uid)})
        except Exception:
            user = None
    if not user and old:
        user = await db.users.find_one({"email": old})
    if not user:
        return None
    # Securite : ne jamais toucher un compte non-owner (email partage avec
    # un compte syndic/gestionnaire/superadmin). On log et on skip.
    if user.get("role") != "owner":
        logger.info(
            f"handle_owner_email_change: user {user.get('email')} is role="
            f"{user.get('role')}, skipping email sync",
        )
        return None
    # Verifier l'unicite de l'email cible
    conflict = await db.users.find_one({"email": new, "_id": {"$ne": user["_id"]}})
    if conflict:
        return {
            "sent": False,
            "reason": "email_conflict",
            "detail": (
                f"Un autre compte utilise deja l'email {new}. Impossible de "
                f"synchroniser le compte de connexion du proprietaire."
            ),
            "invitation_link": _build_setup_url_module(new),
        }
    now_iso = datetime.now(timezone.utc).isoformat()
    # 1 + 2 : synchroniser user.email + forcer re-invitation
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "email": new,
            "must_change_password": True,
            "email_changed_at": now_iso,
        }},
    )
    # Invalider les tokens reset actifs (defense-in-depth : ils pointent
    # peut-etre encore vers l'ancienne adresse)
    try:
        await db.password_reset_tokens.update_many(
            {"user_id": str(user["_id"]), "consumed_at": None},
            {"$set": {"consumed_at": now_iso}},
        )
    except Exception as e:
        logger.warning(f"reset tokens invalidation failed: {e}")
    # 3 : envoyer invitation au NOUVEL email
    name = user.get("name") or owner_doc.get("name") or "Proprietaire"
    invitation = await _send_invitation_email_module(db, new, name, actor)
    # 4 : audit trail
    try:
        await db.owner_access_audit.insert_one({
            "id": str(uuid.uuid4()),
            "action": "email_change_reinvite",
            "owner_id": owner_id,
            "owner_name": owner_doc.get("name") or "",
            "owner_email_old": old,
            "owner_email_new": new,
            "target_user_id": str(user["_id"]),
            "target_user_email": new,
            "actor_user_id": str((actor or {}).get("_id") or (actor or {}).get("id") or ""),
            "actor_email": (actor or {}).get("email", ""),
            "actor_role": (actor or {}).get("role", ""),
            "details": {
                "invitation_sent": invitation.get("sent"),
                "invitation_reason": invitation.get("reason"),
            },
            "created_at": now_iso,
        })
    except Exception as e:
        logger.warning(f"owner_access_audit insert (email_change_reinvite) failed: {e}")
    return invitation


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

    async def _send_invitation_email(email: str, recipient_name: str, inviter: dict | None) -> dict:
        """iter90ca : envoi SYNCHRONE (await) de l'invitation avec retour detaille.

        Retourne un dict :
          - sent: bool          -> True si l'email a effectivement ete accepte par Graph
          - reason: str         -> code : "sent" / "dry_run" / "not_configured" / "graph_error"
          - detail: str         -> message d'erreur explicite si echec
          - invitation_link: str -> URL a communiquer manuellement en cas d'echec
        """
        setup_url = _build_setup_url(email)
        result = {"sent": False, "reason": "unknown", "detail": "", "invitation_link": setup_url}
        try:
            from graph_email import is_configured, send_html_email, build_invitation_email
            if not is_configured():
                logger.warning(f"MSGRAPH not configured; invitation link for {email}: {setup_url}")
                result["reason"] = "not_configured"
                result["detail"] = (
                    "MS Graph n'est pas configure sur ce serveur "
                    "(AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / GRAPH_SENDER_UPN manquants). "
                    "Le lien d'invitation ci-dessous peut etre transmis manuellement."
                )
                return result
            mail_enabled = os.environ.get("MAIL_ENABLED", "true").lower() != "false"
            # iter90hr : si l'inviter a une config par-syndic Graph valide en DB,
            # celle-ci bypass MAIL_ENABLED (le syndic a explicitement fourni ses
            # credentials -> l'envoi est reel meme en dry-run global).
            inviter_id_precheck = str((inviter or {}).get("_id", "")) if inviter else ""
            has_per_syndic = False
            if inviter_id_precheck:
                try:
                    from graph_email import is_configured_for_syndic
                    has_per_syndic = await is_configured_for_syndic(db, inviter_id_precheck)
                except Exception:
                    has_per_syndic = False
            subject, html = build_invitation_email(
                recipient_name=recipient_name or "Proprietaire",
                role_label="Proprietaire",
                setup_url=setup_url,
                inviter_name=(inviter or {}).get("name"),
                inviter_email=(inviter or {}).get("email"),
            )
            # iter90ca/hr : await bloquant + config par-syndic pour bypass
            # MAIL_ENABLED=false quand une config MS Graph est en DB.
            inviter_id = str((inviter or {}).get("_id", "")) if inviter else ""
            try:
                await send_html_email(
                    [email], subject, html,
                    db=db,
                    for_syndic_user_id=inviter_id or None,
                )
            except Exception as e:
                logger.exception(f"Owner invitation email SEND FAILED for {email}: {e}")
                result["reason"] = "graph_error"
                result["detail"] = f"Erreur Microsoft Graph : {str(e)[:250]}"
                return result
            if not mail_enabled and not has_per_syndic:
                # send_html_email a retourne sans exception mais MAIL_ENABLED=false
                # ET aucune config par-syndic -> dry-run
                logger.info(f"[DRY-RUN] Invitation suppressed for {email}; link: {setup_url}")
                result["reason"] = "dry_run"
                result["detail"] = (
                    "Envoi email desactive sur ce serveur (MAIL_ENABLED=false). "
                    "Le lien d'invitation ci-dessous peut etre transmis manuellement."
                )
                return result
            logger.info(f"Owner invitation email sent to {email}")
            result["sent"] = True
            result["reason"] = "sent"
            result["detail"] = "Email d'invitation envoye avec succes."
            return result
        except Exception as e:  # pragma: no cover - safety net
            logger.exception(f"Unexpected error preparing invitation for {email}: {e}")
            result["reason"] = "unexpected_error"
            result["detail"] = str(e)[:250]
            return result

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
        invitation_result = {"sent": False, "reason": "skipped", "detail": "", "invitation_link": ""}
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
                invitation_result = await _send_invitation_email(email, name, inviter)
            refreshed = await db.users.find_one({"_id": existing["_id"]})
            owner_after = await db.owners.find_one({"id": owner_id}, {"_id": 0})
            await _log_audit(
                action="grant", actor=inviter, owner=owner_after or owner,
                user_doc=refreshed, request=request,
                details={"linked_existing_user": True, "invitation_sent": invitation_result.get("sent"),
                         "invitation_reason": invitation_result.get("reason")},
            )
            return {
                "message": "Acces active (compte existant lie)",
                "linked_existing_user": True,
                "invitation_sent": invitation_result.get("sent", False),
                "invitation_reason": invitation_result.get("reason", ""),
                "invitation_detail": invitation_result.get("detail", ""),
                "invitation_link": invitation_result.get("invitation_link", ""),
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
        invitation_result = await _send_invitation_email(email, name, inviter)
        new_user = await db.users.find_one({"_id": result.inserted_id})
        owner_after = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        await _log_audit(
            action="grant", actor=inviter, owner=owner_after or owner,
            user_doc=new_user, request=request,
            details={"linked_existing_user": False, "invitation_sent": invitation_result.get("sent"),
                     "invitation_reason": invitation_result.get("reason")},
        )
        return {
            "message": "Acces active. Invitation envoyee." if invitation_result.get("sent") else "Acces active. Envoi email impossible - communiquez le lien d'invitation manuellement.",
            "linked_existing_user": False,
            "invitation_sent": invitation_result.get("sent", False),
            "invitation_reason": invitation_result.get("reason", ""),
            "invitation_detail": invitation_result.get("detail", ""),
            "invitation_link": invitation_result.get("invitation_link", ""),
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
        invitation_result = await _send_invitation_email(email, name, inviter)
        await _log_audit(
            action="resend", actor=inviter, owner=owner, user_doc=user,
            request=request, details={"invitation_sent": invitation_result.get("sent"),
                                       "invitation_reason": invitation_result.get("reason")},
        )
        sent = invitation_result.get("sent", False)
        if sent:
            message = f"Invitation renvoyee a {email}"
        elif invitation_result.get("reason") == "dry_run":
            message = "Envoi email indisponible (mode maintenance) - transmettez le lien manuellement."
        elif invitation_result.get("reason") == "not_configured":
            message = "MS Graph non configure - transmettez le lien manuellement."
        else:
            message = f"Echec envoi email : {invitation_result.get('detail', 'erreur inconnue')}"
        return {
            "message": message,
            "invitation_sent": sent,
            "invitation_reason": invitation_result.get("reason", ""),
            "invitation_detail": invitation_result.get("detail", ""),
            "invitation_link": invitation_result.get("invitation_link", ""),
        }

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

    # ---------- DELETE access (destructive, suppression du compte user) ----------
    # iter90g : suppression complete de l'acces (vs. revoke qui suspend).
    # Cas d'usage : un proprio quitte definitivement, ou erreur d'invitation
    # qu'on veut purger pour recommencer a zero. Idempotent : 200 OK si rien a faire.
    @router.delete("/{owner_id}/access")
    async def delete_access(owner_id: str, request: Request):
        actor, is_super, allowed_copros = await _get_admin_scope(request)
        owner = await _load_owner_or_404(owner_id, is_super, allowed_copros)
        user = await _find_linked_user(owner)
        if not user:
            # Detacher le user_id residuel et retourner OK (idempotent)
            await db.owners.update_one({"id": owner_id}, {"$unset": {"user_id": ""}})
            return {
                "message": "Aucun acces a supprimer",
                "deleted": False,
                "status": _serialize_status(owner, None),
            }
        # Securite : interdire la suppression d'un user qui n'est pas role=owner
        # (un syndic / gestionnaire / superadmin partage le meme email -> on ne
        # touche jamais a son compte ; on detache simplement la fiche owner).
        user_role = user.get("role", "")
        if user_role != "owner":
            await db.owners.update_one({"id": owner_id}, {"$unset": {"user_id": ""}})
            await _log_audit(
                action="delete", actor=actor, owner=owner, user_doc=user, request=request,
                details={"deleted_user": False, "reason": f"role={user_role}, detache uniquement"},
            )
            return {
                "message": f"Compte {user.get('email')} preserve (role {user_role}). Fiche detachee.",
                "deleted": False,
                "detached": True,
                "status": _serialize_status(owner, None),
            }
        target_email = user.get("email", "")
        target_uid = str(user["_id"])
        # Invalider les sessions actives et les tokens reset
        await db.password_reset_tokens.update_many(
            {"user_id": target_uid, "consumed_at": None},
            {"$set": {"consumed_at": datetime.now(timezone.utc).isoformat()}}
        )
        # Snapshot user pour l'audit avant suppression
        snapshot = {"_id": user["_id"], "email": user.get("email"),
                    "role": user_role, "name": user.get("name", "")}
        await db.users.delete_one({"_id": user["_id"]})
        # Detacher la fiche owner
        await db.owners.update_one({"id": owner_id}, {"$unset": {"user_id": ""}})
        # Audit log
        await _log_audit(
            action="delete", actor=actor, owner=owner, user_doc=snapshot, request=request,
            details={"deleted_user": True, "target_email": target_email},
        )
        return {
            "message": f"Acces supprime ({target_email}). Le proprietaire ne peut plus se connecter.",
            "deleted": True,
            "status": _serialize_status(owner, None),
        }

    # ---------- GET access-audit (history) ----------
    @router.get("/{owner_id}/access-audit")
    async def get_access_audit(owner_id: str, request: Request, limit: int = 50):
        """Returns the access-events history (grant/resend/revoke/reactivate/delete)
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
