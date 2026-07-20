from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import re
import uuid
from difflib import SequenceMatcher
from tier_accounts import assign_supplier_account


_LEGAL_PARTICLES = frozenset({
    "sa", "sprl", "srl", "sarl", "sas", "scrl", "asbl", "scs", "snc",
    "nv", "bv", "bvba", "cvba", "vzw", "sc", "sca", "sepa",
    "sci", "gmbh", "ag", "ltd", "llc", "inc",
})


def _norm_name(value: str) -> str:
    """Normalise un nom : minuscules, espaces multiples reduits, trim, MOTS TRIES.

    Le tri alphabetique des mots permet de detecter les doublons type
    "Finlead srl" vs "SRL Finlead" (meme entreprise, ordre des mots different).

    iter90be : les particules juridiques generiques (sa, sprl, srl, sarl, sas,
    scrl, asbl, scs, snc, nv, bv, bvba, cvba, vzw, sc, sca, sci, gmbh, ag,
    ltd, llc, inc) sont MAINTENANT filtrees pour capturer les vrais doublons
    ("Finlead" vs "SRL Finlead" -> meme entite). La ponctuation dans les
    particules est aussi ignoree ("S.A.", "s.a" -> "sa" -> filtre).
    Si apres filtrage la chaine devient vide (ex: "SRL"), on retombe sur le
    nom d'origine trie (garde-fou).

    iter90fb : les parties entre parentheses ne sont plus considerees comme
    des mots du nom principal (on les ignore ici). Pour matcher aussi le
    contenu entre parentheses, utiliser `_norm_name_candidates`.
    """
    # iter90fb : retire le contenu entre parentheses pour la normalisation
    # principale ("Finlead Properties (Finlead srl)" -> "Finlead Properties").
    stripped = re.sub(r"\([^)]*\)", " ", value or "")
    words = stripped.strip().lower().split()
    # Pour chaque mot, on determine si c'est une particule juridique en
    # retirant TOUS les caracteres non-alphanumeriques avant comparaison.
    def _is_particle(w: str) -> bool:
        core = re.sub(r"[^a-z0-9]", "", w)
        return core in _LEGAL_PARTICLES
    filtered = [w for w in words if not _is_particle(w)]
    if not filtered:
        filtered = words
    return " ".join(sorted(filtered))


def _norm_name_candidates(value: str) -> set[str]:
    """iter90fb : retourne toutes les normalisations candidates pour un nom :
    - le nom complet normalise (sans parentheses)
    - chaque fragment entre parentheses normalise individuellement
    Permet de matcher "Finlead Properties (Finlead srl)" avec la fiche
    "Finlead SRL" (le contenu de la parenthese est traite comme un
    candidat autonome).
    """
    candidates: set[str] = set()
    primary = _norm_name(value)
    if primary:
        candidates.add(primary)
    for m in re.findall(r"\(([^)]+)\)", value or ""):
        c = _norm_name(m)
        if c:
            candidates.add(c)
    return candidates


def _norm_id(value: str) -> str:
    """Normalise un BCE/TVA ou IBAN : alphanumerique uppercase uniquement.
    Ex: 'BE 0123.456.789' -> 'BE0123456789', 'BE12 3456 7890 1234' -> 'BE12345678901234'.
    """
    return re.sub(r"[^A-Za-z0-9]", "", (value or "")).upper()


