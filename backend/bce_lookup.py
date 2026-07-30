"""iter90ip : Lookup BCE (Banque-Carrefour des Entreprises) par nom.

Contexte metier : lors de l'import de fournisseurs Optipro, le BCE n'est
pas toujours renseigne. Ce module interroge le KBO Public Search
(kbopub.economie.fgov.be) pour retrouver le numero d'entreprise a partir
du nom fourni. Il ne remplace pas une saisie utilisateur : les candidats
sont proposes au syndic qui choisit.

Choix techniques :
- Scraping du formulaire de recherche phonetique (KBO ne fournit pas
  d'API publique gratuite ; l'API officielle est payante ~50€/2000 req).
- User-Agent identifiant l'application (transparence + tracabilite).
- Cache Mongo (`bce_lookup_cache`) TTL 30 jours pour eviter les
  requetes redondantes.
- Rate limit local : semaphore asyncio (max 5 requetes concurrentes).
- Timeout court (5s) + fallback gracieux (retourne [] en cas d'erreur,
  ne bloque jamais l'import).
- Score de similarite par tokens (Q4-c choisi par l'utilisateur) :
  intersection normalisee des mots significatifs.

Ce module est appelable directement via `search_kbo_by_name(name)`.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

# iter93bk : helpers deplaces dans bce_shared pour supprimer le cycle
# d'imports circulaire avec bce_opendata.
from bce_shared import _MULTISPACE_RE, _norm, _tokens, token_similarity  # noqa: F401

KBO_SEARCH_URL = "https://kbopub.economie.fgov.be/kbopub/zoeknaamfonetischform.html"
KBO_USER_AGENT = "NextGeCopro/1.0 (Belgian condominium management ; contact via app)"
KBO_TIMEOUT_SEC = 6.0
KBO_MAX_CANDIDATES = 10  # limite d'entites parsees par recherche
CACHE_TTL_DAYS = 30
_semaphore = asyncio.Semaphore(5)

# Regex : BCE au format "0XXX.XXX.XXX" ou "XXXX.XXX.XXX" (10 chiffres avec points).
_BCE_RE = re.compile(r"\b(\d{4}\.\d{3}\.\d{3})\b")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_NBSP_RE = re.compile(r"[\xa0]|&nbsp;|&#160;", re.IGNORECASE)

# Mots vides frequents dans les raisons sociales belges. On les exclut du
# calcul de similarite pour eviter que "SA" ou "SPRL" gonfle artificiellement
# les scores. iter93bk : deplace dans bce_shared, re-exporte pour la
# retrocompatibilite.
from bce_shared import _STOPWORDS  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Normalisation & scoring : deplace dans bce_shared (iter93bk).
# `_norm`, `_tokens`, `token_similarity` sont importes en haut du fichier.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTTP scraping
# ---------------------------------------------------------------------------
def _extract_bce(cell: str) -> Optional[str]:
    m = _BCE_RE.search(cell)
    return m.group(1) if m else None


def _clean_cell(html: str) -> str:
    txt = _TAG_RE.sub(" ", html)
    txt = _NBSP_RE.sub(" ", txt)
    return _MULTISPACE_RE.sub(" ", txt).strip()


def _parse_kbo_results(html: str) -> list[dict]:
    """Parse la liste des entites depuis la page de resultats KBO.

    Structure attendue (colonnes) :
      0: index de ligne ("1", "2", ...)
      1: type entite ("ENT PM Actif", "ENT PP Actif", ...)
      2: BCE + date de creation ("0893.860.839 30 novembre 2007")
      3: unites d'etablissement ("-" ou "1 Unité d'établissement")
      4: NOM (raison sociale)
      5: ADRESSE

    Retourne des dicts {bce, name, address, entity_type}.
    """
    results: list[dict] = []
    for row_html in _TR_RE.findall(html):
        if "toonondernemingps" not in row_html:
            continue
        cells = [_clean_cell(c) for c in _TD_RE.findall(row_html)]
        if not cells:
            continue
        # Localise l'index de la cellule contenant le BCE.
        bce_idx = -1
        bce = None
        for i, c in enumerate(cells):
            b = _extract_bce(c)
            if b:
                bce_idx = i
                bce = b
                break
        if bce is None or bce_idx < 0:
            continue
        # entity_type : cellule juste avant le BCE si elle mentionne "ENT" ou "actif".
        entity_type = ""
        if bce_idx > 0 and (
            cells[bce_idx - 1].lower().startswith(("ent ", "unite", "unité"))
            or "actif" in cells[bce_idx - 1].lower()
        ):
            entity_type = cells[bce_idx - 1]
        # Le nom et l'adresse sont APRES le BCE. On saute la cellule "unites
        # d'etablissement" (souvent "-" ou "N unites...") si presente.
        after = cells[bce_idx + 1:]

        def _is_units_cell(t: str) -> bool:
            tl = t.lower()
            return (
                t == "-"
                or "unite" in tl or "unité" in tl
                or tl.startswith(("aucun", "aucune"))
            )
        # Filtre les cellules significatives (au moins 2 chars + au moins une lettre).
        meaningful = [
            t for t in after
            if t and not _is_units_cell(t) and any(ch.isalpha() for ch in t) and len(t) >= 2
        ]
        if not meaningful:
            continue
        name = meaningful[0]
        address = meaningful[1] if len(meaningful) > 1 else ""
        results.append({
            "bce": bce,
            "name": name,
            "address": address,
            "entity_type": entity_type,
        })
        if len(results) >= KBO_MAX_CANDIDATES:
            break
    return results


async def _fetch_kbo(name: str, postal_code: str = "") -> str:
    """Fait la requete GET vers le formulaire KBO. Retourne le HTML brut."""
    params = {
        "lang": "fr",
        "searchWord": name,
        "_oudeBenaming": "on",
        "pstcdeNPRP": postal_code or "",
        "postgemeente1": "",
        "ondRP": "true",
        "_ondRP": "on",
        "rechtsvormFonetic": "ALL",
        "_vest": "on",
        "filterEnkelActieve": "true",
        "_filterEnkelActieve": "on",
        "actionNPRP": "Rechercher",
    }
    headers = {
        "User-Agent": KBO_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr,en;q=0.5",
    }
    async with _semaphore:
        async with httpx.AsyncClient(timeout=KBO_TIMEOUT_SEC, follow_redirects=True) as client:
            resp = await client.get(KBO_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            return resp.text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def search_kbo_by_name(
    name: str,
    postal_code: str = "",
    top_n: int = 3,
    db=None,
    use_cache: bool = True,
) -> list[dict]:
    """Cherche un fournisseur belge par nom.

    Strategie (iter90iq) :
      1. Essaie D'ABORD le dataset local BCE Open Data si dispo (rapide,
         robuste, fiable) via `bce_opendata.search_bce_opendata`.
      2. Fallback sur le scraping live KBO Public Search si aucun candidat
         local (ou si Open Data pas ingere).

    Retourne les `top_n` meilleurs candidats tries par similarite de tokens
    decroissante. Chaque candidat contient :
      {bce, name, address, entity_type, similarity}

    Retourne [] en cas d'erreur ou de nom vide (jamais d'exception : la
    lookup est un enrichissement, elle ne doit pas casser l'import).
    """
    query = (name or "").strip()
    if len(query) < 2:
        return []

    # 1. iter90iq : tentative Open Data local (rapide, prioritaire)
    if db is not None:
        try:
            from bce_opendata import search_bce_opendata
            local_hits = await search_bce_opendata(db, query, postal_code=postal_code, top_n=top_n)
            if local_hits:
                return local_hits
        except Exception:
            pass  # fallback KBO en cas de probleme local

    cache_key = f"{_norm(query)}|{postal_code or ''}"

    # Cache lookup
    if use_cache and db is not None:
        try:
            cached = await db.bce_lookup_cache.find_one({"key": cache_key})
            if cached and cached.get("expires_at"):
                exp = cached["expires_at"]
                if isinstance(exp, str):
                    try:
                        exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    except Exception:
                        exp = None
                # Motor renvoie les datetime naifs (BSON ne stocke pas les tz).
                # On les traite comme UTC pour la comparaison.
                if isinstance(exp, datetime) and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp and exp > datetime.now(timezone.utc):
                    return (cached.get("results") or [])[:top_n]
        except Exception:
            pass  # cache best-effort

    # Live fetch
    try:
        html = await _fetch_kbo(query, postal_code=postal_code)
    except Exception:
        return []

    entities = _parse_kbo_results(html)
    # Compute similarity + sort desc
    scored = []
    for e in entities:
        sim = round(token_similarity(query, e["name"]), 3)
        scored.append({**e, "similarity": sim})
    scored.sort(key=lambda x: (-x["similarity"], x["name"]))

    # Cache write
    if db is not None:
        try:
            await db.bce_lookup_cache.update_one(
                {"key": cache_key},
                {"$set": {
                    "key": cache_key,
                    "query": query,
                    "postal_code": postal_code or "",
                    "results": scored,
                    "cached_at": datetime.now(timezone.utc),
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=CACHE_TTL_DAYS),
                }},
                upsert=True,
            )
        except Exception:
            pass  # cache best-effort

    return scored[:top_n]
