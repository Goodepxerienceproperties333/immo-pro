"""Export Excel + rappels de paiement automatises."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone, timedelta
import io
import csv

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

    # ---------------------------------------------------------------------
    # iter90fr : Export Journaux CSV & PDF avec selecteur de dates (P1)
    # ---------------------------------------------------------------------

    JOURNAL_LABELS = {
        "OD": "Operations Diverses",
        "AC": "Achats",
        "VE": "Ventes",
        "FI": "Financier",
        "AN": "A-Nouveau",
    }

    async def _build_journal_query(
        request: Request,
        journal_type: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
        copropriete_id: Optional[str],
        include_reversals: bool,
    ) -> dict:
        """Construit la query MongoDB scopee au syndic + ACPs autorisees.

        - Superadmin : pas de restriction syndic. Si copropriete_id est fourni
          on filtre, sinon on prend tout.
        - Syndic / gestionnaire : `syndic_id` obligatoire dans la query,
          `copropriete_id` doit etre dans allowed_copros ou on restreint
          a l'ensemble des ACPs du syndic.
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        is_super = is_superadmin_only(role)
        allowed = user.get("copropriete_ids", []) or []
        sid = getattr(request.state, "syndic_id", None)

        q: dict = {}
        if not is_super:
            if sid:
                q["syndic_id"] = sid
            if copropriete_id:
                if copropriete_id not in allowed:
                    raise HTTPException(403, "Acces refuse a cette ACP (chinese wall)")
                q["copropriete_id"] = copropriete_id
            else:
                if not allowed:
                    q["copropriete_id"] = "__no_scope__"
                else:
                    q["copropriete_id"] = {"$in": allowed}
        else:
            if copropriete_id:
                q["copropriete_id"] = copropriete_id

        if journal_type:
            q["journal_type"] = journal_type
        if date_from:
            q.setdefault("date", {})["$gte"] = date_from
        if date_to:
            q.setdefault("date", {})["$lte"] = date_to
        if not include_reversals:
            q["reversed"] = {"$ne": True}
            q["is_reversal"] = {"$ne": True}
        return q

    async def _resolve_copro_name(copropriete_id: Optional[str]) -> str:
        if not copropriete_id:
            return "Toutes les ACPs autorisees"
        c = await db.coproprietes.find_one(
            {"id": copropriete_id}, {"_id": 0, "name": 1, "reference": 1}
        )
        if not c:
            return copropriete_id
        ref = c.get("reference") or ""
        return f"{c.get('name','')} ({ref})" if ref else c.get("name", copropriete_id)

    @router.get("/journals.csv")
    async def export_journals_csv(
        request: Request,
        journal_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        include_reversals: bool = True,
    ):
        """Export CSV des ecritures de journal, filtrees par periode / type / ACP.

        - Chinese Wall applique via `syndic_id` + `copropriete_id`.
        - Une ligne CSV par LIGNE d'ecriture (pas par ecriture) pour permettre
          l'audit comptable ligne-a-ligne.
        - Encodage UTF-8 avec BOM pour compat Excel FR.
        """
        q = await _build_journal_query(
            request, journal_type, date_from, date_to, copropriete_id, include_reversals
        )
        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(200000)

        buf = io.StringIO()
        # BOM pour Excel FR
        buf.write("\ufeff")
        writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "Date", "Journal", "Reference", "Description",
            "Compte", "Libelle compte", "Tiers", "Debit", "Credit",
            "Contre-passation", "Extournee",
        ])

        total_debit = total_credit = 0.0
        for e in entries:
            jt = e.get("journal_type", "") or ""
            ref = e.get("reference", "") or ""
            desc = e.get("description", "") or ""
            date_e = e.get("date", "") or ""
            is_rev = "OUI" if e.get("is_reversal") else ""
            is_reversed = "OUI" if e.get("reversed") else ""
            for line in e.get("lines", []) or []:
                debit = float(line.get("debit", 0) or 0)
                credit = float(line.get("credit", 0) or 0)
                total_debit += debit
                total_credit += credit
                writer.writerow([
                    date_e, jt, ref, desc,
                    line.get("account_number", "") or "",
                    line.get("account_name", "") or "",
                    line.get("third_party_name", "") or "",
                    f"{debit:.2f}".replace(".", ","),
                    f"{credit:.2f}".replace(".", ","),
                    is_rev, is_reversed,
                ])
        # Ligne de total
        writer.writerow([])
        writer.writerow([
            "TOTAL", "", "", "", "", "", "",
            f"{round(total_debit, 2):.2f}".replace(".", ","),
            f"{round(total_credit, 2):.2f}".replace(".", ","),
            "", "",
        ])

        buf.seek(0)
        content = buf.getvalue().encode("utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        parts = ["journaux"]
        if journal_type:
            parts.append(journal_type.lower())
        if date_from:
            parts.append(f"du_{date_from}")
        if date_to:
            parts.append(f"au_{date_to}")
        parts.append(stamp)
        filename = "_".join(parts) + ".csv"
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/journals.pdf")
    async def export_journals_pdf(
        request: Request,
        journal_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        include_reversals: bool = True,
    ):
        """Export PDF paysage des ecritures de journal.

        - Chinese Wall applique via `syndic_id` + `copropriete_id`.
        - En-tete : ACP, periode, type de journal, date d'edition.
        - Table paginee (reportlab) avec totaux debit/credit en pied.
        """
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        q = await _build_journal_query(
            request, journal_type, date_from, date_to, copropriete_id, include_reversals
        )
        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(200000)
        copro_label = await _resolve_copro_name(copropriete_id)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=landscape(A4),
            topMargin=12 * mm, bottomMargin=12 * mm,
            leftMargin=10 * mm, rightMargin=10 * mm,
            title="Journaux comptables",
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle(
            "h1", parent=styles["Title"], fontSize=13, leading=16,
            textColor=colors.HexColor("#022D52"),
        )
        meta = ParagraphStyle(
            "meta", parent=styles["Normal"], fontSize=9, leading=12,
            textColor=colors.HexColor("#475569"),
        )
        cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=9)
        cell_r = ParagraphStyle("cell_r", parent=cell, alignment=2)
        hdr = ParagraphStyle(
            "hdr", parent=styles["Normal"], fontSize=7, leading=9,
            fontName="Helvetica-Bold", textColor=colors.white,
        )
        hdr_r = ParagraphStyle("hdr_r", parent=hdr, alignment=2)

        elements = []
        j_label = JOURNAL_LABELS.get(journal_type, "Tous les journaux") if journal_type else "Tous les journaux"
        elements.append(Paragraph("Journaux comptables", h1))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(f"<b>ACP :</b> {copro_label}", meta))
        elements.append(Paragraph(f"<b>Journal :</b> {j_label}", meta))
        period = "Toutes periodes"
        if date_from and date_to:
            period = f"du {date_from} au {date_to}"
        elif date_from:
            period = f"a partir du {date_from}"
        elif date_to:
            period = f"jusqu'au {date_to}"
        elements.append(Paragraph(f"<b>Periode :</b> {period}", meta))
        elements.append(Paragraph(
            f"<b>Contre-passations incluses :</b> {'oui' if include_reversals else 'non'}",
            meta,
        ))
        elements.append(Paragraph(
            f"<b>Edite le :</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", meta,
        ))
        elements.append(Spacer(1, 4 * mm))

        rows = [[
            Paragraph("Date", hdr),
            Paragraph("Jnl", hdr),
            Paragraph("Reference", hdr),
            Paragraph("Description", hdr),
            Paragraph("Compte", hdr),
            Paragraph("Libelle compte", hdr),
            Paragraph("Tiers", hdr),
            Paragraph("Debit", hdr_r),
            Paragraph("Credit", hdr_r),
            Paragraph("Flags", hdr),
        ]]
        total_debit = total_credit = 0.0
        for e in entries:
            jt = e.get("journal_type", "") or ""
            ref = e.get("reference", "") or ""
            desc = (e.get("description", "") or "")[:80]
            date_e = e.get("date", "") or ""
            flags = []
            if e.get("is_reversal"):
                flags.append("CP")
            if e.get("reversed"):
                flags.append("X")
            flag_str = " ".join(flags)
            for line in e.get("lines", []) or []:
                debit = float(line.get("debit", 0) or 0)
                credit = float(line.get("credit", 0) or 0)
                total_debit += debit
                total_credit += credit
                rows.append([
                    Paragraph(date_e, cell),
                    Paragraph(jt, cell),
                    Paragraph(ref, cell),
                    Paragraph(desc, cell),
                    Paragraph(line.get("account_number", "") or "", cell),
                    Paragraph((line.get("account_name", "") or "")[:35], cell),
                    Paragraph((line.get("third_party_name", "") or "")[:30], cell),
                    Paragraph(f"{debit:.2f}" if debit else "", cell_r),
                    Paragraph(f"{credit:.2f}" if credit else "", cell_r),
                    Paragraph(flag_str, cell),
                ])
        rows.append([
            "", "", "", "", "", "",
            Paragraph("<b>TOTAUX</b>", cell_r),
            Paragraph(f"<b>{round(total_debit, 2):.2f}</b>", cell_r),
            Paragraph(f"<b>{round(total_credit, 2):.2f}</b>", cell_r),
            "",
        ])

        col_widths = [
            18 * mm, 10 * mm, 26 * mm, 55 * mm, 18 * mm,
            48 * mm, 40 * mm, 22 * mm, 22 * mm, 15 * mm,
        ]
        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#022D52")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
        ]))
        elements.append(t)

        if not entries:
            elements.append(Spacer(1, 6 * mm))
            elements.append(Paragraph(
                "<i>Aucune ecriture ne correspond aux filtres selectionnes.</i>",
                meta,
            ))

        doc.build(elements)
        buf.seek(0)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        parts = ["journaux"]
        if journal_type:
            parts.append(journal_type.lower())
        if date_from:
            parts.append(f"du_{date_from}")
        if date_to:
            parts.append(f"au_{date_to}")
        parts.append(stamp)
        filename = "_".join(parts) + ".pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router


