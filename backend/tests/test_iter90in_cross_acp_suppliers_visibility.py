"""iter90in : Verrouille la visibilite cross-ACP des fournisseurs partages
via `tier_accounts.<copro_id>`.

Contexte : un fournisseur peut etre rattache a plusieurs ACPs sous un meme
syndic. Historiquement il est cree dans une ACP (`copropriete_id = ACP-A`)
puis "etendu" a d'autres ACPs via l'ajout d'entrees dans le dict
`tier_accounts` (ex: `tier_accounts.ACP-B = {main: '44000099'}`).

Bug (pre-fix) :
- `health_audit.py` (ligne 167) requetait `db.suppliers.find({"copropriete_id": copro_id})`.
  Consequence : le fournisseur partage etait invisible pour ACP-B et son
  compte tier 44000099 (utilise dans des JE de ACP-B) etait marque a tort
  comme ORPHELIN dans le rapport de sante comptable.
- `routes/reports.py::balance_tiers_suppliers` (ligne 3062) chargeait TOUS
  les fournisseurs globalement (`find({}, ...)`) mais le fallback par NOM
  filtrait ensuite sur `copropriete_id == copro_id` -> les fournisseurs
  partages via `tier_accounts` n'etaient pas indexes dans `name_to_supplier`
  et n'etaient pas resolus quand le JE avait `account_name` mais pas de
  `third_party_id` (cas classique des AN d'ouverture).

Fix : appliquer le pattern `$or` deja utilise dans
`routes/suppliers.py::find_duplicate_supplier` :

    {"$or": [
        {"copropriete_id": copropriete_id},
        {f"tier_accounts.{copropriete_id}": {"$exists": True}},
    ]}
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


import pytest  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1 : health_audit ne signale plus le compte tier partage comme orphelin
# ---------------------------------------------------------------------------
@pytest.mark.skip(reason="OBSOLETE iter90is (Chinese Wall strict) : le partage "
                         "cross-ACP via tier_accounts a ete supprime. Cf. "
                         "test_iter90is_chinese_wall_strict.py.")
def test_health_audit_does_not_flag_shared_supplier_as_orphan():
    """iter90in-1 : un fournisseur rattache a ACP-A via `tier_accounts.ACP-A`
    (mais dont `copropriete_id` pointe sur ACP-B) doit etre reconnu par
    l'audit de sante de ACP-A. Son compte tier 44000099 utilise dans un JE
    de ACP-A NE DOIT PAS apparaitre dans la liste des orphelins."""
    suffix = uuid.uuid4().hex[:8]
    acp_a = f"acp-a-in-{suffix}"
    acp_b = f"acp-b-in-{suffix}"
    tier_acc = "44000099"

    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from health_audit import compute_health_audit
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            # Fournisseur "primaire" sur ACP-B mais partage vers ACP-A via tier_accounts.
            supp_id = f"supp-shared-{suffix}"
            await db.suppliers.insert_one({
                "id": supp_id,
                "name": f"SharedSupplier-{suffix}",
                "copropriete_id": acp_b,
                "tier_accounts": {
                    acp_a: {"main": tier_acc},
                    acp_b: {"main": tier_acc},
                },
            })
            # JE dans ACP-A qui utilise le compte tier partage.
            await db.journal_entries.insert_one({
                "id": f"je-{suffix}",
                "copropriete_id": acp_a,
                "journal_type": "AC",
                "date": "2026-01-15",
                "reference": "AC-IN-001",
                "total_debit": 100.0,
                "total_credit": 100.0,
                "lines": [
                    {"account_number": "61210", "account_name": "Elec", "debit": 100.0, "credit": 0.0},
                    {"account_number": tier_acc, "account_name": f"SharedSupplier-{suffix}",
                     "debit": 0.0, "credit": 100.0, "third_party_id": supp_id},
                ],
            })

            report = await compute_health_audit(db, acp_a)
            orphan_anomaly = next(
                (a for a in report["anomalies"] if a["category"] == "orphans"),
                None,
            )
            orphan_accounts = []
            if orphan_anomaly:
                orphan_accounts = [i["account"] for i in orphan_anomaly.get("items", [])]
            assert tier_acc not in orphan_accounts, (
                f"Le compte tier partage {tier_acc} NE DOIT PAS etre flag orphelin "
                f"(fournisseur present via tier_accounts.{acp_a}). "
                f"Report: stats={report['stats']}, orphans={orphan_accounts}"
            )
        finally:
            # Nettoyage
            await db.suppliers.delete_many({"id": {"$in": [f"supp-shared-{suffix}"]}})
            await db.journal_entries.delete_many({"copropriete_id": {"$in": [acp_a, acp_b]}})
            client.close()

    _run(_go())


# ---------------------------------------------------------------------------
# Test 2 : health_audit continue de detecter les VRAIS orphelins
# ---------------------------------------------------------------------------
def test_health_audit_still_flags_truly_orphan_account():
    """iter90in-2 : un compte 44XXXXX utilise dans un JE MAIS sans aucun
    supplier (ni via copropriete_id, ni via tier_accounts) doit rester
    detecte comme orphelin. Regression check du fix."""
    suffix = uuid.uuid4().hex[:8]
    acp = f"acp-in2-{suffix}"
    orphan_acc = "44099998"

    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from health_audit import compute_health_audit
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            await db.journal_entries.insert_one({
                "id": f"je-{suffix}",
                "copropriete_id": acp,
                "journal_type": "AC",
                "date": "2026-01-15",
                "reference": "AC-IN-002",
                "total_debit": 50.0,
                "total_credit": 50.0,
                "lines": [
                    {"account_number": "61210", "account_name": "Elec", "debit": 50.0, "credit": 0.0},
                    {"account_number": orphan_acc, "account_name": "MysteryVendor",
                     "debit": 0.0, "credit": 50.0},
                ],
            })
            report = await compute_health_audit(db, acp)
            orphan_anomaly = next(
                (a for a in report["anomalies"] if a["category"] == "orphans"),
                None,
            )
            assert orphan_anomaly is not None, "Un orphelin reel doit etre detecte"
            orphan_accounts = [i["account"] for i in orphan_anomaly.get("items", [])]
            assert orphan_acc in orphan_accounts, (
                f"Le compte {orphan_acc} sans aucune fiche fournisseur (ni "
                f"copropriete_id ni tier_accounts) doit rester detecte orphelin. "
                f"Detectes: {orphan_accounts}"
            )
        finally:
            await db.journal_entries.delete_many({"copropriete_id": acp})
            client.close()

    _run(_go())


# ---------------------------------------------------------------------------
# Test 3 : la query MongoDB elle-meme (contrat $or)
# ---------------------------------------------------------------------------
def test_supplier_query_uses_or_pattern_health_audit():
    """iter90in-3 : verifie qu'un supplier partage via tier_accounts est
    bien recupere par le pattern $or (contrat MongoDB pur, sans passer par
    compute_health_audit). Verrouille la construction de la requete."""
    suffix = uuid.uuid4().hex[:8]
    acp_a = f"acp-q-{suffix}"
    acp_b = f"acp-qb-{suffix}"

    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            # Un supplier "propriete" ACP-A
            sup_a = f"sup-a-{suffix}"
            await db.suppliers.insert_one({
                "id": sup_a, "name": f"OwnerA-{suffix}",
                "copropriete_id": acp_a,
                "tier_accounts": {acp_a: {"main": "44000001"}},
            })
            # Un supplier ACP-B partage vers ACP-A
            sup_shared = f"sup-shared-{suffix}"
            await db.suppliers.insert_one({
                "id": sup_shared, "name": f"Shared-{suffix}",
                "copropriete_id": acp_b,
                "tier_accounts": {
                    acp_a: {"main": "44000099"},
                    acp_b: {"main": "44000050"},
                },
            })
            # Un supplier totalement etranger a ACP-A
            sup_alien = f"sup-alien-{suffix}"
            await db.suppliers.insert_one({
                "id": sup_alien, "name": f"Alien-{suffix}",
                "copropriete_id": f"acp-alien-{suffix}",
                "tier_accounts": {f"acp-alien-{suffix}": {"main": "44000030"}},
            })

            # Le pattern $or attendu (celui applique dans health_audit / reports).
            q = {"$or": [
                {"copropriete_id": acp_a},
                {f"tier_accounts.{acp_a}": {"$exists": True}},
            ]}
            found = await db.suppliers.find(q, {"_id": 0, "id": 1}).to_list(100)
            found_ids = {f["id"] for f in found}

            assert sup_a in found_ids, "Le supplier proprietaire de ACP-A doit apparaitre"
            assert sup_shared in found_ids, (
                "Le supplier partage via tier_accounts.ACP-A doit apparaitre "
                "(pattern $or). Sans le fix, ce test echoue."
            )
            assert sup_alien not in found_ids, (
                "Un supplier etranger a ACP-A ne doit PAS remonter (chinese wall)"
            )
        finally:
            await db.suppliers.delete_many({"id": {"$in": [sup_a, sup_shared, sup_alien]}})
            client.close()

    _run(_go())


# ---------------------------------------------------------------------------
# Test 4 : balance_tiers_suppliers indexe les noms des suppliers partages
# ---------------------------------------------------------------------------
@pytest.mark.skip(reason="OBSOLETE iter90is (Chinese Wall strict) : le partage "
                         "cross-ACP via tier_accounts a ete supprime. Cf. "
                         "test_iter90is_chinese_wall_strict.py.")
def test_balance_tiers_suppliers_indexes_shared_supplier_by_name():
    """iter90in-4 : verifie que `balance_tiers_suppliers` inclut, dans son
    index `name_to_supplier` (utilise pour le fallback matching account_name),
    les suppliers rattaches via `tier_accounts.<copro_id>`. Le test simule
    un JE d'ouverture (AN) qui contient `account_name = 'Shared-XYZ'` mais
    PAS de `third_party_id`. Sans le fix, la balance ignore ce supplier ;
    avec le fix, il apparait avec le bon solde."""
    suffix = uuid.uuid4().hex[:8]
    acp_a = f"acp-bt-{suffix}"
    acp_b = f"acp-btb-{suffix}"
    tier_acc = "44000099"

    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            sup_id = f"sup-shared-bt-{suffix}"
            supplier_name = f"Shared-BT-{suffix}"
            # Supplier partage : appartient a ACP-B mais rattache aussi a ACP-A
            await db.suppliers.insert_one({
                "id": sup_id,
                "name": supplier_name,
                "copropriete_id": acp_b,  # <-- pas ACP-A !
                "bce_number": f"BE{uuid.uuid4().int % 10**10:010d}",
                "tier_accounts": {
                    acp_a: {"main": tier_acc},
                    acp_b: {"main": "44000050"},
                },
            })
            # JE d'ouverture (AN legacy) : `account_name` present, PAS de tpid.
            # Ce cas est celui qui casse en pre-fix : le matching par nom
            # tombe a l'eau car le supplier n'est pas dans name_to_supplier.
            await db.journal_entries.insert_one({
                "id": f"je-an-{suffix}",
                "copropriete_id": acp_a,
                "journal_type": "AN",
                "date": "2025-10-01",
                "reference": "AN-IN-001",
                "total_debit": 250.0,
                "total_credit": 250.0,
                "lines": [
                    {"account_number": "10000", "account_name": "Capital",
                     "debit": 250.0, "credit": 0.0},
                    # Ligne fournisseur : `account_name` = nom canonique du
                    # supplier, PAS de third_party_id (legacy import).
                    {"account_number": tier_acc, "account_name": supplier_name,
                     "debit": 0.0, "credit": 250.0},
                ],
            })

            # Extraction de l'endpoint via son router (pas de FastAPI runtime).
            from routes.reports import create_reports_router
            router = create_reports_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/reports/balance-tiers/suppliers":
                    endpoint = r.endpoint
                    break
            assert endpoint is not None, "endpoint balance_tiers_suppliers introuvable"

            class _MockRequest:
                def __init__(self):
                    self.cookies = {}
                    self.headers = {}
                @property
                def state(self):
                    class _S: pass
                    return _S()

            data = await endpoint(_MockRequest(), copropriete_id=acp_a)
            suppliers_out = data.get("suppliers", [])
            found = next(
                (s for s in suppliers_out if s["supplier_name"] == supplier_name),
                None,
            )
            assert found is not None, (
                f"Le supplier partage '{supplier_name}' doit apparaitre "
                f"dans la balance de ACP-A grace au fallback name matching. "
                f"Suppliers renvoyes: {[s['supplier_name'] for s in suppliers_out]}"
            )
            # Solde crediteur cumulatif = 250 (une AN en credit)
            assert abs(found["balance"] - 250.0) < 0.01, (
                f"Solde attendu 250.0 (credit AN), obtenu {found['balance']}"
            )
            assert found["orphan"] is False, (
                "Le supplier trouve via tier_accounts NE DOIT PAS etre orphelin"
            )
        finally:
            await db.suppliers.delete_many({"id": sup_id})
            await db.journal_entries.delete_many({"copropriete_id": acp_a})
            client.close()

    _run(_go())
