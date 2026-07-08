import asyncio, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    acp = await db.coproprietes.find_one({"name": {"$regex": "acacia", "$options": "i"}})
    if not acp:
        print("ACP Acacia non trouvee dans PREVIEW")
        # List available ACPs
        acps = await db.coproprietes.find({}, {"name": 1}).to_list(50)
        print(f"ACPs presentes: {[a.get('name') for a in acps]}")
        client.close()
        return
    print(f"ACP: {acp['name']} id={acp['id']}\n")

    # Distribution keys
    keys = await db.distribution_keys.find({"copropriete_id": acp['id']}).to_list(50)
    print(f"=== Distribution keys ({len(keys)}):")
    for k in keys:
        excl = k.get('excluded_lots') or []
        print(f"  - {k.get('name'):30} id={k.get('id')[:8]} type={k.get('key_type','?'):15} excluded_lots={len(excl)}")

    # Owners
    owners = await db.owners.find({"copropriete_ids": acp['id']}).to_list(100)
    print(f"\n=== Owners ({len(owners)}):")
    for o in owners:
        print(f"  - {o.get('name','?'):40} id={o.get('id')[:8]}")

    # Lots
    lots = await db.lots.find({"copropriete_id": acp['id']}).to_list(500)
    total_quot = sum(float(lt.get("quotity", 0) or 0) for lt in lots)
    print(f"\n=== Lots ({len(lots)}) - Total quotity: {total_quot:.4f}")
    unassigned = [lt for lt in lots if not lt.get("owner_id")]
    print(f"  Unassigned: {len(unassigned)}")
    # Group by owner
    by_owner = {}
    for lt in lots:
        oid = lt.get("owner_id") or "UNASSIGNED"
        by_owner.setdefault(oid, {"quot": 0, "count": 0, "numbers": []})
        by_owner[oid]["quot"] += float(lt.get("quotity", 0) or 0)
        by_owner[oid]["count"] += 1
        by_owner[oid]["numbers"].append(lt.get("lot_number") or lt.get("number"))
    for oid, info in sorted(by_owner.items(), key=lambda x: -x[1]["quot"]):
        oname = "UNASSIGNED"
        if oid != "UNASSIGNED":
            o = await db.owners.find_one({"id": oid}, {"name": 1})
            oname = o.get("name") if o else "??"
        pct = 100 * info["quot"] / total_quot if total_quot else 0
        print(f"  {oname:40} - {info['count']:3} lots, quot={info['quot']:>10.4f} ({pct:.2f}%)")

    # Fund calls
    fcs = await db.fund_calls.find({"copropriete_id": acp['id']}).sort("date", 1).to_list(100)
    print(f"\n=== Fund calls ({len(fcs)}):")
    for fc in fcs:
        total = fc.get("total_amount", 0)
        dist = fc.get("distribution") or []
        dist_sum = sum(float(d.get("amount", 0) or 0) for d in dist)
        print(f"  {fc.get('date','?')} | {fc.get('name','?'):45} | call_type={fc.get('call_type','?'):12} | total={total:>10.2f} | dist_sum={dist_sum:>10.2f} | diff={total - dist_sum:>7.2f} | N={len(dist)} | key={fc.get('distribution_key_id','?')[:8] if fc.get('distribution_key_id') else 'None'}")
        # Detail per lot (only distribution entries with amount > 0)
        for d in dist:
            oid = d.get("owner_id")
            oname = "NULL"
            if oid:
                o = await db.owners.find_one({"id": oid}, {"name": 1})
                oname = o.get("name") if o else "??"
            print(f"      lot={d.get('lot_number','?'):6} owner={oname[:30]:30} share={d.get('share','?'):.4f} amount={d.get('amount',0):>10.4f}")

    # Journal entries VE and OD source_type=lot_mutation pour Matexi
    matexi_id = None
    for o in owners:
        if 'matexi' in (o.get('name') or '').lower():
            matexi_id = o.get('id')
            break
    if matexi_id:
        print(f"\n=== Journal entries pour Matexi (id={matexi_id[:8]}):")
        # All entries with lines.third_party_id = Matexi
        entries = await db.journal_entries.find({
            "copropriete_id": acp['id'],
            "lines.third_party_id": matexi_id,
        }).sort("date", 1).to_list(1000)
        print(f"  Total entries: {len(entries)}")
        # Grouper par (date, journal_type, description prefix)
        for e in entries:
            for ln in e.get("lines", []):
                if ln.get("third_party_id") == matexi_id:
                    d = float(ln.get("debit", 0) or 0)
                    c = float(ln.get("credit", 0) or 0)
                    if d > 0 or c > 0:
                        desc = (ln.get("line_description") or e.get("description") or "")[:60]
                        acc = ln.get("account_number")
                        rev = "REV" if e.get("reversed") else ("IS_REV" if e.get("is_reversal") else "OK")
                        print(f"    {e.get('date')} | {e.get('journal_type'):3} | {acc:8} | D={d:>10.2f} C={c:>10.2f} | {rev:6} | {desc}")

    client.close()


asyncio.run(main())
