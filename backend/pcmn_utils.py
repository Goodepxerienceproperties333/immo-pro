"""Utilitaires PCMN pour la normalisation des comptes bancaires.

Convention cible : 8 chiffres pour les comptes bancaires (classe 55).
  - 55XXXX   -> 55XXXX00  (6 chiffres paddes a droite)
  - 55XXXXXX -> inchange   (deja 8 chiffres)
  - 550, 551 -> inchange   (comptes parents, pas de normalisation)
"""


def normalize_bank_pcmn(num: str) -> str:
    """Normalise un numero PCMN bancaire vers la convention 8 chiffres.

    Regles :
      - Seuls les comptes commencant par 55 sont normalises
      - 6 chiffres (ex 551618) -> 8 chiffres (55161800)
      - 7 chiffres (ex 5500591) -> 8 chiffres (55005910)
      - 3, 4, 5 chiffres : comptes parents/abstraits -> inchanges
      - Deja 8+ chiffres : inchange
      - Chaine vide / non-55 : inchange
    """
    clean = (num or "").strip()
    if not clean or not clean.startswith("55"):
        return clean
    digits = "".join(c for c in clean if c.isdigit())
    if len(digits) <= 5:
        return clean
    if len(digits) < 8:
        return digits.ljust(8, "0")
    return digits[:8]


def pcmn_bank_match(a: str, b: str) -> bool:
    """Compare deux numeros PCMN bancaires apres normalisation.

    Retourne True si les deux numeros, une fois normalises, sont identiques.
    """
    if not a or not b:
        return False
    return normalize_bank_pcmn(a) == normalize_bank_pcmn(b)
