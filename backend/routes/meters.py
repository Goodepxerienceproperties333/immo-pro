from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid

# iter90ie : liste des types de compteurs autorises. `gas` et
# `boiler_maintenance` ajoutes pour couvrir tous les cas d'usage syndics
# beleges (entretien chaudiere = quotite-part de puissance thermique
# sans releve d'index, donc unite libre / "part").
METER_TYPES_ALLOWED = ("water", "heating", "electricity", "gas", "boiler_maintenance")

METER_TYPE_DEFAULT_UNIT = {
    "water": "m3",
    "heating": "kWh",
    "electricity": "kWh",
    "gas": "m3",
    "boiler_maintenance": "part",
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
        return {
            "created_readings": created_readings,
            "created_meters": created_meters,
            "errors": errors,
            "count": len(created_readings),
        }

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
