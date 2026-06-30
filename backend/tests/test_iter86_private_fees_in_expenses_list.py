"""Regression test - iter86 - Les frais privatifs apparaissent dans /api/fiscal/expenses.

Bug user (Feb 2026, PDF Optipro "Liste des depenses 01/10/2025 - 30/06/2026") :
    "Toutes les depenses doivent etre prises en compte dans la liste des depenses
     meme les frais privatifs comme ici"

Root cause :
    Dans routes/fiscal.py::list_expenses, le filtre cherchait
    `je.get("source_invoice_id")` qui n'existait pas (le champ reel est
    `source_id` lorsque `source_type == "invoice"`). Resultat : pour une
    facture frais privatif :
      - La facture etait listee a +amount (compte 643)
      - L'OD d'imputation (Cr 643) etait AUSSI listee a -amount
      -> Total compte 643 = 0 -> frais privatif "invisible" au total.

Fix :
    1. `if je.get("source_type") == "invoice" and je.get("source_id") in
       invoice_ids_done: continue` -> bonne cle.
    2. Enrichissement du row facture privatif avec `is_private_fee`,
       `private_fee_allocations[]` et `private_fee_owners_display` (transparence
       UI).

Tests :
  1. Une facture privative 155 EUR (1 owner) apparait dans la liste avec
     account_number=643 et total_amount=+155. Le total global = 155 (pas 0).
  2. Une facture privative multi-allocations 600+400 (2 owners) apparait avec
     total=1000 et la liste des owners assignes est exposee.
  3. Une facture normale + sa AC ne sont PAS doublees (regression).
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
    cid = f"itr86-{uuid.uuid4()}"
    fy_id = f"fy-{uuid.uuid4()}"
    o1 = f"o1-{uuid.uuid4()}"
    o2 = f"o2-{uuid.uuid4()}"
    lot1 = f"lot1-{uuid.uuid4()}"
    lot2 = f"lot2-{uuid.uuid4()}"
    sup_id = f"sup-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITR86", "status": "active"})
    await db.fiscal_years.insert_one({
        "id": fy_id, "name": "2026",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "copropriete_id": cid,
    })
    await db.pcmn_accounts.insert_many([
        {"number": "643", "name": "Frais privatifs", "class_num": 6, "copropriete_id": cid},
        {"number": "615", "name": "Entretien", "class_num": 6, "copropriete_id": cid},
        {"number": "4100001", "name": "Owner1", "class_num": 4, "copropriete_id": cid},
        {"number": "4100002", "name": "Owner2", "class_num": 4, "copropriete_id": cid},
        {"number": "44000001", "name": "Supplier", "class_num": 4, "copropriete_id": cid},
    ])
    await db.owners.insert_many([
        {"id": o1, "name": "MATEXI S.A.", "last_name": "MATEXI", "first_name": "S.A.",
         "auxiliary_code": "C0001", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100001"}}, "vcs_code": "+++111+++"},
        {"id": o2, "name": "FROMENT-PEETERS", "last_name": "FROMENT", "first_name": "Peeters",
         "auxiliary_code": "C0002", "copropriete_ids": [cid],
         "tier_accounts": {cid: {"provisions": "4100002"}}, "vcs_code": "+++222+++"},
    ])
    await db.lots.insert_many([
        {"id": lot1, "number": "302", "owner_id": o1, "owner_ids": [o1],
         "copropriete_id": cid, "quotity": 500},
        {"id": lot2, "number": "A1", "owner_id": o2, "owner_ids": [o2],
         "copropriete_id": cid, "quotity": 500},
    ])
    await db.suppliers.insert_one({
        "id": sup_id, "name": "SRL Finlead",
        "tier_accounts": {cid: {"main": "44000001"}},
    })
    return {"db": db, "cid": cid, "fy_id": fy_id,
            "o1": o1, "o2": o2, "lot1": lot1, "lot2": lot2, "sup_id": sup_id}


async def _cleanup(ctx):
    db = ctx["db"]
    await db.coproprietes.delete_one({"id": ctx["cid"]})
    await db.fiscal_years.delete_one({"id": ctx["fy_id"]})
    await db.owners.delete_many({"id": {"$in": [ctx["o1"], ctx["o2"]]}})
    await db.lots.delete_many({"copropriete_id": ctx["cid"]})
    await db.pcmn_accounts.delete_many({"copropriete_id": ctx["cid"]})
    await db.suppliers.delete_one({"id": ctx["sup_id"]})
    await db.invoices.delete_many({"copropriete_id": ctx["cid"]})
    await db.journal_entries.delete_many({"copropriete_id": ctx["cid"]})


def _get_endpoint(router_factory, db, route_path: str, method: str = "GET"):
    router = router_factory(db)
    for r in router.routes:
        if r.path == route_path and method.upper() in (r.methods or set()):
            return r.endpoint
    return None


class _Req:
    """Mock request - headers vides (copropriete_id passe en param)."""
    def __init__(self):
        self.headers = {}


async def _test_private_fee_single_owner_visible():
    """Facture frais privatif 155 EUR sur 1 owner doit apparaitre dans /expenses
    avec le bon total (et l'OD d'imputation NE doit PAS neutraliser)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        from routes.invoices import create_invoices_router
        from routes.fiscal import create_fiscal_router
        import routes.invoices as inv_mod
        # Force pas de RBAC pour le test (mock auth)
        import server as srv_mod
        original_get_user = srv_mod.get_current_user
        async def _mock_user():
            return {"id": "test-user", "role": "superadmin", "email": "t@t.com"}
        srv_mod.get_current_user = _mock_user
        try:
            create_inv = _get_endpoint(create_invoices_router, db, "/api/invoices", "POST")
            list_exp = _get_endpoint(create_fiscal_router, db, "/api/fiscal/expenses", "GET")
            InvoiceInput = inv_mod.InvoiceInput
            PrivateFeeAllocation = inv_mod.PrivateFeeAllocation

            payload = InvoiceInput(
                number="V-NOTAIRE-001", date="2026-03-15",
                supplier="SRL Finlead",
                description="1ere info notaire - appart. 302",
                total_amount=155.0, copropriete_id=ctx["cid"],
                is_private_fee=True,
                private_fee_allocations=[
                    PrivateFeeAllocation(owner_id=ctx["o1"], amount=155.0),
                ],
            )
            inv = await create_inv(data=payload)

            # Verifier que AC + OD ont bien ete crees
            ac = await db.journal_entries.find_one(
                {"source_id": inv["id"], "journal_type": "AC"}, {"_id": 0})
            od = await db.journal_entries.find_one(
                {"source_id": inv["id"], "journal_type": "OD"}, {"_id": 0})
            assert ac is not None, "AC doit etre creee"
            assert od is not None, "OD doit etre creee"

            # Maintenant lister les depenses
            result = await list_exp(
                request=_Req(),
                copropriete_id=ctx["cid"],
                fiscal_year_id=None,
                date_from="2026-01-01", date_to="2026-12-31",
                account_number=None, distribution_key_id=None,
                expense_category_id=None, bank_account=None,
            )
            rows = result["expenses"]
            totals = result["totals"]

            # 1) La facture privative DOIT apparaitre exactement 1 fois
            privatif_rows = [r for r in rows if r.get("invoice_id", r.get("id")) == inv["id"]
                             or r["id"] == inv["id"]]
            assert len(privatif_rows) == 1, (
                f"Une seule row attendue pour la facture privative, trouve {len(privatif_rows)}: {privatif_rows}"
            )
            row = privatif_rows[0]
            assert row["account_number"] == "643"
            assert abs(row["total_amount"] - 155.0) < 0.01, (
                f"total_amount doit etre 155 (montant brut), trouve {row['total_amount']}"
            )
            assert row["is_private_fee"] is True, "le flag is_private_fee doit etre expose"
            assert "MATEXI" in (row.get("private_fee_owners_display") or ""), (
                f"Le nom du proprietaire doit etre expose : {row.get('private_fee_owners_display')}"
            )
            assert len(row.get("private_fee_allocations") or []) == 1

            # 2) Aucune row negative ne doit apparaitre (OD d'imputation skip)
            negative_rows = [r for r in rows if r["total_amount"] < 0 and r["account_number"] == "643"]
            assert len(negative_rows) == 0, (
                f"L'OD d'imputation NE doit PAS apparaitre comme depense negative : {negative_rows}"
            )

            # 3) Le total global est bien +155 (pas 0)
            assert abs(totals["total"] - 155.0) < 0.01, (
                f"Total des depenses doit etre 155 EUR (frais privatif compte), trouve {totals['total']}"
            )

            # 4) Le total par compte 643 est bien 155
            assert abs(totals["by_account"].get("643", 0) - 155.0) < 0.01

            print("OK - iter86 : facture privative 155 EUR visible dans liste depenses (total +155)")
        finally:
            srv_mod.get_current_user = original_get_user
    finally:
        await _cleanup(ctx)


