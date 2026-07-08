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

    Le ZIP est utilisable directement par le syndic sans outil special :
    - CSV avec UTF-8 BOM -> ouverture native dans Excel / LibreOffice
    - PDFs originaux (factures scannees, extraits bancaires) inclus
    - README.txt explicatif a la racine
    - Rapports PDF pre-generes (bilan, compte de resultat)

    Structure :
        {ACP_NAME}/
            README.txt                    <- explique le contenu
            metadata.json
            owners.csv                    <- UTF-8 BOM pour Excel
            lots.csv                      <- UTF-8 BOM pour Excel
            YYYY/
                journal_entries.csv       <- UTF-8 BOM
                invoices.csv              <- UTF-8 BOM
                fund_calls.csv            <- UTF-8 BOM
                bank_transactions.csv     <- UTF-8 BOM
                bilan.pdf                 (si include_pdfs)
                compte_resultat.pdf       (si include_pdfs)
                documents/
                    factures/{invoice_number}.pdf   <- originaux
                    extraits/{stmt_number}.pdf      <- originaux

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

    # iter90bs : UTF-8 BOM devant les CSV pour ouverture native Excel FR/BE
    def _csv_bytes(header: list, rows: list) -> bytes:
        sio = io.StringIO()
        w = csv.writer(sio, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
        return b"\xef\xbb\xbf" + sio.getvalue().encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        # 1. README explicatif
        readme = _build_readme(copro, fiscal_years, owners, lots, include_pdfs)
        z.writestr(f"{acp_name}/README.txt", readme.encode("utf-8"))

        # 2. Metadata global
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
            "format_version": "1.1",  # iter90bs
        }
        z.writestr(f"{acp_name}/metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))

        # 3. Owners CSV (UTF-8 BOM)
        z.writestr(
            f"{acp_name}/owners.csv",
            _csv_bytes(
                ["id", "name", "email", "phone", "address", "postal_code", "city", "vcs_code"],
                [[o.get("id"), o.get("name"), o.get("email"), o.get("phone"),
                  o.get("address"), o.get("postal_code"), o.get("city"), o.get("vcs_code")]
                 for o in owners],
            ),
        )

        # 4. Lots CSV (UTF-8 BOM)
        z.writestr(
            f"{acp_name}/lots.csv",
            _csv_bytes(
                ["id", "number", "description", "quotity", "owner_id"],
                [[lt.get("id"), lt.get("number"), lt.get("description"),
                  lt.get("quotity"), lt.get("owner_id")] for lt in lots],
            ),
        )

        # 5. Par exercice fiscal
        for fy in fiscal_years:
            year_folder = fy.get("name") or fy.get("start_date", "unknown")[:4]
            year_folder = year_folder.replace("/", "_").replace(" ", "_")
            year_prefix = f"{acp_name}/{year_folder}"

            start = fy.get("start_date", "")
            end = fy.get("end_date", "")

            # 5a. Journal entries
            entries = await db.journal_entries.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).sort("date", 1).to_list(200000)
            je_rows = []
            for e in entries:
                for ln in e.get("lines", []) or []:
                    je_rows.append([
                        e.get("date"), e.get("journal_type"), e.get("reference"),
                        e.get("description"),
                        ln.get("account_number"), ln.get("account_name"),
                        ln.get("third_party_id"), ln.get("line_description"),
                        ln.get("debit", 0), ln.get("credit", 0),
                    ])
            z.writestr(f"{year_prefix}/journal_entries.csv", _csv_bytes(
                ["date", "journal_type", "reference", "description",
                 "account_number", "account_name", "third_party_id",
                 "line_description", "debit", "credit"], je_rows))

            # 5b. Invoices
            invs = await db.invoices.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            z.writestr(f"{year_prefix}/invoices.csv", _csv_bytes(
                ["date", "invoice_number", "supplier_id", "supplier_name",
                 "amount_total", "amount_paid", "vat", "status"],
                [[iv.get("date"), iv.get("invoice_number"),
                  iv.get("supplier_id"), iv.get("supplier_name"),
                  iv.get("amount_total"), iv.get("amount_paid"),
                  iv.get("vat"), iv.get("status")] for iv in invs]))

            # 5c. Fund calls
            fcs = await db.fund_calls.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            z.writestr(f"{year_prefix}/fund_calls.csv", _csv_bytes(
                ["date", "type", "amount", "quota_key", "period_label"],
                [[fc.get("date"), fc.get("type"), fc.get("amount"),
                  fc.get("quota_key"), fc.get("period_label")] for fc in fcs]))

            # 5d. Bank transactions
            txs = await db.bank_transactions.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(200000)
            z.writestr(f"{year_prefix}/bank_transactions.csv", _csv_bytes(
                ["date", "counterparty_name", "communication", "amount",
                 "matched", "match_type"],
                [[tx.get("date"), tx.get("counterparty_name"),
                  tx.get("communication"), tx.get("amount"),
                  tx.get("matched"), tx.get("match_type")] for tx in txs]))

            # 5e. Rapports PDF (bilan + compte de resultat)
            if include_pdfs:
                try:
                    from pdf_bilan import build_bilan_pdf
                    from routes.reports import _compute_bilan
                    bilan_data = await _compute_bilan(db, copropriete_id, fy.get("id"))
                    z.writestr(f"{year_prefix}/bilan.pdf", build_bilan_pdf(bilan_data))
                except Exception as e:  # noqa: BLE001
                    logger.warning("Bilan PDF absent pour %s : %s", year_folder, e)
                # iter90bs : ajout compte de resultat
                try:
                    from pdf_resultat import build_resultat_pdf
                    from routes.reports import _compute_resultat
                    resultat_data = await _compute_resultat(db, copropriete_id, fy.get("id"))
                    z.writestr(f"{year_prefix}/compte_resultat.pdf", build_resultat_pdf(resultat_data))
                except Exception as e:  # noqa: BLE001
                    logger.warning("Compte de resultat PDF absent pour %s : %s", year_folder, e)

            # 5f. iter90bs : Documents originaux (factures + extraits) GridFS
            if include_pdfs:
                await _append_original_documents(
                    db, z, year_prefix, copropriete_id, start, end
                )

    data = buf.getvalue()
    buf.close()
    filename = f"archive_{acp_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"
    return data, filename


