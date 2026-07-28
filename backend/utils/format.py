"""iter93ac : formatage numerique unifie cote backend.

Reproduit exactement le comportement de `/app/frontend/src/lib/format.js` :
- Separateur de milliers : ESPACE INSECABLE (matches JS Intl 'fr-BE' output)
- Separateur decimal : VIRGULE
- 2 decimales par defaut pour les montants EUR

Utilise pour :
- Generateurs PDF (Bilan, Decompte, Budget, Balance Tiers, Situation compte,
  Mutation, Synthese/Liste depenses, Journaux + factures)
- Corps d'emails (rappels, factures, appels de fonds)
- Rows CSV/JSON destinees a l'affichage utilisateur (jamais aux calculs)

NE PAS utiliser sur des valeurs destinees a etre re-parsees numeriquement.
"""
from typing import Union


NBSP = "\u202f"  # narrow no-break space (utilise par Intl.NumberFormat fr-BE)


def fmt_eur(v: Union[float, int, str, None], with_suffix: bool = True) -> str:
    """Formate un montant EUR en style belge/francais francophone.

    Args:
        v: valeur numerique (float/int/string). None -> "0,00".
        with_suffix: ajoute " EUR" a la fin (defaut True).

    Returns:
        Ex : `10800.5` -> `"10\u202f800,50 EUR"` (avec suffix)
             `-1234.5` -> `"-1\u202f234,50 EUR"`
    """
    try:
        f = float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return "" if not with_suffix else " EUR"
    sign = "-" if f < 0 else ""
    f = abs(f)
    s = f"{sign}{f:,.2f}".replace(",", NBSP).replace(".", ",")
    return f"{s} EUR" if with_suffix else s


def fmt_number(v: Union[float, int, str, None], decimals: int = 2) -> str:
    """Formate un nombre generique avec espace millier + virgule decimale.

    Args:
        v: valeur numerique.
        decimals: nombre de decimales exact (default 2).

    Returns:
        Ex : `fmt_number(1234.5678, 4)` -> `"1\u202f234,5678"`
    """
    try:
        f = float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return ""
    sign = "-" if f < 0 else ""
    f = abs(f)
    s = f"{sign}{f:,.{decimals}f}".replace(",", NBSP).replace(".", ",")
    return s


def fmt_pct(v: Union[float, int, str, None], decimals: int = 2) -> str:
    """Formate un pourcentage. `10.5` -> `"10,50 %"`."""
    return f"{fmt_number(v, decimals)} %"


def fmt_quotity(v: Union[float, int, str, None]) -> str:
    """Formate une quotite (jusqu'a 6 decimales, zeros de fin retires).
    Utile pour l'affichage des parts sur cles de repartition.
    Ex : `267.270000` -> `"267,27"` ; `800` -> `"800"`.
    """
    try:
        f = float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return ""
    sign = "-" if f < 0 else ""
    f = abs(f)
    s = f"{sign}{f:,.6f}".replace(",", NBSP).replace(".", ",")
    # Retire les zeros de fin apres la virgule ET la virgule si plus rien apres
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s or "0"
