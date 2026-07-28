from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from pathlib import Path
import uuid, re, unicodedata
from gridfs_storage import get_journal_attachments_storage


def _norm_pcmn_name(name: str) -> str:
    """Normalize PCMN account name for similarity comparison."""
    if not name:
        return ""
    s = name.lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _pcmn_names_similar(a: str, b: str) -> bool:
    """Return True if two normalised PCMN names are similar enough to be duplicates."""
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    return overlap / max(len(wa), len(wb)) >= 0.7

# Legacy path kept ONLY for backward-compat fallback reads (iter87 migration).
ATTACHMENTS_DIR = Path("/app/uploads/journal_attachments")
try:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


class JournalEntryLine(BaseModel):
    account_number: str
    account_name: Optional[str] = ""
    debit: Optional[float] = 0.0
    credit: Optional[float] = 0.0
    description: Optional[str] = ""
    # Repartition occupant/proprietaire (pour decompte locataire).
    # None = ignore (sera complete par defaut au moment de la creation si la ligne
    # est une charge 6xxx avec une categorie associee). Somme attendue : 100.
    occupant_pct: Optional[float] = None
    proprietaire_pct: Optional[float] = None
    # iter90g1 : nature de depense (Distribution Key) par ligne d'ecriture.
    # Utilisee cote portail proprietaire pour projeter la quote-part des OD.
    distribution_key_id: Optional[str] = None
    # iter91b : expense_category_id (Nature de depense) - source de la
    # pre-remplissage automatique de account_number / %Occ / %Prop / distribution_key.
    # Conserve pour audit trail et rapport OD par nature.
    expense_category_id: Optional[str] = None


class JournalEntryInput(BaseModel):
    journal_type: str  # OD, AV, AP
    date: str
    reference: Optional[str] = ""
    description: str
    lines: List[JournalEntryLine]
    copropriete_id: Optional[str] = ""


class PCMNAccountInput(BaseModel):
    number: str
    name: str
    class_num: Optional[int] = None  # auto-derived from first digit if absent
    parent: Optional[str] = None
    type: Optional[str] = None  # auto-derived (balance for 1-5, result for 6-7)
    copropriete_id: Optional[str] = ""
    active: Optional[bool] = True  # custom accounts default to active


class PCMNToggleInput(BaseModel):
    active: bool


class PCMNUpdateInput(BaseModel):
    name: Optional[str] = None
    class_num: Optional[int] = None
    parent: Optional[str] = None
    type: Optional[str] = None
    active: Optional[bool] = None
    copropriete_id: Optional[str] = ""


