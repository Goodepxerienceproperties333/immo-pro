from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import uuid
from auto_entries import generate_sale_entry, _delete_auto_entries


class FundCallInput(BaseModel):
    name: str
    date: str
    due_date: Optional[str] = ""
    fiscal_year_id: Optional[str] = ""
    description: Optional[str] = ""
    total_amount: float
    call_type: Optional[str] = "provisions"  # provisions, reserve, special
    distribution_key_id: Optional[str] = ""
    copropriete_id: Optional[str] = ""


class ReserveFund(BaseModel):
    enabled: bool = False
    amount: float = 0.0
    distribution_key_id: Optional[str] = ""
    label: Optional[str] = "Fonds de reserve"
    # Si frequency/start_date sont fournis, le fonds genere sa PROPRE serie d'appels
    # (call_type='reserve'). Sinon, ajoute au 1er appel des provisions (legacy).
    frequency: Optional[int] = 0  # 0 = legacy injection, 1/2/3/4/6/12 = serie propre
    start_date: Optional[str] = ""
    due_offset_days: Optional[int] = 30


class RoulementFund(BaseModel):
    enabled: bool = False
    amount: float = 0.0
    distribution_key_id: Optional[str] = ""
    label: Optional[str] = "Fonds de roulement"
    mode: Optional[str] = "create"
    # idem ReserveFund
    frequency: Optional[int] = 0
    start_date: Optional[str] = ""
    due_offset_days: Optional[int] = 30


class GenerateFromBudgetInput(BaseModel):
    budget_id: str
    frequency: int  # 1, 2, 3, 4, 12 (yearly, semestrial, quadrimestrial, quarterly, monthly)
    start_date: str  # ISO date of the first call
    due_offset_days: Optional[int] = 30  # due date offset from call date
    reserve_fund: Optional[ReserveFund] = None
    roulement_fund: Optional[RoulementFund] = None
    copropriete_id: Optional[str] = ""


def _add_months(iso_date: str, months: int) -> str:
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day to last day of new month
    try:
        nd = d.replace(year=y, month=m)
    except ValueError:
        # day overflow (e.g. 31 Jan -> 28 Feb)
        if m == 12:
            nd = datetime(y + 1, 1, 1) - timedelta(days=1)
        else:
            nd = datetime(y, m + 1, 1) - timedelta(days=1)
    return nd.strftime("%Y-%m-%d")


