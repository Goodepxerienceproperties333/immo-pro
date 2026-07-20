"""iter90ip : Lookup BCE automatique par nom lors de l'import.

Verrouille le module `bce_lookup` (parser + similarite + cache) et
l'integration dans le wizard d'import :
- `POST /api/import-wizard/lookup-bce` : endpoint manuel (bouton UI)
- Enrichissement auto de `preview-suppliers-pdf` avec `bce_candidates`
  si aucun BCE deja renseigne + aucun match strict/fuzzy avec BCE.

Tests d'integration reseau (KBO live) marques `network` : sautes en
CI/offline. Tests unitaires (parser, similarite, cache) sans reseau.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


# ---------------------------------------------------------------------------
# Similarite par tokens (Q4-c choisi par l'utilisateur)
# ---------------------------------------------------------------------------
def test_token_similarity_exact_match():
    from bce_lookup import token_similarity
    assert token_similarity("Engie", "Engie") == 1.0


def test_token_similarity_stopwords_ignored():
    """iter90ip-1 : 'SA', 'SPRL' etc. ne doivent pas booster la similarite."""
    from bce_lookup import token_similarity
    # Meme entreprise, formes juridiques differentes -> match parfait sur
    # les tokens significatifs (les stopwords SA/SPRL sont exclus).
    assert token_similarity("Engie SA", "Engie SPRL") == 1.0


def test_token_similarity_case_insensitive_and_accents():
    from bce_lookup import token_similarity
    assert token_similarity("Électrabel", "electrabel") == 1.0
    assert token_similarity("ENGIE", "engie") == 1.0


def test_token_similarity_partial_overlap():
    """'Engie SA' vs 'Engie Electrabel' partage 'engie' (dominant du plus
    court) -> 1.0 selon la strategie de couverture du plus court cote."""
    from bce_lookup import token_similarity
    sim = token_similarity("Engie SA", "Engie Electrabel")
    assert sim == 1.0  # 'engie' est le seul token significatif de 'Engie SA'


def test_token_similarity_no_overlap():
    from bce_lookup import token_similarity
    assert token_similarity("Belfius", "Random Corp") == 0.0


def test_token_similarity_empty_safe():
    from bce_lookup import token_similarity
    assert token_similarity("", "Belfius") == 0.0
    assert token_similarity("Belfius", "") == 0.0
    assert token_similarity(None, None) == 0.0  # type: ignore


# ---------------------------------------------------------------------------
# Parser KBO (donnees fixtures HTML, offline)
# ---------------------------------------------------------------------------
_FAKE_KBO_HTML = """
<html><body>
<table>
<tr>
  <td>1</td>
  <td>ENT PM Actif</td>
  <td><a href="toonondernemingps.html?ondernemingsnummer=403201185">0403.201.185</a> 23 octobre 1962</td>
  <td>126 Unités d'établissements</td>
  <td>BELFIUS BANQUE</td>
  <td>Place Charles Rogier 11 1210 Saint-Josse-ten-Noode</td>
</tr>
<tr>
  <td>2</td>
  <td>ENT PM Actif</td>
  <td><a href="toonondernemingps.html?ondernemingsnummer=893860839">0893.860.839</a> 30 novembre 2007</td>
  <td>-</td>
  <td>BELFIUS ASSET FINANCE HOLDING</td>
  <td>Place Charles Rogier 11 1210 Saint-Josse-ten-Noode</td>
