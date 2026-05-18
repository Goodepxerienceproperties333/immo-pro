from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


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


def create_coproprietes_router(db):
    router = APIRouter(prefix="/api/coproprietes")

    async def _get_manager(request):
        from server import get_current_user, can_manage
        user = await get_current_user(request)
        if not can_manage(user.get("role", "")):
            raise HTTPException(403, "Acces refuse")
        return user

    def _generate_pcmn_number(iban: str, account_type: str) -> str:
        """Generate PCMN account number: 551xxx for vue, 550xxx for epargne."""
        clean = iban.replace(" ", "").replace("-", "")
        last3 = clean[-3:] if len(clean) >= 3 else clean.zfill(3)
        prefix = "550" if account_type == "epargne" else "551"
        return f"{prefix}{last3}00"

    # Accounts that are pre-activated (visible in default selection lists)
    DEFAULT_ACTIVE_ACCOUNTS = {"614000", "615000"}  # Honoraires syndic + Frais de gestion (admin)

    async def _seed_pcmn_for_acp(copro_id: str):
        """Seed le PCMN belge complet (327 comptes officiels + 10 comptes compat) pour une nouvelle ACP."""
        from pcmn_data import PCMN_ALL_ACCOUNTS
        docs = []
        for acc in PCMN_ALL_ACCOUNTS:
            docs.append({
                **acc,
                "copropriete_id": copro_id,
                "active": acc["number"] in DEFAULT_ACTIVE_ACCOUNTS,
                "is_custom": False,
            })
        if docs:
            await db.pcmn_accounts.insert_many(docs)

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
        from server import get_current_user, is_admin_role
        user = await get_current_user(request)
        q = {} if show_archived else {"status": {"$ne": "archived"}}
        if is_admin_role(user.get("role", "")):
            copros = await db.coproprietes.find(q, {"_id": 0}).sort("reference", -1).to_list(1000)
        else:
            user_copro_ids = user.get("copropriete_ids", [])
            if user_copro_ids:
                q["id"] = {"$in": user_copro_ids}
            copros = await db.coproprietes.find(q, {"_id": 0}).sort("reference", -1).to_list(1000)
        return copros

    @router.post("")
    async def create_copropriete(data: CoproprieteInput, request: Request):
        user = await _get_manager(request)
        reference = await _generate_reference(db)
        bank_accounts = [ba.model_dump() for ba in (data.bank_accounts or [])]
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
            "status": "active",
            "created_by": user.get("_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.coproprietes.insert_one(doc)
        # Seed full PCMN plan for this ACP + bank account PCMN entries
        await _seed_pcmn_for_acp(doc["id"])
        await _create_pcmn_accounts(bank_accounts, doc["id"])
        # Seed default document categories
        from routes.documents import DEFAULT_CATEGORIES
        now_iso = datetime.now(timezone.utc).isoformat()
        cat_docs = [{
            "id": str(uuid.uuid4()), "name": cn, "description": "",
            "copropriete_id": doc["id"], "created_at": now_iso,
        } for cn in DEFAULT_CATEGORIES]
        await db.document_categories.insert_many(cat_docs)
        # Create lots on the fly (if provided during ACP creation)
        if data.lots:
            now_iso = datetime.now(timezone.utc).isoformat()
            lot_docs = []
            for lot in data.lots:
                if not lot.number or not lot.number.strip():
                    continue
                lot_docs.append({
                    "id": str(uuid.uuid4()),
                    "number": lot.number.strip(),
                    "description": lot.description or "",
                    "lot_type": lot.lot_type or "apartment",
                    "floor": lot.floor or 0,
                    "area": lot.area or 0.0,
                    "quotity": lot.quotity or 0.0,
                    "owner_id": (lot.owner_ids or [""])[0],
                    "owner_ids": lot.owner_ids or [],
                    "copropriete_id": doc["id"],
                    "created_at": now_iso,
                })
            if lot_docs:
                await db.lots.insert_many(lot_docs)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/{copro_id}")
    async def update_copropriete(copro_id: str, data: CoproprieteInput, request: Request):
        await _get_manager(request)
        bank_accounts = [ba.model_dump() for ba in (data.bank_accounts or [])]
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
        result = await db.coproprietes.update_one({"id": copro_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Copropriete non trouvee")
        await _create_pcmn_accounts(bank_accounts, copro_id)
        return await db.coproprietes.find_one({"id": copro_id}, {"_id": 0})

    @router.get("/{copro_id}")
    async def get_copropriete(copro_id: str, request: Request):
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        return copro

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
                    "fiscal_years", "budgets", "pcmn_accounts"]:
            await db[col].delete_many({"copropriete_id": copro_id})
        return {"message": "Copropriete supprimee (cascade)"}

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

        Accessible aux syndic/superadmin/gestionnaire.
        """
        await _get_manager(request)

        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "name": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        from pathlib import Path
        deleted_files = 0
        # Cleanup pieces jointes disque (factures + journal entries)
        for coll, dirname in [("invoices", "invoice_attachments"),
                              ("journal_entries", "journal_attachments")]:
            cursor = db[coll].find(
                {"copropriete_id": copro_id, "attachments": {"$exists": True, "$ne": []}},
                {"_id": 0, "attachments": 1}
            )
            async for doc in cursor:
                for att in doc.get("attachments", []) or []:
                    fp = Path(f"/app/uploads/{dirname}") / (att.get("stored_filename") or "")
                    if fp.exists() and fp.is_file():
                        try:
                            fp.unlink()
                            deleted_files += 1
                        except Exception:
                            pass
        # Cleanup documents uploades
        cursor = db.documents.find({"copropriete_id": copro_id}, {"_id": 0, "stored_filename": 1})
        async for doc in cursor:
            sf = doc.get("stored_filename") or ""
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
            "budgets", "fiscal_years", "regularizations", "expense_categories",
            "documents",
        ]
        stats = {"name": copro.get("name", ""), "deleted_files": deleted_files}
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
        Reserve aux syndic/gestionnaire/admin.
        """
        await _get_manager(request)
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

    return router
