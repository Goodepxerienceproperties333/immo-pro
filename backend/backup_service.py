"""iter90ax : Service de backup automatise par ACP.

Objectifs :
1. Dump quotidien (00h00 Europe/Brussels) de chaque copropriete active :
   - Metadata ACP + lots + owners
   - Ecritures comptables (journal_entries)
   - Factures + fund_calls + bank_transactions
   - Documents references (juste la meta, pas les bytes) pour ne pas exploser la
     taille du ZIP
   - Manifest JSON avec horodatage + versions
2. Stockage en GridFS bucket `acp_backups` avec metadata pour retention.
3. Retention : garder 30 backups quotidiens + 12 mensuels (premier de chaque mois).
4. Endpoints admin : list / download / trigger / delete.
5. Trigger CI/DR : le superadmin peut declencher a la demande + restore.

Le job est planifie via APScheduler (BackgroundScheduler dans le process backend).
"""
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("backup_service")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_doc(doc: dict) -> dict:
    """Retire les cles non-JSON (ObjectId, bytes)."""
    if not isinstance(doc, dict):
        return doc
    clean = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, bytes):
            continue
        if isinstance(v, dict):
            clean[k] = _serialize_doc(v)
        elif isinstance(v, list):
            clean[k] = [_serialize_doc(x) if isinstance(x, dict) else x for x in v]
        else:
            clean[k] = v
    return clean


# ---- Collections associees a une ACP (via champ `copropriete_id`) ----
ACP_SCOPED_COLLECTIONS = [
    "lots",
    "journal_entries",
    "invoices",
    "fund_calls",
    "bank_transactions",
    "distribution_keys",
    "expense_categories",
    "fiscal_years",
    "pcmn_accounts",
    "reminders",
    "suppliers",
    "documents",
    "invoice_templates",
]


async def dump_acp_to_zip(db, copropriete_id: str) -> tuple[bytes, dict]:
    """Genere un ZIP complet pour une ACP. Retourne (bytes, manifest_dict).

    Structure interne :
        manifest.json
        copropriete.json
        owners.json (uniquement ceux lies via lots/journal_entries)
        collections/lots.jsonl
        collections/journal_entries.jsonl
        collections/invoices.jsonl
        ...
    """
    buf = io.BytesIO()
    manifest = {
        "copropriete_id": copropriete_id,
        "created_at": _iso_now(),
        "version": "1.0",
        "collections": {},
    }

    copro = await db.coproprietes.find_one({"id": copropriete_id})
    if not copro:
        raise ValueError(f"ACP introuvable : {copropriete_id}")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        # 1. Copropriete metadata
        copro_clean = _serialize_doc(copro)
        z.writestr("copropriete.json", json.dumps(copro_clean, ensure_ascii=False, indent=2))
        manifest["copropriete_name"] = copro.get("name", "")
        manifest["copropriete_reference"] = copro.get("reference", "")

        # 2. Owners lies (via lots)
        lot_docs = await db.lots.find({"copropriete_id": copropriete_id}).to_list(50000)
        owner_ids = set()
        for lt in lot_docs:
            if lt.get("owner_id"):
                owner_ids.add(lt["owner_id"])
        # + owners via journal third_party_id
        tps = await db.journal_entries.distinct("lines.third_party_id",
                                                {"copropriete_id": copropriete_id})
        for tpid in tps:
            if tpid:
                owner_ids.add(tpid)
        owners = []
        if owner_ids:
            owners = await db.owners.find({"id": {"$in": list(owner_ids)}}).to_list(10000)
        z.writestr(
            "owners.json",
            json.dumps([_serialize_doc(o) for o in owners], ensure_ascii=False, indent=2),
        )
        manifest["owners_count"] = len(owners)

        # 3. Collections scopees (JSONL une ligne par doc pour scaler)
        for coll_name in ACP_SCOPED_COLLECTIONS:
            docs = await db[coll_name].find({"copropriete_id": copropriete_id}).to_list(100000)
            lines = [json.dumps(_serialize_doc(d), ensure_ascii=False) for d in docs]
            content = "\n".join(lines)
            z.writestr(f"collections/{coll_name}.jsonl", content)
            manifest["collections"][coll_name] = len(docs)

        # 4. Manifest ecrit en dernier
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    data = buf.getvalue()
    buf.close()
    manifest["size_bytes"] = len(data)
    return data, manifest


