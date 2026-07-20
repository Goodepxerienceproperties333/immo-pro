"""iter90jb : Normalisation stricte des IBAN.

Un IBAN peut arriver dans la DB de plusieurs facons :
- `BE68 5390 0754 7034`  (Optipro / user saisi)
- `BE68-5390-0754-7034`  (documents commerciaux)
- `BE68539007547034`     (canonique - sans separateur)

Ces variantes se retrouvent en base et causent des doublons visuels (ex : 2
comptes "Compte * 9331" dans le menu deroulant de la refonte Banque iter90ja).

Ce helper impose UNE seule representation : lettres MAJUSCULES, chiffres,
sans espace, sans tiret, sans point. C'est le format canonique du standard
ISO 13616 (IBAN).

Usage :
  from iban_utils import normalize_iban
  iban = normalize_iban(user_input)  # "BE68 5390 0754 7034" -> "BE68539007547034"
"""
from __future__ import annotations


def normalize_iban(value: str | None) -> str:
    """Canonise un IBAN : uppercase + suppression des espaces/tirets/points.

    - `None` / `""` -> `""`.
    - `"BE68 5390 0754 7034"` -> `"BE68539007547034"`.
    - `"be04-0019-5208-9331"` -> `"BE04001952089331"`.
    - `"BE04001952089331"`    -> `"BE04001952089331"` (inchange).

    N'effectue AUCUNE validation ISO 13616 (checksum) : on veut normaliser
    des chaines quelquefois mal saisies sans casser l'import.
    """
    if not value:
        return ""
    s = str(value).strip().upper()
    # Supprime tous les separateurs courants
    for sep in (" ", "-", "."):
        s = s.replace(sep, "")
    return s
