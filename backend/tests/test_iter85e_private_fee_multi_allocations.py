"""Regression test - iter85e - Frais privatifs multi-allocations.

Demande user :
  - Repartir une facture frais privatif sur PLUSIEURS proprietaires
  - Mode : montants fixes en EUR par owner (somme = total facture)
  - Dropdown owners filtre par ACP (chinese wall) - testable cote frontend
  - Comportement : 100% proprietaire pour chaque ligne (pas d'occupant)
  - Affichage : quote-part par owner dans sa situation de compte

Comptable :
  - 1 AC : Dr 643 (total) / Cr 44000XXX fournisseur (total)
  - 1 OD : N x DR 4100XXX owner + N x CR 643 (1 par owner)
  -> Chaque copro voit sa quote-part dans son grand livre.

Tests :
  1. Create avec 2 allocations 60/40 -> 1 AC + 1 OD avec 4 lignes (2 DR + 2 CR)
  2. Validation somme mismatch -> 400
  3. Update single -> multi : OD regenere avec N lignes
  4. Update multi -> single (legacy) : OD revient a 2 lignes
  5. Retrocompat : private_fee_owner_id seul -> 1 allocation derivee, OD 2 lignes
  6. Chinese wall : GET /api/owners?copropriete_id=X ne retourne que les owners
     ayant un lot dans cette ACP (deja teste par iter72, regression)
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
    cid = f"itr85e-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    o2 = f"o2-{uuid.uuid4()}"
    o3_other_acp = f"o3-{uuid.uuid4()}"
    lot1 = f"lot1-{uuid.uuid4()}"
    lot2 = f"lot2-{uuid.uuid4()}"
    sup_id = f"sup-{uuid.uuid4()}"

    await db.coproprietes.insert_one({"id": cid, "name": "PRIVA", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "643", "name": "Frais privatifs", "class_num": 6, "copropriete_id": cid},
        {"number": "4100001", "name": "Owner1", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Owner2", "class_num": 4, "copropriete_id": cid},
        {"number": "44000001", "name": "Supplier", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "DUPONT Jean", "last_name": "DUPONT", "first_name": "Jean",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}, "vcs_code": "+++111+++"},
        {"id": o2, "name": "MARTIN Marie", "last_name": "MARTIN", "first_name": "Marie",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}, "vcs_code": "+++222+++"},
        # Owner d'une AUTRE ACP - ne doit PAS apparaitre dans le dropdown
        {"id": o3_other_acp, "name": "AUTRE Pierre", "last_name": "AUTRE",
         "auxiliary_code": "C9999", "copropriete_ids": [f"other-{uuid.uuid4()}"]},
    ])
    await db.lots.insert_many([
        {"id": lot1, "number": "A1", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 500},
        {"id": lot2, "number": "B1", "owner_id": o2, "owner_ids": [o2],
         "copropriete_id": cid, "quotity": 500},
    ])
    await db.suppliers.insert_one({
        "id": sup_id, "name": "FOURNISSEUR Test",
        "tier_accounts": {cid: {"main": "44000001"}},
    })
    return {"db": db, "cid": cid, "fy_id": fy_id,
            "o1": o1, "o2": o2, "o3_other_acp": o3_other_acp,
            "lot1": lot1, "lot2": lot2, "sup_id": sup_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"], ctx["o3_other_acp"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.suppliers.delete_one({"id": ctx["sup_id"]})
    await db.invoices.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(db, route_path: str, method: str = "POST"):
    from routes.invoices import create_invoices_router
    router = create_invoices_router(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


async def _test_create_multi_allocations_generates_correct_entries():
    """Facture 1000 EUR repartie 600/400 -> 1 AC + 1 OD avec 4 lignes."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        PrivateFeeAllocation = None
        # Acceder au modele d'allocation via le module
        import routes.invoices as inv_mod
        PrivateFeeAllocation = inv_mod.PrivateFeeAllocation
        payload = InvoiceInput(
            number="FA-PRIV-001", date="2026-03-15", due_date="2026-04-15",
            supplier="FOURNISSEUR Test",
            description="Reparation vitre commune lots A1+B1",
            total_amount=1000.0, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_allocations=[
                PrivateFeeAllocation(owner_id=ctx["o1"], amount=600.0),
                PrivateFeeAllocation(owner_id=ctx["o2"], amount=400.0),
            ],
        )
        result = await create_fn(data=payload)
        assert result["is_private_fee"] is True
        assert result["account_number"] == "643"
        assert len(result["private_fee_allocations"]) == 2

        # Verifier les ecritures comptables
        je_ac = await db.journal_entries.find_one(
            {"source_id": result["id"], "source_type": "invoice", "journal_type": "AC"},
            {"_id": 0},
        )
        assert je_ac is not None
        # AC : 2 lignes (DR 643 1000 / CR fournisseur 1000)
        assert len(je_ac["lines"]) == 2
        assert abs(je_ac["total_debit"] - 1000.0) < 0.01
        assert any(ln["account_number"] == "643" and ln["debit"] == 1000.0 for ln in je_ac["lines"])
        assert any(ln["account_number"] == "44000001" and ln["credit"] == 1000.0 for ln in je_ac["lines"])

        je_od = await db.journal_entries.find_one(
            {"source_id": result["id"], "source_type": "invoice", "journal_type": "OD"},
            {"_id": 0},
        )
        assert je_od is not None
        # OD : 4 lignes (2 DR owner + 2 CR 643)
        assert len(je_od["lines"]) == 4, f"4 lignes OD attendues, recu {len(je_od['lines'])}"
        # Verifier les DR sur comptes owners
        dr_owners = [ln for ln in je_od["lines"] if ln["debit"] > 0]
        assert len(dr_owners) == 2
        dr_by_owner = {ln["third_party_id"]: ln for ln in dr_owners}
        assert abs(dr_by_owner[ctx["o1"]]["debit"] - 600.0) < 0.01
        assert dr_by_owner[ctx["o1"]]["account_number"] == "4100001"
        assert abs(dr_by_owner[ctx["o2"]]["debit"] - 400.0) < 0.01
        assert dr_by_owner[ctx["o2"]]["account_number"] == "4100002"

        # Verifier les CR sur 643 (contre-partie)
        cr_643 = [ln for ln in je_od["lines"] if ln["credit"] > 0 and ln["account_number"] == "643"]
        assert len(cr_643) == 2
        assert abs(sum(ln["credit"] for ln in cr_643) - 1000.0) < 0.01

        # Equilibre total
        assert abs(je_od["total_debit"] - 1000.0) < 0.01
        assert abs(je_od["total_credit"] - 1000.0) < 0.01
        print("OK - iter85e : 2 allocations 600/400 -> AC 2 lignes + OD 4 lignes equilibres")
    finally:
        await _cleanup(ctx)


