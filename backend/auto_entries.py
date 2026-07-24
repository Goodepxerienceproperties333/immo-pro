"""Auto journal entries generator.

Creates Belgian-style accounting entries automatically when:
- An invoice is created (Achats journal AC)
- A fund call is created (Ventes journal VE)
- A bank transaction is matched (Financier journal FI)

All entries carry `source_type` + `source_id` for traceability and
auto-cleanup when the source document is deleted.
"""
from datetime import datetime, timezone
import uuid
from tier_accounts import (
    assign_owner_accounts,
    assign_supplier_account,
    get_owner_accounts,
    get_supplier_account,
)


async def _delete_auto_entries(db, source_type: str, source_id: str, reason: str = ""):
    """iter90bx : NE SUPPRIME PLUS - genere une contre-passation pour chaque
    ecriture auto-generee non-editee. Preserve `manually_edited` et les
    entrees deja `reversed`/`is_reversal`.

    Nom historique conserve pour compatibilite avec les 15+ call sites.
    Signature etendue : parametre `reason` optionnel pour la trace audit.
    """
    from journal_reversals import reverse_auto_entries
    return await reverse_auto_entries(db, source_type, source_id, reason=reason)


async def _hard_delete_auto_entries(db, source_type: str, source_id: str):
    """Suppression REELLE des ecritures auto-generees pour eviter les doublons.

    Cherche par DEUX schemas de marquage :
      1) auto_entries.py : source_type + source_id + auto_generated
      2) import_wizard :   source_invoice_id (legacy, sans auto_generated)
    Cela garantit qu'une facture n'a JAMAIS plus d'une ecriture AC,
    qu'elle ait ete creee par l'import ou par auto_entries.
    """
    q = {"$or": [
        {"source_type": source_type, "source_id": source_id, "auto_generated": True},
        {"source_invoice_id": source_id},
    ]}
    result = await db.journal_entries.delete_many(q)
    return result.deleted_count



def _balanced(lines: list) -> bool:
    return abs(sum(ln.get("debit", 0) for ln in lines)
               - sum(ln.get("credit", 0) for ln in lines)) < 0.01


