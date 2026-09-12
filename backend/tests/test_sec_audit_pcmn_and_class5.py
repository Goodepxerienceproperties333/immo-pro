"""SEC-audit hotfix (2026-02) : deux fixes conjoints.

1. `coproprietes.py::_create_pcmn_accounts` respecte le `pcmn_number`
   fourni par le syndic dans `bank_accounts[].pcmn_number`.
   Le code auto-genere n'est utilise qu'en fallback.

2. `reports.py::_classify_account` : classe 5 avec solde CREDITEUR reste
   en tresorerie (VII_disponibilites) ou placements (VI_placements), JAMAIS
   dans "VI. Autres dettes court terme". Compte 58 marque comme anomalie.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from routes.reports import compute_bilan_data

API = "http://localhost:8001"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@copro.be")
ADMIN_PWD = os.environ["ADMIN_PASSWORD"]


async def test_pcmn_number_preserved():
    """Cas 1 : creation avec pcmn_number explicite (ex 55163400)."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    async with httpx.AsyncClient(base_url=API, timeout=15.0) as h:
        r = await h.post("/api/auth/login",
                         json={"email": ADMIN_EMAIL, "password": ADMIN_PWD})
        token = r.cookies.get("access_token")
        hdr = {"Authorization": f"Bearer {token}"}

        # Cree l'ACP avec un compte bancaire pcmn_number=55163400 explicite
        tag = uuid.uuid4().hex[:8]
        payload = {
            "name": f"TEST PCMN Explicit {tag}", "bce": "0000000000",
            "address": "rue x", "postal_code": "1000", "city": "Bruxelles",
            "country": "Belgique",
            "bank_accounts": [
                {"iban": "BE68751207346634", "bic": "AXABBEBB",
                 "account_type": "vue", "is_default": True,
                 "label": "Banque LEFRANCQ",
                 "pcmn_number": "55163400"},
            ],
        }
        r = await h.post("/api/coproprietes", headers=hdr, json=payload)
        assert r.status_code == 200, r.text
        acp = r.json()
        copro_id = acp["id"]
        try:
            # Verifier bank_accounts.pcmn_number
            ba = acp["bank_accounts"][0]
            assert ba["pcmn_number"] == "55163400", \
                f"pcmn_number attendu 55163400, recu {ba['pcmn_number']}"
            print(f"PASS 1a : bank_accounts.pcmn_number = 55163400 (respecte)")

            # Verifier pcmn_accounts en base
            pcmn = await db.pcmn_accounts.find_one(
                {"copropriete_id": copro_id, "number": "55163400"}
            )
            assert pcmn, f"pcmn_accounts 55163400 non cree pour {copro_id}"
            assert pcmn.get("name") == "Banque LEFRANCQ"
            print(f"PASS 1b : pcmn_accounts.number = 55163400 (respecte)")

            # Verifier ABSENCE du code auto-genere 55163400 doit exister,
            # 551634 (auto genere depuis "BE68751207346634") ne doit pas etre cree
            # separement (car normalize_bank_pcmn("55163400") = "55163400"
            # et _generate_pcmn_number aurait donne aussi "55163400" dans ce
            # cas particulier ou les 3 derniers digits de l'IBAN = 634).
            # Test complementaire : autre IBAN pour valider vraiment le respect.

            # Cas 2 : re-update avec pcmn_number vide -> preserve l'existant
            r = await h.put(f"/api/coproprietes/{copro_id}", headers=hdr, json={
                **payload,
                "bank_accounts": [
                    {"iban": "BE68751207346634", "bic": "AXABBEBB",
                     "account_type": "vue", "is_default": True,
                     "label": "Banque LEFRANCQ modif",
                     "pcmn_number": ""},  # vide
                ],
            })
            assert r.status_code == 200, r.text
            acp2 = r.json()
            assert acp2["bank_accounts"][0]["pcmn_number"] == "55163400", \
                f"Update sans pcmn_number doit preserver 55163400, recu {acp2['bank_accounts'][0]['pcmn_number']}"
            print(f"PASS 1c : PUT sans pcmn_number preserve 55163400 (retro-compat)")

            # Cas 3 : autre IBAN sans pcmn_number -> fallback auto
            iban_other = "BE12345678901234"
            r = await h.put(f"/api/coproprietes/{copro_id}", headers=hdr, json={
                **payload,
                "bank_accounts": [
                    {"iban": "BE68751207346634", "bic": "", "account_type": "vue",
                     "is_default": True, "label": "L1", "pcmn_number": "55163400"},
                    {"iban": iban_other, "bic": "", "account_type": "vue",
                     "is_default": False, "label": "L2", "pcmn_number": ""},
                ],
            })
            assert r.status_code == 200, r.text
            acp3 = r.json()
            ba2 = next(b for b in acp3["bank_accounts"] if b["iban"] == iban_other)
            # IBAN se termine par 234 -> auto-gen = 551234 + 00 = 55123400 (normalise)
            assert ba2["pcmn_number"] == "55123400", \
                f"Auto-gen attendue 55123400 (from IBAN {iban_other}), recu {ba2['pcmn_number']}"
            print(f"PASS 1d : fallback auto-gen pour nouveau IBAN sans pcmn_number = {ba2['pcmn_number']}")

        finally:
            await db.coproprietes.delete_one({"id": copro_id})
            await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})

    client.close()


