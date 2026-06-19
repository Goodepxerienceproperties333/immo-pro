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
    role: str  # superadmin, syndic, owner
    copropriete_ids: Optional[List[str]] = []
    must_change_password: Optional[bool] = False


class UserUpdateInput(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    copropriete_ids: Optional[List[str]] = None
    password: Optional[str] = None
    must_change_password: Optional[bool] = None


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
                "created_at": u.get("created_at", ""),
            })
        return result

    @router.post("/users")
    async def create_user(data: UserCreateInput, request: Request):
        # Seul le superadmin peut creer des utilisateurs (= gerer les acces a la plateforme)
        await _get_superadmin_only(request)
        from server import hash_password
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
        doc = {
            "email": email,
            "password_hash": pwd_hash,
            "name": data.name,
            "role": data.role if data.role in ("superadmin", "syndic", "gestionnaire", "owner") else "owner",
            "copropriete_ids": data.copropriete_ids or [],
            "must_change_password": must_change,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.users.insert_one(doc)
        return {
            "id": str(result.inserted_id),
            "email": email,
            "name": data.name,
            "role": doc["role"],
            "copropriete_ids": doc["copropriete_ids"],
            "must_change_password": must_change,
        }

    @router.put("/users/{user_id}")
    async def update_user(user_id: str, data: UserUpdateInput, request: Request):
        await _get_superadmin_only(request)
        from server import hash_password
        target = await db.users.find_one({"_id": ObjectId(user_id)})
        if not target:
            raise HTTPException(404, "Utilisateur non trouve")
        update = {}
        if data.name is not None:
            update["name"] = data.name
        if data.role is not None:
            update["role"] = data.role
        if data.copropriete_ids is not None:
            update["copropriete_ids"] = data.copropriete_ids
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
            await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
        updated = await db.users.find_one({"_id": ObjectId(user_id)})
        return {
            "id": str(updated["_id"]),
            "email": updated["email"],
            "name": updated["name"],
            "role": updated.get("role", "owner"),
            "copropriete_ids": updated.get("copropriete_ids", []),
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

    return router
