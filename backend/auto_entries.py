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


async def _delete_auto_entries(db, source_type: str, source_id: str):
    """Remove any previously auto-generated entries for this source.
    Skips entries that have been manually edited (preservation)."""
    await db.journal_entries.delete_many({
        "auto_generated": True,
        "manually_edited": {"$ne": True},
        "source_type": source_type,
        "source_id": source_id,
    })


def _balanced(lines: list) -> bool:
    return abs(sum(l.get("debit", 0) for l in lines)
               - sum(l.get("credit", 0) for l in lines)) < 0.01


async def generate_purchase_entry(db, invoice: dict) -> dict | None:
    """AC: Dr 6xxxxx (expense) + Cr 44000XXX (supplier).
    Skips if invoice has no supplier name or no account_number."""
    copro_id = invoice.get("copropriete_id", "")
    if not copro_id:
        return None
    amount = float(invoice.get("total_amount", 0) or 0)
    if amount <= 0:
        return None
    expense_acc = invoice.get("account_number", "") or "600000"
    supplier_name = (invoice.get("supplier") or "").strip()
    # Resolve/ensure supplier + dedicated 44000XXX account in this ACP.
    # Chinese walls strict: NEVER fallback on "440000" (master) which would mix ACPs.
    supplier_acc = ""
    supplier_doc = None
    if supplier_name:
        import re, uuid
        from datetime import datetime, timezone
        supplier_doc = await db.suppliers.find_one(
            {"name": {"$regex": f"^{re.escape(supplier_name)}$", "$options": "i"}}, {"_id": 0}
        )
        if not supplier_doc:
            # Auto-create a global supplier record so we never use 440000 master
            supplier_doc = {
                "id": str(uuid.uuid4()),
                "name": supplier_name,
                "tier_accounts": {},
                "auto_created": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.suppliers.insert_one(dict(supplier_doc))
        supplier_doc = await assign_supplier_account(db, supplier_doc, copro_id)
        supplier_acc = get_supplier_account(supplier_doc, copro_id)
    if not supplier_acc:
        # As a LAST resort (no supplier name at all), create an ACP-scoped misc account
        # but never the master 440000 (would leak across ACPs).
        from tier_accounts import _next_seq, _format_seq, _ensure_account
        seq = await _next_seq(db, copro_id, "44000")
        supplier_acc = _format_seq("44000", seq, width=3)
        await _ensure_account(db, copro_id, supplier_acc, "Fournisseur divers", 4)

    # Pre-fetch PCMN names
    pcmn_q = {"number": {"$in": [expense_acc, supplier_acc]}, "copropriete_id": copro_id}
    pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(10)
    pcmn_names = {p["number"]: p["name"] for p in pcmns}

    await _delete_auto_entries(db, "invoice", invoice["id"])

    # ---- FRAIS PRIVATIF : 2 ecritures separees ----
    # Ecriture 1 (AC - Achats) : Facture fournisseur
    #   Dr 643 Frais privatif  | Cr 44000XXX Fournisseur
    # Ecriture 2 (OD - Operations Diverses) : Refacturation au proprietaire
    #   Dr 40000XXX Proprietaire | Cr 643 Frais privatif (imputation)
    # Net 643 = 0, fournisseur credite, proprietaire debite.
    if invoice.get("is_private_fee") and invoice.get("private_fee_owner_id"):
        owner_id = invoice["private_fee_owner_id"]
        owner_doc = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if owner_doc:
            owner_doc = await assign_owner_accounts(db, owner_doc, copro_id)
            owner_accs = get_owner_accounts(owner_doc, copro_id)
            owner_prov = owner_accs.get("provisions", "") or "400000"
            owner_name = owner_doc.get("name", "")
            # Re-fetch PCMN names including 643
            pcmn_q2 = {"number": {"$in": ["643", supplier_acc, owner_prov]}, "copropriete_id": copro_id}
            pcmns2 = await db.pcmn_accounts.find(pcmn_q2, {"_id": 0}).to_list(10)
            pcmn_names2 = {p["number"]: p["name"] for p in pcmns2}

            # --- Ecriture 1 : AC (Achats) ---
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
                "description": f"Frais privatif {owner_name} - {invoice.get('supplier','')} - {invoice.get('description','')}".strip(" -"),
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

            # --- Ecriture 2 : OD (Operations Diverses) - refacturation au proprietaire ---
            od_lines = [
                {"account_number": owner_prov,
                 "account_name": pcmn_names2.get(owner_prov, f"Prov. - {owner_name}"),
                 "debit": amount, "credit": 0.0,
                 "third_party_id": owner_id,
                 "third_party_name": owner_name},
                {"account_number": "643",
                 "account_name": pcmn_names2.get("643", "Frais privatifs"),
                 "debit": 0.0, "credit": amount,
                 "third_party_id": None,
                 "third_party_name": f"Imputation - {owner_name}"},
            ]
            od_doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "OD",
                "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
                "reference": f"OD-PRIV-{invoice.get('number','')}",
                "description": f"Refacturation frais privatif a {owner_name} - {invoice.get('supplier','')}".strip(" -"),
                "lines": od_lines,
                "total_debit": amount,
                "total_credit": amount,
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
            amt = round(float(ln.get("amount", 0) or 0), 2)
            if amt <= 0 or not acc:
                continue
            desc = (ln.get("description") or "").strip()
            lines.append({
                "account_number": acc,
                "account_name": pcmn_names_multi.get(acc, "") + (f" - {desc}" if desc else ""),
                "debit": amt, "credit": 0.0,
                "third_party_id": None, "third_party_name": "",
            })
        lines.append({
            "account_number": supplier_acc,
            "account_name": pcmn_names_multi.get(supplier_acc, supplier_name),
            "debit": 0.0, "credit": amount,
            "third_party_id": (supplier_doc or {}).get("id"),
            "third_party_name": supplier_name,
        })
    else:
        lines = [
            {"account_number": expense_acc,
             "account_name": pcmn_names.get(expense_acc, ""),
             "debit": amount, "credit": 0.0,
             "third_party_id": None, "third_party_name": ""},
            {"account_number": supplier_acc,
             "account_name": pcmn_names.get(supplier_acc, supplier_name),
             "debit": 0.0, "credit": amount,
             "third_party_id": (supplier_doc or {}).get("id"),
             "third_party_name": supplier_name},
        ]
    if not _balanced(lines):
        return None
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "AC",
        "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "reference": f"FA-{invoice.get('number','')}",
        "description": f"Facture {invoice.get('supplier','')} - {invoice.get('description','')}".strip(" -"),
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
    """
    copro_id = fund_call.get("copropriete_id", "")
    if not copro_id:
        return None
    distribution = fund_call.get("distribution") or []
    if not distribution:
        return None

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
    # Resoud IBAN -> compte PCMN via la config de l'ACP
    # IBAN peut etre stocke sur la txn (legacy) OU sur le statement parent (cas standard)
    iban = (txn.get("account_number") or "").replace(" ", "").upper()
    if not iban and txn.get("statement_id"):
        stmt = await db.bank_statements.find_one(
            {"id": txn["statement_id"]},
            {"_id": 0, "account_number": 1, "iban": 1},
        )
        if stmt:
            iban = (stmt.get("account_number") or stmt.get("iban") or "").replace(" ", "").upper()
    bank_acc = "550000"  # fallback compte banque generique (ne devrait JAMAIS arriver si IBAN configure)
    bank_label = "Banque"
    if iban:
        copro = await db.coproprietes.find_one({"id": copro_id}, {"_id": 0, "bank_accounts": 1})
        if copro:
            for ba in (copro.get("bank_accounts") or []):
                ba_iban = (ba.get("iban") or "").replace(" ", "").upper()
                if ba_iban == iban and ba.get("pcmn_number"):
                    bank_acc = ba["pcmn_number"]
                    bank_label = ba.get("label") or "Banque"
                    break
    match_type = txn.get("match_type", "")
    txn_type = txn.get("transaction_type", "credit")
    is_credit = txn_type == "credit" or float(txn.get("amount", 0)) > 0

    counterpart_acc = ""
    counterpart_name = ""
    third_party_id = None
    invoice_number = ""  # libelle enrichi pour les lettrages factures
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
            if sname:
                import re
                supplier = await db.suppliers.find_one(
                    {"name": {"$regex": f"^{re.escape(sname)}$", "$options": "i"}}, {"_id": 0}
                )
                if supplier:
                    supplier = await assign_supplier_account(db, supplier, copro_id)
                    counterpart_acc = get_supplier_account(supplier, copro_id)
                    counterpart_name = supplier.get("name", "") or sname
                    third_party_id = supplier["id"]
                else:
                    # Pas de supplier en base : on garde le nom de la facture mais sans tier_id
                    counterpart_name = sname
    elif match_type == "supplier_payment":
        supplier = await db.suppliers.find_one({"id": txn.get("matched_to")}, {"_id": 0})
        if supplier:
            supplier = await assign_supplier_account(db, supplier, copro_id)
            counterpart_acc = get_supplier_account(supplier, copro_id)
            counterpart_name = supplier.get("name", "")
            third_party_id = supplier["id"]

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
        "invoice_number": invoice_number or None,  # backref pour reporting
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}
