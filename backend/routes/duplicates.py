"""Routes admin pour la detection et fusion de doublons (chinese wall).

Endpoints exposes sous /api/admin/duplicates :
 - GET  /suppliers?copropriete_id=...     -> groupes de fournisseurs doublonnes
 - GET  /owners?copropriete_id=...        -> groupes de proprietaires doublonnes
 - GET  /users                            -> doublons utilisateurs (par email/nom)
 - POST /suppliers/merge   {keep_id, remove_ids}    -> fusionne (delegue a /api/suppliers/merge interne)
 - POST /owners/merge      {keep_id, remove_ids}    -> fusionne et reassocie

Scope (chinese wall) :
 - superadmin  : tout
 - syndic/admin: uniquement les entites rattachees a ses copropriete_ids
 - autres roles: 403
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import re


def create_duplicates_router(db):
    router = APIRouter(prefix="/api/admin/duplicates")

    # ----------------- Helpers normalisation -----------------
    def _norm_name(value: str) -> str:
        """Lowercase + tri alphabetique des mots (tolerant a l'ordre +
        tolerant aux caracteres de separation '-', '&', etc.). iter90d :
        on remplace les chars non-alphanum par un espace AVANT split, ce qui
        permet de matcher 'DEWINTER - DRAYE Jean-Claude & Evelyne' avec
        'DEWINTER - DRAYE Jean-Claude Evelyne' (sans &)."""
        v = (value or "").lower()
        # Map accented chars to ascii (NFD + strip)
        import unicodedata
        v = "".join(c for c in unicodedata.normalize("NFD", v) if unicodedata.category(c) != "Mn")
        v = re.sub(r"[^a-z0-9]+", " ", v).strip()
        words = [w for w in v.split() if w]
        return " ".join(sorted(words))

    def _norm_alphanum(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", (value or "")).upper()

    def _norm_email(value: str) -> str:
        return (value or "").strip().lower()

    def _norm_address(addr: str, postal: str, city: str) -> str:
        full = f"{addr} {postal} {city}".strip().lower()
        full = re.sub(r"[^a-z0-9\s]", "", full)
        return " ".join(full.split())

    async def _get_user_scope(request: Request):
        """Retourne (is_super, allowed_copro_ids)."""
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if role not in ("superadmin", "admin", "syndic"):
            raise HTTPException(403, "Acces reserve a l'administration")
        if is_superadmin_only(role):
            return True, None
        return False, user.get("copropriete_ids", []) or []

    async def _resolve_scope(request: Request, copropriete_id: Optional[str]):
        """Verifie l'acces et retourne la liste effective d'ACPs a scanner.
        - Si copropriete_id fourni : doit etre dans le scope (sauf superadmin).
        - Sinon : toutes les ACPs autorisees du user (None = global pour superadmin).
        """
        is_super, allowed = await _get_user_scope(request)
        if copropriete_id:
            if not is_super and copropriete_id not in (allowed or []):
                raise HTTPException(403, "Cette copropriete n'est pas dans votre scope")
            return is_super, [copropriete_id]
        return is_super, allowed  # None pour superadmin = global

    # ----------------- Union-Find utilitaire -----------------
    class UF:
        def __init__(self):
            self.parent = {}

        def find(self, x):
            while self.parent.get(x, x) != x:
                self.parent[x] = self.parent.get(self.parent[x], self.parent[x])
                x = self.parent[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.parent[ra] = rb

        def groups(self, items):
            buckets = {}
            for it in items:
                root = self.find(it)
                buckets.setdefault(root, []).append(it)
            return [v for v in buckets.values() if len(v) >= 2]

    # ============= SUPPLIERS =============
    @router.get("/suppliers")
    async def detect_supplier_duplicates(request: Request, copropriete_id: Optional[str] = None):
        is_super, scope = await _resolve_scope(request, copropriete_id)
        # Construction du filtre Mongo
        query = {}
        if scope is not None:
            # Scope syndic : fournisseur direct OR via tier_accounts
            query["$or"] = []
            for cid in scope:
                query["$or"].append({"copropriete_id": cid})
                query["$or"].append({f"tier_accounts.{cid}": {"$exists": True}})
            if not query["$or"]:
                return {"groups": [], "total_suppliers": 0}
        suppliers = await db.suppliers.find(query, {"_id": 0}).to_list(10000)

        uf = UF()
        # Indexes par cle
        by_name = {}
        by_bce = {}
        by_vat = {}
        by_iban = {}
        for s in suppliers:
            sid = s["id"]
            uf.parent[sid] = sid
            nm = _norm_name(s.get("name", ""))
            if nm:
                by_name.setdefault(nm, []).append(sid)
            for fld, idx in (("bce_number", by_bce), ("vat_number", by_vat), ("iban", by_iban)):
                v = _norm_alphanum(s.get(fld, ""))
                if v:
                    idx.setdefault(v, []).append(sid)
        # Union sur chaque cle partagee
        match_reasons = {}  # supplier_id -> set of reasons
        for idx, label in ((by_name, "nom"), (by_bce, "BCE"), (by_vat, "TVA"), (by_iban, "IBAN")):
            for val, ids in idx.items():
                if len(ids) < 2:
                    continue
                base = ids[0]
                for other in ids[1:]:
                    uf.union(base, other)
                for sid in ids:
                    match_reasons.setdefault(sid, set()).add(label)

        # Construit les groupes
        sup_by_id = {s["id"]: s for s in suppliers}
        groups_ids = uf.groups(list(sup_by_id.keys()))
        groups = []
        for g in groups_ids:
            members = [sup_by_id[i] for i in g]
            reasons = set()
            for i in g:
                reasons |= match_reasons.get(i, set())
            groups.append({
                "match_on": sorted(reasons),
                "members": [{
                    "id": m["id"],
                    "name": m.get("name", ""),
                    "bce_number": m.get("bce_number", ""),
                    "vat_number": m.get("vat_number", ""),
                    "iban": m.get("iban", ""),
                    "city": m.get("city", ""),
                    "email": m.get("email", ""),
                    "phone": m.get("phone", ""),
                    "copropriete_id": m.get("copropriete_id", ""),
                    "tier_accounts": list((m.get("tier_accounts") or {}).keys()),
                    "created_at": m.get("created_at", ""),
                } for m in members],
            })
        # Tri : groupes les plus gros d'abord
        groups.sort(key=lambda g: -len(g["members"]))
        return {"groups": groups, "total_suppliers": len(suppliers), "scope": "syndic" if not is_super else "platform"}

    # ============= OWNERS =============
    @router.get("/owners")
    async def detect_owner_duplicates(request: Request, copropriete_id: Optional[str] = None):
        is_super, scope = await _resolve_scope(request, copropriete_id)
        # Scope : owners ayant un lot dans une ACP autorisee
        if scope is not None:
            if not scope:
                return {"groups": [], "total_owners": 0}
            owner_ids_single = await db.lots.distinct("owner_id", {"copropriete_id": {"$in": scope}})
            owner_ids_multi = await db.lots.distinct("owner_ids", {"copropriete_id": {"$in": scope}})
            owner_ids = list({oid for oid in (owner_ids_single or []) if oid} | {oid for oid in (owner_ids_multi or []) if oid})
            owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(10000) if owner_ids else []
        else:
            owners = await db.owners.find({}, {"_id": 0}).to_list(10000)

        uf = UF()
        by_name, by_email, by_phone, by_bce, by_addr = {}, {}, {}, {}, {}
        for o in owners:
            oid = o["id"]
            uf.parent[oid] = oid
            nm = _norm_name(f"{o.get('first_name','')} {o.get('last_name','')}".strip() or o.get("name", ""))
            if nm:
                by_name.setdefault(nm, []).append(oid)
            for fld in ("email", "email2"):
                e = _norm_email(o.get(fld, ""))
                if e:
                    by_email.setdefault(e, []).append(oid)
            for fld in ("phone", "phone2"):
                p = _norm_alphanum(o.get(fld, ""))
                if p and len(p) >= 6:  # evite faux positifs sur tel court
                    by_phone.setdefault(p, []).append(oid)
            bce = _norm_alphanum(o.get("bce_number", ""))
            if bce:
                by_bce.setdefault(bce, []).append(oid)
            addr = _norm_address(o.get("address", ""), o.get("postal_code", ""), o.get("city", ""))
            if addr and len(addr) > 8:
                by_addr.setdefault(addr, []).append(oid)

        match_reasons = {}
        for idx, label in (
            (by_name, "nom"),
            (by_email, "email"),
            (by_phone, "telephone"),
            (by_bce, "BCE"),
            (by_addr, "adresse"),
        ):
            for val, ids in idx.items():
                if len(ids) < 2:
                    continue
                base = ids[0]
                for other in ids[1:]:
                    uf.union(base, other)
                for oid in ids:
                    match_reasons.setdefault(oid, set()).add(label)

        ow_by_id = {o["id"]: o for o in owners}
        groups_ids = uf.groups(list(ow_by_id.keys()))
        groups = []
        for g in groups_ids:
            members = [ow_by_id[i] for i in g]
            reasons = set()
            for i in g:
                reasons |= match_reasons.get(i, set())
            # Recupere nombre de lots par owner pour aide a la decision
            members_serial = []
            for m in members:
                lots_count = await db.lots.count_documents({
                    "$or": [{"owner_id": m["id"]}, {"owner_ids": m["id"]}]
                })
                members_serial.append({
                    "id": m["id"],
                    "name": m.get("name") or f"{m.get('first_name','')} {m.get('last_name','')}".strip(),
                    "first_name": m.get("first_name", ""),
                    "last_name": m.get("last_name", ""),
                    "email": m.get("email", ""),
                    "email2": m.get("email2", ""),
                    "phone": m.get("phone", ""),
                    "bce_number": m.get("bce_number", ""),
                    "address": m.get("address", ""),
                    "postal_code": m.get("postal_code", ""),
                    "city": m.get("city", ""),
                    "iban": m.get("iban", ""),
                    "vcs_code": m.get("vcs_code", ""),
                    "copropriete_id": m.get("copropriete_id", ""),
                    "lots_count": lots_count,
                    "created_at": m.get("created_at", ""),
                })
            groups.append({"match_on": sorted(reasons), "members": members_serial})
        groups.sort(key=lambda g: -len(g["members"]))
        return {"groups": groups, "total_owners": len(owners), "scope": "syndic" if not is_super else "platform"}

    # ============= USERS (READ-ONLY: superadmin only) =============
    @router.get("/users")
    async def detect_user_duplicates(request: Request):
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        if not is_superadmin_only(user.get("role", "")):
            raise HTTPException(403, "Reserve au super administrateur")
        users = await db.users.find({}).to_list(5000)
        uf = UF()
        by_email, by_name = {}, {}
        idx_map = {}
        for u in users:
            uid = str(u.get("_id"))
            idx_map[uid] = u
            uf.parent[uid] = uid
            e = _norm_email(u.get("email", ""))
            if e:
                by_email.setdefault(e, []).append(uid)
            nm = _norm_name(u.get("name", ""))
            if nm:
                by_name.setdefault(nm, []).append(uid)
        match_reasons = {}
        for idx, label in ((by_email, "email"), (by_name, "nom")):
            for val, ids in idx.items():
                if len(ids) < 2:
                    continue
                base = ids[0]
                for other in ids[1:]:
                    uf.union(base, other)
                for uid in ids:
                    match_reasons.setdefault(uid, set()).add(label)
        groups_ids = uf.groups(list(idx_map.keys()))
        groups = []
        for g in groups_ids:
            members = [idx_map[i] for i in g]
            reasons = set()
            for i in g:
                reasons |= match_reasons.get(i, set())
            groups.append({
                "match_on": sorted(reasons),
                "members": [{
                    "id": str(m.get("_id")),
                    "email": m.get("email", ""),
                    "name": m.get("name", ""),
                    "role": m.get("role", ""),
                    "copropriete_ids": m.get("copropriete_ids", []),
                    "is_suspended": bool(m.get("is_suspended", False)),
                    "created_at": m.get("created_at", ""),
                } for m in members],
            })
        groups.sort(key=lambda g: -len(g["members"]))
        return {"groups": groups, "total_users": len(users)}

    # ============= MERGE OWNERS =============
    class OwnerMergeInput(BaseModel):
        keep_id: str
        remove_ids: list[str]

    @router.post("/owners/merge/preview")
    async def preview_owner_merge(data: OwnerMergeInput, request: Request):
        """Prevision de fusion : retourne les compteurs de references qui seront
        migrees vers keep_id, SANS modification en base. Utilise par le frontend
        pour afficher un dialog de confirmation avant fusion definitive."""
        is_super, allowed_copros = await _get_user_scope(request)
        if data.keep_id in data.remove_ids:
            raise HTTPException(400, "keep_id ne peut pas etre dans remove_ids")
        if not data.remove_ids:
            raise HTTPException(400, "Aucun proprietaire a fusionner")

        keep = await db.owners.find_one({"id": data.keep_id}, {"_id": 0})
        if not keep:
            raise HTTPException(404, f"Proprietaire a conserver introuvable : {data.keep_id}")

        removes = await db.owners.find({"id": {"$in": data.remove_ids}}, {"_id": 0}).to_list(50)
        found_ids = {r["id"] for r in removes}
        missing = set(data.remove_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Proprietaire(s) introuvable(s) : {', '.join(missing)}")

        # Chinese wall check (identique a merge_owners)
        if not is_super:
            allowed_set = set(allowed_copros or [])

            async def _in_scope(owner_id: str) -> bool:
                lots_copros = await db.lots.distinct("copropriete_id", {
                    "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]
                })
                return bool(set(lots_copros) & allowed_set)
            if not await _in_scope(data.keep_id):
                raise HTTPException(403, "Acces refuse au proprietaire a conserver")
            for r in removes:
                if not await _in_scope(r["id"]):
                    raise HTTPException(403, f"Acces refuse au proprietaire {r.get('name','')}")

        # Comptage des references qui seront migrees (aucune modification)
        lots_simple = await db.lots.count_documents({"owner_id": {"$in": data.remove_ids}})
        lots_multi = await db.lots.count_documents({"owner_ids": {"$in": data.remove_ids}})
        txns = await db.bank_transactions.count_documents(
            {"match_type": "owner_payment", "matched_to": {"$in": data.remove_ids}}
        )
        mut_from = await db.mutations.count_documents({"from_owner_id": {"$in": data.remove_ids}})
        mut_to = await db.mutations.count_documents({"to_owner_id": {"$in": data.remove_ids}})
        journals = await db.journal_entries.count_documents(
            {"lines.third_party_id": {"$in": data.remove_ids}}
        )
        fund_calls = await db.fund_calls.count_documents(
            {"details.owner_id": {"$in": data.remove_ids}}
        )

        # Champs qui seront enrichis (par premier non-vide de removes)
        enrich_fields = [
            "first_name", "last_name", "name", "email", "email2", "phone", "phone2",
            "address", "postal_code", "city", "country", "iban", "bce_number",
            "vcs_code", "vcs_digits", "auxiliary_code", "identifier", "civility",
        ]
        will_enrich = []
        for f in enrich_fields:
            if not (str(keep.get(f) or "")).strip():
                for r in removes:
                    if (str(r.get(f) or "")).strip():
                        will_enrich.append({
                            "field": f, "from": r.get("name", "?"), "value": r[f]
                        })
                        break

        return {
            "keep": {
                "id": keep["id"],
                "name": keep.get("name", ""),
                "email": keep.get("email", ""),
                "vcs_code": keep.get("vcs_code", ""),
            },
            "remove_count": len(removes),
            "removes": [
                {"id": r["id"], "name": r.get("name", ""), "email": r.get("email", "")}
                for r in removes
            ],
            "migrations": {
                "lots_as_sole_owner": lots_simple,
                "lots_as_co_owner": lots_multi,
                "bank_transactions_matched": txns,
                "mutations_as_seller": mut_from,
                "mutations_as_buyer": mut_to,
                "journal_entry_lines": journals,
                "fund_call_details": fund_calls,
            },
            "total_refs": lots_simple + lots_multi + txns + mut_from + mut_to + journals + fund_calls,
            "will_enrich_fields": will_enrich,
        }

    @router.post("/owners/merge")
    async def merge_owners(data: OwnerMergeInput, request: Request):
        """Fusion de proprietaires : keep_id conserve, remove_ids absorbes.

        Reassociations :
          - lots.owner_id : remove_ids -> keep_id
          - lots.owner_ids[] : retirer remove_ids et ajouter keep_id (en preservant
            les pourcentages le cas echeant)
          - fund_calls.distribution[lot_id].owner_id : si applicable
          - bank_transactions.matched_to (match_type='owner_payment') -> keep_id
          - mutations.from_owner_id / to_owner_id -> keep_id
        Enrichissement de keep depuis remove (premier non-vide gagne) sur les
        champs vides. Suppression finale des remove_ids.
        """
        is_super, allowed_copros = await _get_user_scope(request)
        if data.keep_id in data.remove_ids:
            raise HTTPException(400, "keep_id ne peut pas etre dans remove_ids")
        if not data.remove_ids:
            raise HTTPException(400, "Aucun proprietaire a fusionner")

        keep = await db.owners.find_one({"id": data.keep_id}, {"_id": 0})
        if not keep:
            raise HTTPException(404, f"Proprietaire a conserver introuvable : {data.keep_id}")

        removes = await db.owners.find({"id": {"$in": data.remove_ids}}, {"_id": 0}).to_list(50)
        found_ids = {r["id"] for r in removes}
        missing = set(data.remove_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Proprietaire(s) introuvable(s) : {', '.join(missing)}")

        # Chinese wall : verifie que TOUS les owners (keep + remove) sont dans le scope
        if not is_super:
            allowed_set = set(allowed_copros or [])

            async def _in_scope(owner_id: str) -> bool:
                lots_copros = await db.lots.distinct("copropriete_id", {
                    "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]
                })
                return bool(set(lots_copros) & allowed_set)
            if not await _in_scope(data.keep_id):
                raise HTTPException(403, "Acces refuse au proprietaire a conserver")
            for r in removes:
                if not await _in_scope(r["id"]):
                    raise HTTPException(403, f"Acces refuse au proprietaire {r.get('name','')}")

        # 1) Enrichissement keep depuis removes (premier non-vide gagne)
        enrich_fields = [
            "first_name", "last_name", "name", "email", "email2", "phone", "phone2",
            "address", "postal_code", "city", "country", "iban", "bce_number",
            "vcs_code", "vcs_digits", "auxiliary_code", "identifier", "civility",
        ]
        enrichment = {}
        for f in enrich_fields:
            if not (str(keep.get(f) or "")).strip():
                for r in removes:
                    if (str(r.get(f) or "")).strip():
                        enrichment[f] = r[f]
                        break
        if enrichment:
            await db.owners.update_one({"id": data.keep_id}, {"$set": enrichment})

        # 2) Migration des lots (owner_id simple)
        lots_simple = await db.lots.update_many(
            {"owner_id": {"$in": data.remove_ids}},
            {"$set": {"owner_id": data.keep_id}},
        )

        # 3) Migration des lots multi-owners : remplacer les remove_ids par keep_id
        #    en preservant les pourcentages si presents. On itere car operateur MongoDB
        #    complexe (set substitution dans array).
        multi_lots = await db.lots.find({"owner_ids": {"$in": data.remove_ids}}).to_list(5000)
        multi_lots_updated = 0
        for lot in multi_lots:
            owner_ids = list(lot.get("owner_ids") or [])
            new_ids = []
            seen_keep = False
            for oid in owner_ids:
                if oid in data.remove_ids:
                    if not seen_keep and data.keep_id not in owner_ids:
                        new_ids.append(data.keep_id)
                        seen_keep = True
                    elif data.keep_id in owner_ids:
                        # keep est deja present : on omet le doublon (consolide les pcts)
                        pass
                else:
                    new_ids.append(oid)
            # Consolide owner_percentages
            new_pcts = {}
            for oid, pct in (lot.get("owner_percentages") or {}).items():
                target = data.keep_id if oid in data.remove_ids else oid
                new_pcts[target] = (new_pcts.get(target, 0) or 0) + float(pct or 0)
            update_set = {"owner_ids": new_ids}
            if lot.get("owner_percentages"):
                update_set["owner_percentages"] = new_pcts
            await db.lots.update_one({"id": lot["id"]}, {"$set": update_set})
            multi_lots_updated += 1

        # 4) Bank transactions matchees owner
        txns_updated = await db.bank_transactions.update_many(
            {"match_type": "owner_payment", "matched_to": {"$in": data.remove_ids}},
            {"$set": {"matched_to": data.keep_id}},
        )

        # 5) Mutations (decompte de mutation : from / to)
        mut_from = await db.mutations.update_many(
            {"from_owner_id": {"$in": data.remove_ids}},
            {"$set": {"from_owner_id": data.keep_id}},
        )
        mut_to = await db.mutations.update_many(
            {"to_owner_id": {"$in": data.remove_ids}},
            {"$set": {"to_owner_id": data.keep_id}},
        )

        # 5b) Ecritures comptables (journal_entries.lines[].third_party_id)
        # ANTI-ORPHELIN : reassigner les references dans les journaux comptables
        # avant de supprimer les owners, sinon les lignes pointeraient dans le vide.
        je_updated = await db.journal_entries.update_many(
            {"lines.third_party_id": {"$in": data.remove_ids}},
            {"$set": {"lines.$[elem].third_party_id": data.keep_id}},
            array_filters=[{"elem.third_party_id": {"$in": data.remove_ids}}],
        )

        # 5c) Fund calls details (owner_id dans le detail par lot)
        fc_updated = await db.fund_calls.update_many(
            {"details.owner_id": {"$in": data.remove_ids}},
            {"$set": {"details.$[elem].owner_id": data.keep_id}},
            array_filters=[{"elem.owner_id": {"$in": data.remove_ids}}],
        )

        # 6) Suppression
        deleted = await db.owners.delete_many({"id": {"$in": data.remove_ids}})

        return {
            "message": f"Fusion effectuee : {deleted.deleted_count} proprietaire(s) absorbe(s)",
            "kept_id": data.keep_id,
            "removed_ids": data.remove_ids,
            "enriched_fields": list(enrichment.keys()),
            "lots_simple_updated": lots_simple.modified_count,
            "lots_multi_updated": multi_lots_updated,
            "bank_transactions_migrated": txns_updated.modified_count,
            "mutations_from_migrated": mut_from.modified_count,
            "mutations_to_migrated": mut_to.modified_count,
            "journal_entries_migrated": je_updated.modified_count,
            "fund_calls_migrated": fc_updated.modified_count,
        }

    return router
