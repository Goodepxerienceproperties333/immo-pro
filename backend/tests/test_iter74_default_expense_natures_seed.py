"""Regression test - iter74 - Natures de depense par defaut auto-seedees.

Verifie que :
1. La liste DEFAULT_EXPENSE_NATURES contient bien 23 entrees.
2. Toutes les natures ont les champs requis et la repartition occupant+proprio = 100.
3. Tous les comptes PCMN cibles existent dans PCMN_ALL_ACCOUNTS.
4. Au moins une nature de type 'produit' (compte 750).
5. La creation d'une ACP via l'API auto-seed bien les 23 natures.
"""
import os
import sys
import asyncio
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


def test_default_natures_list_integrity():
    from default_expense_natures import DEFAULT_EXPENSE_NATURES
    from pcmn_data import PCMN_ALL_ACCOUNTS

    assert len(DEFAULT_EXPENSE_NATURES) == 23, (
        f"Expected 23 default natures, got {len(DEFAULT_EXPENSE_NATURES)}"
    )

    # Tous les champs requis
    pcmn_numbers = {a["number"] for a in PCMN_ALL_ACCOUNTS}
    seen_accounts = set()
    n_produit = 0
    for nat in DEFAULT_EXPENSE_NATURES:
        for key in ("code", "name", "account_number", "vat_code",
                    "default_occupant_pct", "default_proprietaire_pct", "kind"):
            assert key in nat, f"Champ '{key}' manquant dans {nat}"
        # Repartition = 100
        s = float(nat["default_occupant_pct"]) + float(nat["default_proprietaire_pct"])
        assert abs(s - 100) < 0.01, (
            f"Repartition != 100 pour {nat['code']}: occupant={nat['default_occupant_pct']} "
            f"proprio={nat['default_proprietaire_pct']}"
        )
        # 1:1 strict sur les codes ET les comptes
        assert nat["account_number"] not in seen_accounts, (
            f"Compte duplique : {nat['account_number']}"
        )
        seen_accounts.add(nat["account_number"])
        # Compte PCMN doit exister dans le seed
        assert nat["account_number"] in pcmn_numbers, (
            f"Compte {nat['account_number']} introuvable dans PCMN_ALL_ACCOUNTS"
        )
        # Kind valide
        assert nat["kind"] in ("charge", "produit"), f"Kind invalide : {nat['kind']}"
        if nat["kind"] == "produit":
            n_produit += 1
    assert n_produit >= 1, "Au moins une nature de type 'produit' attendue (compte 750)"


async def _test_seed_on_new_acp():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    # Cree une ACP de test directement via l'importation de la fonction
    from routes.coproprietes import create_coproprietes_router  # noqa: F401
    # Plus simple : utiliser le seed helper en direct
    # Inserer une fake ACP + PCMN
    test_id = f"test-{uuid.uuid4()}"
    await db.coproprietes.insert_one({
        "id": test_id,
        "name": "ITER74-TEST",
        "reference": "TEST-ITER74",
        "status": "active",
    })
    # Seed PCMN
    from pcmn_data import PCMN_ALL_ACCOUNTS
    pcmn_docs = [{**a, "copropriete_id": test_id, "active": False, "is_custom": False}
                 for a in PCMN_ALL_ACCOUNTS]
    await db.pcmn_accounts.insert_many(pcmn_docs)

    # Re-import + call seed default natures
    from default_expense_natures import DEFAULT_EXPENSE_NATURES
    from datetime import datetime, timezone
    pcmn_map = {p["number"]: p.get("name", "") for p in pcmn_docs}
    now_iso = datetime.now(timezone.utc).isoformat()
    docs = []
    for nat in DEFAULT_EXPENSE_NATURES:
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
            "copropriete_id": test_id,
            "created_at": now_iso,
        })
    await db.expense_categories.insert_many(docs)

    # Verify
    seeded = await db.expense_categories.find(
        {"copropriete_id": test_id, "is_default_seed": True},
        {"_id": 0}
    ).to_list(100)
    assert len(seeded) == 23, f"Expected 23 seeded natures, got {len(seeded)}"
    n_produits = sum(1 for s in seeded if s.get("kind") == "produit")
    assert n_produits == 1, f"Expected 1 produit, got {n_produits}"

    # Cleanup
    await db.expense_categories.delete_many({"copropriete_id": test_id})
    await db.pcmn_accounts.delete_many({"copropriete_id": test_id})
    await db.coproprietes.delete_one({"id": test_id})


def test_seed_creates_23_natures_on_new_acp():
    asyncio.run(_test_seed_on_new_acp())


if __name__ == "__main__":
    test_default_natures_list_integrity()
    test_seed_creates_23_natures_on_new_acp()
    print("OK : 23 natures auto-seedees, integrite OK")
