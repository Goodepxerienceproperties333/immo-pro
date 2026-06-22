#!/usr/bin/env python3
"""Restore all ACPs after the aggressive `dedup_owners.py` purge from iter72ter.

The previous dedup script deleted ACP-specific copies of owners (e.g. the 4
Optipro apartments LENOTRE-MANSART, RUBENS-RENOIR, VELASQUEZ-GOYA,
RAPHAEL-MICHEL ANGE were duplicated PER-ACP - import, BIS, TER each had its
own copy with a different UUID). The dedup kept only ONE canonical (the
import one for C0960..C0963) and deleted the rest, leaving the other ACPs
with orphan `owner_id` / `third_party_id` references.

This script :
1. For each ACP, finds orphan `lots.owner_id` (references missing owners).
   Uses lot.number to identify the canonical (e.g. 'Lots Le Nôtre-Mansart'
   -> C0960 canonical).
2. For each ACP, finds orphan `journal_entries.lines.third_party_id`.
   Uses line.account_number (4100XXX -> owner aux CXXXX, 4400XXX -> supplier
   aux FXXXX) to identify the canonical.
3. Updates all references to point to the canonical.
4. Adds the ACP to the canonical's `copropriete_ids` + ensures
   `tier_accounts[ACP]` is configured properly.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


# Map for the 4 Optipro apartments (number -> aux_code)
APARTMENT_MAP = {
    "Lots Le Nôtre-Mansart": "C0960",
    "Lots Raphael-Michel- ange": "C0961",
    "Lots Rubens-Renoir": "C0962",
    "Lots Velasquez-Goya": "C0963",
}


def _aux_from_account(acc: str) -> tuple[str, str] | None:
    """Map a tier account number to (entity_type, aux_code).
    410XXXX (7 chars) -> owner CXXXX
    440XXXX (7 chars) -> supplier FXXXX
    44000XXX (8 chars CM-style) -> supplier FXXX
    40000XXX (8 chars CM-style) -> owner provisions (no direct aux match)
    """
    if not acc or not acc.isdigit():
        return None
    if acc.startswith("4100") and len(acc) == 7:
        # Owner Optipro : 4100960 -> C0960
        return ("owner", "C" + acc[3:])
    if acc.startswith("4400") and len(acc) == 7:
        # Supplier Optipro : 4400471 -> F0471
        return ("supplier", "F" + acc[3:])
    if acc.startswith("44000") and len(acc) == 8:
        # Supplier CM : 44000471 -> F0471
        return ("supplier", "F" + acc[4:])
    if acc.startswith("40000") and len(acc) == 8:
        # CM owner provisions : no auxiliary -> can't match by account
        return None
    return None


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    print("=== Restore orphan owner/supplier refs across all ACPs ===\n")

    # Build lookup : aux_code -> canonical doc
    owner_by_aux = {}
    async for o in db.owners.find({"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0}):
        aux = (o.get("auxiliary_code") or "").upper().strip()
        if aux and aux not in owner_by_aux:
            owner_by_aux[aux] = o

    supplier_by_aux = {}
    async for s in db.suppliers.find({"auxiliary_code": {"$exists": True, "$ne": ""}}, {"_id": 0}):
        aux = (s.get("auxiliary_code") or "").upper().strip()
        if aux and aux not in supplier_by_aux:
            supplier_by_aux[aux] = s

    print(f"Lookup built : {len(owner_by_aux)} owners + {len(supplier_by_aux)} suppliers by aux_code\n")

    total_lots_relinked = 0
    total_je_lines_relinked = 0
    total_copro_added = 0
    total_tier_set = 0

    async for cop in db.coproprietes.find({}, {"_id": 0}):
        ACP = cop["id"]
        print(f"\n--- ACP {ACP[:8]} {cop['name']} ---")

        # 1. Lots with orphan owner_id
        orphan_lots = []
        async for lot in db.lots.find({"copropriete_id": ACP, "owner_id": {"$ne": ""}}, {"_id": 0}):
            oid = lot.get("owner_id", "")
            if not oid:
                continue
            exists = await db.owners.count_documents({"id": oid})
            if not exists:
                orphan_lots.append(lot)
        if orphan_lots:
            print(f"  {len(orphan_lots)} orphan lots :")
            for lot in orphan_lots:
                num = lot.get("number", "")
                # Match via apartment_map first
                target_aux = APARTMENT_MAP.get(num)
                target_owner = owner_by_aux.get(target_aux) if target_aux else None
                if target_owner:
                    await db.lots.update_one(
                        {"id": lot["id"]},
                        {"$set": {"owner_id": target_owner["id"], "owner_ids": [target_owner["id"]]}},
                    )
                    total_lots_relinked += 1
                    print(f"    {num} -> {target_aux} {target_owner['name']} ({target_owner['id'][:8]})")
                else:
                    print(f"    {num!r} -> NO MATCH (kept orphan)")

        # 2. JEs with orphan third_party_id
        # Collect distinct orphan tpids in this ACP and their associated account_number
        orphan_tpid_to_acc = {}  # tpid -> {accounts_seen}
        async for je in db.journal_entries.find({"copropriete_id": ACP}, {"_id": 0}):
            for ln in je.get("lines", []) or []:
                tpid = ln.get("third_party_id")
                if not tpid:
                    continue
                exists = await db.owners.count_documents({"id": tpid})
                if exists:
                    continue
                exists_sup = await db.suppliers.count_documents({"id": tpid})
                if exists_sup:
                    continue
                acc = ln.get("account_number", "")
                orphan_tpid_to_acc.setdefault(tpid, set()).add(acc)

        if orphan_tpid_to_acc:
            print(f"  {len(orphan_tpid_to_acc)} orphan tpids in JE lines :")
            # Map each orphan tpid to a canonical via account
            tpid_to_canonical = {}
            for tpid, accs in orphan_tpid_to_acc.items():
                for acc in accs:
                    mapping = _aux_from_account(acc)
                    if not mapping:
                        continue
                    entity_type, aux = mapping
                    if entity_type == "owner":
                        c_doc = owner_by_aux.get(aux)
                    else:
                        c_doc = supplier_by_aux.get(aux)
                    if c_doc:
                        tpid_to_canonical[tpid] = (entity_type, c_doc, acc)
                        break  # first valid map

            # Now rewrite all JE lines
            relinked_je = 0
            async for je in db.journal_entries.find({"copropriete_id": ACP}, {"_id": 0}):
                new_lines = []
                changed = False
                for ln in je.get("lines", []) or []:
                    nl = dict(ln)
                    tpid = nl.get("third_party_id")
                    if tpid and tpid in tpid_to_canonical:
                        entity_type, c_doc, _acc = tpid_to_canonical[tpid]
                        nl["third_party_id"] = c_doc["id"]
                        nl["third_party_type"] = entity_type
                        changed = True
                        total_je_lines_relinked += 1
                    new_lines.append(nl)
                if changed:
                    await db.journal_entries.update_one(
                        {"id": je["id"]},
                        {"$set": {"lines": new_lines, "manually_relinked_from_orphan": True}},
                    )
                    relinked_je += 1
            print(f"    -> {relinked_je} JEs updated, {total_je_lines_relinked} lines so far")
            for tpid, (et, c_doc, acc) in tpid_to_canonical.items():
                print(f"    {tpid[:8]} ({acc}) -> {et} {c_doc.get('auxiliary_code','')} {c_doc.get('name','')[:30]}")

        # 3. Add ACP to canonical owner/supplier copropriete_ids + tier_accounts
        # For owners : the lots+JE refs now use canonical IDs. We need to ensure
        # the canonical has `tier_accounts[ACP]` set properly so that the
        # balance-tiers endpoint resolves them.
        # Iterate over all lots in this ACP and ensure each owner has tier_accounts.
        owner_ids_in_acp = set()
        for oid in await db.lots.distinct("owner_id", {"copropriete_id": ACP}):
            if oid:
                owner_ids_in_acp.add(oid)
        # Also include from JE
        for tpid in await db.journal_entries.distinct("lines.third_party_id", {"copropriete_id": ACP}):
            if tpid:
                # only add if it's an owner
                if await db.owners.count_documents({"id": tpid}):
                    owner_ids_in_acp.add(tpid)

        for oid in owner_ids_in_acp:
            o = await db.owners.find_one({"id": oid})
            if not o:
                continue
            # Ensure copropriete_ids includes ACP
            if ACP not in (o.get("copropriete_ids") or []):
                await db.owners.update_one({"id": oid}, {"$addToSet": {"copropriete_ids": ACP}})
                total_copro_added += 1
            # Ensure tier_accounts[ACP] is set : derive from auxiliary_code (CXXXX -> 4100XXX)
            ta = (o.get("tier_accounts") or {}).get(ACP, {})
            if not ta.get("provisions"):
                aux = (o.get("auxiliary_code") or "").upper().strip()
                if aux.startswith("C") and len(aux) == 5 and aux[1:].isdigit():
                    prov_acc = "410" + aux[1:]  # C0960 -> 4100960 (7 chars)
                    await db.owners.update_one(
                        {"id": oid},
                        {"$set": {f"tier_accounts.{ACP}.provisions": prov_acc,
                                  f"tier_accounts.{ACP}.aliases": [prov_acc]}},
                    )
                    total_tier_set += 1
                    print(f"  tier_accounts[{ACP[:8]}] set for {aux} {o['name']} -> {prov_acc}")

        # Same for suppliers : detect supplier_ids referenced in this ACP
        supplier_ids_in_acp = set()
        for tpid in await db.journal_entries.distinct("lines.third_party_id", {"copropriete_id": ACP}):
            if tpid and await db.suppliers.count_documents({"id": tpid}):
                supplier_ids_in_acp.add(tpid)
        for inv in await db.invoices.find({"copropriete_id": ACP, "supplier_id": {"$ne": ""}}).distinct("supplier_id"):
            if inv:
                supplier_ids_in_acp.add(inv)

        for sid in supplier_ids_in_acp:
            s = await db.suppliers.find_one({"id": sid})
            if not s:
                continue
            ta = (s.get("tier_accounts") or {}).get(ACP, {})
            if not ta.get("main"):
                aux = (s.get("auxiliary_code") or "").upper().strip()
                if aux.startswith("F") and len(aux) == 5 and aux[1:].isdigit():
                    main_acc = "440" + aux[1:]  # F0471 -> 4400471 (7 chars)
                    await db.suppliers.update_one(
                        {"id": sid},
                        {"$set": {f"tier_accounts.{ACP}.main": main_acc}},
                    )
                    total_tier_set += 1
                    print(f"  supplier tier_accounts[{ACP[:8]}] set for {aux} {s['name']} -> {main_acc}")

    print(f"\n\n=== SUMMARY ===")
    print(f"Lots relinked : {total_lots_relinked}")
    print(f"JE lines relinked : {total_je_lines_relinked}")
    print(f"copropriete_ids additions : {total_copro_added}")
    print(f"tier_accounts entries set : {total_tier_set}")


asyncio.run(main())
