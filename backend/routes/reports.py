from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
import uuid
import io


def _apply_copro(q: dict, copropriete_id: Optional[str]) -> dict:
    """Add copropriete_id filter to a Mongo query when provided (chinese wall)."""
    if copropriete_id:
        q["copropriete_id"] = copropriete_id
    return q


def create_reports_router(db):
    router = APIRouter(prefix="/api/reports")

    # ---- GRAND LIVRE (General Ledger) ----
    @router.get("/grand-livre")
    async def grand_livre(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_from: Optional[str] = None,
        account_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
    ):
        q = _apply_copro({}, copropriete_id)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

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
    async def trial_balance(date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        q = _apply_copro({}, copropriete_id)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

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
    async def bilan(date_to: Optional[str] = None, copropriete_id: Optional[str] = None,
                    fiscal_year_id: Optional[str] = None):
        """Bilan PCMN belge structure (Actif / Passif par rubriques)."""
        # Optionally resolve fiscal year
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and not date_to:
                date_to = fy["end_date"]

        q = _apply_copro({}, copropriete_id)
        if date_to:
            q["date"] = {"$lte": date_to}

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
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
            "V_creances": [],
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
            "VI_dettes_court": [],
            "VII_regul_passif": [],
        }

        for acc, b in balances.items():
            solde = round(b["debit"] - b["credit"], 2)
            item = {"account_number": acc, "account_name": b["account_name"], "amount": abs(solde)}
            # ACTIF (solde debiteur)
            if solde > 0.01:
                if acc.startswith(("20", "21")):
                    actif_buckets["I_immo_incorporelles"].append(item)
                elif acc.startswith(("22", "23", "24", "25", "26", "27")):
                    actif_buckets["II_immo_corporelles"].append(item)
                elif acc.startswith("28"):
                    actif_buckets["III_immo_financieres"].append(item)
                elif acc.startswith("3"):
                    actif_buckets["IV_stocks"].append(item)
                elif acc.startswith("40") or acc.startswith("41") or acc.startswith("416"):
                    actif_buckets["V_creances"].append(item)
                elif acc.startswith(("50", "51", "52", "53")):
                    actif_buckets["VI_placements"].append(item)
                elif acc.startswith(("54", "55", "57", "58")):
                    actif_buckets["VII_disponibilites"].append(item)
                elif acc.startswith("49"):
                    actif_buckets["VIII_regul_actif"].append(item)
                else:
                    actif_buckets["V_creances"].append(item)  # default for class 4 debit
            # PASSIF (solde crediteur)
            elif solde < -0.01:
                if acc.startswith("10"):
                    passif_buckets["I_capital"].append(item)
                elif acc.startswith("13"):
                    passif_buckets["II_reserves"].append(item)
                elif acc.startswith("14"):
                    passif_buckets["III_resultat_reporte"].append(item)
                elif acc.startswith("15"):
                    passif_buckets["IV_subsides"].append(item)
                elif acc.startswith("17"):
                    passif_buckets["V_dettes_long"].append(item)
                elif acc.startswith(("44", "45", "46", "48")):
                    passif_buckets["VI_dettes_court"].append(item)
                elif acc.startswith("49"):
                    passif_buckets["VII_regul_passif"].append(item)
                else:
                    passif_buckets["VI_dettes_court"].append(item)

        # Compute current period result (classes 6 & 7) and inject in passif (III bis: resultat exercice)
        date_from = fy["start_date"] if fy else None
        q_res = _apply_copro({}, copropriete_id)
        if date_from or date_to:
            q_res["date"] = {}
            if date_from:
                q_res["date"]["$gte"] = date_from
            if date_to:
                q_res["date"]["$lte"] = date_to
        entries_res = await db.journal_entries.find(q_res, {"_id": 0}).to_list(100000)
        total_charges = 0.0
        total_produits = 0.0
        for entry in entries_res:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc.startswith("6"):
                    total_charges += line.get("debit", 0) - line.get("credit", 0)
                elif acc.startswith("7"):
                    total_produits += line.get("credit", 0) - line.get("debit", 0)
        result_exercise = round(total_produits - total_charges, 2)
        if abs(result_exercise) > 0.01:
            passif_buckets["III_resultat_reporte"].append({
                "account_number": "RESULTAT",
                "account_name": "Resultat de l'exercice" + (" (perte)" if result_exercise < 0 else " (benefice)"),
                "amount": abs(result_exercise),
            })

        rubr_actif = [
            ("I. Immobilisations incorporelles", "I_immo_incorporelles"),
            ("II. Immobilisations corporelles", "II_immo_corporelles"),
            ("III. Immobilisations financieres", "III_immo_financieres"),
            ("IV. Stocks", "IV_stocks"),
            ("V. Creances", "V_creances"),
            ("VI. Placements de tresorerie", "VI_placements"),
            ("VII. Valeurs disponibles", "VII_disponibilites"),
            ("VIII. Comptes de regularisation", "VIII_regul_actif"),
        ]
        rubr_passif = [
            ("I. Capital / Fonds propre", "I_capital"),
            ("II. Reserves", "II_reserves"),
            ("III. Resultat reporte", "III_resultat_reporte"),
            ("IV. Subsides en capital", "IV_subsides"),
            ("V. Dettes a plus d'un an", "V_dettes_long"),
            ("VI. Dettes a un an au plus", "VI_dettes_court"),
            ("VII. Comptes de regularisation", "VII_regul_passif"),
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
        }

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
    async def compte_resultat(date_from: Optional[str] = None, date_to: Optional[str] = None,
                              copropriete_id: Optional[str] = None,
                              fiscal_year_id: Optional[str] = None):
        """Compte de Resultats PCMN belge structure (rubriques 60-67 / 70-76)."""
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy:
                if not date_from:
                    date_from = fy["start_date"]
                if not date_to:
                    date_to = fy["end_date"]

        q = _apply_copro({}, copropriete_id)
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        entries = await db.journal_entries.find(q, {"_id": 0}).to_list(100000)
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
    async def decompte_annuel(fiscal_year_id: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy:
                date_from = fy["start_date"]
                date_to = fy["end_date"]

        # Owners are global, but lots/invoices are ACP-scoped (chinese wall)
        lots_q = _apply_copro({}, copropriete_id)
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(1000)
        owner_ids_in_acp = list({l.get("owner_id") for l in lots if l.get("owner_id")})
        owners = await db.owners.find({"id": {"$in": owner_ids_in_acp}}, {"_id": 0}).sort("name", 1).to_list(1000) if owner_ids_in_acp else []

        inv_q = _apply_copro({"date": {"$gte": date_from or "2000-01-01", "$lte": date_to or "2099-12-31"}}, copropriete_id)
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)

        total_quotity = sum(l.get("quotity", 0) for l in lots)

        decomptes = []
        for owner in owners:
            owner_lots = [l for l in lots if l.get("owner_id") == owner["id"]]
            owner_quotity = sum(l.get("quotity", 0) for l in owner_lots)
            share = owner_quotity / total_quotity if total_quotity > 0 else 0

            charges = []
            total_owner_charges = 0
            for inv in invoices:
                dist_lines = inv.get("distribution_lines", [])
                for dl in dist_lines:
                    if dl.get("lot_id") in [l["id"] for l in owner_lots]:
                        charges.append({
                            "date": inv["date"],
                            "description": f"{inv.get('supplier', '')} - {inv.get('description', '')}",
                            "invoice_number": inv.get("number", ""),
                            "amount": dl.get("amount", 0),
                        })
                        total_owner_charges += dl.get("amount", 0)

                if not dist_lines and share > 0:
                    owner_amount = round(inv.get("total_amount", 0) * share, 2)
                    charges.append({
                        "date": inv["date"],
                        "description": f"{inv.get('supplier', '')} - {inv.get('description', '')}",
                        "invoice_number": inv.get("number", ""),
                        "amount": owner_amount,
                    })
                    total_owner_charges += owner_amount

            decomptes.append({
                "owner_id": owner["id"],
                "owner_name": owner["name"],
                "vcs_code": owner.get("vcs_code", ""),
                "lots": [{"number": l["number"], "quotity": l.get("quotity", 0)} for l in owner_lots],
                "share_pct": round(share * 100, 2),
                "charges": charges,
                "total_charges": round(total_owner_charges, 2),
            })

        return {"decomptes": decomptes, "period": {"from": date_from, "to": date_to}}

    # ---- PDF DECOMPTE ----
    @router.get("/decompte/pdf/{owner_id}")
    async def decompte_pdf(owner_id: str, fiscal_year_id: Optional[str] = None,
                           date_from: Optional[str] = None, date_to: Optional[str] = None,
                           copropriete_id: Optional[str] = None):
        from pdf_decompte import build_decompte_pdf

        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")

        # Resolve fiscal year (or build a virtual one from date_from/date_to)
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if not fy:
            # Build virtual fy from explicit dates or default current year
            today = datetime.now(timezone.utc)
            fy = {
                "name": f"Exercice {today.year}",
                "start_date": date_from or f"{today.year}-01-01",
                "end_date": date_to or f"{today.year}-12-31",
            }
            if copropriete_id:
                # Try to find a real fiscal year covering this period
                real_fy = await db.fiscal_years.find_one(
                    {"copropriete_id": copropriete_id,
                     "start_date": {"$lte": fy["end_date"]},
                     "end_date": {"$gte": fy["start_date"]}},
                    {"_id": 0}
                )
                if real_fy:
                    fy = real_fy

        # Resolve ACP (required)
        copro_id_use = copropriete_id or fy.get("copropriete_id", "")
        if not copro_id_use:
            # Pick first ACP where owner has lots
            sample_lot = await db.lots.find_one(
                {"$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
                {"_id": 0}
            )
            if sample_lot:
                copro_id_use = sample_lot.get("copropriete_id", "")
        if not copro_id_use:
            raise HTTPException(400, "Aucune copropriete identifiee pour ce proprietaire")
        copro = await db.coproprietes.find_one({"id": copro_id_use}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Owner's lots in this ACP
        owner_lots = await db.lots.find(
            {"copropriete_id": copro_id_use,
             "$or": [{"owner_id": owner_id}, {"owner_ids": owner_id}]},
            {"_id": 0}
        ).to_list(100)
        all_lots = await db.lots.find({"copropriete_id": copro_id_use}, {"_id": 0}).to_list(1000)

        # Invoices in period
        invoices = await db.invoices.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Distribution keys
        distribution_keys = await db.distribution_keys.find(
            {"copropriete_id": copro_id_use}, {"_id": 0}
        ).to_list(1000)

        # Fund calls in period
        fund_calls = await db.fund_calls.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
            {"_id": 0}
        ).sort("date", 1).to_list(10000)

        # Payments (bank transactions matched to this owner OR communication = VCS)
        owner_vcs_digits = owner.get("vcs_digits", "")
        all_txns = await db.bank_transactions.find(
            {"copropriete_id": copro_id_use,
             "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]}},
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

        pdf_bytes = build_decompte_pdf(
            owner=owner, copropriete=copro, fiscal_year=fy,
            owner_lots=owner_lots, all_lots=all_lots,
            invoices=invoices, distribution_keys=distribution_keys,
            fund_calls=fund_calls, payments=payments,
            expense_accounts_map=nature_map,
        )

        filename = f"decompte_{owner['name'].replace(' ', '_')}_{fy.get('name','').replace(' ', '_')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- BALANCE DE TIERS PROPRIETAIRES ----
    @router.get("/balance-tiers/owners")
    async def balance_tiers_owners(copropriete_id: Optional[str] = None):
        """Balance de tiers proprietaires, scopee par ACP (chinese wall).
        Debiteur = le proprietaire doit payer a la copropriete.
        Crediteur = la copropriete doit rembourser le proprietaire."""
        # Only owners who have lots in this ACP
        lots_q = _apply_copro({}, copropriete_id)
        lots = await db.lots.find(lots_q, {"_id": 0}).to_list(10000)
        owner_ids = list({l.get("owner_id") for l in lots if l.get("owner_id")})
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).sort("name", 1).to_list(1000) if owner_ids else []

        fc_q = _apply_copro({}, copropriete_id)
        fund_calls = await db.fund_calls.find(fc_q, {"_id": 0}).to_list(10000)

        txn_q = _apply_copro({}, copropriete_id)
        all_bank_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(100000)

        # Build VCS lookup map (owners are global, but we only consider those in this ACP)
        vcs_to_owner = {}
        for owner in owners:
            if owner.get("vcs_digits"):
                vcs_to_owner[owner["vcs_digits"]] = owner["id"]
            if owner.get("vcs_code"):
                vcs_to_owner[owner["vcs_code"]] = owner["id"]

        result = []
        for owner in owners:
            oid = owner["id"]
            total_called = 0
            calls_detail = []
            for fc in fund_calls:
                for d in fc.get("distribution", []):
                    if d.get("owner_id") == oid:
                        total_called += d.get("amount", 0)
                        calls_detail.append({"date": fc["date"], "description": fc["name"], "amount": d["amount"], "type": "appel"})

            total_paid = 0
            payments_detail = []
            for txn in all_bank_txns:
                resolved_owner = None
                if txn.get("matched") and txn.get("match_type") == "owner_payment" and txn.get("matched_to") == oid:
                    resolved_owner = oid
                elif not txn.get("matched"):
                    comm = txn.get("communication", "")
                    if comm:
                        clean = comm.replace("+", "").replace("/", "").replace(" ", "")
                        if vcs_to_owner.get(clean) == oid or vcs_to_owner.get(comm) == oid:
                            resolved_owner = oid

                if resolved_owner == oid:
                    total_paid += abs(txn.get("amount", 0))
                    payments_detail.append({"date": txn["date"], "description": txn.get("counterparty_name", "") or txn.get("communication", ""), "amount": abs(txn["amount"]), "type": "paiement"})

            balance = round(total_called - total_paid, 2)
            movements = sorted(calls_detail + payments_detail, key=lambda x: x["date"])
            tier_acc = (owner.get("tier_accounts") or {}).get(copropriete_id or "", {}) or {}

            result.append({
                "owner_id": oid,
                "owner_name": owner["name"],
                "vcs_code": owner.get("vcs_code", ""),
                "account_provisions": tier_acc.get("provisions", ""),
                "account_reserve": tier_acc.get("reserve", ""),
                "total_called": round(total_called, 2),
                "total_paid": round(total_paid, 2),
                "balance": balance,
                "status": "debiteur" if balance > 0.01 else ("crediteur" if balance < -0.01 else "solde"),
                "movements": movements,
            })

        total_debiteurs = round(sum(r["balance"] for r in result if r["balance"] > 0), 2)
        total_crediteurs = round(sum(abs(r["balance"]) for r in result if r["balance"] < 0), 2)
        return {"owners": result, "total_debiteurs": total_debiteurs, "total_crediteurs": total_crediteurs}

    @router.get("/balance-tiers/owners/{owner_id}")
    async def situation_compte_owner(owner_id: str, copropriete_id: Optional[str] = None):
        """Situation de compte d'un proprietaire scopee par ACP."""
        owner = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Proprietaire non trouve")

        movements = []

        # Fund calls scoped
        fc_q = _apply_copro({}, copropriete_id)
        fund_calls = await db.fund_calls.find(fc_q, {"_id": 0}).to_list(10000)
        for fc in fund_calls:
            for d in fc.get("distribution", []):
                if d.get("owner_id") == owner_id:
                    movements.append({"date": fc["date"], "description": f"Appel: {fc['name']}", "debit": d["amount"], "credit": 0, "type": "appel", "reference": fc.get("id", "")})

        # Invoice distributions scoped
        inv_q = _apply_copro({}, copropriete_id)
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)
        lot_q = _apply_copro({"owner_id": owner_id}, copropriete_id)
        owner_lots = await db.lots.find(lot_q, {"_id": 0}).to_list(100)
        lot_ids = [l["id"] for l in owner_lots]
        for inv in invoices:
            for dl in inv.get("distribution_lines", []):
                if dl.get("lot_id") in lot_ids:
                    movements.append({"date": inv["date"], "description": f"Charge: {inv.get('supplier', '')} - {inv.get('description', '')}", "debit": dl["amount"], "credit": 0, "type": "charge", "reference": inv.get("number", "")})

        # Bank transactions scoped
        txn_q = _apply_copro({}, copropriete_id)
        all_bank_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(100000)
        for txn in all_bank_txns:
            is_owner_payment = False
            if txn.get("matched") and txn.get("match_type") == "owner_payment" and txn.get("matched_to") == owner_id:
                is_owner_payment = True
            elif not txn.get("matched"):
                comm = txn.get("communication", "")
                if comm and owner.get("vcs_digits"):
                    clean = comm.replace("+", "").replace("/", "").replace(" ", "")
                    if clean == owner.get("vcs_digits") or comm == owner.get("vcs_code"):
                        is_owner_payment = True
            if is_owner_payment:
                movements.append({"date": txn["date"], "description": f"Paiement recu: {txn.get('communication', '') or txn.get('counterparty_name', '')}", "debit": 0, "credit": abs(txn["amount"]), "type": "paiement", "reference": txn.get("id", "")})

        movements.sort(key=lambda x: x["date"])
        running = 0
        for m in movements:
            running += m["debit"] - m["credit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)

        return {
            "owner": owner,
            "movements": movements,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": round(total_debit - total_credit, 2),
            "status": "debiteur" if total_debit > total_credit + 0.01 else ("crediteur" if total_credit > total_debit + 0.01 else "solde"),
        }

    # ---- BALANCE DE TIERS FOURNISSEURS ----
    @router.get("/balance-tiers/suppliers")
    async def balance_tiers_suppliers(copropriete_id: Optional[str] = None):
        """Balance de tiers fournisseurs scopee par ACP (les factures et paiements sont scopes).
        Affiche aussi les fournisseurs orphelins (nom present sur des factures mais pas de fiche fournisseur)."""
        inv_q = _apply_copro({}, copropriete_id)
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)

        # Group invoices by case-insensitive supplier name
        from collections import defaultdict
        inv_by_supplier = defaultdict(list)  # lower_name -> [invoice]
        canonical_name = {}  # lower_name -> first observed display name
        for inv in invoices:
            raw = (inv.get("supplier", "") or "").strip()
            if not raw:
                continue
            key = raw.lower()
            inv_by_supplier[key].append(inv)
            if key not in canonical_name:
                canonical_name[key] = raw

        # Fetch supplier docs by case-insensitive regex on each name
        suppliers_map = {}  # lower_name -> supplier doc
        if canonical_name:
            import re
            names_pattern = "|".join(re.escape(n) for n in canonical_name.values())
            if names_pattern:
                cursor = db.suppliers.find(
                    {"name": {"$regex": f"^({names_pattern})$", "$options": "i"}},
                    {"_id": 0}
                )
                async for s in cursor:
                    suppliers_map[s["name"].lower()] = s

        txn_q = _apply_copro({"matched": True, "match_type": "invoice"}, copropriete_id)
        bank_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(10000)
        inv_id_map = {inv["id"]: inv for inv in invoices}

        result = []
        for key, invs in inv_by_supplier.items():
            display_name = canonical_name[key]
            supplier = suppliers_map.get(key)
            total_invoiced = sum(inv.get("total_amount", 0) for inv in invs)
            total_paid = 0.0
            inv_ids = {inv["id"] for inv in invs}
            for txn in bank_txns:
                matched_inv = inv_id_map.get(txn.get("matched_to", ""))
                if matched_inv and matched_inv["id"] in inv_ids:
                    total_paid += abs(txn.get("amount", 0))
            balance = round(total_invoiced - total_paid, 2)
            tier_acc = ""
            if supplier and copropriete_id:
                tier_acc = ((supplier.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
            result.append({
                "supplier_id": supplier["id"] if supplier else "",
                "supplier_name": supplier["name"] if supplier else display_name,
                "vat_number": supplier.get("vat_number", "") if supplier else "",
                "tier_account": tier_acc,
                "orphan": supplier is None,
                "invoice_count": len(invs),
                "total_invoiced": round(total_invoiced, 2),
                "total_paid": round(total_paid, 2),
                "balance": balance,
                "status": "crediteur" if balance > 0.01 else ("debiteur" if balance < -0.01 else "solde"),
            })
        result.sort(key=lambda r: r["supplier_name"].lower())
        total_a_payer = round(sum(r["balance"] for r in result if r["balance"] > 0), 2)
        return {"suppliers": result, "total_a_payer": total_a_payer}

    @router.get("/balance-tiers/suppliers/{supplier_id}")
    async def situation_compte_supplier(supplier_id: str, copropriete_id: Optional[str] = None):
        """Situation de compte fournisseur scopee par ACP."""
        supplier = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not supplier:
            raise HTTPException(404, "Fournisseur non trouve")

        sname = supplier["name"]
        movements = []

        inv_q = _apply_copro({"supplier": sname}, copropriete_id)
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)
        for inv in invoices:
            movements.append({"date": inv["date"], "description": f"Facture {inv.get('number', '')}: {inv.get('description', '')}", "debit": 0, "credit": inv.get("total_amount", 0), "type": "facture", "reference": inv.get("number", "")})

        txn_q = _apply_copro({"matched": True, "match_type": "invoice"}, copropriete_id)
        bank_txns = await db.bank_transactions.find(txn_q, {"_id": 0}).to_list(10000)
        inv_map = {inv["id"]: inv for inv in invoices}
        for txn in bank_txns:
            matched_inv = inv_map.get(txn.get("matched_to", ""))
            if matched_inv:
                movements.append({"date": txn["date"], "description": f"Paiement: {txn.get('communication', '') or txn.get('counterparty_name', '')}", "debit": abs(txn["amount"]), "credit": 0, "type": "paiement", "reference": txn.get("id", "")})

        movements.sort(key=lambda x: x["date"])
        running = 0
        for m in movements:
            running += m["credit"] - m["debit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)

        return {
            "supplier": supplier,
            "movements": movements,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": round(total_credit - total_debit, 2),
            "status": "crediteur" if total_credit > total_debit + 0.01 else ("debiteur" if total_debit > total_credit + 0.01 else "solde"),
        }

    return router
