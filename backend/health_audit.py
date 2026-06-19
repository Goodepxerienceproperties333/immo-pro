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

    # 1) FACTURES IMPAYEES > X jours
    invoices = await db.invoices.find(
        {"copropriete_id": copropriete_id, "status": "unpaid"},
        {"_id": 0, "id": 1, "supplier": 1, "number": 1, "date": 1,
         "due_date": 1, "total_amount": 1},
    ).to_list(10000)
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
    inv_all = await db.invoices.find(
        {"copropriete_id": copropriete_id},
        {"_id": 0, "id": 1, "supplier_id": 1, "supplier": 1, "number": 1,
         "date": 1, "total_amount": 1},
    ).to_list(10000)
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
    je_q = {"copropriete_id": copropriete_id, "journal_type": {"$ne": "AN"}}
    entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)
    accs_used = {}
    for e in entries:
        for ln in e.get("lines", []) or []:
            acc = ln.get("account_number", "")
            if acc.startswith("400") or acc.startswith("440"):
                accs_used.setdefault(acc, {"debit": 0.0, "credit": 0.0, "name": ln.get("account_name", "")})
                accs_used[acc]["debit"] += float(ln.get("debit", 0) or 0)
                accs_used[acc]["credit"] += float(ln.get("credit", 0) or 0)
    # Comptes attendus pour les owners
    owners = await db.owners.find({}, {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1}).to_list(10000)
    valid_owner_accs = set()
    for o in owners:
        accs = ((o.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
        for k in ("provisions", "reserve"):
            if accs.get(k):
                valid_owner_accs.add(accs[k])
    # Comptes attendus pour les suppliers
    suppliers = await db.suppliers.find({}, {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1}).to_list(10000)
    valid_sup_accs = set()
    for s in suppliers:
        accs = ((s.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
        if accs.get("main"):
            valid_sup_accs.add(accs["main"])

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

    # Pre-calcul du solde COMPTABLE de chaque proprietaire (somme par tier_account)
    # Exclusion stricte : contre-passations + ecritures extournees + AN futurs
    owners_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
    owner_balance = {}  # owner_id -> solde net du compte tier (positif = debiteur)
    owner_tier_accs = {}  # owner_id -> set des comptes tier
    for o in owners_acp:
        accs = (o.get("tier_accounts") or {}).get(copropriete_id, {}) or {}
        all_accs = set()
        for k in ("provisions", "reserve", "main"):
            if accs.get(k):
                all_accs.add(accs[k])
        if all_accs:
            owner_tier_accs[o["id"]] = all_accs
            owner_balance[o["id"]] = 0.0

    # Charge les ecritures hors AN et hors reversals
    je_balance = await db.journal_entries.find({
        "copropriete_id": copropriete_id,
        "journal_type": {"$ne": "AN"},
        "is_reversal": {"$ne": True},
        "reversed": {"$ne": True},
    }, {"_id": 0, "lines": 1}).to_list(100000)
    for e in je_balance:
        for ln in e.get("lines", []) or []:
            acc = ln.get("account_number", "")
            tpid = ln.get("third_party_id")
            d = float(ln.get("debit", 0) or 0)
            c = float(ln.get("credit", 0) or 0)
            for oid, accs in owner_tier_accs.items():
                if acc in accs or tpid == oid:
                    owner_balance[oid] = owner_balance.get(oid, 0.0) + (d - c)
                    break

    late_owners = {}
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
                    # Exclusion : si le solde comptable n'est PAS debiteur, c'est paye
                    # (ou en avance). Tolerance 1 centime pour les arrondis.
                    if owner_balance.get(oid, 0.0) <= 0.01:
                        continue
                    if oid not in late_owners:
                        late_owners[oid] = {
                            "owner_id": oid,
                            "owner_name": ds.get("owner_name", ""),
                            "total_due": 0.0, "calls": [], "max_age": 0,
                            "tier_balance": round(owner_balance.get(oid, 0.0), 2),
                        }
                    late_owners[oid]["total_due"] += float(ds.get("amount", 0) or 0)
                    late_owners[oid]["max_age"] = max(late_owners[oid]["max_age"], age)
                    late_owners[oid]["calls"].append({
                        "name": fc.get("name", ""),
                        "amount": ds.get("amount", 0),
                        "due_date": due,
                        "age": age,
                    })
            except Exception:
                continue
    stats["owners_late"] = len(late_owners)
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
