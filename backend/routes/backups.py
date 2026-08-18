"""iter90ax : Router admin pour la gestion des backups quotidiens des ACP +
export archive complet par le syndic.

Endpoints admin (superadmin only) :
- GET  /api/admin/backups                                : liste des backups
- POST /api/admin/backups/trigger                        : declenche un backup manuel
- GET  /api/admin/backups/{backup_id}/download           : DL le ZIP
- DELETE /api/admin/backups/{backup_id}                  : suppression manuelle
- GET  /api/admin/backups/runs                           : historique des jobs
- POST /api/admin/backups/restore/{backup_id}            : restauration (dry-run)

Endpoint syndic :
- POST /api/coproprietes/{id}/archive-download           : genere l'archive ZIP
  structuree par annee (retourne le fichier en streaming).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backup_service import (
    ACP_SCOPED_COLLECTIONS,
    build_acp_archive_zip,
    create_backup_all_acps,
    dump_acp_to_zip,
)

logger = logging.getLogger("backups")


class RestorePayload(BaseModel):
    dry_run: bool = True
    collections: Optional[list[str]] = None  # None = tout


def create_backups_router(db):
    router = APIRouter(prefix="/api")

    async def _require_superadmin(request: Request):
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        u = await db.users.find_one({"_id": ObjectId(uid)})
        if not u or u.get("role") not in ("superadmin", "admin"):
            raise HTTPException(403, "Reserve au superadmin")
        return u

    async def _require_syndic_scope(request: Request, copropriete_id: str):
        """Verifie que le user courant gere cette ACP (syndic ou son gestionnaire)."""
        uid = getattr(request.state, "user_id", None)
        if not uid:
            raise HTTPException(401, "Non authentifie")
        u = await db.users.find_one({"_id": ObjectId(uid)})
        if not u:
            raise HTTPException(401, "Utilisateur introuvable")
        role = u.get("role")
        if role in ("admin", "superadmin"):
            return u
        # Verifie que copro appartient au scope
        allowed_copros = set(u.get("copropriete_ids") or [])
        if role == "gestionnaire" and u.get("parent_syndic_id"):
            parent = await db.users.find_one({"_id": ObjectId(u["parent_syndic_id"])})
            if parent:
                allowed_copros |= set(parent.get("copropriete_ids") or [])
        if copropriete_id not in allowed_copros:
            raise HTTPException(403, "Cette ACP ne fait pas partie de votre portefeuille")
        return u

    # ============ Endpoints admin ============

    @router.get("/admin/backups")
    async def list_backups(
        request: Request,
        copropriete_id: Optional[str] = None,
        limit: int = Query(default=100, le=1000),
    ):
        await _require_superadmin(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        docs = await db.backups_index.find(q).sort("created_at", -1).limit(limit).to_list(limit)
        for d in docs:
            d["_id"] = str(d.get("_id", ""))
        return {"backups": docs}

    @router.get("/admin/backups/runs")
    async def list_runs(request: Request, limit: int = Query(default=30, le=200)):
        await _require_superadmin(request)
        docs = await db.backups_runs.find().sort("created_at", -1).limit(limit).to_list(limit)
        for d in docs:
            d["_id"] = str(d.get("_id", ""))
        return {"runs": docs}

    @router.post("/admin/backups/trigger")
    async def trigger_backup(request: Request):
        """Declenche un backup manuel de toutes les ACP maintenant."""
        await _require_superadmin(request)
        summary = await create_backup_all_acps(db, source="manual")
        return summary

    @router.post("/admin/backups/trigger/{copropriete_id}")
    async def trigger_backup_one(copropriete_id: str, request: Request):
        """Declenche un backup manuel pour une ACP specifique."""
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        try:
            data, manifest = await dump_acp_to_zip(db, copropriete_id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        filename = f"{copropriete_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_manual.zip"
        file_id = await storage.upload(
            filename=filename, contents=data,
            metadata={"copropriete_id": copropriete_id, "manifest": manifest,
                      "type": "manual", "source": "manual"},
        )
        idx = {
            "backup_id": file_id, "copropriete_id": copropriete_id,
            "copropriete_name": manifest.get("copropriete_name", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size_bytes": len(data), "type": "manual",
            "owners_count": manifest.get("owners_count", 0),
            "collections_counts": manifest.get("collections", {}),
        }
        await db.backups_index.insert_one(idx)
        idx["_id"] = str(idx.get("_id", ""))
        return {"success": True, "backup": idx}

    @router.get("/admin/backups/{backup_id}/download")
    async def download_backup(backup_id: str, request: Request):
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            data = await storage.download(backup_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Erreur GridFS : {e}") from e
        filename = f"backup_{info.get('copropriete_name','acp')}_{info['created_at'][:10]}.zip"
        return StreamingResponse(
            iter([data]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/admin/backups/{backup_id}/download-xlsx")
    async def download_backup_xlsx(backup_id: str, request: Request):
        """iter94d : convertit le backup ZIP (JSONL brut) en Excel multi-onglets.
        Un onglet par collection avec entêtes lisibles, tri par date desc si dispo.
        Facilite la consultation par le syndic sans outil de lecture JSONL.
        """
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        import io as _io
        import json as _json
        import zipfile as _zipfile
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            data = await storage.download(backup_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Erreur GridFS : {e}") from e

        wb = Workbook()
        wb.remove(wb.active)  # supprime la sheet par defaut
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1F4E78")
        center = Alignment(horizontal="center", vertical="center")

        def _flatten(doc: dict) -> dict:
            """Aplati les valeurs listes/dicts en JSON string pour Excel."""
            out = {}
            for k, v in (doc or {}).items():
                if k.startswith("_"):
                    continue
                if isinstance(v, (list, dict)):
                    out[k] = _json.dumps(v, ensure_ascii=False, default=str)[:32000]
                elif v is None:
                    out[k] = ""
                else:
                    out[k] = v
            return out

        def _add_sheet(name: str, docs: list):
            """Ajoute un onglet nomme `name` avec en-tetes + lignes."""
            if not docs:
                return
            # Sheet name limit 31 chars, no special chars
            safe = name.replace("/", "_").replace("\\", "_")[:31]
            ws = wb.create_sheet(safe)
            flat = [_flatten(d) for d in docs]
            # Colonnes = union des cles, ordre stable : cles frequentes d'abord
            keys_freq = {}
            for d in flat:
                for k in d.keys():
                    keys_freq[k] = keys_freq.get(k, 0) + 1
            priority = ["id", "number", "date", "name", "last_name", "first_name",
                        "email", "amount", "description", "label", "reference",
                        "status", "copropriete_id", "created_at"]
            all_keys = list(keys_freq.keys())
            ordered = [k for k in priority if k in keys_freq] + \
                      sorted([k for k in all_keys if k not in priority])
            # Header row
            for i, k in enumerate(ordered, 1):
                cell = ws.cell(row=1, column=i, value=k)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center
            # Data rows
            for r_idx, d in enumerate(flat, 2):
                for c_idx, k in enumerate(ordered, 1):
                    val = d.get(k, "")
                    if isinstance(val, (dict, list)):
                        val = _json.dumps(val, ensure_ascii=False, default=str)[:32000]
                    ws.cell(row=r_idx, column=c_idx, value=val)
            # Auto column width (rough)
            for c_idx, k in enumerate(ordered, 1):
                max_len = max(
                    [len(str(k))] + [len(str(d.get(k, ""))[:60]) for d in flat[:200]]
                )
                ws.column_dimensions[get_column_letter(c_idx)].width = min(max(12, max_len + 2), 50)
            ws.freeze_panes = "A2"

        # Parse ZIP
        try:
            with _zipfile.ZipFile(_io.BytesIO(data)) as z:
                names = z.namelist()
                # Manifest
                try:
                    manifest = _json.loads(z.read("manifest.json").decode("utf-8"))
                    ws = wb.create_sheet("_Manifest", 0)
                    ws["A1"] = "Sauvegarde ACP"
                    ws["A1"].font = Font(bold=True, size=14)
                    row = 3
                    for k, v in manifest.items():
                        ws.cell(row=row, column=1, value=k).font = Font(bold=True)
                        ws.cell(
                            row=row, column=2,
                            value=_json.dumps(v, ensure_ascii=False, default=str)
                            if isinstance(v, (dict, list)) else v,
                        )
                        row += 1
                    ws.column_dimensions["A"].width = 30
                    ws.column_dimensions["B"].width = 60
                except KeyError:
                    pass
                # Copropriete
                try:
                    copro_doc = _json.loads(z.read("copropriete.json").decode("utf-8"))
                    _add_sheet("Copropriete", [copro_doc])
                except KeyError:
                    pass
                # Owners
                try:
                    owners = _json.loads(z.read("owners.json").decode("utf-8"))
                    _add_sheet("Proprietaires", owners)
                except KeyError:
                    pass
                # Collections JSONL
                for nm in names:
                    if not nm.startswith("collections/") or not nm.endswith(".jsonl"):
                        continue
                    coll = nm[len("collections/"):-len(".jsonl")]
                    try:
                        raw = z.read(nm).decode("utf-8")
                    except Exception:
                        continue
                    if not raw.strip():
                        continue
                    docs = []
                    for line in raw.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            docs.append(_json.loads(line))
                        except Exception:
                            continue
                    if docs:
                        # Renommer certaines collections pour l'onglet
                        label = {
                            "lots": "Lots",
                            "invoices": "Factures",
                            "bank_statements": "Extraits bancaires",
                            "bank_transactions": "Transactions",
                            "journal_entries": "Ecritures",
                            "fund_calls": "Appels de fonds",
                            "distribution_keys": "Cles de repartition",
                            "expense_categories": "Categories de depense",
                            "suppliers": "Fournisseurs",
                            "fiscal_years": "Exercices",
                            "mutations": "Mutations",
                        }.get(coll, coll)
                        _add_sheet(label, docs)
        except _zipfile.BadZipFile as e:
            raise HTTPException(500, f"Backup ZIP corrompu : {e}") from e

        # Serialize
        out = _io.BytesIO()
        wb.save(out)
        out.seek(0)
        filename = (
            f"backup_{info.get('copropriete_name','acp')}_"
            f"{info['created_at'][:10]}.xlsx"
        )
        return StreamingResponse(
            iter([out.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/admin/backups/{backup_id}/download-pdf")
    async def download_backup_pdf(backup_id: str, request: Request):
        """iter94e : synthese PDF d'un backup ACP.
        Un PDF paysage avec :
        - Page de garde (nom ACP, date, stats)
        - Onglet par collection principale (Proprietaires, Lots, Factures,
          Ecritures, Appels de fonds, Fournisseurs) avec tableau resume
          (colonnes cles uniquement). Truncate au-dela de 200 lignes.
        """
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        import io as _io
        import json as _json
        import zipfile as _zipfile
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
        )

        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            data = await storage.download(backup_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Erreur GridFS : {e}") from e

        # Parse ZIP
        manifest = {}
        copro_doc = {}
        owners = []
        collections_data: dict[str, list] = {}
        try:
            with _zipfile.ZipFile(_io.BytesIO(data)) as z:
                try:
                    manifest = _json.loads(z.read("manifest.json").decode("utf-8"))
                except KeyError:
                    pass
                try:
                    copro_doc = _json.loads(z.read("copropriete.json").decode("utf-8"))
                except KeyError:
                    pass
                try:
                    owners = _json.loads(z.read("owners.json").decode("utf-8"))
                except KeyError:
                    pass
                for nm in z.namelist():
                    if not nm.startswith("collections/") or not nm.endswith(".jsonl"):
                        continue
                    coll = nm[len("collections/"):-len(".jsonl")]
                    raw = z.read(nm).decode("utf-8")
                    if not raw.strip():
                        continue
                    docs = []
                    for line in raw.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            docs.append(_json.loads(line))
                        except Exception:
                            continue
                    if docs:
                        collections_data[coll] = docs
        except _zipfile.BadZipFile as e:
            raise HTTPException(500, f"Backup ZIP corrompu : {e}") from e

        # Build PDF
        buf = _io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4),
            leftMargin=10 * mm, rightMargin=10 * mm,
            topMargin=10 * mm, bottomMargin=10 * mm,
            title=f"Backup {info.get('copropriete_name', 'ACP')}",
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18,
                            textColor=colors.HexColor("#022D52"))
        h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13,
                            textColor=colors.HexColor("#022D52"))
        small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8)
        story = []

        # Page de garde
        story.append(Paragraph(
            f"Sauvegarde ACP — {info.get('copropriete_name', 'ACP')}", h1
        ))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            f"Backup du {info.get('created_at', '')[:19].replace('T', ' ')} — "
            f"Type {info.get('type', 'daily')} — "
            f"Taille {info.get('size_bytes', 0):,} octets", styles["Normal"]
        ))
        story.append(Spacer(1, 6 * mm))
        # Table meta
        meta_rows = [["Cle", "Valeur"]]
        for k in ("copropriete_id", "reference", "owners_count", "created_at"):
            v = manifest.get(k) or copro_doc.get(k) or info.get(k) or ""
            meta_rows.append([str(k), str(v)[:80]])
        meta_rows.append(["Nb collections", str(len(collections_data))])
        meta_rows.append(["Nb proprietaires", str(len(owners))])
        t = Table(meta_rows, colWidths=[50 * mm, 200 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#022D52")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)
        story.append(PageBreak())

        # Definition des vues par collection : (label, cles a afficher, largeurs mm)
        views = {
            "_owners": ("Proprietaires",
                        ["last_name", "first_name", "email", "phone", "vcs_code"],
                        [50, 40, 70, 40, 40]),
            "lots": ("Lots",
                     ["number", "name", "quotas", "owner_id", "type"],
                     [30, 60, 30, 60, 30]),
            "invoices": ("Factures",
                         ["number", "date", "supplier", "amount",
                          "description", "status"],
                         [30, 25, 60, 25, 90, 20]),
            "journal_entries": ("Ecritures comptables",
                                ["journal_type", "date", "reference",
                                 "description", "amount"],
                                [20, 25, 40, 130, 30]),
            "fund_calls": ("Appels de fonds",
                           ["number", "date", "description", "amount", "type"],
                           [30, 25, 130, 25, 30]),
            "bank_statements": ("Extraits bancaires",
                                ["number", "date", "account_number",
                                 "opening_balance", "closing_balance", "status"],
                                [25, 25, 60, 30, 30, 25]),
            "bank_transactions": ("Transactions bancaires",
                                  ["date", "amount", "counterparty_name",
                                   "communication", "transaction_type"],
                                  [25, 25, 60, 100, 25]),
            "suppliers": ("Fournisseurs",
                          ["name", "vat_number", "email", "iban"],
                          [70, 40, 70, 60]),
            "fiscal_years": ("Exercices fiscaux",
                             ["reference", "date_start", "date_end", "status"],
                             [40, 30, 30, 25]),
            "distribution_keys": ("Cles de repartition",
                                  ["name", "description", "coefficients"],
                                  [50, 100, 100]),
            "expense_categories": ("Categories de depense",
                                   ["code", "name", "account_number"],
                                   [30, 130, 30]),
            "mutations": ("Mutations",
                          ["date", "lot_id", "from_owner_id", "to_owner_id",
                           "roulement_quota"],
                          [25, 60, 60, 60, 30]),
        }

        def _render_table(label: str, docs: list, cols: list, widths: list):
            if not docs:
                return
            story.append(Paragraph(f"{label} ({len(docs)} entree(s))", h2))
            story.append(Spacer(1, 2 * mm))
            trunc = len(docs) > 200
            rows_docs = docs[:200]
            header = cols
            rows = [header]
            for d in rows_docs:
                r = []
                for k in cols:
                    v = d.get(k, "")
                    if isinstance(v, (list, dict)):
                        v = _json.dumps(v, ensure_ascii=False, default=str)
                    v = str(v)
                    # Limiter chaque cellule
                    if len(v) > 90:
                        v = v[:87] + "..."
                    r.append(v)
                rows.append(r)
            tbl = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.15, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#F5F8FB")]),
            ]))
            story.append(tbl)
            if trunc:
                story.append(Spacer(1, 2 * mm))
                story.append(Paragraph(
                    f"<i>... {len(docs) - 200} lignes supplementaires "
                    f"tronquees. Utilisez l'export Excel pour la liste "
                    f"complete.</i>", small,
                ))
            story.append(PageBreak())

        # Proprietaires
        if owners:
            lbl, cols, widths = views["_owners"]
            _render_table(lbl, owners, cols, widths)

        # Autres collections dans l'ordre defini
        for coll, (lbl, cols, widths) in views.items():
            if coll.startswith("_"):
                continue
            docs = collections_data.get(coll) or []
            if docs:
                _render_table(lbl, docs, cols, widths)

        doc.build(story)
        buf.seek(0)
        filename = (
            f"backup_{info.get('copropriete_name', 'acp')}_"
            f"{info['created_at'][:10]}.pdf"
        )
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.delete("/admin/backups/{backup_id}")
    async def delete_backup(backup_id: str, request: Request):
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        try:
            await storage.delete(backup_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Erreur suppression GridFS %s : %s", backup_id, e)
        await db.backups_index.delete_one({"backup_id": backup_id})
        return {"success": True}

    @router.post("/admin/backups/restore/{backup_id}")
    async def restore_backup(backup_id: str, payload: RestorePayload, request: Request):
        """Restaure un backup en base. En dry-run par defaut : retourne juste ce
        qui serait fait. En mode non-dry-run : ecrase les documents avec meme
        id + insere ceux qui manquent (upsert).

        !! Attention !! Utiliser avec precaution. Un backup precedent l'appel
        est fortement recommande.
        """
        await _require_superadmin(request)
        from gridfs_storage import GridFSStorage
        import io, json, zipfile
        storage = GridFSStorage(db, bucket_name="acp_backups")
        info = await db.backups_index.find_one({"backup_id": backup_id})
        if not info:
            raise HTTPException(404, "Backup introuvable")
        data = await storage.download(backup_id)
        summary = {"backup_id": backup_id, "dry_run": payload.dry_run,
                   "collections": {}, "would_touch": {}}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            copropriete_id = manifest["copropriete_id"]

            # 1. Copropriete
            copro_doc = json.loads(z.read("copropriete.json").decode("utf-8"))
            summary["copropriete_id"] = copropriete_id
            if not payload.dry_run:
                await db.coproprietes.update_one(
                    {"id": copropriete_id}, {"$set": copro_doc}, upsert=True,
                )
            summary["collections"]["coproprietes"] = 1

            # 2. Owners
            owners = json.loads(z.read("owners.json").decode("utf-8"))
            summary["collections"]["owners"] = len(owners)
            if not payload.dry_run:
                for o in owners:
                    if o.get("id"):
                        await db.owners.update_one({"id": o["id"]}, {"$set": o}, upsert=True)

            # 3. Collections scopees
            target_colls = payload.collections or ACP_SCOPED_COLLECTIONS
            for coll_name in target_colls:
                if coll_name not in ACP_SCOPED_COLLECTIONS:
                    continue
                path = f"collections/{coll_name}.jsonl"
                try:
                    raw = z.read(path).decode("utf-8")
                except KeyError:
                    continue
                if not raw.strip():
                    summary["collections"][coll_name] = 0
                    continue
                docs = [json.loads(l) for l in raw.strip().split("\n") if l.strip()]
                summary["collections"][coll_name] = len(docs)
                if not payload.dry_run:
                    # Strategie : upsert par 'id' si present, sinon insert
                    for d in docs:
                        if d.get("id"):
                            await db[coll_name].update_one(
                                {"id": d["id"]}, {"$set": d}, upsert=True,
                            )
                        else:
                            await db[coll_name].insert_one(d)
        return {"success": True, **summary}

    # ============ Endpoint syndic : archive telechargement ============

    @router.post("/coproprietes/{copropriete_id}/archive-download")
    async def download_archive(
        copropriete_id: str, request: Request,
        include_pdfs: bool = Query(default=True),
    ):
        """Genere un ZIP structure PAR ANNEE FISCALE. Reserve aux syndics
        gestionnaires de l'ACP + admin.

        Le status de l'ACP n'est pas force a 'archived' - le syndic peut aussi
        DL une archive intermediaire. Un flag `require_archived=True` peut etre
        active plus tard si necessaire.
        """
        await _require_syndic_scope(request, copropriete_id)
        try:
            data, filename = await build_acp_archive_zip(
                db, copropriete_id, include_pdfs=include_pdfs,
            )
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        # Log audit
        try:
            uid = getattr(request.state, "user_id", None)
            await db.acp_archive_downloads.insert_one({
                "copropriete_id": copropriete_id,
                "user_id": uid,
                "size_bytes": len(data),
                "include_pdfs": include_pdfs,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        return StreamingResponse(
            iter([data]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
