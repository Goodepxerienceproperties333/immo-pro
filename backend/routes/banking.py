from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class StatementInput(BaseModel):
    number: str
    date: str
    account_number: Optional[str] = ""
    opening_balance: Optional[float] = 0.0
    closing_balance: Optional[float] = 0.0


class TransactionInput(BaseModel):
    statement_id: Optional[str] = ""
    date: str
    amount: float
    counterparty_name: Optional[str] = ""
    counterparty_account: Optional[str] = ""
    communication: Optional[str] = ""
    transaction_type: Optional[str] = "credit"  # credit or debit
    account_number: Optional[str] = ""


class BatchTransactionInput(BaseModel):
    statement_id: str
    transactions: List[TransactionInput]


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

    # ---- BANK STATEMENTS ----
    @router.get("/statements")
    async def list_statements():
        statements = await db.bank_statements.find({}, {"_id": 0}).sort("date", -1).to_list(1000)
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
        return stmt

    @router.delete("/statements/{stmt_id}")
    async def delete_statement(stmt_id: str):
        await db.bank_transactions.delete_many({"statement_id": stmt_id})
        result = await db.bank_statements.delete_one({"id": stmt_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Extrait non trouve")
        return {"message": "Extrait supprime"}

    # ---- TRANSACTIONS ----
    @router.get("/transactions")
    async def list_transactions(statement_id: Optional[str] = None, matched: Optional[bool] = None):
        query = {}
        if statement_id:
            query["statement_id"] = statement_id
        if matched is not None:
            query["matched"] = matched
        txns = await db.bank_transactions.find(query, {"_id": 0}).sort("date", -1).to_list(1000)
        return txns

    @router.post("/transactions")
    async def create_transaction(data: TransactionInput):
        doc = {
            "id": str(uuid.uuid4()),
            "statement_id": data.statement_id,
            "date": data.date,
            "amount": data.amount,
            "counterparty_name": data.counterparty_name,
            "counterparty_account": data.counterparty_account,
            "communication": data.communication,
            "transaction_type": data.transaction_type,
            "account_number": data.account_number,
            "matched": False,
            "matched_to": "",
            "match_type": "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.bank_transactions.insert_one(doc)
        await _try_auto_lettrage_vcs(doc)
        updated = await db.bank_transactions.find_one({"id": doc["id"]}, {"_id": 0})
        return updated or {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/transactions/{txn_id}")
    async def update_transaction(txn_id: str, data: TransactionInput):
        update = {
            "date": data.date, "amount": data.amount,
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
        return {"message": "Lettrage effectue", "transaction_id": data.transaction_id}

    @router.post("/unlettrage/{txn_id}")
    async def unlettrage(txn_id: str):
        result = await db.bank_transactions.update_one(
            {"id": txn_id},
            {"$set": {"matched": False, "matched_to": "", "match_type": ""}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Transaction non trouvee")
        return {"message": "Lettrage annule"}

    # ---- CODA IMPORT ----
    @router.post("/coda/import")
    async def import_coda(file: UploadFile = File(...)):
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
        txns = []
        for line in data.lines:
            if abs(line.amount) < 0.001:
                continue
            txn = {
                "id": str(uuid.uuid4()),
                "statement_id": stmt_id,
                "date": line.date,
                "amount": line.amount,
                "counterparty_name": line.counterparty_name,
                "counterparty_account": line.counterparty_account,
                "communication": line.communication,
                "transaction_type": line.transaction_type,
                "account_number": "",
                "matched": False,
                "matched_to": "",
                "match_type": "",
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
    async def global_lookup(q: str = ""):
        """Search across owners, suppliers, invoices by name/VCS/number."""
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
        invoices_data = await db.invoices.find(inv_q, {"_id": 0}).to_list(10)
        return {"owners": owners, "suppliers": suppliers, "invoices": invoices_data}

    # ---- AUTO-LETTRAGE existing unmatched txns ----
    @router.post("/auto-lettrage-vcs")
    async def auto_lettrage_all_vcs():
        """Scan all unmatched transactions and try to auto-match by VCS."""
        unmatched = await db.bank_transactions.find({"matched": False}, {"_id": 0}).to_list(10000)
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
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            txns.append(txn)
        if txns:
            await db.bank_transactions.insert_many(txns)
        return {"message": f"{len(txns)} transactions creees", "count": len(txns)}

    return router
