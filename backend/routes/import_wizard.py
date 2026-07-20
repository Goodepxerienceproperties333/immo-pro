"""Import Wizard routes - migration Optipro/Sogis -> NextGe Copro.

Session-based, with rollback support (every imported doc carries `import_session_id`).
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from import_wizard.csv_utils import sniff_csv, parse_french_number, parse_date, split_ref_code, normalize_header, parse_invoices_csv, parse_journals_csv
from import_wizard.pdf_utils import extract_pdf, parse_natures_pdf, parse_budget_pdf, parse_distribution_keys_pdf, parse_owners_pdf, parse_lots_pdf, parse_suppliers_pdf, parse_balance_pdf, parse_od_entries_pdf

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
    # iter90gk : decisions par position - {idx: {"action": "reuse|create",
    # "supplier_id": "...", "bce_number": "BE..."}}. Sans decisions, comportement
    # legacy (skip silencieux sur doublon nom).
    decisions: Optional[dict] = {}


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
    # iter90gj : appels hors budget declares avant les mutations.
    # Stocke sur le fiscal_year pour utilisation par le mutation prorata et
    # les rapports "Etat des fonds".
    funds_config: Optional[dict] = None
    # Structure attendue :
    # {
    #   "reserve_fund": {
    #     "opening_balance": float (montant fonds reserve a la cloture N-1),
    #     "has_annual_call": bool,
    #     "call_amount": float (montant total appel N),
    #     "call_frequency": "annual"|"quarterly"|"monthly",
    #   },
    #   "roulement_fund": {
    #     "opening_balance": float (montant fonds roulement a la cloture N-1),
    #     "has_increase": bool,
    #     "new_total": float (nouveau total apres augmentation),
    #   },
    # }


class CommitOdEntriesInput(BaseModel):
    """Each entry must have both a charge account AND a counterpart account
    chosen by the user before commit (no defaults, no fallback)."""
    entries: List[dict]  # [{date, libelle, account_number, account_name,
                         #   amount, counterpart_account, counterpart_account_name,
                         #   proprietaire_pct, occupant_pct}]


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
        if kind == "od_entries":
            return parse_od_entries_pdf(raw)
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
            # iter90gi : hydrate `steps.fiscal_year` a partir de l'exercice
            # ouvert de l'ACP si absent (cas ou l'exercice a ete cree via
            # l'Assistant ACP - iter90gg - sans passer par le wizard).
            if not (existing.get("steps") or {}).get("fiscal_year"):
                fy_open = await db.fiscal_years.find_one(
                    {"copropriete_id": data.copropriete_id, "status": "open"},
                    {"_id": 0}, sort=[("created_at", -1)],
                )
                if fy_open:
                    await db.import_sessions.update_one(
                        {"id": existing["id"]},
                        {"$set": {"steps.fiscal_year": {
                            "count": 1, "fiscal_year_id": fy_open["id"],
                            "auto_hydrated": True,
                        }}},
                    )
                    existing.setdefault("steps", {})["fiscal_year"] = {
                        "count": 1, "fiscal_year_id": fy_open["id"],
                        "auto_hydrated": True,
                    }
            return existing
        # iter90gi : hydrate `steps.fiscal_year` a partir de l'exercice ouvert
        # de l'ACP (cree via l'Assistant ACP - iter90gg). Le wizard doit
        # pouvoir utiliser directement cet exercice pour le budget, les OD,
        # etc. sans redemander a l'utilisateur.
        steps: dict = {}
        fy_open = await db.fiscal_years.find_one(
            {"copropriete_id": data.copropriete_id, "status": "open"},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        if fy_open:
            steps["fiscal_year"] = {
                "count": 1, "fiscal_year_id": fy_open["id"], "auto_hydrated": True,
            }
        session = {
            "id": str(uuid.uuid4()),
            "copropriete_id": data.copropriete_id,
            "syndic_id": str(user.get("_id", "")),
            "source_system": data.source_system or "Optipro",
            "status": "active",
            "steps": steps,
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
        # iter90gi : hydrate `steps.fiscal_year` a partir de l'exercice ouvert
        # de l'ACP (self-healing pour les sessions creees avant iter90gi ou
        # dont le step fiscal_year n'a jamais ete rempli).
        if session and not (session.get("steps") or {}).get("fiscal_year"):
            fy_open = await db.fiscal_years.find_one(
                {"copropriete_id": copropriete_id, "status": "open"},
                {"_id": 0}, sort=[("created_at", -1)],
            )
            if fy_open:
                await db.import_sessions.update_one(
                    {"id": session["id"]},
                    {"$set": {"steps.fiscal_year": {
                        "count": 1, "fiscal_year_id": fy_open["id"],
                        "auto_hydrated": True,
                    }}},
                )
                session.setdefault("steps", {})["fiscal_year"] = {
                    "count": 1, "fiscal_year_id": fy_open["id"],
                    "auto_hydrated": True,
                }
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
        if kind == "od_entries":
            res = parse_od_entries_pdf(raw)
            res["filename"] = file.filename
            return res
        if kind == "invoices":
            # iter90gj : import PDF "Factures fournisseurs" (Optipro).
            # Le parser retourne 1 dict par facture avec `allocations[]`.
            # On aplatit en 1 entree par ligne comptable pour reutiliser
            # le meme pipeline que le CSV (le regroupement multi-ligne est
            # gere par commit-invoices via `_group_key`).
            from import_wizard.pdf_supplier_invoice_list import parse_supplier_invoice_list
            parsed = parse_supplier_invoice_list(raw)
            invoices: list[dict] = []
            for head in parsed:
                sup_code = (head.get("supplier_code") or "").strip()
                sup_name = (head.get("supplier_name") or "").strip()
                base = {
                    "date": head.get("date", ""),
                    "due_date": "",
                    "internal_ref": (head.get("internal_ref") or "").strip(),
                    "external_ref": (head.get("external_ref") or "").strip(),
                    "libelle": (head.get("description") or "").strip(),
                    "ne_pas_payer": False,
                    "supplier_aux_code": sup_code,
                    "supplier_name": sup_name,
                    "vat_code": "",
                    "part_occupant": 0.0,
                    "part_proprietaire": 100.0,
                    "dist_key_code": "",
                    "dist_key_label": "",
                    "nature_code": "",
                    "nature_label": "",
                }
                allocs = head.get("allocations") or []
                if allocs:
                    for a in allocs:
                        invoices.append({
                            **base,
                            "account_number": (a.get("account_number") or "").strip(),
                            "account_label": (a.get("description") or "").strip(),
                            "montant_ht": float(a.get("ht_amount") or 0),
                            "montant_tvac": float(a.get("tvac_amount") or 0),
                            "montant_tva": round(float(a.get("tvac_amount") or 0) - float(a.get("ht_amount") or 0), 2),
                        })
                else:
                    # Facture sans allocation detaillee : 1 ligne unique avec totaux entete.
                    invoices.append({
                        **base,
                        "account_number": "",
                        "account_label": "",
                        "montant_ht": float(head.get("ht_amount") or 0),
                        "montant_tvac": float(head.get("tvac_amount") or 0),
                        "montant_tva": round(float(head.get("tvac_amount") or 0) - float(head.get("ht_amount") or 0), 2),
                    })
            return {"filename": file.filename, "invoices": invoices, "count": len(invoices)}
        info = extract_pdf(raw)
        info["filename"] = file.filename
        return info

    # ----- A: OWNERS -----
    @router.post("/sessions/{session_id}/commit-owners")
    async def commit_owners(session_id: str, data: CommitOwnersInput, request: Request):
        """iter90im : Import owners avec strategie MATCH-OR-UPDATE (upsert).

        Regle metier : "Verification systematique - Avant de creer une
        entite, verifie si elle existe deja en base. Pour les proprietaires,
        utilise l'identifiant Optipro (auxiliary_code) ou l'email. Si
        l'entite existe, mets-la a jour (Update) au lieu de creer un
        nouveau compte."

        Ordre de matching (par priorite) :
          1. `auxiliary_code` (Optipro C0959) - identifiant historique unique
          2. `email` - identifiant unique moderne
          3. `phone` - fallback
          4. `norm_name` (last_name + first_name normalise) - fallback nom
        """
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

        import re as _re
        def _norm_name(first, last):
            return " ".join(sorted(f"{first} {last}".lower().strip().split()))
        def _norm_an(v):
            return _re.sub(r"[^A-Za-z0-9]", "", v or "").upper()

        # Pre-load des owners existants GLOBALEMENT (pour matching cross-ACP :
        # un meme proprietaire peut deja avoir ete cree sur une autre ACP,
        # dans ce cas on ADD l'ACP courante a `copropriete_ids`).
        existing_owners = await db.owners.find({}, {"_id": 0}).to_list(50000)
        by_aux = {}
        by_email = {}
        by_phone = {}
        by_name = {}
        for o in existing_owners:
            aux = (o.get("auxiliary_code") or "").strip().upper()
            if aux:
                by_aux[aux] = o
            for e_field in ("email", "email2"):
                e = (o.get(e_field) or "").strip().lower()
                if e:
                    by_email[e] = o
            for p_field in ("phone", "phone2"):
                p = _norm_an(o.get(p_field, ""))
                if p:
                    by_phone[p] = o
            nn = _norm_name(o.get("first_name", ""), o.get("last_name", "") or o.get("name", ""))
            if nn:
                by_name.setdefault(nn, o)  # premier gagne (le plus ancien)

        inserted = 0
        updated = 0
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
                email = col("email")
                phone = col("phone")
                aux_code = col("auxiliary_code")
                norm_aux = aux_code.upper()
                norm_email = email.lower() if email else ""
                norm_phone = _norm_an(phone) if phone else ""
                norm_name = _norm_name(first_name, last_name)

                # MATCH PRIORITAIRE
                existing = None
                match_reason = None
                if norm_aux and norm_aux in by_aux:
                    existing = by_aux[norm_aux]
                    match_reason = "auxiliary_code"
                elif norm_email and norm_email in by_email:
                    existing = by_email[norm_email]
                    match_reason = "email"
                elif norm_phone and norm_phone in by_phone:
                    existing = by_phone[norm_phone]
                    match_reason = "phone"
                elif norm_name and norm_name in by_name:
                    existing = by_name[norm_name]
                    match_reason = "name"

                # Champs a merger (nouvelle valeur ne remplace que si le
                # champ actuel est vide - on preserve les donnees enrichies
                # manuellement par le syndic).
                new_fields = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "name": full,
                    "address": col("address"),
                    "postal_code": col("postal_code"),
                    "city": col("city"),
                    "country": col("country") or "Belgique",
                    "email": email,
                    "phone": phone,
                    "iban": col("iban"),
                    "auxiliary_code": aux_code,
                }

                if existing:
                    # UPDATE : merge des champs vides + ADD l'ACP a copropriete_ids
                    merged = {**existing}
                    for k, v in new_fields.items():
                        if v and not merged.get(k):
                            merged[k] = v
                    # Ajoute copro_id a copropriete_ids si absent
                    current_acps = set(existing.get("copropriete_ids") or [])
                    if existing.get("copropriete_id") and not current_acps:
                        current_acps.add(existing["copropriete_id"])
                    current_acps.add(copro_id)
                    merged["copropriete_ids"] = sorted(current_acps)
                    # Retire copropriete_id scalaire (deprecie)
                    merged.pop("copropriete_id", None)
                    merged["last_updated_at"] = _now_iso()
                    merged["last_updated_from_import"] = session_id
                    await db.owners.update_one(
                        {"id": existing["id"]},
                        {"$set": {k: v for k, v in merged.items() if k != "id"},
                         "$unset": {"copropriete_id": ""}},
                    )
                    # Refresh cache
                    by_aux[norm_aux] = merged
                    updated += 1
                else:
                    # CREATE : nouveau doc avec copropriete_ids (array)
                    doc = {
                        "id": str(uuid.uuid4()),
                        **new_fields,
                        "copropriete_ids": [copro_id],
                        "import_session_id": session_id,
                        "created_at": _now_iso(),
                    }
                    await db.owners.insert_one(doc)
                    inserted += 1
                    if norm_aux:
                        by_aux[norm_aux] = doc
                    if norm_email:
                        by_email[norm_email] = doc
                    if norm_phone:
                        by_phone[norm_phone] = doc
                    if norm_name:
                        by_name.setdefault(norm_name, doc)
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "owners", {
            "count": inserted, "updated": updated, "errors": errors,
        })
        return {"inserted": inserted, "updated": updated, "errors": errors}

    # ----- C: SUPPLIERS -----
    @router.post("/sessions/{session_id}/commit-suppliers")
    async def commit_suppliers(session_id: str, data: CommitSuppliersInput, request: Request):
        """iter90im : Import suppliers avec strategie UPSERT.

        Regle metier : "Pour les Fournisseurs, utilise le numero de TVA
        comme cle unique. Si la TVA existe deja, mets a jour les infos
        au lieu de creer un nouveau fournisseur."

        Ordre de matching (par priorite) :
          1. `bce_number` (VAT/BCE normalise) - identifiant unique GLOBAL
          2. `vat_number` (idem)
          3. `iban` normalise
          4. `name` (normalise) dans le scope ACP
        """
        from routes.suppliers import find_duplicate_supplier
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        m = data.mapping
        if "name" not in m or m["name"] in ("", None):
            raise HTTPException(400, "Mapping requis pour 'name'")
        inserted = 0
        updated = 0
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
                bce = col("bce_number")
                vat = col("vat_number")
                iban = col("iban")

                # MATCHING PRIORITAIRE (BCE/VAT global, puis nom local)
                dup = await find_duplicate_supplier(
                    db, name=name, bce_number=bce, vat_number=vat,
                    iban=iban, copro_id=copro_id,
                )

                new_fields = {
                    "name": name,
                    "vat_number": vat,
                    "bce_number": bce,
                    "address": col("address"),
                    "postal_code": col("postal_code"),
                    "city": col("city"),
                    "country": col("country") or "Belgique",
                    "phone": col("phone"),
                    "email": col("email"),
                    "iban": iban,
                    "bic": col("bic"),
                    "default_account": col("default_account"),
                    "notes": col("notes"),
                }

                if dup:
                    existing = dup["supplier"]
                    # UPDATE : merge des champs vides + rattache a l'ACP courante
                    merged_fields = {}
                    for k, v in new_fields.items():
                        if v and not existing.get(k):
                            merged_fields[k] = v
                    # Si le supplier existant appartient a une autre ACP, on
                    # le RATTACHE aussi a l'ACP courante en clonant (car
                    # `copropriete_id` est scalaire pour supplier). Pour eviter
                    # de casser la structure, on garde le premier scope mais
                    # on ajoute un tier_accounts.{acp_courant} pour permettre
                    # a l'ACP de voir cette fiche.
                    if existing.get("copropriete_id") != copro_id:
                        # Assign compte canonique 44000XXX sur cette ACP
                        try:
                            from tier_accounts import assign_supplier_account as _assign
                            await _assign(db, existing, copro_id)
                        except Exception:
                            pass
                    if merged_fields:
                        merged_fields["last_updated_at"] = _now_iso()
                        merged_fields["last_updated_from_import"] = session_id
                        await db.suppliers.update_one(
                            {"id": existing["id"]},
                            {"$set": merged_fields},
                        )
                    updated += 1
                    continue

                # CREATE
                doc = {
                    "id": str(uuid.uuid4()),
                    **new_fields,
                    "copropriete_id": copro_id,
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.suppliers.insert_one(doc)
                inserted += 1
            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
        await _update_step(db, session_id, "suppliers", {
            "count": inserted, "updated": updated, "errors": errors,
        })
        return {"inserted": inserted, "updated": updated, "errors": errors}

    # ----- C-bis: SUPPLIERS via PDF (no mapping needed - already structured) -----
    @router.post("/sessions/{session_id}/preview-suppliers-pdf")
    async def preview_suppliers_pdf(session_id: str, data: CommitSuppliersPdfInput, request: Request):
        """iter90is (Chinese Wall strict) : phase preview avant commit.
        Pour chaque fournisseur du PDF, retourne uniquement le match INTRA-ACP
        (aucune proposition cross-ACP). Chaque fiche est locale a l'ACP.
        Les candidats BCE (KBO) restent proposes si le BCE est manquant.

        Decisions possibles :
          - reuse : une fiche existe deja dans CETTE ACP (match strict par nom/BCE).
          - create : creer une nouvelle fiche LOCALE (BCE obligatoire).
        """
        from routes.suppliers import find_duplicate_supplier
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        # iter90ip : import pour la lookup BCE automatique
        from bce_lookup import search_kbo_by_name
        rows = []
        for idx, s in enumerate(data.suppliers):
            name = (s.get("name") or "").strip()
            if not name:
                continue
            # Match STRICT INTRA-ACP uniquement (chinese wall)
            strict_dup = await find_duplicate_supplier(
                db, name=name, bce_number="", vat_number="", iban="", copro_id=copro_id,
            )

            # iter90ip : lookup BCE automatique SI aucun match strict avec BCE
            # + aucun BCE dans les donnees d'import.
            src_bce = (s.get("bce_number") or s.get("vat_number") or "").strip()
            strict_has_bce = bool(strict_dup and (strict_dup["supplier"].get("bce_number") or "").strip())
            bce_candidates: list[dict] = []
            if not src_bce and not strict_has_bce:
                bce_candidates = await search_kbo_by_name(
                    name,
                    postal_code=(s.get("postal_code") or "").strip(),
                    top_n=3,
                    db=db,
                )

            rows.append({
                "index": idx,
                "name": name,
                "auxiliary_code": (s.get("auxiliary_code") or "").strip(),
                "address": (s.get("address") or "").strip(),
                "postal_code": (s.get("postal_code") or "").strip(),
                "city": (s.get("city") or "").strip(),
                "phone": (s.get("phone") or "").strip(),
                "email": (s.get("email") or "").strip(),
                # Match strict INTRA-ACP : si trouve, on pre-selectionne reuse.
                "strict_match": ({
                    "id": strict_dup["supplier"]["id"],
                    "name": strict_dup["supplier"].get("name", ""),
                    "bce_number": strict_dup["supplier"].get("bce_number", ""),
                    "field": strict_dup.get("field", ""),
                } if strict_dup else None),
                # iter90is : fuzzy_matches cross-ACP supprime (chinese wall strict).
                # Le champ reste pour compat frontend mais est TOUJOURS vide.
                "fuzzy_matches": [],
                # iter90ip : candidats BCE proposes par KBO (top 3, similarite decroissante)
                "bce_candidates": bce_candidates,
                "suggested_action": "reuse" if strict_dup else "create",
                "suggested_supplier_id": strict_dup["supplier"]["id"] if strict_dup else "",
            })
        return {"suppliers": rows, "count": len(rows)}

    # ---- iter90ip : Lookup BCE automatique via KBO Public Search ----
    class BceLookupInput(BaseModel):
        name: str
        postal_code: Optional[str] = ""
        top_n: Optional[int] = 3

    @router.post("/lookup-bce")
    async def lookup_bce_endpoint(data: BceLookupInput, request: Request):
        """iter90ip : recherche par nom sur le KBO Public Search
        (kbopub.economie.fgov.be). Retourne les meilleurs candidats tries
        par similarite de tokens. Utilise un cache Mongo TTL 30 jours pour
        eviter les requetes redondantes.

        Ne necessite aucune ACP scope (utilitaire syndic global). Le user
        doit etre authentifie (protege par le middleware).
        """
        from bce_lookup import search_kbo_by_name
        query = (data.name or "").strip()
        if len(query) < 2:
            return {"query": query, "candidates": [], "count": 0}
        candidates = await search_kbo_by_name(
            query,
            postal_code=(data.postal_code or "").strip(),
            top_n=max(1, min(10, data.top_n or 3)),
            db=db,
        )
        return {
            "query": query,
            "postal_code": (data.postal_code or "").strip(),
            "candidates": candidates,
            "count": len(candidates),
        }

    @router.post("/sessions/{session_id}/commit-suppliers-pdf")
    async def commit_suppliers_pdf(session_id: str, data: CommitSuppliersPdfInput, request: Request):
        """iter90gk : accepte maintenant un champ optionnel `decisions` (dict
        indexe par position dans data.suppliers) qui specifie l'action decidee
        par le syndic pour chaque fournisseur :
          {index: {"action": "reuse"|"create", "supplier_id": "..." (si reuse),
                   "bce_number": "BE..." (si create - OBLIGATOIRE)}}

        Sans `decisions` : comportement legacy (skip silencieux si doublon nom).
        Avec `decisions` : applique strictement les choix du syndic.
        """
        from routes.suppliers import find_duplicate_supplier
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        decisions = getattr(data, "decisions", None) or {}
        inserted = 0
        reused = 0
        skipped_duplicates = 0
        errors = []
        for idx, s in enumerate(data.suppliers):
            try:
                name = (s.get("name") or "").strip()
                if not name:
                    continue
                # Recupere la decision du syndic pour cette position (str ou int cle)
                decision = decisions.get(str(idx)) or decisions.get(idx) or {}
                action = (decision.get("action") or "").strip().lower()
                if action == "reuse":
                    # Attache le fournisseur existant a cette ACP (idempotent)
                    sup_id = (decision.get("supplier_id") or "").strip()
                    if not sup_id:
                        errors.append({"row": idx, "error": "action=reuse mais supplier_id vide"})
                        continue
                    sup = await db.suppliers.find_one({"id": sup_id}, {"_id": 0})
                    if not sup:
                        errors.append({"row": idx, "error": f"supplier_id {sup_id} introuvable"})
                        continue
                    # Attache-le a l'ACP courante (multi-ACP support via copropriete_ids)
                    from tier_accounts import assign_supplier_account
                    await assign_supplier_account(db, sup, copro_id)
                    await db.suppliers.update_one(
                        {"id": sup_id},
                        {"$addToSet": {"copropriete_ids": copro_id}},
                    )
                    reused += 1
                    continue

                if action == "create":
                    # BCE OBLIGATOIRE pour toute creation (regle metier)
                    bce = (decision.get("bce_number") or "").strip()
                    if not bce:
                        errors.append({
                            "row": idx,
                            "error": f"Creation refusee : le BCE est obligatoire pour '{name}' (regle stricte anti-doublon)."
                        })
                        continue
                    # Verifie l'unicite GLOBALE du BCE
                    dup_bce = await find_duplicate_supplier(
                        db, name="", bce_number=bce, vat_number="", iban="", copro_id="",
                    )
                    if dup_bce:
                        errors.append({
                            "row": idx,
                            "error": (
                                f"BCE {bce} deja utilise par '{dup_bce['supplier'].get('name','')}' "
                                f"- utilisez action=reuse avec supplier_id={dup_bce['supplier']['id']}."
                            ),
                        })
                        continue
                    # Fall-through pour creer la fiche (voir bloc creation ci-dessous)
                    supplier_bce = bce
                else:
                    # Pas de decision : comportement legacy - check anti-doublon
                    dup = await find_duplicate_supplier(
                        db, name=name, bce_number="", vat_number="", iban="", copro_id=copro_id,
                    )
                    if dup:
                        skipped_duplicates += 1
                        continue
                    supplier_bce = ""

                # Creation du fournisseur (avec ou sans BCE selon la branche)
                doc = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "auxiliary_code": (s.get("auxiliary_code") or "").strip(),
                    "vat_number": "",
                    "bce_number": supplier_bce,
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
        await _update_step(db, session_id, "suppliers", {
            "count": inserted, "reused": reused,
            "skipped_duplicates": skipped_duplicates, "errors": errors,
        })
        return {
            "inserted": inserted, "reused": reused,
            "skipped_duplicates": skipped_duplicates, "errors": errors,
        }

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
        # iter90g9 : conserve la liste complete pour deriver account_number a partir
        # de la nature de depense quand le fichier source ne fournit pas de compte.
        expense_cats: list[dict] = []
        async for c in db.expense_categories.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "code": 1, "account_number": 1}):
            expense_cats.append(c)
            v = (c.get("code") or "").strip()
            if v:
                cats_by_code[v.zfill(4)] = c["id"]
                cats_by_code[v] = c["id"]
            a = (c.get("account_number") or "").strip()
            if a:
                cats_by_account[a] = c["id"]

        # ---- iter90gj : regroupement des lignes de detail multi-ligne ----
        # Optipro exporte 1 ligne CSV/PDF par ligne de detail comptable
        # (compte 61300 + compte 6160 sur la meme facture => 2 lignes).
        # Sans regroupement, on cree 2 factures avec le meme n° externe -> doublons.
        # Strategie : regrouper par internal_ref OU (external_ref + supplier + date).
        def _group_key(inv: dict) -> str:
            ir = (inv.get("internal_ref") or "").strip()
            if ir:
                return f"IR:{ir}"
            er = (inv.get("external_ref") or "").strip()
            sup = (inv.get("supplier_aux_code") or "").strip()
            dt = (inv.get("date") or "").strip()
            return f"EX:{er}|{sup}|{dt}"

        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for inv in data.invoices:
            k = _group_key(inv)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(inv)

        merged_invoices: list[dict] = []
        for k in order:
            lines = groups[k]
            if len(lines) == 1:
                merged_invoices.append(lines[0])
                continue
            head = dict(lines[0])
            # Recalcule les totaux depuis les lignes (fiable si Optipro ne
            # fournit le total qu'a la 1ere ligne ou 0 sur les suivantes).
            total_ht = round(sum(float(l.get("montant_ht") or 0) for l in lines), 2)
            total_tvac = round(sum(float(l.get("montant_tvac") or 0) for l in lines), 2)
            head["montant_ht"] = total_ht
            head["montant_tvac"] = total_tvac
            head["montant_tva"] = round(total_tvac - total_ht, 2)
            # Serialise les lignes de detail pour la persistance dans
            # invoice.distribution_lines (voir commit_invoices ci-dessous).
            head["_split_lines"] = [
                {
                    "account_number": (l.get("account_number") or "").strip(),
                    "account_label": (l.get("account_label") or "").strip(),
                    "dist_key_code": (l.get("dist_key_code") or "").strip(),
                    "nature_code": (l.get("nature_code") or "").strip(),
                    "libelle": (l.get("libelle") or "").strip(),
                    "montant_ht": float(l.get("montant_ht") or 0),
                    "montant_tvac": float(l.get("montant_tvac") or 0),
                    "part_occupant": float(l.get("part_occupant") or 0),
                    "part_proprietaire": float(l.get("part_proprietaire") or 0),
                }
                for l in lines
            ]
            merged_invoices.append(head)

        # ---- Pre-pass : collect all PCMN accounts that will be needed ----
        accounts_needed: dict[str, str] = {}
        for inv in merged_invoices:
            acc_num = (inv.get("account_number") or "").strip()
            acc_lbl = (inv.get("account_label") or "").strip()
            if acc_num:
                accounts_needed[acc_num] = acc_lbl
            # iter90gj : couvre aussi les comptes des lignes de detail regroupees
            for sl in (inv.get("_split_lines") or []):
                if sl.get("account_number"):
                    accounts_needed[sl["account_number"]] = sl.get("account_label") or ""
                sup_aux = (inv.get("supplier_aux_code") or "").upper().strip()
                if sup_aux.startswith("F") and len(sup_aux) >= 5:
                    # iter90io : PCMN supplier sub-account normalise en 8 chars
                    # (format canonique "44000XXX"). L'ancien format "4400XXX"
                    # (7 chars) creait des doublons dans le Bilan avec le
                    # canonique 8 chars deja utilise par assign_supplier_account.
                    from tier_accounts import canonize_supplier_tier_account
                    sup_pcmn = canonize_supplier_tier_account("4400" + sup_aux[1:].zfill(3))
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
        private_fees_detected = 0
        for idx, inv in enumerate(merged_invoices):
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
                # iter90gk : fallback matching par nom si aux_code absent/inconnu.
                # Certaines factures Optipro n'ont pas d'aux_code ou l'aux_code
                # ne matche pas la fiche existante -> matcher par nom pour eviter
                # de creer des lignes AC orphelines (sans tpid) sur un compte
                # tier calcule "4400" + aux[1:] different du tier de la fiche.
                if not supplier_doc:
                    from routes.suppliers import _norm_name_candidates
                    inv_name = (inv.get("supplier_name") or "").strip()
                    if inv_name:
                        inv_cands = _norm_name_candidates(inv_name)
                        for aux_key, s_doc in sup_by_aux.items():
                            other_cands = _norm_name_candidates(s_doc.get("name", ""))
                            if inv_cands & other_cands:
                                supplier_doc = s_doc
                                break
                        # Elargir : chercher parmi TOUS les fournisseurs de l'ACP
                        # (pas seulement ceux avec aux_code).
                        if not supplier_doc:
                            async for cand in db.suppliers.find(
                                {"copropriete_id": copro_id}, {"_id": 0}
                            ):
                                other_cands = _norm_name_candidates(cand.get("name", ""))
                                if inv_cands & other_cands:
                                    supplier_doc = cand
                                    break
                # iter90gk : s'assurer que la fiche fournisseur a un tier_account
                # dans cette ACP AVANT de creer l'ecriture AC (evite la creation
                # d'un compte tier oriente Optipro qui ne correspond pas au
                # compte tier canonique de la fiche).
                if supplier_doc:
                    from tier_accounts import assign_supplier_account
                    supplier_doc = await assign_supplier_account(db, supplier_doc, copro_id)
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

                # iter90g9 : si le fichier source Optipro/CODA n'a pas fourni
                # de compte comptable ET qu'une nature de depense a ete
                # matchee, derive account_num de la nature (elle-meme liee
                # a un compte PCMN). Sans cela, la facture serait importee
                # sans account_number -> tombe dans "Autres charges" du
                # decompte -> violation PCMN.
                if not account_num and expense_cat_id:
                    cat_doc = next(
                        (c for c in expense_cats if c.get("id") == expense_cat_id),
                        None,
                    )
                    if cat_doc and cat_doc.get("account_number"):
                        account_num = (cat_doc.get("account_number") or "").strip()

                # iter90g9 : verrou PCMN - refuse l'import d'une facture sans
                # compte comptable resoluble. On la met dans errors[] avec un
                # message explicite pour que le syndic corrige la source ou
                # cree la nature manquante avant de rejouer l'import.
                if not account_num:
                    errors.append({
                        "row": idx,
                        "error": (
                            "Compte comptable manquant (ni nature_code, ni "
                            "account_number resoluble). Corrigez le fichier "
                            "source ou creez la nature de depense correspondante."
                        ),
                    })
                    continue

                total_amount = float(inv.get("montant_tvac") or 0)
                vat_amount = float(inv.get("montant_tva") or 0)
                # iter90gl : detection note de credit (facture negative).
                # Optipro exporte les NC avec un montant TVAC negatif. Sans
                # ce traitement, l'ecriture AC etait skipee (garde total_amount>0)
                # -> la NC n'apparaissait pas au compte tier fournisseur et le
                # remboursement bancaire creait un solde fictif a payer (cas
                # Engie -57.03 EUR rapporte par l'utilisateur : "tu n'as
                # comptabilise que le remboursement du fournisseur").
                # Sens comptable NC : DEBIT compte tier (reduit dette) /
                # CREDIT compte de charge (reduit la charge deja passee).
                is_credit_note = total_amount < 0
                total_amount_abs = abs(total_amount)
                vat_amount = abs(vat_amount) if is_credit_note else vat_amount
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
                # iter90gk : PRIORITE STRICTE au compte tier declare dans la
                # fiche fournisseur (via assign_supplier_account). Le calcul
                # historique "4400" + aux[1:] etait la CAUSE des lignes AC
                # orphelines (compte 44001115 pour Sneyers alors que la fiche
                # a 44000006, compte 44000216 pour Baloise alors que fiche a
                # 44000005) qui apparaissaient dans le Bilan mais pas dans la
                # Balance des Tiers -> Bilan desequilibre.
                sup_pcmn = ""
                if supplier_doc:
                    sup_pcmn = ((supplier_doc.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
                if not sup_pcmn and supplier_aux.startswith("F") and len(supplier_aux) >= 5:
                    # Fallback historique (aucune fiche fournisseur trouvee) : on
                    # derive un compte tier depuis l'aux Optipro. Cette branche
                    # ne devrait plus etre atteinte grace au matching par nom
                    # ajoute plus haut, mais on la garde pour ne pas casser les
                    # imports historiques ou les CSV sans mapping fiche.
                    # iter90io : format canonique 8 chars ("44000XXX"). L'ancien
                    # "4400" + zfill(3) (7 chars) creait des doublons Bilan.
                    from tier_accounts import canonize_supplier_tier_account
                    sup_pcmn = canonize_supplier_tier_account("4400" + supplier_aux[1:].zfill(3))
                supplier_label = (inv.get("supplier_name") or supplier_aux).strip()

                # ---- Create journal entry (Achats - AC) ----
                # iter90gl : gere aussi les Notes de Credit (NC / avoir) qui
                # ont un montant negatif. Pour une NC : inverse les signes
                # debit/credit (DEBIT compte tier fournisseur, CREDIT compte
                # de charge) pour reduire correctement les soldes.
                je_id = ""
                if account_num and sup_pcmn and total_amount_abs > 0:
                    je_id = str(uuid.uuid4())
                    if is_credit_note:
                        # NC : DEBIT compte tier / CREDIT charge (inverse d'une facture)
                        expense_debit = 0.0
                        expense_credit = total_amount_abs
                        supplier_debit = total_amount_abs
                        supplier_credit = 0.0
                        desc_prefix = "NC"
                    else:
                        # Facture normale : DEBIT charge / CREDIT compte tier
                        expense_debit = total_amount_abs
                        expense_credit = 0.0
                        supplier_debit = 0.0
                        supplier_credit = total_amount_abs
                        desc_prefix = "DA"
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
                                "debit": expense_debit,
                                "credit": expense_credit,
                                "description": (inv.get("libelle") or "").strip(),
                                "occupant_pct": occ_pct,
                                "proprietaire_pct": prop_pct,
                            },
                            {
                                "account_number": sup_pcmn,
                                "account_name": supplier_label,
                                # iter90gk : lie explicitement la ligne compte tier
                                # a la fiche fournisseur pour que la Balance des
                                # Tiers et le Bilan agregent correctement (evite
                                # les lignes orphelines qui apparaissaient sur
                                # les 2 cotes du bilan pour le meme fournisseur).
                                "third_party_id": supplier_id or None,
                                "third_party_type": "supplier" if supplier_id else None,
                                "debit": supplier_debit,
                                "credit": supplier_credit,
                                "description": f"{desc_prefix} {internal_ref}",
                                "occupant_pct": None,
                                "proprietaire_pct": None,
                            },
                        ],
                        "total_debit": total_amount_abs,
                        "total_credit": total_amount_abs,
                        "copropriete_id": copro_id,
                        "import_session_id": session_id,
                        "source_invoice_id": invoice_id,
                        # iter90gl : trace explicite du type d'ecriture
                        "is_credit_note": is_credit_note,
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
                    # iter90gl : marque explicite pour la UI (Balance des Tiers,
                    # Journaux, Situation de compte fournisseur).
                    "is_credit_note": is_credit_note,
                    "account_number": account_num,
                    "expense_category_id": expense_cat_id,
                    "distribution_key_id": dist_key_id,
                    # iter90gj : lignes de detail multi-compte regroupees en une seule facture
                    "distribution_lines": inv.get("_split_lines") or [],
                    "status": "do_not_pay" if inv.get("ne_pas_payer") else "unpaid",
                    "copropriete_id": copro_id,
                    # iter90gj : detection auto des frais privatifs par compte 643xxx.
                    # `private_fee_owner_id` reste vide : le syndic doit assigner
                    # le/les proprietaire(s) via l'invite post-mutations (Phase 3).
                    "is_private_fee": account_num.startswith("643"),
                    "private_fee_owner_id": "",
                    "private_fee_allocations": [],  # rempli par la modale post-import
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
                if doc["is_private_fee"]:
                    private_fees_detected += 1
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
            "grouped": len(data.invoices) - len(merged_invoices),
            "private_fees_detected": private_fees_detected,
        })
        return {
            "inserted": inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "errors": errors,
            "matched_supplier": matched_supplier,
            "matched_key": matched_key,
            "matched_category": matched_category,
            "grouped": len(data.invoices) - len(merged_invoices),
            "private_fees_detected": private_fees_detected,
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
                cp_name_for_je = (t.get("counterparty_name") or "").strip()
                cp_aux_for_je = (t.get("counterparty_aux") or "").strip()
                # Enrichi : nom du fournisseur + lib bancaire si possible
                enriched_label = cp_name_for_je or cp_label
                enriched_desc = libelle
                if cp_name_for_je and cp_name_for_je.lower() not in libelle.lower():
                    enriched_desc = f"{cp_name_for_je} - {libelle}" if libelle else cp_name_for_je
                if bank_pcmn and cp_pcmn and amount > 0 and direction in ("in", "out"):
                    je_id = str(uuid.uuid4())
                    # Try to resolve the third_party (supplier) for the counter-party line
                    tp_id_je = ""
                    if cp_aux_for_je:
                        s_doc = await db.suppliers.find_one(
                            {"copropriete_id": copro_id, "auxiliary_code": cp_aux_for_je},
                            {"_id": 0, "id": 1},
                        )
                        if s_doc:
                            tp_id_je = s_doc["id"]
                    if direction == "in":
                        lines = [
                            {"account_number": bank_pcmn, "account_name": bank_label, "debit": amount, "credit": 0.0,
                             "description": enriched_desc, "occupant_pct": None, "proprietaire_pct": None},
                            {"account_number": cp_pcmn, "account_name": enriched_label, "debit": 0.0, "credit": amount,
                             "description": enriched_desc, "occupant_pct": None, "proprietaire_pct": None,
                             **({"third_party_id": tp_id_je, "third_party_type": "supplier"} if tp_id_je else {})},
                        ]
                    else:  # out
                        lines = [
                            {"account_number": cp_pcmn, "account_name": enriched_label, "debit": amount, "credit": 0.0,
                             "description": enriched_desc, "occupant_pct": None, "proprietaire_pct": None,
                             **({"third_party_id": tp_id_je, "third_party_type": "supplier"} if tp_id_je else {})},
                            {"account_number": bank_pcmn, "account_name": bank_label, "debit": 0.0, "credit": amount,
                             "description": enriched_desc, "occupant_pct": None, "proprietaire_pct": None},
                        ]
                    await db.journal_entries.insert_one({
                        "id": je_id,
                        "journal_type": "FI",
                        "date": date_v,
                        "reference": num_doc,
                        "description": enriched_desc,
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
                    # Signed amount : IN = positive, OUT = negative (NextGe Copro convention)
                    signed_amount = amount if direction == "in" else (-amount if direction == "out" else amount)
                    # Use the REAL supplier/owner name from the CSV if available
                    # (Optipro 'Identite' column), else fallback to the generic
                    # account label ("Fournisseurs" / "Frais bancaires...").
                    cp_name = (t.get("counterparty_name") or "").strip() or cp_label
                    cp_aux = (t.get("counterparty_aux") or "").strip()
                    # Try to resolve the matched supplier_id via auxiliary_code
                    matched_supplier_id = ""
                    if cp_aux:
                        sup_doc = await db.suppliers.find_one(
                            {"copropriete_id": copro_id, "auxiliary_code": cp_aux},
                            {"_id": 0, "id": 1},
                        )
                        if sup_doc:
                            matched_supplier_id = sup_doc["id"]
                    txn_doc = {
                        "id": str(uuid.uuid4()),
                        "statement_id": stmt_id,
                        "copropriete_id": copro_id,
                        "date": date_v,
                        "amount": signed_amount,
                        "counterparty_name": cp_name,
                        "counterparty_aux": cp_aux,
                        "counterparty_supplier_id": matched_supplier_id,
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

        # ---- AUTO-LETTRAGE : matcher chaque transaction "out" (paiement)
        # avec une facture impayee du meme fournisseur et meme montant. Permet
        # au syndic d'eviter le lettrage manuel un-par-un pour les imports
        # massifs Optipro. Tolerance : montant identique a 0.01 EUR pres,
        # facture la plus ancienne d'abord. ----
        auto_matched = 0
        unmatched_txns = await db.bank_transactions.find({
            "copropriete_id": copro_id,
            "import_session_id": session_id,
            "matched": False,
            "counterparty_supplier_id": {"$ne": ""},
        }, {"_id": 0}).to_list(10000)
        for txn in unmatched_txns:
            sup_id = txn.get("counterparty_supplier_id", "")
            amt_signed = float(txn.get("amount") or 0)
            if amt_signed >= 0 or not sup_id:
                # IN movements (positive) ne sont pas des paiements de factures
                continue
            target_amount = round(abs(amt_signed), 2)
            # Find unpaid invoice of this supplier with this amount
            inv = await db.invoices.find_one({
                "copropriete_id": copro_id,
                "supplier_id": sup_id,
                "status": {"$ne": "paid"},
                "total_amount": {"$gte": target_amount - 0.015, "$lte": target_amount + 0.015},
            }, sort=[("date", 1)])
            if not inv:
                continue
            await db.bank_transactions.update_one(
                {"id": txn["id"]},
                {"$set": {
                    "matched": True,
                    "matched_type": "invoice",
                    "match_type": "invoice",
                    "matched_id": inv["id"],
                    "matched_to": inv["id"],
                    "matched_at": _now_iso(),
                    "match_source": "auto_import_journals",
                }},
            )
            await db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {"status": "paid", "paid_at": txn.get("date", "")}},
            )
            auto_matched += 1

        await _update_step(db, session_id, "journals", {
            "count": inserted,
            "statements_created": stmts_inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "auto_matched": auto_matched,
            "errors": errors,
        })
        return {
            "inserted": inserted,
            "statements_created": stmts_inserted,
            "journal_entries": je_inserted,
            "pcmn_created": pcmn_created,
            "auto_matched": auto_matched,
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
        # iter90gi : fallback sur l'exercice ouvert de l'ACP si aucun fiscal_year_id
        # n'est fourni (etape fiscal_year retiree du wizard).
        entry_date = ""
        fy = None
        if data.fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": data.fiscal_year_id, "copropriete_id": copro_id})
        if not fy:
            fy = await db.fiscal_years.find_one(
                {"copropriete_id": copro_id, "status": "open"},
                sort=[("created_at", -1)],
            )
        if fy:
            data.fiscal_year_id = fy["id"]
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

        # iter90gm : indexe les comptes PCMN 55XXXX (bancaires) deja existants
        # pour re-utiliser le canonique 8-char si l'AN veut creer un 6-char
        # equivalent. Sans ce mapping, le Bilan affiche 2 lignes bancaires
        # dupliquees (une du wizard, une du module bank_txn).
        existing_bank_accs: set = set()
        async for pa in db.pcmn_accounts.find(
            {"copropriete_id": copro_id, "number": {"$regex": "^55[0-9]"}},
            {"_id": 0, "number": 1},
        ):
            existing_bank_accs.add(pa["number"])

        def _canonize_bank_account(acc: str) -> str:
            """iter90gm : si acc est un 6-char bancaire (551331) et qu'un
            canonique 8-char existe (55133100), retourne le canonique.
            Sinon retourne acc inchange."""
            if len(acc) == 6 and acc.startswith("55"):
                cand = acc + "00"
                if cand in existing_bank_accs:
                    return cand
            return acc

        # iter90io : normalisation systematique des comptes tier fournisseurs
        # au format canonique 8 chars ("44000XXX") AVANT le matching. Sans ce
        # helper, un import Optipro pouvait produire des lignes AN avec un
        # compte 7 chars ("4400015") qui coexistaient dans la DB avec le
        # canonique 8 chars ("44000015") deja utilise par le module courant
        # (assign_supplier_account). Consequence : deux lignes fournisseur
        # distinctes dans le Bilan / Balance des Tiers pour LA MEME entite.
        from tier_accounts import canonize_supplier_tier_account as _canonize_sup_acc

        # iter90gk : indexe TOUS les suppliers de l'ACP (pas seulement ceux
        # avec aux_code) pour permettre le matching par nom en fallback
        # quand le compte tier orphelin ne matche aucun aux_code.
        all_suppliers_acp: list[dict] = []
        async for sup in db.suppliers.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "name": 1, "auxiliary_code": 1, "tier_accounts": 1}):
            all_suppliers_acp.append(sup)

        def _resolve_third_party(account_number: str, label: str = "") -> tuple:
            """Returns (third_party_id, third_party_type, party_doc, canonical_account) or (None, None, None, None).

            iter90gk : ajout du matching par NOM (label) en fallback quand
            l'aux_code ne matche aucune fiche. Retourne aussi le compte tier
            canonique de la fiche fournisseur pour REECRIRE le numero de
            compte de la ligne AN (evite les orphelins 44001115 / 44000216
            qui doublonnent avec le tier canonique 44000005 dans le bilan).

            iter90ig : meme logique appliquee aux OWNERS. Optipro utilise
            des comptes 7-char (4100959, 4100956...) qui, si laisses tels
            quels dans la ligne AN, generent des DOUBLONS avec les comptes
            canoniques 4101XXXX/4100XXXX crees automatiquement par notre
            systeme via `assign_owner_accounts`. La balance des tiers
            affichait alors 2 lignes par proprietaire ("Bon compte" +
            "Ex-prop.") et le lettrage bancaire ne savait plus quel
            compte utiliser. On renvoie desormais le compte canonique du
            owner pour l'ACP concernee et la ligne AN sera reecrite.
            """
            acc = (account_number or "").strip()
            if not acc:
                return None, None, None, None
            # 410xxxx -> owner (provisions / fonds de roulement Optipro)
            if acc.startswith("410") and len(acc) >= 7:
                aux = "C" + acc[-4:]
                o = owners_by_aux.get(aux)
                if o:
                    canonical = ((o.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("provisions", "")
                    return o["id"], "owner", o, canonical or None
            # 4001xxxx -> owner (fonds de reserve Optipro legacy)
            if acc.startswith("4001") and len(acc) >= 8:
                aux = "C" + acc[-4:]
                o = owners_by_aux.get(aux)
                if o:
                    canonical = ((o.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("reserve", "")
                    return o["id"], "owner", o, canonical or None
            # 440xxxx -> supplier (aux match first, then name match)
            if acc.startswith("440") and len(acc) >= 5:
                aux = "F" + acc[-4:]
                sup = suppliers_by_aux.get(aux)
                if sup:
                    # iter90is : lit directement `tier_account_number` (chinese wall).
                    canonical = (sup.get("tier_account_number") or "").strip() or (
                        (sup.get("tier_accounts") or {}).get(copro_id, {}) or {}
                    ).get("main", "")
                    return sup["id"], "supplier", sup, canonical or None
                # iter90gk : fallback name matching (Levenshtein-lite via
                # _norm_name_candidates). Evite les orphelins pour les
                # bilans d'ouverture ou l'Optipro utilise un numero de
                # compte different de celui declare dans la fiche.
                if label:
                    from routes.suppliers import _norm_name_candidates
                    lbl_cands = _norm_name_candidates(label)
                    if lbl_cands:
                        for cand in all_suppliers_acp:
                            other_cands = _norm_name_candidates(cand.get("name", ""))
                            if lbl_cands & other_cands:
                                canonical = (cand.get("tier_account_number") or "").strip() or (
                                    (cand.get("tier_accounts") or {}).get(copro_id, {}) or {}
                                ).get("main", "")
                                return cand["id"], "supplier", cand, canonical or None
            return None, None, None, None

        # iter90ig : PRE-ASSIGN tier_accounts pour tous les owners de l'ACP
        # AVANT de construire les lignes AN. Sinon, les nouveaux owners
        # crees via PdfImportDialog (sans tier_accounts) recevraient le
        # compte Optipro comme "canonical", ce qui perpetuerait le bug.
        from tier_accounts import assign_owner_accounts as _assign_owner_accounts
        from tier_accounts import assign_supplier_account as _assign_supplier_account
        # On charge les owners candidats (aux_code C0XXX matche par cet AN)
        # et on garantit qu'ils ont provisions + reserve pour cette ACP.
        aux_codes_in_an = set()
        aux_codes_sup_in_an = set()
        for a in actif + passif:
            acc = (a.get("account") or "").strip()
            # iter90io : normaliser en amont pour que l'extraction de l'aux
            # code se fasse sur un format unique (44000XXX en 8 chars).
            acc = _canonize_sup_acc(acc)
            if acc.startswith("410") and len(acc) >= 7:
                aux_codes_in_an.add("C" + acc[-4:])
            elif acc.startswith("4001") and len(acc) >= 8:
                aux_codes_in_an.add("C" + acc[-4:])
            elif acc.startswith("440") and len(acc) >= 5:
                aux_codes_sup_in_an.add("F" + acc[-4:])
        for aux in aux_codes_in_an:
            o = owners_by_aux.get(aux)
            if not o:
                continue
            ta = (o.get("tier_accounts") or {}).get(copro_id, {}) or {}
            if ta.get("provisions") and ta.get("reserve"):
                # iter90ig : garde-fou anti-BUG legacy - si "provisions" est un
                # compte Optipro 7-char (4100XXX au lieu de 4101XXXX), on
                # remet a zero pour que assign_owner_accounts recree le bon.
                bad_prov = ta.get("provisions") and (
                    len(ta["provisions"]) == 7 or not ta["provisions"].startswith(("4101", "40000"))
                )
                bad_res = ta.get("reserve") and (
                    len(ta["reserve"]) == 7 or not ta["reserve"].startswith(("4100", "40010"))
                )
                if not bad_prov and not bad_res:
                    continue
                # Reset les cles corrompues
                fresh = {**ta}
                if bad_prov:
                    fresh.pop("provisions", None)
                if bad_res:
                    fresh.pop("reserve", None)
                await db.owners.update_one(
                    {"id": o["id"]},
                    {"$set": {f"tier_accounts.{copro_id}": fresh}},
                )
                o["tier_accounts"] = {**(o.get("tier_accounts") or {}), copro_id: fresh}
            # Cree provisions + reserve manquants (idempotent)
            o = await _assign_owner_accounts(db, o, copro_id)
            owners_by_aux[aux] = o  # refresh cache

        # iter90ih : meme logique pour les SUPPLIERS. Cas identique au bug
        # owners : si un supplier n'a pas encore `tier_accounts.main` canonique
        # (44000XXX) au moment de l'AN, l'ancien code lui assignait le compte
        # Optipro 7-char (4400015, 44001115, etc.) comme main -> lettrage
        # bancaire cassé + doublons dans la balance des tiers fournisseurs.
        for aux in aux_codes_sup_in_an:
            sup = suppliers_by_aux.get(aux)
            if not sup:
                continue
            ta = (sup.get("tier_accounts") or {}).get(copro_id, {}) or {}
            main = ta.get("main", "")
            # Bad si 7-char (Optipro) ou ne commence pas par 44000
            bad_main = main and (len(main) != 8 or not main.startswith("44000"))
            if bad_main:
                fresh = {k: v for k, v in ta.items() if k != "main"}
                await db.suppliers.update_one(
                    {"id": sup["id"]},
                    {"$set": {f"tier_accounts.{copro_id}": fresh}},
                )
                sup["tier_accounts"] = {**(sup.get("tier_accounts") or {}), copro_id: fresh}
            sup = await _assign_supplier_account(db, sup, copro_id)
            suppliers_by_aux[aux] = sup  # refresh cache

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
            # iter90gm : canonize les comptes bancaires (55XXXX -> 55XXXX00) si
            # un compte 8-char equivalent existe deja (evite les doublons dans
            # le Bilan entre comptes AN et comptes operations bancaires).
            acc_num = _canonize_bank_account(acc_num)
            # iter90io : canonize les comptes tier fournisseurs (440XXX -> 44000XXX)
            # pour uniformiser le format AVANT le matching et l'insertion en DB.
            acc_num = _canonize_sup_acc(acc_num)
            label = (a.get("label") or "").strip()
            tp_id, tp_type, party, canonical_acc = _resolve_third_party(acc_num, label=label)
            # iter90gk / iter90ig : si un compte canonique existe pour ce
            # tiers (owner OU supplier), on reecrit la ligne AN avec ce
            # compte-la. La description conserve la trace du compte Optipro
            # source pour audit.
            source_acc = acc_num
            if canonical_acc and canonical_acc != acc_num:
                acc_num = canonical_acc
            line = {
                "account_number": acc_num,
                "account_name": label,
                "debit": amt if is_actif else 0.0,
                "credit": 0.0 if is_actif else amt,
                "description": f"A-Nouveau : {label}",
                "line_description": (
                    f"A-Nouveau {source_acc}" + (f" -> {acc_num}" if acc_num != source_acc else "")
                    + (f" - {party.get('name','')}" if party else "")
                ),
                "occupant_pct": None,
                "proprietaire_pct": None,
            }
            if tp_id:
                line["third_party_id"] = tp_id
                line["third_party_type"] = tp_type
                if tp_type == "owner":
                    owners_linked += 1
                    # iter90ig : NE PLUS ECRASER tier_accounts avec le compte
                    # Optipro. Le compte canonique est deja assigne par
                    # `assign_owner_accounts` (pre-assign ci-dessus). On ne
                    # planifie une mise a jour QUE si le compte Optipro est
                    # different du canonique (ex : legacy AN avec compte
                    # exotique 4001XXX non-standard).
                    if not canonical_acc:
                        key = "reserve" if source_acc.startswith("4001") else "provisions"
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
            # iter90gk : marque cette AN comme "ouverture wizard" (pas une AN
            # de cloture d'exercice). Le Bilan doit INCLURE ces AN-la
            # (contrairement aux AN de cloture qui sont des duplicats
            # cumulatifs de l'exercice N-1).
            "is_opening_balance": True,
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

        # iter90gj : sauvegarde des appels hors budget sur l'exercice fiscal.
        # Ces informations sont utilisees par :
        # 1. Le calcul de prorata des mutations (roulement_fund.opening_balance)
        # 2. Les rapports "Etat des fonds" (bilan par fonds)
        # 3. La generation planifiee des appels de fonds (reserve_fund)
        funds_config = data.funds_config or {}
        funds_saved = False
        if funds_config and fy:
            fy_update: dict = {}
            rf = funds_config.get("reserve_fund") or {}
            rolf = funds_config.get("roulement_fund") or {}
            if rf:
                fy_update["reserve_fund_opening_balance"] = round(float(rf.get("opening_balance") or 0), 2)
                fy_update["reserve_fund_has_annual_call"] = bool(rf.get("has_annual_call"))
                fy_update["reserve_fund_call_amount"] = round(float(rf.get("call_amount") or 0), 2)
                fy_update["reserve_fund_call_frequency"] = rf.get("call_frequency") or ""
            if rolf:
                fy_update["roulement_fund_opening_balance"] = round(float(rolf.get("opening_balance") or 0), 2)
                fy_update["roulement_fund_has_increase"] = bool(rolf.get("has_increase"))
                fy_update["roulement_fund_new_total"] = round(float(rolf.get("new_total") or 0), 2)
            if fy_update:
                fy_update["funds_config_updated_at"] = _now_iso()
                await db.fiscal_years.update_one(
                    {"id": fy["id"]}, {"$set": fy_update},
                )
                funds_saved = True

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
            "funds_saved": funds_saved,
        }

    # ----- C-BIS: OD YEAR-END ENTRIES -----
    @router.post("/sessions/{session_id}/commit-od-entries")
    async def commit_od_entries(session_id: str, data: CommitOdEntriesInput, request: Request):
        """Commit year-end OD (Operations Diverses) entries.

        Supports two payload formats :

        A) Liste des depenses (legacy) - each entry has charge+counterpart+amount :
           {date, libelle, account_number, account_name, amount,
            counterpart_account, counterpart_account_name,
            proprietaire_pct, occupant_pct}

        B) Journal comptable OD (preferred) - each entry has explicit lines :
           {date, reference, description, lines: [{account_number, account_name,
            auxiliary_info, debit, credit}], included: True/False}

        For format A : a 2-line balanced JE is auto-built.
        For format B : the input lines ARE the JE lines (already balanced).

        The endpoint :
          1. Validates (A) every entry has counterpart OR (B) every entry is balanced.
          2. Auto-creates the PCMN accounts if missing.
          3. Resolves auxiliary_info (e.g. "C1996 M. brumagne") -> third_party_id.
          4. Skips entries whose signature already exists (idempotent).
        """
        from fiscal_lock import ensure_period_open
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)

        entries = data.entries or []
        if not entries:
            raise HTTPException(400, "Aucune ecriture OD a importer")

        # ---- Detect payload format ----
        # Format B if first entry has non-empty 'lines'
        is_journal_od = bool(entries[0].get("lines"))

        # ---- Pre-flight ----
        if is_journal_od:
            unbalanced = []
            for i, e in enumerate(entries):
                if not e.get("included", True):
                    continue
                lines_in = e.get("lines") or []
                sum_d = round(sum(float(ln.get("debit") or 0) for ln in lines_in), 2)
                sum_c = round(sum(float(ln.get("credit") or 0) for ln in lines_in), 2)
                if abs(sum_d - sum_c) > 0.01:
                    unbalanced.append({"idx": i, "ref": e.get("reference", ""), "diff": sum_d - sum_c})
            if unbalanced:
                raise HTTPException(
                    400,
                    f"{len(unbalanced)} ecriture(s) desequilibree(s). Verifiez le PDF source. "
                    f"Exemples : {[u['ref'] for u in unbalanced[:3]]}",
                )
        else:
            missing_counterpart = [
                i for i, e in enumerate(entries)
                if not (e.get("counterpart_account") or "").strip()
            ]
            if missing_counterpart:
                raise HTTPException(
                    400,
                    f"{len(missing_counterpart)} ecriture(s) sans contrepartie definie : "
                    f"lignes {missing_counterpart[:5]}{'...' if len(missing_counterpart) > 5 else ''}. "
                    "Choisissez une contrepartie pour chaque ligne avant validation.",
                )

        # ---- Auto-create missing PCMN accounts ----
        accounts_needed: dict[str, str] = {}
        if is_journal_od:
            for e in entries:
                if not e.get("included", True):
                    continue
                for ln in (e.get("lines") or []):
                    num = (ln.get("account_number") or "").strip()
                    if num:
                        accounts_needed[num] = (ln.get("account_name") or "").strip() or num
        else:
            for e in entries:
                for k_acc, k_name in [
                    ("account_number", "account_name"),
                    ("counterpart_account", "counterpart_account_name"),
                ]:
                    num = (e.get(k_acc) or "").strip()
                    lbl = (e.get(k_name) or "").strip()
                    if num:
                        accounts_needed[num] = lbl
        pcmn_created = await _ensure_pcmn_accounts(copro_id, accounts_needed)

        # ---- Pre-load owner/supplier lookup by auxiliary_code ----
        # For Journal OD : lines like "Coproprietaires | C1996 M. brumagne"
        # need to be linked to the canonical owner id.
        import re as _re_aux
        aux_re = _re_aux.compile(r"^(C|F)(\d{3,5})\b")
        owner_by_aux: dict[str, str] = {}
        async for o in db.owners.find(
            {"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0, "id": 1, "auxiliary_code": 1},
        ):
            ac = (o.get("auxiliary_code") or "").upper().strip()
            if ac:
                owner_by_aux.setdefault(ac, o["id"])
        supplier_by_aux: dict[str, str] = {}
        async for s in db.suppliers.find(
            {"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0, "id": 1, "auxiliary_code": 1},
        ):
            ac = (s.get("auxiliary_code") or "").upper().strip()
            if ac:
                supplier_by_aux.setdefault(ac, s["id"])

        # ---- Build OD journal entries ----
        inserted = 0
        skipped = 0
        errors: list[dict] = []
        existing_count = await db.journal_entries.count_documents({
            "copropriete_id": copro_id, "journal_type": "OD",
        })
        year_for_ref = (entries[0].get("date", "") or "")[:4] or datetime.now(timezone.utc).strftime("%Y")
        seq = existing_count + 1

        for idx, e in enumerate(entries):
            # Skip excluded entries (Journal OD : closing entries by default)
            if is_journal_od and not e.get("included", True):
                skipped += 1
                continue

            date = (e.get("date") or "").strip()
            if not date:
                errors.append({"idx": idx, "error": "Date manquante"})
                continue

            if is_journal_od:
                ref_source = (e.get("reference") or "").strip()
                libelle = (e.get("description") or "").strip() or f"OD {date}"
                # Idempotence : skip si (date, libelle, source_reference) existe deja
                existing = await db.journal_entries.find_one({
                    "copropriete_id": copro_id,
                    "journal_type": "OD",
                    "date": date,
                    "source_reference": ref_source,
                    "manually_created_from_od_wizard": True,
                }, {"_id": 0, "id": 1})
                if existing:
                    skipped += 1
                    continue
                # Fiscal lock check
                try:
                    await ensure_period_open(db, copro_id, date, context=f"OD year-end {libelle[:40]}")
                except Exception as ex:
                    errors.append({"idx": idx, "error": f"Periode fermee : {ex}"})
                    continue
                # Build lines from the PDF lines (already balanced)
                lines = []
                for ln in (e.get("lines") or []):
                    acc = (ln.get("account_number") or "").strip()
                    name = (ln.get("account_name") or "").strip() or acc
                    aux_raw = (ln.get("auxiliary_info") or "").strip()
                    tpid = ""
                    tptype = ""
                    if aux_raw:
                        m = aux_re.match(aux_raw)
                        if m:
                            code = (m.group(1) + m.group(2)).upper()
                            if m.group(1) == "C":
                                tpid = owner_by_aux.get(code, "")
                                tptype = "owner" if tpid else ""
                            elif m.group(1) == "F":
                                tpid = supplier_by_aux.get(code, "")
                                tptype = "supplier" if tpid else ""
                    lines.append({
                        "account_number": acc,
                        "account_name": name,
                        "description": libelle,
                        "debit": round(float(ln.get("debit") or 0), 2),
                        "credit": round(float(ln.get("credit") or 0), 2),
                        "auxiliary_info": aux_raw,
                        "third_party_id": tpid,
                        "third_party_type": tptype,
                    })
                total_d = round(sum(ln["debit"] for ln in lines), 2)
                total_c = round(sum(ln["credit"] for ln in lines), 2)
                doc = {
                    "id": str(uuid.uuid4()),
                    "journal_type": "OD",
                    "date": date,
                    "reference": f"OD-{year_for_ref}-{seq:04d}",
                    "source_reference": ref_source,
                    "description": libelle,
                    "lines": lines,
                    "total_debit": total_d,
                    "total_credit": total_c,
                    "copropriete_id": copro_id,
                    "manually_created_from_od_wizard": True,
                    "od_source_format": "od_journal",
                    "import_session_id": session_id,
                    "created_at": _now_iso(),
                }
                await db.journal_entries.insert_one(doc)
                inserted += 1
                seq += 1
                continue

            # ---- Format A : Liste des depenses ----
            charge_acc = (e.get("account_number") or "").strip()
            counter_acc = (e.get("counterpart_account") or "").strip()
            charge_name = (e.get("account_name") or "").strip() or charge_acc
            counter_name = (e.get("counterpart_account_name") or "").strip() or counter_acc
            amount = float(e.get("amount") or 0)
            if abs(amount) < 0.005:
                skipped += 1
                continue
            libelle = (e.get("libelle") or "").strip() or f"OD {date}"
            occ_pct = float(e.get("occupant_pct") or 0)
            prop_pct = float(e.get("proprietaire_pct") if e.get("proprietaire_pct") is not None else max(0.0, 100.0 - occ_pct))

            # Idempotence : skip ONLY if EXACT match (date+charge+amount+libelle+source_y)
            # already exists from a previous wizard run.
            sig_amount = round(abs(amount), 2)
            source_y = float(e.get("source_y") or 0)
            source_page = int(e.get("source_page") or 0)
            existing = await db.journal_entries.find_one({
                "copropriete_id": copro_id,
                "journal_type": "OD",
                "date": date,
                "description": libelle,
                "manually_created_from_od_wizard": True,
                "od_source_y": source_y,
                "od_source_page": source_page,
                "lines": {"$elemMatch": {
                    "account_number": charge_acc,
                    "$or": [{"debit": sig_amount}, {"credit": sig_amount}],
                }},
            }, {"_id": 0, "id": 1})
            if existing:
                skipped += 1
                continue

            # Fiscal lock check
            try:
                await ensure_period_open(db, copro_id, date, context=f"OD year-end {libelle[:40]}")
            except Exception as ex:
                errors.append({"idx": idx, "error": f"Periode fermee : {ex}"})
                continue

            # Build balanced double-entry
            if amount > 0:
                lines = [
                    {"account_number": charge_acc, "account_name": charge_name,
                     "description": libelle, "debit": round(amount, 2), "credit": 0.0,
                     "occupant_pct": occ_pct, "proprietaire_pct": prop_pct},
                    {"account_number": counter_acc, "account_name": counter_name,
                     "description": libelle, "debit": 0.0, "credit": round(amount, 2),
                     "occupant_pct": 0.0, "proprietaire_pct": 100.0},
                ]
            else:
                # negative -> release : DEBIT counterpart / CREDIT charge
                lines = [
                    {"account_number": counter_acc, "account_name": counter_name,
                     "description": libelle, "debit": round(abs(amount), 2), "credit": 0.0,
                     "occupant_pct": 0.0, "proprietaire_pct": 100.0},
                    {"account_number": charge_acc, "account_name": charge_name,
                     "description": libelle, "debit": 0.0, "credit": round(abs(amount), 2),
                     "occupant_pct": occ_pct, "proprietaire_pct": prop_pct},
                ]
            doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": date,
                "reference": f"OD-{year_for_ref}-{seq:04d}",
                "description": libelle,
                "lines": lines,
                "total_debit": round(abs(amount), 2),
                "total_credit": round(abs(amount), 2),
                "copropriete_id": copro_id,
                "manually_created_from_od_wizard": True,
                "od_source_y": source_y,
                "od_source_page": source_page,
                "od_source_format": "expense_list",
                "import_session_id": session_id,
                "created_at": _now_iso(),
            }
            await db.journal_entries.insert_one(doc)
            inserted += 1
            seq += 1

        await _update_step(db, session_id, "od_entries", {
            "inserted": inserted, "skipped": skipped, "errors": len(errors),
        })
        return {
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
            "pcmn_created": pcmn_created,
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
        skipped_duplicates = 0
        errors = []
        for idx, nat in enumerate(data.natures):
            try:
                code = (nat.get("code") or "").strip()
                libelle = (nat.get("libelle") or "").strip()
                account_number = (nat.get("account_number") or "").strip()
                if not (code and libelle and account_number):
                    continue
                # iter90ih -> iter90ii : IDEMPOTENCE par NOM (pas par compte).
                # Regle metier revisee : 2 natures peuvent partager le meme
                # compte comptable (ex: "Assurance incendie parties communes"
                # + "Assurance incendie parties privees" toutes 2 sur 6140),
                # mais 2 natures ne peuvent PAS avoir le meme nom dans une
                # meme ACP. On skip si `(copro_id, name)` existe deja.
                existing = await db.expense_categories.find_one(
                    {"copropriete_id": copro_id, "name": libelle},
                    {"_id": 0, "id": 1},
                )
                if existing:
                    skipped_duplicates += 1
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
        await _update_step(db, session_id, "natures", {
            "count": inserted,
            "skipped_duplicates": skipped_duplicates,
            "errors": errors,
        })
        return {
            "inserted": inserted,
            "skipped_duplicates": skipped_duplicates,
            "errors": errors,
        }

    # ----- E: FISCAL YEAR -----
    @router.post("/sessions/{session_id}/commit-fiscal-year")
    async def commit_fiscal_year(session_id: str, data: CommitFiscalYearInput, request: Request):
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        # Idempotence : if an exercice with this name already exists for this
        # ACP, check whether it was created BY THIS SESSION. If yes, return it
        # as-is (so user can navigate back-and-forth in the wizard without
        # getting "L'exercice 2025 existe deja" errors). Otherwise it's a real
        # conflict with a previous import.
        existing = await db.fiscal_years.find_one({"copropriete_id": copro_id, "name": data.name})
        if existing:
            if existing.get("import_session_id") == session_id:
                # Same session : update dates/status in case user edited the form
                update = {
                    "start_date": data.start_date,
                    "end_date": data.end_date,
                    "status": data.status or existing.get("status") or "open",
                }
                await db.fiscal_years.update_one({"id": existing["id"]}, {"$set": update})
                await _update_step(db, session_id, "fiscal_year", {"count": 1, "fiscal_year_id": existing["id"]})
                return {"id": existing["id"], "name": existing["name"], "idempotent": True}
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
        # iter90gi : si le client n'a pas transmis de fiscal_year_id (etape
        # fiscal_year retiree du wizard), on resout via l'exercice ouvert de
        # l'ACP. Fallback safe car iter90gg impose la creation du FY a la
        # creation de l'ACP.
        fy = None
        if data.fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": data.fiscal_year_id})
        if not fy:
            fy = await db.fiscal_years.find_one(
                {"copropriete_id": copro_id, "status": "open"},
                sort=[("created_at", -1)],
            )
        if not fy:
            raise HTTPException(400, "Aucun exercice fiscal ouvert pour cette ACP")
        data.fiscal_year_id = fy["id"]

        # ---- Auto-create distribution_keys for each unique section code ----
        # The Optipro budget PDF structures lines by 'key_code' (e.g. 0001, 0006).
        # Each such code corresponds to a distribution key. We auto-create them
        # if missing so the user doesn't have to maintain them separately.
        # Cles "speciales" (Cle Speciale ascenseurs, eau, etc.) sont taggees
        # is_special=True pour permettre un traitement different en repartition
        # (uniquement les lots concernes, pas tous les coproprietaires).
        existing_keys: dict[str, str] = {}  # code -> id
        async for k in db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "code": 1, "import_code": 1}):
            for fld in ("code", "import_code"):
                v = (k.get(fld) or "").strip()
                if v:
                    existing_keys[v] = k["id"]
                    existing_keys[v.zfill(4)] = k["id"]
        keys_created = 0
        keys_special_created = 0
        section_key_codes: dict[str, dict] = {}
        for sec in data.sections:
            key_code = (sec.get("key_code") or "").strip()
            key_label = (sec.get("key_label") or "").strip()
            if not key_code or key_code in section_key_codes:
                continue
            section_key_codes[key_code] = {
                "code": key_code,
                "label": key_label,
                "is_special": bool(sec.get("is_special", False)),
            }
        for code, info in section_key_codes.items():
            if code in existing_keys or code.zfill(4) in existing_keys:
                # Si la cle existe deja mais qu'elle n'est pas marquee is_special
                # alors que la nouvelle section l'indique, on met a jour le flag.
                if info["is_special"]:
                    await db.distribution_keys.update_one(
                        {"id": existing_keys.get(code) or existing_keys.get(code.zfill(4))},
                        {"$set": {"is_special": True, "updated_at": _now_iso()}},
                    )
                continue
            key_id = str(uuid.uuid4())
            await db.distribution_keys.insert_one({
                "id": key_id,
                "code": code,
                "import_code": code,
                "name": info["label"] or f"Cle {code}",
                "description": "",
                "type": "quotities",
                "key_type": "quotity",
                "is_special": info["is_special"],
                "lines": [],
                "lots": [],
                "copropriete_id": copro_id,
                "import_session_id": session_id,
                "created_at": _now_iso(),
            })
            existing_keys[code] = key_id
            existing_keys[code.zfill(4)] = key_id
            keys_created += 1
            if info["is_special"]:
                keys_special_created += 1

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
            "keys_special_created": keys_special_created,
        })
        return {
            "inserted": len(lines),
            "total_amount": round(total_amount, 2),
            "keys_created": keys_created,
            "keys_special_created": keys_special_created,
        }

    # ----- J: DISTRIBUTION KEYS -----
    @router.post("/sessions/{session_id}/commit-distribution-keys")
    async def commit_distribution_keys(session_id: str, data: CommitDistributionKeysInput, request: Request):
        """Cree les cles de repartition + leurs tantiemes par lot.

        Pour chaque cle : 1 doc distribution_keys (schema unifie avec
        /api/distribution-keys) + matching automatique des lots de l'ACP via
        leur numero extrait du libelle (ex. 'B0-1 - APPARTEMENT' -> 'B0-1').
        """
        session = await db.import_sessions.find_one({"id": session_id})
        if not session:
            raise HTTPException(404, "Session introuvable")
        copro_id = session["copropriete_id"]
        await _require_acp_access(request, db, copro_id)
        # Lookup des lots de l'ACP (toute la base, pas juste la session)
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0, "id": 1, "number": 1, "description": 1}).to_list(2000)
        lots_by_number = {(l.get("number") or "").lower().strip(): l for l in lots}
        lots_by_desc = {(l.get("description") or "").lower().strip(): l for l in lots}

        def _extract_lot_number(raw: str) -> str:
            """Extract the lot number from a raw PDF label.
            'B0-1 - APPARTEMENT' -> 'B0-1', 'Cave 1 - CAVE' -> 'Cave 1',
            'Garage 5' -> 'Garage 5'. Returns lowercase stripped."""
            if not raw:
                return ""
            s = str(raw).strip()
            # Strip everything after " - " separator if present
            if " - " in s:
                s = s.split(" - ", 1)[0].strip()
            return s.lower().strip()

        inserted = 0
        for k in data.keys or []:
            code = (k.get("code") or "").strip()
            name = (k.get("name") or code).strip()
            if not name:
                continue
            existing = await db.distribution_keys.find_one({"copropriete_id": copro_id, "code": code} if code else {"copropriete_id": copro_id, "name": name})
            # Build lots[] : match each input line to a real lot
            api_lots = []
            for line in k.get("lines") or []:
                quotity = float(line.get("quotity") or 0)
                if quotity <= 0:
                    continue
                lot_label = (line.get("lot_label") or "")
                lot_code = (line.get("lot_code") or "")
                # Extract lot number from various raw fields (PDF labels like
                # 'B0-1 - APPARTEMENT' are common). Try in order.
                candidates = [
                    _extract_lot_number(lot_code),
                    _extract_lot_number(lot_label),
                    (lot_code or "").lower().strip(),
                    (lot_label or "").lower().strip(),
                ]
                matched_lot = None
                for cand in candidates:
                    if not cand or cand == "-":
                        continue
                    matched_lot = lots_by_number.get(cand) or lots_by_desc.get(cand)
                    if matched_lot:
                        break
                api_lots.append({
                    "lot_id": matched_lot["id"] if matched_lot else "",
                    "lot_number": matched_lot["number"] if matched_lot else (lot_code or lot_label or ""),
                    "share": quotity,
                    # raw fields kept for traceability
                    "lot_label_raw": line.get("lot_label", ""),
                    "lot_code_raw": line.get("lot_code", ""),
                    "owner_label_raw": line.get("owner_label", ""),
                })
            total_q = float(k.get("total_quotities") or sum(l["share"] for l in api_lots))
            key_type_in = (k.get("type") or "").lower()
            api_key_type = "equal" if key_type_in == "equal" else "quotity"
            if existing:
                # Re-import : on met TOUJOURS a jour la cle existante avec les
                # nouveaux lots / quotites parses du PDF. C'est la responsabilite
                # de l'utilisateur de verifier les valeurs avant commit.
                # Si le PDF ne contient pas de lignes, on conserve les lots existants
                # (sinon on viderait la cle accidentellement).
                update_fields = {
                    "name": name,
                    "key_type": api_key_type,
                    "type": k.get("type") or existing.get("type") or "tantiemes",
                    "import_session_id": session_id,
                    "updated_at": _now_iso(),
                }
                if api_lots:
                    update_fields["total_quotities"] = total_q
                    update_fields["lots"] = api_lots
                    update_fields["lines"] = api_lots
                await db.distribution_keys.update_one(
                    {"id": existing["id"]},
                    {"$set": update_fields},
                )
                inserted += 1
                continue
            doc = {
                "id": str(uuid.uuid4()),
                "code": code,
                "name": name,
                "key_type": api_key_type,
                "type": k.get("type") or "tantiemes",
                "total_quotities": total_q,
                "lots": api_lots,
                "lines": api_lots,  # legacy alias
                "copropriete_id": copro_id,
                "import_session_id": session_id,
                "created_at": _now_iso(),
            }
            await db.distribution_keys.insert_one(doc)
            inserted += 1
        # iter90gj : garantir qu'au moins UNE cle de repartition est marquee
        # comme "par defaut" pour cette ACP. Sans cela, l'endpoint mutation
        # (calcul du prorata fonds de roulement) tombe en erreur "Aucune cle
        # de repartition par defaut trouvee".
        # Priorite : cle "Charges communes" (code 0001) > 1ere cle inseree
        # avec le plus grand nombre de lots.
        default_key = await db.distribution_keys.find_one(
            {"copropriete_id": copro_id, "is_default": True}, {"_id": 0, "id": 1},
        )
        if not default_key:
            candidate = await db.distribution_keys.find_one(
                {"copropriete_id": copro_id, "$or": [
                    {"code": "0001"},
                    {"code": "1"},
                    {"name": {"$regex": "^Charges communes", "$options": "i"}},
                ]},
                {"_id": 0, "id": 1, "name": 1},
            )
            if not candidate:
                # Fallback : la 1ere cle triee par nombre de lots decroissant
                all_keys = await db.distribution_keys.find(
                    {"copropriete_id": copro_id}, {"_id": 0, "id": 1, "name": 1, "lots": 1},
                ).to_list(50)
                if all_keys:
                    all_keys.sort(key=lambda x: -len(x.get("lots") or []))
                    candidate = all_keys[0]
            if candidate:
                await db.distribution_keys.update_one(
                    {"id": candidate["id"]}, {"$set": {"is_default": True}},
                )
                await _update_step(db, session_id, "distribution_keys",
                                    {"count": inserted, "default_key": candidate.get("name", "")})
                return {"inserted": inserted, "default_key_auto": candidate.get("name", "")}
        await _update_step(db, session_id, "distribution_keys", {"count": inserted})
        return {"inserted": inserted}

    # ---- SUMMARY / RECAP D'IMPORT (iter90if) ----
    # Renvoie un recapitulatif complet de ce qui a ete cree pour l'ACP,
    # utilise :
    #  - en fin de wizard (ecran de recap avant redirection)
    #  - depuis la liste des ACP pour detecter les imports incomplets
    #    (banner "Reprendre l'import").
    # Ce endpoint fonctionne AVEC ou SANS session_id (recap ACP-wide).
    @router.get("/coproprietes/{copropriete_id}/import-summary")
    async def import_summary(copropriete_id: str, request: Request):
        await _require_acp_access(request, db, copropriete_id)
        # Fiscal years
        fys = await db.fiscal_years.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(50)
        active_fy = next((f for f in fys if f.get("status") == "open"), None)
        # Compteurs directs
        counts = {
            "fiscal_years": len(fys),
            "lots": await db.lots.count_documents({"copropriete_id": copropriete_id}),
            "owners": await db.owners.count_documents({"copropriete_id": copropriete_id}),
            "suppliers": await db.suppliers.count_documents({"copropriete_id": copropriete_id}),
            "distribution_keys": await db.distribution_keys.count_documents({"copropriete_id": copropriete_id}),
            "natures": await db.expense_categories.count_documents({"copropriete_id": copropriete_id}),
            "invoices": await db.invoices.count_documents({"copropriete_id": copropriete_id}),
        }
        # Budget de l'exercice actif
        budget_doc = None
        if active_fy:
            budget_doc = await db.budgets.find_one(
                {"copropriete_id": copropriete_id, "fiscal_year_id": active_fy["id"]},
                {"_id": 0},
            )
        # OD d'ouverture
        opening_od = await db.journal_entries.count_documents({
            "copropriete_id": copropriete_id,
            "journal_type": "OD",
            "$or": [
                {"description": {"$regex": "ouverture", "$options": "i"}},
                {"reference": {"$regex": "^OD-OPEN", "$options": "i"}},
            ],
        })
        # Session d'import la plus recente
        sess = await db.import_sessions.find_one(
            {"copropriete_id": copropriete_id},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        # Statut par etape (a partir de la session)
        step_status = {}
        for step_key in ["suppliers", "natures", "budget", "distribution_keys",
                         "invoices", "journals", "opening_balance", "od_entries"]:
            sdata = (sess or {}).get("steps", {}).get(step_key) or {}
            step_status[step_key] = {
                "done": bool(sdata),
                "count": sdata.get("count") or sdata.get("inserted") or 0,
                "detail": sdata,
            }
        # Manques prioritaires (utilises par le banner)
        missing = []
        if counts["lots"] == 0:
            missing.append({"key": "lots", "label": "Lots", "critical": True})
        if not budget_doc:
            missing.append({"key": "budget", "label": "Budget previsionnel", "critical": False})
        if counts["distribution_keys"] == 0:
            missing.append({"key": "distribution_keys", "label": "Cles de repartition", "critical": True})
        if counts["natures"] == 0:
            missing.append({"key": "natures", "label": "Natures de depense", "critical": False})
        if opening_od == 0:
            missing.append({"key": "opening_balance", "label": "OD d'ouverture", "critical": False})

        return {
            "copropriete_id": copropriete_id,
            "active_fiscal_year": active_fy,
            "counts": counts,
            "budget": ({
                "id": budget_doc["id"],
                "fiscal_year_id": budget_doc.get("fiscal_year_id"),
                "lines_count": len(budget_doc.get("lines") or []),
                "total_amount": budget_doc.get("total_amount") or budget_doc.get("total") or 0,
                "status": budget_doc.get("status") or "draft",
            } if budget_doc else None),
            "opening_od_entries": opening_od,
            "session": ({
                "id": sess["id"],
                "created_at": sess.get("created_at"),
                "finished_at": sess.get("finished_at"),
                "status": sess.get("status", "in_progress"),
            } if sess else None),
            "step_status": step_status,
            "missing": missing,
            "is_complete": len([m for m in missing if m["critical"]]) == 0 and budget_doc is not None,
        }

    return router


async def _update_step(db, session_id: str, step_key: str, payload: dict):
    """Stores per-step state on the session document."""
    await db.import_sessions.update_one(
        {"id": session_id},
        {"$set": {f"steps.{step_key}": {**payload, "updated_at": _now_iso()}}}
    )
