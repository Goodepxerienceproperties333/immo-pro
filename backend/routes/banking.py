from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
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
    async def get_statement(stmt_id: str):
        stmt = await db.bank_statements.find_one({"id": stmt_id}, {"_id": 0})
        if not stmt:
            raise HTTPException(404, "Extrait non trouve")
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

    # ---- CODA IMPORT ----
    @router.post("/coda/import")
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
        header = parsed.get("header", {})

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
