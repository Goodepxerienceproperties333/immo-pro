"""Regression test - iter85d - Mutation OD pre-periode NON lumpee dans Reprise.

Bug user : "Une mutation n'est PAS une reprise comptable tu as completement
foire la". Les ODs de mutation (source_type='lot_mutation') anterieures au
start_date du rapport etaient agregees dans la ligne "Reprise comptable"
synthetique, ce qui masquait totalement le transfert d'un vendeur a l'acheteur.

Fix : exclure les ODs avec source_type='lot_mutation' (ou reference 'MUT-*')
de l'agregation Reprise et les exposer comme lignes distinctes datees a leur
date d'origine.

Tests :
  1. Mutation au 15/12/2025, FY 2026 ouvert au 01/01/2026 :
     - La ligne "Reprise comptable" NE contient PAS le montant de la mutation
     - 2 lignes distinctes datees 15/12/2025 apparaissent dans le statement
       (credit vendeur ET debit acheteur)
  2. Cumul vendeur sur la periode = mouvements normaux + ligne mutation (pas Reprise)
  3. Idem fournisseur (les ODs de mutation ne touchent que comptes 4100xxx,
     donc la balance fournisseur reste intacte)
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
    cid = f"itr85d-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o_seller = f"os-{uuid.uuid4()}"
    o_buyer = f"ob-{uuid.uuid4()}"
    lot_id = f"lt-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "MUT85D", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "100", "name": "Fonds roulement", "class_num": 1, "copropriete_id": cid},
        {"number": "4100001", "name": "Seller", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Buyer", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o_seller, "name": "Vendeur V", "last_name": "Vendeur",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}, "vcs_code": "+++111+++"},
        {"id": o_buyer, "name": "Acheteur A", "last_name": "Acheteur",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}, "vcs_code": "+++222+++"},
    ])
    await db.lots.insert_one({
        "id": lot_id, "number": "A1", "owner_id": o_seller, "owner_ids": [o_seller],
        "copropriete_id": cid, "quotity": 1000.0,
    })
    return {"db": db, "cid": cid, "fy_id": fy_id,
            "o_seller": o_seller, "o_buyer": o_buyer, "lot_id": lot_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"copropriete_ids": ctx["cid"]})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.fund_calls.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


async def _insert_mutation_od(db, ctx, date_str, amount=1000.0, kind="fonds_roulement"):
    """Insere une OD de mutation directement en DB (simule une mutation passee)."""
    eid = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        "id": eid,
        "journal_type": "OD",
        "date": date_str,
        "reference": f"MUT-A1-R" if kind == "fonds_roulement" else "MUT-A1-P",
        "description": f"Mutation lot A1 - {kind}: Vendeur -> Acheteur ({amount:.2f} EUR)",
        "lines": [
            {"account_number": "4100002", "account_name": "Mutation - Acheteur",
             "debit": amount, "credit": 0.0,
             "third_party_id": ctx["o_buyer"], "third_party_name": "Acheteur A"},
            {"account_number": "4100001", "account_name": "Mutation - Vendeur",
             "debit": 0.0, "credit": amount,
             "third_party_id": ctx["o_seller"], "third_party_name": "Vendeur V"},
        ],
        "total_debit": amount, "total_credit": amount,
        "copropriete_id": ctx["cid"],
        "auto_generated": False,
        "manually_edited": True,
        "source_type": "lot_mutation",
        "source_id": ctx["lot_id"],
        "source_subtype": kind,
    })
    return eid


def _get_situation_owner_fn(db):
    from routes.reports import create_reports_router
    router = create_reports_router(db)
    for r in router.routes:
        if r.path == "/api/reports/balance-tiers/owners/{owner_id}":
            return r.endpoint
    return None


class _MockReq:
    def __init__(self, cid):
        self.state = type("S", (), {"copropriete_id": cid})()


async def _test_mutation_pre_period_not_in_reprise():
    """Mutation au 15/12/2025, on visualise FY 2026 (start=01/01/2026).
    Attendu : pas de ligne 'Reprise comptable' pour le vendeur (ses lignes
    de mutation sont EXCLUES de l'agregation et apparaissent distinctement)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await _insert_mutation_od(db, ctx, "2025-12-15", amount=1000.0, kind="fonds_roulement")
        await _insert_mutation_od(db, ctx, "2025-12-15", amount=300.0, kind="prorata")

        sit_fn = _get_situation_owner_fn(db)

        # ---- Statement du vendeur ----
        seller_stmt = await sit_fn(
            owner_id=ctx["o_seller"], request=_MockReq(ctx["cid"]),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        movements = seller_stmt.get("movements", [])

        # Pas de ligne reprise pour le vendeur (les seules entries < 2026-01-01
        # etaient les ODs de mutation qui sont maintenant EXCLUES)
        reprises = [m for m in movements if m.get("is_reprise")]
        assert len(reprises) == 0, (
            f"Aucune ligne 'Reprise comptable' attendue (les mutations sont "
            f"exclues), recu {len(reprises)} : {reprises}"
        )

        # 2 mouvements distincts datees 2025-12-15 doivent apparaitre (CR 1000 + CR 300)
        mut_movs = [m for m in movements if m.get("is_pre_period_mutation")]
        assert len(mut_movs) == 2, (
            f"2 lignes mutation pre-period attendues, recu {len(mut_movs)}"
        )
        assert all(m["date"] == "2025-12-15" for m in mut_movs), (
            "Les lignes doivent garder leur date originale"
        )
        # Cote vendeur : credit (1000 + 300 = 1300)
        total_credit = round(sum(float(m.get("credit", 0)) for m in mut_movs), 2)
        assert abs(total_credit - 1300.0) < 0.01, (
            f"Credit vendeur attendu 1300, recu {total_credit}"
        )

        # ---- Statement de l'acheteur ----
        buyer_stmt = await sit_fn(
            owner_id=ctx["o_buyer"], request=_MockReq(ctx["cid"]),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        movements_b = buyer_stmt.get("movements", [])
        reprises_b = [m for m in movements_b if m.get("is_reprise")]
        assert len(reprises_b) == 0, (
            f"Aucune Reprise pour acheteur (mutation < start_date exclue), recu {len(reprises_b)}"
        )
        mut_movs_b = [m for m in movements_b if m.get("is_pre_period_mutation")]
        assert len(mut_movs_b) == 2
        total_debit = round(sum(float(m.get("debit", 0)) for m in mut_movs_b), 2)
        assert abs(total_debit - 1300.0) < 0.01, (
            f"Debit acheteur attendu 1300, recu {total_debit}"
        )
        print("OK - Mutations pre-period exposees distinctement, hors Reprise")
    finally:
        await _cleanup(ctx)


async def _test_an_entry_remains_in_reprise_alongside_mutation():
    """Si une OD AN (cloture exercice) existe AVANT start_date et une OD mutation
    aussi, l'AN va dans Reprise, la mutation reste distincte."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        # OD A-Nouveau (cloture 2025) : 500 EUR debit vendeur
        await db.journal_entries.insert_one({
            "id": str(uuid.uuid4()), "journal_type": "AN", "date": "2025-12-31",
            "copropriete_id": ctx["cid"],
            "lines": [
                {"account_number": "4100001", "debit": 500.0, "credit": 0.0,
                 "third_party_id": ctx["o_seller"]},
                {"account_number": "100", "debit": 0.0, "credit": 500.0},
            ],
            "total_debit": 500.0, "total_credit": 500.0,
        })
        await _insert_mutation_od(db, ctx, "2025-11-20", amount=800.0, kind="fonds_roulement")

        sit_fn = _get_situation_owner_fn(db)
        stmt = await sit_fn(
            owner_id=ctx["o_seller"], request=_MockReq(ctx["cid"]),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        movements = stmt.get("movements", [])
        # 1 ligne Reprise (AN) + 1 ligne mutation distincte
        reprises = [m for m in movements if m.get("is_reprise")]
        assert len(reprises) == 1
        # La Reprise ne doit refleter que les 500 EUR de l'AN (PAS les 800 EUR mutation)
        assert abs(float(reprises[0]["debit"]) - 500.0) < 0.01, (
            f"Reprise debit doit etre 500 (AN seul), recu {reprises[0]['debit']}"
        )
        assert abs(float(reprises[0]["credit"]) - 0.0) < 0.01

        mut_movs = [m for m in movements if m.get("is_pre_period_mutation")]
        assert len(mut_movs) == 1, f"1 ligne mutation attendue, recu {len(mut_movs)}"
        assert abs(float(mut_movs[0]["credit"]) - 800.0) < 0.01, (
            f"Mutation vendeur credit 800 attendu, recu {mut_movs[0]['credit']}"
        )
        print("OK - AN + Mutation pre-period traites separement")
    finally:
        await _cleanup(ctx)


async def _test_mutation_in_period_still_in_movements():
    """Mutation au 15/02/2026 (dans la periode FY 2026) :
    elle apparait normalement dans les mouvements, pas dans Reprise."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        await _insert_mutation_od(db, ctx, "2026-02-15", amount=1000.0, kind="fonds_roulement")
        sit_fn = _get_situation_owner_fn(db)
        stmt = await sit_fn(
            owner_id=ctx["o_seller"], request=_MockReq(ctx["cid"]),
            copropriete_id=ctx["cid"],
            start_date="2026-01-01", end_date="2026-12-31",
            show_all=False,
        )
        movements = stmt.get("movements", [])
        # Pas de Reprise (rien avant start_date)
        reprises = [m for m in movements if m.get("is_reprise")]
        assert len(reprises) == 0
        # Pas de "is_pre_period_mutation" car la mutation est DANS la periode
        pre = [m for m in movements if m.get("is_pre_period_mutation")]
        assert len(pre) == 0
        # La mutation apparait dans les mouvements normaux datee 2026-02-15
        muts_in_period = [m for m in movements if m.get("date") == "2026-02-15"]
        assert len(muts_in_period) >= 1
        print("OK - Mutation in-period reste dans mouvements normaux")
    finally:
        await _cleanup(ctx)


def test_iter85d_mutation_pre_period_not_in_reprise():
    asyncio.run(_test_mutation_pre_period_not_in_reprise())


def test_iter85d_an_entry_remains_in_reprise_alongside_mutation():
    asyncio.run(_test_an_entry_remains_in_reprise_alongside_mutation())


def test_iter85d_mutation_in_period_still_in_movements():
    asyncio.run(_test_mutation_in_period_still_in_movements())
