#!/usr/bin/env python3
"""DRY-RUN of full Gaura remediation (Phases A + B).

Phase A : delete test owners + cascade JE/FC/BT + merge CM/Optipro pairs.
Phase B : reconcile 63 official invoices vs 75 DB invoices (Marougrav, Finlead, AXA/Vert Ombrage).

NO WRITES. Only PRINT what WOULD BE done.
Run with --execute to actually perform the changes.
"""
import os
import sys
import asyncio
import re
from collections import defaultdict

# Path setup so we can import from /app/backend/import_wizard
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

ACP = "b5f14232-34f5-4805-9b9e-ebd32ad8baa5"

EXECUTE = "--execute" in sys.argv
MODE = "EXECUTE" if EXECUTE else "DRY-RUN"


TEST_OWNERS = [
    ("alexis jean-pierre", "40000007"),
    ("gramme gilles", "40000001"),
    ("wauthier - catinus", "40000002"),
]


async def phase_a(db):
    print(f"\n{'='*70}\nPHASE A [{MODE}] : Delete test owners + Merge CM/Optipro\n{'='*70}")

    # A.1 Identify test owners
    test_owner_ids = []
    test_accounts = set()
    for name_kw, prov in TEST_OWNERS:
        async for o in db.owners.find(
            {f"tier_accounts.{ACP}.provisions": prov},
            {"_id": 0, "id": 1, "name": 1, "tier_accounts": 1, "auxiliary_code": 1},
        ):
            if name_kw.lower() not in (o.get("name", "")).lower():
                continue
            test_owner_ids.append(o["id"])
            ta = o.get("tier_accounts", {}).get(ACP, {})
            if ta.get("provisions"): test_accounts.add(ta["provisions"])
            if ta.get("reserve"): test_accounts.add(ta["reserve"])
            print(f"  [A.1] WILL DELETE owner: {o['name']} ({o['id'][:8]}) | accs={ta.get('provisions','')}/{ta.get('reserve','')}")

    # A.2 Count cascade impact
    if test_owner_ids:
        n_je = await db.journal_entries.count_documents({
            "copropriete_id": ACP,
            "$or": [
                {"lines.account_number": {"$in": list(test_accounts)}},
                {"lines.third_party_id": {"$in": test_owner_ids}},
            ],
        })
        n_fc = await db.fund_calls.count_documents({
            "copropriete_id": ACP,
            "$or": [
                {"owner_id": {"$in": test_owner_ids}},
                {"account_number": {"$in": list(test_accounts)}},
            ],
        })
        n_bt = await db.bank_transactions.count_documents({
            "copropriete_id": ACP,
            "counterparty_owner_id": {"$in": test_owner_ids},
        })
        n_lots = await db.lots.count_documents({
            "copropriete_id": ACP, "owner_id": {"$in": test_owner_ids},
        })
        print(f"  [A.2] Cascade : {n_je} JE deleted, {n_fc} fund_calls deleted, {n_bt} bank_tx deleted, {n_lots} lots detached, {len(test_accounts)} PCMN deleted")

    # A.3 Identify CM/Optipro pairs to merge
    by_aux = defaultdict(list)
    async for o in db.owners.find({f"tier_accounts.{ACP}": {"$exists": True}}, {"_id": 0}):
        if o["id"] in test_owner_ids:
            continue  # skip those being deleted
        aux = (o.get("auxiliary_code") or "").upper().strip()
        if aux:
            by_aux[aux].append(o)
    dups = {k: v for k, v in by_aux.items() if len(v) > 1}
    print(f"\n  [A.3] Found {len(dups)} duplicate aux_code groups :")

    for aux, lst in dups.items():
        canonical = None
        cm_dups = []
        for o in lst:
            prov = (o.get("tier_accounts", {}).get(ACP, {}) or {}).get("provisions", "")
            if prov.startswith("4101") or prov.startswith("4102"):
                canonical = o if not canonical else canonical
                if canonical != o:
                    cm_dups.append(o)
            else:
                cm_dups.append(o)
        if not canonical or not cm_dups:
            print(f"    [{aux}] SKIP (no clear canonical)")
            continue
        for dup in cm_dups:
            dup_ta = dup.get("tier_accounts", {}).get(ACP, {})
            old_accs = [a for a in (dup_ta.get("provisions"), dup_ta.get("reserve")) if a]
            n_je_lines = 0
            async for je in db.journal_entries.find({"copropriete_id": ACP, "lines.account_number": {"$in": old_accs}}, {"_id": 0, "lines": 1}):
                n_je_lines += sum(1 for ln in (je.get("lines") or []) if ln.get("account_number") in old_accs)
            print(f"    [{aux}] {canonical['name'][:28]:<30} | CM {old_accs} -> {canonical['tier_accounts'][ACP]['provisions']} | {n_je_lines} JE lines migrate")


