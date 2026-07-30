from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import uuid

from routes.reports import compute_regularization_per_owner  # iter93cg


class FiscalYearInput(BaseModel):
    name: str
    start_date: str
    end_date: str
    copropriete_id: Optional[str] = ""
    # iter90dq : prefixe libre pour l'auto-numerotation des factures de cet
    # exercice (ex : "ACACIA-2025-", "FA-2025-", "ACP01-", ...).
    # Chaque nouvelle facture creee pendant l'exercice recevra
    # `internal_reference = f"{invoice_number_prefix}{next_seq:04d}"`.
    # Si vide, on retombe sur le prefixe par defaut `FA-YYYY-`.
    invoice_number_prefix: Optional[str] = ""


class BudgetLineInput(BaseModel):
    account_number: str
    account_name: Optional[str] = ""
    amount: float
    distribution_key_id: Optional[str] = ""


class BudgetInput(BaseModel):
    fiscal_year_id: str
    name: Optional[str] = ""
    lines: List[BudgetLineInput]
    copropriete_id: Optional[str] = ""
    # iter90cj : engagement AG de fonds de reserve / fonds de roulement.
    # Ces montants sont persistes sur le budget des le vote (POST/PUT), meme
    # avant que les appels de fonds correspondants ne soient generes.
    # Consommes par _compute_mutation_breakdown (properties.py) pour calculer
    # le transfert MUT-R du capital roulement engage, meme si aucun appel
    # roulement n'a encore ete emis a la date de mutation.
    reserve_fund_amount: Optional[float] = 0.0
    reserve_fund_key_id: Optional[str] = ""
    roulement_fund_amount: Optional[float] = 0.0
    roulement_fund_key_id: Optional[str] = ""


