"""iter93de - Enforcement RBAC (Role-Based Access Control).

Ce module fournit un helper `require_permission()` a appliquer sur les
endpoints d'ecriture. Il verifie que l'utilisateur connecte dispose
d'une permission specifique parmi son `permissions` list.

Regles :
  - superadmin / admin / syndic : bypass (accès total)
  - gestionnaire : verifie que `perm` est dans `user.permissions` (ou celles
    de son role_template si permissions est vide/absent)
  - owner / occupant : rejet (ces roles ne doivent PAS acceder aux endpoints
    protegés par require_permission)

Design volontairement permissif : si `permissions` est None (legacy user),
on autorise pour ne pas bloquer les gestionnaires historiques. C'est
uniquement les LISTES EXPLICITES (comme celle de "Consultation seule") qui
verrouillent.
"""
import logging
from fastapi import HTTPException, Request

logger = logging.getLogger("permissions")


async def _get_user_permissions(db, request: Request) -> tuple[str, list[str] | None]:
    """Retourne (role, permissions_list_or_None) pour le user courant.

    None = pas de permissions explicites -> tout autorise (legacy).
    []   = permissions explicitement vides -> tout refuse.
    ['x'] = liste blanche.
    """
    from bson import ObjectId
    role = getattr(request.state, "user_role", "") or ""
    uid = getattr(request.state, "user_id", "") or ""
    if not uid:
        return role, None
    try:
        u = await db.users.find_one(
            {"_id": ObjectId(uid)},
            {"_id": 0, "role": 1, "permissions": 1, "role_template_id": 1},
        )
    except Exception:
        return role, None
    if not u:
        return role, None
    role = u.get("role", role)
    perms = u.get("permissions")
    # Fallback : permissions du role_template si permissions est None
    if perms is None and u.get("role_template_id"):
        tpl = await db.role_templates.find_one(
            {"id": u["role_template_id"]}, {"_id": 0, "permissions": 1}
        )
        if tpl and tpl.get("permissions") is not None:
            perms = list(tpl["permissions"] or [])
    return role, perms


async def require_permission(db, request: Request, perm: str) -> None:
    """Verifie que l'utilisateur connecte a la permission demandee.

    Raise HTTPException(403) sinon avec un message clair.
    """
    role, perms = await _get_user_permissions(db, request)
    # Bypass pour les roles de haut niveau
    if role in ("superadmin", "admin", "syndic"):
        return
    # Rejet des roles inappropriees
    if role in ("owner", "occupant"):
        raise HTTPException(403, "Cet endpoint n'est pas accessible depuis l'espace proprietaire")
    # Gestionnaire : verifie la liste de permissions
    if perms is None:
        # Aucune contrainte definie -> autorise (retro-compat)
        return
    if perm in perms:
        return
    logger.info("Permission refusee : user %s (role=%s) perm=%s",
                getattr(request.state, "user_id", "?"), role, perm)
    raise HTTPException(
        403,
        f"Permission insuffisante : votre profil ne permet pas '{perm}'. "
        f"Contactez votre syndic pour obtenir cet acces.",
    )
