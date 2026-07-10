"""
iter90cy : REGRESSION LOCK - Situation de compte (interface + PDF) ne doit
PAS perdre de lignes quand un proprietaire possede plusieurs lots avec la
MEME quote-part (donc le MEME montant de debit) dans une seule ecriture VE.

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "je constate que le solde de Matexi est correcte et correspond a
> 19000+1500+5200 c'est juste le detail de la balance qui n'est pas
> correcte [...] erreur dans le layout des apercus PDF et dans l'interface"

Root cause : `_group_movements_by_owner` / le dedup `seen_lines` dans
`situation_compte_owner`, `_build_situation_compte_pdf` et
`situation_compte_supplier` utilisaient une cle
(entry_id, account_number, debit, credit, third_party_id) SANS l'index de
la ligne. Un promoteur avec N lots ayant la MEME quotite genere N lignes
debit identiques (meme montant) dans la MEME ecriture VE -> ces lignes
"identiques" etaient a tort traitees comme des DOUBLONS et une partie du
montant etait perdue dans la vue detaillee / le PDF, alors que le total
reel (grand livre, "Solde" recalcule independamment) restait correct.

Fix iter90cy : la cle de dedup inclut desormais l'INDEX de la ligne dans
`e.get("lines")`, ce qui distingue 2 lignes legitimement separees meme si
leurs valeurs (compte/debit/credit/tiers) sont identiques.
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _scenario_two_identical_lot_lines_not_dropped():
    """Promoteur (Matexi) possede 2 lots de MEME quotite -> l'appel de fonds
    genere 2 lignes debit IDENTIQUES (meme montant/compte/tiers) dans la
    meme ecriture VE. La situation de compte doit sommer les 2 (pas 1)."""
    db = await _mongo()
    cid = f"iter90cy-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"
    acc_prov = "4100099"

    await db.coproprietes.insert_one({
        "id": cid, "name": "iter90cy Test", "reference": "iter90cy", "status": "active",
    })
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "last_name": "Matexi",
        "auxiliary_code": "M001", "copropriete_ids": [cid],
        "tier_accounts": {cid: {"provisions": acc_prov}},
    })
    lot1_id, lot2_id = f"lot-{uuid.uuid4()}", f"lot-{uuid.uuid4()}"
    await db.lots.insert_many([
        {"id": lot1_id, "number": "001", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 5000.0},
        {"id": lot2_id, "number": "002", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 5000.0},
    ])

    entry_id = str(uuid.uuid4())
    # 2 lignes debit IDENTIQUES (meme montant car meme quotite) + 1 credit
    # equilibrant : reproduit exactement generate_sale_entry() pour un
    # proprietaire multi-lots a quotites egales.
    await db.journal_entries.insert_one({
        "id": entry_id, "journal_type": "VE", "date": "2025-10-01",
        "reference": "AF-Fonds de reserve - A",
        "description": "Appel fonds de reserve - Fonds de reserve",
        "lines": [
            {"account_number": acc_prov, "account_name": "Fonds reserve - Matexi",
             "debit": 750.0, "credit": 0.0, "third_party_id": matexi_id,
             "third_party_name": "Matexi",
             "line_description": "Appel fonds de reserve - Fonds de reserve"},
            {"account_number": acc_prov, "account_name": "Fonds reserve - Matexi",
             "debit": 750.0, "credit": 0.0, "third_party_id": matexi_id,
             "third_party_name": "Matexi",
             "line_description": "Appel fonds de reserve - Fonds de reserve"},
            {"account_number": "160", "account_name": "Fonds de reserve",
             "debit": 0.0, "credit": 1500.0, "third_party_id": None, "third_party_name": ""},
        ],
        "total_debit": 1500.0, "total_credit": 1500.0,
        "copropriete_id": cid, "auto_generated": True,
        "source_type": "fund_call", "source_id": str(uuid.uuid4()),
    })

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            # Vue "resumee" (group_by_owner=True, defaut interface)
            r = await client.get(
                f"{BACKEND_URL}/api/reports/balance-tiers/owners/{matexi_id}",
                headers=hdr, params={"copropriete_id": cid},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["total_debit"] == 1500.0, (
                f"REGRESSION iter90cy : total_debit devrait etre 1500.0 "
                f"(2 lignes de 750 sommees), obtenu {data['total_debit']} "
                f"(bug de dedup faux-positif si 750.0)"
            )
            assert data["balance"] == 1500.0, (
                f"REGRESSION iter90cy : balance (solde reel) devrait etre "
                f"1500.0, obtenu {data['balance']}"
            )
            # Le total_debit (movements deduped) doit matcher le balance
            # (recalcule independamment) -> plus d'ecart entre les 2.
            assert data["total_debit"] == data["balance"], (
                f"REGRESSION iter90cy : total_debit ({data['total_debit']}) "
                f"doit matcher balance ({data['balance']})"
            )

            # Vue "detail par lot" (group_by_owner=False)
            r2 = await client.get(
                f"{BACKEND_URL}/api/reports/balance-tiers/owners/{matexi_id}",
                headers=hdr, params={"copropriete_id": cid, "group_by_owner": False},
            )
            assert r2.status_code == 200, r2.text
            data2 = r2.json()
            assert data2["total_debit"] == 1500.0, (
                f"REGRESSION iter90cy (detail non groupe) : total_debit "
                f"devrait etre 1500.0, obtenu {data2['total_debit']}"
            )
            assert len(data2["movements"]) == 2, (
                f"REGRESSION iter90cy : les 2 lignes distinctes (meme "
                f"montant) doivent apparaitre separement en mode detail, "
                f"obtenu {len(data2['movements'])} mouvement(s)"
            )
    finally:
        await db.coproprietes.delete_one({"id": cid})
        await db.owners.delete_one({"id": matexi_id})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.journal_entries.delete_one({"id": entry_id})


def test_two_identical_lot_lines_not_dropped():
    asyncio.run(_scenario_two_identical_lot_lines_not_dropped())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