def _build_readme(copro: dict, fiscal_years: list, owners: list, lots: list, include_pdfs: bool) -> str:
    """Genere le README.txt explicatif du contenu du ZIP (iter90bs)."""
    lines = [
        "=" * 70,
        f"ARCHIVE COPROMANAGER - {copro.get('name', 'ACP')}",
        "=" * 70,
        "",
        f"Genere le : {datetime.now(timezone.utc).strftime('%d/%m/%Y a %H:%M UTC')}",
        f"Copropriete : {copro.get('name', '')}",
        f"Reference : {copro.get('reference', '')}",
        f"Nombre de lots : {len(lots)}",
        f"Nombre de proprietaires : {len(owners)}",
        f"Nombre d'exercices fiscaux : {len(fiscal_years)}",
        "",
        "-" * 70,
        "CONTENU DU ZIP",
        "-" * 70,
        "",
        "  README.txt          <- Ce fichier",
        "  metadata.json       <- Informations globales sur l'ACP",
        "  owners.csv          <- Liste des proprietaires (Excel-ready UTF-8 BOM)",
        "  lots.csv            <- Liste des lots et leurs quotites",
        "",
        "  Par annee fiscale (dossier YYYY) :",
        "    journal_entries.csv       <- Toutes les ecritures comptables",
        "    invoices.csv              <- Toutes les factures fournisseurs",
        "    fund_calls.csv            <- Tous les appels de fonds",
        "    bank_transactions.csv     <- Toutes les transactions bancaires",
    ]
    if include_pdfs:
        lines.extend([
            "    bilan.pdf                 <- Bilan comptable de l'exercice",
            "    compte_resultat.pdf       <- Compte de resultat de l'exercice",
            "    documents/",
            "        factures/*.pdf        <- Factures scannees originales",
            "        extraits/*.pdf        <- Extraits bancaires originaux",
        ])
    lines.extend([
        "",
        "-" * 70,
        "COMMENT UTILISER CET ARCHIVE",
        "-" * 70,
        "",
        "1. LECTURE EXCEL / LIBREOFFICE :",
        "   - Tous les CSV sont encodes en UTF-8 avec BOM.",
        "   - Ouvrez-les directement, les caracteres speciaux (e, e, e, ...) seront",
        "     correctement affiches.",
        "   - Le separateur est le point-virgule (;) - standard belge.",
        "",
        "2. IMPORTER DANS UN AUTRE LOGICIEL COMPTABLE :",
        "   - Le fichier journal_entries.csv contient toutes les ecritures en",
        "     double partie (colonnes debit et credit).",
        "   - Il respecte le PCMN belge (arrete royal 21/10/2018).",
        "   - Chaque ligne d'ecriture porte un account_number et un montant.",
        "",
        "3. ARCHIVER LEGALEMENT :",
        "   - Conservez ce ZIP au moins 10 ans (obligation legale belge pour",
        "     les documents comptables - art. III.86 CDE).",
        "   - Les PDF (factures, extraits) sont les originaux non modifies.",
        "",
        "4. RESTAURER DANS COPROMANAGER (superadmin uniquement) :",
        "   - Rendez-vous dans Plateforme > Sauvegardes ACP.",
        "   - Cliquez sur 'Restaurer un ZIP' et selectionnez ce fichier.",
        "",
        "-" * 70,
        "SUPPORT",
        "-" * 70,
        "",
        "En cas de question : welcome@goodexperienceproperties.be",
        "URL : https://immo-pcmn.emergent.host",
        "",
        "=" * 70,
    ])
    return "\n".join(lines)


