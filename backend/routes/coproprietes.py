from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId
import uuid

# iter90jb : normalisation stricte des IBAN (canonique 8859-1 / ISO 13616).
from iban_utils import normalize_iban


class BankAccountInput(BaseModel):
    iban: str
    bic: Optional[str] = ""
    account_type: str = "vue"  # vue or epargne
    is_default: Optional[bool] = False
    label: Optional[str] = ""


class LotInlineInput(BaseModel):
    number: str
    description: Optional[str] = ""
    lot_type: Optional[str] = "apartment"
    floor: Optional[int] = 0
    area: Optional[float] = 0.0
    quotity: Optional[float] = 0.0
    owner_ids: Optional[List[str]] = []
    # iter90gg : "lot parent" pour lier garage/cave a un appartement principal.
    # Reference le NUMERO du lot parent (pas l'id, car les lots sont crees
    # ensemble dans une transaction, ids pas encore stables cote client).
    parent_lot_number: Optional[str] = ""


class CoproprieteInput(BaseModel):
    name: str
    bce: Optional[str] = ""
    address: Optional[str] = ""
    postal_code: Optional[str] = ""
    city: Optional[str] = ""
    country: Optional[str] = "Belgique"
    description: Optional[str] = ""
    bank_accounts: Optional[List[BankAccountInput]] = []
    quarterly_closing: Optional[bool] = True
    default_provisions: Optional[bool] = True
    lots: Optional[List[LotInlineInput]] = []
    # iter90gg : periode de l'exercice fiscal courant fournie par le
    # syndic lors de la creation. Si fournie, on cree immediatement un
    # `fiscal_year` en base avec status='open'. Permet de valider les
    # proprietaires importes A LA DATE de debut d'exercice (source de
    # verite legale pour l'attribution des lots).
    fy_start: Optional[str] = ""  # format YYYY-MM-DD
    fy_end: Optional[str] = ""
    fy_name: Optional[str] = ""   # ex: "2025-2026", "2026", ou auto-derive
    # iter90kz : liste exhaustive des owner_ids a rattacher a cette ACP
    # (pas seulement ceux affectes a un lot - inclut les orphelins importes
    # pendant la creation). assign_owner_accounts cree copropriete_ids + tiers.
    owner_ids_to_link: Optional[List[str]] = []
    # iter90kz : mode promoteur - si renseigne, TOUS les lots sont affectes
    # a ce proprietaire (promoteur) avec start_date = fy_start.
    promoter_owner_id: Optional[str] = ""


