"""iter95d : API sync securisee pour l'export des proprietaires d'une ACP.

Endpoint dedie a la synchronisation externe (ex. CRM, outil marketing,
plateforme SMS). Contrairement aux exports Excel/PDF humains, cet
endpoint renvoie du JSON pret a etre consomme par un script (n8n, Zapier,
Make, cron interne, etc.).

Securite :
- Header `X-Sync-Token` obligatoire.
- Valeur lue depuis la variable d'environnement `EXPORT_SYNC_TOKEN`.
- Comparaison en temps constant (hmac.compare_digest) pour empecher les
  attaques par timing.
- Aucun cookie / JWT / session : pas de trace utilisateur, pas de risque
  de fuite via CSRF.
- Si la variable env n'est PAS definie, l'endpoint renvoie 503 (l'export
  est desactive tant que le secret n'est pas configure).
"""
import hmac
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header


def create_export_sync_router(db):
    router = APIRouter(prefix="/api/export")

    def _check_token(x_sync_token: Optional[str]) -> None:
        expected = os.environ.get("EXPORT_SYNC_TOKEN", "")
        if not expected:
            # Secret non configure -> endpoint indisponible plutot que d'ouvrir un trou.
            raise HTTPException(
                503,
                "Export sync desactive : la variable d'environnement "
                "EXPORT_SYNC_TOKEN n'est pas definie.",
            )
        provided = x_sync_token or ""
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(401, "Token de synchronisation invalide")

    def _full_address(o: dict) -> str:
        parts = [
            (o.get("address") or "").strip(),
            " ".join(
                p for p in [
                    (o.get("postal_code") or "").strip(),
                    (o.get("city") or "").strip(),
                ] if p
            ).strip(),
            (o.get("country") or "").strip(),
        ]
        return ", ".join(p for p in parts if p)

    @router.get("/owners/{copropriete_id}")
    async def export_owners(
        copropriete_id: str,
        x_sync_token: Optional[str] = Header(None, alias="X-Sync-Token"),
    ):
        """Retourne la liste JSON des proprietaires d'une ACP avec leurs lots.

        Reponse (schema stable pour integrations externes) :
        ```
        {
          "copropriete_id": "...",
          "copropriete_name": "...",
          "count": 3,
          "owners": [
            {
              "id": "...",
              "civility": "M.",
              "first_name": "Jean",
              "last_name": "Dupont",
              "email": "jean@ex.be",
              "email2": "",
              "phone": "+3247512...",   # GSM principal
              "phone2": "",
              "address": {
                "street": "Rue X 12",
                "postal_code": "1000",
                "city": "Bruxelles",
                "country": "Belgique",
                "full": "Rue X 12, 1000 Bruxelles, Belgique"
              },
              "lots": [
                {"id": "...", "number": "1A", "description": "...",
                 "lot_type": "apartment", "floor": 1, "area": 85.0,
                 "quotity": 125.5}
              ],
              "total_quotity": 125.5
            }
          ]
        }
        ```
        """
        _check_token(x_sync_token)

        # 1. Verifie que la copropriete existe (evite les 200 vides silencieux)
        copro = await db.coproprietes.find_one(
            {"id": copropriete_id},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not copro:
            raise HTTPException(404, "Copropriete introuvable")

        # 2. Recupere tous les lots de l'ACP -> map owner_id -> [lots]
        lots_cursor = db.lots.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "id": 1, "number": 1, "description": 1,
             "lot_type": 1, "floor": 1, "area": 1, "quotity": 1,
             "owner_id": 1, "owner_ids": 1},
        )
        lots_by_owner: dict[str, List[dict]] = {}
        owner_ids: set[str] = set()
        async for lot in lots_cursor:
            lot_owners = []
            if lot.get("owner_id"):
                lot_owners.append(lot["owner_id"])
            for oid in lot.get("owner_ids") or []:
                if oid and oid not in lot_owners:
                    lot_owners.append(oid)
            lot_data = {
                "id": lot.get("id", ""),
                "number": lot.get("number", ""),
                "description": lot.get("description", "") or "",
                "lot_type": lot.get("lot_type", "") or "",
                "floor": lot.get("floor", 0) or 0,
                "area": float(lot.get("area", 0) or 0),
                "quotity": float(lot.get("quotity", 0) or 0),
            }
            for oid in lot_owners:
                lots_by_owner.setdefault(oid, []).append(lot_data)
                owner_ids.add(oid)

        if not owner_ids:
            return {
                "copropriete_id": copropriete_id,
                "copropriete_name": copro.get("name", ""),
                "count": 0,
                "owners": [],
            }

        # 3. Recupere les proprietaires correspondants
        owners_cursor = db.owners.find(
            {"id": {"$in": list(owner_ids)}},
            {"_id": 0, "id": 1, "civility": 1, "first_name": 1,
             "last_name": 1, "name": 1, "email": 1, "email2": 1,
             "phone": 1, "phone2": 1, "address": 1,
             "postal_code": 1, "city": 1, "country": 1},
        )
        result_owners: List[dict] = []
        async for o in owners_cursor:
            # Fallback : si first/last vides mais "name" existe (legacy), splitte.
            fn = (o.get("first_name") or "").strip()
            ln = (o.get("last_name") or "").strip()
            if not fn and not ln and o.get("name"):
                parts = (o.get("name") or "").strip().split(None, 1)
                fn = parts[0] if parts else ""
                ln = parts[1] if len(parts) > 1 else ""
            lots = lots_by_owner.get(o["id"], [])
            total_q = round(sum(l["quotity"] for l in lots), 4)
            result_owners.append({
                "id": o["id"],
                "civility": o.get("civility", "") or "",
                "first_name": fn,
                "last_name": ln,
                "email": o.get("email", "") or "",
                "email2": o.get("email2", "") or "",
                "phone": o.get("phone", "") or "",
                "phone2": o.get("phone2", "") or "",
                "address": {
                    "street": o.get("address", "") or "",
                    "postal_code": o.get("postal_code", "") or "",
                    "city": o.get("city", "") or "",
                    "country": o.get("country", "") or "",
                    "full": _full_address(o),
                },
                "lots": lots,
                "total_quotity": total_q,
            })

        # Tri stable par nom pour un output deterministe (facilite les diffs)
        result_owners.sort(key=lambda x: (x["last_name"].lower(), x["first_name"].lower()))

        return {
            "copropriete_id": copropriete_id,
            "copropriete_name": copro.get("name", ""),
            "count": len(result_owners),
            "owners": result_owners,
        }

    return router
