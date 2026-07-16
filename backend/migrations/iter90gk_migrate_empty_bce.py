"""iter90gk : Migration des BCE fournisseurs legacy vides ("") -> absent (null unset).

Contexte : les fiches fournisseur legacy avaient toutes `bce_number=""` (chaine
vide). L'index MongoDB `sparse=True` ne les ignore pas car sparse ne skip que
les documents ou le CHAMP EST ABSENT, pas les valeurs vides. Meme avec
`partialFilterExpression` la coherence est meilleure si on unset les vides.

Ce script :
  1. Supprime le champ `bce_number` (via $unset) pour toutes les fiches ou
     `bce_number == ""` (ou est null/absent)
  2. Log le nombre de fiches migrees
  3. Verifie l'index unique BCE apres migration

Usage :
  python -m migrations.iter90gk_migrate_empty_bce            # dry-run
  python -m migrations.iter90gk_migrate_empty_bce --apply   # applique
"""
import asyncio
import argparse
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Compte le nombre de fiches concernees
    count_empty = await db.suppliers.count_documents({"bce_number": ""})
    count_missing = await db.suppliers.count_documents({"bce_number": {"$exists": False}})
    count_with = await db.suppliers.count_documents(
        {"bce_number": {"$type": "string", "$ne": ""}}
    )
    total = await db.suppliers.count_documents({})
    print(f"Total suppliers : {total}")
    print(f"  bce_number='' (vide)     : {count_empty}")
    print(f"  bce_number absent        : {count_missing}")
    print(f"  bce_number renseigne     : {count_with}")

    if args.apply and count_empty > 0:
        result = await db.suppliers.update_many(
            {"bce_number": ""},
            {"$unset": {"bce_number": ""}},
        )
        print(f"\n[APPLY] Migre {result.modified_count} fiches (bce_number '' -> absent)")

    # Verifie l'index apres migration
    print("\nIndexes suppliers.bce_number :")
    async for i in db.suppliers.list_indexes():
        if "bce" in i.get("name", ""):
            print(f"  {i.get('name')} key={i.get('key')} unique={i.get('unique')} pFE={i.get('partialFilterExpression')}")


if __name__ == "__main__":
    asyncio.run(main())