</tr>
</table>
</body></html>
"""


def test_parser_extracts_name_bce_address():
    """iter90ip-7 : le parser doit distinguer l'index de ligne ('1') du
    NOM ('BELFIUS BANQUE'). Bug initial resolu."""
    from bce_lookup import _parse_kbo_results
    res = _parse_kbo_results(_FAKE_KBO_HTML)
    assert len(res) == 2
    assert res[0]["bce"] == "0403.201.185"
    assert res[0]["name"] == "BELFIUS BANQUE"
    assert "Place Charles Rogier" in res[0]["address"]
    assert res[1]["bce"] == "0893.860.839"
    assert res[1]["name"] == "BELFIUS ASSET FINANCE HOLDING"


def test_parser_ignores_rows_without_bce_link():
    """Une ligne sans lien toonondernemingps est ignoree (garde HTML)."""
    from bce_lookup import _parse_kbo_results
    html = "<table><tr><td>1</td><td>Junk</td></tr></table>"
    assert _parse_kbo_results(html) == []


# ---------------------------------------------------------------------------
# Endpoint /api/import-wizard/lookup-bce
# ---------------------------------------------------------------------------
def test_lookup_bce_endpoint_returns_empty_for_short_query():
    """iter90ip-9 : moins de 2 chars -> [] sans requete reseau."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.import_wizard import create_import_wizard_router
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            router = create_import_wizard_router(db)
            endpoint = None
            for r in router.routes:
                if r.path == "/api/import-wizard/lookup-bce":
                    endpoint = r.endpoint
                    break
            assert endpoint is not None, "endpoint lookup-bce introuvable"

            class _Req:
                cookies = {}
                headers = {}
                @property
                def state(self):
                    class _S: pass
                    return _S()

            # Import local du modele input pour construire la payload propre.
            from pydantic import BaseModel
            class _Input(BaseModel):
                name: str
                postal_code: str = ""
                top_n: int = 3

            data = await endpoint(_Input(name="a"), _Req())
            assert data["count"] == 0
            assert data["candidates"] == []
            data2 = await endpoint(_Input(name=""), _Req())
            assert data2["candidates"] == []
        finally:
            client.close()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Cache Mongo
# ---------------------------------------------------------------------------
def test_cache_is_used_on_second_call(monkeypatch):
    """iter90ip-10 : deux appels rapproches avec la meme query ne doivent
    faire qu'UNE seule requete reseau (le 2eme lit le cache Mongo)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        import bce_lookup as bl
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        try:
            # Purge cache pour un nom unique de test.
            unique = f"CacheTestCorp-{uuid.uuid4().hex[:8]}"
            await db.bce_lookup_cache.delete_many({"query": unique})

            call_counter = {"n": 0}

            async def fake_fetch(name, postal_code=""):
                call_counter["n"] += 1
                return _FAKE_KBO_HTML  # HTML fixture

            monkeypatch.setattr(bl, "_fetch_kbo", fake_fetch)

            r1 = await bl.search_kbo_by_name(unique, top_n=3, db=db)
            r2 = await bl.search_kbo_by_name(unique, top_n=3, db=db)
            assert call_counter["n"] == 1, (
                f"Le second appel devrait etre servi depuis le cache, "
                f"call_counter={call_counter['n']}"
            )
            assert r1 == r2
            # Verifie que le cache Mongo contient bien l'entree.
            cached = await db.bce_lookup_cache.find_one({"query": unique})
            assert cached is not None
            assert cached["results"]  # liste non vide (fixture)
        finally:
            await db.bce_lookup_cache.delete_many({"query": unique})
            client.close()

    asyncio.run(_go())


def test_lookup_returns_empty_on_network_error(monkeypatch):
    """iter90ip-11 : timeout / erreur reseau -> [] sans exception. Le
    silent lookup ne doit JAMAIS bloquer un import."""
    async def _go():
        import bce_lookup as bl

        async def fake_fetch(name, postal_code=""):
            raise TimeoutError("boom")
        monkeypatch.setattr(bl, "_fetch_kbo", fake_fetch)
        res = await bl.search_kbo_by_name("Whatever", db=None)
        assert res == []

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Integration reseau : marque pour pouvoir la skip en offline
# ---------------------------------------------------------------------------
@pytest.mark.network
def test_live_kbo_search_belfius():
    """iter90ip-12 : appel live vers KBO. Doit renvoyer au moins un
    candidat pour 'Belfius Banque' avec BCE 0403.201.185."""
    async def _go():
        from bce_lookup import search_kbo_by_name
        res = await search_kbo_by_name("Belfius Banque", top_n=5, db=None)
        assert len(res) >= 1, "KBO doit renvoyer au moins 1 candidat pour Belfius Banque"
        bces = {r["bce"] for r in res}
        assert "0403.201.185" in bces, (
            f"BELFIUS BANQUE (0403.201.185) attendu dans les resultats, "
            f"trouve : {bces}"
        )

    asyncio.run(_go())
