"""iter90ey : facture multi-lignes -> chaque ligne peut avoir sa propre
repartition Occupant/Proprietaire. Le journal genere doit refleter
ces pourcentages par ligne, et les rapports (decompte locataire /
liste des depenses) les consommer correctement.
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")


async def _setup():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    suffix = uuid.uuid4().hex[:6]
    cid = f"iter90ey-cid-{suffix}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90eyACP", "reference": f"T90ey{suffix}",
        "status": "active",
    })
    accounts = [
        ("6141", "RC copro", 6),
        ("6140", "Assurance incendie", 6),
        ("4400001", "Fournisseur Xyz", 4),
    ]
    for num, name, cls in accounts:
        await db.pcmn_accounts.insert_one({
            "number": f"{num}{suffix[:2]}", "name": name, "class_num": cls,
            "copropriete_id": cid,
        })
    return db, cid, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.expense_categories.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})


def test_line_level_occupant_pct_reflected_in_journal():
    """Ligne A (RC copro) : 100% proprio ; Ligne B (Incendie) : 70/30
    Occupant/Proprio -> le journal genere doit avoir ces valeurs sur
    les debits correspondants."""
    async def _run():
        db, cid, suffix = await _setup()
        try:
            from auto_entries import generate_purchase_entry
            invoice = {
                "id": f"inv-{suffix}",
                "copropriete_id": cid,
                "supplier": f"AGF Assur {suffix}",
                "number": f"F-{suffix}",
                "date": "2026-02-14",
                "total_amount": 100.0,
                "description": "Prime assurance",
                # global : 100% proprio (fallback si ligne = None)
                "occupant_pct": 0.0,
                "proprietaire_pct": 100.0,
                "lines": [
                    # Ligne A : 100% proprio explicitement
                    {"account_number": f"6141{suffix[:2]}", "amount": 40.0,
                     "description": "RC copro",
                     "occupant_pct": 0.0, "proprietaire_pct": 100.0},
                    # Ligne B : 70% occupant, 30% proprio
                    {"account_number": f"6140{suffix[:2]}", "amount": 60.0,
                     "description": "Incendie parties privees",
                     "occupant_pct": 70.0, "proprietaire_pct": 30.0},
                ],
            }
            je = await generate_purchase_entry(db, invoice)
            assert je is not None
            debits = [ln for ln in je["lines"] if ln.get("debit", 0) > 0]
            assert len(debits) == 2, f"attendu 2 debits, got {len(debits)}"
            by_acc = {ln["account_number"]: ln for ln in debits}
            a = by_acc.get(f"6141{suffix[:2]}")
            b = by_acc.get(f"6140{suffix[:2]}")
            assert a is not None and b is not None
            assert a["occupant_pct"] == 0.0
            assert a["proprietaire_pct"] == 100.0
            assert b["occupant_pct"] == 70.0
            assert b["proprietaire_pct"] == 30.0
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_line_level_none_falls_back_to_invoice_global():
    """Ligne sans occupant_pct/proprietaire_pct -> herite du niveau facture."""
    async def _run():
        db, cid, suffix = await _setup()
        try:
            from auto_entries import generate_purchase_entry
            invoice = {
                "id": f"inv-{suffix}",
                "copropriete_id": cid,
                "supplier": f"XYZ {suffix}",
                "number": f"F-{suffix}",
                "date": "2026-02-14",
                "total_amount": 50.0,
                "description": "test",
                "occupant_pct": 50.0,  # global 50/50
                "proprietaire_pct": 50.0,
                "lines": [
                    # Ligne sans pct -> herite 50/50
                    {"account_number": f"6141{suffix[:2]}", "amount": 25.0,
                     "description": "A"},
                    # Ligne avec override 100/0
                    {"account_number": f"6140{suffix[:2]}", "amount": 25.0,
                     "description": "B",
                     "occupant_pct": 100.0, "proprietaire_pct": 0.0},
                ],
            }
            je = await generate_purchase_entry(db, invoice)
            assert je is not None
            debits = [ln for ln in je["lines"] if ln.get("debit", 0) > 0]
            by_acc = {ln["account_number"]: ln for ln in debits}
            a = by_acc[f"6141{suffix[:2]}"]
            b = by_acc[f"6140{suffix[:2]}"]
            assert a["occupant_pct"] == 50.0, f"heritage global attendu 50, got {a['occupant_pct']}"
            assert a["proprietaire_pct"] == 50.0
            assert b["occupant_pct"] == 100.0, f"override attendu 100, got {b['occupant_pct']}"
            assert b["proprietaire_pct"] == 0.0
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_line_level_occupant_pct_reflected_in_journal()
    test_line_level_none_falls_back_to_invoice_global()
    print("OK")
