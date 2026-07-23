"""
Script de contre-passation des 3 MUT-F Degrande Q4.
Utilise reverse_journal_entry pour preserver la piste d'audit.

Ecritures ciblees :
  206339d9-6f8e-454a-9ab7-5ff534010e28  MUT-302-F   350.17 EUR
  845f49e6-22f0-4536-83a1-a3e6507296ee  MUT-C10-F     5.17 EUR
  0b029162-8505-49ef-8a4a-ce486bf83006  MUT-Pe08-F   15.98 EUR

Effet attendu :
  Degrande  1 232.89 -> 861.57 EUR
  Matexi   -19.07   -> +352.25 EUR
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient
from journal_reversals import reverse_journal_entry

ENTRY_IDS = [
    "206339d9-6f8e-454a-9ab7-5ff534010e28",  # MUT-302-F  350.17
    "845f49e6-22f0-4536-83a1-a3e6507296ee",  # MUT-C10-F    5.17
    "0b029162-8505-49ef-8a4a-ce486bf83006",  # MUT-Pe08-F  15.98
]

REASON = "Correction Degrande Q4 : MUT-F en double (distribution deja mise a jour pour le nouvel acquereur)"


async def main():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME", "copro")]

    reversed_count = 0
    for eid in ENTRY_IDS:
        entry = await db.journal_entries.find_one({"id": eid}, {"_id": 0})
        if not entry:
            print(f"  [SKIP] {eid} - introuvable en base")
            continue
        if entry.get("reversed"):
            print(f"  [SKIP] {entry.get('reference',eid)} - deja contre-passee")
            continue
        if entry.get("is_reversal"):
            print(f"  [SKIP] {entry.get('reference',eid)} - c'est une contre-passation")
            continue

        rev = await reverse_journal_entry(db, entry, reason=REASON)
        if rev:
            print(
                f"  [OK]   {entry.get('reference','')} ({entry.get('total_debit',0):.2f} EUR) "
                f"-> contre-passation {rev['id'][:12]}..."
            )
            reversed_count += 1
        else:
            print(f"  [WARN] {entry.get('reference',eid)} - reverse_journal_entry a retourne None")

    print(f"\nTermine : {reversed_count}/{len(ENTRY_IDS)} ecritures contre-passees.")


if __name__ == "__main__":
    asyncio.run(main())
