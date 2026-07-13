from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone
import uuid


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
        # ENFORCE : le superadmin ne cree QUE des syndics. Les gestionnaires
        # sont crees par leur syndic dans /api/team/members. Les owners par
        # le syndic via /api/owners (ou inscription).
        if data.role not in ("syndic",):
            raise HTTPException(
                400,
                "Le superadmin ne cree que des comptes syndic. "
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
        # Pas de role_template ni permissions sur un syndic (acces total a son perimetre).
        # Pas d'ACPs assignees ici : le syndic les creera lui-meme.
        doc = {
            "email": email,
            "password_hash": pwd_hash,
            "name": data.name,
            "role": "syndic",
            "copropriete_ids": [],
            "must_change_password": must_change,
            "role_template_id": None,
            "permissions": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": str(request.state.user.get("_id", "")) if hasattr(request, "state") and hasattr(request.state, "user") else None,
        }
        result = await db.users.insert_one(doc)
        # Envoi de l'invitation par email via Microsoft Graph (background, ne bloque pas)
        try:
            from graph_email import is_configured, send_html_email, build_invitation_email
            import os, asyncio
            if must_change and is_configured():
                frontend_url = os.environ.get("FRONTEND_URL", "")
                setup_url = f"{frontend_url}/login?invite={email}"
                inviter = request.state.user if hasattr(request, "state") and hasattr(request.state, "user") else None
                subject, html = build_invitation_email(
                    recipient_name=data.name,
                    role_label="Syndic",
                    setup_url=setup_url,
                    inviter_name=(inviter or {}).get("name"),
                    inviter_email=(inviter or {}).get("email"),
                )
                asyncio.create_task(send_html_email([email], subject, html))
        except Exception as e:
            import logging
            logging.warning(f"Envoi invitation echoue pour {email}: {e}")
        return {
            "id": str(result.inserted_id),
            "email": email,
            "name": data.name,
            "role": doc["role"],
            "copropriete_ids": doc["copropriete_ids"],
            "must_change_password": must_change,
            "role_template_id": None,
            "permissions": None,
            "invitation_sent": must_change,
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
        if data.role is not None and data.role != target.get("role"):
            raise HTTPException(
                400,
                "Le role d'un utilisateur ne se change pas depuis ici. "
                "Le superadmin gere uniquement les comptes syndic principaux."
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
        existing = await db.role_templates.find_one(
            {"name": {"$regex": f"^{name}$", "$options": "i"}}
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
        return new_tpl

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

    return router
