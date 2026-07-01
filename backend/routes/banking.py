from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import hashlib
import os
import uuid
from auto_entries import generate_bank_entry, _delete_auto_entries


class StatementInput(BaseModel):
    number: str
    date: str
    account_number: Optional[str] = ""
    opening_balance: Optional[float] = 0.0
    closing_balance: Optional[float] = 0.0
    copropriete_id: Optional[str] = ""


class TransactionInput(BaseModel):
    statement_id: Optional[str] = ""
    date: str
    amount: float
    counterparty_name: Optional[str] = ""
    counterparty_account: Optional[str] = ""
    communication: Optional[str] = ""
    transaction_type: Optional[str] = "credit"  # credit or debit
    account_number: Optional[str] = ""
    copropriete_id: Optional[str] = ""
    # ID + type de contrepartie selectionnes EXPLICITEMENT par l'utilisateur via le
    # widget CounterpartySearchSelect. Si fournis, priment sur l'auto-match VCS.
    counterparty_id: Optional[str] = ""
    counterparty_type: Optional[str] = ""  # 'owner' | 'supplier'


class BatchTransactionInput(BaseModel):
    statement_id: str
    transactions: List[TransactionInput]
    copropriete_id: Optional[str] = ""


class LettrageInput(BaseModel):
    transaction_id: str
    match_to_id: str
    match_type: str  # invoice, owner_payment, supplier_payment


class LettrageBatchInput(BaseModel):
    """N transactions vers 1 facture (paiements partiels qui soldent une facture)."""
    transaction_ids: List[str]
    match_to_id: str  # ID de la facture
    match_type: str = "invoice"  # pour l'instant uniquement 'invoice'


class LettrageMultiInvoicesInput(BaseModel):
    """1 transaction vers N factures (un virement qui solde plusieurs factures)."""
    transaction_id: str
    invoice_ids: List[str]


class InlineLineInput(BaseModel):
    date: str
    amount: float
    counterparty_name: Optional[str] = ""
    counterparty_account: Optional[str] = ""
    communication: Optional[str] = ""
    transaction_type: Optional[str] = "credit"
    counterparty_id: Optional[str] = ""
    counterparty_type: Optional[str] = ""


class AddLinesInput(BaseModel):
    lines: List[InlineLineInput]
    copropriete_id: Optional[str] = ""


class CategorySplitInput(BaseModel):
    """Un split de categorisation d'une transaction bancaire (iter90k).
    Ex : facture bancaire trimestrielle 100EUR splittee en 80EUR frais + 20EUR
    commission. Chaque split cible une nature (expense_category) avec sa
    cle de repartition."""
    expense_category_id: str
    distribution_key_id: str
    amount: float
    description: Optional[str] = ""


class CategorizeTransactionInput(BaseModel):
    """Body pour POST /transactions/{txn_id}/categorize."""
    splits: List[CategorySplitInput]


def _ensure_copro_access(request: Request, copro_id: str) -> None:
    """SEC-001 (iter90n) : Enforce chinese wall on by-id / by-body banking
    endpoints where the middleware's query/header check does not apply.
    Superadmin/admin bypass; all other roles must have `copro_id` in their
    `copropriete_ids`. Raises 403 otherwise."""
    if not copro_id:
        return  # nothing to check
    role = getattr(request.state, "user_role", "")
    if role in ("superadmin", "admin"):
        return
    user_copros = getattr(request.state, "user_copropriete_ids", []) or []
    if copro_id not in user_copros:
        raise HTTPException(
            status_code=403,
            detail="Acces refuse a cette copropriete (chinese wall)",
        )


