"""Helper module - compute the same rows that `GET /api/fiscal/expenses`
returns. Used by:
  - routes/fiscal.py (list_expenses endpoint) -> via re-export
  - routes/reports.py (liste_depenses_pdf endpoint, iter90e) -> so that
    the PDF "Liste des depenses" matches EXACTLY the UI invariant :

      sum(TVAC du PDF, sans filtre) == totals.total de /api/fiscal/expenses

Logic copied from fiscal.py::list_expenses (single source of truth).
"""


async def compute_expense_rows(
    db,
    copropriete_id: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    account_number: str | None = None,
    distribution_key_id: str | None = None,
    expense_category_id: str | None = None,
    bank_account: str | None = None,
):
    """Returns (rows, totals).
    Each row carries `total_amount` (TVAC), `vat_amount` (TVA when known),
    `account_number`, `distribution_key_id`, `expense_category_name`, etc.
    """
    if not copropriete_id or copropriete_id == "all":
        return [], {"total": 0.0, "count": 0, "by_account": {}, "by_key": {}, "by_bank": {}}

    # 1) Invoices
    # iter90i : EXCLURE les factures privatives (is_private_fee=true) du
    # total des charges communes. Elles sont refacturees au compte 643
    # mais ne sont PAS une charge commune de la copropriete : elles sont
    # ventilees directement aux proprietaires concernes via le decompte
    # de mutation et/ou l'OD-PRIV. Les inclure ici cree un sur-comptage
    # systematique du total "Dépenses de l'exercice".
    inv_q: dict = {
        "copropriete_id": copropriete_id,
        "is_private_fee": {"$ne": True},
    }
    if date_from or date_to:
        inv_q["date"] = {}
        if date_from:
            inv_q["date"]["$gte"] = date_from
        if date_to:
            inv_q["date"]["$lte"] = date_to
    if account_number:
        inv_q["$or"] = [
            {"account_number": account_number},
            {"lines.account_number": account_number},
        ]
    if distribution_key_id:
        key_or = [
            {"distribution_key_id": distribution_key_id},
            {"lines.distribution_key_id": distribution_key_id},
        ]
        if "$or" in inv_q:
            inv_q = {"$and": [inv_q, {"$or": key_or}]}
        else:
            inv_q["$or"] = key_or
    if expense_category_id:
        cat_or = [
            {"expense_category_id": expense_category_id},
            {"lines.expense_category_id": expense_category_id},
        ]
        if "$and" in inv_q:
            inv_q["$and"].append({"$or": cat_or})
        elif "$or" in inv_q:
            inv_q = {"$and": [inv_q, {"$or": cat_or}]}
        else:
            inv_q["$or"] = cat_or
    invoices = await db.invoices.find(inv_q, {"_id": 0}).sort("date", 1).to_list(50000)

    # 2) Filter by bank account if requested
    if bank_account:
        bank_matches = await db.bank_transactions.find(
            {"matched": True, "match_type": "invoice",
             "account_number": bank_account,
             "copropriete_id": copropriete_id},
            {"_id": 0, "matched_to": 1}
        ).to_list(10000)
        paid_ids = {b["matched_to"] for b in bank_matches}
        invoices = [i for i in invoices if i["id"] in paid_ids]

    # 3) Resolve distribution key names
    keys = await db.distribution_keys.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).to_list(1000)
    keys_map = {k["id"]: k["name"] for k in keys}

    # 3b) Resolve expense categories
    cats = await db.expense_categories.find(
        {"copropriete_id": copropriete_id}, {"_id": 0}
    ).to_list(2000)
    cat_by_id = {c["id"]: c for c in cats}
    cat_by_acc: dict = {}
    for c in cats:
        acc = c.get("account_number", "")
        if acc:
            cat_by_acc.setdefault(acc, c)

    # 4) Resolve PCMN names + bank info
    accs = await db.pcmn_accounts.find(
        {"class_num": 6, "copropriete_id": copropriete_id},
        {"_id": 0, "number": 1, "name": 1}
    ).to_list(1000)
    acc_names = {a["number"]: a["name"] for a in accs}

    # Resolve paid info per invoice
    all_inv_ids = [i["id"] for i in invoices]
    bank_q = {"matched": True, "match_type": "invoice", "matched_to": {"$in": all_inv_ids},
              "copropriete_id": copropriete_id}
    bank_txns = await db.bank_transactions.find(bank_q, {"_id": 0}).to_list(50000)
    paid_map: dict = {}
    for t in bank_txns:
        paid_map[t["matched_to"]] = {
            "amount": abs(t.get("amount", 0)),
            "date": t.get("date", ""),
            "bank_account": t.get("account_number", ""),
        }

    rows = []
    invoice_ids_done = set()

    # iter86 : resolve owner names for private fees
    owner_ids_needed = set()
    for inv in invoices:
        if inv.get("is_private_fee"):
            for a in (inv.get("private_fee_allocations") or []):
                if a.get("owner_id"):
                    owner_ids_needed.add(a["owner_id"])
            if inv.get("private_fee_owner_id"):
                owner_ids_needed.add(inv["private_fee_owner_id"])
    owners_by_id: dict = {}
    if owner_ids_needed:
        owner_docs = await db.owners.find(
            {"id": {"$in": list(owner_ids_needed)}},
            {"_id": 0, "id": 1, "name": 1, "first_name": 1, "last_name": 1}
        ).to_list(1000)
        for o in owner_docs:
            disp = (o.get("name") or f"{o.get('first_name','')} {o.get('last_name','')}").strip()
            owners_by_id[o["id"]] = disp or o["id"]

    for inv in invoices:
        invoice_ids_done.add(inv["id"])
        inv_lines = inv.get("lines") or []
        inv_total = float(inv.get("total_amount", 0) or 0)
        inv_vat = float(inv.get("vat_amount", 0) or 0)
        if inv_lines:
            # Multi-line : split into N rows. TVA is kept at invoice level
            # but distributed pro-rata across lines for correct totals.
            sum_li = sum(float(li.get("amount", 0) or 0) for li in inv_lines) or 1.0
            for li_idx, li in enumerate(inv_lines):
                li_acc = li.get("account_number", "")
                li_key_id = li.get("distribution_key_id", "")
                li_cat = cat_by_id.get(li.get("expense_category_id", "")) or cat_by_acc.get(li_acc) or {}
                li_amt = float(li.get("amount", 0) or 0)
                if account_number and li_acc != account_number:
                    continue
                if distribution_key_id and li_key_id != distribution_key_id:
                    continue
                if expense_category_id and li.get("expense_category_id", "") != expense_category_id and li_cat.get("id", "") != expense_category_id:
                    continue
                occ_pct = float(inv.get("occupant_pct", 0) or 0)
                prop_pct = float(inv.get("proprietaire_pct", 100) or 100) if inv.get("proprietaire_pct") is not None else 100
                # Distribute VAT pro-rata so sum of all line VATs = invoice VAT
                li_vat = round(inv_vat * li_amt / sum_li, 2) if inv_vat else 0.0
                rows.append({
                    "id": f"{inv['id']}::line-{li_idx}",
                    "invoice_id": inv["id"],
                    "is_invoice_line": True,
                    "line_index": li_idx,
                    "line_count": len(inv_lines),
                    "date": inv.get("date", ""),
                    "number": inv.get("number", ""),
                    "supplier": inv.get("supplier", ""),
                    "description": (li.get("description") or inv.get("description", "")).strip(),
                    "account_number": li_acc,
                    "account_name": acc_names.get(li_acc, ""),
                    "expense_category_id": li_cat.get("id", ""),
                    "expense_category_name": li_cat.get("name", ""),
                    "expense_category_code": li_cat.get("code", ""),
                    "distribution_key_id": li_key_id,
                    "distribution_key_name": keys_map.get(li_key_id, "Sans cle"),
                    "vat_amount": li_vat,
                    "total_amount": round(li_amt, 2),
                    "status": inv.get("status", "unpaid"),
                    "paid": inv["id"] in paid_map,
                    "paid_info": paid_map.get(inv["id"]),
                    "attachments_count": len(inv.get("attachments", []) or []),
                    "occupant_pct": occ_pct,
                    "proprietaire_pct": prop_pct,
                    "occupant_amount": round(li_amt * occ_pct / 100, 2),
                    "proprietaire_amount": round(li_amt * prop_pct / 100, 2),
                    "source": "invoice",
                    "journal_type": "AC",
                    "invoice_total_amount": inv_total,
                })
            continue

        # Single-line invoice
        acc = inv.get("account_number", "")
        key_id = inv.get("distribution_key_id", "")
        cat = cat_by_id.get(inv.get("expense_category_id", "")) or cat_by_acc.get(acc) or {}
        is_priv = bool(inv.get("is_private_fee"))
        priv_allocs = []
        priv_owners_display = ""
        if is_priv:
            allocs_raw = inv.get("private_fee_allocations") or []
            if not allocs_raw and inv.get("private_fee_owner_id"):
                allocs_raw = [{"owner_id": inv["private_fee_owner_id"], "amount": inv_total}]
            for a in allocs_raw:
                oid = a.get("owner_id", "")
                priv_allocs.append({
                    "owner_id": oid,
                    "owner_name": owners_by_id.get(oid, ""),
                    "amount": round(float(a.get("amount", 0) or 0), 2),
                })
            priv_owners_display = ", ".join(p["owner_name"] for p in priv_allocs if p["owner_name"])
        rows.append({
            "id": inv["id"],
            "date": inv.get("date", ""),
            "number": inv.get("number", ""),
            "supplier": inv.get("supplier", ""),
            "description": inv.get("description", ""),
            "account_number": acc,
            "account_name": acc_names.get(acc, ""),
            "expense_category_id": cat.get("id", ""),
            "expense_category_name": cat.get("name", ""),
            "expense_category_code": cat.get("code", ""),
            "distribution_key_id": key_id,
            "distribution_key_name": keys_map.get(key_id, "Sans cle"),
            "vat_amount": inv_vat,
            "total_amount": inv_total,
            "status": inv.get("status", "unpaid"),
            "paid": inv["id"] in paid_map,
            "paid_info": paid_map.get(inv["id"]),
            "attachments_count": len(inv.get("attachments", []) or []),
            "occupant_pct": inv.get("occupant_pct", 0) or 0,
            "proprietaire_pct": inv.get("proprietaire_pct", 100) if inv.get("proprietaire_pct") is not None else 100,
            "occupant_amount": inv.get("occupant_amount", 0) or 0,
            "proprietaire_amount": inv.get("proprietaire_amount", 0) or inv_total,
            "source": "invoice",
            "journal_type": "AC",
            "is_private_fee": is_priv,
            "private_fee_allocations": priv_allocs,
            "private_fee_owners_display": priv_owners_display,
        })

    # ----- Include FI / OD entries on class-6 charge accounts -----
    all_charge_accs_q = {"class_num": {"$in": [6, 7]}, "copropriete_id": copropriete_id}
    charge_accs = await db.pcmn_accounts.find(
        all_charge_accs_q, {"_id": 0, "number": 1, "name": 1, "class_num": 1}
    ).to_list(2000)
    charge_acc_set = {a["number"] for a in charge_accs}
    charge_acc_names = {a["number"]: a["name"] for a in charge_accs}

    def _is_charge_account(num: str) -> bool:
        """True si le compte est une vraie charge COMMUNE (classe 6 ou 75).
        iter90h : protection defensive - les comptes commençant par 44 (frais
        privatifs en passage, comptes tampon fournisseur, etc.) ne sont JAMAIS
        consideres comme charges meme si le PCMN les marque class_num=6.
        iter90i : exclure aussi 643* (Frais privatifs) - ce sont des charges
        privatives refacturees aux proprietaires, jamais des charges communes.
        Cela evite le double comptage via la pass FI/OD (l'OD-PRIV de
        refacturation pose un debit sur 643)."""
        if not num:
            return False
        if num.startswith("44") or num.startswith("40") or num.startswith("41") or num.startswith("42"):
            return False  # classe 4 = comptes de tiers, jamais une charge
        if num.startswith("643"):
            return False  # frais privatifs, jamais une charge commune
        if num in charge_acc_set:
            return True
        return num.startswith("6") or num.startswith("75")

    je_q: dict = {"journal_type": {"$in": ["FI", "OD"]}, "copropriete_id": copropriete_id}
    if date_from or date_to:
        je_q["date"] = {}
        if date_from:
            je_q["date"]["$gte"] = date_from
        if date_to:
            je_q["date"]["$lte"] = date_to
    je_q["$and"] = [
        {"reverses_id": {"$exists": False}},
        {"reversed_by_id": {"$exists": False}},
    ]
    je_entries = await db.journal_entries.find(je_q, {"_id": 0}).sort("date", 1).to_list(50000)
    for je in je_entries:
        if je.get("source_type") == "invoice" and je.get("source_id") in invoice_ids_done:
            continue
        for ln in je.get("lines", []) or []:
            acc = (ln.get("account_number") or "").strip()
            if not _is_charge_account(acc):
                continue
            if account_number and acc != account_number:
                continue
            ln_cat_id = ln.get("expense_category_id") or ""
            ln_key_id = ln.get("distribution_key_id") or ""
            cat = cat_by_id.get(ln_cat_id) or cat_by_acc.get(acc) or {}
            if expense_category_id and cat.get("id", "") != expense_category_id:
                continue
            if distribution_key_id and ln_key_id != distribution_key_id:
                continue
            debit = float(ln.get("debit", 0) or 0)
            credit = float(ln.get("credit", 0) or 0)
            amount = debit - credit
            if abs(amount) < 0.005:
                continue
            desc = (ln.get("description") or je.get("description", "") or "").strip()
            occ_pct = ln.get("occupant_pct")
            prop_pct = ln.get("proprietaire_pct")
            if occ_pct is None:
                occ_pct = 0
            if prop_pct is None:
                prop_pct = 100
            rows.append({
                "id": je.get("id", ""),
                "date": je.get("date", ""),
                "number": je.get("reference", "") or "",
                "supplier": ln.get("counterparty_name", "") or je.get("description", "")[:50] or "—",
                "description": desc,
                "account_number": acc,
                "account_name": charge_acc_names.get(acc, "") or ln.get("account_name", ""),
                "expense_category_id": cat.get("id", ""),
                "expense_category_name": cat.get("name", "") or charge_acc_names.get(acc, ""),
                "expense_category_code": cat.get("code", ""),
                "distribution_key_id": ln_key_id,
                "distribution_key_name": keys_map.get(ln_key_id, "Sans cle"),
                "vat_amount": 0,
                "total_amount": round(amount, 2),
                "status": "comptabilise",
                "paid": True,
                "paid_info": None,
                "attachments_count": 0,
                "occupant_pct": float(occ_pct),
                "proprietaire_pct": float(prop_pct),
                "occupant_amount": round(amount * float(occ_pct) / 100, 2),
                "proprietaire_amount": round(amount * float(prop_pct) / 100, 2),
                "source": "journal",
                "source_account": acc,
                "journal_type": je.get("journal_type", ""),
            })

    rows.sort(key=lambda r: (r.get("date", ""), r.get("number", "")))

    totals = {
        "by_account": {},
        "by_key": {},
        "by_bank": {},
        "total": round(sum(r["total_amount"] for r in rows), 2),
        "total_htva": round(sum((r["total_amount"] - r.get("vat_amount", 0)) for r in rows), 2),
        "total_vat": round(sum(r.get("vat_amount", 0) for r in rows), 2),
        "count": len(rows),
    }
    for r in rows:
        acc = r["account_number"] or "—"
        totals["by_account"][acc] = round(totals["by_account"].get(acc, 0) + r["total_amount"], 2)
        kn = r["distribution_key_name"]
        totals["by_key"][kn] = round(totals["by_key"].get(kn, 0) + r["total_amount"], 2)
        if r["paid"] and r["paid_info"]:
            ba = r["paid_info"]["bank_account"] or "—"
            totals["by_bank"][ba] = round(totals["by_bank"].get(ba, 0) + r["paid_info"]["amount"], 2)

    return rows, totals