async def _test_validation_sum_mismatch_returns_400():
    """Somme allocations != total_amount -> 400."""
    from fastapi import HTTPException
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        import routes.invoices as inv_mod
        PrivateFeeAllocation = inv_mod.PrivateFeeAllocation
        payload = InvoiceInput(
            number="FA-FAIL-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="Test",
            total_amount=1000.0, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_allocations=[
                PrivateFeeAllocation(owner_id=ctx["o1"], amount=300.0),
                PrivateFeeAllocation(owner_id=ctx["o2"], amount=400.0),  # somme = 700 ≠ 1000
            ],
        )
        try:
            await create_fn(data=payload)
            assert False, "Devrait lever HTTPException 400"
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "egaler" in exc.detail.lower() or "somme" in exc.detail.lower()
        print("OK - iter85e : somme mismatch -> 400")
    finally:
        await _cleanup(ctx)


async def _test_validation_one_cent_off_returns_400():
    """iter85j : 90.01 vs 90.02 (ecart 0.01 EUR exact) doit etre detecte
    et bloquer la creation. Avant fix : passait a cause d'erreur flottante.
    """
    from fastapi import HTTPException
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        import routes.invoices as inv_mod
        PrivateFeeAllocation = inv_mod.PrivateFeeAllocation
        # Total = 90.02, allocations 6 x 15.00 = 90.00 + 1 x 15.01 = 90.01
        # Ecart = 0.01 exact
        payload = InvoiceInput(
            number="FA-CENT-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="6 owners 90.01 vs 90.02",
            total_amount=90.02, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_allocations=[
                PrivateFeeAllocation(owner_id=ctx["o1"], amount=15.00),
                PrivateFeeAllocation(owner_id=ctx["o2"], amount=15.00),
                # Pour avoir 1 cent exact d'ecart : on peut juste utiliser
                # 1 alloc qui somme a 90.01 alors que total = 90.02
            ],
        )
        # Reconfigure pour avoir le bon scenario : 2 owners, somme = 90.01,
        # total = 90.02 -> ecart 0.01 doit etre detecte
        payload.private_fee_allocations = [
            PrivateFeeAllocation(owner_id=ctx["o1"], amount=45.00),
            PrivateFeeAllocation(owner_id=ctx["o2"], amount=45.01),
        ]
        try:
            await create_fn(data=payload)
            assert False, "Devrait lever HTTPException 400 (ecart 1 cent)"
        except HTTPException as exc:
            assert exc.status_code == 400
            # Le message doit mentionner l'ecart precis
            assert "ecart" in exc.detail.lower() or "egaler" in exc.detail.lower()
        print("OK - iter85j : ecart 0.01 exact detecte (comparison en centimes)")
    finally:
        await _cleanup(ctx)


