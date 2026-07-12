from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import uuid
from tier_accounts import assign_owner_accounts


async def _build_mutation_decompte_context(db, lot_id: str, mutation_id: str) -> dict:
    """iter90au : helper reutilisable qui prepare le contexte pour
    build_mutation_decompte_pdf. Utilise par le download endpoint et par le
    communication router (/api/communication/send/mutation).

    Retourne un dict avec les kwargs pour build_mutation_decompte_pdf :
    {copropriete, lot, seller, buyer, mutation, breakdown}.
    """
    lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
    if not lot:
        raise HTTPException(404, "Lot non trouve")
    muts = lot.get("mutations") or []
    if mutation_id == "last":
        mut = muts[-1] if muts else None
    else:
        mut = next((m for m in muts if m.get("id") == mutation_id), None)
    if not mut:
        raise HTTPException(404, "Mutation non trouvee pour ce lot")

    copro = await db.coproprietes.find_one(
        {"id": lot.get("copropriete_id", "")}, {"_id": 0}
    ) or {}
    seller = await db.owners.find_one(
        {"id": mut.get("old_owner_id", "")}, {"_id": 0}
    ) or {}
    buyer = await db.owners.find_one(
        {"id": mut.get("new_owner_id", "")}, {"_id": 0}
    ) or {}

    breakdown = {
        "roulement_quota": mut.get("roulement_quota", 0),
        "current_period_prorata": mut.get("current_period_prorata", mut.get("prorata_provisions", 0)),
        "current_period_details": mut.get("current_period_details") or mut.get("prorata_details") or [],
        "future_calls": mut.get("future_calls") or [],
        "future_calls_total": mut.get("future_calls_total", 0),
        "budget_frequency": mut.get("budget_frequency"),
        "budget_frequency_label": mut.get("budget_frequency_label", ""),
        "total_transfer": mut.get("total_transfer", 0),
    }
    return {
        "copropriete": copro,
        "lot": lot,
        "seller": seller,
        "buyer": buyer,
        "mutation": mut,
        "breakdown": breakdown,
    }


