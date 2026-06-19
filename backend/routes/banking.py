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


class BatchTransactionInput(BaseModel):
    statement_id: str
    transactions: List[TransactionInput]
    copropriete_id: Optional[str] = ""


class LettrageInput(BaseModel):
    transaction_id: str
    match_to_id: str
    match_type: str  # invoice, owner_payment, supplier_payment


class InlineLineInput(BaseModel):
    date: str
    amount: float
    counterparty_name: Optional[str] = ""
    counterparty_account: Optional[str] = ""
    communication: Optional[str] = ""
    transaction_type: Optional[str] = "credit"


class AddLinesInput(BaseModel):
    lines: List[InlineLineInput]
    copropriete_id: Optional[str] = ""


def create_banking_router(db):
    router = APIRouter(prefix="/api/banking")

    async def _try_auto_lettrage_vcs(txn_doc):
        """Try to auto-match a transaction by VCS communication."""
        comm = txn_doc.get("communication", "")
        if not comm or len(comm) < 3:
            return
        clean = comm.replace("+", "").replace("/", "").replace(" ", "").strip()
        if not clean:
            return
        owner = await db.owners.find_one(
            {"$or": [
                {"vcs_digits": clean},
                {"vcs_digits": {"$regex": clean, "$options": "i"}},
            ]},
            {"_id": 0}
        )
        if not owner:
            owner = await db.owners.find_one(
                {"vcs_code": {"$regex": comm.replace("+", "\\+"), "$options": "i"}},
                {"_id": 0}
            )
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

    # ---- BANK STATEMENTS ----
    @router.get("/statements")
    async def list_statements(request: Request, copropriete_id: Optional[str] = None):
        """Chinese wall STRICT : aucune liste cross-ACP possible. Si aucun
        copropriete_id n'est fourni (ni via param ni via header X-Copropriete-Id),
        on retourne une liste vide pour eviter toute fuite."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        statements = await db.bank_statements.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).sort("date", -1).to_list(1000)
        return statements

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
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "date": data.date,
            "account_number": iban,
            "opening_balance": data.opening_balance,
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
        fi_created = 0
        fi_errors = []
        for t in txns:
            try:
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
    ):
        """Chinese wall STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id) sauf si on liste par `statement_id`."""
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
        txns = await db.bank_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return txns

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
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.bank_transactions.insert_one(doc)
        await _try_auto_lettrage_vcs(doc)
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
        result = await db.bank_transactions.update_one({"id": txn_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        updated = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        if updated and not updated.get("matched"):
            await _try_auto_lettrage_vcs(updated)
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
            {"$set": {"matched": False, "matched_to": "", "match_type": ""}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        # Si on delettre une transaction qui pointait sur une facture : remettre la facture en unpaid
        if prev and prev.get("match_type") == "invoice" and prev.get("matched_to"):
            await db.invoices.update_one(
                {"id": prev["matched_to"]},
                {"$set": {"status": "unpaid"},
                 "$unset": {"paid_at": "", "paid_by_transaction_id": ""}}
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
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            txns.append(txn)
        if txns:
            await db.bank_transactions.insert_many(txns)
            # Auto-lettrage VCS for each new transaction
            for txn in txns:
                await _try_auto_lettrage_vcs(txn)
        return {"message": f"{len(txns)} lignes ajoutees", "count": len(txns)}

    # ---- SEARCH (for owner payments) ----
    @router.get("/search-owners")
    async def search_owners_for_payment(q: Optional[str] = ""):
        if not q:
            owners = await db.owners.find({}, {"_id": 0}).to_list(50)
        else:
            owners = await db.owners.find(
                {"$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"email": {"$regex": q, "$options": "i"}},
                    {"vcs_code": {"$regex": q.replace("+", "\\+"), "$options": "i"}},
                    {"vcs_digits": {"$regex": q.replace("+", "").replace("/", ""), "$options": "i"}},
                ]},
                {"_id": 0}
            ).to_list(50)
        return owners

    # ---- GLOBAL LOOKUP (owners, suppliers, invoices by any text) ----
    @router.get("/lookup")
    async def global_lookup(q: str = "", copropriete_id: Optional[str] = None):
        """Search across owners, suppliers (global), invoices (scoped by ACP)."""
        if not q or len(q) < 2:
            return {"owners": [], "suppliers": [], "invoices": []}
        clean = q.replace("+", "").replace("/", "").replace(" ", "")
        owners = await db.owners.find(
            {"$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"vcs_digits": {"$regex": clean, "$options": "i"}},
                {"vcs_code": {"$regex": q.replace("+", "\\+"), "$options": "i"}},
                {"email": {"$regex": q, "$options": "i"}},
            ]}, {"_id": 0}
        ).to_list(10)
        suppliers = await db.suppliers.find(
            {"$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"vat_number": {"$regex": q, "$options": "i"}},
            ]}, {"_id": 0}
        ).to_list(10)
        inv_q = {"$or": [
            {"number": {"$regex": q, "$options": "i"}},
            {"supplier": {"$regex": q, "$options": "i"}},
        ]}
        if copropriete_id:
            inv_q = {"$and": [inv_q, {"copropriete_id": copropriete_id}]}
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
    async def vcs_lookup(communication: str = ""):
        """Lookup owner by VCS structured communication."""
        if not communication:
            return {"owner": None}
        clean = communication.replace("+", "").replace("/", "").replace(" ", "").strip()
        if not clean:
            return {"owner": None}
        owner = await db.owners.find_one(
            {"$or": [
                {"vcs_digits": {"$regex": clean, "$options": "i"}},
                {"vcs_code": {"$regex": communication.replace("+", "\\+"), "$options": "i"}},
            ]},
            {"_id": 0}
        )
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