def create_fund_calls_router(db):
    router = APIRouter(prefix="/api/fund-calls")

    @router.get("")
    async def list_fund_calls(fiscal_year_id: Optional[str] = None, copropriete_id: Optional[str] = None):
        q = {}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        return calls

    @router.post("")
    async def create_fund_call(data: FundCallInput):
        copro_id = data.copropriete_id or ""
        # Chinese wall: only fetch lots from this ACP
        lots_q = {"copropriete_id": copro_id} if copro_id else {}
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)
        owners = await db.owners.find({}, {"_id": 0}).to_list(1000)  # owners are global
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
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.fund_calls.insert_one(doc)
        clean = {k: v for k, v in doc.items() if k != "_id"}
        try:
            await generate_sale_entry(db, clean)
        except Exception as e:
            print(f"[auto-entry] sale create failed: {e}")
        return clean

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
            "copropriete_id": fc.get("copropriete_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.journal_entries.insert_one(entry)
        return {"message": "Ecritures generees", "entry_id": entry["id"]}

    @router.delete("/{call_id}")
    async def delete_fund_call(call_id: str):
        try:
            await _delete_auto_entries(db, "fund_call", call_id)
        except Exception:
            pass
        result = await db.fund_calls.delete_one({"id": call_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Appel non trouve")
        return {"message": "Appel de fonds supprime"}

    # ---- BULK GENERATION FROM APPROVED BUDGET ----
    @router.post("/preview-from-budget")
    async def preview_from_budget(data: GenerateFromBudgetInput):
        """Preview N fund calls computed from a budget, WITHOUT persisting.
        Used by the frontend wizard to show recap before confirmation."""
        return await _generate_from_budget(data, persist=False)

    @router.post("/generate-from-budget")
    async def generate_from_budget_endpoint(data: GenerateFromBudgetInput):
        """Create N fund calls in DB from an approved budget. Calls are persisted
        with status='pending'. Reserve fund (if enabled) is added to call #1 only."""
        return await _generate_from_budget(data, persist=True)

    async def _generate_from_budget(data: GenerateFromBudgetInput, persist: bool):
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0})
        if not budget:
            raise HTTPException(404, "Budget non trouve")
        if budget.get("status") != "approved":
            raise HTTPException(400, "Budget non approuve - approuvez-le d'abord")
        copro_id = data.copropriete_id or budget.get("copropriete_id", "")
        if not copro_id:
            raise HTTPException(400, "copropriete_id manquant")
        if data.frequency not in (1, 2, 3, 4, 6, 12):
            raise HTTPException(400, "Frequence autorisee: 1, 2, 3, 4, 6, 12 appels par an")

        fy = await db.fiscal_years.find_one({"id": budget["fiscal_year_id"]}, {"_id": 0})
        fy_name = fy.get("name", "") if fy else ""

        # Pre-fetch lots, owners and distribution keys
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10000)
        owners_map = {o["id"]: o for o in await db.owners.find({}, {"_id": 0}).to_list(10000)}
        keys = await db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(1000)
        keys_map = {k["id"]: k for k in keys}

        def _distribute_amount(amount: float, key_id: str) -> list:
            """Distribute amount on owners according to the given distribution key.
            Fallback to quotities if key not found."""
            dist = {}  # owner_id -> {amount, lot_ids, share, owner}
            if key_id and key_id in keys_map:
                key = keys_map[key_id]
                total_shares = sum(l["share"] for l in key.get("lots", []))
                for kl in key.get("lots", []):
                    lot = next((l for l in lots if l["id"] == kl["lot_id"]), None)
                    if not lot or not lot.get("owner_id"):
                        continue
                    owner_id = lot["owner_id"]
                    share_ratio = kl["share"] / total_shares if total_shares > 0 else 0
                    dist.setdefault(owner_id, {"amount": 0.0, "share": 0.0})
                    dist[owner_id]["amount"] += amount * share_ratio
                    dist[owner_id]["share"] += kl["share"]
            else:
                total_quotity = sum(l.get("quotity", 0) for l in lots if l.get("owner_id"))
                for lot in lots:
                    if not lot.get("owner_id"):
                        continue
                    owner_id = lot["owner_id"]
                    share_ratio = lot.get("quotity", 0) / total_quotity if total_quotity > 0 else 0
                    dist.setdefault(owner_id, {"amount": 0.0, "share": 0.0})
                    dist[owner_id]["amount"] += amount * share_ratio
                    dist[owner_id]["share"] += lot.get("quotity", 0)
            return dist

        # Compute schedule
        interval_months = 12 // data.frequency
        n_calls = data.frequency
        budget_lines = budget.get("lines", [])
        budget_total = round(sum(l.get("amount", 0) for l in budget_lines), 2)
        # Per call portion of each budget line
        results = []
        for i in range(n_calls):
            call_date = _add_months(data.start_date, i * interval_months)
            due_date = (datetime.strptime(call_date, "%Y-%m-%d")
                        + timedelta(days=data.due_offset_days or 30)).strftime("%Y-%m-%d")
            # Aggregate distribution by owner combining all budget lines
            owner_agg = {}  # owner_id -> {amount, breakdown_by_line[]}
            call_total = 0.0
            line_details = []
            for bl in budget_lines:
                bl_per_call = round(bl.get("amount", 0) / n_calls, 2)
                if abs(bl_per_call) < 0.01:
                    continue
                line_dist = _distribute_amount(bl_per_call, bl.get("distribution_key_id", ""))
                line_details.append({
                    "account_number": bl.get("account_number", ""),
                    "account_name": bl.get("account_name", ""),
                    "distribution_key_id": bl.get("distribution_key_id", ""),
                    "distribution_key_name": keys_map.get(bl.get("distribution_key_id", ""), {}).get("name", "Tantiemes"),
                    "amount": bl_per_call,
                })
                call_total += bl_per_call
                for oid, d in line_dist.items():
                    owner_agg.setdefault(oid, {"amount": 0.0, "share": 0.0})
                    owner_agg[oid]["amount"] += d["amount"]
                    owner_agg[oid]["share"] += d["share"]

            # Reserve fund injecte sur appel #1 SEULEMENT si fonds reserve sans frequency propre.
            # Si frequency reserve definie, le fonds genere sa propre serie d'appels (plus bas).
            reserve_added = 0.0
            reserve_has_own_schedule = bool(data.reserve_fund and data.reserve_fund.enabled
                                             and data.reserve_fund.amount > 0
                                             and (data.reserve_fund.frequency or 0) > 0)
            if i == 0 and data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.amount > 0 and not reserve_has_own_schedule:
                reserve_amount = float(data.reserve_fund.amount)
                reserve_dist = _distribute_amount(reserve_amount, data.reserve_fund.distribution_key_id or "")
                line_details.append({
                    "account_number": "RESERVE",
                    "account_name": data.reserve_fund.label or "Fonds de reserve",
                    "distribution_key_id": data.reserve_fund.distribution_key_id or "",
                    "distribution_key_name": keys_map.get(data.reserve_fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                    "amount": reserve_amount,
                    "is_reserve": True,
                })
                call_total += reserve_amount
                reserve_added = reserve_amount
                for oid, d in reserve_dist.items():
                    owner_agg.setdefault(oid, {"amount": 0.0, "share": 0.0})
                    owner_agg[oid]["amount"] += d["amount"]
                    owner_agg[oid]["share"] += d["share"]

            # Fonds de roulement injecte sur appel #1 SEULEMENT si pas de frequency propre.
            roulement_added = 0.0
            roul_has_own_schedule = bool(data.roulement_fund and data.roulement_fund.enabled
                                          and data.roulement_fund.amount > 0
                                          and (data.roulement_fund.frequency or 0) > 0)
            if i == 0 and data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.amount > 0 and not roul_has_own_schedule:
                roul_amount = float(data.roulement_fund.amount)
                roul_dist = _distribute_amount(roul_amount, data.roulement_fund.distribution_key_id or "")
                lbl = data.roulement_fund.label or "Fonds de roulement"
                mode_lbl = "(creation)" if (data.roulement_fund.mode or "create") == "create" else "(augmentation)"
                line_details.append({
                    "account_number": "ROULEMENT",
                    "account_name": f"{lbl} {mode_lbl}",
                    "distribution_key_id": data.roulement_fund.distribution_key_id or "",
                    "distribution_key_name": keys_map.get(data.roulement_fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                    "amount": roul_amount,
                    "is_roulement": True,
                    "roulement_mode": data.roulement_fund.mode or "create",
                })
                call_total += roul_amount
                roulement_added = roul_amount
                for oid, d in roul_dist.items():
                    owner_agg.setdefault(oid, {"amount": 0.0, "share": 0.0})
                    owner_agg[oid]["amount"] += d["amount"]
                    owner_agg[oid]["share"] += d["share"]

            distribution = []
            for oid, d in owner_agg.items():
                owner = owners_map.get(oid)
                if not owner:
                    continue
                distribution.append({
                    "owner_id": oid,
                    "owner_name": owner.get("name", ""),
                    "vcs_code": owner.get("vcs_code", ""),
                    "share": round(d["share"], 4),
                    "amount": round(d["amount"], 2),
                    "paid": False,
                    "paid_date": "",
                })
            distribution.sort(key=lambda x: x["owner_name"])

            call_label = ["Annuel", "Semestriel", "Quadrimestriel", "Trimestriel", "Bi-mensuel", "Mensuel"][
                {1: 0, 2: 1, 3: 2, 4: 3, 6: 4, 12: 5}[n_calls]
            ]
            name = f"{call_label} {i + 1}/{n_calls} - {fy_name}".strip(" -")

            results.append({
                "name": name,
                "date": call_date,
                "due_date": due_date,
                "total_amount": round(call_total, 2),
                "reserve_amount": round(reserve_added, 2),
                "roulement_amount": round(roulement_added, 2),
                "roulement_mode": (data.roulement_fund.mode if (data.roulement_fund and data.roulement_fund.enabled) else "") or "",
                "lines": line_details,
                "distribution": distribution,
                "fiscal_year_id": budget["fiscal_year_id"],
                "call_type": "provisions",
                "budget_id": budget["id"],
                "copropriete_id": copro_id,
            })

        # --- Series independantes pour Reserve / Roulement avec frequency propre ---
        def _generate_independent_series(fund, fund_kind: str, account_tag: str):
            """Genere N appels independants pour un fonds (reserve ou roulement).
            fund_kind = 'reserve' ou 'roulement'.
            account_tag = 'RESERVE' ou 'ROULEMENT'.
            """
            if not fund or not fund.enabled or fund.amount <= 0 or (fund.frequency or 0) <= 0:
                return []
            freq = int(fund.frequency)
            if freq not in (1, 2, 3, 4, 6, 12):
                raise HTTPException(400, f"Frequence {fund_kind} invalide : {freq}")
            sdate = fund.start_date or data.start_date
            offset = int(fund.due_offset_days or 30)
            interval = 12 // freq
            per_call = round(fund.amount / freq, 2)
            label_arr = ["Annuel", "Semestriel", "Quadrimestriel", "Trimestriel", "Bi-mensuel", "Mensuel"]
            freq_label = label_arr[{1: 0, 2: 1, 3: 2, 4: 3, 6: 4, 12: 5}[freq]]
            base_label = fund.label or ("Fonds de reserve" if fund_kind == "reserve" else "Fonds de roulement")
            series = []
            for i in range(freq):
                cd = _add_months(sdate, i * interval)
                dd = (datetime.strptime(cd, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
                dist = _distribute_amount(per_call, fund.distribution_key_id or "")
                distribution = []
                for oid, d in dist.items():
                    owner = owners_map.get(oid)
                    if not owner:
                        continue
                    distribution.append({
                        "owner_id": oid,
                        "owner_name": owner.get("name", ""),
                        "vcs_code": owner.get("vcs_code", ""),
                        "share": round(d["share"], 4),
                        "amount": round(d["amount"], 2),
                        "paid": False,
                        "paid_date": "",
                    })
                distribution.sort(key=lambda x: x["owner_name"])
                line_tag = {"account_number": account_tag,
                            "account_name": base_label,
                            "distribution_key_id": fund.distribution_key_id or "",
                            "distribution_key_name": keys_map.get(fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                            "amount": per_call}
                if fund_kind == "reserve":
                    line_tag["is_reserve"] = True
                else:
                    line_tag["is_roulement"] = True
                    line_tag["roulement_mode"] = (fund.mode or "create") if hasattr(fund, 'mode') else "create"
                call_doc = {
                    "name": f"{base_label} - {freq_label} {i + 1}/{freq} - {fy_name}".strip(" -"),
                    "date": cd,
                    "due_date": dd,
                    "total_amount": per_call,
                    "reserve_amount": per_call if fund_kind == "reserve" else 0.0,
                    "roulement_amount": per_call if fund_kind == "roulement" else 0.0,
                    "roulement_mode": (fund.mode or "create") if (fund_kind == "roulement" and hasattr(fund, 'mode')) else "",
                    "lines": [line_tag],
                    "distribution": distribution,
                    "fiscal_year_id": budget["fiscal_year_id"],
                    "call_type": fund_kind,
                    "budget_id": budget["id"],
                    "copropriete_id": copro_id,
                }
                series.append(call_doc)
            return series

        results.extend(_generate_independent_series(data.reserve_fund, "reserve", "RESERVE"))
        results.extend(_generate_independent_series(data.roulement_fund, "roulement", "ROULEMENT"))

        summary = {
            "n_calls": n_calls,
            "interval_months": interval_months,
            "budget_total": budget_total,
            "reserve_total": round(data.reserve_fund.amount if (data.reserve_fund and data.reserve_fund.enabled) else 0.0, 2),
            "roulement_total": round(data.roulement_fund.amount if (data.roulement_fund and data.roulement_fund.enabled) else 0.0, 2),
            "grand_total": round(sum(c["total_amount"] for c in results), 2),
        }

        if not persist:
            return {"calls": results, "summary": summary, "persisted": False}

        # Persist
        created_ids = []
        for c in results:
            doc = {
                "id": str(uuid.uuid4()),
                "name": c["name"],
                "date": c["date"],
                "due_date": c["due_date"],
                "fiscal_year_id": c["fiscal_year_id"],
                "description": f"Appel auto. issu du budget {budget.get('name','')}",
                "total_amount": c["total_amount"],
                "reserve_amount": c["reserve_amount"],
                "roulement_amount": c.get("roulement_amount", 0),
                "roulement_mode": c.get("roulement_mode", ""),
                "call_type": c.get("call_type", "provisions"),
                "lines": c["lines"],
                "distribution": c["distribution"],
                "status": "pending",
                "budget_id": budget["id"],
                "copropriete_id": copro_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.fund_calls.insert_one(doc)
            try:
                await generate_sale_entry(db, doc)
            except Exception as e:
                print(f"[auto-entry] sale bulk failed: {e}")
            created_ids.append(doc["id"])

        return {"calls": results, "summary": summary, "persisted": True, "created_ids": created_ids}

    @router.post("/regenerate-from-budget")
    async def regenerate_from_budget(data: GenerateFromBudgetInput):
        """Delete future unpaid fund calls linked to this budget, then regenerate
        the schedule starting at data.start_date.

        Definition of 'non echu' (deletable): existing call with `date >= start_date`
        AND no `paid` flag set on ANY distribution row. Calls with at least one
        recorded payment are PRESERVED (history protection).
        """
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0})
        if not budget:
            raise HTTPException(404, "Budget non trouve")
        if budget.get("status") != "approved":
            raise HTTPException(400, "Budget non approuve")
        existing = await db.fund_calls.find(
            {"budget_id": data.budget_id, "date": {"$gte": data.start_date}},
            {"_id": 0}
        ).to_list(1000)
        deletable_ids = []
        preserved = 0
        for c in existing:
            has_paid = any(d.get("paid") for d in c.get("distribution", []))
            if has_paid:
                preserved += 1
            else:
                deletable_ids.append(c["id"])
        if deletable_ids:
            await db.fund_calls.delete_many({"id": {"$in": deletable_ids}})
            for did in deletable_ids:
                try:
                    await _delete_auto_entries(db, "fund_call", did)
                except Exception:
                    pass
        result = await _generate_from_budget(data, persist=True)
        result["deleted_count"] = len(deletable_ids)
        result["preserved_count"] = preserved
        return result

    return router