def create_fiscal_router(db):
    router = APIRouter(prefix="/api/fiscal")

    # ---- FISCAL YEARS ----
    @router.get("/years")
    async def list_fiscal_years(request: Request, copropriete_id: Optional[str] = None):
        from syndic_scope import syndic_query
        q = {**syndic_query(request)}
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        years = await db.fiscal_years.find(q, {"_id": 0}).sort("start_date", -1).to_list(100)
        # iter90h0 : si result_net est None (ex: exercice reouvert avant iter90h0
        # ou jamais cloture), on le calcule a la volee = Sum(cl.7) - Sum(cl.6)
        # sur la periode, en excluant les OD de regularisation/extourne et les AN
        # pour eviter le double comptage.
        for y in years:
            if y.get("result_net") in (None, "", 0) and y.get("start_date") and y.get("end_date"):
                je_q = {
                    "copropriete_id": y.get("copropriete_id", ""),
                    "date": {"$gte": y["start_date"], "$lte": (y["end_date"] + "T23:59:59") if len(y["end_date"]) == 10 else y["end_date"]},
                    "journal_type": {"$ne": "AN"},
                    "is_regularization": {"$ne": True},
                    "reversed": {"$ne": True},
                    "is_reversal": {"$ne": True},
                }
                entries = await db.journal_entries.find(je_q, {"_id": 0, "lines": 1}).to_list(100000)
                total_c6 = 0.0
                total_c7 = 0.0
                for e in entries:
                    for ln in e.get("lines", []):
                        acc = ln.get("account_number", "")
                        if not acc:
                            continue
                        if acc.startswith("6"):
                            total_c6 += (ln.get("debit", 0) or 0) - (ln.get("credit", 0) or 0)
                        elif acc.startswith("7"):
                            total_c7 += (ln.get("credit", 0) or 0) - (ln.get("debit", 0) or 0)
                y["result_net"] = round(total_c7 - total_c6, 2)
        return years

    @router.post("/years")
    async def create_fiscal_year(data: FiscalYearInput, request: Request):
        from syndic_scope import inject_syndic
        doc = {
            "id": str(uuid.uuid4()),
            "name": data.name,
            "start_date": data.start_date,
            "end_date": data.end_date,
            "status": "open",
            "copropriete_id": data.copropriete_id or "",
            "invoice_number_prefix": (data.invoice_number_prefix or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        inject_syndic(doc, request)
        await db.fiscal_years.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.put("/years/{year_id}")
    async def update_fiscal_year(year_id: str, data: FiscalYearInput):
        result = await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {
                "name": data.name,
                "start_date": data.start_date,
                "end_date": data.end_date,
                # iter90dq : modifiable en cours d'exercice (prochaines factures
                # utilisent le nouveau prefixe, les anciennes ne sont PAS renumerotees).
                "invoice_number_prefix": (data.invoice_number_prefix or "").strip(),
            }},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "Exercice non trouve")
        return await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})

    @router.post("/years/{year_id}/close")
    async def close_fiscal_year(year_id: str):
        """Cloture d'exercice: verrouille les ecritures et genere l'a-nouveau."""
        fy = await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")
        if fy["status"] == "closed":
            raise HTTPException(400, "Exercice deja cloture")

        # Chinese wall: scope by ACP of the fiscal year
        copro_id = fy.get("copropriete_id", "")
        # IMPORTANT : l'A-nouveau doit refleter le solde REEL au moment de la cloture,
        # donc on prend TOUTES les ecritures jusqu'a la fin de l'exercice (cumul),
        # et INCLURE l'AN d'ouverture (is_opening_balance=True) tout en excluant
        # les AN de cloture (pour eviter le double comptage sur re-closures).
        # iter93cg-fix4 : inclure AN opening pour que le solde AN refleter le
        # bilan preview after_distribution (Guerit doit voir son opening -21 EUR).
        je_q = {
            "date": {"$lte": fy["end_date"]},
            "$or": [
                {"journal_type": {"$ne": "AN"}},
                {"journal_type": "AN", "is_opening_balance": True},
            ],
            # Exclure les paires reversed/is_reversal des anciennes clotures
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }
        if copro_id:
            je_q["copropriete_id"] = copro_id
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Pour le resultat de l'exercice : limiter aux ecritures de la PERIODE (classes 6/7)
        je_q_periode = {
            "date": {"$gte": fy["start_date"], "$lte": fy["end_date"]},
            "journal_type": {"$ne": "AN"},
        }
        if copro_id:
            je_q_periode["copropriete_id"] = copro_id
        entries_periode = await db.journal_entries.find(je_q_periode, {"_id": 0}).to_list(100000)

        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"debit": 0, "credit": 0, "name": line.get("account_name", "")}
                balances[acc]["debit"] += line.get("debit", 0)
                balances[acc]["credit"] += line.get("credit", 0)

        # Compute result (class 6 - class 7) sur la PERIODE de l'exercice
        balances_periode = {}
        for entry in entries_periode:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances_periode:
                    balances_periode[acc] = {"debit": 0, "credit": 0}
                balances_periode[acc]["debit"] += line.get("debit", 0)
                balances_periode[acc]["credit"] += line.get("credit", 0)
        total_charges = sum(b["debit"] - b["credit"] for a, b in balances_periode.items() if a.startswith("6"))
        total_produits = sum(b["credit"] - b["debit"] for a, b in balances_periode.items() if a.startswith("7"))
        result_net = total_produits - total_charges

        # ==================== iter93cg : MOTEUR UNIQUE ====================
        # Utiliser le meme moteur que compute_bilan_data (mode after_distribution)
        # pour garantir que les montants OD-REG generes = montants affiches
        # dans la previsualisation du Bilan (regle utilisateur imperative).
        # Filtre : exclure les extournes/regularisations existantes pour ne
        # calculer les quotes-parts que sur les ecritures reelles de la periode.
        entries_periode_filtered = [
            e for e in entries_periode
            if not e.get("reversed") and not e.get("is_reversal")
            and not e.get("is_regularization")
            and not (e.get("reference", "") or "").startswith(("OD-REG-", "EXT-"))
        ]
        regul = await compute_regularization_per_owner(
            db, copro_id, entries_periode_filtered
        )
        charges_per_owner = regul["charges_per_owner"]
        products_per_owner = regul["products_per_owner"]
        appels_fr_per_owner = regul["appels_fr_per_owner"]
        # Sur-ecrire les totaux calcules avec ceux du moteur (source unique
        # de verite - evite les incoherences de rounding vs bilan preview)
        total_charges = regul["total_charges"]
        total_produits = regul["provisions_appelees"] + regul["total_products"]
        result_net = total_produits - total_charges

        # ==================== iter93cg-fix1 : NORMALISATION ====================
        # Le compute_regularization_per_owner distribue les charges/appels sur
        # les proprietaires via ratios per-lot. Des lots orphelins (sans
        # owner_ids) ou owners sans tier_accounts.provisions peuvent faire
        # que sum(charges_per_owner) < total_charges. Sans normalisation, la
        # difference (`drift`) etait shifted sur le dernier owner en dict
        # iteration order -> bug audit sur Guerit (AN=99.53 credit vs
        # bilan=26.95 debit).
        # Fix : normaliser sum(x_per_owner) == totals correspondants par
        # scaling factor. Chaque proprietaire prend sa juste part du drift.
        def _normalize(per_owner: dict, target_total: float) -> dict:
            s = sum(per_owner.values())
            if s <= 0.01 or abs(s - target_total) < 0.01:
                return per_owner
            scale = target_total / s
            return {oid: round(v * scale, 2) for oid, v in per_owner.items()}

        charges_per_owner = _normalize(charges_per_owner, total_charges)
        products_per_owner = _normalize(
            products_per_owner, regul["total_products"]
        )
        appels_fr_per_owner = _normalize(
            appels_fr_per_owner, regul["provisions_appelees"]
        )

        owners_acp = await db.owners.find({}, {"_id": 0}).to_list(10000)
        owner_by_id = {o["id"]: o for o in owners_acp}

        def _owner_account(oid: str) -> tuple:
            o = owner_by_id.get(oid)
            if not o:
                return "", ""
            accs = ((o.get("tier_accounts") or {}).get(copro_id, {}) or {})
            return accs.get("provisions", ""), o.get("name", "")

        end_dt_iso = fy["end_date"]

        # OD-1 : Annulation des provisions appelees (per-owner via appels_fr reels)
        # Chaque proprietaire est credite EXACTEMENT du montant qu'il a paye
        # (debits VE sur son 41010XX), garantissant l'annulation parfaite au
        # niveau de chaque compte owner.
        if abs(total_produits) > 0.01 and appels_fr_per_owner:
            ad_lines = []
            prod_accs = {a: b for a, b in balances_periode.items() if a.startswith("7")}
            for acc, b in prod_accs.items():
                net = b["credit"] - b["debit"]
                if abs(net) > 0.01:
                    ad_lines.append({
                        "account_number": acc, "account_name": "Annulation produits",
                        "debit": round(net, 2), "credit": 0,
                    })
            for oid, appels_amt in appels_fr_per_owner.items():
                acc_p, name = _owner_account(oid)
                if not acc_p:
                    continue
                amt = round(appels_amt, 2)
                if amt > 0.01:
                    ad_lines.append({
                        "account_number": acc_p, "account_name": name,
                        "debit": 0, "credit": amt,
                        "third_party_id": oid, "third_party_name": name,
                    })
            if ad_lines:
                td = sum(l["debit"] for l in ad_lines)
                tc = sum(l["credit"] for l in ad_lines)
                diff = round(td - tc, 2)
                if abs(diff) >= 0.01:
                    # iter93cg-fix3 : plus de shift sur le "dernier" owner
                    # (bug audit Guerit). Grace a la normalisation en amont
                    # (sum(appels_fr_per_owner) == total_produits), cette
                    # branche ne s'execute plus qu'en cas d'ecart cent-a-cent
                    # residuel. On l'absorbe sur la ligne cl.7 (Dr) plutot que
                    # sur un proprietaire arbitraire.
                    for line in ad_lines:
                        if not line.get("third_party_id"):
                            line["debit"] = round(line["debit"] - diff, 2)
                            break
                    td = sum(l["debit"] for l in ad_lines)
                await db.journal_entries.insert_one({
                    "id": str(uuid.uuid4()),
                    "journal_type": "OD",
                    "date": end_dt_iso,
                    "reference": f"OD-REG-PROV-{fy['name']}",
                    "description": f"Annulation provisions appelees - cloture {fy['name']}",
                    "lines": ad_lines,
                    "total_debit": round(td, 2),
                    "total_credit": round(tc, 2),
                    "fiscal_year_id": year_id,
                    "copropriete_id": copro_id,
                    "is_regularization": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

        # OD-2 : Imputation charges reelles aux owners (per-key via distribution_lines)
        # Chaque proprietaire est debite EXACTEMENT de sa quote-part per-key
        # calculee par compute_regularization_per_owner (meme montant que
        # dans la previsualisation Bilan apres repartition).
        if abs(total_charges) > 0.01 and charges_per_owner:
            ic_lines = []
            for oid, charge_amt in charges_per_owner.items():
                acc_p, name = _owner_account(oid)
                if not acc_p:
                    continue
                amt = round(charge_amt, 2)
                if amt > 0.01:
                    ic_lines.append({
                        "account_number": acc_p, "account_name": name,
                        "debit": amt, "credit": 0,
                        "third_party_id": oid, "third_party_name": name,
                    })
            chrg_accs = {a: b for a, b in balances_periode.items() if a.startswith("6") and not a.startswith("643")}
            for acc, b in chrg_accs.items():
                net = b["debit"] - b["credit"]
                if abs(net) > 0.01:
                    ic_lines.append({
                        "account_number": acc, "account_name": "Imputation charges aux owners",
                        "debit": 0, "credit": round(net, 2),
                    })
            if ic_lines:
                td = sum(l["debit"] for l in ic_lines)
                tc = sum(l["credit"] for l in ic_lines)
                diff = round(td - tc, 2)
                if abs(diff) >= 0.01:
                    # iter93cg-fix3 : absorber le drift cent-a-cent sur la
                    # ligne cl.6 (Cr) plutot que sur un proprietaire.
                    for line in ic_lines:
                        if not line.get("third_party_id"):
                            line["credit"] = round(line["credit"] + diff, 2)
                            break
                    tc = sum(l["credit"] for l in ic_lines)
                await db.journal_entries.insert_one({
                    "id": str(uuid.uuid4()),
                    "journal_type": "OD",
                    "date": end_dt_iso,
                    "reference": f"OD-REG-CHRG-{fy['name']}",
                    "description": f"Imputation charges aux proprietaires - cloture {fy['name']}",
                    "lines": ic_lines,
                    "total_debit": round(td, 2),
                    "total_credit": round(tc, 2),
                    "fiscal_year_id": year_id,
                    "copropriete_id": copro_id,
                    "is_regularization": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

        # Recharge balances apres les 2 OD de regularisation pour generer l'AN
        # (les comptes 6/7 sont maintenant a zero)
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)
        balances = {}
        for entry in entries:
            for line in entry.get("lines", []):
                acc = line["account_number"]
                if acc not in balances:
                    balances[acc] = {"debit": 0, "credit": 0, "name": line.get("account_name", "")}
                balances[acc]["debit"] += line.get("debit", 0)
                balances[acc]["credit"] += line.get("credit", 0)

        # Generate a-nouveau entries for balance sheet accounts (classes 1-5)
        a_nouveau_lines = []
        for acc, bal in sorted(balances.items()):
            if acc[0] in ("1", "2", "3", "4", "5"):
                solde = bal["debit"] - bal["credit"]
                if abs(solde) > 0.01:
                    a_nouveau_lines.append({
                        "account_number": acc,
                        "account_name": bal["name"],
                        "debit": round(solde, 2) if solde > 0 else 0,
                        "credit": round(-solde, 2) if solde < 0 else 0,
                    })

        # ==================== iter93cg-fix2 : PAS de 140100 en AN ====================
        # Le compte 140100/140200 (benefice/perte reporte) N'EST PLUS poste
        # dans l'AN car le resultat de l'exercice a deja ete DISTRIBUE aux
        # proprietaires via les OD-REG-PROV / OD-REG-CHRG (chaque owner a
        # absorbe sa quote-part per-key). Poster 140100=result_net creerait
        # un DOUBLE-COUNT (deja compte dans les soldes owners) et deballancerait
        # l'AN de exactement result_net EUR.
        #
        # Bug detecte par testing agent iter93cg : "AN entry is UNBALANCED
        # by EXACTLY result_net (40485.01 EUR on Agathe FY 2026-2027)".
        #
        # Regle : le resultat de l'exercice appartient collectivement aux
        # proprietaires (dette de l'ACP envers eux ou creance selon le signe).
        # Il est donc redistribue en tant que solde net-a-nouveau sur leurs
        # comptes individuels, PAS conserve dans un compte de report globale.

        if a_nouveau_lines:
            total_d = sum(l["debit"] for l in a_nouveau_lines)
            total_c = sum(l["credit"] for l in a_nouveau_lines)
            # Date AN = 01/01/N+1 (lendemain de la cloture), pas le 31/12 (qui doublerait)
            try:
                end_dt = datetime.strptime(fy["end_date"], "%Y-%m-%d")
                an_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                an_date = fy["end_date"]
            a_nouveau_entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "AN",
                "date": an_date,
                "reference": f"AN-{fy['name']}",
                "description": f"A-nouveau ouverture exercice suivant {fy['name']}",
                "lines": a_nouveau_lines,
                "total_debit": round(total_d, 2),
                "total_credit": round(total_c, 2),
                "fiscal_year_id": year_id,
                "copropriete_id": copro_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(a_nouveau_entry)

        await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {
                "status": "closed",
                "result_net": round(result_net, 2),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

        return {
            "message": f"Exercice {fy['name']} cloture",
            "result_net": round(result_net, 2),
            "total_charges": round(total_charges, 2),
            "total_produits": round(total_produits, 2),
            "a_nouveau_lines": len(a_nouveau_lines),
        }

    @router.post("/years/{year_id}/reopen")
    async def reopen_fiscal_year(year_id: str):
        """Reouvre un exercice CLOTURE.
        Contre-passe (extourne) :
          - les OD de regularisation (`is_regularization=True`)
          - l'ecriture AN (A-nouveau) generee a la cloture
        Les ecritures ORIGINALES sont conservees mais marquees `reversed=True`.
        Les ecritures de contre-passation sont marquees `is_reversal=True` et
        portent un `reverses_entry_id` pointant vers l'originale.
        Le solde des comptes redevient identique a celui d'avant la cloture.
        """
        fy = await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")
        if fy.get("status") != "closed":
            raise HTTPException(400, "Cet exercice n'est pas cloture")

        # Recupere les ecritures de cloture a extourner :
        # 1) OD de regularisation
        # 2) AN (A-nouveau)
        # On exclut celles deja reversees pour eviter les doubles passes
        closing_entries = await db.journal_entries.find({
            "fiscal_year_id": year_id,
            "$or": [
                {"is_regularization": True},
                {"journal_type": "AN"},
            ],
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
        }, {"_id": 0}).to_list(10000)

        reversal_count = 0
        for orig in closing_entries:
            # Construit l'ecriture de contre-passation : inverse Dr<->Cr ligne par ligne
            rev_lines = []
            for ln in orig.get("lines", []):
                rev_lines.append({
                    **{k: v for k, v in ln.items() if k not in ("debit", "credit")},
                    "debit": float(ln.get("credit", 0) or 0),
                    "credit": float(ln.get("debit", 0) or 0),
                })
            rev_id = str(uuid.uuid4())
            rev_doc = {
                "id": rev_id,
                "journal_type": orig.get("journal_type", "OD"),
                "date": datetime.now(timezone.utc).date().isoformat(),
                "reference": f"EXT-{orig.get('reference','')}",
                "description": f"Contre-passation : {orig.get('description','')}",
                "lines": rev_lines,
                "total_debit": round(sum(l["debit"] for l in rev_lines), 2),
                "total_credit": round(sum(l["credit"] for l in rev_lines), 2),
                "fiscal_year_id": year_id,
                "copropriete_id": orig.get("copropriete_id", ""),
                "is_reversal": True,
                "reverses_entry_id": orig.get("id"),
                "is_regularization": bool(orig.get("is_regularization")),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(rev_doc)
            # Marque l'originale comme reversee
            await db.journal_entries.update_one(
                {"id": orig["id"]},
                {"$set": {
                    "reversed": True,
                    "reversed_at": datetime.now(timezone.utc).isoformat(),
                    "reversed_by_entry_id": rev_id,
                }},
            )
            reversal_count += 1

        # Reouverture : on conserve `result_net` (montant calcule a la cloture,
        # utile pour l'affichage du tableau des exercices meme apres extourne
        # des OD de cloture). Le resultat de l'exercice est independant des
        # OD de regularisation (calcule = Sum(cl.7) - Sum(cl.6) sur la periode).
        await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {"status": "open", "closed_at": None}}
        )
        return {
            "message": f"Exercice {fy.get('name','?')} reouvert. {reversal_count} ecriture(s) de cloture extournee(s).",
            "reversed_entries": reversal_count,
            "fiscal_year": fy.get("name", ""),
        }

    @router.post("/years/{year_id}/regularize")
    async def regularize_fiscal_year(year_id: str, dry_run: Optional[bool] = False):
        """Cloture comptable avec regularisation par cle de repartition (belge).
        Workflow:
          1) Calculer Total frais reels (factures classe 6) vs Budget
          2) Extourner les ecritures VE de provisions (compte 700000) pour annuler
             les provisions appelees
          3) Affecter les frais reels par cle de repartition au niveau de chaque
             proprietaire (Dr 40000XXX par proprio, Cr 700000)
          4) La difference entre provisions et frais reels reste sur le compte 40000XXX
             (debiteur = proprio doit; crediteur = proprio a recevoir)
        Le fonds de reserve (compte 700010/40010) n'est PAS extourne (reste au bilan)."""
        fy = await db.fiscal_years.find_one({"id": year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")
        if fy.get("status") == "closed":
            raise HTTPException(400, "Exercice deja cloture")
        copro_id = fy.get("copropriete_id", "")
        if not copro_id:
            raise HTTPException(400, "Exercice sans copropriete")

        start, end = fy["start_date"], fy["end_date"]

        # 1) Budget approved for this FY
        budget = await db.budgets.find_one(
            {"fiscal_year_id": year_id, "status": "approved"}, {"_id": 0}
        )

        # 2) Total real expenses (class 6) booked via AC + manual OD
        inv_q = {"date": {"$gte": start, "$lte": end}, "copropriete_id": copro_id}
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(50000)
        # Group by (account_number, distribution_key_id)
        from collections import defaultdict
        by_nature_key = defaultdict(float)  # (account, key_id) -> amount
        for inv in invoices:
            acc = inv.get("account_number", "") or "600000"
            key_id = inv.get("distribution_key_id", "") or ""
            by_nature_key[(acc, key_id)] += inv.get("total_amount", 0)
        # Also include manual OD entries with class-6 lines
        je_q = {"date": {"$gte": start, "$lte": end}, "copropriete_id": copro_id,
                "journal_type": "OD"}
        manual = await db.journal_entries.find(je_q, {"_id": 0}).to_list(50000)
        for e in manual:
            for line in e.get("lines", []):
                acc = line.get("account_number", "")
                if not acc.startswith("6"):
                    continue
                net = line.get("debit", 0) - line.get("credit", 0)
                if abs(net) < 0.01:
                    continue
                by_nature_key[(acc, "")] += net
        total_real = round(sum(by_nature_key.values()), 2)

        # 3) Total provisions called (700000) - exclude reserve
        # iter90em : EXCLURE les VE deja extournees (reversed=True) et les
        # contre-passations (is_reversal=True) pour eviter de compter les
        # provisions en double si la regularisation est relancee.
        ve_q = {"date": {"$gte": start, "$lte": end}, "copropriete_id": copro_id,
                "journal_type": "VE",
                "reversed": {"$ne": True},
                "is_reversal": {"$ne": True}}
        ve_entries = await db.journal_entries.find(ve_q, {"_id": 0}).to_list(50000)
        # iter90g4 : precharge les fund_calls references par les VE pour
        # distinguer les lignes "provisions charges" (a regulariser) des lignes
        # "fonds de roulement" (non regularisables) qui utilisent le MEME
        # compte tier `accs["provisions"]` = 4101XXXX. Voir auto_entries.py
        # ligne 492-503 : `account_number = accs["provisions"]` pour les
        # DEUX types, seul `account_name`/`line_description` differe.
        fc_ids = list({e.get("source_id") for e in ve_entries
                       if e.get("source_type") == "fund_call" and e.get("source_id")})
        fund_calls_map = {}
        if fc_ids:
            async for fc in db.fund_calls.find(
                {"id": {"$in": fc_ids}}, {"_id": 0}
            ):
                fund_calls_map[fc["id"]] = fc
        provisions_called_by_owner = defaultdict(float)  # owner_id -> amount
        provisions_called_total = 0.0
        # Track VE entry IDs to mark as reversed later (iter90em)
        ve_entry_ids_to_reverse = set()
        from tier_accounts import is_provisions_account
        for e in ve_entries:
            # iter90g4 : detecte le type de l'appel via fund_call. Un appel
            # 100% reserve ou 100% roulement doit etre ignore. Un appel
            # provisions mixte (avec roulement_amount > 0) verra ses lignes
            # roulement filtrees en aval.
            fc = fund_calls_map.get(e.get("source_id"))
            call_type = (fc or {}).get("call_type", "")
            if call_type in ("reserve", "roulement"):
                # Appel dedie a reserve ou roulement -> exclu integralement
                continue
            has_provision_line = False
            for line in e.get("lines", []):
                acc = line.get("account_number", "")
                # provisions only (legacy 40000XXX OR new 4101XXXX), not reserve
                if not is_provisions_account(acc):
                    continue
                # iter90g4 : exclure les lignes "Fonds roulement" qui utilisent
                # le meme compte tier (4101XXXX) que les provisions charges.
                # Le seul discriminant est `account_name` ou `line_description`.
                _an = (line.get("account_name") or "").lower()
                _ld = (line.get("line_description") or "").lower()
                if _an.startswith("fonds roulement") or _ld.startswith("appel fonds de roulement"):
                    continue
                amt = line.get("debit", 0) - line.get("credit", 0)
                if amt > 0 and line.get("third_party_id"):
                    provisions_called_by_owner[line["third_party_id"]] += amt
                    provisions_called_total += amt
                    has_provision_line = True
            if has_provision_line:
                ve_entry_ids_to_reverse.add(e["id"])
        provisions_called_total = round(provisions_called_total, 2)

        # 4) Resolve owner accounts and distribution keys
        owner_ids = list(provisions_called_by_owner.keys())
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(10000)
        from tier_accounts import assign_owner_accounts, get_owner_accounts
        for o in owners:
            await assign_owner_accounts(db, o, copro_id)
        owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(10000)
        owners_map = {o["id"]: o for o in owners}
        lots = await db.lots.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(10000)
        keys = await db.distribution_keys.find({"copropriete_id": copro_id}, {"_id": 0}).to_list(1000)
        keys_map = {k["id"]: k for k in keys}

        def _distribute(amount: float, key_id: str) -> dict:
            dist = defaultdict(float)
            # iter90em : fallback phantom key via lot_number normalise
            # (aligne avec iter90du : les distribution_keys peuvent contenir
            # des lot_id qui ne pointent plus vers les lots actuels apres
            # suppression/re-import Optipro).
            def _norm_num(v):
                return (str(v or "").strip().lstrip("0") or "0")
            lots_by_norm_number = {
                _norm_num(l.get("number", "")): l for l in lots
            }
            if key_id and key_id in keys_map:
                key = keys_map[key_id]
                total_shares = sum(l["share"] for l in key.get("lots", []))
                for kl in key.get("lots", []):
                    lot = next((l for l in lots if l["id"] == kl["lot_id"]), None)
                    # iter90em : phantom fallback par lot_number normalise
                    if not lot:
                        lot = lots_by_norm_number.get(_norm_num(kl.get("lot_number", "")))
                    if not lot or not lot.get("owner_id"):
                        continue
                    ratio = kl["share"] / total_shares if total_shares > 0 else 0
                    dist[lot["owner_id"]] += amount * ratio
            else:
                total_quotity = sum(l.get("quotity", 0) for l in lots if l.get("owner_id"))
                # iter90em : fallback si quotity=0 partout -> utilise default distribution key
                if total_quotity <= 0:
                    default_key = next((k for k in keys if k.get("is_default")), None)
                    if default_key:
                        total_shares = sum(l["share"] for l in default_key.get("lots", []))
                        for kl in default_key.get("lots", []):
                            lot = next((l for l in lots if l["id"] == kl["lot_id"]), None)
                            if not lot:
                                lot = lots_by_norm_number.get(_norm_num(kl.get("lot_number", "")))
                            if not lot or not lot.get("owner_id"):
                                continue
                            ratio = kl["share"] / total_shares if total_shares > 0 else 0
                            dist[lot["owner_id"]] += amount * ratio
                        return dist
                for lot in lots:
                    if not lot.get("owner_id"):
                        continue
                    ratio = lot.get("quotity", 0) / total_quotity if total_quotity > 0 else 0
                    dist[lot["owner_id"]] += amount * ratio
            return dist

        # 5) Compute real expense allocation per owner using each nature's key
        real_by_owner = defaultdict(float)  # owner_id -> amount of real expenses
        by_nature_key_detail = []
        for (acc, key_id), amount in by_nature_key.items():
            d = _distribute(amount, key_id)
            kn = keys_map.get(key_id, {}).get("name", "Tantiemes")
            by_nature_key_detail.append({"account": acc, "key_id": key_id, "key_name": kn, "amount": round(amount, 2)})
            for oid, v in d.items():
                real_by_owner[oid] += v

        # 6) Per-owner regularization
        per_owner = []
        for oid in set(list(provisions_called_by_owner.keys()) + list(real_by_owner.keys())):
            owner = owners_map.get(oid) or await db.owners.find_one({"id": oid}, {"_id": 0})
            if not owner:
                continue
            owner = await assign_owner_accounts(db, owner, copro_id)
            accs = get_owner_accounts(owner, copro_id)
            called = round(provisions_called_by_owner.get(oid, 0), 2)
            real = round(real_by_owner.get(oid, 0), 2)
            regul = round(real - called, 2)  # positive = doit payer plus, negative = trop paye
            per_owner.append({
                "owner_id": oid,
                "owner_name": owner.get("name", ""),
                "vcs_code": owner.get("vcs_code", ""),
                "account_provisions": accs.get("provisions", ""),
                "provisions_called": called,
                "real_expenses": real,
                "regularization": regul,
                "status": "debiteur" if regul > 0.01 else ("crediteur" if regul < -0.01 else "solde"),
            })
        per_owner.sort(key=lambda x: x["owner_name"])

        summary = {
            "fiscal_year": fy["name"],
            "budget_total": budget.get("total", 0) if budget else 0,
            "total_real_expenses": total_real,
            "total_provisions_called": provisions_called_total,
            "difference_budget_vs_real": round((budget.get("total", 0) if budget else 0) - total_real, 2),
            "owners_debiteurs": sum(1 for p in per_owner if p["status"] == "debiteur"),
            "owners_crediteurs": sum(1 for p in per_owner if p["status"] == "crediteur"),
        }

        if dry_run:
            return {"persisted": False, "summary": summary,
                    "per_owner": per_owner, "by_nature_key": by_nature_key_detail}

        # 7) Persist: extourne provisions + affecte frais reels
        # ----- 7a) Extourne (annule) les VE provisions -----
        extourne_lines = []
        sum_extourne = 0.0
        for oid, amt in provisions_called_by_owner.items():
            owner = owners_map.get(oid)
            if not owner:
                continue
            accs = get_owner_accounts(owner, copro_id)
            if not accs.get("provisions") or amt <= 0:
                continue
            # Reverse: credit owner, debit 700000
            extourne_lines.append({
                "account_number": accs["provisions"],
                "account_name": f"Prov. charges - {owner.get('last_name','')}",
                "debit": 0.0, "credit": round(amt, 2),
                "third_party_id": oid, "third_party_name": owner.get("name", ""),
            })
            sum_extourne += amt
        if extourne_lines:
            extourne_lines.append({
                "account_number": "700000",
                "account_name": "Provisions appelees pour charges (extourne)",
                "debit": round(sum_extourne, 2), "credit": 0.0,
                "third_party_id": None, "third_party_name": "",
            })
            extourne_entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": end,
                "reference": f"EXT-{fy['name']}",
                "description": f"Extourne provisions exercice {fy['name']}",
                "lines": extourne_lines,
                "total_debit": round(sum_extourne, 2),
                "total_credit": round(sum_extourne, 2),
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "regularization",
                "source_id": year_id,
                # iter90em : marque cette OD comme contre-passation pour que
                # _exclude_reversals la retire des balances/bilan.
                "is_reversal": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(extourne_entry)
            # iter90em : marque TOUTES les VE originales comme reversed=True
            # avec reference a l'extourne. Sans cela, `_exclude_reversals`
            # les laisserait dans le bilan -> doublons de provisions et
            # boni artificiel enorme si la regularisation est relancee.
            if ve_entry_ids_to_reverse:
                await db.journal_entries.update_many(
                    {"id": {"$in": list(ve_entry_ids_to_reverse)}},
                    {"$set": {
                        "reversed": True,
                        "reversed_by_entry_id": extourne_entry["id"],
                        "reversed_at": datetime.now(timezone.utc).isoformat(),
                        "reversed_reason": "regularization",
                    }},
                )

        # ----- 7b) Affectation frais reels par proprietaire -----
        affect_lines = []
        sum_affect = 0.0
        for p in per_owner:
            if p["real_expenses"] <= 0 or not p["account_provisions"]:
                continue
            affect_lines.append({
                "account_number": p["account_provisions"],
                "account_name": f"Prov. charges - {p['owner_name']}",
                "debit": p["real_expenses"], "credit": 0.0,
                "third_party_id": p["owner_id"], "third_party_name": p["owner_name"],
            })
            sum_affect += p["real_expenses"]
        if affect_lines:
            affect_lines.append({
                "account_number": "700000",
                "account_name": "Affectation frais reels",
                "debit": 0.0, "credit": round(sum_affect, 2),
                "third_party_id": None, "third_party_name": "",
            })
            affect_entry = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": end,
                "reference": f"AFF-{fy['name']}",
                "description": f"Affectation frais reels exercice {fy['name']}",
                "lines": affect_lines,
                "total_debit": round(sum_affect, 2),
                "total_credit": round(sum_affect, 2),
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "regularization",
                "source_id": year_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(affect_entry)

        # 8) Mark FY as regularized (not closed yet - user must do final close after)
        await db.fiscal_years.update_one(
            {"id": year_id},
            {"$set": {
                "regularized_at": datetime.now(timezone.utc).isoformat(),
                "regularization_summary": summary,
            }}
        )

        return {"persisted": True, "summary": summary,
                "per_owner": per_owner, "by_nature_key": by_nature_key_detail,
                "extourne_total": round(sum_extourne, 2),
                "affectation_total": round(sum_affect, 2)}

    @router.delete("/years/{year_id}/regularize")
    async def revert_regularization(year_id: str):
        """Annule la regularisation (utile en cas d'erreur).

        iter90em : demarque aussi les VE originales (reversed=False) qui
        avaient ete marquees par la regularisation. Sans cela, les provisions
        appelees restent invisibles au bilan/balance apres rollback.
        """
        # 1) Trouve l'ID de l'extourne (EXT-XXX) genere par cette regul
        ext_entries = await db.journal_entries.find(
            {"source_type": "regularization", "source_id": year_id},
            {"_id": 0, "id": 1},
        ).to_list(100)
        ext_ids = [e["id"] for e in ext_entries]
        # 2) Demarque les VE originales pointees par ces extournes
        if ext_ids:
            await db.journal_entries.update_many(
                {"reversed_by_entry_id": {"$in": ext_ids}},
                {"$unset": {
                    "reversed": "",
                    "reversed_by_entry_id": "",
                    "reversed_at": "",
                    "reversed_reason": "",
                }},
            )
        # 3) Supprime les OD de regularisation (EXT + AFF)
        await db.journal_entries.delete_many({"source_type": "regularization", "source_id": year_id})
        await db.fiscal_years.update_one(
            {"id": year_id}, {"$unset": {"regularized_at": "", "regularization_summary": ""}}
        )
        return {"message": "Regularisation annulee"}

    @router.get("/expenses")
    async def list_expenses(request: Request,
                            copropriete_id: Optional[str] = None,
                            fiscal_year_id: Optional[str] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None,
                            account_number: Optional[str] = None,
                            distribution_key_id: Optional[str] = None,
                            expense_category_id: Optional[str] = None,
                            bank_account: Optional[str] = None):
        """Liste les depenses (factures expandees + ecritures FI/OD classe 6).
        iter90f : refactore -> utilise compute_expense_rows() comme single
        source of truth (memes rows que le PDF "Liste des depenses").
        Chinese walls STRICT : copropriete_id requis (param ou header)."""
        from expense_rows import compute_expense_rows
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or None
        if fiscal_year_id and not (date_from and date_to):
            fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if fy:
                date_from, date_to = fy["start_date"], fy["end_date"]
                copropriete_id = copropriete_id or fy.get("copropriete_id")
        if not copropriete_id or copropriete_id == "all":
            raise HTTPException(400, "copropriete_id requis - chinese walls strict.")

        rows, totals = await compute_expense_rows(
            db, copropriete_id,
            date_from=date_from, date_to=date_to,
            account_number=account_number,
            distribution_key_id=distribution_key_id,
            expense_category_id=expense_category_id,
            bank_account=bank_account,
        )

        # iter90f : ajoute totals.by_nature (N2) = somme par nature de depense.
        # Invariant : sum(by_nature) == sum(by_account) == sum(by_key) == total.
        totals["by_nature"] = {}
        for r in rows:
            nature_label = r.get("expense_category_name") or "Sans nature"
            totals["by_nature"][nature_label] = round(
                totals["by_nature"].get(nature_label, 0) + r["total_amount"], 2
            )

        # List available filter values (memes que l'ancienne version)
        filters = {
            "accounts": sorted({r["account_number"] for r in rows if r["account_number"]}),
            "keys": sorted({r["distribution_key_name"] for r in rows
                            if r["distribution_key_name"] and r["distribution_key_name"] != "—" and r["distribution_key_name"] != "Sans cle"}),
            "banks": sorted({r["paid_info"]["bank_account"] for r in rows
                             if r.get("paid_info") and r["paid_info"]["bank_account"]}),
        }
        return {"expenses": rows, "totals": totals, "filters": filters}

    # iter90h : endpoint debug pour identifier les anomalies de coherence
    # qui causent des ecarts entre la UI et un export comptable externe (Optipro).
    @router.get("/expenses-diff")
    async def expenses_diff(request: Request,
                            copropriete_id: str,
                            date_from: str,
                            date_to: str):
        """Liste les anomalies de coherence sur la periode :
        - Factures `is_private_fee=true` avec compte non-643 (compte tampon 44xxx)
        - OD-PRIV avec source_id pointant vers une facture inexistante
        - Factures sans OD de refacturation (privatives non comptabilisees)
        - Comptes PCMN classe 4 marques class_num=6 (mal classes)
        - Ecritures journal classe 6 avec total debit != credit
        """
        if not copropriete_id:
            copropriete_id = request.headers.get("X-Copropriete-Id") or ""
        if not copropriete_id:
            raise HTTPException(400, "copropriete_id requis")
        anomalies = {"copropriete_id": copropriete_id, "period": [date_from, date_to]}

        # 1) Factures privatives sur compte non-643
        privatives_wrong_account = await db.invoices.find({
            "copropriete_id": copropriete_id, "is_private_fee": True,
            "date": {"$gte": date_from, "$lte": date_to},
            "account_number": {"$nin": ["643", "643000"]},
        }, {"_id": 0, "id": 1, "number": 1, "date": 1, "supplier": 1,
            "account_number": 1, "total_amount": 1}).to_list(1000)
        anomalies["private_fees_on_wrong_account"] = {
            "count": len(privatives_wrong_account),
            "total_amount": round(sum(float(p.get("total_amount", 0) or 0) for p in privatives_wrong_account), 2),
            "items": privatives_wrong_account[:30],
        }

        # 2) Comptes PCMN classe 4 marques class_num=6 (mal classes)
        misclass = await db.pcmn_accounts.find({
            "copropriete_id": copropriete_id,
            "class_num": {"$in": [6, 7]},
            "number": {"$regex": "^4"},
        }, {"_id": 0, "number": 1, "name": 1, "class_num": 1}).to_list(100)
        anomalies["misclassified_class4_as_6"] = {
            "count": len(misclass), "items": misclass,
        }

        # 3) OD-PRIV avec source_id orphelin (facture inexistante)
        od_priv = await db.journal_entries.find({
            "copropriete_id": copropriete_id,
            "journal_type": "OD",
            "source_type": "invoice",
            "date": {"$gte": date_from, "$lte": date_to},
        }, {"_id": 0, "id": 1, "reference": 1, "source_id": 1, "date": 1}).to_list(5000)
        orphan_ods = []
        for od in od_priv:
            sid = od.get("source_id")
            if sid:
                exists = await db.invoices.count_documents({"id": sid})
                if exists == 0:
                    orphan_ods.append(od)
        anomalies["orphan_od_entries"] = {"count": len(orphan_ods), "items": orphan_ods[:30]}

        # 4) Factures privatives sans OD de refacturation
        privatives = await db.invoices.find({
            "copropriete_id": copropriete_id, "is_private_fee": True,
            "date": {"$gte": date_from, "$lte": date_to},
        }, {"_id": 0, "id": 1, "number": 1, "supplier": 1, "total_amount": 1}).to_list(5000)
        missing_od = []
        for p in privatives:
            has = await db.journal_entries.count_documents({
                "source_id": p["id"], "journal_type": "OD",
            })
            if has == 0:
                missing_od.append(p)
        anomalies["private_fees_without_od"] = {"count": len(missing_od), "items": missing_od[:30]}

        return anomalies

    # ---- BUDGETS ----
    @router.get("/budgets")
    async def list_budgets(request: Request, fiscal_year_id: Optional[str] = None, copropriete_id: Optional[str] = None):
        from syndic_scope import syndic_query
        q = {**syndic_query(request)}
        if fiscal_year_id:
            q["fiscal_year_id"] = fiscal_year_id
        if copropriete_id:
            q["copropriete_id"] = copropriete_id
        budgets = await db.budgets.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)
        # iter90fu : backfill total<->total_amount pour les budgets historiques
        # importes via wizard qui n'avaient pas `total` (le UI FiscalYearPage
        # affichait EUR vide jusqu'a Modifier+Sauvegarder). On repare a la
        # lecture ET on persiste dans la foulee pour eviter de faire ce travail
        # a chaque requete.
        for b in budgets:
            has_total = b.get("total") is not None
            has_total_amount = b.get("total_amount") is not None
            if has_total and has_total_amount:
                continue
            recomputed = round(
                sum(float(ln.get("amount") or 0) for ln in (b.get("lines") or [])), 2
            )
            canonical = (
                b.get("total") if has_total else (b.get("total_amount") if has_total_amount else recomputed)
            )
            b["total"] = canonical
            b["total_amount"] = canonical
            try:
                await db.budgets.update_one(
                    {"id": b["id"]},
                    {"$set": {"total": canonical, "total_amount": canonical}},
                )
            except Exception:
                pass
        return budgets

    @router.post("/budgets")
    async def create_budget(data: BudgetInput, request: Request):
        total = sum(l.amount for l in data.lines)
        doc = {
            "id": str(uuid.uuid4()),
            "fiscal_year_id": data.fiscal_year_id,
            "name": data.name or "Budget",
            "lines": [l.model_dump() for l in data.lines],
            # Persist both field names for consistency with the import wizard
            "total": round(total, 2),
            "total_amount": round(total, 2),
            "status": "draft",
            "approved_at": None,
            "approved_by": None,
            "copropriete_id": data.copropriete_id or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            # iter90cj : engagement AG reserve/roulement persiste des la creation
            "reserve_fund_amount": round(float(data.reserve_fund_amount or 0), 2),
            "reserve_fund_key_id": data.reserve_fund_key_id or "",
            "roulement_fund_amount": round(float(data.roulement_fund_amount or 0), 2),
            "roulement_fund_key_id": data.roulement_fund_key_id or "",
        }
        from syndic_scope import inject_syndic
        inject_syndic(doc, request)
        await db.budgets.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    @router.get("/budgets/{budget_id}")
    async def get_budget(budget_id: str):
        b = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not b:
            raise HTTPException(404, "Budget non trouve")
        return b

    @router.get("/budgets/{budget_id}/pdf")
    async def download_budget_pdf(budget_id: str):
        """iter90cj-ter : export PDF du budget previsionnel.

        Contient : entete ACP + FY, postes budgetaires (compte/nature/cle/montant),
        total, et section "Engagement AG - Fonds permanents" (reserve + roulement).
        """
        import io
        from fastapi.responses import StreamingResponse
        from pdf_budget import build_budget_pdf
        from pdf_layout import resolve_syndic_pdf_context

        budget = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not budget:
            raise HTTPException(404, "Budget non trouve")
        copro_id = budget.get("copropriete_id", "")
        copropriete = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0}) or {}
        fiscal_year = await db.fiscal_years.find_one(
            {"id": budget.get("fiscal_year_id", "")}, {"_id": 0},
        ) or {}
        pcmn = await db.pcmn_accounts.find(
            {"copropriete_id": copro_id}, {"_id": 0},
        ).to_list(10000)
        pcmn_map = {a.get("number", ""): a.get("name", "") for a in pcmn}
        keys = await db.distribution_keys.find(
            {"copropriete_id": copro_id}, {"_id": 0},
        ).to_list(1000)
        keys_map = {k.get("id", ""): k.get("name", "") for k in keys}

        # iter90dj : logo cabinet + mentions legales
        syndic_pdf_ctx = await resolve_syndic_pdf_context(db, copropriete)
        pdf_bytes = build_budget_pdf(
            copropriete=copropriete, budget=budget, fiscal_year=fiscal_year,
            pcmn_map=pcmn_map, keys_map=keys_map,
            syndic_pdf_ctx=syndic_pdf_ctx,
        )
        safe_name = (budget.get("name", "budget") or "budget").replace(" ", "_")[:40]
        fy_name = (fiscal_year.get("name", "") or "").replace(" ", "_")[:20]
        filename = f"budget_{safe_name}_{fy_name}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.put("/budgets/{budget_id}")
    async def update_budget(budget_id: str, data: BudgetInput):
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        if existing.get("status") == "approved":
            raise HTTPException(400, "Budget approuve - modification interdite. Revoquez d'abord l'approbation.")
        total = sum(l.amount for l in data.lines)
        update = {
            "fiscal_year_id": data.fiscal_year_id,
            "name": data.name or "Budget",
            "lines": [l.model_dump() for l in data.lines],
            # Persist both field names for consistency with the import wizard
            # which writes `total_amount`. Older code may still read `total`.
            "total": round(total, 2),
            "total_amount": round(total, 2),
            # iter90cj : engagement AG reserve/roulement mis a jour au vote
            "reserve_fund_amount": round(float(data.reserve_fund_amount or 0), 2),
            "reserve_fund_key_id": data.reserve_fund_key_id or "",
            "roulement_fund_amount": round(float(data.roulement_fund_amount or 0), 2),
            "roulement_fund_key_id": data.roulement_fund_key_id or "",
        }
        await db.budgets.update_one({"id": budget_id}, {"$set": update})
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    @router.delete("/budgets/{budget_id}")
    async def delete_budget(budget_id: str, force: Optional[bool] = False):
        """iter90af : Suppression cascade budget -> fund_calls -> journal_entries.

        Regle metier :
        - DELETE budget doit supprimer aussi les appels lies (fund_calls.budget_id)
          et leurs ecritures auto-generees (journal_entries source_type=fund_call).
        - Sinon les balances de tiers restent faussees.
        - Si des appels ont deja recu des paiements, refuse sauf force=true.
        - force=true : delettre les bank_transactions matchees puis supprime.
        """
        from auto_entries import _delete_auto_entries
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")

        linked_calls = await db.fund_calls.find(
            {"budget_id": budget_id}, {"_id": 0}
        ).to_list(10000)
        paid_calls = [
            c for c in linked_calls
            if any(d.get("paid") for d in c.get("distribution", []))
        ]
        if paid_calls and not force:
            paid_names = [c.get("name", "?") for c in paid_calls]
            suffix = "..." if len(paid_names) > 3 else ""
            raise HTTPException(
                400,
                f"Impossible de supprimer : {len(paid_calls)} appel(s) ont deja "
                f"recu des paiements ({', '.join(paid_names[:3])}{suffix}). "
                "Utilisez `force=true` pour forcer la suppression."
            )

        unlettre_count = 0
        if force and paid_calls:
            for c in paid_calls:
                await db.bank_transactions.update_many(
                    {"matched_fund_call_id": c["id"]},
                    {"$set": {"matched": False, "matched_fund_call_id": None}}
                )
                unlettre_count += 1

        for c in linked_calls:
            try:
                await _delete_auto_entries(db, "fund_call", c["id"])
            except Exception as e:
                print(f"[budget-delete] delete auto entries failed for fund_call {c['id']}: {e}")
            # iter90ch : contre-passe aussi les OD MUT-P retroactives liees a l'appel.
            try:
                from routes.fund_calls import reverse_post_mutation_ods_for_call
                await reverse_post_mutation_ods_for_call(
                    db, c["id"], reason=f"Suppression budget {budget_id}"
                )
            except Exception as e:
                print(f"[budget-delete] MUT-P reversal failed for fund_call {c['id']}: {e}")

        deleted_calls = 0
        if linked_calls:
            res = await db.fund_calls.delete_many({"budget_id": budget_id})
            deleted_calls = res.deleted_count

        await db.budgets.delete_one({"id": budget_id})
        return {
            "message": f"Budget supprime avec {deleted_calls} appel(s) de fonds et leurs ecritures.",
            "deleted_fund_calls": deleted_calls,
            "unlettred_transactions": unlettre_count,
        }

    @router.post("/budgets/{budget_id}/approve")
    async def approve_budget(request: Request, budget_id: str):
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")
        if existing.get("status") == "approved":
            raise HTTPException(400, "Budget deja approuve")
        if not existing.get("lines"):
            raise HTTPException(400, "Budget vide - ajoutez des lignes avant d'approuver")
        user_email = getattr(request.state, "user_email", "")
        await db.budgets.update_one(
            {"id": budget_id},
            {"$set": {
                "status": "approved",
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": user_email,
            }}
        )
        return await db.budgets.find_one({"id": budget_id}, {"_id": 0})

    @router.post("/budgets/{budget_id}/revoke")
    async def revoke_budget(budget_id: str, force: Optional[bool] = False):
        """Devalider un budget approuve.
        Contrepasse (supprime) TOUS les appels de fonds lies a ce budget,
        quel que soit leur type (provisions, reserve, roulement, special) ainsi
        que les ecritures comptables auto-generees (VE) correspondantes.

        Protection historique :
        - Si au moins UN appel a recu un paiement et que `force` n'est pas True : 400.
        - Si `force=True` : supprime quand meme. Les paiements bancaires lies
          deviennent "non lettres" mais ne sont pas supprimes (audit trail).
        """
        from auto_entries import _delete_auto_entries
        existing = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Budget non trouve")

        # Recupere tous les appels lies a ce budget
        linked_calls = await db.fund_calls.find(
            {"budget_id": budget_id}, {"_id": 0}
        ).to_list(10000)
        paid_calls = [
            c for c in linked_calls
            if any(d.get("paid") for d in c.get("distribution", []))
        ]
        if paid_calls and not force:
            paid_names = [c.get("name", "?") for c in paid_calls]
            raise HTTPException(
                400,
                "Impossible de devalider : "
                f"{len(paid_calls)} appel(s) de fonds ont deja recu des paiements "
                f"({', '.join(paid_names[:3])}{'...' if len(paid_names) > 3 else ''}). "
                "Annulez d'abord les paiements ou utilisez `force=true` pour forcer "
                "la contrepassation (les paiements bancaires seront delettres)."
            )

        # Si force=true et appels payes, delettre d'abord les transactions bancaires
        unlettre_count = 0
        if force and paid_calls:
            for c in paid_calls:
                # Trouver les bank_transactions matchees a cet appel
                # (via paid_by_fund_call ou via communication VCS - on se contente
                # de marquer comme non matched et reset matched_owner/matched_invoice)
                await db.bank_transactions.update_many(
                    {"matched_fund_call_id": c["id"]},
                    {"$set": {"matched": False, "matched_fund_call_id": None}}
                )
                unlettre_count += 1

        # Contrepassation : supprime les ecritures VE auto-generees + les appels
        for c in linked_calls:
            try:
                await _delete_auto_entries(db, "fund_call", c["id"])
            except Exception as e:
                print(f"[budget-revoke] delete auto entries failed for fund_call {c['id']}: {e}")
            # iter90ch : contre-passe aussi les OD MUT-P retroactives liees a l'appel.
            try:
                from routes.fund_calls import reverse_post_mutation_ods_for_call
                await reverse_post_mutation_ods_for_call(
                    db, c["id"], reason=f"Devalidation budget {budget_id}"
                )
            except Exception as e:
                print(f"[budget-revoke] MUT-P reversal failed for fund_call {c['id']}: {e}")
        deleted_calls = 0
        if linked_calls:
            res = await db.fund_calls.delete_many({"budget_id": budget_id})
            deleted_calls = res.deleted_count

        # Repasse le budget en draft
        await db.budgets.update_one(
            {"id": budget_id},
            {"$set": {"status": "draft", "approved_at": None, "approved_by": None}}
        )

        updated = await db.budgets.find_one({"id": budget_id}, {"_id": 0})
        return {
            **updated,
            "deleted_fund_calls": deleted_calls,
            "preserved_paid_calls": 0 if force else len(paid_calls),
            "unlettred_transactions": unlettre_count,
            "message": (
                f"Budget devalide. {deleted_calls} appel(s) de fonds contrepasse(s) "
                "(provisions, reserve, roulement, special) avec leurs ecritures auto."
            ),
        }

    # ---- N-1 PREVIOUS YEAR EXPENSES (helper for budget preparation) ----
    @router.get("/previous-year-expenses")
    async def previous_year_expenses(fiscal_year_id: Optional[str] = None,
                                     copropriete_id: Optional[str] = None):
        """Aggregate previous year actual expenses grouped by (account, distribution_key).
        Combines invoices.distribution_lines AND journal_entries class 6 to give a
        comprehensive view of what was spent and how it was distributed.
        Returns rows the gestionnaire can directly reuse as budget lines for year N.
        """
        # Resolve previous fiscal year
        target_fy = None
        if fiscal_year_id:
            current_fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
            if current_fy:
                copropriete_id = copropriete_id or current_fy.get("copropriete_id", "")
                # Find the FY that ends strictly before current.start_date in this ACP
                prev_q = {
                    "end_date": {"$lt": current_fy["start_date"]},
                }
                if copropriete_id:
                    prev_q["copropriete_id"] = copropriete_id
                target_fy = await db.fiscal_years.find_one(
                    prev_q, {"_id": 0}, sort=[("end_date", -1)]
                )
        if not target_fy:
            # Fallback: pick most recent closed FY in this ACP
            q = {"status": "closed"}
            if copropriete_id:
                q["copropriete_id"] = copropriete_id
            target_fy = await db.fiscal_years.find_one(
                q, {"_id": 0}, sort=[("end_date", -1)]
            )
        if not target_fy:
            return {"fiscal_year": None, "lines": [], "total": 0.0}

        start, end = target_fy["start_date"], target_fy["end_date"]
        copro_id = target_fy.get("copropriete_id", "") or copropriete_id or ""

        # 1) Invoices (distribution_lines) - groupe par (account, dist_key)
        inv_q = {"date": {"$gte": start, "$lte": end}}
        if copro_id:
            inv_q["copropriete_id"] = copro_id
        invoices = await db.invoices.find(inv_q, {"_id": 0}).to_list(100000)

        # 2) Journal entries class 6 (manual OD with expense accounts)
        je_q = {"date": {"$gte": start, "$lte": end}}
        if copro_id:
            je_q["copropriete_id"] = copro_id
        entries = await db.journal_entries.find(je_q, {"_id": 0}).to_list(100000)

        # Aggregate: keyed by (account_number, distribution_key_id)
        agg = {}  # {(acc, key_id): {account_name, key_name, amount, sources: {invoices, manual}}}

        # Pre-fetch distribution keys for naming
        dk_q = {"copropriete_id": copro_id} if copro_id else {}
        keys = await db.distribution_keys.find(dk_q, {"_id": 0}).to_list(1000)
        key_name_map = {k["id"]: k["name"] for k in keys}

        for inv in invoices:
            acc = inv.get("account_number", "") or ""
            key_id = inv.get("distribution_key_id", "") or ""
            if not acc or not acc.startswith("6"):
                # Try inferring from journal entry tied to invoice? Skip for now.
                continue
            k = (acc, key_id)
            if k not in agg:
                agg[k] = {
                    "account_number": acc,
                    "account_name": "",
                    "distribution_key_id": key_id,
                    "distribution_key_name": key_name_map.get(key_id, ""),
                    "amount_invoices": 0.0,
                    "amount_manual": 0.0,
                    "invoice_count": 0,
                }
            agg[k]["amount_invoices"] += inv.get("total_amount", 0)
            agg[k]["invoice_count"] += 1

        # Add manual journal entries (class 6 only, exclude those tied to invoices)
        invoice_je_ids = set()  # We don't store inv->je link consistently; just include all class-6 OD
        for e in entries:
            # Only count OD/AC entries (manual or invoice posting). For simplicity, count all,
            # but only the amount that goes to class-6 accounts.
            for line in e.get("lines", []):
                acc = line.get("account_number", "")
                if not acc.startswith("6"):
                    continue
                net = line.get("debit", 0) - line.get("credit", 0)
                if abs(net) < 0.01:
                    continue
                # If this entry has a `fund_call_id` skip (those are appels, not depenses)
                if e.get("fund_call_id"):
                    continue
                k = (acc, "")
                if k not in agg:
                    agg[k] = {
                        "account_number": acc,
                        "account_name": line.get("account_name", ""),
                        "distribution_key_id": "",
                        "distribution_key_name": "",
                        "amount_invoices": 0.0,
                        "amount_manual": 0.0,
                        "invoice_count": 0,
                    }
                if not agg[k]["account_name"]:
                    agg[k]["account_name"] = line.get("account_name", "")
                agg[k]["amount_manual"] += net

        # Enrich missing account_name from PCMN
        accs_missing = [v["account_number"] for v in agg.values() if not v["account_name"]]
        if accs_missing:
            pcmn_q = {"number": {"$in": accs_missing}}
            if copro_id:
                pcmn_q["copropriete_id"] = copro_id
            pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(1000)
            pcmn_map = {p["number"]: p["name"] for p in pcmns}
            for v in agg.values():
                if not v["account_name"]:
                    v["account_name"] = pcmn_map.get(v["account_number"], "")

        lines = []
        for v in agg.values():
            total = round(v["amount_invoices"] + v["amount_manual"], 2)
            if abs(total) < 0.01:
                continue
            v["amount_invoices"] = round(v["amount_invoices"], 2)
            v["amount_manual"] = round(v["amount_manual"], 2)
            v["amount_total"] = total
            lines.append(v)
        lines.sort(key=lambda x: (x["account_number"], x.get("distribution_key_name", "")))
        return {
            "fiscal_year": target_fy,
            "lines": lines,
            "total": round(sum(l["amount_total"] for l in lines), 2),
        }

    # ---- BUDGET VS ACTUAL ----
    @router.get("/budget-comparison/{fiscal_year_id}")
    async def budget_vs_actual(fiscal_year_id: str):
        """Compare budget vs actual expenses for a fiscal year.

        iter90i5 : `actual` DOIT correspondre exactement au total de la
        Liste des depenses (compute_expense_rows) - meme source de verite
        que le dashboard total_charges et le PDF Liste des depenses.
        Avant : aggregation naive `debit - credit` sur toutes les
        journal_entries de classe 6 -> incluait les contre-passations
        (reversals) et les JE liees aux factures privatives (double
        comptabilise ensuite reneutralise par OD-PRIV, susceptible de
        casser l'invariant selon l'ordre).
        """
        from expense_rows import compute_expense_rows
        fy = await db.fiscal_years.find_one({"id": fiscal_year_id}, {"_id": 0})
        if not fy:
            raise HTTPException(404, "Exercice non trouve")

        budget = await db.budgets.find_one({"fiscal_year_id": fiscal_year_id}, {"_id": 0})
        budget_lines = {l["account_number"]: l for l in (budget or {}).get("lines", [])}

        # Chinese wall: scope entries by fiscal year's ACP
        copro_id = fy.get("copropriete_id", "")

        # iter90i5 : source unique = compute_expense_rows. On garde le nom
        # de compte via journal_entries pour les cas ou le pcmn_name n'est
        # pas embarque dans les rows.
        rows, _totals = await compute_expense_rows(
            db, copro_id,
            date_from=fy["start_date"], date_to=fy["end_date"],
        )
        actuals: dict = {}
        account_names: dict = {}
        for r in rows:
            acc = (r.get("account_number") or "").strip()
            if not acc:
                continue
            actuals[acc] = round(actuals.get(acc, 0.0) + float(r.get("total_amount", 0) or 0), 2)
            if acc not in account_names and r.get("account_name"):
                account_names[acc] = r["account_name"]

        # Enrichit les noms de compte via pcmn_accounts si manquants
        missing_names = [a for a in actuals if a not in account_names]
        if missing_names:
            pcmn_docs = await db.pcmn_accounts.find(
                {"copropriete_id": copro_id, "number": {"$in": missing_names}},
                {"_id": 0, "number": 1, "name": 1},
            ).to_list(500)
            for p in pcmn_docs:
                account_names[p["number"]] = p.get("name", "")

        comparison = []
        all_accounts = sorted(set(list(budget_lines.keys()) + list(actuals.keys())))
        for acc in all_accounts:
            if not acc.startswith("6"):
                continue
            bl = budget_lines.get(acc, {})
            actual = round(actuals.get(acc, 0.0), 2)
            budgeted = bl.get("amount", 0)
            comparison.append({
                "account_number": acc,
                "account_name": bl.get("account_name", "") or account_names.get(acc, ""),
                "budgeted": round(budgeted, 2),
                "actual": actual,
                "difference": round(budgeted - actual, 2),
            })

        return {
            "fiscal_year": fy,
            "comparison": comparison,
            "total_budgeted": round(sum(c["budgeted"] for c in comparison), 2),
            "total_actual": round(sum(c["actual"] for c in comparison), 2),
        }

    return router