async def _resolve_or_create_supplier_account(db, supplier_name: str, copro_id: str) -> tuple[str, dict | None]:
    """iter90by : extrait de generate_purchase_entry pour reduire la complexite.

    Retourne (supplier_acc, supplier_doc). Regle Chinese walls STRICT :
    - Match par nom NORMALISE (iter90ef : particules juridiques filtrees +
      mots tries + coquilles Levenshtein >= 0.90) via find_duplicate_supplier
      + find_similar_suppliers. Elimine les doublons "Finlead" vs "Finlead SRL"
      vs "SRL Finlead" vs "finleed" (coquille).
    - Si aucun fournisseur en base, cree un fiche fournisseur auto (auto_created)
      pour eviter tout fallback vers le compte maitre 440000 qui fuiterait les
      donnees entre ACPs.
    - Si supplier_name est vide, cree un compte ACP-scoped "Fournisseur divers"
      44000XXX. JAMAIS 440000.
    """
    supplier_acc = ""
    supplier_doc = None
    if supplier_name:
        import uuid as _uuid
        from datetime import datetime, timezone
        from routes.suppliers import (
            find_duplicate_supplier,
            find_similar_suppliers,
        )
        # iter90ef : recherche via normalisation partagee (particules
        # juridiques filtrees, mots tries, insensible casse/ponctuation).
        # find_duplicate_supplier scope ACP par defaut.
        dup = await find_duplicate_supplier(
            db, name=supplier_name, copro_id=copro_id,
        )
        if dup and dup.get("supplier"):
            supplier_doc = dup["supplier"]
        else:
            # Fallback : recherche par coquille (Levenshtein) sur le meme
            # scope ACP. Reutilise le meilleur match >= 0.90 pour eviter
            # les doublons du type "Finleed" vs "Finlead".
            similars = await find_similar_suppliers(
                db, name=supplier_name, copro_id=copro_id,
                threshold=0.90, limit=1,
            )
            if similars:
                supplier_doc = similars[0]["supplier"]
        if not supplier_doc:
            supplier_doc = {
                "id": str(_uuid.uuid4()),
                "name": supplier_name,
                "copropriete_id": copro_id,
                "tier_accounts": {},
                "auto_created": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.suppliers.insert_one(dict(supplier_doc))
        supplier_doc = await assign_supplier_account(db, supplier_doc, copro_id)
        supplier_acc = get_supplier_account(supplier_doc, copro_id)
    if not supplier_acc:
        from tier_accounts import _next_seq, _format_seq, _ensure_account
        seq = await _next_seq(db, copro_id, "44000")
        supplier_acc = _format_seq("44000", seq, width=3)
        await _ensure_account(db, copro_id, supplier_acc, "Fournisseur divers", 4)
    return supplier_acc, supplier_doc


async def _resolve_bank_account(db, txn: dict, copro_id: str) -> tuple[str, str]:
    """Resout le compte PCMN bancaire a partir de la transaction.

    Ordre de resolution :
      1) IBAN exact (txn ou statement) -> pcmn officiel de l'ACP
      2) account_number direct == pcmn_number configure (extraits sans IBAN,
         ex. account_number = 551618 matche pcmn_number = 551618)
      3) Suffixe IBAN ou correspondance croisee pcmn_number
      4) Compte PCMN bancaire existant (classe 55) dans le plan comptable
      5) Compte par defaut de l'ACP
      6) "" (l'appelant DOIT gerer ce cas : skip ou raise)

    Verrou iter90jj : ne cree JAMAIS de compte fantome.
    """
    from iban_utils import normalize_iban

    # ---- Fetch raw_acc + IBAN en une seule passe (evite double query) ----
    raw_acc = (txn.get("account_number") or "").strip()
    iban = normalize_iban(raw_acc) if raw_acc else ""
    if (not iban or not raw_acc) and txn.get("statement_id"):
        stmt = await db.bank_statements.find_one(
            {"id": txn["statement_id"]},
            {"_id": 0, "account_number": 1, "iban": 1},
        )
        if stmt:
            stmt_acc = (stmt.get("account_number") or stmt.get("iban") or "").strip()
            if not raw_acc:
                raw_acc = stmt_acc
            if not iban:
                iban = normalize_iban(stmt_acc)

    # ---- Fetch ACP bank_accounts config ----
    copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "bank_accounts": 1})
    accounts = (copro or {}).get("bank_accounts") or []

    from pcmn_utils import normalize_bank_pcmn, pcmn_bank_match

    # 1) Match IBAN exact -> pcmn officiel (normalise a 8 chiffres)
    if iban:
        for ba in accounts:
            ba_iban = normalize_iban(ba.get("iban"))
            if ba_iban and ba_iban == iban and ba.get("pcmn_number"):
                return normalize_bank_pcmn(ba["pcmn_number"]), (ba.get("label") or "Banque")

    # 2) Match direct : account_number == pcmn_number configure
    #    Couvre les extraits sans IBAN ou le account_number EST le pcmn (ex. 551618).
    #    Normalisation 8 chiffres (551618 == 55161800).
    if raw_acc:
        for ba in accounts:
            ba_pcmn = (ba.get("pcmn_number") or "").strip()
            if ba_pcmn and pcmn_bank_match(ba_pcmn, raw_acc):
                return normalize_bank_pcmn(ba_pcmn), (ba.get("label") or "Banque")

    # 3) Suffixe IBAN / correspondance croisee pcmn_number
    if raw_acc:
        digits = "".join(c for c in raw_acc if c.isdigit())
        if digits and len(digits) >= 4:
            for ba in accounts:
                ba_iban_raw = (ba.get("iban") or "").replace(" ", "")
                ba_pcmn = (ba.get("pcmn_number") or "").strip()
                # suffixe de l'IBAN
                if ba_iban_raw and ba_iban_raw.endswith(digits) and ba_pcmn:
                    return normalize_bank_pcmn(ba_pcmn), (ba.get("label") or "Banque")
                # correspondance croisee pcmn (suffixe ou prefixe)
                norm_pcmn = normalize_bank_pcmn(ba_pcmn) if ba_pcmn else ""
                if norm_pcmn and not pcmn_bank_match(norm_pcmn, raw_acc) and (
                    norm_pcmn.endswith(digits) or digits.endswith(norm_pcmn)
                ):
                    return norm_pcmn, (ba.get("label") or "Banque")

    # 4) Compte PCMN bancaire existant (classe 55) dans le plan comptable
    #    Essaie d'abord tel quel, puis normalise a 8 chiffres
    if raw_acc:
        digits = "".join(c for c in raw_acc if c.isdigit())
        if digits and digits.startswith("55"):
            norm_digits = normalize_bank_pcmn(digits)
            # Cherche d'abord le numero normalise, puis l'original
            for try_num in dict.fromkeys([norm_digits, digits]):
                pcmn = await db.pcmn_accounts.find_one(
                    {"copropriete_id": copro_id, "number": try_num},
                    {"_id": 0, "number": 1, "name": 1},
                )
                if pcmn:
                    return pcmn["number"], (pcmn.get("name") or "Banque")

    # 5) Fallback : compte par defaut de l'ACP (is_default=True) ou 1er
    if accounts:
        default_ba = next((b for b in accounts if b.get("is_default")), None) or accounts[0]
        if default_ba.get("pcmn_number"):
            return normalize_bank_pcmn(default_ba["pcmn_number"]), (default_ba.get("label") or "Banque")

    # 6) Ultime fallback : "" - l'appelant DOIT gerer ce cas (skip ou raise)
    return "", "Banque"


