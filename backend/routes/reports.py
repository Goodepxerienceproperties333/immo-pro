from fastapi import APIRouter, HTTPException, Request
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


def create_reports_router(db):
    router = APIRouter(prefix="/api/reports")

    # ---- GRAND LIVRE (General Ledger) ----
    @router.get("/grand-livre")
    async def grand_livre(
        request: Request,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_from: Optional[str] = None,
        account_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
    ):
        copropriete_id = _require_copro(copropriete_id, request)
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
    async def trial_balance(request: Request, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        copropriete_id = _require_copro(copropriete_id, request)
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
    async def bilan(request: Request, date_to: Optional[str] = None, copropriete_id: Optional[str] = None,
                    fiscal_year_id: Optional[str] = None,
                    view_mode: Optional[str] = "before_distribution"):
        """Bilan PCMN belge structure (Actif / Passif par rubriques). Chinese walls strict.
        view_mode :
          - 'before_distribution' (defaut) : le boni/mali apparait sur le compte 499.
          - 'after_distribution' : le 499 est reparti sur les comptes 4000XX des proprietaires
            (par quotites globales + cles speciales eventuelles par compte).
        """
        copropriete_id = _require_copro(copropriete_id, request)
        # Optionally resolve fiscal year
        fy = None
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
                raise HTTPException(400, "Cet exercice appartient a une autre ACP.")
            if fy and not date_to:
                date_to = fy["end_date"]

        q = _apply_copro({}, copropriete_id)
        if date_to:
            q["date"] = {"$lte": date_to}
        q["journal_type"] = {"$ne": "AN"}
        if view_mode != "after_distribution":
            # Exclure les ecritures de regularisation/cloture :
            # - flag is_regularization=True (OD nouvelles)
            # - prefixes references : OD-REG-, EXT- (extourne provisions cloture)
            q["$and"] = [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ]

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

        # ---- FUSION comptes 400/401 du meme proprietaire ----
        # Le syndic veut voir UNE seule ligne par proprietaire (nom + solde total)
        # au lieu de "Prov. charges - Dubois" + "Fonds reserve - Dubois" separes.
        owners_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
        owner_acc_map = {}  # acc_number -> {owner_id, owner_name}
        for o in owners_acp:
            tier_accs = ((o.get("tier_accounts") or {}).get(copropriete_id, {}) or {})
            for key in ("provisions", "reserve"):
                acc_n = tier_accs.get(key)
                if acc_n:
                    owner_acc_map[acc_n] = {
                        "owner_id": o["id"], "owner_name": o.get("name", "")}

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
            # Pour les comptes agreges proprietaires : on affiche juste le nom (pas de numero)
            item = {
                "account_number": "" if is_aggregated_owner else acc,
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
                elif acc.startswith("400") or acc.startswith("401") or acc.startswith("416"):
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
                elif acc.startswith("400") or acc.startswith("401"):
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

        # Compute current period result (classes 6 & 7) and inject in 499 (avant repartition)
        # ou re-imputer sur les comptes 4000XX des owners (apres repartition).
        # IMPORTANT : pour que le bilan soit equilibre, on prend toutes les ecritures
        # 6/7 jusqu'a date_to (sans start_date). Par double-entree, le solde net des
        # comptes 1-5 (= bilan) = -(solde net 6-7) = resultat de l'exercice cumule.
        q_res = _apply_copro({}, copropriete_id)
        if date_to:
            q_res["date"] = {"$lte": date_to}
        q_res["journal_type"] = {"$ne": "AN"}
        if view_mode != "after_distribution":
            q_res["$and"] = [
                {"is_regularization": {"$ne": True}},
                {"reference": {"$not": {"$regex": "^(OD-REG-|EXT-)"}}},
            ]
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
        # Resultat = produits - charges. Positif = benefice (boni). Negatif = perte (mali).
        result_exercise = round(total_produits - total_charges, 2)

        # ---- Mode "apres repartition" : repartition du boni/mali sur owners ----
        # Formule garantissant l'equilibre du bilan :
        #   delta[i] = result_exercise * (quotite[i] / total_quotites)
        # (= meme formule que Option B, mais documentee)
        # NOTE : la formule "Option A" stricte (appels_recus[i] - charges_imputees[i])
        # necessite que les appels soient inscrits sur les comptes 4000XX en double-entree,
        # ce qui n'est pas le cas dans le modele actuel. A migrer dans une iteration ulterieure
        # avec materialisation OD permanente a la cloture d'exercice.
        distributed_per_owner = {}
        if view_mode == "after_distribution" and abs(result_exercise) > 0.01:
            lots_for_acp = await db.lots.find(
                {"copropriete_id": copropriete_id}, {"_id": 0}
            ).to_list(10000)
            owners_for_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
            owner_quotities = {}
            total_quotities = 0.0
            for lot in lots_for_acp:
                quo = float(lot.get("quotity", 0) or 0)
                oid = lot.get("owner_id")
                if oid and quo > 0:
                    owner_quotities[oid] = owner_quotities.get(oid, 0.0) + quo
                    total_quotities += quo
            if total_quotities > 0:
                for oid, quo in owner_quotities.items():
                    share = round(result_exercise * (quo / total_quotities), 2)
                    distributed_per_owner[oid] = share

            for oid, delta in distributed_per_owner.items():
                virt_acc = f"OWNER_{oid}"
                if virt_acc in balances:
                    if delta > 0:
                        balances[virt_acc]["credit"] += delta
                    else:
                        balances[virt_acc]["debit"] += abs(delta)
                else:
                    owner_doc = next((o for o in owners_for_acp if o["id"] == oid), None)
                    if not owner_doc:
                        continue
                    balances[virt_acc] = {
                        "account_number": virt_acc,
                        "account_name": owner_doc.get("name", ""),
                        "debit": abs(delta) if delta < 0 else 0.0,
                        "credit": delta if delta > 0 else 0.0,
                        "is_owner_aggregated": True,
                    }

            # Reset buckets et re-classer suite a modification balances
            actif_buckets = {k: [] for k in actif_buckets}
            passif_buckets = {k: [] for k in passif_buckets}
            for acc, b in balances.items():
                solde = round(b["debit"] - b["credit"], 2)
                _classify_account(acc, solde, balances)
            # En mode "apres repartition", le 499 est neutralise (solde = 0), donc PAS d'ajout

        elif abs(result_exercise) > 0.01:
            # Mode AVANT REPARTITION : place le boni/mali sur compte 499
            if result_exercise > 0:
                # Benefice -> 499 CREDITEUR (au Passif)
                passif_buckets["VII_regul_passif"].append({
                    "account_number": "499",
                    "account_name": "Compte de regularisation - Boni a repartir",
                    "amount": abs(result_exercise),
                })
            else:
                # Perte -> 499 DEBITEUR (a l'Actif)
                actif_buckets["VIII_regul_actif"].append({
                    "account_number": "499",
                    "account_name": "Compte de regularisation - Mali a repartir",
                    "amount": abs(result_exercise),
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
        }

    # ---- PDF BILAN (par exercice) ----
    @router.get("/bilan/pdf")
    async def bilan_pdf(
        request: Request,
        copropriete_id: Optional[str] = None,
        fiscal_year_id: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """Genere le PDF Bilan filtree par exercice (ou date_to libre).
        Chinese walls strict : copropriete_id requis."""
        from pdf_bilan import build_bilan_pdf
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
        data = await bilan(
            request=request, date_to=date_to, copropriete_id=copropriete_id,
            fiscal_year_id=fiscal_year_id,
        )
        # Syndic info (si lie a la copro)
        syndic = None
        syndic_id = copro.get("syndic_id")
        if syndic_id:
            syndic = await db.syndics.find_one({"id": syndic_id}, {"_id": 0})
        pdf_bytes = build_bilan_pdf(
            copropriete=copro,
            syndic=syndic,
            fiscal_year=fy,
            bilan_data=data,
            date_to=date_to or (fy.get("end_date") if fy else ""),
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
        """Compte de Resultats PCMN belge structure (rubriques 60-67 / 70-76). Chinese walls strict."""
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
    async def decompte_annuel(request: Request, fiscal_year_id: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, copropriete_id: Optional[str] = None):
        """Decomptes annuels - chinese walls STRICT."""
        copropriete_id = _require_copro(copropriete_id, request)
        if fiscal_year_id:
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy and fy.get("copropriete_id") and fy["copropriete_id"] != copropriete_id:
                raise HTTPException(400, "Cet exercice appartient a une autre ACP.")
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
    async def decompte_pdf(
        owner_id: str,
        request: Request,
        fiscal_year_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        copropriete_id: Optional[str] = None,
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
                 "start_date": {"$lte": fy["end_date"]},
                 "end_date": {"$gte": fy["start_date"]}},
                {"_id": 0}
            )
            if real_fy:
                fy = real_fy

        # Verrou : on ne peut generer un decompte annuel QUE si l'exercice est CLOTURE.
        # Avant cloture, les chiffres sont encore en mouvement et le decompte n'a pas
        # de valeur juridique. Utiliser la situation de compte pour un releve a date.
        fy_status = fy.get("status", "")
        if fy_status != "closed":
            raise HTTPException(
                400,
                f"L'exercice '{fy.get('name','')}' n'est pas cloture (statut: {fy_status or 'inconnu'}). "
                "Le decompte annuel est genere uniquement apres cloture de l'exercice. "
                "Pour un releve a date, utilisez la situation de compte."
            )

        copro_id_use = copropriete_id
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

    # ---- LISTE DES DEPENSES PDF ----
    @router.get("/depenses/pdf")
    async def liste_depenses_pdf(
        copropriete_id: str,
        date_from: str,
        date_to: str,
        distribution_key_id: Optional[str] = None,
        account_number: Optional[str] = None,
    ):
        """Genere le PDF 'Liste des depenses' au format Syndic belge.
        Hierarchie: Cle de repartition -> Nature -> Compte -> lignes.
        Colonnes: Date valeur, Libelle, Fournisseur, Ref. interne, Montant, Part proprietaire, Part occupant.
        """
        from pdf_liste_depenses import build_liste_depenses_pdf

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Build invoice query
        inv_q = {"copropriete_id": copropriete_id,
                 "date": {"$gte": date_from, "$lte": date_to}}
        if distribution_key_id:
            inv_q["distribution_key_id"] = distribution_key_id
        if account_number:
            inv_q["account_number"] = account_number
        invoices = await db.invoices.find(inv_q, {"_id": 0}).sort("date", 1).to_list(100000)

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

        pdf_bytes = build_liste_depenses_pdf(
            copropriete=copro,
            date_from=date_from, date_to=date_to,
            invoices=invoices,
            distribution_keys=distribution_keys,
            pcmn_map=pcmn_map,
            expense_categories=cats,
        )
        filename = f"liste_depenses_{date_from}_au_{date_to}.pdf"
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
        owner_ids = set(l.get("owner_id") for l in lots if l.get("owner_id"))

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
        current_owner_ids = set(l.get("owner_id") for l in lots if l.get("owner_id"))

        # Charge journal entries ACP-scoped (un seul fetch) + filtre periode
        je_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            je_q["date"] = {}
            if start_date:
                je_q["date"]["$gte"] = start_date
            if end_date:
                je_q["date"]["$lte"] = end_date
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Pour le SOLDE CUMULATIF : entries de toutes les dates jusqu'a end_date (inclus anterieurs)
        je_q_cumul = {"copropriete_id": copropriete_id}
        if end_date:
            je_q_cumul["date"] = {"$lte": end_date}
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
                bt_q["date"]["$lte"] = end_date
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
            # Balance CUMULATIF du compte tier (toutes ecritures jusqu'a end_date)
            owner_cumul_accs = (cumul_per_owner.get(oid) or {"by_acc": {}}).get("by_acc", {})
            prov_d_cumul = owner_cumul_accs.get(acc_prov, {}).get("debit", 0.0)
            prov_c_cumul = owner_cumul_accs.get(acc_prov, {}).get("credit", 0.0)
            res_d_cumul = owner_cumul_accs.get(acc_res, {}).get("debit", 0.0)
            res_c_cumul = owner_cumul_accs.get(acc_res, {}).get("credit", 0.0)
            # Fallback : ecritures sans third_party_id mais sur les comptes de l'owner
            if acc_prov and acc_prov in cumul_per_acc:
                prov_d_cumul += cumul_per_acc[acc_prov]["debit"]
                prov_c_cumul += cumul_per_acc[acc_prov]["credit"]
                cumul_per_acc[acc_prov] = {"debit": 0.0, "credit": 0.0}
            if acc_res and acc_res in cumul_per_acc:
                res_d_cumul += cumul_per_acc[acc_res]["debit"]
                res_c_cumul += cumul_per_acc[acc_res]["credit"]
                cumul_per_acc[acc_res] = {"debit": 0.0, "credit": 0.0}
            cumul_called = prov_d_cumul + res_d_cumul
            cumul_paid = prov_c_cumul + res_c_cumul + unmatched_paid
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
    ):
        """Genere un PDF synthese de la balance des tiers (proprietaires + fournisseurs).
        Chinese walls strict : `copropriete_id` requis (param ou header)."""
        from pdf_balance_tiers import build_balance_tiers_pdf

        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") if request else None
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict")

        copro = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not copro:
            raise HTTPException(404, "Copropriete non trouvee")

        # Re-utilise les endpoints internes
        owners_data = await balance_tiers_owners(
            copropriete_id=copropriete_id, start_date=start_date, end_date=end_date,
        )
        suppliers_data = await balance_tiers_suppliers(
            request=request, copropriete_id=copropriete_id,
            start_date=start_date, end_date=end_date,
        )
        # Calcule total_debiteurs/crediteurs pour fournisseurs (positif = a payer)
        sups = suppliers_data.get("suppliers", []) or []
        sup_a_payer = round(sum(s["balance"] for s in sups if s["balance"] > 0), 2)
        sup_acompte = round(sum(abs(s["balance"]) for s in sups if s["balance"] < 0), 2)
        suppliers_data["total_crediteurs"] = sup_a_payer
        suppliers_data["total_debiteurs"] = sup_acompte

        pdf_bytes = build_balance_tiers_pdf(
            copropriete=copro,
            owners_data=owners_data,
            suppliers_data=suppliers_data,
            period_start=start_date or "",
            period_end=end_date or "",
        )
        safe_name = (copro.get("name", "acp") or "acp").replace(" ", "_").replace("/", "_")
        suffix = (end_date or datetime.now(timezone.utc).date().isoformat())
        filename = f"balance-tiers-{safe_name}-{suffix}.pdf"
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
    ):
        """Situation de compte d'un proprietaire (basee sur le grand livre).
        Chinese walls strict. Filtre periode optionnel."""
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
        entry_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            entry_q["date"] = {}
            if start_date: entry_q["date"]["$gte"] = start_date
            if end_date: entry_q["date"]["$lte"] = end_date
        entries = await db.journal_entries.find(entry_q, {"_id": 0}).to_list(100000)
        seen_lines = set()
        for e in entries:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                # Match si compte tiers de l'owner OU explicitement tagge owner
                if acc not in valid_accs and tpid != owner_id:
                    continue
                # Eviter doublons (meme line)
                key = (e.get("id"), acc, ln.get("debit", 0), ln.get("credit", 0), tpid)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                # Utiliser le libelle de la LIGNE en priorite (pour distinguer
                # provisions/reserve/roulement au sein d'une meme ecriture VE).
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

        movements.sort(key=lambda x: (x["date"], x.get("reference", "")))
        running = 0
        for m in movements:
            running += m["debit"] - m["credit"]
            m["running_balance"] = round(running, 2)

        total_debit = round(sum(m["debit"] for m in movements), 2)
        total_credit = round(sum(m["credit"] for m in movements), 2)

        # Detail par compte
        prov_movs = [m for m in movements if m.get("account_number") == acc_prov]
        res_movs = [m for m in movements if m.get("account_number") == acc_res]

        return {
            "owner": owner,
            "account_provisions": acc_prov,
            "account_reserve": acc_res,
            "movements": movements,
            "provisions_debit": round(sum(m["debit"] for m in prov_movs), 2),
            "provisions_credit": round(sum(m["credit"] for m in prov_movs), 2),
            "reserve_debit": round(sum(m["debit"] for m in res_movs), 2),
            "reserve_credit": round(sum(m["credit"] for m in res_movs), 2),
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balance": round(total_debit - total_credit, 2),
            "status": "debiteur" if total_debit > total_credit + 0.01 else ("crediteur" if total_credit > total_debit + 0.01 else "solde"),
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
        from pdf_situation_compte import build_situation_compte_pdf

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

        # Get all entries scoped to ACP
        entries = await db.journal_entries.find(
            {"copropriete_id": copropriete_id}, {"_id": 0}
        ).to_list(100000)

        # Compute opening balance (movements strictly BEFORE start_date)
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
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                if acc not in valid_accs and tpid != owner_id:
                    continue
                key = (e.get("id"), acc, ln.get("debit", 0), ln.get("credit", 0), tpid)
                if key in seen:
                    continue
                seen.add(key)
                d = float(ln.get("debit", 0) or 0)
                c = float(ln.get("credit", 0) or 0)
                date_str = e.get("date", "")
                position = _date_in_range(date_str)
                if position == "before":
                    opening += d - c
                elif position == "in":
                    # Libelle ligne en priorite (distinction provisions/reserve/roulement)
                    line_desc = (ln.get("line_description") or "").strip()
                    entry_desc = (e.get("description", "") or "").strip()
                    movements.append({
                        "date": date_str,
                        "description": line_desc or entry_desc,
                        "reference": e.get("reference", "") or "",
                        "account_number": acc,
                        "account_name": ln.get("account_name", ""),
                        "debit": d,
                        "credit": c,
                        "journal_type": e.get("journal_type", ""),
                    })

        # Bank unmatched (VCS) - dans la periode uniquement
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
                opening -= abs(float(txn.get("amount", 0) or 0))
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

        movements.sort(key=lambda x: (x["date"], x.get("reference", "")))

        # Syndic info (premier syndic admin lie a l'ACP) - fallback sur infos copro
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
        # IBAN par defaut : premier bank_account de l'ACP
        iban = ""
        bic = ""
        for ba in (copro.get("bank_accounts") or []):
            if ba.get("iban"):
                iban = ba["iban"]
                bic = ba.get("bic", "")
                break

        # Owner enrichi pour le PDF
        owner_view = {
            **owner,
            "account_provisions": acc_prov,
            "account_reserve": acc_res,
        }

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
        )
        safe_name = (owner.get("name", "owner") or "owner").replace(" ", "_").replace("/", "_")
        suffix = end_date or datetime.now(timezone.utc).date().isoformat()
        filename = f"situation-{safe_name}-{suffix}.pdf"
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
        suppliers = await db.suppliers.find({}, {"_id": 0}).to_list(10000)
        # Charge journal entries de l'ACP + filtre periode
        je_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            je_q["date"] = {}
            if start_date: je_q["date"]["$gte"] = start_date
            if end_date: je_q["date"]["$lte"] = end_date
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Pour le SOLDE on charge TOUTES les ecritures jusqu'a end_date (sans start_date)
        # car le solde du compte tier est CUMULATIF (anterieurs inclus).
        je_q_cumul = {"copropriete_id": copropriete_id}
        if end_date:
            je_q_cumul["date"] = {"$lte": end_date}
        entries_cumul = await db.journal_entries.find(je_q_cumul, {"_id": 0}).to_list(100000)
        # Charge factures pour les fournisseurs orphelins
        inv_q = {"copropriete_id": copropriete_id}
        if start_date or end_date:
            inv_q["date"] = {}
            if start_date: inv_q["date"]["$gte"] = start_date
            if end_date: inv_q["date"]["$lte"] = end_date
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(10000)

        # Map supplier -> tier account
        supplier_by_id = {}
        tier_to_supplier = {}  # 44000XXX -> supplier
        for s in suppliers:
            supplier_by_id[s["id"]] = s
            tier_acc = ((s.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")
            if tier_acc:
                tier_to_supplier[tier_acc] = s

        # Aggregate by supplier_id (third_party_id) OR by 44000XXX account
        per_supplier = {}  # supplier_id -> {debit, credit, name, tier, invoices_count}
        for e in entries:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                # Compte tier fournisseur reconnu via 44000XXX
                if not acc.startswith("44000") and not acc.startswith("440000"):
                    # Tolerance : autre compte fournisseur ? On ne capture que 4400x
                    if tpid and tpid in supplier_by_id and not acc.startswith("44"):
                        continue
                    elif not (tpid and tpid in supplier_by_id):
                        continue
                # Determine supplier identity
                sup = None
                if tpid and tpid in supplier_by_id:
                    sup = supplier_by_id[tpid]
                elif acc in tier_to_supplier:
                    sup = tier_to_supplier[acc]
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
                tpid = ln.get("third_party_id")
                if not acc.startswith("44000") and not acc.startswith("440000"):
                    if tpid and tpid in supplier_by_id and not acc.startswith("44"):
                        continue
                    elif not (tpid and tpid in supplier_by_id):
                        continue
                sup = None
                if tpid and tpid in supplier_by_id:
                    sup = supplier_by_id[tpid]
                elif acc in tier_to_supplier:
                    sup = tier_to_supplier[acc]
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
    async def situation_compte_supplier(supplier_id: str, copropriete_id: Optional[str] = None):
        """Situation de compte fournisseur basee sur le grand livre.
        Aggrege toutes les ecritures (AC/FI/OD/A-Nouveau) sur 44000XXX du fournisseur."""
        supplier = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if not supplier:
            raise HTTPException(404, "Fournisseur non trouve")

        tier_acc = ""
        if copropriete_id:
            tier_acc = ((supplier.get("tier_accounts") or {}).get(copropriete_id, {}) or {}).get("main", "")

        movements = []
        entry_q = {}
        if copropriete_id:
            entry_q["copropriete_id"] = copropriete_id
        entries = await db.journal_entries.find(entry_q, {"_id": 0}).to_list(100000)
        seen = set()
        for e in entries:
            for ln in e.get("lines", []) or []:
                acc = ln.get("account_number", "")
                tpid = ln.get("third_party_id")
                match = (tpid == supplier_id) or (tier_acc and acc == tier_acc)
                if not match:
                    continue
                key = (e.get("id"), acc, ln.get("debit", 0), ln.get("credit", 0), tpid)
                if key in seen:
                    continue
                seen.add(key)
                movements.append({
                    "date": e.get("date", ""),
                    "description": f"[{e.get('journal_type','?')}] {e.get('description','')}".strip(),
                    "debit": float(ln.get("debit", 0) or 0),
                    "credit": float(ln.get("credit", 0) or 0),
                    "type": e.get("journal_type", "OD").lower(),
                    "reference": e.get("reference", "") or e.get("id", ""),
                    "account_number": acc,
                    "journal_type": e.get("journal_type", ""),
                })

        movements.sort(key=lambda x: (x["date"], x.get("reference", "")))
        running = 0
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

    return router
