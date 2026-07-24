from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
import uuid
import io


def _apply_copro(q: dict, copropriete_id: Optional[str], request=None) -> dict:
    """Add copropriete_id + syndic_id filters to a Mongo query (chinese wall + multi-syndic)."""
    if copropriete_id:
        q["copropriete_id"] = copropriete_id
    if request:
        from syndic_scope import syndic_query
        q.update(syndic_query(request))
    return q


def _date_lte(date_str: str) -> str:
    """Normalise une date pour un filtre Mongo $lte incluant toute la journee.

    Les ecritures peuvent etre stockees avec un composant horaire
    (ex: '2026-12-31T12:00:00'). La comparaison de chaines avec
    '2026-12-31' exclut alors ces ecritures car 'T' > '' au 11e caractere.
    En ajoutant T23:59:59 on garantit l'inclusion de toute la journee.
    """
    if date_str and len(date_str) == 10:
        return date_str + "T23:59:59"
    return date_str


def _require_copro(copropriete_id: Optional[str], request) -> str:
    """Resolve & require copropriete_id from param OR header X-Copropriete-Id.
    Raise 400 if missing. Chinese walls strict - regle non-modifiable."""
    if not copropriete_id:
        copropriete_id = request.headers.get("X-Copropriete-Id") if request else None
    if not copropriete_id or copropriete_id == "all":
        raise HTTPException(
            400,
            "copropriete_id requis - chinese walls strict : aucune ACP n'est selectionnee."
        )
    return copropriete_id


def _exclude_reversals(q: dict) -> dict:
    """Exclude reversal pairs (contre-passations + ecritures extournees) from queries.

    Pour les balances/grand livre/decomptes : une ecriture qui a ete contre-passee
    (`reversed=True`) ainsi que sa contre-passation (`is_reversal=True`) ne doivent
    pas apparaitre dans les soldes "operationnels" : elles s'annulent comptablement.
    Les listings de journaux peuvent les afficher en mode "audit" via un flag UI.

    iter90fl : delegue a `journal_reversals.exclude_reversals` (SOURCE UNIQUE DE
    VERITE) pour eviter toute divergence de noms de champs entre modules -
    c'est exactement ce genre de duplication qui a cause le bug de lignes
    fantomes dans "Liste des depenses" (`expense_rows.py` utilisait des noms
    de champs inexistants). Garde le nom de fonction pour compat avec les
    ~12 appels existants dans ce fichier.
    """
    from journal_reversals import exclude_reversals
    return exclude_reversals(q)


_MUTATION_LOT_RE = __import__("re").compile(
    r"^\s*(?:\[[A-Z]{2,3}\]\s*)?"        # optionnel "[OD] "
    r"(?:Operation\s*:\s*)?"              # optionnel "Operation : "
    r"Mutation\s+lot\s+\S+\s*-\s*"        # "Mutation lot XXX -"
    r"(.+?)"                               # groupe 1 : label (autorise parentheses/hyphens)
    r"(?:\s*:\s*(.+?)\s+->\s+([^(]+?))?"  # groupes 2/3 (opt) : from -> to
    r"(?:\s*\([^)]*EUR[^)]*\))?"           # optionnel "(206.44 EUR)"
    r"\s*$"
)


def _normalize_mutation_desc(desc: str):
    """iter90bz : extrait le suffixe STABLE d'une description "Mutation lot XXX - <label>".

    iter90dw : retourne aussi from_owner_name et to_owner_name pour permettre
    l'aggregation PAR PROPRIETAIRE COUNTERPART dans la vue du vendeur.

    Retourne un tuple (label, from_name, to_name) ou None si le pattern ne matche pas.
    Backward-compat : callers qui n'utilisent que la premiere valeur restent OK.

    Exemples :
      "Mutation lot 001 - Prorata appel (Q1/4): Matexi -> Dewinter (206.44 EUR)"
        -> ("Mutation lots - Prorata appel (Q1/4)", "Matexi", "Dewinter")
      "Mutation lot C9 - Fonds de roulement: Matexi -> Buyer"
        -> ("Mutation lots - Fonds de roulement", "Matexi", "Buyer")
    """
    if not desc:
        return None
    m = _MUTATION_LOT_RE.match(desc)
    if not m:
        return None
    label = (m.group(1) or "").strip().rstrip(" -")
    if not label:
        return None
    from_name = (m.group(2) or "").strip() if m.group(2) else ""
    to_name = (m.group(3) or "").strip() if m.group(3) else ""
    return (f"Mutation lots - {label}", from_name, to_name)


def _group_movements_by_owner(movements: list, self_owner_name: str = "") -> list:
    """iter90bv + iter90bz : regroupe les mouvements d'un meme proprietaire pour
    tous ses lots.

    iter90bv : quand un proprietaire possede plusieurs lots dans une ACP, un
    meme appel de fonds (VE) genere N lignes debit sur son compte tier (une
    par lot x type). Cette fonction aggregge ces lignes en UNE SEULE par
    (entry_id, account_number, description) en sommant debit et credit.

    iter90bz : cas des mutations. Chaque mutation lot cree une ecriture OD
    distincte (reference unique par lot). Pour un promoteur avec 30 lots,
    cela genere 30 lignes visuellement identiques par trimestre. On les
    fusionne via un pattern regex sur "Mutation lot XXX - <label>" en une
    seule ligne "Mutations (N lots) - <label>".

    iter90dw (Feb 2026) : les mutations sont maintenant regroupees PAR
    COUNTERPART OWNER pour la vue du vendeur. Ex : "Mutations (3 lots) -
    Fonds de roulement -> Dewinter" (plutot que d'additionner Dewinter +
    Lahaye dans un seul bucket, non-lisible pour le vendeur).

    Preserve l'ordre chronologique et la structure des mouvements (dates,
    references, types de journal). Les journal_entries en base sont
    INCHANGEES (audit trail preserve).

    Args:
      movements: liste des mouvements
      self_owner_name: nom du proprietaire dont c'est la situation
        (pour identifier le counterpart parmi from/to). Optionnel.
    """
    if not movements:
        return []
    buckets = {}
    counts = {}
    order = []
    for m in movements:
        raw_desc = (m.get("description") or "").strip()
        norm = _normalize_mutation_desc(raw_desc)
        if norm:
            norm_label, from_name, to_name = norm
            # iter90dw : identifie le counterpart (l'autre owner de la mutation)
            counterpart = ""
            if self_owner_name:
                sn = self_owner_name.strip().lower()
                if from_name.strip().lower() == sn:
                    counterpart = to_name
                elif to_name.strip().lower() == sn:
                    counterpart = from_name
            # Fallback : si on n'a pas identifie self, prend to_name par defaut
            if not counterpart:
                counterpart = to_name or from_name
            # iter90bz + iter90dw : cle SANS reference mais AVEC counterpart
            # => fusionne mutations partageant date+label+counterpart
            key = (
                "MUT-AGG",
                m.get("date", ""),
                m.get("account_number", "") or "",
                norm_label,
                counterpart.strip().lower(),
                m.get("journal_type", "") or "",
                m.get("third_party_id", "") or "",
            )
        else:
            # iter90bv : cle standard incluant reference (audit chronologique)
            key = (
                m.get("reference", "") or m.get("entry_id", "") or "",
                m.get("date", ""),
                m.get("account_number", "") or "",
                raw_desc,
                m.get("journal_type", "") or "",
            )
        if key not in buckets:
            b = dict(m)
            b["debit"] = float(m.get("debit", 0) or 0)
            b["credit"] = float(m.get("credit", 0) or 0)
            b["_norm_mutation"] = norm  # tuple ou None
            if norm:
                b["_counterpart"] = counterpart
            buckets[key] = b
            counts[key] = 1
            order.append(key)
        else:
            buckets[key]["debit"] += float(m.get("debit", 0) or 0)
            buckets[key]["credit"] += float(m.get("credit", 0) or 0)
            counts[key] += 1
    result = []
    for key in order:
        b = buckets[key]
        b["debit"] = round(b["debit"], 2)
        b["credit"] = round(b["credit"], 2)
        # iter90bz + iter90dw : reformater la description si N > 1
        # (les mutations uniques gardent leur description originale = plus precis)
        norm = b.pop("_norm_mutation", None)
        counterpart = b.pop("_counterpart", "")
        if norm and counts[key] > 1:
            norm_label, _from, _to = norm
            label_suffix = norm_label.removeprefix("Mutation lots - ")
            n = counts[key]
            cp = counterpart or _to or _from
            arrow = f" -> {cp}" if cp else ""
            b["description"] = f"Mutations ({n} lots) - {label_suffix}{arrow}"
            b["reference"] = f"MUT-AGG ({n})"
        result.append(b)
    return result


async def _build_situation_compte_pdf(db, owner_id, copropriete_id, start_date=None, end_date=None, group_by_owner: bool = True):
    """iter90au : helper reutilisable qui construit les bytes PDF de la situation
    de compte + le nom de fichier. Utilise par le download endpoint et par le
    communication router (/api/communication/send/situation).

    iter90bv : `group_by_owner=True` (defaut) => les lignes d'un meme proprietaire
    portant sur plusieurs lots sont fusionnees en une seule (vue resumee, adaptee
    aux proprietaires non-comptables).
    """
    from pdf_situation_compte import build_situation_compte_pdf
    from pdf_layout import resolve_syndic_pdf_context

    owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
    if not owner:
        raise HTTPException(404, "Proprietaire non trouve")
    copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
    if not copro:
        raise HTTPException(404, "Copropriete non trouvee")

    tier_acc = (owner.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
    acc_prov = tier_acc.get("provisions", "")
    acc_res = tier_acc.get("reserve", "")
    valid_accs = {a for a in (acc_prov, acc_res) if a}

    # iter90ci : EXCLURE les ecritures extournees (reversed=True) et les
    # contre-passations (is_reversal=True). Les paires s'annulent comptablement,
    # mais si on les affiche brutes, le proprietaire voit chaque appel 3 fois
    # (original + contre-passation + regeneration) avec des soldes incoherents.
    # Coherent avec le endpoint JSON situation_compte_owner qui utilise deja
    # _exclude_reversals.
    entries_q = {
        "copropriete_id": copropriete_id,
        "$or": [
            {"journal_type": {"$ne": "AN"}},
            {"journal_type": "AN", "is_opening_balance": True},
        ],
    }
    _exclude_reversals(entries_q)
    entries = await db.journal_entries.find(entries_q, {"_id": 0}).to_list(100000)

    opening = 0.0
    movements = []
    seen = set()

    def _date_in_range(d):
        if start_date and d < start_date:
            return "before"
        if end_date and d > end_date:
            return "after"
        return "in"

    for e in entries:
        for idx, ln in enumerate(e.get("lines", []) or []):
            acc = ln.get("account_number", "")
            tpid = ln.get("third_party_id")
            if acc not in valid_accs and tpid != owner_id:
                continue
            # iter90cx : cle basee sur (entry_id, line_index) au lieu de
            # (id, acc, debit, credit, tpid). Le tuple precedent dedupliquait
            # par erreur des lignes legitimes ayant meme debit/credit/tpid
            # (ex : 30 lots Matexi de meme quotite -> 3.6% perdu dans le PDF).
            key = (e.get("id"), idx)
            if key in seen:
                continue
            seen.add(key)
            d_val = float(ln.get("debit", 0) or 0)
            c_val = float(ln.get("credit", 0) or 0)
            date_str = e.get("date", "")
            position = _date_in_range(date_str)
            if position == "before":
                # iter90gw : convention POV proprio (extrait de compte) -> C - D
                # Un credit sur le compte proprio (paiement recu) augmente
                # le solde "en votre faveur", un debit (appel) le diminue.
                opening += c_val - d_val
            elif position == "in":
                line_desc = (ln.get("line_description") or "").strip()
                entry_desc = (e.get("description", "") or "").strip()
                movements.append({
                    "date": date_str,
                    "description": line_desc or entry_desc,
                    "reference": e.get("reference", "") or "",
                    "entry_id": e.get("id", "") or "",
                    "account_number": acc,
                    "account_name": ln.get("account_name", ""),
                    "debit": d_val,
                    "credit": c_val,
                    "journal_type": e.get("journal_type", ""),
                })

    all_bank_txns = await db.bank_transactions.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).to_list(100000)
    for txn in all_bank_txns:
        if txn.get("matched"):
            continue
        comm = (txn.get("communication", "") or "").replace("+", "").replace("/", "").replace(" ", "")
        if comm != owner.get("vcs_digits") and comm != owner.get("vcs_code"):
            continue
        date_str = txn.get("date", "")
        position = _date_in_range(date_str)
        if position == "before":
            # iter90gw : bank txn = paiement du proprio (credit) -> augmente
            # le solde "en votre faveur" en convention POV proprio.
            opening += abs(float(txn.get("amount", 0) or 0))
        elif position == "in":
            movements.append({
                "date": date_str,
                "description": f"Paiement non lettre: {txn.get('counterparty_name','') or comm}",
                "reference": txn.get("id", "")[:10],
                "account_number": acc_prov,
                "account_name": "Banque (a lettrer)",
                "debit": 0,
                "credit": abs(float(txn.get("amount", 0) or 0)),
                "journal_type": "BANK",
            })

    # iter90gw + iter90he : AN (reprise / solde reporte) TOUJOURS en tete,
    # quelle que soit sa date (avant la 1ere VE de la periode).
    movements.sort(key=lambda x: (
        0 if x.get("journal_type") == "AN" else 1,
        x["date"],
        x.get("reference", ""),
    ))

    # iter90bv : fusion des lignes d'un meme proprietaire pour tous ses lots
    # iter90dw : passe self_owner_name pour differencier les mutations par counterpart
    if group_by_owner:
        movements = _group_movements_by_owner(movements, self_owner_name=owner.get("name", ""))

    syndic_info = {
        "name": copro.get("syndic_name") or copro.get("name", "Syndic"),
        "address": copro.get("syndic_address", ""),
        "postal_code": copro.get("syndic_postal_code", ""),
        "city": copro.get("syndic_city", ""),
        "country": copro.get("syndic_country", "Belgique"),
        "email": copro.get("syndic_email", ""),
        "phone": copro.get("syndic_phone", ""),
        "bce": copro.get("syndic_bce", ""),
    }
    iban = ""
    bic = ""
    for ba in (copro.get("bank_accounts") or []):
        if ba.get("iban"):
            iban = ba["iban"]
            bic = ba.get("bic", "")
            break

    owner_view = {**owner, "account_provisions": acc_prov, "account_reserve": acc_res}

    syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)

    pdf_bytes = build_situation_compte_pdf(
        syndic_info=syndic_info,
        copropriete=copro,
        owner=owner_view,
        movements=movements,
        period_start=start_date or "",
        period_end=end_date or datetime.now(timezone.utc).date().isoformat(),
        opening_balance=opening,
        iban=iban,
        bic=bic,
        syndic_pdf_ctx=syndic_pdf_ctx,
    )
    safe_name = (owner.get("name", "owner") or "owner").replace(" ", "_").replace("/", "_")
    suffix = end_date or datetime.now(timezone.utc).date().isoformat()
    filename = f"situation-{safe_name}-{suffix}.pdf"
    return pdf_bytes, filename


