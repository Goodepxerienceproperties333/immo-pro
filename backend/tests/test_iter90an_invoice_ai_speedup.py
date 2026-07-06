"""
Iter90an : Acceleration reconnaissance IA factures.

Optimisations validees :
1. Path texte utilise `claude-haiku-4-5-20251001` (3-4x plus rapide que Sonnet).
2. Path vision (scan) utilise toujours `claude-sonnet-4-5-20250929` (qualite).
3. max_chars 4000 (au lieu de 8000) pour l'extraction texte PDF.
4. Liste PCMN limitee a 40 comptes (au lieu de 60).
5. Cache in-memory PCMN par ACP (TTL 5 min) - evite refetch a chaque extract.
6. Post-processing DB parallelise via asyncio.gather.

Ce test unitaire valide (2) et (5). Le path IA reel n'est pas testable en unit
(besoin API key + PDF reel + reponse Claude non-deterministe) mais la logique
autour est validable.
"""
import asyncio
import os
import sys
import uuid
import time
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _run_cache_hit_avoids_refetch():
    """Verifie que _get_pcmn_cached met en cache et evite un refetch DB."""
    from routes.invoice_ai import _get_pcmn_cached, _PCMN_CACHE

    db = await _mongo()
    cid = f"iter90an-cache-{uuid.uuid4()}"
    # Seed 3 comptes classe 6 + 2 comptes classe 4
    await db.pcmn_accounts.insert_many([
        {"number": "611000", "name": "Entretien", "class_num": 6, "copropriete_id": cid},
        {"number": "612000", "name": "Honoraires", "class_num": 6, "copropriete_id": cid},
        {"number": "613000", "name": "Assurances", "class_num": 6, "copropriete_id": cid},
        {"number": "400001", "name": "Fournisseur X", "class_num": 4, "copropriete_id": cid},
        {"number": "410001", "name": "Owner Y", "class_num": 4, "copropriete_id": cid},
    ])
    try:
        # Vider cache pour un test propre
        _PCMN_CACHE.clear()

        # Premier appel = cache miss, doit fetcher DB
        class6, valid_accs = await _get_pcmn_cached(db, cid)
        assert len(class6) == 3
        assert {p["number"] for p in class6} == {"611000", "612000", "613000"}
        # valid_accs contient TOUS les comptes (toutes classes)
        assert valid_accs == {"611000", "612000", "613000", "400001", "410001"}

        # Cache stocke
        assert cid in [k for k in _PCMN_CACHE.keys()]

        # Deuxieme appel = cache hit, meme resultat (test rapide via time)
        t0 = time.time()
        class6_bis, valid_bis = await _get_pcmn_cached(db, cid)
        elapsed = time.time() - t0
        assert class6_bis == class6
        assert valid_bis == valid_accs
        # Cache hit doit etre quasi-instantane (< 20ms)
        assert elapsed < 0.02, f"Cache hit trop lent ({elapsed*1000:.1f}ms)"

        # Simule expiration TTL en modifiant le timestamp du cache
        old_entry = _PCMN_CACHE[cid]
        _PCMN_CACHE[cid] = (time.time() - 999, old_entry[1], old_entry[2])

        # Troisieme appel apres expiration -> refetch (nouveau timestamp)
        class6_new, _ = await _get_pcmn_cached(db, cid)
        assert class6_new == class6
        # Timestamp doit avoir ete rafraichi
        assert _PCMN_CACHE[cid][0] > time.time() - 5
    finally:
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        _PCMN_CACHE.pop(cid, None)


async def _run_model_selection_by_use_vision():
    """Verifie le choix de modele selon use_vision (Haiku texte, Sonnet vision)."""
    # On teste indirectement le fait que le code source contient les 2 modeles.
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "invoice_ai.py",
    )
    with open(src_path) as f:
        src = f.read()
    assert "claude-haiku-4-5-20251001" in src, (
        "Iter90an : le path texte doit utiliser Claude Haiku 4.5"
    )
    assert "claude-sonnet-4-5-20250929" in src, (
        "Le path vision doit conserver Claude Sonnet 4.5"
    )
    # Verifie la logique ternaire
    assert 'use_vision else "claude-haiku-4-5-20251001"' in src or \
           '"claude-sonnet-4-5-20250929" if use_vision else "claude-haiku-4-5-20251001"' in src


async def _run_pcmn_list_limit_40():
    """Verifie que la liste PCMN est plafonnee a 40 comptes (au lieu de 60/200)."""
    from routes.invoice_ai import _get_pcmn_cached, _PCMN_CACHE

    db = await _mongo()
    cid = f"iter90an-limit-{uuid.uuid4()}"
    # Seed 60 comptes classe 6 (plus que la limite de 40)
    docs = [
        {"number": f"61{i:04d}", "name": f"Compte {i}", "class_num": 6, "copropriete_id": cid}
        for i in range(60)
    ]
    await db.pcmn_accounts.insert_many(docs)
    try:
        _PCMN_CACHE.clear()
        class6, _ = await _get_pcmn_cached(db, cid)
        assert len(class6) == 40, f"Attendu 40 comptes max, obtenu {len(class6)}"
    finally:
        await db.pcmn_accounts.delete_many({"copropriete_id": cid})
        _PCMN_CACHE.pop(cid, None)


async def _run_max_chars_pdf_text():
    """Verifie que _extract_pdf_text a un default 4000 chars."""
    from routes.invoice_ai import _extract_pdf_text
    import inspect
    sig = inspect.signature(_extract_pdf_text)
    default = sig.parameters["max_chars"].default
    assert default == 4000, f"max_chars default = {default}, attendu 4000"


def test_pcmn_cache_hit_avoids_refetch():
    asyncio.run(_run_cache_hit_avoids_refetch())


def test_model_selection_by_use_vision():
    asyncio.run(_run_model_selection_by_use_vision())


def test_pcmn_list_limit_40():
    asyncio.run(_run_pcmn_list_limit_40())


def test_max_chars_pdf_text():
    asyncio.run(_run_max_chars_pdf_text())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
