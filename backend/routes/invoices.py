from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
from auto_entries import generate_purchase_entry, _delete_auto_entries
from gridfs_storage import (
    get_invoice_attachments_storage,
    get_invoice_bundles_storage,
)

# Legacy paths kept ONLY for backward-compat reads of pre-iter87 attachments.
# All new writes go to GridFS. The migration script `migrate_uploads_to_gridfs.py`
# moves legacy files into GridFS once for all.
INVOICE_ATTACHMENTS_DIR = Path("/app/uploads/invoice_attachments")
INVOICE_BUNDLES_DIR = Path("/app/uploads/invoice_attachments/_bundles")
try:
    INVOICE_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    # Filesystem may be read-only in production - GridFS doesn't need it
    pass


class DistKeyLot(BaseModel):
    lot_id: str
    lot_number: str
    share: float
    # iter90ac : exclusion explicite. Un lot exclu ne participe pas au calcul
    # de cette cle (ex : lot commercial exclu des charges d'ascenseur).
    # Compatible avec les donnees existantes (defaut False = comportement inchange).
    excluded: bool = False


class DistKeyInput(BaseModel):
    name: str
    description: Optional[str] = ""
    key_type: Optional[str] = "quotity"  # quotity, equal, custom
    lots: Optional[List[DistKeyLot]] = []
    copropriete_id: Optional[str] = ""
    # iter88 : numero alphanumerique unique par ACP (optionnel mais recommande)
    # Permet de classer les cles (ex: "001 - General", "002 - Ascenseur").
    code: Optional[str] = ""
    # iter88 : clé par defaut pour cette ACP. Une seule peut etre True par ACP.
    # Utilisee comme fallback quand une nature de depense n'a pas de cle
    # explicite et que la facture n'en specifie pas non plus.
    is_default: Optional[bool] = False


class InvoiceLineInput(BaseModel):
    """Une ligne de detail de facture (split entre plusieurs natures/comptes).
    Si lines=[] (ou None) sur la facture, on reste en mode 1-ligne legacy.
    iter90ey : occupant_pct/proprietaire_pct optionnels au niveau ligne.
    Si None -> herite de la facture (occupant_pct global), qui elle-meme
    herite eventuellement de la nature.
    """
    account_number: str
    expense_category_id: Optional[str] = ""
    distribution_key_id: Optional[str] = ""
    amount: float
    description: Optional[str] = ""
    occupant_pct: Optional[float] = None
    proprietaire_pct: Optional[float] = None


class PrivateFeeAllocation(BaseModel):
    """Repartition d'un frais privatif sur un proprietaire.
    Plusieurs allocations -> la facture est imputee a N proprietaires avec
    leurs montants respectifs (somme = total_amount).
    """
    owner_id: str
    amount: float


class InvoiceInput(BaseModel):
    number: str
    date: str
    due_date: Optional[str] = ""
    supplier: str
    description: str
    total_amount: float
    vat_amount: Optional[float] = 0.0
    account_number: Optional[str] = ""
    expense_category_id: Optional[str] = ""
    distribution_key_id: Optional[str] = ""
    status: Optional[str] = "unpaid"
    copropriete_id: Optional[str] = ""
    # Frais privatifs: la facture est imputee a UN OU PLUSIEURS proprietaires
    # via le compte 643. Si is_private_fee=true, distribution_key_id est ignore
    # et account_number force a 643.
    # - private_fee_owner_id (legacy single-owner) : conserve pour retrocompat
    # - private_fee_allocations (iter85e) : repartition multi-owners avec
    #   montants fixes en EUR (somme = total_amount, tolerance 0.01)
    is_private_fee: Optional[bool] = False
    private_fee_owner_id: Optional[str] = ""
    private_fee_allocations: Optional[List[PrivateFeeAllocation]] = None
    # Repartition occupant/proprietaire (pour decompte locataire).
    # Defaut : herite de la catégorie de dépense si non fourni.
    # Somme doit etre 100.
    occupant_pct: Optional[float] = None  # None = inherit from category
    proprietaire_pct: Optional[float] = None
    # Lignes multiples (split par nature de depense). Si fourni et non-vide,
    # le total des lignes doit egal total_amount. Une ecriture comptable
    # unique sera generee avec N debits (un par ligne) + 1 credit fournisseur.
    lines: Optional[List[InvoiceLineInput]] = None