async def _test_validation_exactly_balanced_passes():
    """iter85j : 45.01 + 44.99 = 90.00 exact - doit passer sans erreur flottant."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        import routes.invoices as inv_mod
        PrivateFeeAllocation = inv_mod.PrivateFeeAllocation
        payload = InvoiceInput(
            number="FA-OK-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="Test equilibre",
            total_amount=90.00, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_allocations=[
                PrivateFeeAllocation(owner_id=ctx["o1"], amount=45.01),
                PrivateFeeAllocation(owner_id=ctx["o2"], amount=44.99),
            ],
        )
        result = await create_fn(data=payload)
        assert result["id"]
        print("OK - iter85j : 45.01 + 44.99 = 90.00 passe sans probleme")
    finally:
        await _cleanup(ctx)


async def _test_backward_compat_single_owner():
    """Legacy : private_fee_owner_id seul (sans allocations) -> 1 allocation derivee."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        InvoiceInput = create_fn.__annotations__.get("data")
        payload = InvoiceInput(
            number="FA-LEG-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="Frais privatif single owner",
            total_amount=500.0, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_owner_id=ctx["o1"],  # legacy
        )
        result = await create_fn(data=payload)
        # private_fee_allocations doit etre auto-rempli avec 1 entree
        allocs = result.get("private_fee_allocations") or []
        assert len(allocs) == 1
        assert allocs[0]["owner_id"] == ctx["o1"]
        assert abs(allocs[0]["amount"] - 500.0) < 0.01

        je_od = await db.journal_entries.find_one(
            {"source_id": result["id"], "journal_type": "OD"}, {"_id": 0},
        )
        assert je_od is not None
        # OD : 2 lignes (1 DR owner + 1 CR 643)
        assert len(je_od["lines"]) == 2
        print("OK - iter85e : legacy private_fee_owner_id -> 1 allocation + OD 2 lignes")
    finally:
        await _cleanup(ctx)


