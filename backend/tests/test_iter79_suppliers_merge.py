"""Regression test - iter79 - Fusion de fournisseurs (centralisation).

Demande : "fusionne les comptes fournisseurs pour centraliser les informations"
- Cas type : "Finlead srl" et "SRL Finlead" sont la meme entreprise.
- Nouveau endpoint POST /api/suppliers/merge accepte (keep_id, remove_ids[]).
- Reassocie : invoices.supplier_id, bank_transactions.matched_to
- Enrichit le supplier conserve avec les champs vides depuis les absorbes.
- Supprime les fournisseurs absorbes.

Egalement teste l'amelioration de _norm_name (tri alphabetique des mots) pour
detecter "Finlead srl" vs "SRL Finlead" comme doublon.
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_norm_name_handles_word_reorder():
    from routes.suppliers import _norm_name
    assert _norm_name("Finlead srl") == _norm_name("SRL Finlead")
    assert _norm_name("BNP Paribas Fortis") == _norm_name("Paribas BNP Fortis")
    # Doit etre stable
    assert _norm_name("foo") == "foo"
    assert _norm_name("") == ""


async def _test_find_duplicate_reordered_words():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import find_duplicate_supplier
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    cid = f"itr79-{uuid.uuid4()}"
    s1_id = f"s1-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITER79", "status": "active"})
    await db.suppliers.insert_one({
        "id": s1_id, "name": "Finlead srl", "copropriete_id": cid,
    })
    try:
        dup = await find_duplicate_supplier(
            db, name="SRL Finlead", bce_number="", vat_number="",
            iban="", copro_id=cid,
        )
        assert dup is not None, "Doit detecter 'SRL Finlead' comme doublon de 'Finlead srl'"
        assert dup["field"] == "name"
        assert dup["supplier"]["id"] == s1_id
    finally:
        await db.coproprietes.delete_many({"id": cid})
        await db.suppliers.delete_many({"copropriete_id": cid})


async def _test_merge_endpoint_e2e():
    """Cree 3 fournisseurs (un avec BCE+IBAN, 2 autres minimaux), des factures
    pour chacun, puis fusionne -> verifie reassociation + enrichissement."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.suppliers import create_suppliers_router, SupplierMergeInput
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cid = f"itr79-{uuid.uuid4()}"
    s_keep = f"keep-{uuid.uuid4()}"
    s_rem1 = f"rem1-{uuid.uuid4()}"
    s_rem2 = f"rem2-{uuid.uuid4()}"
    await db.coproprietes.insert_one({"id": cid, "name": "ITER79", "status": "active"})
    # Keep : BCE + IBAN
    await db.suppliers.insert_one({
        "id": s_keep, "name": "Finlead srl",
        "bce_number": "BE0728990830", "iban": "BE30 6511 6629 7311",
        "email": "", "phone": "",
        "copropriete_id": cid,
    })
    # Remove 1 : email + phone manquants sur keep
    await db.suppliers.insert_one({
        "id": s_rem1, "name": "SRL Finlead",
        "bce_number": "", "iban": "",
        "email": "contact@finlead.be", "phone": "+32499999999",
        "copropriete_id": cid,
    })
    # Remove 2 : adresse
    await db.suppliers.insert_one({
        "id": s_rem2, "name": "Finlead SRL",
        "address": "Rue de Test 1", "city": "Bruxelles", "postal_code": "1000",
        "copropriete_id": cid,
    })
    # Invoices : 3 pointant sur s_rem1, 2 sur s_rem2, 1 sur s_keep
    inv_ids = []
    for _ in range(3):
        iid = str(uuid.uuid4()); inv_ids.append(("rem1", iid))
        await db.invoices.insert_one({"id": iid, "supplier_id": s_rem1, "copropriete_id": cid, "total_amount": 100})
    for _ in range(2):
        iid = str(uuid.uuid4()); inv_ids.append(("rem2", iid))
        await db.invoices.insert_one({"id": iid, "supplier_id": s_rem2, "copropriete_id": cid, "total_amount": 200})
    iid_keep = str(uuid.uuid4())
    await db.invoices.insert_one({"id": iid_keep, "supplier_id": s_keep, "copropriete_id": cid, "total_amount": 500})
    # 1 bank_transaction matched to s_rem1
    txn_id = str(uuid.uuid4())
    await db.bank_transactions.insert_one({
        "id": txn_id, "matched": True, "matched_to": s_rem1, "match_type": "supplier_payment",
        "amount": -100, "date": "2026-01-01", "copropriete_id": cid,
    })

    try:
        # Mock request avec un super-admin (bypass scope)
        from fastapi import Request

        class _MockReq:
            state = type("S", (), {"copropriete_id": ""})()
            headers = {}

        # Patch get_current_user pour retourner un superadmin
        import server
        original = server.get_current_user

        async def fake_get_user(req):
            return {"id": "test", "role": "superadmin", "copropriete_ids": []}
        server.get_current_user = fake_get_user

        try:
            router = create_suppliers_router(db)
            merge_fn = None
            for r in router.routes:
                if r.path == "/api/suppliers/merge":
                    merge_fn = r.endpoint
                    break
            assert merge_fn is not None

            payload = SupplierMergeInput(keep_id=s_keep, remove_ids=[s_rem1, s_rem2])
            res = await merge_fn(data=payload, request=_MockReq())
        finally:
            server.get_current_user = original

        # Verifications
        assert res["kept_id"] == s_keep
        assert set(res["removed_ids"]) == {s_rem1, s_rem2}
        assert res["invoices_migrated"] == 5  # 3 + 2
        assert res["bank_transactions_migrated"] == 1

        # Enrichissement : email, phone, address, city, postal_code copies depuis remove1/remove2
        keep_after = await db.suppliers.find_one({"id": s_keep}, {"_id": 0})
        assert keep_after["email"] == "contact@finlead.be"
        assert keep_after["phone"] == "+32499999999"
        assert keep_after["address"] == "Rue de Test 1"
        assert keep_after["city"] == "Bruxelles"
        assert keep_after["postal_code"] == "1000"

        # BCE / IBAN conserves (etaient deja sur keep)
        assert keep_after["bce_number"] == "BE0728990830"

        # Toutes les factures pointent vers s_keep
        all_invs = await db.invoices.find({"copropriete_id": cid}, {"_id": 0}).to_list(100)
        assert all(i["supplier_id"] == s_keep for i in all_invs), \
            f"Factures non migrees : {[(i['id'][:8], i['supplier_id'][:8]) for i in all_invs]}"

        # Transaction migree
        t = await db.bank_transactions.find_one({"id": txn_id}, {"_id": 0})
        assert t["matched_to"] == s_keep

        # Fournisseurs absorbes supprimes
        remaining = await db.suppliers.count_documents({"id": {"$in": [s_rem1, s_rem2]}})
        assert remaining == 0
    finally:
        await db.coproprietes.delete_many({"id": cid})
        await db.suppliers.delete_many({"copropriete_id": cid})
        await db.invoices.delete_many({"copropriete_id": cid})
        await db.bank_transactions.delete_many({"copropriete_id": cid})


def test_find_duplicate_reordered_words():
    asyncio.run(_test_find_duplicate_reordered_words())


def test_merge_endpoint_e2e():
    asyncio.run(_test_merge_endpoint_e2e())
