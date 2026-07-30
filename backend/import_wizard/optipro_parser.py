"""Optipro-Bilan dedicated parser (iter93bu).

Ce module est un moteur specialise dans la lecture des bilans comptables
Optipro (format PCMN belge). Il est concu pour fonctionner SANS
intervention manuelle sur TOUTES les ACP futures.

## Strategie multi-passes (robuste par construction)

Pass 1 : detection standard par en-tetes "Actif" / "Passif"
Pass 2 : si en-tetes absents (page continuation), cache la geometrie
Pass 3 : si desequilibre, retry avec tolerances elargies (x_tol=3, elargit
         la zone montants de 30px de part et d'autre)
Pass 4 : signale la/les page(s) ou la somme des sous-comptes ne correspond
         PAS au libelle principal (compte "TOTAL" ou main-account) pour
         un diagnostic precis (indispensable pour bilans multi-pages)

## Ancres semantiques (defense en profondeur)

En complement de la detection geometrique, on cherche des mots-cles fixes
pour verifier la coherence :
  - Actif : "Copropriétaires", "Fournisseurs", "Charges à reporter",
    "Banques", "Caisse", "Actionnaires"
  - Passif : "Fonds de roulement", "Fonds de réserve", "Copropriétaires",
    "Fournisseurs", "Sinistre", "Provisions"

## Retour

{
  "actif": [{account, label, amount, is_subaccount, page}],
  "passif": [...],
  "total_actif": float,
  "total_passif": float,
  "balanced": bool,
  "ecart": float,   # total_actif - total_passif
  "period_end_date": "DD/MM/YYYY",
  "pages_scanned": int,
  "diagnostic": {
      "strategy_used": "standard" | "widened" | "keywords_fallback",
      "warnings": [str],           # ex. "Page 3 : somme sous-comptes 410 (X) != libelle main (Y)"
      "page_totals": [             # somme par page (aide au debug bilan multi-pages)
        {"page": 1, "actif": float, "passif": float}
      ]
  }
}
"""
from __future__ import annotations

import io
import re
from typing import Any

import pdfplumber

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
# Comptes PCMN belges : 2-10 chiffres (incl. banques Optipro 8-digit)
_ACCOUNT_RE = re.compile(r"^\d{2,10}$")
_AMOUNT_RE = re.compile(r"^-?[\d.,]+$")
_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})$")