async def find_similar_suppliers(
    db, *, name: str, copro_id: str = "", exclude_id: Optional[str] = None,
    threshold: float = 0.80, limit: int = 5,
) -> List[dict]:
    """Recherche les fournisseurs avec un nom SIMILAIRE (homonymes / coquilles).

    Utilise `difflib.SequenceMatcher` sur les noms normalises (mots tries).
    Retourne les top `limit` matches avec ratio >= `threshold`, tries par
    score decroissant. Exclut les matches strictement identiques (qui sont
    deja captures par `find_duplicate_supplier`).

    Scope ACP identique a `find_duplicate_supplier`.
    """
    norm_query = _norm_name(name)
    if not norm_query or len(norm_query) < 2:
        return []

    base_query: dict = {}
    if copro_id:
        base_query["$or"] = [
            {"copropriete_id": copro_id},
            {f"tier_accounts.{copro_id}": {"$exists": True}},
        ]
    if exclude_id:
        base_query["id"] = {"$ne": exclude_id}

    candidates = await db.suppliers.find(base_query, {"_id": 0}).to_list(5000)

    scored: list = []
    for s in candidates:
        norm_other = _norm_name(s.get("name", ""))
        if not norm_other:
            continue
        # On exclut les matches exacts (geres par find_duplicate_supplier).
        if norm_other == norm_query:
            continue
        ratio = SequenceMatcher(None, norm_query, norm_other).ratio()
        if ratio >= threshold:
            scored.append({"supplier": s, "score": round(ratio, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


async def find_duplicate_supplier(
    db, *, name: str, bce_number: str = "", vat_number: str = "",
    iban: str = "", copro_id: str = "", exclude_id: Optional[str] = None,
) -> Optional[dict]:
    """Recherche un fournisseur en doublon INTRA-ACP sur 3 criteres (l'un suffit) :
        1. BCE ou TVA identique (normalises)
        2. Nom identique (normalise, incluant les candidats parentheses - iter90fd)
        3. IBAN identique (normalise)

    iter90is (Chinese Wall strict) : la recherche est STRICTEMENT LIMITEE
    a l'ACP `copro_id`. Un meme "Engie" peut exister comme fiches
    distinctes dans plusieurs ACPs sans que ce soit considere doublon.

    Retourne {"supplier": <doc>, "field": "bce|vat|name|iban", "value": <str>}
    ou None.
    """
    name_candidates = _norm_name_candidates(name)
    norm_name = _norm_name(name)  # cle principale (pour retro-compat champ "value")  # noqa: F841
    norm_bce = _norm_id(bce_number)
    norm_vat = _norm_id(vat_number)
    norm_iban = _norm_id(iban)
    if not (name_candidates or norm_bce or norm_vat or norm_iban):
        return None
    if not copro_id:
        # iter90is : plus de fallback global. Sans ACP, on ne cherche pas.
        return None

    # Scope ACP STRICT (chinese wall)
    base_query: dict = {"copropriete_id": copro_id}
    if exclude_id:
        base_query["id"] = {"$ne": exclude_id}
    candidates = await db.suppliers.find(base_query, {"_id": 0}).to_list(5000)
    for s in candidates:
        if norm_bce:
            if _norm_id(s.get("bce_number", "")) == norm_bce:
                return {"supplier": s, "field": "bce_number", "value": s.get("bce_number", "")}
            if _norm_id(s.get("vat_number", "")) == norm_bce:
                return {"supplier": s, "field": "vat_number", "value": s.get("vat_number", "")}
        if norm_vat:
            if _norm_id(s.get("vat_number", "")) == norm_vat:
                return {"supplier": s, "field": "vat_number", "value": s.get("vat_number", "")}
            if _norm_id(s.get("bce_number", "")) == norm_vat:
                return {"supplier": s, "field": "bce_number", "value": s.get("bce_number", "")}
        if norm_iban and _norm_id(s.get("iban", "")) == norm_iban:
            return {"supplier": s, "field": "iban", "value": s.get("iban", "")}
        if name_candidates:
            other_candidates = _norm_name_candidates(s.get("name", ""))
            if name_candidates & other_candidates:
                return {"supplier": s, "field": "name", "value": s.get("name", "")}
    return None


class SupplierInput(BaseModel):
    name: str
    vat_number: Optional[str] = ""
    bce_number: Optional[str] = ""
    address: Optional[str] = ""
    postal_code: Optional[str] = ""
    city: Optional[str] = ""
    country: Optional[str] = "Belgique"
    phone: Optional[str] = ""
    email: Optional[str] = ""
    iban: Optional[str] = ""
    bic: Optional[str] = ""
    default_account: Optional[str] = ""
    notes: Optional[str] = ""
    # iter90is : copropriete_id OBLIGATOIRE (Chinese Wall strict).
    # Chaque fiche fournisseur est locale a UNE seule ACP. Un meme
    # fournisseur "Engie" existera comme DEUX fiches distinctes si utilise
    # dans ACP Acacia + ACP Maria Auto 2. Plus de partage global.
    copropriete_id: str
    # iter85g : ignorer la detection de similarites (l'utilisateur a deja confirme
    # via le dialog frontend). N'a aucun effet sur la detection EXACTE (BCE/TVA/IBAN/nom
    # strict) qui reste bloquante.
    force_create_despite_similar: Optional[bool] = False


class SupplierCheckDuplicateInput(BaseModel):
    """Payload pour pre-verifier la presence de doublons / homonymes avant
    de creer un fournisseur. Ne cree rien en base.
    """
    name: str
    vat_number: Optional[str] = ""
    bce_number: Optional[str] = ""
    iban: Optional[str] = ""
    copropriete_id: Optional[str] = ""


class SupplierMergeInput(BaseModel):
    """Fusion de fournisseurs : on garde `keep_id` et on absorbe `remove_ids`.
    Toutes les references (factures, transactions bancaires, ecritures comptables,
    invoice_lines, ...) sont reassociees a `keep_id`. Les champs vides de keep
    sont remplis depuis les remove (premier non-vide gagne).
    """
    keep_id: str
    remove_ids: list[str]


def create_suppliers_router(db):
    router = APIRouter(prefix="/api/suppliers")

    async def _get_user_scope(request: Request):
        """Retourne (is_super_global, allowed_copro_ids).
        - is_super=True pour superadmin/admin (acces total)
        - Sinon allowed_copro_ids = list of copropriete_ids du user (peut etre [])
        """
        from server import get_current_user, is_superadmin_only
        user = await get_current_user(request)
        role = user.get("role", "")
        if is_superadmin_only(role):
            return True, None
        return False, user.get("copropriete_ids", []) or []

    def _supplier_in_scope(supplier_doc: dict, allowed_copros) -> bool:
        """True si le fournisseur est rattache a au moins une ACP du scope."""
        if allowed_copros is None:
            return True
        if not allowed_copros:
            return False
        allowed_set = set(allowed_copros)
        # 1) copropriete_id direct (champ original lors de la creation)
        if supplier_doc.get("copropriete_id") and supplier_doc["copropriete_id"] in allowed_set:
            return True
        # 2) tier_accounts : chaque cle = un copropriete_id ou ce fournisseur a un compte 44000XXX
        ta = supplier_doc.get("tier_accounts") or {}
        for cid in ta.keys():
            if cid in allowed_set:
                return True
        return False

    @router.get("")
    async def list_suppliers(request: Request, search: Optional[str] = None, copropriete_id: Optional[str] = None):
        """Liste des fournisseurs - Chinese Wall STRICT (post iter90is + iter90iy).

        - `copropriete_id` OBLIGATOIRE (400 si absent) - une seule ACP a la fois.
        - Superadmin uniquement : peut passer `copropriete_id=all` pour voir
          l'ensemble (usage admin only, ex : audits, duplicates).
        - Syndic / gestionnaire : ne voit QUE les fournisseurs de l'ACP demandee
          (verif que l'ACP est dans son scope, sinon 403).
        - Filtre applique EN DB (pas post-load) pour minimiser la fuite RGPD
          et garantir des performances stables sur les grosses bases.
        """
        is_super, allowed_copros = await _get_user_scope(request)
        # Fallback : header X-Copropriete-Id si le param n'est pas explicite
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or ""
        copropriete_id = (copropriete_id or "").strip()
        # Bypass super-admin explicite
        if copropriete_id == "all":
            if not is_super:
                raise HTTPException(403, "Seul un superadmin peut lister toutes les ACPs")
            q_all: dict = {}
            if search:
                q_all["$or"] = [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"vat_number": {"$regex": search, "$options": "i"}},
                    {"bce_number": {"$regex": search, "$options": "i"}},
                ]
            return await db.suppliers.find(q_all, {"_id": 0}).sort("name", 1).to_list(2000)
        # copropriete_id absent -> Chinese Wall SILENCIEUX (retourne [])
        # iter90iy-v2 : plus de 400 - toute page qui inclut /suppliers dans un
        # Promise.all sans avoir d'ACP courante (ex: BankingPage mode "all")
        # recoit simplement une liste vide au lieu de casser tout le batch.
        # Le Chinese Wall reste strict : aucune fuite de donnee cross-ACP.
        if not copropriete_id:
            return []
        # Verif scope pour non-superadmin
        if not is_super and copropriete_id not in (allowed_copros or []):
            raise HTTPException(403, "Copropriete hors scope")
        # Query DB filtree par ACP (Chinese Wall strict : copropriete_id direct)
        q: dict = {"copropriete_id": copropriete_id}
        if search:
            q["$and"] = [
                {"$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"vat_number": {"$regex": search, "$options": "i"}},
                    {"bce_number": {"$regex": search, "$options": "i"}},
                ]},
            ]
        return await db.suppliers.find(q, {"_id": 0}).sort("name", 1).to_list(5000)

    @router.post("/check-duplicate")
    async def check_duplicate_supplier(request: Request, data: SupplierCheckDuplicateInput):
        """Pre-verifie la presence de doublons EXACTS et d'homonymes proches.

        Retourne :
          - `exact`: dict {supplier, field, value} si doublon strict trouve
            (BCE/TVA/IBAN/nom normalise), ou null
          - `similar`: liste [{supplier, score}] des fournisseurs avec un nom
            SIMILAIRE (ratio Levenshtein >= 0.80), tries par score decroissant
            (max 5)

        Le frontend appelle ce endpoint AVANT le POST de creation pour decider :
          - exact != null -> bloquer (le POST renverrait de toute facon 409)
          - similar non vide -> demander confirmation a l'utilisateur
          - sinon -> creation autorisee
        """
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or ""
        if not is_super and copro_id and copro_id not in (allowed_copros or []):
            raise HTTPException(403, "Vous ne pouvez verifier que pour vos ACPs")

        exact = await find_duplicate_supplier(
            db,
            name=data.name,
            bce_number=data.bce_number or "",
            vat_number=data.vat_number or "",
            iban=data.iban or "",
            copro_id=copro_id,
        )
        similar = await find_similar_suppliers(
            db, name=data.name, copro_id=copro_id,
        ) if data.name else []
        return {
            "exact": exact,  # peut etre None
            "similar": similar,  # liste de {supplier, score}
        }

    @router.post("")
    async def create_supplier(request: Request, data: SupplierInput):
        is_super, allowed_copros = await _get_user_scope(request)
        copro_id = (data.copropriete_id or "").strip() or getattr(request.state, "copropriete_id", "") or ""
        # iter90is (Chinese Wall strict) : copropriete_id est OBLIGATOIRE
        # pour TOUS les roles (superadmin inclus). Aucune fiche fournisseur
        # "globale" ne peut plus etre creee - chaque fiche est locale a UNE ACP.
        if not copro_id:
            raise HTTPException(400, "Un fournisseur doit etre rattache a une copropriete (chinese wall + RGPD)")
        # Pour un syndic : verifie que l'ACP fait partie de ses ACPs
        if not is_super:
            if copro_id not in (allowed_copros or []):
                raise HTTPException(403, "Vous ne pouvez attribuer ce fournisseur qu'a une de vos ACPs")
        # iter90it : BCE plus obligatoire depuis le Chinese Wall strict (iter90is).
        # Le cloisonnement per-ACP evite deja les doublons globaux. Le BCE peut
        # etre enrichi plus tard via le KBO lookup (iter90ip/iq).
        name = (data.name or "").strip()
        if not name:
            raise HTTPException(400, "Le nom du fournisseur est obligatoire.")
        # Check anti-doublon : BCE/TVA, nom et IBAN normalises (scope ACP)
        dup = await find_duplicate_supplier(
            db,
            name=data.name,
            bce_number=data.bce_number or "",
            vat_number=data.vat_number or "",
            iban=data.iban or "",
            copro_id=copro_id,
        )
        if dup:
            field_label = {
                "bce_number": "numero BCE",
                "vat_number": "numero TVA",
                "iban": "compte bancaire (IBAN)",
                "name": "nom",
            }.get(dup["field"], dup["field"])
            existing = dup["supplier"]
            raise HTTPException(
                409,
                f"Doublon detecte : un fournisseur avec le meme {field_label} existe deja "
                f"({existing.get('name', '')} - {dup['value']}). "
                f"Utilisez l'existant ou modifiez les criteres uniques.",
            )
        # iter85g : detection homonymes (Levenshtein >= 0.80) - bloquant sauf
        # si l'utilisateur a confirme via force_create_despite_similar=True
        if not data.force_create_despite_similar:
            similar = await find_similar_suppliers(db, name=data.name, copro_id=copro_id)
            if similar:
                names = ", ".join(f"\"{s['supplier'].get('name','')}\" ({int(s['score']*100)}%)" for s in similar[:3])
                raise HTTPException(
                    409,
                    f"Homonymes potentiels detectes : {names}. "
                    f"Verifiez si l'un correspond avant de creer un nouveau fournisseur "
                    f"(force_create_despite_similar=true pour passer outre).",
                )
        doc = {
            "id": str(uuid.uuid4()),
            **data.model_dump(exclude={"force_create_despite_similar"}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.suppliers.insert_one(doc)
        if copro_id:
            await assign_supplier_account(db, doc, copro_id)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/{supplier_id}/bce-candidates")
    async def bce_candidates(supplier_id: str, request: Request):
        """iter90gk : au refresh d'une fiche fournisseur SANS BCE, retourne
        la liste des fournisseurs cousins (nom similaire) qui ONT un BCE, pour
        que le syndic puisse rattacher (fusionner) plutot que garder un doublon.

        Retourne :
          {
            "supplier": {fiche courante},
            "candidates": [
              {id, name, bce_number, copropriete_id, matched_by: "name|address|iban",
               usage_count: {invoices, journal_entries}}
            ]
          }
        """
        is_super, allowed_copros = await _get_user_scope(request)
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not s:
            raise HTTPException(404, "Fournisseur introuvable")
        if not is_super and not _supplier_in_scope(s, allowed_copros):
            raise HTTPException(403, "Fournisseur hors scope")
        # Cherche les cousins ayant un BCE renseigne
        name_cands = _norm_name_candidates(s.get("name", ""))
        norm_iban = _norm_id(s.get("iban", ""))
        candidates = []
        all_others = await db.suppliers.find(
            {"id": {"$ne": supplier_id}, "bce_number": {"$type": "string", "$gt": ""}},
            {"_id": 0},
        ).to_list(5000)
        for o in all_others:
            # 1. Match par nom
            other_cands = _norm_name_candidates(o.get("name", ""))
            matched_by = None
            if name_cands & other_cands:
                matched_by = "name"
            elif norm_iban and _norm_id(o.get("iban", "")) == norm_iban:
                matched_by = "iban"
            if not matched_by:
                continue
            # Filtre chinese wall : le syndic ne voit que les fournisseurs
            # de ses ACPs (superadmin voit tout)
            if not is_super and not _supplier_in_scope(o, allowed_copros):
                continue
            inv_cnt = await db.invoices.count_documents({"supplier_id": o["id"]})
            je_cnt = await db.journal_entries.count_documents({"lines.third_party_id": o["id"]})
            candidates.append({
                "id": o["id"],
                "name": o.get("name", ""),
                "bce_number": o.get("bce_number", ""),
                "vat_number": o.get("vat_number", ""),
                "iban": o.get("iban", ""),
                "copropriete_id": o.get("copropriete_id", ""),
                "matched_by": matched_by,
                "usage_count": {"invoices": inv_cnt, "journal_entries": je_cnt},
            })
        # Trie : plus utilise en premier (usage_count.invoices desc)
        candidates.sort(key=lambda c: -(c["usage_count"]["invoices"] + c["usage_count"]["journal_entries"]))
        return {"supplier": s, "candidates": candidates}

    @router.get("/{supplier_id}")
    async def get_supplier(supplier_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not s:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(s, allowed_copros):
            # 404 (et non 403) pour ne pas leaker l'existence du fournisseur (RGPD)
            raise HTTPException(404, "Fournisseur non trouve")
        return s

    @router.put("/{supplier_id}")
    async def update_supplier(supplier_id: str, data: SupplierInput, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(existing, allowed_copros):
            raise HTTPException(404, "Fournisseur non trouve")
        copro_id = data.copropriete_id or getattr(request.state, "copropriete_id", "") or existing.get("copropriete_id", "")
        # Empeche un syndic de "transferer" un fournisseur vers une ACP qui n'est pas la sienne
        if not is_super and copro_id and copro_id not in (allowed_copros or []):
            raise HTTPException(403, "Vous ne pouvez attribuer ce fournisseur qu'a une de vos ACPs")
        # iter90gk : verrou BCE obligatoire aussi en mise a jour. Si le
        # fournisseur existait deja sans BCE (fiches legacy issues du wizard
        # PDF Optipro qui n'incluait pas le BCE), la mise a jour DOIT
        # renseigner le BCE. Message d'erreur explicite pour guider le
        # syndic.
        name = (data.name or "").strip()
        bce = _norm_id(data.bce_number or "")
        vat = _norm_id(data.vat_number or "")
        if not name:
            raise HTTPException(400, "Le nom du fournisseur est obligatoire.")
        if not (bce or vat):
            raise HTTPException(
                400,
                "Le numero BCE (ou TVA equivalent) est obligatoire. "
                "Renseignez-le pour finaliser la fiche fournisseur - "
                "https://kbopub.economie.fgov.be/",
            )
        # Check anti-doublon : exclure le fournisseur en cours d'edition
        dup = await find_duplicate_supplier(
            db,
            name=data.name,
            bce_number=data.bce_number or "",
            vat_number=data.vat_number or "",
            iban=data.iban or "",
            copro_id=copro_id,
            exclude_id=supplier_id,
        )
        if dup:
            field_label = {
                "bce_number": "numero BCE",
                "vat_number": "numero TVA",
                "iban": "compte bancaire (IBAN)",
                "name": "nom",
            }.get(dup["field"], dup["field"])
            existing_dup = dup["supplier"]
            raise HTTPException(
                409,
                f"Doublon detecte : un autre fournisseur avec le meme {field_label} existe deja "
                f"({existing_dup.get('name', '')} - {dup['value']}).",
            )
        result = await db.suppliers.update_one(
            {"id": supplier_id},
            {"$set": data.model_dump(exclude={"force_create_despite_similar"})},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if copro_id:
            await assign_supplier_account(db, s, copro_id)
            s = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        return s

    @router.delete("/{supplier_id}")
    async def delete_supplier(supplier_id: str, request: Request):
        is_super, allowed_copros = await _get_user_scope(request)
        existing = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Fournisseur non trouve")
        if not is_super and not _supplier_in_scope(existing, allowed_copros):
            raise HTTPException(404, "Fournisseur non trouve")
        result = await db.suppliers.delete_one({"id": supplier_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Fournisseur non trouve")
        return {"message": "Fournisseur supprime"}

    @router.post("/merge/preview")
    async def preview_supplier_merge(data: SupplierMergeInput, request: Request):
        """Prevision de fusion supplier : retourne les compteurs de references
        qui seront migrees vers keep_id, SANS modification en base."""
        is_super, allowed_copros = await _get_user_scope(request)
        keep = await db.suppliers.find_one({"id": data.keep_id}, {"_id": 0})
        if not keep:
            raise HTTPException(404, f"Fournisseur a conserver introuvable : {data.keep_id}")
        if not is_super and not _supplier_in_scope(keep, allowed_copros):
            raise HTTPException(403, "Acces refuse au fournisseur a conserver")

        if data.keep_id in data.remove_ids:
            raise HTTPException(400, "keep_id ne peut pas etre dans remove_ids")
        if not data.remove_ids:
            raise HTTPException(400, "Aucun fournisseur a fusionner")

        removes = await db.suppliers.find(
            {"id": {"$in": data.remove_ids}}, {"_id": 0}
        ).to_list(50)
        found_ids = {r["id"] for r in removes}
        missing = set(data.remove_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Fournisseur(s) introuvable(s) : {', '.join(missing)}")
        if not is_super:
            for r in removes:
                if not _supplier_in_scope(r, allowed_copros):
                    raise HTTPException(403, f"Acces refuse au fournisseur {r.get('name', '')}")

        # Compteurs (aucune modification)
        invoices = await db.invoices.count_documents({"supplier_id": {"$in": data.remove_ids}})
        txns = await db.bank_transactions.count_documents(
            {"match_type": "supplier_payment", "matched_to": {"$in": data.remove_ids}}
        )

        # Enrichissement previsionnel
        enrich_fields = [
            "bce_number", "vat_number", "iban", "bic", "email", "phone",
            "address", "postal_code", "city", "country", "notes",
        ]
        will_enrich = []
        for f in enrich_fields:
            if not (keep.get(f) or "").strip():
                for r in removes:
                    if (r.get(f) or "").strip():
                        will_enrich.append({
                            "field": f, "from": r.get("name", "?"), "value": r[f]
                        })
                        break

        return {
            "keep": {
                "id": keep["id"],
                "name": keep.get("name", ""),
                "bce_number": keep.get("bce_number", ""),
                "vat_number": keep.get("vat_number", ""),
            },
            "remove_count": len(removes),
            "removes": [
                {"id": r["id"], "name": r.get("name", ""), "bce_number": r.get("bce_number", "")}
                for r in removes
            ],
            "migrations": {
                "invoices": invoices,
                "bank_transactions_matched": txns,
            },
            "total_refs": invoices + txns,
            "will_enrich_fields": will_enrich,
        }

    @router.post("/merge")
    async def merge_suppliers(data: SupplierMergeInput, request: Request):
        """Fusion de N fournisseurs : keep_id conserve, remove_ids absorbes.

        Reassociations effectuees :
        - invoices.supplier_id : remove_ids -> keep_id
        - invoices.supplier_name : mis a jour si match remove_id
        - bank_transactions.matched_to (match_type='supplier_payment') -> keep_id
        - journal_entries : aucune modification (lignes referencent les comptes
          PCMN, pas les supplier_ids ; les comptes tiers sont par fournisseur
          mais on prefere conserver les ecritures historiques telles quelles
          pour la tracabilite comptable)

        Enrichissement de keep depuis remove (premier non-vide gagne) sur :
        bce_number, vat_number, iban, bic, email, phone, address, postal_code,
        city, country.

        Apres absorption, les remove_ids sont supprimes.
        """
        is_super, allowed_copros = await _get_user_scope(request)
        keep = await db.suppliers.find_one({"id": data.keep_id}, {"_id": 0})
        if not keep:
            raise HTTPException(404, f"Fournisseur a conserver introuvable : {data.keep_id}")
        if not is_super and not _supplier_in_scope(keep, allowed_copros):
            raise HTTPException(403, "Acces refuse au fournisseur a conserver")

        if data.keep_id in data.remove_ids:
            raise HTTPException(400, "keep_id ne peut pas etre dans remove_ids")
        if not data.remove_ids:
            raise HTTPException(400, "Aucun fournisseur a fusionner")

        removes = await db.suppliers.find(
            {"id": {"$in": data.remove_ids}}, {"_id": 0}
        ).to_list(50)
        found_ids = {r["id"] for r in removes}
        missing = set(data.remove_ids) - found_ids
        if missing:
            raise HTTPException(404, f"Fournisseur(s) a fusionner introuvable(s) : {', '.join(missing)}")
        if not is_super:
            for r in removes:
                if not _supplier_in_scope(r, allowed_copros):
                    raise HTTPException(403, f"Acces refuse au fournisseur {r.get('name', '')}")

        # 1) Enrichissement keep depuis removes (premier non-vide gagne)
        enrich_fields = [
            "bce_number", "vat_number", "iban", "bic", "email", "phone",
            "address", "postal_code", "city", "country", "notes",
        ]
        enrichment = {}
        for f in enrich_fields:
            if not (keep.get(f) or "").strip():
                for r in removes:
                    if (r.get(f) or "").strip():
                        enrichment[f] = r[f]
                        break
        if enrichment:
            await db.suppliers.update_one({"id": data.keep_id}, {"$set": enrichment})

        # 2) Migration des factures
        invoices_updated = await db.invoices.update_many(
            {"supplier_id": {"$in": data.remove_ids}},
            {"$set": {"supplier_id": data.keep_id, "supplier_name": keep.get("name", "")}},
        )

        # 3) Migration des bank_transactions appariees a un fournisseur
        txns_updated = await db.bank_transactions.update_many(
            {"match_type": "supplier_payment", "matched_to": {"$in": data.remove_ids}},
            {"$set": {"matched_to": data.keep_id}},
        )

        # 4) Suppression des doublons
        deleted = await db.suppliers.delete_many({"id": {"$in": data.remove_ids}})

        return {
            "message": f"Fusion effectuee : {deleted.deleted_count} fournisseur(s) absorbe(s)",
            "kept_id": data.keep_id,
            "removed_ids": data.remove_ids,
            "enriched_fields": list(enrichment.keys()),
            "invoices_migrated": invoices_updated.modified_count,
            "bank_transactions_migrated": txns_updated.modified_count,
        }

    return router