async def _test_private_fee_multi_owners_visible():
    """Facture privative 1000 EUR repartie 600/400 : visible avec total +1000
    et les 2 owners exposes dans la row."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        from routes.invoices import create_invoices_router
        from routes.fiscal import create_fiscal_router
        import routes.invoices as inv_mod
        import server as srv_mod
        original_get_user = srv_mod.get_current_user
        async def _mock_user():
            return {"id": "test-user", "role": "superadmin", "email": "t@t.com"}
        srv_mod.get_current_user = _mock_user
        try:
            create_inv = _get_endpoint(create_invoices_router, db, "/api/invoices", "POST")
            list_exp = _get_endpoint(create_fiscal_router, db, "/api/fiscal/expenses", "GET")
            payload = inv_mod.InvoiceInput(
                number="V-VITRE-001", date="2026-04-10",
                supplier="SRL Finlead",
                description="Reparation vitre lots 302 et A1",
                total_amount=1000.0, copropriete_id=ctx["cid"],
                is_private_fee=True,
                private_fee_allocations=[
                    inv_mod.PrivateFeeAllocation(owner_id=ctx["o1"], amount=600.0),
                    inv_mod.PrivateFeeAllocation(owner_id=ctx["o2"], amount=400.0),
                ],
            )
            inv = await create_inv(data=payload)

            result = await list_exp(
                request=_Req(),
                copropriete_id=ctx["cid"],
                fiscal_year_id=None,
                date_from="2026-01-01", date_to="2026-12-31",
                account_number=None, distribution_key_id=None,
                expense_category_id=None, bank_account=None,
            )
            rows = result["expenses"]
            totals = result["totals"]
            privatif_rows = [r for r in rows if r["id"] == inv["id"]]
            assert len(privatif_rows) == 1
            row = privatif_rows[0]
            assert row["is_private_fee"] is True
            allocs = row["private_fee_allocations"]
            assert len(allocs) == 2
            allocs_by_owner = {a["owner_id"]: a for a in allocs}
            assert abs(allocs_by_owner[ctx["o1"]]["amount"] - 600.0) < 0.01
            assert "MATEXI" in allocs_by_owner[ctx["o1"]]["owner_name"]
            assert abs(allocs_by_owner[ctx["o2"]]["amount"] - 400.0) < 0.01
            assert "FROMENT" in allocs_by_owner[ctx["o2"]]["owner_name"]
            display = row["private_fee_owners_display"]
            assert "MATEXI" in display and "FROMENT" in display
            assert abs(totals["total"] - 1000.0) < 0.01
            print("OK - iter86 : facture privative multi-allocations 600/400 visible (total +1000)")
        finally:
            srv_mod.get_current_user = original_get_user
    finally:
        await _cleanup(ctx)


async def _test_normal_invoice_not_doubled():
    """Regression : une facture normale n'est PAS comptee 2 fois
    (l'AC qu'elle genere est correctement skip)."""
    ctx = await _setup()
    db = ctx["db"]
    try:
        from routes.invoices import create_invoices_router
        from routes.fiscal import create_fiscal_router
        import routes.invoices as inv_mod
        import server as srv_mod
        original_get_user = srv_mod.get_current_user
        async def _mock_user():
            return {"id": "test-user", "role": "superadmin", "email": "t@t.com"}
        srv_mod.get_current_user = _mock_user
        try:
            create_inv = _get_endpoint(create_invoices_router, db, "/api/invoices", "POST")
            list_exp = _get_endpoint(create_fiscal_router, db, "/api/fiscal/expenses", "GET")
            payload = inv_mod.InvoiceInput(
                number="V-ENT-001", date="2026-05-01",
                supplier="SRL Finlead",
                description="Entretien jardins T2",
                total_amount=500.0, copropriete_id=ctx["cid"],
                account_number="615",
            )
            inv = await create_inv(data=payload)
            result = await list_exp(
                request=_Req(),
                copropriete_id=ctx["cid"],
                fiscal_year_id=None,
                date_from="2026-01-01", date_to="2026-12-31",
                account_number=None, distribution_key_id=None,
                expense_category_id=None, bank_account=None,
            )
            rows = result["expenses"]
            inv_rows = [r for r in rows if r["id"] == inv["id"]]
            assert len(inv_rows) == 1, (
                f"Une seule row attendue (pas de double comptage), trouve {len(inv_rows)}"
            )
            assert abs(result["totals"]["total"] - 500.0) < 0.01
            print("OK - iter86 : facture normale non doublee (AC correctement skip)")
        finally:
            srv_mod.get_current_user = original_get_user
    finally:
        await _cleanup(ctx)


def test_iter86_private_fee_single_owner_in_expenses_list():
    asyncio.run(_test_private_fee_single_owner_visible())


def test_iter86_private_fee_multi_allocations_in_expenses_list():
    asyncio.run(_test_private_fee_multi_owners_visible())


def test_iter86_normal_invoice_not_doubled_in_expenses_list():
    asyncio.run(_test_normal_invoice_not_doubled())
