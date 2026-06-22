#!/usr/bin/env python3
"""EXECUTE full Gaura remediation (Phase A + B with Option 1).

Phase A : delete test owners + cascade, merge CM/Optipro pairs
Phase B : reconcile invoices (Marougrav merge 9->1, Finlead 4 pairs merge, AXA/Vert OK)

WRITES TO DB. Run after dry-run validation.
"""
import os
import sys
import asyncio
from collections import defaultdict

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

ACP = "b5f14232-34f5-4805-9b9e-ebd32ad8baa5"

TEST_OWNERS = [
    ("alexis jean-pierre", "40000007"),
    ("gramme gilles", "40000001"),
    ("wauthier - catinus", "40000002"),
]


async def phase_a_execute(db):
    print("\n=== PHASE A : Delete test owners + Merge CM/Optipro ===")
    test_owner_ids = []
    test_accounts = set()
    for name_kw, prov in TEST_OWNERS:
        async for o in db.owners.find(
            {f"tier_accounts.{ACP}.provisions": prov},
            {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1},
        ):
            if name_kw.lower() not in o.get("name", "").lower():
                continue
            test_owner_ids.append(o["id"])
            ta = o.get("tier_accounts", {}).get(ACP, {})
            if ta.get("provisions"):
                test_accounts.add(ta["provisions"])
            if ta.get("reserve"):
                test_accounts.add(ta["reserve"])

    if test_owner_ids:
        # Delete JEs that touch these accounts/ids (FI receipts are fictitious)
        je_q = {"copropriete_id": ACP, "$or": [
            {"lines.account_number": {"$in": list(test_accounts)}},
            {"lines.third_party_id": {"$in": test_owner_ids}},
        ]}
        rj = await db.journal_entries.delete_many(je_q)
        rfc = await db.fund_calls.delete_many({"copropriete_id": ACP, "$or": [
            {"owner_id": {"$in": test_owner_ids}},
            {"account_number": {"$in": list(test_accounts)}},
        ]})
        rbt = await db.bank_transactions.delete_many({"copropriete_id": ACP, "counterparty_owner_id": {"$in": test_owner_ids}})
        await db.lots.update_many(
            {"copropriete_id": ACP, "owner_id": {"$in": test_owner_ids}},
            {"$set": {"owner_id": ""}},
        )
        await db.invoices.update_many(
            {"copropriete_id": ACP, "private_fee_owner_id": {"$in": test_owner_ids}},
            {"$set": {"private_fee_owner_id": ""}},
        )
        rod = await db.owners.delete_many({"id": {"$in": test_owner_ids}})
        rpc_deleted = 0
        for acc in test_accounts:
            r = await db.pcmn_accounts.delete_one({"copropriete_id": ACP, "number": acc})
            rpc_deleted += r.deleted_count
        print(f"  A.1 : {rod.deleted_count} test owners deleted | {rj.deleted_count} JE | "
              f"{rfc.deleted_count} fund_calls | {rbt.deleted_count} bank_tx | {rpc_deleted} PCMN")

    # A.3 Merge remaining CM/Optipro pairs
    by_aux = defaultdict(list)
    async for o in db.owners.find({f"tier_accounts.{ACP}": {"$exists": True}}, {"_id": 0}):
        aux = (o.get("auxiliary_code") or "").upper().strip()
        if aux:
            by_aux[aux].append(o)
    dups = {k: v for k, v in by_aux.items() if len(v) > 1}
    n_merged = 0
    for aux, lst in dups.items():
        canonical = None
        cm_dups = []
        for o in lst:
            prov = (o.get("tier_accounts", {}).get(ACP, {}) or {}).get("provisions", "")
            if prov.startswith("4101") or prov.startswith("4102"):
                if not canonical:
                    canonical = o
                else:
                    cm_dups.append(o)
            else:
                cm_dups.append(o)
        if not canonical or not cm_dups:
            continue
        canonical_prov = canonical["tier_accounts"][ACP]["provisions"]
        for dup in cm_dups:
            dup_ta = dup.get("tier_accounts", {}).get(ACP, {})
            old_accs = [a for a in (dup_ta.get("provisions"), dup_ta.get("reserve")) if a]
            n_lines = 0
            async for je in db.journal_entries.find(
                {"copropriete_id": ACP, "$or": [
                    {"lines.account_number": {"$in": old_accs}},
                    {"lines.third_party_id": dup["id"]},
                ]},
                {"_id": 0},
            ):
                changed = False
                new_lines = []
                for ln in je.get("lines", []) or []:
                    nl = dict(ln)
                    if nl.get("account_number") in old_accs:
                        nl["account_number"] = canonical_prov
                        nl["account_name"] = f"Coproprietaires - {canonical.get('name', '')[:30]}"
                        nl["migrated_from_cm_account"] = ln.get("account_number")
                        changed = True
                        n_lines += 1
                    if nl.get("third_party_id") == dup["id"]:
                        nl["third_party_id"] = canonical["id"]
                        nl["third_party_type"] = "owner"
                        changed = True
                    new_lines.append(nl)
                if changed:
                    await db.journal_entries.update_one({"id": je["id"]}, {"$set": {"lines": new_lines}})
            # fund_calls
            await db.fund_calls.update_many(
                {"copropriete_id": ACP, "owner_id": dup["id"]},
                {"$set": {"owner_id": canonical["id"]}},
            )
            await db.fund_calls.update_many(
                {"copropriete_id": ACP, "account_number": {"$in": old_accs}},
                {"$set": {"account_number": canonical_prov}},
            )
            await db.lots.update_many({"copropriete_id": ACP, "owner_id": dup["id"]}, {"$set": {"owner_id": canonical["id"]}})
            await db.invoices.update_many({"copropriete_id": ACP, "private_fee_owner_id": dup["id"]}, {"$set": {"private_fee_owner_id": canonical["id"]}})
            await db.bank_transactions.update_many({"copropriete_id": ACP, "counterparty_owner_id": dup["id"]}, {"$set": {"counterparty_owner_id": canonical["id"]}})
            await db.owners.delete_one({"id": dup["id"]})
            for acc in old_accs:
                ncj = await db.journal_entries.count_documents({"copropriete_id": ACP, "lines.account_number": acc})
                if ncj == 0:
                    await db.pcmn_accounts.delete_one({"copropriete_id": ACP, "number": acc})
            n_merged += 1
            print(f"  A.3 [{aux}] merged {canonical['name'][:25]:<26} | {n_lines} JE lines migrated to {canonical_prov}")
    print(f"  A.3 Total merged : {n_merged} CM/Optipro pairs")


