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
    # iter90bw : inclure les proprietaires historiques (via journal entries)
    tps = await db.journal_entries.distinct(
        "lines.third_party_id", {"copropriete_id": copropriete_id}
    )
    for tpid in tps:
        if tpid and tpid not in owner_ids:
            owner_ids.append(tpid)
    # iter90bw : inclure les proprietaires references dans les fund_calls
    fc_docs = await db.fund_calls.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "distribution": 1},
    ).to_list(20000)
    for fc in fc_docs:
        for d in (fc.get("distribution") or []):
            oid = d.get("owner_id")
            if oid and oid not in owner_ids:
                owner_ids.append(oid)
    owners = await db.owners.find({"id": {"$in": owner_ids}}).to_list(5000) if owner_ids else []

    # iter90bw : suppliers de l'ACP + suppliers utilises dans les factures/JE
    suppliers_scoped = await db.suppliers.find({"copropriete_id": copropriete_id}).to_list(10000)
    # Ajout des suppliers references dans les factures/journal (memory des cross-ACP)
    inv_supplier_ids = await db.invoices.distinct(
        "supplier_id", {"copropriete_id": copropriete_id, "supplier_id": {"$nin": [None, ""]}}
    )
    known_sup_ids = {s.get("id") for s in suppliers_scoped}
    missing_sup_ids = [sid for sid in inv_supplier_ids if sid and sid not in known_sup_ids]
    if missing_sup_ids:
        extras = await db.suppliers.find({"id": {"$in": missing_sup_ids}}).to_list(5000)
        suppliers_scoped.extend(extras)

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
            "format_version": "1.2",  # iter90bw
        }
        z.writestr(f"{acp_name}/metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))

        # 3. Owners CSV (UTF-8 BOM) - iter90bw : enrichi (BCE/TVA/IBAN, tier_accounts, roles)
        owners_rows = []
        for o in owners:
            tier_acc = (o.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            owners_rows.append([
                o.get("id"), o.get("name"), o.get("last_name"), o.get("first_name"),
                o.get("email"), o.get("phone"),
                o.get("address"), o.get("postal_code"), o.get("city"), o.get("country"),
                o.get("bce_number") or o.get("vat_number"),
                o.get("iban"), o.get("bic"),
                o.get("vcs_code"), o.get("vcs_digits"),
                tier_acc.get("provisions", ""), tier_acc.get("reserve", ""),
                o.get("is_company"), o.get("notes", ""),
                o.get("created_at", ""),
            ])
        z.writestr(
            f"{acp_name}/owners.csv",
            _csv_bytes(
                ["id", "name", "last_name", "first_name",
                 "email", "phone",
                 "address", "postal_code", "city", "country",
                 "bce_or_vat_number", "iban", "bic",
                 "vcs_code", "vcs_digits",
                 "compte_provisions", "compte_reserve",
                 "is_company", "notes", "created_at"],
                owners_rows,
            ),
        )

        # 4. Lots CSV (UTF-8 BOM) - iter90bw : ajout adresse + parent + type
        lots_rows = []
        for lt in lots:
            lots_rows.append([
                lt.get("id"), lt.get("number") or lt.get("lot_number"),
                lt.get("description"), lt.get("type"),
                lt.get("floor"), lt.get("parent_lot_id"),
                lt.get("address"), lt.get("postal_code"), lt.get("city"),
                lt.get("quotity"), lt.get("owner_id"),
                lt.get("cadastral_reference"),
                lt.get("acte_notarie_date"), lt.get("acte_notarie_notaire"),
            ])
        z.writestr(
            f"{acp_name}/lots.csv",
            _csv_bytes(
                ["id", "number", "description", "type",
                 "floor", "parent_lot_id",
                 "address", "postal_code", "city",
                 "quotity", "owner_id",
                 "cadastral_reference",
                 "acte_notarie_date", "acte_notarie_notaire"],
                lots_rows,
            ),
        )

        # 4b. Suppliers CSV (UTF-8 BOM) - iter90bw : fournisseurs de l'ACP avec details complets
        sup_rows = []
        for s in suppliers_scoped:
            tier_acc = ((s.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
            sup_rows.append([
                s.get("id"), s.get("name"), s.get("auxiliary_code"),
                s.get("bce_number"), s.get("vat_number"),
                s.get("address"), s.get("postal_code"), s.get("city"), s.get("country"),
                s.get("phone"), s.get("email"),
                s.get("iban"), s.get("bic"),
                tier_acc, s.get("default_account"),
                s.get("notes"), s.get("created_at"),
            ])
        z.writestr(
            f"{acp_name}/suppliers.csv",
            _csv_bytes(
                ["id", "name", "auxiliary_code",
                 "bce_number", "vat_number",
                 "address", "postal_code", "city", "country",
                 "phone", "email",
                 "iban", "bic",
                 "compte_tier", "compte_defaut",
                 "notes", "created_at"],
                sup_rows,
            ),
        )

        # 4c. Documents (AG, PV, contrats, ...) CSV + originaux - iter90bw
        await _append_documents_index_and_originals(db, z, acp_name, copropriete_id, _csv_bytes)

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

            # 5b. Invoices - iter90bw : enrichi (description, TVA, categorie, IBAN, statut, note)
            invs = await db.invoices.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            # Denormaliser BCE/TVA fournisseur pour preuve legale meme si le fournisseur est modifie plus tard
            sup_by_id = {s.get("id"): s for s in suppliers_scoped}
            inv_rows = []
            for iv in invs:
                sup = sup_by_id.get(iv.get("supplier_id"), {}) or {}
                inv_rows.append([
                    iv.get("date"), iv.get("due_date"),
                    iv.get("number") or iv.get("invoice_number"),
                    iv.get("supplier_id"), iv.get("supplier") or iv.get("supplier_name"),
                    sup.get("bce_number"), sup.get("vat_number"),
                    sup.get("iban"), sup.get("bic"),
                    iv.get("description"),
                    iv.get("account_number"), iv.get("expense_category_id"),
                    iv.get("distribution_key_id"),
                    iv.get("total_amount") or iv.get("amount_total"),
                    iv.get("vat_amount") or iv.get("vat"),
                    iv.get("amount_paid"),
                    iv.get("status"), iv.get("is_private_fee"),
                    iv.get("created_at", ""),
                ])
            z.writestr(f"{year_prefix}/invoices.csv", _csv_bytes(
                ["date", "due_date", "invoice_number",
                 "supplier_id", "supplier_name", "supplier_bce", "supplier_vat",
                 "supplier_iban", "supplier_bic",
                 "description",
                 "account_number", "expense_category_id", "distribution_key_id",
                 "total_amount", "vat_amount", "amount_paid",
                 "status", "is_private_fee", "created_at"],
                inv_rows,
            ))

            # 5b-bis. Lignes de facture multi-natures (iter90bw)
            inv_lines_rows = []
            for iv in invs:
                for ln in (iv.get("lines") or []):
                    inv_lines_rows.append([
                        iv.get("date"),
                        iv.get("number") or iv.get("invoice_number"),
                        ln.get("account_number"), ln.get("account_name"),
                        ln.get("distribution_key_id"),
                        ln.get("amount"), ln.get("description"),
                    ])
            if inv_lines_rows:
                z.writestr(f"{year_prefix}/invoice_lines.csv", _csv_bytes(
                    ["invoice_date", "invoice_number",
                     "account_number", "account_name",
                     "distribution_key_id", "amount", "description"],
                    inv_lines_rows,
                ))

            # 5c. Fund calls + distribution par proprietaire (iter90bw)
            fcs = await db.fund_calls.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(50000)
            z.writestr(f"{year_prefix}/fund_calls.csv", _csv_bytes(
                ["date", "name", "call_type", "total_amount",
                 "reserve_amount", "roulement_amount",
                 "distribution_key_id", "status", "budget_id", "created_at"],
                [[fc.get("date"), fc.get("name"), fc.get("call_type"),
                  fc.get("total_amount"),
                  fc.get("reserve_amount"), fc.get("roulement_amount"),
                  fc.get("distribution_key_id"), fc.get("status"),
                  fc.get("budget_id"), fc.get("created_at", "")]
                 for fc in fcs]))
            # Distribution par proprietaire (une ligne par (fund_call, owner, lot))
            fc_dist_rows = []
            for fc in fcs:
                for d in (fc.get("distribution") or []):
                    fc_dist_rows.append([
                        fc.get("date"), fc.get("name"),
                        d.get("owner_id"), d.get("owner_name"),
                        d.get("lot_id"), d.get("lot_number"),
                        d.get("vcs_code"),
                        d.get("share"), d.get("amount"),
                        d.get("paid"), d.get("paid_date"),
                    ])
            if fc_dist_rows:
                z.writestr(f"{year_prefix}/fund_calls_distribution.csv", _csv_bytes(
                    ["fund_call_date", "fund_call_name",
                     "owner_id", "owner_name",
                     "lot_id", "lot_number", "vcs_code",
                     "share", "amount", "paid", "paid_date"],
                    fc_dist_rows,
                ))

            # 5d. Bank transactions - iter90bw : ajout matched_to, description, reconciliation
            txs = await db.bank_transactions.find({
                "copropriete_id": copropriete_id,
                "date": {"$gte": start, "$lte": end},
            }).to_list(200000)
            z.writestr(f"{year_prefix}/bank_transactions.csv", _csv_bytes(
                ["date", "value_date", "counterparty_name", "counterparty_iban",
                 "communication", "description", "amount", "currency",
                 "matched", "match_type", "matched_to", "matched_invoice_number",
                 "statement_number", "reference"],
                [[tx.get("date"), tx.get("value_date"),
                  tx.get("counterparty_name"), tx.get("counterparty_iban"),
                  tx.get("communication"), tx.get("description"),
                  tx.get("amount"), tx.get("currency"),
                  tx.get("matched"), tx.get("match_type"),
                  tx.get("matched_to"), tx.get("matched_invoice_number"),
                  tx.get("statement_number"), tx.get("reference")]
                 for tx in txs]))

            # 5e. Rapports PDF (bilan + compte de resultat)
            if include_pdfs:
                try:
                    from pdf_bilan import build_bilan_pdf
                    from routes.reports import compute_bilan_data
                    # iter90fq : fix de l'appel casse (signature incorrecte +
                    # import d'une fonction inexistante `_compute_bilan`).
                    # `compute_bilan_data` est reutilisee depuis routes/reports.py
                    # (meme fonction que l'endpoint /reports/bilan) - evite toute
                    # divergence de logique entre le backup et l'app.
                    bilan_data = await compute_bilan_data(
                        db, copropriete_id, date_to=end, fiscal_year_id=fy.get("id"),
                    )
                    z.writestr(f"{year_prefix}/bilan.pdf", build_bilan_pdf(
                        copropriete=copro, fiscal_year=fy, bilan_data=bilan_data,
                        date_to=end,
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.warning("Bilan PDF absent pour %s : %s", year_folder, e)
                # iter90bs : ajout compte de resultat
                # NOTE (iter90fq) : `pdf_resultat.py`/`build_resultat_pdf` n'existe
                # pas dans le codebase - cette fonctionnalite n'a jamais ete
                # implementee (import ci-dessous echouera toujours, avale par le
                # try/except). A construire dans un futur chantier dedie si le
                # compte de resultat PDF est requis dans les backups.
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
            # iter92a : Decomptes annuels par proprietaire (post-repartition)
            if include_pdfs:
                await _append_owner_decomptes(
                    db, z, year_prefix, copropriete_id, fy
                )

        # iter92a : Documents globaux (hors periode) + historique communications
        # ranges dans <acp_name>/documents/all/ et communications/
        # IMPORTANT: doit rester DANS le `with zipfile.ZipFile(...) as z:`
        if include_pdfs:
            await _append_all_acp_documents(db, z, acp_name, copropriete_id)
            await _append_sent_communications(db, z, acp_name, copropriete_id, _csv_bytes)

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
        "  owners.csv          <- Proprietaires (nom, BCE/TVA, IBAN, VCS, comptes tiers)",
        "  lots.csv            <- Lots avec adresse, quotites, ref cadastrale, acte notarie",
        "  suppliers.csv       <- Fournisseurs avec BCE/TVA/IBAN/BIC/compte tier",
        "  documents.csv       <- Index des documents (AG, PV, contrats, correspondances)",
        "  documents_generaux/ <- Documents originaux groupes par categorie",
        "",
        "  Par annee fiscale (dossier YYYY) :",
        "    journal_entries.csv       <- Toutes les ecritures comptables (double partie)",
        "    invoices.csv              <- Factures fournisseurs (avec TVA + BCE + IBAN + statut)",
        "    invoice_lines.csv         <- Lignes multi-natures des factures (si utilisees)",
        "    fund_calls.csv            <- Appels de fonds (montant, cle, statut, budget)",
        "    fund_calls_distribution.csv <- Repartition par proprietaire et lot",
        "    bank_transactions.csv     <- Transactions bancaires + rapprochement",
    ]
    if include_pdfs:
        lines.extend([
            "    bilan.pdf                 <- Bilan comptable de l'exercice (post-repartition)",
            "    compte_resultat.pdf       <- Compte de resultat de l'exercice",
            "    documents/",
            "        factures/*.pdf        <- Factures scannees originales",
            "        extraits/*.pdf        <- Extraits bancaires originaux",
            "    decomptes/                <- iter92a : decomptes annuels par proprietaire",
            "        DECOMPTE-<Nom>.pdf    <- Un PDF par proprietaire (repartition finale)",
        ])
        lines.extend([
            "",
            "  Global ACP (hors exercice) :",
            "    documents/all/<categorie>/ <- iter92a : tous les documents de l'ACP",
            "                                  (releves compteurs, uploads, PV AG...)",
            "    communications/",
            "        historique.csv         <- iter92a : journal des envois emails/postal",
            "        attachments/*.pdf      <- Pieces jointes envoyees aux proprietaires",
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
    }, {"_id": 0, "number": 1, "attachments": 1, "date": 1}).to_list(20000)
    if invs:
        try:
            from gridfs_storage import GridFSStorage
            storage = GridFSStorage(db, bucket_name="invoice_attachments")
            for iv in invs:
                inv_num = (iv.get("number") or "unknown").replace("/", "_").replace(" ", "_")
                for i, att in enumerate(iv.get("attachments") or []):
                    file_id = att.get("id") if isinstance(att, dict) else att
                    if not file_id:
                        continue
                    try:
                        data = await storage.download(file_id)
                        suffix = "" if i == 0 else f"-{i + 1}"
                        # iter90bw : conserver l'extension originale (pdf/jpg/png)
                        orig_name = (att.get("filename") if isinstance(att, dict) else "") or "attachment.pdf"
                        ext = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else "pdf"
                        zipf.writestr(
                            f"{year_prefix}/documents/factures/{inv_num}{suffix}.{ext}",
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
        "source_file_id": {"$exists": True, "$nin": [None, ""]},
    }, {"_id": 0, "number": 1, "source_file_id": 1, "source_filename": 1}).to_list(2000)
    if stmts:
        try:
            from gridfs_storage import GridFSStorage
            storage = GridFSStorage(db, bucket_name="bank_statement_sources")
            for s in stmts:
                stmt_num = (s.get("number") or "unknown").replace("/", "_").replace(" ", "_")
                try:
                    data = await storage.download(s["source_file_id"])
                    orig = s.get("source_filename") or "statement.pdf"
                    ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "pdf"
                    zipf.writestr(
                        f"{year_prefix}/documents/extraits/{stmt_num}.{ext}",
                        data,
                    )
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning("GridFS bank_statement_sources indisponible : %s", e)


async def _append_documents_index_and_originals(
    db, zipf: "zipfile.ZipFile", acp_name: str, copropriete_id: str, _csv_bytes,
) -> None:
    """iter90bw : ajoute au ZIP l'index CSV + les originaux de la collection
    `documents` (AG, PV, contrats, rapports, correspondance, etc.).

    Ces documents ne sont pas dates par exercice fiscal, ils sont archives a
    la racine dans un dossier `documents_generaux/`. Metadata dans documents.csv.
    """
    docs = await db.documents.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0},
    ).to_list(50000)
    if not docs:
        return

    doc_rows = []
    try:
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="documents")
    except Exception as e:  # noqa: BLE001
        logger.warning("GridFS documents indisponible : %s", e)
        storage = None

    for d in docs:
        doc_rows.append([
            d.get("id"),
            d.get("category") or d.get("type"),
            d.get("title") or d.get("name"),
            d.get("description"),
            d.get("date") or d.get("document_date"),
            d.get("uploaded_by"),
            d.get("created_at"),
            d.get("filename"),
            d.get("size_bytes"),
            d.get("mime_type"),
        ])
        if storage and d.get("file_id"):
            try:
                data = await storage.download(d["file_id"])
                title_safe = ((d.get("title") or d.get("filename") or d.get("id") or "doc")
                              .replace("/", "_").replace(" ", "_"))
                orig = d.get("filename") or "document.pdf"
                ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "pdf"
                cat_safe = (d.get("category") or "divers").replace("/", "_").replace(" ", "_")
                zipf.writestr(
                    f"{acp_name}/documents_generaux/{cat_safe}/{title_safe}.{ext}",
                    data,
                )
            except Exception:
                pass

    zipf.writestr(
        f"{acp_name}/documents.csv",
        _csv_bytes(
            ["id", "category", "title", "description",
             "date", "uploaded_by", "created_at",
             "filename", "size_bytes", "mime_type"],
            doc_rows,
        ),
    )



# ---- iter92a : Post-repartition & documents ACP-wide ----

async def _append_owner_decomptes(
    db, zipf: "zipfile.ZipFile", year_prefix: str,
    copropriete_id: str, fy: dict,
) -> None:
    """iter92a : ajoute les decomptes annuels PDF de tous les proprietaires
    de l'ACP pour cet exercice (`<year>/decomptes/DECOMPTE-<Nom>.pdf`).

    Reflete la repartition finale des charges (post-decompte annuel).
    Silencieux en cas d'erreur : la sauvegarde CSV reste prioritaire.
    """
    try:
        from routes.reports import _build_decompte_annuel_pdf
    except Exception as e:  # noqa: BLE001
        logger.warning("iter92a : _build_decompte_annuel_pdf indisponible : %s", e)
        return

    # Selectionne les proprietaires qui ont un lot dans l'ACP OU un tier_account
    # OU sont impliques dans une mutation intra-FY (couvre les vendeurs sortis)
    fy_id = fy.get("id")
    owner_ids: set = set()
    lots = await db.lots.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "owner_id": 1, "owner_ids": 1},
    ).to_list(10000)
    for lt in lots:
        if lt.get("owner_id"):
            owner_ids.add(lt["owner_id"])
        for oid in (lt.get("owner_ids") or []):
            if oid:
                owner_ids.add(oid)
    tier_owners = await db.owners.find(
        {f"tier_accounts.{copropriete_id}": {"$exists": True}},
        {"_id": 0, "id": 1},
    ).to_list(10000)
    for o in tier_owners:
        owner_ids.add(o["id"])
    if fy.get("start_date") and fy.get("end_date"):
        muts = await db.mutations.find(
            {"copropriete_id": copropriete_id,
             "sale_date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0, "from_owner_id": 1, "to_owner_id": 1},
        ).to_list(10000)
        for m in muts:
            if m.get("from_owner_id"):
                owner_ids.add(m["from_owner_id"])
            if m.get("to_owner_id"):
                owner_ids.add(m["to_owner_id"])

    count = 0
    for owner_id in owner_ids:
        try:
            owner = await db.owners.find_one(
                {"id": owner_id}, {"_id": 0, "name": 1}
            )
            if not owner:
                continue
            pdf_bytes, _fname = await _build_decompte_annuel_pdf(
                db, owner_id, copropriete_id, fiscal_year_id=fy_id, preview=False
            )
            safe_name = (owner.get("name") or owner_id)[:80]
            safe_name = safe_name.replace("/", "_").replace(" ", "_").replace("\\", "_")
            zipf.writestr(
                f"{year_prefix}/decomptes/DECOMPTE-{safe_name}.pdf",
                pdf_bytes,
            )
            count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("iter92a : decompte KO owner=%s : %s", owner_id, e)
    logger.info("iter92a : %s decompte(s) inclus dans %s", count, year_prefix)


async def _append_all_acp_documents(
    db, zipf: "zipfile.ZipFile", acp_name: str, copropriete_id: str,
) -> None:
    """iter92a : ajoute TOUS les documents de la collection `db.documents`
    de cette ACP (relaves de compteurs, communications, uploads, etc.),
    ranges par categorie sous `<acp_name>/documents/all/<category>/`.

    N'ecrase pas les factures/extraits deja copies dans `documents/factures`
    et `documents/extraits` (categories differentes).
    """
    try:
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="documents")
    except Exception as e:  # noqa: BLE001
        logger.warning("iter92a : GridFSStorage indisponible : %s", e)
        return

    docs = await db.documents.find(
        {"copropriete_id": copropriete_id,
         "gridfs_id": {"$exists": True, "$ne": None}},
        {"_id": 0}
    ).to_list(50000)
    count = 0
    for d in docs:
        gid = d.get("gridfs_id")
        if not gid:
            continue
        try:
            data = await storage.download(gid)
            cat_safe = (d.get("category") or "divers").replace("/", "_").replace(" ", "_")
            title_safe = ((d.get("title") or d.get("filename") or d.get("id") or "doc")
                          .replace("/", "_").replace(" ", "_"))[:120]
            orig = d.get("filename") or "document.bin"
            ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "bin"
            # iter92a : suffixe unique pour eviter les collisions de noms
            # (plusieurs documents peuvent partager le meme title)
            uniq = (d.get("id") or "")[-6:] or "xxxxxx"
            zipf.writestr(
                f"{acp_name}/documents/all/{cat_safe}/{title_safe}__{uniq}.{ext}",
                data,
            )
            count += 1
        except Exception:
            pass  # GridFS orphelin -> skip silencieux
    logger.info("iter92a : %s document(s) generaux inclus", count)


