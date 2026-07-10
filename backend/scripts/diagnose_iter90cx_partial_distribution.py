"""
iter90cx : Diagnostic des fund_calls dont la distribution stockee est
INCOMPLETE (non vide mais total distribue < total_amount de l'appel).

Contexte : Le repair iter90cw ne traitait que les distributions VIDES
(distribution == []). L'utilisateur signale un ecart de 3.6% entre le
montant appele (ex: 1500 EUR "Fonds de reserve") et le total effectivement
distribue dans les ecritures VE (1446 EUR). Ce script isole la cause exacte
sans rien modifier (lecture seule).

Usage (a executer sur PROD via la console Emergent) :

    cd /app/backend
    python scripts/diagnose_iter90cx_partial_distribution.py --name acacia
"""
import argparse
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _find_acp(db, copropriete_id: str = None, name: str = None):
    if copropriete_id:
        doc = await db.coproprietes.find_one({"id": copropriete_id}, {"_id": 0})
        if not doc:
            raise SystemExit(f"ACP {copropriete_id} introuvable")
        return doc
    q = {"name": {"$regex": name or "acacia", "$options": "i"}}
    docs = await db.coproprietes.find(q, {"_id": 0}).to_list(10)
    if not docs:
        raise SystemExit(f"Aucune ACP trouvee via name~{name}. Utilisez --copropriete-id.")
    if len(docs) > 1:
        print("[!] Plusieurs ACPs :")
        for d in docs:
            print(f"  - {d.get('id')} : {d.get('name')}")
        raise SystemExit("Precisez --copropriete-id.")
    return docs[0]


async def _run(copropriete_id: str = None, name: str = None):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    acp = await _find_acp(db, copropriete_id, name)
    cid = acp["id"]

    print(f"\n=== iter90cx : Diagnostic distributions partielles ACP : {acp.get('name')} (id={cid}) ===\n")

    all_calls = await db.fund_calls.find({"copropriete_id": cid}, {"_id": 0}).to_list(10000)
    print(f"Appels totaux ACP : {len(all_calls)}\n")

    lots = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(10000)
    lots_by_id = {lt["id"]: lt for lt in lots}
    keys = await db.distribution_keys.find({"copropriete_id": cid}, {"_id": 0}).to_list(1000)
    keys_map = {k["id"]: k for k in keys}

    suspects = []
    for c in all_calls:
        dist = c.get("distribution") or []
        total_dist = round(sum(float(d.get("amount", 0) or 0) for d in dist), 2)
        total_amount = round(float(c.get("total_amount", 0) or 0), 2)
        gap = round(total_amount - total_dist, 2)
        if dist and total_amount > 0 and abs(gap) > 0.05:
            suspects.append((c, total_dist, total_amount, gap))

    if not suspects:
        print("Aucune distribution partielle detectee (tolerance 0.05 EUR).")
        return

    print(f"--- {len(suspects)} appel(s) avec ecart de distribution ---\n")
    for c, total_dist, total_amount, gap in suspects:
        pct = round(100 * gap / total_amount, 2) if total_amount else 0
        print(f"[{c.get('name')}] date={c.get('date')} key_id={c.get('distribution_key_id')}")
        print(f"  total_amount appel  : {total_amount}")
        print(f"  total distribue     : {total_dist}")
        print(f"  ECART               : {gap} ({pct}%)")

        key = keys_map.get(c.get("distribution_key_id"))
        if key:
            all_kls = key.get("lots", [])
            excluded = [kl for kl in all_kls if kl.get("excluded")]
            missing_owner = [
                kl for kl in all_kls
                if not kl.get("excluded") and not lots_by_id.get(kl.get("lot_id"), {}).get("owner_id")
            ]
            phantom = [kl for kl in all_kls if kl.get("lot_id") not in lots_by_id]
            print(f"  cle '{key.get('name')}' : {len(all_kls)} lignes | "
                  f"excluded={len(excluded)} | sans owner={len(missing_owner)} | "
                  f"phantom(lot supprime)={len(phantom)}")
            for kl in excluded:
                lot = lots_by_id.get(kl.get("lot_id"))
                print(f"    - EXCLUDED lot={kl.get('lot_id')} share={kl.get('share')} "
                      f"(lot_number={lot.get('number') if lot else '?'})")
            for kl in missing_owner:
                print(f"    - SANS OWNER lot={kl.get('lot_id')} share={kl.get('share')}")
            for kl in phantom:
                print(f"    - PHANTOM (lot inexistant) lot_id={kl.get('lot_id')} share={kl.get('share')}")
        else:
            print(f"  [!] Cle {c.get('distribution_key_id')} introuvable en DB (phantom key totale)")

        # Cross-check VE existante
        ves = await db.journal_entries.find({
            "copropriete_id": cid, "source_type": "fund_call", "source_id": c["id"],
            "journal_type": "VE",
        }, {"_id": 0}).to_list(50)
        print(f"  VE(s) liee(s) : {len(ves)}")
        print()

    print("=== Fin du diagnostic (lecture seule, aucune modification). ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--copropriete-id", type=str, default=None)
    parser.add_argument("--name", type=str, default="acacia")
    args = parser.parse_args()

    asyncio.run(_run(copropriete_id=args.copropriete_id, name=args.name))