def create_accounting_router(db):
    router = APIRouter(prefix="/api/accounting")

    # ---- PCMN ----
    @router.get("/pcmn")
    async def list_pcmn(request: Request,
                        search: Optional[str] = None, class_num: Optional[int] = None,
                        copropriete_id: Optional[str] = None, only_active: Optional[bool] = False):
        # iter90bf : Chinese walls - si pas de copropriete_id en param, lire
        # depuis le header X-Copropriete-Id. Evite le melange des PCMNs
        # entre ACPs (bug : 1000 premiers comptes de toutes les ACPs).
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        query = {}
        if copropriete_id and copropriete_id != "all":
            query["copropriete_id"] = copropriete_id
        if only_active:
            query["active"] = True
        if search:
            query["$or"] = [
                {"number": {"$regex": search, "$options": "i"}},
                {"name": {"$regex": search, "$options": "i"}}
            ]
        if class_num is not None:
            query["class_num"] = class_num
        # Limite elargie a 5000 pour couvrir un PCMN complet + comptes de tiers
        accounts = await db.pcmn_accounts.find(query, {"_id": 0}).sort("number", 1).to_list(5000)
        return accounts

    @router.post("/pcmn")
    async def create_pcmn_account(data: PCMNAccountInput):
        if not data.number or not data.number.isdigit():
            raise HTTPException(400, "Le numero de compte doit etre numerique")
        q = {"number": data.number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        existing = await db.pcmn_accounts.find_one(q)
        if existing:
            raise HTTPException(400, "Ce numero de compte existe deja dans cette ACP")
        class_num = data.class_num if data.class_num else int(data.number[0])
        # Garde-fou anti-doublons : interdit la creation d'un compte 6/7xxx
        # si un compte avec un nom similaire existe deja dans la meme ACP.
        if class_num in (6, 7) and data.name:
            norm_new = _norm_pcmn_name(data.name)
            if norm_new:
                sim_q = {"class_num": class_num}
                if data.copropriete_id:
                    sim_q["copropriete_id"] = data.copropriete_id
                candidates = await db.pcmn_accounts.find(
                    sim_q, {"_id": 0, "number": 1, "name": 1}
                ).to_list(500)
                for acc in candidates:
                    if _pcmn_names_similar(norm_new, _norm_pcmn_name(acc.get("name", ""))):
                        raise HTTPException(
                            409,
                            f"Un compte similaire existe deja : {acc['number']} - {acc.get('name','')}. "
                            f"Utilisez ce compte existant plutot que d'en creer un nouveau "
                            f"(numero demande : {data.number} - {data.name})."
                        )
        typ = data.type or ("balance" if class_num <= 5 else "result")
        doc = {
            "number": data.number,
            "name": data.name,
            "class_num": class_num,
            "parent": data.parent,
            "type": typ,
            "copropriete_id": data.copropriete_id or "",
            "active": data.active if data.active is not None else True,
            "is_custom": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.pcmn_accounts.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/pcmn/{number}")
    async def update_pcmn_account(number: str, data: PCMNUpdateInput):
        q = {"number": number}
        if data.copropriete_id:
            q["copropriete_id"] = data.copropriete_id
        update_doc = {}
        for fld in ("name", "class_num", "parent", "type", "active"):
            val = getattr(data, fld)
            if val is not None:
                update_doc[fld] = val
        if not update_doc:
            raise HTTPException(400, "Rien a modifier")
        result = await db.pcmn_accounts.update_one(q, {"$set": update_doc})
        if result.matched_count == 0:
            raise HTTPException(404, "Compte non trouve")
        return await db.pcmn_accounts.find_one(q, {"_id": 0})

    @router.patch("/pcmn/{number}/toggle-active")
    async def toggle_pcmn_active(number: str, data: PCMNToggleInput, copropriete_id: Optional[str] = None):
        q = {"number": number}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        result = await db.pcmn_accounts.update_one(q, {"$set": {"active": data.active}})
        if result.matched_count == 0:
            raise HTTPException(404, "Compte non trouve")
        return await db.pcmn_accounts.find_one(q, {"_id": 0})

    @router.delete("/pcmn/{number}")
    async def delete_pcmn_account(number: str, copropriete_id: Optional[str] = None, force: Optional[bool] = False):
        q = {"number": number}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        # Trouver le compte pour verifier son statut
        acc = await db.pcmn_accounts.find_one(q, {"_id": 0})
        if not acc:
            raise HTTPException(404, "Compte non trouve")
        # Protections
        if acc.get("is_tier_account"):
            raise HTTPException(400, "Ce compte tiers (auto) ne peut etre supprime. Supprimez le tiers concerne.")
        if not force and not acc.get("is_custom"):
            raise HTTPException(400, "Ce compte fait partie du PCMN officiel. Utilisez ?force=true pour supprimer quand meme.")
        # Verifier qu'il n'est pas utilise
        copro_filter = {"copropriete_id": copropriete_id} if copropriete_id else {}
        used_entries = await db.journal_entries.count_documents(
            {**copro_filter, "lines.account_number": number}
        )
        used_invoices = await db.invoices.count_documents(
            {**copro_filter, "$or": [{"account_number": number}, {"lines.account_number": number}]}
        )
        used_cats = await db.expense_categories.count_documents(
            {**copro_filter, "account_number": number}
        )
        if used_entries + used_invoices + used_cats > 0:
            raise HTTPException(
                409,
                f"Compte utilise: {used_entries} ecriture(s), {used_invoices} facture(s), {used_cats} nature(s) de depense"
            )
        await db.pcmn_accounts.delete_one(q)
        return {"message": "Compte supprime", "number": number}

    # ---- JOURNAL ENTRIES ----
    @router.get("/entries")
    async def list_entries(
        request: Request,
        journal_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        search: Optional[str] = None,
        reference: Optional[str] = None,
        account_number: Optional[str] = None,
        # iter90bt : nouveaux filtres pour retrouver rapidement des mouvements
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        third_party_id: Optional[str] = None,
        third_party_name: Optional[str] = None,
        include_reversals: Optional[bool] = True,
    ):
        """Chinese walls STRICT : `copropriete_id` requis (param ou header
        X-Copropriete-Id). Sans scope ACP -> liste vide.

        iter90bx : par defaut affiche TOUTES les ecritures y compris
        contre-passations et originales extournees (audit trail legal belge -
        art. III.86 CDE). Chaque ecriture porte des flags `reversed` /
        `is_reversal` que le frontend peut utiliser pour un rendu visuel
        differencie (badges).
        Passer `include_reversals=false` pour n'obtenir que la vue "active"
        (utile pour verifier une nouvelle saisie).

        Filtres iter90bt :
          - amount_min / amount_max : filtre sur les LIGNES (debit OU credit
            d'une ligne au moins doit tomber dans la fourchette).
          - third_party_id : match exact sur lines.third_party_id
          - third_party_name : match regex insensible sur lines.third_party_name
        """
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            return []
        query = {"copropriete_id": copropriete_id}
        if journal_type:
            query["journal_type"] = journal_type
        if date_from:
            query["date"] = {"$gte": date_from}
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to
        if reference:
            import re as _re
            query["reference"] = {"$regex": _re.escape(reference), "$options": "i"}
        if search:
            import re as _re2
            rgx = {"$regex": _re2.escape(search), "$options": "i"}
            query["$or"] = [{"description": rgx}, {"reference": rgx}, {"lines.account_name": rgx}, {"lines.third_party_name": rgx}]
        if account_number:
            query["lines.account_number"] = account_number
        # iter90bt : filtre tiers exact ou par nom
        if third_party_id:
            query["lines.third_party_id"] = third_party_id
        if third_party_name:
            import re as _re3
            query["lines.third_party_name"] = {
                "$regex": _re3.escape(third_party_name), "$options": "i",
            }
        # iter90bt : filtre montant (au moins une ligne dans la fourchette).
        # On combine debit + credit pour couvrir les 2 sens comptables.
        if amount_min is not None or amount_max is not None:
            amt_conds = []
            for side in ("debit", "credit"):
                cond: dict = {}
                if amount_min is not None:
                    cond["$gte"] = float(amount_min)
                if amount_max is not None:
                    cond["$lte"] = float(amount_max)
                amt_conds.append({f"lines.{side}": cond})
            # $or dans le contexte des lignes : une ligne avec debit dans la
            # fourchette OU credit dans la fourchette suffit
            existing_or = query.pop("$or", None)
            if existing_or:
                query["$and"] = [{"$or": existing_or}, {"$or": amt_conds}]
            else:
                query["$or"] = amt_conds
        # Filtre contre-passations
        if not include_reversals:
            query["reversed"] = {"$ne": True}
            query["is_reversal"] = {"$ne": True}
        entries = await db.journal_entries.find(query, {"_id": 0}).sort("date", -1).to_list(5000)
        # iter90x : enrichit les FI entries avec l'info de la facture liee
        # (utile pour le bouton "Delettrer" dans le journal financier).
        if journal_type == "FI":
            bank_txn_ids = [e.get("source_id", "") for e in entries
                            if e.get("source_type") == "bank_txn" and e.get("source_id")]
            if bank_txn_ids:
                txns = await db.bank_transactions.find(
                    {"id": {"$in": bank_txn_ids}},
                    {"_id": 0, "id": 1, "matched": 1, "matched_to": 1,
                     "match_type": 1, "lettrage_code": 1},
                ).to_list(len(bank_txn_ids))
                txn_by_id = {t["id"]: t for t in txns}
                # Pre-fetch invoices for matched txns
                inv_ids = [t.get("matched_to") for t in txns
                           if t.get("match_type") == "invoice" and t.get("matched_to")]
                inv_map = {}
                if inv_ids:
                    invs = await db.invoices.find(
                        {"id": {"$in": list(set(inv_ids))}},
                        {"_id": 0, "id": 1, "number": 1, "invoice_number": 1,
                         "supplier": 1, "supplier_name": 1,
                         "amount_ttc": 1, "total_amount": 1, "amount": 1},
                    ).to_list(len(inv_ids))
                    inv_map = {i["id"]: i for i in invs}
                for e in entries:
                    if e.get("source_type") != "bank_txn":
                        continue
                    t = txn_by_id.get(e.get("source_id", ""))
                    if not t or not t.get("matched"):
                        continue
                    e["bank_txn_matched"] = True
                    e["bank_txn_match_type"] = t.get("match_type", "")
                    if t.get("match_type") == "invoice" and t.get("matched_to") in inv_map:
                        inv = inv_map[t["matched_to"]]
                        amt = inv.get("amount_ttc") or inv.get("total_amount") or inv.get("amount") or 0
                        e["linked_invoice"] = {
                            "id": inv["id"],
                            "invoice_number": inv.get("number") or inv.get("invoice_number") or "",
                            "supplier_name": inv.get("supplier") or inv.get("supplier_name") or "",
                            "amount_ttc": float(amt) if amt else 0,
                        }
        return entries

    async def _enrich_lines_with_occupant_pct(lines: list, copro_id: str) -> list:
        """Pour chaque ligne d'OD qui a un compte de classe 6 (charge) sans pct fourni,
        pre-rempli les % occupant/proprietaire depuis la nature de depense associee
        au compte (defaut 0% occupant / 100% proprio si non mappee).
        Valide aussi la somme = 100 quand au moins un est fourni.
        """
        for ln in lines:
            occ = ln.get("occupant_pct")
            prop = ln.get("proprietaire_pct")
            if occ is None and prop is None:
                # Heriter de la categorie de depense si compte classe 6
                acc_num = ln.get("account_number", "") or ""
                if acc_num.startswith(("6", "7")):
                    cat = await db.expense_categories.find_one(
                        {"account_number": acc_num, "copropriete_id": copro_id}, {"_id": 0}
                    )
                    if cat and cat.get("default_occupant_pct") is not None:
                        ln["occupant_pct"] = float(cat.get("default_occupant_pct") or 0)
                        ln["proprietaire_pct"] = round(100 - ln["occupant_pct"], 2)
                    else:
                        ln["occupant_pct"] = 0.0
                        ln["proprietaire_pct"] = 100.0
                # Autres comptes : pas de repartition (laisser None)
            elif occ is not None or prop is not None:
                occ = float(occ or 0)
                prop = float(prop or 0)
                if abs((occ + prop) - 100) > 0.01:
                    # Auto-corriger en complement si une seule valeur fournie est coherente
                    if ln.get("occupant_pct") is not None and ln.get("proprietaire_pct") is None:
                        ln["proprietaire_pct"] = round(100 - occ, 2)
                    elif ln.get("proprietaire_pct") is not None and ln.get("occupant_pct") is None:
                        ln["occupant_pct"] = round(100 - prop, 2)
                    else:
                        raise HTTPException(
                            400,
                            f"Ligne {ln.get('account_number','?')} : % occupant ({occ}) + % proprietaire ({prop}) doit etre 100."
                        )
        return lines

    @router.post("/entries")
    async def create_entry(data: JournalEntryInput, request: Request):
        from fiscal_lock import ensure_period_open
        copro_id = (data.copropriete_id or "").strip() or (request.headers.get("X-Copropriete-Id") or "").strip()
        if not copro_id or copro_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        # Securisation comptable : la date doit etre dans un exercice OUVERT de l'ACP
        await ensure_period_open(db, copro_id, data.date, context="ecriture")
        # iter90gj : anti-doublon strict. Une ecriture avec la meme reference,
        # meme journal_type, meme copropriete et meme date ne peut PAS etre
        # cree deux fois. Cas typique : le syndic ajoute manuellement une
        # facture dont l'ecriture a deja ete creee par le wizard d'import.
        ref = (data.reference or "").strip()
        if ref:
            existing_dup = await db.journal_entries.find_one({
                "copropriete_id": copro_id,
                "journal_type": data.journal_type,
                "reference": ref,
                "date": data.date,
                "is_reversal": {"$ne": True},
            }, {"_id": 0, "id": 1, "reference": 1, "created_at": 1})
            if existing_dup:
                raise HTTPException(
                    409,
                    f"Doublon detecte : une ecriture {data.journal_type} avec la reference "
                    f"'{ref}' existe deja pour cette ACP au {data.date} "
                    f"(id {existing_dup['id'][:8]}). Modifiez l'existante ou changez la reference.",
                )
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            from utils.format import fmt_eur as _fmt_eur
            raise HTTPException(400, f"Ecriture non equilibree: Debit={_fmt_eur(total_debit)}, Credit={_fmt_eur(total_credit)}")
        # Verify each account_number actually belongs to the ACP's PCMN (no leak)
        accs_used = {l.account_number for l in data.lines if l.account_number}
        if accs_used:
            existing = await db.pcmn_accounts.find(
                {"copropriete_id": copro_id, "number": {"$in": list(accs_used)}}, {"_id": 0, "number": 1}
            ).to_list(2000)
            existing_set = {p["number"] for p in existing}
            missing = accs_used - existing_set
            if missing:
                raise HTTPException(
                    400,
                    f"Comptes PCMN absents de cette ACP : {sorted(missing)}. "
                    "Chaque ACP dispose de son propre plan comptable - aucun melange autorise."
                )
        # Enrichir chaque ligne avec la repartition occupant/proprietaire
        raw_lines = [l.model_dump() for l in data.lines]
        raw_lines = await _enrich_lines_with_occupant_pct(raw_lines, copro_id)
        doc = {
            "id": str(uuid.uuid4()),
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": raw_lines,
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        from syndic_scope import inject_syndic
        inject_syndic(doc, request)
        await db.journal_entries.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/entries/{entry_id}")
    async def get_entry(entry_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        return entry

    @router.put("/entries/{entry_id}")
    async def update_entry(entry_id: str, data: JournalEntryInput):
        from fiscal_lock import ensure_entry_modifiable, ensure_period_open
        existing = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Ecriture non trouvee")
        # Securisation comptable : bloque si exercice cloture ou ecriture extournee
        await ensure_entry_modifiable(db, existing)
        # Verifier aussi la NOUVELLE date (cas ou l'utilisateur change la date vers
        # un autre exercice qui serait cloture)
        if data.date and data.date != existing.get("date"):
            await ensure_period_open(
                db, existing.get("copropriete_id", ""), data.date, context="ecriture"
            )
        copro_id = existing.get("copropriete_id", "") or (data.copropriete_id or "")
        total_debit = sum(l.debit for l in data.lines)
        total_credit = sum(l.credit for l in data.lines)
        if abs(total_debit - total_credit) > 0.01:
            raise HTTPException(400, "Ecriture non equilibree")
        # Enrichir avec la repartition occupant/proprietaire (cf. create_entry)
        raw_lines = [l.model_dump() for l in data.lines]
        raw_lines = await _enrich_lines_with_occupant_pct(raw_lines, copro_id)
        update = {
            "journal_type": data.journal_type,
            "date": data.date,
            "reference": data.reference,
            "description": data.description,
            "lines": raw_lines,
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
        }
        # Mark as manually edited if originally auto-generated
        if existing.get("auto_generated"):
            update["manually_edited"] = True
            update["manually_edited_at"] = datetime.now(timezone.utc).isoformat()
        await db.journal_entries.update_one({"id": entry_id}, {"$set": update})
        return await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})

    @router.delete("/entries/{entry_id}")
    async def delete_entry(entry_id: str, reason: str = ""):
        """iter90bx : NE SUPPRIME PLUS - genere une contre-passation.

        Regle metier belge (PCMN + art. III.86 CDE) : une ecriture comptable ne
        peut jamais etre supprimee. On cree une ecriture INVERSE qui neutralise
        les montants tout en gardant la trace audit.

        Idempotent : renvoie 400 si l'ecriture est deja une contre-passation
        ou deja extournee.
        """
        from journal_reversals import reverse_journal_entry
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        if entry.get("is_reversal"):
            raise HTTPException(
                400,
                "Cette ecriture est deja une contre-passation, elle ne peut pas etre extournee.",
            )
        if entry.get("reversed"):
            raise HTTPException(
                400,
                "Cette ecriture a deja ete contre-passee. Consultez l'ecriture inverse liee.",
            )
        # Ecritures auto : on autorise la contre-passation manuelle avec un warning
        # dans le message. Historiquement on refusait ; iter90bx : le principe de
        # traceabilite prime, l'utilisateur assume la responsabilite.
        if entry.get("auto_generated") and not entry.get("manually_edited"):
            # Cas special : on suggere plutot de supprimer la source
            raise HTTPException(
                400,
                "Ecriture auto-generee : supprimez la source (facture, appel, extrait) plutot que l'ecriture. La contre-passation se fera en cascade.",
            )
        # Verrou fiscal : si l'ecriture est dans un exercice cloture, on utilise
        # la date du jour pour la contre-passation (helper _resolve_reversal_date).
        # On ne bloque plus la contre-passation elle-meme (elle est LEGITIME).
        rev = await reverse_journal_entry(db, entry, reason=reason)
        if not rev:
            raise HTTPException(500, "Contre-passation impossible (etat incoherent)")
        return {
            "message": "Ecriture contre-passee. L'originale est preservee (audit legal).",
            "original_id": entry_id,
            "reversal_id": rev["id"],
            "reversal_reference": rev.get("reference", ""),
        }

    # ---- ATTACHMENTS for journal entries ----
    @router.post("/entries/{entry_id}/attachments")
    async def upload_entry_attachment(entry_id: str, file: UploadFile = File(...)):
        """Attach a PDF document to a journal entry (Operations Diverses or any entry).

        iter87 : stored in MongoDB GridFS bucket `journal_attachments`.
        """
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        ext = Path(file.filename or "file").suffix.lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "Format autorise: PDF, PNG, JPG")
        content = await file.read()
        att_id = str(uuid.uuid4())
        att_storage = get_journal_attachments_storage(db)
        gridfs_id = await att_storage.upload(
            filename=file.filename or f"{att_id}{ext}",
            contents=content,
            metadata={
                "attachment_id": att_id,
                "entry_id": entry_id,
                "copropriete_id": entry.get("copropriete_id", ""),
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
        await db.journal_entries.update_one(
            {"id": entry_id},
            {"$push": {"attachments": attachment}}
        )
        return attachment

    @router.get("/entries/{entry_id}/attachments/{attachment_id}/download")
    async def download_entry_attachment(entry_id: str, attachment_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        att_storage = get_journal_attachments_storage(db)
        for att in entry.get("attachments", []) or []:
            if att.get("id") != attachment_id:
                continue
            media_type = att.get("mime_type", "application/pdf")
            filename = att.get("filename", "attachment.pdf")
            # iter87 : prefer GridFS (new), fallback to disk (legacy)
            gid = att.get("gridfs_id")
            if gid:
                try:
                    data = await att_storage.download(gid)
                except Exception:
                    raise HTTPException(404, "Fichier introuvable dans GridFS")
                safe_name = filename.replace('"', "")
                return Response(
                    content=data,
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )
            path = att.get("stored_path", "")
            if path and Path(path).exists():
                return FileResponse(path, media_type=media_type, filename=filename)
            raise HTTPException(404, "Fichier introuvable sur le disque")
        raise HTTPException(404, "Piece jointe non trouvee")

    @router.delete("/entries/{entry_id}/attachments/{attachment_id}")
    async def delete_entry_attachment(entry_id: str, attachment_id: str):
        entry = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not entry:
            raise HTTPException(404, "Ecriture non trouvee")
        target = None
        for att in entry.get("attachments", []) or []:
            if att.get("id") == attachment_id:
                target = att
                break
        if not target:
            raise HTTPException(404, "Piece jointe non trouvee")
        att_storage = get_journal_attachments_storage(db)
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
        await db.journal_entries.update_one(
            {"id": entry_id},
            {"$pull": {"attachments": {"id": attachment_id}}}
        )
        return {"message": "Piece jointe supprimee"}

    @router.put("/entries/{entry_id}/line-quick")
    async def update_entry_line_quick(entry_id: str, data: dict):
        """Quick edit for a SPECIFIC charge line (account 6XX) in a journal entry.
        Used by /expenses page to allow editing bank fees, financial expenses,
        and any other journal-based expense (FI/OD type).

        Body : {
          line_account: str (the account_number to find in lines),
          new_account: str (optional - change account),
          expense_category_id: str (optional),
          distribution_key_id: str (optional),
          occupant_pct: float (optional),
          proprietaire_pct: float (optional),
          description: str (optional),
        }
        """
        from fiscal_lock import ensure_entry_modifiable
        existing = await db.journal_entries.find_one({"id": entry_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Ecriture non trouvee")
        await ensure_entry_modifiable(db, existing)
        line_account = (data.get("line_account") or "").strip()
        if not line_account:
            raise HTTPException(400, "line_account requis")
        lines = existing.get("lines", []) or []
        target_idx = next((i for i, ln in enumerate(lines) if (ln.get("account_number") or "") == line_account), None)
        if target_idx is None:
            raise HTTPException(404, f"Aucune ligne avec compte {line_account}")
        line = lines[target_idx]
        # Apply patches (only if provided)
        if "new_account" in data and data.get("new_account"):
            line["account_number"] = data["new_account"].strip()
            # Optionally update account_name
            new_acc = await db.pcmn_accounts.find_one({"copropriete_id": existing.get("copropriete_id", ""), "number": data["new_account"]}, {"_id": 0, "name": 1})
            if new_acc:
                line["account_name"] = new_acc.get("name", "")
        if "expense_category_id" in data:
            line["expense_category_id"] = data.get("expense_category_id") or ""
        if "distribution_key_id" in data:
            line["distribution_key_id"] = data.get("distribution_key_id") or ""
        # iter90fn : la repartition Occ/Prop DOIT toujours sommer a 100 -
        # jamais 2 valeurs independantes (meme bug que sur les factures,
        # corrige dans expense_rows.py). Le champ fourni fait foi, l'autre
        # est TOUJOURS derive automatiquement.
        if "occupant_pct" in data:
            line["occupant_pct"] = float(data["occupant_pct"] or 0)
            line["proprietaire_pct"] = round(100.0 - line["occupant_pct"], 2)
        elif "proprietaire_pct" in data:
            line["proprietaire_pct"] = float(data["proprietaire_pct"] or 0)
            line["occupant_pct"] = round(100.0 - line["proprietaire_pct"], 2)
        if "description" in data:
            line["description"] = (data.get("description") or "").strip()
        lines[target_idx] = line
        await db.journal_entries.update_one(
            {"id": entry_id},
            {"$set": {
                "lines": lines,
                "manually_edited": True,
                "manually_edited_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return {"ok": True, "line_index": target_idx, "updated_fields": [k for k in ["new_account", "expense_category_id", "distribution_key_id", "occupant_pct", "proprietaire_pct", "description"] if k in data]}

    # ---- BALANCE / BILAN ----
    @router.get("/balance")
    async def get_balance(copropriete_id: Optional[str] = None):
        """Get trial balance (balance des comptes) scoped by ACP."""
        q = {}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(10000)
        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "total_debit": 0, "total_credit": 0}
                balances[acc]["total_debit"] += line.get("debit", 0)
                balances[acc]["total_credit"] += line.get("credit", 0)
        result = []
        for acc in sorted(balances.keys()):
            b = balances[acc]
            b["balance"] = round(b["total_debit"] - b["total_credit"], 2)
            b["total_debit"] = round(b["total_debit"], 2)
            b["total_credit"] = round(b["total_credit"], 2)
            result.append(b)
        return result

    return router
