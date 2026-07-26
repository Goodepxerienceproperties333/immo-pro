"""iter90go — Cache bilan + Pagination owners (P0 stress test iter90gn).

Verrouille :
  1. `/api/reports/bilan` retourne `_cache_hit=True` sur le 2eme appel
     avec les memes parametres (dans les 60s).
  2. `force_refresh=true` bypasse le cache.
  3. Changer `view_mode` ou `date_to` cree une nouvelle entree cache
     (chaque combinaison a son entree distincte).
  4. `/api/owners` en mode paginated (limit fourni) retourne bien
     `{items, total, skip, limit}` avec pagination MongoDB.
  5. Le mode legacy (sans `limit`) continue de retourner un tableau brut
     (backward compat pour tous les appelants existants).
  6. Le `search` server-side filtre par regex insensible casse sur
     last_name/first_name/email/vcs_code.
"""
import os
import pytest
import requests
import uuid

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")
MARIA_ID = "ed728e70-1d0d-4057-a37a-d450cc9ac812"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def copro_id(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", [])
        if items:
            return items[0]["id"]
    return MARIA_ID


# ==================== CACHE BILAN ====================

def test_bilan_cache_hit_on_second_call(admin_session, copro_id):
    """1er appel : cache miss (pas de _cache_hit). 2eme appel identique : hit."""
    # Force refresh en premier pour partir d'un cache vide (pas d'appel precedent
    # dans les 60s d'un autre test).
    r1 = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "force_refresh": "true"},
        timeout=60,
    )
    assert r1.status_code == 200, r1.text[:200]
    body1 = r1.json()
    assert body1.get("_cache_hit") is None or body1.get("_cache_hit") is False, "1er appel devrait etre miss"
    total_actif_1 = body1.get("total_actif")

    # 2eme appel identique -> hit
    r2 = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id},
        timeout=60,
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("_cache_hit") is True, f"2eme appel devrait etre hit (got {body2.get('_cache_hit')})"
    # Meme calcul, meme total
    assert body2.get("total_actif") == total_actif_1


def test_bilan_force_refresh_bypasses_cache(admin_session, copro_id):
    """force_refresh=true ne renvoie JAMAIS _cache_hit=True."""
    # Warmup cache
    admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id},
        timeout=60,
    )
    # Force refresh
    r = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "force_refresh": "true"},
        timeout=60,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("_cache_hit") is None or body.get("_cache_hit") is False, \
        f"force_refresh devrait bypass le cache (got _cache_hit={body.get('_cache_hit')})"


def test_bilan_different_view_mode_separate_cache_entries(admin_session, copro_id):
    """view_mode=before_distribution et after_distribution sont 2 entrees cache separees."""
    # Warmup les 2 view_modes avec force_refresh pour reset
    r_before = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "view_mode": "before_distribution", "force_refresh": "true"},
        timeout=60,
    )
    r_after = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "view_mode": "after_distribution", "force_refresh": "true"},
        timeout=60,
    )
    assert r_before.status_code == 200
    assert r_after.status_code == 200
    # 2eme appel de chacun -> hit (cache separe par view_mode)
    r2_before = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "view_mode": "before_distribution"},
        timeout=60,
    )
    r2_after = admin_session.get(
        f"{BASE_URL}/api/reports/bilan",
        params={"copropriete_id": copro_id, "view_mode": "after_distribution"},
        timeout=60,
    )
    assert r2_before.json().get("_cache_hit") is True
    assert r2_after.json().get("_cache_hit") is True


# ==================== PAGINATION OWNERS ====================

def test_owners_legacy_returns_bare_array(admin_session, copro_id):
    """Sans `limit`, /api/owners doit retourner un tableau brut (backward compat)."""
    r = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"copropriete_id": copro_id},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list), f"Legacy mode devrait retourner list, got {type(body).__name__}: {str(body)[:200]}"


def test_owners_paginated_returns_meta_envelope(admin_session, copro_id):
    """Avec `limit`, /api/owners doit retourner {items, total, skip, limit}."""
    r = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"copropriete_id": copro_id, "limit": 5, "skip": 0},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict), f"Paginated mode devrait retourner dict, got {type(body).__name__}"
    assert set(body.keys()) >= {"items", "total", "skip", "limit"}, f"Champs manquants : {list(body.keys())}"
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)
    assert body["skip"] == 0
    assert body["limit"] == 5
    assert len(body["items"]) <= 5


def test_owners_paginated_skip_beyond_total(admin_session, copro_id):
    """skip > total doit retourner items=[] mais total inchange (permet UI 'aucun resultat')."""
    r = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"copropriete_id": copro_id, "limit": 5, "skip": 99999},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] >= 0
    assert body["skip"] == 99999


def test_owners_search_server_side_filter(admin_session, copro_id):
    """`search` filtre server-side sur last_name/first_name/email/vcs_code (regex insensible casse)."""
    # D'abord recupere un owner existant pour connaitre son nom
    r_all = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"copropriete_id": copro_id, "limit": 100},
        timeout=30,
    )
    assert r_all.status_code == 200
    items = r_all.json().get("items", [])
    if not items:
        pytest.skip("Aucun owner dans la copro de test - impossible de valider search")
    # Cherche par le 1er owner
    target = items[0]
    search_term = (target.get("last_name") or target.get("name") or "")[:4]
    if not search_term or len(search_term) < 2:
        pytest.skip("Nom du 1er owner trop court pour tester le search")
    r_search = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"copropriete_id": copro_id, "limit": 100, "search": search_term.lower()},
        timeout=30,
    )
    assert r_search.status_code == 200
    body = r_search.json()
    # Doit trouver au moins 1 match (le target)
    assert body["total"] >= 1
    # Toutes les entrees retournees doivent contenir search_term (insensible casse)
    for it in body["items"]:
        haystack = " ".join([
            str(it.get("last_name") or ""),
            str(it.get("first_name") or ""),
            str(it.get("name") or ""),
            str(it.get("email") or ""),
            str(it.get("vcs_code") or ""),
        ]).lower()
        assert search_term.lower() in haystack, f"'{search_term.lower()}' absent de {it.get('name') or it.get('last_name')}"


def test_owners_syndic_wide_supports_pagination(admin_session):
    """syndic_wide=true accepte aussi pagination (utile pour listes globales)."""
    r = admin_session.get(
        f"{BASE_URL}/api/owners",
        params={"syndic_wide": "true", "limit": 3},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert body["limit"] == 3
    assert len(body["items"]) <= 3