def create_coproprietes_router(db):
    router = APIRouter(prefix="/api/coproprietes")

    async def _get_manager(request):
        from server import get_current_user, can_manage
        user = await get_current_user(request)
        if not can_manage(user.get("role", "")):
            raise HTTPException(403, "Acces refuse")
        return user

    def _generate_pcmn_number(iban: str, account_type: str) -> str:
        """Generate PCMN account number: 551xxx00 for vue, 550xxx00 for epargne.
        Convention : toujours 8 chiffres."""
        from pcmn_utils import normalize_bank_pcmn
        clean = iban.replace(" ", "").replace("-", "")
        last3 = clean[-3:] if len(clean) >= 3 else clean.zfill(3)
        prefix = "550" if account_type == "epargne" else "551"
        raw = f"{prefix}{last3}00"
        return normalize_bank_pcmn(raw)

    # Accounts that are pre-activated (visible in default selection lists)
    DEFAULT_ACTIVE_ACCOUNTS = {"614000", "615000"}  # Honoraires syndic + Frais de gestion (admin)

    async def _seed_pcmn_for_acp(copro_id: str, syndic_id: str = ""):
        """Seed le PCMN belge complet (327 comptes officiels + 10 comptes compat) pour une nouvelle ACP."""
        from pcmn_data import PCMN_ALL_ACCOUNTS
        docs = []
        for acc in PCMN_ALL_ACCOUNTS:
            d = {
                **acc,
                "copropriete_id": copro_id,
                "active": acc["number"] in DEFAULT_ACTIVE_ACCOUNTS,
                "is_custom": False,
            }
            if syndic_id:
                d["syndic_id"] = syndic_id
            docs.append(d)
        if docs:
            await db.pcmn_accounts.insert_many(docs)

    async def _seed_default_expense_natures(copro_id: str):
        """Seed les natures de depense par defaut pour une nouvelle ACP.

        Source : `default_expense_natures.DEFAULT_EXPENSE_NATURES` (23 entrees).
        Skip silencieux si une nature existe deja pour le meme `account_number` dans
        cette ACP (la contrainte 1:1 nature<->compte est respectee).

        Retourne le nombre de natures inserees.
        """
        from default_expense_natures import DEFAULT_EXPENSE_NATURES
        # Comptes deja attribues a une nature dans cette ACP (1:1)
        existing = await db.expense_categories.find(
            {"copropriete_id": copro_id}, {"_id": 0, "account_number": 1}
        ).to_list(1000)
        used_accounts = {e.get("account_number") for e in existing}
        # Mapping numero -> nom PCMN pour le `account_name`
        pcmn = await db.pcmn_accounts.find(
            {"copropriete_id": copro_id}, {"_id": 0, "number": 1, "name": 1}
        ).to_list(2000)
        pcmn_map = {p["number"]: p.get("name", "") for p in pcmn}
        now_iso = datetime.now(timezone.utc).isoformat()
        docs = []
        for nat in DEFAULT_EXPENSE_NATURES:
            if nat["account_number"] in used_accounts:
                continue
            docs.append({
                "id": str(uuid.uuid4()),
                "code": nat["code"],
                "name": nat["name"],
                "label": nat["name"],
                "account_number": nat["account_number"],
                "account_name": pcmn_map.get(nat["account_number"], nat["name"]),
                "vat_code": nat.get("vat_code", ""),
                "default_occupant_pct": float(nat["default_occupant_pct"]),
                "default_proprietaire_pct": float(nat["default_proprietaire_pct"]),
                "kind": nat.get("kind", "charge"),
                "is_default_seed": True,
                "copropriete_id": copro_id,
                "created_at": now_iso,
            })
        if docs:
            await db.expense_categories.insert_many(docs)
        return len(docs)

    async def _generate_reference(db_ref):
        """Generate chronological reference ACP-YYYYMM-NNN."""
        now = datetime.now(timezone.utc)
        prefix = f"ACP-{now.strftime('%Y%m')}"
        count = await db_ref.coproprietes.count_documents({"reference": {"$regex": f"^{prefix}"}})
        return f"{prefix}-{str(count + 1).zfill(3)}"

    async def _create_pcmn_accounts(bank_accounts, copro_id: str):
        """Auto-create PCMN accounts for bank accounts, scoped by ACP."""
        for ba in bank_accounts:
            pcmn_number = _generate_pcmn_number(ba["iban"], ba["account_type"])
            label = f"Banque {'epargne' if ba['account_type'] == 'epargne' else 'compte a vue'} {ba['iban'][-4:]}"
            existing = await db.pcmn_accounts.find_one({"number": pcmn_number, "copropriete_id": copro_id})
            if not existing:
                await db.pcmn_accounts.insert_one({
                    "number": pcmn_number,
                    "name": ba.get("label") or label,
                    "class_num": 5,
                    "parent": "550000",
                    "type": "balance",
                    "copropriete_id": copro_id,
                    "active": True,  # bank accounts are always active
                })

    @router.get("")
    async def list_coproprietes(request: Request, show_archived: Optional[bool] = False):
        from server import get_current_user
        from syndic_scope import syndic_query
        user = await get_current_user(request)
        role = user.get("role", "")
        q = {} if show_archived else {"status": {"$ne": "archived"}}
        # Superadmin : voit tout
        if role in ("superadmin", "admin"):
            copros = await db.coproprietes.find(q, {"_id": 0}).sort("reference", -1).to_list(1000)
        else:
            # Syndic / gestionnaire / owner : ne voient QUE leurs ACPs (copropriete_ids)
            # + filtre syndic_id pour isolation multi-syndic
            user_copro_ids = user.get("copropriete_ids", []) or []
            if not user_copro_ids:
                return []
            q["id"] = {"$in": user_copro_ids}
            q.update(syndic_query(request))
            copros = await db.coproprietes.find(q, {"_id": 0}).sort("reference", -1).to_list(1000)
        return copros

    @router.post("")
    async def create_copropriete(data: CoproprieteInput, request: Request):
        user = await _get_manager(request)
        reference = await _generate_reference(db)
        bank_accounts = [ba.model_dump() for ba in (data.bank_accounts or [])]
        # iter90jb : normalise l'IBAN a l'entree (source of truth = sans separateur)
        for ba in bank_accounts:
            ba["iban"] = normalize_iban(ba.get("iban"))
        # Dedup apres normalisation (evite doublons "BE04 xxxx" vs "BE04xxxx")
        seen: set[str] = set()
        deduped = []
        for ba in bank_accounts:
            key = ba.get("iban", "")
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(ba)
        bank_accounts = deduped
        # Ensure one default
        if bank_accounts and not any(ba.get("is_default") for ba in bank_accounts):
            for ba in bank_accounts:
                if ba["account_type"] == "vue":
                    ba["is_default"] = True
                    break
        # Add PCMN number to each bank account
        for ba in bank_accounts:
            ba["pcmn_number"] = _generate_pcmn_number(ba["iban"], ba["account_type"])

        doc = {
            "id": str(uuid.uuid4()),
            "reference": reference,
            "name": data.name,
            "bce": data.bce,
            "address": data.address,
            "postal_code": data.postal_code,
            "city": data.city,
            "country": data.country,
            "description": data.description,
            "bank_accounts": bank_accounts,
            "quarterly_closing": data.quarterly_closing,
            "default_provisions": data.default_provisions,
            "promoter_owner_id": data.promoter_owner_id or "",
            "status": "active",
            "created_by": user.get("_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        from syndic_scope import inject_syndic
        inject_syndic(doc, request)
        await db.coproprietes.insert_one(doc)
        # Auto-rattacher l'ACP au syndic createur (sauf superadmin global qui n'a pas
        # besoin d'etre dans copropriete_ids pour voir tout).
        role = user.get("role", "")
        if role not in ("superadmin", "admin"):
            user_id_str = user.get("_id")
            if user_id_str:
                try:
                    await db.users.update_one(
                        {"_id": ObjectId(user_id_str)},
                        {"$addToSet": {"copropriete_ids": doc["id"]}}
                    )
                except Exception as e:
                    # Log mais ne casse pas la creation de l'ACP
                    import logging
                    logging.warning(f"Echec auto-rattachement ACP {doc['id']} au syndic {user_id_str}: {e}")
        # Seed full PCMN plan for this ACP + bank account PCMN entries
        await _seed_pcmn_for_acp(doc["id"], doc.get("syndic_id", ""))
        await _create_pcmn_accounts(bank_accounts, doc["id"])
        # Seed default expense natures (23 standard categories - PCMN belge)
        await _seed_default_expense_natures(doc["id"])
        # Seed default document categories
        from routes.documents import DEFAULT_CATEGORIES
        now_iso = datetime.now(timezone.utc).isoformat()
        _sid = doc.get("syndic_id", "")
        cat_docs = [{
            "id": str(uuid.uuid4()), "name": cn, "description": "",
            "copropriete_id": doc["id"], "created_at": now_iso,
            **( {"syndic_id": _sid} if _sid else {} ),
        } for cn in DEFAULT_CATEGORIES]
        await db.document_categories.insert_many(cat_docs)
        # Create lots on the fly (if provided during ACP creation)
        # iter90gg : 2-pass pour resoudre les parent_lot_number en parent_lot_id
        seen_oids: set = set()
        if data.lots:
            now_iso = datetime.now(timezone.utc).isoformat()
            lot_docs = []
            number_to_id: dict = {}
            for lot in data.lots:
                if not lot.number or not lot.number.strip():
                    continue
                lid = str(uuid.uuid4())
                number_to_id[lot.number.strip()] = lid
                lot_docs.append({
                    "id": lid,
                    "number": lot.number.strip(),
                    "description": lot.description or "",
                    "lot_type": lot.lot_type or "apartment",
                    "floor": lot.floor or 0,
                    "area": lot.area or 0.0,
                    "quotity": lot.quotity or 0.0,
                    "owner_id": (lot.owner_ids or [""])[0],
                    "owner_ids": lot.owner_ids or [],
                    "copropriete_id": doc["id"],
                    "syndic_id": doc.get("syndic_id", ""),
                    "created_at": now_iso,
                    "_parent_ref": (lot.parent_lot_number or "").strip(),
                })
            # 2e passe : resout parent_lot_number -> parent_lot_id
            for ld in lot_docs:
                pref = ld.pop("_parent_ref", "")
                if pref and pref in number_to_id and number_to_id[pref] != ld["id"]:
                    ld["parent_lot_id"] = number_to_id[pref]
            if lot_docs:
                await db.lots.insert_many(lot_docs)
                # iter90kz : rattacher chaque owner unique a l'ACP
                # (copropriete_ids) et creer ses comptes tiers 4100/4101.
                from tier_accounts import assign_owner_accounts
                seen_oids: set = set()
                for ld in lot_docs:
                    oid = ld.get("owner_id", "")
                    if not oid or oid in seen_oids:
                        continue
                    seen_oids.add(oid)
                    owner_doc = await db.owners.find_one({"id": oid}, {"_id": 0})
                    if owner_doc:
                        await assign_owner_accounts(db, owner_doc, doc["id"])

                # iter90kz : mode promoteur - assigner TOUS les lots au promoteur
                # avec start_date = fy_start (1er jour de l'exercice).
                if data.promoter_owner_id:
                    promoter = await db.owners.find_one({"id": data.promoter_owner_id}, {"_id": 0})
                    if promoter:
                        start = data.fy_start or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        await db.lots.update_many(
                            {"copropriete_id": doc["id"]},
                            {"$set": {
                                "owner_id": promoter["id"],
                                "owner_ids": [promoter["id"]],
                                "ownership_history": [{
                                    "owner_id": promoter["id"],
                                    "start_date": start,
                                    "end_date": None,
                                    "type": "promoteur_initial",
                                }],
                            }},
                        )
                        if promoter["id"] not in seen_oids:
                            await assign_owner_accounts(db, promoter, doc["id"])
                            seen_oids.add(promoter["id"])

        # iter90kz : rattacher TOUS les owners transmis par le frontend
        # (y compris ceux sans lot) a l'ACP + creer leurs comptes tiers.
        if data.owner_ids_to_link:
            from tier_accounts import assign_owner_accounts as _assign
            already_linked = seen_oids if data.lots else set()
            for oid in data.owner_ids_to_link:
                if not oid or oid in already_linked:
                    continue
                already_linked.add(oid)
                owner_doc = await db.owners.find_one({"id": oid}, {"_id": 0})
                if owner_doc:
                    await _assign(db, owner_doc, doc["id"])

        # iter90gg : cree un fiscal_year si la periode est fournie
        if data.fy_start and data.fy_end:
            fy_name = data.fy_name.strip() if data.fy_name else ""
            if not fy_name:
                # Auto-derive : "2025-2026" si annees differentes, sinon "2026"
                y1 = data.fy_start[:4]
                y2 = data.fy_end[:4]
                fy_name = f"{y1}-{y2}" if y1 != y2 else y1
            await db.fiscal_years.insert_one({
                "id": str(uuid.uuid4()),
                "copropriete_id": doc["id"],
                "syndic_id": doc.get("syndic_id", ""),
                "name": fy_name,
                "start_date": data.fy_start,
                "end_date": data.fy_end,
                "status": "open",
                "created_at": now_iso,
            })
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/{copro_id}")
    async def update_copropriete(copro_id: str, data: CoproprieteInput, request: Request):
        user = await _get_manager(request)
        from syndic_scope import syndic_query
        sq = syndic_query(request)
        bank_accounts = [ba.model_dump() for ba in (data.bank_accounts or [])]
        # iter90jb : normalise + dedup les IBAN a l'entree
        for ba in bank_accounts:
            ba["iban"] = normalize_iban(ba.get("iban"))
        seen: set[str] = set()
        deduped = []
        for ba in bank_accounts:
            key = ba.get("iban", "")
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(ba)
        bank_accounts = deduped
        for ba in bank_accounts:
            ba["pcmn_number"] = _generate_pcmn_number(ba["iban"], ba["account_type"])
        update = {
            "name": data.name, "bce": data.bce,
            "address": data.address, "postal_code": data.postal_code,
            "city": data.city, "country": data.country,
            "description": data.description,
            "bank_accounts": bank_accounts,
            "quarterly_closing": data.quarterly_closing,
            "default_provisions": data.default_provisions,
        }
        result = await db.coproprietes.update_one({"id": copro_id, **sq}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        await _create_pcmn_accounts(bank_accounts, copro_id)
        return await db.coproprietes.find_one({"id": copro_id}, {"_id": 0})

    @router.get("/{copro_id}")
    async def get_copropriete(copro_id: str, request: Request):
        from syndic_scope import syndic_query
        copro = await db.coproprietes.find_one({"id": copro_id, **syndic_query(request)}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        return copro

    # iter90hz : Lie explicitement le syndic (et donc son logo/mentions
    # legales/config email) a cette ACP. Cela remplace l'heuristique de
    # `resolve_syndic_pdf_context` (fallback organisation / acronyme /
    # domaine email) par un lien base de donnees explicite : la source de
    # verite est desormais `copropriete.syndic_user_id`.
    #
    # Roles autorises :
    # - syndic : lie SA config a l'ACP (syndic_user_id = son propre user._id).
    # - gestionnaire : lie la config du parent_syndic_id.
    # - admin/superadmin : peut lier SA config (utile en mono-cabinet ou pour
    #   les ACPs geres directement par un admin).
    @router.post("/{copro_id}/link-syndic-config")
    async def link_syndic_config(copro_id: str, request: Request):
        from server import get_current_user
        user = await get_current_user(request)
        role = user.get("role", "")
        if role not in ("syndic", "admin", "superadmin", "gestionnaire"):
            raise HTTPException(403, "Seul un syndic peut lier sa config a une ACP")
        # Resoudre le syndic_user_id "proprietaire du cabinet"
        target_syndic_uid = ""
        if role in ("syndic", "admin", "superadmin"):
            target_syndic_uid = str(user.get("_id"))
        elif role == "gestionnaire":
            parent = user.get("parent_syndic_id")
            if not parent:
                raise HTTPException(
                    403,
                    "Ce gestionnaire n'a pas de cabinet syndic parent configure",
                )
            target_syndic_uid = str(parent)
        # Verifier que la config existe (au moins un legal_name OU un logo).
        # Si aucune config n'existe encore, on cree une entree vide pour que
        # le lien soit stable meme avant que le syndic n'ait complete son
        # identite (idempotent).
        cfg = await db.syndic_configs.find_one({"syndic_user_id": target_syndic_uid})
        if not cfg:
            await db.syndic_configs.insert_one({
                "syndic_user_id": target_syndic_uid,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        # Met a jour l'ACP
        result = await db.coproprietes.update_one(
            {"id": copro_id},
            {"$set": {
                "syndic_user_id": target_syndic_uid,
                "syndic_config_linked_at": datetime.now(timezone.utc).isoformat(),
                "syndic_config_linked_by": str(user.get("_id")),
            }},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        # Recharger la config pour informer le client de l'etat resultant
        cfg_after = await db.syndic_configs.find_one(
            {"syndic_user_id": target_syndic_uid}, {"_id": 0, "legal_name": 1, "logo_gridfs_id": 1},
        ) or {}
        return {
            "success": True,
            "copropriete_id": copro_id,
            "syndic_user_id": target_syndic_uid,
            "has_logo": bool(cfg_after.get("logo_gridfs_id")),
            "legal_name": cfg_after.get("legal_name", ""),
        }

    @router.delete("/{copro_id}")
    async def delete_copropriete(copro_id: str, request: Request):
        from server import get_current_user, is_admin_role
        user = await get_current_user(request)
        if not is_admin_role(user.get("role", "")):
            raise HTTPException(403, "Seul le syndic peut supprimer une copropriete")
        result = await db.coproprietes.delete_one({"id": copro_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        # Cascade: wipe all scoped data for this ACP
        for col in ["lots", "tenants", "distribution_keys", "invoices",
                    "journal_entries", "bank_statements", "bank_transactions",
                    "fund_calls", "meters", "documents", "document_categories",
                    "fiscal_years", "budgets", "pcmn_accounts",
                    "expense_categories", "mutations", "regularizations"]:
            await db[col].delete_many({"copropriete_id": copro_id})
        # Step F : cascade deduplication — delier les proprietaires de cette ACP
        # 1) $pull copro_id de owners.copropriete_ids
        await db.owners.update_many(
            {"copropriete_ids": copro_id},
            {"$pull": {"copropriete_ids": copro_id}},
        )
        # 2) $unset tier_accounts pour cette ACP (cle dynamique)
        await db.owners.update_many(
            {f"tier_accounts.{copro_id}": {"$exists": True}},
            {"$unset": {f"tier_accounts.{copro_id}": ""}},
        )
        # 3) Supprimer les fournisseurs ACP-scoped (chaque fournisseur est LOCAL a une ACP)
        await db.suppliers.delete_many({"copropriete_id": copro_id})
        # 4) Marquer les proprietaires orphelins (copropriete_ids vide apres $pull)
        await db.owners.update_many(
            {"copropriete_ids": {"$size": 0}},
            {"$set": {"is_orphan": True}},
        )
        # Aussi marquer les owners qui n'ont plus du tout copropriete_ids
        await db.owners.update_many(
            {"$or": [
                {"copropriete_ids": {"$exists": False}},
                {"copropriete_ids": None},
            ]},
            {"$set": {"is_orphan": True, "copropriete_ids": []}},
        )
        return {"message": "Copropriete supprimee (cascade + deduplication owners/suppliers)"}

    @router.post("/{copro_id}/archive")
    async def archive_copropriete(copro_id: str, request: Request):
        await _get_manager(request)
        result = await db.coproprietes.update_one({"id": copro_id}, {"$set": {"status": "archived"}})
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        return {"message": "Copropriete archivee"}

    @router.post("/{copro_id}/unarchive")
    async def unarchive_copropriete(copro_id: str, request: Request):
        await _get_manager(request)
        result = await db.coproprietes.update_one({"id": copro_id}, {"$set": {"status": "active"}})
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        return {"message": "Copropriete reactivee"}

    @router.post("/{copro_id}/reset-financial-data")
    async def reset_financial_data(copro_id: str, request: Request):
        """Vide TOUTES les donnees comptables/financieres d'une ACP pour repartir
        de zero, en gardant :
        - L'ACP (config, banques, BCE, IBAN)
        - Les lots et quotites
        - Les proprietaires (nom, email, VCS, comptes tiers 40000XXX/40010XXX)
        - Les fournisseurs (nom, comptes tiers 44000XXX)
        - Le PCMN de l'ACP
        - Les cles de repartition

        Supprime :
        - Factures + pieces jointes
        - Ecritures comptables + pieces jointes
        - Appels de fonds
        - Transactions bancaires
        - Budgets, exercices fiscaux, regularisations
        - Natures de depense
        - Documents uploades

        Reserve au SUPERADMIN uniquement (operation destructive).
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Operation reservee au super administrateur")

        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        from pathlib import Path
        deleted_files = 0
        deleted_gridfs = 0
        # iter87 : Cleanup pieces jointes (factures + journal entries)
        # via GridFS (new) + disque (legacy)
        from gridfs_storage import (
            get_invoice_attachments_storage,
            get_journal_attachments_storage,
            get_documents_storage,
        )
        bucket_map = {
            "invoices": get_invoice_attachments_storage(db),
            "journal_entries": get_journal_attachments_storage(db),
        }
        for coll, dirname in [("invoices", "invoice_attachments"),
                              ("journal_entries", "journal_attachments")]:
            storage = bucket_map[coll]
            cursor = db[coll].find(
                {"copropriete_id": copro_id, "attachments": {"$exists": True, "$ne": []}},
                {"_id": 0, "attachments": 1}
            )
            async for doc in cursor:
                for att in doc.get("attachments", []) or []:
                    gid = att.get("gridfs_id")
                    if gid:
                        try:
                            await storage.delete(gid)
                            deleted_gridfs += 1
                        except Exception:
                            pass
                    sp = att.get("stored_path") or ""
                    fp = Path(sp) if sp else Path(f"/app/uploads/{dirname}") / (att.get("stored_filename") or "")
                    if fp.exists() and fp.is_file():
                        try:
                            fp.unlink()
                            deleted_files += 1
                        except Exception:
                            pass
        # Cleanup documents uploades
        docs_storage = get_documents_storage(db)
        cursor = db.documents.find(
            {"copropriete_id": copro_id}, {"_id": 0, "stored_filename": 1, "gridfs_id": 1, "stored_path": 1}
        )
        async for doc in cursor:
            gid = doc.get("gridfs_id")
            if gid:
                try:
                    await docs_storage.delete(gid)
                    deleted_gridfs += 1
                except Exception:
                    pass
            sp = doc.get("stored_path") or ""
            if sp:
                fp = Path(sp)
                if fp.exists() and fp.is_file():
                    try:
                        fp.unlink()
                        deleted_files += 1
                    except Exception:
                        pass
            sf = doc.get("stored_filename") or ""
            if sf:
                for sub in ["/app/uploads/documents", "/app/uploads"]:
                    fp = Path(sub) / sf
                    if fp.exists() and fp.is_file():
                        try:
                            fp.unlink()
                            deleted_files += 1
                        except Exception:
                            pass

        collections_to_clear = [
            "invoices", "journal_entries", "fund_calls", "bank_transactions",
            "bank_statements", "budgets", "fiscal_years", "regularizations",
            "expense_categories", "documents",
        ]
        stats = {"name": copro.get("name", ""),
                 "deleted_files": deleted_files,
                 "deleted_gridfs": deleted_gridfs}
        for coll in collections_to_clear:
            res = await db[coll].delete_many({"copropriete_id": copro_id})
            stats[coll] = res.deleted_count

        return {"status": "ok", "copropriete": copro.get("name", ""), "stats": stats}

    @router.post("/{copro_id}/cleanup-orphan-entries")
    async def cleanup_orphan_entries(copro_id: str, request: Request):
        """Nettoie les ecritures auto-generees orphelines (source supprimee).
        Verifie chaque ecriture auto_generated=true et supprime celles dont :
        - source_type='invoice' mais l'invoice n'existe plus
        - source_type='fund_call' mais le fund_call n'existe plus
        - source_type='bank_txn' mais la bank_transaction n'existe plus

        Retourne le nombre d'ecritures supprimees par categorie.
        Reserve au SUPERADMIN uniquement (operation potentiellement destructive).
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Operation reservee au super administrateur")
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Recupere toutes les ecritures auto de l'ACP
        auto_entries = await db.journal_entries.find(
            {"copropriete_id": copro_id, "auto_generated": True},
            {"_id": 0, "id": 1, "source_type": 1, "source_id": 1, "journal_type": 1}
        ).to_list(100000)

        # Ids reels
        invoice_ids = set()
        async for d in db.invoices.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1}):
            invoice_ids.add(d["id"])
        fund_call_ids = set()
        async for d in db.fund_calls.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1}):
            fund_call_ids.add(d["id"])
        txn_ids = set()
        async for d in db.bank_transactions.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1}):
            txn_ids.add(d["id"])

        stats = {"invoice": 0, "fund_call": 0, "bank_txn": 0, "other": 0}
        to_delete = []
        for e in auto_entries:
            st = e.get("source_type", "")
            sid = e.get("source_id", "")
            if not sid:
                continue
            orphan = False
            if st == "invoice" and sid not in invoice_ids:
                orphan = True
                stats["invoice"] += 1
            elif st == "fund_call" and sid not in fund_call_ids:
                orphan = True
                stats["fund_call"] += 1
            elif st == "bank_txn" and sid not in txn_ids:
                orphan = True
                stats["bank_txn"] += 1
            elif st not in ("invoice", "fund_call", "bank_txn"):
                pass  # ne touche pas aux ecritures sans source connue
            if orphan:
                to_delete.append(e["id"])

        if to_delete:
            await db.journal_entries.delete_many({"id": {"$in": to_delete}})

        total = sum(stats.values())
        return {
            "status": "ok",
            "copropriete": copro.get("name", ""),
            "total_deleted": total,
            "stats": stats,
        }

    @router.post("/{copro_id}/regenerate-bank-entries")
    async def regenerate_bank_entries(copro_id: str, request: Request):
        """Re-genere les ecritures FI auto pour TOUTES les transactions matchees
        d'une ACP. Utile apres correction d'un IBAN/compte bancaire/PCMN.
        Reserve syndic/gestionnaire/admin."""
        await _get_manager(request)
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        from auto_entries import generate_bank_entry
        txns = await db.bank_transactions.find(
            {"copropriete_id": copro_id, "matched": True}, {"_id": 0}
        ).to_list(100000)
        regenerated = 0
        errors = 0
        for t in txns:
            try:
                res = await generate_bank_entry(db, t)
                if res:
                    regenerated += 1
            except Exception:
                errors += 1
        return {"status": "ok", "copropriete": copro.get("name", ""),
                "transactions_processed": len(txns),
                "regenerated": regenerated, "errors": errors}

    @router.post("/{copro_id}/migrate-reserve-to-classe1")
    async def migrate_reserve_to_classe1(copro_id: str, request: Request):
        """Migre les ecritures VE auto-generees pour Fonds de reserve :
        remplace le credit 701000 (classe 7 - faux) par 160 (classe 1 - correct PCMN).
        Idempotent. Ne touche QUE les lignes auto_generated=True, source_type=fund_call,
        non manually_edited, avec compte 701000. Reserve admin/syndic."""
        await _get_manager(request)
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        entries = await db.journal_entries.find({
            "copropriete_id": copro_id,
            "auto_generated": True,
            "source_type": "fund_call",
            "journal_type": "VE",
        }, {"_id": 0}).to_list(100000)
        updated = 0
        lines_changed = 0
        for e in entries:
            if e.get("manually_edited"):
                continue
            new_lines = []
            touched = False
            for ln in e.get("lines", []):
                if ln.get("account_number") == "701000":
                    ln = {**ln, "account_number": "160",
                          "account_name": "Fonds de reserve"}
                    touched = True
                    lines_changed += 1
                new_lines.append(ln)
            if touched:
                await db.journal_entries.update_one(
                    {"id": e["id"]},
                    {"$set": {"lines": new_lines}}
                )
                updated += 1
        return {"status": "ok", "copropriete": copro.get("name", ""),
                "entries_scanned": len(entries),
                "entries_updated": updated,
                "lines_changed": lines_changed}

    return router
