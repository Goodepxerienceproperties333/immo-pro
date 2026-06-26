"""Migration : seed des natures de depense par defaut sur toutes les ACPs existantes.

Pour chaque ACP en base, insere les natures de la liste
`default_expense_natures.DEFAULT_EXPENSE_NATURES` dont le `account_number`
n'est pas deja attribue a une autre nature dans cette ACP (respect de la
contrainte 1:1 nature<->compte).

Idempotent : peut etre relance sans risque.

Usage :
    python /app/backend/scripts/seed_default_expense_natures_existing_acps.py
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from default_expense_natures import DEFAULT_EXPENSE_NATURES


async def run(dry_run: bool = False):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    coproprietes = await db.coproprietes.find(
        {}, {"_id": 0, "id": 1, "name": 1, "reference": 1}
    ).to_list(1000)

    total_inserted = 0
    total_skipped = 0
    summary = []
    for c in coproprietes:
        copro_id = c["id"]
        existing = await db.expense_categories.find(
            {"copropriete_id": copro_id}, {"_id": 0, "account_number": 1}
        ).to_list(1000)
        used_accounts = {e.get("account_number") for e in existing}

        pcmn = await db.pcmn_accounts.find(
            {"copropriete_id": copro_id}, {"_id": 0, "number": 1, "name": 1}
        ).to_list(2000)
        pcmn_map = {p["number"]: p.get("name", "") for p in pcmn}

        now_iso = datetime.now(timezone.utc).isoformat()
        docs = []
        skipped = []
        for nat in DEFAULT_EXPENSE_NATURES:
            if nat["account_number"] in used_accounts:
                skipped.append(nat["account_number"])
                continue
            docs.append({
                "id": str(uuid.uuid4()),
                "code": nat["code"],
                "name": nat["name"],
                "label": nat["name"],
                "account_number": nat["account_number"],
                "account_name": pcmn_map.get(nat["account_number"], nat["name"]),
                "vat_code": nat.get("vat_code", ""),
                "default_occupant_pct": float(nat["default_occupant_pct"]),
                "default_proprietaire_pct": float(nat["default_proprietaire_pct"]),
                "kind": nat.get("kind", "charge"),
                "is_default_seed": True,
                "copropriete_id": copro_id,
                "created_at": now_iso,
            })

        n_ins = 0
        if docs and not dry_run:
            await db.expense_categories.insert_many(docs)
            n_ins = len(docs)
        elif docs:
            n_ins = len(docs)
        total_inserted += n_ins
        total_skipped += len(skipped)
        summary.append(
            f"  - {c.get('reference','?'):>15s}  {c['name']:30s}  "
            f"inserted={len(docs):>2}  skipped(existing)={len(skipped):>2}"
        )

    print(f"\n{'DRY-RUN ' if dry_run else ''}Seed des natures par defaut sur {len(coproprietes)} ACP(s) :")
    for line in summary:
        print(line)
    print(f"\nTotal inserted : {total_inserted}")
    print(f"Total skipped (compte deja utilise) : {total_skipped}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry))
