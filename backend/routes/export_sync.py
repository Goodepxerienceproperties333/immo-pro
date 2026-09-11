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

        Les quotites sont calculees a partir des `distribution_keys` (cles de
        repartition PCMN), qui sont la source de verite du reglement de
        copropriete. Chaque lot expose :
        - `quotity_founder` : quotite fondatrice (millemes du titre initial)
        - `quotity_default` : part exacte sur la cle marquee `is_default`
          (typiquement "Charges generales"), ou `null` si le lot n'y figure pas
        - `quotities` : detail exhaustif { key_name, is_default, share } pour
          toutes les cles de repartition non-excluantes ou le lot est inscrit

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
                {
                  "id": "...", "number": "1A", "description": "...",
                  "type": "apartment", "floor": 1, "area": 85.0,
                  "quotity_founder": 125.5,
                  "quotity_default": 130.0,
                  "quotities": [
                    {"key_name": "Charges generales", "is_default": true, "share": 130.0},
                    {"key_name": "Ascenseur", "is_default": false, "share": 145.0}
                  ]
                }
              ],
              "total_quotity_default": 130.0,
              "total_quotity_founder": 125.5
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
        lots_raw: List[dict] = []
        async for lot in lots_cursor:
            lots_raw.append(lot)

        # 2bis. Recupere toutes les cles de repartition de l'ACP et indexe par lot_id.
        # C'est la SOURCE DE VERITE des quotites : le champ `lot.quotity` n'est
        # que la quotite fondatrice (millemes), tandis que `distribution_keys`
        # contient les vraies parts par cle (charges generales, ascenseur, etc.).
        keys_cursor = db.distribution_keys.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "id": 1, "name": 1, "is_default": 1, "lots": 1},
        )
        # shares_by_lot : lot_id -> list of {key_id, key_name, is_default, share}
        shares_by_lot: dict[str, List[dict]] = {}
        async for k in keys_cursor:
            key_id = k.get("id", "")
            key_name = (k.get("name") or "").strip()
            is_default = bool(k.get("is_default", False))
            for kl in (k.get("lots") or []):
                if kl.get("excluded"):
                    continue
                lid = kl.get("lot_id")
                if not lid:
                    continue
                share = float(kl.get("share", 0) or 0)
                shares_by_lot.setdefault(lid, []).append({
                    "key_id": key_id,
                    "key_name": key_name,
                    "is_default": is_default,
                    "share": round(share, 4),
                })

        # Construit map owner_id -> [lots_enrichis avec quotites detaillees]
        lots_by_owner: dict[str, List[dict]] = {}
        owner_ids: set[str] = set()
        for lot in lots_raw:
            lot_owners = []
            if lot.get("owner_id"):
                lot_owners.append(lot["owner_id"])
            for oid in lot.get("owner_ids") or []:
                if oid and oid not in lot_owners:
                    lot_owners.append(oid)

            quotities = shares_by_lot.get(lot["id"], [])
            # Tri : cle par defaut en premier, puis alphabetique
            quotities_sorted = sorted(quotities, key=lambda q: (not q["is_default"], q["key_name"].lower()))
            # Quotite "par defaut" (cle marquee is_default) - facilite les
            # integrations qui n'ont besoin que d'une valeur unique.
            default_share = next((q["share"] for q in quotities_sorted if q["is_default"]), None)

            lot_data = {
                "id": lot.get("id", ""),
                "number": lot.get("number", ""),
                "description": lot.get("description", "") or "",
                "type": lot.get("lot_type", "") or "",
                "floor": lot.get("floor", 0) or 0,
                "area": float(lot.get("area", 0) or 0),
                # Quotite fondatrice (millemes) inscrite au reglement de copro.
                "quotity_founder": float(lot.get("quotity", 0) or 0),
                # Part exacte sur la cle par defaut (source PCMN de repartition
                # des charges generales). None si aucune cle par defaut ne
                # comporte ce lot.
                "quotity_default": default_share,
                # Detail complet : une entree par cle de repartition ou le lot
                # est present et non exclu. `share` = part exacte de la cle.
                "quotities": quotities_sorted,
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
            # Total exacte des parts sur la cle de repartition par defaut
            # (typiquement "Charges generales"). Ne totalise que les lots pour
            # lesquels la cle par defaut definit une part explicite.
            total_default = round(
                sum(l["quotity_default"] for l in lots if l["quotity_default"] is not None),
                4,
            )
            # Total des quotites fondatrices (millemes) - reste dispo pour les
            # integrations legacy qui s'appuyaient sur `lot.quotity`.
            total_founder = round(sum(l["quotity_founder"] for l in lots), 4)
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
                "total_quotity_default": total_default,
                "total_quotity_founder": total_founder,
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