def create_reminders_router(db):
    """Rappels automatiques pour appels de fonds en retard."""
    router = APIRouter(prefix="/api/reminders")

    async def _compute_tier_balances_at_date(copropriete_ids: set, cutoff_iso: str):
        """iter90hn : Retourne {(copro_id, owner_id): balance} calculee A LA DATE
        `cutoff_iso` incluse. Positif = debiteur, negatif = crediteur.
        Inclut les journal_entries (hors extournes) + bank_transactions non
        lettrees matchees par VCS. Exclut par definition les JE post-cutoff
        (appels futurs, non echus)."""
        tier_balances = {}
        for cid in copropriete_ids:
            if not cid:
                continue
            lots = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(10000)
            owner_ids_set = set(lt.get("owner_id") for lt in lots if lt.get("owner_id"))
            tpids_in_je = await db.journal_entries.distinct(
                "lines.third_party_id", {"copropriete_id": cid}
            )
            for tpid in tpids_in_je:
                if tpid:
                    owner_ids_set.add(tpid)
            owners_docs = await db.owners.find(
                {"id": {"$in": list(owner_ids_set)}}, {"_id": 0}
            ).to_list(2000) if owner_ids_set else []

            cumul_by_owner = {}
            cumul_by_acc = {}
            je_q = {
                "copropriete_id": cid,
                "date": {"$lte": cutoff_iso},
                "reversed": {"$ne": True},
                "is_reversal": {"$ne": True},
            }
            async for e in db.journal_entries.find(je_q, {"_id": 0}):
                for ln in e.get("lines", []) or []:
                    d_val = float(ln.get("debit", 0) or 0)
                    c_val = float(ln.get("credit", 0) or 0)
                    tpid = ln.get("third_party_id")
                    acc = ln.get("account_number", "")
                    if tpid:
                        cumul_by_owner[tpid] = cumul_by_owner.get(tpid, 0.0) + (d_val - c_val)
                    else:
                        cumul_by_acc[acc] = cumul_by_acc.get(acc, 0.0) + (d_val - c_val)
            vcs_to_owner = {}
            for owner in owners_docs:
                if owner.get("vcs_digits"):
                    vcs_to_owner[owner["vcs_digits"]] = owner["id"]
                if owner.get("vcs_code"):
                    vcs_to_owner[owner["vcs_code"]] = owner["id"]
            bank_q = {
                "copropriete_id": cid, "matched": {"$ne": True},
                "date": {"$lte": cutoff_iso},
            }
            async for txn in db.bank_transactions.find(bank_q, {"_id": 0}):
                comm = (txn.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                oid = vcs_to_owner.get(comm) or vcs_to_owner.get(txn.get("communication") or "")
                if oid:
                    cumul_by_owner[oid] = cumul_by_owner.get(oid, 0.0) - abs(float(txn.get("amount", 0) or 0))
            for owner in owners_docs:
                oid = owner["id"]
                tier_acc = (owner.get("tier_accounts") or {}).get(cid, {}) or {}
                acc_prov = tier_acc.get("provisions", "")
                acc_res = tier_acc.get("reserve", "")
                balance = cumul_by_owner.get(oid, 0.0)
                for acc in (acc_prov, acc_res):
                    if acc and acc in cumul_by_acc:
                        balance += cumul_by_acc[acc]
                        cumul_by_acc[acc] = 0.0
                tier_balances[(cid, oid)] = balance
        return tier_balances

    @router.get("/late-payments")
    async def list_late_payments(
        copropriete_id: Optional[str] = None,
        grace_days: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Liste UNE ligne par proprietaire debiteur (solde tier > 0.01 EUR).

        iter90ho : refactor majeur - au lieu de retourner une ligne par
        `fund_call.distribution` non-paye, on retourne UNE seule ligne
        agregee par proprietaire, avec `amount` = solde tier reel du
        proprietaire a la date_to. Ce solde correspond EXACTEMENT a la
        colonne 'Solde' de la Balance des Tiers (source de verite comptable).

        Contexte des appels concernes : listes des noms + oldest due_date
        pour calculer la severite (jours de retard depuis l'appel le plus
        ancien de la periode).

        grace_days: jours de tolerance apres due_date (base sur oldest due).
        date_from / date_to: filtre optionnel sur `due_date` (YYYY-MM-DD).
        """
        q = {"copropriete_id": copropriete_id} if copropriete_id else {}
        fund_calls = await db.fund_calls.find(q, {"_id": 0}).to_list(10000)
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

        # iter90hk/hn : precompute owner tier balances via le helper partage.
        needed_copros = {fc.get("copropriete_id", "") for fc in fund_calls if fc.get("copropriete_id")}
        balance_cutoff = dt_date or today
        cutoff_iso = balance_cutoff.strftime("%Y-%m-%d")
        tier_balances = await _compute_tier_balances_at_date(needed_copros, cutoff_iso)

        # iter90ho : agregation par (copro_id, owner_id)
        # - collecter la liste des appels concernes dans la periode
        # - oldest_due_date determine la severite et days_late
        per_owner = {}  # (copro_id, owner_id) -> aggregate dict
        for fc in fund_calls:
            due_str = fc.get("due_date") or ""
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
            # iter90ho : exclure les appels dont l'echeance est encore future
            # (pas de rappel avant l'echeance).
            if due_date > today:
                continue
            fc_copro = fc.get("copropriete_id", "")
            for d in fc.get("distribution", []):
                if d.get("paid"):
                    continue
                amount = d.get("amount", 0)
                if amount <= 0:
                    continue
                owner_id = d.get("owner_id")
                if not owner_id:
                    continue
                key = (fc_copro, owner_id)
                agg = per_owner.setdefault(key, {
                    "copropriete_id": fc_copro,
                    "owner_id": owner_id,
                    "owner_name": d.get("owner_name", ""),
                    "vcs_code": d.get("vcs_code", ""),
                    "oldest_due_date": due_str,
                    "fund_calls_names": set(),
                    "total_called": 0.0,
                })
                # Trace l'echeance la plus ancienne (pour severite)
                if due_str < agg["oldest_due_date"]:
                    agg["oldest_due_date"] = due_str
                agg["fund_calls_names"].add(fc.get("name", ""))
                agg["total_called"] += float(amount)
                # Refresh nom du proprio (au cas ou distributions differentes)
                if not agg["owner_name"] and d.get("owner_name"):
                    agg["owner_name"] = d.get("owner_name")
                if not agg["vcs_code"] and d.get("vcs_code"):
                    agg["vcs_code"] = d.get("vcs_code")

        late = []
        for (fc_copro, owner_id), agg in per_owner.items():
            # iter90hk/ho : filtre sur solde tier reel > 0.01 EUR
            bal = round(tier_balances.get((fc_copro, owner_id), 0.0), 2)
            if bal <= 0.01:
                continue
            # Days late base sur echeance la plus ancienne
            try:
                oldest = datetime.strptime(agg["oldest_due_date"], "%Y-%m-%d").date()
                days_late = (today - oldest).days
            except Exception:
                days_late = 0
            if days_late <= grace_days:
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
                "owner_id": owner_id,
                "owner_name": agg["owner_name"],
                "vcs_code": agg["vcs_code"],
                "copropriete_id": fc_copro,
                "fund_call_names": sorted(agg["fund_calls_names"]),
                "fund_call_name": " + ".join(sorted(agg["fund_calls_names"]))[:80],
                "oldest_due_date": agg["oldest_due_date"],
                "due_date": agg["oldest_due_date"],  # compat frontend
                "days_late": days_late,
                # iter90ho : montant = solde tier reel (= Balance des Tiers)
                "amount": bal,
                "tier_balance": bal,
                "total_called_period": round(agg["total_called"], 2),
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
    async def generate_reminder_letter(
        owner_id: str,
        copropriete_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Generate a reminder letter PDF for a specific owner & ACP.
        iter90hl : accepte date_from/date_to (format YYYY-MM-DD) et ne reprend
        dans la lettre que les appels dont l'echeance tombe dans la periode
        choisie par le syndic (coherent avec la vue UI /reminders).
        """
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

        # iter90hl : parsing des bornes periode
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
            due_str = fc.get("due_date") or fc.get("date") or ""
            # iter90hl : filtrer par periode d'echeance
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date() if due_str else None
            except Exception:
                due_date = None
            if df_date and due_date and due_date < df_date:
                continue
            if dt_date and due_date and due_date > dt_date:
                continue
            # iter90hl : exclure les appels dont l'echeance est encore future
            # (pas de rappel avant l'echeance).
            if due_date and due_date > today:
                continue
            for d in fc.get("distribution", []):
                if d.get("owner_id") == owner_id and not d.get("paid"):
                    period_str = fc.get("date") or ""
                    days = (today - due_date).days if due_date else 0
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

        # iter90hn : coherence avec /late-payments - calculer le solde tier
        # a la date de fin de periode et retirer les items si crediteur.
        balance_cutoff = dt_date or today
        cutoff_iso = balance_cutoff.strftime("%Y-%m-%d")
        tier_balances = await _compute_tier_balances_at_date({copropriete_id}, cutoff_iso)
        owner_balance = tier_balances.get((copropriete_id, owner_id), 0.0)
        is_creditor = owner_balance <= 0.01

        # iter90hn : recuperer aussi les paiements reçus dans la periode pour
        # les afficher dans la lettre (repond a la demande utilisateur
        # "les paiements effectues ne sont pas visibles dans les rappels").
        payments_received = []
        total_paid_period = 0.0
        pay_q = {
            "copropriete_id": copropriete_id,
            "lines.third_party_id": owner_id,
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }
        if df_date or dt_date:
            pay_q["date"] = {}
            if df_date:
                pay_q["date"]["$gte"] = df_date.strftime("%Y-%m-%d")
            if dt_date:
                pay_q["date"]["$lte"] = dt_date.strftime("%Y-%m-%d")
        async for e in db.journal_entries.find(pay_q, {"_id": 0}):
            for ln in e.get("lines", []) or []:
                if ln.get("third_party_id") != owner_id:
                    continue
                c_val = float(ln.get("credit", 0) or 0)
                if c_val <= 0:
                    continue
                # On considere uniquement les credits sur compte tier (paiements)
                acc = ln.get("account_number", "")
                if not (acc.startswith("410") or acc.startswith("41")):
                    continue
                payments_received.append({
                    "date": _fmt_date(e.get("date", "")),
                    "description": (e.get("description") or "Paiement recu")[:80],
                    "amount": c_val,
                })
                total_paid_period += c_val
        payments_received.sort(key=lambda x: x.get("date", ""))

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
        # iter90hn : titre adapte au statut (rappel ou confirmation en regle)
        title_text = "RAPPEL DE PAIEMENT" if not is_creditor else "SITUATION EN REGLE"
        title_color = "#0055FF" if not is_creditor else "#059669"
        h1_dynamic = ParagraphStyle(
            "h1_dyn", parent=styles["Title"], fontSize=14, leading=18, alignment=1,
            textColor=colors.HexColor(title_color),
        )
        elements.append(Paragraph(title_text, h1_dynamic))
        elements.append(Spacer(1, 8 * mm))

        elements.append(Paragraph(f"Cher(e) {owner.get('first_name') or owner['name']},", body))
        elements.append(Spacer(1, 4 * mm))
        # iter90hl/hn : construction du message adaptee au statut du proprio
        if is_creditor:
            # Proprietaire crediteur ou solde nul -> pas de rappel a envoyer
            period_line = ""
            if df_date and dt_date:
                period_line = (
                    f"Nous vous confirmons qu'au terme de la periode du "
                    f"<b>{df_date.strftime('%d/%m/%Y')}</b> au "
                    f"<b>{dt_date.strftime('%d/%m/%Y')}</b>, votre situation de compte "
                    f"est <b>en regle</b>. "
                )
            else:
                period_line = "Nous vous confirmons que votre situation de compte est <b>en regle</b>. "
            if owner_balance < -0.01:
                period_line += (
                    f"Solde en votre faveur : <b><font color='#059669'>"
                    f"{abs(owner_balance):.2f} EUR</font></b>. Ce montant sera deduit "
                    "de votre prochain appel de fonds."
                )
            else:
                period_line += "Aucun montant n'est du a ce jour."
            elements.append(Paragraph(period_line, body))
        else:
            period_line = ""
            if df_date and dt_date:
                period_line = (
                    f"Sauf erreur de notre part, les appels de fonds dont l'echeance se situe "
                    f"entre le <b>{df_date.strftime('%d/%m/%Y')}</b> et le "
                    f"<b>{dt_date.strftime('%d/%m/%Y')}</b> restent partiellement ou "
                    f"totalement impayes a ce jour. "
                )
            elif df_date:
                period_line = (
                    f"Sauf erreur de notre part, les appels de fonds echus depuis le "
                    f"<b>{df_date.strftime('%d/%m/%Y')}</b> restent partiellement ou "
                    f"totalement impayes a ce jour. "
                )
            elif dt_date:
                period_line = (
                    f"Sauf erreur de notre part, les appels de fonds echus jusqu'au "
                    f"<b>{dt_date.strftime('%d/%m/%Y')}</b> restent partiellement ou "
                    f"totalement impayes a ce jour. "
                )
            else:
                period_line = "Sauf erreur de notre part, les appels de fonds suivants restent partiellement ou totalement impayes a ce jour. "
            elements.append(Paragraph(
                period_line + "Nous vous prions de bien vouloir regulariser dans les plus brefs delais.",
                body
            ))
        elements.append(Spacer(1, 6 * mm))

        # iter90hn : Table des paiements recus dans la periode (transparence)
        cell_style = ParagraphStyle(
            "tbl_cell", parent=styles["Normal"], fontSize=9, leading=11,
        )
        cell_right = ParagraphStyle(
            "tbl_cell_r", parent=cell_style, alignment=2,
        )
        header_style = ParagraphStyle(
            "tbl_hdr", parent=styles["Normal"], fontSize=9, leading=11,
            fontName="Helvetica-Bold", textColor=colors.white,
        )
        header_right = ParagraphStyle(
            "tbl_hdr_r", parent=header_style, alignment=2,
        )

        # ---- Tableau appels impayes (uniquement si debiteur) ----
        if unpaid_items and not is_creditor:
            elements.append(Paragraph("<b>Appels de fonds concernes</b>", body))
            elements.append(Spacer(1, 2 * mm))
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
                Paragraph("<b>TOTAL APPELS</b>", cell_style),
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
            elements.append(Spacer(1, 5 * mm))

        # ---- iter90hn : Table paiements recus (transparence) ----
        if payments_received:
            elements.append(Paragraph("<b>Paiements pris en compte</b>", body))
            elements.append(Spacer(1, 2 * mm))
            rows_pay = [[
                Paragraph("Date", header_style),
                Paragraph("Operation", header_style),
                Paragraph("Montant paye", header_right),
            ]]
            for p in payments_received:
                rows_pay.append([
                    Paragraph(p["date"], cell_style),
                    Paragraph(p["description"], cell_style),
                    Paragraph(f"{p['amount']:.2f} EUR", cell_right),
                ])
            rows_pay.append([
                "",
                Paragraph("<b>TOTAL PAIEMENTS</b>", cell_style),
                Paragraph(f"<b>{total_paid_period:.2f} EUR</b>", cell_right),
            ])
            tp = Table(
                rows_pay,
                colWidths=[25 * mm, 105 * mm, 25 * mm],
                repeatRows=1,
            )
            tp.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#059669")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F0FDF4")),
            ]))
            elements.append(tp)
            elements.append(Spacer(1, 5 * mm))

        # ---- iter90hn : Encart solde final (debiteur/crediteur) ----
        if not is_creditor and unpaid_items:
            # Debiteur : montrer le solde restant du (peut differer de total_due
            # si des paiements partiels ont deja reduit la dette).
            balance_line = (
                f"<b>Solde restant du a ce jour : "
                f"<font color='#DC2626'>{owner_balance:.2f} EUR</font></b>"
            )
            elements.append(Paragraph(balance_line, body))
            elements.append(Spacer(1, 3 * mm))

        elements.append(Spacer(1, 8 * mm))
        if not is_creditor:
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