async def _resolve_bank_counterpart(db, txn: dict, copro_id: str) -> tuple[str, str, str | None, str]:
    """iter90by : extrait de generate_bank_entry. Resout la contrepartie selon
    `match_type` de la transaction.
    Retourne (counterpart_acc, counterpart_name, third_party_id, invoice_number).
    """
    match_type = txn.get("match_type", "")
    counterpart_acc = ""
    counterpart_name = ""
    third_party_id = None
    invoice_number = ""

    if match_type == "owner_payment":
        owner = await db.owners.find_one({"id": txn.get("matched_to")}, {"_id": 0})
        if owner:
            owner = await assign_owner_accounts(db, owner, copro_id)
            counterpart_acc = get_owner_accounts(owner, copro_id).get("provisions", "")
            counterpart_name = owner.get("name", "")
            third_party_id = owner["id"]
    elif match_type == "invoice":
        inv = await db.invoices.find_one({"id": txn.get("matched_to")}, {"_id": 0})
        if inv:
            invoice_number = (inv.get("number") or "").strip()
            sname = (inv.get("supplier") or "").strip()
            supplier = None
            # iter90jd - VERROU : priorite au supplier_id de la facture (source of truth)
            inv_sup_id = (inv.get("supplier_id") or "").strip()
            if inv_sup_id:
                supplier = await db.suppliers.find_one({"id": inv_sup_id}, {"_id": 0})
            # Sinon : fallback par nom NORMALISE + Chinese Wall strict (copropriete_id)
            if not supplier and sname:
                # Match par name candidates (particules juridiques filtrees) dans l'ACP
                from routes.suppliers import _norm_name_candidates
                inv_cands = set(_norm_name_candidates(sname))
                if inv_cands:
                    async for s in db.suppliers.find(
                        {"copropriete_id": copro_id}, {"_id": 0},
                    ):
                        s_cands = set(_norm_name_candidates(s.get("name", "")))
                        if inv_cands & s_cands:
                            supplier = s
                            break
            if supplier:
                # iter90jd : garanti que le supplier a un tier_account_number
                # (auto-assign si absent - evite le fallback 499 sur fiches "nues")
                supplier = await assign_supplier_account(db, supplier, copro_id)
                counterpart_acc = get_supplier_account(supplier, copro_id)
                counterpart_name = supplier.get("name", "") or sname
                third_party_id = supplier["id"]
            else:
                counterpart_name = sname
    elif match_type == "supplier_payment":
        supplier = await db.suppliers.find_one({"id": txn.get("matched_to")}, {"_id": 0})
        if supplier:
            supplier = await assign_supplier_account(db, supplier, copro_id)
            counterpart_acc = get_supplier_account(supplier, copro_id)
            counterpart_name = supplier.get("name", "")
            third_party_id = supplier["id"]
    return counterpart_acc, counterpart_name, third_party_id, invoice_number


