"""Helper centralise de securisation comptable.

Regle metier :
- Une ecriture ou facture ne peut etre saisie/modifiee/supprimee que si elle
  appartient a une periode couverte par un exercice fiscal OUVERT de l'ACP.
- Si la date tombe dans un exercice CLOTURE -> l'utilisateur doit le rouvrir
  d'abord (POST /fiscal/years/{id}/reopen, qui contre-passe les OD de cloture).
- Si la date n'est couverte par AUCUN exercice -> on refuse aussi (oblige a
  creer prealablement l'exercice avant de saisir).

L'usage est strictement bloquant cote API expose a l'utilisateur. Les flux
internes (cloture/reouverture, contre-passations, AN) ecrivent directement
en base via les routes/fiscal.py et ne sont pas concernes.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException


def _fmt(date_iso: str) -> str:
    """Format ISO -> JJ/MM/AAAA pour les messages d'erreur."""
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_iso or "?"


async def ensure_period_open(db, copropriete_id: str, date_iso: Optional[str],
                              context: str = "ecriture") -> None:
    """Verifie que la date `date_iso` est dans une periode comptable OUVERTE.

    Raise HTTPException 400 si :
    - date manquante ou invalide
    - date dans un exercice fiscal en statut 'closed' -> message "exercice cloture"
    - date hors de tout exercice -> message "aucun exercice ouvert"

    Args:
        db: Motor database instance
        copropriete_id: ID de l'ACP
        date_iso: Date au format YYYY-MM-DD ou ISO
        context: "ecriture", "facture", "appel de fonds", etc. (pour message)
    """
    if not copropriete_id:
        # Pas de chinese-walls verifies ici, autre couche s'en occupe
        return
    if not date_iso:
        raise HTTPException(400, "Date manquante pour la verification de l'exercice fiscal")

    # Normalise (YYYY-MM-DD)
    d10 = (date_iso or "")[:10]
    try:
        datetime.strptime(d10, "%Y-%m-%d")
    except Exception:
        raise HTTPException(400, f"Date invalide: {date_iso} (format attendu: YYYY-MM-DD)")

    # Cherche un FY dont [start_date, end_date] englobe la date
    fy = await db.fiscal_years.find_one(
        {"copropriete_id": copropriete_id,
         "start_date": {"$lte": d10},
         "end_date": {"$gte": d10}},
        {"_id": 0, "id": 1, "name": 1, "status": 1,
         "start_date": 1, "end_date": 1}
    )

    if not fy:
        # Aucun exercice ne couvre cette date
        raise HTTPException(
            400,
            f"Aucun exercice fiscal ouvert ne couvre la date du {_fmt(d10)}. "
            f"Pour saisir cette {context}, creez d'abord l'exercice correspondant "
            "dans Comptabilite > Exercices fiscaux."
        )

    if fy.get("status") == "closed":
        raise HTTPException(
            400,
            f"L'exercice '{fy.get('name','?')}' "
            f"({_fmt(fy.get('start_date',''))} au {_fmt(fy.get('end_date',''))}) "
            f"est cloture. Impossible de saisir/modifier une {context} sur une periode "
            "verrouillee. Pour modifier, rouvrez d'abord l'exercice via "
            "Comptabilite > Exercices fiscaux > Reouvrir (les ecritures de cloture "
            "seront automatiquement contre-passees)."
        )

    # FY ouvert -> OK
    return


async def ensure_entry_modifiable(db, entry: dict) -> None:
    """Verifie qu'une ecriture existante peut etre modifiee/supprimee.

    Verifie :
    - L'ecriture n'est pas une contre-passation (`is_reversal=True`) - immuable
    - L'ecriture n'a pas ete elle-meme contre-passee (`reversed=True`) - immuable
    - L'exercice qui contient la date de l'ecriture est OUVERT
    """
    if entry.get("is_reversal"):
        raise HTTPException(
            400,
            "Cette ecriture est une contre-passation (extourne automatique) et "
            "ne peut pas etre modifiee directement. Elle a ete generee lors de "
            "la reouverture d'un exercice et fait partie de l'audit trail."
        )
    if entry.get("reversed"):
        raise HTTPException(
            400,
            "Cette ecriture a deja ete contre-passee. Elle est conservee pour "
            "l'audit trail et ne peut plus etre modifiee."
        )
    await ensure_period_open(
        db, entry.get("copropriete_id", ""),
        entry.get("date"), context="ecriture"
    )
