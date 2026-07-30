"""bce_shared : helpers communs a `bce_lookup` et `bce_opendata`.

Extrait pour supprimer le cycle d'imports circulaire :
- bce_lookup.py -> (via `from bce_opendata import search_bce_opendata` interne
  a une fonction, tardif) -> bce_opendata.py
- bce_opendata.py -> (via `from bce_lookup import _norm, _tokens,
  token_similarity` en top-level) -> bce_lookup.py

iter93bk : deplace `_norm`, `_tokens`, `token_similarity` + `_STOPWORDS` +
`_MULTISPACE_RE` ici. `bce_lookup` et `bce_opendata` importent depuis
`bce_shared` uniquement.
"""
from __future__ import annotations

import re
import unicodedata

_MULTISPACE_RE = re.compile(r"\s+")

# Mots vides frequents dans les raisons sociales belges. On les exclut du
# calcul de similarite pour eviter que "SA" ou "SPRL" gonfle artificiellement
# les scores.
_STOPWORDS = {
    "sa", "sprl", "srl", "scrl", "scs", "snc", "sca", "asbl", "acp",
    "bv", "nv", "cvba", "vzw", "the", "and", "et", "de", "du", "des",
    "la", "le", "les", "l", "d", "van", "van der", "ter",
}


def _norm(s: str) -> str:
    """Normalise en ASCII minuscule sans ponctuation."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s.lower())
    return _MULTISPACE_RE.sub(" ", s).strip()


def _tokens(s: str) -> set:
    """Extrait les tokens significatifs d'un nom (hors stopwords, len>=2)."""
    return {t for t in _norm(s).split() if len(t) >= 2 and t not in _STOPWORDS}


def token_similarity(a: str, b: str) -> float:
    """Similarite Jaccard sur les tokens significatifs. Retourne [0, 1].

    Choix Q4-c (utilisateur) : plus tolerant que Levenshtein pour matcher
    'Engie SA' vs 'Engie Electrabel' (partage 'engie', dominant).
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    # On privilegie la couverture du plus court cote (le nom saisi par le
    # syndic est souvent plus court que le nom officiel BCE).
    shorter = min(len(ta), len(tb))
    return len(inter) / shorter