def create_invoices_router(db):
    router = APIRouter(prefix="/api")

    # ---- DISTRIBUTION KEYS ----
    @router.get("/distribution-keys")
    async def list_dist_keys(copropriete_id: Optional[str] = None):
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        # iter88 : tri par code (si present) puis par name
        keys = await db.distribution_keys.find(q, {"_id": 0}).to_list(1000)
        keys.sort(key=lambda k: ((k.get("code") or "~~~"), k.get("name", "")))
        return keys

    async def _validate_code_uniqueness(copro_id: str, code: str, exclude_id: str = ""):
        """iter88 : verifie qu'aucune autre cle de cette ACP n'a deja ce code."""
        if not code:
            return
        q = {"copropriete_id": copro_id, "code": code}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        clash = await db.distribution_keys.find_one(q, {"_id": 0, "id": 1, "name": 1})
        if clash:
            raise HTTPException(
                409, f"Le numero '{code}' est deja utilise par la cle '{clash.get('name', '')}' dans cette ACP."
            )

    async def _unset_other_defaults(copro_id: str, exclude_id: str = ""):
        """iter88 : passe is_default=False sur toutes les autres cles de l'ACP."""
        q = {"copropriete_id": copro_id, "is_default": True}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        await db.distribution_keys.update_many(q, {"$set": {"is_default": False}})

    @router.post("/distribution-keys")
    async def create_dist_key(data: DistKeyInput):
        code = (data.code or "").strip()
        copro_id = data.copropriete_id or ""
        await _validate_code_uniqueness(copro_id, code)
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "description": data.description,
            "key_type": data.key_type,
            "lots": [l.model_dump() for l in data.lots],
            "copropriete_id": copro_id,
            "code": code,
            "is_default": bool(data.is_default),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.distribution_keys.insert_one(doc)
        if doc["is_default"]:
            await _unset_other_defaults(copro_id, exclude_id=doc["id"])
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/distribution-keys/{key_id}/usage")
    async def dist_key_usage(key_id: str):
        """Return list of resources linked to this distribution key."""
        invoices = await db.invoices.find({"distribution_key_id": key_id}, {"_id": 0, "id": 1, "number": 1, "date": 1, "supplier": 1, "total_amount": 1}).to_list(10000)
        budgets = await db.budgets.find({"lines.distribution_key_id": key_id}, {"_id": 0, "id": 1, "name": 1, "status": 1}).to_list(1000)
        fund_calls = await db.fund_calls.find({"distribution_key_id": key_id}, {"_id": 0, "id": 1, "name": 1, "date": 1, "total_amount": 1}).to_list(1000)
        return {
            "invoices": invoices,
            "budgets": budgets,
            "fund_calls": fund_calls,
            "total": len(invoices) + len(budgets) + len(fund_calls),
        }

    @router.put("/distribution-keys/{key_id}")
    async def update_dist_key(key_id: str, data: DistKeyInput, force: Optional[bool] = False):
        """Update a key. If `force=true`, detach all invoices using this key first.
        Otherwise returns 409 if invoices are linked."""
        existing = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Cle non trouvee")
        linked_inv = await db.invoices.count_documents({"distribution_key_id": key_id})
        if linked_inv > 0 and not force:
            raise HTTPException(
                409,
                f"{linked_inv} facture(s) utilisent cette cle. Detachez-les avant ou utilisez ?force=true",
            )
        code = (data.code or "").strip()
        copro_id = existing.get("copropriete_id", "") or (data.copropriete_id or "")
        await _validate_code_uniqueness(copro_id, code, exclude_id=key_id)
        update = {
            "name": data.name, "description": data.description,
            "key_type": data.key_type, "lots": [l.model_dump() for l in data.lots],
            "code": code,
            "is_default": bool(data.is_default),
        }
        await db.distribution_keys.update_one({"id": key_id}, {"$set": update})
        if update["is_default"]:
            await _unset_other_defaults(copro_id, exclude_id=key_id)
        if force and linked_inv > 0:
            # Detach: set distribution_key_id="" and clear distribution_lines
            await db.invoices.update_many(
                {"distribution_key_id": key_id},
                {"$set": {"distribution_key_id": "", "distribution_lines": []}}
            )
        return {"updated": True, "detached_invoices": linked_inv if force else 0,
                "key": await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})}

    @router.post("/distribution-keys/{key_id}/set-default")
    async def set_default_dist_key(key_id: str):
        """iter88 : marque cette cle comme cle par defaut pour son ACP.
        Toutes les autres cles de la meme ACP repassent a is_default=False.
        Endpoint dedie : ne modifie que le flag is_default (pas de validation
        sur facture liees, contrairement au PUT classique)."""
        existing = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Cle non trouvee")
        copro_id = existing.get("copropriete_id", "")
        await _unset_other_defaults(copro_id, exclude_id=key_id)
        await db.distribution_keys.update_one({"id": key_id}, {"$set": {"is_default": True}})
        return {"updated": True, "key_id": key_id}

    @router.post("/distribution-keys/{key_id}/unset-default")
    async def unset_default_dist_key(key_id: str):
        """iter88 : retire le flag default (l'ACP n'a plus de cle par defaut)."""
        existing = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Cle non trouvee")
        await db.distribution_keys.update_one({"id": key_id}, {"$set": {"is_default": False}})
        return {"updated": True, "key_id": key_id}

    @router.post("/distribution-keys/{key_id}/rebuild")
    async def rebuild_dist_key(key_id: str, data: dict):
        """iter90cs : Reconstruit une cle de repartition dont les entrees sont
        phantom (lot_ids ne correspondant plus a aucun lot en DB) ou obsoletes.

        Modes :
        - `mode='quotity'` (defaut) : rebuild complet - les `lots` de la cle
          sont remplaces par TOUS les lots actuels de l'ACP, avec `share`
          egal a `quotity` de chaque lot. Total = somme des quotites (peut
          differer de 10000 selon le parametrage). Simple et safe.
        - `mode='match_by_number'` : pour chaque entree phantom, essaie de
          matcher par `lot_number` (si stocke sur l'entree du key). Les entrees
          matchees gardent leur `share` originale. Les non-matches sont
          supprimes, les lots actuels manquants sont ajoutes avec `share=quotity`.
        - `mode='clean_only'` : supprime seulement les entrees phantom
          (sans ajouter les lots manquants). Utile si l'utilisateur veut
          juste nettoyer sans reallouer.

        dry_run=True : retourne le rapport de ce qui SERAIT fait.
        dry_run=False : execute la mise a jour de la cle.

        Requiert Chinese wall via _get_user_scope.
        """
        from datetime import datetime, timezone
        existing = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Cle non trouvee")

        copro_id = existing.get("copropriete_id", "")
        mode = (data.get("mode") or "quotity").strip()
        if mode not in ("quotity", "match_by_number", "clean_only"):
            raise HTTPException(400, f"mode invalide : {mode}")
        dry_run = bool(data.get("dry_run", True))

        # Chinese wall
        req = data.get("_request")  # optionnel : injected par test
        is_super, allowed = True, [copro_id]
        try:
            from routes.utils_scope import _get_user_scope
            is_super, allowed = await _get_user_scope(req) if req else (True, [copro_id])
        except Exception:
            pass
        if not is_super and copro_id not in (allowed or []):
            raise HTTPException(403, "Chinese wall : acces refuse")

        # Lots actuels de l'ACP
        lots = await db.lots.find(
            {"copropriete_id": copro_id}, {"_id": 0},
        ).to_list(5000)
        lots_by_id = {lt["id"]: lt for lt in lots}
        lots_by_number = {str(lt.get("number", "")): lt for lt in lots}

        # Analyse des entrees actuelles
        current_lots_entries = existing.get("lots") or []
        phantom_entries = []
        valid_entries = []
        for kle in current_lots_entries:
            if kle.get("excluded"):
                continue
            lid = kle.get("lot_id")
            if not lid or lid not in lots_by_id:
                phantom_entries.append(kle)
            else:
                valid_entries.append(kle)

        # Construction du nouveau tableau de lots
        new_lots_entries = []
        stats = {
            "phantom_removed": 0,
            "phantom_matched_by_number": 0,
            "current_lots_added": 0,
            "existing_preserved": 0,
        }

        if mode == "clean_only":
            # Nettoie phantoms uniquement, garde le reste tel quel
            for kle in current_lots_entries:
                lid = kle.get("lot_id")
                if not lid or lid not in lots_by_id:
                    stats["phantom_removed"] += 1
                    continue
                new_lots_entries.append(kle)
                stats["existing_preserved"] += 1

        elif mode == "match_by_number":
            # Preserve valid entries + tente de rebrancher les phantoms par
            # lot_number si celui-ci est stocke sur l'entree du key.
            used_lot_ids = set()
            for kle in valid_entries:
                new_lots_entries.append(kle)
                used_lot_ids.add(kle.get("lot_id"))
                stats["existing_preserved"] += 1
            for kle in phantom_entries:
                lot_number = str(kle.get("lot_number") or "")
                matched = lots_by_number.get(lot_number)
                if matched and matched["id"] not in used_lot_ids:
                    new_lots_entries.append({
                        "lot_id": matched["id"],
                        "share": float(kle.get("share", 0) or 0),
                        "lot_number": matched.get("number", ""),
                    })
                    used_lot_ids.add(matched["id"])
                    stats["phantom_matched_by_number"] += 1
                else:
                    stats["phantom_removed"] += 1
            # Ajout des lots actuels non couverts
            for lot in lots:
                if lot["id"] in used_lot_ids:
                    continue
                new_lots_entries.append({
                    "lot_id": lot["id"],
                    "share": float(lot.get("quotity", 0) or 0),
                    "lot_number": lot.get("number", ""),
                })
                stats["current_lots_added"] += 1

        else:  # mode == 'quotity' (defaut)
            # Rebuild complet : remplace TOUT par lots actuels + quotites
            stats["phantom_removed"] = len(phantom_entries)
            stats["existing_preserved"] = 0  # tout est reconstruit
            for lot in lots:
                new_lots_entries.append({
                    "lot_id": lot["id"],
                    "share": float(lot.get("quotity", 0) or 0),
                    "lot_number": lot.get("number", ""),
                })
                stats["current_lots_added"] += 1

        old_total = round(
            sum(float(kle.get("share", 0) or 0) for kle in current_lots_entries), 4,
        )
        new_total = round(
            sum(float(kle.get("share", 0) or 0) for kle in new_lots_entries), 4,
        )

        response = {
            "dry_run": dry_run,
            "key_id": key_id,
            "key_name": existing.get("name", ""),
            "mode": mode,
            "before": {
                "entries_total": len(current_lots_entries),
                "phantom_count": len(phantom_entries),
                "valid_count": len(valid_entries),
                "total_share": old_total,
            },
            "after": {
                "entries_total": len(new_lots_entries),
                "total_share": new_total,
            },
            "stats": stats,
            "sample_new_entries": new_lots_entries[:5],
        }

        if not dry_run:
            await db.distribution_keys.update_one(
                {"id": key_id},
                {"$set": {
                    "lots": new_lots_entries,
                    "iter90cs_rebuilt_at": datetime.now(timezone.utc).isoformat(),
                    "iter90cs_rebuild_mode": mode,
                }},
            )
            response["applied"] = True

        return response

    @router.post("/distribution-keys/bulk-rebuild")
    async def bulk_rebuild_dist_keys(data: dict):
        """iter90du : Detecte et repare toutes les cles avec entrees phantom
        pour une ACP donnee (ou toutes les ACPs d'un superadmin).

        Une cle est consideree "phantom" si AU MOINS UNE de ses entrees actives
        pointe vers un lot_id qui n'existe plus en DB.

        Input :
          {
            "copropriete_id": str (optional - si omis, scanne toutes les ACPs
              accessibles a l'user),
            "mode": "match_by_number" (defaut) | "quotity" | "clean_only",
            "dry_run": bool (defaut True),
            "key_ids": list[str] (optional - si fourni, ne rebuild que ces cles)
          }

        Return :
          {
            "keys_scanned": int,
            "keys_with_phantoms": int,
            "keys_rebuilt": int,
            "details": [
              {
                "key_id", "key_name", "phantom_count",
                "before_total_share", "after_total_share",
                "stats": {...},
                "applied": bool,
              }
            ],
          }
        """
        copro_id = (data.get("copropriete_id") or "").strip()
        mode = (data.get("mode") or "match_by_number").strip()
        if mode not in ("quotity", "match_by_number", "clean_only"):
            raise HTTPException(400, f"mode invalide : {mode}")
        dry_run = bool(data.get("dry_run", True))
        filter_key_ids = set(data.get("key_ids") or [])

        # Query keys (filtered by ACP if provided)
        q = {}
        if copro_id:
            q["copropriete_id"] = copro_id
        keys = await db.distribution_keys.find(q, {"_id": 0}).to_list(5000)

        details = []
        keys_with_phantoms = 0
        keys_rebuilt = 0

        # Group lots by copro to minimize DB calls
        lots_cache: dict = {}  # copro_id -> {lots_by_id, lots_by_number, all_lots}

        async def _get_lots(cid: str):
            if cid in lots_cache:
                return lots_cache[cid]
            lots = await db.lots.find(
                {"copropriete_id": cid}, {"_id": 0},
            ).to_list(5000)
            lots_by_id = {lt["id"]: lt for lt in lots}
            lots_by_number = {str(lt.get("number", "")): lt for lt in lots}
            lots_cache[cid] = {
                "all_lots": lots,
                "lots_by_id": lots_by_id,
                "lots_by_number": lots_by_number,
            }
            return lots_cache[cid]

        from datetime import datetime, timezone

        for existing in keys:
            key_id = existing.get("id", "")
            if filter_key_ids and key_id not in filter_key_ids:
                continue

            k_copro = existing.get("copropriete_id", "")
            if not k_copro:
                continue

            cache = await _get_lots(k_copro)
            lots_by_id = cache["lots_by_id"]
            lots_by_number = cache["lots_by_number"]
            all_lots = cache["all_lots"]

            current_lots_entries = existing.get("lots") or []
            phantom_entries = []
            valid_entries = []
            for kle in current_lots_entries:
                if kle.get("excluded"):
                    continue
                lid = kle.get("lot_id")
                if not lid or lid not in lots_by_id:
                    phantom_entries.append(kle)
                else:
                    valid_entries.append(kle)

            if not phantom_entries:
                continue  # cle propre, skip

            keys_with_phantoms += 1

            # Reproduit la logique de rebuild_dist_key pour ce mode
            new_lots_entries = []
            stats = {
                "phantom_removed": 0,
                "phantom_matched_by_number": 0,
                "current_lots_added": 0,
                "existing_preserved": 0,
            }

            if mode == "clean_only":
                for kle in current_lots_entries:
                    lid = kle.get("lot_id")
                    if not lid or lid not in lots_by_id:
                        stats["phantom_removed"] += 1
                        continue
                    new_lots_entries.append(kle)
                    stats["existing_preserved"] += 1

            elif mode == "match_by_number":
                used_lot_ids = set()
                for kle in valid_entries:
                    new_lots_entries.append(kle)
                    used_lot_ids.add(kle.get("lot_id"))
                    stats["existing_preserved"] += 1
                for kle in phantom_entries:
                    lot_number = str(kle.get("lot_number") or "")
                    matched = lots_by_number.get(lot_number)
                    if matched and matched["id"] not in used_lot_ids:
                        new_lots_entries.append({
                            "lot_id": matched["id"],
                            "share": float(kle.get("share", 0) or 0),
                            "lot_number": matched.get("number", ""),
                        })
                        used_lot_ids.add(matched["id"])
                        stats["phantom_matched_by_number"] += 1
                    else:
                        stats["phantom_removed"] += 1
                # Ajoute les lots actuels non couverts avec share=0
                # (n'affecte pas les distributions existantes, evite les shares
                # inventees). Utilisateur peut ensuite editer manuellement.
                for lot in all_lots:
                    if lot["id"] in used_lot_ids:
                        continue
                    new_lots_entries.append({
                        "lot_id": lot["id"],
                        "share": 0.0,
                        "lot_number": lot.get("number", ""),
                    })
                    stats["current_lots_added"] += 1

            else:  # mode == 'quotity'
                stats["phantom_removed"] = len(phantom_entries)
                for lot in all_lots:
                    new_lots_entries.append({
                        "lot_id": lot["id"],
                        "share": float(lot.get("quotity", 0) or 0),
                        "lot_number": lot.get("number", ""),
                    })
                    stats["current_lots_added"] += 1

            old_total = round(
                sum(float(kle.get("share", 0) or 0) for kle in current_lots_entries), 4,
            )
            new_total = round(
                sum(float(kle.get("share", 0) or 0) for kle in new_lots_entries), 4,
            )

            applied = False
            if not dry_run:
                await db.distribution_keys.update_one(
                    {"id": key_id},
                    {"$set": {
                        "lots": new_lots_entries,
                        "iter90cs_rebuilt_at": datetime.now(timezone.utc).isoformat(),
                        "iter90cs_rebuild_mode": mode,
                    }},
                )
                keys_rebuilt += 1
                applied = True

            details.append({
                "key_id": key_id,
                "key_name": existing.get("name", ""),
                "copropriete_id": k_copro,
                "phantom_count": len(phantom_entries),
                "before_total_share": old_total,
                "after_total_share": new_total,
                "stats": stats,
                "applied": applied,
            })

        return {
            "dry_run": dry_run,
            "mode": mode,
            "keys_scanned": len(keys),
            "keys_with_phantoms": keys_with_phantoms,
            "keys_rebuilt": keys_rebuilt,
            "details": details,
        }

    @router.delete("/distribution-keys/{key_id}")
    async def delete_dist_key(key_id: str):
        linked_inv = await db.invoices.count_documents({"distribution_key_id": key_id})
        if linked_inv > 0:
            raise HTTPException(400, f"{linked_inv} facture(s) liees - detachez-les d'abord")
        result = await db.distribution_keys.delete_one({"id": key_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Cle non trouvee")
        return {"message": "Cle supprimee"}

    @router.post("/invoices/repair-phantom-distribution-lines")
    async def repair_phantom_distribution_lines(data: dict):
        """iter90dz : Repare retroactivement les invoice.distribution_lines
        qui pointent vers des lot_ids phantoms.

        Pour chaque facture d'une ACP, matche les entrees phantom aux lots
        actuels par `lot_number` normalise et met a jour le `lot_id`.

        Input :
          {
            "copropriete_id": str (optional - sinon toutes les ACPs),
            "dry_run": bool (defaut True),
          }

        Return summary : nombre de factures scannees, factures avec phantoms,
        distributions_lines reparees, details par facture.
        """
        copro_id = (data.get("copropriete_id") or "").strip()
        dry_run = bool(data.get("dry_run", True))

        def _norm_num(s: str) -> str:
            return (str(s or "")).strip().lstrip("0") or "0"

        # Load invoices
        q = {}
        if copro_id:
            q["copropriete_id"] = copro_id
        invoices = await db.invoices.find(q, {"_id": 0}).to_list(50000)

        # Cache lots by copro
        lots_cache: dict = {}  # cid -> {ids: set, by_number: {norm_num: id}}

        async def _get_lots(cid: str):
            if cid in lots_cache:
                return lots_cache[cid]
            lots = await db.lots.find(
                {"copropriete_id": cid}, {"_id": 0, "id": 1, "number": 1},
            ).to_list(5000)
            lots_cache[cid] = {
                "ids": {lt["id"] for lt in lots},
                "by_number": {_norm_num(lt.get("number", "")): lt["id"] for lt in lots},
            }
            return lots_cache[cid]

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        details = []
        invoices_with_phantoms = 0
        invoices_repaired = 0
        lines_repaired_total = 0

        for inv in invoices:
            inv_cid = inv.get("copropriete_id", "")
            if not inv_cid:
                continue
            cache = await _get_lots(inv_cid)
            valid_ids = cache["ids"]
            by_num = cache["by_number"]

            distribution_lines = inv.get("distribution_lines") or []
            if not distribution_lines:
                continue

            new_lines = []
            phantom_count = 0
            resolved_count = 0
            unresolvable_count = 0

            for dl in distribution_lines:
                lot_id = dl.get("lot_id", "")
                if lot_id and lot_id in valid_ids:
                    new_lines.append(dl)
                    continue
                # Phantom detecte
                phantom_count += 1
                lot_num_key = _norm_num(dl.get("lot_number", ""))
                if lot_num_key and lot_num_key != "0" and lot_num_key in by_num:
                    new_dl = dict(dl)
                    new_dl["lot_id"] = by_num[lot_num_key]
                    new_dl["iter90dz_rebound_at"] = now_iso
                    new_dl["iter90dz_previous_lot_id"] = lot_id
                    new_lines.append(new_dl)
                    resolved_count += 1
                else:
                    # Aucun match - garde l'original (fallback runtime le gerera)
                    new_lines.append(dl)
                    unresolvable_count += 1

            if phantom_count == 0:
                continue

            invoices_with_phantoms += 1
            details.append({
                "invoice_id": inv.get("id", ""),
                "invoice_number": inv.get("number", ""),
                "copropriete_id": inv_cid,
                "phantom_count": phantom_count,
                "resolved_count": resolved_count,
                "unresolvable_count": unresolvable_count,
                "applied": False,
            })

            if not dry_run and resolved_count > 0:
                await db.invoices.update_one(
                    {"id": inv["id"]},
                    {"$set": {
                        "distribution_lines": new_lines,
                        "iter90dz_repaired_at": now_iso,
                    }},
                )
                invoices_repaired += 1
                lines_repaired_total += resolved_count
                details[-1]["applied"] = True

        return {
            "dry_run": dry_run,
            "invoices_scanned": len(invoices),
            "invoices_with_phantoms": invoices_with_phantoms,
            "invoices_repaired": invoices_repaired,
            "lines_repaired_total": lines_repaired_total,
            "details": details,
        }

    # ---- INVOICES ----
    @router.get("/invoices")
    async def list_invoices(
        request: Request,
        status: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        supplier: Optional[str] = None,
        reference: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ):
        """Chinese walls STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id). Sans scope ACP -> liste vide.
        Filtres optionnels : status, supplier (regex insensible), reference
        (regex insensible), start_date/end_date (ISO YYYY-MM-DD), min/max amount.
        Note: `date_from` et `date_to` sont des alias pour `start_date`/`end_date`."""
        # Alias compat
        start_date = start_date or date_from
        end_date = end_date or date_to
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        query = {"copropriete_id": copropriete_id}
        if status:
            query["status"] = status
        if supplier:
            import re as _re
            query["supplier"] = {"$regex": _re.escape(supplier), "$options": "i"}
        if reference:
            import re as _re2
            query["number"] = {"$regex": _re2.escape(reference), "$options": "i"}
        if start_date or end_date:
            query["date"] = {}
            if start_date:
                query["date"]["$gte"] = start_date
            if end_date:
                query["date"]["$lte"] = end_date
        if min_amount is not None or max_amount is not None:
            query["total_amount"] = {}
            if min_amount is not None:
                query["total_amount"]["$gte"] = float(min_amount)
            if max_amount is not None:
                query["total_amount"]["$lte"] = float(max_amount)
        invoices = await db.invoices.find(query, {"_id": 0}).sort("date", -1).to_list(2000)
        return invoices

    async def _resolve_invoice_lines(data: InvoiceInput) -> tuple:
        """Resolve and validate multi-line invoice payload.
        Returns (resolved_lines, merged_distribution_lines) where:
          - resolved_lines : list of dicts with normalized fields (account_number
            resolved from expense_category if applicable), or [] in single-line mode.
          - merged_distribution_lines : list of {lot_id, lot_number, owner_name,
            share, amount} aggregated across all lines that carry a distribution_key.
        Raises HTTPException if total mismatch or empty/invalid lines.
        """
        if not data.lines:
            return [], None
        if data.is_private_fee:
            raise HTTPException(400, "Une facture frais privatif ne peut pas etre splittee en lignes multiples")

        resolved = []
        total = 0.0
        for idx, ln in enumerate(data.lines, start=1):
            amt = float(ln.amount or 0)
            if amt <= 0:
                raise HTTPException(400, f"Ligne {idx}: montant doit etre > 0")
            acc = (ln.account_number or "").strip()
            # Si une categorie est fournie, derive le compte
            if ln.expense_category_id:
                cat = await db.expense_categories.find_one({"id": ln.expense_category_id}, {"_id": 0})
                if cat and cat.get("account_number"):
                    acc = cat["account_number"]
            if not acc:
                raise HTTPException(400, f"Ligne {idx}: compte PCMN requis")
            resolved.append({
                "account_number": acc,
                "expense_category_id": ln.expense_category_id or "",
                "distribution_key_id": ln.distribution_key_id or "",
                "amount": round(amt, 2),
                "description": ln.description or "",
                # iter90ey : occupant/proprietaire percentage per-line
                # (fallback vers facture globale si None).
                "occupant_pct": ln.occupant_pct,
                "proprietaire_pct": ln.proprietaire_pct,
            })
            total += amt

        if abs(round(total, 2) - round(float(data.total_amount or 0), 2)) > 0.01:
            raise HTTPException(
                400,
                f"Somme des lignes ({round(total, 2):.2f}) different du total facture "
                f"({round(float(data.total_amount or 0), 2):.2f})"
            )

        # Agrege les distribution_lines par lot (somme des amounts cles confondues)
        merged = {}  # lot_id -> {lot_number, owner_name, share, amount}
        for ln in resolved:
            if not ln["distribution_key_id"]:
                continue
            key = await db.distribution_keys.find_one({"id": ln["distribution_key_id"]}, {"_id": 0})
            if not key:
                continue
            # iter90ac : exclut les lots marques excluded=True
            active_kls = [l_ for l_ in key["lots"] if not l_.get("excluded")]
            total_shares = sum(l_["share"] for l_ in active_kls) if active_kls else 1
            for lot_entry in active_kls:
                lot_doc = await db.lots.find_one({"id": lot_entry["lot_id"]}, {"_id": 0})
                owner_name = ""
                if lot_doc and lot_doc.get("owner_id"):
                    owner_doc = await db.owners.find_one({"id": lot_doc["owner_id"]}, {"_id": 0})
                    owner_name = owner_doc["name"] if owner_doc else ""
                share_ratio = lot_entry["share"] / total_shares if total_shares > 0 else 0
                amt = round(ln["amount"] * share_ratio, 2)
                if lot_entry["lot_id"] in merged:
                    merged[lot_entry["lot_id"]]["amount"] = round(merged[lot_entry["lot_id"]]["amount"] + amt, 2)
                else:
                    merged[lot_entry["lot_id"]] = {
                        "lot_id": lot_entry["lot_id"],
                        "lot_number": lot_entry["lot_number"],
                        "owner_name": owner_name,
                        "share": lot_entry["share"],
                        "amount": amt,
                    }
        merged_list = list(merged.values())
        return resolved, merged_list

    def _norm(s: str) -> str:
        """Normalise une chaine pour comparaison anti-doublons : majuscules,
        sans accents, sans espaces multiples, sans dashes/spaces de bord."""
        if not s:
            return ""
        import unicodedata as _u
        n = _u.normalize("NFKD", s)
        n = "".join(c for c in n if not _u.combining(c))
        return " ".join(n.upper().split()).strip(" -.")

    async def _check_invoice_duplicate(data: InvoiceInput, exclude_id: str = "", allow_soft_duplicate: bool = False):
        """Anti-doublon strict, UNE seule regle (iter90bp) :
        - Meme (fournisseur normalise, numero normalise, ACP)
          -> facture certainement identique, HTTPException 409.

        iter90bp : la regle SOFT precedente (meme montant + meme date +/- 3j
        AVEC numeros differents) est SUPPRIMEE a la demande du user. Deux
        factures avec des numeros differents ne sont jamais des doublons,
        meme si montant/date coincident (cas legitime : abonnements
        recurrents, achats identiques a jours differents, etc.).

        Le parametre `allow_soft_duplicate` (et `?force=true` cote API) sont
        conserves pour la compatibilite mais n'ont plus d'effet.

        Le N° interne FA-AAAA-NNNN n'est jamais utilise (c'est le N°
        FOURNISSEUR qui compte)."""
        _ = allow_soft_duplicate  # deprecated, garde pour compatibilite API
        norm_num = _norm(data.number)
        norm_sup = _norm(data.supplier)
        if not norm_sup or not norm_num:
            return  # Sans fournisseur OU sans numero, pas de dedup possible
        copro_id = (data.copropriete_id or "").strip()
        q: dict = {}
        if copro_id:
            q["copropriete_id"] = copro_id
        if exclude_id:
            q["id"] = {"$ne": exclude_id}

        async for inv in db.invoices.find(
            q,
            {"_id": 0, "id": 1, "number": 1, "supplier": 1, "date": 1, "total_amount": 1},
        ):
            existing_num = _norm(inv.get("number", ""))
            existing_sup = _norm(inv.get("supplier", ""))
            if existing_sup != norm_sup:
                continue
            # Regle unique : meme fournisseur + meme numero -> doublon strict
            if existing_num == norm_num:
                raise HTTPException(
                    409,
                    f"Facture en doublon (numero identique) : numero '{data.number}' du "
                    f"fournisseur '{data.supplier}' existe deja (date {inv.get('date','?')}, "
                    f"montant {inv.get('total_amount','?')} EUR). "
                    f"Si c'est une facture distincte, modifiez le numero pour le rendre unique."
                )

    async def _learn_category_split(
        expense_category_id: Optional[str],
        provided_occupant_pct: Optional[float],
        applied_occupant_pct: float,
        lines: Optional[list] = None,
    ) -> None:
        """iter90ed : Apprentissage automatique de la repartition
        occupant/proprietaire par nature de depense.

        Quand l'utilisateur enregistre une facture avec une repartition
        explicite (occupant_pct fourni != None), on met a jour
        `default_occupant_pct` et `default_proprietaire_pct` sur la nature
        cible. La prochaine facture sur cette nature aura ce split
        pre-rempli automatiquement dans le frontend.

        Logique :
          - Mode single-line (lines vide/None) : update category unique
            (`expense_category_id`) avec `applied_occupant_pct`.
          - Mode multi-lignes : ignore (les pcts sont globaux, pas par ligne).
          - Skip si aucune valeur explicite (heritage pur du default).
        """
        # Mode multi-lignes : impossible d'attribuer un split a une nature
        # specifique (occupant_pct est global). Skip.
        if lines and len(lines) > 0:
            return
        # Skip si l'utilisateur n'a rien fourni (heritage pur du default)
        if provided_occupant_pct is None:
            return
        if not expense_category_id:
            return
        # Update default sur la nature
        occ = max(0.0, min(100.0, float(applied_occupant_pct)))
        prop = round(100.0 - occ, 2)
        await db.expense_categories.update_one(
            {"id": expense_category_id},
            {"$set": {
                "default_occupant_pct": occ,
                "default_proprietaire_pct": prop,
            }},
        )

    @router.post("/invoices")
    async def create_invoice(data: InvoiceInput, force: bool = Query(default=False)):
        from fiscal_lock import ensure_period_open
        # iter90fa : snap-to-card - si un nom de fournisseur libre matche
        # une fiche existante (par nom normalise), on remplace par le nom
        # canonique de la fiche pour eviter la creation implicite d'un
        # doublon "sans fiche".
        # iter90fb : matche aussi le contenu entre parentheses (ex:
        # "Finlead Properties (Finlead srl)" -> snap sur "Finlead SRL").
        if data.supplier:
            from routes.suppliers import _norm_name_candidates
            target_norms = _norm_name_candidates(data.supplier)
            if target_norms:
                q_sup = {}
                if data.copropriete_id:
                    q_sup["$or"] = [
                        {"copropriete_id": data.copropriete_id},
                        {"is_global": True},
                        {"copropriete_id": {"$in": [None, ""]}},
                    ]
                from routes.suppliers import _norm_name as _norm_supplier_name
                async for card in db.suppliers.find(q_sup, {"_id": 0, "name": 1}):
                    if _norm_supplier_name(card.get("name", "")) in target_norms:
                        data.supplier = card["name"]
                        break
        # Verrou fiscal : la date de la facture doit etre dans une periode ouverte
        await ensure_period_open(db, data.copropriete_id or "", data.date, context="facture")
        # Anti-doublon strict avant toute persistance (soft duplicate ignore si force=true)
        await _check_invoice_duplicate(data, allow_soft_duplicate=force)
        # Resolve multi-line first (raises if invalid)
        resolved_lines, merged_dist = await _resolve_invoice_lines(data)
        # If expense_category_id provided, derive/override account_number
        account_number = data.account_number
        cat_default_occupant = None
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
            if cat:
                cat_default_occupant = float(cat.get("default_occupant_pct") or 0)
        # Resoudre occupant_pct : valeur fournie ou heritee de la categorie, sinon 0%
        occupant_pct = data.occupant_pct if data.occupant_pct is not None else (cat_default_occupant or 0.0)
        occupant_pct = max(0.0, min(100.0, float(occupant_pct)))
        proprietaire_pct = round(100.0 - occupant_pct, 2)
        # Frais privatif: force compte 643, ignore distribution_key
        # iter85e : supporte multi-allocations (plusieurs proprietaires avec
        # montants fixes en EUR) en plus du legacy single-owner.
        # iter85j : comparaison en CENTIMES (entiers) pour eviter les erreurs
        # d'arrondi flottants (ex. 90.02 - 90.01 != 0.01 en flottant).
        resolved_private_allocs: list = []
        if data.is_private_fee:
            if data.private_fee_allocations:
                # Multi-owners
                total_alloc_cents = 0
                for idx, alloc in enumerate(data.private_fee_allocations, start=1):
                    if not alloc.owner_id:
                        raise HTTPException(400, f"Allocation {idx}: owner_id requis")
                    owner = await db.owners.find_one({"id": alloc.owner_id}, {"_id": 0})
                    if not owner:
                        raise HTTPException(404, f"Allocation {idx}: proprietaire non trouve")
                    amt = float(alloc.amount or 0)
                    if amt <= 0:
                        raise HTTPException(400, f"Allocation {idx}: montant doit etre > 0")
                    total_alloc_cents += round(amt * 100)
                    resolved_private_allocs.append({"owner_id": alloc.owner_id, "amount": round(amt, 2)})
                total_invoice_cents = round(float(data.total_amount) * 100)
                if total_alloc_cents != total_invoice_cents:
                    raise HTTPException(
                        400,
                        f"Somme des allocations ({total_alloc_cents/100:.2f}) doit egaler le total de la facture ({total_invoice_cents/100:.2f}). Ecart : {(total_invoice_cents - total_alloc_cents)/100:.2f} EUR",
                    )
            elif data.private_fee_owner_id:
                # Legacy single-owner
                owner = await db.owners.find_one({"id": data.private_fee_owner_id}, {"_id": 0})
                if not owner:
                    raise HTTPException(404, "Proprietaire non trouve")
                resolved_private_allocs = [{"owner_id": data.private_fee_owner_id, "amount": round(float(data.total_amount), 2)}]
            else:
                raise HTTPException(400, "Au moins un proprietaire doit etre selectionne pour un frais privatif")
            account_number = "643"
        # Compute distribution lines if key provided (skipped for private fees,
        # remplaced by merged_dist in multi-line mode)
        distribution_lines = []
        if resolved_lines:
            distribution_lines = merged_dist or []
        elif data.distribution_key_id and not data.is_private_fee:
            key = await db.distribution_keys.find_one({"id": data.distribution_key_id}, {"_id": 0})
            if key:
                # iter90ac : exclut les lots marques excluded=True
                active_kls = [l for l in key["lots"] if not l.get("excluded")]
                total_shares = sum(l["share"] for l in active_kls) if active_kls else 1
                for lot_entry in active_kls:
                    owner = await db.lots.find_one({"id": lot_entry["lot_id"]}, {"_id": 0})
                    owner_name = ""
                    if owner and owner.get("owner_id"):
                        owner_doc = await db.owners.find_one({"id": owner["owner_id"]}, {"_id": 0})
                        owner_name = owner_doc["name"] if owner_doc else ""
                    share_ratio = lot_entry["share"] / total_shares if total_shares > 0 else 0
                    distribution_lines.append({
                        "lot_id": lot_entry["lot_id"],
                        "lot_number": lot_entry["lot_number"],
                        "owner_name": owner_name,
                        "share": lot_entry["share"],
                        "amount": round(data.total_amount * share_ratio, 2)
                    })

        # iter90dq : prefixe interne configurable par exercice fiscal.
        # 1. Cherche l'exercice actif sur la date de la facture
        # 2. Utilise `fiscal_year.invoice_number_prefix` s'il est defini
        # 3. Sinon fallback `FA-YYYY-` (comportement historique)
        inv_date = data.date or datetime.now(timezone.utc).date().isoformat()
        fy = await db.fiscal_years.find_one(
            {
                "copropriete_id": data.copropriete_id or "",
                "start_date": {"$lte": inv_date},
                "end_date": {"$gte": inv_date},
            },
            {"_id": 0, "invoice_number_prefix": 1},
        )
        prefix_conf = (fy or {}).get("invoice_number_prefix") or ""
        if prefix_conf:
            prefix = prefix_conf
        else:
            year = inv_date[:4]
            prefix = f"FA-{year}-"

        # iter90ds : sequence basee sur MAX des refs existantes, PAS sur
        # count(). Cela evite les doublons quand des factures sont
        # supprimees et preserve la continuite du facturier ACH pour l'audit.
        import re as _re
        existing_refs = await db.invoices.find(
            {
                "copropriete_id": data.copropriete_id or "",
                "internal_reference": {"$regex": f"^{_re.escape(prefix)}[0-9]+$"},
            },
            {"_id": 0, "internal_reference": 1},
        ).to_list(200000)
        seq_pat = _re.compile(f"^{_re.escape(prefix)}([0-9]+)$")
        max_seq = 0
        for e in existing_refs:
            m = seq_pat.match(e.get("internal_reference", "") or "")
            if m:
                try:
                    max_seq = max(max_seq, int(m.group(1)))
                except ValueError:  # noqa: PERF203
                    pass
        internal_reference = f"{prefix}{(max_seq + 1):04d}"
        # Ceinture-bretelles : verifie l'unicite au cas ou (concurrence).
        while await db.invoices.find_one({"copropriete_id": data.copropriete_id or "", "internal_reference": internal_reference}, {"_id": 0, "id": 1}):
            max_seq += 1
            internal_reference = f"{prefix}{(max_seq + 1):04d}"

        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "internal_reference": internal_reference,
            "date": data.date,
            "due_date": data.due_date,
            "supplier": data.supplier,
            "description": data.description,
            "total_amount": data.total_amount,
            "vat_amount": data.vat_amount,
            "account_number": account_number,
            "expense_category_id": data.expense_category_id or "",
            "distribution_key_id": "" if data.is_private_fee else data.distribution_key_id,
            "distribution_lines": distribution_lines,
            "lines": resolved_lines,  # [] = mode 1-ligne legacy
            "status": data.status,
            "copropriete_id": data.copropriete_id or "",
            "is_private_fee": bool(data.is_private_fee),
            "private_fee_owner_id": data.private_fee_owner_id or "",
            "private_fee_allocations": resolved_private_allocs if data.is_private_fee else [],
            # Repartition occupant/proprietaire pour decompte locataire
            "occupant_pct": occupant_pct,
            "proprietaire_pct": proprietaire_pct,
            "occupant_amount": round(data.total_amount * occupant_pct / 100, 2),
            "proprietaire_amount": round(data.total_amount * proprietaire_pct / 100, 2),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.invoices.insert_one(doc)
        clean = {k: v for k, v in doc.items() if k != "_id"}
        # iter90ed : apprentissage automatique de la repartition
        # occupant/proprietaire par nature. Si l'utilisateur a fourni un
        # occupant_pct explicite, on met a jour default_occupant_pct de la
        # nature -> la prochaine facture sur cette nature aura ce split.
        try:
            await _learn_category_split(
                data.expense_category_id, data.occupant_pct,
                occupant_pct, data.lines,
            )
        except Exception as e:
            print(f"[learn-split] failed: {e}")
        try:
            await generate_purchase_entry(db, clean)
        except Exception as e:
            print(f"[auto-entry] purchase failed: {e}")
        return clean

    @router.get("/invoices/supplier-suggestion")
    async def supplier_suggestion(
        request: Request,
        supplier: str,
        copropriete_id: Optional[str] = None,
    ):
        """Retourne la nature de depense la plus utilisee pour ce fournisseur
        au sein d'une ACP (Chinese walls STRICT). Auto-apprentissage pour
        pre-remplir la nature/compte/cle lors de la saisie d'une nouvelle
        facture.

        Comptage :
          - factures mode 1-nature -> +1 pour inv.expense_category_id
          - factures mode multi-lignes -> +1 par ligne pour son
            line.expense_category_id (permet a un fournisseur avec split
            recurrent de proposer la nature la plus souvent utilisee)

        Match fournisseur : exact insensible a la casse (regex ancre).
        Retourne None si aucun historique."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return {"suggestion": None}
        supp = (supplier or "").strip()
        if not supp:
            return {"suggestion": None}
        import re as _re
        query = {
            "copropriete_id": copropriete_id,
            "supplier": {"$regex": f"^{_re.escape(supp)}$", "$options": "i"},
        }
        invoices = await db.invoices.find(
            query,
            {"_id": 0, "expense_category_id": 1, "account_number": 1,
             "distribution_key_id": 1, "lines": 1},
        ).to_list(5000)
        if not invoices:
            return {"suggestion": None}
        # Comptage par expense_category_id (ignore les valeurs vides)
        counts: dict = {}
        # Memorise le dernier (account_number, distribution_key_id) associe
        # a chaque nature -> pour proposer aussi un compte / cle par defaut.
        last_meta: dict = {}
        for inv in invoices:
            lines = inv.get("lines") or []
            if lines:
                for ln in lines:
                    cid = (ln.get("expense_category_id") or "").strip()
                    if not cid:
                        continue
                    counts[cid] = counts.get(cid, 0) + 1
                    last_meta[cid] = {
                        "account_number": (ln.get("account_number") or "").strip(),
                        "distribution_key_id": (ln.get("distribution_key_id") or "").strip(),
                    }
            else:
                cid = (inv.get("expense_category_id") or "").strip()
                if not cid:
                    continue
                counts[cid] = counts.get(cid, 0) + 1
                last_meta[cid] = {
                    "account_number": (inv.get("account_number") or "").strip(),
                    "distribution_key_id": (inv.get("distribution_key_id") or "").strip(),
                }
        if not counts:
            return {"suggestion": None}
        # Nature la plus utilisee
        top_cid = max(counts, key=lambda k: counts[k])
        cat = await db.expense_categories.find_one(
            {"id": top_cid, "copropriete_id": copropriete_id}, {"_id": 0}
        )
        if not cat:
            return {"suggestion": None}
        meta = last_meta.get(top_cid, {})
        # Compte : priorite au compte de la nature elle-meme si defini,
        # sinon le dernier compte utilise avec cette nature pour ce fournisseur.
        account_number = (cat.get("account_number") or "").strip() or meta.get("account_number", "")
        distribution_key_id = (
            (cat.get("default_distribution_key_id") or "").strip()
            or meta.get("distribution_key_id", "")
        )
        return {
            "suggestion": {
                "expense_category_id": top_cid,
                "expense_category_name": cat.get("name", ""),
                "account_number": account_number,
                "distribution_key_id": distribution_key_id,
                "usage_count": counts[top_cid],
                "invoices_matched": len(invoices),
            }
        }

    @router.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        return inv

    @router.put("/invoices/{invoice_id}")
    async def update_invoice(invoice_id: str, data: InvoiceInput, force: bool = Query(default=False)):
        from fiscal_lock import ensure_period_open
        # iter90fa/fb : snap-to-card (idem create_invoice, match parentheses)
        if data.supplier:
            from routes.suppliers import _norm_name_candidates, _norm_name as _norm_supplier_name
            target_norms = _norm_name_candidates(data.supplier)
            if target_norms:
                q_sup = {}
                if data.copropriete_id:
                    q_sup["$or"] = [
                        {"copropriete_id": data.copropriete_id},
                        {"is_global": True},
                        {"copropriete_id": {"$in": [None, ""]}},
                    ]
                async for card in db.suppliers.find(q_sup, {"_id": 0, "name": 1}):
                    if _norm_supplier_name(card.get("name", "")) in target_norms:
                        data.supplier = card["name"]
                        break
        existing_for_lock = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if existing_for_lock:
            # Verrou : la date d'origine ET la nouvelle doivent etre dans un exercice ouvert
            await ensure_period_open(db, existing_for_lock.get("copropriete_id", ""), existing_for_lock.get("date"), context="facture")
        await ensure_period_open(db, data.copropriete_id or (existing_for_lock or {}).get("copropriete_id", ""), data.date, context="facture")
        # Anti-doublon (excluant cette facture elle-meme, soft duplicate ignore si force=true)
        await _check_invoice_duplicate(data, exclude_id=invoice_id, allow_soft_duplicate=force)
        # Resolve multi-line first
        resolved_lines, merged_dist = await _resolve_invoice_lines(data)
        account_number = data.account_number
        cat_default_occupant = None
        if data.expense_category_id:
            cat = await db.expense_categories.find_one({"id": data.expense_category_id}, {"_id": 0})
            if cat and cat.get("account_number"):
                account_number = cat["account_number"]
            if cat:
                cat_default_occupant = float(cat.get("default_occupant_pct") or 0)
        # Si occupant_pct fourni : on l'utilise. Sinon : on garde l'existant
        # (et si pas d'existant : on prend la categorie ou 0%)
        existing = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if data.occupant_pct is not None:
            occupant_pct = float(data.occupant_pct)
        elif existing and existing.get("occupant_pct") is not None:
            occupant_pct = float(existing.get("occupant_pct") or 0)
        else:
            occupant_pct = cat_default_occupant or 0.0
        occupant_pct = max(0.0, min(100.0, occupant_pct))
        proprietaire_pct = round(100.0 - occupant_pct, 2)
        # iter85e : supporte multi-allocations en PUT aussi
        # iter85j : comparaison en CENTIMES (entiers)
        resolved_private_allocs_upd: list = []
        if data.is_private_fee:
            if data.private_fee_allocations:
                total_alloc_cents = 0
                for idx, alloc in enumerate(data.private_fee_allocations, start=1):
                    if not alloc.owner_id:
                        raise HTTPException(400, f"Allocation {idx}: owner_id requis")
                    owner = await db.owners.find_one({"id": alloc.owner_id}, {"_id": 0})
                    if not owner:
                        raise HTTPException(404, f"Allocation {idx}: proprietaire non trouve")
                    amt = float(alloc.amount or 0)
                    if amt <= 0:
                        raise HTTPException(400, f"Allocation {idx}: montant doit etre > 0")
                    total_alloc_cents += round(amt * 100)
                    resolved_private_allocs_upd.append({"owner_id": alloc.owner_id, "amount": round(amt, 2)})
                total_invoice_cents = round(float(data.total_amount) * 100)
                if total_alloc_cents != total_invoice_cents:
                    raise HTTPException(
                        400,
                        f"Somme des allocations ({total_alloc_cents/100:.2f}) doit egaler le total de la facture ({total_invoice_cents/100:.2f}). Ecart : {(total_invoice_cents - total_alloc_cents)/100:.2f} EUR",
                    )
            elif data.private_fee_owner_id:
                owner = await db.owners.find_one({"id": data.private_fee_owner_id}, {"_id": 0})
                if not owner:
                    raise HTTPException(404, "Proprietaire non trouve")
                resolved_private_allocs_upd = [{"owner_id": data.private_fee_owner_id, "amount": round(float(data.total_amount), 2)}]
            else:
                raise HTTPException(400, "Au moins un proprietaire doit etre selectionne pour un frais privatif")
            account_number = "643"
        update = {
            "number": data.number, "date": data.date, "due_date": data.due_date,
            "supplier": data.supplier, "description": data.description,
            "total_amount": data.total_amount, "vat_amount": data.vat_amount,
            "account_number": account_number,
            "expense_category_id": data.expense_category_id or "",
            "distribution_key_id": "" if data.is_private_fee else data.distribution_key_id,
            "lines": resolved_lines,
            "status": data.status,
            "is_private_fee": bool(data.is_private_fee),
            "private_fee_owner_id": data.private_fee_owner_id or "",
            "private_fee_allocations": resolved_private_allocs_upd if data.is_private_fee else [],
            "occupant_pct": occupant_pct,
            "proprietaire_pct": proprietaire_pct,
            "occupant_amount": round(data.total_amount * occupant_pct / 100, 2),
            "proprietaire_amount": round(data.total_amount * proprietaire_pct / 100, 2),
        }
        # If switching to private fee, clear distribution_lines (and lines)
        if data.is_private_fee:
            update["distribution_lines"] = []
            update["lines"] = []
        elif resolved_lines:
            # Multi-line: replace distribution_lines with merged aggregation
            update["distribution_lines"] = merged_dist or []
        result = await db.invoices.update_one({"id": invoice_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        # iter90ed : apprentissage automatique de la repartition
        try:
            await _learn_category_split(
                data.expense_category_id, data.occupant_pct,
                occupant_pct, data.lines,
            )
        except Exception as e:
            print(f"[learn-split] failed: {e}")
        try:
            await generate_purchase_entry(db, inv)
        except Exception as e:
            print(f"[auto-entry] purchase update failed: {e}")
        return inv

    @router.delete("/invoices/{invoice_id}")
    async def delete_invoice(invoice_id: str):
        from fiscal_lock import ensure_period_open
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        # Verrou : refuse la suppression si la facture est dans un exercice cloture
        await ensure_period_open(db, inv.get("copropriete_id", ""), inv.get("date"), context="facture")
        att_storage = get_invoice_attachments_storage(db)
        for att in inv.get("attachments", []) or []:
            # iter87 : delete from GridFS first (new storage), fallback to disk (legacy)
            gid = att.get("gridfs_id")
            if gid:
                try:
                    await att_storage.delete(gid)
                except Exception:
                    pass
            try:
                p = att.get("stored_path")
                if p and Path(p).exists():
                    Path(p).unlink()
            except Exception:
                pass
        try:
            await _delete_auto_entries(db, "invoice", invoice_id)
        except Exception:
            pass
        result = await db.invoices.delete_one({"id": invoice_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Facture non trouvee")
        return {"message": "Facture supprimee"}

    # ---- INVOICE ATTACHMENTS ----
    @router.post("/invoices/{invoice_id}/attachments")
    async def upload_invoice_attachment(invoice_id: str, file: UploadFile = File(...)):
        """Attach a PDF or image scan of the supplier invoice.

        iter87 : stored in MongoDB GridFS bucket `invoice_attachments` (persistent
        across redeploys). Legacy attachments with `stored_path` continue to work
        via fallback in the download endpoint.
        """
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        ext = Path(file.filename or "file").suffix.lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "Format autorise: PDF, PNG, JPG")
        content = await file.read()
        att_id = str(uuid.uuid4())
        att_storage = get_invoice_attachments_storage(db)
        gridfs_id = await att_storage.upload(
            filename=file.filename or f"{att_id}{ext}",
            contents=content,
            metadata={
                "attachment_id": att_id,
                "invoice_id": invoice_id,
                "copropriete_id": inv.get("copropriete_id", ""),
                "mime_type": file.content_type or "application/octet-stream",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        attachment = {
            "id": att_id,
            "filename": file.filename,
            "gridfs_id": gridfs_id,
            "mime_type": file.content_type or "application/octet-stream",
            "size": len(content),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$push": {"attachments": attachment}}
        )
        return attachment

    @router.get("/invoices/{invoice_id}/attachments/{attachment_id}/download")
    async def download_invoice_attachment(invoice_id: str, attachment_id: str, disposition: str = "attachment"):
        """Stream the attachment file.

        Query param `disposition`:
          - `attachment` (default) : Content-Disposition: attachment (forces download)
          - `inline` : Content-Disposition: inline (renders in browser viewer for PDF/PNG/JPG)
        """
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        att_storage = get_invoice_attachments_storage(db)
        for att in inv.get("attachments", []) or []:
            if att.get("id") != attachment_id:
                continue
            filename = att.get("filename", "facture.pdf")
            media_type = att.get("mime_type", "application/pdf")
            safe_name = filename.replace('"', "")
            disp_header = (
                f'inline; filename="{safe_name}"' if disposition == "inline"
                else f'attachment; filename="{safe_name}"'
            )
            # iter87 : prefer GridFS (new), fallback to disk (legacy)
            gid = att.get("gridfs_id")
            if gid:
                try:
                    data = await att_storage.download(gid)
                except Exception:
                    raise HTTPException(404, "Fichier introuvable dans GridFS")
                # iter90dr : appose la reference interne en haut a droite du PDF.
                # Non-destructif : seul le flux telecharge est modifie, le
                # fichier GridFS original reste intact. Utile pour les audits.
                from pdf_stamp import stamp_pdf_with_reference, is_pdf_bytes
                internal_ref = inv.get("internal_reference", "")
                if internal_ref and is_pdf_bytes(data):
                    data = stamp_pdf_with_reference(data, internal_ref)
                return Response(
                    content=data,
                    media_type=media_type,
                    headers={"Content-Disposition": disp_header},
                )
            # Legacy disk fallback
            path = att.get("stored_path", "")
            if path and Path(path).exists():
                # iter90dr : idem pour le fallback disque
                from pdf_stamp import stamp_pdf_with_reference, is_pdf_bytes
                internal_ref = inv.get("internal_reference", "")
                if internal_ref and str(path).lower().endswith(".pdf"):
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    if is_pdf_bytes(raw):
                        raw = stamp_pdf_with_reference(raw, internal_ref)
                        return Response(
                            content=raw,
                            media_type=media_type,
                            headers={"Content-Disposition": disp_header},
                        )
                return FileResponse(
                    path, media_type=media_type,
                    headers={"Content-Disposition": disp_header},
                )
            raise HTTPException(404, "Fichier introuvable")
        raise HTTPException(404, "Piece jointe non trouvee")

    @router.delete("/invoices/{invoice_id}/attachments/{attachment_id}")
    async def delete_invoice_attachment(invoice_id: str, attachment_id: str):
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Facture non trouvee")
        target = None
        for att in inv.get("attachments", []) or []:
            if att.get("id") == attachment_id:
                target = att
                break
        if not target:
            raise HTTPException(404, "Piece jointe non trouvee")
        # iter87 : delete from GridFS (new) and from disk (legacy if any)
        att_storage = get_invoice_attachments_storage(db)
        gid = target.get("gridfs_id")
        if gid:
            try:
                await att_storage.delete(gid)
            except Exception:
                pass
        try:
            p = target.get("stored_path")
            if p and Path(p).exists():
                Path(p).unlink()
        except Exception:
            pass
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$pull": {"attachments": {"id": attachment_id}}}
        )
        return {"message": "Piece jointe supprimee"}

    # ---- INVOICE BUNDLE (Regroupement de PDFs Optipro) ----
    @router.post("/invoices/bundle-analyze")
    async def bundle_analyze(
        request: Request,
        file: UploadFile = File(...),
        copropriete_id: Optional[str] = Form(None),
    ):
        """Etape 1 : Recoit un PDF "Regroupement de documents" (factures concatenees).
        Detecte chaque facture (page_range), extrait les metadonnees et propose un
        matching avec les factures existantes de l'ACP.
        Retourne un session_id (qui pointe sur le PDF stocke temporairement) +
        la liste des blocks avec un suggested_match optionnel."""
        from import_wizard.pdf_invoices_bundle import parse_invoice_bundle, match_invoice

        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or ""
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis pour l'analyse du bundle")

        ext = Path(file.filename or "bundle.pdf").suffix.lower()
        if ext != ".pdf":
            raise HTTPException(400, "Format autorise : PDF uniquement")

        raw = await file.read()
        if not raw or len(raw) < 100:
            raise HTTPException(400, "Fichier vide ou invalide")

        # Parse PDF (slow operation, can take 10s for 100+ pages)
        try:
            info = parse_invoice_bundle(raw)
        except Exception as e:
            raise HTTPException(500, f"Echec analyse PDF : {e}")

        # iter87 : Persist bundle file in GridFS bucket `invoice_bundles` (with
        # TTL via `expires_at` metadata - 24h). Replaces /app/uploads/_bundles/.
        session_id = str(uuid.uuid4())
        bundles_storage = get_invoice_bundles_storage(db)
        now_iso = datetime.now(timezone.utc)
        expires_at = now_iso + timedelta(hours=24)
        pdf_gridfs_id = await bundles_storage.upload(
            filename=f"bundle-{session_id}.pdf",
            contents=raw,
            metadata={
                "session_id": session_id,
                "copropriete_id": copropriete_id,
                "kind": "bundle_pdf",
                "created_at": now_iso.isoformat(),
                "expires_at": expires_at,
            },
        )

        # Load candidate invoices for this ACP (no need for attachments in matcher)
        invoices = await db.invoices.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "id": 1, "number": 1, "internal_reference": 1,
             "supplier": 1, "total_amount": 1, "date": 1, "attachments": 1},
        ).sort("date", -1).to_list(5000)
        # Map to matcher candidate format (supplier_name expected)
        cands = [{
            "id": inv["id"],
            "number": inv.get("number") or "",
            "internal_reference": inv.get("internal_reference") or "",
            "supplier_name": inv.get("supplier") or "",
            "total_amount": float(inv.get("total_amount") or 0),
            "date": inv.get("date") or "",
            "has_attachment": bool(inv.get("attachments")),
        } for inv in invoices]

        blocks_out = []
        for i, blk in enumerate(info.get("blocks", [])):
            match = match_invoice(blk, cands)
            blocks_out.append({
                "block_id": f"blk-{i}",
                "page_range": blk.get("page_range", []),
                "page_count": blk.get("page_count", 0),
                "invoice_number": blk.get("invoice_number", ""),
                "date_iso": blk.get("date_iso", ""),
                "date_display": blk.get("date_display", ""),
                "supplier_hint": blk.get("supplier_hint", ""),
                "supplier_tva": blk.get("supplier_tva", ""),
                "total_amount": blk.get("total_amount", 0.0),
                "raw_text_preview": blk.get("raw_text_preview", ""),
                "suggested_match": (
                    {
                        "invoice_id": match["id"],
                        "invoice_number": match.get("number"),
                        "internal_reference": match.get("internal_reference"),
                        "supplier": match.get("supplier_name"),
                        "total_amount": match.get("total_amount"),
                        "date": match.get("date"),
                        "has_attachment": match.get("has_attachment", False),
                        "confidence": match.get("match_confidence", 0),
                        "method": match.get("match_method", ""),
                    } if match else None
                ),
            })

        # Persist session metadata in a regular Mongo collection
        # (instead of a JSON file on disk). The bundle PDF binary stays in GridFS.
        meta = {
            "session_id": session_id,
            "copropriete_id": copropriete_id,
            "total_pages": info.get("total_pages", 0),
            "invoice_count": info.get("invoice_count", 0),
            "blocks": blocks_out,
            "filename": file.filename or "bundle.pdf",
            "pdf_gridfs_id": pdf_gridfs_id,
            "created_at": now_iso.isoformat(),
            "expires_at": expires_at,
        }
        await db.invoice_bundle_sessions.replace_one(
            {"session_id": session_id}, meta, upsert=True
        )

        return {
            "session_id": session_id,
            "total_pages": info.get("total_pages", 0),
            "invoice_count": info.get("invoice_count", 0),
            "blocks": blocks_out,
            "filename": file.filename or "bundle.pdf",
        }

    @router.post("/invoices/bundle-commit")
    async def bundle_commit(payload: dict):
        """Etape 2 : Pour chaque block, extrait les pages correspondantes du PDF
        bundle et les attache a la facture choisie (ou cree une nouvelle facture
        si mode='create').
        Body : {
          session_id: str,
          assignments: [
            { block_id, page_range, mode: 'attach'|'create'|'skip',
              invoice_id?: str,  # mode=attach
              invoice_data?: {...}  # mode=create (full InvoiceInput payload)
            }, ...
          ]
        }"""
        from import_wizard.pdf_invoices_bundle import extract_block_pdf
        from fiscal_lock import ensure_period_open

        session_id = payload.get("session_id") or ""
        assignments = payload.get("assignments") or []
        if not session_id or not assignments:
            raise HTTPException(400, "session_id et assignments requis")

        # iter87 : Load session from MongoDB + bundle PDF from GridFS
        bundles_storage = get_invoice_bundles_storage(db)
        att_storage = get_invoice_attachments_storage(db)
        meta = await db.invoice_bundle_sessions.find_one(
            {"session_id": session_id}, {"_id": 0}
        )
        if not meta:
            raise HTTPException(404, "Session bundle introuvable ou expiree")
        copropriete_id = meta.get("copropriete_id", "")
        pdf_gid = meta.get("pdf_gridfs_id", "")
        if not pdf_gid:
            raise HTTPException(404, "Bundle PDF introuvable dans GridFS")
        try:
            raw = await bundles_storage.download(pdf_gid)
        except Exception:
            raise HTTPException(404, "Bundle PDF introuvable ou expire")

        attached = 0
        created = 0
        skipped = 0
        errors = []
        results = []

        for assignment in assignments:
            block_id = assignment.get("block_id", "")
            page_range = assignment.get("page_range") or []
            mode = assignment.get("mode", "skip")

            if mode == "skip":
                skipped += 1
                results.append({"block_id": block_id, "status": "skipped"})
                continue

            if not page_range:
                errors.append({"block_id": block_id, "error": "page_range vide"})
                continue

            invoice_id = assignment.get("invoice_id") or ""
            try:
                # Mode 'create' : create the invoice first using InvoiceInput
                if mode == "create":
                    inv_data = assignment.get("invoice_data") or {}
                    if not inv_data.get("number") or not inv_data.get("date") or not inv_data.get("supplier"):
                        errors.append({"block_id": block_id, "error": "Donnees facture incompletes (number, date, supplier requis)"})
                        continue
                    inv_data["copropriete_id"] = inv_data.get("copropriete_id") or copropriete_id
                    # Build InvoiceInput-compatible payload
                    try:
                        invoice_input = InvoiceInput(**inv_data)
                    except Exception as e:
                        errors.append({"block_id": block_id, "error": f"Validation : {e}"})
                        continue
                    new_inv = await create_invoice(invoice_input)
                    invoice_id = new_inv["id"]
                    created += 1

                if not invoice_id:
                    errors.append({"block_id": block_id, "error": "invoice_id manquant"})
                    continue

                inv = await db.invoices.find_one({"id": invoice_id, "copropriete_id": copropriete_id}, {"_id": 0})
                if not inv:
                    errors.append({"block_id": block_id, "error": "Facture introuvable dans l'ACP"})
                    continue

                # Verrou fiscal : ne pas attacher dans un exercice cloture
                try:
                    await ensure_period_open(db, inv.get("copropriete_id", ""), inv.get("date"), context="piece jointe")
                except Exception as e:
                    errors.append({"block_id": block_id, "error": f"Exercice cloture : {e}"})
                    continue

                # iter87 : Extract pages and store as new attachment in GridFS
                pdf_bytes = extract_block_pdf(raw, page_range)
                att_id = str(uuid.uuid4())
                pages_label = f"p{page_range[0]}-{page_range[-1]}" if len(page_range) > 1 else f"p{page_range[0]}"
                filename = f"bundle-{pages_label}.pdf"
                gridfs_id = await att_storage.upload(
                    filename=filename,
                    contents=pdf_bytes,
                    metadata={
                        "attachment_id": att_id,
                        "invoice_id": invoice_id,
                        "copropriete_id": copropriete_id,
                        "mime_type": "application/pdf",
                        "source": "bundle",
                        "bundle_session_id": session_id,
                        "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                attachment = {
                    "id": att_id,
                    "filename": filename,
                    "gridfs_id": gridfs_id,
                    "mime_type": "application/pdf",
                    "size": len(pdf_bytes),
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "bundle",
                    "bundle_session_id": session_id,
                    "bundle_page_range": page_range,
                }
                await db.invoices.update_one(
                    {"id": invoice_id},
                    {"$push": {"attachments": attachment}},
                )
                attached += 1
                results.append({
                    "block_id": block_id,
                    "status": "created_and_attached" if mode == "create" else "attached",
                    "invoice_id": invoice_id,
                    "attachment_id": att_id,
                })
            except HTTPException as e:
                errors.append({"block_id": block_id, "error": e.detail})
            except Exception as e:
                errors.append({"block_id": block_id, "error": str(e)})

        # iter87 : Cleanup the GridFS bundle PDF + the Mongo session doc
        # (each block is committed atomically - no need to keep the bundle).
        try:
            await bundles_storage.delete(pdf_gid)
        except Exception:
            pass
        try:
            await db.invoice_bundle_sessions.delete_one({"session_id": session_id})
        except Exception:
            pass

        return {
            "attached": attached,
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    return router
