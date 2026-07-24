"""Expense categories (Natures de depense).

Niveau metier intermediaire entre Cle de repartition et Compte PCMN.
Relation 1:1 avec un compte PCMN classe 6.
Scopee par ACP.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid, re, unicodedata


def _norm_cat_name(name: str) -> str:
    """Normalize category name for similarity comparison."""
    if not name:
        return ""
    s = name.lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cat_names_similar(a: str, b: str) -> bool:
    """Return True if two normalised nature names are similar enough to flag."""
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    return overlap / max(len(wa), len(wb)) >= 0.7


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
    # Cle de repartition par DEFAUT pour cette nature.
    # Pre-rempli automatiquement sur les nouvelles lignes de facture/budget
    # quand on selectionne cette nature. Vide = pas de defaut (tantiemes).
    default_distribution_key_id: Optional[str] = ""


def create_expense_categories_router(db):
    router = APIRouter(prefix="/api/expense-categories")

    @router.get("")
    async def list_categories(request: Request, copropriete_id: Optional[str] = None, search: Optional[str] = None):
        from syndic_scope import syndic_query
        q = {**syndic_query(request)}
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
            amap = {a["number"]: a.get("name", "") for a in accs}
            for c in cats:
                c["account_name"] = amap.get(c.get("account_number", ""), "")
        # Resolve default_distribution_key NAME for display
        key_ids = list({c.get("default_distribution_key_id", "") for c in cats if c.get("default_distribution_key_id")})
        if key_ids:
            keys = await db.distribution_keys.find(
                {"id": {"$in": key_ids}}, {"_id": 0, "id": 1, "name": 1}
            ).to_list(500)
            kmap = {k["id"]: k.get("name", "") for k in keys}
            for c in cats:
                k = c.get("default_distribution_key_id", "")
                c["default_distribution_key_name"] = kmap.get(k, "") if k else ""
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
    async def create_category(data: ExpenseCategoryInput, request: Request):
        if not data.account_number:
            raise HTTPException(400, "Compte PCMN obligatoire")
        # Validation : occupant_pct + proprietaire_pct = 100
        occ = float(data.default_occupant_pct or 0)
        prop = float(data.default_proprietaire_pct or 0)
        if abs((occ + prop) - 100) > 0.01:
            raise HTTPException(400, f"La somme % occupant ({occ}) + % proprietaire ({prop}) doit etre 100 (recu {occ + prop})")
        # iter90ii : UNICITE du NOM par ACP. Regle metier utilisateur :
        # "tu ne peux pas avoir deux fois le meme nom de nature de depense".
        # Plusieurs natures peuvent partager le meme compte PCMN, mais pas le
        # meme nom. Le check est case-insensitive + trim.
        name_clean = (data.name or "").strip()
        if name_clean:
            existing_name = await db.expense_categories.find_one(
                {
                    "copropriete_id": data.copropriete_id or "",
                    "name": {"$regex": f"^{__import__('re').escape(name_clean)}$", "$options": "i"},
                },
                {"_id": 0, "id": 1, "name": 1},
            )
            if existing_name:
                raise HTTPException(
                    409,
                    f"Une nature de depense avec le nom '{name_clean}' existe deja "
                    f"dans cette ACP (id {existing_name.get('id','')[:8]}). "
                    "Choisissez un nom different ou modifiez la nature existante.",
                )
        # Garde-fou anti-doublons similaires : bloque la creation si un nom
        # tres proche existe deja (ex: "Ascenseur" vs "Ascenseurs").
        if name_clean:
            norm_new = _norm_cat_name(name_clean)
            if norm_new:
                all_cats = await db.expense_categories.find(
                    {"copropriete_id": data.copropriete_id or ""},
                    {"_id": 0, "id": 1, "name": 1, "account_number": 1},
                ).to_list(500)
                for cat in all_cats:
                    if _cat_names_similar(norm_new, _norm_cat_name(cat.get("name", ""))):
                        raise HTTPException(
                            409,
                            f"Une nature similaire existe deja : '{cat.get('name','')}' "
                            f"(compte {cat.get('account_number','')}). "
                            f"Utilisez cette nature existante plutot que de creer "
                            f"'{name_clean}' qui est un doublon potentiel."
                        )
        # iter90ex : suppression du blocage 1:1 (compte PCMN <-> nature).
        # Plusieurs natures de depense peuvent partager le meme compte
        # PCMN (ex: "RC copro" et "Assurance RC CoC & Comm.aux comptes"
        # tous deux sur 6141). Les ecritures comptables restent
        # correctes puisque tout le systeme downstream identifie la
        # nature par `expense_category_id`, jamais en remontant de
        # `account_number`. Validate account exists and is class 5/6/7.
        pcmn = await db.pcmn_accounts.find_one({
            "number": data.account_number,
            "copropriete_id": data.copropriete_id or "",
        }, {"_id": 0})
        if not pcmn:
            raise HTTPException(400, "Compte PCMN inexistant pour cette ACP")
        # iter90m : classe 5 autorisee UNIQUEMENT pour les comptes 58*
        # (Virements internes PCMN belge - transfert compte a vue <-> epargne)
        cls = pcmn.get("class_num")
        num = (data.account_number or "").strip()
        if cls not in (5, 6, 7):
            raise HTTPException(400, "Le compte doit etre de classe 5 (58 Virements internes), 6 (Charges) ou 7 (Produits)")
        if cls == 5 and not num.startswith("58"):
            raise HTTPException(400, "En classe 5, seuls les comptes 58* (Virements internes) sont acceptes comme nature")
        # Auto-derive kind from class_num if not explicitly set
        kind = data.kind or (
            "transfer" if cls == 5 else ("produit" if cls == 7 else "charge")
        )
        doc = {
            "id": str(uuid.uuid4()),
            **data.model_dump(),
            "kind": kind,
            "account_name": pcmn.get("name", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        from syndic_scope import inject_syndic
        inject_syndic(doc, request)
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
        # iter90ii : UNICITE du NOM par ACP a l'UPDATE aussi.
        new_name = (data.name or "").strip()
        if new_name and new_name.lower() != (existing.get("name") or "").strip().lower():
            existing_name = await db.expense_categories.find_one(
                {
                    "copropriete_id": existing.get("copropriete_id") or "",
                    "name": {"$regex": f"^{__import__('re').escape(new_name)}$", "$options": "i"},
                    "id": {"$ne": cat_id},
                },
                {"_id": 0, "id": 1, "name": 1},
            )
            if existing_name:
                raise HTTPException(
                    409,
                    f"Une nature de depense avec le nom '{new_name}' existe deja "
                    f"dans cette ACP (id {existing_name.get('id','')[:8]}).",
                )
        # If account changed: valider que le compte existe et est de la
        # bonne classe. iter90ex : plus de blocage 1:1.
        if data.account_number != existing.get("account_number"):
            pcmn = await db.pcmn_accounts.find_one({
                "number": data.account_number,
                "copropriete_id": data.copropriete_id or existing.get("copropriete_id", ""),
            }, {"_id": 0})
            if not pcmn or pcmn.get("class_num") not in (5, 6, 7):
                raise HTTPException(400, "Compte invalide (classe 5-58*, 6 ou 7 obligatoire)")
            if pcmn.get("class_num") == 5 and not (data.account_number or "").startswith("58"):
                raise HTTPException(400, "En classe 5, seuls les comptes 58* (Virements internes) sont acceptes")
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
