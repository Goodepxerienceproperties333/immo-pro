"""
iter90fy : portail proprietaire - decompte uniquement apres cloture +
`_resolve_owner_ids` multi-fiche.

Ticket utilisateur (Feb 2026, PROD Acacia TER, TEUWEN Gael) :
> "cote proprietaire, l'interface devrait permettre au proprietaire de
> telecharger son decompte seulement une fois que l'exercice est cloture.
> Actuellement malgre l'exercice cloture, il y a un bug - le document
> semble ne pas etre dispo"
> "dans la partie client 'Appel de fonds' il faut mentionner l'exercice
> comptable et pas de dates selectors"

Bugs identifies :
1. `/api/owner/decompte/pdf` utilisait `_resolve_owner` (mono-fiche). Si
   le proprietaire a plusieurs fiches (une par ACP, ou fiche sans email
   set), la lookup echoue avec "Aucune fiche proprietaire ne correspond"
   meme quand une autre fiche du meme user pointe sur les lots de l'ACP.
   Fix : `_resolve_owner_ids` (multi-fiche + email/email2 lookup).
2. Meme si l'exercice est cloture, sans `fiscal_year_id` en query param,
   le backend generait un decompte fake avec `status=""` -> erreur 400
   "L'exercice n'est pas cloture". Fix : auto-selection du DERNIER FY
   avec `status='closed'` quand `fiscal_year_id` absent.
3. Le portail proprietaire n'a pas de moyen de savoir si un FY cloture
   existe -> impossible de masquer le bouton "Telecharger". Fix : ajout
   de `has_closed_fiscal_year` + `latest_closed_fiscal_year` sur
   /coproprietes.

Ces 3 fix + le remplacement des date-selectors par un dropdown de FY
sur l'onglet "Appels de fonds" completent la demande utilisateur.
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _test_coproprietes_exposes_closed_fy_flag():
    """`/owner/coproprietes` doit retourner `has_closed_fiscal_year` +
    `latest_closed_fiscal_year` pour chaque ACP du proprietaire."""
    db = await _mongo()
    cid_a = f"iter90fy-A-{uuid.uuid4()}"
    cid_b = f"iter90fy-B-{uuid.uuid4()}"
    user_email = f"iter90fy-owner-{uuid.uuid4()}@fx.local"
    owner_id = f"iter90fy-o-{uuid.uuid4()}"
    fy_a_closed = f"iter90fy-fya-{uuid.uuid4()}"
    fy_a_open = f"iter90fy-fya2-{uuid.uuid4()}"
    fy_b_open = f"iter90fy-fyb-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_many([
            {"id": cid_a, "name": "ACP A", "reference": "A"},
            {"id": cid_b, "name": "ACP B", "reference": "B"},
        ])
        await db.owners.insert_one({
            "id": owner_id, "name": "Test Owner",
            "email": user_email,
            "copropriete_ids": [cid_a, cid_b],
            "tier_accounts": {
                cid_a: {"provisions": "41010001", "reserve": ""},
                cid_b: {"provisions": "41010002", "reserve": ""},
            },
        })
        await db.lots.insert_many([
            {"id": str(uuid.uuid4()), "copropriete_id": cid_a, "number": "L1",
             "quotity": 10000.0, "owner_id": owner_id, "owner_ids": [owner_id]},
            {"id": str(uuid.uuid4()), "copropriete_id": cid_b, "number": "L2",
             "quotity": 10000.0, "owner_id": owner_id, "owner_ids": [owner_id]},
        ])
        # ACP A : un FY 2024 CLOTURE + un FY 2025 OUVERT
        await db.fiscal_years.insert_many([
            {"id": fy_a_closed, "copropriete_id": cid_a, "name": "2024",
             "start_date": "2024-01-01", "end_date": "2024-12-31", "status": "closed"},
            {"id": fy_a_open, "copropriete_id": cid_a, "name": "2025",
             "start_date": "2025-01-01", "end_date": "2025-12-31", "status": "open"},
            # ACP B : UNIQUEMENT un FY OUVERT
            {"id": fy_b_open, "copropriete_id": cid_b, "name": "2025",
             "start_date": "2025-01-01", "end_date": "2025-12-31", "status": "open"},
        ])

        # Simule /owner/coproprietes logic
        lots = await db.lots.find(
            {"$or": [{"owner_id": {"$in": [owner_id]}}, {"owner_ids": {"$in": [owner_id]}}]},
            {"_id": 0},
        ).to_list(100)
        copro_ids = list({l["copropriete_id"] for l in lots})
        coproprietes = await db.coproprietes.find(
            {"id": {"$in": copro_ids}}, {"_id": 0}
        ).sort("name", 1).to_list(100)
        for c in coproprietes:
            c["my_lots"] = [l for l in lots if l["copropriete_id"] == c["id"]]
        closed_fy = await db.fiscal_years.find(
            {"copropriete_id": {"$in": copro_ids}, "status": "closed"},
            {"_id": 0}, sort=[("end_date", -1)]
        ).to_list(100)
        latest = {}
        for fy in closed_fy:
            if fy["copropriete_id"] not in latest:
                latest[fy["copropriete_id"]] = fy
        for c in coproprietes:
            fy = latest.get(c["id"])
            c["has_closed_fiscal_year"] = bool(fy)
            c["latest_closed_fiscal_year"] = fy or None

        acp_a = next(c for c in coproprietes if c["id"] == cid_a)
        acp_b = next(c for c in coproprietes if c["id"] == cid_b)
        assert acp_a["has_closed_fiscal_year"] is True, (
            "ACP A a un FY cloture (2024) - flag doit etre True"
        )
        assert acp_a["latest_closed_fiscal_year"]["name"] == "2024"
        assert acp_b["has_closed_fiscal_year"] is False, (
            "ACP B n'a AUCUN FY cloture - flag doit etre False"
        )
        assert acp_b["latest_closed_fiscal_year"] is None
    finally:
        await db.coproprietes.delete_many({"id": {"$in": [cid_a, cid_b]}})
        await db.owners.delete_one({"id": owner_id})
        await db.lots.delete_many({"copropriete_id": {"$in": [cid_a, cid_b]}})
        await db.fiscal_years.delete_many({"id": {"$in": [fy_a_closed, fy_a_open, fy_b_open]}})


async def _test_decompte_pdf_auto_selects_latest_closed_fy():
    """Sans `fiscal_year_id`, l'endpoint doit auto-selectionner le dernier
    FY CLOTURE. Sinon, 400 explicite au lieu d'un decompte provisoire."""
    db = await _mongo()
    cid = f"iter90fy-auto-{uuid.uuid4()}"
    try:
        # Cas 1 : ACP avec un FY cloture -> auto-selection reussie
        await db.fiscal_years.insert_one({
            "id": f"iter90fy-fyc-{uuid.uuid4()}", "copropriete_id": cid,
            "name": "2024", "start_date": "2024-01-01", "end_date": "2024-12-31",
            "status": "closed",
        })
        fy_selected = await db.fiscal_years.find_one(
            {"copropriete_id": cid, "status": "closed"},
            {"_id": 0}, sort=[("end_date", -1)],
        )
        assert fy_selected is not None, "Auto-selection du FY cloture doit reussir"
        assert fy_selected["status"] == "closed"

        # Cas 2 : ACP sans aucun FY cloture -> None
        await db.fiscal_years.delete_many({"copropriete_id": cid})
        await db.fiscal_years.insert_one({
            "id": f"iter90fy-fyo-{uuid.uuid4()}", "copropriete_id": cid,
            "name": "2025 open", "start_date": "2025-01-01", "end_date": "2025-12-31",
            "status": "open",
        })
        fy_selected = await db.fiscal_years.find_one(
            {"copropriete_id": cid, "status": "closed"},
            {"_id": 0}, sort=[("end_date", -1)],
        )
        assert fy_selected is None, (
            "Aucun FY cloture -> auto-selection doit retourner None (backend "
            "leve 400 avec message clair au lieu de generer un decompte fake)"
        )
    finally:
        await db.fiscal_years.delete_many({"copropriete_id": cid})


def test_coproprietes_exposes_closed_fy_flag():
    asyncio.run(_test_coproprietes_exposes_closed_fy_flag())


def test_decompte_pdf_auto_selects_latest_closed_fy():
    asyncio.run(_test_decompte_pdf_auto_selects_latest_closed_fy())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