async def _delete_invoice_safely(db, invoice_id):
    """Delete an invoice + its auto-generated journal entries."""
    from auto_entries import _delete_auto_entries
    try:
        await _delete_auto_entries(db, "invoice", invoice_id)
    except Exception:
        pass
    await db.invoices.delete_one({"id": invoice_id})


async def _regenerate_auto_entry(db, invoice_id):
    """Regenerate the AC purchase entry for an invoice."""
    from auto_entries import generate_purchase_entry
    inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if inv:
        try:
            await generate_purchase_entry(db, inv)
        except Exception as e:
            print(f"    [auto-entry] regenerate failed for {invoice_id}: {e}")


async def phase_b_execute(db):
    print("\n=== PHASE B : Reconcile invoices ===")

    # B.1 Marougrav : Keep FA-2025-0065 with total=123.78, delete the 8 others
    print("\n  B.1 SPRL Marougrav 2024/525 : merge 9 records into 1")
    marou = await db.invoices.find(
        {"copropriete_id": ACP, "supplier": "SPRL Marougrav", "number": "2024/525"},
        {"_id": 0},
    ).sort("internal_reference", 1).to_list(20)
    if len(marou) == 9:
        keep = marou[0]  # FA-2025-0065
        # Update total + ensure private fee owner stays (or detach if shared)
        await db.invoices.update_one(
            {"id": keep["id"]},
            {"$set": {"total_amount": 123.78, "vat_amount": 0.0}},
        )
        # Delete the 8 others
        for r in marou[1:]:
            await _delete_invoice_safely(db, r["id"])
            print(f"    DELETED {r.get('internal_reference')}")
        # Regenerate auto-entry for the kept record with the new total
        await _regenerate_auto_entry(db, keep["id"])
        print(f"    KEPT {keep.get('internal_reference')} : total=123.78")
    else:
        print(f"    SKIP : found {len(marou)} records (expected 9)")

    # B.2 Finlead pairs
    finlead_targets = [
        ("251404", 1434.06, 1269.06),
        ("250958", 1434.06, 1269.06),
        ("250569", 1307.16, 1142.16),
        ("V-250261", 1307.16, 1142.16),
    ]
    print(f"\n  B.2 SRL Finlead syndic : 4 pairs to merge")
    for ext_n, target_amt, principal_amt in finlead_targets:
        recs = await db.invoices.find(
            {"copropriete_id": ACP, "supplier": "SRL Finlead", "number": ext_n},
            {"_id": 0},
        ).to_list(20)
        if len(recs) != 2:
            print(f"    SKIP {ext_n} : found {len(recs)} records (expected 2)")
            continue
        principal = next((r for r in recs if abs(float(r.get("total_amount") or 0) - principal_amt) < 1), None)
        admin = next((r for r in recs if abs(float(r.get("total_amount") or 0) - 165) < 1), None)
        if not principal or not admin:
            print(f"    SKIP {ext_n} : cannot identify principal/admin records")
            continue
        # Update principal total + delete admin
        await db.invoices.update_one(
            {"id": principal["id"]},
            {"$set": {"total_amount": target_amt}},
        )
        await _delete_invoice_safely(db, admin["id"])
        await _regenerate_auto_entry(db, principal["id"])
        print(f"    MERGED {ext_n} : kept {principal.get('internal_reference')} total={target_amt}, DELETED {admin.get('internal_reference')}")


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("### Gaura full remediation EXECUTE ###")
    await phase_a_execute(db)
    await phase_b_execute(db)

    # Final verification
    cnt = await db.invoices.count_documents({"copropriete_id": ACP, "date": {"$gte": "2025-01-01", "$lt": "2026-01-01"}})
    print(f"\n=== FINAL STATE ===")
    print(f"Total Gaura 2025 invoices : {cnt} (expected 63)")
    own = await db.owners.count_documents({f"tier_accounts.{ACP}": {"$exists": True}})
    print(f"Total Gaura owners with tier_accounts : {own}")


asyncio.run(main())
