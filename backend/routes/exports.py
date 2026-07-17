"""Export Excel + rappels de paiement automatises."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone, timedelta
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


BLUE = "0055FF"
LIGHT_GREY = "F1F5F9"
DARK_GREY = "475569"


def _style_header(ws, row_idx, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=c)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=Side(style="thin", color="CCCCCC"))


def _style_total(ws, row_idx, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=c)
        cell.font = Font(bold=True, size=10, color=DARK_GREY)
        cell.fill = PatternFill("solid", fgColor=LIGHT_GREY)


def _auto_size(ws):
    for col in ws.columns:
        max_len = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)


def create_exports_router(db):
    router = APIRouter(prefix="/api/exports")

    @router.get("/balance-tiers/owners.xlsx")
    async def export_balance_tiers_owners(copropriete_id: Optional[str] = None):
        """Export balance de tiers proprietaires en Excel."""
        # Re-use report logic via direct query
        lots_q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(10000)
        owner_ids = list({l.get("owner_id") for l in lots if l.get("owner_id")})
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).sort("name", 1).to_list(1000) if owner_ids else []

        fc_q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        fund_calls = await db.fund_calls.find(fc_q, {"_id": 0}).to_list(10000)
        txn_q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        all_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(100000)

        vcs_to_owner = {}
        for o in owners:
            if o.get("vcs_digits"):
                vcs_to_owner[o["vcs_digits"]] = o["id"]

        wb = Workbook()
        ws = wb.active
        ws.title = "Balance de tiers"

        # Title row
        ws.cell(row=1, column=1, value="Balance de tiers - Proprietaires").font = Font(bold=True, size=14, color=DARK_GREY)
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0}) if copropriete_id else None
        ws.cell(row=2, column=1, value=f"Copropriete: {copro['name']} ({copro['reference']})" if copro else "Toutes copropr.").font = Font(size=10, color=DARK_GREY)
        ws.cell(row=3, column=1, value=f"Editee le: {datetime.now().strftime('%d/%m/%Y')}").font = Font(size=10, color=DARK_GREY)

        headers = ["Proprietaire", "VCS", "Email", "Telephone", "Total appele", "Total paye", "Solde", "Statut"]
        for i, h in enumerate(headers, 1):
            ws.cell(row=5, column=i, value=h)
        _style_header(ws, 5, len(headers))

        row = 6
        total_called_all = total_paid_all = 0.0
        for o in owners:
            oid = o["id"]
            total_called = 0.0
            for fc in fund_calls:
                for d in fc.get("distribution", []):
                    if d.get("owner_id") == oid:
                        total_called += d.get("amount", 0)
            total_paid = 0.0
            for t in all_txns:
                if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == oid:
                    total_paid += abs(t.get("amount", 0))
                elif not t.get("matched"):
                    comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                    if comm and vcs_to_owner.get(comm) == oid:
                        total_paid += abs(t.get("amount", 0))
            balance = round(total_called - total_paid, 2)
            status = "Debiteur" if balance > 0.01 else ("Crediteur" if balance < -0.01 else "Solde")

            ws.cell(row=row, column=1, value=o["name"])
            ws.cell(row=row, column=2, value=o.get("vcs_code", ""))
            ws.cell(row=row, column=3, value=o.get("email", ""))
            ws.cell(row=row, column=4, value=o.get("phone", ""))
            ws.cell(row=row, column=5, value=round(total_called, 2)).number_format = "#,##0.00 EUR"
            ws.cell(row=row, column=6, value=round(total_paid, 2)).number_format = "#,##0.00 EUR"
            ws.cell(row=row, column=7, value=balance).number_format = "#,##0.00 EUR"
            ws.cell(row=row, column=8, value=status)
            if balance > 0.01:
                ws.cell(row=row, column=7).font = Font(color="DC2626", bold=True)
            elif balance < -0.01:
                ws.cell(row=row, column=7).font = Font(color="16A34A", bold=True)
            total_called_all += total_called
            total_paid_all += total_paid
            row += 1

        # Totals
        ws.cell(row=row, column=1, value="TOTAL")
        ws.cell(row=row, column=5, value=round(total_called_all, 2)).number_format = "#,##0.00 EUR"
        ws.cell(row=row, column=6, value=round(total_paid_all, 2)).number_format = "#,##0.00 EUR"
        ws.cell(row=row, column=7, value=round(total_called_all - total_paid_all, 2)).number_format = "#,##0.00 EUR"
        _style_total(ws, row, len(headers))

        _auto_size(ws)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        filename = f"balance_tiers_owners_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/bilan.xlsx")
    async def export_bilan(copropriete_id: Optional[str] = None, date_to: Optional[str] = None,
                           fiscal_year_id: Optional[str] = None):
        """Export Bilan en Excel avec structure PCMN belge."""
        q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        if date_to:
            q["date"] = {"$lte": date_to}
        if fiscal_year_id:
            await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
        balances = {}
        for e in entries:
            for line in e.get("lines", []):
                acc = line["account_number"]
                if not acc or acc[0] not in "12345":
                    continue
                if acc not in balances:
                    balances[acc] = {"name": line.get("account_name", ""), "debit": 0.0, "credit": 0.0}
                balances[acc]["debit"] += line.get("debit", 0)
                balances[acc]["credit"] += line.get("credit", 0)

        actif, passif = [], []
        for acc, b in sorted(balances.items()):
            solde = round(b["debit"] - b["credit"], 2)
            if abs(solde) < 0.01:
                continue
            if solde > 0:
                actif.append((acc, b["name"], solde))
            else:
                passif.append((acc, b["name"], -solde))

        wb = Workbook()
        ws = wb.active
        ws.title = "Bilan"

        ws.cell(row=1, column=1, value="BILAN PCMN").font = Font(bold=True, size=14, color=DARK_GREY)
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0}) if copropriete_id else None
        ws.cell(row=2, column=1, value=f"Copropriete: {copro['name']}" if copro else "").font = Font(size=10)
        ws.cell(row=3, column=1, value=f"Au {date_to or datetime.now().strftime('%Y-%m-%d')}").font = Font(size=10)

        # ACTIF
        ws.cell(row=5, column=1, value="ACTIF").font = Font(bold=True, size=12, color=BLUE)
        ws.cell(row=6, column=1, value="N°")
        ws.cell(row=6, column=2, value="Libelle")
        ws.cell(row=6, column=3, value="Montant")
        _style_header(ws, 6, 3)
        row = 7
        for acc, name, amt in actif:
            ws.cell(row=row, column=1, value=acc)
            ws.cell(row=row, column=2, value=name)
            ws.cell(row=row, column=3, value=amt).number_format = "#,##0.00 EUR"
            row += 1
        total_actif = round(sum(a[2] for a in actif), 2)
        ws.cell(row=row, column=2, value="TOTAL ACTIF")
        ws.cell(row=row, column=3, value=total_actif).number_format = "#,##0.00 EUR"
        _style_total(ws, row, 3)

        # PASSIF
        row += 3
        ws.cell(row=row, column=1, value="PASSIF").font = Font(bold=True, size=12, color=BLUE)
        row += 1
        ws.cell(row=row, column=1, value="N°")
        ws.cell(row=row, column=2, value="Libelle")
        ws.cell(row=row, column=3, value="Montant")
        _style_header(ws, row, 3)
        row += 1
        for acc, name, amt in passif:
            ws.cell(row=row, column=1, value=acc)
            ws.cell(row=row, column=2, value=name)
            ws.cell(row=row, column=3, value=amt).number_format = "#,##0.00 EUR"
            row += 1
        total_passif = round(sum(p[2] for p in passif), 2)
        ws.cell(row=row, column=2, value="TOTAL PASSIF")
        ws.cell(row=row, column=3, value=total_passif).number_format = "#,##0.00 EUR"
        _style_total(ws, row, 3)

        _auto_size(ws)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="bilan_{datetime.now().strftime("%Y%m%d")}.xlsx"'})

    @router.get("/grand-livre.xlsx")
    async def export_grand_livre(copropriete_id: Optional[str] = None,
                                 date_from: Optional[str] = None, date_to: Optional[str] = None):
        """Export Grand Livre en Excel."""
        q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(100000)

        wb = Workbook()
        ws = wb.active
        ws.title = "Grand Livre"
        ws.cell(row=1, column=1, value="GRAND LIVRE").font = Font(bold=True, size=14, color=DARK_GREY)

        headers = ["Date", "Journal", "Reference", "Compte", "Libelle compte", "Description", "Debit", "Credit"]
        for i, h in enumerate(headers, 1):
            ws.cell(row=3, column=i, value=h)
        _style_header(ws, 3, len(headers))
        row = 4
        total_debit = total_credit = 0.0
        for e in entries:
            for line in e.get("lines", []):
                ws.cell(row=row, column=1, value=e.get("date", ""))
                ws.cell(row=row, column=2, value=e.get("journal_type", ""))
                ws.cell(row=row, column=3, value=e.get("reference", ""))
                ws.cell(row=row, column=4, value=line.get("account_number", ""))
                ws.cell(row=row, column=5, value=line.get("account_name", ""))
                ws.cell(row=row, column=6, value=e.get("description", ""))
                ws.cell(row=row, column=7, value=line.get("debit", 0)).number_format = "#,##0.00"
                ws.cell(row=row, column=8, value=line.get("credit", 0)).number_format = "#,##0.00"
                total_debit += line.get("debit", 0)
                total_credit += line.get("credit", 0)
                row += 1
        ws.cell(row=row, column=6, value="TOTAUX")
        ws.cell(row=row, column=7, value=round(total_debit, 2)).number_format = "#,##0.00"
        ws.cell(row=row, column=8, value=round(total_credit, 2)).number_format = "#,##0.00"
        _style_total(ws, row, len(headers))
        _auto_size(ws)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="grand_livre_{datetime.now().strftime("%Y%m%d")}.xlsx"'})

    return router


def create_reminders_router(db):
    """Rappels automatiques pour appels de fonds en retard."""
    router = APIRouter(prefix="/api/reminders")

    @router.get("/late-payments")
    async def list_late_payments(
        copropriete_id: Optional[str] = None,
        grace_days: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """List unpaid fund call distributions past due_date.
        grace_days: optional grace period in days before flagging as late.
        date_from / date_to: iter90hg - filtre optionnel sur `due_date` (format YYYY-MM-DD).
        Permet de ne considerer que les appels dont l'echeance tombe dans la periode."""
        q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        fund_calls = await db.fund_calls.find(q, {"_id": 0}).to_list(10000)
        today = datetime.now(timezone.utc).date()
        # Parsing des bornes de periode (iter90hg)
        df_date = None
        dt_date = None
        try:
            if date_from:
                df_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        except Exception:
            df_date = None
        try:
            if date_to:
                dt_date = datetime.strptime(date_to, "%Y-%m-%d").date()
        except Exception:
            dt_date = None
        late = []
        for fc in fund_calls:
            due_str = fc.get("due_date") or ""
            if not due_str:
                continue
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
            except Exception:
                continue
            # iter90hg : filtre par periode d'echeance
            if df_date and due_date < df_date:
                continue
            if dt_date and due_date > dt_date:
                continue
            days_late = (today - due_date).days
            if days_late <= grace_days:
                continue
            for d in fc.get("distribution", []):
                if d.get("paid"):
                    continue
                amount = d.get("amount", 0)
                if amount <= 0:
                    continue
                # Determine severity by days late
                if days_late > 90:
                    severity = "critique"
                elif days_late > 30:
                    severity = "urgent"
                elif days_late > 7:
                    severity = "rappel2"
                else:
                    severity = "rappel1"
                late.append({
                    "fund_call_id": fc["id"],
                    "fund_call_name": fc.get("name", ""),
                    "due_date": due_str,
                    "days_late": days_late,
                    "owner_id": d.get("owner_id"),
                    "owner_name": d.get("owner_name", ""),
                    "vcs_code": d.get("vcs_code", ""),
                    "amount": amount,
                    "copropriete_id": fc.get("copropriete_id", ""),
                    "severity": severity,
                })
        # Group by severity
        by_severity = {"critique": 0, "urgent": 0, "rappel2": 0, "rappel1": 0}
        total_amount = 0.0
        for item in late:
            by_severity[item["severity"]] += 1
            total_amount += item["amount"]
        late.sort(key=lambda x: -x["days_late"])
        return {
            "late_payments": late,
            "summary": {
                "total_count": len(late),
                "total_amount": round(total_amount, 2),
                "by_severity": by_severity,
            },
        }

    # iter90hh : rappels fournisseurs (factures impayees echues)
    @router.get("/supplier-late-payments")
    async def list_supplier_late_payments(
        copropriete_id: Optional[str] = None,
        grace_days: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Liste les factures fournisseurs impayees dont la date d'echeance
        est depassee. Sert a suivre ce que l'ACP doit encore payer.
        Filtres :
          - copropriete_id : chinese wall par ACP.
          - grace_days : jours de tolerance apres due_date.
          - date_from / date_to : plage d'echeance (YYYY-MM-DD).
        Les avoirs (is_credit_note=True) sont exclus."""
        q = {"status": {"$in": ["unpaid", "partially_paid"]}}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        invoices = await db.invoices.find(q, {"_id": 0}).to_list(20000)
        today = datetime.now(timezone.utc).date()
        df_date = None
        dt_date = None
        try:
            if date_from:
                df_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        except Exception:
            df_date = None
        try:
            if date_to:
                dt_date = datetime.strptime(date_to, "%Y-%m-%d").date()
        except Exception:
            dt_date = None
        late = []
        for inv in invoices:
            if inv.get("is_credit_note"):
                continue
            due_str = inv.get("due_date") or ""
            if not due_str:
                continue
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if df_date and due_date < df_date:
                continue
            if dt_date and due_date > dt_date:
                continue
            days_late = (today - due_date).days
            if days_late <= grace_days:
                continue
            amount_total = float(inv.get("total_amount", 0) or 0)
            paid = float(inv.get("paid_amount", 0) or 0)
            amount_due = max(0.0, amount_total - paid)
            if amount_due <= 0:
                continue
            if days_late > 90:
                severity = "critique"
            elif days_late > 30:
                severity = "urgent"
            elif days_late > 7:
                severity = "rappel2"
            else:
                severity = "rappel1"
            late.append({
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("number", ""),
                "due_date": due_str,
                "days_late": days_late,
                "supplier_id": inv.get("supplier_id"),
                "supplier_name": inv.get("supplier_name", "") or "Fournisseur inconnu",
                "amount": round(amount_due, 2),
                "amount_total": round(amount_total, 2),
                "amount_paid": round(paid, 2),
                "copropriete_id": inv.get("copropriete_id", ""),
                "severity": severity,
            })
        by_severity = {"critique": 0, "urgent": 0, "rappel2": 0, "rappel1": 0}
        total_amount = 0.0
        for item in late:
            by_severity[item["severity"]] += 1
            total_amount += item["amount"]
        late.sort(key=lambda x: -x["days_late"])
        return {
            "late_payments": late,
            "summary": {
                "total_count": len(late),
                "total_amount": round(total_amount, 2),
                "by_severity": by_severity,
            },
        }

    @router.get("/owner/{owner_id}/letter")
    async def generate_reminder_letter(owner_id: str, copropriete_id: str):
        """Generate a reminder letter PDF for a specific owner & ACP."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Gather unpaid fund call shares
        fund_calls = await db.fund_calls.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        today = datetime.now(timezone.utc).date()

        def _fmt_date(s: str) -> str:
            """ISO YYYY-MM-DD -> DD/MM/YYYY. Renvoie la valeur d'origine si parse echoue."""
            if not s:
                return ""
            try:
                return datetime.strptime(s, "%Y-%m-%d").date().strftime("%d/%m/%Y")
            except Exception:
                return s

        unpaid_items = []
        total_due = 0.0
        for fc in fund_calls:
            for d in fc.get("distribution", []):
                if d.get("owner_id") == owner_id and not d.get("paid"):
                    due_str = fc.get("due_date") or fc.get("date") or ""
                    period_str = fc.get("date") or ""
                    try:
                        due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
                        days = (today - due_date).days
                    except Exception:
                        days = 0
                    unpaid_items.append({
                        "name": fc.get("name", ""),
                        "period": _fmt_date(period_str),
                        "due": _fmt_date(due_str),
                        "amount": d.get("amount", 0),
                        "days_late": days,
                    })
                    total_due += d.get("amount", 0)
        # Tri par date d'appel pour une lecture chronologique
        unpaid_items.sort(key=lambda x: x.get("period", ""))

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm,
                                leftMargin=20 * mm, rightMargin=20 * mm)
        styles = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14)
        h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=14, leading=18, alignment=1,
                            textColor=colors.HexColor("#0055FF"))

        elements = []
        elements.append(Paragraph(f"<b>{copro['name']}</b><br/>{copro.get('address','')}<br/>{copro.get('postal_code','')} {copro.get('city','')}", body))
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph(f"<b>{owner['name']}</b><br/>{owner.get('address','')}<br/>{owner.get('postal_code','')} {owner.get('city','')}", body))
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph(f"Edite le {today.strftime('%d/%m/%Y')}", body))
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph("RAPPEL DE PAIEMENT", h1))
        elements.append(Spacer(1, 8 * mm))

        elements.append(Paragraph(f"Cher(e) {owner.get('first_name') or owner['name']},", body))
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(
            "Sauf erreur de notre part, les appels de fonds suivants restent impayes a ce jour. "
            "Nous vous prions de bien vouloir regulariser dans les plus brefs delais.",
            body
        ))
        elements.append(Spacer(1, 6 * mm))

        if unpaid_items:
            # Style pour cellules avec word-wrap (evite la superposition de texte
            # quand le libelle de l'appel depasse la largeur de la colonne).
            cell_style = ParagraphStyle(
                "tbl_cell", parent=styles["Normal"], fontSize=9, leading=11,
            )
            cell_right = ParagraphStyle(
                "tbl_cell_r", parent=cell_style, alignment=2,  # right
            )
            header_style = ParagraphStyle(
                "tbl_hdr", parent=styles["Normal"], fontSize=9, leading=11,
                fontName="Helvetica-Bold", textColor=colors.white,
            )
            header_right = ParagraphStyle(
                "tbl_hdr_r", parent=header_style, alignment=2,
            )
            rows = [[
                Paragraph("Appel", header_style),
                Paragraph("Periode", header_style),
                Paragraph("Echeance", header_style),
                Paragraph("Jours de retard", header_style),
                Paragraph("Montant", header_right),
            ]]
            for u in unpaid_items:
                rows.append([
                    Paragraph(u["name"], cell_style),
                    Paragraph(u.get("period", ""), cell_style),
                    Paragraph(u["due"], cell_style),
                    Paragraph(f"{u['days_late']} j", cell_style),
                    Paragraph(f"{u['amount']:.2f} EUR", cell_right),
                ])
            rows.append([
                "", "", "",
                Paragraph("<b>TOTAL</b>", cell_style),
                Paragraph(f"<b>{total_due:.2f} EUR</b>", cell_right),
            ])
            t = Table(
                rows,
                colWidths=[55 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
                repeatRows=1,
            )
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0055FF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
            ]))
            elements.append(t)

        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph(
            f"Le versement doit etre effectue sur le compte de la copropriete "
            f"avec la <b>communication structuree obligatoire</b>: "
            f"<font color='#0055FF'><b>{owner.get('vcs_code','-')}</b></font>.",
            body
        ))
        elements.append(Spacer(1, 10 * mm))
        elements.append(Paragraph(
            "Nous restons a votre disposition pour toute question.<br/><br/>Cordialement,<br/>Le syndic.",
            body
        ))

        doc.build(elements)
        buf.seek(0)
        filename = f"rappel_{owner['name'].replace(' ', '_')}_{today.strftime('%Y%m%d')}.pdf"
        return StreamingResponse(buf, media_type="application/pdf",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return router