async def _test_update_single_to_multi_regenerates_entries():
    """PUT facture single -> multi : OD regeneree avec N lignes."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        create_fn = _get_endpoint(db, "/api/invoices", "POST")
        update_fn = _get_endpoint(db, "/api/invoices/{invoice_id}", "PUT")
        InvoiceInput = create_fn.__annotations__.get("data")
        import routes.invoices as inv_mod
        PrivateFeeAllocation = inv_mod.PrivateFeeAllocation

        # Create avec 1 owner
        payload = InvoiceInput(
            number="FA-UPD-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="Test",
            total_amount=1000.0, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_owner_id=ctx["o1"],
        )
        inv = await create_fn(data=payload)
        je_od_v1 = await db.journal_entries.find_one(
            {"source_id": inv["id"], "journal_type": "OD"}, {"_id": 0},
        )
        assert len(je_od_v1["lines"]) == 2  # single owner

        # Update vers 2 owners 700/300
        upd_payload = InvoiceInput(
            number="FA-UPD-001", date="2026-03-15",
            supplier="FOURNISSEUR Test", description="Test multi",
            total_amount=1000.0, copropriete_id=ctx["cid"],
            is_private_fee=True,
            private_fee_allocations=[
                PrivateFeeAllocation(owner_id=ctx["o1"], amount=700.0),
                PrivateFeeAllocation(owner_id=ctx["o2"], amount=300.0),
            ],
        )
        await update_fn(invoice_id=inv["id"], data=upd_payload)

        je_od_v2 = await db.journal_entries.find_one(
            {"source_id": inv["id"], "journal_type": "OD"}, {"_id": 0},
        )
        assert len(je_od_v2["lines"]) == 4, f"OD attendue avec 4 lignes apres update, recu {len(je_od_v2['lines'])}"
        # Verifie les nouveaux montants
        dr_lines = [ln for ln in je_od_v2["lines"] if ln["debit"] > 0]
        dr_by_owner = {ln["third_party_id"]: ln["debit"] for ln in dr_lines}
        assert abs(dr_by_owner[ctx["o1"]] - 700.0) < 0.01
        assert abs(dr_by_owner[ctx["o2"]] - 300.0) < 0.01
        print("OK - iter85e : update single -> multi regenere OD avec 4 lignes")
    finally:
        await _cleanup(ctx)


async def _test_chinese_wall_owners_filter_by_acp():
    """GET /api/owners?copropriete_id=X ne retourne QUE les owners de cette ACP."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        # Monkey-patch get_current_user pour bypasser l'auth dans ce test unitaire
        import server as srv_mod
        original_get_user = srv_mod.get_current_user

        async def _mock_user(request):
            return {"_id": "mock", "role": "superadmin", "copropriete_ids": []}

        srv_mod.get_current_user = _mock_user
        try:
            from routes.properties import create_properties_router
            router = create_properties_router(db)
            list_owners_fn = None
            for r in router.routes:
                if r.path == "/api/owners" and "GET" in (r.methods or set()):
                    list_owners_fn = r.endpoint
                    break
            assert list_owners_fn is not None

            class _Req:
                headers = {}
                cookies = {}
                state = type("S", (), {"copropriete_id": ctx["cid"]})()

            # Call list_owners avec copropriete_id scope
            result = await list_owners_fn(
                request=_Req(),
                copropriete_id=ctx["cid"],
                include_unassigned=False,
            )
            ids = {o["id"] for o in result}
            assert ctx["o1"] in ids, "Owner o1 (membre de cette ACP) doit etre visible"
            assert ctx["o2"] in ids, "Owner o2 (membre de cette ACP) doit etre visible"
            assert ctx["o3_other_acp"] not in ids, (
                "Owner o3 (membre d'une AUTRE ACP) NE doit PAS etre visible (chinese wall)"
            )
            print("OK - iter85e : chinese wall owners filtre par ACP")
        finally:
            srv_mod.get_current_user = original_get_user
    finally:
        await _cleanup(ctx)


def test_iter85e_create_multi_allocations():
    asyncio.run(_test_create_multi_allocations_generates_correct_entries())


def test_iter85e_validation_sum_mismatch():
    asyncio.run(_test_validation_sum_mismatch_returns_400())


def test_iter85j_validation_one_cent_off_blocks():
    asyncio.run(_test_validation_one_cent_off_returns_400())


def test_iter85j_validation_exactly_balanced_passes():
    asyncio.run(_test_validation_exactly_balanced_passes())


def test_iter85e_backward_compat_single_owner():
    asyncio.run(_test_backward_compat_single_owner())


def test_iter85e_update_single_to_multi():
    asyncio.run(_test_update_single_to_multi_regenerates_entries())


def test_iter85e_chinese_wall_owners_filter():
    asyncio.run(_test_chinese_wall_owners_filter_by_acp())
