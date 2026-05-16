from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid


class MeterInput(BaseModel):
    name: str
    meter_type: str  # water, heating, electricity
    unit: Optional[str] = ""
    lot_id: Optional[str] = ""
    serial_number: Optional[str] = ""


class ReadingInput(BaseModel):
    date: str
    value: float


def create_meters_router(db):
    router = APIRouter(prefix="/api/meters")

    @router.get("")
    async def list_meters(meter_type: Optional[str] = None):
        query = {}
        if meter_type:
            query["meter_type"] = meter_type
        meters = await db.meters.find(query, {"_id": 0}).sort("name", 1).to_list(1000)
        return meters

    @router.post("")
    async def create_meter(data: MeterInput):
        unit = data.unit
        if not unit:
            unit = {"water": "m3", "heating": "kWh", "electricity": "kWh"}.get(data.meter_type, "")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "meter_type": data.meter_type,
            "unit": unit,
            "lot_id": data.lot_id,
            "serial_number": data.serial_number,
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

    return router
