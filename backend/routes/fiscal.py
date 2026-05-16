from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class FiscalYearInput(BaseModel):
    name: str
    start_date: str
    end_date: str


class BudgetLineInput(BaseModel):
    account_number: str
    account_name: Optional[str] = ""
    amount: float
    distribution_key_id: Optional[str] = ""


class BudgetInput(BaseModel):
    fiscal_year_id: str
    name: Optional[str] = ""
    lines: List[BudgetLineInput]


def create_fiscal_router(db):
    router = APIRouter(prefix="/api/fiscal")

    # ---- FISCAL YEARS ----
    @router.get("/years")
    async def list_fiscal_years():
        years = await db.fiscal_years.find({}, {"_id": 0}).sort("start_date", -1).to_list(100)
        return years

    @router.post("/years")
    async def create_fiscal_year(data: FiscalYearInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "start_date": data.start_date,
            "end_date": data.end_date,
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.fiscal_years.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/years/{year_id}")
    async def update_fiscal_year(year_id: str, data: FiscalYearInput):
        result = await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {"name": data.name, "start_date": data.start_date, "end_date": data.end_date}},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Exercice non trouve")
        return await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})

    @router.post("/years/{year_id}/close")
    async def close_fiscal_year(year_id: str):
        """Cloture d'exercice: verrouille les ecritures et genere l'a-nouveau."""
        fy = await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")
        if fy["status"] == "closed":
            raise HTTPException(400, "Exercice deja cloture")

        # Compute closing balances for balance-sheet accounts (classes 1-5)
        entries = await db.journal_entries.find(
            {"date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}}, {"_id": 0}
        ).to_list(100000)

        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"debit": 0, "credit": 0, "name": line.get("account_name", "")}
                balances[acc]["debit"] += line.get("debit", 0)
                balances[acc]["credit"] += line.get("credit", 0)

        # Compute result (class 6 - class 7)
        total_charges = sum(b["debit"] - b["credit"] for a, b in balances.items() if a.startswith("6"))
        total_produits = sum(b["credit"] - b["debit"] for a, b in balances.items() if a.startswith("7"))
        result_net = total_produits - total_charges

        # Generate a-nouveau entries for balance sheet accounts (classes 1-5)
        a_nouveau_lines = []
        for acc, bal in sorted(balances.items()):
            if acc[0] in ("1", "2", "3", "4", "5"):
                solde = bal["debit"] - bal["credit"]
                if abs(solde) > 0.01:
                    a_nouveau_lines.append({
                        "account_number": acc,
                        "account_name": bal["name"],
                        "debit": round(solde, 2) if solde > 0 else 0,
                        "credit": round(-solde, 2) if solde < 0 else 0,
                    })

        # Add result to report account
        if abs(result_net) > 0.01:
            if result_net >= 0:
                a_nouveau_lines.append({
                    "account_number": "140100",
                    "account_name": "Benefice reporte",
                    "debit": 0,
                    "credit": round(result_net, 2),
                })
            else:
                a_nouveau_lines.append({
                    "account_number": "140200",
                    "account_name": "Perte reportee",
                    "debit": round(-result_net, 2),
                    "credit": 0,
                })

        if a_nouveau_lines:
            total_d = sum(l["debit"] for l in a_nouveau_lines)
            total_c = sum(l["credit"] for l in a_nouveau_lines)
            a_nouveau_entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "AN",
                "date": fy["end_date"],
                "reference": f"AN-{fy['name']}",
                "description": f"A-nouveau cloture exercice {fy['name']}",
                "lines": a_nouveau_lines,
                "total_debit": round(total_d, 2),
                "total_credit": round(total_c, 2),
                "fiscal_year_id": year_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(a_nouveau_entry)

        await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {
                "status": "closed",
                "result_net": round(result_net, 2),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

        return {
            "message": f"Exercice {fy['name']} cloture",
            "result_net": round(result_net, 2),
            "total_charges": round(total_charges, 2),
            "total_produits": round(total_produits, 2),
            "a_nouveau_lines": len(a_nouveau_lines),
        }

    @router.post("/years/{year_id}/reopen")
    async def reopen_fiscal_year(year_id: str):
        result = await db.fiscal_years.update_one(
            {"id": year_id}, {"$set": {"status": "open", "closed_at": None}}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Exercice non trouve")
        return {"message": "Exercice reouvert"}

    # ---- BUDGETS ----
    @router.get("/budgets")
    async def list_budgets(fiscal_year_id: Optional[str] = None):
        q = {}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        budgets = await db.budgets.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)
        return budgets

    @router.post("/budgets")
    async def create_budget(data: BudgetInput):
        total = sum(l.amount for l in data.lines)
        doc = {
            "id": str(uuid.uuid4()),
            "fiscal_year_id": data.fiscal_year_id,
            "name": data.name or "Budget",
            "lines": [l.model_dump() for l in data.lines],
            "total": round(total, 2),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.budgets.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/budgets/{budget_id}")
    async def get_budget(budget_id: str):
        b = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not b:
            raise HTTPException(404, "Budget non trouve")
        return b

    @router.put("/budgets/{budget_id}")
    async def update_budget(budget_id: str, data: BudgetInput):
        total = sum(l.amount for l in data.lines)
        update = {
            "fiscal_year_id": data.fiscal_year_id,
            "name": data.name or "Budget",
            "lines": [l.model_dump() for l in data.lines],
            "total": round(total, 2),
        }
        result = await db.budgets.update_one({"id": budget_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Budget non trouve")
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    @router.delete("/budgets/{budget_id}")
    async def delete_budget(budget_id: str):
        result = await db.budgets.delete_one({"id": budget_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Budget non trouve")
        return {"message": "Budget supprime"}

    # ---- BUDGET VS ACTUAL ----
    @router.get("/budget-comparison/{fiscal_year_id}")
    async def budget_vs_actual(fiscal_year_id: str):
        """Compare budget vs actual expenses for a fiscal year."""
        fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")

        budget = await db.budgets.find_one({"fiscal_year_id": fiscal_year_id}, {"_id": 0})
        budget_lines = {l["account_number"]: l for l in (budget or {}).get("lines", [])}

        entries = await db.journal_entries.find(
            {"date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}}, {"_id": 0}
        ).to_list(100000)

        actuals = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in actuals:
                    actuals[acc] = {"debit": 0, "credit": 0, "name": line.get("account_name", "")}
                actuals[acc]["debit"] += line.get("debit", 0)
                actuals[acc]["credit"] += line.get("credit", 0)

        comparison = []
        all_accounts = sorted(set(list(budget_lines.keys()) + list(actuals.keys())))
        for acc in all_accounts:
            if not acc.startswith("6"):
                continue
            bl = budget_lines.get(acc, {})
            act = actuals.get(acc, {"debit": 0, "credit": 0, "name": ""})
            budgeted = bl.get("amount", 0)
            actual = round(act["debit"] - act["credit"], 2)
            comparison.append({
                "account_number": acc,
                "account_name": bl.get("account_name", "") or act.get("name", ""),
                "budgeted": round(budgeted, 2),
                "actual": actual,
                "difference": round(budgeted - actual, 2),
            })

        return {
            "fiscal_year": fy,
            "comparison": comparison,
            "total_budgeted": round(sum(c["budgeted"] for c in comparison), 2),
            "total_actual": round(sum(c["actual"] for c in comparison), 2),
        }

    return router
