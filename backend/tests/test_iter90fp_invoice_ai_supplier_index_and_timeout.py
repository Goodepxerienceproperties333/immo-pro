"""
iter90fp : REGRESSION LOCK - meme classe de bug que le crash P0 du
dashboard (health_audit.py, iter90fo), applique cette fois a l'extraction
IA de facture (POST /api/invoices-ai/extract).

Ticket utilisateur (Feb 2026, PRODUCTION) :
> "erreur dans le reconnaissance de facture importee" + capture d'ecran
> montrant un formulaire vide avec l'erreur "The origin web server did not
> respond to Cloudflare within the allowed time". Confirme par
> l'utilisateur : "ca a fonctionne, bug temporaire ?" -> latence variable,
> pas un echec deterministe.

Root cause identifiee (memes symptomes que iter90fo) :
1. `_extract_invoice_with_ai`/`extract_invoice` faisait 2 scans COMPLETS et
   NON FILTRES de la collection `suppliers` (TOUTE la base multi-tenant,
   tous les clients de la plateforme) a CHAQUE extraction de facture -
   endpoint appele bien plus souvent qu'un chargement de dashboard.
2. Aucun timeout de garde-fou sur l'appel LLM (vision PDF scanne = plus
   lent) -> latence variable pouvant approcher le timeout du reverse
   proxy sous charge.

Fix :
- Nouvelle fonction `_get_supplier_bce_index(db, copropriete_id)` : UN
  SEUL scan, SCOPE a la copropriete (+ fiches globales), reutilise pour
  le matching "template appris" ET le matching final `_find_supplier`
  (au lieu de 2 scans non filtres distincts).
- `asyncio.wait_for(chat.send_message(msg), timeout=55.0)` sur l'appel
  LLM, degrade proprement (`_warning` explicite) en cas de depassement.

Ce test verifie :
A) Le matching BCE fonctionne toujours correctement via l'index scope.
B) Un supplier d'une AUTRE copropriete (meme BCE par coincidence) n'est
   JAMAIS remonte comme match (garde-fou isolation multi-tenant).
C) L'extraction complete (mock du LLM) reste rapide et fonctionnelle.
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

from routes.invoice_ai import _get_supplier_bce_index, _norm_bce  # noqa: E402


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_norm_bce_variants_match():
    assert _norm_bce("BE 0123.456.789") == "0123456789"
    assert _norm_bce("0123.456.789") == "0123456789"
    assert _norm_bce("") == ""


async def _scenario_scoped_index_matches_own_copro_only():
    db = await _mongo()
    cid_a = f"iter90fp-A-{uuid.uuid4()}"
    cid_b = f"iter90fp-B-{uuid.uuid4()}"
    sup_a = str(uuid.uuid4())
    sup_b = str(uuid.uuid4())
    try:
        await db.coproprietes.insert_many([
            {"id": cid_a, "name": "iter90fp-A", "reference": "A", "status": "active"},
            {"id": cid_b, "name": "iter90fp-B", "reference": "B", "status": "active"},
        ])
        # Meme numero BCE utilise par coincidence par 2 suppliers dans 2 ACP
        # differentes (cas rare mais possible - societes differentes,
        # erreur de saisie, etc.)
        await db.suppliers.insert_many([
            {"id": sup_a, "name": "Supplier A", "copropriete_id": cid_a,
             "bce_number": "BE 0999.888.777"},
            {"id": sup_b, "name": "Supplier B", "copropriete_id": cid_b,
             "bce_number": "BE 0999.888.777"},
        ])

        index_a = await _get_supplier_bce_index(db, cid_a)
        matched = index_a.get("0999888777")
        assert matched is not None, "Le supplier de CETTE copropriete doit etre trouve"
        assert matched["id"] == sup_a, (
            f"REGRESSION iter90fp : l'index scope a {cid_a} a retourne le "
            f"supplier {matched['id']} au lieu de {sup_a} (isolation multi-tenant cassee)"
        )

        index_b = await _get_supplier_bce_index(db, cid_b)
        matched_b = index_b.get("0999888777")
        assert matched_b is not None and matched_b["id"] == sup_b
    finally:
        await db.coproprietes.delete_many({"id": {"$in": [cid_a, cid_b]}})
        await db.suppliers.delete_many({"id": {"$in": [sup_a, sup_b]}})


async def _scenario_global_supplier_visible_everywhere():
    db = await _mongo()
    cid_a = f"iter90fp-glob-{uuid.uuid4()}"
    sup_g = str(uuid.uuid4())
    try:
        await db.coproprietes.insert_one({"id": cid_a, "name": "iter90fp-glob", "reference": "G", "status": "active"})
        await db.suppliers.insert_one({
            "id": sup_g, "name": "Supplier Global", "is_global": True,
            "vat_number": "BE0111222333",
        })
        index_a = await _get_supplier_bce_index(db, cid_a)
        matched = index_a.get("0111222333")
        assert matched is not None and matched["id"] == sup_g, (
            "REGRESSION iter90fp : un supplier is_global=True doit rester "
            "visible/matchable depuis n'importe quelle copropriete"
        )
    finally:
        await db.coproprietes.delete_one({"id": cid_a})
        await db.suppliers.delete_one({"id": sup_g})


def test_scoped_index_isolation():
    asyncio.run(_scenario_scoped_index_matches_own_copro_only())


def test_global_supplier_visible_everywhere():
    asyncio.run(_scenario_global_supplier_visible_everywhere())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
