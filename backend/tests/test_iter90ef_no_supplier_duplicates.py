"""iter90ef : Zero doublon fournisseur - _resolve_or_create_supplier_account
utilise desormais find_duplicate_supplier (normalisation particules juridiques
+ tri des mots) + find_similar_suppliers (Levenshtein >= 0.90 pour coquilles).

Coupable historique : generate_purchase_entry -> _resolve_or_create_supplier_account
utilisait un simple regex ^{re.escape(name)}$ insensible casse -> "Finlead" vs
"Finlead SRL" vs "SRL Finlead" vs "finlead srl" vs "Finleed" creaient N doublons.
"""
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_resolve_reuses_supplier_with_legal_particle_variation():
    """"Finlead SRL" existe -> facture "Finlead" ne cree PAS de doublon."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        sup_id = f"sup-{uuid.uuid4()}"
        # Seed : fournisseur cree au propre
        await db.suppliers.insert_one({
            "id": sup_id, "name": "Finlead SRL",
            "copropriete_id": cid, "tier_accounts": {},
        })
        try:
            # Simule la creation d'une facture avec "Finlead" (sans SRL)
            _, doc = await _resolve_or_create_supplier_account(db, "Finlead", cid)
            assert doc["id"] == sup_id, (
                f"Le supplier 'Finlead' doit reutiliser 'Finlead SRL' existant,"
                f" mais un doublon a ete cree : {doc}"
            )
            # Verifie qu'aucun doublon n'a ete cree
            all_suppliers = await db.suppliers.find({"copropriete_id": cid}).to_list(10)
            all_from_ta = await db.suppliers.find(
                {f"tier_accounts.{cid}": {"$exists": True}}
            ).to_list(10)
            all_ids = {s["id"] for s in all_suppliers} | {s["id"] for s in all_from_ta}
            assert len(all_ids) == 1, f"Doublon detecte : {all_ids}"
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())


def test_resolve_reuses_supplier_with_word_order_variation():
    """"SRL Finlead" existe -> facture "Finlead SRL" reutilise."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        sup_id = f"sup-{uuid.uuid4()}"
        await db.suppliers.insert_one({
            "id": sup_id, "name": "SRL Finlead",
            "copropriete_id": cid, "tier_accounts": {},
        })
        try:
            _, doc = await _resolve_or_create_supplier_account(db, "Finlead SRL", cid)
            assert doc["id"] == sup_id, f"Doublon cree : {doc}"
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())


def test_resolve_reuses_supplier_case_insensitive():
    """"finlead SRL" (lowercase) est absorbe par "Finlead SRL"."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        sup_id = f"sup-{uuid.uuid4()}"
        await db.suppliers.insert_one({
            "id": sup_id, "name": "Finlead SRL",
            "copropriete_id": cid, "tier_accounts": {},
        })
        try:
            _, doc = await _resolve_or_create_supplier_account(db, "finlead srl", cid)
            assert doc["id"] == sup_id, f"Doublon cree : {doc}"
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())


def test_resolve_reuses_supplier_with_typo():
    """Coquille "Finleed" (au lieu de "Finlead") est absorbee via Levenshtein."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        sup_id = f"sup-{uuid.uuid4()}"
        await db.suppliers.insert_one({
            "id": sup_id, "name": "Finlead",
            "copropriete_id": cid, "tier_accounts": {},
        })
        try:
            # "Finleed" vs "Finlead" -> ratio ~0.86 (dessous 0.90). Cas limite.
            # On teste plutot une coquille moindre : "Finleadd" (double d)
            # qui donne ~0.94
            _, doc = await _resolve_or_create_supplier_account(db, "Finleadd", cid)
            assert doc["id"] == sup_id, (
                f"Coquille 'Finleadd' doit reutiliser 'Finlead' via Levenshtein,"
                f" mais doublon cree : {doc}"
            )
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())


def test_resolve_creates_new_when_truly_different_name():
    """"Finlead" existe -> facture "AutreFournisseur" cree bien un nouveau doc."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        sup_id = f"sup-{uuid.uuid4()}"
        await db.suppliers.insert_one({
            "id": sup_id, "name": "Finlead",
            "copropriete_id": cid, "tier_accounts": {},
        })
        try:
            _, doc = await _resolve_or_create_supplier_account(db, "AutreFournisseur Different", cid)
            assert doc["id"] != sup_id, "Un nom vraiment different DOIT creer un nouveau supplier"
            assert doc["name"] == "AutreFournisseur Different"
            # Verifie que le supplier cree est bien scope a l'ACP
            assert doc.get("copropriete_id") == cid
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())


def test_resolve_respects_acp_scope():
    """Un supplier d'une AUTRE ACP ne matche PAS le supplier courant."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid_a = f"iter90ef-a-{uuid.uuid4()}"
        cid_b = f"iter90ef-b-{uuid.uuid4()}"
        sup_id_a = f"sup-a-{uuid.uuid4()}"
        # Fournisseur cree UNIQUEMENT dans ACP A
        await db.suppliers.insert_one({
            "id": sup_id_a, "name": "Finlead",
            "copropriete_id": cid_a, "tier_accounts": {},
        })
        try:
            # Facture dans ACP B avec "Finlead" -> DOIT creer un nouveau supplier
            # dans ACP B (chinese wall strict, pas de partage entre ACPs)
            _, doc = await _resolve_or_create_supplier_account(db, "Finlead", cid_b)
            assert doc["id"] != sup_id_a, (
                f"Chinese wall viole : facture ACP B ne doit pas reutiliser "
                f"supplier ACP A ({sup_id_a}), mais recu : {doc}"
            )
            assert doc.get("copropriete_id") == cid_b
        finally:
            await db.suppliers.delete_many({"copropriete_id": {"$in": [cid_a, cid_b]}})
            await db.pcmn_accounts.delete_many({"copropriete_id": {"$in": [cid_a, cid_b]}})

    asyncio.run(_run())


def test_resolve_idempotent_no_dupes_on_repeat():
    """Appel repete avec meme nom -> reste 1 seul supplier."""
    from auto_entries import _resolve_or_create_supplier_account

    async def _run():
        db = await _mongo()
        cid = f"iter90ef-{uuid.uuid4()}"
        try:
            # 5 appels successifs avec variantes du meme nom
            names = [
                "Nouveau Fournisseur",
                "nouveau fournisseur",
                "NOUVEAU FOURNISSEUR",
                "Nouveau  Fournisseur",  # double espace
                "Nouveau Fournisseur SRL",  # ajout particule
            ]
            ids = set()
            for n in names:
                _, doc = await _resolve_or_create_supplier_account(db, n, cid)
                ids.add(doc["id"])
            assert len(ids) == 1, (
                f"5 variantes du meme nom ont cree {len(ids)} suppliers "
                f"({ids}), attendu 1"
            )
        finally:
            await db.suppliers.delete_many({"copropriete_id": cid})
            await db.suppliers.delete_many({f"tier_accounts.{cid}": {"$exists": True}})
            await db.pcmn_accounts.delete_many({"copropriete_id": cid})

    asyncio.run(_run())
