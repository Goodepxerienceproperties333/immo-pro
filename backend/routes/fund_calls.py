from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import uuid
from auto_entries import generate_sale_entry, _delete_auto_entries


class FundCallInput(BaseModel):
    name: str
    date: str
    due_date: Optional[str] = ""
    fiscal_year_id: Optional[str] = ""
    description: Optional[str] = ""
    total_amount: float
    call_type: Optional[str] = "provisions"  # provisions, reserve, special
    distribution_key_id: Optional[str] = ""
    copropriete_id: Optional[str] = ""


class ReserveFund(BaseModel):
    enabled: bool = False
    amount: float = 0.0
    distribution_key_id: Optional[str] = ""
    label: Optional[str] = "Fonds de reserve"
    # Si frequency/start_date sont fournis, le fonds genere sa PROPRE serie d'appels
    # (call_type='reserve'). Sinon, ajoute au 1er appel des provisions (legacy).
    frequency: Optional[int] = 0  # 0 = legacy injection, 1/2/3/4/6/12 = serie propre
    start_date: Optional[str] = ""
    due_offset_days: Optional[int] = 30
    # iter90eo : arrondi au superieur (EUR entier) de la quote-part par lot.
    # Utile pour l'affichage sur les avis d'appel (evite les centimes).
    # L'exces recolte alimente le fonds (surprovision utilisable).
    round_up: Optional[bool] = False


class RoulementFund(BaseModel):
    enabled: bool = False
    amount: float = 0.0
    distribution_key_id: Optional[str] = ""
    label: Optional[str] = "Fonds de roulement"
    mode: Optional[str] = "create"
    # idem ReserveFund
    frequency: Optional[int] = 0
    start_date: Optional[str] = ""
    due_offset_days: Optional[int] = 30
    # iter90eo : arrondi au superieur (EUR entier) de la quote-part par lot.
    round_up: Optional[bool] = False


class GenerateFromBudgetInput(BaseModel):
    budget_id: str
    frequency: int  # 1, 2, 3, 4, 12 (yearly, semestrial, quadrimestrial, quarterly, monthly)
    start_date: str  # ISO date of the first call
    due_offset_days: Optional[int] = 30  # due date offset from call date
    reserve_fund: Optional[ReserveFund] = None
    roulement_fund: Optional[RoulementFund] = None
    copropriete_id: Optional[str] = ""


def _add_months(iso_date: str, months: int) -> str:
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day to last day of new month
    try:
        nd = d.replace(year=y, month=m)
    except ValueError:
        # day overflow (e.g. 31 Jan -> 28 Feb)
        if m == 12:
            nd = datetime(y + 1, 1, 1) - timedelta(days=1)
        else:
            nd = datetime(y, m + 1, 1) - timedelta(days=1)
    return nd.strftime("%Y-%m-%d")


def _snap_distribution_to_total(distribution: list, target_total: float) -> None:
    """Ajuste in-place les 'amount' d'une distribution pour que leur somme
    egale exactement target_total (a 0,01 EUR pres). Utilise la methode des
    'plus grands restes' : les lots avec le reste fractionnaire le plus eleve
    recoivent un centime supplementaire, ceux avec le plus faible perdent un
    centime. Evite les 0,04 EUR de derive sur la balance de tiers.
    """
    if not distribution:
        return
    # Somme actuelle (deja arrondie a 2 decimales)
    current = round(sum(float(d.get("amount", 0.0) or 0.0) for d in distribution), 2)
    diff_cents = int(round((target_total - current) * 100))
    if diff_cents == 0:
        return
    # Trier par "reste fractionnaire" (avant arrondi) pour distribuer le residu
    # Comme on n'a plus le raw amount ici, on utilise share comme proxy :
    # les lots avec le plus grand share portent plus naturellement les centimes.
    if diff_cents > 0:
        # Il manque des centimes -> ajoute 1c aux lots avec le plus gros share
        sorted_dist = sorted(distribution,
                             key=lambda d: (-float(d.get("share", 0) or 0),
                                            d.get("lot_number", "")))
        for i in range(diff_cents):
            sorted_dist[i % len(sorted_dist)]["amount"] = round(
                float(sorted_dist[i % len(sorted_dist)]["amount"]) + 0.01, 2)
    else:
        # Il y a des centimes en trop -> retire 1c aux lots avec le plus gros share
        sorted_dist = sorted(distribution,
                             key=lambda d: (-float(d.get("share", 0) or 0),
                                            d.get("lot_number", "")))
        for i in range(-diff_cents):
            sorted_dist[i % len(sorted_dist)]["amount"] = round(
                float(sorted_dist[i % len(sorted_dist)]["amount"]) - 0.01, 2)


async def _ensure_mutations_synced_for_acp(db, copro_id: str) -> int:
    """iter90cj : Backfill defensif de db.mutations depuis lot.mutations[].

    Root Cause 1 : properties.py::mutate_lot ecrit dans db.mutations dans un
    try/except silencieux. Si l'insert echoue (auth transitoire, panne
    Mongo...), lot.mutations[] est bien peuple mais db.mutations est vide,
    et les lecteurs (_resolve_owner_at_date, _rebind_owner_at_call_date,
    generate_prorata_mut_ods_for_call) fallback silencieusement sur le
    current owner (nouvel acheteur), corrompant l'attribution des appels.

    Ce helper garantit que toute lecture de db.mutations est precedee d'un
    sync a la volee : parcourt les lots de l'ACP ayant `mutations[]` peuple,
    upsert chaque entree manquante dans db.mutations. Idempotent, safe multi-
    appels.

    Doit etre appele en debut de tous les endpoints qui lisent db.mutations.
    Retourne le nombre d'entrees synchronisees (pour log/debug).
    """
    if not copro_id:
        return 0
    synced = 0
    try:
        async for lot_doc in db.lots.find(
            {"copropriete_id": copro_id, "mutations": {"$exists": True, "$ne": []}},
            {"_id": 0, "id": 1, "copropriete_id": 1, "mutations": 1},
        ):
            for mr in (lot_doc.get("mutations") or []):
                mid = mr.get("id")
                if not mid or not mr.get("date"):
                    continue
                # Verifie si l'entree existe deja
                existing = await db.mutations.find_one({"id": mid}, {"_id": 0, "id": 1})
                if existing:
                    continue
                doc = {
                    "id": mid,
                    "copropriete_id": lot_doc.get("copropriete_id", ""),
                    "lot_id": lot_doc["id"],
                    "from_owner_id": mr.get("old_owner_id", ""),
                    "to_owner_id": mr.get("new_owner_id", ""),
                    "sale_date": mr.get("date", ""),
                    "roulement_quota": mr.get("roulement_quota", 0.0),
                    "current_period_prorata": mr.get(
                        "current_period_prorata", mr.get("prorata_provisions", 0.0)
                    ),
                    "total_transfer": mr.get("total_transfer", 0.0),
                    "journal_entry_ids": mr.get("journal_entry_ids")
                    or ([mr.get("journal_entry_id")] if mr.get("journal_entry_id") else []),
                    "created_at": mr.get("created_at", ""),
                    "backfilled": True,
                }
                await db.mutations.update_one({"id": mid}, {"$set": doc}, upsert=True)
                synced += 1
        if synced:
            print(f"[iter90cj] db.mutations backfill synced {synced} entries for {copro_id}")
    except Exception as _e:
        print(f"[iter90cj] backfill sync failed (soft): {_e}")
    return synced


async def reverse_post_mutation_ods_for_call(db, call_id: str, reason: str = "") -> int:
    """iter90ch + iter90co : Contre-passe les OD retroactives liees a un appel :
    - OD MUT-P (source_type='lot_mutation', source_subtype='prorata_post_mutation')
    - OD MUT-R backfillees iter90ck (source_type='lot_mutation',
      source_subtype='fonds_roulement', fund_call_id=call_id present)

    Le filtre `fund_call_id=call_id` garantit qu'on NE TOUCHE PAS les OD MUT-R
    d'origine creees par `mutate_lot` (elles n'ont pas de fund_call_id).

    Utilisee lors de :
    - Suppression d'un appel de fonds
    - Suppression/dévalidation d'un budget (cascade sur les fund_calls)
    - delete-all-fund-calls
    - regenerate-from-budget (avant regeneration)

    iter90co : synchronise aussi db.mutations.roulement_quota = 0 pour les
    mutations dont l'OD MUT-R backfillee a ete contre-passee (evite d'afficher
    un transfert dans le decompte alors que le montant est retire du grand livre).

    Retourne le nombre d'ecritures contre-passees.
    """
    from journal_reversals import reverse_journal_entry
    reversed_count = 0
    mutation_ids_to_reset: set = set()
    try:
        post_muts = await db.journal_entries.find({
            "fund_call_id": call_id,
            "source_subtype": {"$in": ["prorata_post_mutation", "fonds_roulement"]},
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }, {"_id": 0}).to_list(1000)
        for pm in post_muts:
            # Trace de la mutation impactee (uniquement OD MUT-R backfillee)
            if pm.get("source_subtype") == "fonds_roulement" and pm.get("backfilled_by_iter90ck"):
                lot_id = pm.get("source_id")
                if lot_id:
                    mutation_ids_to_reset.add(lot_id)
            rev = await reverse_journal_entry(
                db, pm, reason=reason or f"Cleanup appel {call_id}"
            )
            if rev:
                reversed_count += 1
    except Exception as _e:
        print(f"[iter90ch/co] reversal of post-mutation ODs failed for {call_id}: {_e}")

    # iter90co : sync db.mutations.roulement_quota=0 pour les lots impactes.
    # On ne touche que les mutations dont l'OD MUT-R backfillee a ete
    # contre-passee (les autres mutations avec OD MUT-R d'origine mutate_lot
    # restent intactes). L'utilisateur peut ainsi regenerer un nouvel appel
    # et declencher un nouveau backfill iter90ck coherent.
    for lot_id in mutation_ids_to_reset:
        try:
            # Trouve les mutations du lot dont journal_entry_ids contient
            # un ID d'entree qui est desormais 'reversed'. Reset seulement
            # celles-la (evite d'ecraser un OD MUT-R d'origine encore actif).
            muts = await db.mutations.find(
                {"lot_id": lot_id}, {"_id": 0}
            ).to_list(100)
            for mut in muts:
                je_ids = mut.get("journal_entry_ids", []) or []
                if not je_ids:
                    continue
                # Verifie si TOUS les JE actifs de cette mutation sont reversed
                remaining = await db.journal_entries.count_documents({
                    "id": {"$in": je_ids},
                    "reversed": {"$ne": True},
                    "is_reversal": {"$ne": True},
                })
                if remaining == 0:
                    await db.mutations.update_one(
                        {"id": mut["id"]},
                        {"$set": {"roulement_quota": 0.0, "journal_entry_ids": []}},
                    )
        except Exception as _e:
            print(f"[iter90co] db.mutations sync failed for lot {lot_id}: {_e}")

    return reversed_count