async def _append_sent_communications(
    db, zipf: "zipfile.ZipFile", acp_name: str,
    copropriete_id: str, _csv_bytes,
) -> None:
    """iter92a : exporte l'historique des envois (`db.sent_communications`)
    en CSV + les PDF joints (via `attached_document_ids` -> `db.documents` +
    GridFS bucket `documents`) sous `<acp_name>/communications/`.
    """
    comms = await db.sent_communications.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).sort("sent_at", -1).to_list(20000)
    if not comms:
        return
    # 1) CSV recap
    rows = []
    for c in comms:
        rows.append([
            c.get("id", ""),
            c.get("sent_at", ""),
            c.get("channel", ""),
            c.get("type", ""),
            c.get("subject", ""),
            (c.get("recipient") or {}).get("name", "") if isinstance(c.get("recipient"), dict) else "",
            (c.get("recipient") or {}).get("email", "") if isinstance(c.get("recipient"), dict) else "",
            c.get("status", ""),
            c.get("dry_run", False),
            len(c.get("attached_document_ids") or []),
        ])
    zipf.writestr(
        f"{acp_name}/communications/historique.csv",
        _csv_bytes(
            ["id", "sent_at", "channel", "type", "subject",
             "recipient_name", "recipient_email", "status",
             "dry_run", "nb_attachments"],
            rows,
        ),
    )
    # 2) PDFs joints (documents avec source in sent_communications)
    try:
        from gridfs_storage import GridFSStorage
        storage = GridFSStorage(db, bucket_name="documents")
    except Exception:
        return
    attached_ids: set = set()
    for c in comms:
        for did in (c.get("attached_document_ids") or []):
            if did:
                attached_ids.add(did)
    if not attached_ids:
        return
    docs = await db.documents.find(
        {"id": {"$in": list(attached_ids)}, "copropriete_id": copropriete_id},
        {"_id": 0}
    ).to_list(50000)
    count = 0
    for d in docs:
        gid = d.get("gridfs_id")
        if not gid:
            continue
        try:
            data = await storage.download(gid)
            title_safe = ((d.get("title") or d.get("filename") or d.get("id") or "envoi")
                          .replace("/", "_").replace(" ", "_"))[:120]
            orig = d.get("filename") or "attachment.pdf"
            ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "pdf"
            # iter92a : suffixe unique pour eviter les collisions de noms
            uniq = (d.get("id") or "")[-6:] or "xxxxxx"
            zipf.writestr(
                f"{acp_name}/communications/attachments/{title_safe}__{uniq}.{ext}",
                data,
            )
            count += 1
        except Exception:
            pass
    logger.info("iter92a : %s attachment(s) communications inclus", count)
