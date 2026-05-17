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
    # Resolve/ensure supplier account
    supplier_acc = ""
    supplier_doc = None
    if supplier_name:
        import re
        supplier_doc = await db.suppliers.find_one(
            {"name": {"$regex": f"^{re.escape(supplier_name)}$", "$options": "i"}}, {"_id": 0}
        )
        if supplier_doc:
            supplier_doc = await assign_supplier_account(db, supplier_doc, copro_id)
            supplier_acc = get_supplier_account(supplier_doc, copro_id)
    if not supplier_acc:
        supplier_acc = "440000"  # fallback collective

    # Pre-fetch PCMN names
    pcmn_q = {"number": {"$in": [expense_acc, supplier_acc]}, "copropriete_id": copro_id}
    pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(10)
    pcmn_names = {p["number"]: p["name"] for p in pcmns}

    await _delete_auto_entries(db, "invoice", invoice["id"])

    # ---- FRAIS PRIVATIF : ecriture 4 lignes ----
    # Dr 643 Frais privatif    | Cr 44000XXX Fournisseur
    # Dr 40000XXX Proprietaire | Cr 643 (imputation)
    # Resultat : 643 net = 0, supplier credite, owner debite.
    if invoice.get("is_private_fee") and invoice.get("private_fee_owner_id"):
        owner_id = invoice["private_fee_owner_id"]
        owner_doc = await db.owners.find_one({"id": owner_id}, {"_id": 0})
        if owner_doc:
            owner_doc = await assign_owner_accounts(db, owner_doc, copro_id)
            owner_accs = get_owner_accounts(owner_doc, copro_id)
            owner_prov = owner_accs.get("provisions", "")
            owner_name = owner_doc.get("name", "")
            # Re-fetch PCMN names including 643
            pcmn_q2 = {"number": {"$in": ["643", supplier_acc, owner_prov]}, "copropriete_id": copro_id}
            pcmns2 = await db.pcmn_accounts.find(pcmn_q2, {"_id": 0}).to_list(10)
            pcmn_names2 = {p["number"]: p["name"] for p in pcmns2}
            lines = [
                {"account_number": "643",
                 "account_name": pcmn_names2.get("643", "Frais privatifs"),
                 "debit": amount, "credit": 0.0,
                 "third_party_id": None, "third_party_name": ""},
                {"account_number": supplier_acc,
                 "account_name": pcmn_names2.get(supplier_acc, f"Fourn. - {supplier_name}"),
                 "debit": 0.0, "credit": amount,
                 "third_party_id": (supplier_doc or {}).get("id"),
                 "third_party_name": supplier_name},
                {"account_number": owner_prov or "400000",
                 "account_name": pcmn_names2.get(owner_prov, f"Prov. - {owner_name}"),
                 "debit": amount, "credit": 0.0,
                 "third_party_id": owner_id,
                 "third_party_name": owner_name},
                {"account_number": "643",
                 "account_name": pcmn_names2.get("643", "Frais privatifs"),
                 "debit": 0.0, "credit": amount,
                 "third_party_id": owner_id,
                 "third_party_name": f"Imputation - {owner_name}"},
            ]
            if not _balanced(lines):
                return None
            doc = {
                "id": str(uuid.uuid4()),
                "journal_type": "AC",
                "date": invoice.get("date") or datetime.now(timezone.utc).date().isoformat(),
                "reference": f"FA-{invoice.get('number','')}",
                "description": f"Frais privatif {owner_name} - {invoice.get('supplier','')} - {invoice.get('description','')}".strip(" -"),
                "lines": lines,
                "total_debit": amount * 2,
                "total_credit": amount * 2,
                "copropriete_id": copro_id,
                "auto_generated": True,
                "source_type": "invoice",
                "source_id": invoice["id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.journal_entries.insert_one(doc)
            return {k: v for k, v in doc.items() if k != "_id"}

    # ---- ECRITURE STANDARD : 2 lignes ----
    lines = [
        {"account_number": expense_acc,
         "account_name": pcmn_names.get(expense_acc, ""),
         "debit": amount, "credit": 0.0,
         "third_party_id": None, "third_party_name": ""},
        {"account_number": supplier_acc,
         "account_name": pcmn_names.get(supplier_acc, f"Fourn. - {supplier_name}"),
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
    """VE: Dr 40000XXX per owner (or 40010XXX for reserve part) + Cr 700000/701000.
    Uses fund_call.distribution to know per-owner amounts.
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
            })
            sum_dr_prov += owner_prov
        if owner_reserve > 0.001 and accs.get("reserve"):
            lines.append({
                "account_number": accs["reserve"],
                "account_name": f"Fonds reserve - {owner.get('last_name') or owner.get('name')}",
                "debit": owner_reserve, "credit": 0.0,
                "third_party_id": oid,
                "third_party_name": owner.get("name", ""),
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
            "account_number": "701000",
            "account_name": "Appels fonds de reserve",
            "debit": 0.0, "credit": sum_dr_res,
            "third_party_id": None, "third_party_name": "",
        })
    if sum_dr_roul > 0:
        lines.append({
            "account_number": "100",
            "account_name": "Fonds de roulement general",
            "debit": 0.0, "credit": sum_dr_roul,
            "third_party_id": None, "third_party_name": "",
        })

    if not lines or not _balanced(lines):
        return None

    await _delete_auto_entries(db, "fund_call", fund_call["id"])
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "VE",
        "date": fund_call.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "reference": f"AF-{fund_call.get('name','')[:20]}",
        "description": f"Appel: {fund_call.get('name','')}",
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
    Only triggered for MATCHED transactions."""
    copro_id = txn.get("copropriete_id", "")
    if not copro_id:
        return None
    if not txn.get("matched"):
        return None
    amount = abs(float(txn.get("amount", 0) or 0))
    if amount <= 0:
        return None
    bank_acc = txn.get("account_number") or "550000"
    match_type = txn.get("match_type", "")
    txn_type = txn.get("transaction_type", "credit")
    is_credit = txn_type == "credit" or float(txn.get("amount", 0)) > 0

    counterpart_acc = ""
    counterpart_name = ""
    third_party_id = None
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
            sname = (inv.get("supplier") or "").strip()
            if sname:
                import re
                supplier = await db.suppliers.find_one(
                    {"name": {"$regex": f"^{re.escape(sname)}$", "$options": "i"}}, {"_id": 0}
                )
                if supplier:
                    supplier = await assign_supplier_account(db, supplier, copro_id)
                    counterpart_acc = get_supplier_account(supplier, copro_id)
                    counterpart_name = supplier.get("name", "")
                    third_party_id = supplier["id"]
    elif match_type == "supplier_payment":
        supplier = await db.suppliers.find_one({"id": txn.get("matched_to")}, {"_id": 0})
        if supplier:
            supplier = await assign_supplier_account(db, supplier, copro_id)
            counterpart_acc = get_supplier_account(supplier, copro_id)
            counterpart_name = supplier.get("name", "")
            third_party_id = supplier["id"]

    if not counterpart_acc:
        return None

    pcmn_q = {"number": {"$in": [bank_acc, counterpart_acc]}, "copropriete_id": copro_id}
    pcmns = await db.pcmn_accounts.find(pcmn_q, {"_id": 0}).to_list(10)
    pcmn_names = {p["number"]: p["name"] for p in pcmns}

    if is_credit:
        # Money in: Dr bank + Cr counterpart (owner pays / refund)
        lines = [
            {"account_number": bank_acc, "account_name": pcmn_names.get(bank_acc, "Banque"),
             "debit": amount, "credit": 0.0, "third_party_id": None, "third_party_name": ""},
            {"account_number": counterpart_acc, "account_name": pcmn_names.get(counterpart_acc, counterpart_name),
             "debit": 0.0, "credit": amount, "third_party_id": third_party_id, "third_party_name": counterpart_name},
        ]
    else:
        # Money out: Dr counterpart + Cr bank (supplier paid / refund owner)
        lines = [
            {"account_number": counterpart_acc, "account_name": pcmn_names.get(counterpart_acc, counterpart_name),
             "debit": amount, "credit": 0.0, "third_party_id": third_party_id, "third_party_name": counterpart_name},
            {"account_number": bank_acc, "account_name": pcmn_names.get(bank_acc, "Banque"),
             "debit": 0.0, "credit": amount, "third_party_id": None, "third_party_name": ""},
        ]

    await _delete_auto_entries(db, "bank_txn", txn["id"])
    doc = {
        "id": str(uuid.uuid4()),
        "journal_type": "FI",
        "date": txn.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "reference": f"FI-{txn['id'][:8]}",
        "description": f"{txn.get('counterparty_name','') or counterpart_name} - {txn.get('communication','')}".strip(" -"),
        "lines": lines,
        "total_debit": amount,
        "total_credit": amount,
        "copropriete_id": copro_id,
        "auto_generated": True,
        "source_type": "bank_txn",
        "source_id": txn["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.journal_entries.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}