async def _build_decompte_annuel_pdf(db, owner_id, copropriete_id, fiscal_year_id=None, preview=True):
    """iter90au : helper reutilisable qui construit les bytes PDF du decompte
    annuel + le nom de fichier. Utilise par le download endpoint et par le
    communication router. `preview=True` par defaut pour permettre l'envoi meme
    si l'exercice n'est pas cloture (le PDF sera alors marque "APERCU")."""
    from pdf_decompte import build_decompte_pdf
    from pdf_layout import resolve_syndic_pdf_context

    owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
    if not owner:
        raise HTTPException(404, "Proprietaire non trouve")
    if not copropriete_id or copropriete_id == "all":
        raise HTTPException(400, "copropriete_id requis - chinese walls strict")

    fy = None
    if fiscal_year_id:
        fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
            raise HTTPException(400, "Exercice appartient a une autre ACP")
    if not fy:
        today = datetime.now(timezone.utc)
        fy = {
            "name": f"Exercice {today.year}",
            "start_date": f"{today.year}-01-01",
            "end_date": f"{today.year}-12-31",
        }

    copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
    if not copro:
        raise HTTPException(404, "Copropriete non trouvee")

    owner_lots = await db.lots.find(
        {"copropriete_id": copropriete_id,
         "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
        {"_id": 0}
    ).to_list(100)
    # iter90g7 : inclut aussi les lots impliques dans une mutation intra-FY
    # (via db.mutations), meme si lot.owner_id est bloque sur l'ancien
    # proprietaire (cas bug TEUWEN/MATEXI legacy).
    muts_for_owner_h = await db.mutations.find(
        {"copropriete_id": copropriete_id,
         "$or": [{"from_owner_id": owner_id}, {"to_owner_id": owner_id}],
         "sale_date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
        {"_id": 0, "lot_id": 1},
    ).to_list(1000)
    existing_lot_ids_h = {l["id"] for l in owner_lots}
    extra_lot_ids_h = {
        m["lot_id"] for m in muts_for_owner_h
        if m.get("lot_id") and m["lot_id"] not in existing_lot_ids_h
    }
    if extra_lot_ids_h:
        extra_lots_h = await db.lots.find(
            {"id": {"$in": list(extra_lot_ids_h)},
             "copropriete_id": copropriete_id},
            {"_id": 0}
        ).to_list(100)
        owner_lots.extend(extra_lots_h)
    all_lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(1000)

    invoices = await db.invoices.find(
        {"copropriete_id": copropriete_id,
         "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
        {"_id": 0}
    ).sort("date", 1).to_list(10000)

    distribution_keys = await db.distribution_keys.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).to_list(1000)

    # iter90g5 : bug fix - mutations DOIVENT etre passees a build_decompte_pdf
    # pour que le prorata (days_owned/days_total) soit applique. Sans cela,
    # un proprietaire qui achete en cours d'exercice paie 100% des charges
    # au lieu de sa quote-part au prorata. Ce chemin (envoi email groupe
    # via communication, decompte_annuel_pdf helper) etait casse.
    owner_lot_ids_helper = [l["id"] for l in owner_lots]
    mutations_docs = await db.mutations.find(
        {"copropriete_id": copropriete_id,
         "lot_id": {"$in": owner_lot_ids_helper},
         "sale_date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
        {"_id": 0},
    ).to_list(1000) if owner_lot_ids_helper else []

    # iter90g6 : ecritures OD lot_mutation touchant le compte tier de
    # l'owner (MUT-R fonds de roulement, MUT-P prorata, MUT-F futurs).
    # Impactent le solde comptable definitif du proprietaire (dettes
    # reprises ou cedees lors du transfert de propriete).
    mutation_entries_docs = await db.journal_entries.find(
        {"copropriete_id": copropriete_id,
         "source_type": "lot_mutation",
         "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])},
         "reversed": {"$ne": True},
         "is_reversal": {"$ne": True},
         "lines.third_party_id": owner_id},
        {"_id": 0},
    ).to_list(10000)

    # iter90i9 : grand livre canonique du proprio - TOUTES les JE (non
    # contre-passees) qui touchent son compte tier ou dont une ligne porte
    # `third_party_id = owner_id`. Utilise pour recalculer total_called et
    # total_payments (meme source que la Situation de compte) et garantir
    # que Decompte et Situation restent alignes meme apres une mutation
    # mid-year.
    _tier_vals_i9 = [
        v for v in ((owner.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).values()
        if isinstance(v, str) and v
    ]
    owner_ledger_query = {
        "copropriete_id": copropriete_id,
        "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])},
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
    }
    _or_clauses = [{"lines.third_party_id": owner_id}]
    if _tier_vals_i9:
        _or_clauses.append({"lines.account_number": {"$in": _tier_vals_i9}})
    owner_ledger_query["$or"] = _or_clauses
    owner_ledger_entries = await db.journal_entries.find(
        owner_ledger_query, {"_id": 0},
    ).to_list(100000)

    fund_calls = await db.fund_calls.find(
        {"copropriete_id": copropriete_id,
         "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
        {"_id": 0}
    ).sort("date", 1).to_list(10000)

    owner_vcs_digits = owner.get("vcs_digits", "")
    all_txns = await db.bank_transactions.find(
        {"copropriete_id": copropriete_id,
         "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
        {"_id": 0}
    ).sort("date", 1).to_list(100000)
    payments = []
    for t in all_txns:
        is_owner = False
        if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
            is_owner = True
        elif not t.get("matched") and owner_vcs_digits:
            comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
            if comm == owner_vcs_digits:
                is_owner = True
        if is_owner:
            payments.append(t)

    cats = await db.expense_categories.find(
        {"copropriete_id": copro["id"]} if copro.get("id") else {}, {"_id": 0}
    ).to_list(1000)
    pcmn_acc = await db.pcmn_accounts.find(
        {"class_num": 6, "copropriete_id": copro.get("id", "")}, {"_id": 0}
    ).to_list(1000)
    nature_map = {a["number"]: a.get("name", "") for a in pcmn_acc}
    for c in cats:
        nature_map[c["account_number"]] = c["name"]

    # iter90ie : charger les compteurs + releves de l'ACP pour que les cles
    # de type "meter" soient resolues dynamiquement lors du calcul du decompte.
    meters_ = await db.meters.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).to_list(1000)
    meter_ids = [m["id"] for m in meters_]
    meter_readings_ = []
    if meter_ids:
        meter_readings_ = await db.meter_readings.find(
            {"meter_id": {"$in": meter_ids}}, {"_id": 0}
        ).to_list(100000)

    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=owner_lots, all_lots=all_lots,
        invoices=invoices, distribution_keys=distribution_keys,
        fund_calls=fund_calls, payments=payments,
        expense_accounts_map=nature_map,
        preview=preview,
        syndic_pdf_ctx=await resolve_syndic_pdf_context(db, copro),
        mutations=mutations_docs,  # iter90g5 : prorata mutation
        mutation_entries=mutation_entries_docs,  # iter90g6 : OD lot_mutation
        owner_ledger_entries=owner_ledger_entries,  # iter90i9 : align Situation<->Decompte
        meters=meters_,  # iter90ie : cles de type meter
        meter_readings=meter_readings_,
    )
    safe_name = (owner.get("name", "owner") or "owner").replace(" ", "_").replace("/", "_")
    fy_name = (fy.get("name", "") or "").replace(" ", "_")
    filename = f"decompte_{safe_name}_{fy_name}.pdf"
    return pdf_bytes, filename


async def _compute_balance_tiers_for_ui(db, copropriete_id):
    """iter90au : version legere de balance_tiers_owners destinee a l'UI de
    selection des proprietaires (module communication). Retourne pour chaque
    proprietaire de l'ACP : {owner_id, owner_name, email, phone, balance, status,
    account_provisions, account_reserve}. Le solde est CUMULATIF (toutes dates,
    hors extournes)."""
    if not copropriete_id or copropriete_id == "all":
        return {"owners": [], "total_debiteurs": 0.0, "total_crediteurs": 0.0}

    lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
    owner_ids = set(lt.get("owner_id") for lt in lots if lt.get("owner_id"))
    # Inclure aussi les co-proprietaires (owner_ids pluriel sur les lots)
    for lt in lots:
        for oid in (lt.get("owner_ids") or []):
            if oid:
                owner_ids.add(oid)

    # Proprietaires avec tier_accounts configures pour cette ACP
    tier_candidates = await db.owners.find(
        {f"tier_accounts.{copropriete_id}": {"$exists": True}},
        {"_id": 0, "id": 1},
    ).to_list(10000)
    for c in tier_candidates:
        owner_ids.add(c["id"])

    # Anciens proprietaires via mutations
    muts = await db.mutations.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "from_owner_id": 1, "to_owner_id": 1},
    ).to_list(10000)
    for m in muts:
        if m.get("from_owner_id"):
            owner_ids.add(m["from_owner_id"])
        if m.get("to_owner_id"):
            owner_ids.add(m["to_owner_id"])

    third_party_ids_in_je = await db.journal_entries.distinct(
        "lines.third_party_id", {"copropriete_id": copropriete_id}
    )
    for tpid in third_party_ids_in_je:
        if tpid:
            owner_ids.add(tpid)
    owner_ids = list(owner_ids)
    owners = (
        await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).sort("name", 1).to_list(1000)
        if owner_ids else []
    )
    current_owner_ids = set(lt.get("owner_id") for lt in lots if lt.get("owner_id"))
    for lt in lots:
        for oid in (lt.get("owner_ids") or []):
            if oid:
                current_owner_ids.add(oid)

    # Cumul entries (toutes dates, hors extournes)
    # iter90ia : appliquer le MEME filtre AN que la Situation de compte et
    # le Bilan : inclure UNIQUEMENT les AN avec is_opening_balance=True
    # (vraies reprises comptables) et EXCLURE les AN de cloture (report
    # de solde en double, cree lors du closing de l'exercice N-1).
    je_q = {
        "copropriete_id": copropriete_id,
        "$or": [
            {"journal_type": {"$ne": "AN"}},
            {"journal_type": "AN", "is_opening_balance": True},
        ],
        # Alignement bilan : exclure regularisations et OD-REG/EXT
        "$and": [
            {"is_regularization": {"$ne": True}},
            {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
        ],
    }
    _exclude_reversals(je_q)
    entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(200000)

    # Aggregation : par owner_id via third_party_id + fallback par compte tier
    cumul_by_owner = {}  # oid -> debit - credit
    cumul_by_acc = {}   # account_number -> debit - credit
    for e in entries:
        for ln in e.get("lines", []) or []:
            d_val = float(ln.get("debit", 0) or 0)
            c_val = float(ln.get("credit", 0) or 0)
            tpid = ln.get("third_party_id")
            acc = ln.get("account_number", "")
            if tpid:
                cumul_by_owner[tpid] = cumul_by_owner.get(tpid, 0.0) + (d_val - c_val)
            else:
                cumul_by_acc[acc] = cumul_by_acc.get(acc, 0.0) + (d_val - c_val)

    # Bank txns non lettres reconnus par VCS -> credit additionnel
    vcs_to_owner = {}
    for owner in owners:
        if owner.get("vcs_digits"):
            vcs_to_owner[owner["vcs_digits"]] = owner["id"]
        if owner.get("vcs_code"):
            vcs_to_owner[owner["vcs_code"]] = owner["id"]
    all_bank_txns = await db.bank_transactions.find(
        {"copropriete_id": copropriete_id, "matched": {"$ne": True}}, {"_id": 0}
    ).to_list(100000)
    for txn in all_bank_txns:
        comm = (txn.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
        oid = vcs_to_owner.get(comm) or vcs_to_owner.get(txn.get("communication") or "")
        if oid:
            cumul_by_owner[oid] = cumul_by_owner.get(oid, 0.0) - abs(float(txn.get("amount", 0) or 0))

    result = []
    for owner in owners:
        oid = owner["id"]
        tier_acc = (owner.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
        acc_prov = tier_acc.get("provisions", "")
        acc_res = tier_acc.get("reserve", "")
        balance = cumul_by_owner.get(oid, 0.0)
        # Fallback : lignes sur les comptes de l'owner sans third_party_id
        for acc in (acc_prov, acc_res):
            if acc and acc in cumul_by_acc:
                balance += cumul_by_acc[acc]
                cumul_by_acc[acc] = 0.0
        balance = round(balance, 2)
        result.append({
            "owner_id": oid,
            "owner_name": owner.get("name", ""),
            "email": owner.get("email", ""),
            "phone": owner.get("phone", ""),
            "account_provisions": acc_prov,
            "account_reserve": acc_res,
            "balance": balance,
            "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
            "is_former_owner": oid not in current_owner_ids,
        })
    # Sort by name
    result.sort(key=lambda x: (x["owner_name"] or "").lower())
    total_debiteurs = round(sum(r["balance"] for r in result if r["balance"] > 0), 2)
    total_crediteurs = round(sum(abs(r["balance"]) for r in result if r["balance"] < 0), 2)
    return {"owners": result, "total_debiteurs": total_debiteurs, "total_crediteurs": total_crediteurs}


# ---- iter90jl : Helper module-level pour deduplication FI auto vs importe ----
def _fi_signature(je: dict, tier_prefixes=("440", "4100", "4101", "400")) -> tuple:
    """iter90jl : Signature canonique d'un JE FI pour detection de doublon
    (memes regles que scripts/cleanup_duplicate_auto_fi.py).

    (copropriete_id, date, montant_arrondi, frozenset_comptes_tier)
    """
    tier_accs = []
    for ln in je.get("lines", []):
        acc = (ln.get("account_number") or "").strip()
        if any(acc.startswith(p) for p in tier_prefixes):
            tier_accs.append(acc)
    total = round(float(je.get("total_debit") or je.get("total_credit") or 0), 2)
    return (
        (je.get("copropriete_id") or "").strip(),
        (je.get("date") or "").strip(),
        total,
        frozenset(tier_accs),
    )


def _dedup_duplicate_auto_fi_entries(entries: list) -> list:
    """iter90jl : filtre defensif read-time des JEs FI auto qui doublonnent
    un JE FI importe ou manuel (meme date, montant, comptes tier). Priorite :
    importe > manuel > auto. Les FI auto en doublon sont EXCLUS du calcul.

    Ne modifie PAS la DB. Purement read-only. Le script
    `cleanup_duplicate_auto_fi.py` reste le fix DEFINITIF pour retirer les
    doublons proprement (contre-passation + coherence cross-modules).

    Retourne la liste filtree (meme ordre pour les entrees conservees).
    """
    from collections import defaultdict
    fi_entries = [e for e in entries if e.get("journal_type") == "FI"]
    non_fi = [e for e in entries if e.get("journal_type") != "FI"]
    # Groupe les FI par signature
    by_sig: dict = defaultdict(list)
    for je in fi_entries:
        sig = _fi_signature(je)
        if not sig[3]:  # pas de compte tier -> hors scope
            continue
        by_sig[sig].append(je)
    # Detecte les JE auto a exclure (doublons d'un importe/manuel)
    to_exclude_ids: set = set()
    for sig, group in by_sig.items():
        if len(group) < 2:
            continue
        # Y a-t-il un keeper importe ou manuel ?
        keepers = [j for j in group if j.get("import_session_id") or not j.get("auto_generated")]
        if not keepers:
            continue
        # Exclut les auto en doublon
        for j in group:
            if j.get("auto_generated") and j.get("id") not in {k.get("id") for k in keepers}:
                to_exclude_ids.add(j.get("id"))
    if not to_exclude_ids:
        return entries
    # Preserve l'ordre original
    return [e for e in entries if e.get("id") not in to_exclude_ids]


async def compute_bilan_data(db, copropriete_id: str, date_to: Optional[str] = None,
                             fiscal_year_id: Optional[str] = None,
                             view_mode: str = "before_distribution") -> dict:
    """Calcul du Bilan PCMN belge (Actif / Passif par rubriques). Chinese walls strict.

    iter90fq : extrait de l'endpoint `/reports/bilan` (module-level, importable)
    pour etre reutilise a la fois par le router FastAPI ET par `backup_service.py`
    (generation des PDF de backup) - evite la duplication de la logique metier
    de repartition du boni/mali qui a deja cause des bugs par le passe (cf.
    iter90fl "lignes fantomes" : divergence entre 2 implementations du meme calcul).

    view_mode :
      - 'before_distribution' (defaut) : le boni/mali apparait sur le compte 499.
      - 'after_distribution' : le 499 est reparti sur les comptes 4000XX des proprietaires
        (par quotites globales + cles speciales eventuelles par compte).
    """
    # Optionally resolve fiscal year
    fy = None
    if fiscal_year_id:
        fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
            raise HTTPException(400, "Cet exercice appartient a une autre ACP.")
        if fy and not date_to:
            date_to = fy["end_date"]

    # Normaliser date_to pour inclure TOUTE la journee (23:59:59).
    # Sans ce suffix, les ecritures stockees avec un composant horaire
    # (ex: "2026-12-31T12:00:00") sont exclues car la comparaison de
    # chaines "2026-12-31T..." > "2026-12-31" est True -> $lte echoue.
    date_to_lte = _date_lte(date_to) if date_to else None

    q = _apply_copro({}, copropriete_id)
    if date_to_lte:
        q["date"] = {"$lte": date_to_lte}
    # iter90gk : exclure les AN de CLOTURE (duplicats cumulatifs de N-1)
    # mais INCLURE les AN d'OUVERTURE (wizard) marquees `is_opening_balance=True`.
    # Sans cette inclusion, un ACP frais importe via le wizard perd ses
    # ouvertures : dettes fournisseurs 1132.92 (AG) + 639.69 (SRL Finlead)
    # non visibles au Bilan alors que la Balance des Tiers les affichait
    # (bug rapporte par l'utilisateur : "le bilan est different de la
    # situation de compte, ce n'est pas normal").
    q["$or"] = [
        {"journal_type": {"$ne": "AN"}},
        {"journal_type": "AN", "is_opening_balance": True},
    ]
    # SECURISATION : toujours exclure les ecritures EXTOURNEES (reversed=True)
    # et leurs CONTRE-PASSATIONS (is_reversal=True). Ces paires s'annulent au bilan
    # mais leur presence introduit du bruit + des doublons quand la cloture a ete
    # relancee apres une reouverture.
    _exclude_reversals(q)
    if view_mode != "after_distribution":
        # Exclure les ecritures de regularisation/cloture :
        # - flag is_regularization=True (OD nouvelles)
        # - prefixes references : OD-REG-, EXT- (extourne provisions cloture)
        q["$and"] = [
            {"is_regularization": {"$ne": True}},
            {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
        ]

    entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)

    # ---- iter90jl : DEDUP DEFENSIF read-time des JEs FI en doublon ----
    # Le user peut avoir des FI auto-generees ET des FI importees pour un meme
    # paiement (data legacy pre-iter90ji). Ces doublons gonflent le solde
    # bancaire (compte 55XXXX) et faussent le Bilan. On applique ici le meme
    # algorithme que `scripts/cleanup_duplicate_auto_fi.py` mais AU READ pour
    # que le Bilan reflete la realite meme sans avoir relance le cleanup.
    # Priorite du keeper : importe > manuel > auto.
    # Note : ce filtre est purement defensif. Le script `cleanup_duplicate_auto_fi`
    # reste le fix DEFINITIF (contre-passation propre + coherence avec Balance
    # des Tiers et Journaux).
    entries = _dedup_duplicate_auto_fi_entries(entries)

    # ---- DEDUP AC orphelines (doublons d'import Optipro) ----
    # Chaque facture (`invoices`) pointe vers UNE ecriture AC via
    # `journal_entry_id`.  Toute ecriture AC non referencee par une
    # facture est un doublon orphelin (import Optipro + saisie manuelle
    # pour la meme charge).  On l'exclut du bilan pour eviter le sur-
    # comptage des charges (cl. 6) ET des dettes fournisseurs (cl. 440).
    # NOTE : le filtre ne s'applique QUE si des factures avec journal_entry_id
    # existent, pour ne pas casser les scenarii de test ou les ACPs legacy
    # sans factures materialisees.
    inv_q_bilan = {"copropriete_id": copropriete_id}
    if date_to_lte:
        inv_q_bilan["date"] = {"$lte": date_to_lte}
    invoices_for_bilan = await db.invoices.find(
        inv_q_bilan, {"_id": 0, "journal_entry_id": 1}
    ).to_list(50000)
    valid_ac_je_ids = {
        inv["journal_entry_id"]
        for inv in invoices_for_bilan
        if inv.get("journal_entry_id")
    }
    if valid_ac_je_ids:
        entries = [
            e for e in entries
            if e.get("journal_type") != "AC" or e.get("id") in valid_ac_je_ids
        ]

    # Compute net balance per account (classes 1-5 only)
    balances = {}
    for entry in entries:
        for line in entry.get("lines", []):
            acc = line["account_number"]
            if not acc or acc[0] not in ("1", "2", "3", "4", "5"):
                continue
            if acc not in balances:
                balances[acc] = {
                    "account_number": acc,
                    "account_name": line.get("account_name", ""),
                    "debit": 0.0, "credit": 0.0,
                }
            balances[acc]["debit"] += line.get("debit", 0)
            balances[acc]["credit"] += line.get("credit", 0)

    # ---- FUSION comptes 400/401 du meme proprietaire ----
    # Le syndic veut voir UNE seule ligne par proprietaire (nom + solde total)
    # au lieu de "Prov. charges - Dubois" + "Fonds reserve - Dubois" separes.
    owners_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
    owner_acc_map = {}  # acc_number -> {owner_id, owner_name}
    owner_primary_acc = {}  # owner_id -> account number to DISPLAY on the bilan line
    for o in owners_acp:
        tier_accs = ((o.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
        for key in ("provisions", "reserve"):
            acc_n = tier_accs.get(key)
            if acc_n:
                owner_acc_map[acc_n] = {
                    "owner_id": o["id"], "owner_name": o.get("name", "")}
        # Le n° de compte affiche sur la ligne bilan = compte "provisions"
        # (fonds de roulement = 41010XXX ou legacy 40000XXX), sinon reserve.
        display_acc = tier_accs.get("provisions") or tier_accs.get("reserve") or ""
        if display_acc:
            owner_primary_acc[o["id"]] = display_acc

    # Aggreger par owner_id
    merged = {}  # owner_id -> {debit, credit, name}
    keep_balances = {}  # comptes non-owners
    for acc, b in balances.items():
        if acc in owner_acc_map:
            oid = owner_acc_map[acc]["owner_id"]
            onm = owner_acc_map[acc]["owner_name"]
            m = merged.setdefault(oid, {
                "owner_id": oid, "owner_name": onm,
                "debit": 0.0, "credit": 0.0,
            })
            m["debit"] += b["debit"]
            m["credit"] += b["credit"]
        else:
            keep_balances[acc] = b
    # Remplacer balances : 1 entree synthetique par owner + comptes restants
    balances = keep_balances
    for oid, m in merged.items():
        virt_acc = f"OWNER_{oid}"
        balances[virt_acc] = {
            "account_number": virt_acc,
            "account_name": m["owner_name"],
            "debit": m["debit"],
            "credit": m["credit"],
            "is_owner_aggregated": True,
            # iter90v : compte comptable a afficher sur la ligne bilan
            "display_account": owner_primary_acc.get(oid, ""),
        }

    def _rub(label, accounts):
        return {
            "label": label,
            "total": round(sum(a["amount"] for a in accounts), 2),
            "accounts": [a for a in accounts if a["amount"] > 0.01],
        }

    # Classify into Belgian PCMN rubriques (default: net debit -> actif, net credit -> passif)
    actif_buckets = {
        "I_immo_incorporelles": [],
        "II_immo_corporelles": [],
        "III_immo_financieres": [],
        "IV_stocks": [],
        "V_creances_coproprietaires": [],
        "V_creances_fournisseurs_acompte": [],
        "V_creances_autres": [],
        "VI_placements": [],
        "VII_disponibilites": [],
        "VIII_regul_actif": [],
    }
    passif_buckets = {
        "I_capital": [],
        "II_reserves": [],
        "III_resultat_reporte": [],
        "IV_subsides": [],
        "V_dettes_long": [],
        "VI_dettes_coproprietaires": [],
        "VI_dettes_fournisseurs": [],
        "VI_dettes_autres": [],
        "VII_regul_passif": [],
    }

    def _clean_account_name(acc, name):
        """Nettoie les libelles redondants : 'Fourn. - X' -> 'X' quand le contexte
        (440xxx) suffit a identifier qu'on parle d'un fournisseur."""
        if name and acc and acc.startswith("440"):
            if name.startswith("Fourn. - "):
                return name[len("Fourn. - "):]
            if name.startswith("Fourn.- "):
                return name[len("Fourn.- "):]
            if name.startswith("Fourn. "):
                return name[len("Fourn. "):]
        return name

    def _classify_account(acc, solde, balances_dict):
        """Classe un compte dans le bon bucket selon son numero et son solde."""
        is_aggregated_owner = balances_dict[acc].get("is_owner_aggregated", False)
        # Pour les comptes agreges proprietaires : afficher le compte
        # "principal" (fonds de roulement = 41010XXX ou legacy 40000XXX)
        # avec le nom, plutot que le numero virtuel OWNER_xxx.
        display_acc = balances_dict[acc].get("display_account", "") if is_aggregated_owner else acc
        item = {
            "account_number": display_acc if is_aggregated_owner else acc,
            "account_name": _clean_account_name(acc, balances_dict[acc]["account_name"]),
            "amount": abs(solde),
        }
        if solde > 0.01:
            # ACTIF (solde debiteur)
            if is_aggregated_owner:
                actif_buckets["V_creances_coproprietaires"].append(item)
            elif acc.startswith(("20", "21")):
                actif_buckets["I_immo_incorporelles"].append(item)
            elif acc.startswith(("22", "23", "24", "25", "26", "27")):
                actif_buckets["II_immo_corporelles"].append(item)
            elif acc.startswith("28"):
                actif_buckets["III_immo_financieres"].append(item)
            elif acc.startswith("3"):
                actif_buckets["IV_stocks"].append(item)
            elif acc.startswith(("400", "401", "410", "411", "416")):
                # Coproprietaires debiteurs : PCMN belge (410x) + legacy (400/401) + doutes (416)
                actif_buckets["V_creances_coproprietaires"].append(item)
            elif acc.startswith("440"):
                actif_buckets["V_creances_fournisseurs_acompte"].append(item)
            elif acc.startswith("4") and not acc.startswith("49"):
                actif_buckets["V_creances_autres"].append(item)
            elif acc.startswith(("50", "51", "52", "53")):
                actif_buckets["VI_placements"].append(item)
            elif acc.startswith(("54", "55", "57", "58")):
                actif_buckets["VII_disponibilites"].append(item)
            elif acc.startswith("49"):
                actif_buckets["VIII_regul_actif"].append(item)
            else:
                actif_buckets["V_creances_autres"].append(item)
        elif solde < -0.01:
            # PASSIF (solde crediteur)
            if is_aggregated_owner:
                passif_buckets["VI_dettes_coproprietaires"].append(item)
            elif acc.startswith("10"):
                passif_buckets["I_capital"].append(item)
            elif acc.startswith("13") or acc.startswith("16"):
                passif_buckets["II_reserves"].append(item)
            elif acc.startswith("14"):
                passif_buckets["III_resultat_reporte"].append(item)
            elif acc.startswith("15"):
                passif_buckets["IV_subsides"].append(item)
            elif acc.startswith("17"):
                passif_buckets["V_dettes_long"].append(item)
            elif acc.startswith(("400", "401", "410", "411")):
                # Coproprietaires crediteurs (excedents)
                passif_buckets["VI_dettes_coproprietaires"].append(item)
            elif acc.startswith("440"):
                passif_buckets["VI_dettes_fournisseurs"].append(item)
            elif acc.startswith(("44", "45", "46", "48")):
                passif_buckets["VI_dettes_autres"].append(item)
            elif acc.startswith("49"):
                passif_buckets["VII_regul_passif"].append(item)
            else:
                passif_buckets["VI_dettes_autres"].append(item)

    for acc, b in balances.items():
        solde = round(b["debit"] - b["credit"], 2)
        _classify_account(acc, solde, balances)

    # ---- Calcul du compte 499 (regle comptable belge copropriete) ----
    # Regle du syndic :
    #   499 = Appels (cl.70) - (Depenses nettes - Produits financiers (cl.75))
    # Les interets crediteurs (cl.75) viennent en DEDUCTION des charges.
    # Les "depenses nettes" proviennent du moteur expense_rows (meme base que
    # la page "Liste des depenses") pour garantir la coherence entre les rapports.
    total_charges = 0.0
    private_charges_643 = 0.0
    provisions_appelees = 0.0
    produits_hors_provisions = 0.0
    for entry in entries:
        for line in entry.get("lines", []):
            acc = line.get("account_number", "")
            if not acc:
                continue
            if acc.startswith("6"):
                charge_net = line.get("debit", 0) - line.get("credit", 0)
                total_charges += charge_net
                # Frais privatifs (643) : factures individuellement aux
                # proprietaires, ne doivent PAS impacter la regularisation
                # des charges communes (boni/mali compte 499).
                if acc.startswith("643"):
                    private_charges_643 += charge_net
            elif acc.startswith("70"):
                provisions_appelees += line.get("credit", 0) - line.get("debit", 0)
            elif acc.startswith("7"):
                produits_hors_provisions += line.get("credit", 0) - line.get("debit", 0)
    # Equilibre comptable : le compte 499 = budget - charges communes
    # Les frais privatifs (643) sont exclus car factures individuellement.
    common_charges = round(total_charges - private_charges_643, 2)
    result_exercise = round(provisions_appelees + produits_hors_provisions - common_charges, 2)
    # Compte 499 = result_exercise (equilibre Actif = Passif garanti)
    compte_499 = result_exercise

    # ---- Mode "apres repartition" : repartition du boni/mali sur owners ----
    # Formule garantissant l'equilibre du bilan :
    #   delta[i] = result_exercise * (quotite[i] / total_quotites)
    # (= meme formule que Option B, mais documentee)
    # NOTE : la formule "Option A" stricte (appels_recus[i] - charges_imputees[i])
    # necessite que les appels soient inscrits sur les comptes 4000XX en double-entree,
    # ce qui n'est pas le cas dans le modele actuel. A migrer dans une iteration ulterieure
    # avec materialisation OD permanente a la cloture d'exercice.
    distributed_per_owner = {}
    # iter90fs : totaux reellement distribues (peut differer de
    # `result_exercise` si un lot n'a pas de proprietaire connu, ou si le
    # proprietaire refere par owner_id/owner_ids n'existe plus dans la
    # collection `owners`). Toute part "perdue" est renvoyee sur le
    # compte 499 en fin de traitement (safety net strict Actif = Passif).
    actual_boni_distributed = 0.0
    if view_mode == "after_distribution" and abs(compte_499) > 0.01:
        lots_for_acp = await db.lots.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(10000)
        owners_for_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
        owners_by_id = {o["id"]: o for o in owners_for_acp if o.get("id")}
        # iter90ft : fallback quotites via la default distribution_key.
        # Sur certaines ACPs (import legacy, ACP Acacia TER, etc.), le
        # champ `quotity` n'est PAS peuple directement sur le document lot.
        # Les quotites vivent alors dans la "cle de repartition generale"
        # (`distribution_keys` avec `is_default=True`), sous la forme
        # `[{lot_id, share, excluded}, ...]`. Meme approche que
        # `_lot_share` deja utilise par le decompte annuel (iter90ej) et
        # `owner_portal` (iter90cz). Sans ce fallback, `total_quotities`
        # reste a 0 -> repartition du boni impossible -> le safety net
        # iter90fs reaffichait le 499 avec "quotites manquantes" et le
        # boni n'etait pas reparti sur les proprietaires (bug rapporte
        # par l'utilisateur sur ACP Acacia TER apres iter90fs).
        default_key = await db.distribution_keys.find_one(
            {"copropriete_id": copropriete_id, "is_default": True},
            {"_id": 0},
        )
        lot_share_map = {}
        if default_key:
            for kl in default_key.get("lots", []) or []:
                if kl.get("excluded"):
                    continue
                lot_id = kl.get("lot_id")
                if lot_id:
                    lot_share_map[lot_id] = float(kl.get("share", 0) or 0)

        def _lot_quotity(lot):
            """Retourne la quotite du lot avec fallback sur la cle generale.

            Ordre de priorite (memes regles que iter90ej/iter90cz) :
            1. `lot.quotity` (champ direct sur le document lot) - source
               historique, prioritaire.
            2. `default_key.lots[lot_id].share` - fallback pour les ACPs
               dont les quotites vivent uniquement dans la cle generale.
            """
            q = float(lot.get("quotity", 0) or 0)
            if q > 0:
                return q
            return lot_share_map.get(lot.get("id"), 0.0)

        owner_quotities = {}
        total_quotities = 0.0
        for lot in lots_for_acp:
            quo = _lot_quotity(lot)
            if quo <= 0:
                continue
            # BUG FIX (iter90fr) : certains lots (notamment en co-propriete/
            # co-indivision) n'ont pas `owner_id` (singulier) rempli et
            # utilisent uniquement `owner_ids` (liste) - meme convention que
            # `duplicates.py`/`banking.py` ($or owner_id/owner_ids). Ignorer
            # `owner_ids` faisait chuter `total_quotities` a une valeur trop
            # faible (voire 0), empechant la repartition du boni/mali sur
            # CES proprietaires -> bilan "apres repartition" desequilibre
            # (le compte 499 est retire de l'affichage mais son montant
            # n'est jamais recredite a personne).
            oids = [o for o in (lot.get("owner_ids") or []) if o]
            if not oids and lot.get("owner_id"):
                oids = [lot["owner_id"]]
            if not oids:
                continue
            # Pas de cle de repartition fractionnelle par co-proprietaire
            # stockee -> partage egal de la quotite du lot entre les
            # co-proprietaires (meme heuristique que la notification
            # groupee des lots en co-indivision ailleurs dans l'app).
            quo_per_owner = quo / len(oids)
            for oid in oids:
                owner_quotities[oid] = owner_quotities.get(oid, 0.0) + quo_per_owner
                total_quotities += quo_per_owner
        if total_quotities > 0:
            # Repartition proportionnelle a la quotite, avec correction
            # d'arrondi sur le dernier owner pour que la somme distribuee
            # egale EXACTEMENT `compte_499`.
            owner_items = list(owner_quotities.items())
            running_sum = 0.0
            for idx, (oid, quo) in enumerate(owner_items):
                is_last = (idx == len(owner_items) - 1)
                if is_last:
                    share = round(compte_499 - running_sum, 2)
                else:
                    share = round(compte_499 * (quo / total_quotities), 2)
                    running_sum += share
                distributed_per_owner[oid] = share

        for oid, delta in distributed_per_owner.items():
            virt_acc = f"OWNER_{oid}"
            if virt_acc in balances:
                if delta > 0:
                    balances[virt_acc]["credit"] += delta
                else:
                    balances[virt_acc]["debit"] += abs(delta)
                actual_boni_distributed += delta
            else:
                owner_doc = owners_by_id.get(oid)
                if not owner_doc:
                    # Proprietaire refere par un lot mais absent de la
                    # collection `owners` (donnees corrompues / owner
                    # supprime). Ne PAS silencieusement perdre sa quote-
                    # part : la difference sera recreditee sur 499 par la
                    # safety net finale (voir plus bas).
                    continue
                balances[virt_acc] = {
                    "account_number": virt_acc,
                    "account_name": owner_doc.get("name", ""),
                    "debit": abs(delta) if delta < 0 else 0.0,
                    "credit": delta if delta > 0 else 0.0,
                    "is_owner_aggregated": True,
                    # iter90fs : sans cette ligne, la nouvelle entree
                    # owner (proprietaire sans activite prealable) etait
                    # affichee sans numero de compte dans le bilan.
                    "display_account": owner_primary_acc.get(oid, ""),
                }
                actual_boni_distributed += delta

        # ---- Repartition des comptes de regularisation 49X sur les proprietaires ----
        # Regle metier (PCMN copro) : en consultation "Apres repartition", les comptes
        # de regularisation (490-498, hors 499 synthetique) sont consideres comme
        # appartenant collectivement aux proprietaires. On les repartit par quotite.
        #
        # PRINCIPE D'EQUILIBRE : un compte 49X conserve sa NATURE (actif/passif) en
        # passant sur les comptes proprietaires. C'est juste un changement de rubrique
        # de presentation, pas une re-affectation comptable.
        #   - Compte 49X ACTIF (solde debiteur, ex. 490 charges a reporter)
        #       -> ajoute au DEBIT des proprietaires (reste cote ACTIF, rubrique V.A)
        #   - Compte 49X PASSIF (solde crediteur, ex. 493 produits a reporter)
        #       -> ajoute au CREDIT des proprietaires (reste cote PASSIF, rubrique VI.A)
        # Cela preserve mathematiquement l'equilibre : on deplace simplement le
        # montant d'une rubrique a l'autre du MEME cote du bilan.
        regul_actif_by_account = {}  # acc -> solde positif (actif)
        regul_passif_by_account = {}  # acc -> abs(solde negatif) (passif)
        for acc, b in balances.items():
            if not acc.startswith("49") or acc == "499":
                continue
            solde = round(b["debit"] - b["credit"], 2)
            if abs(solde) < 0.01:
                continue
            if solde > 0:
                regul_actif_by_account[acc] = solde
            else:
                regul_passif_by_account[acc] = abs(solde)

        # iter90fs : totaux reellement distribues pour les comptes 49X (regul
        # actif/passif hors 499). Analogue a `actual_boni_distributed` : si
        # un owner n'a pas pu recevoir sa part (owner_id oriente vers une
        # fiche disparue), on ne peut PAS neutraliser aveuglement le 49X
        # d'origine sans casser l'equilibre. On track les montants
        # effectivement distribues et on n'invalidera que ceux-la.
        actual_regul_actif_distributed = 0.0
        actual_regul_passif_distributed = 0.0
        if (regul_actif_by_account or regul_passif_by_account) and total_quotities > 0:
            total_actif_regul = sum(regul_actif_by_account.values())
            total_passif_regul = sum(regul_passif_by_account.values())
            # Repartition par quotite avec gestion d'arrondi : on calcule la somme
            # distribuee et on l'ajuste sur le dernier owner pour neutraliser les
            # ecarts d'arrondi cumules.
            actif_distributed = 0.0
            passif_distributed = 0.0
            owner_items = list(owner_quotities.items())
            for idx, (oid, quo) in enumerate(owner_items):
                ratio = quo / total_quotities
                is_last = (idx == len(owner_items) - 1)
                if is_last:
                    actif_share = round(total_actif_regul - actif_distributed, 2)
                    passif_share = round(total_passif_regul - passif_distributed, 2)
                else:
                    actif_share = round(total_actif_regul * ratio, 2)
                    passif_share = round(total_passif_regul * ratio, 2)
                    actif_distributed += actif_share
                    passif_distributed += passif_share
                if abs(actif_share) < 0.005 and abs(passif_share) < 0.005:
                    continue
                virt_acc = f"OWNER_{oid}"
                if virt_acc not in balances:
                    owner_doc = owners_by_id.get(oid)
                    if not owner_doc:
                        continue
                    balances[virt_acc] = {
                        "account_number": virt_acc,
                        "account_name": owner_doc.get("name", ""),
                        "debit": 0.0, "credit": 0.0,
                        "is_owner_aggregated": True,
                        # iter90fs : eviter d'afficher une ligne sans
                        # numero de compte pour ce nouveau proprietaire.
                        "display_account": owner_primary_acc.get(oid, ""),
                    }
                # Regul actif -> owner DEBIT (reste cote ACTIF, rubrique V.A)
                balances[virt_acc]["debit"] += actif_share
                actual_regul_actif_distributed += actif_share
                # Regul passif -> owner CREDIT (reste cote PASSIF, rubrique VI.A)
                balances[virt_acc]["credit"] += passif_share
                actual_regul_passif_distributed += passif_share
            # iter90fs : on ne neutralise chaque compte 49X d'origine QUE si
            # sa part a effectivement ete redistribuee (au centime pres).
            # Si un ou plusieurs owners ont echoue (owner_doc None), la
            # portion perdue reste visible sur le compte 49X initial afin
            # que le bilan reste equilibre. Cas rare mais indispensable
            # pour garantir Actif = Passif dans TOUTES les configurations.
            if abs(actual_regul_actif_distributed - total_actif_regul) < 0.01:
                for acc in regul_actif_by_account:
                    balances[acc]["debit"] = 0.0
                    balances[acc]["credit"] = 0.0
            if abs(actual_regul_passif_distributed - total_passif_regul) < 0.01:
                for acc in regul_passif_by_account:
                    balances[acc]["debit"] = 0.0
                    balances[acc]["credit"] = 0.0

        # Reset buckets et re-classer suite a modification balances
        actif_buckets = {k: [] for k in actif_buckets}
        passif_buckets = {k: [] for k in passif_buckets}
        for acc, b in balances.items():
            solde = round(b["debit"] - b["credit"], 2)
            _classify_account(acc, solde, balances)
        # En mode "apres repartition", le 499 est neutralise (solde = 0), donc PAS d'ajout

        # SAFETY NET Actif = Passif.
        missing_boni = round(compte_499 - actual_boni_distributed, 2)
        if abs(missing_boni) > 0.01:
            reason = (
                "quotites manquantes - completer les lots"
                if total_quotities <= 0
                else "proprietaire(s) introuvable(s) - verifier les fiches proprietaires"
            )
            if missing_boni > 0:
                passif_buckets["VII_regul_passif"].append({
                    "account_number": "499",
                    "account_name": f"Compte de regularisation - Boni non reparti ({reason})",
                    "amount": abs(missing_boni),
                })
            else:
                actif_buckets["VIII_regul_actif"].append({
                    "account_number": "499",
                    "account_name": f"Compte de regularisation - Mali non reparti ({reason})",
                    "amount": abs(missing_boni),
                })

    elif abs(compte_499) > 0.01:
        # Mode AVANT REPARTITION : place le boni/mali sur compte 499
        # 499 = Appels (cl.70) - (Charges cl.6 - Produits financiers cl.75)
        if compte_499 > 0:
            passif_buckets["VII_regul_passif"].append({
                "account_number": "499",
                "account_name": "Compte de regularisation - Boni a repartir",
                "amount": abs(compte_499),
            })
        else:
            actif_buckets["VIII_regul_actif"].append({
                "account_number": "499",
                "account_name": "Compte de regularisation - Mali a repartir",
                "amount": abs(compte_499),
            })

    rubr_actif = [
        ("I. Immobilisations incorporelles", "I_immo_incorporelles"),
        ("II. Immobilisations corporelles", "II_immo_corporelles"),
        ("III. Immobilisations financieres", "III_immo_financieres"),
        ("IV. Stocks", "IV_stocks"),
        ("V.A Coproprietaires debiteurs (cl. 400)", "V_creances_coproprietaires"),
        ("V.B Fournisseurs - acomptes / avoirs (cl. 440 D)", "V_creances_fournisseurs_acompte"),
        ("V.C Autres creances", "V_creances_autres"),
        ("VI. Placements de tresorerie", "VI_placements"),
        ("VII. Valeurs disponibles", "VII_disponibilites"),
        ("VIII. Comptes de regularisation (mali)", "VIII_regul_actif"),
    ]
    rubr_passif = [
        ("I. Capital / Fonds propre", "I_capital"),
        ("II. Reserves", "II_reserves"),
        ("III. Resultat reporte", "III_resultat_reporte"),
        ("IV. Subsides en capital", "IV_subsides"),
        ("V. Dettes a plus d'un an", "V_dettes_long"),
        ("VI.A Coproprietaires crediteurs (cl. 400)", "VI_dettes_coproprietaires"),
        ("VI.B Fournisseurs (cl. 440)", "VI_dettes_fournisseurs"),
        ("VI.C Autres dettes court terme", "VI_dettes_autres"),
        ("VII. Comptes de regularisation (boni)", "VII_regul_passif"),
    ]

    actif_rubr = [_rub(lbl, actif_buckets[k]) for lbl, k in rubr_actif]
    passif_rubr = [_rub(lbl, passif_buckets[k]) for lbl, k in rubr_passif]

    total_actif = round(sum(r["total"] for r in actif_rubr), 2)
    total_passif = round(sum(r["total"] for r in passif_rubr), 2)

    return {
        "actif": actif_rubr,
        "passif": passif_rubr,
        "total_actif": total_actif,
        "total_passif": total_passif,
        "equilibre": abs(total_actif - total_passif) < 0.01,
        "ecart": round(total_actif - total_passif, 2),
        "date": date_to or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "fiscal_year": fy.get("name") if fy else None,
        "provisions_appelees": round(provisions_appelees, 2),
        "total_charges": round(total_charges, 2),
        "compte_499": compte_499,
        "produits_hors_provisions": round(produits_hors_provisions, 2),
    }



def create_reports_router(db):
    router = APIRouter(prefix="/api/reports")

    # ==================== iter90go : CACHE BILAN ====================
    # Le stress test iter90gn a mesure /reports/bilan p95=1030ms sous
    # 20 concurrent (le seul WARN restant apres le fix duplicates-audit).
    # Pic reel attendu : envoi groupe de decomptes en fin d'exercice ou
    # consultation simultanee par plusieurs gestionnaires.
    # Cache in-memory {key -> (timestamp, data)} avec TTL court (60s) :
    #   - assez court pour rester "vivant" apres import bancaire / passage
    #     d'ecritures ;
    #   - assez long pour absorber les acces concurrent lors d'une consultation
    #     collective (AG, envoi groupe).
    # La cle inclut (copro_id, date_to, fiscal_year_id, view_mode) - toutes
    # les entrees qui changent le resultat du calcul. `force_refresh=true`
    # bypasse le cache. Header `_cache_hit=true` retourne pour l'observabilite.
    _bilan_cache: dict = {}
    _BILAN_CACHE_TTL = 60  # 60 secondes - compromis fraicheur/perf

    def _bilan_cache_get(key: str):
        import time as _t
        entry = _bilan_cache.get(key)
        if not entry:
            return None
        ts, data = entry
        if _t.time() - ts > _BILAN_CACHE_TTL:
            _bilan_cache.pop(key, None)
            return None
        return data

    def _bilan_cache_set(key: str, data):
        import time as _t
        _bilan_cache[key] = (_t.time(), data)

    # ---- GRAND LIVRE (General Ledger) ----
    @router.get("/grand-livre")
    async def grand_livre(
        request: Request,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_from: Optional[str] = None,
        account_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        include_reversals: bool = False,
    ):
        copropriete_id = _require_copro(copropriete_id, request)
        q = _apply_copro({}, copropriete_id, request)
        if not include_reversals:
            _exclude_reversals(q)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = _date_lte(date_to)

        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(100000)

        ledger = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if account_from and acc < account_from:
                    continue
                if account_to and acc > account_to:
                    continue
                if acc not in ledger:
                    ledger[acc] = {"account_number": acc, "account_name": line.get("account_name", ""), "movements": [], "total_debit": 0, "total_credit": 0}
                ledger[acc]["movements"].append({
                    "date": entry["date"],
                    "journal": entry.get("journal_type", ""),
                    "reference": entry.get("reference", ""),
                    "description": entry.get("description", ""),
                    "debit": line.get("debit", 0),
                    "credit": line.get("credit", 0),
                })
                ledger[acc]["total_debit"] += line.get("debit", 0)
                ledger[acc]["total_credit"] += line.get("credit", 0)

        result = []
        for acc in sorted(ledger.keys()):
            d = ledger[acc]
            d["total_debit"] = round(d["total_debit"], 2)
            d["total_credit"] = round(d["total_credit"], 2)
            d["balance"] = round(d["total_debit"] - d["total_credit"], 2)
            running = 0
            for m in d["movements"]:
                running += m["debit"] - m["credit"]
                m["running_balance"] = round(running, 2)
            result.append(d)

        return result

    # ---- BALANCE DES COMPTES (Trial Balance) ----
    @router.get("/balance")
    async def trial_balance(request: Request, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        copropriete_id = _require_copro(copropriete_id, request)
        q = _apply_copro({}, copropriete_id, request)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = _date_lte(date_to)

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
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
            b["total_debit"] = round(b["total_debit"], 2)
            b["total_credit"] = round(b["total_credit"], 2)
            b["solde_debit"] = round(max(b["total_debit"] - b["total_credit"], 0), 2)
            b["solde_credit"] = round(max(b["total_credit"] - b["total_debit"], 0), 2)
            result.append(b)

        totals = {
            "total_debit": round(sum(b["total_debit"] for b in result), 2),
            "total_credit": round(sum(b["total_credit"] for b in result), 2),
            "solde_debit": round(sum(b["solde_debit"] for b in result), 2),
            "solde_credit": round(sum(b["solde_credit"] for b in result), 2),
        }
        return {"accounts": result, "totals": totals}

    # ---- BILAN (Balance Sheet) - Structure PCMN belge officielle ----
    # ACTIF (debiteur net):
    #   I.   Frais d'etablissement / Immobilisations incorporelles (classe 20-21)
    #   II.  Immobilisations corporelles (classe 22-27): terrains, batiments, mobilier
    #   III. Immobilisations financieres (classe 28)
    #   IV.  Stocks (classe 3)
    #   V.   Creances <= 1 an (classe 40, 41, 416)
    #   VI.  Placements de tresorerie (classe 50-53)
    #   VII. Valeurs disponibles (classe 54-58): banques, caisse
    #   VIII.Comptes de regularisation (classe 49 actif)
    # PASSIF (crediteur net):
    #   I.   Capital / Fonds (classe 10): capital syndic, fonds reserve
    #   II.  Reserves (classe 13): fonds de roulement, reserves obligatoires
    #   III. Resultat reporte (classe 14)
    #   IV.  Subsides en capital (classe 15)
    #   V.   Dettes > 1 an (classe 17)
    #   VI.  Dettes <= 1 an: fournisseurs (44), fiscales/sociales (45), autres (48)
    #   VII. Comptes de regularisation (classe 49 passif)

    @router.get("/bilan")
    async def bilan(request: Request, date_to: Optional[str] = None, copropriete_id: Optional[str] = None,
                    fiscal_year_id: Optional[str] = None,
                    view_mode: Optional[str] = "before_distribution",
                    force_refresh: bool = False):
        """Bilan PCMN belge structure (Actif / Passif par rubriques). Chinese walls strict.
        view_mode :
          - 'before_distribution' (defaut) : le boni/mali apparait sur le compte 499.
          - 'after_distribution' : le 499 est reparti sur les comptes 4000XX des proprietaires
            (par quotites globales + cles speciales eventuelles par compte).

        iter90go : cache in-memory TTL 60s par (copro, date_to, fy, view_mode).
        `force_refresh=true` bypasse le cache.
        """
        copropriete_id = _require_copro(copropriete_id, request)
        # iter90go : cache lookup - key inclut TOUS les parametres qui changent le calcul
        cache_key = f"{copropriete_id}|{date_to or ''}|{fiscal_year_id or ''}|{view_mode or 'before_distribution'}"
        if not force_refresh:
            cached = _bilan_cache_get(cache_key)
            if cached is not None:
                return {**cached, "_cache_hit": True}
        data = await compute_bilan_data(
            db, copropriete_id, date_to=date_to,
            fiscal_year_id=fiscal_year_id, view_mode=view_mode,
        )
        _bilan_cache_set(cache_key, data)
        return data

    # ---- PDF BILAN (par exercice) ----
    @router.get("/bilan/pdf")
    async def bilan_pdf(
        request: Request,
        copropriete_id: Optional[str] = None,
        fiscal_year_id: Optional[str] = None,
        date_to: Optional[str] = None,
        view_mode: Optional[str] = "before_distribution",
    ):
        """Genere le PDF Bilan filtree par exercice (ou date_to libre).
        Chinese walls strict : copropriete_id requis."""
        from pdf_bilan import build_bilan_pdf
        from pdf_layout import resolve_syndic_pdf_context
        copropriete_id = _require_copro(copropriete_id, request)
        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        # Resolve fiscal year (must belong to the ACP)
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one(
                {"id": fiscal_year_id, "copropriete_id": copropriete_id},
                {"_id": 0},
            )
            if not fy:
                raise HTTPException(404, "Exercice non trouve pour cette ACP")
        # Re-utilise le calcul du bilan via l'endpoint interne
        # BUG FIX (iter90fq) : `view_mode` n'etait jamais transmis a l'endpoint
        # interne `bilan()` (parametre absent de la signature) -> le PDF etait
        # TOUJOURS genere en mode "before_distribution" (compte 499 non reparti)
        # meme quand l'utilisateur choisissait "Apres repartition" (le titre du
        # PDF etait aussi hardcode "APRES REPARTITION", ce qui masquait le bug).
        data = await bilan(
            request=request, date_to=date_to, copropriete_id=copropriete_id,
            fiscal_year_id=fiscal_year_id, view_mode=view_mode,
        )
        # Syndic info (si lie a la copro)
        syndic = None
        syndic_id = copro.get("syndic_id")
        if syndic_id:
            syndic = await db.syndics.find_one({"id": syndic_id}, {"_id": 0})
        # iter90dj : logo cabinet + mentions legales dans le PDF
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_bilan_pdf(
            copropriete=copro,
            syndic=syndic,
            fiscal_year=fy,
            bilan_data=data,
            date_to=date_to or (fy.get("end_date") if fy else ""),
            syndic_pdf_ctx=syndic_pdf_ctx,
            view_mode=view_mode,
        )
        safe_name = (copro.get("name", "acp") or "acp").replace(" ", "_").replace("/", "_")
        suffix = (date_to or (fy.get("end_date") if fy else datetime.now(timezone.utc).date().isoformat()))
        filename = f"bilan-{safe_name}-{suffix}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- COMPTE DE RESULTATS (Income Statement) - Structure PCMN belge ----
    # CHARGES (classe 6):
    #   I.   60 Approvisionnements & marchandises (rare en copro)
    #   II.  61 Services et biens divers (entretien, fournitures, syndic honoraires)
    #   III. 62 Remunerations, charges sociales (concierge, etc.)
    #   IV.  63 Amortissements, reductions de valeur
    #   V.   64 Autres charges d'exploitation
    #   VI.  65 Charges financieres
    #   VII. 66 Charges exceptionnelles
    #   VIII.67 Impots sur le resultat
    # PRODUITS (classe 7):
    #   I.   70 Chiffre d'affaires (provisions/cotisations encaissees)
    #   II.  71 Variation des stocks
    #   III. 72 Production immobilisee
    #   IV.  74 Autres produits d'exploitation
    #   V.   75 Produits financiers (interets epargne)
    #   VI.  76 Produits exceptionnels
    @router.get("/resultat")
    async def compte_resultat(request: Request, date_from: Optional[str] = None, date_to: Optional[str] = None,
                              copropriete_id: Optional[str] = None,
                              fiscal_year_id: Optional[str] = None):
        """Compte de Resultats PCMN belge structure (rubriques 60-67 / 70-76). Chinese walls strict.
        Applique les MEMES filtres que le bilan pour garantir la coherence :
        - exclusion des extournes (reversed / is_reversal / EXT-*)
        - exclusion des AN de cloture (garde uniquement les AN d'ouverture)
        - exclusion des OD de regularisation
        - dedup AC orphelines (doublons Optipro)
        - dedup FI auto-generees en doublon
        """
        copropriete_id = _require_copro(copropriete_id, request)
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
                raise HTTPException(400, "Cet exercice appartient a une autre ACP.")
            if fy:
                if not date_from:
                    date_from = fy["start_date"]
                if not date_to:
                    date_to = fy["end_date"]

        q = _apply_copro({}, copropriete_id, request)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = _date_lte(date_to)
        # Memes filtres que compute_bilan_data pour coherence stricte
        q["$or"] = [
            {"journal_type": {"$ne": "AN"}},
            {"journal_type": "AN", "is_opening_balance": True},
        ]
        _exclude_reversals(q)
        q["$and"] = [
            {"is_regularization": {"$ne": True}},
            {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
        ]

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)

        # Dedup FI auto-generees (meme algo que le bilan)
        entries = _dedup_duplicate_auto_fi_entries(entries)

        # Dedup AC orphelines : ne garder que les AC liees a une facture
        inv_q_res = {"copropriete_id": copropriete_id}
        if date_from or date_to:
            inv_q_res["date"] = {}
            if date_from:
                inv_q_res["date"]["$gte"] = date_from
            if date_to:
                inv_q_res["date"]["$lte"] = _date_lte(date_to)
        invoices_for_res = await db.invoices.find(
            inv_q_res, {"_id": 0, "journal_entry_id": 1}
        ).to_list(50000)
        valid_ac_je_ids = {
            inv["journal_entry_id"]
            for inv in invoices_for_res
            if inv.get("journal_entry_id")
        }
        if valid_ac_je_ids:
            entries = [
                e for e in entries
                if e.get("journal_type") != "AC" or e.get("id") in valid_ac_je_ids
            ]

        accounts = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if not acc or acc[0] not in ("6", "7"):
                    continue
                if acc not in accounts:
                    accounts[acc] = {"account_number": acc, "account_name": line.get("account_name", ""),
                                     "debit": 0.0, "credit": 0.0}
                accounts[acc]["debit"] += line.get("debit", 0)
                accounts[acc]["credit"] += line.get("credit", 0)

        # Group by 2-digit rubrique
        ch_rubr_def = [
            ("60. Approvisionnements et marchandises", ["60"]),
            ("61. Services et biens divers", ["61"]),
            ("62. Remunerations et charges sociales", ["62"]),
            ("63. Amortissements et reductions de valeur", ["63"]),
            ("64. Autres charges d'exploitation", ["64"]),
            ("65. Charges financieres", ["65"]),
            ("66. Charges exceptionnelles", ["66"]),
            ("67. Impots sur le resultat", ["67"]),
        ]
        pr_rubr_def = [
            ("70. Chiffre d'affaires (provisions encaissees)", ["70"]),
            ("71. Variation des stocks", ["71"]),
            ("72. Production immobilisee", ["72"]),
            ("74. Autres produits d'exploitation", ["74"]),
            ("75. Produits financiers", ["75"]),
            ("76. Produits exceptionnels", ["76"]),
        ]

        def _build(rubr_def, is_charge):
            out = []
            for label, prefixes in rubr_def:
                items = []
                tot = 0.0
                for acc, b in accounts.items():
                    if any(acc.startswith(p) for p in prefixes):
                        amt = (b["debit"] - b["credit"]) if is_charge else (b["credit"] - b["debit"])
                        if abs(amt) < 0.01:
                            continue
                        items.append({"account_number": acc, "account_name": b["account_name"],
                                      "amount": round(amt, 2)})
                        tot += amt
                out.append({"label": label, "total": round(tot, 2),
                            "accounts": sorted(items, key=lambda x: x["account_number"])})
            return out

        charges_rubr = _build(ch_rubr_def, True)
        produits_rubr = _build(pr_rubr_def, False)

        total_charges = round(sum(r["total"] for r in charges_rubr), 2)
        total_produits = round(sum(r["total"] for r in produits_rubr), 2)
        resultat = round(total_produits - total_charges, 2)

        return {
            "charges": charges_rubr,
            "produits": produits_rubr,
            "total_charges": total_charges,
            "total_produits": total_produits,
            "resultat": resultat,
            "resultat_label": "Benefice" if resultat > 0.01 else ("Perte" if resultat < -0.01 else "Equilibre"),
            "period": {"from": date_from, "to": date_to},
            "fiscal_year": fy.get("name") if fy else None,
        }

    # ---- DECOMPTE ANNUEL PAR PROPRIETAIRE ----
    @router.get("/decompte")
    async def decompte_annuel(request: Request, fiscal_year_id: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None, owner_filter: str = "current"):
        """Decomptes annuels - chinese walls STRICT.

        iter90fw : `owner_filter` (defaut 'current') :
        - 'current' : proprietaires qui possedent >= 1 lot AU 1er JOUR de
          l'exercice selectionne (resolu via `db.mutations` si historique
          disponible). Un vendeur ayant cede en cours d'exercice reste
          "current" pour cet exercice ; un acheteur ayant achete apres le
          start_date est aussi inclus (il apparait des le decompte de
          l'exercice ou il possede pour la 1ere fois). NB : dans la
          pratique on inclut tout owner qui a possede a UN moment de
          l'exercice pour eviter des trous dans les decomptes de mutation.
        - 'all' : proprietaires actuels + anciens qui n'ont plus de lot
          mais gardent un solde tier != 0 dans cette ACP. Chaque owner a
          un flag booleen `is_former` pour distinguer visuellement.
        """
        copropriete_id = _require_copro(copropriete_id, request)
        if owner_filter not in ("current", "all"):
            owner_filter = "current"
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
                raise HTTPException(400, "Cet exercice appartient a une autre ACP.")
            if fy:
                date_from = fy["start_date"]
                date_to = fy["end_date"]

        # Owners are global, but lots/invoices are ACP-scoped (chinese wall)
        lots_q = _apply_copro({}, copropriete_id, request)
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)

        # iter90fw : construction de l'ensemble de reference des owners
        # selon `owner_filter`. Approche :
        # 1. Owners "current" = owner_id direct sur les lots (soit owner
        #    actuel, soit un ancien present dans l'historique de mutations
        #    dont le sale_date recouvre l'exercice).
        # 2. Owners "all" = current + owners ayant deja possede un lot
        #    dans cette ACP (via mutations.from_owner_id/to_owner_id).
        current_owner_ids = set()
        for lt in lots:
            if lt.get("owner_id"):
                current_owner_ids.add(lt["owner_id"])
            for oid in (lt.get("owner_ids") or []):
                if oid:
                    current_owner_ids.add(oid)

        # iter90fw : ajouter les proprietaires qui possedaient un lot AU
        # 1er JOUR de l'exercice ou qui ont possede pendant l'exercice
        # (via `db.mutations`). Un vendeur cedant sa quote-part au cours
        # de l'exercice doit apparaitre dans son decompte final.
        fy_start = (fy or {}).get("start_date") if fy else (date_from or "")
        fy_end = (fy or {}).get("end_date") if fy else (date_to or "")
        muts_all = []
        if fy_start:
            muts_all = await db.mutations.find(
                {"copropriete_id": copropriete_id},
                {"_id": 0, "lot_id": 1, "from_owner_id": 1, "to_owner_id": 1, "sale_date": 1},
            ).to_list(10000)
            # Pour chaque lot, si mutation.sale_date > fy_start,
            # from_owner_id etait le proprietaire AVANT (donc au fy_start
            # si sale_date <= fy_end), on l'inclut.
            # iter90g7 : on inclut AUSSI to_owner_id (acheteur intra-FY),
            # sinon un acheteur dont lot.owner_id n'a pas ete mis a jour
            # (bug legacy import TEUWEN/MATEXI) n'apparait pas dans les
            # decomptes. Le prorata iter90g1 partagera correctement les
            # charges vendeur/acheteur.
            for m in muts_all:
                sd = (m.get("sale_date") or "")
                if not sd:
                    continue
                if not (sd >= fy_start and (not fy_end or sd <= fy_end)):
                    continue
                if m.get("from_owner_id"):
                    current_owner_ids.add(m["from_owner_id"])
                if m.get("to_owner_id"):
                    current_owner_ids.add(m["to_owner_id"])

        # Pour "all" : owners globaux dont un compte tier n'est PAS
        # a zero dans cette ACP.
        all_owner_ids = set(current_owner_ids)
        if owner_filter == "all":
            # Owners avec tier_accounts configures pour cette ACP.
            candidates = await db.owners.find(
                {f"tier_accounts.{copropriete_id}": {"$exists": True}},
                {"_id": 0, "id": 1},
            ).to_list(10000)
            for c in candidates:
                all_owner_ids.add(c["id"])
            # Owners historiques via mutations
            for m in muts_all:
                if m.get("from_owner_id"):
                    all_owner_ids.add(m["from_owner_id"])
                if m.get("to_owner_id"):
                    all_owner_ids.add(m["to_owner_id"])

        target_ids = list(all_owner_ids if owner_filter == "all" else current_owner_ids)
        owners = await db.owners.find({"id": {"$in": target_ids}}, {"_id": 0}).sort("name", 1).to_list(1000) if target_ids else []

        inv_q = _apply_copro({"date": {"$gte": date_from or "2000-01-01", "$lte": _date_lte(date_to or "2099-12-31")}}, copropriete_id, request)
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)

        # iter90ej : fallback quotites via la default distribution_key quand
        # lot.quotity=0 (le champ direct sur le lot n'est pas peuple pour
        # certaines ACPs, les shares vivent dans distribution_keys[is_default]).
        default_key = await db.distribution_keys.find_one(
            {"copropriete_id": copropriete_id, "is_default": True},
            {"_id": 0},
        )
        lot_share_map = {}
        if default_key:
            for kl in default_key.get("lots", []) or []:
                if kl.get("excluded"):
                    continue
                lot_share_map[kl.get("lot_id")] = float(kl.get("share", 0) or 0)

        def _lot_share(lt) -> float:
            q = float(lt.get("quotity", 0) or 0)
            if q > 0:
                return q
            return lot_share_map.get(lt.get("id"), 0)

        # iter90fv : pre-fetch journal entries filtres sur cette ACP pour
        # calculer le SOLDE NET COMPTE TIERS de chaque proprietaire (meme
        # metrique que Balance des Tiers). Positif = debiteur, negatif =
        # crediteur. Utilise par le nouveau tableau compact des decomptes
        # cote frontend (iter90fv), l'utilisateur voulant voir en un
        # coup d'oeil qui doit / a paye trop, sans deployer chaque decompte.
        # On exclut les extournes (is_reversal + reversed) comme
        # `_compute_balance_tiers_for_ui` et health_audit le font.
        # iter90ib : appliquer le MEME filtre AN que balance_tiers_owners
        # et la Situation de compte : inclure UNIQUEMENT les AN avec
        # is_opening_balance=True (vraies reprises) et EXCLURE les AN de
        # cloture (report technique du solde N-1). Sans ce filtre, le
        # tableau compact des decomptes diverge de la Situation PDF et de
        # la Balance des Tiers (bug user Boxus Wivine : 930 vs 638.75).
        balance_q = {
                "copropriete_id": copropriete_id,
                "$or": [
                    {"journal_type": {"$ne": "AN"}},
                    {"journal_type": "AN", "is_opening_balance": True},
                ],
            }
        if date_to:
            balance_q["date"] = {"$lte": _date_lte(date_to)}
        entries_for_balance = await db.journal_entries.find(
            balance_q,
            {"_id": 0, "id": 1, "lines": 1, "is_reversal": 1, "reversed": 1},
        ).to_list(100000)
        owner_acc_to_id = {}
        for o in owners:
            accs = ((o.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
            for k in ("provisions", "reserve", "main"):
                acc = accs.get(k)
                if acc:
                    owner_acc_to_id[acc] = o["id"]
        owner_balances = {o["id"]: 0.0 for o in owners}
        for e in entries_for_balance:
            if e.get("is_reversal") or e.get("reversed"):
                continue
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                oid = owner_acc_to_id.get(acc) or (
                    ln.get("third_party_id") if ln.get("third_party_id") in owner_balances else None
                )
                if oid:
                    d = float(ln.get("debit", 0) or 0)
                    c = float(ln.get("credit", 0) or 0)
                    owner_balances[oid] += d - c

        # iter90ej : phantom fallback - matche les lot_id des distribution_lines
        # legacy avec les lot_id actuels via lot_number normalise (voir iter90du).
        def _norm_lot_num(v: str) -> str:
            return (str(v or "").strip().lstrip("0") or "0")
        lots_by_norm_number = {
            _norm_lot_num(lt.get("number", "")): lt["id"] for lt in lots
        }
        current_lot_ids = {lt["id"] for lt in lots}

        # iter90g7 : mapping owner -> lots impliques dans une mutation intra-FY.
        # Corrige le bug TEUWEN/MATEXI : quand lot.owner_id reste bloque sur
        # l'ancien proprietaire (import legacy, migration incomplete), l'acheteur
        # n'apparait dans AUCUN decompte. En s'appuyant sur db.mutations (source
        # de verite pour l'historique), on rattrape ces cas. Le prorata iter90g1
        # partagera ensuite correctement les charges entre vendeur/acheteur.
        from collections import defaultdict
        lots_via_mutations = defaultdict(set)  # owner_id -> set(lot_id)
        for m in muts_all:
            sd = (m.get("sale_date") or "")
            if not sd:
                continue
            if fy_start and sd < fy_start:
                continue
            if fy_end and sd > fy_end:
                continue
            lid = m.get("lot_id")
            if not lid:
                continue
            if m.get("from_owner_id"):
                lots_via_mutations[m["from_owner_id"]].add(lid)
            if m.get("to_owner_id"):
                lots_via_mutations[m["to_owner_id"]].add(lid)

        total_quotity = sum(_lot_share(lt) for lt in lots)

        decomptes = []
        for owner in owners:
            # iter90g7 : trois criteres pour rattacher un lot a un owner :
            #   1. lot.owner_id == owner.id (owner ACTUEL, cas standard)
            #   2. owner.id in lot.owner_ids (co-propriete legacy)
            #   3. lot implique dans une mutation intra-FY (vendeur ou acheteur
            #      via db.mutations, meme si lot.owner_id est bloque sur
            #      l'ancien proprio suite a un import legacy)
            mut_lot_ids = lots_via_mutations.get(owner["id"], set())
            owner_lots = [
                lt for lt in lots
                if lt.get("owner_id") == owner["id"]
                or owner["id"] in (lt.get("owner_ids") or [])
                or lt["id"] in mut_lot_ids
            ]
            owner_lot_ids = {lt["id"] for lt in owner_lots}
            owner_quotity = sum(_lot_share(lt) for lt in owner_lots)
            share = owner_quotity / total_quotity if total_quotity > 0 else 0

            charges = []
            total_owner_charges = 0.0
            for inv in invoices:
                # iter85f : pour multi-lignes, on agrege les descriptions de
                # lignes par facture (separees par " - ") pour les afficher
                # dans le decompte.
                inv_lines_raw = inv.get("lines") or []
                line_descs = [
                    (li.get("description") or "").strip()
                    for li in inv_lines_raw
                    if (li.get("description") or "").strip()
                ]
                desc_combined = " - ".join(line_descs) if line_descs else (inv.get("description", "") or "")
                desc_label = f"{inv.get('supplier', '')} - {desc_combined}".strip(" -")

                dist_lines = inv.get("distribution_lines") or []
                for dl in dist_lines:
                    # iter90ej : match direct par lot_id OU fallback par
                    # lot_number normalise pour les factures legacy dont les
                    # lot_id ne pointent plus vers des lots reels (phantom).
                    dl_lot_id = dl.get("lot_id", "")
                    if dl_lot_id and dl_lot_id in current_lot_ids:
                        matched_lot_id = dl_lot_id
                    else:
                        matched_lot_id = lots_by_norm_number.get(_norm_lot_num(dl.get("lot_number", "")))
                    if matched_lot_id and matched_lot_id in owner_lot_ids:
                        amt = float(dl.get("amount", 0) or 0)
                        charges.append({
                            "date": inv["date"],
                            "description": desc_label,
                            "invoice_number": inv.get("number", ""),
                            "amount": amt,
                        })
                        total_owner_charges += amt

                if not dist_lines and share > 0:
                    owner_amount = round(inv.get("total_amount", 0) * share, 2)
                    charges.append({
                        "date": inv["date"],
                        "description": desc_label,
                        "invoice_number": inv.get("number", ""),
                        "amount": owner_amount,
                    })
                    total_owner_charges += owner_amount

            decomptes.append({
                "owner_id": owner["id"],
                "owner_name": owner["name"],
                "vcs_code": owner.get("vcs_code", ""),
                "email": owner.get("email", ""),
                "lots": [{"number": lt["number"], "quotity": _lot_share(lt)} for lt in owner_lots],
                "share_pct": round(share * 100, 2),
                "charges": charges,
                "total_charges": round(total_owner_charges, 2),
                # iter90fv : solde net tier accounts (meme metrique que la
                # Balance des Tiers et le dashboard health audit). Positif
                # = proprietaire debiteur (doit de l'argent), negatif =
                # crediteur (a paye trop / a du remboursement).
                "tier_balance": round(owner_balances.get(owner["id"], 0.0), 2),
                # iter90fw : owner considere comme "actuel" s'il possede
                # au moins un lot au 1er jour de l'exercice OU s'il a
                # possede pendant l'exercice (vendeur mid-year). Sinon,
                # c'est un ancien proprietaire (visible uniquement en
                # mode owner_filter='all').
                "is_former": owner["id"] not in current_owner_ids,
            })

        return {
            "decomptes": decomptes,
            "period": {"from": date_from, "to": date_to},
            "owner_filter": owner_filter,
        }

    # ---- PDF DECOMPTE ----
    @router.get("/decompte/pdf/{owner_id}")
    async def decompte_pdf(
        owner_id: str,
        request: Request,
        fiscal_year_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        preview: bool = False,
    ):
        """Genere le PDF Decompte annuel pour un proprietaire. Chinese walls
        STRICT : `copropriete_id` (param ou header X-Copropriete-Id) requis,
        sinon 400. Si fiscal_year_id, son `copropriete_id` doit matcher."""
        from pdf_decompte import build_decompte_pdf

        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")

        # Resolve ACP from param OR header ONLY (no implicit fallback to "first lot")
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(
                400,
                "copropriete_id requis - les decomptes sont strictement scopes a une ACP. "
                "Selectionnez une copropriete avant de generer le PDF."
            )

        # Resolve fiscal year - if provided, MUST match the ACP
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
                raise HTTPException(
                    400,
                    f"L'exercice {fy.get('name','')} appartient a une autre ACP. "
                    "Chinese walls strict : aucun melange autorise."
                )
        if not fy:
            today = datetime.now(timezone.utc)
            fy = {
                "name": f"Exercice {today.year}",
                "start_date": date_from or f"{today.year}-01-01",
                "end_date": date_to or f"{today.year}-12-31",
            }
            # Best-effort lookup of real fiscal year covering this period IN THIS ACP
            real_fy = await db.fiscal_years.find_one(
                {"copropriete_id": copropriete_id,
                 "start_date": {"$lte": _date_lte(fy["end_date"])},
                 "end_date": {"$gte": fy["start_date"]}},
                {"_id": 0}
            )
            if real_fy:
                fy = real_fy

        # Verrou : on ne peut generer un decompte annuel DEFINITIF QUE si l'exercice
        # est CLOTURE. Avant cloture, les chiffres sont encore en mouvement et le
        # decompte n'a pas de valeur juridique. Utiliser la situation de compte pour
        # un releve a date. En mode `preview=true`, on permet la generation avec un
        # filigrane "APERCU - NON DEFINITIF" sur le PDF.
        fy_status = fy.get("status", "")
        if fy_status != "closed" and not preview:
            raise HTTPException(
                400,
                f"L'exercice '{fy.get('name','')}' n'est pas cloture (statut: {fy_status or 'inconnu'}). "
                "Le decompte annuel definitif est genere uniquement apres cloture de l'exercice. "
                "Pour une previsualisation, utilisez le mode `preview=true`."
            )

        copro_id_use = copropriete_id
        copro = await db.coproprietes.find_one({"id": copro_id_use}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Owner's lots in this ACP - iter90g7 : inclut aussi les lots impliques
        # dans une mutation intra-FY (via db.mutations), meme si lot.owner_id est
        # bloque sur l'ancien proprietaire (cas bug TEUWEN/MATEXI legacy).
        owner_lots = await db.lots.find(
            {"copropriete_id": copro_id_use,
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(100)
        # Ajoute les lots impliques dans une mutation ou l'owner est vendeur/acheteur
        muts_for_owner = await db.mutations.find(
            {"copropriete_id": copro_id_use,
             "$or": [{"from_owner_id": owner_id}, {"to_owner_id": owner_id}],
             "sale_date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
            {"_id": 0, "lot_id": 1},
        ).to_list(1000)
        existing_lot_ids = {l["id"] for l in owner_lots}
        extra_lot_ids = {
            m["lot_id"] for m in muts_for_owner
            if m.get("lot_id") and m["lot_id"] not in existing_lot_ids
        }
        if extra_lot_ids:
            extra_lots = await db.lots.find(
                {"id": {"$in": list(extra_lot_ids)},
                 "copropriete_id": copro_id_use},
                {"_id": 0}
            ).to_list(100)
            owner_lots.extend(extra_lots)
        all_lots = await db.lots.find({"copropriete_id": copro_id_use}, {"_id": 0}).to_list(1000)

        # Invoices in period
        invoices = await db.invoices.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Distribution keys
        distribution_keys = await db.distribution_keys.find(
            {"copropriete_id": copro_id_use}, {"_id": 0}
        ).to_list(1000)

        # iter90el : mutations pour ce owner sur ses lots dans l'exercice
        owner_lot_ids_list = [l["id"] for l in owner_lots]
        mutations_docs = await db.mutations.find(
            {"copropriete_id": copro_id_use,
             "lot_id": {"$in": owner_lot_ids_list},
             "sale_date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
            {"_id": 0},
        ).to_list(1000) if owner_lot_ids_list else []

        # iter90g6 : ecritures OD lot_mutation touchant le compte tier de
        # l'owner (MUT-R/MUT-P/MUT-F) - impactent le solde definitif.
        mutation_entries_docs = await db.journal_entries.find(
            {"copropriete_id": copro_id_use,
             "source_type": "lot_mutation",
             "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])},
             "reversed": {"$ne": True},
             "is_reversal": {"$ne": True},
             "lines.third_party_id": owner_id},
            {"_id": 0},
        ).to_list(10000)

        # iter90i9 : grand livre canonique du proprio (source Situation)
        _tier_vals_i9 = [
            v for v in ((owner.get("tier_accounts") or {}).get(copro_id_use, {}) or {}).values()
            if isinstance(v, str) and v
        ]
        _owner_ledger_q = {
            "copropriete_id": copro_id_use,
            "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])},
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }
        _or_i9 = [{"lines.third_party_id": owner_id}]
        if _tier_vals_i9:
            _or_i9.append({"lines.account_number": {"$in": _tier_vals_i9}})
        _owner_ledger_q["$or"] = _or_i9
        owner_ledger_entries = await db.journal_entries.find(
            _owner_ledger_q, {"_id": 0},
        ).to_list(100000)

        # Fund calls in period
        fund_calls = await db.fund_calls.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Payments (bank transactions matched to this owner OR communication = VCS)
        owner_vcs_digits = owner.get("vcs_digits", "")
        all_txns = await db.bank_transactions.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": _date_lte(fy["end_date"])}},
            {"_id": 0}
        ).sort("date", 1).to_list(100000)
        payments = []
        for t in all_txns:
            is_owner = False
            if t.get("matched") and t.get("match_type") == "owner_payment" and t.get("matched_to") == owner_id:
                is_owner = True
            elif not t.get("matched") and owner_vcs_digits:
                comm = (t.get("communication") or "").replace("+", "").replace("/", "").replace(" ", "")
                if comm == owner_vcs_digits:
                    is_owner = True
            if is_owner:
                payments.append(t)

        # Build natures map (account_number -> nature_name + PCMN name fallback)
        cats = await db.expense_categories.find(
            {"copropriete_id": copro["id"]} if copro.get("id") else {}, {"_id": 0}
        ).to_list(1000)
        pcmn_acc = await db.pcmn_accounts.find(
            {"class_num": 6, "copropriete_id": copro.get("id", "")}, {"_id": 0}
        ).to_list(1000)
        nature_map = {a["number"]: a.get("name", "") for a in pcmn_acc}
        for c in cats:
            nature_map[c["account_number"]] = c["name"]

        # iter90ie : compteurs + releves pour cles de type "meter"
        meters_ = await db.meters.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(1000)
        meter_ids = [m["id"] for m in meters_]
        meter_readings_ = []
        if meter_ids:
            meter_readings_ = await db.meter_readings.find(
                {"meter_id": {"$in": meter_ids}}, {"_id": 0}
            ).to_list(100000)

        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copro, fiscal_year=fy,
            owner_lots=owner_lots, all_lots=all_lots,
            invoices=invoices, distribution_keys=distribution_keys,
            fund_calls=fund_calls, payments=payments,
            expense_accounts_map=nature_map,
            # iter90ek : le filigrane APERCU depend UNIQUEMENT du statut reel
            # de l'exercice. Un exercice cloture ne doit JAMAIS avoir de
            # filigrane, meme si le parametre preview=true (bouton Apercu)
            # est envoye. Le mode "preview" du frontend est juste un mode
            # d'affichage inline, pas une decision juridique.
            preview=(preview and fy_status != "closed"),
            # iter90el : prise en compte des mutations pour le prorata jours
            mutations=mutations_docs,
            # iter90g6 : OD MUT-R/P/F pour solde exact
            mutation_entries=mutation_entries_docs,
            # iter90i9 : align Decompte totals with Situation
            owner_ledger_entries=owner_ledger_entries,
            # iter90ie : cles meter
            meters=meters_,
            meter_readings=meter_readings_,
        )

        filename = f"decompte_{owner['name'].replace(' ', '_')}_{fy.get('name','').replace(' ', '_')}.pdf"
        # In preview mode, render inline (so browsers display in iframe instead of downloading)
        disposition = 'inline' if preview else 'attachment'
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )

    # ---- LISTE DES DEPENSES PDF ----
    @router.get("/depenses/pdf")
    async def liste_depenses_pdf(
        copropriete_id: str,
        date_from: str,
        date_to: str,
        distribution_key_id: Optional[str] = None,
        account_number: Optional[str] = None,
        expense_category_id: Optional[str] = None,
    ):
        """Genere le PDF 'Liste des depenses' au format Syndic belge.
        iter90e : aligne sur la vue UI /api/fiscal/expenses (factures expandees
        + ecritures FI/OD classe 6) pour respecter l'invariant
            sum(TVAC du PDF) == totals.total renvoye par /api/fiscal/expenses.
        Hierarchie: Cle de repartition -> Nature -> Compte -> lignes.
        Colonnes: Date valeur, Libelle, Fournisseur, Ref. interne, HTVA, TVA, TVAC, Part proprietaire, Part occupant.
        """
        from pdf_liste_depenses import build_liste_depenses_pdf
        from pdf_layout import resolve_syndic_pdf_context
        from expense_rows import compute_expense_rows

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # iter90e : same logic as /api/fiscal/expenses -> guarantees the invariant.
        rows, totals = await compute_expense_rows(
            db, copropriete_id,
            date_from=date_from, date_to=date_to,
            distribution_key_id=distribution_key_id,
            account_number=account_number,
            expense_category_id=expense_category_id,
        )

        distribution_keys = await db.distribution_keys.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(1000)
        pcmn = await db.pcmn_accounts.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(10000)
        pcmn_map = {a["number"]: a.get("name", "") for a in pcmn}
        cats = await db.expense_categories.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(1000)

        # iter90dj : logo cabinet + mentions legales
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_liste_depenses_pdf(
            copropriete=copro,
            date_from=date_from, date_to=date_to,
            invoices=rows,   # iter90e : pass already-expanded rows (not raw invoices)
            distribution_keys=distribution_keys,
            pcmn_map=pcmn_map,
            expense_categories=cats,
            syndic_pdf_ctx=syndic_pdf_ctx,
        )
        filename = f"liste_depenses_{date_from}_au_{date_to}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- iter90d : EXPORT PDF DE TOUS LES JOURNAUX COMPTABLES ----
    @router.get("/journals/pdf")
    async def journals_pdf(
        copropriete_id: str,
        date_from: str,
        date_to: str,
        journal_type: Optional[str] = None,
    ):
        """Exporte TOUS les journaux comptables (AC, OD, BQ, VE...) sur la
        periode demandee, en un PDF unique. Format paysage A4, controle PCMN
        (somme debits = somme credits = total general)."""
        from pdf_journals_and_invoices import build_journals_pdf
        from pdf_layout import resolve_syndic_pdf_context

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        q = {"copropriete_id": copropriete_id,
             "date": {"$gte": date_from, "$lte": _date_lte(date_to)}}
        if journal_type:
            q["journal_type"] = journal_type.upper()
        entries = await db.journal_entries.find(q, {"_id": 0}).sort("date", 1).to_list(100000)

        # iter90dj : logo cabinet + mentions legales
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_journals_pdf(
            copropriete=copro,
            date_from=date_from, date_to=date_to,
            entries=entries,
            syndic_pdf_ctx=syndic_pdf_ctx,
        )
        filename = f"journaux_{date_from}_au_{date_to}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- iter90d : EXPORT PDF LISTE EXHAUSTIVE DES FACTURES ----
    @router.get("/invoices-list/pdf")
    async def invoices_list_pdf(
        copropriete_id: str,
        date_from: str,
        date_to: str,
        status: Optional[str] = None,
    ):
        """Exporte la LISTE EXHAUSTIVE des factures (toutes natures, tous
        statuts) sur la periode demandee. Colonnes: Date, N piece,
        Fournisseur, Libelle, Compte, HTVA, TVA, TVAC, Statut."""
        from pdf_journals_and_invoices import build_invoices_list_pdf
        from pdf_layout import resolve_syndic_pdf_context

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        q = {"copropriete_id": copropriete_id,
             "date": {"$gte": date_from, "$lte": _date_lte(date_to)}}
        if status:
            q["status"] = status
        invoices = await db.invoices.find(q, {"_id": 0}).sort("date", 1).to_list(100000)

        # iter90dj : logo cabinet + mentions legales
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_invoices_list_pdf(
            copropriete=copro,
            date_from=date_from, date_to=date_to,
            invoices=invoices,
            syndic_pdf_ctx=syndic_pdf_ctx,
        )
        filename = f"liste_factures_{date_from}_au_{date_to}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- BALANCE DE TIERS PROPRIETAIRES ----
    @router.get("/balance-tiers/owners")
    async def balance_tiers_owners(
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Balance de tiers proprietaires (basee sur le grand livre).

        Calcule pour chaque proprietaire le solde des comptes :
          - 40000XXX Provisions (debit = appels & charges privatives, credit = paiements)
          - 40010XXX Fonds de reserve (debit = appels reserve, credit = paiements)

        Inclut TOUS les journal_entries (AC/VE/FI/OD/A-Nouveau) - donc les
        Operations Diverses manuelles apparaissent aussi.
        Les paiements bancaires non encore lettres mais reconnus par VCS sont
        comptes en credit additionnel (ils generent un FI automatique au matching).
        Filtre optionnel par periode `start_date` / `end_date` (inclusif, ISO YYYY-MM-DD).
        """
        if not copropriete_id:
            return {"owners": [], "total_debiteurs": 0, "total_crediteurs": 0}

        lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        owner_ids = set(lt.get("owner_id") for lt in lots if lt.get("owner_id"))
        # Inclure aussi les co-proprietaires (owner_ids pluriel sur les lots)
        for lt in lots:
            for oid in (lt.get("owner_ids") or []):
                if oid:
                    owner_ids.add(oid)

        # Proprietaires avec tier_accounts configures pour cette ACP
        # (ex: promoteurs comme Matexi qui n'ont pas de lots mais des comptes)
        tier_candidates = await db.owners.find(
            {f"tier_accounts.{copropriete_id}": {"$exists": True}},
            {"_id": 0, "id": 1},
        ).to_list(10000)
        for c in tier_candidates:
            owner_ids.add(c["id"])

        # Anciens proprietaires via mutations
        muts = await db.mutations.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0, "from_owner_id": 1, "to_owner_id": 1},
        ).to_list(10000)
        for m in muts:
            if m.get("from_owner_id"):
                owner_ids.add(m["from_owner_id"])
            if m.get("to_owner_id"):
                owner_ids.add(m["to_owner_id"])

        # Aussi inclure les anciens proprietaires (vendus) qui ont encore un
        # mouvement / solde sur leurs comptes tiers dans cette ACP.
        # On scanne les journal_entries pour trouver tous les third_party_id et
        # tous les comptes 40000XXX/40010XXX rencontres, puis on retrouve les owners
        # qui ont ces comptes en tier_accounts[copropriete_id].
        third_party_ids_in_je = await db.journal_entries.distinct(
            "lines.third_party_id", {"copropriete_id": copropriete_id}
        )
        for tpid in third_party_ids_in_je:
            if tpid:
                owner_ids.add(tpid)
        owner_ids = list(owner_ids)
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).sort("name", 1).to_list(1000) if owner_ids else []
        # Set of owners that still hold at least one lot in this ACP
        current_owner_ids = set(lt.get("owner_id") for lt in lots if lt.get("owner_id"))
        for lt in lots:
            for oid in (lt.get("owner_ids") or []):
                if oid:
                    current_owner_ids.add(oid)

        # Charge journal entries ACP-scoped (un seul fetch) + filtre periode
        # SECURISATION : exclure les contre-passations et leurs ecritures
        # extournees -> elles s'annulent au bilan, ne doivent pas figurer
        # dans les balances tiers operationnelles.
        # iter90ia : appliquer le MEME filtre AN que la Situation de compte
        # et le Bilan : inclure UNIQUEMENT les AN avec is_opening_balance=True
        # (vraies reprises comptables) et EXCLURE les AN de cloture (report
        # de solde en double, cree lors du closing de l'exercice N-1).
        je_q = {
            "copropriete_id": copropriete_id,
            "$or": [
                {"journal_type": {"$ne": "AN"}},
                {"journal_type": "AN", "is_opening_balance": True},
            ],
            # Alignement bilan : exclure regularisations et OD-REG/EXT
            "$and": [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ],
        }
        if start_date or end_date:
            je_q["date"] = {}
            if start_date:
                je_q["date"]["$gte"] = start_date
            if end_date:
                je_q["date"]["$lte"] = _date_lte(end_date)
        _exclude_reversals(je_q)
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Pour le SOLDE CUMULATIF : entries de toutes les dates jusqu'a end_date (inclus anterieurs)
        # iter90ia : meme filtre AN que ci-dessus
        je_q_cumul = {
            "copropriete_id": copropriete_id,
            "$or": [
                {"journal_type": {"$ne": "AN"}},
                {"journal_type": "AN", "is_opening_balance": True},
            ],
            # Alignement bilan : exclure regularisations et OD-REG/EXT
            "$and": [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ],
        }
        if end_date:
            je_q_cumul["date"] = {"$lte": _date_lte(end_date)}
        _exclude_reversals(je_q_cumul)
        entries_cumul = await db.journal_entries.find(je_q_cumul, {"_id": 0}).to_list(100000)
        # Agregation cumulatif par owner_id x compte
        cumul_per_owner = {}  # oid -> {prov_debit, prov_credit, res_debit, res_credit}
        cumul_per_acc = {}   # account_number -> {debit, credit}
        for e in entries_cumul:
            for ln in e.get("lines", []) or []:
                tpid = ln.get("third_party_id")
                acc = ln.get("account_number", "")
                d_val = float(ln.get("debit", 0) or 0)
                c_val = float(ln.get("credit", 0) or 0)
                if tpid:
                    cumul_per_owner.setdefault(tpid, {"by_acc": {}})
                    cumul_per_owner[tpid]["by_acc"].setdefault(acc, {"debit": 0.0, "credit": 0.0})
                    cumul_per_owner[tpid]["by_acc"][acc]["debit"] += d_val
                    cumul_per_owner[tpid]["by_acc"][acc]["credit"] += c_val
                else:
                    cumul_per_acc.setdefault(acc, {"debit": 0.0, "credit": 0.0})
                    cumul_per_acc[acc]["debit"] += d_val
                    cumul_per_acc[acc]["credit"] += c_val
        # Aggregate Dr/Cr per account_number x third_party_id (or third_party falls back to scan all owners)
        # Pour les comptes tiers les lignes ont third_party_id = owner_id
        per_owner_lines = {}  # owner_id -> [line + entry_meta]
        per_acc_lines = {}  # account_number -> [line + entry_meta]  (fallback if no third_party)
        for e in entries:
            for ln in e.get("lines", []) or []:
                meta = {
                    "date": e.get("date", ""),
                    "journal_type": e.get("journal_type", ""),
                    "description": e.get("description", ""),
                    "reference": e.get("reference", ""),
                    "entry_id": e.get("id", ""),
                    "account_number": ln.get("account_number", ""),
                    "account_name": ln.get("account_name", ""),
                    "debit": float(ln.get("debit", 0) or 0),
                    "credit": float(ln.get("credit", 0) or 0),
                }
                tpid = ln.get("third_party_id")
                if tpid:
                    per_owner_lines.setdefault(tpid, []).append(meta)
                else:
                    per_acc_lines.setdefault(ln.get("account_number", ""), []).append(meta)

        # Charge bank txns ACP-scoped + filtre periode (pour VCS non lettres)
        bt_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            bt_q["date"] = {}
            if start_date:
                bt_q["date"]["$gte"] = start_date
            if end_date:
                bt_q["date"]["$lte"] = _date_lte(end_date)
        all_bank_txns = await db.bank_transactions.find(bt_q, {"_id": 0}).to_list(100000)

        # VCS lookup
        vcs_to_owner = {}
        for owner in owners:
            if owner.get("vcs_digits"):
                vcs_to_owner[owner["vcs_digits"]] = owner["id"]
            if owner.get("vcs_code"):
                vcs_to_owner[owner["vcs_code"]] = owner["id"]

        result = []
        for owner in owners:
            oid = owner["id"]
            tier_acc = (owner.get("tier_accounts") or {}).get(copropriete_id or "", {}) or {}
            acc_prov = tier_acc.get("provisions", "")
            acc_res = tier_acc.get("reserve", "")

            # 1) Lines explicitly tagged with third_party_id = owner_id
            owner_lines = list(per_owner_lines.get(oid, []))
            # 2) Lines on the owner's accounts without third_party_id tag
            seen_keys = set((m["entry_id"], m["account_number"], m["debit"], m["credit"]) for m in owner_lines)
            for acc in (acc_prov, acc_res):
                if not acc:
                    continue
                for m in per_acc_lines.get(acc, []):
                    key = (m["entry_id"], m["account_number"], m["debit"], m["credit"])
                    if key in seen_keys:
                        continue
                    owner_lines.append(m)
                    seen_keys.add(key)

            # Filtrer pour ne garder que les lignes sur 40000XXX ou 40010XXX de cet owner
            valid_accs = {acc_prov, acc_res}
            owner_lines = [m for m in owner_lines if m["account_number"] in valid_accs]

            prov_debit = sum(m["debit"] for m in owner_lines if m["account_number"] == acc_prov)
            prov_credit = sum(m["credit"] for m in owner_lines if m["account_number"] == acc_prov)
            res_debit = sum(m["debit"] for m in owner_lines if m["account_number"] == acc_res)
            res_credit = sum(m["credit"] for m in owner_lines if m["account_number"] == acc_res)

            # Unmatched bank txns recognized via VCS -> credit additionnel
            unmatched_paid = 0
            unmatched_movements = []
            for txn in all_bank_txns:
                if txn.get("matched"):
                    continue
                comm = txn.get("communication", "") or ""
                if not comm:
                    continue
                clean = comm.replace("+", "").replace("/", "").replace(" ", "")
                if vcs_to_owner.get(clean) == oid or vcs_to_owner.get(comm) == oid:
                    amt = abs(float(txn.get("amount", 0) or 0))
                    unmatched_paid += amt
                    unmatched_movements.append({
                        "date": txn.get("date", ""),
                        "journal_type": "BANK-UNMATCHED",
                        "description": f"Paiement non lettre: {txn.get('counterparty_name','') or comm}",
                        "reference": txn.get("id", ""),
                        "entry_id": "",
                        "account_number": acc_prov,
                        "account_name": "Banque (a lettrer)",
                        "debit": 0.0,
                        "credit": amt,
                    })

            total_called = round(prov_debit + res_debit, 2)
            total_paid = round(prov_credit + res_credit + unmatched_paid, 2)
            # iter90ia : Balance CUMULATIF - MEME source que la Situation de
            # compte (PDF). On somme TOUTES les lignes du grand livre marquees
            # avec `third_party_id = owner_id`, quel que soit le compte
            # (provisions, reserve, MAIS AUSSI comptes historiques post-
            # renumerotation, ODs d'imputation charges, ODs de mutation, etc.).
            # Avant iter90ia : la balance ne comptait que les comptes canoniques
            # acc_prov + acc_res -> divergence avec le PDF Situation (ex : 930
            # dans la liste vs 638.75 dans le PDF pour Boxus Wivine).
            owner_cumul_all = (cumul_per_owner.get(oid) or {"by_acc": {}}).get("by_acc", {})
            cumul_called_all = 0.0
            cumul_paid_all = 0.0
            for _acc, _dc in owner_cumul_all.items():
                cumul_called_all += float(_dc.get("debit", 0) or 0)
                cumul_paid_all += float(_dc.get("credit", 0) or 0)
            # Fallback : lignes sans third_party_id mais sur les comptes tiers
            # canoniques du proprio (retrocompat pour anciens imports).
            prov_d_cumul = owner_cumul_all.get(acc_prov, {}).get("debit", 0.0)
            prov_c_cumul = owner_cumul_all.get(acc_prov, {}).get("credit", 0.0)
            res_d_cumul = owner_cumul_all.get(acc_res, {}).get("debit", 0.0)
            res_c_cumul = owner_cumul_all.get(acc_res, {}).get("credit", 0.0)
            if acc_prov and acc_prov in cumul_per_acc:
                prov_d_cumul += cumul_per_acc[acc_prov]["debit"]
                prov_c_cumul += cumul_per_acc[acc_prov]["credit"]
                cumul_called_all += cumul_per_acc[acc_prov]["debit"]
                cumul_paid_all += cumul_per_acc[acc_prov]["credit"]
                cumul_per_acc[acc_prov] = {"debit": 0.0, "credit": 0.0}
            if acc_res and acc_res in cumul_per_acc:
                res_d_cumul += cumul_per_acc[acc_res]["debit"]
                res_c_cumul += cumul_per_acc[acc_res]["credit"]
                cumul_called_all += cumul_per_acc[acc_res]["debit"]
                cumul_paid_all += cumul_per_acc[acc_res]["credit"]
                cumul_per_acc[acc_res] = {"debit": 0.0, "credit": 0.0}
            cumul_called = cumul_called_all
            cumul_paid = cumul_paid_all + unmatched_paid
            balance = round(cumul_called - cumul_paid, 2)

            movements = sorted(owner_lines + unmatched_movements, key=lambda x: x["date"])

            result.append({
                "owner_id": oid,
                "owner_name": owner["name"],
                "vcs_code": owner.get("vcs_code", ""),
                "account_provisions": acc_prov,
                "account_reserve": acc_res,
                "provisions_debit": round(prov_debit, 2),
                "provisions_credit": round(prov_credit, 2),
                # provisions_balance / reserve_balance = soldes CUMULATIFS du compte tier
                "provisions_balance": round(prov_d_cumul - prov_c_cumul, 2),
                "reserve_debit": round(res_debit, 2),
                "reserve_credit": round(res_credit, 2),
                "reserve_balance": round(res_d_cumul - res_c_cumul, 2),
                "unmatched_paid": round(unmatched_paid, 2),
                "total_called": total_called,
                "total_paid": total_paid,
                "balance": balance,
                "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
                "movements_count": len(movements),
                "is_former_owner": oid not in current_owner_ids,
            })

        # Hide ex-proprietaires that have a zero balance and no movement
        result = [r for r in result if not (r.get("is_former_owner") and abs(r["balance"]) < 0.01 and r["movements_count"] == 0)]

        # iter90ak : ajouter des lignes synthetiques pour les comptes tiers
        # avec solde non nul mais qui ne sont rattaches a AUCUN proprietaire
        # (ex. ancien proprietaire supprime de la collection owners, ou
        # tier_accounts jamais renseigne). Sans ce fallback, ces soldes sont
        # invisibles ici alors que la Sante comptable les detecte comme
        # "comptes tier orphelins".
        acc_names = {}
        try:
            _pcmn = await db.pcmn_accounts.find(
                {"copropriete_id": copropriete_id},
                {"_id": 0, "number": 1, "name": 1},
            ).to_list(10000)
            for _a in _pcmn:
                acc_names[_a.get("number", "")] = _a.get("name", "")
        except Exception:
            pass
        for _acc, _b in list(cumul_per_acc.items()):
            if not _acc:
                continue
            if not (_acc.startswith("4100") or _acc.startswith("4000") or _acc.startswith("4001")):
                continue
            _solde = round(float(_b.get("debit", 0)) - float(_b.get("credit", 0)), 2)
            if abs(_solde) < 0.01:
                continue
            # Recuperer les mouvements associes pour l'expand-details
            _mvts_period = per_acc_lines.get(_acc, [])
            # Detecter provisions vs reserve via prefixe (bilan belge PCMN)
            _is_reserve = _acc.startswith("4001") or _acc.startswith("40010")
            _prov_d = 0.0 if _is_reserve else float(_b.get("debit", 0))
            _prov_c = 0.0 if _is_reserve else float(_b.get("credit", 0))
            _res_d = float(_b.get("debit", 0)) if _is_reserve else 0.0
            _res_c = float(_b.get("credit", 0)) if _is_reserve else 0.0
            result.append({
                "owner_id": "",
                "owner_name": acc_names.get(_acc) or f"Ancien proprietaire (compte {_acc})",
                "vcs_code": "",
                "account_provisions": "" if _is_reserve else _acc,
                "account_reserve": _acc if _is_reserve else "",
                "provisions_debit": round(_prov_d, 2),
                "provisions_credit": round(_prov_c, 2),
                "provisions_balance": round(_prov_d - _prov_c, 2),
                "reserve_debit": round(_res_d, 2),
                "reserve_credit": round(_res_c, 2),
                "reserve_balance": round(_res_d - _res_c, 2),
                "unmatched_paid": 0.0,
                "total_called": round(_prov_d + _res_d, 2),
                "total_paid": round(_prov_c + _res_c, 2),
                "balance": _solde,
                "status": "debiteur" if _solde > 0.01 else ("crediteur" if _solde < -0.01 else "solde"),
                "movements_count": len(_mvts_period),
                "is_former_owner": True,
                "is_orphan_account": True,
            })

        total_debiteurs = round(sum(r["balance"] for r in result if r["balance"] > 0), 2)
        total_crediteurs = round(sum(abs(r["balance"]) for r in result if r["balance"] < 0), 2)
        return {"owners": result, "total_debiteurs": total_debiteurs, "total_crediteurs": total_crediteurs}

    # ---- PDF SYNTHESE BALANCE DES TIERS (proprietaires + fournisseurs) ----
    @router.get("/balance-tiers/pdf")
    async def balance_tiers_pdf(
        request: Request,
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        scope: str = "both",
        view: str = "simplified",
    ):
        """Genere un PDF de la balance des tiers (proprietaires et/ou fournisseurs).
        Chinese walls strict : `copropriete_id` requis (param ou header).

        iter90fm :
        - `scope` = "owners" | "suppliers" | "both" (defaut) - permet un export
          UNIQUEMENT proprietaires ou UNIQUEMENT fournisseurs.
        - `view` = "simplified" (defaut, 1 ligne de totaux par tiers) |
          "detailed" (le detail des mouvements du grand livre par tiers, en
          plus du solde - document interne syndic, distinct de la
          "Situation de compte" postale individuelle)."""
        from pdf_layout import resolve_syndic_pdf_context

        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") if request else None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        if scope not in ("owners", "suppliers", "both"):
            raise HTTPException(400, "scope invalide (owners|suppliers|both)")
        if view not in ("simplified", "detailed"):
            raise HTTPException(400, "view invalide (simplified|detailed)")

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        want_owners = scope in ("owners", "both")
        want_suppliers = scope in ("suppliers", "both")
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        safe_name = (copro.get("name", "acp") or "acp").replace(" ", "_").replace("/", "_")
        suffix = (end_date or datetime.now(timezone.utc).date().isoformat())

        if view == "detailed":
            from pdf_balance_tiers import build_balance_tiers_detailed_pdf
            owners_detail = None
            suppliers_detail = None
            if want_owners:
                owners_data = await balance_tiers_owners(
                    copropriete_id=copropriete_id, start_date=start_date, end_date=end_date,
                )
                owners_detail = []
                for o in owners_data.get("owners", []) or []:
                    if not o.get("owner_id"):
                        continue
                    detail = await situation_compte_owner(
                        owner_id=o["owner_id"], request=request, copropriete_id=copropriete_id,
                        start_date=start_date, end_date=end_date,
                    )
                    owners_detail.append(detail)
            if want_suppliers:
                suppliers_data = await balance_tiers_suppliers(
                    request=request, copropriete_id=copropriete_id,
                    start_date=start_date, end_date=end_date,
                )
                suppliers_detail = []
                for s in suppliers_data.get("suppliers", []) or []:
                    if not s.get("supplier_id"):
                        continue
                    detail = await situation_compte_supplier(
                        supplier_id=s["supplier_id"], copropriete_id=copropriete_id,
                        start_date=start_date, end_date=end_date,
                    )
                    suppliers_detail.append(detail)
            pdf_bytes = build_balance_tiers_detailed_pdf(
                copropriete=copro,
                owners_detail=owners_detail,
                suppliers_detail=suppliers_detail,
                period_start=start_date or "",
                period_end=end_date or "",
                syndic_pdf_ctx=syndic_pdf_ctx,
            )
            filename = f"balance-tiers-detaillee-{scope}-{safe_name}-{suffix}.pdf"
        else:
            from pdf_balance_tiers import build_balance_tiers_pdf
            owners_data = {"owners": [], "total_debiteurs": 0.0, "total_crediteurs": 0.0}
            suppliers_data = {"suppliers": [], "total_debiteurs": 0.0, "total_crediteurs": 0.0}
            if want_owners:
                owners_data = await balance_tiers_owners(
                    copropriete_id=copropriete_id, start_date=start_date, end_date=end_date,
                )
            if want_suppliers:
                suppliers_data = await balance_tiers_suppliers(
                    request=request, copropriete_id=copropriete_id,
                    start_date=start_date, end_date=end_date,
                )
                # Calcule total_debiteurs/crediteurs pour fournisseurs (positif = a payer)
                sups = suppliers_data.get("suppliers", []) or []
                suppliers_data["total_crediteurs"] = round(sum(s["balance"] for s in sups if s["balance"] > 0), 2)
                suppliers_data["total_debiteurs"] = round(sum(abs(s["balance"]) for s in sups if s["balance"] < 0), 2)
            pdf_bytes = build_balance_tiers_pdf(
                copropriete=copro,
                owners_data=owners_data,
                suppliers_data=suppliers_data,
                period_start=start_date or "",
                period_end=end_date or "",
                syndic_pdf_ctx=syndic_pdf_ctx,
            )
            filename = f"balance-tiers-{scope}-{safe_name}-{suffix}.pdf"

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/balance-tiers/owners/{owner_id}")
    async def situation_compte_owner(
        owner_id: str,
        request: Request,
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        show_all: bool = False,
        group_by_owner: bool = True,
    ):
        """Situation de compte d'un proprietaire (basee sur le grand livre).
        Chinese walls strict. Filtre periode optionnel.

        Par defaut, masque les ecritures techniques de cloture "Annulation provisions
        appelees" : elles annulent comptablement les appels au moment de l'imputation
        des frais reels. Pour le proprietaire, c'est confus. On affiche uniquement :
          - les appels de fonds (VE)
          - les paiements (FI / banque)
          - l'imputation des frais reels a la cloture (OD-REG-CHRG)
          - le report a-nouveau (AN)
        Passer `show_all=true` pour voir aussi les annulations (mode comptable expert).

        iter90bv : `group_by_owner=true` (defaut) => fusionne les lignes portant sur
        plusieurs lots d'un meme proprietaire dans une meme ecriture (vue simplifiee,
        adaptee au proprietaire non-comptable). Passer `false` pour voir le detail
        par lot (utile pour audit)."""
        copropriete_id = _require_copro(copropriete_id, request)
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")

        tier_acc = (owner.get("tier_accounts") or {}).get(copropriete_id or "", {}) or {}
        acc_prov = tier_acc.get("provisions", "")
        acc_res = tier_acc.get("reserve", "")
        valid_accs = {a for a in (acc_prov, acc_res) if a}

        movements = []

        # 1) Lignes du grand livre (avec filtre periode)
        # SECURISATION : exclure contre-passations + ecritures extournees
        entry_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            entry_q["date"] = {}
            if start_date:
                entry_q["date"]["$gte"] = start_date
            if end_date:
                entry_q["date"]["$lte"] = _date_lte(end_date)
        _exclude_reversals(entry_q)
        entries = await db.journal_entries.find(entry_q, {"_id": 0}).to_list(100000)
        seen_lines = set()

        # ---- Reprise comptable : 2 sources combinees ----
        # (a) Toutes les ecritures AVANT start_date (cumul des exercices clos)
        # (b) Les ecritures AN dans la periode (OD d'ouverture du Bilan)
        # Les 2 sources sont agregees en UNE SEULE ligne synthetique au sommet,
        # datee du 1er jour de la periode visualisee (= start_date).
        # iter85d : EXCLURE les ODs de mutation (source_type='lot_mutation') de
        # l'agregation -> elles apparaissent comme lignes distinctes datees a
        # leur date d'origine (transparence comptable pour acheteur/vendeur).
        an_debit = 0.0
        an_credit = 0.0
        an_account = ""
        # Collecte les lignes de mutation pre-periode pour les ajouter en
        # mouvements distincts apres la ligne Reprise.
        pre_mutation_movements: list = []

        def _is_mutation_entry(entry: dict) -> bool:
            if (entry.get("source_type") or "") == "lot_mutation":
                return True
            ref = (entry.get("reference") or "")
            return ref.startswith("MUT-")

        # (a) Cumul des mouvements anterieurs au start_date
        if start_date:
            pre_q = {"copropriete_id": copropriete_id, "date": {"$lt": start_date}}
            _exclude_reversals(pre_q)
            pre_entries = await db.journal_entries.find(pre_q, {"_id": 0}).to_list(100000)
            for pe in pre_entries:
                is_mut = _is_mutation_entry(pe)
                for pln in pe.get("lines", []) or []:
                    p_acc = pln.get("account_number", "")
                    p_tpid = pln.get("third_party_id")
                    if p_acc not in valid_accs and p_tpid != owner_id:
                        continue
                    if is_mut:
                        # Ligne distincte avec date originale (pas dans Reprise)
                        pre_mutation_movements.append({
                            "date": pe.get("date", ""),
                            "description": f"[{pe.get('journal_type','OD')}] {(pln.get('line_description') or pe.get('description') or '').strip()}".strip(),
                            "debit": float(pln.get("debit", 0) or 0),
                            "credit": float(pln.get("credit", 0) or 0),
                            "type": (pe.get("journal_type") or "OD").lower(),
                            "reference": pe.get("reference") or pe.get("id", ""),
                            "account_number": p_acc,
                            "journal_type": pe.get("journal_type", "OD"),
                            "source_type": "lot_mutation",
                            "is_pre_period_mutation": True,
                        })
                        continue
                    an_debit += float(pln.get("debit", 0) or 0)
                    an_credit += float(pln.get("credit", 0) or 0)
                    if not an_account:
                        an_account = p_acc

        # (b) Ecritures AN dans la periode visualisee (l'OD d'ouverture pose le
        # 1er jour de la periode est consideree comme reprise et pas comme mouvement)
        for e in entries:
            if (e.get("journal_type") or "") != "AN":
                continue
            for idx, ln in enumerate(e.get("lines", []) or []):
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                if acc not in valid_accs and tpid != owner_id:
                    continue
                an_debit += float(ln.get("debit", 0) or 0)
                an_credit += float(ln.get("credit", 0) or 0)
                if not an_account:
                    an_account = acc
                # iter90cx : cle basee sur (entry_id, line_index) pour eviter
                # les faux doublons quand 2 lots ont la meme quotite (donc
                # meme debit). Le tuple (id, acc, debit, credit, tpid) precedent
                # dedupliquait par erreur des lignes legitimes.
                seen_lines.add((e.get("id"), idx))

        if abs(an_debit - an_credit) > 0.001:
            reprise_date = start_date or ""
            movements.append({
                "date": reprise_date,
                # iter90he : mention explicite "Decompte periode precedente"
                # demandee par l'utilisateur pour clarifier l'origine du solde.
                "description": f"Reprise comptable au {reprise_date} (Decompte periode precedente)" if reprise_date else "Reprise comptable (Decompte periode precedente)",
                "debit": round(an_debit, 2),
                "credit": round(an_credit, 2),
                "type": "reprise",
                "reference": "REPRISE",
                "account_number": an_account or (next(iter(valid_accs)) if valid_accs else ""),
                "journal_type": "AN",
                "is_reprise": True,
            })
        # iter85d : ajoute les ODs de mutation pre-periode en mouvements
        # distincts (datees a leur vraie date) juste apres la Reprise.
        for m in pre_mutation_movements:
            movements.append(m)
        for e in entries:
            for idx, ln in enumerate(e.get("lines", []) or []):
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                # Match si compte tiers de l'owner OU explicitement tagge owner
                if acc not in valid_accs and tpid != owner_id:
                    continue
                # iter90cx : cle basee sur (entry_id, line_index) pour eviter
                # de dedupliquer 2 lignes legitimes ayant meme (debit, credit,
                # tpid). Ex : reserve fund distribue sur 30 lots dont plusieurs
                # ont la meme quotite -> anciennement 3.6% des lignes etaient
                # skippees par erreur.
                key = (e.get("id"), idx)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                # Utiliser le libelle de la LIGNE en priorite (pour distinguer
                # provisions/reserve/roulement au sein d'une meme ecriture VE).
                line_desc = (ln.get("line_description") or "").strip()
                entry_desc = (e.get("description", "") or "").strip()
                final_desc = line_desc or entry_desc
                # Masquer la ligne "Annulation provisions appelees" de la vue copro
                # (sauf si show_all=true).
                is_provisions_cancel = (
                    "annulation provisions" in (entry_desc or "").lower()
                    or "annulation provisions" in (final_desc or "").lower()
                    or (e.get("reference", "") or "").startswith("OD-REG-PROV")
                )
                if is_provisions_cancel and not show_all:
                    continue
                # iter90eg : applique la meme regle que le PDF situation
                # (iter90dv) au libelle des mouvements FI pour clarifier la
                # balance des tiers.
                # - Compte tier proprietaire au CREDIT (paiement du proprio)
                #   -> "Paiement recu"
                # - Compte tier proprietaire au DEBIT (remboursement de l'ACP
                #   au proprio) -> "Votre remboursement"
                journal_type = e.get("journal_type", "") or ""
                is_owner_tier_line = (acc in valid_accs) or (tpid == owner_id)
                if journal_type == "FI" and is_owner_tier_line:
                    debit_amt = float(ln.get("debit", 0) or 0)
                    credit_amt = float(ln.get("credit", 0) or 0)
                    counterpart = (ln.get("counterparty_name") or ln.get("third_party_name") or "").strip()
                    ref = (e.get("reference") or "").strip()
                    detail_parts = [p for p in (counterpart, ref) if p]
                    detail_suffix = f" - {' / '.join(detail_parts)}" if detail_parts else ""
                    if credit_amt > 0.001:
                        final_desc = f"Paiement recu{detail_suffix}"
                    elif debit_amt > 0.001:
                        final_desc = f"Votre remboursement{detail_suffix}"
                movements.append({
                    "date": e.get("date", ""),
                    "description": f"[{e.get('journal_type','?')}] {final_desc}".strip(),
                    "debit": float(ln.get("debit", 0) or 0),
                    "credit": float(ln.get("credit", 0) or 0),
                    "type": e.get("journal_type", "OD").lower(),
                    "reference": e.get("reference", "") or e.get("id", ""),
                    "account_number": acc,
                    "journal_type": e.get("journal_type", ""),
                })

        # 2) Paiements bancaires reconnus par VCS mais non encore lettres
        txn_q = {}
        if copropriete_id:
            txn_q["copropriete_id"] = copropriete_id
        all_bank_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(100000)
        for txn in all_bank_txns:
            if txn.get("matched"):
                continue
            comm = txn.get("communication", "") or ""
            if not comm:
                continue
            clean = comm.replace("+", "").replace("/", "").replace(" ", "")
            if clean == owner.get("vcs_digits") or comm == owner.get("vcs_code"):
                movements.append({
                    "date": txn.get("date", ""),
                    "description": f"Paiement non lettre: {txn.get('counterparty_name','') or comm}",
                    "debit": 0,
                    "credit": abs(float(txn.get("amount", 0) or 0)),
                    "type": "paiement-unmatched",
                    "reference": txn.get("id", ""),
                    "account_number": acc_prov,
                    "journal_type": "BANK",
                })

        # iter90he : ligne REPRISE TOUJOURS en tete de la situation de
        # compte (avant meme la 1ere ecriture VE de la periode), quelle que
        # soit la date de l'AN. `is_reprise=True` -> tuple 0 avant 1.
        movements.sort(key=lambda x: (0 if x.get("is_reprise") else 1, x["date"], x.get("reference", "")))

        # iter90bv : fusion des lignes d'un meme proprietaire pour tous ses lots.
        # Un appel de fonds VE cree N lignes debit par lot ; on les cumule en 1.
        # `group_by_owner=false` pour audit / vue detaillee par lot.
        # iter90dw : passe self_owner_name pour differencier les mutations par counterpart
        if group_by_owner:
            movements = _group_movements_by_owner(
                movements, self_owner_name=(owner or {}).get("name", ""),
            )

        running = 0
        for m in movements:
            running += m["debit"] - m["credit"]
            m["running_balance"] = round(running, 2)

        # Soldes affiches = somme des mouvements VISIBLES
        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)

        # Solde COMPTABLE REEL : recharge toutes les ecritures (y compris annulations
        # masquees) pour rester aligne avec le bilan.
        all_debit = 0.0
        all_credit = 0.0
        hidden_count = 0
        if not show_all:
            for e in entries:
                for ln in e.get("lines", []) or []:
                    acc = ln.get("account_number", "")
                    tpid = ln.get("third_party_id")
                    if acc not in valid_accs and tpid != owner_id:
                        continue
                    all_debit += float(ln.get("debit", 0) or 0)
                    all_credit += float(ln.get("credit", 0) or 0)
                    # Detect hidden ones for transparency
                    entry_desc = (e.get("description", "") or "").lower()
                    line_desc = (ln.get("line_description") or "").lower()
                    if ("annulation provisions" in entry_desc
                            or "annulation provisions" in line_desc
                            or (e.get("reference", "") or "").startswith("OD-REG-PROV")):
                        hidden_count += 1
            real_balance = round(all_debit - all_credit, 2)
        else:
            real_balance = round(total_debit - total_credit, 2)

        # Detail par compte (sur les mouvements VISIBLES)
        prov_movs = [m for m in movements if m.get("account_number") == acc_prov]
        res_movs = [m for m in movements if m.get("account_number") == acc_res]

        return {
            "owner": owner,
            "account_provisions": acc_prov,
            "account_reserve": acc_res,
            "movements": movements,
            "hidden_lines_count": hidden_count,
            "provisions_debit": round(sum(m["debit"] for m in prov_movs), 2),
            "provisions_credit": round(sum(m["credit"] for m in prov_movs), 2),
            "reserve_debit": round(sum(m["debit"] for m in res_movs), 2),
            "reserve_credit": round(sum(m["credit"] for m in res_movs), 2),
            "total_debit": total_debit,
            "total_credit": total_credit,
            # `balance` reflete le solde COMPTABLE REEL (aligne avec le bilan)
            # meme si des lignes techniques sont masquees dans le listing visuel.
            "balance": real_balance,
            "status": "debiteur" if real_balance > 0.01 else ("crediteur" if real_balance < -0.01 else "solde"),
        }

    @router.get("/situation-compte/{owner_id}/pdf")
    async def situation_compte_pdf(
        owner_id: str,
        copropriete_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Genere un PDF "Situation de compte" detaille pour le proprietaire.
        Periode optionnelle [start_date, end_date]. Retourne application/pdf
        en streaming (filename : `situation-{owner_name}-{end_date|today}.pdf`)."""
        from fastapi.responses import Response as FastAPIResponse
        pdf_bytes, filename = await _build_situation_compte_pdf(
            db, owner_id, copropriete_id, start_date, end_date,
        )
        return FastAPIResponse(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # iter90hd : PDF de situation de compte pour UN fournisseur specifique
    # (equivalent de /situation-compte/{owner_id}/pdf pour les proprietaires).
    # Utilise le PDF "balance-tiers detaillee" avec suppliers_detail=[1 seul].
    @router.get("/balance-tiers/suppliers/{supplier_id}/pdf")
    async def balance_tiers_supplier_pdf(
        supplier_id: str,
        request: Request,
        copropriete_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """PDF situation de compte fournisseur (avec ligne REPRISE si presente).

        iter90hd : la logique de reprise (journal A-Nouveau agregge en 1 ligne
        au sommet) est deja implementee dans situation_compte_supplier, donc
        les soldes crediteurs de l'exercice precedent apparaissent naturellement
        dans le PDF genere.
        """
        from fastapi.responses import Response as FastAPIResponse
        from pdf_balance_tiers import build_balance_tiers_detailed_pdf
        from pdf_layout import resolve_syndic_pdf_context

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")
        supplier = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not supplier:
            raise HTTPException(404, "Fournisseur non trouve")

        detail = await situation_compte_supplier(
            supplier_id=supplier_id,
            copropriete_id=copropriete_id,
            start_date=start_date,
            end_date=end_date,
        )
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copro)
        pdf_bytes = build_balance_tiers_detailed_pdf(
            copropriete=copro,
            owners_detail=None,
            suppliers_detail=[detail],
            period_start=start_date or "",
            period_end=end_date or datetime.now(timezone.utc).date().isoformat(),
            syndic_pdf_ctx=syndic_pdf_ctx,
        )
        safe_name = (supplier.get("name", "fournisseur") or "fournisseur").replace(" ", "_").replace("/", "_")
        suffix = (end_date or datetime.now(timezone.utc).date().isoformat())
        filename = f"situation-fournisseur-{safe_name}-{suffix}.pdf"
        return FastAPIResponse(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- BALANCE DE TIERS FOURNISSEURS ----
    @router.get("/balance-tiers/suppliers")
    async def balance_tiers_suppliers(
        request: Request,
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Balance de tiers fournisseurs basee sur le grand livre. Chinese walls strict.

        Pour chaque fournisseur (fiche supplier ou nom non-reference), on aggrege
        toutes les ecritures journal_entries (AC, FI, OD, A-Nouveau) sur son compte
        tier 44000XXX. Resultat :
          - credit = factures recues + OD credit
          - debit = paiements emis + OD debit
          - balance = credit - debit (positif = a payer, negatif = trop paye)

        Affiche aussi les fournisseurs sans fiche (nom present uniquement sur les
        factures) - marques 'orphan=true'.
        """
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") if request else None
        if not copropriete_id or copropriete_id == "all":
            return {"suppliers": [], "total_a_payer": 0}

        # Charge fournisseurs
        # iter90is (Chinese Wall strict) : STRICTEMENT limite a `copropriete_id`
        # direct. Le partage cross-ACP via `tier_accounts` est definitivement
        # supprime -- chaque fiche fournisseur est locale a UNE ACP.
        suppliers = await db.suppliers.find(
            {"copropriete_id": copropriete_id},
            {"_id": 0},
        ).to_list(10000)
        # Charge journal entries de l'ACP + filtre periode
        # SECURISATION : exclure contre-passations + ecritures extournees
        je_q = {
            "copropriete_id": copropriete_id,
            "$and": [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ],
        }
        if start_date or end_date:
            je_q["date"] = {}
            if start_date:
                je_q["date"]["$gte"] = start_date
            if end_date:
                je_q["date"]["$lte"] = end_date
        _exclude_reversals(je_q)
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Pour le SOLDE on charge TOUTES les ecritures jusqu'a end_date (sans start_date)
        # car le solde du compte tier est CUMULATIF (anterieurs inclus).
        je_q_cumul = {
            "copropriete_id": copropriete_id,
            "$and": [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ],
        }
        if end_date:
            je_q_cumul["date"] = {"$lte": _date_lte(end_date)}
        _exclude_reversals(je_q_cumul)
        entries_cumul = await db.journal_entries.find(je_q_cumul, {"_id": 0}).to_list(100000)
        # Charge factures pour les fournisseurs orphelins
        inv_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            inv_q["date"] = {}
            if start_date:
                inv_q["date"]["$gte"] = start_date
            if end_date:
                inv_q["date"]["$lte"] = end_date
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)

        # Map supplier -> tier account
        supplier_by_id = {}
        tier_to_supplier = {}  # 44000XXX -> supplier
        # iter90gv : matching fallback par NOM du fournisseur.
        # Cas reel client (ACP Maria Test V1) : le journal AN d'ouverture
        # ecrit sur des comptes "4400001" / "4400015" (variantes anciennes)
        # avec `account_name="AG Insurance"` etc., SANS `third_party_id`. Ces
        # lignes ne matchaient ni par tpid ni par tier_account declare
        # -> l'AN etait totalement ignoree du solde, le fournisseur apparaissait
        # "trop-paye" alors que la reprise couvrait le paiement. Ce fallback
        # remonte le nom canonique du supplier pour ces cas.
        # IMPORTANT : le matching par nom est LIMITE aux suppliers de la
        # copropriete courante pour eviter le cross-contamination avec des
        # homonymes d'autres ACPs (bug decouvert : SRL Finlead / Finlead
        # Properties creaient des doublons quand on autorisait le matching global).
        from routes.suppliers import _norm_name_candidates
        import re as _re_supp
        _EXT_PREFIX_RE = _re_supp.compile(r"^F\d{3,5}\s*-\s*", _re_supp.IGNORECASE)

        def _strip_ref_prefix(name: str) -> str:
            """Retire le prefixe 'FXXXX - ' des noms importes depuis un
            systeme externe pour permettre le matching entre 'F0110 - Engie'
            (fiche importee) et 'Engie' (account_name des ecritures AN d'ouverture)."""
            return _EXT_PREFIX_RE.sub("", (name or "").strip())

        def _all_name_candidates(name: str) -> set:
            """Candidats normaux + candidats sans prefixe externe FXXXX."""
            cands = set(_norm_name_candidates(name))
            stripped = _strip_ref_prefix(name)
            if stripped and stripped != name:
                cands |= _norm_name_candidates(stripped)
            return cands

        name_to_supplier: dict = {}  # nom_normalise -> supplier (LOCAL a l'ACP)
        for s in suppliers:
            supplier_by_id[s["id"]] = s
            tier_acc = ((s.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
            if tier_acc:
                tier_to_supplier[tier_acc] = s
            # iter90is (Chinese Wall strict) : les suppliers sont deja
            # filtres par copropriete_id direct au fetch (plus de $or sur
            # tier_accounts). Le check ci-dessous est donc devenu un noop
            # defensif -- on l'ecrit simplement pour rester lisible.
            if s.get("copropriete_id") == copropriete_id:
                for cand in _all_name_candidates(s.get("name", "")):
                    name_to_supplier.setdefault(cand, s)

        def _resolve_supplier(ln: dict):
            """Trouve le supplier attribuable a une ligne JE (priorite : tpid ->
            tier_account -> nom dans account_name). Renvoie None si aucun match.
            """
            acc = ln.get("account_number", "")
            tpid = ln.get("third_party_id")
            if tpid and tpid in supplier_by_id:
                return supplier_by_id[tpid]
            if acc in tier_to_supplier:
                return tier_to_supplier[acc]
            # Fallback iter90gv : matcher par account_name (contient nom du supplier)
            if acc.startswith("44"):
                acc_name = (ln.get("account_name") or "").strip()
                if acc_name:
                    cands = _all_name_candidates(acc_name)
                    for c in cands:
                        s = name_to_supplier.get(c)
                        if s:
                            return s
            return None

        # Aggregate by supplier_id (third_party_id) OR by 44000XXX account
        per_supplier = {}  # supplier_id -> {debit, credit, name, tier, invoices_count}
        for e in entries:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                # iter90gv : accepter TOUS les comptes commencant par "44"
                # (44XXXXX = fournisseurs PCMN, 5 a 8 chars, tolerant aux
                # variantes historiques 4400XXX vs 44000XXX).
                if not acc.startswith("44"):
                    continue
                sup = _resolve_supplier(ln)
                if not sup:
                    continue
                sid = sup["id"]
                tier_acc = ((sup.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", acc)
                d = per_supplier.setdefault(sid, {
                    "supplier_id": sid,
                    "supplier_name": sup.get("name", ""),
                    "vat_number": sup.get("vat_number", "") or sup.get("bce_number", ""),
                    "tier_account": tier_acc,
                    "orphan": False,
                    "debit": 0.0, "credit": 0.0,
                    "cumul_debit": 0.0, "cumul_credit": 0.0,
                    "invoice_count": 0,
                })
                d["debit"] += float(ln.get("debit", 0) or 0)
                d["credit"] += float(ln.get("credit", 0) or 0)

        # SOLDE CUMULATIF : aggregation sur entries_cumul (toutes ecritures jusqu'a end_date)
        for e in entries_cumul:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                if not acc.startswith("44"):
                    continue
                sup = _resolve_supplier(ln)
                if not sup:
                    continue
                sid = sup["id"]
                tier_acc = ((sup.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", acc)
                d = per_supplier.setdefault(sid, {
                    "supplier_id": sid,
                    "supplier_name": sup.get("name", ""),
                    "vat_number": sup.get("vat_number", "") or sup.get("bce_number", ""),
                    "tier_account": tier_acc,
                    "orphan": False,
                    "debit": 0.0, "credit": 0.0,
                    "cumul_debit": 0.0, "cumul_credit": 0.0,
                    "invoice_count": 0,
                })
                d["cumul_debit"] += float(ln.get("debit", 0) or 0)
                d["cumul_credit"] += float(ln.get("credit", 0) or 0)

        # Aggregate by supplier_id (third_party_id) OR by 44000XXX account
        per_supplier = {}  # supplier_id -> {debit, credit, name, tier, invoices_count}
        for e in entries:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                # iter90gv : accepter TOUS les comptes commencant par "44"
                # (44XXXXX = fournisseurs PCMN, 5 a 8 chars, tolerant aux
                # variantes historiques 4400XXX vs 44000XXX).
                if not acc.startswith("44"):
                    continue
                sup = _resolve_supplier(ln)
                if not sup:
                    continue
                sid = sup["id"]
                tier_acc = ((sup.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", acc)
                d = per_supplier.setdefault(sid, {
                    "supplier_id": sid,
                    "supplier_name": sup.get("name", ""),
                    "vat_number": sup.get("vat_number", "") or sup.get("bce_number", ""),
                    "tier_account": tier_acc,
                    "orphan": False,
                    "debit": 0.0, "credit": 0.0,
                    "cumul_debit": 0.0, "cumul_credit": 0.0,
                    "invoice_count": 0,
                })
                d["debit"] += float(ln.get("debit", 0) or 0)
                d["credit"] += float(ln.get("credit", 0) or 0)

        # SOLDE CUMULATIF : aggregation sur entries_cumul (toutes ecritures jusqu'a end_date)
        for e in entries_cumul:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                if not acc.startswith("44"):
                    continue
                sup = _resolve_supplier(ln)
                if not sup:
                    continue
                sid = sup["id"]
                tier_acc = ((sup.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", acc)
                d = per_supplier.setdefault(sid, {
                    "supplier_id": sid,
                    "supplier_name": sup.get("name", ""),
                    "vat_number": sup.get("vat_number", "") or sup.get("bce_number", ""),
                    "tier_account": tier_acc,
                    "orphan": False,
                    "debit": 0.0, "credit": 0.0,
                    "cumul_debit": 0.0, "cumul_credit": 0.0,
                    "invoice_count": 0,
                })
                d["cumul_debit"] += float(ln.get("debit", 0) or 0)
                d["cumul_credit"] += float(ln.get("credit", 0) or 0)

        # Compte les factures par fournisseur
        from collections import defaultdict
        invs_by_name = defaultdict(list)
        for inv in invoices:
            name = (inv.get("supplier", "") or "").strip().lower()
            if name:
                invs_by_name[name].append(inv)

        # Marque le nb de factures pour les fournisseurs presents dans per_supplier
        for sid, d in per_supplier.items():
            sname_l = d["supplier_name"].strip().lower()
            d["invoice_count"] = len(invs_by_name.get(sname_l, []))

        # Identifier les fournisseurs "orphelins" (nom present dans factures mais aucune ecriture aggregee)
        known_names = {d["supplier_name"].strip().lower() for d in per_supplier.values()}
        for name_l, invs in invs_by_name.items():
            if name_l in known_names:
                continue
            display = invs[0].get("supplier", "")
            invoiced = sum(inv.get("total_amount", 0) for inv in invs)
            # Pour les orphelins on tombe en mode fallback : Cr = factures, debit = 0
            # (pas d'ecriture donc pas de tier - balance = total facture impaye)
            per_supplier[f"orphan-{name_l}"] = {
                "supplier_id": "",
                "supplier_name": display,
                "vat_number": "",
                "tier_account": "",
                "orphan": True,
                "debit": 0.0,
                "credit": float(invoiced),
                "invoice_count": len(invs),
            }

        result = []
        for d in per_supplier.values():
            # Solde CUMULATIF (toutes ecritures jusqu'a end_date)
            cumul_credit = d.get("cumul_credit", 0.0) or d["credit"]
            cumul_debit = d.get("cumul_debit", 0.0) or d["debit"]
            balance = round(cumul_credit - cumul_debit, 2)
            result.append({
                "supplier_id": d["supplier_id"],
                "supplier_name": d["supplier_name"],
                "vat_number": d["vat_number"],
                "tier_account": d["tier_account"],
                "orphan": d["orphan"],
                "invoice_count": d["invoice_count"],
                # Facture/Paye = mouvements de la PERIODE
                "total_invoiced": round(d["credit"], 2),
                "total_paid": round(d["debit"], 2),
                # Compte = mouvements cumulatifs du compte tier (grand livre)
                "account_debit": round(cumul_debit, 2),
                "account_credit": round(cumul_credit, 2),
                "account_balance": balance,
                # Solde affiche = solde CUMULATIF du compte tier
                "balance": balance,
                "status": "crediteur" if balance > 0.01 else ("debiteur" if balance < -0.01 else "solde"),
            })
        result.sort(key=lambda r: r["supplier_name"].lower())
        total_a_payer = round(sum(r["balance"] for r in result if r["balance"] > 0), 2)
        return {"suppliers": result, "total_a_payer": total_a_payer}

    @router.get("/balance-tiers/suppliers/{supplier_id}")
    async def situation_compte_supplier(
        supplier_id: str,
        copropriete_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Situation de compte fournisseur basee sur le grand livre.
        Aggrege toutes les ecritures (AC/FI/OD/A-Nouveau) sur 44000XXX du fournisseur.
        Filtre periode optionnel : si start_date est fourni, une ligne synthetique
        "Reprise comptable" est inseree au sommet avec le solde cumule des
        mouvements anterieurs (typiquement l'OD d'ouverture du Bilan)."""
        supplier = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not supplier:
            raise HTTPException(404, "Fournisseur non trouve")

        tier_acc = ""
        if copropriete_id:
            tier_acc = ((supplier.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")

        # iter90fe : elargit le matching pour capturer les ecritures AC
        # historiques dont le third_party_id / account ne matche pas la
        # fiche canonique actuelle (invoices creees avec un nom libre
        # avant l'existence de la fiche).
        # 1. Set des noms normalises pointant vers ce fournisseur (nom
        #    complet + fragments parentheses).
        # 2. Set des ids d'invoices dont supplier_id == supplier_id OU
        #    dont supplier normalise matche.
        from routes.suppliers import _norm_name, _norm_name_candidates
        supplier_candidates = _norm_name_candidates(supplier.get("name", ""))
        matching_invoice_ids: set[str] = set()
        if copropriete_id and supplier_candidates:
            inv_q: dict = {"copropriete_id": copropriete_id}
            inv_cur = db.invoices.find(inv_q, {"_id": 0, "id": 1, "supplier": 1, "supplier_id": 1})
            async for inv in inv_cur:
                if inv.get("supplier_id") == supplier_id:
                    matching_invoice_ids.add(inv["id"])
                    continue
                inv_candidates = _norm_name_candidates(inv.get("supplier", ""))
                if inv_candidates & supplier_candidates:
                    matching_invoice_ids.add(inv["id"])

        def _line_matches(ln, entry=None):
            acc = ln.get("account_number", "")
            tpid = ln.get("third_party_id")
            if tpid == supplier_id:
                return True
            if tier_acc and acc == tier_acc:
                return True
            # iter90fe : fallback historique - ligne appartenant a une
            # ecriture AC dont le source_invoice_id pointe vers une
            # facture de ce fournisseur (matching par nom normalise).
            if entry is not None and matching_invoice_ids:
                if (entry.get("source_invoice_id") or "") in matching_invoice_ids:
                    # iter90gs : ne prendre QUE la ligne compte tier fournisseur
                    # (44XXXX PCMN). Ancien filtre `credit > 0` remontait par
                    # erreur la ligne de charge (61000) pour une NC ecrite en
                    # DEBIT compte tier / CREDIT charge -> creait 2 lignes
                    # inversees et cassait la situation de compte (bug
                    # utilisateur du 16/07 : NC visible au CREDIT au lieu du
                    # DEBIT). Filtrer par prefixe 44 garantit qu'on prend le
                    # bon cote de l'ecriture, quel que soit le sens.
                    if acc.startswith("44"):
                        return True
            return False

        movements = []
        entry_q = {}
        if copropriete_id:
            entry_q["copropriete_id"] = copropriete_id

        # ----- Mouvements de la periode -----
        period_q = dict(entry_q)
        if start_date or end_date:
            period_q["date"] = {}
            if start_date:
                period_q["date"]["$gte"] = start_date
            if end_date:
                period_q["date"]["$lte"] = end_date
        _exclude_reversals(period_q)
        entries = await db.journal_entries.find(period_q, {"_id": 0}).to_list(100000)
        seen = set()

        # ---- Reprise comptable : 2 sources combinees ----
        # (a) Toutes les ecritures AVANT start_date (cumul des exercices clos)
        # (b) Les ecritures AN dans la periode (OD d'ouverture du Bilan)
        # iter85d : EXCLURE les ODs de mutation (source_type='lot_mutation') de
        # l'agregation -> elles apparaissent comme lignes distinctes (par
        # securite, meme si en pratique les mutations ne touchent que les
        # comptes proprietaire 4100xxx).
        an_debit = 0.0
        an_credit = 0.0
        an_account = ""
        pre_mutation_movements_sup: list = []

        def _is_mutation_entry_sup(entry: dict) -> bool:
            if (entry.get("source_type") or "") == "lot_mutation":
                return True
            ref = (entry.get("reference") or "")
            return ref.startswith("MUT-")

        # (a) Cumul des mouvements anterieurs au start_date
        if start_date:
            pre_q = dict(entry_q)
            pre_q["date"] = {"$lt": start_date}
            _exclude_reversals(pre_q)
            pre_entries = await db.journal_entries.find(pre_q, {"_id": 0}).to_list(100000)
            for pe in pre_entries:
                is_mut = _is_mutation_entry_sup(pe)
                for pln in pe.get("lines", []) or []:
                    if not _line_matches(pln, pe):
                        continue
                    if is_mut:
                        pre_mutation_movements_sup.append({
                            "date": pe.get("date", ""),
                            "description": f"[{pe.get('journal_type','OD')}] {(pln.get('line_description') or pe.get('description') or '').strip()}".strip(),
                            "debit": float(pln.get("debit", 0) or 0),
                            "credit": float(pln.get("credit", 0) or 0),
                            "type": (pe.get("journal_type") or "OD").lower(),
                            "reference": pe.get("reference") or pe.get("id", ""),
                            "account_number": pln.get("account_number", ""),
                            "journal_type": pe.get("journal_type", "OD"),
                            "source_type": "lot_mutation",
                            "is_pre_period_mutation": True,
                        })
                        continue
                    an_debit += float(pln.get("debit", 0) or 0)
                    an_credit += float(pln.get("credit", 0) or 0)
                    if not an_account:
                        an_account = pln.get("account_number", "")

        # (b) Ecritures AN dans la periode - AGREGEES en 1 ligne "REPRISE"
        # iter90gu : regle utilisateur explicite (16/07/2026) :
        #   "il faut ajouter une ligne dans les situations de compte permettant
        #    de mentionner le montant de la reprise (journal A-Nouveau) et
        #    faire le calcul sur cette base"
        # -> les AN (peu importe qu'elles soient AVANT ou DANS la periode)
        # sont TOUJOURS agregees en UNE SEULE ligne "REPRISE" en tete de la
        # situation de compte, portant :
        #   - la date de la 1ere AN (date d'ouverture d'exercice reelle)
        #   - la reference du journal A-Nouveau (AN-YYYY-XXX)
        #   - le solde d'ouverture (debit + credit cumules des AN)
        # Le running_balance PART de ce montant de reprise (base de calcul).
        # Immuabilite du journal AN : cette ligne n'est jamais editable par le
        # syndic - elle refletee 1:1 le contenu du journal A-Nouveau.
        reprise_ref = "REPRISE"
        reprise_dates: list = []
        an_refs: list = []
        for e in entries:
            if (e.get("journal_type") or "") != "AN":
                continue
            an_ref = e.get("reference") or e.get("id", "")
            matched_this_entry = False
            for idx, ln in enumerate(e.get("lines", []) or []):
                if not _line_matches(ln, e):
                    continue
                an_debit += float(ln.get("debit", 0) or 0)
                an_credit += float(ln.get("credit", 0) or 0)
                if not an_account:
                    an_account = ln.get("account_number", "")
                # iter90cx : dedup par (entry_id, line_index) - marque comme
                # deja consomme pour eviter double comptabilisation dans la
                # boucle "mouvements de la periode" plus bas.
                seen.add((e.get("id"), idx))
                matched_this_entry = True
            if matched_this_entry:
                if e.get("date"):
                    reprise_dates.append(e.get("date"))
                if an_ref and an_ref not in an_refs:
                    an_refs.append(an_ref)
        # Choix de la date de la ligne REPRISE : la plus ancienne AN (debut
        # d'exercice reel) prime sur le start_date qui n'est qu'un filtre UI.
        if reprise_dates:
            reprise_dates.sort()
            reprise_date = reprise_dates[0]
        else:
            reprise_date = start_date or ""
        # Reference : concat des AN-YYYY-XXX (utile pour audit)
        if an_refs:
            reprise_ref = " + ".join(an_refs[:3]) + (" (...)" if len(an_refs) > 3 else "")
        # Description enrichie mentionnant explicitement "Journal A-Nouveau"
        # (mot cle metier + rappel de l'immuabilite pour le syndic).
        # iter90he : mention "Decompte periode precedente" demandee par
        # l'utilisateur pour clarifier l'origine du solde reporte.
        if reprise_date:
            reprise_desc = f"Reprise comptable au {reprise_date} (Decompte periode precedente - Journal A-Nouveau immuable)"
        else:
            reprise_desc = "Reprise comptable (Decompte periode precedente - Journal A-Nouveau immuable)"
        if abs(an_debit) > 0.001 or abs(an_credit) > 0.001:
            movements.append({
                "date": reprise_date,
                "description": reprise_desc,
                "debit": round(an_debit, 2),
                "credit": round(an_credit, 2),
                "type": "reprise",
                "reference": reprise_ref,
                "account_number": an_account or tier_acc,
                "journal_type": "AN",
                "is_reprise": True,
            })
        for m in pre_mutation_movements_sup:
            movements.append(m)

        for e in entries:
            for idx, ln in enumerate(e.get("lines", []) or []):
                if not _line_matches(ln, e):
                    continue
                # iter90cx : dedup par (entry_id, line_index)
                key = (e.get("id"), idx)
                if key in seen:
                    continue
                seen.add(key)
                line_desc = (ln.get("line_description") or "").strip()
                entry_desc = (e.get("description", "") or "").strip()
                final_desc = line_desc or entry_desc
                movements.append({
                    "date": e.get("date", ""),
                    "description": f"[{e.get('journal_type','?')}] {final_desc}".strip(),
                    "debit": float(ln.get("debit", 0) or 0),
                    "credit": float(ln.get("credit", 0) or 0),
                    "type": e.get("journal_type", "OD").lower(),
                    "reference": e.get("reference", "") or e.get("id", ""),
                    "account_number": ln.get("account_number", ""),
                    "journal_type": e.get("journal_type", ""),
                })

        # iter90he : ligne REPRISE TOUJOURS en tete de la situation de
        # compte (avant meme la 1ere ecriture VE de la periode), quelle que
        # soit la date. `is_reprise=True` -> tuple 0 avant `is_reprise=False` -> 1.
        movements.sort(key=lambda x: (0 if x.get("is_reprise") else 1, x["date"], x.get("reference", "")))
        # Cote fournisseur : credit a payer = positif (convention NextGe Copro)
        running = 0.0
        for m in movements:
            running += m["credit"] - m["debit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)

        return {
            "supplier": supplier,
            "tier_account": tier_acc,
            "movements": movements,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": round(total_credit - total_debit, 2),
            "status": "crediteur" if total_credit > total_debit + 0.01 else ("debiteur" if total_debit > total_credit + 0.01 else "solde"),
        }

    @router.post("/balance-tiers/lettrer-supplier")
    async def lettrer_supplier(payload: dict, request: Request):
        """Manual reconciliation : link an 'orphan' supplier name to an existing
        supplier in DB and regenerate missing journal entries (AC) for that
        supplier's invoices.

        Body : { copropriete_id, orphan_name, supplier_id }
        Effects :
        1. Update `supplier.tier_accounts[copro_id].main` to a 4400xxx code (built
           from auxiliary_code if not yet set : F0606 -> 4400606).
        2. For each invoice with supplier matching orphan_name and NO existing
           journal entry, create an AC double-entry (debit charges 6xxx, credit
           supplier tier 4400xxx) so the balance-tiers can aggregate the supplier.
        3. Backfill supplier_id on the invoices if missing.
        """
        copro_id = (payload.get("copropriete_id") or "").strip()
        orphan_name = (payload.get("orphan_name") or "").strip()
        supplier_id = (payload.get("supplier_id") or "").strip()
        if not (copro_id and orphan_name and supplier_id):
            raise HTTPException(400, "copropriete_id, orphan_name et supplier_id requis")

        # Load supplier and validate it belongs to user scope (chinese wall)
        sup = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not sup:
            raise HTTPException(404, "Fournisseur introuvable")

        # Resolve / ensure the tier account 4400XXX for this supplier in this ACP
        tier = ((sup.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
        if not tier:
            aux = (sup.get("auxiliary_code") or "").strip().upper()
            if aux.startswith("F") and len(aux) >= 5:
                tier = "4400" + aux[1:].zfill(3)
            else:
                # Generate a fresh 44000XXX code (next available)
                existing_codes = await db.pcmn_accounts.distinct("number", {"copropriete_id": copro_id, "number": {"$regex": "^44000"}})
                next_n = 1
                used = {int(c[-3:]) for c in existing_codes if c[-3:].isdigit()}
                while next_n in used:
                    next_n += 1
                tier = f"44000{next_n:03d}"
            await db.suppliers.update_one(
                {"id": supplier_id},
                {"$set": {f"tier_accounts.{copro_id}.main": tier}},
            )

        # Ensure PCMN account exists
        existing = await db.pcmn_accounts.find_one({"copropriete_id": copro_id, "number": tier})
        if not existing:
            await db.pcmn_accounts.insert_one({
                "id": str(uuid.uuid4()),
                "number": tier,
                "label": sup.get("name") or tier,
                "category": "44",
                "is_tier": True,
                "copropriete_id": copro_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        # Find invoices matching orphan_name in this ACP
        cur = db.invoices.find({
            "copropriete_id": copro_id,
            "supplier": {"$regex": f"^{orphan_name}$", "$options": "i"},
        }, {"_id": 0})
        invs = await cur.to_list(1000)

        je_created = 0
        invoices_relinked = 0
        for inv in invs:
            inv_id = inv.get("id")
            # Backfill supplier_id if missing or different
            if inv.get("supplier_id") != supplier_id:
                await db.invoices.update_one(
                    {"id": inv_id},
                    {"$set": {"supplier_id": supplier_id}},
                )
                invoices_relinked += 1
            # Check if this invoice already has a journal entry
            existing_je = await db.journal_entries.find_one({
                "copropriete_id": copro_id,
                "source_invoice_id": inv_id,
            })
            if existing_je:
                continue
            # Create AC journal entry
            account_num = (inv.get("account_number") or "").strip()
            total = float(inv.get("total_amount") or 0)
            if not account_num or total <= 0:
                continue
            je_id = str(uuid.uuid4())
            await db.journal_entries.insert_one({
                "id": je_id,
                "journal_type": "AC",
                "date": inv.get("date") or "",
                "reference": inv.get("internal_reference") or inv.get("number") or "",
                "description": f"{sup.get('name','')} - {(inv.get('description') or '').strip()}".strip(" -"),
                "lines": [
                    {
                        "account_number": account_num,
                        "account_name": "",
                        "debit": total,
                        "credit": 0.0,
                        "description": (inv.get("description") or "").strip(),
                        "occupant_pct": float(inv.get("occupant_pct") or 100.0),
                        "proprietaire_pct": float(inv.get("proprietaire_pct") or 0.0),
                    },
                    {
                        "account_number": tier,
                        "account_name": sup.get("name", ""),
                        "debit": 0.0,
                        "credit": total,
                        "description": f"DA {inv.get('internal_reference','')}",
                        "third_party_id": supplier_id,
                        "third_party_type": "supplier",
                        "occupant_pct": None,
                        "proprietaire_pct": None,
                    },
                ],
                "total_debit": total,
                "total_credit": total,
                "copropriete_id": copro_id,
                "source_invoice_id": inv_id,
                "manual_reconciled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            je_created += 1

        return {
            "supplier_id": supplier_id,
            "supplier_name": sup.get("name", ""),
            "tier_account": tier,
            "invoices_matched": len(invs),
            "invoices_relinked": invoices_relinked,
            "journal_entries_created": je_created,
        }

    # ---- DIAGNOSTIC: Debug balance owner ----
    @router.get("/debug-owner-balance")
    async def debug_owner_balance(
        request: Request,
        copropriete_id: Optional[str] = None,
        search: Optional[str] = None,
        account: Optional[str] = None,
    ):
        """Diagnostic complet pour un proprietaire manquant de la balance.

        Usage :
          /api/reports/debug-owner-balance?copropriete_id=XXX&search=matexi
          /api/reports/debug-owner-balance?copropriete_id=XXX&account=41010986
        """
        copropriete_id = copropriete_id or request.headers.get("x-copropriete-id", "")
        if not copropriete_id:
            return {"error": "copropriete_id requis"}

        result = {"copropriete_id": copropriete_id, "steps": []}

        # 1. Trouver le proprietaire
        owner = None
        if search:
            owner = await db.owners.find_one(
                {"name": {"$regex": search, "$options": "i"}},
                {"_id": 0},
            )
        if not owner and account:
            # Chercher un owner dont tier_accounts contient ce compte
            all_owners = await db.owners.find({}, {"_id": 0}).to_list(10000)
            for o in all_owners:
                tier = (o.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
                if tier.get("provisions") == account or tier.get("reserve") == account:
                    owner = o
                    break
        if owner:
            oid = owner["id"]
            tier = (owner.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
            result["owner"] = {
                "id": oid,
                "name": owner.get("name"),
                "tier_accounts_for_acp": tier,
                "vcs_code": owner.get("vcs_code", ""),
            }
            result["steps"].append(f"Owner trouve: {owner.get('name')} (id={oid})")
        else:
            result["steps"].append(f"AUCUN owner trouve (search={search}, account={account})")
            # Lister les owners dont le nom ressemble
            if search:
                similar = await db.owners.find(
                    {"name": {"$regex": search[:3], "$options": "i"}},
                    {"_id": 0, "id": 1, "name": 1},
                ).to_list(50)
                result["similar_owners"] = [{"id": o["id"], "name": o["name"]} for o in similar]
            return result

        # 2. Verifier lots
        lots = await db.lots.find({"copropriete_id": copropriete_id}, {"_id": 0}).to_list(10000)
        owner_lots = [lt for lt in lots if lt.get("owner_id") == oid or oid in (lt.get("owner_ids") or [])]
        result["lots"] = [{"id": lt.get("id"), "ref": lt.get("reference", "")} for lt in owner_lots]
        result["steps"].append(f"Lots dans cette ACP: {len(owner_lots)}")

        # 3. Verifier journal entries avec third_party_id
        je_with_tp = await db.journal_entries.find(
            {"copropriete_id": copropriete_id, "lines.third_party_id": oid},
            {"_id": 0, "id": 1, "journal_type": 1, "date": 1, "description": 1, "lines": 1},
        ).to_list(1000)
        tp_lines = []
        for e in je_with_tp:
            for ln in e.get("lines", []):
                if ln.get("third_party_id") == oid:
                    tp_lines.append({
                        "entry_id": e["id"],
                        "journal_type": e.get("journal_type"),
                        "date": e.get("date"),
                        "account": ln.get("account_number"),
                        "debit": ln.get("debit", 0),
                        "credit": ln.get("credit", 0),
                    })
        result["lines_with_third_party_id"] = tp_lines
        result["steps"].append(f"Lignes avec third_party_id={oid}: {len(tp_lines)}")

        # 4. Verifier journal entries avec le compte cible
        target_acc = account or tier.get("provisions", "") or ""
        if not target_acc and account:
            target_acc = account
        result["target_account"] = target_acc

        if target_acc:
            je_with_acc = await db.journal_entries.find(
                {"copropriete_id": copropriete_id, "lines.account_number": target_acc},
                {"_id": 0, "id": 1, "journal_type": 1, "date": 1, "description": 1, "lines": 1,
                 "is_opening_balance": 1},
            ).to_list(1000)
            acc_lines = []
            for e in je_with_acc:
                for ln in e.get("lines", []):
                    if ln.get("account_number") == target_acc:
                        acc_lines.append({
                            "entry_id": e["id"],
                            "journal_type": e.get("journal_type"),
                            "date": e.get("date"),
                            "is_opening_balance": e.get("is_opening_balance", False),
                            "account": ln.get("account_number"),
                            "third_party_id": ln.get("third_party_id", ""),
                            "debit": ln.get("debit", 0),
                            "credit": ln.get("credit", 0),
                        })
            result["lines_on_account"] = acc_lines
            result["steps"].append(f"Lignes sur compte {target_acc}: {len(acc_lines)}")

            # Diagnostic: les lignes AN sans is_opening_balance
            an_no_open = [l for l in acc_lines if l["journal_type"] == "AN" and not l.get("is_opening_balance")]
            if an_no_open:
                result["steps"].append(f"PROBLEME: {len(an_no_open)} ecriture(s) AN SANS is_opening_balance=True -> EXCLUES de la balance!")
                result["an_entries_missing_opening_flag"] = an_no_open

            # Diagnostic: lignes sans third_party_id
            no_tp = [l for l in acc_lines if not l.get("third_party_id")]
            if no_tp:
                result["steps"].append(f"PROBLEME: {len(no_tp)} ligne(s) sur {target_acc} SANS third_party_id")
                result["lines_missing_third_party_id"] = no_tp

        # 5. Verifier mutations
        muts = await db.mutations.find(
            {"copropriete_id": copropriete_id,
             "$or": [{"from_owner_id": oid}, {"to_owner_id": oid}]},
            {"_id": 0},
        ).to_list(100)
        result["mutations"] = len(muts)

        # 6. Calculer le solde attendu
        total_d = sum(l.get("debit", 0) for l in tp_lines)
        total_c = sum(l.get("credit", 0) for l in tp_lines)
        result["balance_via_third_party_id"] = round(total_d - total_c, 2)
        if target_acc:
            acc_d = sum(l.get("debit", 0) for l in acc_lines)
            acc_c = sum(l.get("credit", 0) for l in acc_lines)
            result["balance_via_account_number"] = round(acc_d - acc_c, 2)

        result["steps"].append("Diagnostic termine")
        return result

    @router.get("/debug-bilan-exclusions")
    async def debug_bilan_exclusions(
        request: Request,
        copropriete_id: Optional[str] = None,
        target_amount: float = 4.0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Diagnostic chirurgical : trouve toutes les ecritures d'un montant cible
        et verifie si chacune est INCLUSE ou EXCLUE du calcul du bilan (compte 499).

        Usage :
          /api/reports/debug-bilan-exclusions?copropriete_id=XXX&target_amount=4.0&date_from=2026-01-01&date_to=2026-12-31
        """
        copropriete_id = _require_copro(copropriete_id, request)

        # 1) Charger TOUTES les ecritures de l'ACP (sans aucun filtre)
        raw_q = {"copropriete_id": copropriete_id}
        if date_from:
            raw_q.setdefault("date", {})["$gte"] = date_from
        if date_to:
            raw_q.setdefault("date", {})["$lte"] = _date_lte(date_to)
        all_entries = await db.journal_entries.find(raw_q, {"_id": 0}).to_list(100000)

        # 2) Filtrer les ecritures ayant une ligne avec le montant cible
        tolerance = 0.01
        matching_entries = []
        for e in all_entries:
            for ln in e.get("lines", []):
                d = float(ln.get("debit", 0) or 0)
                c = float(ln.get("credit", 0) or 0)
                if abs(d - target_amount) < tolerance or abs(c - target_amount) < tolerance:
                    matching_entries.append(e)
                    break
        # Aussi chercher 12.00 (3x4)
        for e in all_entries:
            if e in matching_entries:
                continue
            for ln in e.get("lines", []):
                d = float(ln.get("debit", 0) or 0)
                c = float(ln.get("credit", 0) or 0)
                if abs(d - 12.0) < tolerance or abs(c - 12.0) < tolerance:
                    matching_entries.append(e)
                    break

        # 3) Pour chaque ecriture, simuler les filtres du bilan
        # Charger les valid_ac_je_ids (factures liees)
        inv_q = {"copropriete_id": copropriete_id}
        if date_to:
            inv_q["date"] = {"$lte": _date_lte(date_to)}
        invoices = await db.invoices.find(inv_q, {"_id": 0, "journal_entry_id": 1}).to_list(50000)
        valid_ac_je_ids = {inv["journal_entry_id"] for inv in invoices if inv.get("journal_entry_id")}

        # Dedup FI signature
        fi_dedup_excluded = set()
        try:
            deduped = _dedup_duplicate_auto_fi_entries(all_entries)
            deduped_ids = {e.get("id") for e in deduped}
            fi_dedup_excluded = {e.get("id") for e in all_entries if e.get("id") not in deduped_ids}
        except Exception:
            pass

        results = []
        for e in matching_entries:
            eid = e.get("id", "")
            jtype = e.get("journal_type", "")
            exclusion_reasons = []
            included = True

            # Filtre 1: AN sans is_opening_balance
            if jtype == "AN" and not e.get("is_opening_balance"):
                exclusion_reasons.append("AN sans is_opening_balance=True (exclue)")
                included = False

            # Filtre 2: reversed / is_reversal
            if e.get("reversed"):
                exclusion_reasons.append("reversed=True (extournee)")
                included = False
            if e.get("is_reversal"):
                exclusion_reasons.append("is_reversal=True (contre-passation)")
                included = False

            # Filtre 3: is_regularization
            if e.get("is_regularization"):
                exclusion_reasons.append("is_regularization=True")
                included = False

            # Filtre 4: reference OD-REG- ou EXT-
            ref = e.get("reference", "")
            if ref.startswith("OD-REG-") or ref.startswith("EXT-"):
                exclusion_reasons.append(f"reference={ref} (OD-REG-/EXT- prefix)")
                included = False

            # Filtre 5: dedup FI auto
            if eid in fi_dedup_excluded:
                exclusion_reasons.append("FI auto-generee en doublon (dedup FI)")
                included = False

            # Filtre 6: AC orpheline (pas de facture liee)
            if jtype == "AC" and valid_ac_je_ids and eid not in valid_ac_je_ids:
                exclusion_reasons.append("AC orpheline (aucune facture ne pointe vers cette ecriture)")
                included = False

            # Lister les lignes avec le montant cible
            target_lines = []
            for ln in e.get("lines", []):
                d = float(ln.get("debit", 0) or 0)
                c = float(ln.get("credit", 0) or 0)
                if abs(d - target_amount) < tolerance or abs(c - target_amount) < tolerance or abs(d - 12.0) < tolerance or abs(c - 12.0) < tolerance:
                    target_lines.append({
                        "account_number": ln.get("account_number", ""),
                        "account_name": ln.get("account_name", ""),
                        "debit": d,
                        "credit": c,
                        "class": ln.get("account_number", "")[0] if ln.get("account_number") else "",
                        "impacts_499": ln.get("account_number", "")[0] in ("6", "7") if ln.get("account_number") else False,
                    })

            results.append({
                "entry_id": eid,
                "date": e.get("date", ""),
                "journal_type": jtype,
                "reference": ref,
                "description": e.get("description", ""),
                "total_debit": e.get("total_debit", 0),
                "total_credit": e.get("total_credit", 0),
                "is_opening_balance": e.get("is_opening_balance", False),
                "reversed": e.get("reversed", False),
                "is_reversal": e.get("is_reversal", False),
                "is_regularization": e.get("is_regularization", False),
                "auto_generated": e.get("auto_generated", False),
                "import_session_id": e.get("import_session_id", ""),
                "source_type": e.get("source_type", ""),
                "INCLUDED_IN_BILAN": included,
                "EXCLUSION_REASONS": exclusion_reasons if exclusion_reasons else ["AUCUNE - incluse dans le calcul"],
                "target_lines": target_lines,
            })

        return {
            "copropriete_id": copropriete_id,
            "target_amount": target_amount,
            "total_entries_in_period": len(all_entries),
            "matching_entries": len(results),
            "included_count": sum(1 for r in results if r["INCLUDED_IN_BILAN"]),
            "excluded_count": sum(1 for r in results if not r["INCLUDED_IN_BILAN"]),
            "entries": sorted(results, key=lambda x: x["date"]),
        }

    return router
