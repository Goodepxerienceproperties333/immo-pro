"""Module audit comptable - sante comptable de la copropriete.

Detection des anomalies courantes :
- Factures > X jours sans paiement
- Doublons potentiels (meme fournisseur + meme montant + dates proches)
- Comptes orphelins (soldes sur comptes non rattaches a un owner/supplier)
- Ecritures non equilibrees
- Lettrages manquants

Calcule aussi un score de "Sante comptable" sur 100.
"""
from datetime import datetime, timezone
from typing import Optional


async def compute_health_audit(db, copropriete_id: str, days_threshold: int = 60) -> dict:
    """Retourne le rapport d'audit de sante comptable d'une ACP."""
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()

    anomalies = []
    score = 100
    stats = {
        "invoices_unpaid": 0,
        "invoices_overdue": 0,
        "duplicates": 0,
        "orphans": 0,
        "unbalanced": 0,
        "owners_late": 0,
    }

    # P0 fix (iter90fo) : UNE seule requete invoices (au lieu de 2) reutilisee
    # pour le calcul des impayees ET des doublons. Reduit la charge DB et le
    # temps de reponse, cause probable des timeouts Cloudflare 502 constates
    # en production sur les ACP avec beaucoup de factures.
    inv_all = await db.invoices.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "id": 1, "supplier_id": 1, "supplier": 1, "number": 1,
         "date": 1, "due_date": 1, "total_amount": 1, "status": 1},
    ).to_list(20000)

    # 1) FACTURES IMPAYEES > X jours
    invoices = [inv for inv in inv_all if inv.get("status") == "unpaid"]
    stats["invoices_unpaid"] = len(invoices)
    overdue_list = []
    for inv in invoices:
        ref_date = inv.get("due_date") or inv.get("date")
        if not ref_date:
            continue
        try:
            ref = datetime.strptime(ref_date, "%Y-%m-%d").date()
            age = (today - ref).days
            if age > days_threshold:
                overdue_list.append({
                    "id": inv["id"],
                    "supplier": inv.get("supplier", ""),
                    "number": inv.get("number", ""),
                    "amount": inv.get("total_amount", 0),
                    "age_days": age,
                    "due_date": ref_date,
                })
        except Exception:
            continue
    stats["invoices_overdue"] = len(overdue_list)
    if overdue_list:
        score -= min(25, len(overdue_list) * 5)
        anomalies.append({
            "severity": "high",
            "category": "invoices_overdue",
            "title": f"{len(overdue_list)} facture(s) impayee(s) > {days_threshold}j",
            "items": overdue_list[:10],
            "count": len(overdue_list),
        })

    # 2) DOUBLONS POTENTIELS : meme fournisseur + meme montant + dates proches (<= 7j)
    # iter90ct : Ne pas flagger comme doublon si les DEUX numeros de facture
    # sont non-vides ET differents. Regle metier : chaque facture a un numero
    # unique chez le fournisseur, donc numeros differents = factures reelles
    # distinctes (jamais des doublons). On garde le flag uniquement si les
    # numeros sont identiques OU si au moins un est vide (import legacy).
    by_sig = {}
    for inv in inv_all:
        amt = round(float(inv.get("total_amount", 0) or 0), 2)
        if amt == 0:
            continue
        sid = inv.get("supplier_id") or inv.get("supplier", "")
        key = (sid, amt)
        by_sig.setdefault(key, []).append(inv)
    dups = []
    for key, items in by_sig.items():
        if len(items) < 2:
            continue
        # Check date proximity (sliding window 7 jours)
        items_sorted = sorted(items, key=lambda x: x.get("date", ""))
        for i in range(1, len(items_sorted)):
            try:
                d1 = datetime.strptime(items_sorted[i - 1]["date"], "%Y-%m-%d").date()
                d2 = datetime.strptime(items_sorted[i]["date"], "%Y-%m-%d").date()
                if abs((d2 - d1).days) <= 7:
                    # iter90ct : filtre supplementaire sur les numeros de facture
                    n1 = (items_sorted[i - 1].get("number") or "").strip()
                    n2 = (items_sorted[i].get("number") or "").strip()
                    if n1 and n2 and n1 != n2:
                        # Deux numeros non-vides et differents -> pas un doublon
                        continue
                    dups.append({
                        "supplier": items_sorted[i].get("supplier", ""),
                        "amount": items_sorted[i].get("total_amount", 0),
                        "invoices": [
                            {"id": items_sorted[i - 1]["id"], "number": items_sorted[i - 1].get("number", ""), "date": items_sorted[i - 1].get("date", "")},
                            {"id": items_sorted[i]["id"], "number": items_sorted[i].get("number", ""), "date": items_sorted[i].get("date", "")},
                        ],
                    })
            except Exception:
                continue
    stats["duplicates"] = len(dups)
    if dups:
        score -= min(15, len(dups) * 5)
        anomalies.append({
            "severity": "medium",
            "category": "duplicates",
            "title": f"{len(dups)} doublon(s) potentiel(s) detecte(s)",
            "items": dups[:10],
            "count": len(dups),
        })

    # 3) COMPTES TIER ORPHELINS (soldes 400/440 sans owner/supplier link)
    # P0 fix (iter90fo) : UNE seule requete journal_entries (au lieu de 2)
    # reutilisee pour orphelins + ecritures non equilibrees + solde owners.
    # Avant : 2 fetchs de la collection ENTIERE (jusqu'a 100k docs chacun)
    # + une boucle O(lignes * nb_owners) pour calculer le solde de chaque
    # proprietaire -> avec des annees d'historique et des centaines de lots,
    # cette boucle imbriquee pouvait prendre plusieurs MINUTES et, en mode
    # single-worker, geler TOUTE l'application (tous les clients) pendant ce
    # temps -> timeout Cloudflare 502. Remplace par une map de lookup O(1).
    entries = await db.journal_entries.find(
        {"copropriete_id": copropriete_id, "journal_type": {"$ne": "AN"}},
        {"_id": 0, "id": 1, "reference": 1, "date": 1, "total_debit": 1,
         "total_credit": 1, "is_reversal": 1, "reversed": 1, "lines": 1},
    ).to_list(50000)

    # Comptes attendus pour les owners (filtres sur CETTE copropriete uniquement,
    # pas toute la base multi-tenant comme avant)
    owners = await db.owners.find(
        {f"tier_accounts.{copropriete_id}": {"$exists": True}},
        {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1},
    ).to_list(5000)
    valid_owner_accs = set()
    owner_acc_to_id = {}  # account_number -> owner_id (lookup O(1) au lieu de boucle)
    owner_tier_accs = {}  # owner_id -> set des comptes tier
    owner_balance = {}  # owner_id -> solde net du compte tier (positif = debiteur)
    for o in owners:
        accs = ((o.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
        all_accs = set()
        for k in ("provisions", "reserve"):
            if accs.get(k):
                valid_owner_accs.add(accs[k])
        for k in ("provisions", "reserve", "main"):
            if accs.get(k):
                all_accs.add(accs[k])
                owner_acc_to_id[accs[k]] = o["id"]
        if all_accs:
            owner_tier_accs[o["id"]] = all_accs
            owner_balance[o["id"]] = 0.0

    # iter90is (Chinese Wall strict) : lookup UNIQUEMENT par copropriete_id
    # direct. Chaque fiche fournisseur est locale a UNE ACP -- le partage
    # cross-ACP via `tier_accounts` est definitivement supprime.
    suppliers = await db.suppliers.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "id": 1, "name": 1, "copropriete_id": 1,
         "tier_account_number": 1, "tier_accounts": 1},  # tier_accounts pour legacy read
    ).to_list(5000)
    valid_sup_accs = set()
    for s in suppliers:
        # iter90is : le compte tier est desormais un champ simple
        # `tier_account_number` (chinese wall). Fallback lecture `tier_accounts`
        # pour eventuels docs legacy non migres.
        main_acc = (s.get("tier_account_number") or "").strip()
        if not main_acc:
            main_acc = ((s.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
        if main_acc:
            valid_sup_accs.add(main_acc)

    accs_used = {}
    unbalanced = []
    for e in entries:
        td = float(e.get("total_debit", 0) or 0)
        tc = float(e.get("total_credit", 0) or 0)
        if abs(td - tc) > 0.01:
            unbalanced.append({
                "id": e["id"],
                "reference": e.get("reference", ""),
                "date": e.get("date", ""),
                "debit": td,
                "credit": tc,
                "ecart": round(td - tc, 2),
            })
        # Solde owner : exclut extournes/contre-passations (sous-ensemble
        # des criteres deja appliques par le filtre journal_type != AN)
        skip_balance = bool(e.get("is_reversal")) or bool(e.get("reversed"))
        for ln in e.get("lines", []) or []:
            acc = ln.get("account_number", "")
            if acc.startswith("400") or acc.startswith("440"):
                accs_used.setdefault(acc, {"debit": 0.0, "credit": 0.0, "name": ln.get("account_name", "")})
                accs_used[acc]["debit"] += float(ln.get("debit", 0) or 0)
                accs_used[acc]["credit"] += float(ln.get("credit", 0) or 0)
            if skip_balance:
                continue
            tpid = ln.get("third_party_id")
            oid = owner_acc_to_id.get(acc) or (tpid if tpid in owner_balance else None)
            if oid:
                d = float(ln.get("debit", 0) or 0)
                c = float(ln.get("credit", 0) or 0)
                owner_balance[oid] = owner_balance.get(oid, 0.0) + (d - c)

    orphan_list = []
    for acc, b in accs_used.items():
        solde = abs(b["debit"] - b["credit"])
        if solde < 0.01:
            continue
        if acc.startswith("400") and acc not in valid_owner_accs:
            orphan_list.append({"account": acc, "name": b["name"], "balance": round(solde, 2),
                               "type": "owner"})
        elif acc.startswith("440") and acc not in valid_sup_accs:
            orphan_list.append({"account": acc, "name": b["name"], "balance": round(solde, 2),
                               "type": "supplier"})
    stats["orphans"] = len(orphan_list)
    if orphan_list:
        score -= min(15, len(orphan_list) * 5)
        anomalies.append({
            "severity": "medium",
            "category": "orphans",
            "title": f"{len(orphan_list)} compte(s) tier orphelin(s)",
            "items": orphan_list[:10],
            "count": len(orphan_list),
        })

    # 4) ECRITURES NON EQUILIBREES
    stats["unbalanced"] = len(unbalanced)
    if unbalanced:
        score -= min(20, len(unbalanced) * 10)
        anomalies.append({
            "severity": "high",
            "category": "unbalanced",
            "title": f"{len(unbalanced)} ecriture(s) non equilibree(s)",
            "items": unbalanced[:10],
            "count": len(unbalanced),
        })

    # 5) COPROPRIETAIRES EN RETARD > X jours
    # Critere : appel non marque "paid" ET dont la date d'echeance depasse X jours
    # ET dont le solde COMPTABLE du compte tier est DEBITEUR (l'owner doit reellement
    # de l'argent). Si le copropriétaire a soldé via virement bancaire (FI), son solde
    # est crediteur et il ne doit PAS apparaitre en retard, meme si le flag `paid`
    # du fund_call n'a pas ete coche.
    fund_calls = await db.fund_calls.find(
        {"copropriete_id": copropriete_id}, {"_id": 0},
    ).to_list(10000)

    late_owners = {}
    # iter90ar : deuxieme piste - proprietaires avec appels non payes mais
    # sans solde tier debiteur (VE non generee OU extraits bancaires non
    # comptabilises). Le tableau de bord doit refleter la situation
    # comptable reelle : ces proprietaires sont "en attente de traitement".
    pending_owners = {}
    for fc in fund_calls:
        for ds in (fc.get("distribution") or []):
            if ds.get("paid"):
                continue
            due = fc.get("due_date") or fc.get("date")
            if not due:
                continue
            try:
                age = (today - datetime.strptime(due, "%Y-%m-%d").date()).days
                if age > days_threshold:
                    oid = ds.get("owner_id")
                    if not oid:
                        continue
                    balance = owner_balance.get(oid, 0.0)
                    # iter90fu : ne JAMAIS flagger un proprietaire CREDITEUR
                    # (solde tier < -0.01). Le proprietaire a paye PLUS que
                    # ce qui lui a ete appele - il ne peut pas etre "en
                    # retard" ni "en attente" quel que soit le flag `paid`
                    # des fund_calls (souvent legacy/non mis a jour apres
                    # lettrage bancaire). Bug remonte par l'utilisateur
                    # sur ACP Acacia TER PROD : Matexi affiche Solde -1755
                    # EUR (creditrice) mais apparaissait en "attente de
                    # traitement" avec 20950 EUR de dettes fantomes.
                    if balance < -0.01:
                        continue
                    # Rule : solde tier debiteur > 0.01 -> retard confirme
                    # Sinon (=0 environ) -> retard en attente de comptabilisation
                    target = late_owners if balance > 0.01 else pending_owners
                    if oid not in target:
                        target[oid] = {
                            "owner_id": oid,
                            "owner_name": ds.get("owner_name", ""),
                            "total_due": 0.0, "calls": [], "max_age": 0,
                            "tier_balance": round(balance, 2),
                        }
                    target[oid]["total_due"] += float(ds.get("amount", 0) or 0)
                    target[oid]["max_age"] = max(target[oid]["max_age"], age)
                    target[oid]["calls"].append({
                        "name": fc.get("name", ""),
                        "amount": ds.get("amount", 0),
                        "due_date": due,
                        "age": age,
                    })
            except Exception:
                continue
    stats["owners_late"] = len(late_owners)
    stats["owners_pending"] = len(pending_owners)
    if late_owners:
        score -= min(20, len(late_owners) * 4)
        anomalies.append({
            "severity": "high",
            "category": "owners_late",
            "title": f"{len(late_owners)} proprietaire(s) en retard > {days_threshold}j",
            "items": [
                {**v, "total_due": round(v["total_due"], 2)}
                for v in sorted(late_owners.values(), key=lambda x: -x["max_age"])
            ][:10],
            "count": len(late_owners),
        })

    # iter90ar : anomalie "medium" pour les pending (VE non generee ou extraits
    # non lettres). Aide le syndic a comprendre pourquoi certains proprietaires
    # n'apparaissent pas dans "owners_late" alors que le budget est cense les
    # avoir facturees.
    if pending_owners:
        score -= min(10, len(pending_owners) * 1)
        anomalies.append({
            "severity": "medium",
            "category": "owners_pending",
            "title": f"{len(pending_owners)} proprietaire(s) avec appel non paye et solde tier non debiteur",
            "items": [
                {**v, "total_due": round(v["total_due"], 2)}
                for v in sorted(pending_owners.values(), key=lambda x: -x["max_age"])
            ][:10],
            "count": len(pending_owners),
        })

    score = max(0, min(100, score))
    health_label = (
        "Excellent" if score >= 90 else
        "Bon" if score >= 75 else
        "Moyen" if score >= 50 else
        "Critique"
    )

    return {
        "copropriete_id": copropriete_id,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "days_threshold": days_threshold,
        "score": score,
        "health_label": health_label,
        "stats": stats,
        "anomalies": anomalies,
    }
