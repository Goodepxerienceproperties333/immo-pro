from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import uuid
from tier_accounts import assign_owner_accounts


def create_properties_router(db):
    router = APIRouter(prefix="/api")

    async def _get_user_scope(request):
        """Retourne (is_super_global, allowed_copro_ids).
        - is_super_global=True pour superadmin/admin : acces total, allowed=None.
        - Sinon allowed_copro_ids = liste des ACPs de l'utilisateur (peut etre []).
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if is_superadmin_only(role):
            return True, None
        return False, user.get("copropriete_ids", []) or []

    async def _owner_in_scope(owner_id: str, allowed_copros) -> bool:
        """True si l'owner a au moins un lot dans une ACP du scope (ou superadmin)."""
        if allowed_copros is None:
            return True
        if not allowed_copros:
            return False
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

    async def _allowed_owner_ids(allowed_copros):
        """Retourne le set des owner_id presents dans les lots des ACPs du scope."""
        if allowed_copros is None:
            return None  # superadmin = pas de filtre
        if not allowed_copros:
            return set()
        ids1 = await db.lots.distinct("owner_id", {"copropriete_id": {"$in": allowed_copros}})
        ids2 = await db.lots.distinct("owner_ids", {"copropriete_id": {"$in": allowed_copros}})
        return {x for x in (ids1 or []) if x} | {x for x in (ids2 or []) if x}

    # ---- OWNERS ----
    class OwnerInput(BaseModel):
        first_name: Optional[str] = ""
        last_name: Optional[str] = ""
        name: Optional[str] = ""
        civility: Optional[str] = ""
        address: Optional[str] = ""
        postal_code: Optional[str] = ""
        city: Optional[str] = ""
        country: Optional[str] = "Belgique"
        email: Optional[str] = ""
        email2: Optional[str] = ""
        phone: Optional[str] = ""
        phone2: Optional[str] = ""
        copropriete_id: Optional[str] = ""
        # Optional Optipro/Sogis import fields - kept as legacy reference
        auxiliary_code: Optional[str] = ""  # ex. "C0777"
        identifier: Optional[str] = ""      # ex. "193M04"
        vcs_code: Optional[str] = ""        # if provided, keep it; otherwise auto-generate
        vcs_digits: Optional[str] = ""
        iban: Optional[str] = ""

    @router.get("/owners")
    async def list_owners(request: Request, copropriete_id: Optional[str] = None, include_unassigned: bool = False):
        """Liste des proprietaires - chinese wall STRICT (RGPD).

        - Superadmin/admin : voit tout.
        - Syndic/gestionnaire : ne voit QUE les proprietaires ayant un lot dans
          UNE DE SES ACPs (`user.copropriete_ids`).
        - Avec `copropriete_id` : restreint a cette ACP (verifie deja par middleware).
        - `include_unassigned=true` : ajoute les owners orphelins (copropriete_id="")
          qui ont ete crees mais pas encore lies a un lot. Indispensable pour
          l'assistant de creation d'ACP (drop-down d'affectation lot->owner) afin
          que les owners juste importes via PdfImportDialog soient visibles.
        """
        # Aussi accepter le header X-Copropriete-Id pour homogeneiser
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        is_super, allowed_copros = await _get_user_scope(request)
        # Helper to also fetch orphan owners (copropriete_id == "" or missing) when requested
        async def _fetch_orphans():
            return await db.owners.find(
                {"$or": [{"copropriete_id": ""}, {"copropriete_id": {"$exists": False}}, {"copropriete_id": None}]},
                {"_id": 0},
            ).sort("last_name", 1).to_list(2000)
        if copropriete_id:
            # Cas ACP specifique : owners ayant un lot dans cette ACP
            owner_ids_single = await db.lots.distinct("owner_id", {"copropriete_id": copropriete_id})
            owner_ids_multi = await db.lots.distinct("owner_ids", {"copropriete_id": copropriete_id})
            allowed = {oid for oid in (owner_ids_single or []) if oid} | {oid for oid in (owner_ids_multi or []) if oid}
            owners = await db.owners.find({"id": {"$in": list(allowed)}}, {"_id": 0}).sort("last_name", 1).to_list(2000) if allowed else []
            if include_unassigned:
                owners.extend(await _fetch_orphans())
            return owners
        if is_super:
            # Superadmin sans param : tous les owners (vue plateforme)
            owners = await db.owners.find({}, {"_id": 0}).sort("last_name", 1).to_list(2000)
            return owners
        # Syndic / gestionnaire sans param : owners de TOUTES SES ACPs
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros)
        owners = []
        if allowed_owner_ids:
            owners = await db.owners.find(
                {"id": {"$in": list(allowed_owner_ids)}}, {"_id": 0}
            ).sort("last_name", 1).to_list(2000)
        if include_unassigned:
            owners.extend(await _fetch_orphans())
        return owners

    @router.post("/owners")
    async def create_owner(data: OwnerInput):
        from server import generate_vcs
        # If a VCS code is provided (e.g. from an Optipro import), reuse it
        # to preserve the legacy reference. Otherwise auto-generate one.
        vcs_code = (data.vcs_code or "").strip()
        if not vcs_code:
            vcs_code = await generate_vcs(db)
        vcs_digits = (data.vcs_digits or "").strip()
        if not vcs_digits:
            vcs_digits = vcs_code.replace("+", "").replace("/", "")
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        doc = {
            "id": str(uuid.uuid4()),
            "first_name": data.first_name,
            "last_name": data.last_name,
            "name": full_name,
            "civility": data.civility or "",
            "address": data.address,
            "postal_code": data.postal_code,
            "city": data.city,
            "country": data.country,
            "email": data.email,
            "email2": data.email2,
            "phone": data.phone,
            "phone2": data.phone2,
            "vcs_code": vcs_code,
            "vcs_digits": vcs_digits,
            "auxiliary_code": (data.auxiliary_code or "").strip(),
            "identifier": (data.identifier or "").strip(),
            "iban": (data.iban or "").strip(),
            "copropriete_id": data.copropriete_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.owners.insert_one(doc)
        if data.copropriete_id:
            await assign_owner_accounts(db, doc, data.copropriete_id)
            doc = await db.owners.find_one({"id": doc["id"]}, {"_id": 0})
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/owners/check-duplicate")
    async def check_duplicate_owner(request: Request, email: Optional[str] = None, phone: Optional[str] = None):
        """Check if email or phone already exists.
        Pour un syndic : cherche UNIQUEMENT dans les owners de ses ACPs (RGPD).
        Pour un superadmin : cherche dans tous les owners.
        """
        is_super, allowed_copros = await _get_user_scope(request)
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros) if not is_super else None
        duplicates = []
        def _filter_owner(owner_id):
            return is_super or (allowed_owner_ids is not None and owner_id in allowed_owner_ids)
        if email and email.strip():
            found = await db.owners.find(
                {"$or": [{"email": email.strip()}, {"email2": email.strip()}]},
                {"_id": 0, "id": 1, "name": 1, "email": 1, "phone": 1, "copropriete_id": 1}
            ).to_list(50)
            for f in found:
                if _filter_owner(f.get("id", "")):
                    duplicates.append({"field": "email", "value": email, "owner_name": f.get("name", ""), "copropriete_id": f.get("copropriete_id", "")})
        if phone and phone.strip():
            found = await db.owners.find(
                {"$or": [{"phone": phone.strip()}, {"phone2": phone.strip()}]},
                {"_id": 0, "id": 1, "name": 1, "email": 1, "phone": 1, "copropriete_id": 1}
            ).to_list(50)
            for f in found:
                if _filter_owner(f.get("id", "")):
                    duplicates.append({"field": "phone", "value": phone, "owner_name": f.get("name", ""), "copropriete_id": f.get("copropriete_id", "")})
        return {"duplicates": duplicates, "has_duplicates": len(duplicates) > 0}

    @router.get("/owners/lookup-vcs")
    async def lookup_vcs(request: Request, vcs: str = ""):
        if not vcs:
            return []
        is_super, allowed_copros = await _get_user_scope(request)
        clean = vcs.replace("+", "").replace("/", "").replace(" ", "").strip()
        results = await db.owners.find(
            {"$or": [
                {"vcs_code": {"$regex": vcs.replace("+", "\\+"), "$options": "i"}},
                {"vcs_digits": clean},
                {"name": {"$regex": vcs, "$options": "i"}},
                {"last_name": {"$regex": vcs, "$options": "i"}},
                {"first_name": {"$regex": vcs, "$options": "i"}},
            ]},
            {"_id": 0}
        ).to_list(50)
        if is_super:
            return results[:20]
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros)
        filtered = [o for o in results if allowed_owner_ids is not None and o.get("id") in allowed_owner_ids]
        return filtered[:20]

    @router.get("/owners/{owner_id}")
    async def get_owner(owner_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            # 404 plutot que 403 pour ne pas leaker l'existence (RGPD)
            raise HTTPException(404, "Proprietaire non trouve")
        return owner

    @router.put("/owners/{owner_id}")
    async def update_owner(owner_id: str, data: OwnerInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            raise HTTPException(404, "Proprietaire non trouve")
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        result = await db.owners.update_one(
            {"id": owner_id},
            {"$set": {
                "first_name": data.first_name, "last_name": data.last_name, "name": full_name,
                "address": data.address, "postal_code": data.postal_code, "city": data.city,
                "country": data.country, "email": data.email, "email2": data.email2,
                "phone": data.phone, "phone2": data.phone2,
            }}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or owner.get("copropriete_id", "")
        if copro_id:
            await assign_owner_accounts(db, owner, copro_id)
            owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        return owner

    @router.delete("/owners/{owner_id}")
    async def delete_owner(owner_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            raise HTTPException(404, "Proprietaire non trouve")
        result = await db.owners.delete_one({"id": owner_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        return {"message": "Proprietaire supprime"}

    # ---- LOTS ----
    class LotInput(BaseModel):
        number: str
        description: Optional[str] = ""
        lot_type: Optional[str] = "apartment"
        floor: Optional[int] = 0
        area: Optional[float] = 0.0
        quotity: Optional[float] = 0.0
        owner_id: Optional[str] = ""
        owner_ids: Optional[List[str]] = []
        copropriete_id: Optional[str] = ""

    @router.get("/lots")
    async def list_lots(request: Request, copropriete_id: Optional[str] = None):
        """Liste des lots - chinese wall STRICT.
        - Superadmin : voit tout
        - Syndic : ne voit que les lots de ses ACPs
        """
        is_super, allowed_copros = await _get_user_scope(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        elif not is_super:
            if not allowed_copros:
                return []
            q["copropriete_id"] = {"$in": allowed_copros}
        lots = await db.lots.find(q, {"_id": 0}).sort("number", 1).to_list(2000)
        return lots

    @router.post("/lots")
    async def create_lot(data: LotInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or ""
        if not is_super:
            if not copro_id:
                raise HTTPException(400, "Un lot doit etre rattache a une copropriete")
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez creer un lot que pour une de vos ACPs")
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "description": data.description,
            "lot_type": data.lot_type,
            "floor": data.floor,
            "area": data.area,
            "quotity": data.quotity,
            "owner_id": ids[0] if ids else "",
            "owner_ids": ids,
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.lots.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/lots/{lot_id}")
    async def update_lot(lot_id: str, data: LotInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Lot non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Lot non trouve")
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        update = {
            "number": data.number, "description": data.description,
            "lot_type": data.lot_type, "floor": data.floor,
            "area": data.area, "quotity": data.quotity,
            "owner_id": ids[0] if ids else "",
            "owner_ids": ids,
        }
        result = await db.lots.update_one({"id": lot_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return await db.lots.find_one({"id": lot_id}, {"_id": 0})

    @router.delete("/lots/{lot_id}")
    async def delete_lot(lot_id: str):
        result = await db.lots.delete_one({"id": lot_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return {"message": "Lot supprime"}

    # ---- MUTATION (vente / changement de proprietaire) ----
    class LotMutationInput(BaseModel):
        new_owner_id: str
        sale_date: str  # ISO YYYY-MM-DD
        sale_price: Optional[float] = 0.0
        note: Optional[str] = ""

    @router.post("/lots/{lot_id}/mutate")
    async def mutate_lot(lot_id: str, data: LotMutationInput):
        """Mutation d'un lot (vente entre proprietaires). Calcule et passe l'OD
        comptable de transfert :
        - Fonds de roulement : transfert de la quote-part du lot du vendeur vers l'acquereur
          (calculee sur les quotities du lot vs total des quotities de l'ACP, sur le solde
          actuel du compte 100).
        - Provisions pour charges : prorata sur les appels deja emis dont la periode
          chevauche sale_date. La part posterieure a sale_date est creditee au vendeur
          et debitee a l'acquereur.
        Met a jour lot.owner_id = new_owner_id et conserve l'historique dans lot.mutations[].
        """
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        copro_id = lot.get("copropriete_id", "")
        if not copro_id:
            raise HTTPException(400, "Lot sans copropriete")
        old_owner_id = lot.get("owner_id", "")
        if not old_owner_id:
            raise HTTPException(400, "Lot sans proprietaire actuel - impossible de muter")
        if data.new_owner_id == old_owner_id:
            raise HTTPException(400, "L'acquereur doit etre different du proprietaire actuel")

        old_owner = await db.owners.find_one({"id": old_owner_id}, {"_id": 0})
        new_owner = await db.owners.find_one({"id": data.new_owner_id}, {"_id": 0})
        if not old_owner:
            raise HTTPException(404, "Proprietaire actuel introuvable")
        if not new_owner:
            raise HTTPException(404, "Acquereur introuvable")

        # Assure les comptes tiers existent dans cette ACP
        old_owner = await assign_owner_accounts(db, old_owner, copro_id)
        new_owner = await assign_owner_accounts(db, new_owner, copro_id)
        old_acc = (old_owner.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
        new_acc = (new_owner.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
        if not old_acc or not new_acc:
            raise HTTPException(500, "Impossible de resoudre les comptes tiers")

        # ---- 1) Quote-part fonds de roulement (compte 100) ----
        # Solde actuel du compte 100 cote credit (passif) pour cette ACP
        roul_pipeline = [
            {"$match": {"copropriete_id": copro_id}},
            {"$unwind": "$lines"},
            {"$match": {"lines.account_number": "100"}},
            {"$group": {"_id": None,
                        "credit": {"$sum": "$lines.credit"},
                        "debit": {"$sum": "$lines.debit"}}},
        ]
        agg = await db.journal_entries.aggregate(roul_pipeline).to_list(1)
        fonds_roul_total = round((agg[0]["credit"] - agg[0]["debit"]) if agg else 0.0, 2)
        # Quotites totales de l'ACP
        all_lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0, "quotity": 1}).to_list(10000)
        total_quotity = round(sum(float(l.get("quotity", 0) or 0) for l in all_lots), 6)
        lot_quotity = float(lot.get("quotity", 0) or 0)
        if total_quotity > 0 and lot_quotity > 0 and fonds_roul_total > 0:
            roulement_quota = round(fonds_roul_total * (lot_quotity / total_quotity), 2)
        else:
            roulement_quota = 0.0

        # ---- 2) Prorata provisions sur appels emis chevauchant sale_date ----
        # On utilise period_start / period_end (periode effective COUVERTE par l'appel)
        # si presents. Sinon fallback intelligent : deduit la periode depuis le nom
        # "X/N" et l'exercice fiscal, OU [date, date + 90j] en dernier recours.
        sale_date = data.sale_date
        try:
            sale_dt = datetime.strptime(sale_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(400, "sale_date doit etre au format YYYY-MM-DD")

        # Pre-fetch des exercices fiscaux pour le fallback
        fy_by_id: dict = {}
        async for _fy in db.fiscal_years.find({"copropriete_id": copro_id}, {"_id": 0}):
            fy_by_id[_fy["id"]] = _fy

        def _resolve_period(call: dict) -> tuple:
            """Retourne (period_start_date, period_end_date) pour un appel.
            Priorite : period_start/period_end stockes > deduction depuis nom + fy
            > [date, date+90j] fallback."""
            # 1) Champs explicites (appels generes apres iter76)
            ps = call.get("period_start")
            pe = call.get("period_end")
            if ps and pe:
                try:
                    return (datetime.strptime(ps, "%Y-%m-%d").date(),
                            datetime.strptime(pe, "%Y-%m-%d").date())
                except Exception:
                    pass
            # 2) Deduction depuis "X/N" dans le nom + fiscal year
            import re as _re
            try:
                start_dt = datetime.strptime(call.get("date", ""), "%Y-%m-%d").date()
            except Exception:
                return None, None
            m = _re.search(r"(\d+)\s*/\s*(\d+)", call.get("name", "") or "")
            if m:
                try:
                    n_calls = int(m.group(2))
                    if n_calls in (1, 2, 3, 4, 6, 12) and n_calls > 0:
                        interval = 12 // n_calls
                        fy = fy_by_id.get(call.get("fiscal_year_id", ""))
                        # period_end = start_dt + interval mois - 1 jour
                        # (en respectant fin d'exercice si dispo)
                        try:
                            year = start_dt.year
                            month = start_dt.month + interval
                            while month > 12:
                                month -= 12
                                year += 1
                            try:
                                next_start = start_dt.replace(year=year, month=month)
                            except ValueError:
                                # Cas 31 janvier + 1 mois -> 28/29 fevrier
                                from calendar import monthrange
                                last_day = monthrange(year, month)[1]
                                next_start = start_dt.replace(year=year, month=month, day=min(start_dt.day, last_day))
                            end_dt = next_start - timedelta(days=1)
                            # Borne haut fin d'exercice
                            if fy:
                                try:
                                    fy_end_dt = datetime.strptime(fy["end_date"], "%Y-%m-%d").date()
                                    if end_dt > fy_end_dt:
                                        end_dt = fy_end_dt
                                except Exception:
                                    pass
                            return start_dt, end_dt
                        except Exception:
                            pass
                except (ValueError, ZeroDivisionError):
                    pass
            # 3) Fallback : 90 jours
            return start_dt, start_dt + timedelta(days=90)

        # On considere les fund_calls de type 'provisions' (pas reserve/roulement)
        # dont la periode COUVERTE chevauche sale_date
        calls = await db.fund_calls.find(
            {"copropriete_id": copro_id}, {"_id": 0}
        ).sort("date", 1).to_list(10000)
        calls = [c for c in calls if (c.get("call_type") or "provisions") == "provisions"]

        prorata_total = 0.0
        prorata_details = []
        for c in calls:
            c_start, c_end = _resolve_period(c)
            if not c_start or not c_end or c_end < c_start:
                continue
            if not (c_start <= sale_dt <= c_end):
                continue
            # Montant appele pour le vendeur sur ce call
            dist = c.get("distribution") or []
            owner_line = next((d for d in dist if d.get("owner_id") == old_owner_id), None)
            if not owner_line:
                continue
            amount_owner = float(owner_line.get("amount", 0) or 0)
            if amount_owner <= 0:
                continue
            # Prorata: portion APRES sale_date (jour de la vente inclus pour l'acquereur)
            total_days = (c_end - c_start).days + 1
            days_after = (c_end - sale_dt).days + 1
            if total_days <= 0:
                continue
            prorata = round(amount_owner * (days_after / total_days), 2)
            if prorata < 0.01:
                continue
            prorata_total += prorata
            prorata_details.append({
                "fund_call_id": c.get("id"),
                "fund_call_name": c.get("name", ""),
                "period_start": c_start.isoformat(),
                "period_end": c_end.isoformat(),
                "owner_amount": amount_owner,
                "prorata": prorata,
                "days_after": days_after,
                "total_days": total_days,
            })
        prorata_total = round(prorata_total, 2)

        # ---- 3) Generation de l'ecriture OD ----
        total_transfer = round(roulement_quota + prorata_total, 2)
        entry_id = None
        if total_transfer > 0.001:
            lines = [
                {
                    "account_number": new_acc,
                    "account_name": f"Mutation - {new_owner.get('last_name') or new_owner.get('name')}",
                    "debit": total_transfer, "credit": 0.0,
                    "third_party_id": data.new_owner_id,
                    "third_party_name": new_owner.get("name", ""),
                },
                {
                    "account_number": old_acc,
                    "account_name": f"Mutation - {old_owner.get('last_name') or old_owner.get('name')}",
                    "debit": 0.0, "credit": total_transfer,
                    "third_party_id": old_owner_id,
                    "third_party_name": old_owner.get("name", ""),
                },
            ]
            entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": sale_date,
                "reference": f"MUT-{lot.get('number','')[:20]}",
                "description": (
                    f"Mutation lot {lot.get('number','')}: {old_owner.get('name','')} -> {new_owner.get('name','')} "
                    f"(roulement {roulement_quota:.2f} EUR + prorata provisions {prorata_total:.2f} EUR)"
                ),
                "lines": lines,
                "total_debit": total_transfer,
                "total_credit": total_transfer,
                "copropriete_id": copro_id,
                "auto_generated": False,
                "manually_edited": True,
                "source_type": "lot_mutation",
                "source_id": lot_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(entry)
            entry_id = entry["id"]

        # ---- 4) Maj du lot + historique ----
        mutation_record = {
            "id": str(uuid.uuid4()),
            "date": sale_date,
            "old_owner_id": old_owner_id,
            "old_owner_name": old_owner.get("name", ""),
            "new_owner_id": data.new_owner_id,
            "new_owner_name": new_owner.get("name", ""),
            "roulement_quota": roulement_quota,
            "prorata_provisions": prorata_total,
            "total_transfer": total_transfer,
            "sale_price": float(data.sale_price or 0),
            "note": data.note or "",
            "journal_entry_id": entry_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "prorata_details": prorata_details,
        }
        await db.lots.update_one(
            {"id": lot_id},
            {"$set": {"owner_id": data.new_owner_id,
                      "owner_ids": [data.new_owner_id]},
             "$push": {"mutations": mutation_record}}
        )
        updated = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        return {
            "lot": updated,
            "mutation": mutation_record,
        }

    @router.delete("/lots/{lot_id}/mutate/{mutation_id}")
    async def cancel_mutation(lot_id: str, mutation_id: str):
        """Annule la DERNIERE mutation d'un lot : restaure l'ancien proprietaire,
        supprime l'ecriture OD de mutation et retire l'entree d'historique.
        Refuse si la mutation visee n'est pas la plus recente."""
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        mutations = lot.get("mutations") or []
        if not mutations:
            raise HTTPException(400, "Ce lot n'a aucune mutation a annuler")
        last = mutations[-1]
        # Accept "last" as a shortcut for the most recent mutation, or accept
        # the id if it matches. Older mutations created before the `id` field
        # was added won't have one, so we also accept a match-by-position when
        # the id is missing.
        if mutation_id != "last" and last.get("id") and last.get("id") != mutation_id:
            raise HTTPException(
                400,
                "Seule la derniere mutation peut etre annulee (les mutations anterieures sont figees)."
            )
        # Restore previous owner
        old_owner_id = last.get("old_owner_id")
        if not old_owner_id:
            raise HTTPException(400, "Mutation sans old_owner_id - impossible de restaurer")
        # Delete OD entry if any
        entry_id = last.get("journal_entry_id")
        if entry_id:
            await db.journal_entries.delete_one({"id": entry_id})
        # Pop the last mutation + restore owner
        await db.lots.update_one(
            {"id": lot_id},
            {"$set": {"owner_id": old_owner_id, "owner_ids": [old_owner_id]},
             "$pop": {"mutations": 1}}
        )
        updated = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        return {
            "status": "ok",
            "message": "Mutation annulee, proprietaire precedent restaure",
            "lot": updated,
            "cancelled_mutation": last,
        }

    @router.post("/lots/{lot_id}/mutate-preview")
    async def mutate_lot_preview(lot_id: str, data: LotMutationInput):
        """Preview du calcul de mutation sans rien ecrire en base."""
        # Reuse the same logic but avoid persistence. We implement a small variant:
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        copro_id = lot.get("copropriete_id", "")
        old_owner_id = lot.get("owner_id", "")
        if not (copro_id and old_owner_id):
            raise HTTPException(400, "Lot incomplet")

        try:
            sale_dt = datetime.strptime(data.sale_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(400, "sale_date doit etre au format YYYY-MM-DD")

        # Fonds de roulement
        roul_pipeline = [
            {"$match": {"copropriete_id": copro_id}},
            {"$unwind": "$lines"},
            {"$match": {"lines.account_number": "100"}},
            {"$group": {"_id": None,
                        "credit": {"$sum": "$lines.credit"},
                        "debit": {"$sum": "$lines.debit"}}},
        ]
        agg = await db.journal_entries.aggregate(roul_pipeline).to_list(1)
        fonds_roul_total = round((agg[0]["credit"] - agg[0]["debit"]) if agg else 0.0, 2)
        all_lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0, "quotity": 1}).to_list(10000)
        total_quotity = round(sum(float(l.get("quotity", 0) or 0) for l in all_lots), 6)
        lot_quotity = float(lot.get("quotity", 0) or 0)
        roulement_quota = round(fonds_roul_total * (lot_quotity / total_quotity), 2) if (total_quotity > 0 and lot_quotity > 0 and fonds_roul_total > 0) else 0.0

        # Prorata provisions
        calls = await db.fund_calls.find({"copropriete_id": copro_id}, {"_id": 0}).sort("date", 1).to_list(10000)
        calls = [c for c in calls if (c.get("call_type") or "provisions") == "provisions"]
        prorata_total = 0.0
        prorata_details = []
        from datetime import timedelta as _td
        for c in calls:
            try:
                c_start = datetime.strptime(c.get("date", ""), "%Y-%m-%d").date()
                c_end = datetime.strptime(c.get("due_date") or c.get("date"), "%Y-%m-%d").date()
            except Exception:
                continue
            if c_end <= c_start:
                c_end = c_start + _td(days=90)
            if not (c_start <= sale_dt <= c_end):
                continue
            owner_line = next((d for d in (c.get("distribution") or []) if d.get("owner_id") == old_owner_id), None)
            if not owner_line:
                continue
            amount_owner = float(owner_line.get("amount", 0) or 0)
            if amount_owner <= 0:
                continue
            total_days = (c_end - c_start).days + 1
            days_after = (c_end - sale_dt).days + 1
            prorata = round(amount_owner * (days_after / total_days), 2)
            if prorata >= 0.01:
                prorata_total += prorata
                prorata_details.append({
                    "fund_call_id": c.get("id"),
                    "fund_call_name": c.get("name", ""),
                    "period_start": c_start.isoformat(),
                    "period_end": c_end.isoformat(),
                    "owner_amount": amount_owner,
                    "prorata": prorata,
                    "days_after": days_after,
                    "total_days": total_days,
                })
        prorata_total = round(prorata_total, 2)
        return {
            "fonds_roulement_total": fonds_roul_total,
            "lot_quotity": lot_quotity,
            "total_quotity": total_quotity,
            "roulement_quota": roulement_quota,
            "prorata_provisions": prorata_total,
            "prorata_details": prorata_details,
            "total_transfer": round(roulement_quota + prorata_total, 2),
        }

    # ---- TENANTS ----
    class TenantInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        lot_id: Optional[str] = ""
        lease_start: Optional[str] = ""
        lease_end: Optional[str] = ""
        rent_amount: Optional[float] = 0.0
        copropriete_id: Optional[str] = ""

    @router.get("/tenants")
    async def list_tenants(request: Request, copropriete_id: Optional[str] = None):
        """Liste des locataires - chinese wall STRICT (RGPD).
        - Superadmin : voit tout
        - Syndic / gestionnaire : ne voit QUE les locataires de ses ACPs (via tenant.copropriete_id)
        """
        is_super, allowed_copros = await _get_user_scope(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        elif not is_super:
            if not allowed_copros:
                return []
            q["copropriete_id"] = {"$in": allowed_copros}
        tenants = await db.tenants.find(q, {"_id": 0}).sort("name", 1).to_list(2000)
        return tenants

    @router.post("/tenants")
    async def create_tenant(data: TenantInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or ""
        if not is_super:
            if not copro_id:
                raise HTTPException(400, "Un locataire doit etre rattache a une copropriete (chinese wall + RGPD)")
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez creer un locataire que pour une de vos ACPs")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "lot_id": data.lot_id,
            "lease_start": data.lease_start,
            "lease_end": data.lease_end,
            "rent_amount": data.rent_amount,
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.tenants.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/tenants/{tenant_id}")
    async def update_tenant(tenant_id: str, data: TenantInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Locataire non trouve")
        update = {
            "name": data.name, "email": data.email, "phone": data.phone,
            "lot_id": data.lot_id, "lease_start": data.lease_start,
            "lease_end": data.lease_end, "rent_amount": data.rent_amount
        }
        result = await db.tenants.update_one({"id": tenant_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return await db.tenants.find_one({"id": tenant_id}, {"_id": 0})

    @router.delete("/tenants/{tenant_id}")
    async def delete_tenant(tenant_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Locataire non trouve")
        result = await db.tenants.delete_one({"id": tenant_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return {"message": "Locataire supprime"}

    return router