def create_properties_router(db):
    router = APIRouter(prefix="/api")

    async def _get_user_scope(request):
        """Retourne (is_super_global, allowed_copro_ids).
        - is_super_global=True pour superadmin/admin : acces total, allowed=None.
        - Sinon allowed_copro_ids = liste des ACPs de l'utilisateur (peut etre []).
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if is_superadmin_only(role):
            return True, None
        return False, user.get("copropriete_ids", []) or []

    async def _owner_in_scope(owner_id: str, allowed_copros) -> bool:
        """True si l'owner a au moins un lot OU est rattache via copropriete_ids
        a une ACP du scope (iter90c). Superadmin retourne toujours True."""
        if allowed_copros is None:
            return True
        if not allowed_copros:
            return False
        c1 = await db.lots.count_documents({
            "owner_id": owner_id,
            "copropriete_id": {"$in": allowed_copros}
        })
        if c1 > 0:
            return True
        c2 = await db.lots.count_documents({
            "owner_ids": owner_id,
            "copropriete_id": {"$in": allowed_copros}
        })
        if c2 > 0:
            return True
        # iter90c : aussi en scope via copropriete_ids (proprio rattache sans lot)
        c3 = await db.owners.count_documents({
            "id": owner_id,
            "copropriete_ids": {"$in": allowed_copros},
        })
        return c3 > 0

    async def _allowed_owner_ids(allowed_copros):
        """Retourne le set des owner_id presents dans les lots des ACPs du scope,
        UNION les owners dont copropriete_ids contient une ACP du scope (iter90c)."""
        if allowed_copros is None:
            return None  # superadmin = pas de filtre
        if not allowed_copros:
            return set()
        ids1 = await db.lots.distinct("owner_id", {"copropriete_id": {"$in": allowed_copros}})
        ids2 = await db.lots.distinct("owner_ids", {"copropriete_id": {"$in": allowed_copros}})
        ids3 = await db.owners.distinct("id", {"copropriete_ids": {"$in": allowed_copros}})
        return (
            {x for x in (ids1 or []) if x}
            | {x for x in (ids2 or []) if x}
            | {x for x in (ids3 or []) if x}
        )

    # ---- OWNERS ----
    class OwnerInput(BaseModel):
        first_name: Optional[str] = ""
        last_name: Optional[str] = ""
        name: Optional[str] = ""
        civility: Optional[str] = ""
        address: Optional[str] = ""
        postal_code: Optional[str] = ""
        city: Optional[str] = ""
        country: Optional[str] = "Belgique"
        email: Optional[str] = ""
        email2: Optional[str] = ""
        phone: Optional[str] = ""
        phone2: Optional[str] = ""
        copropriete_id: Optional[str] = ""
        # Optional Optipro/Sogis import fields - kept as legacy reference
        auxiliary_code: Optional[str] = ""  # ex. "C0777"
        identifier: Optional[str] = ""      # ex. "193M04"
        vcs_code: Optional[str] = ""        # if provided, keep it; otherwise auto-generate
        vcs_digits: Optional[str] = ""
        iban: Optional[str] = ""
        # BCE pour les proprietaires personnes morales (societes)
        bce_number: Optional[str] = ""

    # ---- Helpers anti-doublon (cf. routes/suppliers.py pour la meme logique) ----
    import re as _re

    def _norm_owner_name(first: str, last: str, name: str) -> str:
        """Normalise un nom proprietaire en triant les mots alphabetiquement.
        Combine first_name + last_name si fournis, sinon name. Tolerant a
        l'ordre "Jean DUPONT" vs "DUPONT Jean"."""
        combined = (f"{first} {last}".strip() or name or "").lower()
        return " ".join(sorted(combined.split()))

    def _norm_alphanum(value: str) -> str:
        """Alphanumerique uppercase uniquement (BCE, IBAN, telephone)."""
        return _re.sub(r"[^A-Za-z0-9]", "", (value or "")).upper()

    def _norm_address(addr: str, postal: str, city: str) -> str:
        """Normalise une adresse complete : minuscules + alphanumerique
        + espaces multiples reduits. Tolerant aux ponctuations et casse."""
        full = f"{addr} {postal} {city}".strip().lower()
        # Garde espaces mais retire ponctuation
        full = _re.sub(r"[^a-z0-9\s]", "", full)
        return " ".join(full.split())

    async def find_duplicate_owner(
        *, first_name: str, last_name: str, name: str,
        email: str, phone: str, bce_number: str,
        address: str, postal_code: str, city: str,
        copro_id: str = "", exclude_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Detecte un doublon de proprietaire sur 5 criteres (l'un suffit) :
        1. Nom + prenom normalises (mots tries alphabetiquement)
        2. Email (champ email OU email2) normalise
        3. Telephone normalise (alphanumerique)
        4. BCE normalise
        5. Adresse complete normalisee (adresse + cp + ville)

        Scope : limite a copro_id si fourni (chinese wall), sinon global.
        Retourne {"owner": doc, "field": "name|email|phone|bce|address", "value": ...}.
        """
        norm_name = _norm_owner_name(first_name, last_name, name)
        norm_email = (email or "").strip().lower()
        norm_phone = _norm_alphanum(phone)
        norm_bce = _norm_alphanum(bce_number)
        norm_addr = _norm_address(address, postal_code, city)
        if not (norm_name or norm_email or norm_phone or norm_bce or norm_addr):
            return None

        base_query: dict = {}
        if copro_id:
            base_query["copropriete_id"] = copro_id
        if exclude_id:
            base_query["id"] = {"$ne": exclude_id}
        candidates = await db.owners.find(base_query, {"_id": 0}).to_list(5000)
        for o in candidates:
            if norm_email:
                e1 = (o.get("email") or "").strip().lower()
                e2 = (o.get("email2") or "").strip().lower()
                if e1 == norm_email or e2 == norm_email:
                    return {"owner": o, "field": "email", "value": email}
            if norm_phone:
                p1 = _norm_alphanum(o.get("phone", ""))
                p2 = _norm_alphanum(o.get("phone2", ""))
                if p1 == norm_phone or p2 == norm_phone:
                    return {"owner": o, "field": "phone", "value": phone}
            if norm_bce and _norm_alphanum(o.get("bce_number", "")) == norm_bce:
                return {"owner": o, "field": "bce_number", "value": bce_number}
            if norm_name:
                o_name = _norm_owner_name(o.get("first_name", ""), o.get("last_name", ""), o.get("name", ""))
                if o_name and o_name == norm_name:
                    return {"owner": o, "field": "name", "value": f"{first_name} {last_name}".strip() or name}
            if norm_addr:
                o_addr = _norm_address(o.get("address", ""), o.get("postal_code", ""), o.get("city", ""))
                if o_addr and o_addr == norm_addr:
                    return {"owner": o, "field": "address", "value": f"{address}, {postal_code} {city}".strip()}
        return None

    @router.get("/owners")
    async def list_owners(request: Request, copropriete_id: Optional[str] = None, include_unassigned: bool = False, syndic_wide: bool = False):
        """Liste des proprietaires - chinese wall STRICT (RGPD).

        - Superadmin/admin : voit tout.
        - Syndic/gestionnaire : ne voit QUE les proprietaires ayant un lot dans
          UNE DE SES ACPs (`user.copropriete_ids`).
        - Avec `copropriete_id` : restreint a cette ACP (verifie deja par middleware).
        - `include_unassigned=true` : ajoute les owners orphelins (copropriete_id="")
          qui ont ete crees mais pas encore lies a un lot. Indispensable pour
          l'assistant de creation d'ACP (drop-down d'affectation lot->owner) afin
          que les owners juste importes via PdfImportDialog soient visibles.
        - iter89b : `syndic_wide=true` : retourne TOUS les proprios accessibles
          au syndic (toutes ses ACPs confondues), IGNORANT le param `copropriete_id`
          et l'header `X-Copropriete-Id`. Indispensable pour le picker de
          mutation (l'acquereur peut etre un proprio existant dans une AUTRE
          ACP du meme syndic). Sans ca, on force l'utilisateur a creer un doublon.
        """
        # Aussi accepter le header X-Copropriete-Id pour homogeneiser
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        # iter85k : "all" est une valeur sentinelle indiquant "TOUS les owners"
        # (utilisee par CoproprietesPage pour creer une nouvelle ACP et lier
        # des proprietaires existants). On la traite comme None.
        if copropriete_id == "all":
            copropriete_id = None
        is_super, allowed_copros = await _get_user_scope(request)
        # Helper to also fetch orphan owners (copropriete_id == "" or missing) when requested
        async def _fetch_orphans():
            return await db.owners.find(
                {"$or": [{"copropriete_id": ""}, {"copropriete_id": {"$exists": False}}, {"copropriete_id": None}]},
                {"_id": 0},
            ).sort("last_name", 1).to_list(2000)
        # iter89b : syndic_wide -> ignorer le scope ACP courant
        if syndic_wide:
            if is_super:
                return await db.owners.find({}, {"_id": 0}).sort("last_name", 1).to_list(2000)
            allowed_owner_ids = await _allowed_owner_ids(allowed_copros)
            if not allowed_owner_ids:
                return []
            return await db.owners.find(
                {"id": {"$in": list(allowed_owner_ids)}}, {"_id": 0}
            ).sort("last_name", 1).to_list(2000)
        if copropriete_id:
            # Cas ACP specifique : owners ayant un lot dans cette ACP, OU lies a cette
            # ACP via `copropriete_ids[]` (proprio cree/importe mais lot pas encore
            # assigne) -- iter90c : evite que la creation d'un nouveau proprio
            # cree un doublon parce que l'existant sans lot etait invisible.
            owner_ids_single = await db.lots.distinct("owner_id", {"copropriete_id": copropriete_id})
            owner_ids_multi = await db.lots.distinct("owner_ids", {"copropriete_id": copropriete_id})
            owner_ids_linked = await db.owners.distinct("id", {"copropriete_ids": copropriete_id})
            allowed = (
                {oid for oid in (owner_ids_single or []) if oid}
                | {oid for oid in (owner_ids_multi or []) if oid}
                | {oid for oid in (owner_ids_linked or []) if oid}
            )
            owners = await db.owners.find({"id": {"$in": list(allowed)}}, {"_id": 0}).sort("last_name", 1).to_list(2000) if allowed else []
            if include_unassigned:
                owners.extend(await _fetch_orphans())
            return owners
        if is_super:
            # Superadmin sans param : tous les owners (vue plateforme)
            owners = await db.owners.find({}, {"_id": 0}).sort("last_name", 1).to_list(2000)
            return owners
        # Syndic / gestionnaire sans param : owners de TOUTES SES ACPs
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros)
        owners = []
        if allowed_owner_ids:
            owners = await db.owners.find(
                {"id": {"$in": list(allowed_owner_ids)}}, {"_id": 0}
            ).sort("last_name", 1).to_list(2000)
        if include_unassigned:
            owners.extend(await _fetch_orphans())
        return owners

    @router.post("/owners")
    async def create_owner(data: OwnerInput):
        from server import generate_vcs
        # Check anti-doublon avant creation
        dup = await find_duplicate_owner(
            first_name=data.first_name or "", last_name=data.last_name or "",
            name=data.name or "",
            email=data.email or "", phone=data.phone or "",
            bce_number=data.bce_number or "",
            address=data.address or "", postal_code=data.postal_code or "",
            city=data.city or "",
            copro_id=data.copropriete_id or "",
        )
        if dup:
            field_label = {
                "name": "nom + prenom",
                "email": "email",
                "phone": "telephone",
                "bce_number": "numero BCE",
                "address": "adresse postale",
            }.get(dup["field"], dup["field"])
            existing = dup["owner"]
            existing_name = existing.get("name") or f"{existing.get('first_name','')} {existing.get('last_name','')}".strip()
            raise HTTPException(
                409,
                f"Doublon detecte : un proprietaire avec le meme {field_label} existe deja "
                f"({existing_name} - id {existing.get('id','')[:8]}). "
                f"Utilisez le proprietaire existant plutot que d'en creer un nouveau.",
            )
        # If a VCS code is provided (e.g. from an Optipro import), reuse it
        # to preserve the legacy reference. Otherwise auto-generate one.
        vcs_code = (data.vcs_code or "").strip()
        if not vcs_code:
            vcs_code = await generate_vcs(db)
        vcs_digits = (data.vcs_digits or "").strip()
        if not vcs_digits:
            vcs_digits = vcs_code.replace("+", "").replace("/", "")
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        doc = {
            "id": str(uuid.uuid4()),
            "first_name": data.first_name,
            "last_name": data.last_name,
            "name": full_name,
            "civility": data.civility or "",
            "address": data.address,
            "postal_code": data.postal_code,
            "city": data.city,
            "country": data.country,
            "email": data.email,
            "email2": data.email2,
            "phone": data.phone,
            "phone2": data.phone2,
            "vcs_code": vcs_code,
            "vcs_digits": vcs_digits,
            "auxiliary_code": (data.auxiliary_code or "").strip(),
            "identifier": (data.identifier or "").strip(),
            "iban": (data.iban or "").strip(),
            "bce_number": (data.bce_number or "").strip(),
            "copropriete_id": data.copropriete_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.owners.insert_one(doc)
        if data.copropriete_id:
            await assign_owner_accounts(db, doc, data.copropriete_id)
            doc = await db.owners.find_one({"id": doc["id"]}, {"_id": 0})
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/owners/check-duplicate")
    async def check_duplicate_owner(request: Request, email: Optional[str] = None, phone: Optional[str] = None):
        """Check if email or phone already exists.
        Pour un syndic : cherche UNIQUEMENT dans les owners de ses ACPs (RGPD).
        Pour un superadmin : cherche dans tous les owners.

        iter88d : la response inclut maintenant owner_id + details (email, phone,
        vcs, first/last name, ACPs liees) pour permettre a l'UI de proposer de
        SELECTIONNER ce proprio existant au lieu d'en creer un nouveau.
        """
        is_super, allowed_copros = await _get_user_scope(request)
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros) if not is_super else None
        duplicates = []
        seen_ids = set()  # dedupe : meme owner_id peut matcher 2 fois (email + phone)
        proj = {"_id": 0, "id": 1, "name": 1, "first_name": 1, "last_name": 1,
                "email": 1, "email2": 1, "phone": 1, "phone2": 1,
                "vcs_code": 1, "copropriete_id": 1, "copropriete_ids": 1}

        def _allowed(owner_id):
            return is_super or (allowed_owner_ids is not None and owner_id in allowed_owner_ids)

        def _make_row(field, value, doc):
            return {
                "field": field, "value": value,
                "owner_id": doc.get("id", ""),
                "owner_name": doc.get("name", "") or f"{doc.get('first_name','')} {doc.get('last_name','')}".strip(),
                "owner_first_name": doc.get("first_name", ""),
                "owner_last_name": doc.get("last_name", ""),
                "owner_email": doc.get("email", ""),
                "owner_phone": doc.get("phone", ""),
                "owner_vcs_code": doc.get("vcs_code", ""),
                "copropriete_id": doc.get("copropriete_id", ""),
                "copropriete_ids": doc.get("copropriete_ids", []) or [],
            }

        if email and email.strip():
            found = await db.owners.find(
                {"$or": [{"email": email.strip()}, {"email2": email.strip()}]}, proj
            ).to_list(50)
            for f in found:
                oid = f.get("id", "")
                if _allowed(oid) and oid not in seen_ids:
                    seen_ids.add(oid)
                    duplicates.append(_make_row("email", email, f))
        if phone and phone.strip():
            found = await db.owners.find(
                {"$or": [{"phone": phone.strip()}, {"phone2": phone.strip()}]}, proj
            ).to_list(50)
            for f in found:
                oid = f.get("id", "")
                if _allowed(oid) and oid not in seen_ids:
                    seen_ids.add(oid)
                    duplicates.append(_make_row("phone", phone, f))
        return {"duplicates": duplicates, "has_duplicates": len(duplicates) > 0}

    @router.get("/owners/lookup-vcs")
    async def lookup_vcs(request: Request, vcs: str = ""):
        if not vcs:
            return []
        is_super, allowed_copros = await _get_user_scope(request)
        clean = vcs.replace("+", "").replace("/", "").replace(" ", "").strip()
        results = await db.owners.find(
            {"$or": [
                {"vcs_code": {"$regex": vcs.replace("+", "\\+"), "$options": "i"}},
                {"vcs_digits": clean},
                {"name": {"$regex": vcs, "$options": "i"}},
                {"last_name": {"$regex": vcs, "$options": "i"}},
                {"first_name": {"$regex": vcs, "$options": "i"}},
            ]},
            {"_id": 0}
        ).to_list(50)
        if is_super:
            return results[:20]
        allowed_owner_ids = await _allowed_owner_ids(allowed_copros)
        filtered = [o for o in results if allowed_owner_ids is not None and o.get("id") in allowed_owner_ids]
        return filtered[:20]

    @router.get("/owners/{owner_id}")
    async def get_owner(owner_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            # 404 plutot que 403 pour ne pas leaker l'existence (RGPD)
            raise HTTPException(404, "Proprietaire non trouve")
        return owner

    @router.put("/owners/{owner_id}")
    async def update_owner(owner_id: str, data: OwnerInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            raise HTTPException(404, "Proprietaire non trouve")
        # Check anti-doublon (excluant l'owner en cours d'edition)
        existing_doc = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        copro_id_check = (data.copropriete_id or
                          getattr(request.state, "copropriete_id", "") or
                          (existing_doc or {}).get("copropriete_id", ""))
        dup = await find_duplicate_owner(
            first_name=data.first_name or "", last_name=data.last_name or "",
            name=data.name or "",
            email=data.email or "", phone=data.phone or "",
            bce_number=data.bce_number or "",
            address=data.address or "", postal_code=data.postal_code or "",
            city=data.city or "",
            copro_id=copro_id_check,
            exclude_id=owner_id,
        )
        if dup:
            field_label = {
                "name": "nom + prenom",
                "email": "email",
                "phone": "telephone",
                "bce_number": "numero BCE",
                "address": "adresse postale",
            }.get(dup["field"], dup["field"])
            existing = dup["owner"]
            existing_name = existing.get("name") or f"{existing.get('first_name','')} {existing.get('last_name','')}".strip()
            raise HTTPException(
                409,
                f"Doublon detecte : un autre proprietaire avec le meme {field_label} existe deja "
                f"({existing_name}).",
            )
        full_name = data.name or f"{data.last_name} {data.first_name}".strip()
        result = await db.owners.update_one(
            {"id": owner_id},
            {"$set": {
                "first_name": data.first_name, "last_name": data.last_name, "name": full_name,
                "address": data.address, "postal_code": data.postal_code, "city": data.city,
                "country": data.country, "email": data.email, "email2": data.email2,
                "phone": data.phone, "phone2": data.phone2,
                "bce_number": (data.bce_number or "").strip(),
            }}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or owner.get("copropriete_id", "")
        if copro_id:
            await assign_owner_accounts(db, owner, copro_id)
            owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        return owner

    @router.delete("/owners/{owner_id}")
    async def delete_owner(owner_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        if not is_super and not await _owner_in_scope(owner_id, allowed_copros):
            raise HTTPException(404, "Proprietaire non trouve")

        # === Garde-fou : refuser la suppression si des ecritures comptables ===
        # === ou des donnees liees existent (securite comptable / anti-orphelins).
        blocking = []
        # 1. Ecritures comptables (journal_entries.lines[].third_party_id)
        je_count = await db.journal_entries.count_documents(
            {"lines.third_party_id": owner_id}
        )
        if je_count > 0:
            blocking.append(f"{je_count} ecriture(s) comptable(s) dans les journaux")
        # 2. Lots encore assignes
        lot_count = await db.lots.count_documents(
            {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]}
        )
        if lot_count > 0:
            blocking.append(f"{lot_count} lot(s) encore assigne(s) a ce proprietaire")
        # 3. Appels de fonds referencant l'owner (par lot possede)
        fc_count = await db.fund_calls.count_documents(
            {"details.owner_id": owner_id}
        )
        if fc_count > 0:
            blocking.append(f"{fc_count} appel(s) de fonds")
        # 4. Factures fournisseurs avec third_party_id (rare mais possible)
        inv_count = await db.invoices.count_documents(
            {"third_party_id": owner_id}
        )
        if inv_count > 0:
            blocking.append(f"{inv_count} facture(s) liee(s)")

        if blocking:
            raise HTTPException(
                409,
                "Suppression refusee (securite comptable) : ce proprietaire est reference par "
                + ", ".join(blocking)
                + ". Pour supprimer un proprietaire, il faut d'abord annuler / reaffecter "
                + "toutes les ecritures liees, ou fusionner ce compte avec un autre proprietaire "
                + "(outil Doublons)."
            )

        result = await db.owners.delete_one({"id": owner_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Proprietaire non trouve")
        return {"message": "Proprietaire supprime"}

    # iter90c : rattachement idempotent owner -> ACP, indispensable pour
    # la mutation et tout flow ou un proprio existant doit etre lie a une
    # ACP sans qu'un lot soit encore assigne.
    class AttachToCoproInput(BaseModel):
        copropriete_id: str

    @router.post("/owners/{owner_id}/attach-to-copro")
    async def attach_owner_to_copro(owner_id: str, data: AttachToCoproInput, request: Request):
        """Ajoute idempotemment `copropriete_id` a `owners.copropriete_ids[]`.
        Verifie que le requester a acces a l'ACP cible. Si l'owner est deja
        rattache, retourne 200 OK sans rien faire. Cree aussi le compte
        auxiliaire dans l'ACP si necessaire (via assign_owner_accounts).
        """
        from server import get_current_user, is_superadmin_only
        from tier_accounts import assign_owner_accounts
        user = await get_current_user(request)
        role = user.get("role", "")
        is_super = is_superadmin_only(role)
        allowed = user.get("copropriete_ids", []) or []
        target_copro = (data.copropriete_id or "").strip()
        if not target_copro:
            raise HTTPException(400, "copropriete_id requis")
        if not is_super and target_copro not in allowed:
            raise HTTPException(403, "Acces refuse a cette ACP (chinese wall)")
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")
        # Idempotent : assign_owner_accounts gere le $addToSet sur copropriete_ids
        await assign_owner_accounts(db, owner, target_copro)
        refreshed = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        return {
            "message": "Proprietaire rattache a l'ACP",
            "owner": refreshed,
        }

    # ---- LOTS ----
    class LotInput(BaseModel):
        number: str
        description: Optional[str] = ""
        lot_type: Optional[str] = "apartment"
        floor: Optional[int] = 0
        area: Optional[float] = 0.0
        quotity: Optional[float] = 0.0
        owner_id: Optional[str] = ""
        owner_ids: Optional[List[str]] = []
        copropriete_id: Optional[str] = ""

    @router.get("/lots")
    async def list_lots(request: Request, copropriete_id: Optional[str] = None):
        """Liste des lots - chinese wall STRICT.
        - Superadmin : voit tout
        - Syndic : ne voit que les lots de ses ACPs
        """
        is_super, allowed_copros = await _get_user_scope(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        elif not is_super:
            if not allowed_copros:
                return []
            q["copropriete_id"] = {"$in": allowed_copros}
        lots = await db.lots.find(q, {"_id": 0}).sort("number", 1).to_list(2000)
        return lots

    @router.get("/lots/ownership-audit")
    async def lots_ownership_audit(
        request: Request,
        copropriete_id: str,
        at_date: Optional[str] = None,
        founder_owner_id: Optional[str] = None,
    ):
        """iter90cm : Diagnostic pour identifier les lots dont l'ownership ne
        remonte pas a un proprietaire donne (fondateur/promoteur) a une date
        cible + audit COMPLET des cles de repartition (entrees phantom,
        totaux, orphelins).

        Query params :
        - copropriete_id : ACP a auditer (obligatoire)
        - at_date : date cible ISO YYYY-MM-DD (defaut = aujourd'hui)
        - founder_owner_id : id du proprietaire fondateur attendu (optionnel)
        """
        is_super, allowed_copros = await _get_user_scope(request)
        if not is_super and copropriete_id not in (allowed_copros or []):
            raise HTTPException(403, "Chinese wall: acces refuse")

        from datetime import date as _date_cls
        target = at_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            target_dt = _date_cls.fromisoformat(target)
        except Exception:
            raise HTTPException(400, "at_date doit etre ISO YYYY-MM-DD")

        lots = await db.lots.find(
            {"copropriete_id": copropriete_id}, {"_id": 0},
        ).sort("number", 1).to_list(5000)
        lots_by_id = {lt["id"]: lt for lt in lots}
        owners = await db.owners.find(
            {"copropriete_ids": copropriete_id}, {"_id": 0},
        ).to_list(5000)
        owners_map = {o["id"]: o for o in owners}
        keys = await db.distribution_keys.find(
            {"copropriete_id": copropriete_id}, {"_id": 0},
        ).to_list(1000)

        # --- Audit des cles de repartition ---
        # iter90cm : detecte les anomalies structurelles qui provoquent des
        # ecarts de calcul lors de la distribution (ex : gap 3.6% Acacia).
        keys_audit = []
        for k in keys:
            key_lots = k.get("lots") or []
            active_kls = [kl for kl in key_lots if not kl.get("excluded")]
            total_share_active = sum(float(kl.get("share", 0) or 0) for kl in active_kls)

            phantom_entries = []   # lot_id ne correspond a aucun lot en DB
            orphan_entries = []    # lot existe mais owner_id vide
            valid_entries = []     # lot existe + owner assigne
            for kl in active_kls:
                lid = kl.get("lot_id")
                share = float(kl.get("share", 0) or 0)
                if not lid:
                    continue
                lot = lots_by_id.get(lid)
                if not lot:
                    phantom_entries.append({
                        "lot_id": lid,
                        "share": share,
                        "note": "Lot inexistant en DB (phantom entry)",
                    })
                    continue
                if not lot.get("owner_id"):
                    orphan_entries.append({
                        "lot_id": lid,
                        "lot_number": lot.get("number", ""),
                        "share": share,
                        "note": "Lot existe mais owner_id vide",
                    })
                    continue
                valid_entries.append({
                    "lot_id": lid,
                    "lot_number": lot.get("number", ""),
                    "share": share,
                    "owner_id": lot["owner_id"],
                    "owner_name": owners_map.get(lot["owner_id"], {}).get("name", ""),
                })
            phantom_total = round(sum(e["share"] for e in phantom_entries), 4)
            orphan_total = round(sum(e["share"] for e in orphan_entries), 4)
            valid_total = round(sum(e["share"] for e in valid_entries), 4)
            keys_audit.append({
                "key_id": k["id"],
                "key_name": k.get("name", ""),
                "is_default": bool(k.get("is_default", False)),
                "total_entries": len(active_kls),
                "total_share_active": round(total_share_active, 4),
                "phantom_share": phantom_total,
                "phantom_entries": phantom_entries,
                "orphan_share": orphan_total,
                "orphan_entries": orphan_entries,
                "valid_share": valid_total,
                # Anomalie majeure : phantom OU orphan provoque un ecart dans
                # la distribution car _distribute_amount les exclut du calcul.
                "structural_anomaly": phantom_total > 0 or orphan_total > 0,
                "sums_to_10000": abs(total_share_active - 10000) < 0.01,
            })

        # Map lot_id -> {key_id: share} pour audit rapide par lot
        lot_shares_by_key: dict = {}
        for k in keys:
            for kl in (k.get("lots") or []):
                if kl.get("excluded"):
                    continue
                lid = kl.get("lot_id")
                if not lid:
                    continue
                lot_shares_by_key.setdefault(lid, {})[k["id"]] = {
                    "key_name": k.get("name", ""),
                    "share": float(kl.get("share", 0) or 0),
                    "is_default": bool(k.get("is_default", False)),
                }

        # Prepare db.mutations synced (iter90cj sync defensif)
        async for lot_doc in db.lots.find(
            {"copropriete_id": copropriete_id, "mutations": {"$exists": True, "$ne": []}},
            {"_id": 0, "id": 1, "mutations": 1},
        ):
            for mr in (lot_doc.get("mutations") or []):
                mid = mr.get("id")
                if not mid or not mr.get("date"):
                    continue
                existing = await db.mutations.find_one({"id": mid}, {"_id": 0, "id": 1})
                if not existing:
                    await db.mutations.update_one(
                        {"id": mid},
                        {"$set": {
                            "id": mid, "copropriete_id": copropriete_id,
                            "lot_id": lot_doc["id"],
                            "from_owner_id": mr.get("old_owner_id", ""),
                            "to_owner_id": mr.get("new_owner_id", ""),
                            "sale_date": mr.get("date", ""),
                            "backfilled": True,
                        }},
                        upsert=True,
                    )

        mutations_by_lot: dict = {}
        async for m in db.mutations.find(
            {"copropriete_id": copropriete_id}, {"_id": 0},
        ):
            lid = m.get("lot_id")
            if lid:
                mutations_by_lot.setdefault(lid, []).append(m)
        for lid in mutations_by_lot:
            mutations_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

        def _resolve_owner(lot_id: str, current_owner: str) -> str:
            muts = mutations_by_lot.get(lot_id, [])
            if not muts:
                return current_owner
            current = muts[0].get("from_owner_id") or current_owner
            for m in muts:
                try:
                    sd = _date_cls.fromisoformat(m.get("sale_date") or "")
                except Exception:
                    continue
                if sd <= target_dt:
                    current = m.get("to_owner_id") or current
                else:
                    break
            return current

        result_lots = []
        total_expected = 0.0
        total_actual = 0.0
        flagged = []
        for lot in lots:
            lid = lot["id"]
            current = lot.get("owner_id", "")
            owner_at = _resolve_owner(lid, current)
            muts = mutations_by_lot.get(lid, [])
            has_founder_history = False
            if founder_owner_id:
                if founder_owner_id == current:
                    has_founder_history = True
                for m in muts:
                    if (m.get("from_owner_id") == founder_owner_id
                            or m.get("to_owner_id") == founder_owner_id):
                        has_founder_history = True
                        break
            is_flagged = False
            if founder_owner_id and owner_at != founder_owner_id:
                is_flagged = True
            if founder_owner_id and not has_founder_history:
                is_flagged = True
            row = {
                "lot_id": lid,
                "lot_number": lot.get("number", ""),
                "quotity": float(lot.get("quotity", 0) or 0),
                "current_owner_id": current,
                "current_owner_name": owners_map.get(current, {}).get("name", "MISSING"),
                "owner_at_date": owner_at,
                "owner_at_date_name": owners_map.get(owner_at, {}).get("name", "MISSING"),
                "mutations": [
                    {
                        "date": m.get("sale_date", ""),
                        "from_owner_id": m.get("from_owner_id", ""),
                        "from_owner_name": owners_map.get(m.get("from_owner_id", ""), {}).get("name", ""),
                        "to_owner_id": m.get("to_owner_id", ""),
                        "to_owner_name": owners_map.get(m.get("to_owner_id", ""), {}).get("name", ""),
                    } for m in muts
                ],
                "per_key": lot_shares_by_key.get(lid, {}),
                "has_founder_history": has_founder_history,
                "flagged": is_flagged,
            }
            result_lots.append(row)
            # Aggregate default key quotity for gap analysis
            default_share = 0.0
            for kid, kinfo in (lot_shares_by_key.get(lid) or {}).items():
                if kinfo.get("is_default"):
                    default_share = kinfo.get("share", 0.0)
                    break
            if founder_owner_id:
                total_expected += default_share
                if owner_at == founder_owner_id:
                    total_actual += default_share
                else:
                    flagged.append({
                        "lot_id": lid,
                        "lot_number": lot.get("number", ""),
                        "current_owner_name": row["current_owner_name"],
                        "default_key_share": default_share,
                    })
        return {
            "copropriete_id": copropriete_id,
            "at_date": target,
            "founder_owner_id": founder_owner_id,
            "founder_owner_name": (owners_map.get(founder_owner_id or "", {}) or {}).get("name", ""),
            "total_lots": len(result_lots),
            "total_quotity_expected_founder": round(total_expected, 4),
            "total_quotity_actual_founder": round(total_actual, 4),
            "gap_quotity": round(total_expected - total_actual, 4),
            "flagged_count": len(flagged),
            "flagged_lots": flagged,
            "keys_audit": keys_audit,
            "lots": result_lots,
        }

    class RepairFounderOwnershipInput(BaseModel):
        copropriete_id: str
        founder_owner_id: Optional[str] = None  # iter90cp : None = auto-detection
        founder_start_date: Optional[str] = None  # iter90cp : None = 1ere FY.start_date
        dry_run: bool = True
        # iter90cp : ajout Case C (orphelins owner_id="")
        fix_orphans: bool = True

    @router.post("/lots/repair-founder-ownership")
    async def repair_founder_ownership(
        request: Request, data: RepairFounderOwnershipInput,
    ):
        """iter90cm-bis + iter90cp : Repare l'ownership retroactif des lots qui
        ne remontent pas au fondateur/promoteur a la date de reference.

        Trois cas de reparation :
        A) Lot avec mutation history dont muts[0].from_owner_id != founder :
           insere une "foundation mutation" a founder_start_date avec
           from=founder, to=muts[0].from_owner_id. La chaine de mutation
           existante est ainsi prefixee correctement.
        B) Lot sans mutation ET owner_id != founder :
           insere une mutation founder_start_date -> current_owner
           (represente l'assignation initiale du lot au buyer si le lot avait
           ete cree directement chez le buyer sans passer par mutate_lot).
        C) iter90cp : Lot ORPHELIN (owner_id vide ou None) :
           set lot.owner_id = founder_owner_id (le lot est repris par le
           fondateur au premier jour de l'exercice). Aucune mutation creee
           car il n'y a pas de transfert - c'est une simple assignation
           initiale d'un lot "oublie" au fondateur.

        Auto-detection iter90cp :
        - founder_owner_id=None => proprietaire qui apparait le plus souvent
          comme muts[0].from_owner_id (chain founders) ou lot.owner_id
          (foundation direct). En cas d'egalite, prend celui qui a le plus
          gros nombre total (mut+direct).
        - founder_start_date=None => date de debut du 1er fiscal_year de l'ACP.

        dry_run=True : retourne le rapport de ce qui SERAIT fait sans commit.
        dry_run=False : execute les inserts + assignations orphelins.

        Superadmin only (modification retroactive de l'historique).
        """
        from datetime import date as _date_cls
        is_super, _ = await _get_user_scope(request)
        if not is_super:
            raise HTTPException(403, "Superadmin uniquement")

        # Preload lots et fiscal_years pour auto-detection
        lots = await db.lots.find(
            {"copropriete_id": data.copropriete_id}, {"_id": 0},
        ).to_list(5000)

        # iter90cp : auto-detection du fondateur si non fourni
        founder_owner_id = data.founder_owner_id
        auto_detected = False
        if not founder_owner_id:
            from collections import Counter
            counter = Counter()
            for lot in lots:
                muts_sorted = sorted(
                    lot.get("mutations") or [], key=lambda x: x.get("date") or "",
                )
                if muts_sorted:
                    ff = (muts_sorted[0].get("old_owner_id")
                          or muts_sorted[0].get("from_owner_id") or "")
                    if ff:
                        counter[ff] += 1
                elif lot.get("owner_id"):
                    counter[lot["owner_id"]] += 1
            if not counter:
                raise HTTPException(
                    400,
                    "Auto-detection impossible : aucun lot n'a d'owner_id ni de mutations",
                )
            founder_owner_id = counter.most_common(1)[0][0]
            auto_detected = True

        # iter90cp : auto-fallback founder_start_date si non fourni
        founder_start_date = data.founder_start_date
        if not founder_start_date:
            first_fy = await db.fiscal_years.find_one(
                {"copropriete_id": data.copropriete_id},
                {"_id": 0, "start_date": 1},
                sort=[("start_date", 1)],
            )
            if not first_fy or not first_fy.get("start_date"):
                raise HTTPException(
                    400,
                    "Auto-fallback founder_start_date impossible : aucun fiscal_year defini pour cette ACP",
                )
            founder_start_date = first_fy["start_date"]

        try:
            _date_cls.fromisoformat(founder_start_date)
        except Exception:
            raise HTTPException(400, "founder_start_date doit etre ISO YYYY-MM-DD")

        # Verifie que le fondateur existe et est bien lie a l'ACP
        founder = await db.owners.find_one(
            {"id": founder_owner_id}, {"_id": 0},
        )
        if not founder:
            raise HTTPException(404, "Fondateur introuvable")
        if data.copropriete_id not in (founder.get("copropriete_ids") or []):
            # Assign automatiquement le fondateur a l'ACP si necessaire
            if not data.dry_run:
                await db.owners.update_one(
                    {"id": founder_owner_id},
                    {"$addToSet": {"copropriete_ids": data.copropriete_id}},
                )

        # (lots deja loade plus haut pour auto-detection)

        cases_a = []  # Prefix mutations
        cases_b = []  # Missing foundation mutation
        cases_c = []  # iter90cp : Orphelins owner_id vide
        for lot in lots:
            muts = list(lot.get("mutations") or [])
            muts_sorted = sorted(muts, key=lambda x: x.get("date") or "")
            current_owner = lot.get("owner_id", "")

            # iter90cp : Cas C prealable - lot totalement orphelin (owner_id vide)
            # ET sans mutations : le lot n'a jamais ete assigne. Le fondateur
            # en devient proprietaire au founder_start_date.
            if not current_owner and not muts_sorted:
                if data.fix_orphans:
                    cases_c.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "reason": "lot orphelin (owner_id vide, aucune mutation)",
                    })
                continue

            if muts_sorted:
                first_from = muts_sorted[0].get("old_owner_id") or ""
                if first_from and first_from != founder_owner_id:
                    # Cas A : prefixe une foundation mutation
                    cases_a.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "existing_first_from": first_from,
                        "existing_first_date": muts_sorted[0].get("date", ""),
                    })
                elif not first_from and current_owner != founder_owner_id:
                    # Cas rare : mutation avec from vide + owner different
                    cases_b.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "current_owner_id": current_owner,
                        "reason": "first mutation from_owner_id vide",
                    })
            else:
                if current_owner and current_owner != founder_owner_id:
                    # Cas B : lot sans mutation, owner != founder
                    cases_b.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "current_owner_id": current_owner,
                        "reason": "aucune mutation, owner_id != founder",
                    })

        applied = {"cases_a": 0, "cases_b": 0, "cases_c": 0, "errors": []}
        if not data.dry_run:
            for case in cases_a:
                try:
                    mut_id = str(uuid.uuid4())
                    # Founding mutation : from=founder, to=existing_first_from
                    # date=founder_start_date (avant toutes les mutations reelles)
                    foundation_mut = {
                        "id": mut_id,
                        "date": founder_start_date,
                        "old_owner_id": founder_owner_id,
                        "new_owner_id": case["existing_first_from"],
                        "sale_price": 0.0,
                        "roulement_quota": 0.0,
                        "current_period_prorata": 0.0,
                        "prorata_provisions": 0.0,
                        "total_transfer": 0.0,
                        "journal_entry_ids": [],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "foundation_mutation": True,
                        "iter90cm_bis_repair": True,
                    }
                    # Prepend to lot.mutations[]
                    await db.lots.update_one(
                        {"id": case["lot_id"]},
                        {"$push": {"mutations": {"$each": [foundation_mut], "$position": 0}}},
                    )
                    # Miroir dans db.mutations
                    await db.mutations.update_one(
                        {"id": mut_id},
                        {"$set": {
                            **foundation_mut,
                            "copropriete_id": data.copropriete_id,
                            "lot_id": case["lot_id"],
                            "from_owner_id": founder_owner_id,
                            "to_owner_id": case["existing_first_from"],
                            "sale_date": founder_start_date,
                        }},
                        upsert=True,
                    )
                    applied["cases_a"] += 1
                except Exception as e:
                    applied["errors"].append({"lot_id": case["lot_id"], "err": str(e)})

            for case in cases_b:
                try:
                    mut_id = str(uuid.uuid4())
                    foundation_mut = {
                        "id": mut_id,
                        "date": founder_start_date,
                        "old_owner_id": founder_owner_id,
                        "new_owner_id": case["current_owner_id"],
                        "sale_price": 0.0,
                        "roulement_quota": 0.0,
                        "current_period_prorata": 0.0,
                        "prorata_provisions": 0.0,
                        "total_transfer": 0.0,
                        "journal_entry_ids": [],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "foundation_mutation": True,
                        "iter90cm_bis_repair": True,
                    }
                    await db.lots.update_one(
                        {"id": case["lot_id"]},
                        {"$push": {"mutations": {"$each": [foundation_mut], "$position": 0}}},
                    )
                    await db.mutations.update_one(
                        {"id": mut_id},
                        {"$set": {
                            **foundation_mut,
                            "copropriete_id": data.copropriete_id,
                            "lot_id": case["lot_id"],
                            "from_owner_id": founder_owner_id,
                            "to_owner_id": case["current_owner_id"],
                            "sale_date": founder_start_date,
                        }},
                        upsert=True,
                    )
                    applied["cases_b"] += 1
                except Exception as e:
                    applied["errors"].append({"lot_id": case["lot_id"], "err": str(e)})

            # iter90cp : Cas C - orphelins owner_id vide
            for case in cases_c:
                try:
                    await db.lots.update_one(
                        {"id": case["lot_id"]},
                        {"$set": {
                            "owner_id": founder_owner_id,
                            "owner_ids": [founder_owner_id],
                            "iter90cp_repaired_orphan": True,
                            "iter90cp_repaired_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                    applied["cases_c"] += 1
                except Exception as e:
                    applied["errors"].append({"lot_id": case["lot_id"], "err": str(e)})

        return {
            "dry_run": data.dry_run,
            "copropriete_id": data.copropriete_id,
            "founder_owner_id": founder_owner_id,
            "founder_owner_name": (founder or {}).get("name", ""),
            "founder_auto_detected": auto_detected,
            "founder_start_date": founder_start_date,
            "cases_a_count": len(cases_a),
            "cases_a_detail": cases_a,
            "cases_b_count": len(cases_b),
            "cases_b_detail": cases_b,
            "cases_c_count": len(cases_c),
            "cases_c_detail": cases_c,
            "applied": applied,
        }

    @router.post("/lots")
    async def create_lot(data: LotInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or ""
        if not is_super:
            if not copro_id:
                raise HTTPException(400, "Un lot doit etre rattache a une copropriete")
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez creer un lot que pour une de vos ACPs")
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        doc = {
            "id": str(uuid.uuid4()),
            "number": data.number,
            "description": data.description,
            "lot_type": data.lot_type,
            "floor": data.floor,
            "area": data.area,
            "quotity": data.quotity,
            "owner_id": ids[0] if ids else "",
            "owner_ids": ids,
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.lots.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/lots/{lot_id}")
    async def update_lot(lot_id: str, data: LotInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Lot non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Lot non trouve")
        ids = data.owner_ids if data.owner_ids else ([data.owner_id] if data.owner_id else [])
        new_owner_id = ids[0] if ids else ""

        # iter90cm : PROTECTION structurelle contre les changements d'ownership
        # non traces. Empêche la creation retroactive de lots "phantom".
        # Un changement d'ownership DOIT passer par POST /lots/{id}/mutate qui
        # cree la mutation history requise pour les calculs comptables.
        existing_owner = existing.get("owner_id", "") or ""
        existing_owner_ids = existing.get("owner_ids") or (
            [existing_owner] if existing_owner else []
        )
        new_owner_ids = ids
        # Comparaison ensembliste (l'ordre n'importe pas mais le contenu si)
        if (existing_owner_ids and new_owner_ids
                and set(existing_owner_ids) != set(new_owner_ids)):
            raise HTTPException(
                400,
                "Impossible de changer le proprietaire via PUT /lots. Utilisez "
                "l'action 'Mutation' (POST /lots/{id}/mutate) qui cree "
                "automatiquement l'historique de mutation requis pour la "
                "coherence comptable (OD MUT-R, MUT-P, prorata provisions). "
                f"Owner actuel : {existing_owner_ids}, tentative : {new_owner_ids}."
            )
        # Cas special : owner passe de "" (lot nouveau, non-assigne) a un
        # proprietaire -> autorise sans mutation (assignation initiale).
        # Note : si owner_ids etait rempli et devient vide, autorise aussi
        # (retour a l'etat non-assigne, sans historique).

        update = {
            "number": data.number, "description": data.description,
            "lot_type": data.lot_type, "floor": data.floor,
            "area": data.area, "quotity": data.quotity,
            "owner_id": new_owner_id,
            "owner_ids": ids,
        }
        result = await db.lots.update_one({"id": lot_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return await db.lots.find_one({"id": lot_id}, {"_id": 0})

    @router.delete("/lots/{lot_id}")
    async def delete_lot(lot_id: str):
        result = await db.lots.delete_one({"id": lot_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Lot non trouve")
        return {"message": "Lot supprime"}

    # ---- MUTATION (vente / changement de proprietaire) ----
    class LotMutationInput(BaseModel):
        new_owner_id: str
        sale_date: str  # ISO YYYY-MM-DD
        sale_price: Optional[float] = 0.0
        note: Optional[str] = ""
        # iter90dk : lots supplementaires appartenant au meme vendeur qui sont
        # aussi cedes lors de cette mutation (checklist frontend). Le lot cible
        # (lot_id du path) ET ses enfants parent-lot sont TOUJOURS inclus. Ce
        # champ permet en plus d'inclure des lots INDEPENDANTS du meme vendeur
        # (ex : parking non lie parent-enfant a l'appart).
        additional_lot_ids: List[str] = []

    # ---- LIENS ENTRE LOTS (regroupements appartement + cave/parking) ----
    class LotLinkInput(BaseModel):
        child_lot_ids: List[str]  # IDs des lots a lier comme enfants du parent

    @router.post("/lots/{parent_id}/link")
    async def link_lots(parent_id: str, data: LotLinkInput):
        """Lie des lots enfants (caves, parkings...) a un lot parent (appartement).
        Contraintes :
        - Le parent ne doit pas etre deja un enfant (pas de chaine).
        - Tous les enfants doivent appartenir au meme proprietaire que le parent.
        - Tous les lots doivent etre dans la meme ACP.
        - Un enfant ne peut pas se lier a lui-meme.
        - Un enfant deja lie ailleurs doit etre delie avant.
        """
        parent = await db.lots.find_one({"id": parent_id}, {"_id": 0})
        if not parent:
            raise HTTPException(404, "Lot parent non trouve")
        if parent.get("parent_lot_id"):
            raise HTTPException(400, "Ce lot est deja un lot enfant - impossible de creer une chaine de liens")
        if not parent.get("owner_id"):
            raise HTTPException(400, "Le lot parent n'a pas de proprietaire - assignez-en un avant de lier")

        copro_id = parent.get("copropriete_id", "")
        owner_id = parent.get("owner_id", "")
        added = []
        for child_id in data.child_lot_ids:
            if child_id == parent_id:
                raise HTTPException(400, "Un lot ne peut pas se lier a lui-meme")
            child = await db.lots.find_one({"id": child_id}, {"_id": 0})
            if not child:
                raise HTTPException(404, f"Lot enfant {child_id} non trouve")
            if child.get("copropriete_id") != copro_id:
                raise HTTPException(400, f"Lot {child.get('number','?')} : ACP differente du parent")
            if child.get("owner_id") != owner_id:
                raise HTTPException(400, f"Lot {child.get('number','?')} : proprietaire different du parent (parent={owner_id}, enfant={child.get('owner_id') or 'aucun'})")
            if child.get("parent_lot_id"):
                if child["parent_lot_id"] == parent_id:
                    continue  # deja lie a ce parent, idempotent
                other_parent = await db.lots.find_one({"id": child["parent_lot_id"]}, {"_id": 0, "number": 1})
                raise HTTPException(400, f"Lot {child.get('number','?')} : deja lie au lot {other_parent.get('number','?') if other_parent else '?'} - deliez-le d'abord")
            # Verifie que l'enfant n'est pas lui-meme un parent
            grandkids = await db.lots.count_documents({"parent_lot_id": child_id})
            if grandkids:
                raise HTTPException(400, f"Lot {child.get('number','?')} : est deja un lot parent (a {grandkids} enfants). Impossible de creer une chaine.")
            await db.lots.update_one({"id": child_id}, {"$set": {"parent_lot_id": parent_id}})
            added.append(child_id)
        updated_parent = await db.lots.find_one({"id": parent_id}, {"_id": 0})
        children = await db.lots.find({"parent_lot_id": parent_id}, {"_id": 0}).to_list(100)
        return {"parent": updated_parent, "children": children, "added": added}

    @router.post("/lots/{parent_id}/unlink")
    async def unlink_lots(parent_id: str, data: LotLinkInput):
        """Delie un ou plusieurs lots enfants du parent."""
        removed = []
        for child_id in data.child_lot_ids:
            result = await db.lots.update_one(
                {"id": child_id, "parent_lot_id": parent_id},
                {"$unset": {"parent_lot_id": ""}}
            )
            if result.modified_count > 0:
                removed.append(child_id)
        updated_parent = await db.lots.find_one({"id": parent_id}, {"_id": 0})
        children = await db.lots.find({"parent_lot_id": parent_id}, {"_id": 0}).to_list(100)
        return {"parent": updated_parent, "children": children, "removed": removed}

    async def _resolve_call_period(call: dict, fy_by_id: dict) -> tuple:
        """Retourne (period_start_date, period_end_date) pour un appel.
        Priorite : period_start/period_end stockes > deduction depuis nom + fy
        > [date, date+90j] fallback."""
        ps = call.get("period_start")
        pe = call.get("period_end")
        if ps and pe:
            try:
                return (datetime.strptime(ps, "%Y-%m-%d").date(),
                        datetime.strptime(pe, "%Y-%m-%d").date())
            except Exception:
                pass
        import re as _re
        try:
            start_dt = datetime.strptime(call.get("date", ""), "%Y-%m-%d").date()
        except Exception:
            return None, None
        m = _re.search(r"(\d+)\s*/\s*(\d+)", call.get("name", "") or "")
        if m:
            try:
                n_calls = int(m.group(2))
                if n_calls in (1, 2, 3, 4, 6, 12) and n_calls > 0:
                    interval = 12 // n_calls
                    fy = fy_by_id.get(call.get("fiscal_year_id", ""))
                    try:
                        year = start_dt.year
                        month = start_dt.month + interval
                        while month > 12:
                            month -= 12
                            year += 1
                        try:
                            next_start = start_dt.replace(year=year, month=month)
                        except ValueError:
                            from calendar import monthrange
                            last_day = monthrange(year, month)[1]
                            next_start = start_dt.replace(year=year, month=month, day=min(start_dt.day, last_day))
                        end_dt = next_start - timedelta(days=1)
                        if fy:
                            try:
                                fy_end_dt = datetime.strptime(fy["end_date"], "%Y-%m-%d").date()
                                if end_dt > fy_end_dt:
                                    end_dt = fy_end_dt
                            except Exception:
                                pass
                        return start_dt, end_dt
                    except Exception:
                        pass
            except (ValueError, ZeroDivisionError):
                pass
        return start_dt, start_dt + timedelta(days=90)

    async def _compute_lot_amount_in_call(call: dict, lot_id_this: str, lot_doc: dict,
                                          keys_cache: dict, all_lots_cache: dict) -> float:
        """Calcule la quote-part EXACTE d'un lot dans un appel de fonds.

        Ordre de resolution :
        1. Si `distribution` contient des entrees avec `lot_id`, sommer celles
           qui matchent le lot mute (cas explicite, le plus precis).
        2. Sinon, si l'appel a `lines` (budget lines avec distribution_key_id),
           recalculer via chaque cle : line.amount * (lot_share / total_shares).
        3. Sinon (legacy), repartir le montant aggrege par owner aux quotites
           des lots du proprietaire dans l'ACP.
        """
        # Cas 1 : distribution avec lot_id explicite
        dist = call.get("distribution") or []
        lot_entries = [d for d in dist if d.get("lot_id") == lot_id_this]
        if lot_entries:
            return round(sum(float(d.get("amount", 0) or 0) for d in lot_entries), 2)

        # Cas 2 : pas de lot_id dans distribution mais on a les budget lines
        lines = call.get("lines") or []
        if lines:
            lot_total = 0.0
            for ln in lines:
                key_id = ln.get("distribution_key_id", "")
                line_amt = float(ln.get("amount", 0) or 0)
                if line_amt <= 0:
                    continue
                if key_id:
                    if key_id not in keys_cache:
                        keys_cache[key_id] = await db.distribution_keys.find_one({"id": key_id}, {"_id": 0})
                    key = keys_cache[key_id]
                    if key and key.get("lots"):
                        lot_entry = next((kl for kl in key["lots"] if kl["lot_id"] == lot_id_this), None)
                        if lot_entry:
                            total_shares = sum(float(kl.get("share", 0) or 0) for kl in key["lots"])
                            if total_shares > 0:
                                lot_total += line_amt * (float(lot_entry["share"]) / total_shares)
                else:
                    # Pas de cle -> repartition aux quotites globales
                    lot_quot = float(lot_doc.get("quotity", 0) or 0)
                    if "all_lots_quotity_total" not in all_lots_cache:
                        all_lots_cache["all_lots_quotity_total"] = sum(
                            float(lt.get("quotity", 0) or 0)
                            for lt in all_lots_cache.get("all_lots", [])
                        )
                    total_q = all_lots_cache["all_lots_quotity_total"]
                    if total_q > 0 and lot_quot > 0:
                        lot_total += line_amt * (lot_quot / total_q)
            return round(lot_total, 2)

        # Cas 3 (legacy fallback) : on prend la part owner aggregee du call et on
        # repartit aux quotites des lots du proprietaire dans l'ACP.
        owner_id = lot_doc.get("owner_id", "")
        # Somme TOUS les entries de cet owner (un owner peut avoir N entries)
        owner_amount = sum(float(d.get("amount", 0) or 0) for d in dist if d.get("owner_id") == owner_id)
        if owner_amount <= 0:
            return 0.0
        # Quotities totales du proprietaire dans cette ACP
        if "owner_lots_quotity" not in all_lots_cache:
            all_lots_cache["owner_lots_quotity"] = {}
        if owner_id not in all_lots_cache["owner_lots_quotity"]:
            owner_lots = [lt for lt in all_lots_cache.get("all_lots", []) if lt.get("owner_id") == owner_id]
            all_lots_cache["owner_lots_quotity"][owner_id] = sum(float(lt.get("quotity", 0) or 0) for lt in owner_lots)
        owner_total_q = all_lots_cache["owner_lots_quotity"][owner_id]
        lot_quot = float(lot_doc.get("quotity", 0) or 0)
        if owner_total_q <= 0 or lot_quot <= 0:
            return 0.0
        return round(owner_amount * (lot_quot / owner_total_q), 2)

    async def _compute_mutation_breakdown(lot: dict, old_owner_id: str, sale_dt) -> dict:
        """Compute the full mutation breakdown WITHOUT persisting anything.
        Returns a dict with three explicit sections:
        - Fonds de roulement (capital transfer between seller and buyer)
        - Prorata appel en cours (call covering sale_date, only the days_after portion)
        - Appels de provisions futurs (calls with period_start > sale_dt, informational)

        IMPORTANT (iter83 fix) : le prorata + futurs sont calcules sur la quote-part
        DU LOT muté uniquement (et non sur l'ensemble des lots du vendeur dans
        l'appel). On filtre la distribution par `lot_id == lot["id"]`.
        """
        copro_id = lot.get("copropriete_id", "")
        lot_id_this = lot.get("id", "")

        # ---- 1) Quote-part fonds de roulement (compte 100) ----
        roul_pipeline = [
            {"$match": {"copropriete_id": copro_id}},
            {"$unwind": "$lines"},
            {"$match": {"lines.account_number": "100"}},
            {"$group": {"_id": None,
                        "credit": {"$sum": "$lines.credit"},
                        "debit": {"$sum": "$lines.debit"}}},
        ]
        agg = await db.journal_entries.aggregate(roul_pipeline).to_list(1)
        fonds_roul_posted = round((agg[0]["credit"] - agg[0]["debit"]) if agg else 0.0, 2)

        # iter90cj (Root Cause 2) : le fonds de roulement engage par l'AG ne
        # correspond PAS forcement aux ecritures deja postees. Si la mutation
        # intervient avant que les appels de roulement aient ete generes,
        # posted_roulement = 0 alors qu'un capital est engage par le budget.
        # Regle metier : le vendeur est engage sur le capital roulement vote,
        # meme si l'appel n'a pas encore ete emis (art. 3.86 CDE, capital
        # permanent). On prend donc max(posted, budgeted).
        budgeted_roulement = 0.0
        try:
            sale_dt_iso = sale_dt.isoformat() if hasattr(sale_dt, "isoformat") else str(sale_dt)
            # Trouve tous les FY (approuves/ouverts) couvrant sale_dt
            fys_covering = [
                fy async for fy in db.fiscal_years.find(
                    {"copropriete_id": copro_id,
                     "start_date": {"$lte": sale_dt_iso},
                     "end_date": {"$gte": sale_dt_iso}},
                    {"_id": 0, "id": 1},
                )
            ]
            fy_ids = [fy["id"] for fy in fys_covering]
            if fy_ids:
                # Budget vote (approved) le plus recent pour cette FY
                budget_covering = await db.budgets.find_one(
                    {"copropriete_id": copro_id,
                     "fiscal_year_id": {"$in": fy_ids},
                     "status": "approved"},
                    {"_id": 0},
                    sort=[("approved_at", -1)],
                )
                if budget_covering:
                    budgeted_roulement = float(
                        budget_covering.get("roulement_fund_amount", 0) or 0
                    )
        except Exception as _e:
            print(f"[iter90cj] budget lookup for roulement failed (soft): {_e}")

        fonds_roul_total = round(max(fonds_roul_posted, budgeted_roulement), 2)

        # iter90ab : fonds de roulement / reserve = cle de repartition GENERALE
        # (celle marquee is_default=true), et non plus lot.quotity.
        # Regle metier : provisions -> cle par ligne budgetaire (via
        # _compute_lot_amount_in_call, INCHANGE). Roulement/reserve -> cle par
        # defaut, avec possibilite d'exception future.
        default_key = await db.distribution_keys.find_one(
            {"copropriete_id": copro_id, "is_default": True},
            {"_id": 0},
        )
        if not default_key or not default_key.get("lots"):
            raise HTTPException(
                400,
                "Aucune cle de repartition par defaut trouvee (ou cle sans lots). "
                "Marquez une cle comme 'par defaut' dans Factures > Cles de repartition.",
            )
        lot_entry_in_key = next(
            (kl for kl in default_key["lots"] if kl.get("lot_id") == lot_id_this),
            None,
        )
        if not lot_entry_in_key:
            # Iter90df : fallback match par lot_number normalise (sans zeros
            # en tete). Robuste apres re-creation/re-import de lots (Optipro)
            # ou apres migration : le lot.id (UUID) peut changer mais le
            # lot_number reste stable.
            def _norm(v):
                return str(v or "").lstrip("0") or "0"
            lot_num_norm = _norm(lot.get("number"))
            lot_entry_in_key = next(
                (kl for kl in default_key["lots"]
                 if _norm(kl.get("lot_number")) == lot_num_norm),
                None,
            )
        if not lot_entry_in_key:
            raise HTTPException(
                400,
                f"Le lot {lot.get('number', '?')} n'est pas dans la cle par defaut "
                f"\u00ab {default_key.get('name', '?')} \u00bb. Ajoutez-le a la cle ou "
                f"definissez une exception pour cette mutation.",
            )
        # iter90ac : denominateur = somme des shares des lots NON exclus.
        # Un lot exclu ne participe pas au calcul de cette cle.
        key_total_quotity = round(
            sum(float(kl.get("share", 0) or 0)
                for kl in default_key["lots"] if not kl.get("excluded")),
            6,
        )
        lot_excluded_from_key = bool(lot_entry_in_key.get("excluded"))
        if lot_excluded_from_key:
            # Lot explicitement exclu -> aucune quote-part de roulement transferee.
            lot_share_in_key = 0.0
            roulement_quota = 0.0
        else:
            lot_share_in_key = float(lot_entry_in_key.get("share", 0) or 0)
            if key_total_quotity > 0 and lot_share_in_key > 0 and fonds_roul_total > 0:
                roulement_quota = round(fonds_roul_total * (lot_share_in_key / key_total_quotity), 2)
            else:
                roulement_quota = 0.0

        # conservé pour la suite (prorata appels, etc.)
        all_lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0, "quotity": 1, "id": 1, "owner_id": 1}).to_list(10000)

        # Pre-fetch fiscal years for period resolution
        fy_by_id: dict = {}
        async for _fy in db.fiscal_years.find({"copropriete_id": copro_id}, {"_id": 0}):
            fy_by_id[_fy["id"]] = _fy

        # All provisions calls for this ACP
        calls = await db.fund_calls.find(
            {"copropriete_id": copro_id}, {"_id": 0}
        ).sort("date", 1).to_list(10000)
        # Regle metier : seules les PROVISIONS participent au prorata et aux appels futurs.
        # Fonds de reserve : jamais de transfert vendeur/acheteur.
        # Fonds de roulement : traite via le bloc 1 (transfert capital, date mutation).
        calls = [c for c in calls if (c.get("call_type") or "provisions") == "provisions"]

        # Caches pour eviter de recharger les cles a chaque iteration
        keys_cache: dict = {}
        all_lots_cache: dict = {"all_lots": all_lots}

        # ---- 2) Prorata sur l'appel en cours (couvrant sale_date) ----
        # ---- 3) Appels de provisions futurs (period_start > sale_dt) ----
        current_prorata_total = 0.0
        current_prorata_details = []
        future_calls = []
        future_calls_total = 0.0
        for c in calls:
            c_start, c_end = await _resolve_call_period(c, fy_by_id)
            if not c_start or not c_end or c_end < c_start:
                continue
            # CORRECT : calcul de la quote-part DU LOT mute en re-resolvant via cle
            amount_lot = await _compute_lot_amount_in_call(c, lot_id_this, lot, keys_cache, all_lots_cache)
            if amount_lot <= 0.001:
                continue

            # iter90ai : exclure la part fonds de reserve de la quote-part lot AVANT
            # prorata et appel futur. Regle metier : le fonds de reserve ne fait JAMAIS
            # l'objet d'un transfert entre vendeur et acheteur (ni via OD de mutation,
            # ni via prorata). Cas legacy : appel call_type='provisions' avec
            # reserve_amount > 0 injecte -> soustraire prorata de la part reserve.
            c_reserve = float(c.get("reserve_amount", 0) or 0)
            c_total = float(c.get("total_amount", 0) or 0)
            if c_reserve > 0 and c_total > 0:
                amount_lot = round(amount_lot * ((c_total - c_reserve) / c_total), 2)
                if amount_lot <= 0.001:
                    continue

            if c_start <= sale_dt <= c_end:
                total_days = (c_end - c_start).days + 1
                days_after = (c_end - sale_dt).days + 1
                if total_days <= 0:
                    continue
                prorata = round(amount_lot * (days_after / total_days), 2)
                if prorata < 0.01:
                    continue
                current_prorata_total += prorata
                current_prorata_details.append({
                    "fund_call_id": c.get("id"),
                    "fund_call_name": c.get("name", ""),
                    "call_date": c.get("date", ""),  # date originale de l'appel (pour ecriture OD)
                    "period_start": c_start.isoformat(),
                    "period_end": c_end.isoformat(),
                    "lot_amount": amount_lot,
                    "owner_amount": amount_lot,  # alias retro-compat
                    "prorata": prorata,
                    "days_after": days_after,
                    "total_days": total_days,
                })
            elif c_start > sale_dt:
                future_calls.append({
                    "fund_call_id": c.get("id"),
                    "fund_call_name": c.get("name", ""),
                    "date": c.get("date", ""),
                    "due_date": c.get("due_date", ""),
                    "period_start": c_start.isoformat(),
                    "period_end": c_end.isoformat(),
                    "amount": amount_lot,
                })
                future_calls_total += amount_lot

        current_prorata_total = round(current_prorata_total, 2)
        future_calls_total = round(future_calls_total, 2)

        # Detect budget frequency for display (use the first future or current call)
        budget_frequency = None
        budget_frequency_label = ""
        import re as _re
        for c in calls:
            m = _re.search(r"(\d+)\s*/\s*(\d+)", c.get("name", "") or "")
            if m:
                try:
                    n = int(m.group(2))
                    if n in (1, 2, 3, 4, 6, 12):
                        budget_frequency = n
                        budget_frequency_label = {
                            1: "Annuel", 2: "Semestriel", 3: "Quadrimestriel",
                            4: "Trimestriel", 6: "Bi-mensuel", 12: "Mensuel",
                        }[n]
                        break
                except ValueError:
                    pass

        total_transfer = round(roulement_quota + current_prorata_total, 2)

        return {
            # Section 1 : Fonds de roulement (base = cle de repartition par defaut)
            "fonds_roulement_total": fonds_roul_total,
            "lot_share_in_key": lot_share_in_key,
            "key_total_quotity": key_total_quotity,
            "default_key_name": default_key.get("name", ""),
            "lot_excluded_from_key": lot_excluded_from_key,
            "roulement_quota": roulement_quota,
            # Section 2 : Prorata appel en cours (portion apres vente, transferee
            # de l'acquereur vers le vendeur via OD)
            "current_period_prorata": current_prorata_total,
            "current_period_details": current_prorata_details,
            # Alias retrocompatibles (anciens noms utilises par le frontend)
            "prorata_provisions": current_prorata_total,
            "prorata_details": current_prorata_details,
            # Section 3 : Appels de provisions futurs a prevoir (informatif)
            "future_calls": future_calls,
            "future_calls_total": future_calls_total,
            "budget_frequency": budget_frequency,
            "budget_frequency_label": budget_frequency_label,
            # Total ECRITURE OD (roulement + prorata appel en cours uniquement)
            "total_transfer": total_transfer,
        }

    @router.get("/lots/{lot_id}/mutation-candidates")
    async def get_mutation_candidates(lot_id: str):
        """iter90dk : Liste des lots pouvant etre mutes en meme temps que
        le lot cible (checklist frontend). Le decoupage :

        - `primary_lot`  : le lot cible (obligatoirement mute, non decochable).
        - `children`     : lots enfants (parent_lot_id = lot_id).
                           Pre-selectionnes et non decochables (lien technique).
        - `other_owner_lots` : autres lots INDEPENDANTS du meme proprietaire.
                           Pre-selectionnes = false. L'utilisateur coche ceux
                           qu'il souhaite muter simultanement.

        Refuse si `lot_id` est un enfant (parent_lot_id non vide) : il faut
        muter le lot parent.
        """
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        if lot.get("parent_lot_id"):
            other = await db.lots.find_one(
                {"id": lot["parent_lot_id"]}, {"_id": 0, "number": 1},
            )
            raise HTTPException(400, (
                f"Ce lot est lie au lot parent {other.get('number','?') if other else '?'} - "
                "mutez le parent."
            ))
        old_owner_id = lot.get("owner_id", "")
        copro_id = lot.get("copropriete_id", "")
        if not old_owner_id:
            return {"primary_lot": lot, "children": [], "other_owner_lots": []}

        # Enfants (parent_lot_id = lot_id) - toujours pre-selectionnes.
        children = await db.lots.find(
            {"parent_lot_id": lot_id},
            {"_id": 0, "id": 1, "number": 1, "type": 1, "unit": 1,
             "quotity": 1, "owner_id": 1},
        ).to_list(100)

        # Autres lots INDEPENDANTS du meme proprietaire.
        child_ids = [c["id"] for c in children]
        others_cursor = db.lots.find(
            {
                "copropriete_id": copro_id,
                "owner_id": old_owner_id,
                "id": {"$nin": [lot_id] + child_ids},
                # exclure lots dont parent est deja dans notre groupe (evite doublons)
                "$or": [
                    {"parent_lot_id": {"$exists": False}},
                    {"parent_lot_id": ""},
                    {"parent_lot_id": None},
                ],
            },
            {"_id": 0, "id": 1, "number": 1, "type": 1, "unit": 1,
             "quotity": 1, "parent_lot_id": 1},
        ).sort("number", 1)
        other_owner_lots = await others_cursor.to_list(500)

        # Compact primary lot response
        primary_lot = {
            "id": lot.get("id"),
            "number": lot.get("number"),
            "type": lot.get("type"),
            "unit": lot.get("unit"),
            "quotity": lot.get("quotity"),
        }
        return {
            "primary_lot": primary_lot,
            "children": children,
            "other_owner_lots": other_owner_lots,
        }

    @router.post("/lots/{lot_id}/mutate")
    async def mutate_lot(lot_id: str, data: LotMutationInput):
        """Mutation d'un lot (vente entre proprietaires). Calcule et passe l'OD
        comptable de transfert.

        Si le lot a des enfants lies (autres lots dont parent_lot_id = lot_id),
        la mutation est appliquee a TOUS les lots du groupe (mutation groupee).
        Chaque lot recoit sa propre ecriture OD et son propre mutation_record.

        iter90dk : `data.additional_lot_ids` permet en plus d'inclure des lots
        INDEPENDANTS du meme vendeur (checklist frontend). Les lots doivent
        appartenir au meme old_owner_id ET a la meme ACP, sinon 400.

        Refuse si le lot vise est un enfant (parent_lot_id non vide) :
        l'utilisateur doit muter le lot parent pour declencher la mutation groupee.
        """
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        if lot.get("parent_lot_id"):
            other = await db.lots.find_one({"id": lot["parent_lot_id"]}, {"_id": 0, "number": 1})
            raise HTTPException(400, f"Ce lot est lie au lot parent {other.get('number','?') if other else '?'} - mutez le parent pour effectuer la mutation groupee")
        copro_id = lot.get("copropriete_id", "")
        if not copro_id:
            raise HTTPException(400, "Lot sans copropriete")
        old_owner_id = lot.get("owner_id", "")
        if not old_owner_id:
            raise HTTPException(400, "Lot sans proprietaire actuel - impossible de muter")
        if data.new_owner_id == old_owner_id:
            raise HTTPException(400, "L'acquereur doit etre different du proprietaire actuel")

        old_owner = await db.owners.find_one({"id": old_owner_id}, {"_id": 0})
        new_owner = await db.owners.find_one({"id": data.new_owner_id}, {"_id": 0})
        if not old_owner:
            raise HTTPException(404, "Proprietaire actuel introuvable")
        if not new_owner:
            raise HTTPException(404, "Acquereur introuvable")

        # Assure les comptes tiers existent dans cette ACP
        old_owner = await assign_owner_accounts(db, old_owner, copro_id)
        new_owner = await assign_owner_accounts(db, new_owner, copro_id)
        old_acc = (old_owner.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
        new_acc = (new_owner.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
        if not old_acc or not new_acc:
            raise HTTPException(500, "Impossible de resoudre les comptes tiers")

        try:
            sale_dt = datetime.strptime(data.sale_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(400, "sale_date doit etre au format YYYY-MM-DD")

        sale_date = data.sale_date
        # Recupere les lots enfants pour la mutation groupee
        children = await db.lots.find({"parent_lot_id": lot_id}, {"_id": 0}).to_list(100)
        # Securite : tous les enfants doivent avoir le meme proprietaire courant
        for ch in children:
            if ch.get("owner_id") != old_owner_id:
                raise HTTPException(400, f"Lot enfant {ch.get('number','?')} a un proprietaire different - lien incoherent. Deliez-le ou alignez les proprietaires.")

        # iter90dk : lots additionnels selectionnes par l'utilisateur (checklist).
        # Doivent appartenir au meme vendeur ET a la meme ACP. Deduplique.
        additional_lots = []
        add_ids = [x for x in (data.additional_lot_ids or []) if x]
        already_ids = {lot_id} | {c["id"] for c in children}
        add_ids = [x for x in add_ids if x not in already_ids]
        if add_ids:
            add_lots_docs = await db.lots.find(
                {"id": {"$in": add_ids}}, {"_id": 0},
            ).to_list(500)
            found_ids = {l["id"] for l in add_lots_docs}
            missing = [x for x in add_ids if x not in found_ids]
            if missing:
                raise HTTPException(400, f"Lot(s) additionnel(s) introuvable(s) : {missing}")
            for al in add_lots_docs:
                if al.get("copropriete_id") != copro_id:
                    raise HTTPException(400, f"Lot {al.get('number','?')} n'appartient pas a la meme ACP")
                if al.get("owner_id") != old_owner_id:
                    raise HTTPException(400, f"Lot {al.get('number','?')} n'a pas le meme vendeur ({old_owner.get('name','?')})")
                if al.get("parent_lot_id"):
                    # Ne devrait pas arriver (l'endpoint mutation-candidates les filtre)
                    raise HTTPException(400, f"Lot {al.get('number','?')} est enfant d'un autre lot - deliez-le d'abord")
                additional_lots.append(al)

        all_lots = [lot] + children + additional_lots

        # ----- Helper iter84++ : regeneration des appels FUTURS apres mutation -----
        async def _regenerate_future_calls_after_mutation(
            lot_id: str, copro_id: str, sale_date_str: str,
            old_owner_id: str, new_owner_id: str,
        ) -> dict:
            """Met a jour les appels FUTURS (date > sale_date) pour cette ACP/FY :

            - PROVISIONS (et autres types courants) : remplace old_owner par
              new_owner dans la distribution + regenere l'ecriture comptable.
            - RESERVE / ROULEMENT (alimentations de fonds permanents classe 1) :
              si un appel du MEME type a deja ete emis AVANT sale_date pour ce
              lot dans la meme FY, le lot est EXCLU de la distribution des
              appels futurs (le vendeur a deja paye, l'acheteur le rembourse
              via la mutation - pas de double ecriture).

            Les appels avec paiement deja recu sont preserves (pas de
            destruction d'historique).
            """
            from auto_entries import generate_sale_entry, _delete_auto_entries
            try:
                sale_dt_local = datetime.strptime(sale_date_str, "%Y-%m-%d").date()
            except Exception:
                return {"error": "sale_date invalide", "fixed": 0}

            # Identifie la FY couvrant la date de vente
            fys = await db.fiscal_years.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(100)
            current_fy = None
            for fy in fys:
                try:
                    fs = datetime.strptime(fy.get("start_date", ""), "%Y-%m-%d").date()
                    fe = datetime.strptime(fy.get("end_date", ""), "%Y-%m-%d").date()
                    if fs <= sale_dt_local <= fe:
                        current_fy = fy
                        break
                except Exception:
                    continue
            if not current_fy:
                return {"info": "Aucune FY couvrant la date de vente", "fixed": 0}

            fy_start_str = current_fy.get("start_date", "")
            fy_end_str = current_fy.get("end_date", "")

            # Tous les appels de la FY pour cette ACP
            calls = await db.fund_calls.find({
                "copropriete_id": copro_id,
                "date": {"$gte": fy_start_str, "$lte": fy_end_str},
            }, {"_id": 0}).sort("date", 1).to_list(2000)

            # Types alimentant un fonds permanent (classe 1) -> exclusion si paye avant
            CAPITAL_TYPES = {"reserve", "roulement"}

            # Pour chaque type "capital", verifie si ce lot a deja eu un appel
            # AVANT sale_date dans cette FY (peu importe paid ou non - le vendeur
            # est legalement engage car l'appel a ete emis avant la vente).
            had_prior_capital_call: dict = {"reserve": False, "roulement": False}
            for c in calls:
                ctype = c.get("call_type", "")
                if ctype not in CAPITAL_TYPES:
                    continue
                if c.get("date", "") > sale_date_str:
                    continue
                # Verifie que LE LOT etait inclus dans la distribution
                for d in (c.get("distribution") or []):
                    if d.get("lot_id") == lot_id:
                        had_prior_capital_call[ctype] = True
                        break

            updated_calls = []
            excluded_calls = []
            skipped_paid = []

            for c in calls:
                # Ne touche que les appels strictement FUTURS
                if c.get("date", "") <= sale_date_str:
                    continue
                ctype = c.get("call_type", "")
                dist = list(c.get("distribution") or [])

                # Cherche les rows concernant CE lot
                lot_rows_idx = [i for i, d in enumerate(dist) if d.get("lot_id") == lot_id]
                if not lot_rows_idx:
                    continue  # Lot pas concerne par cet appel

                # Si AU MOINS une row a deja ete payee : on ne touche pas
                # (preserver historique comptable).
                if any(dist[i].get("paid") for i in lot_rows_idx):
                    skipped_paid.append({"id": c["id"], "name": c.get("name", ""), "date": c.get("date", "")})
                    continue

                if ctype in CAPITAL_TYPES and had_prior_capital_call.get(ctype):
                    # Regle "fonds permanent deja paye par vendeur" : exclure le lot
                    excluded_amount = sum(float(dist[i].get("amount", 0) or 0) for i in lot_rows_idx)
                    for i in sorted(lot_rows_idx, reverse=True):
                        dist.pop(i)
                    new_total = round(sum(float(d.get("amount", 0) or 0) for d in dist), 2)
                    await db.fund_calls.update_one(
                        {"id": c["id"]},
                        {"$set": {"distribution": dist, "total_amount": new_total}},
                    )
                    excluded_calls.append({
                        "id": c["id"], "name": c.get("name", ""), "date": c.get("date", ""),
                        "call_type": ctype, "excluded_amount": round(excluded_amount, 2),
                        "new_total": new_total,
                    })
                else:
                    # Remplace owner_id (et eventuellement vcs_code/owner_name) pour ce lot
                    new_owner = await db.owners.find_one({"id": new_owner_id}, {"_id": 0})
                    new_name = (new_owner or {}).get("name", "") if new_owner else ""
                    new_vcs = (new_owner or {}).get("vcs_code", "") if new_owner else ""
                    for i in lot_rows_idx:
                        dist[i]["owner_id"] = new_owner_id
                        if new_name:
                            dist[i]["owner_name"] = new_name
                        if new_vcs:
                            dist[i]["vcs_code"] = new_vcs
                    await db.fund_calls.update_one(
                        {"id": c["id"]},
                        {"$set": {"distribution": dist}},
                    )
                    updated_calls.append({
                        "id": c["id"], "name": c.get("name", ""), "date": c.get("date", ""),
                        "call_type": ctype, "rows_migrated": len(lot_rows_idx),
                    })

                # Regenere l'ecriture comptable (delete + create)
                try:
                    fresh = await db.fund_calls.find_one({"id": c["id"]}, {"_id": 0})
                    if fresh:
                        await _delete_auto_entries(db, "fund_call", c["id"])
                        await generate_sale_entry(db, fresh)
                except Exception as e:
                    print(f"[mutation-regen] entry regen failed for {c.get('name')}: {e}")

            return {
                "fy_name": current_fy.get("name", ""),
                "fixed": len(updated_calls) + len(excluded_calls),
                "migrated_owner": updated_calls,
                "excluded_capital": excluded_calls,
                "skipped_paid": skipped_paid,
                "had_prior_reserve": had_prior_capital_call["reserve"],
                "had_prior_roulement": had_prior_capital_call["roulement"],
            }

        async def _apply_to_lot(lt: dict) -> dict:
            """Applique la mutation a un seul lot et retourne le mutation_record.

            REGLE FISCALE (iter84) : ecritures eclatees par date d'origine.
              - Fonds de roulement : 1 ecriture OD datee `sale_date` (mutation du capital).
              - Prorata appels en cours : 1 ecriture OD par DATE D'APPEL (agregee si
                plusieurs lignes meme date), pour que les situations de compte
                refletent le bon timing des budgets.
            """
            bd = await _compute_mutation_breakdown(lt, old_owner_id, sale_dt)
            r_quota = bd["roulement_quota"]
            c_prorata = bd["current_period_prorata"]
            t_transfer = bd["total_transfer"]

            journal_entry_ids: list = []
            entries_created: list = []

            def _build_entry(amount: float, entry_date: str, kind: str, label: str, ref_suffix: str) -> dict:
                lines = [
                    {"account_number": new_acc,
                     "account_name": f"Mutation - {new_owner.get('last_name') or new_owner.get('name')}",
                     "debit": amount, "credit": 0.0,
                     "third_party_id": data.new_owner_id,
                     "third_party_name": new_owner.get("name", "")},
                    {"account_number": old_acc,
                     "account_name": f"Mutation - {old_owner.get('last_name') or old_owner.get('name')}",
                     "debit": 0.0, "credit": amount,
                     "third_party_id": old_owner_id,
                     "third_party_name": old_owner.get("name", "")},
                ]
                return {
                    "id": str(uuid.uuid4()),
                    "journal_type": "OD",
                    "date": entry_date,
                    "reference": f"MUT-{lt.get('number','')[:18]}-{ref_suffix}",
                    "description": (
                        f"Mutation lot {lt.get('number','')} - {label}: "
                        f"{old_owner.get('name','')} -> {new_owner.get('name','')} ({amount:.2f} EUR)"
                    ),
                    "lines": lines,
                    "total_debit": amount,
                    "total_credit": amount,
                    "copropriete_id": copro_id,
                    "auto_generated": False,
                    "manually_edited": True,
                    "source_type": "lot_mutation",
                    "source_id": lt["id"],
                    "source_subtype": kind,  # fonds_roulement | prorata
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

            # 1) Fonds de roulement : datee sale_date (transfert du capital permanent)
            if r_quota > 0.001:
                entry = _build_entry(
                    amount=r_quota,
                    entry_date=sale_date,
                    kind="fonds_roulement",
                    label="Fonds de roulement",
                    ref_suffix="R",
                )
                await db.journal_entries.insert_one(entry)
                journal_entry_ids.append(entry["id"])
                entries_created.append({
                    "kind": "fonds_roulement",
                    "id": entry["id"],
                    "date": sale_date,
                    "amount": r_quota,
                })

            # 2) Prorata appel en cours : 1 ecriture par DATE D'APPEL d'origine
            #    (agrege les lignes ayant la meme call_date pour limiter le bruit).
            from collections import defaultdict
            prorata_by_date: dict = defaultdict(list)
            for d in (bd.get("current_period_details") or []):
                cd = d.get("call_date") or sale_date  # fallback si manquant (ancien data)
                prorata_by_date[cd].append(d)
            for entry_date, details in prorata_by_date.items():
                subtotal = round(sum(float(d.get("prorata", 0) or 0) for d in details), 2)
                if subtotal <= 0.001:
                    continue
                call_names = ", ".join(d.get("fund_call_name", "?") for d in details)
                entry = _build_entry(
                    amount=subtotal,
                    entry_date=entry_date,
                    kind="prorata",
                    label=f"Prorata appel ({call_names})",
                    ref_suffix="P",
                )
                await db.journal_entries.insert_one(entry)
                journal_entry_ids.append(entry["id"])
                entries_created.append({
                    "kind": "prorata",
                    "id": entry["id"],
                    "date": entry_date,
                    "amount": subtotal,
                    "fund_call_ids": [d.get("fund_call_id") for d in details],
                })

            # 3) Appels futurs : 1 OD par appel futur, datee a la DATE DE L'APPEL.
            #    Le VE des appels futurs reste au nom du proprietaire ORIGINAL
            #    (vendeur) dans la distribution. La mutation cree une ecriture OD
            #    (DR acheteur / CR vendeur) a la date de chaque appel futur, pour
            #    le montant de la quote-part du lot mute.
            #    Cela permet a la balance de tier de refleter la mutation a chaque
            #    date d'appel (01.01, 01.04, 01.07, 01.10 etc.) peu importe la
            #    date de mutation.
            future_by_date: dict = defaultdict(list)
            for f in (bd.get("future_calls") or []):
                fd = f.get("date") or ""
                if not fd:
                    continue
                future_by_date[fd].append(f)
            for entry_date, details in future_by_date.items():
                subtotal = round(
                    sum(float(d.get("amount", d.get("lot_amount", d.get("owner_amount", 0))) or 0) for d in details),
                    2,
                )
                if subtotal <= 0.001:
                    continue
                call_names = ", ".join(d.get("fund_call_name", "?") for d in details)
                entry = _build_entry(
                    amount=subtotal,
                    entry_date=entry_date,
                    kind="future_call",
                    label=f"Appel futur ({call_names})",
                    ref_suffix="F",
                )
                await db.journal_entries.insert_one(entry)
                journal_entry_ids.append(entry["id"])
                entries_created.append({
                    "kind": "future_call",
                    "id": entry["id"],
                    "date": entry_date,
                    "amount": subtotal,
                    "fund_call_ids": [d.get("fund_call_id") for d in details],
                })

            mut_rec = {
                "id": str(uuid.uuid4()),
                "date": sale_date,
                "old_owner_id": old_owner_id,
                "old_owner_name": old_owner.get("name", ""),
                "new_owner_id": data.new_owner_id,
                "new_owner_name": new_owner.get("name", ""),
                "roulement_quota": r_quota,
                "prorata_provisions": c_prorata,  # alias legacy
                "current_period_prorata": c_prorata,
                "current_period_details": bd["current_period_details"],
                "future_calls": bd["future_calls"],
                "future_calls_total": bd["future_calls_total"],
                "budget_frequency": bd["budget_frequency"],
                "budget_frequency_label": bd["budget_frequency_label"],
                "total_transfer": t_transfer,
                "sale_price": float(data.sale_price or 0) if lt["id"] == lot_id else 0.0,
                "note": data.note or "",
                # journal_entry_id (legacy) = premiere ecriture creee (fonds de roulement
                # si present, sinon premier prorata). journal_entry_ids = liste complete.
                "journal_entry_id": journal_entry_ids[0] if journal_entry_ids else None,
                "journal_entry_ids": journal_entry_ids,
                "entries_created": entries_created,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "prorata_details": bd["current_period_details"],  # alias legacy
                # Lien parent : si ce lot fait partie d'une mutation groupee
                "grouped_mutation": len(all_lots) > 1,
                "grouped_parent_lot_id": lot_id if lt["id"] != lot_id else "",
            }
            await db.lots.update_one(
                {"id": lt["id"]},
                {"$set": {"owner_id": data.new_owner_id, "owner_ids": [data.new_owner_id]},
                 "$push": {"mutations": mut_rec}}
            )

            # iter90cf : DOUBLE-ECRITURE dans db.mutations collection.
            # Historiquement, mutate_lot n'ecrivait que dans lot.mutations. Mais
            # _rebind_owner_at_call_date (fund_calls.py) lit dans db.mutations,
            # laissant les mutations invisibles lors de la generation d'appels
            # post-mutation. On corrige en peuplant les deux endroits.
            # iter90cj : upsert idempotent + log critical si echec (user choix B :
            # ne bloque pas mais alerte). Le backfill defensif dans les lecteurs
            # (_ensure_mutations_synced_for_acp) rattrape la situation a la lecture.
            _mutations_doc = {
                "id": mut_rec["id"],
                "copropriete_id": copro_id,
                "lot_id": lt["id"],
                "from_owner_id": old_owner_id,
                "to_owner_id": data.new_owner_id,
                "sale_date": sale_date,
                "roulement_quota": r_quota,
                "current_period_prorata": c_prorata,
                "total_transfer": t_transfer,
                "journal_entry_ids": journal_entry_ids,
                "created_at": mut_rec["created_at"],
            }
            try:
                await db.mutations.update_one(
                    {"id": mut_rec["id"]}, {"$set": _mutations_doc}, upsert=True,
                )
            except Exception as _me:
                # Log CRITICAL (user choix B). Le backfill defensif dans les
                # lecteurs rattrapera la situation en lisant lot.mutations[].
                import logging
                logging.critical(
                    "[iter90cj][CRITICAL] db.mutations upsert failed for lot=%s "
                    "sale_date=%s mutation_id=%s : %s. Fallback : lot.mutations[] "
                    "reste peuple, _ensure_mutations_synced_for_acp backfill "
                    "a la volee lors des reads suivants.",
                    lt.get("id"), sale_date, mut_rec.get("id"), _me,
                )

            # iter85 : NEUTRALISATION de la regeneration des appels futurs.
            # La nouvelle approche cree une OD (DR acheteur / CR vendeur) a la date
            # de CHAQUE appel futur (cf. bloc "3) Appels futurs" ci-dessus). On ne
            # modifie donc PLUS le owner_id dans la distribution, pour eviter le
            # double comptage avec les ODs futures.
            # Pour les fonds permanents (reserve / roulement) : la logique de
            # capital est portee par l'ecriture "fonds_roulement" datee sale_date.
            # Les appels reserve/roulement futurs restent donc tels quels au nom
            # du vendeur dans la distribution (volonte expresse user iter85).
            mut_rec["regenerated_calls"] = {"info": "future calls now booked via OD per call date (iter85)", "fixed": 0}

            return mut_rec

        all_mutations = []
        agg_total = 0.0
        for lt in all_lots:
            mr = await _apply_to_lot(lt)
            all_mutations.append({"lot_id": lt["id"], "lot_number": lt.get("number", ""), "mutation": mr})
            agg_total += mr["total_transfer"]

        updated_lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        return {
            "lot": updated_lot,
            "mutation": all_mutations[0]["mutation"],  # racine = parent (compat retro)
            "grouped_mutations": all_mutations,
            "linked_lots_count": len(children),
            "grouped_total_transfer": round(agg_total, 2),
        }

    @router.delete("/lots/{lot_id}/mutate/{mutation_id}")
    async def cancel_mutation(lot_id: str, mutation_id: str):
        """Annule la DERNIERE mutation d'un lot : restaure l'ancien proprietaire,
        supprime l'ecriture OD de mutation et retire l'entree d'historique.

        Si la mutation etait GROUPEE (parent + enfants), annule aussi
        automatiquement les mutations synchronisees sur les lots enfants
        (meme date + meme journal_entry source).

        Refuse :
        - si la mutation visee n'est pas la plus recente
        - si on tente d'annuler la mutation depuis un lot enfant (passer par
          le parent)
        """
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        mutations = lot.get("mutations") or []
        if not mutations:
            raise HTTPException(400, "Ce lot n'a aucune mutation a annuler")
        last = mutations[-1]
        if mutation_id != "last" and last.get("id") and last.get("id") != mutation_id:
            raise HTTPException(
                400,
                "Seule la derniere mutation peut etre annulee (les mutations anterieures sont figees)."
            )
        # Refus annulation depuis enfant : doit passer par le parent
        if last.get("grouped_parent_lot_id"):
            parent_id = last["grouped_parent_lot_id"]
            parent = await db.lots.find_one({"id": parent_id}, {"_id": 0, "number": 1})
            raise HTTPException(
                400,
                f"Ce lot fait partie d'une mutation groupee avec le lot parent {parent.get('number','?') if parent else '?'}. "
                f"Annulez la mutation depuis le lot parent pour annuler le groupe entier."
            )

        old_owner_id = last.get("old_owner_id")
        if not old_owner_id:
            raise HTTPException(400, "Mutation sans old_owner_id - impossible de restaurer")

        async def _cancel_single(target_lot_id: str, mutation_record: dict):
            """Annule une mutation pour un lot : supprime TOUTES les ecritures OD
            (fonds de roulement + prorata) + pop l'historique.
            Compatible avec ancien format (journal_entry_id seul) et nouveau
            format (journal_entry_ids liste).
            """
            entry_ids = list(mutation_record.get("journal_entry_ids") or [])
            legacy_id = mutation_record.get("journal_entry_id")
            if legacy_id and legacy_id not in entry_ids:
                entry_ids.append(legacy_id)
            for eid in entry_ids:
                if eid:
                    await db.journal_entries.delete_one({"id": eid})
            await db.lots.update_one(
                {"id": target_lot_id},
                {"$set": {"owner_id": mutation_record.get("old_owner_id"),
                          "owner_ids": [mutation_record.get("old_owner_id")]},
                 "$pop": {"mutations": 1}}
            )
            # iter90cf : synchronisation avec db.mutations collection
            try:
                if mutation_record.get("id"):
                    await db.mutations.delete_one({"id": mutation_record["id"]})
            except Exception as _me:
                print(f"[iter90cf] mutations collection delete failed (soft): {_me}")

        cancelled = [{"lot_id": lot_id, "lot_number": lot.get("number", ""), "mutation_id": last.get("id")}]
        # Annule le parent
        await _cancel_single(lot_id, last)

        # Si c'etait une mutation groupee (parent), annule aussi les enfants
        # dont la DERNIERE mutation a le meme grouped_parent_lot_id == lot_id
        # ET la meme date que la mutation parent (securite)
        if last.get("grouped_mutation"):
            sale_date = last.get("date")
            # Cherche tous les lots qui ont actuellement parent_lot_id = lot_id
            # OR qui avaient une mutation groupee pointant vers ce parent
            potential_children = await db.lots.find({
                "$or": [
                    {"parent_lot_id": lot_id},
                    {"mutations.grouped_parent_lot_id": lot_id},
                ]
            }, {"_id": 0}).to_list(100)
            for ch in potential_children:
                if ch["id"] == lot_id:
                    continue
                ch_muts = ch.get("mutations") or []
                if not ch_muts:
                    continue
                ch_last = ch_muts[-1]
                # Match : meme date + meme parent + meme owner d'origine
                if (ch_last.get("grouped_parent_lot_id") == lot_id and
                        ch_last.get("date") == sale_date and
                        ch_last.get("old_owner_id") == old_owner_id):
                    cancelled.append({"lot_id": ch["id"], "lot_number": ch.get("number", ""), "mutation_id": ch_last.get("id")})
                    await _cancel_single(ch["id"], ch_last)

        updated = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        return {
            "status": "ok",
            "message": (f"Mutation groupee annulee sur {len(cancelled)} lots, proprietaire precedent restaure"
                        if len(cancelled) > 1
                        else "Mutation annulee, proprietaire precedent restaure"),
            "lot": updated,
            "cancelled_mutation": last,
            "cancelled_count": len(cancelled),
            "cancelled_lots": cancelled,
        }

    @router.get("/lots/{lot_id}/mutations/{mutation_id}/decompte.pdf")
    async def download_mutation_decompte_pdf(lot_id: str, mutation_id: str):
        """Genere le PDF "Decompte de mutation" pour une mutation donnee.

        Reprend les 3 blocs comptables (fonds de roulement + prorata appel en
        cours + appels futurs) stockes dans le mutation_record. Format
        notarial, joint a l'acte de vente.
        """
        from fastapi.responses import StreamingResponse
        from io import BytesIO
        from pdf_mutation_decompte import build_mutation_decompte_pdf

        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        muts = lot.get("mutations") or []
        # mutation_id == "last" -> derniere mutation
        if mutation_id == "last":
            mut = muts[-1] if muts else None
        else:
            mut = next((m for m in muts if m.get("id") == mutation_id), None)
        if not mut:
            raise HTTPException(404, "Mutation non trouvee pour ce lot")

        copro = await db.coproprietes.find_one(
            {"id": lot.get("copropriete_id", "")}, {"_id": 0}
        ) or {}
        seller = await db.owners.find_one(
            {"id": mut.get("old_owner_id", "")}, {"_id": 0}
        ) or {}
        buyer = await db.owners.find_one(
            {"id": mut.get("new_owner_id", "")}, {"_id": 0}
        ) or {}

        # Reconstruit breakdown depuis mut_rec (champs persistes lors de la mutation)
        breakdown = {
            "roulement_quota": mut.get("roulement_quota", 0),
            "current_period_prorata": mut.get("current_period_prorata", mut.get("prorata_provisions", 0)),
            "current_period_details": mut.get("current_period_details") or mut.get("prorata_details") or [],
            "future_calls": mut.get("future_calls") or [],
            "future_calls_total": mut.get("future_calls_total", 0),
            "budget_frequency": mut.get("budget_frequency"),
            "budget_frequency_label": mut.get("budget_frequency_label", ""),
            "total_transfer": mut.get("total_transfer", 0),
        }

        # iter90dk : cherche les mutations "soeurs" (meme grouped_parent_lot_id =
        # lot_id courant, meme date, meme old_owner). Chaque lot a sa propre
        # mutation mais elles font partie du meme "groupe" (parent-enfant OU
        # lots additionnels selectionnes en checklist par le vendeur).
        lots_group = [{"lot": lot, "breakdown": breakdown}]
        sale_dt_str = mut.get("date", "")
        old_owner_id = mut.get("old_owner_id", "")
        if lot.get("copropriete_id") and sale_dt_str and old_owner_id:
            sibling_lots = await db.lots.find(
                {"copropriete_id": lot.get("copropriete_id"),
                 "id": {"$ne": lot_id},
                 "mutations": {"$exists": True, "$ne": []}},
                {"_id": 0},
            ).to_list(1000)
            for sl in sibling_lots:
                sl_muts = sl.get("mutations") or []
                for sm in sl_muts:
                    if (sm.get("grouped_parent_lot_id") == lot_id
                            and sm.get("date") == sale_dt_str
                            and sm.get("old_owner_id") == old_owner_id
                            and sm.get("new_owner_id") == mut.get("new_owner_id")):
                        lots_group.append({
                            "lot": sl,
                            "breakdown": {
                                "roulement_quota": sm.get("roulement_quota", 0),
                                "current_period_prorata": sm.get("current_period_prorata",
                                                                  sm.get("prorata_provisions", 0)),
                                "current_period_details": sm.get("current_period_details")
                                                          or sm.get("prorata_details") or [],
                                "future_calls": sm.get("future_calls") or [],
                                "future_calls_total": sm.get("future_calls_total", 0),
                                "budget_frequency": sm.get("budget_frequency"),
                                "budget_frequency_label": sm.get("budget_frequency_label", ""),
                                "total_transfer": sm.get("total_transfer", 0),
                            },
                        })
                        break

        from pdf_layout import resolve_syndic_pdf_context
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_mutation_decompte_pdf(
            copropriete=copro, lot=lot,
            seller=seller, buyer=buyer,
            mutation=mut, breakdown=breakdown,
            syndic_pdf_ctx=syndic_pdf_ctx,
            lots_group=lots_group,
        )

        sale_date = (mut.get("date", "") or "").replace("-", "")
        lot_num = (lot.get("number", "") or "").replace("/", "_").replace(" ", "_")
        filename = f"decompte_mutation_lot_{lot_num}_{sale_date}.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/lots/{lot_id}/mutate-preview")
    async def mutate_lot_preview(lot_id: str, data: LotMutationInput):
        """Preview du calcul de mutation sans rien ecrire en base.
        Retourne le decompte structure en 3 sections :
        - Fonds de roulement (quote-part sur quotites, JAMAIS au prorata temporel)
        - Prorata appel en cours (provisions, portion apres la vente)
        - Appels de provisions futurs (informatif, montant complet a l'acquereur)

        Si le lot a des enfants lies (parent_lot_id pointe vers lui), le decompte
        agrege automatiquement parent + enfants et retourne aussi `per_lot_breakdowns`.
        """
        lot = await db.lots.find_one({"id": lot_id}, {"_id": 0})
        if not lot:
            raise HTTPException(404, "Lot non trouve")
        if lot.get("parent_lot_id"):
            other = await db.lots.find_one({"id": lot["parent_lot_id"]}, {"_id": 0, "number": 1})
            raise HTTPException(400, f"Ce lot est lie au lot parent {other.get('number','?') if other else '?'} - mutez le parent pour effectuer la mutation groupee")
        copro_id = lot.get("copropriete_id", "")
        old_owner_id = lot.get("owner_id", "")
        if not (copro_id and old_owner_id):
            raise HTTPException(400, "Lot incomplet")

        try:
            sale_dt = datetime.strptime(data.sale_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(400, "sale_date doit etre au format YYYY-MM-DD")

        # Recupere les lots enfants lies (mutation groupee)
        children = await db.lots.find({"parent_lot_id": lot_id}, {"_id": 0}).to_list(100)

        # iter90dk : lots additionnels choisis par le vendeur (checklist frontend)
        additional_lots = []
        add_ids = [x for x in (data.additional_lot_ids or []) if x]
        already_ids = {lot_id} | {c["id"] for c in children}
        add_ids = [x for x in add_ids if x not in already_ids]
        if add_ids:
            add_lots_docs = await db.lots.find(
                {"id": {"$in": add_ids}}, {"_id": 0},
            ).to_list(500)
            for al in add_lots_docs:
                # Validation stricte : meme ACP + meme vendeur, pas d'enfant.
                if al.get("copropriete_id") != copro_id:
                    raise HTTPException(400, f"Lot {al.get('number','?')} n'appartient pas a la meme ACP")
                if al.get("owner_id") != old_owner_id:
                    raise HTTPException(400, f"Lot {al.get('number','?')} n'a pas le meme vendeur")
                if al.get("parent_lot_id"):
                    raise HTTPException(400, f"Lot {al.get('number','?')} est enfant d'un autre lot")
                additional_lots.append(al)

        all_lots = [lot] + children + additional_lots

        per_lot = []
        agg = {
            "fonds_roulement_total_acp": 0.0,
            "roulement_quota": 0.0,
            "current_period_prorata": 0.0,
            "future_calls_total": 0.0,
            "total_transfer": 0.0,
        }
        for lt in all_lots:
            bd = await _compute_mutation_breakdown(lt, lt.get("owner_id", ""), sale_dt)
            per_lot.append({"lot_id": lt["id"], "lot_number": lt.get("number", ""), **bd})
            agg["fonds_roulement_total_acp"] = bd["fonds_roulement_total"]  # meme valeur sur tous (ACP)
            agg["roulement_quota"] += bd["roulement_quota"]
            agg["current_period_prorata"] += bd["current_period_prorata"]
            agg["future_calls_total"] += bd["future_calls_total"]
            agg["total_transfer"] += bd["total_transfer"]
        # Round agregats
        for k in ("roulement_quota", "current_period_prorata", "future_calls_total", "total_transfer"):
            agg[k] = round(agg[k], 2)

        # Retourne le breakdown DU PARENT en racine (pour compat retro) + per_lot_breakdowns
        root = per_lot[0]
        root_payload = {k: v for k, v in root.items() if k not in ("lot_id", "lot_number")}
        return {
            **root_payload,
            "per_lot_breakdowns": per_lot,
            "linked_lots_count": len(children),
            # iter90dk : nombre de lots additionnels manuellement selectionnes
            "additional_lots_count": len(additional_lots),
            # Totaux agreges
            "grouped_total_roulement": agg["roulement_quota"],
            "grouped_total_current_prorata": agg["current_period_prorata"],
            "grouped_total_future": agg["future_calls_total"],
            "grouped_total_transfer": agg["total_transfer"],
        }

    # ---- TENANTS ----
    class TenantInput(BaseModel):
        name: str
        email: Optional[str] = ""
        phone: Optional[str] = ""
        lot_id: Optional[str] = ""
        lease_start: Optional[str] = ""
        lease_end: Optional[str] = ""
        rent_amount: Optional[float] = 0.0
        copropriete_id: Optional[str] = ""

    @router.get("/tenants")
    async def list_tenants(request: Request, copropriete_id: Optional[str] = None):
        """Liste des locataires - chinese wall STRICT (RGPD).
        - Superadmin : voit tout
        - Syndic / gestionnaire : ne voit QUE les locataires de ses ACPs (via tenant.copropriete_id)
        """
        is_super, allowed_copros = await _get_user_scope(request)
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        elif not is_super:
            if not allowed_copros:
                return []
            q["copropriete_id"] = {"$in": allowed_copros}
        tenants = await db.tenants.find(q, {"_id": 0}).sort("name", 1).to_list(2000)
        return tenants

    @router.post("/tenants")
    async def create_tenant(data: TenantInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or ""
        if not is_super:
            if not copro_id:
                raise HTTPException(400, "Un locataire doit etre rattache a une copropriete (chinese wall + RGPD)")
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez creer un locataire que pour une de vos ACPs")
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "lot_id": data.lot_id,
            "lease_start": data.lease_start,
            "lease_end": data.lease_end,
            "rent_amount": data.rent_amount,
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.tenants.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/tenants/{tenant_id}")
    async def update_tenant(tenant_id: str, data: TenantInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Locataire non trouve")
        update = {
            "name": data.name, "email": data.email, "phone": data.phone,
            "lot_id": data.lot_id, "lease_start": data.lease_start,
            "lease_end": data.lease_end, "rent_amount": data.rent_amount
        }
        result = await db.tenants.update_one({"id": tenant_id}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return await db.tenants.find_one({"id": tenant_id}, {"_id": 0})

    @router.delete("/tenants/{tenant_id}")
    async def delete_tenant(tenant_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Locataire non trouve")
        if not is_super and existing.get("copropriete_id", "") not in (allowed_copros or []):
            raise HTTPException(404, "Locataire non trouve")
        result = await db.tenants.delete_one({"id": tenant_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Locataire non trouve")
        return {"message": "Locataire supprime"}

    return router
