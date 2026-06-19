from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import uuid
from tier_accounts import assign_supplier_account


class SupplierInput(BaseModel):
    name: str
    vat_number: Optional[str] = ""
    bce_number: Optional[str] = ""
    address: Optional[str] = ""
    postal_code: Optional[str] = ""
    city: Optional[str] = ""
    country: Optional[str] = "Belgique"
    phone: Optional[str] = ""
    email: Optional[str] = ""
    iban: Optional[str] = ""
    bic: Optional[str] = ""
    default_account: Optional[str] = ""
    notes: Optional[str] = ""
    copropriete_id: Optional[str] = ""


def create_suppliers_router(db):
    router = APIRouter(prefix="/api/suppliers")

    async def _get_user_scope(request: Request):
        """Retourne (is_super_global, allowed_copro_ids).
        - is_super=True pour superadmin/admin (acces total)
        - Sinon allowed_copro_ids = list of copropriete_ids du user (peut etre [])
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if is_superadmin_only(role):
            return True, None
        return False, user.get("copropriete_ids", []) or []

    def _supplier_in_scope(supplier_doc: dict, allowed_copros) -> bool:
        """True si le fournisseur est rattache a au moins une ACP du scope."""
        if allowed_copros is None:
            return True
        if not allowed_copros:
            return False
        allowed_set = set(allowed_copros)
        # 1) copropriete_id direct (champ original lors de la creation)
        if supplier_doc.get("copropriete_id") and supplier_doc["copropriete_id"] in allowed_set:
            return True
        # 2) tier_accounts : chaque cle = un copropriete_id ou ce fournisseur a un compte 44000XXX
        ta = supplier_doc.get("tier_accounts") or {}
        for cid in ta.keys():
            if cid in allowed_set:
                return True
        return False

    @router.get("")
    async def list_suppliers(request: Request, search: Optional[str] = None, copropriete_id: Optional[str] = None):
        """Liste des fournisseurs - chinese wall STRICT (RGPD).
        - Superadmin : voit tout
        - Syndic / gestionnaire : ne voit QUE les fournisseurs rattaches a une de ses ACPs
          (via copropriete_id direct OU via tier_accounts.<copro_id>).
        - Si copropriete_id fourni : restreint a cette ACP (verifie deja par middleware).
        """
        is_super, allowed_copros = await _get_user_scope(request)
        q = {}
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"vat_number": {"$regex": search, "$options": "i"}},
                {"bce_number": {"$regex": search, "$options": "i"}},
            ]
        all_suppliers = await db.suppliers.find(q, {"_id": 0}).sort("name", 1).to_list(2000)
        # Cas ACP specifique demandee
        if copropriete_id:
            allowed_set = {copropriete_id}
            return [s for s in all_suppliers if (
                s.get("copropriete_id") in allowed_set
                or any(cid in allowed_set for cid in (s.get("tier_accounts") or {}).keys())
            )]
        if is_super:
            return all_suppliers
        return [s for s in all_suppliers if _supplier_in_scope(s, allowed_copros)]

    @router.post("")
    async def create_supplier(request: Request, data: SupplierInput):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or ""
        # Pour un syndic : un fournisseur DOIT etre rattache a une de ses ACPs (RGPD)
        if not is_super:
            if not copro_id:
                raise HTTPException(400, "Un fournisseur doit etre rattache a une copropriete (chinese wall + RGPD)")
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez attribuer ce fournisseur qu'a une de vos ACPs")
        doc = {
            "id": str(uuid.uuid4()),
            **data.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.suppliers.insert_one(doc)
        if copro_id:
            await assign_supplier_account(db, doc, copro_id)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{supplier_id}")
    async def get_supplier(supplier_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not s:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(s, allowed_copros):
            # 404 (et non 403) pour ne pas leaker l'existence du fournisseur (RGPD)
            raise HTTPException(404, "Fournisseur non trouve")
        return s

    @router.put("/{supplier_id}")
    async def update_supplier(supplier_id: str, data: SupplierInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(existing, allowed_copros):
            raise HTTPException(404, "Fournisseur non trouve")
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or ""
        # Empeche un syndic de "transferer" un fournisseur vers une ACP qui n'est pas la sienne
        if not is_super and copro_id and copro_id not in (allowed_copros or []):
            raise HTTPException(403, "Vous ne pouvez attribuer ce fournisseur qu'a une de vos ACPs")
        result = await db.suppliers.update_one({"id": supplier_id}, {"$set": data.model_dump()})
        if result.matched_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if copro_id:
            await assign_supplier_account(db, s, copro_id)
            s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        return s

    @router.delete("/{supplier_id}")
    async def delete_supplier(supplier_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(existing, allowed_copros):
            raise HTTPException(404, "Fournisseur non trouve")
        result = await db.suppliers.delete_one({"id": supplier_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        return {"message": "Fournisseur supprime"}

    return router
