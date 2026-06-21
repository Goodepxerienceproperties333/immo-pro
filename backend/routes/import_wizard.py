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
from import_wizard.pdf_utils import extract_pdf, parse_natures_pdf, parse_budget_pdf, parse_distribution_keys_pdf, parse_owners_pdf, parse_lots_pdf, parse_suppliers_pdf

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
                                "bank_statement_lines"]
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
    @router.post("/sessions/{session_id}/commit-invoices")
    async def commit_invoices(session_id: str, data: CommitInvoicesInput, request: Request):
        """Commit pre-parsed invoices. Each invoice is auto-matched to:
        - supplier (via F0XXX auxiliary_code)
        - distribution_key (via key code from 'Cle' column)
        - expense_category (via Nature code or account_number)
        """
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        # Build matching lookups (scoped to current ACP)
        sup_by_aux: dict[str, str] = {}
        async for s in db.suppliers.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "auxiliary_code": 1, "name": 1}):
            ax = (s.get("auxiliary_code") or "").upper().strip()
            if ax:
                sup_by_aux[ax] = s["id"]
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

        # Resolve unique invoice internal_reference per ACP/year
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
                supplier_id = sup_by_aux.get(supplier_aux) if supplier_aux else None
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

                doc = {
                    "id": str(uuid.uuid4()),
                    "number": (inv.get("external_ref") or internal_ref).strip(),
                    "internal_reference": internal_ref,
                    "date": date_str,
                    "due_date": (inv.get("due_date") or "").strip(),
                    "supplier": (inv.get("supplier_name") or "").strip(),
                    "supplier_id": supplier_id or "",
                    "description": (inv.get("libelle") or "").strip(),
                    "total_amount": total_amount,
                    "vat_amount": vat_amount,
                    "account_number": account_num,
                    "expense_category_id": expense_cat_id,
                    "distribution_key_id": dist_key_id,
                    "distribution_lines": [],
                    "status": "to_pay" if not inv.get("ne_pas_payer") else "do_not_pay",
                    "copropriete_id": copro_id,
                    "is_private_fee": False,
                    "private_fee_owner_id": "",
                    "occupant_pct": occ_pct,
                    "proprietaire_pct": prop_pct,
                    "occupant_amount": round(total_amount * occ_pct / 100, 2),
                    "proprietaire_amount": round(total_amount * prop_pct / 100, 2),
                    "vat_code": (inv.get("vat_code") or "").strip(),
                    "supplier_aux_code": supplier_aux,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.invoices.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})

        await _update_step(db, session_id, "invoices", {
            "count": inserted,
            "errors": errors,
            "matched_supplier": matched_supplier,
            "matched_key": matched_key,
            "matched_category": matched_category,
        })
        return {
            "inserted": inserted,
            "errors": errors,
            "matched_supplier": matched_supplier,
            "matched_key": matched_key,
            "matched_category": matched_category,
        }

    # ----- H: JOURNALS (extraits bancaires) - CSV Optipro -----
    @router.post("/sessions/{session_id}/commit-journals")
    async def commit_journals(session_id: str, data: CommitJournalsInput, request: Request):
        """Commit pre-parsed bank journal transactions as bank statement lines."""
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        bank_lookup: dict[str, str] = dict(data.bank_account_mapping or {})
        async for ba in db.bank_accounts.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "account_number": 1, "pcmn_account": 1}):
            for fld in ("account_number", "pcmn_account"):
                v = (ba.get(fld) or "").strip()
                if v and v not in bank_lookup:
                    bank_lookup[v] = ba["id"]

        inserted = 0
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
                amount = float(t.get("amount") or 0)
                direction = (t.get("direction") or "").strip()
                doc = {
                    "id": str(uuid.uuid4()),
                    "copropriete_id": copro_id,
                    "bank_account_id": bank_id or "",
                    "bank_pcmn_code": bank_pcmn,
                    "bank_pcmn_label": (t.get("bank_account_label") or "").strip(),
                    "counterparty_account": (t.get("counterparty_account") or "").strip(),
                    "counterparty_account_label": (t.get("counterparty_account_label") or "").strip(),
                    "num_doc": (t.get("num_doc") or "").strip(),
                    "code_journal": (t.get("code_journal") or "FIN").strip(),
                    "date_value": date_v,
                    "date_compta": (t.get("date_compta") or date_v).strip(),
                    "libelle": (t.get("libelle") or "").strip(),
                    "ext_reference": (t.get("ext_reference") or "").strip(),
                    "amount": amount,
                    "direction": direction,
                    "status": "imported",
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.bank_statement_lines.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})

        await _update_step(db, session_id, "journals", {"count": inserted, "errors": errors})
        return {"inserted": inserted, "errors": errors}

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
        # Construire les lignes a partir des sections
        lines = []
        total_amount = 0.0
        for sec in data.sections:
            key_code = (sec.get("key_code") or "").strip()
            key_label = (sec.get("key_label") or "").strip()
            for ln in sec.get("lines") or []:
                amt = float(ln.get("amount") or 0)
                lines.append({
                    "account_number": (ln.get("account") or "").strip(),
                    "label": (ln.get("libelle") or "").strip(),
                    "amount": amt,
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
        })
        return {"inserted": len(lines), "total_amount": round(total_amount, 2)}

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
            if existing:
                continue
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
            doc = {
                "id": str(uuid.uuid4()),
                "code": code,
                "name": name,
                "type": k.get("type") or "tantiemes",
                "total_quotities": float(k.get("total_quotities") or sum(l["quotity"] for l in kl_lines)),
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
