from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class FundCallInput(BaseModel):
    name: str
    date: str
    due_date: Optional[str] = ""
    fiscal_year_id: Optional[str] = ""
    description: Optional[str] = ""
    total_amount: float
    call_type: Optional[str] = "provisions"  # provisions, reserve, special
    distribution_key_id: Optional[str] = ""


def create_fund_calls_router(db):
    router = APIRouter(prefix="/api/fund-calls")

    @router.get("")
    async def list_fund_calls(fiscal_year_id: Optional[str] = None):
        q = {}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        return calls

    @router.post("")
    async def create_fund_call(data: FundCallInput):
        lots = await db.lots.find({}, {"_id": 0}).to_list(1000)
        owners = await db.owners.find({}, {"_id": 0}).to_list(1000)
        owners_map = {o["id"]: o for o in owners}

        # Compute distribution
        distribution = []
        if data.distribution_key_id:
            key = await db.distribution_keys.find_one({"id": data.distribution_key_id}, {"_id": 0})
            if key:
                total_shares = sum(l["share"] for l in key.get("lots", []))
                for kl in key.get("lots", []):
                    lot = next((l for l in lots if l["id"] == kl["lot_id"]), None)
                    owner = owners_map.get(lot["owner_id"]) if lot else None
                    share_ratio = kl["share"] / total_shares if total_shares > 0 else 0
                    distribution.append({
                        "lot_id": kl["lot_id"],
                        "lot_number": kl["lot_number"],
                        "owner_id": lot.get("owner_id", "") if lot else "",
                        "owner_name": owner["name"] if owner else "",
                        "vcs_code": owner.get("vcs_code", "") if owner else "",
                        "share": kl["share"],
                        "amount": round(data.total_amount * share_ratio, 2),
                        "paid": False,
                        "paid_date": "",
                    })
        else:
            # Default: distribute by tantiemes
            total_quotity = sum(l.get("quotity", 0) for l in lots)
            for lot in lots:
                if not lot.get("owner_id"):
                    continue
                owner = owners_map.get(lot["owner_id"])
                share_ratio = lot.get("quotity", 0) / total_quotity if total_quotity > 0 else 0
                distribution.append({
                    "lot_id": lot["id"],
                    "lot_number": lot["number"],
                    "owner_id": lot["owner_id"],
                    "owner_name": owner["name"] if owner else "",
                    "vcs_code": owner.get("vcs_code", "") if owner else "",
                    "share": lot.get("quotity", 0),
                    "amount": round(data.total_amount * share_ratio, 2),
                    "paid": False,
                    "paid_date": "",
                })

        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "date": data.date,
            "due_date": data.due_date,
            "fiscal_year_id": data.fiscal_year_id,
            "description": data.description,
            "total_amount": data.total_amount,
            "call_type": data.call_type,
            "distribution_key_id": data.distribution_key_id,
            "distribution": distribution,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.fund_calls.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{call_id}")
    async def get_fund_call(call_id: str):
        fc = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not fc:
            raise HTTPException(404, "Appel de fonds non trouve")
        return fc

    @router.post("/{call_id}/mark-paid")
    async def mark_owner_paid(call_id: str, owner_id: str, paid_date: Optional[str] = None):
        """Mark a specific owner's portion as paid."""
        fc = await db.fund_calls.find_one({"id": call_id})
        if not fc:
            raise HTTPException(404, "Appel non trouve")

        distribution = fc.get("distribution", [])
        updated = False
        for d in distribution:
            if d.get("owner_id") == owner_id:
                d["paid"] = True
                d["paid_date"] = paid_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                updated = True

        if not updated:
            raise HTTPException(404, "Proprietaire non trouve dans cet appel")

        all_paid = all(d.get("paid", False) for d in distribution)
        await db.fund_calls.update_one(
            {"id": call_id},
            {"$set": {"distribution": distribution, "status": "completed" if all_paid else "partial"}}
        )
        return {"message": "Paiement enregistre"}

    @router.post("/{call_id}/generate-entries")
    async def generate_journal_entries(call_id: str):
        """Generate accounting entries for a fund call."""
        fc = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not fc:
            raise HTTPException(404, "Appel non trouve")

        account_map = {
            "provisions": ("400000", "700000"),
            "reserve": ("401000", "701000"),
            "special": ("405000", "710000"),
        }
        debit_acc, credit_acc = account_map.get(fc.get("call_type", "provisions"), ("400000", "700000"))

        lines = [
            {"account_number": debit_acc, "account_name": "Proprietaires - Appels", "debit": fc["total_amount"], "credit": 0},
            {"account_number": credit_acc, "account_name": "Provisions charges", "debit": 0, "credit": fc["total_amount"]},
        ]

        entry = {
            "id": str(uuid.uuid4()),
            "journal_type": "AP",
            "date": fc["date"],
            "reference": f"AP-{fc['name']}",
            "description": f"Appel de fonds: {fc['name']}",
            "lines": lines,
            "total_debit": fc["total_amount"],
            "total_credit": fc["total_amount"],
            "fund_call_id": call_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.journal_entries.insert_one(entry)
        return {"message": "Ecritures generees", "entry_id": entry["id"]}

    @router.delete("/{call_id}")
    async def delete_fund_call(call_id: str):
        result = await db.fund_calls.delete_one({"id": call_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Appel non trouve")
        return {"message": "Appel de fonds supprime"}

    return router
