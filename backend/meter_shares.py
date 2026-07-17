"""iter90ie - Repartition des charges basee sur les compteurs (eau, gaz,
electricite, chauffage, entretien chaudiere).

Un `distribution_keys` avec `key_type == "meter"` a des `lots` calcules
DYNAMIQUEMENT au moment de la generation du decompte/rapport, a partir
des consommations (`meter_readings`) enregistrees sur la periode de
l'exercice comptable.

Regles :

1. On ne considere que les compteurs de la meme ACP dont le
   `meter_type` correspond au `meter_type` de la cle.
2. Un compteur DIVISIONNAIRE est attache a un `lot_id`. Un compteur
   COMMUN (lot_id vide) est ignore (il sert seulement de temoin de la
   conso totale).
3. Consommation d'un lot sur la periode `[start, end]` :
   - Si au moins 2 releves bornent la periode
     (releve <= start et releve >= end), on prend `index_end - index_start`.
     La periode couvre EXACTEMENT l'exercice, la conso est calculee
     mathematiquement, ce qui gere naturellement les mutations avec
     releve intermediaire (on capture l'index au jour de la vente).
   - Sinon, fallback : somme des `consumption` de tous les releves
     dans la periode.
   - Si aucun releve dans la periode, le lot est considere sans
     consommation -> il tombe dans le "fallback".
4. Fallback : les lots sans consommation reelle recuperent une share
   depuis la cle `fallback_key_id` (typiquement la cle par defaut de
   l'ACP). Sans fallback, ces lots n'apparaissent pas dans la cle.

Retourne un dict :

    {
      "lots": [{"lot_id", "lot_number", "share", "source"}, ...],
      "total": float,
      "coverage": {
        "with_meter_reading": int,   # lots avec conso reelle
        "via_fallback": int,          # lots reallies via fallback
        "excluded": int,              # lots ni compteur ni fallback
      },
    }

`source` = "meter" | "fallback" | "excluded".
"""
from typing import List, Optional


def _iso_date_lte(a: str, b: str) -> bool:
    return (a or "")[:10] <= (b or "")[:10]


def _iso_date_gte(a: str, b: str) -> bool:
    return (a or "")[:10] >= (b or "")[:10]


def _iso_date_between(d: str, start: str, end: str) -> bool:
    return _iso_date_gte(d, start) and _iso_date_lte(d, end)


def compute_meter_key_lots(
    *,
    meter_type: str,
    meters: List[dict],
    readings: List[dict],
    all_lots: List[dict],
    start_date: str,
    end_date: str,
    fallback_key_lots: Optional[List[dict]] = None,
) -> dict:
    """Calcule la repartition dynamique d'une cle "meter".

    Params
    ------
    meter_type : "water" | "gas" | "electricity" | "heating" | "boiler_maintenance"
    meters : compteurs de l'ACP (deja filtres sur copropriete_id).
    readings : releves de l'ACP (tous compteurs melanges - ce helper filtre).
    all_lots : tous les lots de l'ACP.
    start_date, end_date : bornes ISO "YYYY-MM-DD".
    fallback_key_lots : lots de la cle de repli (schema {lot_id, share, excluded}).
    """
    # 1. Filtrer les compteurs par type et n'utiliser que les divisionnaires
    mtype_meters = [
        m for m in (meters or [])
        if (m.get("meter_type") or "") == meter_type and (m.get("lot_id") or "")
    ]
    meters_by_lot: dict = {}
    for m in mtype_meters:
        lid = m.get("lot_id") or ""
        meters_by_lot.setdefault(lid, []).append(m)

    # 2. Indexer les releves par meter_id
    readings_by_meter: dict = {}
    for r in (readings or []):
        mid = r.get("meter_id") or ""
        if not mid:
            continue
        readings_by_meter.setdefault(mid, []).append(r)
    for mid, lst in readings_by_meter.items():
        lst.sort(key=lambda x: (x.get("date") or ""))

    # 3. Calculer la conso par lot sur la periode
    def _lot_consumption(lot_id: str) -> float:
        total = 0.0
        for m in meters_by_lot.get(lot_id, []):
            rs = readings_by_meter.get(m.get("id") or "", [])
            # a) bornes : chercher le dernier releve <= start_date
            #    et le dernier releve <= end_date
            r_start_idx = None
            r_end_idx = None
            for r in rs:
                d = (r.get("date") or "")[:10]
                if not d:
                    continue
                if d <= start_date:
                    r_start_idx = float(r.get("value") or 0)
                if d <= end_date:
                    r_end_idx = float(r.get("value") or 0)
            if r_start_idx is not None and r_end_idx is not None and r_end_idx >= r_start_idx:
                total += r_end_idx - r_start_idx
                continue
            # b) fallback : somme des `consumption` sur la periode
            for r in rs:
                d = (r.get("date") or "")[:10]
                if not d:
                    continue
                if _iso_date_between(d, start_date, end_date):
                    total += float(r.get("consumption") or 0)
        return round(total, 3)

    # 4. Repartir sur les lots
    fallback_map: dict = {}
    if fallback_key_lots:
        for fl in fallback_key_lots:
            if fl.get("excluded"):
                continue
            fallback_map[fl.get("lot_id") or ""] = float(fl.get("share") or 0)

    result_lots = []
    with_meter = 0
    via_fallback = 0
    excluded = 0
    total_meter_conso = 0.0
    for lot in (all_lots or []):
        lid = lot.get("id") or ""
        conso = _lot_consumption(lid) if lid in meters_by_lot else 0.0
        if conso > 0:
            with_meter += 1
            total_meter_conso += conso
            result_lots.append({
                "lot_id": lid,
                "lot_number": lot.get("number") or "",
                "share": conso,
                "source": "meter",
            })
        elif lid in fallback_map and fallback_map[lid] > 0:
            via_fallback += 1
            result_lots.append({
                "lot_id": lid,
                "lot_number": lot.get("number") or "",
                "share": fallback_map[lid],
                "source": "fallback",
            })
        else:
            excluded += 1

    total = sum(l["share"] for l in result_lots)
    return {
        "lots": result_lots,
        "total": round(total, 3),
        "coverage": {
            "with_meter_reading": with_meter,
            "via_fallback": via_fallback,
            "excluded": excluded,
        },
        "total_meter_consumption": round(total_meter_conso, 3),
    }