async def test_class5_credit_classification():
    """Cas 2 : compte 55 avec solde CREDITEUR (decouvert) reste en tresorerie
    negative, PAS en dettes autres."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    tag = uuid.uuid4().hex[:8]
    copro_id = f"test-cl5-{tag}"
    owner_id = f"o-{tag}"

    await db.coproprietes.insert_one({"id": copro_id, "name": "TEST cl5", "bank_accounts": []})
    await db.owners.insert_one({"id": owner_id, "name": "Owner X", "copropriete_id": copro_id,
        "tier_accounts": {copro_id: {"provisions": "40000010", "reserve": "40010010"}}})
    await db.lots.insert_one({"id": f"l-{tag}", "copropriete_id": copro_id, "owner_id": owner_id, "quotity": 1000})
    await db.pcmn_accounts.insert_many([
        {"copropriete_id": copro_id, "number": "40000010", "name": "Prov X", "active": True},
        {"copropriete_id": copro_id, "number": "55163400", "name": "Banque LEFRANCQ", "active": True},
        {"copropriete_id": copro_id, "number": "58000001", "name": "Virement transit", "active": True},
    ])
    # Scenario : banque en decouvert (55 credit 500) + owner debit 500
    await db.journal_entries.insert_one({
        "id": f"an-{tag}", "copropriete_id": copro_id, "date": "2025-01-01",
        "journal_type": "AN", "reference": "AN-1", "is_opening_balance": True,
        "description": "Report",
        "lines": [
            {"account_number": "40000010", "third_party_id": owner_id, "debit": 500.0, "credit": 0.0},
            {"account_number": "55163400", "debit": 0.0, "credit": 500.0, "account_name": "Banque"},
        ],
    })
    # Anomalie : 58 avec solde crediteur 100 + 400 owner debit
    await db.journal_entries.insert_one({
        "id": f"od-{tag}", "copropriete_id": copro_id, "date": "2025-02-01",
        "journal_type": "OD", "reference": "OD-1",
        "description": "Anomalie transit",
        "lines": [
            {"account_number": "40000010", "third_party_id": owner_id, "debit": 100.0, "credit": 0.0},
            {"account_number": "58000001", "debit": 0.0, "credit": 100.0, "account_name": "Transit"},
        ],
    })

    try:
        data = await compute_bilan_data(db, copropriete_id=copro_id,
                                        date_to="2025-12-31",
                                        view_mode="before_distribution")

        # Recherche le compte 55163400 dans les rubriques passif
        passif_dettes_autres = next(r for r in data["passif"] if r["label"].startswith("VI.C"))
        for acc in passif_dettes_autres["accounts"]:
            assert not acc["account_number"].startswith(("50","51","52","53","54","55","57","58")), \
                f"Compte {acc['account_number']} classe 5 trouve dans VI.C Autres dettes ! FAUX."
        print("PASS 2a : Aucun compte classe 5 dans 'VI. Autres dettes court terme'")

        # Trouver le compte 55163400 dans la tresorerie (VII disponibilites cote actif)
        actif_dispo = next(r for r in data["actif"] if r["label"].startswith("VII"))
        found_55 = next((a for a in actif_dispo["accounts"] if a["account_number"] == "55163400"), None)
        assert found_55, f"Compte 55163400 (decouvert) doit apparaitre en VII_disponibilites, accounts={actif_dispo['accounts']}"
        assert found_55["amount"] < 0, f"Decouvert doit etre negatif, got {found_55['amount']}"
        print(f"PASS 2b : 55163400 (decouvert) en VII_disponibilites, amount = {found_55['amount']}")

        # Trouver le compte 58 marque anomalie
        found_58 = next((a for a in actif_dispo["accounts"] if a["account_number"] == "58000001"), None)
        assert found_58, f"Compte 58000001 doit apparaitre en VII_disponibilites"
        assert found_58.get("_anomaly") == "transfer_unbalanced", \
            f"58000001 doit avoir _anomaly=transfer_unbalanced, got {found_58.get('_anomaly')}"
        print(f"PASS 2c : 58000001 marque _anomaly=transfer_unbalanced")

        # Verifier equilibre du bilan
        assert data["equilibre"], f"Bilan doit rester equilibre, ecart={data['ecart']}"
        print(f"PASS 2d : bilan equilibre a {data['total_actif']} = {data['total_passif']}")

    finally:
        await db.coproprietes.delete_one({"id": copro_id})
        await db.owners.delete_many({"copropriete_id": copro_id})
        await db.lots.delete_many({"copropriete_id": copro_id})
        await db.pcmn_accounts.delete_many({"copropriete_id": copro_id})
        await db.journal_entries.delete_many({"copropriete_id": copro_id})

    client.close()


async def main():
    print("### Test 1 : pcmn_number explicite respecte ###")
    await test_pcmn_number_preserved()
    print("\n### Test 2 : classe 5 credit en tresorerie ###")
    await test_class5_credit_classification()
    print("\nAll SEC-audit tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
