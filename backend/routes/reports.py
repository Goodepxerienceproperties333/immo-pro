from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
import uuid
import io


def create_reports_router(db):
    router = APIRouter(prefix="/api/reports")

    # ---- GRAND LIVRE (General Ledger) ----
    @router.get("/grand-livre")
    async def grand_livre(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_from: Optional[str] = None,
        account_to: Optional[str] = None,
    ):
        q = {}
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(100000)

        ledger = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if account_from and acc < account_from:
                    continue
                if account_to and acc > account_to:
                    continue
                if acc not in ledger:
                    ledger[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "movements": [], "total_debit": 0, "total_credit": 0}
                ledger[acc]["movements"].append({
                    "date": entry["date"],
                    "journal": entry.get("journal_type", ""),
                    "reference": entry.get("reference", ""),
                    "description": entry.get("description", ""),
                    "debit": line.get("debit", 0),
                    "credit": line.get("credit", 0),
                })
                ledger[acc]["total_debit"] += line.get("debit", 0)
                ledger[acc]["total_credit"] += line.get("credit", 0)

        result = []
        for acc in sorted(ledger.keys()):
            d = ledger[acc]
            d["total_debit"] = round(d["total_debit"], 2)
            d["total_credit"] = round(d["total_credit"], 2)
            d["balance"] = round(d["total_debit"] - d["total_credit"], 2)
            running = 0
            for m in d["movements"]:
                running += m["debit"] - m["credit"]
                m["running_balance"] = round(running, 2)
            result.append(d)

        return result

    # ---- BALANCE DES COMPTES (Trial Balance) ----
    @router.get("/balance")
    async def trial_balance(date_from: Optional[str] = None, date_to: Optional[str] = None):
        q = {}
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "total_debit": 0, "total_credit": 0}
                balances[acc]["total_debit"] += line.get("debit", 0)
                balances[acc]["total_credit"] += line.get("credit", 0)

        result = []
        for acc in sorted(balances.keys()):
            b = balances[acc]
            b["total_debit"] = round(b["total_debit"], 2)
            b["total_credit"] = round(b["total_credit"], 2)
            b["solde_debit"] = round(max(b["total_debit"] - b["total_credit"], 0), 2)
            b["solde_credit"] = round(max(b["total_credit"] - b["total_debit"], 0), 2)
            result.append(b)

        totals = {
            "total_debit": round(sum(b["total_debit"] for b in result), 2),
            "total_credit": round(sum(b["total_credit"] for b in result), 2),
            "solde_debit": round(sum(b["solde_debit"] for b in result), 2),
            "solde_credit": round(sum(b["solde_credit"] for b in result), 2),
        }
        return {"accounts": result, "totals": totals}

    # ---- BILAN (Balance Sheet) ----
    @router.get("/bilan")
    async def bilan(date_to: Optional[str] = None):
        q = {}
        if date_to:
            q["date"] = {"$lte": date_to}

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc[0] not in ("1", "2", "3", "4", "5"):
                    continue
                if acc not in balances:
                    balances[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "debit": 0, "credit": 0}
                balances[acc]["debit"] += line.get("debit", 0)
                balances[acc]["credit"] += line.get("credit", 0)

        actif = []
        passif = []
        for acc in sorted(balances.keys()):
            b = balances[acc]
            solde = round(b["debit"] - b["credit"], 2)
            item = {"account_number": acc, "account_name": b["account_name"], "amount": abs(solde)}
            if acc[0] in ("2", "3", "5") or (acc[0] == "4" and acc.startswith("40")):
                actif.append({**item, "amount": solde if solde > 0 else 0})
            else:
                passif.append({**item, "amount": -solde if solde < 0 else 0})

        actif = [a for a in actif if a["amount"] != 0]
        passif = [p for p in passif if p["amount"] != 0]

        return {
            "actif": actif,
            "passif": passif,
            "total_actif": round(sum(a["amount"] for a in actif), 2),
            "total_passif": round(sum(p["amount"] for p in passif), 2),
            "date": date_to or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

    # ---- COMPTE DE RESULTATS (Income Statement) ----
    @router.get("/resultat")
    async def compte_resultat(date_from: Optional[str] = None, date_to: Optional[str] = None):
        q = {}
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
        charges = {}
        produits = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                amount_d = line.get("debit", 0)
                amount_c = line.get("credit", 0)
                item = {"account_number": acc, "account_name": line.get("account_name", "")}
                if acc.startswith("6"):
                    if acc not in charges:
                        charges[acc] = {**item, "amount": 0}
                    charges[acc]["amount"] += amount_d - amount_c
                elif acc.startswith("7"):
                    if acc not in produits:
                        produits[acc] = {**item, "amount": 0}
                    produits[acc]["amount"] += amount_c - amount_d

        charges_list = [{"account_number": k, "account_name": v["account_name"], "amount": round(v["amount"], 2)} for k, v in sorted(charges.items()) if abs(v["amount"]) > 0.01]
        produits_list = [{"account_number": k, "account_name": v["account_name"], "amount": round(v["amount"], 2)} for k, v in sorted(produits.items()) if abs(v["amount"]) > 0.01]

        total_charges = round(sum(c["amount"] for c in charges_list), 2)
        total_produits = round(sum(p["amount"] for p in produits_list), 2)

        return {
            "charges": charges_list,
            "produits": produits_list,
            "total_charges": total_charges,
            "total_produits": total_produits,
            "resultat": round(total_produits - total_charges, 2),
        }

    # ---- DECOMPTE ANNUEL PAR PROPRIETAIRE (Annual settlement per owner) ----
    @router.get("/decompte")
    async def decompte_annuel(fiscal_year_id: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None):
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy:
                date_from = fy["start_date"]
                date_to = fy["end_date"]

        owners = await db.owners.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        lots = await db.lots.find({}, {"_id": 0}).to_list(1000)
        invoices = await db.invoices.find({"date": {"$gte": date_from or "2000-01-01", "$lte": date_to or "2099-12-31"}}, {"_id": 0}).to_list(10000)

        total_quotity = sum(l.get("quotity", 0) for l in lots)

        decomptes = []
        for owner in owners:
            owner_lots = [l for l in lots if l.get("owner_id") == owner["id"]]
            owner_quotity = sum(l.get("quotity", 0) for l in owner_lots)
            share = owner_quotity / total_quotity if total_quotity > 0 else 0

            charges = []
            total_owner_charges = 0
            for inv in invoices:
                dist_lines = inv.get("distribution_lines", [])
                for dl in dist_lines:
                    if dl.get("lot_id") in [l["id"] for l in owner_lots]:
                        charges.append({
                            "date": inv["date"],
                            "description": f"{inv.get('supplier', '')} - {inv.get('description', '')}",
                            "invoice_number": inv.get("number", ""),
                            "amount": dl.get("amount", 0),
                        })
                        total_owner_charges += dl.get("amount", 0)

                if not dist_lines and share > 0:
                    owner_amount = round(inv.get("total_amount", 0) * share, 2)
                    charges.append({
                        "date": inv["date"],
                        "description": f"{inv.get('supplier', '')} - {inv.get('description', '')}",
                        "invoice_number": inv.get("number", ""),
                        "amount": owner_amount,
                    })
                    total_owner_charges += owner_amount

            decomptes.append({
                "owner_id": owner["id"],
                "owner_name": owner["name"],
                "vcs_code": owner.get("vcs_code", ""),
                "lots": [{"number": l["number"], "quotity": l.get("quotity", 0)} for l in owner_lots],
                "share_pct": round(share * 100, 2),
                "charges": charges,
                "total_charges": round(total_owner_charges, 2),
            })

        return {"decomptes": decomptes, "period": {"from": date_from, "to": date_to}}

    # ---- PDF DECOMPTE ----
    @router.get("/decompte/pdf/{owner_id}")
    async def decompte_pdf(owner_id: str, date_from: Optional[str] = "2024-01-01", date_to: Optional[str] = "2024-12-31"):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table as RLTable, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")

        lots = await db.lots.find({"owner_id": owner_id}, {"_id": 0}).to_list(100)
        invoices = await db.invoices.find({"date": {"$gte": date_from, "$lte": date_to}}, {"_id": 0}).to_list(10000)
        all_lots = await db.lots.find({}, {"_id": 0}).to_list(1000)
        total_quotity = sum(l.get("quotity", 0) for l in all_lots)
        owner_quotity = sum(l.get("quotity", 0) for l in lots)
        share = owner_quotity / total_quotity if total_quotity > 0 else 0

        charges = []
        total_charges = 0
        for inv in invoices:
            dist_lines = inv.get("distribution_lines", [])
            owner_lot_ids = [l["id"] for l in lots]
            matched = False
            for dl in dist_lines:
                if dl.get("lot_id") in owner_lot_ids:
                    charges.append([inv["date"], f"{inv.get('supplier', '')} - {inv.get('description', '')}", inv.get("number", ""), f"{dl.get('amount', 0):.2f} EUR"])
                    total_charges += dl.get("amount", 0)
                    matched = True
            if not matched and share > 0:
                amt = round(inv.get("total_amount", 0) * share, 2)
                charges.append([inv["date"], f"{inv.get('supplier', '')} - {inv.get('description', '')}", inv.get("number", ""), f"{amt:.2f} EUR"])
                total_charges += amt

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm, leftMargin=15*mm, rightMargin=15*mm)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("CustomTitle", parent=styles["Title"], fontSize=16, spaceAfter=6)
        subtitle_style = ParagraphStyle("CustomSubtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
        elements = []

        elements.append(Paragraph("Decompte Annuel de Charges", title_style))
        elements.append(Paragraph(f"Periode: {date_from} au {date_to}", subtitle_style))
        elements.append(Spacer(1, 10*mm))
        elements.append(Paragraph(f"<b>Proprietaire:</b> {owner['name']}", styles["Normal"]))
        if owner.get("vcs_code"):
            elements.append(Paragraph(f"<b>Communication VCS:</b> {owner['vcs_code']}", styles["Normal"]))
        lot_str = ", ".join([f"Lot {l['number']} ({l.get('quotity', 0)} tantiemes)" for l in lots])
        elements.append(Paragraph(f"<b>Lots:</b> {lot_str}", styles["Normal"]))
        elements.append(Paragraph(f"<b>Quote-part:</b> {share*100:.2f}%", styles["Normal"]))
        elements.append(Spacer(1, 8*mm))

        if charges:
            data = [["Date", "Description", "Facture", "Montant"]] + charges
            data.append(["", "", "<b>TOTAL</b>", f"<b>{total_charges:.2f} EUR</b>"])
            t = RLTable(data, colWidths=[25*mm, 90*mm, 25*mm, 30*mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0, 0.33, 1)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.8, 0.8, 0.8)),
                ("BACKGROUND", (0, -1), (-1, -1), colors.Color(0.95, 0.95, 0.95)),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(t)
        else:
            elements.append(Paragraph("Aucune charge pour cette periode.", styles["Normal"]))

        doc.build(elements)
        buf.seek(0)
        filename = f"decompte_{owner['name'].replace(' ', '_')}_{date_from}_{date_to}.pdf"
        return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return router
