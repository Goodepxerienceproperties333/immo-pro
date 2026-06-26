"""Expense categories (Natures de depense).

Niveau metier intermediaire entre Cle de repartition et Compte PCMN.
Relation 1:1 avec un compte PCMN classe 6.
Scopee par ACP.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class ExpenseCategoryInput(BaseModel):
    name: str
    account_number: str  # PCMN class 6 (Charges) or class 7 (Produits)
    description: Optional[str] = ""
    copropriete_id: Optional[str] = ""
    # Repartition occupant/proprietaire pour le decompte locataire annuel.
    # Somme = 100. Defaut : 0% occupant, 100% proprio (charge restant a charge du proprio).
    default_occupant_pct: Optional[float] = 0.0
    default_proprietaire_pct: Optional[float] = 100.0
    # Code Optipro/Sogis (4 chiffres) - optionnel, pour tracabilite avec les imports
    code: Optional[str] = ""
    # Code TVA standard belge : A1 (21%), A2 (6%), A3 (12%), A4 (0%/exonere)
    vat_code: Optional[str] = ""
    # "charge" (classe 6) ou "produit" (classe 7). Auto-derive depuis le compte
    # si non fourni cote backend.
    kind: Optional[str] = ""


def create_expense_categories_router(db):
    router = APIRouter(prefix="/api/expense-categories")

    @router.get("")
    async def list_categories(copropriete_id: Optional[str] = None, search: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        if search:
            q["name"] = {"$regex": search, "$options": "i"}
        cats = await db.expense_categories.find(q, {"_id": 0}).sort("name", 1).to_list(1000)
        # Resolve account name from PCMN
        acc_nums = list({c["account_number"] for c in cats if c.get("account_number")})
        if acc_nums:
            pq = {"number": {"$in": acc_nums}}
            if copropriete_id:
                pq["copropriete_id"] = copropriete_id
            accs = await db.pcmn_accounts.find(pq, {"_id": 0}).to_list(1000)
            amap = {a["number"]: a["name"] for a in accs}
            for c in cats:
                c["account_name"] = amap.get(c.get("account_number", ""), "")
        # Compute usage count (invoices referencing this category)
        ids = [c["id"] for c in cats]
        if ids:
            pipeline = [
                {"$match": {"expense_category_id": {"$in": ids}}},
                {"$group": {"_id": "$expense_category_id", "count": {"$sum": 1},
                            "total": {"$sum": "$total_amount"}}},
            ]
            usage = {u["_id"]: u async for u in db.invoices.aggregate(pipeline)}
            for c in cats:
                u = usage.get(c["id"], {})
                c["invoice_count"] = u.get("count", 0)
                c["invoice_total"] = round(u.get("total", 0), 2)
        return cats

    @router.post("")
    async def create_category(data: ExpenseCategoryInput):
        if not data.account_number:
            raise HTTPException(400, "Compte PCMN obligatoire")
        # Validation : occupant_pct + proprietaire_pct = 100
        occ = float(data.default_occupant_pct or 0)
        prop = float(data.default_proprietaire_pct or 0)
        if abs((occ + prop) - 100) > 0.01:
            raise HTTPException(400, f"La somme % occupant ({occ}) + % proprietaire ({prop}) doit etre 100 (recu {occ + prop})")
        # Enforce 1:1 - reject if account_number already used in this ACP
        existing = await db.expense_categories.find_one({
            "account_number": data.account_number,
            "copropriete_id": data.copropriete_id or "",
        }, {"_id": 0})
        if existing:
            raise HTTPException(409, f"Une nature existe deja pour ce compte ({existing.get('name','?')})")
        # Validate account exists and is class 6 (Charges) or class 7 (Produits)
        pcmn = await db.pcmn_accounts.find_one({
            "number": data.account_number,
            "copropriete_id": data.copropriete_id or "",
        }, {"_id": 0})
        if not pcmn:
            raise HTTPException(400, "Compte PCMN inexistant pour cette ACP")
        if pcmn.get("class_num") not in (6, 7):
            raise HTTPException(400, "Le compte doit etre de classe 6 (Charges) ou 7 (Produits)")
        # Auto-derive kind from class_num if not explicitly set
        kind = data.kind or ("produit" if pcmn.get("class_num") == 7 else "charge")
        doc = {
            "id": str(uuid.uuid4()),
            **data.model_dump(),
            "kind": kind,
            "account_name": pcmn.get("name", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.expense_categories.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{cat_id}")
    async def get_category(cat_id: str):
        c = await db.expense_categories.find_one({"id": cat_id}, {"_id": 0})
        if not c:
            raise HTTPException(404, "Nature non trouvee")
        return c

    @router.put("/{cat_id}")
    async def update_category(cat_id: str, data: ExpenseCategoryInput):
        existing = await db.expense_categories.find_one({"id": cat_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Nature non trouvee")
        # Validation : occupant_pct + proprietaire_pct = 100
        occ = float(data.default_occupant_pct or 0)
        prop = float(data.default_proprietaire_pct or 0)
        if abs((occ + prop) - 100) > 0.01:
            raise HTTPException(400, f"La somme % occupant ({occ}) + % proprietaire ({prop}) doit etre 100 (recu {occ + prop})")
        # If account changed: enforce 1:1
        if data.account_number != existing.get("account_number"):
            dup = await db.expense_categories.find_one({
                "account_number": data.account_number,
                "copropriete_id": data.copropriete_id or existing.get("copropriete_id", ""),
                "id": {"$ne": cat_id},
            }, {"_id": 0})
            if dup:
                raise HTTPException(409, f"Compte deja attribue a la nature '{dup.get('name','?')}'")
            pcmn = await db.pcmn_accounts.find_one({
                "number": data.account_number,
                "copropriete_id": data.copropriete_id or existing.get("copropriete_id", ""),
            }, {"_id": 0})
            if not pcmn or pcmn.get("class_num") not in (6, 7):
                raise HTTPException(400, "Compte invalide (classe 6 ou 7 obligatoire)")
        await db.expense_categories.update_one(
            {"id": cat_id}, {"$set": data.model_dump()}
        )
        return await db.expense_categories.find_one({"id": cat_id}, {"_id": 0})

    @router.delete("/{cat_id}")
    async def delete_category(cat_id: str):
        used = await db.invoices.count_documents({"expense_category_id": cat_id})
        if used > 0:
            raise HTTPException(400, f"Nature utilisee par {used} facture(s) - detachez-les d'abord")
        result = await db.expense_categories.delete_one({"id": cat_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Nature non trouvee")
        return {"message": "Nature supprimee"}

    return router
