from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class FiscalYearInput(BaseModel):
    name: str
    start_date: str
    end_date: str
    copropriete_id: Optional[str] = ""


class BudgetLineInput(BaseModel):
    account_number: str
    account_name: Optional[str] = ""
    amount: float
    distribution_key_id: Optional[str] = ""


class BudgetInput(BaseModel):
    fiscal_year_id: str
    name: Optional[str] = ""
    lines: List[BudgetLineInput]
    copropriete_id: Optional[str] = ""


def create_fiscal_router(db):
    router = APIRouter(prefix="/api/fiscal")

    # ---- FISCAL YEARS ----
    @router.get("/years")
    async def list_fiscal_years(copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        years = await db.fiscal_years.find(q, {"_id": 0}).sort("start_date", -1).to_list(100)
        return years

    @router.post("/years")
    async def create_fiscal_year(data: FiscalYearInput):
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "start_date": data.start_date,
            "end_date": data.end_date,
            "status": "open",
            "copropriete_id": data.copropriete_id or "",
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

        # Chinese wall: scope by ACP of the fiscal year
        copro_id = fy.get("copropriete_id", "")
        je_q = {"date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}}
        if copro_id:
            je_q["copropriete_id"] = copro_id
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

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
                "copropriete_id": copro_id,
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
    async def list_budgets(fiscal_year_id: Optional[str] = None, copropriete_id: Optional[str] = None):
        q = {}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
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
            "status": "draft",
            "approved_at": None,
            "approved_by": None,
            "copropriete_id": data.copropriete_id or "",
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
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        if existing.get("status") == "approved":
            raise HTTPException(400, "Budget approuve - modification interdite. Revoquez d'abord l'approbation.")
        total = sum(l.amount for l in data.lines)
        update = {
            "fiscal_year_id": data.fiscal_year_id,
            "name": data.name or "Budget",
            "lines": [l.model_dump() for l in data.lines],
            "total": round(total, 2),
        }
        await db.budgets.update_one({"id": budget_id}, {"$set": update})
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    @router.delete("/budgets/{budget_id}")
    async def delete_budget(budget_id: str):
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        if existing.get("status") == "approved":
            raise HTTPException(400, "Budget approuve - suppression interdite.")
        await db.budgets.delete_one({"id": budget_id})
        return {"message": "Budget supprime"}

    @router.post("/budgets/{budget_id}/approve")
    async def approve_budget(request: Request, budget_id: str):
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        if existing.get("status") == "approved":
            raise HTTPException(400, "Budget deja approuve")
        if not existing.get("lines"):
            raise HTTPException(400, "Budget vide - ajoutez des lignes avant d'approuver")
        user_email = getattr(request.state, "user_email", "")
        await db.budgets.update_one(
            {"id": budget_id},
            {"$set": {
                "status": "approved",
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": user_email,
            }}
        )
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    @router.post("/budgets/{budget_id}/revoke")
    async def revoke_budget(budget_id: str):
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        await db.budgets.update_one(
            {"id": budget_id},
            {"$set": {"status": "draft", "approved_at": None, "approved_by": None}}
        )
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    # ---- N-1 PREVIOUS YEAR EXPENSES (helper for budget preparation) ----
    @router.get("/previous-year-expenses")
    async def previous_year_expenses(fiscal_year_id: Optional[str] = None,
                                     copropriete_id: Optional[str] = None):
        """Aggregate previous year actual expenses grouped by (account, distribution_key).
        Combines invoices.distribution_lines AND journal_entries class 6 to give a
        comprehensive view of what was spent and how it was distributed.
        Returns rows the gestionnaire can directly reuse as budget lines for year N.
        """
        # Resolve previous fiscal year
        target_fy = None
        if fiscal_year_id:
            current_fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if current_fy:
                copropriete_id = copropriete_id or current_fy.get("copropriete_id", "")
                # Find the FY that ends strictly before current.start_date in this ACP
                prev_q = {
                    "end_date": {"$lt": current_fy["start_date"]},
                }
                if copropriete_id:
                    prev_q["copropriete_id"] = copropriete_id
                target_fy = await db.fiscal_years.find_one(
                    prev_q, {"_id": 0}, sort=[("end_date", -1)]
                )
        if not target_fy:
            # Fallback: pick most recent closed FY in this ACP
            q = {"status": "closed"}
            if copropriete_id:
                q["copropriete_id"] = copropriete_id
            target_fy = await db.fiscal_years.find_one(
                q, {"_id": 0}, sort=[("end_date", -1)]
            )
        if not target_fy:
            return {"fiscal_year": None, "lines": [], "total": 0.0}

        start, end = target_fy["start_date"], target_fy["end_date"]
        copro_id = target_fy.get("copropriete_id", "") or copropriete_id or ""

        # 1) Invoices (distribution_lines) - groupe par (account, dist_key)
        inv_q = {"date": {"$gte": start, "$lte": end}}
        if copro_id:
            inv_q["copropriete_id"] = copro_id
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(100000)

        # 2) Journal entries class 6 (manual OD with expense accounts)
        je_q = {"date": {"$gte": start, "$lte": end}}
        if copro_id:
            je_q["copropriete_id"] = copro_id
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Aggregate: keyed by (account_number, distribution_key_id)
        agg = {}  # {(acc, key_id): {account_name, key_name, amount, sources: {invoices, manual}}}

        # Pre-fetch distribution keys for naming
        dk_q = {"copropriete_id": copro_id} if copro_id else {}
        keys = await db.distribution_keys.find(dk_q, {"_id": 0}).to_list(1000)
        key_name_map = {k["id"]: k["name"] for k in keys}

        for inv in invoices:
            acc = inv.get("account_number", "") or ""
            key_id = inv.get("distribution_key_id", "") or ""
            if not acc or not acc.startswith("6"):
                # Try inferring from journal entry tied to invoice? Skip for now.
                continue
            k = (acc, key_id)
            if k not in agg:
                agg[k] = {
                    "account_number": acc,
                    "account_name": "",
                    "distribution_key_id": key_id,
                    "distribution_key_name": key_name_map.get(key_id, ""),
                    "amount_invoices": 0.0,
                    "amount_manual": 0.0,
                    "invoice_count": 0,
                }
            agg[k]["amount_invoices"] += inv.get("total_amount", 0)
            agg[k]["invoice_count"] += 1

        # Add manual journal entries (class 6 only, exclude those tied to invoices)
        invoice_je_ids = set()  # We don't store inv->je link consistently; just include all class-6 OD
        for e in entries:
            # Only count OD/AC entries (manual or invoice posting). For simplicity, count all,
            # but only the amount that goes to class-6 accounts.
            for line in e.get("lines", []):
                acc = line.get("account_number", "")
                if not acc.startswith("6"):
                    continue
                net = line.get("debit", 0) - line.get("credit", 0)
                if abs(net) < 0.01:
                    continue
                # If this entry has a `fund_call_id` skip (those are appels, not depenses)
                if e.get("fund_call_id"):
                    continue
                k = (acc, "")
                if k not in agg:
                    agg[k] = {
                        "account_number": acc,
                        "account_name": line.get("account_name", ""),
                        "distribution_key_id": "",
                        "distribution_key_name": "",
                        "amount_invoices": 0.0,
                        "amount_manual": 0.0,
                        "invoice_count": 0,
                    }
                if not agg[k]["account_name"]:
                    agg[k]["account_name"] = line.get("account_name", "")
                agg[k]["amount_manual"] += net

        # Enrich missing account_name from PCMN
        accs_missing = [v["account_number"] for v in agg.values() if not v["account_name"]]
        if accs_missing:
            pcmn_q = {"number": {"$in": accs_missing}}
            if copro_id:
                pcmn_q["copropriete_id"] = copro_id
            pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(1000)
            pcmn_map = {p["number"]: p["name"] for p in pcmns}
            for v in agg.values():
                if not v["account_name"]:
                    v["account_name"] = pcmn_map.get(v["account_number"], "")

        lines = []
        for v in agg.values():
            total = round(v["amount_invoices"] + v["amount_manual"], 2)
            if abs(total) < 0.01:
                continue
            v["amount_invoices"] = round(v["amount_invoices"], 2)
            v["amount_manual"] = round(v["amount_manual"], 2)
            v["amount_total"] = total
            lines.append(v)
        lines.sort(key=lambda x: (x["account_number"], x.get("distribution_key_name", "")))
        return {
            "fiscal_year": target_fy,
            "lines": lines,
            "total": round(sum(l["amount_total"] for l in lines), 2),
        }

    # ---- BUDGET VS ACTUAL ----
    @router.get("/budget-comparison/{fiscal_year_id}")
    async def budget_vs_actual(fiscal_year_id: str):
        """Compare budget vs actual expenses for a fiscal year."""
        fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")

        budget = await db.budgets.find_one({"fiscal_year_id": fiscal_year_id}, {"_id": 0})
        budget_lines = {l["account_number"]: l for l in (budget or {}).get("lines", [])}

        # Chinese wall: scope entries by fiscal year's ACP
        copro_id = fy.get("copropriete_id", "")
        je_q = {"date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}}
        if copro_id:
            je_q["copropriete_id"] = copro_id
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

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
