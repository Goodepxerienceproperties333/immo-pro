"""Isolation multi-syndic — helpers centraux.

Chaque document metier porte un champ `syndic_id` qui identifie
le cabinet syndic proprietaire. Ce module fournit les helpers
pour resoudre, filtrer et injecter ce champ.

Convention :
  - syndic      -> syndic_id = str(user["_id"])
  - gestionnaire -> syndic_id = user["parent_syndic_id"]
  - superadmin  -> bypass (voit tout)
  - owner       -> acces via /api/owner/* (isolation separee)
"""

from fastapi import Request


def resolve_syndic_id(user: dict) -> str | None:
    """Retourne le syndic_id effectif pour un utilisateur.

    - superadmin/admin : None (pas de filtre)
    - syndic           : str(user._id)
    - gestionnaire     : user.parent_syndic_id
    - owner            : None (isolation geree separement)
    """
    role = (user.get("role") or "").lower()
    if role in ("superadmin", "admin"):
        return None
    if role == "syndic":
        uid = user.get("_id") or user.get("id")
        return str(uid) if uid else None
    if role == "gestionnaire":
        return user.get("parent_syndic_id") or None
    return None


def syndic_query(request: Request) -> dict:
    """Retourne le filtre MongoDB {'syndic_id': X} ou {} pour superadmin."""
    sid = getattr(request.state, "syndic_id", None)
    if sid:
        return {"syndic_id": sid}
    return {}


def inject_syndic(doc: dict, request: Request) -> dict:
    """Ajoute syndic_id au document si l'utilisateur en a un."""
    sid = getattr(request.state, "syndic_id", None)
    if sid:
        doc["syndic_id"] = sid
    return doc


async def get_syndic_id_for_copro(db, copro_id: str) -> str:
    """Resout le syndic_id a partir du copropriete_id.

    Utilise dans les fichiers utilitaires (auto_entries, etc.)
    qui n'ont pas acces au Request.
    """
    if not copro_id:
        return ""
    copro = await db.coproprietes.find_one(
        {"id": copro_id}, {"_id": 0, "syndic_id": 1}
    )
    return (copro or {}).get("syndic_id", "")