def create_banking_router(db):
    router = APIRouter(prefix="/api/banking")

    async def _try_explicit_match_then_vcs(txn_doc):
        """Match d'une transaction :
        1) PRIORITE : si l'utilisateur a explicitement selectionne une contrepartie
           via le widget CounterpartySearchSelect (counterparty_id + counterparty_type
           non vides), utilise cet ID directement (override VCS).
        2) FALLBACK : tentative d'auto-lettrage par VCS (regex sur 12 chiffres VCS belge).
        """
        cp_id = (txn_doc.get("counterparty_id") or "").strip()
        cp_type = (txn_doc.get("counterparty_type") or "").strip()
        if cp_id and cp_type in ("owner", "supplier"):
            match_type = "owner_payment" if cp_type == "owner" else "supplier_payment"
            # Verifie que l'ID existe (eviter de pointer sur du vide)
            coll = db.owners if cp_type == "owner" else db.suppliers
            target = await coll.find_one({"id": cp_id}, {"_id": 0})
            if target:
                await db.bank_transactions.update_one(
                    {"id": txn_doc["id"]},
                    {"$set": {"matched": True, "matched_to": cp_id, "match_type": match_type,
                              "counterparty_name": txn_doc.get("counterparty_name") or target.get("name", "")}}
                )
                try:
                    fresh = await db.bank_transactions.find_one({"id": txn_doc["id"]}, {"_id": 0})
                    if fresh:
                        await generate_bank_entry(db, fresh)
                except Exception as e:
                    print(f"[explicit-match] FI generation failed: {e}")
                return  # Pas de fallback VCS - l'explicite prime
        # Sinon fallback VCS classique
        await _try_auto_lettrage_vcs(txn_doc)

    async def _try_auto_lettrage_vcs(txn_doc):
        """Try to auto-match a transaction by VCS communication.
        Extrait le code VCS (12 chiffres) de la communication peu importe le suffixe
        ('+++100/7407/40231+++ - Votre paiement au 19/06/2026' -> '100740740231').

        Si echec VCS, tente un fallback par NOM (counterparty_name -> owner.name
        case-insensitive) puis par numero de facture (counterparty_name or
        communication contient le numero d'une facture impayee).
        """
        import re as _re
        comm = txn_doc.get("communication", "")
        cp_name = (txn_doc.get("counterparty_name") or "").strip()
        # 1) Tentative VCS sur communication
        vcs_clean = ""
        if comm and len(comm) >= 3:
            m = _re.search(r"(\d{3})[\s/]*(\d{4})[\s/]*(\d{5})", comm)
            if m:
                vcs_clean = m.group(1) + m.group(2) + m.group(3)
            else:
                digits = _re.sub(r"\D", "", comm)
                if len(digits) >= 12:
                    vcs_clean = digits[:12]
        owner = None
        if vcs_clean and len(vcs_clean) == 12:
            owner = await db.owners.find_one({"vcs_digits": vcs_clean}, {"_id": 0})
            if not owner:
                owner = await db.owners.find_one(
                    {"vcs_code": {"$regex": _re.escape(vcs_clean)}}, {"_id": 0}
                )
        # 2) Fallback : match par counterparty_name exact (case-insensitive)
        if not owner and cp_name and len(cp_name) >= 3:
            esc = _re.escape(cp_name)
            owner = await db.owners.find_one(
                {"name": {"$regex": f"^{esc}$", "$options": "i"}}, {"_id": 0}
            )
        # 3) Fallback : nom partiel "Last First" ou "First Last"
        if not owner and cp_name and " " in cp_name:
            parts = [p for p in cp_name.split() if p]
            if len(parts) >= 2:
                # Tente toutes les permutations du nom
                possible = [parts[0], parts[-1], " ".join(parts[:2]), " ".join(parts[-2:])]
                for p in possible:
                    if len(p) < 3:
                        continue
                    owner = await db.owners.find_one(
                        {"$or": [
                            {"last_name": {"$regex": f"^{_re.escape(p)}$", "$options": "i"}},
                            {"name": {"$regex": _re.escape(p), "$options": "i"}},
                        ]},
                        {"_id": 0}
                    )
                    if owner:
                        break
        if owner:
            await db.bank_transactions.update_one(
                {"id": txn_doc["id"]},
                {"$set": {"matched": True, "matched_to": owner["id"], "match_type": "owner_payment",
                          "counterparty_name": txn_doc.get("counterparty_name") or owner["name"]}}
            )
            try:
                fresh = await db.bank_transactions.find_one({"id": txn_doc["id"]}, {"_id": 0})
                if fresh:
                    await generate_bank_entry(db, fresh)
            except Exception as e:
                print(f"[auto-entry] bank auto-vcs failed: {e}")
            return
        # 4) Fallback supplier : counterparty_name correspond a un fournisseur
        supplier = None
        if cp_name and len(cp_name) >= 3:
            esc = _re.escape(cp_name)
            supplier = await db.suppliers.find_one(
                {"name": {"$regex": f"^{esc}$", "$options": "i"}}, {"_id": 0}
            )
        if supplier:
            # Pour les paiements sortants (debit) seulement -> payment to supplier
            if float(txn_doc.get("amount", 0) or 0) < 0 or txn_doc.get("transaction_type") == "debit":
                await db.bank_transactions.update_one(
                    {"id": txn_doc["id"]},
                    {"$set": {"matched": True, "matched_to": supplier["id"], "match_type": "supplier_payment",
                              "counterparty_name": txn_doc.get("counterparty_name") or supplier["name"]}}
                )
                try:
                    fresh = await db.bank_transactions.find_one({"id": txn_doc["id"]}, {"_id": 0})
                    if fresh:
                        await generate_bank_entry(db, fresh)
                except Exception as e:
                    print(f"[auto-entry] supplier match failed: {e}")
                return
        # 5) Fallback ULTIME : numero de facture dans counterparty_name ou communication
        #    Cherche une facture impayee de cette ACP dont le numero apparait dans le texte
        copro_id = txn_doc.get("copropriete_id") or ""
        if copro_id:
            search_text = f"{cp_name} {comm}".strip()
            if search_text:
                unpaid = await db.invoices.find(
                    {"copropriete_id": copro_id, "status": "unpaid"}, {"_id": 0}
                ).to_list(500)
                for inv in unpaid:
                    inv_num = (inv.get("number") or "").strip()
                    if inv_num and len(inv_num) >= 3 and inv_num in search_text:
                        await db.bank_transactions.update_one(
                            {"id": txn_doc["id"]},
                            {"$set": {"matched": True, "matched_to": inv["id"], "match_type": "invoice"}}
                        )
                        try:
                            fresh = await db.bank_transactions.find_one({"id": txn_doc["id"]}, {"_id": 0})
                            if fresh:
                                await generate_bank_entry(db, fresh)
                                # Mark invoice as paid
                                await db.invoices.update_one(
                                    {"id": inv["id"]},
                                    {"$set": {"status": "paid", "paid_at": fresh.get("date"),
                                              "paid_by_transaction_id": txn_doc["id"]}}
                                )
                        except Exception as e:
                            print(f"[auto-entry] invoice match failed: {e}")
                        return

    async def _suggest_match_for_movement(mov: dict, copro_id: str) -> dict:
        """Calcule une SUGGESTION de match pour un mouvement CODA SANS rien persister.

        Retourne dict {
          match_type: "owner_payment"|"supplier_payment"|"invoice"|"",
          match_id: <id> | "",
          match_label: <nom affiche>,
          match_reason: "vcs"|"name_exact"|"name_partial"|"supplier_iban"|"supplier_name"|"invoice_number"|"",
          confidence: "high"|"medium"|"low",
        }

        Reutilise la meme logique que `_try_auto_lettrage_vcs` mais retourne au
        lieu de persister. Permet l'UI de mapping CODA d'afficher des suggestions.
        """
        import re as _re
        comm = (mov.get("communication") or "").strip()
        cp_name = (mov.get("counterparty_name") or "").strip()
        cp_account = (mov.get("counterparty_account") or "").strip().replace(" ", "")
        amount = float(mov.get("amount", 0) or 0)
        is_debit = amount < 0 or (mov.get("type") == "debit")

        empty = {"match_type": "", "match_id": "", "match_label": "",
                 "match_reason": "", "confidence": ""}

        # 1) VCS sur la communication (highest confidence)
        vcs_clean = ""
        if comm and len(comm) >= 3:
            m = _re.search(r"(\d{3})[\s/]*(\d{4})[\s/]*(\d{5})", comm)
            if m:
                vcs_clean = m.group(1) + m.group(2) + m.group(3)
            else:
                digits = _re.sub(r"\D", "", comm)
                if len(digits) >= 12:
                    vcs_clean = digits[:12]
        if vcs_clean and len(vcs_clean) == 12:
            owner = await db.owners.find_one({"vcs_digits": vcs_clean}, {"_id": 0})
            if not owner:
                owner = await db.owners.find_one(
                    {"vcs_code": {"$regex": _re.escape(vcs_clean)}}, {"_id": 0}
                )
            if owner:
                return {
                    "match_type": "owner_payment",
                    "match_id": owner["id"],
                    "match_label": owner.get("name", ""),
                    "match_reason": "vcs",
                    "confidence": "high",
                }

        # 2) Nom exact du counterparty -> owner
        if cp_name and len(cp_name) >= 3:
            esc = _re.escape(cp_name)
            owner = await db.owners.find_one(
                {"name": {"$regex": f"^{esc}$", "$options": "i"}}, {"_id": 0}
            )
            if owner:
                return {
                    "match_type": "owner_payment",
                    "match_id": owner["id"],
                    "match_label": owner.get("name", ""),
                    "match_reason": "name_exact",
                    "confidence": "medium",
                }
            # Nom partiel
            if " " in cp_name:
                parts = [p for p in cp_name.split() if p]
                if len(parts) >= 2:
                    for p in [parts[0], parts[-1], " ".join(parts[:2]), " ".join(parts[-2:])]:
                        if len(p) < 3:
                            continue
                        owner = await db.owners.find_one(
                            {"$or": [
                                {"last_name": {"$regex": f"^{_re.escape(p)}$", "$options": "i"}},
                                {"name": {"$regex": _re.escape(p), "$options": "i"}},
                            ]},
                            {"_id": 0}
                        )
                        if owner:
                            return {
                                "match_type": "owner_payment",
                                "match_id": owner["id"],
                                "match_label": owner.get("name", ""),
                                "match_reason": "name_partial",
                                "confidence": "low",
                            }

        # 3) Fournisseur par IBAN
        if cp_account:
            sup = await db.suppliers.find_one(
                {"iban": {"$regex": _re.escape(cp_account), "$options": "i"}}, {"_id": 0}
            )
            if sup:
                if is_debit:
                    return {
                        "match_type": "supplier_payment",
                        "match_id": sup["id"],
                        "match_label": sup.get("name", ""),
                        "match_reason": "supplier_iban",
                        "confidence": "high",
                    }

        # 4) Fournisseur par nom exact
        if cp_name and len(cp_name) >= 3:
            esc = _re.escape(cp_name)
            sup = await db.suppliers.find_one(
                {"name": {"$regex": f"^{esc}$", "$options": "i"}}, {"_id": 0}
            )
            if sup and is_debit:
                return {
                    "match_type": "supplier_payment",
                    "match_id": sup["id"],
                    "match_label": sup.get("name", ""),
                    "match_reason": "supplier_name",
                    "confidence": "medium",
                }

        # 5) Numero de facture impayee dans communication
        if copro_id:
            search_text = f"{cp_name} {comm}".strip()
            if search_text and len(search_text) >= 3:
                unpaid = await db.invoices.find(
                    {"copropriete_id": copro_id, "status": "unpaid"}, {"_id": 0}
                ).to_list(500)
                for inv in unpaid:
                    inv_num = (inv.get("number") or "").strip()
                    if inv_num and len(inv_num) >= 3 and inv_num in search_text:
                        return {
                            "match_type": "invoice",
                            "match_id": inv["id"],
                            "match_label": f"Facture {inv_num} - {inv.get('supplier','')}",
                            "match_reason": "invoice_number",
                            "confidence": "medium",
                        }
        return empty

    # ---- BANK STATEMENTS ----
    @router.get("/statements")
    async def list_statements(request: Request, copropriete_id: Optional[str] = None,
                              date_from: Optional[str] = None, date_to: Optional[str] = None):
        """Chinese wall STRICT : aucune liste cross-ACP possible. Si aucun
        copropriete_id n'est fourni (ni via param ni via header X-Copropriete-Id),
        on retourne une liste vide pour eviter toute fuite.
        Filtre periode optionnel via date_from/date_to (inclusif, ISO YYYY-MM-DD)."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        q = {"copropriete_id": copropriete_id}
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        statements = await db.bank_statements.find(
            q, {"_id": 0}
        ).sort("date", -1).to_list(1000)
        return statements

    @router.get("/statements/previous-closing")
    async def get_previous_closing_balance(
        request: Request,
        account_number: Optional[str] = None,
        copropriete_id: Optional[str] = None,
    ):
        """Retourne le solde de cloture du DERNIER extrait pour un IBAN donne dans une ACP.
        Utilise pour pre-remplir le 'solde d'ouverture' a la creation d'un nouvel extrait.
        Si aucun extrait precedent : retourne {balance: 0.0, source: 'none'}.
        Si extrait precedent draft : utilise computed_closing (opening + sum mouvements)
        car le closing_balance n'est figeable qu'a la comptabilisation."""
        copro_id = (copropriete_id or "").strip() or (request.headers.get("X-Copropriete-Id") or "").strip()
        if not copro_id or copro_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        iban_q = (account_number or "").replace(" ", "").upper()
        query = {"copropriete_id": copro_id}
        if iban_q:
            query["account_number"] = {"$regex": f"^{iban_q}$", "$options": "i"}
        # Dernier extrait par date (puis created_at en tiebreaker)
        last_stmt = await db.bank_statements.find_one(
            query, {"_id": 0}, sort=[("date", -1), ("created_at", -1)]
        )
        if not last_stmt:
            return {"balance": 0.0, "source": "none", "message": "Aucun extrait precedent pour ce compte."}
        # Si l'extrait est posted, on a confiance dans closing_balance saisi
        # Sinon on calcule depuis les mouvements
        if last_stmt.get("status") == "posted":
            return {
                "balance": float(last_stmt.get("closing_balance", 0) or 0),
                "source": "posted",
                "previous_statement_id": last_stmt.get("id"),
                "previous_statement_date": last_stmt.get("date"),
                "previous_statement_number": last_stmt.get("number") or "",
            }
        # Draft : calcul live
        txns = await db.bank_transactions.find(
            {"statement_id": last_stmt["id"]}, {"_id": 0, "amount": 1}
        ).to_list(10000)
        mvts_sum = sum(float(t.get("amount", 0) or 0) for t in txns)
        computed = round(float(last_stmt.get("opening_balance", 0) or 0) + mvts_sum, 2)
        return {
            "balance": computed,
            "source": "draft_computed",
            "previous_statement_id": last_stmt.get("id"),
            "previous_statement_date": last_stmt.get("date"),
            "previous_statement_number": last_stmt.get("number") or "",
            "warning": "Extrait precedent encore en brouillon - solde calcule depuis les mouvements.",
        }

    @router.post("/statements")
    async def create_statement(data: StatementInput, request: Request):
        # Resolve ACP : payload OR header
        copro_id = (data.copropriete_id or "").strip() or (request.headers.get("X-Copropriete-Id") or "").strip()
        if not copro_id or copro_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        # Verify ACP exists
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "id": 1, "bank_accounts": 1})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        # If an account_number (IBAN) is provided, it MUST belong to this ACP's bank_accounts
        iban = (data.account_number or "").strip()
        if iban:
            allowed_ibans = {(b.get("iban") or "").replace(" ", "") for b in (copro.get("bank_accounts") or [])}
            if allowed_ibans and iban.replace(" ", "") not in allowed_ibans:
                raise HTTPException(
                    400,
                    f"L'IBAN {iban} n'est pas configure dans les comptes bancaires de cette ACP. "
                    "Ajoutez-le dans la fiche ACP avant d'importer."
                )
        # AUTO-FILL solde d'ouverture : si non fourni (ou 0), reprend le closing du
        # dernier extrait du meme IBAN. Garantit la continuite des soldes.
        opening_balance = float(data.opening_balance or 0)
        opening_source = "user"
        if abs(opening_balance) < 0.001 and iban:
            iban_q = iban.replace(" ", "").upper()
            last_stmt = await db.bank_statements.find_one(
                {"copropriete_id": copro_id, "account_number": {"$regex": f"^{iban_q}$", "$options": "i"}},
                {"_id": 0}, sort=[("date", -1), ("created_at", -1)]
            )
            if last_stmt:
                if last_stmt.get("status") == "posted":
                    opening_balance = float(last_stmt.get("closing_balance", 0) or 0)
                    opening_source = "previous_posted"
                else:
                    txns = await db.bank_transactions.find(
                        {"statement_id": last_stmt["id"]}, {"_id": 0, "amount": 1}
                    ).to_list(10000)
                    mvts_sum = sum(float(t.get("amount", 0) or 0) for t in txns)
                    opening_balance = round(float(last_stmt.get("opening_balance", 0) or 0) + mvts_sum, 2)
                    opening_source = "previous_draft_computed"
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "date": data.date,
            "account_number": iban,
            "opening_balance": opening_balance,
            "opening_balance_source": opening_source,
            "closing_balance": data.closing_balance,
            "copropriete_id": copro_id,
            "status": "draft",  # draft | posted
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.bank_statements.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/statements/{stmt_id}")
    async def get_statement(stmt_id: str, request: Request):
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
        _ensure_copro_access(request, stmt.get("copropriete_id", ""))
        txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0}).sort("date", 1).to_list(1000)
        stmt["transactions"] = txns
        # Calcul de l'equilibre
        mvts_sum = sum(
            (1 if t.get("transaction_type") == "credit" else -1) * abs(float(t.get("amount", 0) or 0))
            for t in txns
        )
        computed_closing = round(float(stmt.get("opening_balance", 0) or 0) + mvts_sum, 2)
        actual_closing = round(float(stmt.get("closing_balance", 0) or 0), 2)
        stmt["computed_closing"] = computed_closing
        stmt["balance_diff"] = round(computed_closing - actual_closing, 2)
        stmt["is_balanced"] = abs(stmt["balance_diff"]) < 0.01
        return stmt

    @router.post("/statements/{stmt_id}/post")
    async def post_statement(stmt_id: str):
        """Comptabilise l'extrait : valide l'equilibre opening + mouvements = closing,
        passe le statut a 'posted'. Refuse 400 si non equilibre."""
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
        if stmt.get("status") == "posted":
            raise HTTPException(400, "Extrait deja comptabilise")
        txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0}).to_list(10000)
        mvts_sum = sum(
            (1 if t.get("transaction_type") == "credit" else -1) * abs(float(t.get("amount", 0) or 0))
            for t in txns
        )
        opening = round(float(stmt.get("opening_balance", 0) or 0), 2)
        closing = round(float(stmt.get("closing_balance", 0) or 0), 2)
        computed = round(opening + mvts_sum, 2)
        diff = round(computed - closing, 2)
        if abs(diff) >= 0.01:
            raise HTTPException(
                400,
                f"Extrait non equilibre. Solde ouverture ({opening:.2f}) + mouvements ({mvts_sum:.2f}) = {computed:.2f}, mais solde fermeture saisi = {closing:.2f}. Difference : {diff:.2f}"
            )
        # GENERATION DES ECRITURES COMPTABLES (FI) : une ecriture par transaction.
        # - Si lettree -> Dr/Cr counterpart correspondant (owner/supplier/invoice)
        # - Sinon -> Dr/Cr 499000 compte d'attente (visible dans bilan, neutralise)
        from auto_entries import generate_bank_entry
        # ETAPE 1 : tentative de match - contrepartie explicite (UI) en priorite, fallback VCS.
        for t in txns:
            if not t.get("matched"):
                try:
                    await _try_explicit_match_then_vcs(t)
                except Exception as e:
                    print(f"[post-stmt] match failed for txn {t.get('id')}: {e}")
        # Re-fetch des txns apres auto-lettrage pour avoir l'etat le plus a jour
        txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0}).to_list(10000)
        # ETAPE 2 : generation des ecritures FI pour TOUTES les transactions
        fi_created = 0
        fi_errors = []
        for t in txns:
            try:
                # Si la txn a deja une ecriture FI (creee a l'auto-lettrage VCS), on skip
                # (sinon on aurait un doublon supprime puis recreer)
                existing_fi = await db.journal_entries.count_documents({
                    "source_type": "bank_txn", "source_id": t["id"], "auto_generated": True
                })
                if existing_fi > 0:
                    fi_created += 1
                    continue
                result = await generate_bank_entry(db, t)
                if result:
                    fi_created += 1
            except Exception as e:
                fi_errors.append({"txn_id": t.get("id"), "error": str(e)})
        await db.bank_statements.update_one(
            {"id": stmt_id},
            {"$set": {"status": "posted", "posted_at": datetime.now(timezone.utc).isoformat()}}
        )
        return {
            "status": "ok",
            "message": f"Extrait comptabilise. {fi_created} ecriture(s) financiere(s) creee(s).",
            "computed_closing": computed,
            "transactions_count": len(txns),
            "fi_entries_created": fi_created,
            "fi_errors": fi_errors,
        }

    @router.post("/statements/{stmt_id}/unpost")
    async def unpost_statement(stmt_id: str):
        """Repasse l'extrait en draft (permet correction).
        Supprime egalement toutes les ecritures FI auto-generees pour les
        transactions de cet extrait (sera regenere a la prochaine comptabilisation)."""
        from auto_entries import _delete_auto_entries
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
        txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0}).to_list(10000)
        deleted = 0
        for t in txns:
            try:
                # Count first then delete (atomicity is OK here since post is single-threaded for the stmt)
                cnt = await db.journal_entries.count_documents({
                    "auto_generated": True,
                    "manually_edited": {"$ne": True},
                    "source_type": "bank_txn",
                    "source_id": t["id"],
                })
                await _delete_auto_entries(db, "bank_txn", t["id"])
                deleted += cnt
            except Exception as e:
                print(f"[unpost] delete FI entry failed for txn {t.get('id')}: {e}")
        await db.bank_statements.update_one(
            {"id": stmt_id},
            {"$set": {"status": "draft", "posted_at": None}}
        )
        return {"status": "ok", "message": f"Extrait repasse en brouillon. {deleted} ecriture(s) FI supprimee(s)."}

    @router.put("/statements/{stmt_id}")
    async def update_statement(stmt_id: str, data: StatementInput):
        update = {
            "number": data.number,
            "date": data.date,
            "account_number": data.account_number,
            "opening_balance": data.opening_balance,
            "closing_balance": data.closing_balance,
        }
        result = await db.bank_statements.update_one({"id": stmt_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Extrait non trouve")
        return await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})

    @router.delete("/statements/{stmt_id}")
    async def delete_statement(stmt_id: str):
        # Recupere les txns du statement pour supprimer leurs ecritures auto
        txns = await db.bank_transactions.find({"statement_id": stmt_id}, {"_id": 0, "id": 1}).to_list(10000)
        for t in txns:
            try:
                await _delete_auto_entries(db, "bank_txn", t["id"])
            except Exception:
                pass
        await db.bank_transactions.delete_many({"statement_id": stmt_id})
        result = await db.bank_statements.delete_one({"id": stmt_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Extrait non trouve")
        return {"message": "Extrait supprime", "txns_deleted": len(txns)}

    # ---- TRANSACTIONS ----
    @router.get("/transactions")
    async def list_transactions(
        request: Request,
        statement_id: Optional[str] = None,
        matched: Optional[bool] = None,
        copropriete_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Chinese wall STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id) sauf si on liste par `statement_id`.
        Filtre periode optionnel via date_from/date_to (inclusif, ISO YYYY-MM-DD)."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not statement_id and (not copropriete_id or copropriete_id == "all"):
            return []
        query = {}
        if statement_id:
            query["statement_id"] = statement_id
            # Verify the statement belongs to the resolved ACP (defense in depth)
            if copropriete_id and copropriete_id != "all":
                stmt = await db.bank_statements.find_one(
                    {"id": statement_id, "copropriete_id": copropriete_id}, {"_id": 0, "id": 1}
                )
                if not stmt:
                    return []
        if matched is not None:
            query["matched"] = matched
        if copropriete_id and copropriete_id != "all":
            query["copropriete_id"] = copropriete_id
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        txns = await db.bank_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return txns

    async def _refresh_fi_if_posted(txn_id):
        """Si l'extrait parent est `posted`, regenere l'ecriture FI de cette txn
        (Dr Banque / Cr counterpart si lettree, sinon Cr 499000). Permet aux balances
        de tiers et au bilan de rester synchronises avec les modifications."""
        try:
            txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            if not txn or not txn.get("statement_id"):
                return
            stmt = await db.bank_statements.find_one(
                {"id": txn["statement_id"]}, {"_id": 0, "status": 1}
            )
            if not stmt or stmt.get("status") != "posted":
                return
            # generate_bank_entry s'occupe de supprimer l'ancienne FI auto avant d'en creer une nouvelle
            await generate_bank_entry(db, txn)
        except Exception as e:
            print(f"[_refresh_fi_if_posted] failed for txn {txn_id}: {e}")

    @router.post("/transactions")
    async def create_transaction(data: TransactionInput):
        # Force amount sign based on transaction_type
        stored_amount = abs(float(data.amount))
        if data.transaction_type == "debit":
            stored_amount = -stored_amount
        doc = {
            "id": str(uuid.uuid4()),
            "statement_id": data.statement_id,
            "date": data.date,
            "amount": stored_amount,
            "counterparty_name": data.counterparty_name,
            "counterparty_account": data.counterparty_account,
            "communication": data.communication,
            "transaction_type": data.transaction_type,
            "account_number": data.account_number,
            "matched": False,
            "matched_to": "",
            "match_type": "",
            "copropriete_id": data.copropriete_id or "",
            "counterparty_id": data.counterparty_id or "",
            "counterparty_type": data.counterparty_type or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.bank_transactions.insert_one(doc)
        # PRIORITE 1: contrepartie explicite (via UI). PRIORITE 2: auto-VCS.
        await _try_explicit_match_then_vcs(doc)
        # Si l'extrait est deja comptabilise, generer/regenerer l'ecriture FI maintenant
        await _refresh_fi_if_posted(doc["id"])
        updated = await db.bank_transactions.find_one({"id": doc["id"]}, {"_id": 0})
        return updated or {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/transactions/{txn_id}")
    async def update_transaction(txn_id: str, data: TransactionInput):
        stored_amount = abs(float(data.amount))
        if data.transaction_type == "debit":
            stored_amount = -stored_amount
        update = {
            "date": data.date, "amount": stored_amount,
            "counterparty_name": data.counterparty_name,
            "counterparty_account": data.counterparty_account,
            "communication": data.communication,
            "transaction_type": data.transaction_type,
            "account_number": data.account_number,
        }
        # Si l'utilisateur a explicitement (re)selectionne une contrepartie via le
        # widget, on enregistre l'ID + on FORCE le re-match (reset l'ancien lettrage).
        if data.counterparty_id and data.counterparty_type:
            update["counterparty_id"] = data.counterparty_id
            update["counterparty_type"] = data.counterparty_type
            update["matched"] = False  # force re-match avec le nouveau ID
            update["matched_to"] = ""
            update["match_type"] = ""
        result = await db.bank_transactions.update_one({"id": txn_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        updated = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        if updated and not updated.get("matched"):
            # PRIORITE 1: contrepartie explicite. PRIORITE 2: auto-VCS.
            await _try_explicit_match_then_vcs(updated)
            updated = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        # Regenere l'ecriture FI si l'extrait est comptabilise (montant/date/lettrage
        # peuvent avoir change -> balance des tiers et bilan doivent suivre)
        await _refresh_fi_if_posted(txn_id)
        updated = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        return updated

    @router.delete("/transactions/{txn_id}")
    async def delete_transaction(txn_id: str):
        try:
            await _delete_auto_entries(db, "bank_txn", txn_id)
        except Exception:
            pass
        result = await db.bank_transactions.delete_one({"id": txn_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        return {"message": "Transaction supprimee"}

    # ---- LETTRAGE ----
    @router.post("/lettrage")
    async def lettrage(data: LettrageInput):
        txn = await db.bank_transactions.find_one({"id": data.transaction_id}, {"_id": 0})
        if not txn:
            raise HTTPException(404, "Transaction non trouvee")
        await db.bank_transactions.update_one(
            {"id": data.transaction_id},
            {"$set": {"matched": True, "matched_to": data.match_to_id, "match_type": data.match_type}}
        )
        # Si lettrage vers une facture : marquer la facture comme payee
        if data.match_type == "invoice":
            await db.invoices.update_one(
                {"id": data.match_to_id},
                {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat(),
                          "paid_by_transaction_id": data.transaction_id}}
            )
        try:
            fresh = await db.bank_transactions.find_one({"id": data.transaction_id}, {"_id": 0})
            if fresh:
                await generate_bank_entry(db, fresh)
        except Exception as e:
            print(f"[auto-entry] bank lettrage failed: {e}")
        return {"message": "Lettrage effectue", "transaction_id": data.transaction_id}

    @router.post("/lettrage-batch")
    async def lettrage_batch(data: LettrageBatchInput):
        """Lettrage multi-transactions vers UNE facture (paiements partiels).

        - Valide que toutes les transactions existent et ne sont pas deja lettrees
          a une AUTRE facture (deja lettrees a la meme facture = OK, on resoumet).
        - Calcule le total des montants et le compare au montant TVAC de la facture.
        - Marque toutes les transactions comme `matched=True, match_type='invoice',
          matched_to=<invoice_id>` + assigne un `lettrage_code` commun pour les
          tracer comme groupe.
        - La facture est marquee `paid` si la somme atteint le total TVAC
          (a 0.01 EUR pres), sinon `partially_paid` avec `amount_paid` = somme.
        """
        if not data.transaction_ids:
            raise HTTPException(400, "Aucune transaction selectionnee")
        if data.match_type != "invoice":
            raise HTTPException(400, "Seul match_type='invoice' est supporte en batch pour l'instant")
        invoice = await db.invoices.find_one({"id": data.match_to_id}, {"_id": 0})
        if not invoice:
            raise HTTPException(404, "Facture non trouvee")
        txns = await db.bank_transactions.find(
            {"id": {"$in": data.transaction_ids}}, {"_id": 0}
        ).to_list(1000)
        found_ids = {t["id"] for t in txns}
        missing = set(data.transaction_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Transactions introuvables : {', '.join(missing)}")

        # Verifie qu'aucune txn n'est deja lettree a une AUTRE facture (autre que celle-ci)
        conflicts = [
            t for t in txns
            if t.get("matched") and t.get("match_type") == "invoice"
            and t.get("matched_to") and t["matched_to"] != data.match_to_id
        ]
        if conflicts:
            details = ", ".join(f"{t['id'][:8]} -> {t['matched_to'][:8]}" for t in conflicts[:3])
            raise HTTPException(
                400,
                f"Ces transactions sont deja lettrees a une autre facture : {details}. "
                "Delettrez-les d'abord.",
            )

        # Cohérence ACP : toutes les txns doivent etre dans la meme ACP que la facture
        inv_acp = invoice.get("copropriete_id", "")
        for t in txns:
            if t.get("copropriete_id") and t["copropriete_id"] != inv_acp:
                raise HTTPException(
                    400,
                    f"Transaction {t['id'][:8]} appartient a une autre ACP que la facture.",
                )

        # Calcul somme des montants ABSOLUS (les debits sont stockes negatifs)
        total_txn = round(sum(abs(float(t.get("amount", 0) or 0)) for t in txns), 2)
        # Montant TVAC de la facture (cle 'amount' dans Optipro, sinon total_amount)
        inv_amount = round(float(
            invoice.get("amount_ttc") or invoice.get("total_amount") or invoice.get("amount") or 0
        ), 2)
        if inv_amount <= 0:
            raise HTTPException(400, "Facture sans montant TVAC connu")
        # Note: les sur-paiements sont autorises. Le solde excedentaire se reflete
        # automatiquement sur le compte tiers du proprietaire (44XXXXX) lors de
        # la comptabilisation de l'extrait bancaire. Pas de check ici.

        # Lettrage : meme lettrage_code pour toutes les txns du groupe
        lettrage_code = str(uuid.uuid4())[:8].upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.bank_transactions.update_many(
            {"id": {"$in": data.transaction_ids}},
            {"$set": {
                "matched": True,
                "matched_to": data.match_to_id,
                "match_type": "invoice",
                "lettrage_code": lettrage_code,
                "lettrage_at": now_iso,
            }},
        )

        # Statut facture : paid si exact, partially_paid sinon, overpaid si > inv_amount
        is_full = abs(total_txn - inv_amount) < 0.01
        is_overpaid = total_txn > inv_amount + 0.01
        invoice_update = {
            "amount_paid": total_txn,
            "lettrage_code": lettrage_code,
        }
        if is_full or is_overpaid:
            invoice_update["status"] = "paid"
            invoice_update["paid_at"] = now_iso
            invoice_update["paid_by_transaction_id"] = data.transaction_ids[0]
            invoice_update["paid_by_transaction_ids"] = list(data.transaction_ids)
            if is_overpaid:
                # Excedent : sera reflete sur le compte tiers lors de la comptabilisation
                invoice_update["overpaid_amount"] = round(total_txn - inv_amount, 2)
        else:
            invoice_update["status"] = "partially_paid"
            invoice_update["paid_by_transaction_ids"] = list(data.transaction_ids)
        await db.invoices.update_one({"id": data.match_to_id}, {"$set": invoice_update})

        # Regenere les ecritures FI pour chaque transaction lettrée
        for tid in data.transaction_ids:
            try:
                fresh = await db.bank_transactions.find_one({"id": tid}, {"_id": 0})
                if fresh:
                    await generate_bank_entry(db, fresh)
            except Exception as e:
                print(f"[lettrage-batch] FI regen failed for {tid}: {e}")

        return {
            "message": f"Lettrage en lot effectue ({len(data.transaction_ids)} transactions)",
            "transaction_ids": data.transaction_ids,
            "invoice_id": data.match_to_id,
            "lettrage_code": lettrage_code,
            "total_paid": total_txn,
            "invoice_amount": inv_amount,
            "status": "paid" if (is_full or is_overpaid) else "partially_paid",
            "remaining": round(inv_amount - total_txn, 2),
            "overpaid_amount": round(max(0, total_txn - inv_amount), 2),
        }

    @router.post("/lettrage-multi-invoices")
    async def lettrage_multi_invoices(data: LettrageMultiInvoicesInput):
        """Lettrage d'UNE transaction vers PLUSIEURS factures.

        Cas type : un virement bancaire qui solde 2+ factures en un seul mouvement
        (ex. paiement groupe d'un fournisseur a un trimestre, ou solde + acompte).

        - Verifie que la transaction existe et n'est pas deja lettree a une autre cible
          (sauf si deja lettree a une de ces memes factures = OK pour la resoumission).
        - Calcule la somme des montants TVAC des factures et compare au montant txn.
        - Chaque facture passe a paid + amount_paid=son_total + paid_by_transaction_ids=[txn_id]
          partagent un lettrage_code commun.
        - La transaction est marquee matched=True avec match_type='multi_invoice' et
          matched_to_ids=[inv_ids...] (champ liste). matched_to (singulier) = premier inv_id pour
          la retro-compatibilite avec l'UI existante.
        - Sur-paiement accepte (excedent sera reflete sur compte tiers a la comptabilisation).
        """
        if not data.invoice_ids:
            raise HTTPException(400, "Aucune facture selectionnee")
        txn = await db.bank_transactions.find_one({"id": data.transaction_id}, {"_id": 0})
        if not txn:
            raise HTTPException(404, "Transaction non trouvee")
        invoices = await db.invoices.find(
            {"id": {"$in": data.invoice_ids}}, {"_id": 0}
        ).to_list(1000)
        found_ids = {i["id"] for i in invoices}
        missing = set(data.invoice_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Factures introuvables : {', '.join(missing)}")

        # Coherence ACP
        txn_acp = txn.get("copropriete_id", "")
        for inv in invoices:
            if txn_acp and inv.get("copropriete_id") and inv["copropriete_id"] != txn_acp:
                raise HTTPException(
                    400,
                    f"Facture {inv.get('number', inv['id'][:8])} dans une autre ACP que la transaction.",
                )

        # Calcul
        txn_amount = abs(float(txn.get("amount", 0) or 0))
        invoice_total = round(sum(float(
            i.get("amount_ttc") or i.get("total_amount") or i.get("amount") or 0
        ) for i in invoices), 2)
        txn_amount = round(txn_amount, 2)

        lettrage_code = str(uuid.uuid4())[:8].upper()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Marque la transaction
        await db.bank_transactions.update_one(
            {"id": data.transaction_id},
            {"$set": {
                "matched": True,
                "matched_to": data.invoice_ids[0],  # back-compat (1er invoice)
                "matched_to_ids": list(data.invoice_ids),
                "match_type": "multi_invoice",
                "lettrage_code": lettrage_code,
                "lettrage_at": now_iso,
            }},
        )

        # Chaque facture : paid (son montant integral est couvert par la part de la txn)
        for inv in invoices:
            inv_amt = round(float(
                inv.get("amount_ttc") or inv.get("total_amount") or inv.get("amount") or 0
            ), 2)
            await db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {
                    "status": "paid",
                    "paid_at": now_iso,
                    "amount_paid": inv_amt,
                    "paid_by_transaction_id": data.transaction_id,
                    "paid_by_transaction_ids": [data.transaction_id],
                    "lettrage_code": lettrage_code,
                }},
            )

        # Regenere ecriture FI
        try:
            fresh = await db.bank_transactions.find_one({"id": data.transaction_id}, {"_id": 0})
            if fresh:
                await generate_bank_entry(db, fresh)
        except Exception as e:
            print(f"[lettrage-multi-invoices] FI regen failed: {e}")

        is_exact = abs(txn_amount - invoice_total) < 0.01
        return {
            "message": f"Lettrage effectue : 1 transaction -> {len(data.invoice_ids)} factures",
            "transaction_id": data.transaction_id,
            "invoice_ids": data.invoice_ids,
            "lettrage_code": lettrage_code,
            "transaction_amount": txn_amount,
            "invoice_total": invoice_total,
            "remaining": round(txn_amount - invoice_total, 2),
            "is_exact": is_exact,
        }

    @router.post("/unlettrage/{txn_id}")
    async def unlettrage(txn_id: str):
        # Recupere la txn avant unset pour gerer le statut facture
        prev = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        try:
            await _delete_auto_entries(db, "bank_txn", txn_id)
        except Exception:
            pass
        result = await db.bank_transactions.update_one(
            {"id": txn_id},
            {"$set": {"matched": False, "matched_to": "", "match_type": ""},
             "$unset": {"lettrage_code": "", "lettrage_at": ""}},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        # Si on delettre une transaction qui pointait sur une facture : recalculer
        # le statut de la facture en fonction des transactions restantes encore
        # lettrees (cas d'un lettrage en lot N->1 ou on retire une seule txn).
        if prev and prev.get("match_type") == "invoice" and prev.get("matched_to"):
            invoice_id = prev["matched_to"]
            remaining = await db.bank_transactions.find(
                {"match_type": "invoice", "matched_to": invoice_id, "matched": True},
                {"_id": 0},
            ).to_list(100)
            if not remaining:
                # Plus aucune txn lettree : facture revient en unpaid
                await db.invoices.update_one(
                    {"id": invoice_id},
                    {"$set": {"status": "unpaid"},
                     "$unset": {"paid_at": "", "paid_by_transaction_id": "",
                                "paid_by_transaction_ids": "", "amount_paid": "",
                                "lettrage_code": ""}},
                )
            else:
                # Il reste des txns lettrees : recalcul partial / full
                total_paid = round(sum(abs(float(r.get("amount", 0) or 0)) for r in remaining), 2)
                inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
                inv_amount = round(float(
                    inv.get("amount_ttc") or inv.get("total_amount") or inv.get("amount") or 0
                ), 2) if inv else 0
                is_full = inv_amount > 0 and abs(total_paid - inv_amount) < 0.01
                upd = {
                    "status": "paid" if is_full else "partially_paid",
                    "amount_paid": total_paid,
                    "paid_by_transaction_ids": [r["id"] for r in remaining],
                }
                unset = {}
                if not is_full:
                    unset["paid_at"] = ""
                await db.invoices.update_one(
                    {"id": invoice_id},
                    {"$set": upd, **({"$unset": unset} if unset else {})},
                )
        # Re-genere l'ecriture FI en mode "compte d'attente 499000" si l'extrait est comptabilise
        # (sinon le compte bancaire disparait du bilan apres delettrage).
        try:
            if prev and prev.get("statement_id"):
                stmt = await db.bank_statements.find_one(
                    {"id": prev["statement_id"]}, {"_id": 0, "status": 1}
                )
                if stmt and stmt.get("status") == "posted":
                    fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
                    if fresh:
                        await generate_bank_entry(db, fresh)
        except Exception as e:
            print(f"[unlettrage] regen FI failed: {e}")
        return {"message": "Lettrage annule"}

    @router.post("/unlettrage-by-invoice/{invoice_id}")
    async def unlettrage_by_invoice(invoice_id: str):
        """Delettre la (ou les) transaction(s) bancaire(s) lettree(s) a une facture."""
        cursor = db.bank_transactions.find(
            {"match_type": "invoice", "matched_to": invoice_id, "matched": True},
            {"_id": 0},
        )
        txns = await cursor.to_list(50)
        if not txns:
            raise HTTPException(404, "Aucune transaction lettree a cette facture")
        for t in txns:
            try:
                await _delete_auto_entries(db, "bank_txn", t["id"])
            except Exception:
                pass
            await db.bank_transactions.update_one(
                {"id": t["id"]},
                {"$set": {"matched": False, "matched_to": "", "match_type": ""}}
            )
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$set": {"status": "unpaid"},
             "$unset": {"paid_at": "", "paid_by_transaction_id": ""}}
        )
        return {"message": f"{len(txns)} transaction(s) delettree(s)", "count": len(txns)}

    # ---- CATEGORIZATION (iter90k) ----
    # Attacher une nature (expense_category + distribution_key) a une
    # transaction bancaire non lettree. Support des splits multi-natures
    # (une transaction = plusieurs lignes de charge/produit). Genere
    # automatiquement une ecriture FI multi-lignes qui apparait ensuite
    # dans /api/fiscal/expenses via la pass FI/OD de compute_expense_rows.
    @router.post("/transactions/{txn_id}/categorize")
    async def categorize_transaction(txn_id: str, data: CategorizeTransactionInput, request: Request):
        txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        if not txn:
            raise HTTPException(404, "Transaction non trouvee")
        _ensure_copro_access(request, txn.get("copropriete_id", ""))
        if txn.get("matched") and txn.get("match_type") != "expense_category":
            raise HTTPException(400,
                "Transaction deja lettree a un tiers - delettrez d'abord")
        if not data.splits:
            raise HTTPException(422, "Au moins un split (nature) est requis")

        copro_id = txn.get("copropriete_id", "")
        txn_amt = round(abs(float(txn.get("amount", 0) or 0)), 2)
        if txn_amt <= 0:
            raise HTTPException(400, "Montant de la transaction est nul")

        total_split = round(sum(float(s.amount or 0) for s in data.splits), 2)
        if abs(total_split - txn_amt) > 0.01:
            raise HTTPException(400,
                f"Somme des splits ({total_split:.2f}) doit egaler le montant "
                f"de la transaction ({txn_amt:.2f})")

        resolved: List[dict] = []
        for i, s in enumerate(data.splits):
            if float(s.amount or 0) <= 0:
                raise HTTPException(400, f"Split #{i+1}: montant doit etre > 0")
            if not s.expense_category_id:
                raise HTTPException(400, f"Split #{i+1}: nature manquante")
            if not s.distribution_key_id:
                raise HTTPException(400, f"Split #{i+1}: cle de repartition manquante")
            cat = await db.expense_categories.find_one(
                {"id": s.expense_category_id, "copropriete_id": copro_id},
                {"_id": 0},
            )
            if not cat:
                raise HTTPException(400, f"Split #{i+1}: nature inconnue")
            pcmn = await db.pcmn_accounts.find_one(
                {"number": cat["account_number"], "copropriete_id": copro_id},
                {"_id": 0},
            )
            if not pcmn or pcmn.get("class_num") not in (5, 6, 7):
                raise HTTPException(400,
                    f"Split #{i+1}: le compte {cat.get('account_number')} "
                    f"doit etre de classe 5 (58 Virements internes), 6 (charge) ou 7 (produit)")
            if pcmn.get("class_num") == 5 and not (cat.get("account_number") or "").startswith("58"):
                raise HTTPException(400,
                    f"Split #{i+1}: en classe 5, seul le compte 58* (Virements internes) est accepte")
            dk = await db.distribution_keys.find_one(
                {"id": s.distribution_key_id, "copropriete_id": copro_id},
                {"_id": 0},
            )
            if not dk:
                raise HTTPException(400, f"Split #{i+1}: cle inconnue")
            resolved.append({
                "expense_category_id": s.expense_category_id,
                "expense_category_name": cat.get("name", ""),
                "account_number": cat["account_number"],
                "account_name": pcmn.get("name", ""),
                "account_class": pcmn.get("class_num"),
                "distribution_key_id": s.distribution_key_id,
                "distribution_key_name": dk.get("name", ""),
                "amount": round(float(s.amount), 2),
                "description": (s.description or "").strip(),
            })

        await db.bank_transactions.update_one(
            {"id": txn_id},
            {"$set": {
                "matched": True,
                "match_type": "expense_category",
                "matched_to": "",
                "category_splits": resolved,
            }}
        )
        fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        entry = None
        try:
            entry = await generate_bank_entry(db, fresh)
        except Exception as e:
            print(f"[categorize] generate_bank_entry failed: {e}")
        return {
            "message": f"Transaction categorisee en {len(resolved)} nature(s)",
            "splits": resolved,
            "journal_entry_id": entry.get("id") if entry else None,
        }

    @router.delete("/transactions/{txn_id}/categorize")
    async def uncategorize_transaction(txn_id: str, request: Request):
        txn = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        if not txn:
            raise HTTPException(404, "Transaction non trouvee")
        _ensure_copro_access(request, txn.get("copropriete_id", ""))
        if txn.get("match_type") != "expense_category":
            raise HTTPException(400, "Transaction n'est pas categorisee")
        try:
            await _delete_auto_entries(db, "bank_txn", txn_id)
        except Exception:
            pass
        await db.bank_transactions.update_one(
            {"id": txn_id},
            {"$set": {"matched": False, "match_type": "", "matched_to": ""},
             "$unset": {"category_splits": ""}}
        )
        # Regenere l'ecriture FI en compte d'attente si l'extrait est comptabilise
        if txn.get("statement_id"):
            stmt = await db.bank_statements.find_one(
                {"id": txn["statement_id"]}, {"_id": 0, "status": 1}
            )
            if stmt and stmt.get("status") == "posted":
                fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
                if fresh:
                    try:
                        await generate_bank_entry(db, fresh)
                    except Exception as e:
                        print(f"[uncategorize] regen FI failed: {e}")
        return {"message": "Categorisation annulee"}

    # ---- CODA IMPORT ----
    @router.post("/statements/import-files")
    async def import_statement_files(
        request: Request,
        files: List[UploadFile] = File(...),
        copropriete_id: str = Form(""),
    ):
        """iter90l : Import multi-fichiers PDF/CSV -> creation auto de
        bank_statements + bank_transactions en status='draft'.

        Strategie d'extraction :
        - PDF : IA Vision (Gemini) directement
        - CSV : parser generique (detection auto de colonnes) avec fallback IA

        Le fichier original est stocke en GridFS (bucket 'bank_statement_sources')
        et référencé dans bank_statements.source_file_id pour audit.

        Securite (iter90n) :
        - Chinese wall enforced sur copropriete_id (SEC-001)
        - Max 20 fichiers par upload, max 10 MB par fichier (SEC-002)
        - Total 100 MB agrege par upload
        - Content-type sniffing : uniquement PDF (%PDF-) et texte imprimable
        """
        from bank_import import extract_bank_statement
        from gridfs_storage import get_bank_statement_sources_storage
        import tempfile
        import mimetypes

        if not files:
            raise HTTPException(400, "Aucun fichier fourni")
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id est obligatoire")

        # iter90n : SEC-001 : chinese wall
        _ensure_copro_access(request, copropriete_id)

        # iter90n : SEC-002 : bornes anti-abus
        MAX_FILES = 20
        MAX_FILE_BYTES = 10 * 1024 * 1024      # 10 MB par fichier
        MAX_TOTAL_BYTES = 100 * 1024 * 1024    # 100 MB agrege
        if len(files) > MAX_FILES:
            raise HTTPException(
                413,
                f"Trop de fichiers ({len(files)} > {MAX_FILES}). "
                "Limitez a 20 fichiers par upload.",
            )

        # Verifier que la copro existe
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0, "id": 1})
        if not copro:
            raise HTTPException(400, "Copropriete inconnue")

        storage = get_bank_statement_sources_storage(db)
        results: list[dict] = []
        total_stmts = 0
        total_txns = 0
        total_bytes_read = 0

        for uf in files:
            fname = uf.filename or "extrait.pdf"
            mime = uf.content_type or mimetypes.guess_type(fname)[0] or "application/octet-stream"
            content = await uf.read()
            # iter90n : SEC-002 : bornes taille et content-type sniffing
            if not content:
                results.append({
                    "filename": fname, "status": "error",
                    "error": "Fichier vide",
                })
                continue
            if len(content) > MAX_FILE_BYTES:
                results.append({
                    "filename": fname, "status": "error",
                    "error": f"Fichier trop volumineux ({len(content) // 1024} KB > {MAX_FILE_BYTES // 1024} KB max)",
                })
                continue
            total_bytes_read += len(content)
            if total_bytes_read > MAX_TOTAL_BYTES:
                results.append({
                    "filename": fname, "status": "error",
                    "error": "Volume total depasse (100 MB max par upload)",
                })
                continue
            # Content-type sniffing : verifier magic bytes / texte imprimable
            is_pdf = content[:5] == b"%PDF-"
            lower_name = fname.lower()
            if not is_pdf and not (lower_name.endswith(".csv") or lower_name.endswith(".txt")):
                results.append({
                    "filename": fname, "status": "error",
                    "error": "Format non supporte (attendu PDF ou CSV)",
                })
                continue
            if not is_pdf:
                # CSV/TXT : verifier que le contenu est textuel imprimable
                sample = content[:4096]
                non_printable = sum(1 for b in sample if b < 0x09 or (0x0e <= b < 0x20 and b not in (0x0a, 0x0d)))
                if non_printable > len(sample) * 0.1:
                    results.append({
                        "filename": fname, "status": "error",
                        "error": "Fichier CSV binaire ou corrompu",
                    })
                    continue
            # Persister l'original en GridFS
            try:
                gid = await storage.upload(
                    filename=fname, contents=content,
                    metadata={"copropriete_id": copropriete_id, "mime": mime,
                              "uploaded_at": datetime.now(timezone.utc).isoformat()},
                )
            except Exception as e:
                results.append({
                    "filename": fname, "status": "error",
                    "error": f"Persistance GridFS echouee: {e}",
                })
                continue

            # Ecrire un fichier temp pour LlmChat (necessaire pour Gemini)
            suffix = ".pdf" if (fname.lower().endswith(".pdf")
                                or "pdf" in mime) else ".csv"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                extracted = await extract_bank_statement(
                    content=content, filename=fname,
                    mime_type=mime, tmp_path=tmp_path,
                )
            except Exception as e:
                # Nettoyer et enregistrer l'erreur
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                await storage.delete(gid)
                results.append({
                    "filename": fname, "status": "error",
                    "error": f"Extraction echouee: {str(e)[:200]}",
                })
                continue
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            txns_data = extracted.get("transactions") or []
            if not txns_data:
                await storage.delete(gid)
                results.append({
                    "filename": fname, "status": "error",
                    "error": "Aucune transaction extraite",
                    "warnings": extracted.get("warnings", []),
                    "extraction_method": extracted.get("extraction_method"),
                })
                continue

            # Creer statement en draft
            stmt_id = str(uuid.uuid4())
            period_from = extracted.get("period_from") or txns_data[0].get("date", "")
            period_to = extracted.get("period_to") or txns_data[-1].get("date", "")
            stmt_doc = {
                "id": stmt_id,
                "number": f"IMP-{fname[:30]}",
                "date": period_to or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "period_from": period_from,
                "period_to": period_to,
                "account_number": extracted.get("account_number") or "",
                "opening_balance": float(extracted.get("opening_balance") or 0),
                "closing_balance": float(extracted.get("closing_balance") or 0),
                "status": "draft",
                "source": "PDF" if fname.lower().endswith(".pdf") else "CSV",
                "source_extraction_method": extracted.get("extraction_method", ""),
                "source_file_id": gid,
                "source_file_name": fname,
                "filename": fname,
                "warnings": extracted.get("warnings", []),
                "copropriete_id": copropriete_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.bank_statements.insert_one(stmt_doc)

            # Creer transactions non lettrees
            txns_docs = []
            for t in txns_data:
                amt = float(t.get("amount", 0) or 0)
                txns_docs.append({
                    "id": str(uuid.uuid4()),
                    "statement_id": stmt_id,
                    "date": t.get("date", ""),
                    "amount": amt,
                    "counterparty_name": (t.get("counterparty_name") or "").strip(),
                    "counterparty_account": (t.get("counterparty_account") or "").strip(),
                    "communication": (t.get("communication") or "").strip(),
                    "transaction_type": t.get("transaction_type")
                                        or ("credit" if amt >= 0 else "debit"),
                    "account_number": extracted.get("account_number") or "",
                    "matched": False, "matched_to": "", "match_type": "",
                    "copropriete_id": copropriete_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            if txns_docs:
                await db.bank_transactions.insert_many(txns_docs)

            total_stmts += 1
            total_txns += len(txns_docs)
            results.append({
                "filename": fname, "status": "ok",
                "statement_id": stmt_id,
                "transactions_count": len(txns_docs),
                "extraction_method": extracted.get("extraction_method"),
                "warnings": extracted.get("warnings", []),
                "period_from": period_from, "period_to": period_to,
            })

        return {
            "results": results,
            "total_statements": total_stmts,
            "total_transactions": total_txns,
        }

    @router.get("/statements/{stmt_id}/source-file")
    async def download_statement_source(stmt_id: str, request: Request):
        """Retourne le PDF/CSV original importe (audit)."""
        from gridfs_storage import get_bank_statement_sources_storage
        from fastapi.responses import Response
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait introuvable")
        _ensure_copro_access(request, stmt.get("copropriete_id", ""))
        gid = stmt.get("source_file_id")
        if not gid:
            raise HTTPException(404, "Aucun fichier source attache")
        storage = get_bank_statement_sources_storage(db)
        try:
            data = await storage.download(gid)
            info = await storage.stat(gid)
        except Exception:
            raise HTTPException(404, "Fichier introuvable en GridFS")
        fname = (info or {}).get("filename") or stmt.get("source_file_name") or "extrait.pdf"
        mime = (info or {}).get("metadata", {}).get("mime") or "application/octet-stream"
        return Response(content=data, media_type=mime, headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
        })

    # ---- CODA IMPORT (legacy) ----
    async def import_coda(file: UploadFile = File(...), copropriete_id: Optional[str] = Form("")):
        from coda_parser import parse_coda_file

        content = await file.read()
        text = content.decode("latin-1")

        try:
            parsed = parse_coda_file(text)
        except Exception as e:
            raise HTTPException(400, f"Erreur de parsing CODA: {str(e)}")

        # Create statement
        stmt_id = str(uuid.uuid4())
        old_bal = parsed.get("old_balance", {})
        new_bal = parsed.get("new_balance", {})

        statement = {
            "id": stmt_id,
            "number": old_bal.get("statement_number", ""),
            "date": new_bal.get("date", old_bal.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))),
            "account_number": old_bal.get("account_number", ""),
            "opening_balance": old_bal.get("balance", 0),
            "closing_balance": new_bal.get("balance", 0),
            "source": "CODA",
            "filename": file.filename,
            "copropriete_id": copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.bank_statements.insert_one(statement)

        # Create transactions
        transactions = []
        for mov in parsed.get("movements", []):
            txn = {
                "id": str(uuid.uuid4()),
                "statement_id": stmt_id,
                "date": mov.get("value_date", ""),
                "amount": mov.get("amount", 0),
                "counterparty_name": mov.get("counterparty_name", ""),
                "counterparty_account": mov.get("counterparty_account", ""),
                "communication": mov.get("communication", ""),
                "transaction_type": mov.get("type", "credit"),
                "account_number": "",
                "matched": False,
                "matched_to": "",
                "match_type": "",
                "copropriete_id": copropriete_id or "",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            transactions.append(txn)

        if transactions:
            await db.bank_transactions.insert_many(transactions)
            for txn in transactions:
                await _try_auto_lettrage_vcs(txn)

        return {
            "message": f"Import CODA reussi: {len(transactions)} transactions importees",
            "statement_id": stmt_id,
            "transactions_count": len(transactions),
            "opening_balance": statement["opening_balance"],
            "closing_balance": statement["closing_balance"]
        }

    # ---- CODA PREVIEW + CONFIRMED IMPORT (mapping UI) ----
    @router.post("/coda/preview")
    async def preview_coda(
        file: UploadFile = File(...),
        copropriete_id: Optional[str] = Form(""),
    ):
        """Parse un fichier CODA SANS rien persister.
        Retourne la structure parsee + une suggestion de match par mouvement,
        + un flag `duplicate_warning` si le fichier (hash SHA256) a deja ete importe.

        L'UI utilise ce retour pour afficher une table de mapping et permettre
        a l'utilisateur d'override les suggestions avant import definitif.
        """
        from coda_parser import parse_coda_file
        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        text = content.decode("latin-1", errors="replace")
        try:
            parsed = parse_coda_file(text)
        except Exception as e:
            raise HTTPException(400, f"Erreur de parsing CODA: {str(e)}")

        # Detection doublon : meme hash deja importe pour cette ACP
        duplicate_warning = None
        if copropriete_id:
            existing = await db.bank_statements.find_one(
                {"coda_hash": file_hash, "copropriete_id": copropriete_id},
                {"_id": 0},
            )
            if existing:
                duplicate_warning = {
                    "statement_id": existing.get("id"),
                    "statement_number": existing.get("number", ""),
                    "imported_at": existing.get("created_at", ""),
                    "message": f"Ce fichier CODA a deja ete importe le {(existing.get('created_at','') or '')[:10]} (extrait {existing.get('number','?')}). Verifiez avant de reimporter.",
                }

        # Compatibilite IBAN compte detenteur : verifie que le compte de
        # l'extrait correspond a un compte bancaire connu pour cette ACP.
        account_holder_warning = None
        if copropriete_id and parsed.get("old_balance", {}).get("account_number"):
            stmt_account = parsed["old_balance"]["account_number"].replace(" ", "")
            copro = await db.coproprietes.find_one(
                {"id": copropriete_id}, {"_id": 0, "bank_accounts": 1}
            )
            known_ibans = [
                (ba.get("iban") or "").replace(" ", "")
                for ba in (copro.get("bank_accounts") or [])
            ] if copro else []
            if known_ibans and stmt_account not in known_ibans:
                # Compare aussi sans le pays/format
                clean = "".join(c for c in stmt_account if c.isalnum())
                if not any(clean and clean in ki for ki in known_ibans):
                    account_holder_warning = (
                        f"Le compte {stmt_account} ne correspond a aucun IBAN "
                        f"connu pour cette ACP ({len(known_ibans)} IBAN(s) enregistre(s))."
                    )

        # Suggestions de match par mouvement
        movements_enriched = []
        for idx, mov in enumerate(parsed.get("movements", [])):
            suggestion = await _suggest_match_for_movement(mov, copropriete_id or "")
            movements_enriched.append({
                "index": idx,
                "value_date": mov.get("value_date") or "",
                "entry_date": mov.get("entry_date") or "",
                "amount": mov.get("amount", 0),
                "type": mov.get("type", "credit"),
                "counterparty_name": mov.get("counterparty_name", ""),
                "counterparty_account": mov.get("counterparty_account", ""),
                "communication": mov.get("communication", ""),
                "transaction_code": mov.get("transaction_code", ""),
                "reference": mov.get("reference", ""),
                "suggestion": suggestion,
            })

        return {
            "file_hash": file_hash,
            "filename": file.filename,
            "header": parsed.get("header", {}),
            "old_balance": parsed.get("old_balance", {}),
            "new_balance": parsed.get("new_balance", {}),
            "summary": parsed.get("summary", {}),
            "movements": movements_enriched,
            "duplicate_warning": duplicate_warning,
            "account_holder_warning": account_holder_warning,
        }

    class CodaMovementConfirm(BaseModel):
        # Champs natifs du mouvement CODA (transmis tel quel depuis le preview)
        value_date: str = ""
        entry_date: str = ""
        amount: float = 0.0
        type: str = "credit"
        counterparty_name: str = ""
        counterparty_account: str = ""
        communication: str = ""
        transaction_code: str = ""
        reference: str = ""
        # Override utilisateur (None ou {match_type, match_id})
        manual_match_type: Optional[str] = ""  # "owner_payment" | "supplier_payment" | "invoice" | ""
        manual_match_id: Optional[str] = ""
        # Si False, l'utilisateur veut IGNORER ce mouvement (ne pas creer de txn)
        include: bool = True

    class CodaConfirmInput(BaseModel):
        file_hash: str
        filename: Optional[str] = ""
        copropriete_id: str
        # Header / balances (issus du preview, repostes tel quel)
        statement_number: Optional[str] = ""
        statement_date: Optional[str] = ""
        account_number: Optional[str] = ""
        opening_balance: Optional[float] = 0.0
        closing_balance: Optional[float] = 0.0
        # Mouvements valides par l'utilisateur
        movements: List[CodaMovementConfirm] = []

    @router.post("/coda/import-confirmed")
    async def import_coda_confirmed(data: CodaConfirmInput):
        """Importe les mouvements CODA apres validation par l'utilisateur (UI mapping).

        - Verifie que `file_hash` n'a pas ete deja importe pour cette ACP (idempotence)
        - Cree le statement avec `coda_hash` stocke
        - Cree une transaction par mouvement included
        - Si manual_match fourni : applique le match + tente generer ecriture
        - Sinon : tente auto-lettrage VCS comme dans /coda/import classique
        """
        if not data.copropriete_id:
            raise HTTPException(400, "copropriete_id requis")
        # Re-verifie hash unique pour eviter race
        existing = await db.bank_statements.find_one(
            {"coda_hash": data.file_hash, "copropriete_id": data.copropriete_id},
            {"_id": 0},
        )
        if existing:
            raise HTTPException(
                409,
                f"Ce fichier CODA a deja ete importe (extrait {existing.get('number','?')} "
                f"du {(existing.get('created_at','') or '')[:10]}). Suppression requise avant re-import."
            )

        stmt_id = str(uuid.uuid4())
        statement = {
            "id": stmt_id,
            "number": data.statement_number or "",
            "date": data.statement_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "account_number": data.account_number or "",
            "opening_balance": float(data.opening_balance or 0),
            "closing_balance": float(data.closing_balance or 0),
            "source": "CODA",
            "filename": data.filename or "",
            "copropriete_id": data.copropriete_id,
            "coda_hash": data.file_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.bank_statements.insert_one(statement)

        included = [m for m in data.movements if m.include]
        ignored = len(data.movements) - len(included)
        transactions_to_insert = []
        manual_decisions = []  # (txn_id, manual_match_type, manual_match_id)
        for mov in included:
            txn_id = str(uuid.uuid4())
            txn = {
                "id": txn_id,
                "statement_id": stmt_id,
                "date": mov.value_date or mov.entry_date or "",
                "amount": float(mov.amount or 0),
                "counterparty_name": mov.counterparty_name or "",
                "counterparty_account": mov.counterparty_account or "",
                "communication": mov.communication or "",
                "transaction_type": mov.type or "credit",
                "account_number": "",
                "matched": False,
                "matched_to": "",
                "match_type": "",
                "copropriete_id": data.copropriete_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            transactions_to_insert.append(txn)
            if mov.manual_match_type and mov.manual_match_id:
                manual_decisions.append((txn_id, mov.manual_match_type, mov.manual_match_id))

        if transactions_to_insert:
            await db.bank_transactions.insert_many(transactions_to_insert)

        # Applique les overrides utilisateur + auto-lettrage pour les autres
        matched_manual = 0
        matched_auto = 0
        manual_ids = {d[0] for d in manual_decisions}
        for txn_id, mtype, mid in manual_decisions:
            # Verifie que l'entite existe et applique le match
            ok = False
            if mtype == "owner_payment":
                ok = (await db.owners.count_documents({"id": mid}, limit=1)) > 0
            elif mtype == "supplier_payment":
                ok = (await db.suppliers.count_documents({"id": mid}, limit=1)) > 0
            elif mtype == "invoice":
                ok = (await db.invoices.count_documents({"id": mid}, limit=1)) > 0
            if not ok:
                continue
            await db.bank_transactions.update_one(
                {"id": txn_id},
                {"$set": {"matched": True, "matched_to": mid, "match_type": mtype}},
            )
            fresh = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
            try:
                if fresh:
                    await generate_bank_entry(db, fresh)
                    if mtype == "invoice":
                        await db.invoices.update_one(
                            {"id": mid},
                            {"$set": {"status": "paid", "paid_at": fresh.get("date"),
                                      "paid_by_transaction_id": txn_id}},
                        )
            except Exception as e:
                print(f"[coda-confirmed] manual match entry failed: {e}")
            matched_manual += 1

        # Auto-lettrage VCS pour les txns sans override
        for txn in transactions_to_insert:
            if txn["id"] in manual_ids:
                continue
            await _try_auto_lettrage_vcs(txn)
            fresh = await db.bank_transactions.find_one({"id": txn["id"]}, {"_id": 0})
            if fresh and fresh.get("matched"):
                matched_auto += 1

        return {
            "message": f"Import CODA confirme : {len(transactions_to_insert)} transaction(s) creee(s), {ignored} ignoree(s)",
            "statement_id": stmt_id,
            "transactions_count": len(transactions_to_insert),
            "ignored_count": ignored,
            "matched_manual": matched_manual,
            "matched_auto": matched_auto,
            "opening_balance": statement["opening_balance"],
            "closing_balance": statement["closing_balance"],
        }


    # ---- ADD LINES TO EXISTING STATEMENT ----
    @router.post("/statements/{stmt_id}/add-lines")
    async def add_lines_to_statement(stmt_id: str, data: AddLinesInput):
        """Add multiple transaction lines to an existing statement at once."""
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
        # Inherit ACP from the parent statement (or from payload)
        copro_id = stmt.get("copropriete_id") or (data.copropriete_id or "")
        txns = []
        for line in data.lines:
            if abs(line.amount) < 0.001:
                continue
            # Si transaction_type='debit', stocker amount NEGATIF pour coherence affichage
            stored_amount = abs(float(line.amount))
            if line.transaction_type == "debit":
                stored_amount = -stored_amount
            txn = {
                "id": str(uuid.uuid4()),
                "statement_id": stmt_id,
                "date": line.date,
                "amount": stored_amount,
                "counterparty_name": line.counterparty_name,
                "counterparty_account": line.counterparty_account,
                "communication": line.communication,
                "transaction_type": line.transaction_type,
                "account_number": stmt.get("account_number", ""),
                "matched": False,
                "matched_to": "",
                "match_type": "",
                "copropriete_id": copro_id,
                "counterparty_id": line.counterparty_id or "",
                "counterparty_type": line.counterparty_type or "",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            txns.append(txn)
        if txns:
            await db.bank_transactions.insert_many(txns)
            # PRIORITE 1: contrepartie explicite (UI). PRIORITE 2: auto-VCS.
            for txn in txns:
                await _try_explicit_match_then_vcs(txn)
        return {"message": f"{len(txns)} lignes ajoutees", "count": len(txns)}

    # ---- SEARCH (for owner payments) ----
    async def _get_scope_for_filter(request):
        """Retourne (is_super, allowed_copros, allowed_owner_ids).
        - superadmin -> (True, None, None) = pas de filtre
        - syndic -> (False, [copro_ids], {owner_ids_set})
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if is_superadmin_only(role):
            return True, None, None
        allowed_copros = user.get("copropriete_ids", []) or []
        if not allowed_copros:
            return False, [], set()
        ids1 = await db.lots.distinct("owner_id", {"copropriete_id": {"$in": allowed_copros}})
        ids2 = await db.lots.distinct("owner_ids", {"copropriete_id": {"$in": allowed_copros}})
        allowed_owner_ids = {x for x in (ids1 or []) if x} | {x for x in (ids2 or []) if x}
        return False, allowed_copros, allowed_owner_ids

    def _supplier_visible(s: dict, allowed_copros) -> bool:
        if allowed_copros is None:
            return True
        s_copros = set()
        if s.get("copropriete_id"):
            s_copros.add(s["copropriete_id"])
        s_copros.update((s.get("tier_accounts") or {}).keys())
        return any(c in allowed_copros for c in s_copros)

    @router.get("/search-owners")
    async def search_owners_for_payment(request: Request, q: Optional[str] = ""):
        is_super, allowed_copros, allowed_owner_ids = await _get_scope_for_filter(request)
        base_q = {}
        if q:
            base_q = {"$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"email": {"$regex": q, "$options": "i"}},
                {"vcs_code": {"$regex": q.replace("+", "\\+"), "$options": "i"}},
                {"vcs_digits": {"$regex": q.replace("+", "").replace("/", ""), "$options": "i"}},
            ]}
        if not is_super:
            if not allowed_owner_ids:
                return []
            if base_q:
                base_q = {"$and": [base_q, {"id": {"$in": list(allowed_owner_ids)}}]}
            else:
                base_q = {"id": {"$in": list(allowed_owner_ids)}}
        owners = await db.owners.find(base_q, {"_id": 0}).to_list(50)
        return owners

    # ---- GLOBAL LOOKUP (owners, suppliers, invoices by any text) ----
    @router.get("/lookup")
    async def global_lookup(request: Request, q: str = "", copropriete_id: Optional[str] = None):
        """Search across owners, suppliers (global), invoices (scoped by ACP)."""
        if not q or len(q) < 2:
            return {"owners": [], "suppliers": [], "invoices": []}
        is_super, allowed_copros, allowed_owner_ids = await _get_scope_for_filter(request)
        clean = q.replace("+", "").replace("/", "").replace(" ", "")
        own_q = {"$or": [
            {"name": {"$regex": q, "$options": "i"}},
            {"vcs_digits": {"$regex": clean, "$options": "i"}},
            {"vcs_code": {"$regex": q.replace("+", "\\+"), "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
        ]}
        if not is_super:
            if not allowed_owner_ids:
                owners = []
            else:
                own_q = {"$and": [own_q, {"id": {"$in": list(allowed_owner_ids)}}]}
                owners = await db.owners.find(own_q, {"_id": 0}).to_list(10)
        else:
            owners = await db.owners.find(own_q, {"_id": 0}).to_list(10)
        suppliers_raw = await db.suppliers.find(
            {"$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"vat_number": {"$regex": q, "$options": "i"}},
            ]}, {"_id": 0}
        ).to_list(50)
        suppliers = [s for s in suppliers_raw if _supplier_visible(s, allowed_copros)][:10]
        inv_q = {"$or": [
            {"number": {"$regex": q, "$options": "i"}},
            {"supplier": {"$regex": q, "$options": "i"}},
        ]}
        if copropriete_id:
            inv_q = {"$and": [inv_q, {"copropriete_id": copropriete_id}]}
        elif not is_super:
            if not allowed_copros:
                return {"owners": owners, "suppliers": suppliers, "invoices": []}
            inv_q = {"$and": [inv_q, {"copropriete_id": {"$in": allowed_copros}}]}
        invoices_data = await db.invoices.find(inv_q, {"_id": 0}).to_list(10)
        return {"owners": owners, "suppliers": suppliers, "invoices": invoices_data}

    # ---- AUTO-LETTRAGE existing unmatched txns ----
    @router.post("/auto-lettrage-vcs")
    async def auto_lettrage_all_vcs(request: Request):
        """Scan all unmatched transactions and try to auto-match by VCS (scoped by ACP)."""
        copropriete_id = request.query_params.get("copropriete_id") or request.headers.get("x-copropriete-id") or ""
        try:
            body = await request.json()
            if isinstance(body, dict) and not copropriete_id:
                copropriete_id = body.get("copropriete_id", "") or ""
        except Exception:
            pass
        q = {"matched": False}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        unmatched = await db.bank_transactions.find(q, {"_id": 0}).to_list(10000)
        matched_count = 0
        for txn in unmatched:
            comm = txn.get("communication", "")
            if comm and len(comm) >= 3:
                await _try_auto_lettrage_vcs(txn)
                updated = await db.bank_transactions.find_one({"id": txn["id"]}, {"_id": 0})
                if updated and updated.get("matched"):
                    matched_count += 1
        return {"message": f"{matched_count} transactions auto-lettrees", "count": matched_count}

    # ---- VCS LOOKUP ----
    @router.get("/vcs-lookup")
    async def vcs_lookup(request: Request, communication: str = ""):
        """Lookup owner by VCS structured communication.
        Chinese wall : un syndic ne trouve QUE les owners de ses ACPs.
        """
        if not communication:
            return {"owner": None}
        clean = communication.replace("+", "").replace("/", "").replace(" ", "").strip()
        if not clean:
            return {"owner": None}
        is_super, allowed_copros, allowed_owner_ids = await _get_scope_for_filter(request)
        q = {"$or": [
            {"vcs_digits": {"$regex": clean, "$options": "i"}},
            {"vcs_code": {"$regex": communication.replace("+", "\\+"), "$options": "i"}},
        ]}
        if not is_super:
            if not allowed_owner_ids:
                return {"owner": None}
            q = {"$and": [q, {"id": {"$in": list(allowed_owner_ids)}}]}
        owner = await db.owners.find_one(q, {"_id": 0})
        return {"owner": owner}

    # ---- BATCH TRANSACTIONS ----
    @router.post("/transactions/batch")
    async def create_batch_transactions(data: BatchTransactionInput):
        """Create multiple transactions at once for a statement."""
        stmt = await db.bank_statements.find_one({"id": data.statement_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
        copro_id = stmt.get("copropriete_id") or (data.copropriete_id or "")
        txns = []
        for t in data.transactions:
            txn = {
                "id": str(uuid.uuid4()),
                "statement_id": data.statement_id,
                "date": t.date,
                "amount": t.amount,
                "counterparty_name": t.counterparty_name,
                "counterparty_account": t.counterparty_account,
                "communication": t.communication,
                "transaction_type": t.transaction_type,
                "account_number": t.account_number,
                "matched": False,
                "matched_to": "",
                "match_type": "",
                "copropriete_id": copro_id,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            txns.append(txn)
        if txns:
            await db.bank_transactions.insert_many(txns)
        return {"message": f"{len(txns)} transactions creees", "count": len(txns)}

    @router.post("/migrate-fallback-bank-account/{copropriete_id}")
    async def migrate_fallback_bank_account(copropriete_id: str):
        """One-shot : remappe les lignes de journal_entries qui pointent sur 550000
        (compte banque fallback) vers le compte PCMN configure sur l'IBAN par defaut
        de l'ACP (ex 55143100). Repare les ecritures historiques."""
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        # Trouver le compte PCMN du compte bancaire par defaut
        target_acc = None
        target_label = None
        for ba in (copro.get("bank_accounts") or []):
            if ba.get("is_default") and ba.get("pcmn_number"):
                target_acc = ba["pcmn_number"]
                target_label = ba.get("label") or "Banque"
                break
        if not target_acc:
            # fallback : 1er bank_account avec pcmn_number
            for ba in (copro.get("bank_accounts") or []):
                if ba.get("pcmn_number"):
                    target_acc = ba["pcmn_number"]
                    target_label = ba.get("label") or "Banque"
                    break
        if not target_acc:
            raise HTTPException(400,
                "Aucun compte PCMN bancaire configure dans bank_accounts de cette ACP")
        # Recupere les entries de cette ACP qui contiennent une ligne 550000
        entries = await db.journal_entries.find(
            {"copropriete_id": copropriete_id, "lines.account_number": "550000"},
            {"_id": 0},
        ).to_list(10000)
        updated = 0
        for e in entries:
            new_lines = []
            changed = False
            for ln in e.get("lines", []):
                if ln.get("account_number") == "550000":
                    ln = dict(ln)
                    ln["account_number"] = target_acc
                    ln["account_name"] = target_label
                    changed = True
                new_lines.append(ln)
            if changed:
                await db.journal_entries.update_one(
                    {"id": e["id"]},
                    {"$set": {"lines": new_lines}},
                )
                updated += 1
        return {
            "message": f"{updated} ecriture(s) migree(s) de 550000 vers {target_acc}",
            "from": "550000",
            "to": target_acc,
            "label": target_label,
            "updated": updated,
        }

    return router