async def create_backup_all_acps(db, source: str = "scheduler") -> dict:
    """Cree un backup pour chaque ACP active. Utilise par le job planifie
    OU manuellement (source='manual').

    Retourne un dict resume : {total, success, failed, backups}
    """
    from gridfs_storage import GridFSStorage

    storage = GridFSStorage(db, bucket_name="acp_backups")

    # Include les ACP archivees aussi (elles peuvent avoir des donnees critiques)
    coprs = await db.coproprietes.find({}, {"_id": 0, "id": 1, "name": 1, "status": 1}).to_list(10000)
    summary = {
        "created_at": _iso_now(),
        "source": source,
        "total": len(coprs),
        "success": 0,
        "failed": 0,
        "errors": [],
        "backups": [],
    }
    for copro in coprs:
        try:
            data, manifest = await dump_acp_to_zip(db, copro["id"])
            filename = f"{copro['id']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip"
            file_id = await storage.upload(
                filename=filename,
                contents=data,
                metadata={
                    "copropriete_id": copro["id"],
                    "copropriete_name": copro.get("name", ""),
                    "created_at": _iso_now(),
                    "size_bytes": len(data),
                    "manifest": manifest,
                    "source": source,
                    "type": "daily" if source == "scheduler" else source,
                },
            )
            await db.backups_index.insert_one({
                "backup_id": file_id,
                "copropriete_id": copro["id"],
                "copropriete_name": copro.get("name", ""),
                "created_at": _iso_now(),
                "size_bytes": len(data),
                "type": "daily" if source == "scheduler" else source,
                "owners_count": manifest.get("owners_count", 0),
                "collections_counts": manifest.get("collections", {}),
            })
            summary["success"] += 1
            summary["backups"].append({
                "copropriete_id": copro["id"],
                "backup_id": file_id,
                "size_bytes": len(data),
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("Backup failed for ACP %s : %s", copro.get("id"), e)
            summary["failed"] += 1
            summary["errors"].append({"copropriete_id": copro.get("id"), "error": str(e)[:200]})

    # Retention policy
    await _apply_retention(db, storage)

    # Log dans backups_runs pour audit
    await db.backups_runs.insert_one({**summary, "created_at": _iso_now()})
    logger.info("Backup run terminee : %d success / %d fail", summary["success"], summary["failed"])
    return summary


async def _apply_retention(db, storage) -> None:
    """Retention : garde les 30 derniers backups quotidiens par ACP + 1 backup
    mensuel (premier du mois) sur 12 mois."""
    # Par ACP
    coprs_ids = await db.backups_index.distinct("copropriete_id")
    for cid in coprs_ids:
        docs = await db.backups_index.find(
            {"copropriete_id": cid}
        ).sort("created_at", -1).to_list(10000)
        keep_ids = set()
        # Garde 30 derniers de type daily
        daily_kept = 0
        monthly_kept_months = set()
        cutoff_monthly = datetime.now(timezone.utc) - timedelta(days=365)
        for d in docs:
            created_dt = None
            try:
                created_dt = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
            except Exception:
                continue
            if d.get("type") == "manual":
                keep_ids.add(d["backup_id"])
                continue
            if daily_kept < 30:
                keep_ids.add(d["backup_id"])
                daily_kept += 1
            # Monthly (premier de chaque mois sur 12 mois)
            if created_dt >= cutoff_monthly:
                month_key = created_dt.strftime("%Y-%m")
                if month_key not in monthly_kept_months:
                    keep_ids.add(d["backup_id"])
                    monthly_kept_months.add(month_key)
        # Supprime les autres
        to_delete = [d for d in docs if d["backup_id"] not in keep_ids]
        for d in to_delete:
            try:
                await storage.delete(d["backup_id"])
                await db.backups_index.delete_one({"backup_id": d["backup_id"]})
            except Exception as e:  # noqa: BLE001
                logger.warning("Impossible de supprimer backup %s : %s", d["backup_id"], e)


# ---- Archive syndic (Phase B) ----

async def build_acp_archive_zip(db, copropriete_id: str, include_pdfs: bool = True) -> tuple[bytes, str]:
    """Genere un ZIP structure PAR ANNEE FISCALE pour archivage syndic.

    Structure :
        {ACP_NAME}/
            metadata.json
            owners.csv
            lots.csv
            YYYY/
                journal_entries.csv
                invoices.csv
                fund_calls.csv
                bank_transactions.csv
                bilan.pdf (optionnel)
                decomptes/{owner_name}.pdf (optionnel)

    Retourne (bytes, filename).
    """
    import csv

    copro = await db.coproprietes.find_one({"id": copropriete_id})
    if not copro:
        raise ValueError("ACP introuvable")
    acp_name = (copro.get("name") or "acp").replace("/", "_").replace(" ", "_")

    lots = await db.lots.find({"copropriete_id": copropriete_id}).to_list(10000)
    fiscal_years = await db.fiscal_years.find({"copropriete_id": copropriete_id}).sort("start_date", 1).to_list(1000)
    owner_ids = list({lt.get("owner_id") for lt in lots if lt.get("owner_id")})
    owners = await db.owners.find({"id": {"$in": owner_ids}}).to_list(5000) if owner_ids else []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        # 1. Metadata global
        meta = {
            "copropriete_id": copropriete_id,
            "name": copro.get("name"),
            "reference": copro.get("reference"),
            "status": copro.get("status"),
            "created_at_export": _iso_now(),
            "syndic_name": copro.get("syndic_name"),
            "fiscal_years_count": len(fiscal_years),
            "owners_count": len(owners),
            "lots_count": len(lots),
        }
        z.writestr(f"{acp_name}/metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))

        # 2. Owners CSV
        owners_io = io.StringIO()
        w = csv.writer(owners_io, delimiter=";")
        w.writerow(["id", "name", "email", "phone", "address", "postal_code", "city", "vcs_code"])
        for o in owners:
            w.writerow([o.get("id"), o.get("name"), o.get("email"), o.get("phone"),
                        o.get("address"), o.get("postal_code"), o.get("city"), o.get("vcs_code")])
        z.writestr(f"{acp_name}/owners.csv", owners_io.getvalue())

        # 3. Lots CSV
        lots_io = io.StringIO()
        w = csv.writer(lots_io, delimiter=";")
        w.writerow(["id", "number", "description", "quotity", "owner_id"])
        for lt in lots:
            w.writerow([lt.get("id"), lt.get("number"), lt.get("description"),
                        lt.get("quotity"), lt.get("owner_id")])
        z.writestr(f"{acp_name}/lots.csv", lots_io.getvalue())

        # 4. Par exercice fiscal
        for fy in fiscal_years:
            year_folder = fy.get("name") or fy.get("start_date", "unknown")[:4]
            year_folder = year_folder.replace("/", "_").replace(" ", "_")

            start = fy.get("start_date", "")
            end = fy.get("end_date", "")

            # 4a. Journal entries
            entries = await db.journal_entries.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).sort("date", 1).to_list(200000)
            je_io = io.StringIO()
            wj = csv.writer(je_io, delimiter=";")
            wj.writerow(["date", "journal_type", "reference", "description",
                         "account_number", "account_name", "third_party_id",
                         "line_description", "debit", "credit"])
            for e in entries:
                for ln in e.get("lines", []) or []:
                    wj.writerow([
                        e.get("date"), e.get("journal_type"), e.get("reference"),
                        e.get("description"),
                        ln.get("account_number"), ln.get("account_name"),
                        ln.get("third_party_id"), ln.get("line_description"),
                        ln.get("debit", 0), ln.get("credit", 0),
                    ])
            z.writestr(f"{acp_name}/{year_folder}/journal_entries.csv", je_io.getvalue())

            # 4b. Invoices
            invs = await db.invoices.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            inv_io = io.StringIO()
            wi = csv.writer(inv_io, delimiter=";")
            wi.writerow(["date", "invoice_number", "supplier_id", "supplier_name",
                         "amount_total", "amount_paid", "vat", "status"])
            for iv in invs:
                wi.writerow([iv.get("date"), iv.get("invoice_number"),
                             iv.get("supplier_id"), iv.get("supplier_name"),
                             iv.get("amount_total"), iv.get("amount_paid"),
                             iv.get("vat"), iv.get("status")])
            z.writestr(f"{acp_name}/{year_folder}/invoices.csv", inv_io.getvalue())

            # 4c. Fund calls
            fcs = await db.fund_calls.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            fc_io = io.StringIO()
            wf = csv.writer(fc_io, delimiter=";")
            wf.writerow(["date", "type", "amount", "quota_key", "period_label"])
            for fc in fcs:
                wf.writerow([fc.get("date"), fc.get("type"), fc.get("amount"),
                             fc.get("quota_key"), fc.get("period_label")])
            z.writestr(f"{acp_name}/{year_folder}/fund_calls.csv", fc_io.getvalue())

            # 4d. Bank transactions
            txs = await db.bank_transactions.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(200000)
            tx_io = io.StringIO()
            wt = csv.writer(tx_io, delimiter=";")
            wt.writerow(["date", "counterparty_name", "communication", "amount", "matched", "match_type"])
            for tx in txs:
                wt.writerow([tx.get("date"), tx.get("counterparty_name"),
                             tx.get("communication"), tx.get("amount"),
                             tx.get("matched"), tx.get("match_type")])
            z.writestr(f"{acp_name}/{year_folder}/bank_transactions.csv", tx_io.getvalue())

            # 4e. Bilan PDF (optionnel)
            if include_pdfs:
                try:
                    from pdf_bilan import build_bilan_pdf
                    from routes.reports import _compute_bilan
                    bilan_data = await _compute_bilan(db, copropriete_id, fy.get("id"))
                    pdf = build_bilan_pdf(bilan_data)
                    z.writestr(f"{acp_name}/{year_folder}/bilan.pdf", pdf)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Bilan PDF absent pour %s : %s", year_folder, e)

    data = buf.getvalue()
    buf.close()
    filename = f"archive_{acp_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"
    return data, filename