async def generate_purchase_entry(db, invoice: dict) -> dict | None:
    """AC: Dr 6xxxxx (expense) + Cr 44000XXX (supplier).

    iter90i6 : pour les notes de credit (amount < 0), l'ecriture est
    INVERSEE :
      - Dr 44000XXX (supplier) : reduit la dette au fournisseur.
      - Cr 6xxxxx (expense)     : reduit la charge (contrepassation).
    Cela permet la comptabilisation correcte des notes de credit dans le
    journal des achats ET dans la situation de compte fournisseur.
    Skips uniquement si amount == 0 ou pas de fournisseur.
    """
    copro_id = invoice.get("copropriete_id", "")
    if not copro_id:
        return None
    amount_signed = float(invoice.get("total_amount", 0) or 0)
    if abs(amount_signed) < 0.01:
        return None
    # iter90i6 : deux cas -> facture (positif) ou note de credit (negatif)
    is_credit_note = amount_signed < 0
    amount = abs(amount_signed)  # les colonnes debit/credit sont TOUJOURS positives
    expense_acc = invoice.get("account_number", "") or "600000"
    supplier_name = (invoice.get("supplier") or "").strip()
    # iter90by : resolution du compte fournisseur extraite en helper
    supplier_acc, supplier_doc = await _resolve_or_create_supplier_account(
        db, supplier_name, copro_id,
    )

    # Pre-fetch PCMN names
    pcmn_q = {"number": {"$in": [expense_acc, supplier_acc]}, "copropriete_id": copro_id}
    pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(10)
    pcmn_names = {p["number"]: p["name"] for p in pcmns}

    await _hard_delete_auto_entries(db, "invoice", invoice["id"])

    # ---- FRAIS PRIVATIF : 2 ecritures separees ----
    # Ecriture 1 (AC - Achats) : Facture fournisseur
    #   Dr 643 Frais privatif  | Cr 44000XXX Fournisseur
    # Ecriture 2 (OD - Operations Diverses) : Refacturation au(x) proprietaire(s)
    #   Dr 4100XXX Proprietaire | Cr 643 Frais privatif (imputation)
    # iter85e : si private_fee_allocations contient N owners, l'OD a N debits
    # owner + N credits 643 (1 par owner) -> chaque copro voit sa quote-part.
    # Net 643 = 0, fournisseur credite, proprietaires debites.
    is_private = invoice.get("is_private_fee")
    allocations = list(invoice.get("private_fee_allocations") or [])
    # Retro-compat : si allocations vide mais private_fee_owner_id existe,
    # on construit une allocation single-owner avec le total.
    if is_private and not allocations and invoice.get("private_fee_owner_id"):
        allocations = [{"owner_id": invoice["private_fee_owner_id"], "amount": amount}]

    if is_private and allocations:
        # Pre-resoud chaque owner + son compte de provisions 4100XXX
        owner_rows = []  # [{owner_id, owner_name, owner_prov, amount}]
        for a in allocations:
            oid = a.get("owner_id", "")
            a_amount = float(a.get("amount") or 0)
            if not oid or a_amount <= 0:
                continue
            owner_doc = await db.owners.find_one({"id": oid}, {"_id": 0})
            if not owner_doc:
                continue
            owner_doc = await assign_owner_accounts(db, owner_doc, copro_id)
            owner_accs = get_owner_accounts(owner_doc, copro_id)
            owner_prov = owner_accs.get("provisions", "") or "400000"
            owner_rows.append({
                "owner_id": oid,
                "owner_name": owner_doc.get("name", ""),
                "owner_prov": owner_prov,
                "amount": round(a_amount, 2),
            })
        if owner_rows:
            # Re-fetch PCMN names including 643 + tous les owner_prov + supplier
            all_accs = ["643", supplier_acc] + list({r["owner_prov"] for r in owner_rows})
            pcmn_q2 = {"number": {"$in": all_accs}, "copropriete_id": copro_id}
            pcmns2 = await db.pcmn_accounts.find(pcmn_q2, {"_id": 0}).to_list(50)
            pcmn_names2 = {p["number"]: p["name"] for p in pcmns2}

            owner_names_str = ", ".join(r["owner_name"] for r in owner_rows)

            # --- Ecriture 1 : AC (Achats) : Dr 643 / Cr fournisseur ---
            ac_lines = [
                {"account_number": "643",
                 "account_name": pcmn_names2.get("643", "Frais privatifs"),
                 "debit": amount, "credit": 0.0,
                 "third_party_id": None, "third_party_name": ""},
                {"account_number": supplier_acc,
                 "account_name": pcmn_names2.get(supplier_acc, supplier_name),
                 "debit": 0.0, "credit": amount,
                 "third_party_id": (supplier_doc or {}).get("id"),
                 "third_party_name": supplier_name},
            ]
            ac_doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "AC",
                "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
                "reference": f"FA-{invoice.get('number','')}",
                "description": f"Frais privatif {owner_names_str} - {invoice.get('supplier','')} - {invoice.get('description','')}".strip(" -"),
                "lines": ac_lines,
                "total_debit": amount,
                "total_credit": amount,
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "invoice",
                "source_id": invoice["id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(ac_doc)

            # --- Ecriture 2 : OD (Operations Diverses) - refacturation N owners ---
            # N x DR owner_prov + N x CR 643. Equilibre par construction.
            od_lines = []
            for r in owner_rows:
                od_lines.append({
                    "account_number": r["owner_prov"],
                    "account_name": pcmn_names2.get(r["owner_prov"], f"Prov. - {r['owner_name']}"),
                    "debit": r["amount"], "credit": 0.0,
                    "third_party_id": r["owner_id"],
                    "third_party_name": r["owner_name"],
                    "line_description": f"Frais privatif - {r['owner_name']}",
                })
                od_lines.append({
                    "account_number": "643",
                    "account_name": pcmn_names2.get("643", "Frais privatifs"),
                    "debit": 0.0, "credit": r["amount"],
                    "third_party_id": None,
                    "third_party_name": f"Imputation - {r['owner_name']}",
                    "line_description": f"Imputation frais privatif - {r['owner_name']}",
                })
            total_od = round(sum(r["amount"] for r in owner_rows), 2)
            od_doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
                "reference": f"OD-PRIV-{invoice.get('number','')}",
                "description": f"Refacturation frais privatif a {owner_names_str} - {invoice.get('supplier','')}".strip(" -"),
                "lines": od_lines,
                "total_debit": total_od,
                "total_credit": total_od,
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "invoice",
                "source_id": invoice["id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(od_doc)
            return {k: v for k, v in ac_doc.items() if k != "_id"}

    # ---- ECRITURE STANDARD ----
    # Mode multi-lignes : 1 ecriture avec N debits (1 par ligne) + 1 credit fournisseur.
    # Mode 1-ligne : 1 debit + 1 credit (legacy).
    # iter90ey : chaque debit porte son propre occupant_pct/proprietaire_pct.
    # Ligne : fallback ligne -> facture -> defaut (0% occ / 100% prop).
    invoice_occ_pct = invoice.get("occupant_pct")
    invoice_prop_pct = invoice.get("proprietaire_pct")
    def _resolve_line_pct(line_occ, line_prop):
        occ = line_occ if line_occ is not None else invoice_occ_pct
        prop = line_prop if line_prop is not None else invoice_prop_pct
        if occ is None and prop is None:
            occ, prop = 0.0, 100.0
        elif occ is None:
            occ = max(0.0, 100.0 - float(prop))
        elif prop is None:
            prop = max(0.0, 100.0 - float(occ))
        return float(occ), float(prop)

    invoice_lines = invoice.get("lines") or []
    if invoice_lines:
        # Pre-fetch des noms PCMN pour toutes les natures
        line_accounts = list({ln.get("account_number", "") for ln in invoice_lines if ln.get("account_number")})
        pcmn_q_multi = {"number": {"$in": line_accounts + [supplier_acc]}, "copropriete_id": copro_id}
        pcmns_multi = await db.pcmn_accounts.find(pcmn_q_multi, {"_id": 0}).to_list(50)
        pcmn_names_multi = {p["number"]: p["name"] for p in pcmns_multi}
        lines = []
        for ln in invoice_lines:
            acc = ln.get("account_number", "")
            # iter90i6 : accepter les lignes de NC (amount < 0). On travaille
            # sur la valeur absolue et on inverse le sens debit/credit selon
            # `is_credit_note`.
            amt = round(abs(float(ln.get("amount", 0) or 0)), 2)
            if amt < 0.01 or not acc:
                continue
            desc = (ln.get("description") or "").strip()
            occ_pct, prop_pct = _resolve_line_pct(
                ln.get("occupant_pct"), ln.get("proprietaire_pct")
            )
            lines.append({
                "account_number": acc,
                "account_name": pcmn_names_multi.get(acc, "") + (f" - {desc}" if desc else ""),
                # iter90i6 : facture = Dr charge / NC = Cr charge (contrepasse)
                "debit": 0.0 if is_credit_note else amt,
                "credit": amt if is_credit_note else 0.0,
                "third_party_id": None, "third_party_name": "",
                "occupant_pct": occ_pct,
                "proprietaire_pct": prop_pct,
                "description": desc,
            })
        lines.append({
            "account_number": supplier_acc,
            "account_name": pcmn_names_multi.get(supplier_acc, supplier_name),
            # iter90i6 : facture = Cr fournisseur / NC = Dr fournisseur
            "debit": amount if is_credit_note else 0.0,
            "credit": 0.0 if is_credit_note else amount,
            "third_party_id": (supplier_doc or {}).get("id"),
            "third_party_name": supplier_name,
        })
    else:
        occ_pct, prop_pct = _resolve_line_pct(None, None)
        lines = [
            {"account_number": expense_acc,
             "account_name": pcmn_names.get(expense_acc, ""),
             # iter90i6 : facture Dr charge / NC Cr charge
             "debit": 0.0 if is_credit_note else amount,
             "credit": amount if is_credit_note else 0.0,
             "third_party_id": None, "third_party_name": "",
             "occupant_pct": occ_pct, "proprietaire_pct": prop_pct},
            {"account_number": supplier_acc,
             "account_name": pcmn_names.get(supplier_acc, supplier_name),
             # iter90i6 : facture Cr fournisseur / NC Dr fournisseur
             "debit": amount if is_credit_note else 0.0,
             "credit": 0.0 if is_credit_note else amount,
             "third_party_id": (supplier_doc or {}).get("id"),
             "third_party_name": supplier_name},
        ]
    if not _balanced(lines):
        return None
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "AC",
        "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
        # iter90i6 : reference "NC-..." si note de credit, "FA-..." sinon
        "reference": ("NC-" if is_credit_note else "FA-") + f"{invoice.get('number','')}",
        "description": (("Note de credit " if is_credit_note else "Facture ") +
                        f"{invoice.get('supplier','')} - {invoice.get('description','')}").strip(" -"),
        "lines": lines,
        "total_debit": amount,
        "total_credit": amount,
        "copropriete_id": copro_id,
        "auto_generated": True,
        "source_type": "invoice",
        "source_id": invoice["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def generate_sale_entry(db, fund_call: dict) -> dict | None:
    """VE: Dr 40000XXX per owner + Cr 700000 (provisions).
    Reserve part: Dr 40010XXX per owner + Cr 160 (Fonds de reserve, classe 1).
    Roulement part: Dr 40000XXX per owner + Cr 100 (Fonds de roulement, classe 1).
    Reserve et Roulement sont des augmentations de PASSIF (classe 1), pas
    des produits (classe 7) - conforme PCMN belge copropriete.

    iter90i7 : garde symetrique - si un JE AP legacy existe deja pour le
    meme fund_call, le supprime AVANT de creer le VE. Cela verrouille le
    fix cote generateur : impossible d'obtenir un doublon AP/VE, meme si
    la route legacy /fund-calls/{id}/generate-entries a ete appelee en
    premier.
    """
    copro_id = fund_call.get("copropriete_id", "")
    if not copro_id:
        return None
    distribution = fund_call.get("distribution") or []
    if not distribution:
        return None

    # iter90i7 : purge des AP legacy en doublon
    fc_id = fund_call.get("id", "")
    if fc_id:
        try:
            purge = await db.journal_entries.delete_many({
                "journal_type": "AP",
                "fund_call_id": fc_id,
                "reversed": {"$ne": True},
                "is_reversal": {"$ne": True},
            })
            if purge.deleted_count:
                import logging
                logging.getLogger(__name__).info(
                    f"[iter90i7] generate_sale_entry: purged "
                    f"{purge.deleted_count} legacy AP for fund_call {fc_id}"
                )
        except Exception:
            pass

    # Fetch owners for tier accounts
    owner_ids = [d.get("owner_id") for d in distribution if d.get("owner_id")]
    owners = await db.owners.find({"id": {"$in": owner_ids}}, {"_id": 0}).to_list(1000)
    owners_map = {}
    for o in owners:
        o = await assign_owner_accounts(db, o, copro_id)
        owners_map[o["id"]] = o

    reserve_total = float(fund_call.get("reserve_amount", 0) or 0)
    roulement_total = float(fund_call.get("roulement_amount", 0) or 0)
    full_total = float(fund_call.get("total_amount", 0) or 0)
    if full_total <= 0:
        return None

    # iter90ah : Appels manuels standalone (call_type='reserve' ou 'roulement'
    # sans reserve_amount/roulement_amount injecte). Redirige la totalite du
    # montant sur la bonne categorie pour que les comptes de credit soient
    # 160 (reserve) ou 100 (roulement), et non 700000 (provisions).
    ct = fund_call.get("call_type", "")
    if ct == "reserve" and reserve_total <= 0:
        reserve_total = full_total
    elif ct == "roulement" and roulement_total <= 0:
        roulement_total = full_total

    # Compute per-owner reserve/roulement share: prorate.
    # Libelles differencies par ligne selon ce qu'elle represente
    _type_labels_call = {
        "provisions": "Appel de provisions",
        "reserve": "Appel fonds de reserve",
        "roulement": "Appel fonds de roulement",
        "special": "Appel special",
    }
    fc_name_for_lines = fund_call.get("name", "")
    call_type_for_default = fund_call.get("call_type", "")
    # Si l'appel global est de type "provisions" mais contient des lignes reserve/roulement
    # injectees (mode legacy), chaque ligne aura son propre libelle.
    label_prov = f"{_type_labels_call.get('provisions')} - {fc_name_for_lines}".rstrip(" -")
    label_res = f"{_type_labels_call.get('reserve')} - {fc_name_for_lines}".rstrip(" -")
    label_roul = f"{_type_labels_call.get('roulement')} - {fc_name_for_lines}".rstrip(" -")
    label_default = f"{_type_labels_call.get(call_type_for_default, 'Appel de fonds')} - {fc_name_for_lines}".rstrip(" -")

    lines = []
    sum_dr_prov = 0.0
    sum_dr_res = 0.0
    sum_dr_roul = 0.0
    for d in distribution:
        oid = d.get("owner_id")
        owner = owners_map.get(oid)
        if not owner:
            continue
        accs = get_owner_accounts(owner, copro_id)
        owner_total = float(d.get("amount", 0) or 0)
        # iter90ea : persiste lot_id + lot_number sur chaque ligne du journal
        # pour eliminer definitivement le risque phantom au niveau schema.
        # Ces champs restent stables meme apres suppression/re-import du lot,
        # permettant une resolution robuste par lot_number.
        lot_id_for_line = d.get("lot_id", "") or ""
        lot_number_for_line = d.get("lot_number", "") or ""
        # Prorate reserve + roulement vs provisions per owner
        owner_reserve = round(owner_total * (reserve_total / full_total), 2) if reserve_total > 0 else 0
        owner_roul = round(owner_total * (roulement_total / full_total), 2) if roulement_total > 0 else 0
        owner_prov = round(owner_total - owner_reserve - owner_roul, 2)
        if owner_prov > 0.001 and accs.get("provisions"):
            lines.append({
                "account_number": accs["provisions"],
                "account_name": f"Prov. charges - {owner.get('last_name') or owner.get('name')}",
                "debit": owner_prov, "credit": 0.0,
                "third_party_id": oid,
                "third_party_name": owner.get("name", ""),
                "line_description": label_prov if call_type_for_default in ("", "provisions") else label_default,
                "lot_id": lot_id_for_line,
                "lot_number": lot_number_for_line,
            })
            sum_dr_prov += owner_prov
        if owner_reserve > 0.001 and accs.get("reserve"):
            lines.append({
                "account_number": accs["reserve"],
                "account_name": f"Fonds reserve - {owner.get('last_name') or owner.get('name')}",
                "debit": owner_reserve, "credit": 0.0,
                "third_party_id": oid,
                "third_party_name": owner.get("name", ""),
                "line_description": label_res,
                "lot_id": lot_id_for_line,
                "lot_number": lot_number_for_line,
            })
            sum_dr_res += owner_reserve
        if owner_roul > 0.001 and accs.get("provisions"):
            # Fonds de roulement: meme compte tier owner 40000XXX
            lines.append({
                "account_number": accs["provisions"],
                "account_name": f"Fonds roulement - {owner.get('last_name') or owner.get('name')}",
                "debit": owner_roul, "credit": 0.0,
                "third_party_id": oid,
                "third_party_name": owner.get("name", ""),
                "line_description": label_roul,
                "lot_id": lot_id_for_line,
                "lot_number": lot_number_for_line,
            })
            sum_dr_roul += owner_roul

    sum_dr_prov = round(sum_dr_prov, 2)
    sum_dr_res = round(sum_dr_res, 2)
    sum_dr_roul = round(sum_dr_roul, 2)
    if sum_dr_prov > 0:
        lines.append({
            "account_number": "700000",
            "account_name": "Provisions appelees pour charges",
            "debit": 0.0, "credit": sum_dr_prov,
            "third_party_id": None, "third_party_name": "",
        })
    if sum_dr_res > 0:
        lines.append({
            "account_number": "160",
            "account_name": "Fonds de reserve",
            "debit": 0.0, "credit": sum_dr_res,
            "third_party_id": None, "third_party_name": "",
        })
    if sum_dr_roul > 0:
        lines.append({
            "account_number": "100",
            "account_name": "Fonds de roulement",
            "debit": 0.0, "credit": sum_dr_roul,
            "third_party_id": None, "third_party_name": "",
        })

    if not lines or not _balanced(lines):
        return None

    await _delete_auto_entries(db, "fund_call", fund_call["id"])
    # Description claire en fonction du type d'appel (provisions/reserve/roulement/special)
    _type_labels = {
        "provisions": "Appel de provisions",
        "reserve": "Appel fonds de reserve",
        "roulement": "Appel fonds de roulement",
        "special": "Appel special",
    }
    type_label = _type_labels.get(fund_call.get("call_type", ""), "Appel de fonds")
    fc_name = fund_call.get("name", "")
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "VE",
        "date": fund_call.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "reference": f"AF-{fc_name[:20]}",
        "description": f"{type_label} - {fc_name}" if fc_name else type_label,
        "lines": lines,
        "total_debit": round(sum_dr_prov + sum_dr_res + sum_dr_roul, 2),
        "total_credit": round(sum_dr_prov + sum_dr_res + sum_dr_roul, 2),
        "copropriete_id": copro_id,
        "auto_generated": True,
        "source_type": "fund_call",
        "source_id": fund_call["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def generate_bank_entry(db, txn: dict) -> dict | None:
    """FI: Dr 550xxx + Cr 40000XXX (owner payment) OR
        Dr 44000XXX + Cr 550xxx (supplier payment) OR
        Dr/Cr 550xxx + 70xxxx/6xxxxx (manual category).
    Only triggered for MATCHED transactions.

    NOTE : txn['account_number'] est un IBAN (ex BE68...). On le resout vers
    le compte PCMN bancaire (ex 55103400) via les bank_accounts de l'ACP.
    Fallback: 550000.
    """
    copro_id = txn.get("copropriete_id", "")
    if not copro_id:
        return None
    # NOTE : on ne return PAS quand non matched - on utilise un compte d'attente
    # plus loin pour que le compte bancaire apparaisse quand meme au bilan.
    amount = abs(float(txn.get("amount", 0) or 0))
    if amount <= 0:
        return None
    # iter90by : resolution IBAN -> compte PCMN bancaire extraite en helper
    bank_acc, bank_label = await _resolve_bank_account(db, txn, copro_id)
    match_type = txn.get("match_type", "")
    txn_type = txn.get("transaction_type", "credit")
    is_credit = txn_type == "credit" or float(txn.get("amount", 0)) > 0

    # iter90by : resolution match_type -> contrepartie extraite en helper
    # (sauf expense_category, gere plus bas car il produit une ecriture complete)
    if match_type != "expense_category":
        counterpart_acc, counterpart_name, third_party_id, invoice_number = (
            await _resolve_bank_counterpart(db, txn, copro_id)
        )
    else:
        counterpart_acc = ""
        counterpart_name = ""
        third_party_id = None
        invoice_number = ""

    if match_type == "expense_category":
        # iter90k : catégorisation d'une transaction par nature(s) de charge/produit.
        # Support multi-splits. Structure de la ligne bancaire (banque) + N
        # lignes de contreparties (comptes 6xxx charges ou 7xxx produits).
        splits = txn.get("category_splits") or []
        if splits:
            pcmn_bank_q = {"number": bank_acc, "copropriete_id": copro_id}
            pcmn_bank = await db.pcmn_accounts.find_one(pcmn_bank_q, {"_id": 0})
            bank_name = (pcmn_bank or {}).get("name") or bank_label
            if is_credit:
                # Money in (ex intérêt créditeur) : Dr banque + Cr N x produit
                lines = [{
                    "account_number": bank_acc, "account_name": bank_name,
                    "debit": amount, "credit": 0.0,
                    "third_party_id": None, "third_party_name": "",
                }]
                for s in splits:
                    lines.append({
                        "account_number": s.get("account_number", ""),
                        "account_name": s.get("account_name", ""),
                        "debit": 0.0, "credit": round(float(s.get("amount", 0) or 0), 2),
                        "distribution_key_id": s.get("distribution_key_id", ""),
                        "expense_category_id": s.get("expense_category_id", ""),
                        "line_description": s.get("description")
                            or s.get("expense_category_name", ""),
                        "third_party_id": None, "third_party_name": "",
                    })
            else:
                # Money out (ex frais bancaires) : Dr N x charge + Cr banque
                lines = []
                for s in splits:
                    lines.append({
                        "account_number": s.get("account_number", ""),
                        "account_name": s.get("account_name", ""),
                        "debit": round(float(s.get("amount", 0) or 0), 2),
                        "credit": 0.0,
                        "distribution_key_id": s.get("distribution_key_id", ""),
                        "expense_category_id": s.get("expense_category_id", ""),
                        "line_description": s.get("description")
                            or s.get("expense_category_name", ""),
                        "third_party_id": None, "third_party_name": "",
                    })
                lines.append({
                    "account_number": bank_acc, "account_name": bank_name,
                    "debit": 0.0, "credit": amount,
                    "third_party_id": None, "third_party_name": "",
                })
            comm = (txn.get("communication") or "").strip()
            cp = (txn.get("counterparty_name") or "").strip()
            desc = f"Categorisation {cp}".strip()
            if comm:
                desc = f"{desc} - {comm}" if desc else comm
            desc = desc.strip(" -") or "Transaction categorisee"
            await _delete_auto_entries(db, "bank_txn", txn["id"])
            doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "FI",
                "date": txn.get("date") or datetime.now(timezone.utc).date().isoformat(),
                "reference": f"FI-CAT-{txn['id'][:8]}",
                "description": desc,
                "lines": lines,
                "total_debit": amount,
                "total_credit": amount,
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "bank_txn",
                "source_id": txn["id"],
                # iter90jm : lien Master/Slave explicite
                "statement_line_id": txn["id"],
                "bank_statement_id": txn.get("statement_id") or "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(doc)
            return doc

    # Fallback : transaction non lettree -> compte d'attente 499000
    # Permet au compte bancaire d'apparaitre dans le bilan meme avant le lettrage.
    if not counterpart_acc:
        counterpart_acc = "499000"
        counterpart_name = (
            f"Encaissement non identifie - {txn.get('communication','')[:40]}"
            if is_credit else
            f"Decaissement non identifie - {txn.get('communication','')[:40]}"
        )
        # S'assurer que le compte 499000 existe dans le pcmn de cette ACP
        existing = await db.pcmn_accounts.find_one(
            {"number": "499000", "copropriete_id": copro_id}, {"_id": 0}
        )
        if not existing:
            await db.pcmn_accounts.insert_one({
                "number": "499000",
                "name": "Encaissements / Decaissements non identifies",
                "class_num": 4,
                "parent": "499",
                "type": "balance",
                "copropriete_id": copro_id,
                "active": True,
                "is_custom": True,
            })

    pcmn_q = {"number": {"$in": [bank_acc, counterpart_acc]}, "copropriete_id": copro_id}
    pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(10)
    pcmn_names = {p["number"]: p["name"] for p in pcmns}

    # Libelle ligne tier : enrichi avec le numero de facture quand applicable
    tier_line_desc = ""
    if invoice_number:
        tier_line_desc = f"Paiement facture {invoice_number}"
        if counterpart_name:
            tier_line_desc += f" - {counterpart_name}"
    elif counterpart_name:
        tier_line_desc = counterpart_name

    if is_credit:
        # Money in: Dr bank + Cr counterpart (owner pays / refund)
        lines = [
            {"account_number": bank_acc, "account_name": pcmn_names.get(bank_acc, bank_label),
             "debit": amount, "credit": 0.0, "third_party_id": None, "third_party_name": ""},
            {"account_number": counterpart_acc, "account_name": pcmn_names.get(counterpart_acc, counterpart_name),
             "debit": 0.0, "credit": amount, "third_party_id": third_party_id, "third_party_name": counterpart_name,
             "line_description": tier_line_desc},
        ]
    else:
        # Money out: Dr counterpart + Cr bank (supplier paid / refund owner)
        lines = [
            {"account_number": counterpart_acc, "account_name": pcmn_names.get(counterpart_acc, counterpart_name),
             "debit": amount, "credit": 0.0, "third_party_id": third_party_id, "third_party_name": counterpart_name,
             "line_description": tier_line_desc},
            {"account_number": bank_acc, "account_name": pcmn_names.get(bank_acc, bank_label),
             "debit": 0.0, "credit": amount, "third_party_id": None, "third_party_name": ""},
        ]

    # Libelle global de l'ecriture : prefixe la facture si applicable
    if invoice_number:
        full_desc = f"Paiement facture {invoice_number} - {counterpart_name or txn.get('counterparty_name','')}"
        full_desc = full_desc.rstrip(" -")
    else:
        full_desc = f"{txn.get('counterparty_name','') or counterpart_name} - {txn.get('communication','')}".strip(" -")

    await _delete_auto_entries(db, "bank_txn", txn["id"])
    # iter90ji : VERROU anti-doublon. Si un JE FI importe/manuel existe DEJA pour
    # cette meme signature (ACP + date + montant + compte tier), on ne cree PAS
    # de nouveau JE (l'importe est source of truth) et on marque juste la txn
    # comme lettree a ce JE existant.
    tier_accounts_new = {ln["account_number"] for ln in lines if ln.get("account_number", "").startswith(("440", "4100", "4101", "400"))}
    if tier_accounts_new:
        existing = await db.journal_entries.find_one({
            "copropriete_id": copro_id,
            "journal_type": "FI",
            "date": txn.get("date"),
            "total_debit": amount,
            "reversed": {"$ne": True},
            "is_reversal": {"$ne": True},
            # au moins un compte tier commun
            "lines.account_number": {"$in": list(tier_accounts_new)},
            # doit etre importe ou manuel, pas un autre auto
            "$or": [
                {"import_session_id": {"$exists": True, "$ne": None}},
                {"auto_generated": {"$ne": True}},
            ],
        }, {"_id": 0, "id": 1, "reference": 1, "import_session_id": 1})
        if existing:
            # Lie la txn au JE existant (audit trail) et ne cree PAS de doublon.
            await db.bank_transactions.update_one(
                {"id": txn["id"]},
                {"$set": {
                    "matched_je_id": existing["id"],
                    "matched_je_ref": existing.get("reference"),
                    "matched_je_source": "imported" if existing.get("import_session_id") else "manual",
                }},
            )
            return existing
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "FI",
        "date": txn.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "reference": f"FI-{txn['id'][:8]}",
        "description": full_desc,
        "lines": lines,
        "total_debit": amount,
        "total_credit": amount,
        "copropriete_id": copro_id,
        "auto_generated": True,
        "source_type": "bank_txn",
        "source_id": txn["id"],
        # iter90jm : lien Master/Slave explicite - la FI depend de cette txn
        # (statement_line_id) et de son extrait parent (bank_statement_id).
        "statement_line_id": txn["id"],
        "bank_statement_id": txn.get("statement_id") or "",
        "invoice_number": invoice_number or None,  # backref pour reporting
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(doc)
    # iter90ji : lie la txn a ce JE nouvellement cree (piste d'audit)
    await db.bank_transactions.update_one(
        {"id": txn["id"]},
        {"$set": {"matched_je_id": doc["id"], "matched_je_ref": doc["reference"], "matched_je_source": "auto"}},
    )
    return {k: v for k, v in doc.items() if k != "_id"}
