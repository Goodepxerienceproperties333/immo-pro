"""Owner portal: routes scopees automatiquement au proprietaire connecte.
Le lien user<->owner se fait par email (les owners sont globaux).
Toutes les donnees sont en lecture seule (RBAC enforce write-block sur role=owner).

iter89 : ajout de PUT /me (modif coords par le proprio) et CRUD locataires
self-service depuis le portail, avec notification automatique au syndic.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
import uuid
from datetime import datetime, timezone
from owner_self_notify import notify_syndic_of_owner_change


# Champs qu'un proprio est AUTORISE a modifier via le portail
# (les autres - vcs_code, auxiliary_code, name, tier_accounts - sont locked
# car ils ont un impact comptable et identitaire).
OWNER_SELF_EDITABLE = {
    "first_name", "last_name", "address", "postal_code", "city",
    "country", "email", "email2", "phone", "phone2",
}


async def _resolve_owner(db, request: Request) -> dict:
    """Find the owner record matching the current authenticated user (by email)."""
    email = getattr(request.state, "user_email", "") or ""
    if not email:
        raise HTTPException(401, "Not authenticated")
    owner = await db.owners.find_one({"email": email.lower().strip()}, {"_id": 0})
    if not owner:
        # Fallback: try email2 too
        owner = await db.owners.find_one({"email2": email.lower().strip()}, {"_id": 0})
    if not owner:
        raise HTTPException(404, "Aucune fiche proprietaire ne correspond a votre compte. Contactez le syndic.")
    return owner


def create_owner_portal_router(db):
    router = APIRouter(prefix="/api/owner")

    @router.get("/me")
    async def get_my_owner_profile(request: Request):
        """Owner's own profile (read-only)."""
        return await _resolve_owner(db, request)

    @router.get("/coproprietes")
    async def my_coproprietes(request: Request):
        """ACPs where the owner has at least one lot."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Lots owned (single or multi-owner)
        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if not copro_ids:
            return []
        coproprietes = await db.coproprietes.find(
            {"id": {"$in": copro_ids}}, {"_id": 0}
        ).sort("name", 1).to_list(100)
        # Attach lots to each
        for c in coproprietes:
            c["my_lots"] = [l for l in lots if l.get("copropriete_id") == c["id"]]
            c["my_total_quotity"] = sum(l.get("quotity", 0) for l in c["my_lots"])
        return coproprietes

    @router.get("/dashboard")
    async def my_dashboard(request: Request):
        """Aggregated owner overview across all ACPs."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]

        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})

        # Compute aggregate balance across all ACPs (batch fetch: fix N+1)
        total_called = 0.0
        total_paid = 0.0
        pending_calls = []

        if copro_ids:
            all_fund_calls = await db.fund_calls.find(
                {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
            ).to_list(10000)
            paid_txns = await db.bank_transactions.find(
                {"copropriete_id": {"$in": copro_ids}, "matched": True,
                 "match_type": "owner_payment", "matched_to": owner_id},
                {"_id": 0, "amount": 1},
            ).to_list(50000)
            total_paid += sum(abs(t.get("amount", 0)) for t in paid_txns)

            if owner.get("vcs_digits"):
                unmatched_all = await db.bank_transactions.find(
                    {"copropriete_id": {"$in": copro_ids}, "matched": False},
                    {"_id": 0, "amount": 1, "communication": 1},
                ).to_list(50000)
                for t in unmatched_all:
                    comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                    if comm == owner["vcs_digits"]:
                        total_paid += abs(t.get("amount", 0))

            for fc in all_fund_calls:
                copro_id = fc.get("copropriete_id", "")
                for d in fc.get("distribution", []):
                    if d.get("owner_id") == owner_id:
                        total_called += d.get("amount", 0)
                        if not d.get("paid"):
                            pending_calls.append({
                                "fund_call_name": fc.get("name", ""),
                                "due_date": fc.get("due_date", ""),
                                "amount": d.get("amount", 0),
                                "vcs_code": d.get("vcs_code", owner.get("vcs_code", "")),
                                "copropriete_id": copro_id,
                            })

        balance = round(total_called - total_paid, 2)
        return {
            "owner": owner,
            "stats": {
                "coproprietes_count": len(copro_ids),
                "lots_count": len(lots),
                "total_called": round(total_called, 2),
                "total_paid": round(total_paid, 2),
                "balance": balance,
                "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
                "pending_calls_count": len(pending_calls),
            },
            "pending_calls": pending_calls[:10],
        }

    @router.get("/fund-calls")
    async def my_fund_calls(request: Request, copropriete_id: Optional[str] = None):
        """All fund calls where the owner has a distribution."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        all_calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        # Batch fetch des coproprietes (fix N+1)
        copro_ids_needed = list({fc.get("copropriete_id", "") for fc in all_calls if fc.get("copropriete_id")})
        copro_map: dict = {}
        if copro_ids_needed:
            copros = await db.coproprietes.find(
                {"id": {"$in": copro_ids_needed}},
                {"_id": 0, "id": 1, "name": 1, "reference": 1},
            ).to_list(len(copro_ids_needed))
            copro_map = {c["id"]: c for c in copros}
        result = []
        for fc in all_calls:
            my_share = next((d for d in fc.get("distribution", []) if d.get("owner_id") == owner_id), None)
            if not my_share:
                continue
            copro = copro_map.get(fc.get("copropriete_id", "")) or {}
            result.append({
                "id": fc["id"],
                "name": fc.get("name", ""),
                "date": fc.get("date", ""),
                "due_date": fc.get("due_date", ""),
                "call_type": fc.get("call_type", ""),
                "copropriete_id": fc.get("copropriete_id", ""),
                "copropriete_name": copro.get("name", ""),
                "copropriete_ref": copro.get("reference", ""),
                "my_amount": my_share.get("amount", 0),
                "my_share": my_share.get("share", 0),
                "vcs_code": my_share.get("vcs_code", owner.get("vcs_code", "")),
                "paid": my_share.get("paid", False),
                "paid_date": my_share.get("paid_date", ""),
            })
        return result

    @router.get("/invoices")
    async def my_invoices_charges(request: Request, copropriete_id: Optional[str] = None):
        """Invoices that affect this owner via distribution_lines (his share)."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Get owner's lots
        lots_q = {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]}
        if copropriete_id:
            lots_q["copropriete_id"] = copropriete_id
        my_lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)
        my_lot_ids = {l["id"] for l in my_lots}

        inv_q = {}
        if copropriete_id:
            inv_q["copropriete_id"] = copropriete_id
        else:
            copro_ids = list({l["copropriete_id"] for l in my_lots})
            inv_q["copropriete_id"] = {"$in": copro_ids} if copro_ids else "__none__"

        invoices = await db.invoices.find(inv_q, {"_id": 0}).sort("date", -1).to_list(10000)
        result = []
        for inv in invoices:
            my_amount = 0.0
            for dl in inv.get("distribution_lines", []):
                if dl.get("lot_id") in my_lot_ids:
                    my_amount += dl.get("amount", 0)
            if my_amount <= 0:
                continue
            result.append({
                "id": inv["id"],
                "number": inv.get("number", ""),
                "date": inv.get("date", ""),
                "supplier": inv.get("supplier", ""),
                "description": inv.get("description", ""),
                "total_amount": inv.get("total_amount", 0),
                "my_amount": round(my_amount, 2),
                "copropriete_id": inv.get("copropriete_id", ""),
                "status": inv.get("status", "unpaid"),
            })
        return result

    @router.get("/documents")
    async def my_documents(request: Request, copropriete_id: Optional[str] = None):
        """Documents from ACPs where the owner has lots."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]}, {"_id": 0, "copropriete_id": 1}
        ).to_list(1000)
        copro_ids = list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})
        if copropriete_id:
            if copropriete_id not in copro_ids:
                return []
            copro_ids = [copropriete_id]
        if not copro_ids:
            return []
        docs = await db.documents.find(
            {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
        ).sort("created_at", -1).to_list(1000)
        # Attach category names + ACP names
        cats = await db.document_categories.find(
            {"copropriete_id": {"$in": copro_ids}}, {"_id": 0}
        ).to_list(1000)
        cat_by_id = {c["id"]: c["name"] for c in cats}
        coprops = await db.coproprietes.find(
            {"id": {"$in": copro_ids}}, {"_id": 0, "id": 1, "name": 1}
        ).to_list(100)
        copro_by_id = {c["id"]: c["name"] for c in coprops}
        for d in docs:
            d["category_name"] = cat_by_id.get(d.get("category_id", ""), "")
            d["copropriete_name"] = copro_by_id.get(d.get("copropriete_id", ""), "")
            d.pop("stored_path", None)  # don't expose disk path
        return docs

    @router.get("/situation/{copropriete_id}")
    async def my_situation(copropriete_id: str, request: Request):
        """Detailed account situation (mouvements) for owner within a specific ACP."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Verify owner has lots in this ACP
        my_lots = await db.lots.find(
            {"copropriete_id": copropriete_id, "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(100)
        if not my_lots:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")
        my_lot_ids = {l["id"] for l in my_lots}

        movements = []
        # Fund calls - iter90bv : aggreger par (fc_id, name) pour un proprietaire
        # ayant plusieurs lots. Un meme appel apparait sinon N fois.
        fund_calls = await db.fund_calls.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        for fc in fund_calls:
            owner_amount = 0.0
            for d in fc.get("distribution", []):
                if d.get("owner_id") == owner_id:
                    owner_amount += float(d.get("amount", 0) or 0)
            if owner_amount > 0.001:
                movements.append({
                    "date": fc["date"],
                    "description": f"Appel: {fc['name']}",
                    "debit": round(owner_amount, 2),
                    "credit": 0,
                    "type": "appel",
                })

        # Invoice distributions - iter90bv : cumuler tous les lots du proprietaire
        # sur une meme facture en une seule ligne (sinon N lignes par lot).
        invoices = await db.invoices.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        for inv in invoices:
            inv_amount = 0.0
            for dl in inv.get("distribution_lines", []):
                if dl.get("lot_id") in my_lot_ids:
                    inv_amount += float(dl.get("amount", 0) or 0)
            if inv_amount > 0.001:
                movements.append({
                    "date": inv["date"],
                    "description": f"Charge: {inv.get('supplier', '')} - {inv.get('description', '')}",
                    "debit": round(inv_amount, 2),
                    "credit": 0,
                    "type": "charge",
                })

        # Bank transactions matching this owner
        txns = await db.bank_transactions.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(100000)
        for t in txns:
            is_owner = False
            if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
                is_owner = True
            elif not t.get("matched"):
                comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                if comm and owner.get("vcs_digits") and comm == owner["vcs_digits"]:
                    is_owner = True
            if is_owner:
                movements.append({
                    "date": t["date"],
                    "description": f"Paiement: {t.get('communication','') or t.get('counterparty_name','')}",
                    "debit": 0,
                    "credit": abs(t.get("amount", 0)),
                    "type": "paiement",
                })

        movements.sort(key=lambda m: m["date"])
        running = 0.0
        for m in movements:
            running += m["debit"] - m["credit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)
        balance = round(total_debit - total_credit, 2)
        return {
            "owner": owner,
            "lots": my_lots,
            "movements": movements,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": balance,
            "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
        }

    @router.get("/decompte/pdf")
    async def my_decompte_pdf(request: Request, copropriete_id: str, fiscal_year_id: Optional[str] = None,
                              date_from: Optional[str] = None, date_to: Optional[str] = None):
        """Generate the owner's annual statement PDF (for any of his ACPs)."""
        from datetime import datetime, timezone
        from fastapi.responses import StreamingResponse
        import io
        from pdf_decompte import build_decompte_pdf

        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]

        # Ensure owner has lots in this ACP
        owner_lots = await db.lots.find(
            {"copropriete_id": copropriete_id,
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(100)
        if not owner_lots:
            raise HTTPException(403, "Vous n'avez aucun lot dans cette copropriete")

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Fiscal year
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id, "copropriete_id": copropriete_id}, {"_id": 0})
        if not fy:
            today = datetime.now(timezone.utc)
            fy = {
                "name": f"Exercice {today.year}",
                "start_date": date_from or f"{today.year}-01-01",
                "end_date": date_to or f"{today.year}-12-31",
            }

        # Verrou : decompte annuel uniquement apres cloture de l'exercice
        if fy.get("status", "") != "closed":
            raise HTTPException(
                400,
                f"L'exercice '{fy.get('name','')}' n'est pas cloture. "
                "Le decompte annuel sera disponible apres la cloture par le syndic."
            )

        all_lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(1000)
        invoices = await db.invoices.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)
        dks = await db.distribution_keys.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(1000)
        fcs = await db.fund_calls.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Payments
        all_txns = await db.bank_transactions.find(
            {"copropriete_id": copropriete_id, "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(100000)
        vcs_digits = owner.get("vcs_digits", "")
        payments = []
        for t in all_txns:
            is_owner = False
            if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
                is_owner = True
            elif not t.get("matched") and vcs_digits:
                comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                if comm == vcs_digits:
                    is_owner = True
            if is_owner:
                payments.append(t)

        cats = await db.expense_categories.find(
            {"copropriete_id": copro["id"]} if copro.get("id") else {}, {"_id": 0}
        ).to_list(1000)
        pcmn_acc = await db.pcmn_accounts.find(
            {"class_num": 6, "copropriete_id": copro.get("id", "")}, {"_id": 0}
        ).to_list(1000)
        nature_map = {a["number"]: a.get("name", "") for a in pcmn_acc}
        for c in cats:
            nature_map[c["account_number"]] = c["name"]

        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copro, fiscal_year=fy,
            owner_lots=owner_lots, all_lots=all_lots,
            invoices=invoices, distribution_keys=dks,
            fund_calls=fcs, payments=payments,
            expense_accounts_map=nature_map,
        )

        filename = f"decompte_{owner['name'].replace(' ', '_')}_{fy.get('name','').replace(' ', '_')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/fiscal-years/{copropriete_id}")
    async def my_fiscal_years(copropriete_id: str, request: Request):
        """List fiscal years of an ACP where the owner has lots."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Verify access
        n = await db.lots.count_documents({
            "copropriete_id": copropriete_id,
            "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]
        })
        if n == 0:
            raise HTTPException(403, "Acces refuse")
        years = await db.fiscal_years.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).sort("start_date", -1).to_list(50)
        return years

    # ====================================================================
    # iter89 : SELF-SERVICE - le proprio peut modifier ses coords et gerer
    # ses locataires depuis son espace. Le syndic est averti par email.
    # ====================================================================

    class OwnerSelfUpdate(BaseModel):
        first_name: Optional[str] = None
        last_name: Optional[str] = None
        address: Optional[str] = None
        postal_code: Optional[str] = None
        city: Optional[str] = None
        country: Optional[str] = None
        email: Optional[str] = None
        email2: Optional[str] = None
        phone: Optional[str] = None
        phone2: Optional[str] = None

    async def _owner_copropriete_ids(owner_id: str) -> List[str]:
        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0, "copropriete_id": 1}
        ).to_list(1000)
        return list({l["copropriete_id"] for l in lots if l.get("copropriete_id")})

    @router.put("/me")
    async def update_my_profile(data: OwnerSelfUpdate, request: Request):
        """Owner self-updates his coordinates. Trigger syndic email notification."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        # Build update payload : only whitelisted fields, only non-None values
        update = {}
        diffs = []
        for field, new_val in data.model_dump(exclude_none=True).items():
            if field not in OWNER_SELF_EDITABLE:
                continue
            old_val = owner.get(field, "") or ""
            new_val_str = (new_val or "").strip() if isinstance(new_val, str) else new_val
            if (new_val_str or "") != (old_val or ""):
                update[field] = new_val_str
                diffs.append(f"{field} : '{old_val}' -> '{new_val_str}'")
        if not update:
            return {"updated": False, "message": "Aucune modification detectee", "owner": owner}
        # Recompute `name` if last/first changed
        new_last = update.get("last_name", owner.get("last_name", "")) or ""
        new_first = update.get("first_name", owner.get("first_name", "")) or ""
        if "last_name" in update or "first_name" in update:
            update["name"] = f"{new_last} {new_first}".strip()
        await db.owners.update_one({"id": owner_id}, {"$set": update})
        updated_owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        # Notify syndic
        copro_ids = await _owner_copropriete_ids(owner_id)
        notif_result = await notify_syndic_of_owner_change(
            db, updated_owner,
            change_type="modifier ses coordonnees",
            summary_lines=diffs,
            copropriete_ids=copro_ids,
        )
        return {"updated": True, "owner": updated_owner, "notification": notif_result}

    @router.get("/tenants")
    async def my_tenants(request: Request):
        """Tenants associated to the owner's lots only (scope strict)."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        lots = await db.lots.find(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0, "id": 1, "number": 1, "copropriete_id": 1, "description": 1}
        ).to_list(1000)
        lot_ids = [l["id"] for l in lots]
        if not lot_ids:
            return {"tenants": [], "lots": []}
        tenants = await db.tenants.find(
            {"lot_id": {"$in": lot_ids}}, {"_id": 0}
        ).sort("created_at", -1).to_list(1000)
        return {"tenants": tenants, "lots": lots}

    class TenantInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        lot_id: str
        lease_start: Optional[str] = ""
        lease_end: Optional[str] = ""
        rent_amount: Optional[float] = 0.0

    @router.post("/tenants")
    async def create_my_tenant(data: TenantInput, request: Request):
        """Owner creates a tenant - the tenant MUST be linked to one of his lots."""
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        lot = await db.lots.find_one(
            {"id": data.lot_id,
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        )
        if not lot:
            raise HTTPException(403, "Le lot ne vous appartient pas")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name.strip(),
            "email": (data.email or "").strip(),
            "phone": (data.phone or "").strip(),
            "lot_id": data.lot_id,
            "lease_start": data.lease_start or "",
            "lease_end": data.lease_end or "",
            "rent_amount": float(data.rent_amount or 0),
            "copropriete_id": lot.get("copropriete_id", ""),
            "created_by_owner_id": owner_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.tenants.insert_one(doc)
        # Notify syndic
        copro = await db.coproprietes.find_one(
            {"id": lot.get("copropriete_id", "")}, {"_id": 0, "name": 1}
        )
        await notify_syndic_of_owner_change(
            db, owner,
            change_type="ajouter un locataire",
            summary_lines=[
                f"Locataire : {doc['name']}",
                f"Lot : {lot.get('number','')} {(lot.get('description') or '').strip()}".strip(),
                f"Email : {doc['email'] or '-'}",
                f"GSM : {doc['phone'] or '-'}",
                f"Bail : {doc['lease_start'] or '-'} -> {doc['lease_end'] or '-'}",
                f"Loyer : {doc['rent_amount']:.2f} EUR" if doc['rent_amount'] else "Loyer : non renseigne",
            ],
            copropriete_ids=[lot.get("copropriete_id", "")] if lot.get("copropriete_id") else [],
            copropriete_name=(copro or {}).get("name", ""),
        )
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/tenants/{tenant_id}")
    async def update_my_tenant(tenant_id: str, data: TenantInput, request: Request):
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        # Ownership check via lot
        lot = await db.lots.find_one(
            {"id": existing.get("lot_id", ""),
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        )
        if not lot:
            raise HTTPException(403, "Ce locataire n'est pas dans l'un de vos lots")
        # New lot must also belong to owner (if changed)
        new_lot = lot
        if data.lot_id and data.lot_id != existing.get("lot_id"):
            new_lot = await db.lots.find_one(
                {"id": data.lot_id,
                 "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
                {"_id": 0}
            )
            if not new_lot:
                raise HTTPException(403, "Le nouveau lot ne vous appartient pas")
        diffs = []
        new_email = (data.email or "").strip()
        new_phone = (data.phone or "").strip()
        new_rent = float(data.rent_amount or 0)
        for k, old, new in [
            ("name", existing.get("name", ""), data.name.strip()),
            ("email", existing.get("email", ""), new_email),
            ("phone", existing.get("phone", ""), new_phone),
            ("lease_start", existing.get("lease_start", ""), data.lease_start or ""),
            ("lease_end", existing.get("lease_end", ""), data.lease_end or ""),
            ("rent_amount", existing.get("rent_amount", 0), new_rent),
            ("lot_id", existing.get("lot_id", ""), data.lot_id or existing.get("lot_id", "")),
        ]:
            if old != new:
                diffs.append(f"{k} : '{old}' -> '{new}'")
        update = {
            "name": data.name.strip(), "email": new_email, "phone": new_phone,
            "lot_id": data.lot_id or existing.get("lot_id", ""),
            "lease_start": data.lease_start or "",
            "lease_end": data.lease_end or "",
            "rent_amount": new_rent,
            "copropriete_id": new_lot.get("copropriete_id", existing.get("copropriete_id", "")),
        }
        await db.tenants.update_one({"id": tenant_id}, {"$set": update})
        updated = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if diffs:
            copro = await db.coproprietes.find_one(
                {"id": update["copropriete_id"]}, {"_id": 0, "name": 1}
            )
            await notify_syndic_of_owner_change(
                db, owner,
                change_type=f"modifier le locataire {updated['name']}",
                summary_lines=diffs,
                copropriete_ids=[update["copropriete_id"]] if update.get("copropriete_id") else [],
                copropriete_name=(copro or {}).get("name", ""),
            )
        return updated

    @router.delete("/tenants/{tenant_id}")
    async def delete_my_tenant(tenant_id: str, request: Request):
        owner = await _resolve_owner(db, request)
        owner_id = owner["id"]
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        lot = await db.lots.find_one(
            {"id": existing.get("lot_id", ""),
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0, "number": 1, "copropriete_id": 1}
        )
        if not lot:
            raise HTTPException(403, "Ce locataire n'est pas dans l'un de vos lots")
        await db.tenants.delete_one({"id": tenant_id})
        copro = await db.coproprietes.find_one(
            {"id": lot.get("copropriete_id", "")}, {"_id": 0, "name": 1}
        )
        await notify_syndic_of_owner_change(
            db, owner,
            change_type=f"supprimer le locataire {existing.get('name','')}",
            summary_lines=[
                f"Locataire supprime : {existing.get('name','')}",
                f"Lot : {lot.get('number','')}",
            ],
            copropriete_ids=[lot.get("copropriete_id", "")] if lot.get("copropriete_id") else [],
            copropriete_name=(copro or {}).get("name", ""),
        )
        return {"message": "Locataire supprime"}

    return router
