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
    async def list_statements(copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        statements = await db.bank_statements.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        return statements

    @router.post("/statements")
    async def create_statement(data: StatementInput):
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "date": data.date,
            "account_number": data.account_number,
            "opening_balance": data.opening_balance,
            "closing_balance": data.closing_balance,
            "copropriete_id": data.copropriete_id or "",
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
        await db.bank_statements.update_one(
            {"id": stmt_id},
            {"$set": {"status": "posted", "posted_at": datetime.now(timezone.utc).isoformat()}}
        )
        return {"status": "ok", "message": "Extrait comptabilise", "computed_closing": computed, "transactions_count": len(txns)}

    @router.post("/statements/{stmt_id}/unpost")
    async def unpost_statement(stmt_id: str):
        """Repasse l'extrait en draft (permet correction)."""
        result = await db.bank_statements.update_one(
            {"id": stmt_id},
            {"$set": {"status": "draft", "posted_at": None}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Extrait non trouve")
        return {"status": "ok", "message": "Extrait repasse en brouillon"}

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
    async def list_transactions(statement_id: Optional[str] = None, matched: Optional[bool] = None, copropriete_id: Optional[str] = None):
        query = {}
        if statement_id:
            query["statement_id"] = statement_id
        if matched is not None:
            query["matched"] = matched
        if copropriete_id:
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
        try:
            fresh = await db.bank_transactions.find_one({"id": data.transaction_id}, {"_id": 0})
            if fresh:
                await generate_bank_entry(db, fresh)
        except Exception as e:
            print(f"[auto-entry] bank lettrage failed: {e}")
        return {"message": "Lettrage effectue", "transaction_id": data.transaction_id}

    @router.post("/unlettrage/{txn_id}")
    async def unlettrage(txn_id: str):
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
        return {"message": "Lettrage annule"}

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

    return router