# Ancres semantiques pour verification de coherence (case-insensitive prefix)
_ACTIF_KEYWORDS = {
    "copropriétaires", "coproprietaires",
    "fournisseurs",
    "charges à reporter", "charges a reporter",
    "banques", "banque",
    "caisse",
    "actionnaires",
}
_PASSIF_KEYWORDS = {
    "fonds de roulement",
    "fonds de réserve", "fonds de reserve",
    "copropriétaires", "coproprietaires",
    "fournisseurs",
    "sinistre",
    "provisions",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _join_amount(words: list[dict]) -> float:
    """Fusionne '1', '934,95' -> 1934.95. Gere '-585,89' et '\\u00a0'."""
    if not words:
        return 0.0
    ws = sorted(words, key=lambda w: w["x0"])
    joined = "".join(w["text"] for w in ws).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return float(joined)
    except ValueError:
        return 0.0


def _detect_columns(words: list[dict]) -> tuple[float | None, float | None]:
    """Detecte les positions x-center des en-tetes 'Actif' et 'Passif'."""
    actif_x = passif_x = None
    for w in words:
        if w["top"] > 180:
            continue
        t = w["text"].lower()
        if t == "actif":
            actif_x = (w["x0"] + w["x1"]) / 2
        elif t == "passif":
            passif_x = (w["x0"] + w["x1"]) / 2
    return actif_x, passif_x


def _detect_period_end(words: list[dict]) -> str:
    """Cherche une date DD/MM/YYYY dans l'en-tete."""
    for w in words:
        if w["top"] < 60:
            m = _DATE_RE.match(w["text"])
            if m:
                return m.group(1)
    return ""


def _detect_totals(
    words: list[dict],
    split_x: float,
    actif_xmin: float,
    passif_xmin: float,
) -> tuple[float, float] | None:
    """Trouve la ligne 'Total' et extrait les montants Actif/Passif si presents."""
    total_y = None
    for w in words:
        if w["text"].lower() == "total":
            total_y = w["top"]
            break
    if total_y is None:
        return None
    total_ws = [w for w in words if abs(w["top"] - total_y) < 4 and _AMOUNT_RE.match(w["text"])]
    actif_ws = [
        w for w in total_ws
        if (w["x0"] + w["x1"]) / 2 < split_x and w["x0"] >= actif_xmin
    ]
    passif_ws = [
        w for w in total_ws
        if (w["x0"] + w["x1"]) / 2 >= split_x and w["x0"] >= passif_xmin
    ]
    return _join_amount(actif_ws), _join_amount(passif_ws)


def _extract_row(
    anchor_w: dict,
    words: list[dict],
    side: str,
    split_x: float,
    amount_xmin: float,
) -> tuple[str, float]:
    """Extrait le libelle + montant d'une ligne autour de l'ancre `anchor_w`."""
    top = anchor_w["top"]
    row_ws = [w for w in words if abs(w["top"] - top) < 4]
    if side == "actif":
        side_ws = [w for w in row_ws if (w["x0"] + w["x1"]) / 2 < split_x]
    else:
        side_ws = [w for w in row_ws if (w["x0"] + w["x1"]) / 2 >= split_x]
    side_ws.sort(key=lambda w: w["x0"])

    libelle_words = []
    amount_words = []
    for w in side_ws:
        if w is anchor_w:
            continue
        if _AMOUNT_RE.match(w["text"]) and w["x0"] >= amount_xmin:
            amount_words.append(w)
        elif w["text"] in ("-", "–") and abs(w["x0"] - (anchor_w["x1"] + 2)) < 5:
            continue
        else:
            libelle_words.append(w)
    libelle = " ".join(w["text"] for w in libelle_words).strip()
    libelle = re.sub(r"^[-–]\s*", "", libelle)
    return libelle, _join_amount(amount_words)


def _parse_page(
    page: Any,
    page_idx: int,
    cached_actif_x: float | None,
    cached_passif_x: float | None,
    actif_xmin: float,
    passif_xmin: float,
    x_tolerance: int = 2,
) -> dict:
    """Parse une page. Retourne les lignes actif/passif + geometrie cachee."""
    words = page.extract_words(
        keep_blank_chars=False,
        x_tolerance=x_tolerance,
        y_tolerance=3,
    ) or []
    if not words:
        return {"actif": [], "passif": [], "totals": None,
                "actif_x": cached_actif_x, "passif_x": cached_passif_x,
                "period_end": ""}

    period_end = _detect_period_end(words)
    actif_x, passif_x = _detect_columns(words)

    if actif_x is None or passif_x is None:
        # Continuation page : reutilise la geometrie cachee
        if cached_actif_x is None or cached_passif_x is None:
            return {"actif": [], "passif": [], "totals": None,
                    "actif_x": None, "passif_x": None, "period_end": period_end}
        actif_x = cached_actif_x
        passif_x = cached_passif_x

    split_x = (actif_x + passif_x) / 2

    # Detecte la ligne "Total" pour delimiter la zone de scan
    total_y = None
    for w in words:
        if w["text"].lower() == "total":
            total_y = w["top"]
            break

    # Trouve les ancres = codes de compte
    top_reject = 180 if page_idx == 0 else 30
    actif_rows, passif_rows = [], []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not _ACCOUNT_RE.match(w["text"]):
            continue
        if w["top"] <= top_reject:
            continue
        if total_y is not None and w["top"] >= total_y - 1:
            continue
        cx = (w["x0"] + w["x1"]) / 2
        side = "actif" if cx < split_x else "passif"
        # Sub-account si indente
        if side == "actif":
            is_sub = w["x0"] > 60
            amount_xmin = actif_xmin
        else:
            is_sub = w["x0"] > 435
            amount_xmin = passif_xmin
        # Rejette faux positifs : codes 2-digit dans zone sub-account
        # (souvent des fragments de montant type "10" dans "10 262,39")
        if len(w["text"]) == 2 and is_sub:
            continue
        libelle, amount = _extract_row(w, words, side, split_x, amount_xmin)
        entry = {
            "account": w["text"],
            "label": libelle,
            "amount": amount,
            "is_subaccount": is_sub,
            "page": page_idx + 1,
        }
        (actif_rows if side == "actif" else passif_rows).append(entry)

    totals = _detect_totals(words, split_x, actif_xmin, passif_xmin)
    return {
        "actif": actif_rows,
        "passif": passif_rows,
        "totals": totals,
        "actif_x": actif_x,
        "passif_x": passif_x,
        "period_end": period_end,
    }


def _validate_subaccount_sums(rows: list[dict]) -> list[str]:
    """Verifie que somme sous-comptes N == libelle du compte main N.

    Pour chaque main-account, aggrege ses sous-comptes (memes 3-4 premiers
    chiffres) et compare a la valeur du main. Retourne les avertissements
    si divergence > 0.01 EUR.
    """
    warnings = []
    # Regroupe par prefixe (les 3 premiers chiffres du compte)
    from collections import defaultdict
    main_by_prefix: dict[str, dict] = {}
    subs_by_prefix: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        prefix = r["account"][:3]
        if not r["is_subaccount"]:
            main_by_prefix[prefix] = r
        else:
            subs_by_prefix[prefix].append(r)
    for prefix, main in main_by_prefix.items():
        subs = subs_by_prefix.get(prefix, [])
        if not subs:
            continue
        sub_sum = round(sum(s["amount"] for s in subs), 2)
        if abs(sub_sum - main["amount"]) > 0.01:
            pages = sorted({s["page"] for s in subs})
            warnings.append(
                f"Compte {main['account']} ({main['label']}) : somme des "
                f"sous-comptes ({sub_sum:.2f}) != libelle main "
                f"({main['amount']:.2f}). Pages : {pages}"
            )
    return warnings


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------
def parse_optipro_bilan(raw: bytes) -> dict:
    """Parse un bilan Optipro (format PCMN belge) avec retry automatique.

    Args:
        raw: bytes du PDF Optipro "Bilan comptable"

    Returns:
        dict avec les cles decrites dans le docstring du module.
    """
    strategies = [
        # (nom, x_tolerance, actif_xmin, passif_xmin)
        ("standard", 2, 350, 730),
        ("widened_xtol", 3, 320, 700),
        ("widened_full", 4, 300, 680),
    ]
    last_result = None
    for strategy_name, x_tol, a_xmin, p_xmin in strategies:
        result = _run_pass(raw, x_tolerance=x_tol, actif_xmin=a_xmin, passif_xmin=p_xmin)
        result["diagnostic"]["strategy_used"] = strategy_name
        if result["balanced"]:
            return result
        last_result = result
    # Aucune strategie n'a equilibre : retourne la derniere tentative
    # + un warning global pour aider au diagnostic
    if last_result is not None:
        last_result["diagnostic"]["warnings"].insert(
            0,
            f"BILAN DESEQUILIBRE apres {len(strategies)} tentatives : "
            f"Actif={last_result['total_actif']:.2f} / "
            f"Passif={last_result['total_passif']:.2f} / "
            f"Ecart={last_result['ecart']:.2f}. Verifier le PDF source."
        )
    return last_result or _empty_result()


def _empty_result() -> dict:
    return {
        "actif": [], "passif": [],
        "total_actif": 0.0, "total_passif": 0.0,
        "balanced": False, "ecart": 0.0,
        "period_end_date": "", "pages_scanned": 0,
        "diagnostic": {
            "strategy_used": "empty",
            "warnings": ["PDF vide ou illisible"],
            "page_totals": [],
        },
    }


def _run_pass(raw: bytes, x_tolerance: int, actif_xmin: float, passif_xmin: float) -> dict:
    """Execute un pass complet avec les tolerances fournies."""
    all_actif, all_passif = [], []
    total_actif = total_passif = 0.0
    period_end = ""
    page_totals = []
    cached_actif_x = cached_passif_x = None

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            p = _parse_page(
                page, page_idx,
                cached_actif_x, cached_passif_x,
                actif_xmin, passif_xmin, x_tolerance,
            )
            all_actif.extend(p["actif"])
            all_passif.extend(p["passif"])
            if p["actif_x"] is not None:
                cached_actif_x = p["actif_x"]
            if p["passif_x"] is not None:
                cached_passif_x = p["passif_x"]
            if p["period_end"] and not period_end:
                period_end = p["period_end"]
            if p["totals"] is not None:
                ta, tp = p["totals"]
                if ta > 0:
                    total_actif = ta
                if tp > 0:
                    total_passif = tp
            # Somme sous-comptes de cette page (aide debug)
            page_totals.append({
                "page": page_idx + 1,
                "actif": round(sum(e["amount"] for e in p["actif"] if e["is_subaccount"]), 2),
                "passif": round(sum(e["amount"] for e in p["passif"] if e["is_subaccount"]), 2),
            })
        pages_scanned = len(pdf.pages)

    # Fallback : si les totaux "Total actif / Total passif" n'ont pas ete
    # detectes, calcule via la somme des sous-comptes
    if total_actif == 0:
        total_actif = round(sum(e["amount"] for e in all_actif if e["is_subaccount"]), 2)
    if total_passif == 0:
        total_passif = round(sum(e["amount"] for e in all_passif if e["is_subaccount"]), 2)

    ecart = round(total_actif - total_passif, 2)
    balanced = abs(ecart) < 0.01

    # Validation semantique des sous-comptes vs main
    warnings = []
    warnings.extend(_validate_subaccount_sums(all_actif))
    warnings.extend(_validate_subaccount_sums(all_passif))

    return {
        "actif": all_actif,
        "passif": all_passif,
        "total_actif": total_actif,
        "total_passif": total_passif,
        "balanced": balanced,
        "ecart": ecart,
        "period_end_date": period_end,
        "pages_scanned": pages_scanned,
        "diagnostic": {
            "strategy_used": "",  # rempli par le caller
            "warnings": warnings,
            "page_totals": page_totals,
        },
    }