async def _append_original_documents(
    db, zipf: "zipfile.ZipFile", year_prefix: str,
    copropriete_id: str, start_date: str, end_date: str,
) -> None:
    """Ajoute au ZIP les PDF originaux (factures + extraits) de l'exercice
    depuis GridFS (iter90bs).

    Silencieux en cas d'erreur : la sauvegarde CSV est prioritaire, les
    documents originaux sont un bonus. Les factures sans PDF (saisie
    manuelle) sont simplement ignorees.
    """
    # Factures avec attachment_id
    invs = await db.invoices.find({
        "copropriete_id": copropriete_id,
        "date": {"$gte": start_date, "$lte": end_date},
        "attachments": {"$exists": True, "$ne": []},
    }, {"_id": 0, "number": 1, "attachments": 1}).to_list(20000)
    if invs:
        try:
            from gridfs_storage import GridFSStorage
            storage = GridFSStorage(db, bucket_name="invoice_attachments")
            for iv in invs:
                inv_num = (iv.get("number") or "").replace("/", "_").replace(" ", "_")
                for i, att in enumerate(iv.get("attachments") or []):
                    file_id = att.get("id") if isinstance(att, dict) else att
                    if not file_id:
                        continue
                    try:
                        data = await storage.download(file_id)
                        suffix = "" if i == 0 else f"-{i + 1}"
                        zipf.writestr(
                            f"{year_prefix}/documents/factures/{inv_num}{suffix}.pdf",
                            data,
                        )
                    except Exception:
                        pass  # facture sans pdf original ou grifs error -> skip
        except Exception as e:  # noqa: BLE001
            logger.warning("GridFS invoice_attachments indisponible : %s", e)

    # Extraits bancaires avec source_file_id
    stmts = await db.bank_statements.find({
        "copropriete_id": copropriete_id,
        "date": {"$gte": start_date, "$lte": end_date},
        "source_file_id": {"$exists": True, "$ne": None, "$ne": ""},
    }, {"_id": 0, "number": 1, "source_file_id": 1}).to_list(2000)
    if stmts:
        try:
            from gridfs_storage import GridFSStorage
            storage = GridFSStorage(db, bucket_name="bank_statement_sources")
            for s in stmts:
                stmt_num = (s.get("number") or "").replace("/", "_").replace(" ", "_")
                try:
                    data = await storage.download(s["source_file_id"])
                    zipf.writestr(
                        f"{year_prefix}/documents/extraits/{stmt_num}.pdf",
                        data,
                    )
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning("GridFS bank_statement_sources indisponible : %s", e)
