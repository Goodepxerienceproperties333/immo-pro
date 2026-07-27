from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid

# iter90ie / iter90i9 : liste des types de compteurs autorises.
# `private_consumption` = valeur libre pour couvrir tout frais privatif de
# consommation non classe (ex: sechoir collectif, cave a vin partagee...).
METER_TYPES_ALLOWED = ("water", "heating", "electricity", "gas", "boiler_maintenance", "private_consumption")

METER_TYPE_DEFAULT_UNIT = {
    "water": "m3",
    "heating": "kWh",
    "electricity": "kWh",
    "gas": "m3",
    "boiler_maintenance": "part",
    "private_consumption": "unite",
}


class MeterInput(BaseModel):
    name: str
    meter_type: str  # water, heating, electricity, gas, boiler_maintenance
    unit: Optional[str] = ""
    lot_id: Optional[str] = ""
    serial_number: Optional[str] = ""
    copropriete_id: Optional[str] = ""


class ReadingInput(BaseModel):
    date: str
    value: float


def create_meters_router(db):
    router = APIRouter(prefix="/api/meters")

    @router.get("")
    async def list_meters(meter_type: Optional[str] = None, copropriete_id: Optional[str] = None):
        query = {}
        if meter_type:
            query["meter_type"] = meter_type
        if copropriete_id:
            query["copropriete_id"] = copropriete_id
        meters = await db.meters.find(query, {"_id": 0}).sort("name", 1).to_list(1000)
        return meters

    @router.post("")
    async def create_meter(data: MeterInput):
        if data.meter_type not in METER_TYPES_ALLOWED:
            raise HTTPException(
                400,
                f"Type de compteur invalide '{data.meter_type}'. "
                f"Valeurs autorisees : {', '.join(METER_TYPES_ALLOWED)}."
            )
        unit = data.unit
        if not unit:
            unit = METER_TYPE_DEFAULT_UNIT.get(data.meter_type, "")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "meter_type": data.meter_type,
            "unit": unit,
            "lot_id": data.lot_id,
            "serial_number": data.serial_number,
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.meters.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/{meter_id}")
    async def update_meter(meter_id: str, data: MeterInput):
        update = {
            "name": data.name, "meter_type": data.meter_type,
            "unit": data.unit, "lot_id": data.lot_id, "serial_number": data.serial_number
        }
        result = await db.meters.update_one({"id": meter_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Compteur non trouve")
        return await db.meters.find_one({"id": meter_id}, {"_id": 0})

    @router.delete("/{meter_id}")
    async def delete_meter(meter_id: str):
        result = await db.meters.delete_one({"id": meter_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Compteur non trouve")
        return {"message": "Compteur supprime"}

    # ---- READINGS ----
    class BatchReadingEntry(BaseModel):
        lot_id: str
        value: float

    class BatchReadingInput(BaseModel):
        date: str
        meter_type: str
        copropriete_id: str
        entries: List[BatchReadingEntry]

    @router.post("/batch-readings")
    async def add_batch_readings(data: BatchReadingInput):
        """iter90g5 : Cree un releve pour plusieurs lots en 1 appel.

        Pour chaque `entries[].lot_id`, on cherche le compteur de type
        `meter_type` rattache a ce lot dans l'ACP donnee. Si aucun compteur
        n'existe, on en cree un automatiquement (nom "Releve <type> - <lot>",
        serial vide). On enregistre ensuite la valeur comme un `reading`
        classique (avec calcul de la consommation).
        Retour : {created_readings: [...], created_meters: [...], errors: [...]}.
        """
        if data.meter_type not in METER_TYPES_ALLOWED:
            raise HTTPException(400, f"Type invalide '{data.meter_type}'")
        unit = METER_TYPE_DEFAULT_UNIT.get(data.meter_type, "")
        # iter90i6 : batch_id partage entre tous les releves du batch
        # permet d'attacher une meme PJ (decompte fournisseur) a tous.
        batch_id = str(uuid.uuid4())
        created_readings = []
        created_meters = []
        errors = []
        for entry in data.entries:
            # Trouve le lot
            lot = await db.lots.find_one({"id": entry.lot_id, "copropriete_id": data.copropriete_id}, {"_id": 0, "id": 1, "number": 1})
            if not lot:
                errors.append({"lot_id": entry.lot_id, "error": "Lot introuvable dans l'ACP"})
                continue
            # Trouve le compteur (ou cree)
            meter = await db.meters.find_one({
                "lot_id": entry.lot_id,
                "meter_type": data.meter_type,
                "copropriete_id": data.copropriete_id,
            }, {"_id": 0})
            if not meter:
                meter = {
                    "id": str(uuid.uuid4()),
                    "name": f"Releve {data.meter_type} - {lot.get('number','')}",
                    "meter_type": data.meter_type,
                    "unit": unit,
                    "lot_id": entry.lot_id,
                    "serial_number": "",
                    "copropriete_id": data.copropriete_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await db.meters.insert_one(meter)
                created_meters.append({"id": meter["id"], "lot_id": entry.lot_id, "lot_number": lot.get("number", "")})
            # Ajoute le reading (avec calcul consommation)
            prev = await db.meter_readings.find({"meter_id": meter["id"]}, {"_id": 0}).sort("date", -1).to_list(1)
            consumption = round(entry.value - prev[0]["value"], 2) if prev else 0.0
            doc = {
                "id": str(uuid.uuid4()),
                "meter_id": meter["id"],
                "date": data.date,
                "value": float(entry.value),
                "consumption": consumption,
                "batch_id": batch_id,
                "meter_type": data.meter_type,
                "copropriete_id": data.copropriete_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.meter_readings.insert_one(doc)
            created_readings.append({
                "id": doc["id"],
                "meter_id": meter["id"],
                "lot_id": entry.lot_id,
                "lot_number": lot.get("number", ""),
                "value": doc["value"],
                "consumption": consumption,
            })
        # iter90i5 : cree/met a jour la cle de repartition consommation
        dk_id, dk_name = "", ""
        if created_readings:
            dk_id, dk_name = await _upsert_meter_distribution_key(
                db=db, copropriete_id=data.copropriete_id,
                meter_type=data.meter_type, reading_date=data.date,
                readings=created_readings,
            )
        return {
            "created_readings": created_readings,
            "created_meters": created_meters,
            "errors": errors,
            "count": len(created_readings),
            "batch_id": batch_id,
            "distribution_key_id": dk_id,
            "distribution_key_name": dk_name,
        }

    async def _upsert_meter_distribution_key(*, db, copropriete_id: str, meter_type: str,
                                             reading_date: str, readings: list) -> tuple:
        """iter90i5 : Cree/met a jour une cle de repartition a partir des
        consommations d'un releve multi-lots.

        - Nom : "Consommation {type} - {date}" (ex: "Consommation water - 2026-07-26")
        - key_type : "consumption"
        - lots : [{lot_id, share = consommation}]

        Retourne (dk_id, dk_name). Cette cle peut ensuite etre selectionnee
        dans les formulaires OD pour repartir des frais proportionnellement
        a la consommation reelle.
        """
        type_label_fr = {
            "water": "eau", "heating": "chauffage",
            "electricity": "electricite", "gas": "gaz",
            "boiler_maintenance": "entretien chaudiere",
            "private_consumption": "frais privatif consommation",
        }.get(meter_type, meter_type)
        dk_name = f"Consommation {type_label_fr} - {reading_date}"
        # Construit la liste des lots avec leur consommation comme share
        lots_shares = []
        total_conso = 0.0
        for r in readings:
            conso = float(r.get("consumption") or 0)
            # Meme si consommation = 0 ou negative, on garde le lot dans la cle
            # (pour eviter les trous de repartition)
            lots_shares.append({"lot_id": r["lot_id"], "share": max(0.0, conso)})
            total_conso += max(0.0, conso)
        # Si toutes les conso sont nulles, on n'a pas de sens de creer une cle
        if total_conso < 0.005:
            return ("", "")
        # Upsert : si une cle avec le meme nom (meme type + date) existe deja
        # pour cette ACP, on la remplace (permet re-execution d'un releve).
        existing = await db.distribution_keys.find_one({
            "copropriete_id": copropriete_id, "name": dk_name,
        }, {"_id": 0, "id": 1})
        if existing:
            dk_id = existing["id"]
            await db.distribution_keys.update_one(
                {"id": dk_id},
                {"$set": {"lots": lots_shares, "key_type": "consumption",
                          "meter_type": meter_type, "reading_date": reading_date,
                          "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
        else:
            dk_id = str(uuid.uuid4())
            await db.distribution_keys.insert_one({
                "id": dk_id,
                "copropriete_id": copropriete_id,
                "name": dk_name,
                "key_type": "consumption",
                "is_default": False,
                "meter_type": meter_type,
                "reading_date": reading_date,
                "lots": lots_shares,
                "source": "meter_readings",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return (dk_id, dk_name)

    @router.get("/{meter_id}/readings")
    async def list_readings(meter_id: str):
        readings = await db.meter_readings.find({"meter_id": meter_id}, {"_id": 0}).sort("date", -1).to_list(1000)
        return readings

    @router.post("/{meter_id}/readings")
    async def add_reading(meter_id: str, data: ReadingInput):
        meter = await db.meters.find_one({"id": meter_id}, {"_id": 0})
        if not meter:
            raise HTTPException(404, "Compteur non trouve")
        # Get previous reading to compute consumption
        prev = await db.meter_readings.find({"meter_id": meter_id}, {"_id": 0}).sort("date", -1).to_list(1)
        consumption = 0.0
        if prev:
            consumption = round(data.value - prev[0]["value"], 2)

        doc = {
            "id": str(uuid.uuid4()),
            "meter_id": meter_id,
            "date": data.date,
            "value": data.value,
            "consumption": consumption,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.meter_readings.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.delete("/{meter_id}/readings/{reading_id}")
    async def delete_reading(meter_id: str, reading_id: str):
        result = await db.meter_readings.delete_one({"id": reading_id, "meter_id": meter_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Releve non trouve")
        return {"message": "Releve supprime"}

    # ---- ATTACHMENTS PJ (iter90i6) ----
    # Permet de joindre un document (decompte du fournisseur, PV de releve
    # d'index, facture d'eau...) a un ou plusieurs releves. Le document est
    # stocke dans GridFS et sera automatiquement joint au decompte annuel de
    # charges envoye aux proprietaires en fin d'exercice.
    @router.post("/readings/{reading_id}/attachment")
    async def upload_reading_attachment(reading_id: str, file: UploadFile = File(...)):
        r = await db.meter_readings.find_one({"id": reading_id}, {"_id": 0})
        if not r:
            raise HTTPException(404, "Releve introuvable")
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(400, "Fichier vide")
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(400, "Fichier trop volumineux (max 15 Mo)")
        from gridfs_storage import get_documents_storage
        storage = get_documents_storage(db)
        gid = await storage.upload(
            filename=file.filename or "attachment.pdf",
            contents=content,
            metadata={
                "reading_id": reading_id,
                "meter_id": r.get("meter_id", ""),
                "copropriete_id": r.get("copropriete_id", ""),
                "mime_type": file.content_type or "application/octet-stream",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        # iter90ia : la PJ est PROPRE AU LOT (au reading precis, pas au batch).
        # Chaque proprio ne recoit dans ses documents QUE la PJ de son lot.
        upd = {
            "attachment_gridfs_id": gid,
            "attachment_filename": file.filename or "attachment.pdf",
            "attachment_size": len(content),
            "attachment_mime": file.content_type or "application/pdf",
        }
        await db.meter_readings.update_one({"id": reading_id}, {"$set": upd})

        # iter90i7 / iter90ia : creation d'une entree documents/ pour le
        # proprio du LOT concerne uniquement (pas broadcast a tout le batch).
        # Cloisonnement strict : chaque proprio ne voit QUE la PJ de son lot.
        copro_id = r.get("copropriete_id", "")
        meter_type = r.get("meter_type", "")
        meter_id = r.get("meter_id", "")
        if copro_id and meter_id:
            meter = await db.meters.find_one({"id": meter_id}, {"_id": 0, "lot_id": 1})
            lot_id = (meter or {}).get("lot_id", "")
            owner_ids = set()
            if lot_id:
                lot = await db.lots.find_one({"id": lot_id}, {"_id": 0, "owner_id": 1, "owner_ids": 1})
                if lot:
                    if lot.get("owner_id"):
                        owner_ids.add(lot["owner_id"])
                    for oid in (lot.get("owner_ids") or []):
                        if oid:
                            owner_ids.add(oid)
            # Categorie unique "Releve de compteur"
            cat = await db.document_categories.find_one(
                {"copropriete_id": copro_id, "name": "Releve de compteur"},
                {"_id": 0, "id": 1}
            )
            if not cat:
                cat_id = str(uuid.uuid4())
                await db.document_categories.insert_one({
                    "id": cat_id,
                    "name": "Releve de compteur",
                    "description": "Documents des releves de compteur (decomptes fournisseurs, PV releves d'index)",
                    "copropriete_id": copro_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            else:
                cat_id = cat["id"]
            # Supprime les documents crees pour CE reading precis (permet
            # re-upload : ecrase les anciens sans doublon).
            await db.documents.delete_many({
                "source": "meter_reading", "reading_id": reading_id,
                "copropriete_id": copro_id,
            })
            title_meter_type = {
                "water": "Releve compteur eau", "heating": "Releve compteur chauffage",
                "electricity": "Releve compteur electricite", "gas": "Releve compteur gaz",
                "boiler_maintenance": "Entretien chaudiere",
                "private_consumption": "Frais privatif consommation",
            }.get(meter_type, "Releve compteur")
            # Recupere le numero de lot pour le titre
            lot_number = ""
            if lot_id:
                lot_doc = await db.lots.find_one({"id": lot_id}, {"_id": 0, "number": 1})
                lot_number = (lot_doc or {}).get("number", "")
            title_lot_suffix = f" (Lot {lot_number})" if lot_number else ""
            doc_title = f"{title_meter_type}{title_lot_suffix} - {r.get('date', '')}"
            for oid in owner_ids:
                await db.documents.insert_one({
                    "id": str(uuid.uuid4()),
                    "title": doc_title,
                    "description": f"Piece jointe releve compteur {meter_type} du {r.get('date','')} pour le lot {lot_number}",
                    "category_id": cat_id,
                    "filename": upd["attachment_filename"],
                    "gridfs_id": gid,
                    "mime_type": upd["attachment_mime"],
                    "size_bytes": len(content),
                    "copropriete_id": copro_id,
                    "owner_id": oid,
                    "source": "meter_reading",
                    "reading_id": reading_id,
                    "batch_id": r.get("batch_id", ""),
                    "lot_id": lot_id,
                    "meter_type": meter_type,
                    "reading_date": r.get("date", ""),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
        return {"ok": True, "gridfs_id": gid, "size": len(content), "filename": upd["attachment_filename"]}

    @router.get("/readings/{reading_id}/attachment/download")
    async def download_reading_attachment(reading_id: str):
        r = await db.meter_readings.find_one({"id": reading_id}, {"_id": 0})
        if not r or not r.get("attachment_gridfs_id"):
            raise HTTPException(404, "Aucune piece jointe pour ce releve")
        from gridfs_storage import get_documents_storage
        storage = get_documents_storage(db)
        data = await storage.download(r["attachment_gridfs_id"])
        if not data:
            raise HTTPException(404, "Fichier introuvable en GridFS")
        filename = (r.get("attachment_filename") or "attachment.pdf").replace('"', '')
        return Response(
            content=data,
            media_type=r.get("attachment_mime") or "application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @router.delete("/readings/{reading_id}/attachment")
    async def delete_reading_attachment(reading_id: str):
        r = await db.meter_readings.find_one({"id": reading_id}, {"_id": 0})
        if not r:
            raise HTTPException(404, "Releve introuvable")
        upd = {"attachment_gridfs_id": "", "attachment_filename": "",
               "attachment_size": 0, "attachment_mime": ""}
        await db.meter_readings.update_one({"id": reading_id}, {"$set": upd})
        # iter90ia : supprime les documents associes a CE reading precis
        await db.documents.delete_many({
            "source": "meter_reading", "reading_id": reading_id,
        })
        return {"ok": True}

    @router.get("/attachments/for-fiscal-year")
    async def list_fy_attachments(copropriete_id: str = Query(...),
                                  start_date: str = Query(...),
                                  end_date: str = Query(...)):
        """iter90i6 : liste des PJ de releves pour l'exercice donne, utilisee
        par la generation du decompte annuel (envoie automatiquement les
        justificatifs de consommation aux proprietaires)."""
        rows = await db.meter_readings.find({
            "copropriete_id": copropriete_id,
            "date": {"$gte": start_date, "$lte": end_date},
            "attachment_gridfs_id": {"$exists": True, "$ne": ""},
        }, {"_id": 0, "id": 1, "meter_type": 1, "date": 1, "batch_id": 1,
            "attachment_gridfs_id": 1, "attachment_filename": 1,
            "attachment_size": 1, "attachment_mime": 1}).to_list(1000)
        # Dedup par batch (un seul fichier par batch)
        seen = set()
        deduped = []
        for r in rows:
            key = r.get("batch_id") or r.get("id")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        return deduped

    # ---- CONSUMPTION PER LOT (iter90ie) ----
    # Utilise par la page de configuration des cles "meter" et par la
    # generation du decompte annuel pour repartir automatiquement les
    # charges d'eau/gaz/electricite au prorata des consommations reelles.
    @router.get("/consumption/by-lot")
    async def consumption_by_lot(
        copropriete_id: str = Query(...),
        meter_type: str = Query(...),
        start_date: str = Query(...),
        end_date: str = Query(...),
        fallback_key_id: Optional[str] = None,
    ):
        """Retourne la consommation par lot pour un type de compteur sur
        la periode [start_date, end_date] (bornes ISO). Utilise le meme
        helper que le calcul du decompte, ce qui garantit que la vue
        d'apercu et le PDF final restent alignes."""
        if meter_type not in METER_TYPES_ALLOWED:
            raise HTTPException(400, f"meter_type invalide '{meter_type}'.")
        from meter_shares import compute_meter_key_lots

        meters = await db.meters.find(
            {"copropriete_id": copropriete_id, "meter_type": meter_type},
            {"_id": 0},
        ).to_list(1000)
        meter_ids = [m["id"] for m in meters]
        readings = []
        if meter_ids:
            readings = await db.meter_readings.find(
                {"meter_id": {"$in": meter_ids}}, {"_id": 0}
            ).to_list(100000)
        all_lots = await db.lots.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(10000)
        fallback_key_lots = None
        if fallback_key_id:
            fbk = await db.distribution_keys.find_one(
                {"id": fallback_key_id, "copropriete_id": copropriete_id},
                {"_id": 0},
            )
            if fbk:
                fallback_key_lots = fbk.get("lots") or []
        return compute_meter_key_lots(
            meter_type=meter_type,
            meters=meters,
            readings=readings,
            all_lots=all_lots,
            start_date=start_date,
            end_date=end_date,
            fallback_key_lots=fallback_key_lots,
        )

    return router
