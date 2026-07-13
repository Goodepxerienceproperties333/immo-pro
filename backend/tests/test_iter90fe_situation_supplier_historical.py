"""iter90fe : situation-de-compte fournisseur inclut les ecritures AC
dont le third_party_id ne matche pas (creees avant la fiche canonique)
en les retrouvant via source_invoice_id -> invoice.supplier normalise.
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
    cid = f"iter90fe-cid-{suffix}"
    sup_id = f"sup-{suffix}"
    inv_id_a = f"inv-a-{suffix}"
    inv_id_b = f"inv-b-{suffix}"
    je_id_a = f"je-a-{suffix}"
    je_id_b = f"je-b-{suffix}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90feACP", "status": "active",
    })
    await db.suppliers.insert_one({
        "id": sup_id,
        "name": f"Finlead {suffix} SRL",
        "copropriete_id": cid,
        "bce_number": f"BE0{suffix[:2]}00",
        "tier_accounts": {cid: {"main": "4400001"}},
    })
    await db.pcmn_accounts.insert_one({
        "number": "4400001", "name": "Tier fournisseur",
        "class_num": 4, "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_one({
        "number": "6140", "name": "Charge test",
        "class_num": 6, "copropriete_id": cid,
    })
    # Cas A : invoice avec supplier_id + AC entry avec third_party_id
    #         correct -> DOIT etre inclus (fonctionnement normal).
    await db.invoices.insert_one({
        "id": inv_id_a, "copropriete_id": cid,
        "supplier": f"Finlead {suffix} SRL", "supplier_id": sup_id,
        "number": "F-A-001", "date": "2026-01-10",
        "total_amount": 100.0,
    })
    await db.journal_entries.insert_one({
        "id": je_id_a, "journal_type": "AC", "date": "2026-01-10",
        "copropriete_id": cid, "source_invoice_id": inv_id_a,
        "reference": "F-A-001",
        "lines": [
            {"account_number": "6140", "debit": 100.0, "credit": 0.0},
            {"account_number": "4400001", "debit": 0.0, "credit": 100.0,
             "third_party_id": sup_id, "third_party_name": f"Finlead {suffix} SRL"},
        ],
        "total_debit": 100.0, "total_credit": 100.0,
    })
    # Cas B : invoice avec supplier libre "Finlead X Properties (Finlead X srl)"
    #         sans supplier_id, et AC entry sans third_party_id (legacy).
    #         DOIT etre inclus grace au fallback iter90fe.
    await db.invoices.insert_one({
        "id": inv_id_b, "copropriete_id": cid,
        "supplier": f"Finlead {suffix} Properties (Finlead {suffix} srl)",
        # NB : pas de supplier_id (legacy)
        "number": "F-B-001", "date": "2026-01-15",
        "total_amount": 200.0,
    })
    await db.journal_entries.insert_one({
        "id": je_id_b, "journal_type": "AC", "date": "2026-01-15",
        "copropriete_id": cid, "source_invoice_id": inv_id_b,
        "reference": "F-B-001",
        "lines": [
            {"account_number": "6140", "debit": 200.0, "credit": 0.0},
            # NB : pas de third_party_id, compte generic auto-cree, DIFFERENT
            # du tier 4400001 canonique du fournisseur.
            {"account_number": "4400099", "debit": 0.0, "credit": 200.0},
        ],
        "total_debit": 200.0, "total_credit": 200.0,
    })
    return db, cid, sup_id, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.suppliers.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.invoices.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def _endpoint(db, path):
    from routes.reports import create_reports_router
    router = create_reports_router(db)
    for r in router.routes:
        if r.path == path:
            return r.endpoint
    return None


def test_situation_compte_supplier_includes_historical_ac_entries():
    async def _run():
        db, cid, sup_id, suffix = await _setup()
        try:
            fn = _endpoint(db, "/api/reports/balance-tiers/suppliers/{supplier_id}")
            assert fn, "endpoint not found"
            result = await fn(supplier_id=sup_id, copropriete_id=cid)
            total_credit = float(result.get("total_credit") or 0)
            # Doit inclure 100 (cas A) + 200 (cas B fallback iter90fe) = 300
            assert abs(total_credit - 300.0) < 0.01, (
                f"iter90fe : attendu credit total 300.0 (100 direct + 200 via "
                f"source_invoice_id fallback), got {total_credit}"
            )
            movements = result.get("movements") or []
            refs = [m.get("reference") for m in movements]
            assert "F-A-001" in refs
            assert "F-B-001" in refs, (
                f"L'ecriture legacy sans third_party_id doit etre incluse via "
                f"le fallback source_invoice_id. Refs vus : {refs}"
            )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_situation_compte_supplier_includes_historical_ac_entries()
    print("OK")