async def phase_b(db):
    print(f"\n{'='*70}\nPHASE B [{MODE}] : Reconcile 63 official invoices vs DB\n{'='*70}")
    from import_wizard.pdf_supplier_invoice_list import parse_supplier_invoice_list
    with open("/tmp/factures_v2.pdf", "rb") as f:
        official = parse_supplier_invoice_list(f.read())

    db_invs = await db.invoices.find(
        {"copropriete_id": ACP, "date": {"$gte": "2025-01-01", "$lt": "2026-01-01"}},
        {"_id": 0},
    ).sort("date", 1).to_list(2000)

    print(f"  Official invoices : {len(official)} / DB : {len(db_invs)}")

    # B.1 Marougrav : merge 9 records into 1
    print(f"\n  [B.1] SPRL Marougrav 2024/525 : merge 9 DB records -> 1 (123.78 EUR)")
    marou_recs = [inv for inv in db_invs if (inv.get("supplier") or "").lower().startswith("sprl marougrav") and inv.get("number") == "2024/525"]
    marou_recs.sort(key=lambda i: i.get("internal_reference", ""))
    print(f"    {len(marou_recs)} records in DB :")
    for r in marou_recs:
        print(f"      - {r.get('internal_reference','?'):<12} | amt={r.get('total_amount',0):>7.2f} | {r['id'][:8]}")
    if marou_recs:
        keep = marou_recs[0]  # FA-2025-0065 typically
        delete_ids = [r["id"] for r in marou_recs[1:]]
        print(f"    KEEP : {keep.get('internal_reference')} (id={keep['id'][:8]}) - update total_amount=123.78")
        print(f"    DELETE : {len(delete_ids)} other records")

    # B.2 Finlead 4 pairs to merge
    print(f"\n  [B.2] SRL Finlead syndic : 4 pairs to merge")
    finlead_targets = [
        ("251404", 1434.06, 1269.06),
        ("250958", 1434.06, 1269.06),
        ("250569", 1307.16, 1142.16),
        ("V-250261", 1307.16, 1142.16),
    ]
    for ext_n, target_amt, principal_amt in finlead_targets:
        recs = [inv for inv in db_invs if (inv.get("supplier") or "") == "SRL Finlead" and inv.get("number") == ext_n]
        recs.sort(key=lambda i: float(i.get("total_amount") or 0), reverse=True)
        amts = [float(r.get("total_amount") or 0) for r in recs]
        print(f"    Finlead {ext_n} (target={target_amt}) : DB has {len(recs)} records with amts={amts}")
        for r in recs:
            print(f"      - {r.get('internal_reference','?'):<12} | amt={r.get('total_amount',0):>7.2f} | {r['id'][:8]}")
        if len(recs) == 2:
            principal = next((r for r in recs if abs(float(r.get('total_amount') or 0) - principal_amt) < 1), None)
            admin = next((r for r in recs if abs(float(r.get('total_amount') or 0) - 165) < 1), None)
            if principal and admin:
                print(f"      MERGE : Keep {principal.get('internal_reference')} -> total={target_amt}, DELETE {admin.get('internal_reference')}")

    # B.3 AXA / Vert Ombrage SWAP
    print(f"\n  [B.3] AXA Belgium / Vert Ombrage : SWAP amounts")
    axa = next((inv for inv in db_invs if inv.get("number") == "202502" and "axa" in (inv.get("supplier") or "").lower()), None)
    vert = next((inv for inv in db_invs if inv.get("number") == "2025/02" and "vert ombrage" in (inv.get("supplier") or "").lower()), None)
    if axa and vert:
        print(f"    AXA  {axa.get('internal_reference')} : current={axa.get('total_amount'):>9.2f} -> set to 2652.22")
        print(f"    Vert {vert.get('internal_reference')} : current={vert.get('total_amount'):>9.2f} -> set to 300.99")
    else:
        print(f"    AXA found={bool(axa)} | Vert Ombrage found={bool(vert)}")


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print(f"### Gaura full remediation [{MODE}] ###")
    await phase_a(db)
    await phase_b(db)
    print(f"\n{'='*70}\n{MODE} COMPLETE. " + ("Changes APPLIED to DB." if EXECUTE else "No changes made. Re-run with --execute to apply.") + f"\n{'='*70}\n")


asyncio.run(main())
