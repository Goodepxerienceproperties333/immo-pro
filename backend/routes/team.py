"""Gestion d'equipe pour les syndics.

Chaque syndic peut creer, modifier et suspendre ses propres gestionnaires.
Les gestionnaires sont automatiquement rattaches au syndic createur via
`parent_syndic_id`. Le syndic ne voit QUE ses propres gestionnaires.

Le superadmin (gestionnaire de plateforme) ne passe PAS par cette interface :
il gere les syndics eux-memes via /api/admin/users.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId


class TeamMemberInput(BaseModel):
    email: str
    password: Optional[str] = None
    name: str
    copropriete_ids: Optional[List[str]] = []
    role_template_id: Optional[str] = None
    permissions: Optional[List[str]] = None
    must_change_password: Optional[bool] = True


class TeamMemberUpdate(BaseModel):
    name: Optional[str] = None
    copropriete_ids: Optional[List[str]] = None
    password: Optional[str] = None
    role_template_id: Optional[str] = None
    permissions: Optional[List[str]] = None
    must_change_password: Optional[bool] = None


def create_team_router(db):
    router = APIRouter(prefix="/api/team")

    async def _get_syndic(request):
        """Le membre courant doit etre syndic OU superadmin. Si gestionnaire :
        on remonte au syndic parent. Retourne (current_user, effective_syndic_id)."""
        from server import get_current_user
        user = await get_current_user(request)
        role = user.get("role", "")
        if role in ("superadmin", "admin"):
            # Le superadmin peut acceder a TOUTES les equipes (vue plateforme)
            return user, None  # None = pas de scope syndic
        if role == "syndic":
            return user, user.get("id")
        if role == "gestionnaire":
            parent = user.get("parent_syndic_id")
            if not parent:
                raise HTTPException(403, "Compte gestionnaire orphelin (pas de syndic parent)")
            return user, parent
        raise HTTPException(403, "Acces reserve aux syndics et leur equipe")

    async def _validate_acps_belong_to_syndic(syndic_id: str, copro_ids: List[str]):
        """Verifie que les ACPs demandees sont bien dans le perimetre du syndic.
        Le syndic ne peut attribuer a son gestionnaire que des ACPs qu'il gere."""
        if not syndic_id:
            return  # superadmin : pas de scope
        syndic = await db.users.find_one({"_id": ObjectId(syndic_id)})
        if not syndic:
            raise HTTPException(403, "Syndic introuvable")
        syndic_acps = set(syndic.get("copropriete_ids") or [])
        invalid = [c for c in (copro_ids or []) if c not in syndic_acps]
        if invalid:
            raise HTTPException(
                400,
                f"Vous ne pouvez attribuer que les ACPs que vous gerez. "
                f"Inconnu(es) : {', '.join(invalid)}"
            )

    @router.get("/members")
    async def list_members(request: Request):
        """Liste les membres de l'equipe du syndic courant (gestionnaires uniquement).
        Si superadmin : liste TOUS les gestionnaires de la plateforme."""
        user, scope = await _get_syndic(request)
        q = {"role": "gestionnaire"}
        if scope:
            q["parent_syndic_id"] = scope
        members = await db.users.find(q).sort("name", 1).to_list(500)
        return [{
            "id": str(m["_id"]),
            "email": m["email"],
            "name": m["name"],
            "role": "gestionnaire",
            "copropriete_ids": m.get("copropriete_ids", []),
            "role_template_id": m.get("role_template_id"),
            "permissions": m.get("permissions"),
            "parent_syndic_id": m.get("parent_syndic_id"),
            "must_change_password": m.get("must_change_password", False),
            "created_at": m.get("created_at", ""),
        } for m in members]

    @router.post("/members")
    async def create_member(data: TeamMemberInput, request: Request):
        """Cree un gestionnaire rattache au syndic courant."""
        from server import hash_password
        user, scope = await _get_syndic(request)
        if not scope:
            raise HTTPException(
                400,
                "Le superadmin doit creer les gestionnaires via le syndic concerne. "
                "Connectez-vous en tant que ce syndic ou utilisez /api/admin/users."
            )
        email = data.email.lower().strip()
        if not email:
            raise HTTPException(400, "Email requis")
        # Unicite
        existing = await db.users.find_one({"email": email})
        if existing:
            raise HTTPException(400, f"L'email {email} existe deja")
        # Verifie que les ACPs sont dans le perimetre du syndic
        await _validate_acps_belong_to_syndic(scope, data.copropriete_ids or [])
        # Permissions
        perms = None
        if data.role_template_id:
            tpl = await db.role_templates.find_one({"id": data.role_template_id})
            if not tpl:
                raise HTTPException(400, "Profil (role_template_id) introuvable")
            perms = list(tpl.get("permissions") or [])
        if data.permissions is not None:
            perms = list(data.permissions)
        pwd_hash = hash_password(data.password or str(uuid.uuid4())[:8])
        doc = {
            "email": email,
            "password_hash": pwd_hash,
            "name": data.name,
            "role": "gestionnaire",
            "parent_syndic_id": scope,
            "copropriete_ids": data.copropriete_ids or [],
            "role_template_id": data.role_template_id,
            "permissions": perms,
            "must_change_password": data.must_change_password if data.password else True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": user.get("id"),
        }
        result = await db.users.insert_one(doc)
        return {"id": str(result.inserted_id), "email": email, "name": data.name,
                "role": "gestionnaire", "parent_syndic_id": scope,
                "copropriete_ids": doc["copropriete_ids"],
                "role_template_id": doc["role_template_id"],
                "permissions": doc["permissions"]}

    @router.put("/members/{member_id}")
    async def update_member(member_id: str, data: TeamMemberUpdate, request: Request):
        """Modifie un gestionnaire de l'equipe du syndic courant."""
        from server import hash_password
        user, scope = await _get_syndic(request)
        try:
            obj_id = ObjectId(member_id)
        except Exception:
            raise HTTPException(400, "ID invalide")
        m = await db.users.find_one({"_id": obj_id})
        if not m:
            raise HTTPException(404, "Membre introuvable")
        if m.get("role") != "gestionnaire":
            raise HTTPException(400, "Cet endpoint ne gere que les gestionnaires")
        # Verifie l'ownership
        if scope and m.get("parent_syndic_id") != scope:
            raise HTTPException(403, "Ce gestionnaire n'appartient pas a votre equipe")
        update = {}
        if data.name is not None:
            update["name"] = data.name
        if data.copropriete_ids is not None:
            await _validate_acps_belong_to_syndic(scope, data.copropriete_ids)
            update["copropriete_ids"] = data.copropriete_ids
        if data.password:
            update["password_hash"] = hash_password(data.password)
            update["must_change_password"] = data.must_change_password if data.must_change_password is not None else False
        elif data.must_change_password is not None:
            update["must_change_password"] = data.must_change_password
        if data.role_template_id is not None:
            update["role_template_id"] = data.role_template_id or None
            if data.permissions is None and data.role_template_id:
                tpl = await db.role_templates.find_one({"id": data.role_template_id})
                if tpl:
                    update["permissions"] = list(tpl.get("permissions") or [])
        if data.permissions is not None:
            update["permissions"] = list(data.permissions)
        if update:
            update["updated_at"] = datetime.now(timezone.utc).isoformat()
            await db.users.update_one({"_id": obj_id}, {"$set": update})
        u = await db.users.find_one({"_id": obj_id})
        return {"id": str(u["_id"]), "email": u["email"], "name": u["name"],
                "role": u.get("role"), "copropriete_ids": u.get("copropriete_ids", []),
                "role_template_id": u.get("role_template_id"),
                "permissions": u.get("permissions"),
                "parent_syndic_id": u.get("parent_syndic_id")}

    @router.delete("/members/{member_id}")
    async def delete_member(member_id: str, request: Request):
        user, scope = await _get_syndic(request)
        try:
            obj_id = ObjectId(member_id)
        except Exception:
            raise HTTPException(400, "ID invalide")
        m = await db.users.find_one({"_id": obj_id})
        if not m:
            raise HTTPException(404, "Membre introuvable")
        if m.get("role") != "gestionnaire":
            raise HTTPException(400, "Cet endpoint ne supprime que des gestionnaires")
        if scope and m.get("parent_syndic_id") != scope:
            raise HTTPException(403, "Ce gestionnaire n'appartient pas a votre equipe")
        await db.users.delete_one({"_id": obj_id})
        return {"status": "ok"}

    return router
