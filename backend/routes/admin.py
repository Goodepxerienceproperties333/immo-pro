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
        # Envoi de l'invitation par email via Microsoft Graph (background, ne bloque pas)
        try:
            from graph_email import is_configured, send_html_email, build_invitation_email
            import os, asyncio
            if must_change and is_configured():
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

    return router
