"""iter90fh : le bilan DOIT etre equilibre (Actif = Passif) meme en
presence de contre-passations partielles ou d'ecritures speciales.

Bug reproduit :
- Ecriture originale AC : Debit 6141 100 / Credit 4400001 100
- Ecriture reversee (is_reversal=True + reversed=True sur source)
- Avant fix : bilan calculait le resultat DEPUIS des entries qui ne
  filtraient PAS les reversals -> Actif != Passif.
- Apres fix : entries et resultat calcules depuis LA MEME source
  -> equation mathematique garantie par double-entree.
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
    cid = f"iter90fh-cid-{suffix}"
    fy_id = f"fy-{suffix}"

    await db.coproprietes.insert_one({
        "id": cid, "name": "Iter90fhACP", "status": "active",
    })
    await db.fiscal_years.insert_one({
        "id": fy_id, "copropriete_id": cid, "name": "2025-2026",
        "start_date": "2025-10-01", "end_date": "2026-09-30", "status": "active",
    })
    for num, name, cls in [
        ("6141", "RC copro", 6),
        ("4400001", "Fournisseur X", 4),
        ("55000001", "Banque", 5),
        ("70100", "Appels de fonds", 7),
        ("400001", "Owner A", 4),
        ("100000", "Fonds roulement", 1),
    ]:
        await db.pcmn_accounts.insert_one({
            "number": num, "name": name, "class_num": cls, "copropriete_id": cid,
        })

    # Ecriture originale (achat facture) - marquee reversed=True
    ac_id = f"ac-{suffix}"
    await db.journal_entries.insert_one({
        "id": ac_id, "journal_type": "AC", "date": "2026-01-15",
        "copropriete_id": cid, "fiscal_year_id": fy_id,
        "reference": f"F-{suffix}",
        "lines": [
            {"account_number": "6141", "debit": 100.0, "credit": 0.0},
            {"account_number": "4400001", "debit": 0.0, "credit": 100.0},
        ],
        "total_debit": 100.0, "total_credit": 100.0,
        "reversed": True,  # <-- ecriture extournee
    })
    # Contre-passation
    await db.journal_entries.insert_one({
        "id": f"rev-{suffix}", "journal_type": "OD", "date": "2026-01-20",
        "copropriete_id": cid, "fiscal_year_id": fy_id,
        "reference": f"EXT-{suffix}",
        "is_reversal": True,
        "reverses_entry_id": ac_id,
        "lines": [
            {"account_number": "4400001", "debit": 100.0, "credit": 0.0},
            {"account_number": "6141", "debit": 0.0, "credit": 100.0},
        ],
        "total_debit": 100.0, "total_credit": 100.0,
    })
    # Autre ecriture NORMALE (non reversee) : encaissement d'un appel de fonds
    await db.journal_entries.insert_one({
        "id": f"fi-{suffix}", "journal_type": "FI", "date": "2026-01-10",
        "copropriete_id": cid, "fiscal_year_id": fy_id,
        "reference": f"BQ-{suffix}",
        "lines": [
            {"account_number": "55000001", "debit": 500.0, "credit": 0.0},
            {"account_number": "70100", "debit": 0.0, "credit": 500.0},
        ],
        "total_debit": 500.0, "total_credit": 500.0,
    })
    return db, cid, fy_id, suffix


async def _cleanup(db, cid):
    await db.coproprietes.delete_one({"id": cid})
    await db.fiscal_years.delete_many({"copropriete_id": cid})
    await db.pcmn_accounts.delete_many({"copropriete_id": cid})
    await db.journal_entries.delete_many({"copropriete_id": cid})


def _endpoint(db, path):
    from routes.reports import create_reports_router
    router = create_reports_router(db)
    for r in router.routes:
        if r.path == path:
            return r.endpoint
    return None


def test_bilan_actif_equals_passif_with_reversals():
    async def _run():
        db, cid, fy_id, suffix = await _setup()
        try:
            fn = _endpoint(db, "/api/reports/bilan")
            assert fn, "endpoint /api/reports/bilan not found"
            # request est optionnel (juste utilise pour lire user pref) -> None ok
            result = await fn(request=None, copropriete_id=cid, fiscal_year_id=fy_id)
            total_actif = float(result.get("total_actif") or 0)
            total_passif = float(result.get("total_passif") or 0)
            ecart = round(total_actif - total_passif, 2)
            assert abs(ecart) < 0.01, (
                f"iter90fh : bilan DOIT etre equilibre. "
                f"Actif={total_actif} Passif={total_passif} Ecart={ecart}"
            )
            assert result.get("equilibre") is True
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


def test_bilan_actif_equals_passif_after_distribution():
    """En mode 'apres repartition', les charges/produits sont redistribues
    aux copropros. Ici on veut juste verifier qu'il n'y a plus d'ecart
    lie a la desynchronisation entries/entries_res. On simule donc en
    verifiant le mode 'before_distribution' avec un scenario diff."""
    async def _run():
        db, cid, fy_id, suffix = await _setup()
        try:
            # Ajoute une deuxieme ecriture d'achat NORMALE (non reversee)
            # pour verifier que le resultat converge bien
            await db.journal_entries.insert_one({
                "id": f"ac2-{suffix}", "journal_type": "AC", "date": "2026-02-05",
                "copropriete_id": cid, "fiscal_year_id": fy_id,
                "reference": f"F2-{suffix}",
                "lines": [
                    {"account_number": "6141", "debit": 300.0, "credit": 0.0},
                    {"account_number": "4400001", "debit": 0.0, "credit": 300.0},
                ],
                "total_debit": 300.0, "total_credit": 300.0,
            })
            fn = _endpoint(db, "/api/reports/bilan")
            result = await fn(request=None, copropriete_id=cid, fiscal_year_id=fy_id)
            total_actif = float(result.get("total_actif") or 0)
            total_passif = float(result.get("total_passif") or 0)
            ecart = round(total_actif - total_passif, 2)
            assert abs(ecart) < 0.01, (
                f"iter90fh (multi ecritures) : bilan doit etre equilibre. "
                f"Actif={total_actif} Passif={total_passif} Ecart={ecart}"
            )
        finally:
            await _cleanup(db, cid)

    asyncio.run(_run())


if __name__ == "__main__":
    test_bilan_actif_equals_passif_with_reversals()
    test_bilan_actif_equals_passif_after_distribution()
    print("OK")
