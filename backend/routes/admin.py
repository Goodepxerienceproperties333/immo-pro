from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone
import uuid
import re


class UserCreateInput(BaseModel):
    email: str
    password: Optional[str] = None
    name: str
    role: str  # superadmin, syndic, gestionnaire, owner
    copropriete_ids: Optional[List[str]] = []
    must_change_password: Optional[bool] = False
    role_template_id: Optional[str] = None
    permissions: Optional[List[str]] = None


class UserUpdateInput(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    copropriete_ids: Optional[List[str]] = None
    password: Optional[str] = None
    must_change_password: Optional[bool] = None
    role_template_id: Optional[str] = None
    permissions: Optional[List[str]] = None


def create_admin_router(db):
    router = APIRouter(prefix="/api/admin")

    async def _get_admin_user(request):
        """Pour les endpoints generaux d'admin (migrations, etc.) : syndic ou superadmin."""
        from server import get_current_user, is_admin_role
        user = await get_current_user(request)
        if not is_admin_role(user.get("role", "")):
            raise HTTPException(403, "Acces reserve a l'administration")
        return user

    async def _get_superadmin_only(request):
        """Pour la GESTION DES UTILISATEURS : SEUL le superadmin (gestionnaire de la plateforme)
        peut creer/modifier/supprimer/lister les autres utilisateurs.
        Un syndic n'a acces qu'a SON propre profil via /api/auth/me."""
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Seul un super administrateur peut gerer les acces a la plateforme")
        return user

    @router.get("/users")
    async def list_users(request: Request):
        # Seul le superadmin peut voir la liste des utilisateurs
        await _get_superadmin_only(request)
        users = await db.users.find({}).sort("name", 1).to_list(1000)
        result = []
        for u in users:
            result.append({
                "id": str(u["_id"]),
                "email": u["email"],
                "name": u["name"],
                "role": u.get("role", "owner"),
                "copropriete_ids": u.get("copropriete_ids", []),
                "must_change_password": u.get("must_change_password", False),
                "role_template_id": u.get("role_template_id"),
                "permissions": u.get("permissions"),
                "created_at": u.get("created_at", ""),
            })
        return result

    @router.post("/users")
    async def create_user(data: UserCreateInput, request: Request):
        # Seul le superadmin peut creer des comptes syndic (pas d'autres roles)
        await _get_superadmin_only(request)
        from server import hash_password
        # ENFORCE : le superadmin cree des comptes 'syndic' ou 'superadmin'.
        # iter90h0 : creation d'autres superadmins autorisee (co-gerance plateforme).
        # Les gestionnaires sont crees par leur syndic dans /api/team/members.
        # Les owners par le syndic via /api/owners (ou inscription).
        if data.role not in ("syndic", "superadmin"):
            raise HTTPException(
                400,
                "Le superadmin ne cree que des comptes 'syndic' ou 'superadmin'. "
                "Les gestionnaires sont crees par chaque syndic via /team, "
                "et les proprietaires via la gestion des proprietaires de l'ACP."
            )
        email = data.email.lower().strip()
        existing = await db.users.find_one({"email": email})
        if existing:
            raise HTTPException(400, "Cet email existe deja")
        # Password setup-on-first-login flow
        must_change = bool(data.must_change_password) or not (data.password and data.password.strip())
        if must_change:
            # Random unguessable placeholder (login impossible until first-set-password)
            placeholder = uuid.uuid4().hex + uuid.uuid4().hex
            pwd_hash = hash_password(placeholder)
        else:
            pwd_hash = hash_password(data.password)
        # iter90h0 : Ni role_template ni permissions ne s'appliquent ici
        # (syndic et superadmin ont un acces total sur leur perimetre).
        # Pas d'ACPs assignees : le syndic les creera lui-meme, le superadmin
        # voit toutes les ACPs de la plateforme.
        doc = {
            "email": email,
            "password_hash": pwd_hash,
            "name": data.name,
            "role": data.role,  # iter90h0 : syndic OU superadmin
            "copropriete_ids": [],
            "must_change_password": must_change,
            "role_template_id": None,
            "permissions": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": str(request.state.user.get("_id", "")) if hasattr(request, "state") and hasattr(request.state, "user") else None,
        }
        result = await db.users.insert_one(doc)
        # iter95m : envoi synchrone (await) pour remonter honnetement l'echec
        # a l'utilisateur (au lieu du fire-and-forget qui masquait les erreurs).
        # `send_html_email` fait deja fallback SMTP -> Graph automatiquement.
        invitation_sent = False
        invitation_error = None
        if must_change:
            try:
                from graph_email import is_configured, send_html_email, build_invitation_email
                import os
                if is_configured():
                    frontend_url = os.environ.get("FRONTEND_URL", "")
                    setup_url = f"{frontend_url}/login?invite={email}"
                    inviter = request.state.user if hasattr(request, "state") and hasattr(request.state, "user") else None
                    role_label = "Super Administrateur" if data.role == "superadmin" else "Syndic"
                    subject, html = build_invitation_email(
                        recipient_name=data.name,
                        role_label=role_label,
                        setup_url=setup_url,
                        inviter_name=(inviter or {}).get("name"),
                        inviter_email=(inviter or {}).get("email"),
                    )
                    await send_html_email([email], subject, html)
                    invitation_sent = True
                else:
                    invitation_error = "Service email non configure"
            except Exception as e:
                import logging
                logging.warning(f"Envoi invitation echoue pour {email}: {e}")
                invitation_error = str(e)[:200]
        return {
            "id": str(result.inserted_id),
            "email": email,
            "name": data.name,
            "role": doc["role"],
            "copropriete_ids": doc["copropriete_ids"],
            "must_change_password": must_change,
            "role_template_id": None,
            "permissions": None,
            "invitation_sent": invitation_sent,
            "invitation_error": invitation_error,
        }

    @router.post("/users/{user_id}/resend-invitation")
    async def resend_user_invitation(user_id: str, request: Request):
        """Renvoie l'email d'invitation a un syndic dont le mot de passe n'est pas defini."""
        await _get_superadmin_only(request)
        from graph_email import is_configured, send_html_email, build_invitation_email
        import os
        if not is_configured():
            raise HTTPException(503, "Service email non configure (MSGRAPH)")
        target = await db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(404, "Utilisateur non trouve")
        if not target.get("must_change_password"):
            raise HTTPException(400, "Cet utilisateur a deja un mot de passe defini")
        frontend_url = os.environ.get("FRONTEND_URL", "")
        setup_url = f"{frontend_url}/login?invite={target['email']}"
        role_label = "Syndic" if target.get("role") == "syndic" else target.get("role", "Utilisateur").capitalize()
        subject, html = build_invitation_email(
            recipient_name=target["name"],
            role_label=role_label,
            setup_url=setup_url,
        )
        try:
            await send_html_email([target["email"]], subject, html)
        except Exception as e:
            raise HTTPException(500, f"Echec envoi : {e}")
        return {"status": "ok", "email": target["email"]}

    @router.get("/email-config")
    async def get_email_config(request: Request):
        """Verifie l'etat de la configuration MSGRAPH (test sans envoyer)."""
        await _get_superadmin_only(request)
        from graph_email import is_configured
        import os
        return {
            "configured": is_configured(),
            "sender_upn": os.environ.get("GRAPH_SENDER_UPN", ""),
            "tenant_id_set": bool(os.environ.get("AZURE_TENANT_ID")),
            "client_id_set": bool(os.environ.get("AZURE_CLIENT_ID")),
            "client_secret_set": bool(os.environ.get("AZURE_CLIENT_SECRET")),
        }

    # ============================================================
    # LOGIN HISTORY (audit connexions par utilisateur)
    # ============================================================
    @router.get("/login-history/syndics-summary")
    async def login_history_summary(request: Request):
        """Pour chaque syndic, retourne : last_login, nb_connexions_30j, nb_connexions_7j,
        nb_echecs_30j. Pour vue d'ensemble superadmin."""
        await _get_superadmin_only(request)
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        cut_7 = (now - timedelta(days=7)).isoformat()
        cut_30 = (now - timedelta(days=30)).isoformat()
        # Tous les syndics (et gestionnaires) pour avoir une vue d'agence
        users = await db.users.find(
            {"role": {"$in": ["syndic", "superadmin", "gestionnaire"]}}
        ).sort("name", 1).to_list(500)
        results = []
        for u in users:
            uid = str(u["_id"])
            last = await db.login_history.find_one(
                {"user_id": uid, "success": True},
                sort=[("created_at", -1)]
            )
            cnt_7 = await db.login_history.count_documents(
                {"user_id": uid, "success": True, "created_at": {"$gte": cut_7}}
            )
            cnt_30 = await db.login_history.count_documents(
                {"user_id": uid, "success": True, "created_at": {"$gte": cut_30}}
            )
            fail_30 = await db.login_history.count_documents(
                {"user_id": uid, "success": False, "created_at": {"$gte": cut_30}}
            )
            results.append({
                "user_id": uid,
                "email": u.get("email"),
                "name": u.get("name"),
                "role": u.get("role"),
                "parent_syndic_id": u.get("parent_syndic_id"),
                "last_login_at": (last or {}).get("created_at"),
                "last_login_ip": (last or {}).get("ip"),
                "logins_last_7d": cnt_7,
                "logins_last_30d": cnt_30,
                "failed_last_30d": fail_30,
                "copropriete_ids": u.get("copropriete_ids", []),
            })
        return results

    @router.get("/login-history")
    async def login_history(
        request: Request,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        success_only: Optional[bool] = None,
        limit: int = 100,
        skip: int = 0,
    ):
        """Liste paginee des connexions, filtrable par user_id, email, success."""
        await _get_superadmin_only(request)
        q = {}
        if user_id:
            q["user_id"] = user_id
        if email:
            q["email"] = email.lower().strip()
        if success_only is True:
            q["success"] = True
        elif success_only is False:
            q["success"] = False
        limit = max(1, min(limit, 500))
        skip = max(0, skip)
        cursor = db.login_history.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
        rows = await cursor.to_list(limit)
        total = await db.login_history.count_documents(q)
        return {"items": rows, "total": total, "limit": limit, "skip": skip}

    @router.post("/email-config/test")
    async def test_email_config(request: Request):
        """Envoie un email de test au superadmin connecte pour valider la config MSGRAPH."""
        from server import get_current_user
        user = await get_current_user(request)
        if not user or user.get("role") not in ("superadmin", "admin"):
            raise HTTPException(403, "Reserve au superadmin")
        from graph_email import is_configured, send_html_email
        if not is_configured():
            raise HTTPException(503, "Service email non configure (MSGRAPH)")
        if not user.get("email"):
            raise HTTPException(400, "Email du superadmin introuvable")
        html = "<html><body style='font-family:sans-serif'><h2>Test MSGRAPH OK</h2><p>Si vous recevez cet email, l'integration Microsoft Graph fonctionne correctement.</p></body></html>"
        try:
            await send_html_email([user["email"]], "[NextGe Copro] Test MSGRAPH", html)
        except Exception as e:
            raise HTTPException(500, f"Echec envoi : {e}")
        return {"status": "ok", "sent_to": user["email"]}

    @router.put("/users/{user_id}")
    async def update_user(user_id: str, data: UserUpdateInput, request: Request):
        await _get_superadmin_only(request)
        from server import hash_password
        target = await db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(404, "Utilisateur non trouve")
        # ENFORCE : le superadmin ne peut pas changer le role d'un user ici, ni
        # ses copropriete_ids (les ACPs sont gerees par le syndic lui-meme).
        # Il peut juste : modifier le nom, reinitialiser le mot de passe, ou
        # forcer un changement de mdp au prochain login.
        # iter90h0 : le superadmin peut basculer un compte entre 'syndic' et
        # 'superadmin' (co-gerance plateforme). Toute autre valeur reste
        # interdite (gestionnaires -> /team, owners -> /owners).
        if data.role is not None and data.role != target.get("role"):
            if data.role not in ("syndic", "superadmin"):
                raise HTTPException(
                    400,
                    "Le role d'un compte gere ici doit etre 'syndic' ou 'superadmin'. "
                    "Les gestionnaires sont geres par leur syndic dans /team."
                )
            # Empeche un superadmin de se retrograder lui-meme (evite le
            # scenario ou plus aucun superadmin n'existe sur la plateforme).
            requester_id = getattr(request.state, "user_id", "") or ""
            if requester_id == user_id and target.get("role") == "superadmin" and data.role != "superadmin":
                raise HTTPException(
                    400,
                    "Vous ne pouvez pas retrograder votre propre compte superadmin. "
                    "Demandez a un autre superadmin de le faire, ou creez un autre superadmin avant."
                )
        if data.copropriete_ids is not None:
            raise HTTPException(
                400,
                "Les ACPs ne s'attribuent pas depuis cette interface. "
                "Le syndic gere lui-meme ses coproprietes (creation/modification) "
                "et leur affectation a son equipe."
            )
        if data.role_template_id is not None or data.permissions is not None:
            raise HTTPException(
                400,
                "Les profils et permissions sont geres par le syndic dans /team. "
                "Cette interface ne sert qu'a creer les comptes syndic principaux."
            )
        update = {}
        if data.name is not None:
            update["name"] = data.name
        # iter90h0 : le role peut evoluer entre syndic <-> superadmin (deja
        # valide plus haut). Sauvegarde du changement effectif.
        if data.role is not None and data.role != target.get("role"):
            update["role"] = data.role
        if data.password:
            update["password_hash"] = hash_password(data.password)
            update["must_change_password"] = False
        if data.must_change_password is not None:
            update["must_change_password"] = bool(data.must_change_password)
            if data.must_change_password:
                # When (re)activating, blank-out the password so login is impossible
                placeholder = uuid.uuid4().hex + uuid.uuid4().hex
                update["password_hash"] = hash_password(placeholder)
        if update:
            update["updated_at"] = datetime.now(timezone.utc).isoformat()
            await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
        updated = await db.users.find_one({"_id": ObjectId(user_id)})
        return {
            "id": str(updated["_id"]),
            "email": updated["email"],
            "name": updated["name"],
            "role": updated.get("role", "owner"),
            "copropriete_ids": updated.get("copropriete_ids", []),
            "role_template_id": updated.get("role_template_id"),
            "permissions": updated.get("permissions"),
        }

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, request: Request):
        admin = await _get_superadmin_only(request)
        if str(admin["_id"]) == user_id:
            raise HTTPException(400, "Vous ne pouvez pas vous supprimer vous-meme")
        result = await db.users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            raise HTTPException(404, "Utilisateur non trouve")
        return {"message": "Utilisateur supprime"}

    @router.post("/migrate/tier-accounts")
    async def migrate_tier_accounts(request: Request, copropriete_id: Optional[str] = None):
        """Backfill 40000XXX/40010XXX accounts for all owners and 44000XXX for all
        suppliers across all coproprietes (or a single one). Idempotent."""
        from tier_accounts import assign_owner_accounts, assign_supplier_account
        await _get_admin_user(request)
        stats = {"owners_processed": 0, "suppliers_processed": 0, "acps": []}
        copro_q = {"id": copropriete_id} if copropriete_id else {}
        coproprietes = await db.coproprietes.find(copro_q, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
        for copro in coproprietes:
            cid = copro["id"]
            # Process owners: any owner that has a lot in this ACP must have accounts here
            owner_ids = await db.lots.distinct("owner_id", {"copropriete_id": cid})
            owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(10000)
            for o in owners:
                await assign_owner_accounts(db, o, cid)
                stats["owners_processed"] += 1
            # Process suppliers: any supplier referenced by an invoice in this ACP
            supplier_names = await db.invoices.distinct("supplier", {"copropriete_id": cid})
            for sname in supplier_names:
                if not sname:
                    continue
                s = await db.suppliers.find_one({"name": sname}, {"_id": 0})
                if not s:
                    continue
                await assign_supplier_account(db, s, cid)
                stats["suppliers_processed"] += 1
            stats["acps"].append({"id": cid, "name": copro.get("name", "")})
        return stats

    @router.post("/migrate/pcmn-import")
    async def migrate_pcmn_import(request: Request, copropriete_id: Optional[str] = None):
        """Import (idempotent) le PCMN complet (327 comptes + 10 compat) dans toutes les ACPs (ou une seule).
        Ajoute uniquement les comptes manquants. Ne supprime ni ne modifie l'existant.
        Les comptes ajoutes sont inactifs par defaut (sauf 614000/615000)."""
        from pcmn_data import PCMN_ALL_ACCOUNTS
        await _get_admin_user(request)
        DEFAULT_ACTIVE = {"614000", "615000"}
        copro_q = {"id": copropriete_id} if copropriete_id else {}
        coproprietes = await db.coproprietes.find(copro_q, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
        stats = {"acps": [], "total_added": 0, "total_existing": 0}
        for copro in coproprietes:
            cid = copro["id"]
            existing_nums = set()
            async for d in db.pcmn_accounts.find(
                {"copropriete_id": cid}, {"_id": 0, "number": 1}
            ):
                existing_nums.add(d["number"])
            to_insert = []
            for acc in PCMN_ALL_ACCOUNTS:
                if acc["number"] in existing_nums:
                    continue
                to_insert.append({
                    **acc,
                    "copropriete_id": cid,
                    "active": acc["number"] in DEFAULT_ACTIVE,
                    "is_custom": False,
                })
            if to_insert:
                await db.pcmn_accounts.insert_many(to_insert)
            stats["acps"].append({
                "id": cid, "name": copro.get("name", ""),
                "added": len(to_insert),
                "existing": len(existing_nums),
            })
            stats["total_added"] += len(to_insert)
            stats["total_existing"] += len(existing_nums)
        return stats

    # ============================================================================
    # OUTILS SUPERADMIN : DEBLOCAGE COMPTABLE + AUDIT LOG
    # ============================================================================
    # Seul le SUPERADMIN (gestionnaire de la plateforme) peut utiliser ces outils.
    # Chaque action genere une trace dans la collection `audit_log` pour compliance.

    async def _audit(user, action, target_type, target_id, details=None,
                     copropriete_id=None):
        """Enregistre une action superadmin dans audit_log."""
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user.get("id"),
            "user_email": user.get("email"),
            "action": action,  # ex: "unlock_entry", "force_reopen_fy", "user_create"
            "target_type": target_type,  # ex: "journal_entry", "fiscal_year"
            "target_id": target_id,
            "copropriete_id": copropriete_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @router.post("/unlock-entry/{entry_id}")
    async def unlock_entry(entry_id: str, request: Request):
        """Force la modification d'une ecriture comptable verrouillee.
        - Si l'ecriture est dans un exercice cloture : bypass le verrou
        - Si l'ecriture est `is_reversal=True` : permet de l'editer (cas exceptionnel)
        - Marque l'ecriture comme 'unlocked_by_admin' + trace dans audit_log.

        Body attendu (JSON) : `{ "patch": {...changes to apply...}, "reason": "..." }`
        """
        admin = await _get_superadmin_only(request)
        body = await request.json()
        patch = body.get("patch") or {}
        reason = body.get("reason") or ""
        if not patch:
            raise HTTPException(400, "Aucune modification fournie (champ `patch` vide)")
        if not reason or len(reason) < 5:
            raise HTTPException(400, "Une justification est requise (minimum 5 caracteres)")
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture introuvable")
        # Apply patch + flag unlock
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        patch["unlocked_by_admin"] = True
        patch["unlock_reason"] = reason
        patch["unlock_admin_id"] = admin.get("id")
        await db.journal_entries.update_one({"id": entry_id}, {"$set": patch})
        await _audit(
            admin, "unlock_entry", "journal_entry", entry_id,
            details={"reason": reason, "patch_keys": list(patch.keys()),
                     "original_date": entry.get("date"),
                     "original_journal_type": entry.get("journal_type")},
            copropriete_id=entry.get("copropriete_id"),
        )
        updated = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        return {"status": "ok", "entry": updated}

    @router.post("/force-reopen-fy/{fy_id}")
    async def force_reopen_fy(fy_id: str, request: Request):
        """Force la reouverture d'un exercice fiscal en cas d'urgence.
        Contrairement au reopen normal qui contre-passe les OD de cloture,
        ce mode FORCE l'exercice a 'open' sans contre-passation - utile uniquement
        si le reopen normal echoue. Trace dans audit_log avec justification."""
        admin = await _get_superadmin_only(request)
        body = await request.json()
        reason = body.get("reason") or ""
        if not reason or len(reason) < 10:
            raise HTTPException(400, "Justification detaillee requise (minimum 10 caracteres)")
        fy = await db.fiscal_years.find_one({"id": fy_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice introuvable")
        await db.fiscal_years.update_one(
            {"id": fy_id},
            {"$set": {"status": "open",
                      "forced_reopen_by_admin": True,
                      "forced_reopen_reason": reason,
                      "forced_reopen_at": datetime.now(timezone.utc).isoformat(),
                      "forced_reopen_admin_id": admin.get("id")}}
        )
        await _audit(
            admin, "force_reopen_fy", "fiscal_year", fy_id,
            details={"reason": reason, "previous_status": fy.get("status"),
                     "fy_name": fy.get("name")},
            copropriete_id=fy.get("copropriete_id"),
        )
        return {"status": "ok", "fy_id": fy_id, "new_status": "open"}

    @router.delete("/entries/{entry_id}/force")
    async def force_delete_entry(entry_id: str, request: Request):
        """Force la suppression d'une ecriture (meme si verrouillee ou liee
        a une facture/appel). Action critique - trace systematiquement."""
        admin = await _get_superadmin_only(request)
        body = await request.json() if request.headers.get("content-length") else {}
        reason = body.get("reason") or ""
        if not reason or len(reason) < 10:
            raise HTTPException(400, "Justification detaillee requise (minimum 10 caracteres)")
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture introuvable")
        # Conserve une copie dans deleted_entries (audit trail)
        await db.deleted_entries.insert_one({
            **{k: v for k, v in entry.items() if k != "_id"},
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by_admin_id": admin.get("id"),
            "delete_reason": reason,
        })
        await db.journal_entries.delete_one({"id": entry_id})
        await _audit(
            admin, "force_delete_entry", "journal_entry", entry_id,
            details={"reason": reason,
                     "total_debit": entry.get("total_debit"),
                     "journal_type": entry.get("journal_type"),
                     "date": entry.get("date")},
            copropriete_id=entry.get("copropriete_id"),
        )
        return {"status": "ok"}

    @router.post("/journal-entries/bulk-force-delete")
    async def bulk_force_delete_entries(request: Request):
        """iter90en : suppression bulk d'ecritures (admin only).

        Body : { entry_ids: [str], reason: str }
        Chaque ecriture est archivee dans `deleted_entries` (audit trail)
        avant suppression definitive. Action critique reservee superadmin.
        """
        admin = await _get_superadmin_only(request)
        body = await request.json() if request.headers.get("content-length") else {}
        entry_ids = body.get("entry_ids") or []
        reason = body.get("reason") or ""
        if not entry_ids or not isinstance(entry_ids, list):
            raise HTTPException(400, "Liste entry_ids requise")
        if not reason or len(reason) < 10:
            raise HTTPException(400, "Justification detaillee requise (minimum 10 caracteres)")
        entries = await db.journal_entries.find(
            {"id": {"$in": entry_ids}}, {"_id": 0}
        ).to_list(len(entry_ids) + 10)
        if not entries:
            raise HTTPException(404, "Aucune ecriture trouvee dans la liste")
        # iter90ep : interdiction de supprimer contre-passations & extournes
        # (integrite audit trail PCMN)
        blocked = [
            e for e in entries
            if e.get("is_reversal") or e.get("reversed")
        ]
        if blocked:
            refs = ", ".join(
                (b.get("reference") or b.get("id", ""))[:20] for b in blocked[:5]
            )
            raise HTTPException(
                400,
                f"Impossible de supprimer une contre-passation ou une ecriture "
                f"extournee (audit trail legal). Ecritures bloquees : {refs}"
                + (f" (+{len(blocked) - 5} autres)" if len(blocked) > 5 else ""),
            )
        # Archive dans deleted_entries (audit trail)
        deleted_at = datetime.now(timezone.utc).isoformat()
        archive_docs = [
            {**{k: v for k, v in e.items() if k != "_id"},
             "deleted_at": deleted_at,
             "deleted_by_admin_id": admin.get("id"),
             "delete_reason": reason,
             "bulk_delete": True}
            for e in entries
        ]
        if archive_docs:
            await db.deleted_entries.insert_many(archive_docs)
        result = await db.journal_entries.delete_many({"id": {"$in": entry_ids}})
        # Audit consolide (une entree par bulk operation, pas par entry, pour
        # ne pas polluer le log)
        copro_ids = {e.get("copropriete_id") for e in entries if e.get("copropriete_id")}
        await _audit(
            admin, "bulk_force_delete_entries", "journal_entry", "bulk",
            details={"reason": reason,
                     "count": result.deleted_count,
                     "entry_ids_sample": entry_ids[:10],
                     "copropriete_ids": list(copro_ids)},
            copropriete_id=next(iter(copro_ids)) if len(copro_ids) == 1 else None,
        )
        return {"status": "ok", "deleted_count": result.deleted_count}

    @router.get("/audit-log")
    async def get_audit_log(
        request: Request,
        limit: int = 100,
        action: Optional[str] = None,
        copropriete_id: Optional[str] = None,
    ):
        """Liste les actions superadmin (lecture seule)."""
        await _get_superadmin_only(request)
        q = {}
        if action:
            q["action"] = action
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        logs = await db.audit_log.find(q, {"_id": 0}).sort("timestamp", -1).limit(limit).to_list(limit)
        return logs

    @router.get("/syndics-overview")
    async def syndics_overview(request: Request):
        """Vue consolidee des syndics pour le superadmin : pour chaque syndic,
        retourne ses ACPs gerees avec compteurs (lots, proprietaires, factures,
        derniere activite) - utile pour appliquer une facturation a la prestation.
        """
        await _get_superadmin_only(request)
        # Tous les syndics (et admins) actifs
        syndics_db = await db.users.find(
            {"role": {"$in": ["syndic", "admin"]}}
        ).sort("name", 1).to_list(500)
        # Index global ACPs
        all_copros = await db.coproprietes.find(
            {"status": {"$ne": "archived"}}, {"_id": 0}
        ).to_list(1000)
        copro_by_id = {c["id"]: c for c in all_copros}

        result = []
        for s in syndics_db:
            copro_ids = list(s.get("copropriete_ids") or [])
            copros_detail = []
            for cid in copro_ids:
                copro = copro_by_id.get(cid)
                if not copro:
                    continue
                lots_count = await db.lots.count_documents({"copropriete_id": cid})
                # Owners distinct via lots.owner_id
                owner_ids = await db.lots.distinct("owner_id", {"copropriete_id": cid})
                owner_ids = [o for o in owner_ids if o]
                invoices_count = await db.invoices.count_documents({"copropriete_id": cid})
                # Derniere transaction bancaire = signe de vie
                last_tx = await db.bank_transactions.find_one(
                    {"copropriete_id": cid},
                    sort=[("date", -1)]
                )
                # Derniere ecriture comptable
                last_je = await db.journal_entries.find_one(
                    {"copropriete_id": cid},
                    sort=[("date", -1)]
                )
                # Active fiscal year ?
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                fy = await db.fiscal_years.find_one({
                    "copropriete_id": cid,
                    "status": "open",
                    "start_date": {"$lte": today},
                    "end_date": {"$gte": today},
                }, {"_id": 0, "name": 1, "start_date": 1, "end_date": 1})
                copros_detail.append({
                    "id": cid,
                    "name": copro.get("name", ""),
                    "reference": copro.get("reference", ""),
                    "address": copro.get("address", ""),
                    "lots_count": lots_count,
                    "owners_count": len(set(owner_ids)),
                    "invoices_count": invoices_count,
                    "current_fy": fy,
                    "last_bank_tx_date": last_tx.get("date") if last_tx else None,
                    "last_je_date": last_je.get("date") if last_je else None,
                })
            total_lots = sum(c["lots_count"] for c in copros_detail)
            total_owners = sum(c["owners_count"] for c in copros_detail)
            total_invoices = sum(c["invoices_count"] for c in copros_detail)
            result.append({
                "syndic_id": str(s["_id"]),
                "email": s.get("email", ""),
                "name": s.get("name", ""),
                "role": s.get("role", "syndic"),
                "created_at": s.get("created_at", ""),
                "copros_count": len(copros_detail),
                "total_lots": total_lots,
                "total_owners": total_owners,
                "total_invoices": total_invoices,
                "coproprietes": copros_detail,
            })
        return result

    @router.get("/locked-entries-search")
    async def search_locked_entries(
        request: Request,
        copropriete_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        journal_type: Optional[str] = None,
        q: Optional[str] = None,
    ):
        """Recherche des ecritures dans des exercices clotures pour intervention.
        Retourne les ecritures + le statut de leur FY (closed/open) + flags
        (is_reversal, reversed) pour aider l'admin a localiser l'ecriture cible."""
        await _get_superadmin_only(request)
        query = {}
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        if journal_type:
            query["journal_type"] = journal_type
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if q:
            query["$or"] = [
                {"reference": {"$regex": q, "$options": "i"}},
                {"description": {"$regex": q, "$options": "i"}},
            ]
        entries = await db.journal_entries.find(query, {"_id": 0}).sort("date", -1).limit(200).to_list(200)
        # Enrich with FY status
        fy_ids = {e.get("fiscal_year_id") for e in entries if e.get("fiscal_year_id")}
        fys = {}
        if fy_ids:
            async for fy in db.fiscal_years.find(
                {"id": {"$in": list(fy_ids)}}, {"_id": 0, "id": 1, "name": 1, "status": 1}
            ):
                fys[fy["id"]] = fy
        for e in entries:
            fy = fys.get(e.get("fiscal_year_id"))
            e["fy_status"] = fy.get("status") if fy else "unknown"
            e["fy_name"] = fy.get("name") if fy else ""
        return entries

    # ============================================================================
    # ROLE TEMPLATES (PROFILS GESTIONNAIRE)
    # ============================================================================
    # Le superadmin definit des profils reutilisables (set de permissions).
    # Le syndic peut piocher un profil quand il cree un gestionnaire,
    # puis ajuster a la piece (mode hybride).

    from permissions_catalog import PERMISSIONS_CATALOG, SYSTEM_ROLE_TEMPLATES

    async def _ensure_system_templates_seeded():
        """Insere les profils systeme s'ils n'existent pas encore."""
        for tpl in SYSTEM_ROLE_TEMPLATES:
            existing = await db.role_templates.find_one({"code": tpl["code"]})
            if not existing:
                await db.role_templates.insert_one({
                    "id": str(uuid.uuid4()),
                    **tpl,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

    @router.get("/permissions-catalog")
    async def get_permissions_catalog(request: Request):
        """Liste tous les codes de permission disponibles avec leur libelle."""
        # Lecture autorisee aux admins ET aux syndics (pour qu'ils puissent
        # voir les libelles quand ils editent les permissions d'un gestionnaire)
        await _get_admin_user(request)
        return [{"code": k, "label": v} for k, v in PERMISSIONS_CATALOG.items()]

    @router.get("/role-templates")
    async def list_role_templates(request: Request):
        """Liste tous les profils utilisateurs.
        Accessible aussi aux syndics (lecture seule, pour les piocher au moment
        de la creation d'un gestionnaire).
        """
        await _get_admin_user(request)
        await _ensure_system_templates_seeded()
        templates = await db.role_templates.find({}, {"_id": 0}).sort("name", 1).to_list(100)
        return templates

    @router.post("/role-templates")
    async def create_role_template(request: Request):
        """Cree un nouveau profil utilisateur (superadmin uniquement).
        Body: { name, description, permissions: ["accounting.read", ...] }
        """
        admin = await _get_superadmin_only(request)
        body = await request.json()
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        permissions = body.get("permissions") or []
        if not name:
            raise HTTPException(400, "Le nom du profil est requis")
        if len(name) < 2:
            raise HTTPException(400, "Le nom doit faire au moins 2 caracteres")
        if not isinstance(permissions, list):
            raise HTTPException(400, "permissions doit etre une liste")
        # Valider que chaque permission existe dans le catalogue
        invalid = [p for p in permissions if p not in PERMISSIONS_CATALOG]
        if invalid:
            raise HTTPException(400, f"Permissions inconnues : {', '.join(invalid)}")
        # Verifier unicite du nom (insensible a la casse)
        # SEC-P3 : escape user input to prevent ReDoS / regex injection
        existing = await db.role_templates.find_one(
            {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
        )
        if existing:
            raise HTTPException(400, f"Un profil nomme '{name}' existe deja")
        new_tpl = {
            "id": str(uuid.uuid4()),
            "code": name.lower().replace(" ", "_")[:32],
            "name": name,
            "description": description,
            "permissions": permissions,
            "is_system": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": admin.get("id"),
        }
        await db.role_templates.insert_one(new_tpl)
        await _audit(admin, "role_template_create", "role_template", new_tpl["id"],
                     details={"name": name, "perms_count": len(permissions)})
        return {k: v for k, v in new_tpl.items() if k != "_id"}

    @router.put("/role-templates/{template_id}")
    async def update_role_template(template_id: str, request: Request):
        """Modifie un profil (superadmin uniquement). Les profils systeme
        peuvent etre modifies (perms ajustables) mais leur `is_system` reste."""
        admin = await _get_superadmin_only(request)
        body = await request.json()
        tpl = await db.role_templates.find_one({"id": template_id}, {"_id": 0})
        if not tpl:
            raise HTTPException(404, "Profil introuvable")
        update = {}
        if "name" in body:
            name = (body["name"] or "").strip()
            if not name or len(name) < 2:
                raise HTTPException(400, "Nom invalide")
            update["name"] = name
        if "description" in body:
            update["description"] = (body["description"] or "").strip()
        if "permissions" in body:
            perms = body["permissions"] or []
            if not isinstance(perms, list):
                raise HTTPException(400, "permissions doit etre une liste")
            invalid = [p for p in perms if p not in PERMISSIONS_CATALOG]
            if invalid:
                raise HTTPException(400, f"Permissions inconnues : {', '.join(invalid)}")
            update["permissions"] = perms
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.role_templates.update_one({"id": template_id}, {"$set": update})
        await _audit(admin, "role_template_update", "role_template", template_id,
                     details={"updated_keys": list(update.keys())})
        updated = await db.role_templates.find_one({"id": template_id}, {"_id": 0})
        return updated

    @router.delete("/role-templates/{template_id}")
    async def delete_role_template(template_id: str, request: Request):
        """Supprime un profil (superadmin uniquement). Les profils SYSTEME
        ne peuvent PAS etre supprimes."""
        admin = await _get_superadmin_only(request)
        tpl = await db.role_templates.find_one({"id": template_id}, {"_id": 0})
        if not tpl:
            raise HTTPException(404, "Profil introuvable")
        if tpl.get("is_system"):
            raise HTTPException(400, "Les profils systeme ne peuvent pas etre supprimes")
        # Verifier qu'aucun utilisateur n'utilise ce profil (champ role_template_id)
        usage = await db.users.count_documents({"role_template_id": template_id})
        if usage > 0:
            raise HTTPException(
                400,
                f"Ce profil est utilise par {usage} utilisateur(s). "
                "Reattribuez-les avant de supprimer."
            )
        await db.role_templates.delete_one({"id": template_id})
        await _audit(admin, "role_template_delete", "role_template", template_id,
                     details={"name": tpl.get("name")})
        return {"status": "ok"}

    @router.get("/journal-entries/diagnostic")
    async def journal_entries_diagnostic(request: Request):
        """iter90gb : inventaire diagnostic de journal_entries par ACP.

        Objectif : quand le syndic constate "les OD sont vides" sur l'ecran
        Journaux Comptables, permet en un appel de determiner :
        - Y a-t-il vraiment des donnees ? Par ACP, par journal_type ?
        - Les OD attendues (mutations, cloture, appels de fonds) existent-elles ?
        - Y a-t-il eu des suppressions bulk (deleted_entries) ?
        - Range de dates : les OD sont-elles peut-etre datees en dehors du
          FY selectionne dans le UI ?

        Retourne, par ACP, un breakdown :
        {
          "coproprietes": [{
            "id": "...", "name": "Acacia TER",
            "total_entries": 123,
            "by_journal_type": {"AC": 45, "OD": 12, "FI": 30, "BQ": 36, "VE": 0},
            "by_source_type": {"invoice": 45, "fund_call": 5,
                               "lot_mutation": 3, "fiscal_regularization": 4, ...},
            "date_min": "2025-10-01",
            "date_max": "2026-09-30",
            "reversed_count": 2,
            "is_reversal_count": 2,
            "deleted_entries_archived": 0,  # dans db.deleted_entries
            "mutation_entries_count": 3,  # source_type=lot_mutation
            "fiscal_year_count": 2,
          }, ...],
          "total_all_acp": {...},
        }
        """
        await _get_superadmin_only(request)
        copros = await db.coproprietes.find(
            {}, {"_id": 0, "id": 1, "name": 1}
        ).sort("name", 1).to_list(200)

        result = []
        grand_total = {"total_entries": 0, "by_journal_type": {},
                       "by_source_type": {}}
        for copro in copros:
            cid = copro["id"]
            entries = await db.journal_entries.find(
                {"copropriete_id": cid},
                {"_id": 0, "journal_type": 1, "source_type": 1,
                 "source_subtype": 1, "date": 1,
                 "reversed": 1, "is_reversal": 1},
            ).to_list(100000)
            by_jt: dict = {}
            by_st: dict = {}
            mut_count = 0
            reversed_c = 0
            is_reversal_c = 0
            dates = []
            for e in entries:
                jt = e.get("journal_type", "?")
                by_jt[jt] = by_jt.get(jt, 0) + 1
                grand_total["by_journal_type"][jt] = grand_total["by_journal_type"].get(jt, 0) + 1
                st = e.get("source_type", "manual") or "manual"
                by_st[st] = by_st.get(st, 0) + 1
                grand_total["by_source_type"][st] = grand_total["by_source_type"].get(st, 0) + 1
                if st == "lot_mutation":
                    mut_count += 1
                if e.get("reversed"):
                    reversed_c += 1
                if e.get("is_reversal"):
                    is_reversal_c += 1
                d = e.get("date")
                if d:
                    dates.append(d)
            deleted_count = await db.deleted_entries.count_documents({"copropriete_id": cid})
            fy_count = await db.fiscal_years.count_documents({"copropriete_id": cid})
            result.append({
                "id": cid,
                "name": copro.get("name", ""),
                "total_entries": len(entries),
                "by_journal_type": by_jt,
                "by_source_type": by_st,
                "date_min": min(dates) if dates else None,
                "date_max": max(dates) if dates else None,
                "reversed_count": reversed_c,
                "is_reversal_count": is_reversal_c,
                "deleted_entries_archived": deleted_count,
                "mutation_entries_count": mut_count,
                "fiscal_year_count": fy_count,
            })
            grand_total["total_entries"] += len(entries)
        return {
            "coproprietes": result,
            "grand_total": grand_total,
            "acp_count": len(result),
        }

    @router.get("/owners/duplicates-diagnostic")
    async def owners_duplicates_diagnostic(
        request: Request,
        copropriete_id: Optional[str] = None,
    ):
        """iter90gh : identifie les fiches owner en doublon dans une ACP.
        Cause classique : imports Optipro/CODA repetes.
        Retourne des groupes {master, slaves[]} par nom normalise.
        """
        await _get_superadmin_only(request)
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or ""
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis (chinese wall)")
        lots = await db.lots.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "owner_id": 1, "owner_ids": 1},
        ).to_list(10000)
        owner_ids_from_lots: set = set()
        for l in lots:
            if l.get("owner_id"):
                owner_ids_from_lots.add(l["owner_id"])
            for oid in (l.get("owner_ids") or []):
                owner_ids_from_lots.add(oid)
        owners = await db.owners.find(
            {"$or": [
                {"id": {"$in": list(owner_ids_from_lots)}},
                {"copropriete_ids": copropriete_id},
            ]},
            {"_id": 0},
        ).to_list(50000)

        def _norm(s: str) -> str:
            import re
            return re.sub(r"\s+", " ", (s or "").strip().lower())

        def _score(o: dict) -> int:
            s = 0
            if (o.get("vcs_code") or "").strip(): s += 4
            if (o.get("auxiliary_code") or "").strip(): s += 2
            if (o.get("email") or "").strip(): s += 1
            if (o.get("iban") or "").strip(): s += 1
            if (o.get("last_name") or "").strip(): s += 1
            return s

        from collections import defaultdict
        by_norm = defaultdict(list)
        for o in owners:
            key = _norm(o.get("name", ""))
            if key:
                by_norm[key].append(o)
        duplicate_groups = []
        for key, group in by_norm.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda o: -_score(o))
            master, slaves = group[0], group[1:]
            slaves_detail = []
            for s in slaves:
                sid = s["id"]
                lots_count = sum(
                    1 for l in lots
                    if l.get("owner_id") == sid or sid in (l.get("owner_ids") or [])
                )
                inv_c = await db.invoices.count_documents({
                    "copropriete_id": copropriete_id,
                    "$or": [{"private_fee_owner_id": sid}, {"owner_id": sid}],
                })
                mut_c = await db.mutations.count_documents({
                    "copropriete_id": copropriete_id,
                    "$or": [{"from_owner_id": sid}, {"to_owner_id": sid}],
                })
                je_c = await db.journal_entries.count_documents({
                    "copropriete_id": copropriete_id,
                    "lines.third_party_id": sid,
                })
                slaves_detail.append({
                    "id": sid,
                    "name": s.get("name", ""),
                    "email": s.get("email", ""),
                    "vcs_code": s.get("vcs_code", ""),
                    "auxiliary_code": s.get("auxiliary_code", ""),
                    "lots_count": lots_count,
                    "invoices_count": inv_c,
                    "mutations_count": mut_c,
                    "journal_lines_count": je_c,
                    "score": _score(s),
                })
            duplicate_groups.append({
                "normalized_name": key,
                "master": {
                    "id": master["id"],
                    "name": master.get("name", ""),
                    "vcs_code": master.get("vcs_code", ""),
                    "email": master.get("email", ""),
                    "score": _score(master),
                },
                "slaves": slaves_detail,
                "total_duplicates": len(slaves),
            })
        duplicate_groups.sort(key=lambda g: -g["total_duplicates"])
        return {
            "copropriete_id": copropriete_id,
            "total_owners_scanned": len(owners),
            "total_groups": len(duplicate_groups),
            "total_slaves_to_merge": sum(g["total_duplicates"] for g in duplicate_groups),
            "groups": duplicate_groups,
        }

    class OwnersMergeInput(BaseModel):
        copropriete_id: str
        master_id: str
        slave_ids: List[str]
        dry_run: bool = True

    @router.post("/owners/merge-duplicates")
    async def owners_merge_duplicates(data: OwnersMergeInput, request: Request):
        """iter90gh : fusionne N slaves dans 1 master. Propage sur lots,
        invoices, mutations, journal_entries, fund_calls, bank_accounts.
        Puis SUPPRIME les slaves. Chinese wall strict.
        """
        await _get_superadmin_only(request)
        if not data.copropriete_id or data.copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis")
        if not data.master_id or not data.slave_ids:
            raise HTTPException(400, "master_id et slave_ids requis")
        if data.master_id in data.slave_ids:
            raise HTTPException(400, "master ne peut pas etre dans slave_ids")
        all_ids = [data.master_id] + data.slave_ids
        owners = await db.owners.find(
            {"id": {"$in": all_ids}}, {"_id": 0, "id": 1},
        ).to_list(len(all_ids))
        found = {o["id"] for o in owners}
        if data.master_id not in found:
            raise HTTPException(404, "Master introuvable")
        missing = [s for s in data.slave_ids if s not in found]
        if missing:
            raise HTTPException(404, f"Slaves introuvables : {missing}")
        report = {
            "dry_run": data.dry_run, "master_id": data.master_id,
            "slaves_merged": len(data.slave_ids),
            "lots_updated": 0, "invoices_updated": 0, "mutations_updated": 0,
            "journal_entries_updated": 0, "fund_calls_updated": 0,
            "bank_accounts_updated": 0, "slaves_deleted": 0,
        }
        for sid in data.slave_ids:
            lots_c = await db.lots.count_documents({
                "copropriete_id": data.copropriete_id,
                "$or": [{"owner_id": sid}, {"owner_ids": sid}],
            })
            report["lots_updated"] += lots_c
            if not data.dry_run and lots_c:
                await db.lots.update_many(
                    {"copropriete_id": data.copropriete_id, "owner_id": sid},
                    {"$set": {"owner_id": data.master_id}})
                await db.lots.update_many(
                    {"copropriete_id": data.copropriete_id, "owner_ids": sid},
                    {"$addToSet": {"owner_ids": data.master_id}})
                await db.lots.update_many(
                    {"copropriete_id": data.copropriete_id, "owner_ids": sid},
                    {"$pull": {"owner_ids": sid}})
            inv_c = await db.invoices.count_documents({
                "copropriete_id": data.copropriete_id,
                "$or": [{"private_fee_owner_id": sid}, {"owner_id": sid}]})
            report["invoices_updated"] += inv_c
            if not data.dry_run and inv_c:
                await db.invoices.update_many(
                    {"copropriete_id": data.copropriete_id, "private_fee_owner_id": sid},
                    {"$set": {"private_fee_owner_id": data.master_id}})
                await db.invoices.update_many(
                    {"copropriete_id": data.copropriete_id, "owner_id": sid},
                    {"$set": {"owner_id": data.master_id}})
            mut_c = await db.mutations.count_documents({
                "copropriete_id": data.copropriete_id,
                "$or": [{"from_owner_id": sid}, {"to_owner_id": sid}]})
            report["mutations_updated"] += mut_c
            if not data.dry_run and mut_c:
                await db.mutations.update_many(
                    {"copropriete_id": data.copropriete_id, "from_owner_id": sid},
                    {"$set": {"from_owner_id": data.master_id}})
                await db.mutations.update_many(
                    {"copropriete_id": data.copropriete_id, "to_owner_id": sid},
                    {"$set": {"to_owner_id": data.master_id}})
            je_c = await db.journal_entries.count_documents({
                "copropriete_id": data.copropriete_id,
                "lines.third_party_id": sid})
            report["journal_entries_updated"] += je_c
            if not data.dry_run and je_c:
                await db.journal_entries.update_many(
                    {"copropriete_id": data.copropriete_id,
                     "lines.third_party_id": sid},
                    {"$set": {"lines.$[l].third_party_id": data.master_id}},
                    array_filters=[{"l.third_party_id": sid}])
            fc_c = await db.fund_calls.count_documents({
                "copropriete_id": data.copropriete_id,
                "distribution.owner_id": sid})
            report["fund_calls_updated"] += fc_c
            if not data.dry_run and fc_c:
                await db.fund_calls.update_many(
                    {"copropriete_id": data.copropriete_id,
                     "distribution.owner_id": sid},
                    {"$set": {"distribution.$[e].owner_id": data.master_id}},
                    array_filters=[{"e.owner_id": sid}])
            ba_c = await db.owner_bank_accounts.count_documents({"owner_id": sid})
            report["bank_accounts_updated"] += ba_c
            if not data.dry_run and ba_c:
                await db.owner_bank_accounts.update_many(
                    {"owner_id": sid},
                    {"$set": {"owner_id": data.master_id}})
            if not data.dry_run:
                await db.owners.delete_one({"id": sid})
                report["slaves_deleted"] += 1
        return report

    class GridfsMigrationInput(BaseModel):
        dry_run: bool = True

    @router.post("/gridfs-migration")
    async def run_gridfs_migration(data: GridfsMigrationInput, request: Request):
        """iter90ga : execute la migration `migrate_uploads_to_gridfs.py`
        depuis l'UI admin (evite d'avoir besoin d'un shell PROD).

        - dry_run=True (defaut) : simule et retourne le plan (compte + bytes)
        - dry_run=False : execute reellement la migration

        Restrict : superadmin only (operation lourde).

        Le script est IDEMPOTENT : les attachments qui ont deja un `gridfs_id`
        sont skip. Peut donc etre rejoue sans risque de double-upload.

        Retour :
        {
          "invoices":       {"migrated": N, "skipped": N, "missing": N, "bytes": N},
          "journal_entries":{"migrated": N, "skipped": N, "missing": N, "bytes": N},
          "documents":      {"migrated": N, "skipped": N, "missing": N, "bytes": N},
          "total_migrated": N,
          "total_bytes": N,
          "duration_ms": N,
          "dry_run": bool,
        }
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(
                403,
                "Migration GridFS reservee au superadmin (operation lourde)",
            )
        # Import lazy pour eviter de charger le script au boot
        import sys as _sys
        import time as _time
        from pathlib import Path
        _sys.path.insert(0, "/app/backend/scripts")
        from migrate_uploads_to_gridfs import (  # noqa: E402
            _migrate_attachments_collection,
            _migrate_documents,
            _create_bundles_ttl_index,
        )
        from gridfs_storage import (
            get_invoice_attachments_storage,
            get_journal_attachments_storage,
        )
        t0 = _time.time()
        inv_storage = get_invoice_attachments_storage(db)
        je_storage = get_journal_attachments_storage(db)
        m, s, mi, b = await _migrate_attachments_collection(
            db, "invoices", inv_storage,
            Path("/app/uploads/invoice_attachments"), data.dry_run,
        )
        m2, s2, mi2, b2 = await _migrate_attachments_collection(
            db, "journal_entries", je_storage,
            Path("/app/uploads/journal_attachments"), data.dry_run,
        )
        m3, s3, mi3, b3 = await _migrate_documents(db, data.dry_run)
        if not data.dry_run:
            await _create_bundles_ttl_index(db)
        duration_ms = int((_time.time() - t0) * 1000)
        return {
            "dry_run": data.dry_run,
            "invoices": {"migrated": m, "skipped": s, "missing": mi, "bytes": b},
            "journal_entries": {"migrated": m2, "skipped": s2, "missing": mi2, "bytes": b2},
            "documents": {"migrated": m3, "skipped": s3, "missing": mi3, "bytes": b3},
            "total_migrated": m + m2 + m3,
            "total_skipped": s + s2 + s3,
            "total_missing": mi + mi2 + mi3,
            "total_bytes": b + b2 + b3,
            "duration_ms": duration_ms,
        }

    # ==================== iter90gk : DUPLICATES AUDIT GLOBAL ====================
    # iter90gn : cache TTL 5min pour eviter le rescan complet a chaque appel.
    # Le stress test a mesure duplicates-audit p95=2440ms - prohibitif si polle.
    # Format cache : {key -> (timestamp, data)}. Cle inclut copro_id + format.
    _audit_cache: dict = {}
    _AUDIT_CACHE_TTL = 300  # 5 minutes

    def _audit_cache_get(key: str):
        import time as _t
        entry = _audit_cache.get(key)
        if not entry:
            return None
        ts, data = entry
        if _t.time() - ts > _AUDIT_CACHE_TTL:
            _audit_cache.pop(key, None)
            return None
        return data

    def _audit_cache_set(key: str, data):
        import time as _t
        _audit_cache[key] = (_t.time(), data)

    # ---- iter90iq : BCE Open Data (dataset officiel local) ------------
    @router.get("/bce-opendata/status")
    async def bce_opendata_status(request: Request):
        """Retourne le statut du dataset local BCE Open Data.
        Superadmin only.
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Reserve au superadmin")
        from bce_opendata import opendata_status
        return await opendata_status(db)

    @router.post("/bce-opendata/upload")
    async def bce_opendata_upload(request: Request, file: UploadFile = File(...)):
        """iter90iq : Ingere un ZIP officiel BCE Open Data (enterprise.csv,
        denomination.csv, address.csv). Superadmin only.

        L'utilisateur telecharge le ZIP mensuel gratuit depuis
        https://kbopub.economie.fgov.be/kbo-open-data/signup (inscription
        gratuite requise) puis l'upload ici. L'ingestion est idempotente
        (upsert par bce_raw) et cree les indexes MongoDB necessaires.

        Retourne un rapport {parsed, inserted, updated, took_ms, snapshot_at}.
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Reserve au superadmin (operation lourde)")
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(400, "Un fichier ZIP est attendu (format officiel BCE Open Data)")
        content = await file.read()
        if len(content) < 1000:
            raise HTTPException(400, "Fichier trop petit ou corrompu")
        from bce_opendata import ingest_bce_opendata_zip
        try:
            report = await ingest_bce_opendata_zip(db, content)
        except Exception as e:
            raise HTTPException(500, f"Erreur ingestion : {e}")
        return {"ok": True, "filename": file.filename, "size_bytes": len(content), **report}



    @router.get("/duplicates-audit")
    async def duplicates_audit(request: Request, format: str = "json", copro_id: Optional[str] = None, force_refresh: bool = False):
        """iter90gk : rapport global anti-doublons pour toutes les ACPs
        (ou une seule si `copro_id` fourni).

        Detecte :
          - Suppliers avec BCE duplique (global)
          - Suppliers avec meme nom + copro (potentiel doublon dans une ACP)
          - Suppliers sans BCE (a completer pour activer la protection strict)
          - Owners avec email/telephone duplique (bloquant)
          - Owners homonymes (meme nom, coordonnees differentes)
          - PCMN accounts orphelins (utilises en JE sans matching supplier fiche)
          - PCMN accounts bancaires dupliques (6-char + 8-char pour la meme banque)
          - Notes de Credit (facture negative) sans ecriture AC

        `format=json` (defaut) ou `format=csv` (telechargement).
        """
        from fastapi.responses import Response
        from collections import defaultdict
        from routes.suppliers import _norm_id, _norm_name_candidates
        await _get_admin_user(request)

        # iter90gn : cache lookup (skip si force_refresh=true ou format=csv qui
        # generate a new response de toute facon). Cle par copro_id.
        cache_key = f"{copro_id or 'all'}|{format}"
        if not force_refresh and format.lower() != "csv":
            cached = _audit_cache_get(cache_key)
            if cached is not None:
                return {**cached, "_cache_hit": True}

        report: dict = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": copro_id or "all_acps",
            "suppliers": {
                "bce_duplicates": [],  # groupes de fiches partageant le meme BCE
                "name_duplicates_per_acp": [],  # groupes de fiches meme nom dans meme ACP
                "missing_bce_count": 0,  # nombre de fiches sans BCE (renseigne=vide/absent)
                "missing_bce_examples": [],  # top 10 fiches sans BCE (avec usage)
            },
            "owners": {
                "email_duplicates": [],  # groupes owner meme email
                "phone_duplicates": [],  # groupes owner meme telephone
                "name_homonyms_per_acp": [],  # homonymes non-strict a valider
            },
            "pcmn_accounts": {
                "orphan_tier_accounts": [],  # 440XXX sans matching supplier fiche
                "duplicated_bank_accounts": [],  # 6-char + 8-char pour meme banque
            },
            "credit_notes_without_entry": [],
            # iter90gn : verification GridFS documents. Sans gridfs_id, les
            # fichiers sont sur filesystem ephemere et perdus au redeploiement.
            "documents_without_gridfs": [],
        }

        # ==== SUPPLIERS ====
        sup_query = {}
        if copro_id:
            sup_query = {"$or": [
                {"copropriete_id": copro_id},
                {f"tier_accounts.{copro_id}": {"$exists": True}},
            ]}
        all_sups = await db.suppliers.find(sup_query, {"_id": 0}).to_list(20000)

        # BCE duplicates global
        by_bce = defaultdict(list)
        for s in all_sups:
            bce = _norm_id(s.get("bce_number", ""))
            if bce:
                by_bce[bce].append(s)
        for bce, group in by_bce.items():
            if len(group) > 1:
                report["suppliers"]["bce_duplicates"].append({
                    "bce": bce,
                    "count": len(group),
                    "suppliers": [{"id": s["id"], "name": s.get("name"), "copro_id": s.get("copropriete_id")} for s in group],
                })

        # Name duplicates per ACP
        by_name_copro = defaultdict(list)
        for s in all_sups:
            copro = s.get("copropriete_id", "")
            cands = _norm_name_candidates(s.get("name", ""))
            for cand in cands:
                by_name_copro[(cand, copro)].append(s)
        seen_pairs = set()
        for (cand, copro), group in by_name_copro.items():
            if len(group) > 1:
                ids = tuple(sorted(s["id"] for s in group))
                if ids in seen_pairs:
                    continue
                seen_pairs.add(ids)
                report["suppliers"]["name_duplicates_per_acp"].append({
                    "name_candidate": cand,
                    "copro_id": copro,
                    "count": len(group),
                    "suppliers": [{"id": s["id"], "name": s.get("name"), "bce": s.get("bce_number","")} for s in group],
                })

        # Suppliers sans BCE (used + BCE missing = a fixer en priorite)
        missing = 0
        examples = []
        for s in all_sups:
            bce = _norm_id(s.get("bce_number", ""))
            if bce:
                continue
            missing += 1
            if len(examples) < 10:
                inv_cnt = await db.invoices.count_documents({"supplier_id": s["id"]})
                if inv_cnt > 0:
                    examples.append({
                        "id": s["id"], "name": s.get("name"),
                        "copro_id": s.get("copropriete_id"),
                        "invoices_using": inv_cnt,
                    })
        report["suppliers"]["missing_bce_count"] = missing
        report["suppliers"]["missing_bce_examples"] = examples

        # ==== OWNERS ====
        owner_query = {"copropriete_id": copro_id} if copro_id else {}
        all_owners = await db.owners.find(owner_query, {"_id": 0}).to_list(20000)
        # Email duplicates
        by_email = defaultdict(list)
        for o in all_owners:
            for f in ("email", "email2"):
                v = (o.get(f) or "").strip().lower()
                if v:
                    by_email[v].append(o)
        for email, group in by_email.items():
            if len(group) > 1:
                ids = set(o["id"] for o in group)
                if len(ids) > 1:
                    report["owners"]["email_duplicates"].append({
                        "email": email, "count": len(ids),
                        "owners": [{"id": o["id"], "name": o.get("name",""), "copro_id": o.get("copropriete_id","")} for o in group],
                    })
        # Phone duplicates
        by_phone = defaultdict(list)
        for o in all_owners:
            for f in ("phone", "phone2"):
                v = "".join(c for c in (o.get(f) or "") if c.isalnum()).upper()
                if v and len(v) >= 6:  # skip trop courts (bruit)
                    by_phone[v].append(o)
        for phone, group in by_phone.items():
            if len(group) > 1:
                ids = set(o["id"] for o in group)
                if len(ids) > 1:
                    report["owners"]["phone_duplicates"].append({
                        "phone": phone, "count": len(ids),
                        "owners": [{"id": o["id"], "name": o.get("name","")} for o in group],
                    })

        # ==== PCMN ACCOUNTS - orphan tier + duplicated bank ====
        pcmn_query = {"copropriete_id": copro_id} if copro_id else {}
        # Tier accounts orphelins : 44000XXX en journal_entries sans tpid + qui
        # ne matche aucune fiche fournisseur
        je_query = {"lines.account_number": {"$regex": "^440"}}
        if copro_id:
            je_query["copropriete_id"] = copro_id
        pipeline = [
            {"$match": je_query},
            {"$unwind": "$lines"},
            {"$match": {"lines.account_number": {"$regex": "^440"}}},
            {"$group": {
                "_id": {"copro": "$copropriete_id", "acc": "$lines.account_number"},
                "count": {"$sum": 1},
                "tpids": {"$addToSet": "$lines.third_party_id"},
                "sample_name": {"$first": "$lines.account_name"},
            }},
        ]
        supplier_tier_by_copro = defaultdict(set)
        for s in all_sups:
            for cid, ta in (s.get("tier_accounts") or {}).items():
                main = (ta or {}).get("main", "")
                if main:
                    supplier_tier_by_copro[cid].add(main)
        async for r in db.journal_entries.aggregate(pipeline):
            cid = r["_id"]["copro"]
            acc = r["_id"]["acc"]
            tpids = [t for t in r["tpids"] if t]
            has_tpid = bool(tpids)
            # Orphan if : NO tpid AND NOT in supplier fiches for that ACP
            if not has_tpid and acc not in supplier_tier_by_copro.get(cid, set()):
                report["pcmn_accounts"]["orphan_tier_accounts"].append({
                    "copro_id": cid, "account": acc,
                    "name": r.get("sample_name") or "",
                    "count": r["count"],
                })
        # Duplicated bank accounts : 6-char + 8-char pour meme copro
        all_pcmn = await db.pcmn_accounts.find(pcmn_query, {"_id": 0, "number": 1, "copropriete_id": 1, "name": 1}).to_list(20000)
        by_copro_prefix = defaultdict(list)
        for p in all_pcmn:
            n = p.get("number", "")
            cid = p.get("copropriete_id", "")
            if n.startswith("55") and len(n) in (6, 8):
                by_copro_prefix[(cid, n[:6])].append(p)
        for (cid, prefix), group in by_copro_prefix.items():
            if len(group) > 1:
                report["pcmn_accounts"]["duplicated_bank_accounts"].append({
                    "copro_id": cid, "prefix": prefix,
                    "accounts": [{"number": p["number"], "name": p.get("name","")} for p in group],
                })

        # ==== CREDIT NOTES sans entry ====
        inv_query = {"total_amount": {"$lt": 0}}
        if copro_id:
            inv_query["copropriete_id"] = copro_id
        async for inv in db.invoices.find(inv_query, {"_id": 0}):
            je = await db.journal_entries.find_one(
                {"source_invoice_id": inv["id"], "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "id": 1},
            )
            if not je:
                report["credit_notes_without_entry"].append({
                    "invoice_id": inv["id"],
                    "internal_reference": inv.get("internal_reference"),
                    "supplier": inv.get("supplier"),
                    "amount": inv.get("total_amount"),
                    "copro_id": inv.get("copropriete_id"),
                    "date": inv.get("date"),
                })

        # ==== iter90gn : GridFS documents check ====
        # Docs sans gridfs_id -> fichiers sur filesystem ephemere. Perdus au
        # redeploiement K8s. Doivent etre re-uploades (ou supprimes si test).
        doc_query = {"gridfs_id": {"$in": [None, ""]}}
        if copro_id:
            doc_query["copropriete_id"] = copro_id
        async for d in db.documents.find(doc_query, {"_id": 0, "id": 1, "title": 1, "copropriete_id": 1, "created_at": 1}).limit(50):
            report["documents_without_gridfs"].append({
                "document_id": d["id"],
                "title": d.get("title", ""),
                "copro_id": d.get("copropriete_id", ""),
                "created_at": d.get("created_at", ""),
            })

        # ==== SUMMARY ====
        report["summary"] = {
            "supplier_bce_duplicates": len(report["suppliers"]["bce_duplicates"]),
            "supplier_name_dup_groups": len(report["suppliers"]["name_duplicates_per_acp"]),
            "supplier_missing_bce": missing,
            "owner_email_dup_groups": len(report["owners"]["email_duplicates"]),
            "owner_phone_dup_groups": len(report["owners"]["phone_duplicates"]),
            "pcmn_orphan_tier": len(report["pcmn_accounts"]["orphan_tier_accounts"]),
            "pcmn_dup_bank": len(report["pcmn_accounts"]["duplicated_bank_accounts"]),
            "credit_notes_missing_entry": len(report["credit_notes_without_entry"]),
            "documents_without_gridfs": len(report["documents_without_gridfs"]),
        }
        report["healthy"] = all(v == 0 for v in report["summary"].values())

        # iter90gn : cache le resultat pour les 5 minutes suivantes (JSON only)
        if format.lower() != "csv":
            _audit_cache_set(cache_key, report)

        if format.lower() == "csv":
            # CSV export : tableau plat pour analyse dans Excel
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["category", "type", "detail", "copro_id", "count"])
            for g in report["suppliers"]["bce_duplicates"]:
                w.writerow(["supplier", "bce_dup", g["bce"], ";".join(s["copro_id"] or "" for s in g["suppliers"]), g["count"]])
            for g in report["suppliers"]["name_duplicates_per_acp"]:
                w.writerow(["supplier", "name_dup", g["name_candidate"], g["copro_id"], g["count"]])
            for ex in report["suppliers"]["missing_bce_examples"]:
                w.writerow(["supplier", "missing_bce", f"{ex['name']} (id={ex['id'][:8]})", ex["copro_id"], ex["invoices_using"]])
            for g in report["owners"]["email_duplicates"]:
                w.writerow(["owner", "email_dup", g["email"], "", g["count"]])
            for g in report["owners"]["phone_duplicates"]:
                w.writerow(["owner", "phone_dup", g["phone"], "", g["count"]])
            for r in report["pcmn_accounts"]["orphan_tier_accounts"]:
                w.writerow(["pcmn", "orphan_tier", f"{r['account']} - {r['name']}", r["copro_id"], r["count"]])
            for r in report["pcmn_accounts"]["duplicated_bank_accounts"]:
                w.writerow(["pcmn", "dup_bank", "+".join(a["number"] for a in r["accounts"]), r["copro_id"], len(r["accounts"])])
            for r in report["credit_notes_without_entry"]:
                w.writerow(["credit_note", "missing_entry", f"{r['internal_reference']} - {r['supplier']} {r['amount']}", r["copro_id"], 1])
            for d in report["documents_without_gridfs"]:
                w.writerow(["document", "missing_gridfs", d["title"] or d["document_id"], d["copro_id"], 1])
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=duplicates-audit-{report['generated_at'][:10]}.csv"},
            )
        return report

    # iter90i1 : Endpoint superadmin pour executer la migration des uploads
    # vers MongoDB GridFS en production. Idempotent (skip les documents deja
    # migres), supporte le mode dry-run pour prevoir avant execution.
    @router.post("/migrate-uploads-to-gridfs")
    async def migrate_uploads_to_gridfs(request: Request, dry_run: bool = True):
        """Execute la migration des pieces jointes/documents du filesystem
        vers MongoDB GridFS. Superadmin uniquement (protection critique :
        cette operation lit/ecrit sur tous les buckets).

        - `dry_run=True` (defaut) : compte les fichiers, aucune ecriture.
        - `dry_run=False` : effectue la migration reelle et retourne le
          resume complet.

        La collection `db.invoice_bundle_sessions` recoit un index TTL
        automatique (expires_at, expireAfterSeconds=0) uniquement en mode
        live.
        """
        await _get_superadmin_only(request)
        from pathlib import Path
        # Import differe pour eviter les imports transitifs au demarrage
        import sys as _sys
        _sys.path.insert(0, "/app/backend")
        from scripts.migrate_uploads_to_gridfs import (
            _migrate_attachments_collection,
            _migrate_documents,
            _create_bundles_ttl_index,
            _human_bytes,
        )
        from gridfs_storage import (
            get_invoice_attachments_storage,
            get_journal_attachments_storage,
        )
        started = datetime.now(timezone.utc)
        inv_storage = get_invoice_attachments_storage(db)
        je_storage = get_journal_attachments_storage(db)

        # 1) invoices.attachments[]
        m1, s1, mi1, b1 = await _migrate_attachments_collection(
            db, "invoices", inv_storage,
            Path("/app/uploads/invoice_attachments"), dry_run,
        )
        # 2) journal_entries.attachments[]
        m2, s2, mi2, b2 = await _migrate_attachments_collection(
            db, "journal_entries", je_storage,
            Path("/app/uploads/journal_attachments"), dry_run,
        )
        # 3) documents
        m3, s3, mi3, b3 = await _migrate_documents(db, dry_run)
        # 4) TTL index (live seulement)
        ttl_info = "skipped (dry_run)"
        if not dry_run:
            try:
                await _create_bundles_ttl_index(db)
                ttl_info = "created (or already present)"
            except Exception as e:
                ttl_info = f"failed: {e}"

        finished = datetime.now(timezone.utc)
        totals = {
            "migrated": m1 + m2 + m3,
            "skipped_already_in_gridfs": s1 + s2 + s3,
            "missing_file_on_disk": mi1 + mi2 + mi3,
            "total_bytes": b1 + b2 + b3,
            "total_bytes_human": _human_bytes(b1 + b2 + b3),
        }
        return {
            "mode": "dry_run" if dry_run else "live",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "invoices_attachments": {
                "migrated": m1, "skipped": s1, "missing": mi1,
                "bytes": b1, "bytes_human": _human_bytes(b1),
            },
            "journal_attachments": {
                "migrated": m2, "skipped": s2, "missing": mi2,
                "bytes": b2, "bytes_human": _human_bytes(b2),
            },
            "documents": {
                "migrated": m3, "skipped": s3, "missing": mi3,
                "bytes": b3, "bytes_human": _human_bytes(b3),
            },
            "ttl_index_invoice_bundle_sessions": ttl_info,
            "totals": totals,
        }

    # iter90i6 : Endpoint superadmin pour "guerir" les notes de credit sans
    # ecriture AC (bug historique : `if amount <= 0: return None` skipait la
    # generation d'ecriture pour les NC). Regenere les entrees AC manquantes
    # via generate_purchase_entry (qui gere desormais les NC correctement :
    # Dr fournisseur / Cr charge).
    @router.post("/heal-credit-notes")
    async def heal_credit_notes_endpoint(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        """Trouve toutes les invoices avec `total_amount < 0` sans
        journal_entry AC associee et regenere l'ecriture. Idempotent.
        Superadmin only.

        Params :
          - `copropriete_id` : scope optionnel a une ACP (defaut : toutes)
          - `dry_run=True` (defaut) : compte sans creer
          - `dry_run=False` : cree les ecritures manquantes
        """
        await _get_superadmin_only(request)
        from auto_entries import generate_purchase_entry
        query: dict = {"total_amount": {"$lt": 0}}
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        healed_details = []
        skipped_details = []
        cursor = db.invoices.find(query, {"_id": 0})
        async for inv in cursor:
            inv_id = inv["id"]
            # Existe-t-il deja une AC pour cette NC ?
            existing = await db.journal_entries.find_one(
                {"source_id": inv_id, "source_type": "invoice",
                 "journal_type": "AC",
                 "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "id": 1},
            )
            if existing:
                skipped_details.append({
                    "invoice_id": inv_id,
                    "number": inv.get("number", ""),
                    "supplier": inv.get("supplier", ""),
                    "amount": float(inv.get("total_amount", 0) or 0),
                    "reason": "je_exists",
                    "je_id": existing["id"],
                })
                continue
            if dry_run:
                healed_details.append({
                    "invoice_id": inv_id,
                    "number": inv.get("number", ""),
                    "supplier": inv.get("supplier", ""),
                    "amount": float(inv.get("total_amount", 0) or 0),
                    "date": inv.get("date", ""),
                    "would_create_je": True,
                })
                continue
            # Live : appelle le generateur (qui gere is_credit_note)
            try:
                je = await generate_purchase_entry(db, inv)
                if je:
                    healed_details.append({
                        "invoice_id": inv_id,
                        "number": inv.get("number", ""),
                        "supplier": inv.get("supplier", ""),
                        "amount": float(inv.get("total_amount", 0) or 0),
                        "je_id": je["id"],
                        "reference": je.get("reference", ""),
                    })
                else:
                    skipped_details.append({
                        "invoice_id": inv_id,
                        "number": inv.get("number", ""),
                        "supplier": inv.get("supplier", ""),
                        "amount": float(inv.get("total_amount", 0) or 0),
                        "reason": "generator_returned_none",
                    })
            except Exception as e:
                skipped_details.append({
                    "invoice_id": inv_id,
                    "number": inv.get("number", ""),
                    "reason": "generator_error",
                    "error": str(e)[:200],
                })
        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "healed_count": len(healed_details),
            "skipped_count": len(skipped_details),
            "healed": healed_details[:100],
            "skipped": skipped_details[:100],
            "healed_total": len(healed_details),
            "skipped_total": len(skipped_details),
        }

    # iter90i7 : Nettoie les ecritures AP legacy en doublon avec les VE
    # auto-generees. Bug historique : la route legacy /fund-calls/{id}/generate-entries
    # creait un JE AP alors que le POST /fund-calls creait deja un VE via
    # generate_sale_entry, ce qui doublait le solde du fonds de reserve/roulement
    # sur le bilan (5000 AN + 2000 VE + 2000 AP = 9000 au lieu de 7000).
    @router.post("/heal-duplicate-fund-call-entries")
    async def heal_duplicate_fund_call_entries(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        """Trouve les JE AP en doublon (meme `fund_call_id` qu'une JE VE
        existante non-contre-passee) et les supprime (mode live) ou les
        liste (dry_run). Superadmin only, idempotent."""
        await _get_superadmin_only(request)
        dupes = []
        query: dict = {"journal_type": "AP", "fund_call_id": {"$exists": True}}
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        async for ap in db.journal_entries.find(query, {"_id": 0}):
            call_id = ap.get("fund_call_id")
            if not call_id:
                continue
            ve = await db.journal_entries.find_one(
                {"source_type": "fund_call", "source_id": call_id,
                 "journal_type": "VE",
                 "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
                {"_id": 0, "id": 1, "reference": 1, "total_debit": 1},
            )
            if not ve:
                continue
            dupes.append({
                "ap_id": ap["id"],
                "ap_reference": ap.get("reference", ""),
                "ap_amount": float(ap.get("total_debit", 0) or 0),
                "ve_id": ve["id"],
                "ve_reference": ve.get("reference", ""),
                "ve_amount": float(ve.get("total_debit", 0) or 0),
                "fund_call_id": call_id,
                "copropriete_id": ap.get("copropriete_id", ""),
                "date": ap.get("date", ""),
            })
        removed = 0
        if not dry_run:
            for d in dupes:
                r = await db.journal_entries.delete_one({"id": d["ap_id"]})
                if r.deleted_count:
                    removed += 1
        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "duplicates_found": len(dupes),
            "removed": removed,
            "duplicates": dupes[:200],
        }

    # iter90ig : "Guerit" les ACP dont les tier_accounts owners pointent vers
    # des comptes Optipro 7-char (4100XXX au lieu du canonique 4101XXXX) ou
    # dont la balance des tiers affiche des lignes "Ex-prop." dupliquees.
    #
    # Actions (par ACP) :
    #   1. Pour chaque owner de l'ACP avec un compte "provisions" ou "reserve"
    #      corrompu, on:
    #      a. Determine son compte CANONIQUE via `assign_owner_accounts`
    #         (cree si absent, garde si legacy 40000/40010).
    #      b. Reecrit toutes les lignes journal_entries qui utilisent le
    #         compte pollue vers le compte canonique.
    #      c. Met a jour `tier_accounts.{copro_id}` sur le owner.
    #      d. Optionnellement supprime le compte pcmn "orphelin" si plus
    #         aucune ligne ne l'utilise dans cette ACP.
    @router.post("/heal-optipro-owner-accounts")
    async def heal_optipro_owner_accounts(
        request: Request,
        copropriete_id: str,
        dry_run: bool = True,
        include_suppliers: bool = True,
    ):
        """Fusionne les comptes Optipro pollues (4100XXX 7-char) vers les
        comptes canoniques (4101XXXX) crees automatiquement par notre
        systeme. Superadmin only.

        Un compte "corrompu" est :
          - `provisions` avec longueur 7 (ex: 4100959) OU ne commence pas
            par 4101/40000 (nouveau/legacy schema).
          - `reserve` avec longueur 7 (ex: 4001959) OU ne commence pas
            par 4100/40010.
          - `main` (supplier) avec longueur != 8 OU ne commence pas par 44000.

        NOTE : les comptes Optipro `4100XXX` sont ambigus car ils
        collident avec le NOUVEAU prefixe reserve `4100XXXX`. La
        detection utilise donc la LONGUEUR (7 = Optipro, 8 = canonique).
        """
        await _get_superadmin_only(request)
        from tier_accounts import assign_owner_accounts as _assign_owner_accounts
        from tier_accounts import assign_supplier_account as _assign_supplier_account

        def _is_bad_prov(acc: str) -> bool:
            if not acc:
                return False
            # Optipro 7-char ou schema non-standard
            return len(acc) == 7 or not acc.startswith(("4101", "40000"))

        def _is_bad_res(acc: str) -> bool:
            if not acc:
                return False
            # Optipro 7-char (4001XXX) ou 8-char sans prefixe correct
            return len(acc) == 7 or not acc.startswith(("4100", "40010"))

        def _is_bad_supplier_main(acc: str) -> bool:
            if not acc:
                return False
            # Canonique = 44000XXX (8 chars). Tout autre format (7-char
            # Optipro 4400XXX, 44001XXX legacy) est considere corrompu.
            return len(acc) != 8 or not acc.startswith("44000")

        heals = []
        remaps: dict = {}  # source_acc -> canonical_acc
        supplier_heals = []

        async for o in db.owners.find(
            {"copropriete_ids": copropriete_id}, {"_id": 0},
        ):
            oid = o["id"]
            ta = (o.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            prov = (ta.get("provisions") or "").strip()
            reserve = (ta.get("reserve") or "").strip()
            bad_prov = _is_bad_prov(prov)
            bad_res = _is_bad_res(reserve)
            if not bad_prov and not bad_res:
                continue
            # Cree/recupere les comptes canoniques
            fresh_ta = {**ta}
            if bad_prov:
                fresh_ta.pop("provisions", None)
            if bad_res:
                fresh_ta.pop("reserve", None)
            o_with_reset = {**o, "tier_accounts": {**(o.get("tier_accounts") or {}), copropriete_id: fresh_ta}}
            if not dry_run:
                await db.owners.update_one(
                    {"id": oid},
                    {"$set": {f"tier_accounts.{copropriete_id}": fresh_ta}},
                )
                o_after = await _assign_owner_accounts(db, o_with_reset, copropriete_id)
            else:
                # En dry_run on simule : le canonique sera 4101XXXX/4100XXXX
                # base sur la sequence existante (approximation).
                o_after = o_with_reset
            new_ta = (o_after.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            entry = {
                "owner_id": oid,
                "owner_name": o.get("name") or f"{o.get('last_name','')} {o.get('first_name','')}",
                "before": ta,
                "after": new_ta if not dry_run else "canonique_a_creer",
                "bad_provisions": bad_prov,
                "bad_reserve": bad_res,
            }
            heals.append(entry)
            if bad_prov and prov and not dry_run:
                remaps[prov] = new_ta.get("provisions", prov)
            if bad_res and reserve and not dry_run:
                remaps[reserve] = new_ta.get("reserve", reserve)

        # iter90ih : meme logique pour les SUPPLIERS pollues
        if include_suppliers:
            async for sup in db.suppliers.find(
                {"copropriete_id": copropriete_id}, {"_id": 0},
            ):
                sid = sup["id"]
                ta = (sup.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                main = (ta.get("main") or "").strip()
                if not _is_bad_supplier_main(main):
                    continue
                # Reset et re-assign
                fresh_ta = {k: v for k, v in ta.items() if k != "main"}
                if not dry_run:
                    await db.suppliers.update_one(
                        {"id": sid},
                        {"$set": {f"tier_accounts.{copropriete_id}": fresh_ta}},
                    )
                    sup_after = await _assign_supplier_account(
                        db,
                        {**sup, "tier_accounts": {**(sup.get("tier_accounts") or {}), copropriete_id: fresh_ta}},
                        copropriete_id,
                    )
                else:
                    sup_after = sup
                new_ta = (sup_after.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                supplier_heals.append({
                    "supplier_id": sid,
                    "supplier_name": sup.get("name") or "?",
                    "before": {"main": main},
                    "after": new_ta if not dry_run else "canonique_a_creer",
                })
                if not dry_run and new_ta.get("main"):
                    remaps[main] = new_ta["main"]

        # Reecriture des lignes journal_entries : source_acc -> canonical
        lines_remapped = 0
        entries_touched = 0
        if not dry_run and remaps:
            async for je in db.journal_entries.find(
                {"copropriete_id": copropriete_id}, {"_id": 0, "id": 1, "lines": 1},
            ):
                new_lines = []
                touched = False
                for ln in je.get("lines") or []:
                    acc = (ln.get("account_number") or "").strip()
                    if acc in remaps:
                        new_lines.append({**ln, "account_number": remaps[acc]})
                        lines_remapped += 1
                        touched = True
                    else:
                        new_lines.append(ln)
                if touched:
                    entries_touched += 1
                    await db.journal_entries.update_one(
                        {"id": je["id"]}, {"$set": {"lines": new_lines}},
                    )

        # Supprime les comptes pcmn orphelins (plus utilises)
        pcmn_deleted = 0
        if not dry_run:
            for old_acc in remaps.keys():
                still_used = await db.journal_entries.count_documents({
                    "copropriete_id": copropriete_id,
                    "lines.account_number": old_acc,
                })
                if still_used == 0:
                    r = await db.pcmn_accounts.delete_one(
                        {"copropriete_id": copropriete_id, "number": old_acc}
                    )
                    if r.deleted_count:
                        pcmn_deleted += 1

        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id,
            "owners_healed": len(heals),
            "suppliers_healed": len(supplier_heals),
            "account_remaps": remaps,
            "journal_entries_touched": entries_touched,
            "lines_remapped": lines_remapped,
            "pcmn_accounts_deleted": pcmn_deleted,
            "details": heals[:200],
            "supplier_details": supplier_heals[:200],
        }

    # iter90ih -> iter90ii : Deduplication des natures de depenses.
    # Regroupement par (copropriete_id, NAME) (regle metier revisee : 2
    # natures peuvent partager le meme compte comptable, mais pas le meme
    # nom). Le PLUS ANCIEN gagne, les autres sont supprimes, les factures
    # qui referencaient un doublon sont repointees vers le survivant.
    @router.post("/heal-duplicate-natures")
    async def heal_duplicate_natures(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        await _get_superadmin_only(request)
        match_stage: dict = {}
        if copropriete_id:
            match_stage["copropriete_id"] = copropriete_id
        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": {"copro": "$copropriete_id", "name": "$name"},
                "count": {"$sum": 1},
                "docs": {"$push": {
                    "id": "$id",
                    "account_number": "$account_number",
                    "created_at": "$created_at",
                }},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        groups = await db.expense_categories.aggregate(pipeline).to_list(1000)

        heals = []
        deleted = 0
        invoices_repointed = 0
        for g in groups:
            copro = g["_id"]["copro"]
            name = g["_id"]["name"]
            docs = sorted(g["docs"], key=lambda d: (d.get("created_at") or ""))
            keeper = docs[0]
            trash = docs[1:]
            trash_ids = [t["id"] for t in trash]
            entry = {
                "copro": copro,
                "name": name,
                "keeper": {"id": keeper["id"], "account_number": keeper.get("account_number", "")},
                "removed_ids": trash_ids,
                "removed_count": len(trash_ids),
            }
            heals.append(entry)
            if not dry_run:
                res = await db.invoices.update_many(
                    {"copropriete_id": copro, "expense_category_id": {"$in": trash_ids}},
                    {"$set": {"expense_category_id": keeper["id"]}},
                )
                invoices_repointed += res.modified_count
                r2 = await db.expense_categories.delete_many({"id": {"$in": trash_ids}})
                deleted += r2.deleted_count
        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "duplicate_groups": len(heals),
            "deleted_natures": deleted,
            "invoices_repointed": invoices_repointed,
            "details": heals[:200],
        }

    # iter90ih : Deduplication des comptes PCMN par (ACP, number). En theorie,
    # aucun doublon ne devrait exister (contrainte metier a la creation). Cet
    # endpoint sert de filet de securite si un import brut a contourne les
    # gardes-fous.
    @router.post("/heal-duplicate-pcmn")
    async def heal_duplicate_pcmn(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        await _get_superadmin_only(request)
        match_stage: dict = {}
        if copropriete_id:
            match_stage["copropriete_id"] = copropriete_id
        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": {"copro": "$copropriete_id", "num": "$number"},
                "count": {"$sum": 1},
                "ids": {"$push": {
                    "id": "$_id",
                    "name": "$name",
                }},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        groups = await db.pcmn_accounts.aggregate(pipeline).to_list(1000)
        heals = []
        deleted = 0
        for g in groups:
            ids = g["ids"]
            keeper = ids[0]
            trash = ids[1:]
            heals.append({
                "copro": g["_id"]["copro"],
                "number": g["_id"]["num"],
                "kept": {"id": str(keeper["id"]), "name": keeper.get("name", "")},
                "removed_count": len(trash),
            })
            if not dry_run:
                # NOTE : PCMN docs use MongoDB _id (ObjectId) since no `id`
                # field is set for them. We delete by _id.
                for t in trash:
                    r = await db.pcmn_accounts.delete_one({"_id": t["id"]})
                    if r.deleted_count:
                        deleted += 1
        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "duplicate_groups": len(heals),
            "deleted_pcmn": deleted,
            "details": heals[:200],
        }

    # iter90ii : Deduplication des FOURNISSEURS par (ACP, nom normalise).
    # Un fournisseur dupplique (meme nom, meme ACP) est fusionne : le PLUS
    # ANCIEN est garde, les factures/OD referencant les doublons sont
    # repointees vers le survivant. Superadmin only.
    @router.post("/heal-duplicate-suppliers")
    async def heal_duplicate_suppliers(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        """iter90ii/ij : Deduplication des SUPPLIERS par nom normalise.
        Nettoie aussi les tier_accounts orphelins. Le survivant est le plus
        ancien, factures + journal_entries.lines sont repointees."""
        await _get_superadmin_only(request)
        import re
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip().lower())

        query = {"copropriete_id": copropriete_id} if copropriete_id else {}
        all_sups = await db.suppliers.find(query, {"_id": 0}).to_list(50000)
        groups: dict = {}
        for s in all_sups:
            name = _norm(s.get("name", ""))
            if not name:
                continue
            key = (s.get("copropriete_id") or "", name)
            groups.setdefault(key, []).append(s)
        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        heals = []
        deleted = 0
        invoices_repointed = 0
        orphan_ta_cleaned = 0
        for (copro, name), docs in dup_groups.items():
            docs_sorted = sorted(docs, key=lambda d: (d.get("created_at") or ""))
            keeper = docs_sorted[0]
            trash = docs_sorted[1:]
            trash_ids = [t["id"] for t in trash]
            # Merge tier_accounts (union, garde uniquement pour ACP == copro
            # car un supplier a un `copropriete_id` scalar, pas array)
            merged_ta = dict(keeper.get("tier_accounts") or {})
            for t in trash:
                for c, v in (t.get("tier_accounts") or {}).items():
                    if c not in merged_ta:
                        merged_ta[c] = v
            heals.append({
                "copro": copro,
                "name": name,
                "keeper": {"id": keeper["id"], "created_at": keeper.get("created_at", "")},
                "removed_ids": trash_ids,
                "removed_count": len(trash_ids),
            })
            if not dry_run:
                res = await db.invoices.update_many(
                    {"copropriete_id": copro, "supplier_id": {"$in": trash_ids}},
                    {"$set": {"supplier_id": keeper["id"]}},
                )
                invoices_repointed += res.modified_count
                async for je in db.journal_entries.find(
                    {"copropriete_id": copro, "lines.third_party_id": {"$in": trash_ids}},
                    {"_id": 0, "id": 1, "lines": 1},
                ):
                    new_lines = [
                        ({**ln, "third_party_id": keeper["id"]}
                         if ln.get("third_party_id") in trash_ids else ln)
                        for ln in (je.get("lines") or [])
                    ]
                    await db.journal_entries.update_one(
                        {"id": je["id"]}, {"$set": {"lines": new_lines}}
                    )
                r2 = await db.suppliers.delete_many({"id": {"$in": trash_ids}})
                deleted += r2.deleted_count
                # Update keeper avec merged tier_accounts
                if merged_ta != (keeper.get("tier_accounts") or {}):
                    await db.suppliers.update_one(
                        {"id": keeper["id"]},
                        {"$set": {"tier_accounts": merged_ta}},
                    )
        # Second passage : nettoie les tier_accounts orphelins sur suppliers
        if not dry_run:
            async for s in db.suppliers.find({}, {"_id": 0}):
                sup_copro = s.get("copropriete_id") or ""
                ta = s.get("tier_accounts") or {}
                # Un supplier appartient a une seule ACP. Ses tier_accounts
                # ne devraient contenir que cette ACP.
                cleaned = {c: v for c, v in ta.items() if c == sup_copro}
                if len(cleaned) < len(ta):
                    orphan_ta_cleaned += len(ta) - len(cleaned)
                    await db.suppliers.update_one(
                        {"id": s["id"]},
                        {"$set": {"tier_accounts": cleaned}},
                    )
        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "duplicate_groups": len(heals),
            "deleted_suppliers": deleted,
            "invoices_repointed": invoices_repointed,
            "orphan_tier_accounts_cleaned": orphan_ta_cleaned,
            "details": heals[:200],
        }

    # iter90ii : Deduplication des PROPRIETAIRES par (ACP, auxiliary_code).
    # Un owner duplique (meme aux_code + partage au moins une ACP) est
    # fusionne : le PLUS ANCIEN garde toutes les ACP, les lots + tenants +
    # journal_entries des doublons sont repointes vers le survivant.
    @router.post("/heal-duplicate-owners")
    async def heal_duplicate_owners(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
        cross_acp: bool = True,
    ):
        """iter90ii/ij : Deduplication des OWNERS par auxiliary_code.

        Deux modes :
          * `cross_acp=True` (default) : fusion GLOBALE par `auxiliary_code`.
            Deux owners avec le meme aux_code sont consideres UNE SEULE
            personne physique. Le survivant herite de TOUTES les ACPs des
            doublons (union), et de tous les `tier_accounts`. C'est la
            regle metier utilisateur : "aucun proprietaire ne peut etre
            dedouble".
          * `cross_acp=False` : ancien comportement, ne fusionne que si
            les 2 owners ont au moins une ACP en commun.

        Nettoie AUSSI les `tier_accounts` orphelins : entree qui pointe
        vers une ACP non presente dans `copropriete_ids` du owner.

        Actions par groupe (dry_run=False) :
          1. Repoint lots.owner_id vers le survivant
          2. Repoint tenants.owner_id
          3. Repoint journal_entries.lines.third_party_id
          4. Merge tier_accounts (union, garde les entrees pour ACP dans
             copropriete_ids seulement)
          5. Delete les owners doublons
          6. Nettoie les tier_accounts orphelins du survivant
        """
        await _get_superadmin_only(request)
        query = {}
        if copropriete_id and not cross_acp:
            query["copropriete_ids"] = copropriete_id
        all_owners = await db.owners.find(query, {"_id": 0}).to_list(50000)

        groups: dict = {}
        if cross_acp:
            # Groupe global par aux_code seul
            for o in all_owners:
                aux = (o.get("auxiliary_code") or "").strip()
                if not aux:
                    continue
                groups.setdefault(aux, []).append(o)
        else:
            # Groupe par (ACP, aux_code) - ancien comportement
            for o in all_owners:
                aux = (o.get("auxiliary_code") or "").strip()
                if not aux:
                    continue
                for cid in (o.get("copropriete_ids") or []):
                    if copropriete_id and cid != copropriete_id:
                        continue
                    groups.setdefault((cid, aux), []).append(o)

        # Un groupe = doublon si >= 2 owners distincts partagent la cle
        dup_groups: dict = {}
        for k, v in groups.items():
            unique_ids = {x["id"] for x in v}
            if len(unique_ids) > 1:
                seen = set()
                uniques = []
                for o in v:
                    if o["id"] in seen:
                        continue
                    seen.add(o["id"])
                    uniques.append(o)
                dup_groups[k] = uniques

        heals = []
        deleted = 0
        lots_repointed = 0
        tenants_repointed = 0
        je_lines_repointed = 0
        orphan_ta_cleaned = 0

        for gkey, docs in dup_groups.items():
            docs_sorted = sorted(docs, key=lambda d: (d.get("created_at") or ""))
            keeper = docs_sorted[0]
            trash = docs_sorted[1:]
            trash_ids = [t["id"] for t in trash]
            # Union des ACPs
            all_acps = set(keeper.get("copropriete_ids") or [])
            for t in trash:
                all_acps.update(t.get("copropriete_ids") or [])
            all_acps = sorted(all_acps)
            # Merge tier_accounts (garde uniquement pour ACPs valides)
            merged_ta = dict(keeper.get("tier_accounts") or {})
            for t in trash:
                for c, v in (t.get("tier_accounts") or {}).items():
                    if c not in merged_ta and c in all_acps:
                        merged_ta[c] = v
            # Nettoie les tier_accounts orphelins (ACP pas dans union)
            cleaned_ta = {c: v for c, v in merged_ta.items() if c in all_acps}
            orphan_ta_cleaned += len(merged_ta) - len(cleaned_ta)

            aux_str = gkey if cross_acp else f"{gkey[0][:8]}/{gkey[1]}"
            heals.append({
                "aux_code": aux_str,
                "keeper": {
                    "id": keeper["id"],
                    "name": keeper.get("name") or "",
                    "created_at": keeper.get("created_at", ""),
                    "final_copropriete_ids": all_acps,
                    "final_tier_accounts_count": len(cleaned_ta),
                },
                "removed_ids": trash_ids,
                "removed_names": [t.get("name") or "" for t in trash],
                "removed_count": len(trash_ids),
            })
            if dry_run:
                continue

            # Repoint lots.owner_id
            for cid in all_acps:
                res = await db.lots.update_many(
                    {"copropriete_id": cid, "owner_id": {"$in": trash_ids}},
                    {"$set": {"owner_id": keeper["id"]}},
                )
                lots_repointed += res.modified_count
                # Repoint tenants
                try:
                    res2 = await db.tenants.update_many(
                        {"copropriete_id": cid, "owner_id": {"$in": trash_ids}},
                        {"$set": {"owner_id": keeper["id"]}},
                    )
                    tenants_repointed += res2.modified_count
                except Exception:
                    pass
                # Repoint journal_entries.lines
                async for je in db.journal_entries.find(
                    {"copropriete_id": cid, "lines.third_party_id": {"$in": trash_ids}},
                    {"_id": 0, "id": 1, "lines": 1},
                ):
                    new_lines = []
                    touched_cnt = 0
                    for ln in (je.get("lines") or []):
                        if ln.get("third_party_id") in trash_ids:
                            new_lines.append({**ln, "third_party_id": keeper["id"]})
                            touched_cnt += 1
                        else:
                            new_lines.append(ln)
                    je_lines_repointed += touched_cnt
                    if touched_cnt:
                        await db.journal_entries.update_one(
                            {"id": je["id"]}, {"$set": {"lines": new_lines}}
                        )
            # Delete les doublons
            for t in trash:
                r = await db.owners.delete_one({"id": t["id"]})
                if r.deleted_count:
                    deleted += 1
            # Update keeper : union des ACPs + tier_accounts nettoye
            await db.owners.update_one(
                {"id": keeper["id"]},
                {"$set": {
                    "copropriete_ids": all_acps,
                    "tier_accounts": cleaned_ta,
                }},
            )

        # Second passage : nettoie les tier_accounts orphelins sur les
        # owners restants qui n'ont PAS ete traites par la fusion.
        if not dry_run:
            async for o in db.owners.find({}, {"_id": 0}):
                acps = set(o.get("copropriete_ids") or [])
                ta = o.get("tier_accounts") or {}
                cleaned = {c: v for c, v in ta.items() if c in acps}
                if len(cleaned) < len(ta):
                    orphan_ta_cleaned += len(ta) - len(cleaned)
                    await db.owners.update_one(
                        {"id": o["id"]},
                        {"$set": {"tier_accounts": cleaned}},
                    )

        return {
            "mode": "dry_run" if dry_run else "live",
            "scope": "cross_acp" if cross_acp else f"acp={copropriete_id or 'all'}",
            "duplicate_groups": len(heals),
            "deleted_owners": deleted,
            "lots_repointed": lots_repointed,
            "tenants_repointed": tenants_repointed,
            "journal_lines_repointed": je_lines_repointed,
            "orphan_tier_accounts_cleaned": orphan_ta_cleaned,
            "details": heals[:200],
        }

    # iter90ij : Detection et fusion des COMPTES TIERS ORPHELINS (owners + suppliers).
    #
    # Symptomes :
    #   * Balance des tiers OWNERS : lignes "Ex-prop." avec comptes 8-char
    #     inconnus (`41010959`, `41010956` = prefixe canonique + suffixe
    #     Optipro aux_code).
    #   * Balance FOURNISSEURS : lignes dupliquees (44000009 + 4400015 pour
    #     AG Insurance) avec des comptes 7-char Optipro (`4400015`, `4400110`)
    #     ET 8-char (`44000008`, `44000009`) sans supplier associe.
    #
    # Strategie unifiee (owners + suppliers) :
    #   1. Recense les comptes tiers CANONIQUES (tier_accounts.provisions/
    #      reserve pour owners, tier_accounts.main pour suppliers) pour l'ACP.
    #   2. Liste les comptes 41xxx / 4001xxx / 44xxxx utilises dans les JE.
    #   3. Pour chaque compte ORPHELIN (utilise mais pas dans les canoniques) :
    #      a. Match via `third_party_id` de la ligne (canonical du party).
    #      b. Sinon match owner via les 4 derniers chars = aux_code suffix.
    #      c. Sinon match supplier via `account_name` de la ligne (nom du
    #         fournisseur present dans les JE) vs nom des suppliers de l'ACP.
    #   4. Reecrit les lignes JE vers le compte canonique et remplit
    #      third_party_id.
    #   5. Supprime les comptes PCMN orphelins non-utilises.
    @router.post("/heal-orphan-tier-accounts")
    async def heal_orphan_tier_accounts(
        request: Request,
        copropriete_id: str,
        dry_run: bool = True,
    ):
        await _get_superadmin_only(request)
        import re

        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip().lower())

        # 1. OWNERS de l'ACP
        owners = await db.owners.find(
            {"copropriete_ids": copropriete_id}, {"_id": 0},
        ).to_list(10000)
        valid_owner_accounts = set()
        aux_to_owner: dict = {}
        for o in owners:
            ta = (o.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            if ta.get("provisions"):
                valid_owner_accounts.add(ta["provisions"])
            if ta.get("reserve"):
                valid_owner_accounts.add(ta["reserve"])
            aux = (o.get("auxiliary_code") or "").strip().upper()
            if aux:
                digits = aux[1:] if aux.startswith("C") else aux
                aux_to_owner[digits.zfill(4)] = o

        # 2. SUPPLIERS de l'ACP + global (autres ACPs pour matching cross-ACP)
        suppliers_acp = await db.suppliers.find(
            {"copropriete_id": copropriete_id}, {"_id": 0},
        ).to_list(10000)
        # iter90il : cherche AUSSI les suppliers d'autres ACPs (matching global
        # par nom pour retrouver le supplier canonique meme s'il a ete cree
        # dans une autre ACP - ex: Engie present sur ACP Maria mais pas sur
        # ACP Acacia Auto). Le healing va CLONER le supplier vers l'ACP cible
        # (avec BCE conserve) et rebasculer les tp_ids.
        suppliers_global = await db.suppliers.find(
            {"copropriete_id": {"$ne": copropriete_id}}, {"_id": 0},
        ).to_list(50000)
        valid_supplier_accounts = set()
        name_to_supplier: dict = {}
        name_to_supplier_global: dict = {}
        for s in suppliers_acp:
            ta = (s.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            if ta.get("main"):
                valid_supplier_accounts.add(ta["main"])
            name_norm = _norm(s.get("name", ""))
            if name_norm:
                name_to_supplier[name_norm] = s
        for s in suppliers_global:
            name_norm = _norm(s.get("name", ""))
            # Retire prefixe "F0110 - Engie" -> "Engie"
            cleaned = re.sub(r"^F\d{3,4}\s*-\s*", "", s.get("name", ""), flags=re.IGNORECASE).strip()
            cleaned_norm = _norm(cleaned)
            if name_norm and name_norm not in name_to_supplier:
                name_to_supplier_global[name_norm] = s
            if cleaned_norm and cleaned_norm != name_norm and cleaned_norm not in name_to_supplier:
                name_to_supplier_global.setdefault(cleaned_norm, s)

        valid_accounts = valid_owner_accounts | valid_supplier_accounts

        # 3. Comptes utilises dans les JE de l'ACP
        used_accounts: dict = {}
        async for je in db.journal_entries.find(
            {"copropriete_id": copropriete_id}, {"_id": 0, "id": 1, "lines": 1}
        ):
            for ln in je.get("lines") or []:
                acc = (ln.get("account_number") or "").strip()
                if not acc or len(acc) < 5:
                    continue
                # Owners (410x/4001x) et Suppliers (44xxx)
                if not (acc.startswith("41") or acc.startswith("4001")
                        or acc.startswith("4100") or acc.startswith("44")):
                    continue
                used_accounts.setdefault(acc, []).append({
                    "je_id": je["id"],
                    "tp_id": ln.get("third_party_id") or "",
                    "name": ln.get("account_name") or "",
                })

        # 4. Detecte les orphelins
        orphans = {a: v for a, v in used_accounts.items() if a not in valid_accounts}

        # 5. Determine le remap
        remaps: dict = {}
        remap_kind: dict = {}  # source_acc -> "owner" | "supplier"
        remap_tp: dict = {}    # source_acc -> tp_id (survivant)
        remap_tp_type: dict = {}

        # Suppliers a cloner (nom global -> supplier source a cloner vers ACP)
        suppliers_to_clone: dict = {}  # name_norm -> {"source": supplier_doc}

        def _try_match_supplier(orphan_acc, tp_id_candidates, name_candidates):
            """Match un compte 44xxxx a un supplier existant de l'ACP.
            Retourne (canonical_acc, supplier_id) ou (None, None).

            iter90il : si aucun supplier local ne matche, cherche en GLOBAL
            (autres ACPs) et MARQUE le supplier a cloner vers cette ACP.
            """
            # a) via tp_id sur suppliers locaux
            for tp_id in tp_id_candidates:
                target = next((s for s in suppliers_acp if s["id"] == tp_id), None)
                if target:
                    ta = (target.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                    canonical = ta.get("main", "")
                    if canonical and canonical != orphan_acc:
                        return canonical, target["id"]
            # b) via nom sur suppliers locaux
            for nm in name_candidates:
                cleaned = re.sub(r"^F\d{3,4}\s*-\s*", "", nm or "", flags=re.IGNORECASE).strip()
                nm_norm = _norm(cleaned)
                if not nm_norm:
                    continue
                target = name_to_supplier.get(nm_norm)
                if not target:
                    for supplier_nm, s in name_to_supplier.items():
                        if nm_norm in supplier_nm or supplier_nm in nm_norm:
                            target = s
                            break
                if target:
                    ta = (target.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                    canonical = ta.get("main", "")
                    if canonical and canonical != orphan_acc:
                        return canonical, target["id"]
            # c) via nom sur suppliers GLOBAUX (autres ACPs) - marque pour clonage
            for nm in name_candidates:
                cleaned = re.sub(r"^F\d{3,4}\s*-\s*", "", nm or "", flags=re.IGNORECASE).strip()
                nm_norm = _norm(cleaned)
                if not nm_norm:
                    continue
                global_match = name_to_supplier_global.get(nm_norm)
                if not global_match:
                    for supplier_nm, s in name_to_supplier_global.items():
                        if nm_norm in supplier_nm or supplier_nm in nm_norm:
                            global_match = s
                            break
                if global_match:
                    # Marque pour clonage vers l'ACP courante
                    suppliers_to_clone[nm_norm] = {
                        "source": global_match,
                        "orphan_accs": suppliers_to_clone.get(nm_norm, {}).get("orphan_accs", []) + [orphan_acc],
                    }
                    # Le vrai remap sera fait apres le clonage (voir plus bas)
                    return "__CLONE_PENDING__", global_match["id"]
            return None, None

        for orphan_acc in orphans:
            tp_ids = {r["tp_id"] for r in orphans[orphan_acc] if r["tp_id"]}
            names = {r["name"] for r in orphans[orphan_acc] if r["name"]}

            # OWNER (prefixe 410/4001/4100 uniquement)
            if orphan_acc.startswith(("410", "4001", "4100")) and not orphan_acc.startswith("44"):
                # a) via tp_id
                if len(tp_ids) == 1:
                    tp_id = list(tp_ids)[0]
                    target = next((o for o in owners if o["id"] == tp_id), None)
                    if target:
                        ta = (target.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                        is_reserve = orphan_acc.startswith(("4100", "40010", "4001"))
                        canonical = ta.get("reserve" if is_reserve else "provisions", "")
                        if canonical and canonical != orphan_acc:
                            remaps[orphan_acc] = canonical
                            remap_kind[orphan_acc] = "owner"
                            remap_tp[orphan_acc] = target["id"]
                            remap_tp_type[orphan_acc] = "owner"
                            continue
                # b) via suffix (4 derniers chars = aux_code suffix)
                suffix = orphan_acc[-4:]
                target = aux_to_owner.get(suffix)
                if target:
                    ta = (target.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                    is_reserve = orphan_acc.startswith(("4100", "40010", "4001"))
                    canonical = ta.get("reserve" if is_reserve else "provisions", "")
                    if canonical and canonical != orphan_acc:
                        remaps[orphan_acc] = canonical
                        remap_kind[orphan_acc] = "owner"
                        remap_tp[orphan_acc] = target["id"]
                        remap_tp_type[orphan_acc] = "owner"
                        continue

            # SUPPLIER (prefixe 44)
            elif orphan_acc.startswith("44"):
                canonical, sup_id = _try_match_supplier(orphan_acc, tp_ids, names)
                if canonical:
                    remaps[orphan_acc] = canonical
                    remap_kind[orphan_acc] = "supplier"
                    remap_tp[orphan_acc] = sup_id
                    remap_tp_type[orphan_acc] = "supplier"

        # 6. iter90il : Clone les suppliers globaux vers l'ACP (creation
        # locale + assign compte canonique). Effectue AVANT le remap.
        cloned_suppliers = []
        if not dry_run and suppliers_to_clone:
            from tier_accounts import assign_supplier_account as _assign_supplier_account
            import uuid
            for name_norm, meta in suppliers_to_clone.items():
                src = meta["source"]
                # Verifie qu'un supplier avec ce nom n'a pas ete cree entretemps
                clean_name = re.sub(r"^F\d{3,4}\s*-\s*", "", src.get("name",""), flags=re.IGNORECASE).strip()
                existing = await db.suppliers.find_one(
                    {"copropriete_id": copropriete_id, "name": clean_name},
                    {"_id": 0},
                )
                if existing:
                    target = existing
                else:
                    # Clone : nouveau doc avec meme BCE/VAT/IBAN, nouvel id, copro cible
                    new_id = str(uuid.uuid4())
                    doc = {
                        "id": new_id,
                        "name": clean_name,
                        "bce_number": src.get("bce_number", ""),
                        "vat_number": src.get("vat_number", ""),
                        "iban": src.get("iban", ""),
                        "address": src.get("address", ""),
                        "postal_code": src.get("postal_code", ""),
                        "city": src.get("city", ""),
                        "phone": src.get("phone", ""),
                        "email": src.get("email", ""),
                        "copropriete_id": copropriete_id,
                        "tier_accounts": {},
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "note": f"Clone automatique depuis ACP {src.get('copropriete_id','')[:8]} par heal-orphan-tier-accounts",
                    }
                    try:
                        await db.suppliers.insert_one(doc)
                    except Exception:
                        # DuplicateKeyError sur uq_supplier_copro_name -> recupere existant
                        existing2 = await db.suppliers.find_one(
                            {"copropriete_id": copropriete_id, "name": clean_name},
                            {"_id": 0},
                        )
                        if existing2:
                            target = existing2
                        else:
                            continue
                    else:
                        target = doc
                # Assign compte canonique via helper (44000XXX)
                target = await _assign_supplier_account(db, target, copropriete_id)
                canonical = ((target.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
                cloned_suppliers.append({
                    "name": clean_name,
                    "new_id": target["id"],
                    "canonical": canonical,
                    "orphan_accs": meta["orphan_accs"],
                })
                # Mise a jour du remap : "__CLONE_PENDING__" -> canonique
                for orphan in meta["orphan_accs"]:
                    remaps[orphan] = canonical
                    remap_tp[orphan] = target["id"]

        # Purge du placeholder de dry_run
        if dry_run:
            for orphan_acc, canonical in list(remaps.items()):
                if canonical == "__CLONE_PENDING__":
                    # En dry_run, remplace par un tag descriptif
                    remaps[orphan_acc] = "(sera cree)"

        # 7. Applique les remaps sur les journal_entries
        entries_touched = 0
        lines_remapped = 0
        pcmn_deleted = 0
        if not dry_run and remaps:
            # Filtre les remaps valides (exclus les placeholders)
            remaps_live = {k: v for k, v in remaps.items() if v and v != "__CLONE_PENDING__" and v != "(sera cree)"}
            async for je in db.journal_entries.find(
                {"copropriete_id": copropriete_id, "lines.account_number": {"$in": list(remaps_live.keys())}},
                {"_id": 0, "id": 1, "lines": 1},
            ):
                new_lines = []
                touched = False
                for ln in je.get("lines") or []:
                    acc = ln.get("account_number", "")
                    if acc in remaps_live:
                        new_line = {**ln, "account_number": remaps_live[acc]}
                        if not new_line.get("third_party_id") and acc in remap_tp:
                            new_line["third_party_id"] = remap_tp[acc]
                            new_line["third_party_type"] = remap_tp_type.get(acc, "supplier")
                        new_lines.append(new_line)
                        lines_remapped += 1
                        touched = True
                    else:
                        new_lines.append(ln)
                if touched:
                    entries_touched += 1
                    await db.journal_entries.update_one(
                        {"id": je["id"]}, {"$set": {"lines": new_lines}}
                    )
            # Supprime les comptes PCMN orphelins
            for orphan in remaps_live:
                still = await db.journal_entries.count_documents({
                    "copropriete_id": copropriete_id,
                    "lines.account_number": orphan,
                })
                if still == 0:
                    r = await db.pcmn_accounts.delete_one(
                        {"copropriete_id": copropriete_id, "number": orphan}
                    )
                    if r.deleted_count:
                        pcmn_deleted += 1

        unresolved = [
            {"account": a, "sample_names": list({r["name"] for r in orphans[a] if r["name"]})[:3]}
            for a in orphans if a not in remaps
        ]

        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id,
            "orphan_accounts_found": len(orphans),
            "remaps": remaps,
            "remap_kinds": remap_kind,
            "cloned_suppliers": cloned_suppliers,
            "unresolved_orphans": unresolved,
            "journal_entries_touched": entries_touched,
            "lines_remapped": lines_remapped,
            "pcmn_accounts_deleted": pcmn_deleted,
        }

    # iter90im : Nettoyage des fiches ORPHELINES sans transactions.
    # Regle metier user : "supprimer tous les orphelins actuels qui n'ont
    # pas de transactions liees".
    #
    # Definition orphelin :
    #  * Owner : aucun `journal_entries.lines.third_party_id == owner.id`,
    #    AUCUN `lots.owner_id == owner.id`, AUCUN `tenants.owner_id`.
    #  * Supplier : aucun `journal_entries.lines.third_party_id == supplier.id`,
    #    AUCUNE `invoices.supplier_id == supplier.id`.
    # Optionnel : `copropriete_id` pour scoper le nettoyage.
    # Ces owners/suppliers ont ete crees par erreur (import defectueux
    # avant iter90im) et n'ont aucun lien business - on peut les supprimer
    # sans risque.
    @router.post("/heal-remove-orphan-tiers-without-transactions")
    async def heal_remove_orphan_tiers_without_transactions(
        request: Request,
        copropriete_id: str = "",
        dry_run: bool = True,
    ):
        await _get_superadmin_only(request)

        # Owners : criteres OWNER SCOPE
        own_query = {}
        if copropriete_id:
            own_query["copropriete_ids"] = copropriete_id
        candidate_owners = await db.owners.find(own_query, {"_id": 0}).to_list(50000)
        orphan_owners = []
        for o in candidate_owners:
            oid = o["id"]
            # Verifie si des transactions existent
            has_je = await db.journal_entries.count_documents(
                {"lines.third_party_id": oid}
            )
            if has_je:
                continue
            has_lots = await db.lots.count_documents({"owner_id": oid})
            if has_lots:
                continue
            try:
                has_tenants = await db.tenants.count_documents({"owner_id": oid})
            except Exception:
                has_tenants = 0
            if has_tenants:
                continue
            orphan_owners.append({
                "id": oid,
                "name": o.get("name") or f"{o.get('last_name','')} {o.get('first_name','')}".strip(),
                "aux": o.get("auxiliary_code", ""),
                "copropriete_ids": o.get("copropriete_ids") or [],
                "created_at": o.get("created_at", ""),
            })

        # Suppliers
        sup_query = {}
        if copropriete_id:
            sup_query["copropriete_id"] = copropriete_id
        candidate_suppliers = await db.suppliers.find(sup_query, {"_id": 0}).to_list(50000)
        orphan_suppliers = []
        for s in candidate_suppliers:
            sid = s["id"]
            has_je = await db.journal_entries.count_documents(
                {"lines.third_party_id": sid}
            )
            if has_je:
                continue
            has_inv = await db.invoices.count_documents({"supplier_id": sid})
            if has_inv:
                continue
            orphan_suppliers.append({
                "id": sid,
                "name": s.get("name", ""),
                "bce": s.get("bce_number", ""),
                "vat": s.get("vat_number", ""),
                "copropriete_id": s.get("copropriete_id", ""),
                "created_at": s.get("created_at", ""),
            })

        deleted_owners = 0
        deleted_suppliers = 0
        if not dry_run:
            if orphan_owners:
                r1 = await db.owners.delete_many(
                    {"id": {"$in": [o["id"] for o in orphan_owners]}}
                )
                deleted_owners = r1.deleted_count
            if orphan_suppliers:
                r2 = await db.suppliers.delete_many(
                    {"id": {"$in": [s["id"] for s in orphan_suppliers]}}
                )
                deleted_suppliers = r2.deleted_count

        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id or "all",
            "orphan_owners_found": len(orphan_owners),
            "orphan_suppliers_found": len(orphan_suppliers),
            "deleted_owners": deleted_owners,
            "deleted_suppliers": deleted_suppliers,
            "orphan_owners_sample": orphan_owners[:50],
            "orphan_suppliers_sample": orphan_suppliers[:50],
        }

    # ------------------------------------------------------------------
    # iter90ir : Reparation ciblee - lier des fournisseurs a une ACP
    # via un mapping explicite {name -> account_number}.
    #
    # Cas d'usage : suite a un import legacy Optipro, des lignes de
    # journal entries d'une ACP (ex: Maria Auto 2) utilisent des comptes
    # tier 44000XXX qui ne sont rattaches a AUCUNE fiche fournisseur de
    # cette ACP -> ils apparaissent orphelins dans Quality Audit. Les
    # fiches existent bien mais dans D'AUTRES ACPs (ex: SRL Finlead sur
    # ACP Acacia). Cette route etend le rattachement de ces fiches vers
    # l'ACP cible via `tier_accounts` + repare les lignes JE sans tpid.
    # ------------------------------------------------------------------
    @router.post("/heal-link-suppliers-to-acp")
    async def heal_link_suppliers_to_acp(request: Request):
        """Body attendu :
            {
              "copropriete_id": "<acp_id>",
              "mapping": [
                {"name": "Engie", "account": "44000110"},
                {"name": "SRL Finlead", "account": "44000004"},
                ...
              ],
              "dry_run": true|false  (defaut true)
            }

        Idempotent : peut etre relance sans risque. Utilise
        `_norm_name_candidates` pour un matching robuste sur le nom.
        """
        await _get_superadmin_only(request)
        body = await request.json()
        copropriete_id = (body.get("copropriete_id") or "").strip()
        mapping = body.get("mapping") or []
        dry_run = bool(body.get("dry_run", True))
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis")
        if not mapping:
            raise HTTPException(400, "mapping (liste de {name, account}) requis")

        # Verifie que l'ACP existe.
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, f"ACP {copropriete_id} introuvable")

        from routes.suppliers import _norm_name_candidates
        from tier_accounts import canonize_supplier_tier_account

        report = {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id,
            "copropriete_name": copro.get("name", ""),
            "results": [],
            "totals": {
                "suppliers_matched": 0,
                "suppliers_not_found": 0,
                "tier_accounts_set": 0,
                "pcmn_accounts_created": 0,
                "je_lines_repaired": 0,
                "je_lines_already_ok": 0,
            },
        }

        for entry in mapping:
            supplier_name = (entry.get("name") or "").strip()
            account_raw = (entry.get("account") or "").strip()
            account = canonize_supplier_tier_account(account_raw)
            row = {
                "name": supplier_name,
                "account_source": account_raw,
                "account_canonical": account,
                "action": "",
                "supplier_id": "",
                "tier_updated": False,
                "pcmn_created": False,
                "je_lines_repaired": 0,
                "je_lines_already_ok": 0,
            }
            if not supplier_name or not account:
                row["action"] = "skipped_invalid_input"
                report["results"].append(row)
                continue

            # 1. Cherche le supplier en GLOBAL par nom (candidats normalises).
            name_cands = _norm_name_candidates(supplier_name)
            if not name_cands:
                row["action"] = "skipped_no_name_candidates"
                report["results"].append(row)
                continue
            supplier_doc = None
            async for s in db.suppliers.find({}, {"_id": 0}):
                other_cands = _norm_name_candidates(s.get("name", ""))
                if name_cands & other_cands:
                    supplier_doc = s
                    break
            if not supplier_doc:
                row["action"] = "supplier_not_found"
                report["totals"]["suppliers_not_found"] += 1
                report["results"].append(row)
                continue

            report["totals"]["suppliers_matched"] += 1
            row["supplier_id"] = supplier_doc["id"]
            row["supplier_name_actual"] = supplier_doc.get("name", "")

            # 2. Set tier_accounts.<copro_id>.main (idempotent).
            existing_ta = ((supplier_doc.get("tier_accounts") or {}).get(copropriete_id) or {})
            existing_main = existing_ta.get("main", "")
            if existing_main == account:
                row["tier_updated"] = False
                row["action"] = "already_linked"
            else:
                if not dry_run:
                    await db.suppliers.update_one(
                        {"id": supplier_doc["id"]},
                        {"$set": {f"tier_accounts.{copropriete_id}.main": account}},
                    )
                row["tier_updated"] = True
                report["totals"]["tier_accounts_set"] += 1
                row["action"] = "linked" if not existing_main else f"remapped_from_{existing_main}"

            # 3. Ensure PCMN account exists in this ACP.
            pcmn_exists = await db.pcmn_accounts.count_documents({
                "copropriete_id": copropriete_id,
                "number": account,
            }, limit=1)
            if not pcmn_exists:
                if not dry_run:
                    try:
                        await db.pcmn_accounts.insert_one({
                            "copropriete_id": copropriete_id,
                            "number": account,
                            "name": (supplier_doc.get("name") or supplier_name)[:60],
                            "class_num": 4,
                            "type": "balance",
                            "is_tier_account": True,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception:
                        pass  # index unique peut lever - deja cree en concurrent
                row["pcmn_created"] = True
                report["totals"]["pcmn_accounts_created"] += 1

            # 4. Repare les lignes JE de cette ACP qui utilisent ce compte
            #    mais sans third_party_id valide.
            supplier_id = supplier_doc["id"]
            # Compte les lignes deja OK (tpid deja = supplier_id).
            already_ok = await db.journal_entries.count_documents({
                "copropriete_id": copropriete_id,
                "lines": {"$elemMatch": {
                    "account_number": account,
                    "third_party_id": supplier_id,
                }},
            })
            row["je_lines_already_ok"] = already_ok
            report["totals"]["je_lines_already_ok"] += already_ok

            # Trouve les JE avec des lignes du bon compte MAIS sans le bon tpid.
            candidates = await db.journal_entries.find({
                "copropriete_id": copropriete_id,
                "lines": {"$elemMatch": {
                    "account_number": account,
                    "$or": [
                        {"third_party_id": {"$exists": False}},
                        {"third_party_id": ""},
                        {"third_party_id": None},
                        {"third_party_id": {"$ne": supplier_id}},
                    ],
                }},
            }, {"_id": 0, "id": 1, "lines": 1}).to_list(100000)
            lines_repaired = 0
            for je in candidates:
                new_lines = []
                changed = False
                for ln in je.get("lines", []) or []:
                    if ln.get("account_number") == account and ln.get("third_party_id") != supplier_id:
                        # Ne repare que si tpid absent/vide/orphelin (pas ecraser un vrai tpid).
                        current_tpid = ln.get("third_party_id") or ""
                        if not current_tpid or current_tpid == supplier_id:
                            new_ln = {**ln, "third_party_id": supplier_id, "third_party_type": "supplier"}
                            new_lines.append(new_ln)
                            lines_repaired += 1
                            changed = True
                        else:
                            # tpid pointe vers un autre supplier : verifie s'il existe
                            other = await db.suppliers.count_documents({"id": current_tpid}, limit=1)
                            if not other:
                                # tpid orphelin -> on remplace
                                new_ln = {**ln, "third_party_id": supplier_id, "third_party_type": "supplier"}
                                new_lines.append(new_ln)
                                lines_repaired += 1
                                changed = True
                            else:
                                new_lines.append(ln)  # tpid valide, on ne touche pas
                    else:
                        new_lines.append(ln)
                if changed and not dry_run:
                    await db.journal_entries.update_one(
                        {"id": je["id"]},
                        {"$set": {"lines": new_lines}},
                    )
            row["je_lines_repaired"] = lines_repaired
            report["totals"]["je_lines_repaired"] += lines_repaired
            report["results"].append(row)

        return report

    # ------------------------------------------------------------------
    # iter90it : Reconciliation des fournisseurs orphelins depuis les JE.
    #
    # Cas d'usage (Chinese Wall strict + import Optipro sans BCE) :
    # Apres un import Optipro d'AN, les journal_entries d'une ACP peuvent
    # contenir des lignes 44000XXX SANS third_party_id, parce que la fiche
    # fournisseur n'a jamais ete creee dans commit-suppliers-pdf (BCE
    # manquant bloquait iter90gk). Ce endpoint :
    # 1. Scanne les JE de l'ACP -> identifie les comptes 440XXX orphelins.
    # 2. Pour chaque compte unique, cree une fiche fournisseur LOCALE a
    #    l'ACP (nom = account_name, tier_account_number = account_number,
    #    BCE vide - a enrichir via KBO lookup plus tard).
    # 3. Repointe toutes les lignes JE de ce compte vers la nouvelle fiche.
    # ------------------------------------------------------------------
    @router.post("/reconcile-orphan-suppliers-from-je")
    async def reconcile_orphan_suppliers_from_je(request: Request):
        """Body : `{copropriete_id: "...", dry_run: bool}`.

        Superadmin only. Idempotent (peut etre relance apres chaque import).
        """
        await _get_superadmin_only(request)
        body = await request.json()
        copropriete_id = (body.get("copropriete_id") or "").strip()
        dry_run = bool(body.get("dry_run", True))
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis")
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, f"ACP {copropriete_id} introuvable")

        # 1. Charge tous les suppliers deja existants dans cette ACP,
        #    indexes par tier_account_number et par nom normalise.
        from routes.suppliers import _norm_name_candidates
        from tier_accounts import canonize_supplier_tier_account

        existing_suppliers = await db.suppliers.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0},
        ).to_list(5000)
        sup_by_tier: dict[str, dict] = {}
        sup_by_name_cand: dict[str, dict] = {}
        for s in existing_suppliers:
            num = (s.get("tier_account_number") or "").strip()
            if num:
                sup_by_tier[num] = s
            for cand in _norm_name_candidates(s.get("name", "")):
                sup_by_name_cand.setdefault(cand, s)

        # 2. Scanne les journal_entries -> map account_number -> {names, ids}.
        # On ne considere que les comptes 440XXX (fournisseurs) avec un solde
        # non nul et sans third_party_id valide.
        orphans: dict[str, dict] = {}  # tier_acc -> {names: set, line_refs: [(je_id, line_idx)]}
        async for je in db.journal_entries.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "id": 1, "lines": 1, "reversed": 1, "is_reversal": 1},
        ):
            if je.get("reversed") or je.get("is_reversal"):
                continue
            for lidx, ln in enumerate(je.get("lines", []) or []):
                acc = (ln.get("account_number") or "").strip()
                if not acc.startswith("440"):
                    continue
                # Skip si tpid deja pose et supplier valide dans cette ACP.
                tpid = (ln.get("third_party_id") or "").strip()
                if tpid:
                    # Verifie que le tpid pointe vers une fiche de CETTE ACP.
                    match = next((s for s in existing_suppliers if s["id"] == tpid), None)
                    if match:
                        continue
                acc_canonical = canonize_supplier_tier_account(acc)
                entry = orphans.setdefault(acc_canonical, {
                    "raw_account": acc,
                    "canonical": acc_canonical,
                    "names": {},  # name -> count
                    "line_refs": [],
                })
                nm = (ln.get("account_name") or ln.get("third_party_name") or "").strip()
                if nm:
                    entry["names"][nm] = entry["names"].get(nm, 0) + 1
                entry["line_refs"].append((je["id"], lidx))

        # 3. Pour chaque orphelin unique : cree la fiche (si absente) et
        #    repointe les lignes.
        report_items = []
        totals = {
            "orphan_accounts": len(orphans),
            "suppliers_created": 0,
            "suppliers_reused": 0,
            "je_lines_repointed": 0,
        }
        for acc, meta in orphans.items():
            # Nom le plus frequent parmi les lignes JE de ce compte.
            names_sorted = sorted(meta["names"].items(), key=lambda x: -x[1])
            best_name = names_sorted[0][0] if names_sorted else f"Fournisseur {acc}"
            item = {
                "tier_account": acc,
                "candidate_name": best_name,
                "occurrences": len(meta["line_refs"]),
                "action": "",
                "supplier_id": "",
                "lines_repointed": 0,
            }
            # iter90iv (change de strategie) : matching par NOM d'ABORD,
            # compte tier en FALLBACK. Cette strategie evite de creer de
            # nouvelles fiches quand un homonyme existe deja dans l'ACP
            # (ex: Engie deja cree avec 44000005, on ne cree PAS Engie-bis
            # avec 44000110 - on repointe la ligne JE vers Engie et on
            # reecrit le compte de la ligne avec 44000005).
            sup = None
            matched_by = ""
            for cand in _norm_name_candidates(best_name):
                if cand in sup_by_name_cand:
                    sup = sup_by_name_cand[cand]
                    matched_by = "name"
                    break
            if not sup:
                # Fallback : matching par compte tier (cas rare : ligne JE
                # avec compte tier + account_name absent/non-canonique)
                sup = sup_by_tier.get(acc)
                if sup:
                    matched_by = "account"
            if sup:
                # Fiche existante trouvee. iter90iv : la ligne JE sera
                # reecrite avec le tier_account_number CANONIQUE de la
                # fiche (voir plus bas), pas avec le compte source `acc`.
                cur_num = (sup.get("tier_account_number") or "").strip()
                if not cur_num:
                    # Fiche sans tier_account_number : on en attribue un
                    # (le compte source de la ligne orpheline, canonique).
                    if not dry_run:
                        await db.suppliers.update_one(
                            {"id": sup["id"]},
                            {"$set": {"tier_account_number": acc}},
                        )
                    sup["tier_account_number"] = acc
                item["action"] = "reused"
                item["supplier_id"] = sup["id"]
                item["matched_by"] = matched_by
                totals["suppliers_reused"] += 1
            else:
                # Creer une nouvelle fiche LOCALE a l'ACP.
                new_sup = {
                    "id": str(uuid.uuid4()),
                    "name": best_name[:100],
                    "copropriete_id": copropriete_id,
                    "tier_account_number": acc,
                    "bce_number": "",  # a enrichir via KBO plus tard
                    "vat_number": "",
                    "auto_created": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "auxiliary_code": "",
                    "notes": f"Auto-cree par reconcile-orphan-suppliers-from-je "
                             f"(compte tier {acc}, {len(meta['line_refs'])} lignes JE).",
                }
                if not dry_run:
                    try:
                        await db.suppliers.insert_one(new_sup.copy())
                    except Exception as e:
                        item["action"] = f"error_insert: {e}"
                        report_items.append(item)
                        continue
                    # Ensure PCMN account existe
                    pcmn_exists = await db.pcmn_accounts.count_documents({
                        "copropriete_id": copropriete_id, "number": acc,
                    }, limit=1)
                    if not pcmn_exists:
                        await db.pcmn_accounts.insert_one({
                            "copropriete_id": copropriete_id,
                            "number": acc,
                            "name": best_name[:60],
                            "class_num": 4,
                            "type": "balance",
                            "is_tier_account": True,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                sup = new_sup
                sup_by_tier[acc] = sup
                item["action"] = "created"
                item["supplier_id"] = sup["id"]
                totals["suppliers_created"] += 1

            # Repointer les lignes JE.
            lines_repointed = 0
            for je_id, lidx in meta["line_refs"]:
                if dry_run:
                    lines_repointed += 1
                    continue
                # iter90iu (point n°3) : $set precis sur l'index de la ligne.
                # ATTENTION : on met a jour AUSSI `account_number` avec le
                # compte tier CANONIQUE (8 chars) pour corriger les JE legacy
                # qui contenaient un compte 7 chars ("4400015"). Sans cette
                # correction, le Bilan continuait d'afficher deux lignes
                # (7 chars vs 8 chars) pour le meme fournisseur.
                sup_canonical_acc = (sup.get("tier_account_number") or acc).strip()
                res = await db.journal_entries.update_one(
                    {"id": je_id},
                    {"$set": {
                        f"lines.{lidx}.third_party_id": sup["id"],
                        f"lines.{lidx}.third_party_type": "supplier",
                        f"lines.{lidx}.account_number": sup_canonical_acc,
                    }},
                )
                if res.modified_count:
                    lines_repointed += 1
            item["lines_repointed"] = lines_repointed
            totals["je_lines_repointed"] += lines_repointed
            report_items.append(item)

        return {
            "mode": "dry_run" if dry_run else "live",
            "copropriete_id": copropriete_id,
            "copropriete_name": copro.get("name", ""),
            "totals": totals,
            "results": report_items,
        }

    # ---- PURGE COMPLETE DES DONNEES D'UN SYNDIC ----
    @router.delete("/syndic/{user_id}/purge-data")
    async def purge_syndic_data(user_id: str, request: Request):
        """Supprime TOUTES les donnees de TOUTES les ACPs liees a un syndic.

        Requiert :
          - header/cookie superadmin
          - body JSON : {"confirm_email": "<email du syndic>"}

        Collections purgees par ACP : lots, owners, suppliers, invoices,
        fund_calls, mutations, journal_entries, bank_statements,
        bank_transactions, pcmn_accounts, expense_categories,
        distribution_keys, fiscal_years, coproprietes.
        """
        await _get_superadmin_only(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        confirm_email = (body.get("confirm_email") or "").strip().lower()

        try:
            target = await db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            raise HTTPException(404, "Utilisateur non trouve")
        if not target:
            raise HTTPException(404, "Utilisateur non trouve")
        if target.get("email", "").lower() != confirm_email:
            raise HTTPException(
                400,
                f"Confirmation incorrecte. Retapez l'email du syndic ({target.get('email')}) pour confirmer la purge."
            )

        copro_ids = target.get("copropriete_ids") or []
        if not copro_ids:
            return {"message": "Ce syndic n'a aucune ACP. Rien a purger.", "deleted": {}}

        deleted_counts = {}

        # --- 1. Collecter TOUS les owner_ids lies aux ACPs ---

        # 1a. Via lots.owner_id et lots.owner_ids
        owner_ids_from_lots = set()
        for cid in copro_ids:
            ids1 = await db.lots.distinct("owner_id", {"copropriete_id": cid})
            ids2 = await db.lots.distinct("owner_ids", {"copropriete_id": cid})
            owner_ids_from_lots.update(i for i in ids1 if i)
            owner_ids_from_lots.update(i for i in ids2 if i)

        # 1b. Via copropriete_ids (array) sur la fiche owner
        owner_ids_from_field = set(await db.owners.distinct(
            "id", {"copropriete_ids": {"$in": copro_ids}}
        ))
        # 1c. Via copropriete_id (singulier) sur la fiche owner
        owner_ids_from_singular = set(await db.owners.distinct(
            "id", {"copropriete_id": {"$in": copro_ids}}
        ))
        # 1d. Via import_session_id (owners importes sans copropriete_id direct)
        session_ids = await db.import_sessions.distinct(
            "id", {"copropriete_id": {"$in": copro_ids}}
        )
        owner_ids_from_sessions = set()
        if session_ids:
            owner_ids_from_sessions = set(await db.owners.distinct(
                "id", {"import_session_id": {"$in": session_ids}}
            ))

        all_owner_ids = (
            owner_ids_from_lots | owner_ids_from_field
            | owner_ids_from_singular | owner_ids_from_sessions
        )

        # --- 2. Supprimer les owners (union de tous les criteres) ---
        if all_owner_ids:
            r = await db.owners.delete_many({"id": {"$in": list(all_owner_ids)}})
            deleted_counts["owners"] = r.deleted_count
            # Supprimer les comptes bancaires des owners
            r2 = await db.owner_bank_accounts.delete_many(
                {"owner_id": {"$in": list(all_owner_ids)}}
            )
            deleted_counts["owner_bank_accounts"] = r2.deleted_count
            # Supprimer les audits d'acces proprietaire
            r3 = await db.owner_access_audit.delete_many(
                {"owner_id": {"$in": list(all_owner_ids)}}
            )
            deleted_counts["owner_access_audit"] = r3.deleted_count
        else:
            deleted_counts["owners"] = 0
            deleted_counts["owner_bank_accounts"] = 0
            deleted_counts["owner_access_audit"] = 0

        # --- 3. Collections standard avec copropriete_id ---
        COPRO_COLLECTIONS = [
            "lots", "suppliers", "invoices", "fund_calls",
            "mutations", "journal_entries", "bank_statements",
            "bank_transactions", "pcmn_accounts", "expense_categories",
            "distribution_keys", "fiscal_years", "tier_accounts",
            "deleted_entries", "import_sessions", "documents",
            "document_categories",
        ]
        for coll_name in COPRO_COLLECTIONS:
            coll = db[coll_name]
            result = await coll.delete_many({"copropriete_id": {"$in": copro_ids}})
            deleted_counts[coll_name] = result.deleted_count

        # --- 4. Supprimer les coproprietes elles-memes ---
        result = await db.coproprietes.delete_many({"id": {"$in": copro_ids}})
        deleted_counts["coproprietes"] = result.deleted_count

        # --- 5. Vider la liste copropriete_ids du syndic ---
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"copropriete_ids": []}},
        )

        # --- 6. Purger les comptes utilisateur lies ---
        # 6a. Gestionnaires rattaches via syndic_user_id
        team_result = await db.users.delete_many({
            "syndic_user_id": user_id,
            "role": {"$in": ["gestionnaire", "owner"]},
        })
        deleted_counts["team_users"] = team_result.deleted_count

        # 6b. Comptes owner (role=owner) lies aux ACPs purgees
        #     Ces users n'ont PAS de syndic_user_id mais ont copropriete_ids
        owner_users_result = await db.users.delete_many({
            "role": "owner",
            "copropriete_ids": {"$in": copro_ids},
        })
        deleted_counts["owner_user_accounts"] = owner_users_result.deleted_count

        total = sum(deleted_counts.values())
        return {
            "message": f"Purge terminee : {total} documents supprimes pour {len(copro_ids)} ACP(s) du syndic {target.get('name')}",
            "syndic_email": target.get("email"),
            "copropriete_ids_purged": copro_ids,
            "deleted": deleted_counts,
        }

    @router.post("/migrate/normalize-bank-pcmn")
    async def migrate_normalize_bank_pcmn(request: Request):
        """Normalise TOUS les numeros PCMN bancaires (classe 55) a 8 chiffres.

        3 cibles :
          1. coproprietes.bank_accounts[].pcmn_number
          2. pcmn_accounts.number (comptes 55xxxx -> 55xxxx00)
          3. journal_entries.lines[].account_number (55xxxx -> 55xxxx00)

        Idempotent : les numeros deja a 8 chiffres sont ignores.
        """
        await _get_superadmin_only(request)
        from pcmn_utils import normalize_bank_pcmn

        stats = {"copros_updated": 0, "pcmn_updated": 0, "je_lines_updated": 0}

        # 1. coproprietes.bank_accounts[].pcmn_number
        copros = await db.coproprietes.find(
            {"bank_accounts": {"$exists": True, "$ne": []}},
            {"_id": 0, "id": 1, "bank_accounts": 1},
        ).to_list(1000)
        for copro in copros:
            changed = False
            for ba in (copro.get("bank_accounts") or []):
                old = (ba.get("pcmn_number") or "").strip()
                if old and old.startswith("55"):
                    new = normalize_bank_pcmn(old)
                    if new != old:
                        ba["pcmn_number"] = new
                        changed = True
            if changed:
                await db.coproprietes.update_one(
                    {"id": copro["id"]},
                    {"$set": {"bank_accounts": copro["bank_accounts"]}},
                )
                stats["copros_updated"] += 1

        # 2. pcmn_accounts.number (classe 55, 6-7 chiffres -> 8)
        pcmn_55 = await db.pcmn_accounts.find(
            {"number": {"$regex": "^55\\d{3,4}$"}},
            {"_id": 1, "number": 1, "copropriete_id": 1},
        ).to_list(10000)
        for p in pcmn_55:
            old = p["number"]
            new = normalize_bank_pcmn(old)
            if new != old:
                existing = await db.pcmn_accounts.find_one({
                    "number": new, "copropriete_id": p.get("copropriete_id"),
                })
                if not existing:
                    await db.pcmn_accounts.update_one(
                        {"_id": p["_id"]}, {"$set": {"number": new}}
                    )
                    stats["pcmn_updated"] += 1

        # 3. journal_entries.lines[].account_number (55xxxx -> 55xxxx00)
        je_cursor = db.journal_entries.find(
            {"lines.account_number": {"$regex": "^55\\d{3,4}$"}},
            {"_id": 1, "lines": 1},
        )
        async for je in je_cursor:
            changed = False
            for ln in (je.get("lines") or []):
                old_acc = (ln.get("account_number") or "").strip()
                if old_acc and old_acc.startswith("55") and 6 <= len(old_acc) <= 7:
                    ln["account_number"] = normalize_bank_pcmn(old_acc)
                    changed = True
                    stats["je_lines_updated"] += 1
            if changed:
                await db.journal_entries.update_one(
                    {"_id": je["_id"]}, {"$set": {"lines": je["lines"]}}
                )

        return {"status": "ok", **stats}

    # ---- iter91e : Dedupe / Cleanup des Natures de depense (expense_categories) ----
    class ExpenseCatDedupeInput(BaseModel):
        copropriete_id: str
        dry_run: bool = True
        # Strategie de detection : "name" | "name_account" | "name_normalized"
        strategy: str = "name_normalized"
        # Nettoyage des libelles malformes (ex: "7362)", noms < 3 chars, tout-chiffres)
        cleanup_malformed: bool = True
        # Si dry_run=False, exiger la liste des IDs a fusionner explicitement
        # (source_ids -> target_id) pour eviter les fusions accidentelles
        merges: Optional[list] = None  # [{"target_id": "...", "source_ids": ["...", "..."]}]

    def _normalize_cat_name(name: str) -> str:
        """iter91e : normalise un nom de nature pour detection tolerante :
        - lowercase
        - retire accents
        - retire ponctuation non alphanumerique
        - reduit espaces multiples
        """
        import re as _re
        import unicodedata as _ud
        s = (name or "").strip().lower()
        s = "".join(c for c in _ud.normalize("NFD", s) if _ud.category(c) != "Mn")
        s = _re.sub(r"[^a-z0-9\s]", " ", s)
        s = _re.sub(r"\s+", " ", s).strip()
        return s

    def _is_malformed_name(name: str) -> bool:
        """Detecte les libelles malformes."""
        import re as _re
        n = (name or "").strip()
        if not n or len(n) < 3:
            return True
        # Commence par chiffres + ')' (ex: "7362)")
        if _re.match(r"^\d+\s*\)", n):
            return True
        # Uniquement chiffres/espaces/tirets/points (ex: "61010" ou "61-01")
        if _re.match(r"^[\d\s\-\.]+$", n):
            return True
        return False

    @router.post("/expense-categories/dedupe")
    async def dedupe_expense_categories(data: ExpenseCatDedupeInput, request: Request):
        """iter91e : outil admin pour detecter et fusionner les Natures de
        depense dupliquees (ex. accents, casse, malformes) sur une ACP.

        Mode `dry_run=True` (defaut) : retourne un rapport JSON sans modifier
        la base. Mode `dry_run=False` avec `merges` explicites : execute les
        fusions demandees (chaque source -> target, refs mises a jour dans
        journal_entries.lines.expense_category_id).

        Chinese wall : superadmin ou admin uniquement (endpoint sous /api/admin).
        """
        from collections import defaultdict
        user = await _get_admin_user(request)
        role = user.get("role", "")

        cid = (data.copropriete_id or "").strip()
        if not cid:
            raise HTTPException(400, "copropriete_id requis")
        # Chinese wall syndic : verifier l'acces a l'ACP
        if role not in ("superadmin", "admin"):
            user_copros = getattr(request.state, "user_copropriete_ids", []) or []
            if cid not in user_copros:
                raise HTTPException(403, "Acces refuse a cette copropriete")

        cats = await db.expense_categories.find(
            {"copropriete_id": cid}, {"_id": 0}
        ).to_list(5000)

        # ---- Detection doublons ----
        groups = defaultdict(list)
        for c in cats:
            if data.strategy == "name_account":
                key = ((c.get("name") or "").strip().lower(), c.get("account_number", ""))
            elif data.strategy == "name_normalized":
                key = _normalize_cat_name(c.get("name") or "")
            else:  # "name"
                key = (c.get("name") or "").strip().lower()
            groups[key].append(c)

        duplicate_groups = []
        for key, grp in groups.items():
            if len(grp) < 2:
                continue
            # Le "target" par defaut = le plus utilise (via journal_entries lines),
            # sinon le plus ancien (created_at).
            ids = [c["id"] for c in grp]
            usages = {}
            for cid_ in ids:
                usages[cid_] = await db.journal_entries.count_documents(
                    {"copropriete_id": cid, "lines.expense_category_id": cid_}
                )
            target = max(grp, key=lambda c: (usages.get(c["id"], 0), c.get("created_at", "")))
            duplicate_groups.append({
                "key": str(key),
                "target": {
                    "id": target["id"],
                    "name": target.get("name"),
                    "account_number": target.get("account_number"),
                    "usages": usages.get(target["id"], 0),
                },
                "sources": [
                    {
                        "id": c["id"], "name": c.get("name"),
                        "account_number": c.get("account_number"),
                        "usages": usages.get(c["id"], 0),
                        "malformed": _is_malformed_name(c.get("name") or ""),
                    }
                    for c in grp if c["id"] != target["id"]
                ],
            })

        # ---- Detection malformes ----
        malformed = []
        if data.cleanup_malformed:
            for c in cats:
                if _is_malformed_name(c.get("name") or ""):
                    usages = await db.journal_entries.count_documents(
                        {"copropriete_id": cid, "lines.expense_category_id": c["id"]}
                    )
                    malformed.append({
                        "id": c["id"], "name": c.get("name"),
                        "account_number": c.get("account_number"),
                        "usages": usages,
                    })

        report = {
            "copropriete_id": cid,
            "total_categories": len(cats),
            "duplicate_groups": duplicate_groups,
            "malformed": malformed,
            "dry_run": data.dry_run,
        }

        # ---- Execution (si dry_run=False) ----
        if data.dry_run:
            return report

        if not data.merges:
            raise HTTPException(400, "Mode execution : `merges` requis (liste des fusions)")

        merges_done = 0
        entries_updated = 0
        cats_deleted = 0
        errors = []
        for m in data.merges:
            target_id = (m.get("target_id") or "").strip()
            source_ids = m.get("source_ids") or []
            if not target_id or not source_ids:
                errors.append({"merge": m, "error": "target_id / source_ids requis"})
                continue
            # Verifier que target existe dans cette ACP
            target = await db.expense_categories.find_one(
                {"id": target_id, "copropriete_id": cid}, {"_id": 0}
            )
            if not target:
                errors.append({"merge": m, "error": f"Target {target_id} introuvable dans l'ACP"})
                continue
            # Ne pas laisser fusionner target vers lui-meme
            source_ids = [s for s in source_ids if s and s != target_id]
            if not source_ids:
                continue
            # Reassigner les references dans journal_entries.lines.expense_category_id
            for sid in source_ids:
                res = await db.journal_entries.update_many(
                    {"copropriete_id": cid, "lines.expense_category_id": sid},
                    {"$set": {"lines.$[elt].expense_category_id": target_id}},
                    array_filters=[{"elt.expense_category_id": sid}],
                )
                entries_updated += res.modified_count
            # Supprimer les sources
            res_del = await db.expense_categories.delete_many(
                {"id": {"$in": source_ids}, "copropriete_id": cid}
            )
            cats_deleted += res_del.deleted_count
            merges_done += 1

        return {
            **report,
            "executed": True,
            "merges_done": merges_done,
            "entries_updated": entries_updated,
            "categories_deleted": cats_deleted,
            "errors": errors,
        }


    return router
