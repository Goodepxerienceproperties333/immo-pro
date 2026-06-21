"""Import Wizard routes - migration Optipro/Sogis -> CoproManager.

Session-based, with rollback support (every imported doc carries `import_session_id`).
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from import_wizard.csv_utils import sniff_csv, parse_french_number, parse_date, split_optipro_code, normalize_header, parse_invoices_csv, parse_journals_csv
from import_wizard.pdf_utils import extract_pdf, parse_natures_pdf, parse_budget_pdf, parse_distribution_keys_pdf, parse_owners_pdf, parse_lots_pdf, parse_suppliers_pdf, parse_balance_pdf

logger = logging.getLogger("import_wizard")


# ---- Helpers ----
def _is_super(role: str) -> bool:
    return role in ("superadmin", "admin")


async def _require_acp_access(request, db, copropriete_id: str):
    """Verifies the connected user has access to this ACP (chinese wall).
    Returns the user document."""
    from server import get_current_user
    user = await get_current_user(request)
    role = user.get("role", "")
    if _is_super(role):
        return user
    user_copros = user.get("copropriete_ids", []) or []
    if copropriete_id not in user_copros:
        raise HTTPException(403, "Acces refuse a cette copropriete (chinese wall)")
    return user


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Pydantic ----
class CreateSessionInput(BaseModel):
    copropriete_id: str
    source_system: Optional[str] = "Optipro"


class CommitOwnersInput(BaseModel):
    mapping: dict  # { 'last_name': 'col_idx', 'first_name': 'col_idx', ... }
    rows: List[List[Optional[str]]]  # raw rows to import (pre-validated by user)


class CommitSuppliersInput(BaseModel):
    mapping: dict
    rows: List[List[Optional[str]]]


class CommitLotsInput(BaseModel):
    mapping: dict
    rows: List[List[Optional[str]]]


class CommitNaturesInput(BaseModel):
    natures: List[dict]  # parsed natures (from PDF) confirmed by user


class CommitFiscalYearInput(BaseModel):
    name: str
    start_date: str  # ISO YYYY-MM-DD
    end_date: str
    status: Optional[str] = "open"


class CommitBudgetInput(BaseModel):
    fiscal_year_id: str
    sections: List[dict]  # parsed budget sections, possibly edited


class CommitDistributionKeysInput(BaseModel):
    keys: List[dict]  # parsed keys with their lines


class CommitSuppliersPdfInput(BaseModel):
    suppliers: List[dict]  # parsed suppliers (from PDF) confirmed by user


class CommitInvoicesInput(BaseModel):
    invoices: List[dict]  # parsed invoices confirmed by user


class CommitJournalsInput(BaseModel):
    transactions: List[dict]  # parsed journal transactions confirmed by user
    bank_account_mapping: Optional[dict] = {}  # {'550472': 'bank_account_id_in_DB'}


class CommitOpeningBalanceInput(BaseModel):
    actif: List[dict]  # [{account, label, amount, is_subaccount}]
    passif: List[dict]
    period_end_date: Optional[str] = ""  # DD/MM/YYYY of the balance sheet
    fiscal_year_id: Optional[str] = ""  # FY into which to post the AN entry


# ============================================================
# Router
# ============================================================
def create_import_wizard_router(db):
    router = APIRouter(prefix="/api/import-wizard")

    # ----- STANDALONE PDF PARSE (no session required) -----
    @router.post("/parse-pdf")
    async def parse_pdf_standalone(request: Request, file: UploadFile = File(...), kind: str = Form("generic")):
        """Parse a PDF without needing an active session.
        Used from the ACP creation wizard (where the ACP doesn't exist yet).
        Returns parsed structured data, the caller is responsible for committing.
        """
        from server import get_current_user
        await get_current_user(request)  # ensure logged in
        raw = await file.read()
        if len(raw) > 30 * 1024 * 1024:
            raise HTTPException(400, "Fichier trop volumineux (max 30 Mo)")
        if kind == "natures":
            return parse_natures_pdf(raw)
        if kind == "budget":
            return parse_budget_pdf(raw)
        if kind == "keys":
            return parse_distribution_keys_pdf(raw)
        if kind == "owners":
            return parse_owners_pdf(raw)
        if kind == "lots":
            return parse_lots_pdf(raw)
        if kind == "suppliers":
            return parse_suppliers_pdf(raw)
        if kind == "balance":
            return parse_balance_pdf(raw)
        return extract_pdf(raw)

    # ----- SESSIONS -----
    @router.post("/sessions")
    async def create_session(data: CreateSessionInput, request: Request):
        user = await _require_acp_access(request, db, data.copropriete_id)
        # Check there is not already an active session for this ACP
        existing = await db.import_sessions.find_one({
            "copropriete_id": data.copropriete_id,
            "status": "active",
        }, {"_id": 0})
        if existing:
            return existing
        session = {
            "id": str(uuid.uuid4()),
            "copropriete_id": data.copropriete_id,
            "syndic_id": str(user.get("_id", "")),
            "source_system": data.source_system or "Optipro",
            "status": "active",
            "steps": {},  # filled progressively
            "created_at": _now_iso(),
        }
        await db.import_sessions.insert_one(session)
        # Remove any ObjectId left by Mongo
        session.pop("_id", None)
        return session

    @router.get("/sessions/active")
    async def get_active_session(copropriete_id: str, request: Request):
        await _require_acp_access(request, db, copropriete_id)
        session = await db.import_sessions.find_one({
            "copropriete_id": copropriete_id, "status": "active"
        }, {"_id": 0})
        return session

    @router.delete("/sessions/{session_id}")
    async def rollback_session(session_id: str, request: Request):
        """Rollback : delete every document tagged with this import_session_id."""
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        await _require_acp_access(request, db, session["copropriete_id"])
        collections_to_clean = ["owners", "suppliers", "tenants", "lots",
                                "expense_categories", "distribution_keys",
                                "fiscal_years", "budgets", "invoices",
                                "journal_entries", "bank_statements", "bank_transactions",
                                "bank_statement_lines", "pcmn_accounts"]
        report = {}
        for col in collections_to_clean:
            try:
                res = await db[col].delete_many({"import_session_id": session_id})
                if res.deleted_count > 0:
                    report[col] = res.deleted_count
            except Exception as e:
                logger.warning(f"Rollback {col} failed: {e}")
        await db.import_sessions.update_one(
            {"id": session_id},
            {"$set": {"status": "cancelled", "cancelled_at": _now_iso(), "rollback_report": report}}
        )
        return {"status": "rolled_back", "report": report}

    @router.post("/sessions/{session_id}/finish")
    async def finish_session(session_id: str, request: Request):
        """Mark session as committed (final lock). After this, rollback is no longer guaranteed."""
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        await _require_acp_access(request, db, session["copropriete_id"])
        await db.import_sessions.update_one(
            {"id": session_id}, {"$set": {"status": "committed", "committed_at": _now_iso()}}
        )
        return {"status": "ok"}

    # ----- SNIFF (preview) -----
    @router.post("/sessions/{session_id}/sniff-csv")
    async def sniff_csv_file(session_id: str, request: Request, file: UploadFile = File(...), kind: str = Form("generic")):
        """Inspect a CSV. For `kind=invoices` / `kind=journals` : returns the
        structured parsed data directly (no manual mapping needed - Optipro
        format is known). Otherwise : returns generic sniff (headers + preview).
        """
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        await _require_acp_access(request, db, session["copropriete_id"])
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(400, "Fichier trop volumineux (max 20 Mo)")
        if kind == "invoices":
            res = parse_invoices_csv(raw)
            res["filename"] = file.filename
            return res
        if kind == "journals":
            res = parse_journals_csv(raw)
            res["filename"] = file.filename
            return res
        info = sniff_csv(raw)
        info["filename"] = file.filename
        return info

    @router.post("/sessions/{session_id}/sniff-pdf")
    async def sniff_pdf_file(session_id: str, request: Request, file: UploadFile = File(...), kind: str = Form("generic")):
        """Inspect a PDF. For natures : returns parsed natures list."""
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        await _require_acp_access(request, db, session["copropriete_id"])
        raw = await file.read()
        if len(raw) > 30 * 1024 * 1024:
            raise HTTPException(400, "Fichier trop volumineux (max 30 Mo)")
        if kind == "natures":
            res = parse_natures_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "budget":
            res = parse_budget_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "keys":
            res = parse_distribution_keys_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "owners":
            res = parse_owners_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "lots":
            res = parse_lots_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "suppliers":
            res = parse_suppliers_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "balance":
            res = parse_balance_pdf(raw)
            res["filename"] = file.filename
            return res
        info = extract_pdf(raw)
        info["filename"] = file.filename
        return info

    # ----- A: OWNERS -----
    @router.post("/sessions/{session_id}/commit-owners")
    async def commit_owners(session_id: str, data: CommitOwnersInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        m = data.mapping
        required = ["last_name"]
        for k in required:
            if k not in m or m[k] in ("", None):
                raise HTTPException(400, f"Mapping requis pour '{k}'")
        inserted = 0
        errors = []
        for idx, row in enumerate(data.rows):
            try:
                def col(key):
                    i = m.get(key)
                    if i is None or i == "" or int(i) >= len(row):
                        return ""
                    return (row[int(i)] or "").strip()
                last_name = col("last_name")
                if not last_name:
                    continue
                first_name = col("first_name")
                full = (f"{last_name} {first_name}".strip()) if first_name else last_name
                doc = {
                    "id": str(uuid.uuid4()),
                    "first_name": first_name,
                    "last_name": last_name,
                    "name": full,
                    "address": col("address"),
                    "postal_code": col("postal_code"),
                    "city": col("city"),
                    "country": col("country") or "Belgique",
                    "email": col("email"),
                    "phone": col("phone"),
                    "iban": col("iban"),
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.owners.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "owners", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

    # ----- C: SUPPLIERS -----
    @router.post("/sessions/{session_id}/commit-suppliers")
    async def commit_suppliers(session_id: str, data: CommitSuppliersInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        m = data.mapping
        if "name" not in m or m["name"] in ("", None):
            raise HTTPException(400, "Mapping requis pour 'name'")
        inserted = 0
        errors = []
        for idx, row in enumerate(data.rows):
            try:
                def col(key):
                    i = m.get(key)
                    if i is None or i == "" or int(i) >= len(row):
                        return ""
                    return (row[int(i)] or "").strip()
                name = col("name")
                if not name:
                    continue
                doc = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "vat_number": col("vat_number"),
                    "bce_number": col("bce_number"),
                    "address": col("address"),
                    "postal_code": col("postal_code"),
                    "city": col("city"),
                    "country": col("country") or "Belgique",
                    "phone": col("phone"),
                    "email": col("email"),
                    "iban": col("iban"),
                    "bic": col("bic"),
                    "default_account": col("default_account"),
                    "notes": col("notes"),
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.suppliers.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "suppliers", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

    # ----- C-bis: SUPPLIERS via PDF (no mapping needed - already structured) -----
    @router.post("/sessions/{session_id}/commit-suppliers-pdf")
    async def commit_suppliers_pdf(session_id: str, data: CommitSuppliersPdfInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        inserted = 0
        errors = []
        for idx, s in enumerate(data.suppliers):
            try:
                name = (s.get("name") or "").strip()
                if not name:
                    continue
                doc = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "auxiliary_code": (s.get("auxiliary_code") or "").strip(),
                    "vat_number": "",
                    "bce_number": "",
                    "address": (s.get("address") or "").strip(),
                    "postal_code": (s.get("postal_code") or "").strip(),
                    "city": (s.get("city") or "").strip(),
                    "country": (s.get("country") or "Belgique").strip(),
                    "phone": (s.get("phone") or "").strip(),
                    "email": (s.get("email") or "").strip(),
                    "iban": "",
                    "bic": "",
                    "default_account": "",
                    "is_default": bool(s.get("is_default")),
                    "notes": "",
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.suppliers.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "suppliers", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

    # ----- G: INVOICES (factures) - CSV Optipro -----
    async def _ensure_pcmn_accounts(copro_id: str, accounts_needed: dict[str, str]) -> int:
        """Ensure each (account_number -> account_name) exists in this ACP's PCMN.

        Returns the count of newly-created accounts.
        """
        if not accounts_needed:
            return 0
        existing = set()
        async for p in db.pcmn_accounts.find(
            {"copropriete_id": copro_id, "number": {"$in": list(accounts_needed.keys())}},
            {"_id": 0, "number": 1}
        ):
            existing.add(p["number"])
        missing = {n: lbl for n, lbl in accounts_needed.items() if n not in existing}
        created = 0
        for num, lbl in missing.items():
            try:
                class_num = int(num[0]) if num and num[0].isdigit() else 0
            except (ValueError, IndexError):
                class_num = 0
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()),
                "number": num,
                "name": lbl or num,
                "class_num": class_num,
                "parent": "",
                "is_system": False,
                "is_imported": True,
                "copropriete_id": copro_id,
                "created_at": _now_iso(),
            })
            created += 1
        return created

    @router.post("/sessions/{session_id}/commit-invoices")
    async def commit_invoices(session_id: str, data: CommitInvoicesInput, request: Request):
        """Commit pre-parsed invoices. Each invoice is auto-matched to:
        - supplier (via F0XXX auxiliary_code)
        - distribution_key (via key code from 'Cle' column)
        - expense_category (via Nature code or account_number)

        Side effects:
        - Missing PCMN accounts (charge 61xxx + supplier sub-account 4400xxx)
          are auto-created in the ACP's chart of accounts.
        - A journal entry of type 'AC' (Achats) is created for each invoice
          with double-entry: DEBIT charge / CREDIT supplier.
        - Invoice status is set to 'unpaid' (validated, awaiting payment).
        """
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        # Build matching lookups
        sup_by_aux: dict[str, dict] = {}
        async for s in db.suppliers.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "auxiliary_code": 1, "name": 1}):
            ax = (s.get("auxiliary_code") or "").upper().strip()
            if ax:
                sup_by_aux[ax] = s
        keys_by_code: dict[str, str] = {}
        async for k in db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "code": 1, "import_code": 1}):
            for fld in ("code", "import_code"):
                v = (k.get(fld) or "").strip()
                if v:
                    keys_by_code[v.zfill(4)] = k["id"]
                    keys_by_code[v] = k["id"]
        cats_by_code: dict[str, str] = {}
        cats_by_account: dict[str, str] = {}
        async for c in db.expense_categories.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "code": 1, "account_number": 1}):
            v = (c.get("code") or "").strip()
            if v:
                cats_by_code[v.zfill(4)] = c["id"]
                cats_by_code[v] = c["id"]
            a = (c.get("account_number") or "").strip()
            if a:
                cats_by_account[a] = c["id"]

        # ---- Pre-pass : collect all PCMN accounts that will be needed ----
        accounts_needed: dict[str, str] = {}
        for inv in data.invoices:
            acc_num = (inv.get("account_number") or "").strip()
            acc_lbl = (inv.get("account_label") or "").strip()
            if acc_num:
                accounts_needed[acc_num] = acc_lbl
            sup_aux = (inv.get("supplier_aux_code") or "").upper().strip()
            if sup_aux.startswith("F") and len(sup_aux) >= 5:
                # PCMN supplier sub-account: 4400 + last 3 digits of F-code
                # F0471 -> 4400471
                sup_pcmn = "4400" + sup_aux[1:].zfill(3)
                sup_lbl = (inv.get("supplier_name") or "").strip() or sup_aux
                accounts_needed[sup_pcmn] = sup_lbl
        pcmn_created = await _ensure_pcmn_accounts(copro_id, accounts_needed)

        year_counters: dict[str, int] = {}

        async def next_internal_ref(year: str) -> str:
            prefix = f"FA-{year}-"
            if year not in year_counters:
                year_counters[year] = await db.invoices.count_documents({
                    "copropriete_id": copro_id,
                    "internal_reference": {"$regex": f"^{prefix}"},
                })
            year_counters[year] += 1
            return f"{prefix}{year_counters[year]:04d}"

        inserted = 0
        je_inserted = 0
        errors = []
        matched_supplier = 0
        matched_key = 0
        matched_category = 0
        for idx, inv in enumerate(data.invoices):
            try:
                date_str = (inv.get("date") or "").strip()
                if not date_str:
                    errors.append({"row": idx, "error": "Date facture manquante"})
                    continue
                try:
                    await ensure_period_open(db, copro_id, date_str, context="facture (import)")
                except Exception as e:
                    errors.append({"row": idx, "error": f"Periode fermee : {e}"})
                    continue

                supplier_aux = (inv.get("supplier_aux_code") or "").upper().strip()
                supplier_doc = sup_by_aux.get(supplier_aux) if supplier_aux else None
                supplier_id = supplier_doc["id"] if supplier_doc else None
                if supplier_id:
                    matched_supplier += 1

                key_code = (inv.get("dist_key_code") or "").strip()
                dist_key_id = ""
                if key_code:
                    dist_key_id = keys_by_code.get(key_code) or keys_by_code.get(key_code.zfill(4)) or ""
                    if dist_key_id:
                        matched_key += 1

                nature_code = (inv.get("nature_code") or "").strip()
                account_num = (inv.get("account_number") or "").strip()
                expense_cat_id = ""
                if nature_code:
                    expense_cat_id = cats_by_code.get(nature_code) or cats_by_code.get(nature_code.zfill(4)) or ""
                if not expense_cat_id and account_num:
                    expense_cat_id = cats_by_account.get(account_num) or ""
                if expense_cat_id:
                    matched_category += 1

                total_amount = float(inv.get("montant_tvac") or 0)
                vat_amount = float(inv.get("montant_tva") or 0)
                occ_pct = float(inv.get("part_occupant") or 0)
                prop_pct = float(inv.get("part_proprietaire") or 0)
                if occ_pct == 0 and prop_pct == 0:
                    occ_pct = 100.0
                occ_pct = max(0.0, min(100.0, occ_pct))
                prop_pct = round(100.0 - occ_pct, 2)

                year = date_str[:4]
                internal_ref = await next_internal_ref(year)
                invoice_id = str(uuid.uuid4())

                # Compute supplier PCMN sub-account for the journal entry
                sup_pcmn = ""
                if supplier_aux.startswith("F") and len(supplier_aux) >= 5:
                    sup_pcmn = "4400" + supplier_aux[1:].zfill(3)
                supplier_label = (inv.get("supplier_name") or supplier_aux).strip()

                # ---- Create journal entry (Achats - AC) ----
                je_id = ""
                if account_num and sup_pcmn and total_amount > 0:
                    je_id = str(uuid.uuid4())
                    je_doc = {
                        "id": je_id,
                        "journal_type": "AC",
                        "date": date_str,
                        "reference": internal_ref,
                        "description": f"{supplier_label} - {(inv.get('libelle') or '').strip()}".strip(" -"),
                        "lines": [
                            {
                                "account_number": account_num,
                                "account_name": (inv.get("account_label") or "").strip(),
                                "debit": total_amount,
                                "credit": 0.0,
                                "description": (inv.get("libelle") or "").strip(),
                                "occupant_pct": occ_pct,
                                "proprietaire_pct": prop_pct,
                            },
                            {
                                "account_number": sup_pcmn,
                                "account_name": supplier_label,
                                "debit": 0.0,
                                "credit": total_amount,
                                "description": f"DA {internal_ref}",
                                "occupant_pct": None,
                                "proprietaire_pct": None,
                            },
                        ],
                        "total_debit": total_amount,
                        "total_credit": total_amount,
                        "copropriete_id": copro_id,
                        "import_session_id": session_id,
                        "source_invoice_id": invoice_id,
                        "created_at": _now_iso(),
                    }
                    await db.journal_entries.insert_one(je_doc)
                    je_inserted += 1

                doc = {
                    "id": invoice_id,
                    "number": (inv.get("external_ref") or internal_ref).strip(),
                    "internal_reference": internal_ref,
                    "date": date_str,
                    "due_date": (inv.get("due_date") or "").strip(),
                    "supplier": supplier_label,
                    "supplier_id": supplier_id or "",
                    "description": (inv.get("libelle") or "").strip(),
                    "total_amount": total_amount,
                    "vat_amount": vat_amount,
                    "account_number": account_num,
                    "expense_category_id": expense_cat_id,
                    "distribution_key_id": dist_key_id,
                    "distribution_lines": [],
                    "status": "do_not_pay" if inv.get("ne_pas_payer") else "unpaid",
                    "copropriete_id": copro_id,
                    "is_private_fee": False,
                    "private_fee_owner_id": "",
                    "occupant_pct": occ_pct,
                    "proprietaire_pct": prop_pct,
                    "occupant_amount": round(total_amount * occ_pct / 100, 2),
                    "proprietaire_amount": round(total_amount * prop_pct / 100, 2),
                    "vat_code": (inv.get("vat_code") or "").strip(),
                    "supplier_aux_code": supplier_aux,
                    "journal_entry_id": je_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.invoices.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})

        await _update_step(db, session_id, "invoices", {
            "count": inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "errors": errors,
            "matched_supplier": matched_supplier,
            "matched_key": matched_key,
            "matched_category": matched_category,
        })
        return {
            "inserted": inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "errors": errors,
            "matched_supplier": matched_supplier,
            "matched_key": matched_key,
            "matched_category": matched_category,
        }

    # ----- H: JOURNALS (extraits bancaires) - CSV Optipro -----
    @router.post("/sessions/{session_id}/commit-journals")
    async def commit_journals(session_id: str, data: CommitJournalsInput, request: Request):
        """Commit pre-parsed bank journal transactions.

        Side effects:
        - Missing PCMN accounts (bank 55x/57x + counterparty) auto-created.
        - A journal entry of type 'FI' (Financier) is created for each
          transaction with double-entry.
        - bank_statements + bank_transactions ALSO created (grouped per bank
          + month) so the user can see the imported movements directly in
          the Banking interface (/banking).
        - bank_statement_lines kept too for traceability/audit.
        """
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        bank_lookup: dict[str, str] = dict(data.bank_account_mapping or {})
        # Also build PCMN -> IBAN map (needed for the bank_statements.account_number)
        pcmn_to_iban: dict[str, str] = {}
        async for ba in db.bank_accounts.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "account_number": 1, "pcmn_account": 1, "iban": 1}):
            for fld in ("account_number", "pcmn_account"):
                v = (ba.get(fld) or "").strip()
                if v and v not in bank_lookup:
                    bank_lookup[v] = ba["id"]
            pcmn_v = (ba.get("pcmn_account") or "").strip()
            iban_v = (ba.get("iban") or ba.get("account_number") or "").strip()
            if pcmn_v and iban_v:
                pcmn_to_iban[pcmn_v] = iban_v

        # Pre-pass: collect PCMN accounts needed
        accounts_needed: dict[str, str] = {}
        for t in data.transactions:
            bp = (t.get("bank_account") or "").strip()
            bl = (t.get("bank_account_label") or "").strip()
            if bp:
                accounts_needed[bp] = bl
            cp = (t.get("counterparty_account") or "").strip()
            cl = (t.get("counterparty_account_label") or "").strip()
            if cp:
                accounts_needed[cp] = cl
        pcmn_created = await _ensure_pcmn_accounts(copro_id, accounts_needed)

        # ---- Group transactions by (bank_pcmn, year-month) to build statements ----
        # bank_pcmn -> month_key (YYYY-MM) -> {first_date, last_date, txns: [...]}
        groups: dict[tuple[str, str], dict] = {}
        for t in data.transactions:
            bp = (t.get("bank_account") or "").strip()
            date_v = (t.get("date_value") or "").strip()
            if not bp or not date_v or len(date_v) < 7:
                continue
            month_key = date_v[:7]  # YYYY-MM
            key = (bp, month_key)
            g = groups.setdefault(key, {"first_date": date_v, "last_date": date_v, "label": (t.get("bank_account_label") or "").strip(), "txns": []})
            if date_v < g["first_date"]:
                g["first_date"] = date_v
            if date_v > g["last_date"]:
                g["last_date"] = date_v
            g["txns"].append(t)

        # Create one bank_statement per group, then transactions inside
        # statement_by_key : (bank_pcmn, month_key) -> statement_id
        statement_by_key: dict[tuple[str, str], str] = {}
        stmts_inserted = 0
        for (bp, month_key), g in groups.items():
            stmt_id = str(uuid.uuid4())
            stmt_doc = {
                "id": stmt_id,
                "number": f"IMP-{month_key}-{bp}",
                "date": g["last_date"],
                "account_number": pcmn_to_iban.get(bp, bp),
                "opening_balance": 0.0,
                "opening_balance_source": "import",
                "closing_balance": 0.0,
                "copropriete_id": copro_id,
                "status": "draft",
                "imported_label": g["label"] or f"Import journaux {month_key}",
                "import_session_id": session_id,
                "created_at": _now_iso(),
            }
            await db.bank_statements.insert_one(stmt_doc)
            statement_by_key[(bp, month_key)] = stmt_id
            stmts_inserted += 1

        inserted = 0
        je_inserted = 0
        errors = []
        for idx, t in enumerate(data.transactions):
            try:
                date_v = (t.get("date_value") or "").strip()
                if not date_v:
                    errors.append({"row": idx, "error": "Date valeur manquante"})
                    continue
                try:
                    await ensure_period_open(db, copro_id, date_v, context="releve bancaire (import)")
                except Exception as e:
                    errors.append({"row": idx, "error": f"Periode fermee : {e}"})
                    continue
                bank_pcmn = (t.get("bank_account") or "").strip()
                bank_id = bank_lookup.get(bank_pcmn) if bank_pcmn else None
                cp_pcmn = (t.get("counterparty_account") or "").strip()
                bank_label = (t.get("bank_account_label") or "").strip()
                cp_label = (t.get("counterparty_account_label") or "").strip()
                amount = float(t.get("amount") or 0)
                direction = (t.get("direction") or "").strip()
                libelle = (t.get("libelle") or "").strip()
                num_doc = (t.get("num_doc") or "").strip()

                # ---- Create journal entry (Financier - FI) ----
                je_id = ""
                if bank_pcmn and cp_pcmn and amount > 0 and direction in ("in", "out"):
                    je_id = str(uuid.uuid4())
                    if direction == "in":
                        lines = [
                            {"account_number": bank_pcmn, "account_name": bank_label, "debit": amount, "credit": 0.0,
                             "description": libelle, "occupant_pct": None, "proprietaire_pct": None},
                            {"account_number": cp_pcmn, "account_name": cp_label, "debit": 0.0, "credit": amount,
                             "description": libelle, "occupant_pct": None, "proprietaire_pct": None},
                        ]
                    else:  # out
                        lines = [
                            {"account_number": cp_pcmn, "account_name": cp_label, "debit": amount, "credit": 0.0,
                             "description": libelle, "occupant_pct": None, "proprietaire_pct": None},
                            {"account_number": bank_pcmn, "account_name": bank_label, "debit": 0.0, "credit": amount,
                             "description": libelle, "occupant_pct": None, "proprietaire_pct": None},
                        ]
                    await db.journal_entries.insert_one({
                        "id": je_id,
                        "journal_type": "FI",
                        "date": date_v,
                        "reference": num_doc,
                        "description": libelle,
                        "lines": lines,
                        "total_debit": amount,
                        "total_credit": amount,
                        "copropriete_id": copro_id,
                        "import_session_id": session_id,
                        "created_at": _now_iso(),
                    })
                    je_inserted += 1

                # ---- Create bank_transaction inside its monthly statement ----
                stmt_id = statement_by_key.get((bank_pcmn, date_v[:7])) if bank_pcmn else ""
                if stmt_id:
                    # Signed amount : IN = positive, OUT = negative (CoproManager convention)
                    signed_amount = amount if direction == "in" else (-amount if direction == "out" else amount)
                    txn_doc = {
                        "id": str(uuid.uuid4()),
                        "statement_id": stmt_id,
                        "copropriete_id": copro_id,
                        "date": date_v,
                        "amount": signed_amount,
                        "counterparty_name": cp_label,
                        "counterparty_account": "",  # IBAN of counterparty (unknown from journal)
                        "communication": libelle,
                        "transaction_type": "credit" if direction == "in" else "debit",
                        "account_number": bank_pcmn,
                        "matched": False,
                        "matched_type": "",
                        "matched_id": "",
                        "auto_je_id": je_id,
                        "import_session_id": session_id,
                        "created_at": _now_iso(),
                    }
                    await db.bank_transactions.insert_one(txn_doc)

                # ---- Bank statement line (audit copy) ----
                doc = {
                    "id": str(uuid.uuid4()),
                    "copropriete_id": copro_id,
                    "bank_account_id": bank_id or "",
                    "bank_pcmn_code": bank_pcmn,
                    "bank_pcmn_label": bank_label,
                    "counterparty_account": cp_pcmn,
                    "counterparty_account_label": cp_label,
                    "num_doc": num_doc,
                    "code_journal": (t.get("code_journal") or "FIN").strip(),
                    "date_value": date_v,
                    "date_compta": (t.get("date_compta") or date_v).strip(),
                    "libelle": libelle,
                    "ext_reference": (t.get("ext_reference") or "").strip(),
                    "amount": amount,
                    "direction": direction,
                    "status": "imported",
                    "journal_entry_id": je_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.bank_statement_lines.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})

        # ---- Update statements' opening / closing balances based on inserted txns ----
        for (bp, month_key), stmt_id in statement_by_key.items():
            txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0, "amount": 1}).to_list(10000)
            net = round(sum(float(t.get("amount", 0) or 0) for t in txns), 2)
            await db.bank_statements.update_one(
                {"id": stmt_id},
                {"$set": {"closing_balance": net, "txn_count": len(txns)}},
            )

        await _update_step(db, session_id, "journals", {
            "count": inserted,
            "statements_created": stmts_inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "errors": errors,
        })
        return {
            "inserted": inserted,
            "statements_created": stmts_inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "errors": errors,
        }

    # ----- I: OPENING BALANCE (OD d'ouverture - Bilan comptable) -----
    @router.post("/sessions/{session_id}/commit-opening-balance")
    async def commit_opening_balance(session_id: str, data: CommitOpeningBalanceInput, request: Request):
        """Commit an opening balance from a 'Bilan comptable' PDF.

        Generates ONE journal entry of type 'AN' (A-Nouveau) with:
        - DEBIT lines for each ACTIF account
        - CREDIT lines for each PASSIF account
        Total debit = Total credit by construction (balance sheet equilibrium).

        Also auto-creates missing PCMN accounts and sets the entry date to
        the FIRST DAY of the current fiscal year (or 1st January of the year
        following the balance period_end_date).
        """
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        actif_raw = data.actif or []
        passif_raw = data.passif or []

        # Filter to LEAF accounts only: skip parent accounts when they have
        # sub-accounts (otherwise the parent + children would double-count).
        # A leaf account = either is_subaccount=True OR a main account whose
        # account_number prefix is not shared by any sub-account on the same side.
        def _filter_leaves(rows: list) -> list:
            sub_codes = [str(r.get("account") or "") for r in rows if r.get("is_subaccount")]
            leaves = []
            for r in rows:
                if r.get("is_subaccount"):
                    leaves.append(r)
                    continue
                code = str(r.get("account") or "")
                # main account is a leaf only if no sub-account starts with this code
                has_children = any(sc.startswith(code) and len(sc) > len(code) for sc in sub_codes)
                if not has_children:
                    leaves.append(r)
            return leaves

        actif = _filter_leaves(actif_raw)
        passif = _filter_leaves(passif_raw)
        total_actif = round(sum(float(a.get("amount") or 0) for a in actif), 2)
        total_passif = round(sum(float(p.get("amount") or 0) for p in passif), 2)
        if abs(total_actif - total_passif) > 0.01:
            raise HTTPException(400, f"Bilan non equilibre : Actif {total_actif} != Passif {total_passif}")
        if total_actif == 0:
            raise HTTPException(400, "Bilan vide (aucun montant a importer)")

        # Auto-create missing PCMN accounts (only for committed leaves)
        accounts_needed: dict[str, str] = {}
        for a in actif + passif:
            num = (a.get("account") or "").strip()
            lbl = (a.get("label") or "").strip()
            if num:
                accounts_needed[num] = lbl
        pcmn_created = await _ensure_pcmn_accounts(copro_id, accounts_needed)

        # Determine entry date : 1st day of the FY containing the year+1 of period_end_date
        # OR the FY's start_date if available
        entry_date = ""
        fy = None
        if data.fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": data.fiscal_year_id, "copropriete_id": copro_id})
            if fy:
                entry_date = (fy.get("start_date") or "").strip()
        if not entry_date and data.period_end_date:
            # Convert DD/MM/YYYY -> YYYY+1-01-01
            try:
                parts = data.period_end_date.split("/")
                if len(parts) == 3:
                    year = int(parts[2])
                    entry_date = f"{year + 1}-01-01"
            except (ValueError, IndexError):
                pass
        if not entry_date:
            entry_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Fiscal lock check
        try:
            await ensure_period_open(db, copro_id, entry_date, context="OD d'ouverture (AN)")
        except Exception as e:
            raise HTTPException(400, f"Periode fermee : {e}")

        # ---- Mapping comptes 410xxxx / 440xxxx -> owners / suppliers Optipro ----
        # Optipro convention :
        #   compte 4100960  ->  owner  avec auxiliary_code = "C0960"
        #   compte 4400025  ->  supplier avec auxiliary_code = "F0025"
        # Pour les comptes 4001xxxx (fonds de reserve Optipro) idem owner.
        # On indexe TOUS les owners/suppliers (sans filtre copropriete_id car les
        # owners importes via PdfImportDialog peuvent avoir copropriete_id="").
        owners_by_aux: dict[str, dict] = {}
        async for o in db.owners.find({"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0, "id": 1, "name": 1, "auxiliary_code": 1, "tier_accounts": 1, "copropriete_id": 1}):
            aux = (o.get("auxiliary_code") or "").upper().strip()
            if aux:
                owners_by_aux[aux] = o
        suppliers_by_aux: dict[str, dict] = {}
        async for sup in db.suppliers.find({"auxiliary_code": {"$exists": True, "$ne": ""}, "copropriete_id": copro_id}, {"_id": 0, "id": 1, "name": 1, "auxiliary_code": 1, "tier_accounts": 1}):
            aux = (sup.get("auxiliary_code") or "").upper().strip()
            if aux:
                suppliers_by_aux[aux] = sup

        def _resolve_third_party(account_number: str) -> tuple:
            """Returns (third_party_id, third_party_type, party_doc) or (None, None, None)."""
            acc = (account_number or "").strip()
            if not acc:
                return None, None, None
            # 410xxxx or 4001xxxx -> owner (4 digits after prefix)
            if acc.startswith("410") and len(acc) >= 7:
                aux = "C" + acc[-4:]
                o = owners_by_aux.get(aux)
                if o:
                    return o["id"], "owner", o
            if acc.startswith("4001") and len(acc) >= 8:
                aux = "C" + acc[-4:]
                o = owners_by_aux.get(aux)
                if o:
                    return o["id"], "owner", o
            # 440xxxx -> supplier
            if acc.startswith("440") and len(acc) >= 7:
                aux = "F" + acc[-4:]
                sup = suppliers_by_aux.get(aux)
                if sup:
                    return sup["id"], "supplier", sup
            return None, None, None

        # Build the journal entry lines + collect tier_accounts updates
        lines = []
        owner_tier_updates: dict[str, dict] = {}  # owner_id -> {"provisions": "...", "reserve": "..."}
        supplier_tier_updates: dict[str, str] = {}  # supplier_id -> "4400xxx"
        owners_linked = 0
        suppliers_linked = 0
        for a in actif + passif:
            amt = round(float(a.get("amount") or 0), 2)
            if amt == 0:
                continue
            is_actif = a in actif
            acc_num = (a.get("account") or "").strip()
            tp_id, tp_type, party = _resolve_third_party(acc_num)
            line = {
                "account_number": acc_num,
                "account_name": (a.get("label") or "").strip(),
                "debit": amt if is_actif else 0.0,
                "credit": 0.0 if is_actif else amt,
                "description": f"A-Nouveau : {(a.get('label') or '').strip()}",
                "line_description": f"A-Nouveau {acc_num}" + (f" - {party.get('name','')}" if party else ""),
                "occupant_pct": None,
                "proprietaire_pct": None,
            }
            if tp_id:
                line["third_party_id"] = tp_id
                line["third_party_type"] = tp_type
                if tp_type == "owner":
                    owners_linked += 1
                    # Schedule tier_account update : provisions for 410xxx, reserve for 4001xxx
                    key = "reserve" if acc_num.startswith("4001") else "provisions"
                    owner_tier_updates.setdefault(tp_id, {})[key] = acc_num
                elif tp_type == "supplier":
                    suppliers_linked += 1
                    supplier_tier_updates[tp_id] = acc_num
            lines.append(line)

        # ---- Apply tier_accounts updates (owners / suppliers) ----
        for oid, updates in owner_tier_updates.items():
            owner = owners_by_aux.get(next(iter([k for k, v in owners_by_aux.items() if v["id"] == oid]), ""), None)
            existing_tier = ((owner or {}).get("tier_accounts") or {}).get(copro_id, {}) or {}
            merged = {**existing_tier, **updates}
            await db.owners.update_one(
                {"id": oid},
                {"$set": {f"tier_accounts.{copro_id}": merged}},
            )
        for sid, acc in supplier_tier_updates.items():
            sup = next((s for s in suppliers_by_aux.values() if s["id"] == sid), None)
            existing_tier = ((sup or {}).get("tier_accounts") or {}).get(copro_id, {}) or {}
            merged = {**existing_tier, "main": acc}
            await db.suppliers.update_one(
                {"id": sid},
                {"$set": {f"tier_accounts.{copro_id}": merged}},
            )

        period_end = (data.period_end_date or "").strip() or "n-1"
        je_id = str(uuid.uuid4())
        await db.journal_entries.insert_one({
            "id": je_id,
            "journal_type": "AN",
            "date": entry_date,
            "reference": f"AN-{entry_date[:4]}-001",
            "description": f"OD d'ouverture - Bilan au {period_end}",
            "lines": lines,
            "total_debit": total_actif,
            "total_credit": total_passif,
            "copropriete_id": copro_id,
            "import_session_id": session_id,
            "created_at": _now_iso(),
        })

        await _update_step(db, session_id, "opening_balance", {
            "count": len(lines),
            "total_debit": total_actif,
            "total_credit": total_passif,
            "journal_entry_id": je_id,
            "pcmn_created": pcmn_created,
            "owners_linked": owners_linked,
            "suppliers_linked": suppliers_linked,
            "entry_date": entry_date,
        })
        return {
            "inserted": 1,
            "lines": len(lines),
            "total_debit": total_actif,
            "total_credit": total_passif,
            "journal_entry_id": je_id,
            "pcmn_created": pcmn_created,
            "owners_linked": owners_linked,
            "suppliers_linked": suppliers_linked,
            "entry_date": entry_date,
        }

    # ----- D: LOTS -----
    @router.post("/sessions/{session_id}/commit-lots")
    async def commit_lots(session_id: str, data: CommitLotsInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        m = data.mapping
        if "number" not in m or m["number"] in ("", None):
            raise HTTPException(400, "Mapping requis pour 'number'")
        inserted = 0
        errors = []
        # Build a lookup of existing owners by last_name (case-insensitive) for the current ACP/import session
        owners_in_session = await db.owners.find(
            {"import_session_id": session_id}, {"_id": 0, "id": 1, "name": 1, "last_name": 1}
        ).to_list(5000)
        owners_by_name = {(o.get("name") or "").lower().strip(): o["id"] for o in owners_in_session}
        owners_by_last = {(o.get("last_name") or "").lower().strip(): o["id"] for o in owners_in_session}
        for idx, row in enumerate(data.rows):
            try:
                def col(key):
                    i = m.get(key)
                    if i is None or i == "" or int(i) >= len(row):
                        return ""
                    return (row[int(i)] or "").strip()
                number = col("number")
                if not number:
                    continue
                owner_lookup = col("owner_name").lower().strip()
                owner_id = owners_by_name.get(owner_lookup) or owners_by_last.get(owner_lookup) or ""
                doc = {
                    "id": str(uuid.uuid4()),
                    "number": number,
                    "description": col("description"),
                    "lot_type": col("lot_type") or "appartement",
                    "floor": col("floor"),
                    "area": parse_french_number(col("area")),
                    "quotity": parse_french_number(col("quotity")),
                    "owner_id": owner_id,
                    "owner_ids": [owner_id] if owner_id else [],
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.lots.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "lots", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

    # ----- K: EXPENSE CATEGORIES (natures de depense) -----
    @router.post("/sessions/{session_id}/commit-natures")
    async def commit_natures(session_id: str, data: CommitNaturesInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        inserted = 0
        errors = []
        for idx, nat in enumerate(data.natures):
            try:
                code = (nat.get("code") or "").strip()
                libelle = (nat.get("libelle") or "").strip()
                account_number = (nat.get("account_number") or "").strip()
                if not (code and libelle and account_number):
                    continue
                doc = {
                    "id": str(uuid.uuid4()),
                    "code": code,
                    "name": libelle,
                    "label": libelle,
                    "account_number": account_number,
                    "vat_code": (nat.get("vat_code") or "").strip(),
                    "default_occupant_pct": float(nat.get("part_occupant") or 0.0),
                    "default_proprietaire_pct": float(nat.get("part_proprietaire") or 100.0),
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.expense_categories.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "natures", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

    # ----- E: FISCAL YEAR -----
    @router.post("/sessions/{session_id}/commit-fiscal-year")
    async def commit_fiscal_year(session_id: str, data: CommitFiscalYearInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        # Verifie qu'on n'a pas deja un exercice avec ce nom dans l'ACP
        existing = await db.fiscal_years.find_one({"copropriete_id": copro_id, "name": data.name})
        if existing:
            raise HTTPException(400, f"L'exercice '{data.name}' existe deja dans cette ACP")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "start_date": data.start_date,
            "end_date": data.end_date,
            "status": data.status or "open",
            "copropriete_id": copro_id,
            "import_session_id": session_id,
            "created_at": _now_iso(),
        }
        await db.fiscal_years.insert_one(doc)
        await _update_step(db, session_id, "fiscal_year", {"count": 1, "fiscal_year_id": doc["id"]})
        return {"id": doc["id"], "name": doc["name"]}

    # ----- F: BUDGET -----
    @router.post("/sessions/{session_id}/commit-budget")
    async def commit_budget(session_id: str, data: CommitBudgetInput, request: Request):
        """Cree un budget unifie pour l'exercice avec des lignes par (section/cle, compte).

        Le PDF Optipro structure le budget par section (= cle de repartition). Pour
        chaque section, on a une liste de lignes (compte, libelle, montant). Notre
        modele Budget supporte des lignes : { account_number, label, amount,
        distribution_key_code (libre) }.
        """
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        # Verifie que l'exercice existe
        fy = await db.fiscal_years.find_one({"id": data.fiscal_year_id})
        if not fy:
            raise HTTPException(400, "Exercice fiscal introuvable")

        # ---- Auto-create distribution_keys for each unique section code ----
        # The Optipro budget PDF structures lines by 'key_code' (e.g. 0001, 0006).
        # Each such code corresponds to a distribution key. We auto-create them
        # if missing so the user doesn't have to maintain them separately.
        existing_keys: dict[str, str] = {}  # code -> id
        async for k in db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "code": 1, "import_code": 1}):
            for fld in ("code", "import_code"):
                v = (k.get(fld) or "").strip()
                if v:
                    existing_keys[v] = k["id"]
                    existing_keys[v.zfill(4)] = k["id"]
        keys_created = 0
        section_key_codes: dict[str, dict] = {}
        for sec in data.sections:
            key_code = (sec.get("key_code") or "").strip()
            key_label = (sec.get("key_label") or "").strip()
            if not key_code or key_code in section_key_codes:
                continue
            section_key_codes[key_code] = {"code": key_code, "label": key_label}
        for code, info in section_key_codes.items():
            if code in existing_keys or code.zfill(4) in existing_keys:
                continue
            key_id = str(uuid.uuid4())
            await db.distribution_keys.insert_one({
                "id": key_id,
                "code": code,
                "import_code": code,
                "name": info["label"] or f"Cle {code}",
                "description": "",
                "type": "quotities",
                "is_special": False,
                "lines": [],
                "copropriete_id": copro_id,
                "import_session_id": session_id,
                "created_at": _now_iso(),
            })
            existing_keys[code] = key_id
            existing_keys[code.zfill(4)] = key_id
            keys_created += 1

        # Construire les lignes a partir des sections (with key_id linking)
        lines = []
        total_amount = 0.0
        for sec in data.sections:
            key_code = (sec.get("key_code") or "").strip()
            key_label = (sec.get("key_label") or "").strip()
            key_id = existing_keys.get(key_code) or existing_keys.get(key_code.zfill(4)) or ""
            for ln in sec.get("lines") or []:
                amt = float(ln.get("amount") or 0)
                lines.append({
                    "account_number": (ln.get("account") or "").strip(),
                    "label": (ln.get("libelle") or "").strip(),
                    "amount": amt,
                    "distribution_key_id": key_id,
                    "distribution_key_code": key_code,
                    "distribution_key_label": key_label,
                })
                total_amount += amt
        # Cree (ou remplace) le budget de l'exercice
        existing = await db.budgets.find_one({"fiscal_year_id": data.fiscal_year_id, "copropriete_id": copro_id})
        if existing:
            await db.budgets.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "lines": lines,
                    "total_amount": round(total_amount, 2),
                    "updated_at": _now_iso(),
                    "import_session_id": session_id,
                }}
            )
            budget_id = existing["id"]
        else:
            budget_id = str(uuid.uuid4())
            await db.budgets.insert_one({
                "id": budget_id,
                "fiscal_year_id": data.fiscal_year_id,
                "copropriete_id": copro_id,
                "lines": lines,
                "total_amount": round(total_amount, 2),
                "import_session_id": session_id,
                "created_at": _now_iso(),
            })
        await _update_step(db, session_id, "budget", {
            "count": len(lines),
            "total_amount": round(total_amount, 2),
            "budget_id": budget_id,
            "keys_created": keys_created,
        })
        return {
            "inserted": len(lines),
            "total_amount": round(total_amount, 2),
            "keys_created": keys_created,
        }

    # ----- J: DISTRIBUTION KEYS -----
    @router.post("/sessions/{session_id}/commit-distribution-keys")
    async def commit_distribution_keys(session_id: str, data: CommitDistributionKeysInput, request: Request):
        """Cree les cles de repartition + leurs tantiemes par lot.

        Pour chaque cle : 1 doc distribution_keys + matching automatique des lots
        importes pendant la session (basee sur le numero / le libelle).
        """
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        # Lookup des lots de l'ACP (toute la base, pas juste la session)
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "number": 1, "description": 1}).to_list(2000)
        lots_by_number = {(l.get("number") or "").lower().strip(): l["id"] for l in lots}
        lots_by_desc = {(l.get("description") or "").lower().strip(): l["id"] for l in lots}
        inserted = 0
        for k in data.keys or []:
            code = (k.get("code") or "").strip()
            name = (k.get("name") or code).strip()
            if not name:
                continue
            existing = await db.distribution_keys.find_one({"copropriete_id": copro_id, "code": code} if code else {"copropriete_id": copro_id, "name": name})
            # Build lines : match each lot by number or label (case-insensitive)
            kl_lines = []
            for line in k.get("lines") or []:
                quotity = float(line.get("quotity") or 0)
                if quotity <= 0:
                    continue
                lot_label = (line.get("lot_label") or "").lower().strip()
                lot_code = (line.get("lot_code") or "").lower().strip()
                lot_id = lots_by_number.get(lot_code) or lots_by_number.get(lot_label) or lots_by_desc.get(lot_label) or ""
                kl_lines.append({
                    "lot_id": lot_id,
                    "lot_label_raw": line.get("lot_label", ""),
                    "lot_code_raw": line.get("lot_code", ""),
                    "owner_label_raw": line.get("owner_label", ""),
                    "quotity": quotity,
                })
            total_q = float(k.get("total_quotities") or sum(l["quotity"] for l in kl_lines))
            if existing:
                # If the existing key has NO lines (was previously created from an
                # empty parse), enrich it with the new lines + import_session_id.
                # Otherwise we keep the existing one untouched to avoid clobbering
                # user edits.
                if not (existing.get("lines") or []):
                    await db.distribution_keys.update_one(
                        {"id": existing["id"]},
                        {"$set": {
                            "name": name,
                            "type": k.get("type") or existing.get("type") or "tantiemes",
                            "total_quotities": total_q,
                            "lines": kl_lines,
                            "import_session_id": session_id,
                            "updated_at": _now_iso(),
                        }},
                    )
                    inserted += 1
                continue
            doc = {
                "id": str(uuid.uuid4()),
                "code": code,
                "name": name,
                "type": k.get("type") or "tantiemes",
                "total_quotities": total_q,
                "lines": kl_lines,
                "copropriete_id": copro_id,
                "import_session_id": session_id,
                "created_at": _now_iso(),
            }
            await db.distribution_keys.insert_one(doc)
            inserted += 1
        await _update_step(db, session_id, "distribution_keys", {"count": inserted})
        return {"inserted": inserted}

    return router


async def _update_step(db, session_id: str, step_key: str, payload: dict):
    """Stores per-step state on the session document."""
    await db.import_sessions.update_one(
        {"id": session_id},
        {"$set": {f"steps.{step_key}": {**payload, "updated_at": _now_iso()}}}
    )
