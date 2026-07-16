"""iter90gk : Diagnostic des doublons BCE fournisseurs.

Identifie les paires (ou triplets) de suppliers ayant le meme BCE et affiche
leurs metadonnees pour permettre au syndic de decider quel fournisseur garder
comme "master".
"""
import asyncio
import os
import sys
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Regroupe par BCE normalise
    from routes.suppliers import _norm_id
    by_bce = defaultdict(list)
    async for s in db.suppliers.find(
        {"bce_number": {"$type": "string", "$ne": ""}}, {"_id": 0}
    ):
        norm = _norm_id(s.get("bce_number", ""))
        if norm:
            by_bce[norm].append(s)

    print("=== BCE DUPLICATES ===\n")
    for bce, sups in by_bce.items():
        if len(sups) <= 1:
            continue
        print(f"BCE={bce} - {len(sups)} fiches:")
        for s in sups:
            copro = ""
            if s.get("copropriete_id"):
                c = await db.coproprietes.find_one({"id": s["copropriete_id"]}, {"_id": 0, "name": 1})
                copro = f"[{c['name']}]" if c else "[?]"
            # Compte les usages
            invoices = await db.invoices.count_documents({"supplier_id": s["id"]})
            je_count = await db.journal_entries.count_documents({"lines.third_party_id": s["id"]})
            tiers = list((s.get("tier_accounts") or {}).items())
            print(f"  id={s['id'][:12]} name={s.get('name'):40s} {copro}")
            print(f"    email={s.get('email','')!r}  tel={s.get('phone','')!r}  iban={s.get('iban','')!r}")
            print(f"    created_at={s.get('created_at','')}")
            print(f"    tier_accounts={tiers}")
            print(f"    invoices_using={invoices}  journal_entries_using={je_count}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