async def generate_prorata_mut_ods_for_call(db, call_doc: dict) -> dict:
    """
    iter90cf + iter90ck : Genere retroactivement les OD 'Mutation - Prorata'
    (compte tier provisions) ET l'OD 'Mutation - Fonds de roulement' (compte
    100 -> tier) pour un appel dont la periode chevauche une ou plusieurs
    mutations existantes.

    Contexte : quand un appel est cree APRES une mutation, la logique de
    mutate_lot (properties.py) n'a pas pu creer l'OD MUT-P car l'appel
    n'existait pas encore. iter90ck etend ce filet a l'OD MUT-R : si la
    mutation existante n'a jamais recu d'OD fonds_roulement (cas legacy
    avant iter90cj), cette fonction la cree retroactivement en lisant le
    budget approuve pour la FY couvrant sale_date.

    Regles :
    - OD MUT-P (source_subtype='prorata_post_mutation') : idempotente sur
      reference MUTP-POST-{lot}-{call_id}-{to_owner}.
    - OD MUT-R (source_subtype='fonds_roulement') : idempotente sur
      reference MUT-{lot}-R et source_id=lot_id (ne double-pas les
      ecritures deja creees par mutate_lot ou une precedente generation).
    - Si aucune OD MUT-R n'existe ET aucun budget approuve avec
      roulement_fund_amount > 0 ne couvre sale_date, RAISE HTTPException
      400 avec message clair (user choix : visible error au lieu de EUR 0
      silencieux).

    Retourne : {"created": n, "mutr_created": n, "skipped": n, "details": [...]}
    """
    from datetime import date as _date_cls
    from tier_accounts import assign_owner_accounts

    stats = {"created": 0, "mutr_created": 0, "skipped": 0, "details": []}

    # Ne s'applique qu'aux appels de provisions (les OD MUT-P sont sur
    # tier provisions ; l'OD MUT-R backfill s'applique aussi ici car c'est
    # a la generation post-mutation qu'on decouvre le trou).
    if (call_doc.get("call_type") or "provisions") != "provisions":
        return stats

    period_start_iso = call_doc.get("period_start") or ""
    period_end_iso = call_doc.get("period_end") or ""
    call_date_iso = call_doc.get("date") or ""
    call_id = call_doc.get("id") or ""
    copro_id = call_doc.get("copropriete_id") or ""

    if not (period_start_iso and period_end_iso and call_date_iso and call_id and copro_id):
        return stats
    try:
        p_start = _date_cls.fromisoformat(period_start_iso)
        p_end = _date_cls.fromisoformat(period_end_iso)
    except Exception:
        return stats
    if p_end <= p_start:
        return stats
    total_days = (p_end - p_start).days + 1

    # Charge toutes les mutations de l'ACP indexees par lot_id
    # iter90cj : sync defensif avant lecture (Root Cause 1)
    await _ensure_mutations_synced_for_acp(db, copro_id)
    muts_by_lot: dict = {}
    async for m in db.mutations.find(
        {"copropriete_id": copro_id}, {"_id": 0}
    ):
        lid = m.get("lot_id")
        if lid and m.get("sale_date") and m.get("from_owner_id") and m.get("to_owner_id"):
            muts_by_lot.setdefault(lid, []).append(m)
    for lid in muts_by_lot:
        muts_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

    if not muts_by_lot:
        return stats

    # Pour eviter les I/O redondants
    owners_cache: dict = {}

    async def _get_owner(owner_id: str) -> dict:
        if owner_id in owners_cache:
            return owners_cache[owner_id]
        own = await db.owners.find_one({"id": owner_id}, {"_id": 0}) or {}
        owners_cache[owner_id] = own
        return own

    for entry in (call_doc.get("distribution") or []):
        lot_id = entry.get("lot_id") or ""
        base_amount = float(entry.get("amount", 0) or 0)
        if base_amount <= 0.01 or not lot_id:
            continue
        muts = muts_by_lot.get(lot_id, [])
        if not muts:
            continue
        # Ne considere que les mutations dans la periode (exclues period_start
        # car sale_date == period_start -> tout apres la mutation = buyer,
        # deja capture par owner-at-call-date).
        in_period = []
        for m in muts:
            try:
                sd = _date_cls.fromisoformat(m["sale_date"])
            except Exception:
                continue
            if p_start < sd <= p_end:
                in_period.append((sd, m))
        if not in_period:
            continue

        # Construction des segments temporels
        # current_owner initial = owner detenteur AVANT la 1ere mutation en periode
        first_mut = in_period[0][1]
        current_owner = first_mut.get("from_owner_id")
        segments: list = []  # (owner_id, days)
        cursor = p_start
        for sd, m in in_period:
            if sd <= cursor:
                current_owner = m.get("to_owner_id") or current_owner
                continue
            days_before = (sd - cursor).days  # exclusif de sd
            if days_before > 0 and current_owner:
                segments.append((current_owner, days_before))
            cursor = sd
            current_owner = m.get("to_owner_id")
        final_days = (p_end - cursor).days + 1
        if final_days > 0 and current_owner:
            segments.append((current_owner, final_days))

        if not segments:
            continue

        # owner de la distribution = owner-at-call-date. On genere les OD
        # pour tous les segments dont l'owner differe.
        dist_owner_id = entry.get("owner_id") or ""

        # Pour chaque segment != dist_owner_id, une OD transfert dist_owner -> segment_owner
        # Aggregation par (from_owner, to_owner) pour eviter les OD multiples
        transfers: dict = {}
        for seg_owner, seg_days in segments:
            if seg_owner == dist_owner_id:
                continue
            seg_amount = round(base_amount * seg_days / total_days, 2)
            if seg_amount < 0.01:
                continue
            key = (dist_owner_id, seg_owner)
            transfers[key] = transfers.get(key, 0.0) + seg_amount

        # Idempotence : verifier qu'aucune OD MUT-P n'existe deja pour ce lot
        # + cet appel + ce transfert (from -> to).
        for (from_owner, to_owner), amount in transfers.items():
            if amount < 0.01:
                continue
            # Reference unique : MUTP-{lot_number}-{call_id_short}
            ref = f"MUTP-POST-{entry.get('lot_number','')[:12]}-{call_id[:8]}-{to_owner[:6]}"
            existing = await db.journal_entries.find_one({
                "copropriete_id": copro_id,
                "reference": ref,
            }, {"_id": 0})
            if existing:
                stats["skipped"] += 1
                continue

            # Resoudre les comptes tiers
            from_own = await _get_owner(from_owner)
            to_own = await _get_owner(to_owner)
            from_own = await assign_owner_accounts(db, from_own, copro_id)
            to_own = await assign_owner_accounts(db, to_own, copro_id)
            from_acc = (from_own.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
            to_acc = (to_own.get("tier_accounts", {}) or {}).get(copro_id, {}).get("provisions")
            if not from_acc or not to_acc:
                print(f"[iter90cf] compte tier manquant pour lot {entry.get('lot_number')}: from={from_acc} to={to_acc}")
                continue

            od_entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": call_date_iso,
                "reference": ref,
                "description": (
                    f"Mutation lot {entry.get('lot_number','')} - Prorata (appel post-mutation): "
                    f"{from_own.get('name','')} -> {to_own.get('name','')} ({amount:.2f} EUR)"
                ),
                "lines": [
                    {"account_number": to_acc,
                     "account_name": f"Mutation - {to_own.get('last_name') or to_own.get('name')}",
                     "debit": amount, "credit": 0.0,
                     "third_party_id": to_owner,
                     "third_party_name": to_own.get("name", ""),
                     # iter90ea : persistance lot_number
                     "lot_id": lot_id or "",
                     "lot_number": entry.get("lot_number", "") or ""},
                    {"account_number": from_acc,
                     "account_name": f"Mutation - {from_own.get('last_name') or from_own.get('name')}",
                     "debit": 0.0, "credit": amount,
                     "third_party_id": from_owner,
                     "third_party_name": from_own.get("name", ""),
                     # iter90ea : persistance lot_number
                     "lot_id": lot_id or "",
                     "lot_number": entry.get("lot_number", "") or ""},
                ],
                "total_debit": amount,
                "total_credit": amount,
                "copropriete_id": copro_id,
                "auto_generated": True,
                "manually_edited": True,
                "source_type": "lot_mutation",
                "source_id": lot_id,
                "source_subtype": "prorata_post_mutation",
                "fund_call_id": call_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(od_entry)
            stats["created"] += 1
            stats["details"].append({
                "lot_id": lot_id,
                "lot_number": entry.get("lot_number", ""),
                "from_owner": from_owner,
                "to_owner": to_owner,
                "amount": amount,
                "reference": ref,
                "entry_id": od_entry["id"],
            })

    # ============================================================
    # iter90ck : BACKFILL retroactif de l'OD MUT-R (fonds de roulement)
    # ============================================================
    # Pour chaque lot touche par une mutation dans la periode de l'appel,
    # verifier qu'une OD MUT-R (source_subtype='fonds_roulement') existe.
    # Sinon, la creer en lisant le budget approuve couvrant sale_date.
    # Si aucun budget approuve avec roulement > 0 ne couvre sale_date,
    # collecte les manques et raise HTTPException 400 en fin de fonction.
    from fastapi import HTTPException
    missing_budgets = []

    # Collecte des lots touches par une mutation dans la periode de l'appel
    lots_to_check: dict = {}  # lot_id -> [mutation_docs]
    for entry in (call_doc.get("distribution") or []):
        lot_id = entry.get("lot_id") or ""
        if not lot_id:
            continue
        for m in muts_by_lot.get(lot_id, []):
            try:
                sd = _date_cls.fromisoformat(m["sale_date"])
            except Exception:
                continue
            if p_start <= sd <= p_end:
                lots_to_check.setdefault(lot_id, []).append(m)

    if lots_to_check:
        # Chargement une fois : FY, budgets, distribution_keys
        fys_map: dict = {}
        async for _fy in db.fiscal_years.find(
            {"copropriete_id": copro_id}, {"_id": 0}
        ):
            fys_map[_fy["id"]] = _fy
        # Charge tous les budgets approuves de l'ACP (petit N)
        approved_budgets = await db.budgets.find(
            {"copropriete_id": copro_id, "status": "approved"}, {"_id": 0},
        ).sort("approved_at", -1).to_list(100)
        default_key = await db.distribution_keys.find_one(
            {"copropriete_id": copro_id, "is_default": True}, {"_id": 0},
        )

        def _budget_covering(sale_date_iso: str) -> dict:
            """Retourne le budget approuve le plus recent dont la FY couvre
            sale_date, ou {} si aucun."""
            for b in approved_budgets:
                fy = fys_map.get(b.get("fiscal_year_id", ""))
                if not fy:
                    continue
                if (fy.get("start_date", "") <= sale_date_iso
                        <= fy.get("end_date", "")):
                    return b
            return {}

        for lot_id, muts_in_period in lots_to_check.items():
            for m in muts_in_period:
                sale_date_iso = m.get("sale_date", "")
                mut_id = m.get("id") or ""
                from_owner = m.get("from_owner_id") or ""
                to_owner = m.get("to_owner_id") or ""
                if not (sale_date_iso and from_owner and to_owner):
                    continue

                # Idempotence : cherche une OD MUT-R deja existante pour ce
                # lot ET cette mutation (par lot_id + date + source_subtype)
                existing_mutr = await db.journal_entries.find_one({
                    "copropriete_id": copro_id,
                    "source_type": "lot_mutation",
                    "source_subtype": "fonds_roulement",
                    "source_id": lot_id,
                    "date": sale_date_iso,
                    "reversed": {"$ne": True},
                    "is_reversal": {"$ne": True},
                }, {"_id": 0})
                if existing_mutr:
                    continue

                # Recherche du budget vote couvrant sale_date
                budget = _budget_covering(sale_date_iso)
                budgeted_roul = float(
                    (budget or {}).get("roulement_fund_amount", 0) or 0
                )
                # iter90ck-bis (user clarification Feb 2026) : la base doit
                # TOUJOURS etre max(posted, budgeted). Le compte 100 au bilan
                # (somme des augmentations historiques + repris du bilan
                # precedent) prime si superieur au budget vote.
                # Cas A (fresh ACP)         : posted=0, budget=5200  -> 5200
                # Cas B (ACP coherente)     : posted=5200, budget=5200 -> 5200
                # Cas C (partiellement col.) : posted=3000, budget=5200 -> 5200
                # Cas D (roulement augmente) : posted=8000, budget=5200 -> 8000
                # Cas E (rien engage)       : posted=0, budget=0      -> erreur
                posted_agg = await db.journal_entries.aggregate([
                    {"$match": {"copropriete_id": copro_id}},
                    {"$unwind": "$lines"},
                    {"$match": {"lines.account_number": "100"}},
                    {"$group": {"_id": None,
                                "credit": {"$sum": "$lines.credit"},
                                "debit": {"$sum": "$lines.debit"}}},
                ]).to_list(1)
                posted_roul = float(
                    (posted_agg[0]["credit"] - posted_agg[0]["debit"])
                    if posted_agg else 0.0
                )
                budgeted_roul = max(posted_roul, budgeted_roul)
                if budgeted_roul <= 0.001:
                    # Cas E : ni compte 100 ni budget engage -> erreur visible
                    # (uniquement pour appels wizard cf. check budget_id plus bas).
                    missing_budgets.append({
                        "lot_id": lot_id,
                        "sale_date": sale_date_iso,
                        "mutation_id": mut_id,
                        "reason": "no_engagement_no_posted",
                    })
                    continue

                # Calcul de la quote-part du lot dans la cle par defaut
                if not default_key or not default_key.get("lots"):
                    missing_budgets.append({
                        "lot_id": lot_id, "sale_date": sale_date_iso,
                        "mutation_id": mut_id, "reason": "no_default_key",
                    })
                    continue
                lot_entry_in_key = next(
                    (kl for kl in default_key["lots"] if kl.get("lot_id") == lot_id),
                    None,
                )
                if not lot_entry_in_key:
                    # Iter90df : fallback match par lot_number normalise
                    # (robuste apres re-creation/re-import de lots).
                    lot_doc = await db.lots.find_one(
                        {"id": lot_id}, {"_id": 0, "number": 1},
                    )
                    if lot_doc:
                        lot_num_str = str(lot_doc.get("number", "")).lstrip("0") or "0"
                        lot_entry_in_key = next(
                            (kl for kl in default_key["lots"]
                             if (str(kl.get("lot_number", "")).lstrip("0") or "0") == lot_num_str),
                            None,
                        )
                if not lot_entry_in_key or lot_entry_in_key.get("excluded"):
                    continue
                key_total = sum(
                    float(kl.get("share", 0) or 0)
                    for kl in default_key["lots"] if not kl.get("excluded")
                )
                lot_share = float(lot_entry_in_key.get("share", 0) or 0)
                if key_total <= 0 or lot_share <= 0:
                    continue
                r_quota = round(budgeted_roul * (lot_share / key_total), 2)
                if r_quota <= 0.001:
                    continue

                # Resoudre les comptes tiers
                from_own = await _get_owner(from_owner)
                to_own = await _get_owner(to_owner)
                from_own = await assign_owner_accounts(db, from_own, copro_id)
                to_own = await assign_owner_accounts(db, to_own, copro_id)
                from_acc = (from_own.get("tier_accounts", {}) or {}).get(
                    copro_id, {}).get("provisions")
                to_acc = (to_own.get("tier_accounts", {}) or {}).get(
                    copro_id, {}).get("provisions")
                if not from_acc or not to_acc:
                    print(f"[iter90ck] compte tier manquant pour lot {lot_id}: from={from_acc} to={to_acc}")
                    continue

                # Recupere lot_number pour la reference
                lot_doc = await db.lots.find_one({"id": lot_id}, {"_id": 0, "number": 1})
                lot_number = (lot_doc or {}).get("number", lot_id[:8])

                od_mutr = {
                    "id": str(uuid.uuid4()),
                    "journal_type": "OD",
                    "date": sale_date_iso,
                    "reference": f"MUT-{lot_number[:18]}-R",
                    "description": (
                        f"Mutation lot {lot_number} - Fonds de roulement "
                        f"(backfill retroactif via appel {call_doc.get('name','')}): "
                        f"{from_own.get('name','')} -> {to_own.get('name','')} "
                        f"({r_quota:.2f} EUR)"
                    ),
                    "lines": [
                        {"account_number": to_acc,
                         "account_name": f"Mutation - {to_own.get('last_name') or to_own.get('name')}",
                         "debit": r_quota, "credit": 0.0,
                         "third_party_id": to_owner,
                         "third_party_name": to_own.get("name", ""),
                         # iter90ea : persistance lot_number
                         "lot_id": lot_id or "",
                         "lot_number": lot_number or ""},
                        {"account_number": from_acc,
                         "account_name": f"Mutation - {from_own.get('last_name') or from_own.get('name')}",
                         "debit": 0.0, "credit": r_quota,
                         "third_party_id": from_owner,
                         "third_party_name": from_own.get("name", ""),
                         # iter90ea : persistance lot_number
                         "lot_id": lot_id or "",
                         "lot_number": lot_number or ""},
                    ],
                    "total_debit": r_quota,
                    "total_credit": r_quota,
                    "copropriete_id": copro_id,
                    "auto_generated": False,
                    "manually_edited": True,
                    "source_type": "lot_mutation",
                    "source_id": lot_id,
                    "source_subtype": "fonds_roulement",
                    "fund_call_id": call_id,  # trace de l'appel declencheur
                    "backfilled_by_iter90ck": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                await db.journal_entries.insert_one(od_mutr)
                stats["mutr_created"] += 1
                stats["details"].append({
                    "kind": "fonds_roulement",
                    "lot_id": lot_id, "lot_number": lot_number,
                    "sale_date": sale_date_iso,
                    "amount": r_quota,
                    "entry_id": od_mutr["id"],
                    "reference": od_mutr["reference"],
                })

                # Met a jour db.mutations.roulement_quota pour refleter le
                # transfert reel (au cas ou l'utilisateur consulte le doc).
                try:
                    await db.mutations.update_one(
                        {"id": mut_id},
                        {"$set": {"roulement_quota": r_quota,
                                  "journal_entry_ids": [od_mutr["id"]]}},
                    )
                except Exception as _e:
                    print(f"[iter90ck] db.mutations update_one failed (soft): {_e}")

    # Raise HTTPException si des mutations sans budget approuve ont ete
    # rencontrees ET l'appel a ete genere par le wizard (call.budget_id
    # existe). Pour les appels manuels (POST /fund-calls sans budget_id),
    # on skip silencieusement (le syndic est cense connaitre ce qu'il fait).
    # User choix : visible error au lieu de EUR 0 silencieux, mais uniquement
    # dans le flux wizard car c'est la que le budget doit etre coherent.
    if missing_budgets and call_doc.get("budget_id"):
        details = "; ".join(
            f"lot={mb['lot_id'][:8]} mutation={mb.get('mutation_id','?')[:8]} "
            f"sale_date={mb['sale_date']}"
            for mb in missing_budgets[:3]
        )
        suffix = f" ({len(missing_budgets) - 3} autres...)" if len(missing_budgets) > 3 else ""
        raise HTTPException(
            400,
            f"Impossible de creer l'OD MUT-R (fonds de roulement) pour "
            f"{len(missing_budgets)} mutation(s) : aucun budget approuve avec "
            f"roulement_fund_amount > 0 ne couvre la sale_date. "
            f"Editez le budget de la FY concernee et fixez le fonds de "
            f"roulement engage par l'AG avant de regenerer les appels. "
            f"Details : {details}{suffix}"
        )

    return stats


def create_fund_calls_router(db):
    router = APIRouter(prefix="/api/fund-calls")

    @router.get("")
    async def list_fund_calls(
        request: Request,
        fiscal_year_id: Optional[str] = None,
        copropriete_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Liste les appels de fonds. Filtres : `fiscal_year_id`, `copropriete_id`,
        `date_from`/`date_to` (filtre sur le champ `date` de l'appel)."""
        from syndic_scope import syndic_query
        q = {**syndic_query(request)}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        calls = await db.fund_calls.find(q, {"_id": 0}).sort("date", -1).to_list(1000)
        return calls

    @router.post("")
    async def create_fund_call(data: FundCallInput, request: Request):
        from fiscal_lock import ensure_period_open
        from syndic_scope import inject_syndic
        copro_id = data.copropriete_id or ""
        # Verrou fiscal : la date de l'appel doit etre dans une periode ouverte
        await ensure_period_open(db, copro_id, data.date, context="appel de fonds")
        # Chinese wall: only fetch lots from this ACP
        lots_q = {"copropriete_id": copro_id} if copro_id else {}
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)
        owners = await db.owners.find({}, {"_id": 0}).to_list(1000)  # owners are global
        owners_map = {o["id"]: o for o in owners}

        # Compute distribution
        distribution = []
        # Map des lots scopes a l'ACP pour resoudre parent_lot_id
        lots_by_id = {lt["id"]: lt for lt in lots}
        if data.distribution_key_id:
            key = await db.distribution_keys.find_one({"id": data.distribution_key_id}, {"_id": 0})
            if key:
                # iter90ac : exclut les lots marques excluded=True
                active_kls = [kle for kle in key.get("lots", []) if not kle.get("excluded")]
                total_shares = sum(kle["share"] for kle in active_kls)
                for kl in active_kls:
                    lot = lots_by_id.get(kl["lot_id"])
                    owner = owners_map.get(lot["owner_id"]) if lot else None
                    share_ratio = kl["share"] / total_shares if total_shares > 0 else 0
                    distribution.append({
                        "lot_id": kl["lot_id"],
                        "lot_number": kl["lot_number"],
                        "parent_lot_id": (lot or {}).get("parent_lot_id", "") or "",
                        "owner_id": lot.get("owner_id", "") if lot else "",
                        "owner_name": owner["name"] if owner else "",
                        "vcs_code": owner.get("vcs_code", "") if owner else "",
                        "share": kl["share"],
                        "amount": round(data.total_amount * share_ratio, 2),
                        "paid": False,
                        "paid_date": "",
                    })
        else:
            # Default: distribute by tantiemes
            total_quotity = sum(lt.get("quotity", 0) for lt in lots)
            for lot in lots:
                if not lot.get("owner_id"):
                    continue
                owner = owners_map.get(lot["owner_id"])
                share_ratio = lot.get("quotity", 0) / total_quotity if total_quotity > 0 else 0
                distribution.append({
                    "lot_id": lot["id"],
                    "lot_number": lot["number"],
                    "parent_lot_id": lot.get("parent_lot_id", "") or "",
                    "owner_id": lot["owner_id"],
                    "owner_name": owner["name"] if owner else "",
                    "vcs_code": owner.get("vcs_code", "") if owner else "",
                    "share": lot.get("quotity", 0),
                    "amount": round(data.total_amount * share_ratio, 2),
                    "paid": False,
                    "paid_date": "",
                })

        # iter90cf + iter90cg : deduire period_start/period_end pour :
        # 1. rebind owner sur la date effective (min(call_date, period_end))
        # 2. permettre le calcul retroactif d'OD de prorata mutation (uniquement provisions).
        # On calcule la periode pour TOUS les call_types afin que le rebind
        # tienne compte des appels emis apres la fin de la periode (Q3 emis en
        # retard apres mutation posterieure -> attribue au vendeur).
        period_start = ""
        period_end = ""
        try:
            import sys
            if "/app/backend/scripts" not in sys.path:
                sys.path.insert(0, "/app/backend/scripts")
            from migrate_fund_calls_periods import _compute_period  # type: ignore
            fy = None
            if data.fiscal_year_id:
                fy = await db.fiscal_years.find_one({"id": data.fiscal_year_id}, {"_id": 0})
            fy_by_id = {fy["id"]: fy} if fy else {}
            ps, pe = _compute_period({
                "name": data.name,
                "date": data.date,
                "fiscal_year_id": data.fiscal_year_id,
            }, fy_by_id)
            period_start = ps or ""
            period_end = pe or ""
        except Exception as _e:
            print(f"[iter90cf/cg] period computation skipped: {_e}")

        # iter90aj + iter90cf + iter90cg : rebind owner sur date effective
        # min(call_date, period_end) pour TOUS les call_types. Regle metier :
        # un appel Q3 emis en retard apres une mutation posterieure a la periode
        # doit revenir au vendeur (proprietaire durant la periode).
        try:
            from datetime import date as _dt_cls
            _cd = _dt_cls.fromisoformat(data.date)
            _pe = None
            if period_end:
                try:
                    _pe = _dt_cls.fromisoformat(period_end)
                except Exception:
                    _pe = None
            target = min(_cd, _pe) if _pe else _cd
            # iter90cj : sync defensif avant lecture (Root Cause 1)
            await _ensure_mutations_synced_for_acp(db, copro_id)
            muts_all = await db.mutations.find(
                {"copropriete_id": copro_id}, {"_id": 0}
            ).to_list(10000)
            muts_by_lot: dict = {}
            for _m in muts_all:
                _lid = _m.get("lot_id")
                if _lid and _m.get("sale_date") and _m.get("from_owner_id") and _m.get("to_owner_id"):
                    muts_by_lot.setdefault(_lid, []).append(_m)
            for _lid in muts_by_lot:
                muts_by_lot[_lid].sort(key=lambda x: x.get("sale_date") or "")
            for entry in distribution:
                _lid = entry.get("lot_id") or ""
                _muts = muts_by_lot.get(_lid, [])
                if not _muts:
                    continue
                _current = _muts[0].get("from_owner_id") or entry.get("owner_id")
                for _m in _muts:
                    try:
                        _sd = _dt_cls.fromisoformat(_m.get("sale_date") or "")
                    except Exception:
                        continue
                    if _sd <= target:
                        _current = _m.get("to_owner_id") or _current
                    else:
                        break
                if _current and _current != entry.get("owner_id"):
                    _own = owners_map.get(_current) or {}
                    entry["owner_id"] = _current
                    entry["owner_name"] = _own.get("name", "")
                    entry["vcs_code"] = _own.get("vcs_code", "")
        except Exception as _e:
            print(f"[iter90aj/cf/cg] Rebind owner_at_date skipped: {_e}")

        # iter90w : garantit sum(distribution.amount) == total_amount exactement
        # (evite les 0,01 EUR de derive par appel manuel).
        _snap_distribution_to_total(distribution, round(data.total_amount, 2))

        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "date": data.date,
            "due_date": data.due_date,
            "period_start": period_start,
            "period_end": period_end,
            "fiscal_year_id": data.fiscal_year_id,
            "description": data.description,
            "total_amount": data.total_amount,
            "call_type": data.call_type,
            "distribution_key_id": data.distribution_key_id,
            "distribution": distribution,
            "status": "pending",
            "copropriete_id": copro_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # iter93x : injection du syndic_id (bug : appels speciaux crees
        # manuellement etaient invisibles dans list_fund_calls car filtre
        # syndic_query les excluait faute de champ syndic_id).
        inject_syndic(doc, request)
        await db.fund_calls.insert_one(doc)
        clean = {k: v for k, v in doc.items() if k != "_id"}
        try:
            await generate_sale_entry(db, clean)
        except Exception as e:
            print(f"[auto-entry] sale create failed: {e}")
        # iter90cf : genere retroactivement les OD MUT-P si periode straddle
        # une mutation existante (uniquement pour provisions).
        try:
            _mut_stats = await generate_prorata_mut_ods_for_call(db, clean)
            if _mut_stats.get("created"):
                print(f"[iter90cf] {_mut_stats['created']} OD MUT-P retroactives creees pour {data.name}")
        except HTTPException:
            # iter90ck : propager les erreurs visibles (budget roulement manquant).
            raise
        except Exception as _e:
            print(f"[iter90cf] retroactive OD generation skipped: {_e}")
        return clean

    @router.get("/{call_id}")
    async def get_fund_call(call_id: str):
        fc = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not fc:
            raise HTTPException(404, "Appel de fonds non trouve")
        return fc

    @router.post("/{call_id}/mark-paid")
    async def mark_owner_paid(call_id: str, owner_id: str, paid_date: Optional[str] = None):
        """Mark a specific owner's portion as paid."""
        fc = await db.fund_calls.find_one({"id": call_id})
        if not fc:
            raise HTTPException(404, "Appel non trouve")

        distribution = fc.get("distribution", [])
        updated = False
        for d in distribution:
            if d.get("owner_id") == owner_id:
                d["paid"] = True
                d["paid_date"] = paid_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                updated = True

        if not updated:
            raise HTTPException(404, "Proprietaire non trouve dans cet appel")

        all_paid = all(d.get("paid", False) for d in distribution)
        await db.fund_calls.update_one(
            {"id": call_id},
            {"$set": {"distribution": distribution, "status": "completed" if all_paid else "partial"}}
        )
        return {"message": "Paiement enregistre"}

    @router.post("/{call_id}/generate-entries")
    async def generate_journal_entries(call_id: str):
        """Generate accounting entries for a fund call.

        iter90i7 : DEPRECATED - cette route legacy creait un JE de type AP
        alors que le systeme auto-genere deja un JE VE (via `generate_sale_entry`)
        lors du POST /fund-calls. Le double-booking creait un
        DOUBLE-COMPTAGE du fonds de reserve (bug reporte par user :
        bilan affiche 9000 au lieu de 7000). La route est conservee pour
        retro-compat, mais retourne 409 si une ecriture VE existe deja pour
        cet appel afin d'empecher le doublon.
        """
        fc = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not fc:
            raise HTTPException(404, "Appel non trouve")

        # iter90i7 : verifier qu'aucune ecriture VE n'existe deja pour cet appel
        existing_ve = await db.journal_entries.find_one(
            {"source_type": "fund_call", "source_id": call_id,
             "journal_type": "VE",
             "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
            {"_id": 0, "id": 1, "reference": 1},
        )
        if existing_ve:
            raise HTTPException(
                409,
                f"Une ecriture VE (id={existing_ve['id']}, ref={existing_ve.get('reference','')}) "
                f"a deja ete auto-generee pour cet appel. La route legacy "
                f"generate-entries est desactivee pour prevenir un double-comptage."
            )
        # iter90i7 : aussi verifier l'idempotence sur AP legacy lui-meme
        existing_ap = await db.journal_entries.find_one(
            {"fund_call_id": call_id, "journal_type": "AP",
             "reversed": {"$ne": True}, "is_reversal": {"$ne": True}},
            {"_id": 0, "id": 1},
        )
        if existing_ap:
            raise HTTPException(
                409,
                f"Une ecriture AP existe deja pour cet appel (id={existing_ap['id']})",
            )

        account_map = {
            "provisions": ("400000", "700000"),
            "reserve": ("401000", "160"),
            "roulement": ("400000", "100"),
            "special": ("405000", "710000"),
        }
        debit_acc, credit_acc = account_map.get(fc.get("call_type", "provisions"), ("400000", "700000"))

        lines = [
            {"account_number": debit_acc, "account_name": "Proprietaires - Appels", "debit": fc["total_amount"], "credit": 0},
            {"account_number": credit_acc, "account_name": "Provisions charges", "debit": 0, "credit": fc["total_amount"]},
        ]

        entry = {
            "id": str(uuid.uuid4()),
            "journal_type": "AP",
            "date": fc["date"],
            "reference": f"AP-{fc['name']}",
            "description": f"Appel de fonds: {fc['name']}",
            "lines": lines,
            "total_debit": fc["total_amount"],
            "total_credit": fc["total_amount"],
            "fund_call_id": call_id,
            "copropriete_id": fc.get("copropriete_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.journal_entries.insert_one(entry)
        return {"message": "Ecritures generees", "entry_id": entry["id"]}

    @router.delete("/{call_id}")
    async def delete_fund_call(call_id: str):
        from fiscal_lock import ensure_period_open
        fc = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not fc:
            raise HTTPException(404, "Appel non trouve")
        # Verrou : refuse la suppression si la date tombe dans un exercice cloture
        await ensure_period_open(db, fc.get("copropriete_id", ""), fc.get("date"), context="appel de fonds")
        try:
            await _delete_auto_entries(db, "fund_call", call_id)
        except Exception:
            pass
        # iter90cf/ch : contre-passe egalement les OD MUT-P retroactives liees a
        # cet appel pour eviter les orphelins comptables.
        await reverse_post_mutation_ods_for_call(
            db, call_id, reason=f"Suppression appel {call_id}"
        )
        # CASCADE MUT-F : supprime les ecritures MUT-*-F generees par les
        # mutations qui referencent cet appel (fund_call_ids contient call_id).
        copro_id = fc.get("copropriete_id", "")
        mut_f_q = {
            "copropriete_id": copro_id,
            "journal_type": "OD",
            "reference": {"$regex": "^MUT-.*-F"},
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }
        async for je in db.journal_entries.find(mut_f_q, {"_id": 0, "id": 1, "fund_call_ids": 1}):
            fc_ids = je.get("fund_call_ids") or []
            if call_id in fc_ids:
                await db.journal_entries.delete_one({"id": je["id"]})
        result = await db.fund_calls.delete_one({"id": call_id})
        if result.deleted_count == 0:
            raise HTTPException(404, "Appel non trouve")
        return {"message": "Appel de fonds supprime"}

    @router.post("/regenerate-entries")
    async def regenerate_fund_call_entries(request: Request, copropriete_id: Optional[str] = None):
        """Regenere les ecritures comptables auto-generees (VE) de TOUS les appels
        de fonds d'une ACP. Utile apres une mise a jour du moteur de generation
        (ex: ajout du libelle differencie par type d'appel).

        Pour chaque fund_call de l'ACP, supprime l'ancienne ecriture VE auto-generee
        et en regenere une nouvelle. Les ecritures `manually_edited=true` sont
        preservees (jamais touchees - cf. auto_entries._delete_auto_entries).
        """
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        calls = await db.fund_calls.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(10000)
        regenerated = 0
        errors = []
        for c in calls:
            try:
                res = await generate_sale_entry(db, c)
                if res:
                    regenerated += 1
            except Exception as e:
                errors.append({"call_id": c.get("id"), "name": c.get("name"), "error": str(e)})
        return {
            "status": "ok",
            "scanned": len(calls),
            "regenerated": regenerated,
            "errors": errors,
            "copropriete_id": copropriete_id,
            "message": f"{regenerated}/{len(calls)} ecriture(s) comptable(s) regeneree(s) avec libelle correct.",
        }

    @router.post("/delete-all")
    async def delete_all_fund_calls(request: Request, copropriete_id: Optional[str] = None):
        """Supprime TOUS les appels de fonds d'une ACP + leurs ecritures auto-generees.
        Chinese walls strict : copropriete_id requis (param ou header)."""
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")
        calls = await db.fund_calls.find(
            {"copropriete_id": copropriete_id}, {"_id": 0, "id": 1, "name": 1}
        ).to_list(10000)
        deleted_calls = 0
        for c in calls:
            try:
                await _delete_auto_entries(db, "fund_call", c["id"])
            except Exception:
                pass
            # iter90ch : contre-passe aussi les OD MUT-P retroactives
            await reverse_post_mutation_ods_for_call(
                db, c["id"], reason=f"delete-all-fund-calls ACP {copropriete_id}"
            )
        res = await db.fund_calls.delete_many({"copropriete_id": copropriete_id})
        deleted_calls = res.deleted_count
        return {
            "status": "ok",
            "deleted_calls": deleted_calls,
            "scanned": len(calls),
            "copropriete_id": copropriete_id,
            "message": f"{deleted_calls} appel(s) de fonds supprime(s) avec leurs ecritures auto.",
        }

    async def _compute_call_dates_from_budget(data: GenerateFromBudgetInput) -> list:
        """iter90cn helper : reconstitue les dates d'appel depuis les params wizard
        sans reellement generer les appels (utilise pour warnings preview)."""
        from datetime import date as _date_cls
        from dateutil.relativedelta import relativedelta
        try:
            start = _date_cls.fromisoformat(data.start_date)
        except Exception:
            return []
        step = 12 // (data.frequency or 4)
        dates = []
        for i in range(data.frequency or 4):
            call_dt = start + relativedelta(months=step * i)
            dates.append(call_dt.isoformat())
        return dates

    # ---- BULK GENERATION FROM APPROVED BUDGET ----
    @router.post("/preview-from-budget")
    async def preview_from_budget(data: GenerateFromBudgetInput):
        """Preview N fund calls computed from a budget, WITHOUT persisting.
        Used by the frontend wizard to show recap before confirmation.

        iter90cc : orphan_lots_warning (share > 0 sans owner_id).
        iter90cn : ownership_at_date_warning (lot dont l'owner-at-call-date
        ne peut pas etre resolu correctement).
        """
        result = await _generate_from_budget(data, persist=False)
        result["orphan_lots_warning"] = await _detect_orphan_lots_for_budget(data)
        # iter90cn : warning ownership-at-date
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0}) or {}
        copro_id = data.copropriete_id or budget.get("copropriete_id", "")
        key_ids = list({bl.get("distribution_key_id") for bl in budget.get("lines", []) if bl.get("distribution_key_id")})
        if data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.distribution_key_id:
            if data.reserve_fund.distribution_key_id not in key_ids:
                key_ids.append(data.reserve_fund.distribution_key_id)
        if data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.distribution_key_id:
            if data.roulement_fund.distribution_key_id not in key_ids:
                key_ids.append(data.roulement_fund.distribution_key_id)
        call_dates = await _compute_call_dates_from_budget(data)
        result["ownership_at_date_warning"] = await _detect_lots_unresolved_at_dates(
            copro_id, call_dates, key_ids,
        )
        return result

    @router.post("/preflight-orphan-check")
    async def preflight_orphan_check(data: GenerateFromBudgetInput):
        """iter90cc + iter90cn : verification legere avant emission d'appels."""
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0}) or {}
        copro_id = data.copropriete_id or budget.get("copropriete_id", "")
        key_ids = list({bl.get("distribution_key_id") for bl in budget.get("lines", []) if bl.get("distribution_key_id")})
        if data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.distribution_key_id:
            if data.reserve_fund.distribution_key_id not in key_ids:
                key_ids.append(data.reserve_fund.distribution_key_id)
        if data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.distribution_key_id:
            if data.roulement_fund.distribution_key_id not in key_ids:
                key_ids.append(data.roulement_fund.distribution_key_id)
        call_dates = await _compute_call_dates_from_budget(data)
        return {
            "orphan_lots_warning": await _detect_orphan_lots_for_budget(data),
            "ownership_at_date_warning": await _detect_lots_unresolved_at_dates(
                copro_id, call_dates, key_ids,
            ),
        }

    class ManualCallPreflightInput(BaseModel):
        copropriete_id: str
        date: str  # ISO YYYY-MM-DD
        distribution_key_id: str

    @router.post("/preflight-manual-call")
    async def preflight_manual_call(data: ManualCallPreflightInput):
        """iter90cn : verification avant emission d'un appel manuel.
        Renvoie les warnings ownership-at-date pour les lots de la cle."""
        return {
            "ownership_at_date_warning": await _detect_lots_unresolved_at_dates(
                data.copropriete_id, [data.date], [data.distribution_key_id],
            ),
        }

    async def _detect_lots_unresolved_at_dates(
        copro_id: str, call_dates_iso: list, key_ids: list,
    ) -> dict:
        """iter90cn : Detecte les lots dont l'owner-at-date ne peut pas etre
        resolu correctement pour au moins une des dates d'appel prevues.

        Cas detectes :
        - Lot avec share > 0 dans une cle utilisee mais lot.owner_id vide
          (deja capture par _detect_orphan_lots_for_budget mais reprend ici
          pour vue unifiee des warnings).
        - Lot avec current_owner_id mais aucune mutation ET call_date < creation_date
          du lot (cas rare).
        - Lot avec mutations dont from_owner_id vide sur la premiere mutation
          (chaine cassee).

        Retourne :
          {
            "unresolved_count": int,
            "warnings": [
              {"lot_id","lot_number","call_date","current_owner_id",
               "current_owner_name","reason"}
            ],
          }
        """
        from datetime import date as _date_cls
        if not copro_id or not call_dates_iso or not key_ids:
            return {"unresolved_count": 0, "warnings": []}

        # Sync defensif db.mutations
        await _ensure_mutations_synced_for_acp(db, copro_id)

        lots = await db.lots.find(
            {"copropriete_id": copro_id}, {"_id": 0},
        ).to_list(10000)
        lots_by_id = {lt["id"]: lt for lt in lots}
        owners_map = {}
        async for o in db.owners.find(
            {"copropriete_ids": copro_id}, {"_id": 0, "id": 1, "name": 1},
        ):
            owners_map[o["id"]] = o.get("name", "")
        keys = await db.distribution_keys.find(
            {"copropriete_id": copro_id, "id": {"$in": key_ids}}, {"_id": 0},
        ).to_list(1000)

        # Charge mutations groupees par lot
        muts_by_lot: dict = {}
        async for m in db.mutations.find(
            {"copropriete_id": copro_id}, {"_id": 0},
        ):
            lid = m.get("lot_id")
            if lid:
                muts_by_lot.setdefault(lid, []).append(m)
        for lid in muts_by_lot:
            muts_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

        def _resolve(lot_id: str, target_iso: str, fallback: str) -> tuple:
            """Retourne (owner_id, reason_if_broken)."""
            try:
                target = _date_cls.fromisoformat(target_iso)
            except Exception:
                return fallback, "call_date invalide"
            muts = muts_by_lot.get(lot_id, [])
            if not muts:
                # Aucune mutation : owner-at-date = fallback (lot.owner_id)
                if not fallback:
                    return "", "lot sans owner_id ET sans mutation history"
                return fallback, ""
            first_from = muts[0].get("from_owner_id") or ""
            if not first_from:
                # Premiere mutation avec from_owner_id vide -> chaine cassee
                return fallback, (
                    "premiere mutation avec from_owner_id vide "
                    "(historique incomplet)"
                )
            current = first_from
            for m in muts:
                try:
                    sd = _date_cls.fromisoformat(m.get("sale_date") or "")
                except Exception:
                    continue
                if sd <= target:
                    current = m.get("to_owner_id") or current
                else:
                    break
            return current, ""

        # iter90cu : Detecte les cles 100% phantom (toutes entrees pointent vers
        # des lot_ids inexistants). Ces cles beneficient du fallback iter90cr :
        # la distribution utilise les quotites des lots actuels. Ces phantom ne
        # constituent PAS une erreur - juste une info.
        keys_100pct_phantom = set()
        for k in keys:
            entries = [kle for kle in (k.get("lots") or []) if not kle.get("excluded")]
            if not entries:
                continue
            phantom_only = all(
                not kle.get("lot_id") or kle["lot_id"] not in lots_by_id
                for kle in entries
            )
            if phantom_only:
                keys_100pct_phantom.add(k["id"])

        # Collect lots avec share > 0 dans les cles utilisees
        lots_in_keys: dict = {}  # lot_id -> [(key_name, key_id)]
        for k in keys:
            for kle in (k.get("lots") or []):
                if kle.get("excluded"):
                    continue
                lid = kle.get("lot_id")
                share = float(kle.get("share", 0) or 0)
                if lid and share > 0:
                    lots_in_keys.setdefault(lid, []).append((k.get("name", ""), k["id"]))

        warnings = []
        phantom_keys_fallback_info = []  # iter90cu : infos softs
        for lid, key_infos in lots_in_keys.items():
            lot = lots_by_id.get(lid)
            if not lot:
                # iter90cu : si TOUTES les cles referencant ce phantom sont
                # 100% phantom, on skip le warning rouge (fallback iter90cr
                # produira une distribution correcte via quotites des lots reels)
                all_keys_are_100pct_phantom = all(
                    kid in keys_100pct_phantom for _, kid in key_infos
                )
                key_names = [kn for kn, _ in key_infos]
                if all_keys_are_100pct_phantom:
                    # Info soft uniquement (regroupee cote reponse)
                    phantom_keys_fallback_info.append({
                        "lot_id": lid,
                        "keys": key_names,
                    })
                    continue
                warnings.append({
                    "lot_id": lid,
                    "lot_number": "MISSING",
                    "call_date": call_dates_iso[0] if call_dates_iso else "",
                    "current_owner_id": "",
                    "current_owner_name": "",
                    "reason": (
                        f"lot_id {lid[:8]}... referencé dans cle(s) "
                        f"{', '.join(key_names)} mais n'existe pas en DB (phantom)"
                    ),
                    "keys": key_names,
                })
                continue
            current_owner = lot.get("owner_id", "") or ""
            for cd in call_dates_iso:
                resolved, reason = _resolve(lid, cd, current_owner)
                if not resolved or reason:
                    warnings.append({
                        "lot_id": lid,
                        "lot_number": lot.get("number", ""),
                        "call_date": cd,
                        "current_owner_id": current_owner,
                        "current_owner_name": owners_map.get(current_owner, ""),
                        "reason": reason or "aucun proprietaire resolu",
                        "keys": [kn for kn, _ in key_infos],
                    })

        # iter90cu : liste des cles concernees par le fallback (info soft)
        phantom_keys_summary = []
        for k in keys:
            if k["id"] not in keys_100pct_phantom:
                continue
            phantom_keys_summary.append({
                "key_id": k["id"],
                "key_name": k.get("name", ""),
                "entries_count": len(k.get("lots") or []),
            })

        return {
            "unresolved_count": len(warnings),
            "warnings": warnings,
            "phantom_keys_fallback": {
                "count": len(phantom_keys_summary),
                "keys": phantom_keys_summary,
                "phantom_lot_ids_ignored": len(phantom_keys_fallback_info),
            },
        }

    async def _detect_orphan_lots_for_budget(data: GenerateFromBudgetInput) -> dict:
        """iter90cc : detecte les lots avec share > 0 mais sans owner_id
        parmi les cles utilisees (budget.lines + reserve_fund + roulement_fund).
        Retourne :
          {
            "orphan_count": int,
            "orphan_share_percentage": float,  # % de shares perdues avant fix
            "keys_affected": [{"key_id","key_name","orphan_share","total_share"}],
            "lots": [{"lot_id","lot_number","share","keys":[<key_name>...]}],
          }
        """
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0})
        if not budget:
            return {"orphan_count": 0, "orphan_share_percentage": 0.0, "keys_affected": [], "lots": []}
        copro_id = data.copropriete_id or budget.get("copropriete_id", "")

        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10000)
        lots_by_id = {lt["id"]: lt for lt in lots}
        keys = await db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(1000)
        keys_map = {k["id"]: k for k in keys}

        # Collect all key_ids used by this budget generation
        used_key_ids = set()
        for bl in budget.get("lines", []):
            kid = bl.get("distribution_key_id")
            if kid:
                used_key_ids.add(kid)
        if data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.distribution_key_id:
            used_key_ids.add(data.reserve_fund.distribution_key_id)
        if data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.distribution_key_id:
            used_key_ids.add(data.roulement_fund.distribution_key_id)

        keys_affected = []
        orphan_lots_by_id: dict = {}
        phantom_keys_summary = []  # iter90cu : cles 100% phantom (fallback iter90cr)
        for kid in used_key_ids:
            key = keys_map.get(kid)
            if not key:
                continue
            active_kls = [kle for kle in key.get("lots", []) if not kle.get("excluded")]
            total_share = sum(float(kle.get("share", 0) or 0) for kle in active_kls)
            # iter90cu : detecte cle 100% phantom -> iter90cr fallback distribue
            # sur les lots reels de l'ACP avec leur quotity. AUCUNE perte de
            # shares donc on ignore ce cas du warning orphan.
            phantom_only = active_kls and all(
                not lots_by_id.get(kle.get("lot_id"))
                for kle in active_kls
            )
            if phantom_only:
                phantom_keys_summary.append({
                    "key_id": kid,
                    "key_name": key.get("name", ""),
                    "phantom_total_share": round(total_share, 4),
                    "entries_count": len(active_kls),
                })
                continue  # skip orphan detection - fallback iter90cr applique
            orphan_share = 0.0
            for kle in active_kls:
                lot = lots_by_id.get(kle.get("lot_id"))
                if not lot or not lot.get("owner_id"):
                    share = float(kle.get("share", 0) or 0)
                    if share > 0:
                        orphan_share += share
                        # Aggreger par lot_id
                        lid = kle.get("lot_id") or "MISSING"
                        entry = orphan_lots_by_id.setdefault(lid, {
                            "lot_id": lid,
                            "lot_number": (lot or {}).get("number", "MISSING") if lot else "MISSING",
                            "share": share,
                            "keys": [],
                        })
                        entry["keys"].append(key.get("name", ""))
            if orphan_share > 0:
                keys_affected.append({
                    "key_id": kid,
                    "key_name": key.get("name", ""),
                    "orphan_share": round(orphan_share, 4),
                    "total_share": round(total_share, 4),
                    "orphan_percentage": round(100.0 * orphan_share / total_share, 2) if total_share > 0 else 0.0,
                })

        # Percentage global = moyenne ponderee des cles affectees
        if keys_affected:
            worst_pct = max(k["orphan_percentage"] for k in keys_affected)
        else:
            worst_pct = 0.0

        return {
            "orphan_count": len(orphan_lots_by_id),
            "orphan_share_percentage": worst_pct,
            "keys_affected": keys_affected,
            "lots": list(orphan_lots_by_id.values()),
            "phantom_keys_fallback": phantom_keys_summary,  # iter90cu
        }

    @router.post("/generate-from-budget")
    async def generate_from_budget_endpoint(data: GenerateFromBudgetInput, request: Request):
        """Create N fund calls in DB from an approved budget. Calls are persisted
        with status='pending'. Reserve fund (if enabled) is added to call #1 only."""
        return await _generate_from_budget(data, persist=True, request=request)

    async def _generate_from_budget(data: GenerateFromBudgetInput, persist: bool, request: Optional[Request] = None):
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0})
        if not budget:
            raise HTTPException(404, "Budget non trouve")
        if budget.get("status") != "approved":
            raise HTTPException(400, "Budget non approuve - approuvez-le d'abord")
        copro_id = data.copropriete_id or budget.get("copropriete_id", "")
        if not copro_id:
            raise HTTPException(400, "copropriete_id manquant")
        if data.frequency not in (1, 2, 3, 4, 6, 12):
            raise HTTPException(400, "Frequence autorisee: 1, 2, 3, 4, 6, 12 appels par an")

        # iter90cj (Root Cause 2) : backfill l'engagement reserve/roulement sur
        # le budget doc a partir des inputs du wizard. Garantit que meme si
        # l'utilisateur n'a pas re-poste le budget avec les 4 champs, le
        # budget porte bien l'engagement pour _compute_mutation_breakdown.
        # Uniquement en mode persist=True (evite d'ecrire pendant un preview).
        if persist:
            _backfill_set = {}
            if data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.amount:
                _backfill_set["reserve_fund_amount"] = round(float(data.reserve_fund.amount), 2)
                if data.reserve_fund.distribution_key_id:
                    _backfill_set["reserve_fund_key_id"] = data.reserve_fund.distribution_key_id
            if data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.amount:
                _backfill_set["roulement_fund_amount"] = round(float(data.roulement_fund.amount), 2)
                if data.roulement_fund.distribution_key_id:
                    _backfill_set["roulement_fund_key_id"] = data.roulement_fund.distribution_key_id
            if _backfill_set:
                await db.budgets.update_one(
                    {"id": budget["id"]}, {"$set": _backfill_set},
                )
                budget.update(_backfill_set)

        fy = await db.fiscal_years.find_one({"id": budget["fiscal_year_id"]}, {"_id": 0})
        fy_name = fy.get("name", "") if fy else ""

        # Pre-fetch lots, owners and distribution keys
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10000)
        owners_map = {o["id"]: o for o in await db.owners.find({}, {"_id": 0}).to_list(10000)}
        keys = await db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(1000)
        keys_map = {k["id"]: k for k in keys}

        # iter90ag : prorata mutation pour PROVISIONS uniquement.
        # On charge une fois toutes les mutations de l'ACP pour eviter N+1 queries.
        # Groupe par lot_id, tri par sale_date ASC pour la construction de segments.
        # iter90cj : sync defensif avant lecture (Root Cause 1)
        await _ensure_mutations_synced_for_acp(db, copro_id)
        from datetime import date as _date_cls
        mutations_by_lot: dict = {}
        mutations_all = await db.mutations.find(
            {"copropriete_id": copro_id}, {"_id": 0}
        ).to_list(10000)
        for m in mutations_all:
            lid = m.get("lot_id")
            if lid and m.get("sale_date") and m.get("from_owner_id") and m.get("to_owner_id"):
                mutations_by_lot.setdefault(lid, []).append(m)
        for lid in mutations_by_lot:
            mutations_by_lot[lid].sort(key=lambda x: x.get("sale_date") or "")

        def _split_lot_entry_by_mutations(entry: dict, period_start: str, period_end: str) -> list:
            """iter90ag : Split un lot_entry en plusieurs entrees prorata temporis
            en fonction des mutations survenues dans [period_start, period_end].

            Regle : si mutation le jour D, le vendeur (from_owner) supporte
            jusqu'au jour D exclu, l'acheteur (to_owner) supporte a partir de D.
            Exemple : periode 01-01 -> 31-03 (90 jours), mutation le 15-03 :
                vendeur = 73 jours (01-01 -> 14-03), acheteur = 17 jours (15-03 -> 31-03).

            Retourne une liste d'entries clonees avec owner_id et amount ajustes.
            Preserve l'entree originale si aucune mutation dans la periode.
            """
            lid = entry.get("lot_id")
            muts_all = mutations_by_lot.get(lid, [])
            if not muts_all:
                return [entry]
            try:
                p_start = _date_cls.fromisoformat(period_start)
                p_end = _date_cls.fromisoformat(period_end)
            except Exception:
                return [entry]
            if p_end < p_start:
                return [entry]

            in_period = []
            for m in muts_all:
                try:
                    sd = _date_cls.fromisoformat(m["sale_date"])
                except Exception:
                    continue
                if p_start <= sd <= p_end:
                    in_period.append((sd, m))
            if not in_period:
                return [entry]

            total_days = (p_end - p_start).days + 1
            if total_days <= 0:
                return [entry]

            # Construction des segments temporels
            segments: list = []  # list of (owner_id, days)
            cursor = p_start
            current_owner = in_period[0][1]["from_owner_id"]
            for sd, m in in_period:
                if sd <= cursor:
                    # Mutation avant/au curseur -> shift owner sans creer de segment
                    current_owner = m["to_owner_id"]
                    continue
                days_before = (sd - cursor).days  # exclusif de sd
                if days_before > 0 and current_owner:
                    segments.append((current_owner, days_before))
                cursor = sd
                current_owner = m["to_owner_id"]
            # Segment final (curseur -> p_end inclus)
            final_days = (p_end - cursor).days + 1
            if final_days > 0 and current_owner:
                segments.append((current_owner, final_days))

            if not segments:
                return [entry]
            # Dedupe : si tous les segments pointent vers le meme owner (mutations
            # qui se compensent), ne rien splitter.
            distinct_owners = {oid for oid, _ in segments}
            if len(distinct_owners) == 1:
                # Meme owner sur toute la periode -> juste corriger l'owner
                if segments[0][0] == entry.get("owner_id"):
                    return [entry]
                new_entry = dict(entry)
                new_entry["owner_id"] = segments[0][0]
                own = owners_map.get(segments[0][0]) or {}
                new_entry["owner_name"] = own.get("name", "")
                new_entry["vcs_code"] = own.get("vcs_code", "")
                new_entry["prorata_days"] = final_days
                new_entry["prorata_total_days"] = total_days
                return [new_entry]

            base_amount = float(entry.get("amount", 0) or 0)
            result: list = []
            allocated = 0.0
            for owner_id, days in segments[:-1]:
                slice_amount = round(base_amount * days / total_days, 2)
                own = owners_map.get(owner_id) or {}
                result.append({
                    **entry,
                    "owner_id": owner_id,
                    "owner_name": own.get("name", ""),
                    "vcs_code": own.get("vcs_code", ""),
                    "amount": slice_amount,
                    "prorata_days": days,
                    "prorata_total_days": total_days,
                })
                allocated += slice_amount
            # Dernier segment absorbe le reste (evite le drift d'arrondi)
            last_owner, last_days = segments[-1]
            last_amount = round(base_amount - allocated, 2)
            own = owners_map.get(last_owner) or {}
            result.append({
                **entry,
                "owner_id": last_owner,
                "owner_name": own.get("name", ""),
                "vcs_code": own.get("vcs_code", ""),
                "amount": last_amount,
                "prorata_days": last_days,
                "prorata_total_days": total_days,
            })
            return result

        def _resolve_owner_at_date(lot_id: str, target_date_iso: str, fallback_owner_id: str) -> str:
            """iter90aj : Retourne le proprietaire du lot a la date cible en
            marchant dans l'historique des mutations. Utilise pour reserve/
            roulement quand l'appel est cree avec une date retroactive (ex.
            budget 2026 vote apres une mutation, avec date d'appel 01/10/2025
            anterieure a la mutation 17/11/2025)."""
            muts = mutations_by_lot.get(lot_id, [])
            if not muts:
                return fallback_owner_id
            try:
                target = _date_cls.fromisoformat(target_date_iso)
            except Exception:
                return fallback_owner_id
            current = muts[0].get("from_owner_id") or fallback_owner_id
            for m in muts:
                try:
                    sd = _date_cls.fromisoformat(m.get("sale_date") or "")
                except Exception:
                    continue
                if sd <= target:
                    current = m.get("to_owner_id") or current
                else:
                    break
            return current

        def _rebind_owner_at_call_date(entries: list, call_date_iso: str, period_end_iso: str = "") -> list:
            """iter90aj + iter90cg : Reserve/roulement/provisions -> re-affecte
            chaque entree au proprietaire qui detenait le lot a la DATE EFFECTIVE
            de charge de l'appel.

            Date effective = min(call_date, period_end) :
            - Cas nominal (call_date <= period_end) : effective = call_date
              (comportement iter90aj/iter90cd : owner-at-call-date).
            - Cas d'un appel emis EN RETARD apres la fin de la periode
              (call_date > period_end) : effective = period_end, pour
              attribuer le montant au proprietaire durant la periode
              (le vendeur si la mutation est posterieure a la periode).

            Regle metier : reserve/roulement/provisions sont des charges de
            periode. Un appel Q3 2025 (01.07-30.09) emis le 20.10 apres une
            mutation le 01.10 doit revenir au VENDEUR (proprietaire durant
            Q3), pas au nouvel acquereur (owner-at-call-date).

            Le prorata temporis pour un appel dont la periode CHEVAUCHE une
            mutation est toujours gere par l'OD MUT-P separee (iter90cf).
            """
            def _effective(cdate: str) -> str:
                if not period_end_iso or not cdate:
                    return cdate
                try:
                    return period_end_iso if period_end_iso < cdate else cdate
                except Exception:
                    return cdate
            eff = _effective(call_date_iso)
            result = []
            for e in entries:
                lid = e.get("lot_id") or ""
                current_oid = e.get("owner_id") or ""
                correct_oid = _resolve_owner_at_date(lid, eff, current_oid)
                if correct_oid == current_oid:
                    result.append(e)
                    continue
                own = owners_map.get(correct_oid) or {}
                result.append({
                    **e,
                    "owner_id": correct_oid,
                    "owner_name": own.get("name", ""),
                    "vcs_code": own.get("vcs_code", ""),
                })
            return result

        def _distribute_amount(amount: float, key_id: str) -> list:
            """Distribute amount on LOTS according to the given distribution key.
            Fallback to quotities if key not found.
            Retourne une LISTE de dicts {lot_id, lot_number, parent_lot_id,
            owner_id, owner_name, vcs_code, amount, share}. Plusieurs entrees
            peuvent partager le meme owner si l'owner a plusieurs lots, ce qui
            permet la cascade parent/enfant cote frontend.

            iter90cb : les lots sans owner (orphelins) sont EXCLUS du calcul du
            denominateur `total_shares`. Leurs shares sont ainsi redistribuees
            proportionnellement sur les proprietaires actuels. Cela garantit que
            la somme des amounts distribues == amount (bug 3.6% Matexi).

            iter90cr : si la cle EXISTE mais que TOUTES ses entrees sont
            invalides (phantom lot_ids ou lots sans owner), fallback sur la
            distribution par quotites des lots actuels de l'ACP. Evite le cas
            "0 proprietaires" quand la cle contient uniquement des references
            perimees (lots supprimes/recrees avec de nouveaux IDs).
            """

            def _fallback_by_quotity() -> list:
                """Distribution de secours : quotites des lots ACTUELS avec owner."""
                _entries: list = []
                total_quotity = sum(lt.get("quotity", 0) for lt in lots if lt.get("owner_id"))
                for lot in lots:
                    if not lot.get("owner_id"):
                        continue
                    owner = owners_map.get(lot["owner_id"]) or {}
                    share_ratio = lot.get("quotity", 0) / total_quotity if total_quotity > 0 else 0
                    _entries.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "parent_lot_id": lot.get("parent_lot_id", "") or "",
                        "owner_id": lot["owner_id"],
                        "owner_name": owner.get("name", ""),
                        "vcs_code": owner.get("vcs_code", ""),
                        "amount": amount * share_ratio,
                        "share": float(lot.get("quotity", 0)),
                    })
                return _entries

            entries: list = []
            if key_id and key_id in keys_map:
                key = keys_map[key_id]
                # iter90ac : exclut les lots marques excluded=True
                # iter90cb : exclut aussi les lots orphelins (sans owner_id)
                # pour eviter la perte de valeur sur les shares non attribuables.
                lots_by_id = {lt["id"]: lt for lt in lots}
                # iter90du : index by normalized lot_number for fallback matching
                # (robuste apres re-import : les lot_ids peuvent avoir change mais
                # les numeros de lot restent stables).
                def _norm_lot_num(s: str) -> str:
                    return (str(s or "")).strip().lstrip("0") or "0"
                lots_by_number = {}
                for lt in lots:
                    lots_by_number[_norm_lot_num(lt.get("number", ""))] = lt

                # Pour chaque entree de la cle, resoud le lot actuel (par lot_id
                # ou par lot_number normalise). Conserve la SHARE definie dans
                # la cle (les quotites/shares de la cle sont la source de verite,
                # meme apres re-import).
                resolved_kls: list = []  # [(kl, lot)]
                for kle in key.get("lots", []):
                    if kle.get("excluded"):
                        continue
                    kle_lot_id = kle.get("lot_id") or ""
                    lot = lots_by_id.get(kle_lot_id)
                    if not lot or not lot.get("owner_id"):
                        # iter90du fallback : match par lot_number normalise
                        num_key = _norm_lot_num(kle.get("lot_number", ""))
                        if num_key and num_key != "0":
                            candidate = lots_by_number.get(num_key)
                            if candidate and candidate.get("owner_id"):
                                lot = candidate
                    if not lot or not lot.get("owner_id"):
                        continue
                    resolved_kls.append((kle, lot))

                total_shares = sum(float(kle.get("share", 0) or 0) for kle, _ in resolved_kls)
                # iter90cr : fallback si la cle est totalement invalide
                # (aucune entree resolvable, meme par lot_number)
                if not resolved_kls or total_shares <= 0:
                    return _fallback_by_quotity()
                for kle, lot in resolved_kls:
                    owner = owners_map.get(lot["owner_id"]) or {}
                    share_ratio = float(kle.get("share", 0) or 0) / total_shares if total_shares > 0 else 0
                    entries.append({
                        "lot_id": lot["id"],
                        "lot_number": lot.get("number", ""),
                        "parent_lot_id": lot.get("parent_lot_id", "") or "",
                        "owner_id": lot["owner_id"],
                        "owner_name": owner.get("name", ""),
                        "vcs_code": owner.get("vcs_code", ""),
                        "amount": amount * share_ratio,
                        "share": float(kle.get("share", 0) or 0),
                    })
            else:
                entries = _fallback_by_quotity()
            return entries

        # Compute schedule
        interval_months = 12 // data.frequency
        n_calls = data.frequency
        budget_lines = budget.get("lines", [])
        budget_total = round(sum(bl.get("amount", 0) for bl in budget_lines), 2)
        # Fiscal year end for the last call's period_end
        fy_end = (fy or {}).get("end_date", "") or ""
        # Per call portion of each budget line
        results = []
        # Pre-calcul des dates de chaque appel pour deduire les period_start/period_end
        call_dates = [_add_months(data.start_date, i * interval_months) for i in range(n_calls)]
        for i in range(n_calls):
            call_date = call_dates[i]
            due_date = (datetime.strptime(call_date, "%Y-%m-%d")
                        + timedelta(days=data.due_offset_days or 30)).strftime("%Y-%m-%d")
            # period_end = veille du prochain appel, ou fy_end pour le dernier
            if i + 1 < n_calls:
                next_dt = datetime.strptime(call_dates[i + 1], "%Y-%m-%d")
                period_end = (next_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                fallback_period_end = (datetime.strptime(call_date, "%Y-%m-%d")
                                        + timedelta(days=interval_months * 30 - 1)).strftime("%Y-%m-%d")
                # Garde-fou : si le budget demarre tard dans l'annee et que le
                # dernier appel trimestriel deborde apres la fin d'exercice,
                # ne pas plafonner sur fy_end (period_end < period_start
                # donnerait une periode inversee) - retomber sur le calcul
                # base sur l'intervalle.
                period_end = fy_end if (fy_end and fy_end >= call_date) else fallback_period_end
            # Aggregate distribution by LOT combining all budget lines (iter85b)
            # iter90ag : cle d'agregation = (lot_id, owner_id) pour permettre
            # le prorata mutation (plusieurs owners sur le meme lot dans une meme
            # periode). Reserve/roulement gardent l'owner courant (pas de split).
            lot_agg: dict = {}  # (lot_id, owner_id) -> {meta, amount, share}

            def _merge_into_lot_agg(entries: list) -> None:
                for e in entries:
                    lid = e.get("lot_id") or ""
                    oid = e.get("owner_id") or ""
                    if not lid:
                        continue
                    key = (lid, oid)
                    if key not in lot_agg:
                        lot_agg[key] = {
                            "lot_id": lid,
                            "lot_number": e.get("lot_number", ""),
                            "parent_lot_id": e.get("parent_lot_id", "") or "",
                            "owner_id": oid,
                            "owner_name": e.get("owner_name", ""),
                            "vcs_code": e.get("vcs_code", ""),
                            "amount": 0.0,
                            "share": 0.0,
                            "prorata_days": e.get("prorata_days"),
                            "prorata_total_days": e.get("prorata_total_days"),
                        }
                    lot_agg[key]["amount"] += e.get("amount", 0)
                    # share : on prend le max plutot que la somme (sinon on
                    # multiplie quand plusieurs budget_lines partagent la meme cle)
                    if e.get("share", 0) > lot_agg[key]["share"]:
                        lot_agg[key]["share"] = float(e.get("share", 0))

            call_total = 0.0
            line_details = []
            for bl in budget_lines:
                bl_per_call = round(bl.get("amount", 0) / n_calls, 2)
                if abs(bl_per_call) < 0.01:
                    continue
                line_dist = _distribute_amount(bl_per_call, bl.get("distribution_key_id", ""))
                # iter90cd + iter90cg : PLUS DE SPLIT prorata dans l'appel lui-meme
                # (regle metier belge : "Aucune ventilation entre vendeur et
                # acquereur n'est effectuee pour cet appel"). On reaffecte chaque
                # entree au proprietaire qui detenait le lot a la DATE EFFECTIVE
                # de charge (min(call_date, period_end)) : appel en retard apres
                # une mutation posterieure a la periode -> vendeur (proprietaire
                # durant la periode). La repartition prorata temporis vendeur/
                # acheteur pour un appel dont la PERIODE chevauche une mutation
                # est gere par l'OD "Mutation Prorata" (properties.py::
                # _apply_mutation_writes, generate_prorata_mut_ods_for_call).
                line_dist = _rebind_owner_at_call_date(line_dist, call_date, period_end)
                line_details.append({
                    "account_number": bl.get("account_number", ""),
                    "account_name": bl.get("account_name", ""),
                    "distribution_key_id": bl.get("distribution_key_id", ""),
                    "distribution_key_name": keys_map.get(bl.get("distribution_key_id", ""), {}).get("name", "Tantiemes"),
                    "amount": bl_per_call,
                })
                call_total += bl_per_call
                _merge_into_lot_agg(line_dist)

            # Reserve fund injecte sur appel #1 SEULEMENT si fonds reserve sans frequency propre.
            # Si frequency reserve definie, le fonds genere sa propre serie d'appels (plus bas).
            reserve_added = 0.0
            reserve_has_own_schedule = bool(data.reserve_fund and data.reserve_fund.enabled
                                             and data.reserve_fund.amount > 0
                                             and (data.reserve_fund.frequency or 0) > 0)
            if i == 0 and data.reserve_fund and data.reserve_fund.enabled and data.reserve_fund.amount > 0 and not reserve_has_own_schedule:
                reserve_amount = float(data.reserve_fund.amount)
                reserve_dist = _distribute_amount(reserve_amount, data.reserve_fund.distribution_key_id or "")
                # iter90aj + iter90cg : rebind sur date effective = min(call_date, period_end).
                # Cas cible : appel Q3 emis en retard apres mutation posterieure a la periode -> vendeur.
                reserve_dist = _rebind_owner_at_call_date(reserve_dist, call_date, period_end)
                # iter90ge : REGLE METIER PCMN BELGE - l'arrondi euro
                # superieur (surprovision) ne s'applique QU'AUX PROVISIONS
                # POUR CHARGES ORDINAIRES. Les fonds de reserve et roulement
                # sont des CAPITAUX (comptes 160/100) votes par l'AG, dont
                # le montant est fige et ne doit JAMAIS etre depasse par
                # arrondi. On ignore donc round_up=True cote serveur pour
                # la reserve, meme si un client envoie ce flag.
                # if data.reserve_fund.round_up:  # <-- desactive iter90ge
                #     import math as _m
                #     for e in reserve_dist:
                #         e["amount"] = float(_m.ceil(float(e.get("amount", 0) or 0)))
                #     reserve_amount = round(sum(e.get("amount", 0) for e in reserve_dist), 2)
                line_details.append({
                    "account_number": "RESERVE",
                    "account_name": data.reserve_fund.label or "Fonds de reserve",
                    "distribution_key_id": data.reserve_fund.distribution_key_id or "",
                    "distribution_key_name": keys_map.get(data.reserve_fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                    "amount": reserve_amount,
                    "is_reserve": True,
                    "round_up": False,  # iter90ge : jamais d'arrondi sur reserve
                })
                call_total += reserve_amount
                reserve_added = reserve_amount
                _merge_into_lot_agg(reserve_dist)

            # Fonds de roulement injecte sur appel #1 SEULEMENT si pas de frequency propre.
            roulement_added = 0.0
            roul_has_own_schedule = bool(data.roulement_fund and data.roulement_fund.enabled
                                          and data.roulement_fund.amount > 0
                                          and (data.roulement_fund.frequency or 0) > 0)
            if i == 0 and data.roulement_fund and data.roulement_fund.enabled and data.roulement_fund.amount > 0 and not roul_has_own_schedule:
                roul_amount = float(data.roulement_fund.amount)
                roul_dist = _distribute_amount(roul_amount, data.roulement_fund.distribution_key_id or "")
                # iter90aj + iter90cg : rebind sur date effective = min(call_date, period_end).
                roul_dist = _rebind_owner_at_call_date(roul_dist, call_date, period_end)
                # iter90ge : arrondi euro superieur INTERDIT sur roulement
                # (meme regle que la reserve : capital vote, montant fige).
                # if data.roulement_fund.round_up:  # <-- desactive iter90ge
                #     import math as _m
                #     for e in roul_dist:
                #         e["amount"] = float(_m.ceil(float(e.get("amount", 0) or 0)))
                #     roul_amount = round(sum(e.get("amount", 0) for e in roul_dist), 2)
                lbl = data.roulement_fund.label or "Fonds de roulement"
                mode_lbl = "(creation)" if (data.roulement_fund.mode or "create") == "create" else "(augmentation)"
                line_details.append({
                    "account_number": "ROULEMENT",
                    "account_name": f"{lbl} {mode_lbl}",
                    "distribution_key_id": data.roulement_fund.distribution_key_id or "",
                    "distribution_key_name": keys_map.get(data.roulement_fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                    "amount": roul_amount,
                    "is_roulement": True,
                    "roulement_mode": data.roulement_fund.mode or "create",
                    "round_up": False,  # iter90ge : jamais d'arrondi sur roulement
                })
                call_total += roul_amount
                roulement_added = roul_amount
                _merge_into_lot_agg(roul_dist)

            distribution = []
            for (lid, _oid), agg in lot_agg.items():
                dist_entry = {
                    "lot_id": lid,
                    "lot_number": agg["lot_number"],
                    "parent_lot_id": agg["parent_lot_id"],
                    "owner_id": agg["owner_id"],
                    "owner_name": agg["owner_name"],
                    "vcs_code": agg["vcs_code"],
                    "share": round(agg["share"], 4),
                    "amount": round(agg["amount"], 2),
                    "paid": False,
                    "paid_date": "",
                }
                # iter90ag : trace du prorata sur les lignes issues d'une mutation
                if agg.get("prorata_days") is not None:
                    dist_entry["prorata_days"] = agg["prorata_days"]
                    dist_entry["prorata_total_days"] = agg["prorata_total_days"]
                distribution.append(dist_entry)
            # iter90w : garantit sum(distribution.amount) == call_total (evite
            # les 0,01 EUR de derive par appel qui deviennent 0,04 sur 4 trimestres).
            _snap_distribution_to_total(distribution, round(call_total, 2))
            distribution.sort(key=lambda x: (x["owner_name"] or "", x["lot_number"] or ""))

            call_label = ["Annuel", "Semestriel", "Quadrimestriel", "Trimestriel", "Bi-mensuel", "Mensuel"][
                {1: 0, 2: 1, 3: 2, 4: 3, 6: 4, 12: 5}[n_calls]
            ]
            name = f"{call_label} {i + 1}/{n_calls} - {fy_name}".strip(" -")

            results.append({
                "name": name,
                "date": call_date,
                "due_date": due_date,
                "period_start": call_date,
                "period_end": period_end,
                "total_amount": round(call_total, 2),
                "reserve_amount": round(reserve_added, 2),
                "roulement_amount": round(roulement_added, 2),
                "roulement_mode": (data.roulement_fund.mode if (data.roulement_fund and data.roulement_fund.enabled) else "") or "",
                "lines": line_details,
                "distribution": distribution,
                "fiscal_year_id": budget["fiscal_year_id"],
                "call_type": "provisions",
                "budget_id": budget["id"],
                "copropriete_id": copro_id,
            })

        # --- Series independantes pour Reserve / Roulement avec frequency propre ---
        def _generate_independent_series(fund, fund_kind: str, account_tag: str):
            """Genere N appels independants pour un fonds (reserve ou roulement).
            fund_kind = 'reserve' ou 'roulement'.
            account_tag = 'RESERVE' ou 'ROULEMENT'.
            """
            if not fund or not fund.enabled or fund.amount <= 0 or (fund.frequency or 0) <= 0:
                return []
            freq = int(fund.frequency)
            if freq not in (1, 2, 3, 4, 6, 12):
                raise HTTPException(400, f"Frequence {fund_kind} invalide : {freq}")
            sdate = fund.start_date or data.start_date
            offset = int(fund.due_offset_days or 30)
            interval = 12 // freq
            per_call = round(fund.amount / freq, 2)
            label_arr = ["Annuel", "Semestriel", "Quadrimestriel", "Trimestriel", "Bi-mensuel", "Mensuel"]
            freq_label = label_arr[{1: 0, 2: 1, 3: 2, 4: 3, 6: 4, 12: 5}[freq]]
            base_label = fund.label or ("Fonds de reserve" if fund_kind == "reserve" else "Fonds de roulement")
            series = []
            for i in range(freq):
                cd = _add_months(sdate, i * interval)
                dd = (datetime.strptime(cd, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
                # period_end = veille du prochain appel, ou cd + interval - 1 jour pour le dernier
                if i + 1 < freq:
                    next_cd = _add_months(sdate, (i + 1) * interval)
                    pend = (datetime.strptime(next_cd, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                else:
                    pend = ((fy or {}).get("end_date") or
                            (datetime.strptime(cd, "%Y-%m-%d") + timedelta(days=interval * 30 - 1)).strftime("%Y-%m-%d"))
                dist = _distribute_amount(per_call, fund.distribution_key_id or "")
                # iter90aj + iter90cg : rebind sur date effective = min(cd, pend).
                # Cas cible : appel reserve/roulement Q3 emis en retard apres
                # mutation posterieure a la periode -> vendeur (proprietaire
                # durant la periode Q3), pas nouvel acheteur (owner-at-call-date).
                dist = _rebind_owner_at_call_date(dist, cd, pend)
                # iter90eo : arrondi au superieur (EUR entier) par lot si active.
                _per_call_actual = per_call
                if getattr(fund, "round_up", False):
                    import math as _m
                    for e in dist:
                        e["amount"] = float(_m.ceil(float(e.get("amount", 0) or 0)))
                    _per_call_actual = round(sum(e.get("amount", 0) for e in dist), 2)
                # iter85b : distribution par lot (cascade parent/enfant)
                distribution = []
                for e in dist:
                    distribution.append({
                        "lot_id": e.get("lot_id", ""),
                        "lot_number": e.get("lot_number", ""),
                        "parent_lot_id": e.get("parent_lot_id", "") or "",
                        "owner_id": e.get("owner_id", ""),
                        "owner_name": e.get("owner_name", ""),
                        "vcs_code": e.get("vcs_code", ""),
                        "share": round(float(e.get("share", 0)), 4),
                        "amount": round(float(e.get("amount", 0)), 2),
                        "paid": False,
                        "paid_date": "",
                    })
                # iter90w : garantit sum(distribution.amount) == per_call
                # iter90eo : si round_up actif, on ne resnap PAS (on veut garder
                # les valeurs arrondies au superieur, la somme reelle peut
                # depasser per_call - c'est la surprovision voulue).
                if not getattr(fund, "round_up", False):
                    _snap_distribution_to_total(distribution, per_call)
                distribution.sort(key=lambda x: (x["owner_name"] or "", x["lot_number"] or ""))
                line_tag = {"account_number": account_tag,
                            "account_name": base_label,
                            "distribution_key_id": fund.distribution_key_id or "",
                            "distribution_key_name": keys_map.get(fund.distribution_key_id or "", {}).get("name", "Tantiemes"),
                            "amount": _per_call_actual,
                            "round_up": bool(getattr(fund, "round_up", False))}
                if fund_kind == "reserve":
                    line_tag["is_reserve"] = True
                else:
                    line_tag["is_roulement"] = True
                    line_tag["roulement_mode"] = (fund.mode or "create") if hasattr(fund, 'mode') else "create"
                call_doc = {
                    "name": f"{base_label} - {freq_label} {i + 1}/{freq} - {fy_name}".strip(" -"),
                    "date": cd,
                    "due_date": dd,
                    "period_start": cd,
                    "period_end": pend,
                    "total_amount": _per_call_actual,
                    "reserve_amount": _per_call_actual if fund_kind == "reserve" else 0.0,
                    "roulement_amount": _per_call_actual if fund_kind == "roulement" else 0.0,
                    "roulement_mode": (fund.mode or "create") if (fund_kind == "roulement" and hasattr(fund, 'mode')) else "",
                    "lines": [line_tag],
                    "distribution": distribution,
                    "fiscal_year_id": budget["fiscal_year_id"],
                    "call_type": fund_kind,
                    "budget_id": budget["id"],
                    "copropriete_id": copro_id,
                }
                series.append(call_doc)
            return series

        results.extend(_generate_independent_series(data.reserve_fund, "reserve", "RESERVE"))
        results.extend(_generate_independent_series(data.roulement_fund, "roulement", "ROULEMENT"))

        summary = {
            "n_calls": n_calls,
            "interval_months": interval_months,
            "budget_total": budget_total,
            "reserve_total": round(data.reserve_fund.amount if (data.reserve_fund and data.reserve_fund.enabled) else 0.0, 2),
            "roulement_total": round(data.roulement_fund.amount if (data.roulement_fund and data.roulement_fund.enabled) else 0.0, 2),
            "grand_total": round(sum(c["total_amount"] for c in results), 2),
        }

        if not persist:
            return {"calls": results, "summary": summary, "persisted": False}

        # Persist
        created_ids = []
        for c in results:
            doc = {
                "id": str(uuid.uuid4()),
                "name": c["name"],
                "date": c["date"],
                "due_date": c["due_date"],
                "period_start": c.get("period_start", c["date"]),
                "period_end": c.get("period_end", c["due_date"]),
                "fiscal_year_id": c["fiscal_year_id"],
                "description": f"Appel auto. issu du budget {budget.get('name','')}",
                "total_amount": c["total_amount"],
                "reserve_amount": c["reserve_amount"],
                "roulement_amount": c.get("roulement_amount", 0),
                "roulement_mode": c.get("roulement_mode", ""),
                "call_type": c.get("call_type", "provisions"),
                "lines": c["lines"],
                "distribution": c["distribution"],
                "status": "pending",
                "budget_id": budget["id"],
                "copropriete_id": copro_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            _sid = getattr(request.state, "syndic_id", None) if request else None
            if _sid:
                doc["syndic_id"] = _sid
            await db.fund_calls.insert_one(doc)
            try:
                await generate_sale_entry(db, doc)
            except Exception as e:
                print(f"[auto-entry] sale bulk failed: {e}")
            # iter90cf : genere retroactivement les OD MUT-P si l'appel est
            # cree apres une mutation dont la sale_date tombe dans la periode
            # couverte par cet appel.
            try:
                _mut_stats = await generate_prorata_mut_ods_for_call(db, doc)
                if _mut_stats.get("created"):
                    print(f"[iter90cf] {_mut_stats['created']} OD MUT-P retroactives pour {doc.get('name','?')}")
            except HTTPException:
                # iter90ck : propager les erreurs visibles (budget roulement manquant).
                raise
            except Exception as _e:
                print(f"[iter90cf] retroactive OD (budget) skipped: {_e}")
            created_ids.append(doc["id"])

        return {"calls": results, "summary": summary, "persisted": True, "created_ids": created_ids}

    @router.post("/regenerate-from-budget")
    async def regenerate_from_budget(data: GenerateFromBudgetInput, request: Request):
        """Delete future unpaid fund calls linked to this budget, then regenerate
        the schedule starting at data.start_date.

        Definition of 'non echu' (deletable): existing call with `date >= start_date`
        AND no `paid` flag set on ANY distribution row. Calls with at least one
        recorded payment are PRESERVED (history protection).
        """
        budget = await db.budgets.find_one({"id": data.budget_id}, {"_id": 0})
        if not budget:
            raise HTTPException(404, "Budget non trouve")
        if budget.get("status") != "approved":
            raise HTTPException(400, "Budget non approuve")
        existing = await db.fund_calls.find(
            {"budget_id": data.budget_id, "date": {"$gte": data.start_date}},
            {"_id": 0}
        ).to_list(1000)
        deletable_ids = []
        preserved = 0
        for c in existing:
            has_paid = any(d.get("paid") for d in c.get("distribution", []))
            if has_paid:
                preserved += 1
            else:
                deletable_ids.append(c["id"])
        if deletable_ids:
            await db.fund_calls.delete_many({"id": {"$in": deletable_ids}})
            for did in deletable_ids:
                try:
                    await _delete_auto_entries(db, "fund_call", did)
                except Exception:
                    pass
                # iter90ch : contre-passe aussi les OD MUT-P retroactives
                await reverse_post_mutation_ods_for_call(
                    db, did, reason=f"regenerate-from-budget {data.budget_id}"
                )
        result = await _generate_from_budget(data, persist=True, request=request)
        result["deleted_count"] = len(deletable_ids)
        result["preserved_count"] = preserved
        return result

    # ---- REGENERATE DISTRIBUTION FROM EXISTING LINES ----
    async def _rebuild_distribution_from_lines(call: dict) -> tuple:
        """Reconstruit la distribution d'un appel a partir de ses `lines` (budget
        categories avec distribution_key_id). Ne modifie PAS le call lui-meme.

        Retourne (distribution_list, lot_total_map) :
          - distribution_list : [{lot_id, lot_number, owner_id, owner_name, vcs_code,
                                  share, amount, paid, paid_date}]
          - lot_total_map : {lot_id: amount_total} pour debug
        """
        lines = call.get("lines") or []
        if not lines:
            return [], {}

        copro_id = call.get("copropriete_id", "")
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(5000)
        owners = await db.owners.find({}, {"_id": 0}).to_list(5000)
        owners_map = {o["id"]: o for o in owners}

        # Accumulateur par lot_id
        lot_amounts: dict = {}  # lot_id -> total
        lot_shares: dict = {}   # lot_id -> sum of shares (pour info)

        # Cache cles
        keys_cache: dict = {}
        total_quotity_global = sum(float(lt.get("quotity", 0) or 0) for lt in lots)

        for ln in lines:
            key_id = ln.get("distribution_key_id", "")
            line_amt = float(ln.get("amount", 0) or 0)
            if line_amt <= 0:
                continue
            if key_id:
                if key_id not in keys_cache:
                    keys_cache[key_id] = await db.distribution_keys.find_one(
                        {"id": key_id}, {"_id": 0}
                    )
                key = keys_cache[key_id]
                if not key or not key.get("lots"):
                    continue
                # iter90ac : exclut les lots marques excluded=True
                active_kls = [kl for kl in key["lots"] if not kl.get("excluded")]
                total_shares = sum(float(kl.get("share", 0) or 0) for kl in active_kls)
                if total_shares <= 0:
                    continue
                for kl in active_kls:
                    lot_id = kl.get("lot_id")
                    if not lot_id:
                        continue
                    share = float(kl.get("share", 0) or 0)
                    if share <= 0:
                        continue
                    portion = line_amt * (share / total_shares)
                    lot_amounts[lot_id] = lot_amounts.get(lot_id, 0.0) + portion
                    lot_shares[lot_id] = lot_shares.get(lot_id, 0.0) + share
            else:
                # Fallback : repartition aux quotites globales
                if total_quotity_global <= 0:
                    continue
                for lt in lots:
                    lot_q = float(lt.get("quotity", 0) or 0)
                    if lot_q <= 0:
                        continue
                    portion = line_amt * (lot_q / total_quotity_global)
                    lot_amounts[lt["id"]] = lot_amounts.get(lt["id"], 0.0) + portion
                    lot_shares[lt["id"]] = lot_shares.get(lt["id"], 0.0) + lot_q

        # Build distribution list (1 entry par lot avec montant > 0)
        distribution = []
        lots_by_id = {lt["id"]: lt for lt in lots}
        for lot_id, amt in lot_amounts.items():
            if amt <= 0.001:
                continue
            lot = lots_by_id.get(lot_id)
            if not lot:
                continue
            owner_id = lot.get("owner_id") or ""
            owner = owners_map.get(owner_id) if owner_id else None
            distribution.append({
                "lot_id": lot_id,
                "lot_number": lot.get("number", ""),
                "owner_id": owner_id,
                "owner_name": owner.get("name", "") if owner else "",
                "vcs_code": owner.get("vcs_code", "") if owner else "",
                "share": round(lot_shares.get(lot_id, 0.0), 4),
                "amount": round(amt, 2),
                "paid": False,
                "paid_date": "",
            })
        distribution.sort(key=lambda d: (d["owner_name"], d["lot_number"]))
        # iter90w : garantit sum(distribution.amount) == somme des lines
        target = round(sum(float(ln.get("amount", 0) or 0) for ln in lines), 2)
        _snap_distribution_to_total(distribution, target)
        return distribution, lot_amounts

    @router.post("/{call_id}/regenerate-distribution")
    async def regenerate_call_distribution(call_id: str):
        """Recalcule la distribution d'UN appel a partir de ses `lines`.

        Utile pour reparer les appels Q2/Q3/Q4 dont la distribution s'est
        retrouvee vide suite a une regeneration partielle ou un bug.

        Refuse si au moins une distribution row existe avec paid=true (protection
        historique).
        """
        call = await db.fund_calls.find_one({"id": call_id}, {"_id": 0})
        if not call:
            raise HTTPException(404, "Appel introuvable")
        existing_dist = call.get("distribution") or []
        if any(d.get("paid") for d in existing_dist):
            raise HTTPException(
                400,
                "Cet appel contient des paiements - regenerer la distribution detruirait l'historique."
            )
        new_dist, _ = await _rebuild_distribution_from_lines(call)
        if not new_dist:
            raise HTTPException(
                400,
                "Impossible de reconstruire la distribution : "
                "verifiez que les cles de repartition referencees existent et contiennent des lots."
            )
        new_total = round(sum(d["amount"] for d in new_dist), 2)
        await db.fund_calls.update_one(
            {"id": call_id},
            {"$set": {"distribution": new_dist}},
        )
        return {
            "status": "ok",
            "call_id": call_id,
            "distribution_count": len(new_dist),
            "recalculated_total": new_total,
            "stored_total": call.get("total_amount", 0),
            "delta": round(new_total - float(call.get("total_amount", 0) or 0), 2),
            "message": f"{len(new_dist)} ligne(s) de distribution regeneree(s) pour un total de {new_total:.2f} EUR.",
        }

    @router.post("/regenerate-empty-distributions")
    async def regenerate_empty_distributions(
        request: Request, copropriete_id: Optional[str] = None,
    ):
        """Scan une ACP et regenere la distribution pour TOUS les appels dont
        la distribution est vide (ou tous les montants = 0) ET dont les `lines`
        sont presentes. Les appels avec paiements sont preserves.

        Endpoint de reparation suite a une regeneration partielle.
        """
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese wall strict")
        calls = await db.fund_calls.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(10000)
        fixed = []
        skipped_paid = []
        skipped_no_lines = []
        for c in calls:
            dist = c.get("distribution") or []
            has_useful = any(float(d.get("amount", 0) or 0) > 0.001 for d in dist)
            if has_useful:
                continue  # distribution OK, on ne touche pas
            if any(d.get("paid") for d in dist):
                skipped_paid.append({"id": c["id"], "name": c.get("name", "")})
                continue
            if not (c.get("lines") or []):
                skipped_no_lines.append({"id": c["id"], "name": c.get("name", "")})
                continue
            new_dist, _ = await _rebuild_distribution_from_lines(c)
            if not new_dist:
                continue
            await db.fund_calls.update_one(
                {"id": c["id"]},
                {"$set": {"distribution": new_dist}},
            )
            fixed.append({
                "id": c["id"],
                "name": c.get("name", ""),
                "date": c.get("date", ""),
                "lines_count": len(new_dist),
                "total": round(sum(d["amount"] for d in new_dist), 2),
            })
        return {
            "status": "ok",
            "scanned": len(calls),
            "fixed_count": len(fixed),
            "fixed": fixed,
            "skipped_paid": skipped_paid,
            "skipped_no_lines": skipped_no_lines,
            "message": (
                f"{len(fixed)} appel(s) repare(s)."
                + (f" {len(skipped_paid)} ignore(s) car contient des paiements." if skipped_paid else "")
                + (f" {len(skipped_no_lines)} ignore(s) car aucune ligne budget." if skipped_no_lines else "")
            ),
        }

    @router.post("/fix-rounding-drift")
    async def fix_rounding_drift(
        request: Request, copropriete_id: Optional[str] = None,
    ):
        """iter90w : Corrige la derive d'arrondi dans les distributions existantes.

        Pour chaque appel non paye de l'ACP :
        - Recalcule sum(distribution.amount) vs total_amount
        - Si drift > 0.01, applique la methode des plus grands restes pour
          faire correspondre exactement
        - Regenere aussi les journal entries auto-generes lies (VE)

        Preserve tout appel contenant au moins un paiement (protection historique).
        """
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis")

        calls = await db.fund_calls.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(2000)

        fixed = []
        skipped_paid = []
        no_drift = []
        for c in calls:
            if any(d.get("paid") for d in (c.get("distribution") or [])):
                skipped_paid.append({"call_id": c["id"], "name": c.get("name", "")})
                continue
            dist = list(c.get("distribution") or [])
            if not dist:
                continue
            target = round(float(c.get("total_amount", 0) or 0), 2)
            current = round(sum(float(d.get("amount", 0) or 0) for d in dist), 2)
            drift_cents = int(round((target - current) * 100))
            if drift_cents == 0:
                no_drift.append(c["id"])
                continue
            _snap_distribution_to_total(dist, target)
            new_sum = round(sum(float(d.get("amount", 0) or 0) for d in dist), 2)
            await db.fund_calls.update_one(
                {"id": c["id"]}, {"$set": {"distribution": dist}}
            )
            # Regenere les journal entries auto-generees liees a cet appel
            try:
                from auto_entries import generate_sale_entry, _delete_auto_entries
                # Fetch la version fraiche + patch la distribution corrigee
                updated_call = await db.fund_calls.find_one(
                    {"id": c["id"]}, {"_id": 0}
                )
                if updated_call:
                    await generate_sale_entry(db, updated_call)
            except Exception as e:
                print(f"[fix-drift] regen JE failed for {c['id']}: {e}")
            fixed.append({
                "call_id": c["id"], "name": c.get("name", ""),
                "drift_cents_before": drift_cents,
                "old_sum": current, "new_sum": new_sum,
                "target": target,
            })
        return {
            "status": "ok",
            "scanned": len(calls),
            "fixed_count": len(fixed),
            "fixed": fixed,
            "no_drift_count": len(no_drift),
            "skipped_paid_count": len(skipped_paid),
            "skipped_paid": skipped_paid,
            "message": (
                f"{len(fixed)} appel(s) corrige(s). "
                f"{len(no_drift)} deja OK. "
                f"{len(skipped_paid)} ignore(s) car contient des paiements."
            ),
        }

    return router
